/*
 * esc_host_env.v - S1: the configuration the core runs with never changes during a run
 * (formal only).
 *
 * The core proofs (esc_core.sby) assume that ctx_enable, entry_pcs, pin_owned, pin_owner
 * and pin_od hold still while the core runs. They come from the host interface, so this
 * harness proves it there: the host's SPI pins and every core-side input are free, and
 * the core's run flag is modelled exactly as esc_core drives it (set the cycle after
 * `start`, cleared after `stop`, start winning). A PASS discharges the core proofs'
 * only environment assumption besides reset.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none
module esc_host_env (
    input wire         clk,
    input wire         rst_n,
    input wire         cs_n_pin,
    input wire         sck_pin,
    input wire         mosi_pin,
    input wire [7:0]   ctx_state,
    input wire [3:0]   irq_pending,
    input wire         fault_valid,
    input wire [31:0]  mem_rdata,
    input wire [31:0]  dbg_rdata,
    input wire [3:0]   mbx_rx_full,
    input wire [127:0] mbx_tx_data,
    input wire [3:0]   mbx_tx_empty
);
  wire        start, stop;
  wire [3:0]  ctx_enable;
  wire [31:0] entry_pcs;
  wire [7:0]  pin_owned, pin_od;
  wire [15:0] pin_owner;

  // the core's run flag (esc_core.v: `if (start) run <= 1; else if (stop) run <= 0;`)
  reg run;
  always @(posedge clk or negedge rst_n)
    if (!rst_n)     run <= 1'b0;
    else if (start) run <= 1'b1;
    else if (stop)  run <= 1'b0;

  escapement_host u (
      .clk(clk), .rst_n(rst_n), .cs_n_pin(cs_n_pin), .sck_pin(sck_pin), .mosi_pin(mosi_pin),
      .miso(), .start(start), .stop(stop), .ctx_enable(ctx_enable), .entry_pcs(entry_pcs),
      .pin_owned(pin_owned), .pin_owner(pin_owner), .pin_od(pin_od), .irq_clear(),
      .running(run), .ctx_state(ctx_state), .irq_pending(irq_pending),
      .fault_valid(fault_valid), .mem_we(), .mem_addr(), .mem_wdata(), .mem_space(),
      .mem_re(), .mem_rdata(mem_rdata), .dbg_ctx(), .dbg_sel(), .dbg_rdata(dbg_rdata),
      .mbx_rx_push(), .mbx_rx_data(), .mbx_rx_full(mbx_rx_full), .mbx_tx_pop(),
      .mbx_tx_data(mbx_tx_data), .mbx_tx_empty(mbx_tx_empty));

  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;
  always @(*) if (!f_past) assume (!rst_n);

  // S1, in exactly the form esc_core.v assumes it
  always @(posedge clk)
    if (f_past && rst_n && run) begin
      assert ($stable(ctx_enable));
      assert ($stable(entry_pcs));
      assert ($stable(pin_owned));
      assert ($stable(pin_owner));
      assert ($stable(pin_od));
    end

  // reachability: a configuration is written, and a run starts afterwards
  always @(posedge clk) if (f_past && rst_n) begin
    cover ($changed(pin_owned) && !run);
    cover ($rose(run) && pin_owned != 8'd0);
  end
endmodule
`default_nettype wire
