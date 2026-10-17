// Proof harness for the clock-gated register bank (src/esc_greg.v, D-29).
//
// clk, rst_n, en and d are unconstrained inputs that may change at any solver step
// (multiclock: every step is a point in continuous time, the clock is just a signal),
// so this covers en changing while clk is high, while it is low, and at the same step
// as either edge. Proved for all time (k-induction):
//   G1 the gated bank equals a plain enable flip-flop at every step;
//   G2 the gated clock is never high while clk is low;
//   G3 the gated clock rises only at a rising edge of clk (no glitch, no extra edge).
`default_nettype none
module esc_greg_env (
    input wire       clk,
    input wire       rst_n,
    input wire       en,
    input wire [3:0] d
);
  wire [3:0] q;
  esc_greg #(4) dut (.clk(clk), .rst_n(rst_n), .en(en), .d(d), .q(q));

  reg [3:0] q_ref;
  always @(posedge clk or negedge rst_n)
    if (!rst_n)  q_ref <= 4'd0;
    else if (en) q_ref <= d;

  wire gclk;
  esc_icg icg (.clk(clk), .en(en), .gclk(gclk));

  reg init = 1'b1;
  always @($global_clock) init <= 1'b0;
  always @(*) if (init) assume (!rst_n);

  always @(*) if (!init) assert (q == q_ref);                    // G1
  always @(*) assert (!gclk || clk);                             // G2
  always @($global_clock) if (!init && !$past(gclk) && gclk)
    assert (!$past(clk) && clk);                                 // G3

  always @(*) cover (!init && rst_n && en && q != 4'd0);
endmodule
`default_nettype wire
