/*
 * esc_ctx.v - Escapement per-context state: architectural registers, event FIFO,
 * expectation checker, telemetry, IRQ and host mailboxes (docs/11-SEMANTICS.md v1.1.0).
 *
 * Copyright (c) 2026 Escapement contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * One instance per context. The shared commit stage in esc_core evaluates the
 * committing instruction and drives the w_* write port (broadcast to all
 * contexts); `cm_ok` qualifies it for this context and means outcome COMPLETE.
 * Everything a commit observes is exported combinationally from the state
 * during the current cycle (o_*), so the commit stage can decide on the
 * current cycle's state (sec. 3 "What a commit sees").
 *
 * Event engine (sec. 6.3/6.4) runs every edge, independently per context:
 *   due   : head deadline == now           -> retire at this edge
 *   late  : reached(now, head) && != now   -> LATE_HEAD (reported to esc_core)
 * Retirement and checker evaluation are suppressed by `fault` or `stop_i`
 * at the same edge (sec. 6.3, 8, 9).
 *
 * FIFO entry layout (58 bits): [57:56] kind (0 LINE, 1 DRIVE, 2 EXPECT, 3 SAMPLE),
 * [55:24] deadline, [23:0] payload = instruction bits [23:0] of the queued
 * instruction (DRIVE: mask/value/oe with the effective value; EXPECT:
 * mask/level/mode/window; SAMPLE: pin [18:16], dir [15]; LINE, v1.4: unused, the line
 * unit keeps the SEND command, and the entry only starts it at its deadline).
 */
`default_nettype none
module esc_ctx (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start_i,       // run initialisation at this edge (wins over all)
    input  wire        stop_i,        // stop at this edge (already masked by start)
    input  wire        run,           // run flag during this cycle
    input  wire [31:0] now,
    input  wire [7:0]  sync,          // synchronised input vector during this cycle
    input  wire        cfg_enable,
    input  wire [7:0]  cfg_entry_pc,

    // ---- commit write port (broadcast data, per-context qualifiers) ----
    input  wire        cm_ok,         // COMPLETE commit of this context at this edge
    input  wire        cm_waitfull,   // WAIT caused by a full FIFO (sec. 7.4)
    input  wire        fault,         // this context faults at this edge
    input  wire        w_rd_we,
    input  wire [1:0]  w_rd_idx,
    input  wire [31:0] w_rd_data,
    input  wire        w_z_we,
    input  wire        w_z,
    input  wire        w_c_we,
    input  wire        w_c,
    input  wire        w_sh_we,
    input  wire [31:0] w_sh,
    input  wire        w_lc_we,
    input  wire [15:0] w_lc,
    input  wire        w_tmr_we,
    input  wire [31:0] w_tmr,
    input  wire [7:0]  w_pc,          // next PC (applied on COMPLETE)
    input  wire        w_lr_we,
    input  wire [7:0]  w_lr,
    input  wire        w_halt,
    input  wire        w_irq,
    input  wire [7:0]  w_irq_code,
    input  wire        w_geti,        // clear ish at this edge
    input  wire        w_push,        // append an event entry
    input  wire [57:0] w_entry,
    input  wire [15:0] w_lead16,      // min(lead, 0xFFFF)
    input  wire        w_exp_admit,   // EXPECT admitted: update EXPECT history
    input  wire [31:0] w_lee,         // d + window
    input  wire        w_rx_pop,
    input  wire        w_tx_push,
    input  wire [31:0] w_tx_data,
    // ---- v1.1 deferred LDT write (the LDT committed at the previous edge, sec. 7.6)
    input  wire        ld_we,
    input  wire [1:0]  ld_idx,
    input  wire [31:0] ld_data,

    // ---- host mailbox side ----
    input  wire        h_rx_push,
    input  wire [31:0] h_rx_data,
    input  wire        h_tx_pop,
    input  wire        h_irq_clear,   // host IRQ clear (v1.0.1); a same-edge IRQ commit wins

    // ---- state outputs ----
    output wire [31:0] o_r0,
    output wire [31:0] o_r1,
    output wire [31:0] o_r2,
    output wire [31:0] o_r3,
    output wire [31:0] o_sh,
    output reg  [15:0] o_lc,
    output wire [31:0] o_tmr,
    output reg  [7:0]  o_pc,
    output reg  [7:0]  o_lr,
    output reg         o_z,
    output reg         o_c,
    output reg  [1:0]  o_state,
    output wire [31:0] o_ish,
    output reg  [2:0]  o_cnt,
    output wire [2:0]  o_cnt_after,   // FIFO occupancy after this edge's retirement
    output reg  [31:0] o_tail_dl,
    output wire        o_late,
    output reg         o_act,         // checker active during this cycle
    output wire        o_act_after,   // checker active after this edge (fault-free)
    output reg  [7:0]  o_pass,
    output reg  [7:0]  o_fail,
    output reg         o_failed,
    output wire [31:0] o_ff_tick,
    output wire [7:0]  o_ff_obs,
    output wire [7:0]  o_ff_mask,
    output wire [7:0]  o_ff_level,
    output reg         o_exp_seen,
    output wire [31:0] o_lee,
    output reg  [15:0] o_min_lead,
    output reg  [2:0]  o_max_occ,
    output reg  [15:0] o_wait_full,
    output reg         o_irq_p,
    output reg  [7:0]  o_irq_code,
    output wire        o_rx_empty,
    output wire        o_rx_full,
    output wire [31:0] o_rx_head,
    output wire        o_tx_empty,
    output wire        o_tx_full,
    output wire [31:0] o_tx_head,
    output wire        o_drv_valid,   // DRIVE retiring at this edge (after suppression)
    output wire [7:0]  o_drv_mask,
    output wire [7:0]  o_drv_val,
    output wire [7:0]  o_drv_oe,
    output wire        o_has_smp,     // v1.1: a SAMPLE entry is in the FIFO (sec. 7.3 GETI)
    output wire        o_line_ret     // v1.4: a LINE entry retires at this edge (after suppression)
`ifdef FORMAL
    , input wire [7:0] f_owned        // formal only: pins this context owns (for H4)
`endif
);

  localparam [1:0] ST_IDLE = 2'd0, ST_RUN = 2'd1, ST_HALT = 2'd2, ST_FAULT = 2'd3;
  localparam [1:0] K_LINE = 2'd0, K_DRIVE = 2'd1, K_EXPECT = 2'd2, K_SAMPLE = 2'd3;

  // ------------------------------------------------------------------ FIFO
  wire [57:0] ent0, ent1, ent2, ent3;   // clock-gated banks, below
  reg [1:0]  head;

  reg [57:0] h;
  always @* begin
    case (head)
      2'd0:    h = ent0;
      2'd1:    h = ent1;
      2'd2:    h = ent2;
      default: h = ent3;
    endcase
  end

  wire [1:0] tail_idx = head + o_cnt[1:0] - 2'd1;
  wire [1:0] wr_idx   = head + o_cnt[1:0];
  always @* begin
    case (tail_idx)
      2'd0:    o_tail_dl = ent0[55:24];
      2'd1:    o_tail_dl = ent1[55:24];
      2'd2:    o_tail_dl = ent2[55:24];
      default: o_tail_dl = ent3[55:24];
    endcase
  end

  // v1.1 GETI: any valid entry (head .. head+cnt-1) of kind SAMPLE during this cycle
  wire [1:0] rel0 = 2'd0 - head, rel1 = 2'd1 - head, rel2 = 2'd2 - head, rel3 = 2'd3 - head;
  assign o_has_smp = ((ent0[57:56] == K_SAMPLE) & ({1'b0, rel0} < o_cnt)) |
                     ((ent1[57:56] == K_SAMPLE) & ({1'b0, rel1} < o_cnt)) |
                     ((ent2[57:56] == K_SAMPLE) & ({1'b0, rel2} < o_cnt)) |
                     ((ent3[57:56] == K_SAMPLE) & ({1'b0, rel3} < o_cnt));

  wire [1:0]  h_kind = h[57:56];
  wire [31:0] h_dl   = h[55:24];
  wire [23:0] h_pl   = h[23:0];

  wire        nonempty  = (o_cnt != 3'd0);
  wire [31:0] late_diff = now - h_dl;
  // sec. 6.3: retire when head deadline == now; LATE_HEAD when reached and !=.
  wire        due       = run & nonempty & (h_dl == now);
  assign      o_late    = run & nonempty & (late_diff != 32'd0) & ~late_diff[31];
  assign      o_cnt_after = o_cnt - {2'b0, due};

  wire kill = fault | stop_i;

  // ------------------------------------------------------------------ checker
  reg [7:0] ck_mask, ck_level;
  reg       ck_mode;
  reg [6:0] ck_rem;               // (d + w) - u for the tick u evaluated this cycle

  wire       starting = due & (h_kind == K_EXPECT);
  wire       eval_en  = starting | (run & o_act);
  wire [7:0] e_mask   = starting ? h_pl[23:16] : ck_mask;
  wire [7:0] e_level  = starting ? h_pl[15:8]  : ck_level;
  wire       e_mode   = starting ? h_pl[7]     : ck_mode;
  wire       e_last   = starting ? (h_pl[6:0] == 7'd0) : (ck_rem == 7'd0);
  wire       m        = ((sync & e_mask) == e_level);
  // HOLD (mode 0): fail on !m, pass on m at the last tick.
  // ARRIVE (mode 1): pass on m, fail on !m at the last tick.
  wire       ck_pass  = eval_en & (e_mode ? m : (m & e_last));
  wire       ck_fail  = eval_en & (e_mode ? (~m & e_last) : ~m);
  assign     o_act_after = eval_en & ~ck_pass & ~ck_fail;

  // ------------------------------------------------------------------ retirement
  wire       ret_ok     = due & ~kill;
  assign     o_drv_valid = ret_ok & (h_kind == K_DRIVE);
  assign     o_drv_mask  = h_pl[23:16];
  assign     o_drv_val   = h_pl[15:8];
  assign     o_drv_oe    = h_pl[7:0];
  wire       sample_ret = ret_ok & (h_kind == K_SAMPLE);
  assign     o_line_ret = ret_ok & (h_kind == K_LINE);
  wire       s_bit      = sync[h_pl[18:16]];
  wire [31:0] ish_base  = (cm_ok & w_geti) ? 32'd0 : o_ish;
  wire [31:0] ish_next  = ~sample_ret ? ish_base :
                          h_pl[15] ? {ish_base[30:0], s_bit}    // MSB-first
                                   : {s_bit, ish_base[31:1]};   // LSB-first

  wire       push = cm_ok & w_push;

  // ------------------------------------------------------------------ mailboxes
  wire [31:0] rx0, rx1, tx0, tx1;       // clock-gated banks, below
  reg [1:0]  rx_cnt, tx_cnt;
  assign o_rx_empty = (rx_cnt == 2'd0);
  assign o_rx_full  = (rx_cnt == 2'd2);
  assign o_rx_head  = rx0;
  assign o_tx_empty = (tx_cnt == 2'd0);
  assign o_tx_full  = (tx_cnt == 2'd2);
  assign o_tx_head  = tx0;

  // RX: host pushes (ignored when full during this cycle), context pops.
  wire       rx_popped = cm_ok & w_rx_pop;
  wire       rx_pushed = h_rx_push & ~o_rx_full;
  wire [1:0] rx_cnt_p  = rx_cnt - {1'b0, rx_popped};
  // TX: context pushes, host pops (ignored when empty during this cycle).
  wire       tx_popped = h_tx_pop & ~o_tx_empty;
  wire       tx_pushed = cm_ok & w_tx_push;
  wire [1:0] tx_cnt_p  = tx_cnt - {1'b0, tx_popped};

  // ------------------------------------------------------------------ registers
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      o_lc <= 16'd0; o_pc <= 8'd0; o_lr <= 8'd0;
      o_z <= 1'b0; o_c <= 1'b0; o_state <= ST_IDLE;
      head <= 2'd0; o_cnt <= 3'd0;
      o_act <= 1'b0; ck_mask <= 8'd0; ck_level <= 8'd0; ck_mode <= 1'b0; ck_rem <= 7'd0;
      o_pass <= 8'd0; o_fail <= 8'd0; o_failed <= 1'b0;
      o_exp_seen <= 1'b0;
      o_min_lead <= 16'hFFFF; o_max_occ <= 3'd0; o_wait_full <= 16'd0;
      o_irq_p <= 1'b0; o_irq_code <= 8'd0;
      rx_cnt <= 2'd0; tx_cnt <= 2'd0;
    end else if (start_i) begin
      // sec. 9 Start
      o_lc <= 16'd0; o_pc <= cfg_entry_pc; o_lr <= 8'd0;
      o_z <= 1'b0; o_c <= 1'b0; o_state <= cfg_enable ? ST_RUN : ST_IDLE;
      head <= 2'd0; o_cnt <= 3'd0;
      o_act <= 1'b0; ck_rem <= 7'd0;
      o_pass <= 8'd0; o_fail <= 8'd0; o_failed <= 1'b0;
      o_exp_seen <= 1'b0;
      o_min_lead <= 16'hFFFF; o_max_occ <= 3'd0; o_wait_full <= 16'd0;
      o_irq_p <= 1'b0; o_irq_code <= 8'd0;
      rx_cnt <= 2'd0; tx_cnt <= 2'd0;
    end else begin
      // ---- host IRQ clear (overridden below by an IRQ commit at the same edge)
      if (h_irq_clear) o_irq_p <= 1'b0;

      // ---- commit effects (cm_ok implies no fault and no stop at this edge)
      if (cm_ok) begin
        if (w_z_we)   o_z   <= w_z;
        if (w_c_we)   o_c   <= w_c;
        if (w_lc_we)  o_lc  <= w_lc;
        if (w_lr_we)  o_lr  <= w_lr;
        o_pc <= w_pc;
        if (w_halt) o_state <= ST_HALT;
        if (w_irq) begin
          o_irq_p    <= 1'b1;
          o_irq_code <= w_irq_code;
        end
        if (w_exp_admit) o_exp_seen <= 1'b1;
        if (w_push) begin
          if (w_lead16 < o_min_lead) o_min_lead <= w_lead16;
          if ((o_cnt_after + 3'd1) > o_max_occ) o_max_occ <= o_cnt_after + 3'd1;
        end
      end
      if (cm_waitfull && o_wait_full != 16'hFFFF) o_wait_full <= o_wait_full + 16'd1;

      // ---- state transitions that override the commit (sec. 8, 9)
      if (stop_i) begin
        if (o_state == ST_RUN) o_state <= ST_HALT;
      end else if (fault) begin
        o_state <= ST_FAULT;
      end

      // ---- FIFO
      if (kill) begin
        o_cnt <= 3'd0;
      end else begin
        o_cnt <= o_cnt - {2'b0, due} + {2'b0, push};
        if (due) head <= head + 2'd1;
      end

      // ---- checker
      if (kill) begin
        o_act <= 1'b0;
      end else begin
        o_act <= o_act_after;
        if (starting) begin
          ck_mask  <= h_pl[23:16];
          ck_level <= h_pl[15:8];
          ck_mode  <= h_pl[7];
          ck_rem   <= h_pl[6:0] - 7'd1;
        end else if (o_act) begin
          ck_rem <= ck_rem - 7'd1;
        end
        if (ck_pass && o_pass != 8'hFF) o_pass <= o_pass + 8'd1;
        if (ck_fail) begin
          if (o_fail != 8'hFF) o_fail <= o_fail + 8'd1;
          if (!o_failed) o_failed <= 1'b1;
        end
      end

      // ---- mailboxes
      rx_cnt <= rx_cnt_p + {1'b0, rx_pushed};

      tx_cnt <= tx_cnt_p + {1'b0, tx_pushed};
    end
  end

  // ------------------------------------------------------------------ clock-gated banks
  // D-29: registers written rarely get their own gated clock (esc_greg.v) instead of an
  // enable mux. Each enable is exactly the condition under which the always block above
  // used to assign the bank (priority: rst_n, then start_i, then the edge's effects), and
  // each d is the value it assigned; when en is low the bank holds, as before.
  wire       st   = start_i;
  wire       live = ~start_i;                    // the always block's final else-branch
  wire       ldk0 = ld_we & (ld_idx == 2'd0), ldk1 = ld_we & (ld_idx == 2'd1);
  wire       ldk2 = ld_we & (ld_idx == 2'd2), ldk3 = ld_we & (ld_idx == 2'd3);
  wire       rdw  = cm_ok & w_rd_we;
  // A deferred LDT write never meets this context's own commit; if it did, LDT wins,
  // as the later assignment did.
  esc_greg #(32) g_r0 (.clk(clk), .rst_n(rst_n),
      .en(st | (live & ((rdw & (w_rd_idx == 2'd0)) | ldk0))),
      .d(st ? 32'd0 : ldk0 ? ld_data : w_rd_data), .q(o_r0));
  esc_greg #(32) g_r1 (.clk(clk), .rst_n(rst_n),
      .en(st | (live & ((rdw & (w_rd_idx == 2'd1)) | ldk1))),
      .d(st ? 32'd0 : ldk1 ? ld_data : w_rd_data), .q(o_r1));
  esc_greg #(32) g_r2 (.clk(clk), .rst_n(rst_n),
      .en(st | (live & ((rdw & (w_rd_idx == 2'd2)) | ldk2))),
      .d(st ? 32'd0 : ldk2 ? ld_data : w_rd_data), .q(o_r2));
  esc_greg #(32) g_r3 (.clk(clk), .rst_n(rst_n),
      .en(st | (live & ((rdw & (w_rd_idx == 2'd3)) | ldk3))),
      .d(st ? 32'd0 : ldk3 ? ld_data : w_rd_data), .q(o_r3));
  esc_greg #(32) g_sh (.clk(clk), .rst_n(rst_n), .en(st | (live & cm_ok & w_sh_we)),
      .d(st ? 32'd0 : w_sh), .q(o_sh));
  esc_greg #(32) g_tmr (.clk(clk), .rst_n(rst_n), .en(st | (live & cm_ok & w_tmr_we)),
      .d(st ? 32'd0 : w_tmr), .q(o_tmr));
  esc_greg #(32) g_lee (.clk(clk), .rst_n(rst_n), .en(st | (live & cm_ok & w_exp_admit)),
      .d(st ? 32'd0 : w_lee), .q(o_lee));
  // ish: the block assigned ish_next every live edge; it differs from o_ish only on a
  // SAMPLE retirement or a GETI clear.
  esc_greg #(32) g_ish (.clk(clk), .rst_n(rst_n),
      .en(st | (live & (sample_ret | (cm_ok & w_geti)))),
      .d(st ? 32'd0 : ish_next), .q(o_ish));
  esc_greg #(56) g_ff (.clk(clk), .rst_n(rst_n),
      .en(st | (live & ~kill & ck_fail & ~o_failed)),
      .d(st ? 56'd0 : {now, sync, e_mask, e_level}),
      .q({o_ff_tick, o_ff_obs, o_ff_mask, o_ff_level}));

  // FIFO entries: written only by a push (never by start_i, which only empties the FIFO).
  wire ent_w = live & ~kill & push;
  esc_greg #(58) g_ent0 (.clk(clk), .rst_n(rst_n), .en(ent_w & (wr_idx == 2'd0)),
      .d(w_entry), .q(ent0));
  esc_greg #(58) g_ent1 (.clk(clk), .rst_n(rst_n), .en(ent_w & (wr_idx == 2'd1)),
      .d(w_entry), .q(ent1));
  esc_greg #(58) g_ent2 (.clk(clk), .rst_n(rst_n), .en(ent_w & (wr_idx == 2'd2)),
      .d(w_entry), .q(ent2));
  esc_greg #(58) g_ent3 (.clk(clk), .rst_n(rst_n), .en(ent_w & (wr_idx == 2'd3)),
      .d(w_entry), .q(ent3));

  // Mailboxes: start_i resets the counts only; the words hold.
  wire rx_w0 = rx_pushed & (rx_cnt_p == 2'd0), tx_w0 = tx_pushed & (tx_cnt_p == 2'd0);
  esc_greg #(32) g_rx0 (.clk(clk), .rst_n(rst_n), .en(live & (rx_popped | rx_w0)),
      .d(rx_w0 ? h_rx_data : rx1), .q(rx0));
  esc_greg #(32) g_rx1 (.clk(clk), .rst_n(rst_n), .en(live & rx_pushed & ~(rx_cnt_p == 2'd0)),
      .d(h_rx_data), .q(rx1));
  esc_greg #(32) g_tx0 (.clk(clk), .rst_n(rst_n), .en(live & (tx_popped | tx_w0)),
      .d(tx_w0 ? w_tx_data : tx1), .q(tx0));
  esc_greg #(32) g_tx1 (.clk(clk), .rst_n(rst_n), .en(live & tx_pushed & ~(tx_cnt_p == 2'd0)),
      .d(w_tx_data), .q(tx1));

`ifdef FORMAL
  // Inductive strengthening for P2 "never late" (esc_core.v), so it can be proved for
  // all time by k-induction (formal/esc_core.sby task `kind`):
  //  H1 every queued deadline is at or after now (within the half range);
  //  H2 queued deadlines strictly increase from head to tail (within the half range);
  //  H3 only a RUNNING context has queued events.
  // With H1+H2 only the head can reach lead 0, and it retires at that edge; admission
  // (P6) keeps both for a new entry; time moving on keeps every lead >= 0.
  wire [57:0] f_ent [0:3];
  assign f_ent[0] = ent0; assign f_ent[1] = ent1; assign f_ent[2] = ent2; assign f_ent[3] = ent3;
  integer fi;
  reg [1:0]  f_slot, f_next;
  reg [31:0] f_lead, f_step;
  // H3 and H4 are proved here and again in the core's PDR task, where they are the
  // lemmas for "a faulted context's pins stay released" (lemma group `faulted`). H1 and
  // H2 are proved only in formal/esc_ctx.sby; the core's PDR task skips them.
  always @(*) if (rst_n) begin
    if (o_state != ST_RUN) assert (o_cnt == 3'd0);           // H3  lemma-group: faulted
    for (fi = 0; fi < 4; fi = fi + 1) begin
      f_slot = head + fi[1:0];
      f_next = head + fi[1:0] + 2'd1;
      f_lead = f_ent[f_slot][55:24] - now;
      f_step = f_ent[f_next][55:24] - f_ent[f_slot][55:24];
`ifndef ESC_CORE_PDR
      if (fi < o_cnt) assert (!f_lead[31]);                                 // H1
      if (fi + 1 < o_cnt) assert (f_step != 32'd0 && !f_step[31]);          // H2
`endif
      // H4 a queued DRIVE only touches pins this context owns (admission OWNER rule)
      if (fi < o_cnt && f_ent[f_slot][57:56] == K_DRIVE)
        assert ((f_ent[f_slot][23:16] & ~f_owned) == 8'd0);  // H4  lemma-group: faulted
    end
  end
`ifndef ESC_CORE_PDR
  always @(*) if (rst_n) assert (o_cnt <= 3'd4);
`endif
`endif

endmodule
`default_nettype wire
