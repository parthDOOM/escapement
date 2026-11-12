/*
 * escapement_sram_big.v - the 512x32 and 1024x32 IHP 1P SRAM macros behind escapement_sram's
 * contract (v2, S-A: the program store's alternatives to four 256x32 macros; see
 * escapement_pstore.v, MACROS).
 *
 * Copyright (c) 2026 Escapement contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Exactly escapement_sram.v's timing contract and tie-offs, with a 9- or 10-bit address:
 * read `re` in cycle N -> `rdata` from N+1, held; no access without `re` or `we` (A_MEN
 * low); `we` wins and makes no read (A_REN = re & ~we); reset makes no access. The instance
 * name `sram` is part of the flattened path src/config.json names.
 */
`default_nettype none
module escapement_sram512 (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [8:0]  addr,
    input  wire        re,
    input  wire        we,
    input  wire [31:0] wdata,
    output wire [31:0] rdata
);
  RM_IHPSG13_1P_512x32_c2_bm_bist sram (
      .A_CLK      (clk),
      .A_MEN      (rst_n & (re | we)),
      .A_WEN      (we),
      .A_REN      (re & ~we),
      .A_ADDR     (addr),
      .A_DIN      (wdata),
      .A_DLY      (1'b1),
      .A_DOUT     (rdata),
      .A_BM       (32'hFFFF_FFFF),
      .A_BIST_CLK (1'b0),
      .A_BIST_EN  (1'b0),
      .A_BIST_MEN (1'b0),
      .A_BIST_WEN (1'b0),
      .A_BIST_REN (1'b0),
      .A_BIST_ADDR(9'b0),
      .A_BIST_DIN (32'b0),
      .A_BIST_BM  (32'b0)
  );
endmodule

module escapement_sram1024 (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [9:0]  addr,
    input  wire        re,
    input  wire        we,
    input  wire [31:0] wdata,
    output wire [31:0] rdata
);
  RM_IHPSG13_1P_1024x32_c2_bm_bist sram (
      .A_CLK      (clk),
      .A_MEN      (rst_n & (re | we)),
      .A_WEN      (we),
      .A_REN      (re & ~we),
      .A_ADDR     (addr),
      .A_DIN      (wdata),
      .A_DLY      (1'b1),
      .A_DOUT     (rdata),
      .A_BM       (32'hFFFF_FFFF),
      .A_BIST_CLK (1'b0),
      .A_BIST_EN  (1'b0),
      .A_BIST_MEN (1'b0),
      .A_BIST_WEN (1'b0),
      .A_BIST_REN (1'b0),
      .A_BIST_ADDR(10'b0),
      .A_BIST_DIN (32'b0),
      .A_BIST_BM  (32'b0)
  );
endmodule
`default_nettype wire
