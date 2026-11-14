/*
 * pinid.v - wiring check for the Tang Primer 25K Dock before the chip is loaded.
 *
 * Every one of escapement_board's 26 pins sends its own index, over and over, as two
 * UART-style bytes (8N1, LSB first): code = 0x80 | index, then ~code, then 4 idle bits;
 * 24 bit times per repeat. Index: 0 clk, 1 rst_n, 2-9 ui_in[0..7], 10-17 uo_out[0..7],
 * 18-25 uio[0..7]. Pins are open drain (driven 0 or released): the Pico's pull-ups make
 * the 1s, so a Pico pin left as an output cannot fight a driven 1, and two pins bridged
 * together read as a wired AND that fails the complement check.
 *
 * Clocked by the Dock's 50 MHz oscillator (E2); BIT_DIV clocks per bit (1 kbit/s).
 * pinid.py on the Pico decodes all 26 GPIOs at once and prints the wiring.
 */
`default_nettype none
module pinid #(
    parameter integer BIT_DIV = 50000
) (
    input  wire       clk50,
    inout  wire       clk,
    inout  wire       rst_n,
    inout  wire [7:0] ui_in,
    inout  wire [7:0] uo_out,
    inout  wire [7:0] uio
);
  reg [15:0] div = 16'd0;
  reg [4:0]  slot = 5'd0;                 // bit time 0..23 of the repeat
  always @(posedge clk50) begin
    if (div == BIT_DIV - 1) begin
      div  <= 16'd0;
      slot <= (slot == 5'd23) ? 5'd0 : slot + 5'd1;
    end else begin
      div <= div + 16'd1;
    end
  end

  function bitval(input [4:0] idx, input [4:0] s);
    reg [7:0] code;
    begin
      code = {3'b100, idx};
      if (s == 5'd0 || s == 5'd10) bitval = 1'b0;               // start bits
      else if (s >= 5'd1 && s <= 5'd8) bitval = code[s - 5'd1];
      else if (s >= 5'd11 && s <= 5'd18) bitval = ~code[s - 5'd11];
      else bitval = 1'b1;                                     // stop bits and idle
    end
  endfunction

  wire [25:0] level;
  genvar i;
  generate
    for (i = 0; i < 26; i = i + 1) begin : pin
      assign level[i] = bitval(i, slot);
    end
  endgenerate

  assign clk   = level[0] ? 1'bz : 1'b0;
  assign rst_n = level[1] ? 1'bz : 1'b0;
  generate
    for (i = 0; i < 8; i = i + 1) begin : port
      assign ui_in[i]  = level[2 + i]  ? 1'bz : 1'b0;
      assign uo_out[i] = level[10 + i] ? 1'bz : 1'b0;
      assign uio[i]    = level[18 + i] ? 1'bz : 1'b0;
    end
  endgenerate
endmodule
`default_nettype wire
