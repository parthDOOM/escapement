/*
 * Co-simulation top: esc_core + the real program-store and tape wrappers (SRAM
 * macro models). v1.1: load_space selects the tape (1) or the program (0).
 * Written from the port list in docs/11-SEMANTICS.md §10 only.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none
`timescale 1ns / 1ps

module esc_tb (
    input  wire         clk,
    input  wire         rst_n,
    input  wire         start,
    input  wire         stop,
    input  wire [3:0]   ctx_enable,
    input  wire [31:0]  entry_pcs,
    input  wire [7:0]   pin_owned,
    input  wire [15:0]  pin_owner,
    input  wire [7:0]   pin_od,
    // program load (only while not running)
    input  wire         load_we,
    input  wire         load_space,
    input  wire [7:0]   load_addr,
    input  wire [31:0]  load_data,
    // pins
    input  wire [7:0]   uio_in,
    output wire [7:0]   uio_out,
    output wire [7:0]   uio_oe,
    // status
    output wire         running,
    output wire [7:0]   ctx_state,
    output wire [3:0]   irq_pending,
    output wire         fault_valid,
    input  wire [3:0]   irq_clear,
    // mailboxes (host side)
    input  wire [3:0]   mbx_rx_push,
    input  wire [127:0] mbx_rx_data,
    output wire [3:0]   mbx_rx_full,
    input  wire [3:0]   mbx_tx_pop,
    output wire [127:0] mbx_tx_data,
    output wire [3:0]   mbx_tx_empty,
    // observation
    output wire         dbg_commit_valid,
    output wire [1:0]   dbg_commit_ctx,
    output wire [7:0]   dbg_commit_pc,
    output wire [31:0]  dbg_commit_insn,
    output wire [1:0]   dbg_commit_outcome,
    output wire [31:0]  dbg_post_r0,
    output wire [31:0]  dbg_post_r1,
    output wire [31:0]  dbg_post_r2,
    output wire [31:0]  dbg_post_r3,
    output wire [31:0]  dbg_post_sh,
    output wire [15:0]  dbg_post_lc,
    output wire [31:0]  dbg_post_tmr,
    output wire         dbg_post_z,
    output wire         dbg_post_c,
    output wire [7:0]   dbg_post_pc,
    input  wire [1:0]   dbg_sel_ctx,
    input  wire [4:0]   dbg_sel,
    output wire [31:0]  dbg_rdata
);
  // +nodump (COCOTB_PLUSARGS) skips the waveform, so parallel runs sharing this
  // directory do not write the same file (test/cosim/run_parallel.py, mutation runner).
  initial if (!$test$plusargs("nodump")) begin
    $dumpfile("esc_tb.fst");
    $dumpvars(0, esc_tb);
  end

  wire [7:0]  imem_addr;
  wire        imem_re;
  wire [31:0] imem_rdata;

  escapement_sram u_sram (
      .clk  (clk),
      .rst_n(rst_n),
      .addr ((load_we & ~load_space) ? load_addr : imem_addr),
      .re   (running ? imem_re : 1'b1),          // as project.v while running (D-29)
      .we   (load_we & ~load_space),
      .wdata(load_data),
      .rdata(imem_rdata)
  );

  wire [7:0]  tape_addr;
  wire        tape_re, tape_we, tape_busy;
  wire [31:0] tape_wdata, tape_rdata;
  wire        tload = load_we & load_space & ~tape_busy;
  escapement_sram u_tape (
      .clk  (clk),
      .rst_n(rst_n),
      .addr (tload ? load_addr : tape_addr),
      .re   (tape_busy ? tape_re : 1'b1),          // as project.v while running (D-29)
      .we   (tload | (tape_we & tape_busy)),
      .wdata(tload ? load_data : tape_wdata),
      .rdata(tape_rdata)
  );

  esc_core u_core (
      .clk(clk), .rst_n(rst_n), .start(start), .stop(stop),
      .ctx_enable(ctx_enable), .entry_pcs(entry_pcs),
      .pin_owned(pin_owned), .pin_owner(pin_owner), .pin_od(pin_od),
      .imem_addr(imem_addr), .imem_re(imem_re), .imem_rdata(imem_rdata),
      .uio_in(uio_in), .uio_out(uio_out), .uio_oe(uio_oe),
      .running(running), .ctx_state(ctx_state),
      .irq_pending(irq_pending), .fault_valid(fault_valid), .irq_clear(irq_clear),
      .mbx_rx_push(mbx_rx_push), .mbx_rx_data(mbx_rx_data), .mbx_rx_full(mbx_rx_full),
      .mbx_tx_pop(mbx_tx_pop), .mbx_tx_data(mbx_tx_data), .mbx_tx_empty(mbx_tx_empty),
      .dbg_commit_valid(dbg_commit_valid), .dbg_commit_ctx(dbg_commit_ctx),
      .dbg_commit_pc(dbg_commit_pc), .dbg_commit_insn(dbg_commit_insn),
      .dbg_commit_outcome(dbg_commit_outcome),
      .dbg_post_r0(dbg_post_r0), .dbg_post_r1(dbg_post_r1),
      .dbg_post_r2(dbg_post_r2), .dbg_post_r3(dbg_post_r3),
      .dbg_post_sh(dbg_post_sh), .dbg_post_lc(dbg_post_lc), .dbg_post_tmr(dbg_post_tmr),
      .dbg_post_z(dbg_post_z), .dbg_post_c(dbg_post_c), .dbg_post_pc(dbg_post_pc),
      .dbg_sel_ctx(dbg_sel_ctx), .dbg_sel(dbg_sel), .dbg_rdata(dbg_rdata),
      .tape_addr(tape_addr), .tape_re(tape_re), .tape_we(tape_we), .tape_wdata(tape_wdata),
      .tape_rdata(tape_rdata), .tape_busy(tape_busy)
  );
endmodule
`default_nettype wire
