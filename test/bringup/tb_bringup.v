`default_nettype none
`timescale 1ns / 1ps

/* The chip on a simulated Tiny Tapeout demo board, for test_bringup_rtl.py.
   Each uio pin is driven by the chip when uio_oe is set, else by the RP2xxx when rp_oe
   is set, else pulled up by the RP's pull-up, else reads 0. This is the electrical model
   of sw/tests/demoboard_mock.py (Pads) and, with the pull-ups on, of the ISS default
   board that sw/rp2350/make_bundle.py predicts against.
*/
module tb_bringup ();
  reg clk = 1'b0;
  reg rst_n = 1'b1;
  reg [7:0] ui_in = 8'h01;
  reg [7:0] rp_oe = 8'h00, rp_val = 8'h00, rp_pull = 8'h00;
  wire [7:0] uo_out, uio_out, uio_oe;

  wire [7:0] uio_level = (uio_oe & uio_out) | (~uio_oe & rp_oe & rp_val)
                         | (~uio_oe & ~rp_oe & rp_pull);
  wire [7:0] contention = uio_oe & rp_oe;   // the RP and the chip both driving a pin

  tt_um_escapement user_project (
      .ui_in  (ui_in),
      .uo_out (uo_out),
      .uio_in (uio_level),
      .uio_out(uio_out),
      .uio_oe (uio_oe),
      .ena    (1'b1),
      .clk    (clk),
      .rst_n  (rst_n)
  );
endmodule
