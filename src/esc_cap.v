/*
 * esc_cap.v - Escapement capture unit (docs/11-SEMANTICS.md v1.2.0 §6.5, §7.6).
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
 * staged records still drain (`active` keeps the port).
 * v1.2: the unit watches one pin bank, chosen by CAPT operand bit 25.
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
    input  wire        busy,        // an LDT/STT commit owns the tape port this cycle
    input  wire        capt_we,     // a CAPT completes at this edge
    input  wire [31:0] capt_v,      // its operand
    output wire        drain,       // write the staged head this cycle
    output wire [7:0]  ptr_o,
    output wire [31:0] head_o,
    output wire        active,      // records staged
    output wire [31:0] status,      // GETC / dbg sel 17
    output wire [31:0] cfg,         // dbg sel 18
    output reg  [31:0] ovf_tick     // dbg sel 19
);
  reg       en, snap, ring, bank, ovf, full, wrapped;
  reg [7:0] mask, base, lim, ptr, prev;
  reg [31:0] st0, st1;
  reg [1:0] n;
  wire [7:0] sync = bank ? {3'd0, psync[12:8]} : psync[7:0];   // the watched bank

  // step 1: drain
  wire       drain_c  = (n != 2'd0) & ~busy & ~start & ~stop;
  wire [7:0] ptr_inc  = ptr + 8'd1;
  wire       hit_lim  = (ptr_inc == lim);
  wire       set_full = drain_c & hit_lim & ~ring;
  wire [1:0] n_after  = n - {1'b0, drain_c};

  // step 2: generate
  wire       changed  = ((sync ^ prev) & mask) != 8'd0;
  wire       epoch    = (now[23:0] == 24'd0);
  wire       gen      = en & run & ~start & ~stop & ~set_full & ~capt_we &
                        (snap | changed | epoch);
  wire       ovf_now  = gen & (n_after == 2'd2);
  wire       push     = gen & ~ovf_now;
  wire [31:0] rec     = {sync, now[23:0]};

  assign drain  = drain_c;
  assign ptr_o  = ptr;
  assign head_o = st0;
  assign active = (n != 2'd0);
  assign status = {en, ovf, full, wrapped, 18'd0, n, ptr};
  assign cfg    = {6'd0, bank, ring, mask, lim, base};
  wire   _unused = &{1'b0, capt_v[30:26]};   // CAPT operand bits 30:26 are ignored (sec. 7.6)

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      en <= 1'b0; snap <= 1'b0; ring <= 1'b0; bank <= 1'b0; ovf <= 1'b0; full <= 1'b0; wrapped <= 1'b0;
      mask <= 8'd0; base <= 8'd0; lim <= 8'd0; ptr <= 8'd0; prev <= 8'd0;
      st0 <= 32'd0; st1 <= 32'd0; n <= 2'd0; ovf_tick <= 32'd0;
    end else if (start) begin
      en <= 1'b0; snap <= 1'b0; ring <= 1'b0; bank <= 1'b0; ovf <= 1'b0; full <= 1'b0; wrapped <= 1'b0;
      mask <= 8'd0; base <= 8'd0; lim <= 8'd0; ptr <= 8'd0; prev <= 8'd0;
      n <= 2'd0; ovf_tick <= 32'd0;
    end else begin
      // ---- 1 drain
      if (drain_c) begin
        st0 <= st1;
        if (hit_lim && ring) begin
          ptr <= base; wrapped <= 1'b1;
        end else begin
          ptr <= ptr_inc;
        end
        if (set_full) begin
          full <= 1'b1; en <= 1'b0;
        end
      end
      // ---- 2 generate
      if (push) begin
        if (n_after == 2'd0) st0 <= rec;
        else                 st1 <= rec;
      end
      if (gen) begin
        prev <= sync; snap <= 1'b0;
        if (ovf_now) begin
          ovf <= 1'b1; ovf_tick <= now; en <= 1'b0;
        end
      end
      n <= set_full ? 2'd0 : (n_after + {1'b0, push});
      // ---- stop: generation ends; staged records keep draining
      if (stop) begin
        en <= 1'b0; snap <= 1'b0;
      end
      // ---- 3 CAPT overrides (commits never coincide with start/stop)
      if (capt_we) begin
        if (capt_v[31]) begin
          en <= 1'b1; snap <= 1'b1;
          mask <= capt_v[23:16]; lim <= capt_v[15:8]; base <= capt_v[7:0];
          ring <= capt_v[24]; bank <= capt_v[25]; ptr <= capt_v[7:0];
          ovf <= 1'b0; full <= 1'b0; wrapped <= 1'b0; ovf_tick <= 32'd0;
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
  end
  always @(*) if (rst_n && run) begin
    cover (ovf);
    cover (full);
    cover (wrapped);
    cover (drain_c && n == 2'd2 && push);
  end
`endif

endmodule
`default_nettype wire
