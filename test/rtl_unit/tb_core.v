// tb_core.v - esc_core + program store (real wrapper + vendored SRAM model).
// The testbench loads the program through ld_* while the core is stopped.
// Pins: uio_in = lb ? (oe ? out : ext) : ext   (per bit; ext models pull-ups /
// an external driver, lb models a wire looped back from the pad).
`default_nettype none
`timescale 1ns / 1ps
module tb_core ();
  reg         clk = 1'b0;
  reg         rst_n = 1'b0;
  reg         start = 1'b0, stop = 1'b0;
  reg  [3:0]  ctx_enable = 4'd0;
  reg  [31:0] entry_pcs = 32'd0;
  reg  [12:0] pin_owned = 13'd0;
  reg  [7:0]  pin_od = 8'd0;
  reg  [3:0]  ctx_bank = 4'd0;
  reg  [4:0]  ib_in = 5'd0;
  reg  [25:0] pin_owner = 26'd0;
  reg         ld_en = 1'b0, ld_we = 1'b0;
  reg  [7:0]  ld_addr = 8'd0;
  reg  [31:0] ld_data = 32'd0;
  reg         lb = 1'b0;
  reg  [7:0]  ext = 8'd0;
  reg  [3:0]  mbx_rx_push = 4'd0, mbx_tx_pop = 4'd0, irq_clear = 4'd0;
  reg  [127:0] mbx_rx_data = 128'd0;
  reg  [1:0]  dbg_sel_ctx = 2'd0;
  reg  [4:0]  dbg_sel = 5'd0;
  reg         tl_we = 1'b0;          // v1.1 tape load (only while the core is stopped)
  reg  [7:0]  tl_addr = 8'd0;
  reg  [31:0] tl_data = 32'd0;
  wire [7:0]  tape_addr;
  wire        tape_re, tape_we, tape_busy;
  wire [31:0] tape_wdata, tape_rdata;

  wire [7:0]  imem_addr;
  wire        imem_re;
  wire [31:0] imem_rdata;
  wire [7:0]  uio_out, uio_oe;
  wire [4:0]  ob_out;
  wire [7:0]  uio_in = lb ? ((uio_oe & uio_out) | (~uio_oe & ext)) : ext;
  wire        running, fault_valid;
  wire [7:0]  ctx_state;
  wire [3:0]  irq_pending, mbx_rx_full, mbx_tx_empty;
  wire [127:0] mbx_tx_data;
  wire        dbg_commit_valid, dbg_post_z, dbg_post_c;
  wire [1:0]  dbg_commit_ctx, dbg_commit_outcome;
  wire [7:0]  dbg_commit_pc, dbg_post_pc;
  wire [31:0] dbg_commit_insn, dbg_post_r0, dbg_post_r1, dbg_post_r2, dbg_post_r3;
  wire [31:0] dbg_post_sh, dbg_post_tmr, dbg_rdata;
  wire [15:0] dbg_post_lc;

  escapement_sram u_sram (
      .clk(clk), .rst_n(rst_n),
      .addr(ld_en ? ld_addr : imem_addr), .re(running ? imem_re : 1'b1), .we(ld_we),
      .wdata(ld_data),
      .rdata(imem_rdata));

  esc_core u_core (
      .clk(clk), .rst_n(rst_n), .start(start), .stop(stop),
      .ctx_enable(ctx_enable), .entry_pcs(entry_pcs), .pin_owned(pin_owned),
      .pin_owner(pin_owner), .pin_od(pin_od), .ctx_bank(ctx_bank),
      .imem_addr(imem_addr), .imem_re(imem_re), .imem_rdata(imem_rdata),
      .uio_in(uio_in), .uio_out(uio_out), .uio_oe(uio_oe), .ib_in(ib_in), .ob_out(ob_out),
      .running(running), .ctx_state(ctx_state),
      .irq_pending(irq_pending), .fault_valid(fault_valid), .irq_clear(irq_clear),
      .mbx_rx_push(mbx_rx_push), .mbx_rx_data(mbx_rx_data), .mbx_rx_full(mbx_rx_full),
      .mbx_tx_pop(mbx_tx_pop), .mbx_tx_data(mbx_tx_data), .mbx_tx_empty(mbx_tx_empty),
      .dbg_commit_valid(dbg_commit_valid), .dbg_commit_ctx(dbg_commit_ctx),
      .dbg_commit_pc(dbg_commit_pc), .dbg_commit_insn(dbg_commit_insn),
      .dbg_commit_outcome(dbg_commit_outcome),
      .dbg_post_r0(dbg_post_r0), .dbg_post_r1(dbg_post_r1), .dbg_post_r2(dbg_post_r2),
      .dbg_post_r3(dbg_post_r3), .dbg_post_sh(dbg_post_sh), .dbg_post_lc(dbg_post_lc),
      .dbg_post_tmr(dbg_post_tmr), .dbg_post_z(dbg_post_z), .dbg_post_c(dbg_post_c),
      .dbg_post_pc(dbg_post_pc),
      .dbg_sel_ctx(dbg_sel_ctx), .dbg_sel(dbg_sel), .dbg_rdata(dbg_rdata),
      .tape_addr(tape_addr), .tape_re(tape_re), .tape_we(tape_we), .tape_wdata(tape_wdata),
      .tape_rdata(tape_rdata), .tape_busy(tape_busy));
  escapement_sram u_tape (
      .clk(clk), .rst_n(rst_n),
      .addr(tl_we ? tl_addr : tape_addr), .re(tape_busy ? tape_re : 1'b1),
      .we(tl_we | (tape_we & tape_busy)),
      .wdata(tl_we ? tl_data : tape_wdata), .rdata(tape_rdata));
endmodule
`default_nettype wire
