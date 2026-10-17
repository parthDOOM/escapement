/*
 * Port-only blackbox for the IHP 256x32 single-port SRAM hard macro.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Synthesis and lint only. The macro itself comes from the MACROS block in
 * src/config.json (GDS/LEF/LIB/CDL vendored in macro/); simulation uses the
 * vendored behavioural model via test/Makefile. Never give this module a body:
 * synthesis would then build 8192 flops instead of instancing the macro.
 * Port list copied from macro/RM_IHPSG13_1P_256x32_c2_bm_bist/*.v.
 */
`default_nettype none
/* verilator lint_off UNUSEDSIGNAL */
/* verilator lint_off UNDRIVEN */
module RM_IHPSG13_1P_256x32_c2_bm_bist (
    input  wire        A_CLK,
    input  wire        A_MEN,
    input  wire        A_WEN,
    input  wire        A_REN,
    input  wire [7:0]  A_ADDR,
    input  wire [31:0] A_DIN,
    input  wire        A_DLY,
    output wire [31:0] A_DOUT,
    input  wire [31:0] A_BM,
    input  wire        A_BIST_CLK,
    input  wire        A_BIST_EN,
    input  wire        A_BIST_MEN,
    input  wire        A_BIST_WEN,
    input  wire        A_BIST_REN,
    input  wire [7:0]  A_BIST_ADDR,
    input  wire [31:0] A_BIST_DIN,
    input  wire [31:0] A_BIST_BM
);
endmodule
/* verilator lint_on UNDRIVEN */
/* verilator lint_on UNUSEDSIGNAL */
`default_nettype wire
