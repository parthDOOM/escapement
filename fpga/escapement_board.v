/*
 * escapement_board.v - the chip on an FPGA board: tt_um_escapement with its 24 Tiny
 * Tapeout pins, its clock and its reset on header pins, as on the Tiny Tapeout demo board.
 *
 * Copyright (c) 2026 Escapement contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * The host (an RP2040 or RP2350 board running sw/rp2350) drives clk, rst_n and ui_in[2:0]
 * and reads uo_out[2:0], so the bring-up script sets the clock as it does on the demo
 * board. Bank A's uio pins are three-state pads driven by uio_oe, as on the chip.
 * Built with ESC_NO_CG and fpga/escapement_sram_fpga.v (fpga/build.sh).
 */
`default_nettype none
module escapement_board (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    inout  wire [7:0] uio
);
  wire [7:0] uio_out, uio_oe;

  tt_um_escapement chip (
      .ui_in(ui_in), .uo_out(uo_out),
      .uio_in(uio), .uio_out(uio_out), .uio_oe(uio_oe),
      .ena(1'b1), .clk(clk), .rst_n(rst_n)
  );

  genvar i;
  generate
    for (i = 0; i < 8; i = i + 1) begin : pad
      assign uio[i] = uio_oe[i] ? uio_out[i] : 1'bz;
    end
  endgenerate
endmodule
`default_nettype wire
