// Proof harness for the latch bank (src/esc_greg.v esc_lreg, D-40).
//
// As formal/esc_greg_env.v: clk, rst_n, en and d are free and may change at any solver
// step (multiclock). dq is a flip-flop that loads a free value dn at every rising edge of
// clk, so it changes only there, as every dq in the design does (each is a function of
// flip-flops); the one contract is that dn is d whenever en is high, i.e. after an edge
// that writes the bank, dq is the d of that edge. Proved for all time (k-induction):
//   L1 the latch bank equals a plain enable flip-flop at every step, so also while its
//      latches are transparent: they then copy dq, which already is the written value;
//   L2 (the inductive strengthening) while the gated clock is high, dq is that value.
`default_nettype none
module esc_lreg_env (
    input wire       clk,
    input wire       rst_n,
    input wire       en,
    input wire [3:0] d,
    input wire [3:0] dn
);
  always @(*) if (en) assume (dn == d);

  reg [3:0] dq;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) dq <= 4'd0;
    else        dq <= dn;

  wire [3:0] q;
  esc_lreg #(4) dut (.clk(clk), .rst_n(rst_n), .en(en), .d(d), .dq(dq), .q(q));

  reg [3:0] q_ref;
  always @(posedge clk or negedge rst_n)
    if (!rst_n)  q_ref <= 4'd0;
    else if (en) q_ref <= d;

  wire gclk;                           // the bank's gated clock (the same gate, as esc_greg_env)
  esc_icg icg (.clk(clk), .en(en), .gclk(gclk));

  reg init = 1'b1;
  always @($global_clock) init <= 1'b0;
  always @(*) if (init) assume (!rst_n);

  always @(*) if (!init) assert (q == q_ref);                                   // L1
  always @(*) if (!init && gclk) assert (dq == q_ref);                       // L2

  always @(*) cover (!init && rst_n && !clk && q != 4'd0);
endmodule
`default_nettype wire
