/*
 * esc_line_env.v - assume-guarantee harness for the line unit (formal only, v1.6).
 *
 * Proves esc_line's own assertions (the registered transmit bit, A2, and the registered
 * pin writes, A4, against their definitions; the CONFIG slot lemma; and the lemmas that
 * make them inductive: a configuration in use is valid, the transmit timer's range, a
 * packet's state before its first boundary, when a stream word is read) for all time, by
 * k-induction (`sby -f esc_line.sby kind`, seconds), from what esc_core guarantees about
 * the unit's inputs:
 *   - `now` starts anywhere at each start and advances by exactly 1 per cycle of a run;
 *   - at most one op commits at an edge, only in a run, and only in its context's commit
 *     cycle (now[1:0] == ctx - 1, sec. 3); every op but a CONFIG is the owner's, and a
 *     CONFIG is the owner's or takes an unowned unit (sec. 7.7 admission);
 *   - SEND, RECV, CRC and CONFIG commit only while the unit is not engaged; RECV and CRC
 *     only when configured, SEND only with claimed pins (the admission rules);
 *   - stop at an edge kills the unit (the core ORs it into kill), and so does a CONFIG
 *     completing invalid, which faults its owner at that edge;
 *   - the first cycle is in reset.
 * Everything else (operands, tape data, the owner's inputs and pins, LINE retirements,
 * HALT completions, faults) is unconstrained.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none
module esc_line_env (
    input wire        clk,
    input wire        rst_n,
    input wire        start,
    input wire        stop,
    input wire [7:0]  view,
    input wire [7:0]  owns,
    input wire        op_send, op_off, op_recv, op_crc, op_cfg,
    input wire [1:0]  op_ctx,
    input wire [29:0] op_v,
    input wire        line_ret,
    input wire        halt_done,
    input wire        fault,
    input wire [31:0] rdata
);
  reg        run;
  reg [31:0] now;
  (* anyseq *) reg [31:0] f_now0;                // any value at each start
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

  wire        owned, conf, engaged, busy, flt_owner, flt_config, send_rd;
  wire        port, port_we, drv_oe;
  wire [1:0]  owner;
  wire [2:0]  mode;
  wire [7:0]  claimed, port_addr, rel_m, drv_m, drv_val;
  wire [31:0] status, e, port_wdata;
  // esc_core's kill: stop, or a fault of the owner, which a CONFIG completing invalid (or with
  // pins its owner does not hold) raises at that edge (lu_cfgf, fault_k)
  wire kill = stop | (fault & run) | (~start & ~stop & owned & (flt_owner | flt_config));
  esc_line dut (
      .clk(clk), .rst_n(rst_n), .start(start), .now(now), .view(view), .owns(owns),
      .op_send(op_send), .op_off(op_off), .op_recv(op_recv), .op_crc(op_crc),
      .op_cfg(op_cfg), .op_ctx(op_ctx), .op_v(op_v), .line_ret(line_ret),
      .halt_done(halt_done), .kill(kill), .rdata(rdata),
      .owned(owned), .owner(owner), .conf(conf), .claimed(claimed), .engaged(engaged),
      .busy(busy), .status(status), .e(e), .mode(mode), .flt_owner(flt_owner),
      .flt_config(flt_config), .send_rd(send_rd), .port(port), .port_we(port_we),
      .port_addr(port_addr), .port_wdata(port_wdata), .rel_m(rel_m), .drv_m(drv_m),
      .drv_val(drv_val), .drv_oe(drv_oe));

  wire anyop = op_send | op_off | op_recv | op_crc | op_cfg;
  reg f_past_valid = 1'b0;                     // the chip starts in reset, as in esc_core
  always @(posedge clk) f_past_valid <= 1'b1;
  always @(*) begin
    if (!f_past_valid) assume (!rst_n);
    if (!rst_n) assume (!start);
    assume ({2'd0, op_send} + op_off + op_recv + op_crc + op_cfg <= 3'd1);
    if (anyop) begin
      assume (run && !start && !stop && !(fault & run));
      assume (now[1:0] == op_ctx - 2'd1);
      if (op_cfg) assume (!owned || op_ctx == owner);
      else assume (owned && op_ctx == owner);
    end
    if (op_send | op_recv | op_crc | op_cfg) assume (!engaged);
    if (op_recv | op_crc) assume (conf);
    if (op_send) assume (claimed != 8'd0);
  end
  // lemmas for the induction: a busy unit belongs to a run (a stop kills it, at the edge
  // that stops the run), and pins are claimed only under a configuration
  always @(*) if (rst_n && mode != 3'd0) assert (run);
  always @(*) if (rst_n && claimed != 8'd0) assert (conf);
endmodule
