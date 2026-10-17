// Host-interface unit testbench: escapement_host + real SRAM wrapper + stand-in core signals.
// SPDX-License-Identifier: Apache-2.0
`default_nettype none
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
    // observed host outputs
    output wire start, stop,
    output wire [3:0] ctx_enable,
    output wire [31:0] entry_pcs,
    output wire [7:0] pin_owned,
    output wire [15:0] pin_owner,
    output wire [7:0] pin_od,
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
  escapement_sram u_sram (.clk(clk), .rst_n(rst_n), .addr(mem_addr), .re(mem_re & ~mem_space),
                          .we(mem_we & ~running & ~mem_space),
                          .wdata(mem_wdata), .rdata(imem_rdata));
  escapement_sram u_tape (.clk(clk), .rst_n(rst_n), .addr(mem_addr), .re(mem_re & mem_space),
                          .we(mem_we & ~running & mem_space),
                          .wdata(mem_wdata), .rdata(tape_rdata));
  escapement_host u_host (
      .clk(clk), .rst_n(rst_n), .cs_n_pin(cs_n), .sck_pin(sck), .mosi_pin(mosi), .miso(miso),
      .start(start), .stop(stop), .ctx_enable(ctx_enable), .entry_pcs(entry_pcs),
      .pin_owned(pin_owned), .pin_owner(pin_owner), .pin_od(pin_od), .irq_clear(irq_clear),
      .running(running), .ctx_state(ctx_state), .irq_pending(irq_pending), .fault_valid(fault_valid),
      .mem_we(mem_we), .mem_addr(mem_addr), .mem_wdata(mem_wdata), .mem_rdata(mem_rdata),
      .mem_space(mem_space), .mem_re(mem_re),
      .dbg_ctx(dbg_ctx), .dbg_sel(dbg_sel), .dbg_rdata(dbg_rdata),
      .mbx_rx_push(mbx_rx_push), .mbx_rx_data(mbx_rx_data), .mbx_rx_full(mbx_rx_full),
      .mbx_tx_pop(mbx_tx_pop), .mbx_tx_data(mbx_tx_data), .mbx_tx_empty(mbx_tx_empty));
endmodule
`default_nettype wire
