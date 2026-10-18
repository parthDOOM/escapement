/*
 * Copyright (c) 2026 Escapement contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Escapement: programmable protocol engine with reject-not-late timed I/O.
 *   ui_in[0] CS_n, ui_in[1] SCK, ui_in[2] MOSI   host SPI (docs/12-HOST-PROTOCOL.md)
 *   uo_out[0] MISO, uo_out[1] IRQ, uo_out[2] RUN
 *   uio[7:0] protocol pins, bank A, owned per context (docs/11-SEMANTICS.md)
 *   uo_out[7:3] / ui_in[7:3] protocol pins, bank B: five split pins (v1.2)
 * v1.1: a second 256x32 SRAM, the tape (firmware LDT/STT, capture, host while stopped).
 * v1.3: the host reads the tape during a run too, in cycles the core leaves free.
 */

`default_nettype none

module tt_um_escapement (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

  // ------------------------------------------------------------------ host
  wire         start, stop, running, fault_valid, miso;
  wire [3:0]   ctx_enable, irq_clear, irq_pending;
  wire [31:0]  entry_pcs;
  wire [12:0]  pin_owned;
  wire [7:0]   pin_od, ctx_state;
  wire [3:0]   ctx_bank;
  wire [4:0]   ob_out;
  wire [25:0]  pin_owner;
  wire         host_mem_we, host_mem_space, host_mem_re;
  wire         host_tape_req, host_tape_gnt, host_tape_ack;
  wire [7:0]   host_tape_ack_v;
  wire [7:0]   host_mem_addr;
  wire [31:0]  host_mem_wdata, imem_rdata, tape_rdata;
  wire [1:0]   dbg_ctx;
  wire [4:0]   dbg_sel;
  wire [31:0]  dbg_rdata;
  wire [3:0]   mbx_rx_push, mbx_rx_full, mbx_tx_pop, mbx_tx_empty;
  wire [127:0] mbx_rx_data, mbx_tx_data;

  escapement_host u_host (
      .clk(clk), .rst_n(rst_n),
      .cs_n_pin(ui_in[0]), .sck_pin(ui_in[1]), .mosi_pin(ui_in[2]), .miso(miso),
      .start(start), .stop(stop), .ctx_enable(ctx_enable), .entry_pcs(entry_pcs),
      .pin_owned(pin_owned), .pin_owner(pin_owner), .pin_od(pin_od), .ctx_bank(ctx_bank),
      .irq_clear(irq_clear),
      .running(running), .ctx_state(ctx_state), .irq_pending(irq_pending),
      .fault_valid(fault_valid),
      .mem_we(host_mem_we), .mem_addr(host_mem_addr), .mem_wdata(host_mem_wdata),
      .mem_space(host_mem_space), .mem_re(host_mem_re), .mem_rdata(host_mem_space ? tape_rdata : imem_rdata),
      .tape_req(host_tape_req), .tape_gnt(host_tape_gnt),
      .tape_ack(host_tape_ack), .tape_ack_v(host_tape_ack_v),
      .dbg_ctx(dbg_ctx), .dbg_sel(dbg_sel), .dbg_rdata(dbg_rdata),
      .mbx_rx_push(mbx_rx_push), .mbx_rx_data(mbx_rx_data), .mbx_rx_full(mbx_rx_full),
      .mbx_tx_pop(mbx_tx_pop), .mbx_tx_data(mbx_tx_data), .mbx_tx_empty(mbx_tx_empty));

  // ------------------------------------------------------------------ program store
  // The core owns the single port while running; the host only while stopped.
  wire [7:0] core_imem_addr;
  wire       core_imem_re;
  escapement_sram u_sram (
      .clk  (clk),
      .rst_n(rst_n),
      .addr (running ? core_imem_addr : host_mem_addr),
      .re   (running ? core_imem_re : (host_mem_re & ~host_mem_space)),  // D-29
      .we   (host_mem_we & ~running & ~host_mem_space),
      .wdata(host_mem_wdata),
      .rdata(imem_rdata));

  // ------------------------------------------------------------------ tape (v1.1)
  // The core has the port in the cycles it uses it (tape_busy: an LDT/STT commit or a
  // capture drain); the host has it in every other cycle, during a run too (v1.3). A
  // host write happens only while stopped, and a drain waits for it (tape_host_we).
  wire [7:0]  core_tape_addr;
  wire        core_tape_we, core_tape_re, tape_busy;
  wire [31:0] core_tape_wdata;
  wire        host_tape_we = host_mem_we & host_mem_space;
  assign      host_tape_gnt = host_tape_req & ~tape_busy;
  escapement_sram u_tape (
      .clk  (clk),
      .rst_n(rst_n),
      .addr (tape_busy ? core_tape_addr : host_mem_addr),
      .re   (tape_busy ? core_tape_re : host_tape_gnt),  // D-29: one read per host word
      .we   (tape_busy ? core_tape_we : host_tape_we),
      .wdata(tape_busy ? core_tape_wdata : host_mem_wdata),
      .rdata(tape_rdata));

  // Layout art (macro/escapement_art): no ports, nothing to connect.
  (* keep *) escapement_art u_art ();

  // ------------------------------------------------------------------ core
  /* verilator lint_off PINCONNECTEMPTY */
  esc_core u_core (
      .clk(clk), .rst_n(rst_n), .start(start), .stop(stop),
      .ctx_enable(ctx_enable), .entry_pcs(entry_pcs),
      .pin_owned(pin_owned), .pin_owner(pin_owner), .pin_od(pin_od), .ctx_bank(ctx_bank),
      .imem_addr(core_imem_addr), .imem_re(core_imem_re), .imem_rdata(imem_rdata),
      .uio_in(uio_in), .uio_out(uio_out), .uio_oe(uio_oe), .ib_in(ui_in[7:3]), .ob_out(ob_out),
      .running(running), .ctx_state(ctx_state),
      .irq_pending(irq_pending), .fault_valid(fault_valid), .irq_clear(irq_clear),
      .mbx_rx_push(mbx_rx_push), .mbx_rx_data(mbx_rx_data), .mbx_rx_full(mbx_rx_full),
      .mbx_tx_pop(mbx_tx_pop), .mbx_tx_data(mbx_tx_data), .mbx_tx_empty(mbx_tx_empty),
      .dbg_commit_valid(), .dbg_commit_ctx(), .dbg_commit_pc(), .dbg_commit_insn(),
      .dbg_commit_outcome(),
      .dbg_post_r0(), .dbg_post_r1(), .dbg_post_r2(), .dbg_post_r3(), .dbg_post_sh(),
      .dbg_post_lc(), .dbg_post_tmr(), .dbg_post_z(), .dbg_post_c(), .dbg_post_pc(),
      .dbg_sel_ctx(dbg_ctx), .dbg_sel(dbg_sel), .dbg_rdata(dbg_rdata),
      .tape_addr(core_tape_addr), .tape_re(core_tape_re), .tape_we(core_tape_we), .tape_wdata(core_tape_wdata),
      .tape_rdata(tape_rdata), .tape_busy(tape_busy), .tape_host_we(host_tape_we),
      .tape_ack(host_tape_ack), .tape_ack_v(host_tape_ack_v));
  /* verilator lint_on PINCONNECTEMPTY */

  assign uo_out = {ob_out, running, |irq_pending | fault_valid, miso};

  wire _unused = &{ena, 1'b0};

endmodule
