/*
 * escapement_sram.v - program store: one IHP 1P 256x32 SRAM macro.
 *
 * Copyright (c) 2026 Escapement contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Timing contract (docs/01-ARCHITECTURE.md, "Core"; docs/11-SEMANTICS.md §3):
 *   read   `re` and `addr` presented in cycle N -> `rdata` = mem[addr] from cycle
 *          N+1, held until the next read completes. A cycle with neither `re` nor
 *          `we` makes no access at all (A_MEN low: D-29, the macro idles instead of
 *          reading every cycle); the program store ties `re` high.
 *   write  `we` in cycle N stores `wdata` at `addr` on the N->N+1 edge; all 32
 *          bits (bit mask tied all-ones). No read happens in a write cycle, so
 *          `rdata` keeps its previous value (A_REN = ~we avoids the macro's
 *          write-through, which would return the written data instead). `we` wins
 *          over `re`.
 *   reset  `rst_n` low deasserts A_MEN: no access, `rdata` holds. Contents are
 *          not reset; SRAM powers up undefined.
 *
 * Tie-offs, from the vendored model: A_DLY must be 1 (the model $stops
 * otherwise); BIST is disabled and every A_BIST_* pin tied to 0. All enables
 * are active high. The instance name `sram` is part of the flattened path
 * used by src/config.json (MACROS, PDN_MACRO_CONNECTIONS).
 */
`default_nettype none
module escapement_sram (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  addr,
    input  wire        re,
    input  wire        we,
    input  wire [31:0] wdata,
    output wire [31:0] rdata
);
  RM_IHPSG13_1P_256x32_c2_bm_bist sram (
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
      .A_BIST_ADDR(8'b0),
      .A_BIST_DIN (32'b0),
      .A_BIST_BM  (32'b0)
  );
endmodule
`default_nettype wire
