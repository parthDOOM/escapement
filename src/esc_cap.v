/*
 * esc_cap.v - Escapement capture unit (docs/11-SEMANTICS.md §6.5, §7.6).
 *
 * Copyright (c) 2026 Escapement contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Records the synchronised pin vector into the tape whenever a watched pin changes,
 * as {levels[7:0], tick[23:0]} words: the same timebase as DRIVE deadlines, so a
 * captured trace is a replayable event list after adding an offset. A 2-record
 * staging FIFO absorbs cycles in which an LDT/STT owns the tape port; if it ever
 * overflows, generation stops and `ovf`/`ovf_tick` latch (never a silent drop).
 *
 * Per edge, in order (unless start or stop is at the edge): 1 drain the staged head
 * to T[ptr] when the port is free; 2 generate a record (snapshot, watched change, or
 * the epoch marker at tick mod 2^24 == 0); 3 a completing CAPT overrides. After stop,
 * staged records still drain.
 * The unit watches one pin bank, chosen by CAPT operand bit 25.
 * Stream mode (CAPT bit 26) shares the ring with the host, which reads records
 * during the run and acknowledges them by setting `rptr` (TAPE_ACK). The drain is held
 * rather than write over an unacknowledged record, so a slow host ends in `ovf`, never
 * in silently lost data.
 */
`default_nettype none
module esc_cap (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,       // run initialisation at this edge
    input  wire        stop,        // stop at this edge (already masked by start)
    input  wire        run,         // run flag during this cycle
    input  wire [31:0] now,
    input  wire [12:0] psync,       // synchronised physical pins during this cycle
    input  wire        busy,        // an LDT/STT commit or a host write owns the port
    input  wire        ack,         // TAPE_ACK this cycle
    input  wire [7:0]  ack_v,       //       its new rptr
    input  wire        capt_we,     // a CAPT completes at this edge
    input  wire [31:0] capt_v,      // its operand
    output wire        drain,       // write the staged head this cycle
    output wire [7:0]  ptr_o,
    output wire [31:0] head_o,
    output wire [31:0] status,      // GETC / dbg sel 17
    output wire [31:0] cfg,         // dbg sel 18
    output wire [7:0]  rptr,        // dbg sel 20
    output wire [31:0] ovf_tick     // dbg sel 19
);
  reg       en, snap, ovf, full, wrapped;
  reg [1:0] n;
  // v3.2: everything below that is not a flag is a clock-gated bank (esc_greg, D-29): the
  // configuration, the pointers, the two staged records, the edge detector and the overflow
  // tick. Each enable is exactly the condition under which the always block assigned the
  // bank (start, then the drain, then generation, then a CAPT), and each d what it assigned,
  // so the unit is unchanged; with no capture armed none of them sees the clock.
  wire       ring, stream, bank;
  wire [7:0] mask, base, lim, ptr, prev;
  wire [31:0] st0, st1;
  wire [7:0] sync = bank ? {3'd0, psync[12:8]} : psync[7:0];   // the watched bank

  // step 1: drain, unless stream mode would write over the record at rptr
  wire [7:0] ptr_inc  = ptr + 8'd1;
  wire       hit_lim  = (ptr_inc == lim);
  wire       wrap     = ring | stream;
  wire [7:0] ptr_nxt  = (hit_lim & wrap) ? base : ptr_inc;
  wire       held     = stream & (ptr_nxt == rptr);
  wire       drain_c  = (n != 2'd0) & ~busy & ~start & ~stop & ~held;
  wire       set_full = drain_c & hit_lim & ~wrap;
  wire [1:0] n_after  = n - {1'b0, drain_c};

  // step 2: generate
  wire       changed  = ((sync ^ prev) & mask) != 8'd0;
  wire       epoch    = (now[23:0] == 24'd0);
  wire       gen      = en & run & ~start & ~stop & ~set_full & ~capt_we &
                        (snap | changed | epoch);
  wire       ovf_now  = gen & (n_after == 2'd2);
  wire       push     = gen & ~ovf_now;
  wire [31:0] rec     = {sync, now[23:0]};

  wire       arm      = capt_we & capt_v[31];               // a CAPT that arms
  wire       st0_rec  = push & (n_after == 2'd0);
  esc_greg #(27) g_cfg (.clk(clk), .rst_n(rst_n), .en(start | arm),
      .d(start ? 27'd0 : {capt_v[23:16], capt_v[15:8], capt_v[7:0], capt_v[24], capt_v[25], capt_v[26]}),
      .q({mask, lim, base, ring, bank, stream}));
  esc_greg #(8) g_ptr (.clk(clk), .rst_n(rst_n), .en(start | drain_c | arm),
      .d(start ? 8'd0 : arm ? capt_v[7:0] : ptr_nxt), .q(ptr));
  esc_greg #(8) g_rptr (.clk(clk), .rst_n(rst_n), .en(start | ack | arm),
      .d(start ? 8'd0 : arm ? capt_v[7:0] : ack_v), .q(rptr));
  esc_greg #(8) g_prev (.clk(clk), .rst_n(rst_n), .en(start | gen),
      .d(start ? 8'd0 : sync), .q(prev));
  esc_greg #(32) g_ovt (.clk(clk), .rst_n(rst_n), .en(start | (gen & ovf_now) | arm),
      .d((start | arm) ? 32'd0 : now), .q(ovf_tick));
  // the records are not cleared by a start (staging is empty then, n = 0)
  esc_greg #(32) g_st0 (.clk(clk), .rst_n(rst_n), .en(drain_c | st0_rec),
      .d(st0_rec ? rec : st1), .q(st0));
  esc_greg #(32) g_st1 (.clk(clk), .rst_n(rst_n), .en(push & (n_after != 2'd0)),
      .d(rec), .q(st1));

  assign drain  = drain_c;
  assign ptr_o  = ptr;
  assign head_o = st0;
  assign status = {en, ovf, full, wrapped, 18'd0, n, ptr};
  assign cfg    = {5'd0, stream, bank, ring, mask, lim, base};
  wire   _unused = &{1'b0, capt_v[30:27]};   // CAPT operand bits 30:27 are ignored (sec. 7.6)

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      en <= 1'b0; snap <= 1'b0;
      ovf <= 1'b0; full <= 1'b0; wrapped <= 1'b0;
      n <= 2'd0;
    end else if (start) begin
      en <= 1'b0; snap <= 1'b0;
      ovf <= 1'b0; full <= 1'b0; wrapped <= 1'b0;
      n <= 2'd0;
    end else begin
      // ---- 1 drain (st0 and ptr: g_st0, g_ptr)
      if (drain_c) begin
        if (hit_lim && wrap) wrapped <= 1'b1;
        if (set_full) begin
          full <= 1'b1; en <= 1'b0;
        end
      end
      // (rptr: the host's acknowledgement, g_rptr; a CAPT overrides)
      // ---- 2 generate (the records, prev and the overflow tick: g_st0, g_st1, g_prev, g_ovt)
      if (gen) begin
        snap <= 1'b0;
        if (ovf_now) begin
          ovf <= 1'b1; en <= 1'b0;
        end
      end
      n <= set_full ? 2'd0 : (n_after + {1'b0, push});
      // ---- stop: generation ends; staged records keep draining
      if (stop) begin
        en <= 1'b0; snap <= 1'b0;
      end
      // ---- 3 CAPT overrides (commits never coincide with start/stop)
      if (capt_we) begin
        if (capt_v[31]) begin             // (configuration, pointers, tick: the banks)
          en <= 1'b1; snap <= 1'b1;
          ovf <= 1'b0; full <= 1'b0; wrapped <= 1'b0;
          n <= 2'd0;
        end else begin
          en <= 1'b0; snap <= 1'b0;
        end
      end
    end
  end

`ifdef FORMAL
  // C1 capture never writes outside its region [base, lim) (mod 256; base == lim: all).
  // C2 staging never exceeds 2 records. C3 generation implies a run.
  wire [7:0] f_size = lim - base;
  wire [7:0] f_off  = ptr - base;
  always @(*) if (rst_n) begin
    if (drain_c) assert (f_size == 8'd0 || f_off < f_size);
    // inductive strengthening of C1: while generating or holding records, ptr is in the
    // region (arming sets ptr = base; reaching lim wraps or clears en and the staging)
    if (en || n != 2'd0) assert (f_size == 8'd0 || f_off < f_size);
    assert (n <= 2'd2);
    if (gen) assert (run);
    // C4: in stream mode a drain never makes ptr reach rptr, so it never writes
    // over a record the host has not acknowledged
    if (drain_c && stream) assert (ptr_nxt != rptr);
  end
  always @(*) if (rst_n && run) begin
    cover (ovf);
    cover (full);
    cover (wrapped);
    cover (drain_c && n == 2'd2 && push);
    cover (stream && held && n == 2'd2);          // the host fell behind; staging full
    cover (stream && ovf);
  end
`endif

endmodule
`default_nettype wire
