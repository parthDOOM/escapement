/*
 * escapement_sram_fpga.v - escapement_sram for an FPGA: the same module name and ports,
 * an inferred 256x32 block RAM in place of the IHP SRAM macro.
 *
 * Copyright (c) 2026 Escapement contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * It keeps the macro wrapper's timing contract (src/escapement_sram.v): a read in cycle N
 * gives mem[addr] from cycle N+1 and holds it until the next read; a write cycle stores
 * and reads nothing (rdata holds); `we` wins over `re`; under reset no access is made.
 * Contents start at 0 (the macro's are undefined; firmware never relies on either).
 */
`default_nettype none
module escapement_sram (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  addr,
    input  wire        re,
    input  wire        we,
    input  wire [31:0] wdata,
    output reg  [31:0] rdata
);
  reg [31:0] mem [0:255];
  integer i;
  initial begin
    for (i = 0; i < 256; i = i + 1) mem[i] = 32'd0;
    rdata = 32'd0;
  end
  always @(posedge clk) begin
    if (rst_n && we)
      mem[addr] <= wdata;
    else if (rst_n && re)
      rdata <= mem[addr];
  end
endmodule
`default_nettype wire
