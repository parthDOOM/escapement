/*
 * escapement_sram.v - one IHP 1P 256x32 SRAM macro: the tape (and escapement_pstore's pages).
 *
 * Copyright (c) 2026 Escapement contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Timing contract (docs/01-ARCHITECTURE.md, "Core"; docs/11-SEMANTICS.md §3):
 *   read   `re` and `addr` presented in cycle N -> `rdata` = mem[addr] from cycle
 *          N+1, held until the next read completes. A cycle with neither `re` nor
 *          `we` makes no access at all (A_MEN low: D-29, the macro idles instead of
 *          reading every cycle); each user reads only when it uses the word.
 *   write  `we` in cycle N stores `wdata` at `addr` on the N->N+1 edge; all 32
 *          bits (bit mask tied all-ones). No read happens in a write cycle, so
 *          `rdata` keeps its previous value (A_REN = re & ~we avoids the macro's
 *          write-through, which would return the written data instead). `we` wins
 *          over `re`.
 *   reset  `rst_n` low deasserts A_MEN: no access, `rdata` holds. Contents are
 *          not reset; SRAM powers up undefined.
 *
 * Tie-offs, from the vendored model: A_DLY must be 1 (the model $stops
 * otherwise); BIST is disabled and every A_BIST_* pin tied to 0. All enables
 * are active high. The instance name `sram` is part of the flattened path
 * used by src/config.json (MACROS, PDN_MACRO_CONNECTIONS).
 *
 * Every input that switches reaches the macro through escapement_mbuf (below): a
 * buffer of its own beside nothing but the macro pin. The macro's inputs allow a
 * transition of 0.595 ns at the slow corner (the cells: 2.5 ns), and the flow's
 * repair judges slew at the fast corner only, where those pins are within their
 * limit, so a minimum-size gate could drive one over ~150 um of wire unrepaired.
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
  wire        macro_in_men, macro_in_wen, macro_in_ren;
  wire [7:0]  macro_in_addr;
  wire [31:0] macro_in_din;
  escapement_mbuf #(.W(43)) u_mbuf (
      .a({rst_n & (re | we), we, re & ~we, addr, wdata}),
      .x({macro_in_men, macro_in_wen, macro_in_ren, macro_in_addr, macro_in_din})
  );
  RM_IHPSG13_1P_256x32_c2_bm_bist sram (
      .A_CLK      (clk),
      .A_MEN      (macro_in_men),
      .A_WEN      (macro_in_wen),
      .A_REN      (macro_in_ren),
      .A_ADDR     (macro_in_addr),
      .A_DIN      (macro_in_din),
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

// One buffer per bit in front of a macro input (see above). Synthesis (VERILOG_DEFINES
// ESC_MACRO_BUFFERS in src/config.json) places a sg13cmos5l_buf_4 per bit; simulation,
// formal and equivalence see a wire, which is all a buffer is. The nets it drives are
// named macro_in_* and are don't-touch in place and route (RSZ_DONT_TOUCH_RX), so the
// resizer neither removes a buffer nor puts another on the net after it. That net is not
// always short: placement can leave a buffer 600 um from the macro (137 fF in one 3.1 layout),
// and a buf_2 then reached the slow-corner pin limit, 0.595 ns, within 3 ps. A buf_4 has
// about half the drive resistance (3.1, 11-22).
module escapement_mbuf #(
    parameter integer W = 1
) (
    input  wire [W-1:0] a,
    output wire [W-1:0] x
);
`ifdef ESC_MACRO_BUFFERS
  genvar i;
  generate
    for (i = 0; i < W; i = i + 1) begin : g_buf
      sg13cmos5l_buf_4 u_buf (.A(a[i]), .X(x[i]));
    end
  endgenerate
`else
  assign x = a;
`endif
endmodule
`default_nettype wire
