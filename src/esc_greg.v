/*
 * esc_greg.v - clock-gated register bank (D-29: power).
 *
 * Copyright (c) 2026 Escapement contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Function: q <= d at a rising edge of clk where en is high; q holds otherwise; rst_n
 * low clears q asynchronously. Exactly an enable flip-flop, so every proof and every
 * model of the chip is unchanged by it (formal/esc_greg.sby proves the equivalence).
 *
 * Why: post-CTS power analysis of the v1.0 chip (asic/reports, docs/06 D-29) put 72%
 * of total power in flip-flop internal power and 22% in the clock tree, against 1.5%
 * in logic; nearly all of it is clock pins toggling on registers that are written
 * rarely (a context's FIFO entry is written at most once per 4 ticks, a mailbox word
 * once per host transfer). Gating stops their clock instead of recirculating q through
 * a multiplexer, which also removes the mux and the hold-critical q->d self-loop.
 *
 * The gate is the textbook latch-based integrated clock gate: a latch, transparent
 * while clk is low, holds en through the high phase, so gclk = clk & en_l cannot
 * glitch. The PDK's own ICG cells (sg13cmos5l_lgcp_1, slgcp_1) are on its LibreLane
 * synthesis and PnR exclusion lists, so in the flow the gate is built from two allowed
 * cells, instantiated by name so that synthesis cannot restructure the clock path:
 * sg13cmos5l_dllrq_1 (latch, transparent while GATE_N is low) and sg13cmos5l_and2_1.
 * Simulation and formal use the behavioural model below. ESC_NO_CG turns every bank
 * back into enable flip-flops (one VERILOG_DEFINES line in src/config.json).
 */
`default_nettype none

// Formal proofs of the logic (single clock) see plain enable flip-flops; the gate itself
// is proved equivalent under multiclock in formal/esc_greg.sby (ESC_CG_FORMAL).
`ifdef FORMAL
`ifndef ESC_CG_FORMAL
`define ESC_GREG_PLAIN
`endif
`endif
`ifdef ESC_NO_CG
`define ESC_GREG_PLAIN
`endif

/* verilator lint_off DECLFILENAME */
module esc_icg (
    input  wire clk,
    input  wire en,
    output wire gclk
);
`ifdef SCL_sg13cmos5l_stdcell
  wire en_l;
  sg13cmos5l_dllrq_1 lat (.D(en), .GATE_N(clk), .RESET_B(1'b1), .Q(en_l));
  sg13cmos5l_and2_1  gate (.A(clk), .B(en_l), .X(gclk));
`else
  reg en_l;
  /* verilator lint_off LATCH */
  always @(clk or en) if (!clk) en_l = en;
  /* verilator lint_on LATCH */
  assign gclk = clk & en_l;
`endif
endmodule
/* verilator lint_on DECLFILENAME */

module esc_greg #(
    parameter W = 32
) (
    input  wire         clk,
    input  wire         rst_n,
    input  wire         en,
    input  wire [W-1:0] d,
    output reg  [W-1:0] q
);
`ifdef ESC_GREG_PLAIN
  always @(posedge clk or negedge rst_n)
    if (!rst_n)  q <= {W{1'b0}};
    else if (en) q <= d;
`else
  wire gclk;
  esc_icg icg (.clk(clk), .en(en), .gclk(gclk));
  always @(posedge gclk or negedge rst_n)
    if (!rst_n) q <= {W{1'b0}};
    else        q <= d;
`endif
endmodule

/*
 * esc_lreg - a gated bank stored in latches (v1.4, D-40: area).
 *
 * Function: exactly esc_greg (q <= d at a rising edge where en is high). The caller also
 * supplies dq, the value d had at the last rising edge, from a flip-flop it shares with
 * every bank written through the same port. In the standard-cell build each bit is a
 * latch (sg13cmos5l_dlhrq_1, 27.2 um2 against 49.0 for the flip-flop) that is transparent
 * while the gated clock is high and copies dq, which is stable then: q changes right
 * after the edge, as a flip-flop's would, and holds from the falling edge on. RTL
 * simulation and the logic proofs use esc_greg; formal/esc_lreg.sby proves the latch
 * build equal to it whenever dq is d registered at each enabled edge, and the core's
 * proofs show that every bank's dq is (docs/04).
 */
module esc_lreg #(
    parameter W = 32
) (
    input  wire         clk,
    input  wire         rst_n,
    input  wire         en,
    input  wire [W-1:0] d,
    input  wire [W-1:0] dq,
    output wire [W-1:0] q
);
`ifdef SCL_sg13cmos5l_stdcell
`ifndef ESC_NO_CG
`define ESC_LREG_LATCH
`endif
`endif
`ifdef ESC_LREG_FORMAL
`define ESC_LREG_LATCH
`endif
`ifdef ESC_LREG_LATCH
  wire gclk;
  esc_icg icg (.clk(clk), .en(en), .gclk(gclk));
`ifdef SCL_sg13cmos5l_stdcell
  genvar i;
  for (i = 0; i < W; i = i + 1) begin : g_l
    sg13cmos5l_dlhrq_1 l (.D(dq[i]), .GATE(gclk), .RESET_B(rst_n), .Q(q[i]));
  end
`else
  reg [W-1:0] l;
  /* verilator lint_off LATCH */
  always @(*) if (!rst_n) l = {W{1'b0}}; else if (gclk) l = dq;
  /* verilator lint_on LATCH */
  assign q = l;
`endif
  /* verilator lint_off UNUSEDSIGNAL */
  wire unused_d = &{1'b0, d};
  /* verilator lint_on UNUSEDSIGNAL */
`else
  esc_greg #(W) g (.clk(clk), .rst_n(rst_n), .en(en), .d(d), .q(q));
  /* verilator lint_off UNUSEDSIGNAL */
  wire unused_dq = &{1'b0, dq};
  /* verilator lint_on UNUSEDSIGNAL */
`endif
`undef ESC_LREG_LATCH
endmodule

`default_nettype wire
