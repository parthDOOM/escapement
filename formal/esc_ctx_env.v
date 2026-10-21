/*
 * esc_ctx_env.v - assume-guarantee harness for one context (formal only).
 *
 * Proves "never late" (P2) for ONE esc_ctx for all time, from exactly what esc_core
 * guarantees about it (and proves itself: P6, P7):
 *   - a push happens only on a COMPLETE commit of a RUNNING context, with room in the
 *     FIFO, and its deadline is strictly after the commit tick (`now`) and after the
 *     FIFO tail, within the half range (the admission rules, sec. 6.2);
 *   - `now` starts anywhere at start and advances by exactly 1 per cycle of a run.
 * Everything else (instruction stream, register writes, host strobes, pins, faults,
 * stop) is unconstrained. Together with P6 on the core this gives P2 on the chip.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none
module esc_ctx_env (
    input wire        clk,
    input wire        rst_n,
    input wire        start,
    input wire        stop,
    input wire [7:0]  sync,
    input wire        cfg_enable,
    input wire [7:0]  cfg_entry_pc,
    input wire        cm_ok,
    input wire        cm_waitfull,
    input wire        fault,
    input wire        w_rd_we, w_z_we, w_z, w_c_we, w_c, w_sh_we, w_lc_we, w_tmr_we, w_lr_we,
    input wire        w_halt, w_irq, w_geti, w_push, w_exp_admit, w_rx_pop, w_tx_push,
    input wire [1:0]  w_rd_idx,
    input wire [31:0] w_rd_data, w_sh, w_tmr, w_lee, w_tx_data,
    input wire [15:0] w_lc, w_lead16,
    input wire [7:0]  w_pc, w_lr, w_irq_code,
    input wire [57:0] w_entry,
    input wire        h_rx_push, h_tx_pop, h_irq_clear,
    input wire [31:0] h_rx_data,
    input wire        ld_we,
    input wire [1:0]  ld_idx,
    input wire [31:0] ld_data,
    input wire [7:0]  owned
);
  // ---- the run and the tick counter, as esc_core drives them
  reg        run;
  reg [31:0] now;
  (* anyconst *) reg [31:0] f_now0;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      run <= 1'b0; now <= 32'd0;
    end else if (start) begin
      run <= 1'b1; now <= f_now0;
    end else if (stop) begin
      run <= 1'b0;
    end else if (run) begin
      now <= now + 32'd1;
    end
  end

  wire [1:0]  st;
  wire [2:0]  cnt, cnt_after;
  wire [31:0] tail_dl;
  wire        late;

  esc_ctx u (
      .clk(clk), .rst_n(rst_n), .start_i(start), .stop_i(stop & ~start), .run(run),
      .now(now), .sync(sync), .cfg_enable(cfg_enable), .cfg_entry_pc(cfg_entry_pc),
      .cm_ok(cm_ok), .cm_waitfull(cm_waitfull), .fault(fault),
      .w_rd_we(w_rd_we), .w_rd_idx(w_rd_idx), .w_rd_data(w_rd_data), .w_z_we(w_z_we),
      .w_z(w_z), .w_c_we(w_c_we), .w_c(w_c), .w_sh_we(w_sh_we), .w_sh(w_sh),
      .w_lc_we(w_lc_we), .w_lc(w_lc), .w_tmr_we(w_tmr_we), .w_tmr(w_tmr), .w_pc(w_pc),
      .w_lr_we(w_lr_we), .w_lr(w_lr), .w_halt(w_halt), .w_irq(w_irq),
      .w_irq_code(w_irq_code), .w_geti(w_geti), .w_push(w_push), .w_entry(w_entry),
      .w_lead16(w_lead16), .w_exp_admit(w_exp_admit), .w_lee(w_lee), .w_rx_pop(w_rx_pop),
      .w_tx_push(w_tx_push), .w_tx_data(w_tx_data),
      .h_rx_push(h_rx_push), .h_rx_data(h_rx_data), .h_tx_pop(h_tx_pop),
      .h_irq_clear(h_irq_clear),
      .ld_we(ld_we), .ld_idx(ld_idx), .ld_data(ld_data),
      // the latch banks' write data (D-40) is used only by the standard-cell build
      .q_wr(32'd0), .q_tmr(32'd0), .q_entry(58'd0), .q_ld(32'd0), .q_ld_we(1'b0),
      .o_r0(), .o_r1(), .o_r2(), .o_r3(), .o_sh(), .o_lc(), .o_tmr(), .o_pc(), .o_lr(),
      .o_z(), .o_c(), .o_state(st), .o_ish(), .o_cnt(cnt), .o_cnt_after(cnt_after),
      .o_tail_dl(tail_dl), .o_late(late), .o_act(), .o_act_after(), .o_pass(), .o_fail(),
      .o_failed(), .o_ff_tick(), .o_ff_obs(), .o_ff_mask(), .o_ff_level(), .o_exp_seen(),
      .o_lee(), .o_min_lead(), .o_max_occ(), .o_wait_full(), .o_irq_p(), .o_irq_code(),
      .o_rx_empty(), .o_rx_full(), .o_rx_head(), .o_tx_empty(), .o_tx_full(), .o_tx_head(),
      .o_drv_valid(), .o_drv_mask(), .o_drv_val(), .o_drv_oe(), .o_has_smp(),
      .f_owned(owned));

  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;
  always @(*) if (!f_past) assume (!rst_n);

  // ---- what esc_core guarantees (its P6/P7 and the commit qualification)
  wire [31:0] d      = w_entry[55:24];
  wire [31:0] d_now  = d - now;
  wire [31:0] d_tail = d - tail_dl;
  always @(*) begin
    // No fault on a commit, except lateness itself: the core proves only that much (its
    // P8 under ESC_CORE_PDR), and `late` is what this file proves never happens. `late`
    // depends on state alone, never on this cycle's inputs, so the exception cannot
    // hide a late head.
    if (cm_ok) assume (run && st == 2'd1 && !start && !stop && (!fault || late));
    if (cm_waitfull) assume (run && st == 2'd1 && !start && !stop);
    if (fault) assume (run && !start && !stop);
    if (cm_ok && w_halt) assume (cnt_after == 3'd0 && !w_push);       // HALT waits (sec. 7.2)
    if (cm_ok && w_push) begin
      assume (cnt_after != 3'd4);
      assume (d_now != 32'd0 && !d_now[31]);                        // ahead(d, c)
      if (cnt_after != 3'd0) assume (d_tail != 32'd0 && !d_tail[31]); // ahead(d, tail)
      if (w_entry[57:56] == 2'd1) assume ((w_entry[23:16] & ~owned) == 8'd0); // OWNER
    end
    if (ld_we) assume (!start);
  end
  always @(posedge clk) if (f_past && run) assume ($stable(owned));

  // ---- the guarantee
  always @(*) if (rst_n) begin
    assert (!late);                                                    // P2 for this context
    if (!run) assert (st != 2'd1);       // lemma: stop turns RUNNING into HALTED
  end
endmodule
`default_nettype wire
