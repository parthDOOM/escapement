// Host-interface unit testbench: escapement_host + real SRAM wrapper + stand-in core signals.
// v1.3: core_busy stands in for the core's tape_busy; the tape port is arbitrated as in
// project.v (the host gets it in every cycle core_busy is low, while running too).
// SPDX-License-Identifier: Apache-2.0
`default_nettype none
`ifndef ESC_STORE_MACROS
  // test/sim.mk sets it from src/project.v STORE_MACROS (the shipped store build); unset, this
  // names an undefined identifier, so the bench does not build on the wrong store.
  `define ESC_STORE_MACROS ESC_STORE_MACROS_not_set_by_test_sim_mk
`endif
`timescale 1ns / 1ps
module tb_host (
    input  wire clk, rst_n,
    input  wire cs_n, sck, mosi,
    output wire miso,
    // stand-in core status driven by the test
    input  wire running,
    input  wire [7:0] ctx_state,
    input  wire [3:0] irq_pending,
    input  wire fault_valid,
    input  wire [3:0] mbx_rx_full,
    input  wire [127:0] mbx_tx_data,
    input  wire [3:0] mbx_tx_empty,
    input  wire core_busy,
    output wire tape_ack,
    output wire [7:0] tape_ack_v,
    output wire tape_req,
    output wire tape_gnt,
    // observed host outputs
    output wire start, stop,
    output wire [3:0] ctx_enable,
    output wire [31:0] entry_pcs,
    output wire [12:0] pin_owned,
    output wire [25:0] pin_owner,
    output wire [7:0] pin_od,
    output wire [3:0] ctx_bank,
    output wire [3:0] ctx_pair,
    output wire [7:0] ctx_page,      // v2 (S-A)
    output wire [1:0] mem_page,
    output wire [3:0] irq_clear,
    output wire [1:0] dbg_ctx,
    output wire [4:0] dbg_sel,
    output wire [3:0] mbx_rx_push,
    output wire [127:0] mbx_rx_data,
    output wire [3:0] mbx_tx_pop
);
  initial begin $dumpfile("tb_host.fst"); $dumpvars(0, tb_host); end
  wire        mem_we, mem_space, mem_re;
  wire [7:0]  mem_addr;
  wire [31:0] mem_wdata, imem_rdata, tape_rdata;
  wire [31:0] mem_rdata = mem_space ? tape_rdata : imem_rdata;
  // stand-in debug read port: a recognisable function of (ctx, sel)
  wire [31:0] dbg_rdata = {8'hD0 | {6'b0, dbg_ctx}, 3'h0, dbg_sel, 16'hBEEF ^ {11'h0, dbg_sel}};

  // re as project.v while the host owns the memories (D-29)
  // v1.7 (S3): live writes, granted as in project.v; tb_free stands for the core's free slot
  wire [3:0] ctx_stop, ctx_start;
  wire       imem_wpend;
  reg        tb_free = 1'b1;
  wire       imem_wgnt = imem_wpend & (tb_free | ~running);
  escapement_pstore #(.MACROS(`ESC_STORE_MACROS)) u_sram (.clk(clk), .rst_n(rst_n), .addr({mem_page, mem_addr}), .re(mem_re & ~mem_space),
                          .we((mem_we & ~running & ~mem_space) | imem_wgnt),
                          .wdata(mem_wdata), .rdata(imem_rdata));
  // the stand-in core never really accesses the tape: while busy it only holds the port
  assign tape_gnt = tape_req & ~core_busy;
  escapement_sram u_tape (.clk(clk), .rst_n(rst_n), .addr(mem_addr), .re(tape_gnt & ~core_busy),
                          .we(mem_we & mem_space & ~core_busy),
                          .wdata(mem_wdata), .rdata(tape_rdata));
  escapement_host u_host (
      .clk(clk), .rst_n(rst_n), .cs_n_pin(cs_n), .sck_pin(sck), .mosi_pin(mosi), .miso(miso),
      .start(start), .stop(stop), .ctx_enable(ctx_enable), .entry_pcs(entry_pcs),
      .pin_owned(pin_owned), .pin_owner(pin_owner), .pin_od(pin_od), .ctx_bank(ctx_bank),
      .ctx_pair(ctx_pair), .ctx_page(ctx_page), .irq_clear(irq_clear), .ctx_stop(ctx_stop), .ctx_start(ctx_start),
      .imem_wpend(imem_wpend), .imem_wgnt(imem_wgnt),
      .running(running), .ctx_state(ctx_state), .irq_pending(irq_pending), .fault_valid(fault_valid),
      .mem_we(mem_we), .mem_addr(mem_addr), .mem_page(mem_page), .mem_wdata(mem_wdata), .mem_rdata(mem_rdata),
      .mem_space(mem_space), .mem_re(mem_re),
      .tape_req(tape_req), .tape_gnt(tape_gnt), .tape_ack(tape_ack), .tape_ack_v(tape_ack_v),
      .dbg_ctx(dbg_ctx), .dbg_sel(dbg_sel), .dbg_rdata(dbg_rdata),
      .mbx_rx_push(mbx_rx_push), .mbx_rx_data(mbx_rx_data), .mbx_rx_full(mbx_rx_full),
      .mbx_tx_pop(mbx_tx_pop), .mbx_tx_data(mbx_tx_data), .mbx_tx_empty(mbx_tx_empty));
endmodule
`default_nettype wire
