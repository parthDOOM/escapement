/*
 * Copyright (c) 2026 Escapement contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Escapement: programmable protocol engine with reject-not-late timed I/O.
 *   ui_in[0] CS_n, ui_in[1] SCK, ui_in[2] MOSI   host SPI (docs/12-HOST-PROTOCOL.md)
 *   uo_out[0] MISO, uo_out[1] IRQ, uo_out[2] RUN
 *   uio[7:0] protocol pins, bank A, owned per context (docs/11-SEMANTICS.md)
 *   uo_out[7:3] / ui_in[7:3] protocol pins, bank B: five split pins
 * The tape: a second 256x32 SRAM (firmware LDT/STT, capture, and the host, which reads it
 * during a run too, in cycles the core leaves free). The program store: 1,024 words in
 * four pages of 256 (escapement_pstore.v); context k fetches from page ctx_page[k]
 * (docs/11-SEMANTICS.md §3).
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

  // Program pages. 4: four pages; 1: one page of 256 words, with the store and the host
  // interface of a chip without pages, cycle for cycle.
  localparam integer PAGES = 4;
  // How the four pages are built (escapement_pstore.v): 4 x 256x32, 2 x 512x32 or 1 x 1024x32.
  localparam integer STORE_MACROS = 1;
  // Proof tripwires. 1: on; 0: the core without the tripwire bank (debug
  // select 27 reads 0), cycle for cycle.
  localparam integer TRIPWIRES = 1;

  // ------------------------------------------------------------------ reset
  // rst_n enters the chip asynchronously to clk: the board's microcontroller releases it
  // whenever its firmware gets there. Every register and latch bank takes its reset from
  // rst_n_s instead, which falls with rst_n at once (no clock needed) but rises only on a
  // clock edge, two flip-flops after rst_n does. The release then reaches the whole chip
  // from one flip-flop on a known edge, and the flow times its spread across the reset tree
  // like any other path in the clock period. Fed straight from the pin, the release was
  // timed from the 20% input delay assumed for every input, which left the latch banks a
  // partial period at best (docs/06 D-63).
  reg  [1:0] rst_q;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) rst_q <= 2'b00;
    else        rst_q <= {rst_q[0], 1'b1};
  wire rst_n_s = rst_q[1];

  // ------------------------------------------------------------------ host
  wire         start, stop, running, fault_valid, miso;
  wire [3:0]   ctx_stop, ctx_start;            // live reload
  wire         imem_wpend, imem_wgnt, core_imem_free;
  wire [3:0]   ctx_enable, irq_clear, irq_pending;
  wire [31:0]  entry_pcs;
  wire [12:0]  pin_owned;
  wire [7:0]   pin_od, ctx_state;
  wire [3:0]   ctx_bank;
  wire [3:0]   ctx_pair;
  wire [7:0]   ctx_page;                       // program pages
  wire [3:0]   ctx_span;                       // 3.1: contexts that also fetch their partner's page
  wire [1:0]   host_mem_page;
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

  escapement_host #(.PAGES(PAGES)) u_host (
      .clk(clk), .rst_n(rst_n_s),
      .cs_n_pin(ui_in[0]), .sck_pin(ui_in[1]), .mosi_pin(ui_in[2]), .miso(miso),
      .start(start), .stop(stop), .ctx_enable(ctx_enable), .entry_pcs(entry_pcs),
      .pin_owned(pin_owned), .pin_owner(pin_owner), .pin_od(pin_od), .ctx_bank(ctx_bank), .ctx_pair(ctx_pair),
      .ctx_page(ctx_page), .ctx_span(ctx_span),
      .irq_clear(irq_clear), .ctx_stop(ctx_stop), .ctx_start(ctx_start),
      .imem_wpend(imem_wpend), .imem_wgnt(imem_wgnt),
      .running(running), .ctx_state(ctx_state), .irq_pending(irq_pending),
      .fault_valid(fault_valid),
      .mem_we(host_mem_we), .mem_addr(host_mem_addr), .mem_page(host_mem_page), .mem_wdata(host_mem_wdata),
      .mem_space(host_mem_space), .mem_re(host_mem_re), .mem_rdata(host_mem_space ? tape_rdata : imem_rdata),
      .tape_req(host_tape_req), .tape_gnt(host_tape_gnt),
      .tape_ack(host_tape_ack), .tape_ack_v(host_tape_ack_v),
      .dbg_ctx(dbg_ctx), .dbg_sel(dbg_sel), .dbg_rdata(dbg_rdata),
      .mbx_rx_push(mbx_rx_push), .mbx_rx_data(mbx_rx_data), .mbx_rx_full(mbx_rx_full),
      .mbx_tx_pop(mbx_tx_pop), .mbx_tx_data(mbx_tx_data), .mbx_tx_empty(mbx_tx_empty));

  // ------------------------------------------------------------------ program store
  // The core owns the single port while running, except in a cycle it reads nothing
  // (core_imem_free) when a program word written during the run waits (live
  // reload); the host has it while stopped. The select is two flip-flops. The address
  // is {page, word} on both sides; a live write is granted in a cycle with no used fetch,
  // whatever page either is in.
  wire [9:0] core_imem_addr;
  wire       core_imem_re;
  assign     imem_wgnt = imem_wpend & (core_imem_free | ~running);
  wire [9:0] store_addr = (running & ~imem_wgnt) ? core_imem_addr : {host_mem_page, host_mem_addr};
  wire       store_re   = running ? (core_imem_re & ~imem_wgnt) : (host_mem_re & ~host_mem_space);  // D-29
  wire       store_we   = (host_mem_we & ~running & ~host_mem_space) | imem_wgnt;
  escapement_pstore #(.PAGES(PAGES), .MACROS(STORE_MACROS)) u_sram (
      .clk  (clk),
      .rst_n(rst_n_s),
      .addr (store_addr),
      .re   (store_re),
      .we   (store_we),
      .wdata(host_mem_wdata),
      .rdata(imem_rdata));

`ifdef FORMAL
  // C6 (formal/esc_top.sby): the live-write grant is the one piece of the store arbitration
  // outside the core, whose proofs cover the fetch side. With every submodule cut free, and
  // the core's own proved rule as the only assumption (esc_core.v: a free slot reads nothing),
  //   G1 a granted write never meets a fetch the running core uses;
  //   G2 a grant cycle writes the host's word at the host's {page, word} and reads nothing
  //      (while stopped, given no host read then: esc_host.sby P3 proves there is none);
  //   G3 a waiting word is granted in the first free slot, or at once while stopped;
  //   G4 otherwise the running core has the port, reading only, at its own address.
  // With esc_core.sby (a free slot comes within 4 cycles while a context is not RUNNING) and
  // esc_host.sby (a waiting word holds still), a live word is written intact without
  // touching any running context's fetch.
  always @(*) if (rst_n_s) begin
    assume (!core_imem_free || !core_imem_re);
    if (running && imem_wgnt) assert (!core_imem_re);                               // G1
    if (imem_wgnt && (running || !host_mem_re))                                    // G2
      assert (store_we && !store_re && store_addr == {host_mem_page, host_mem_addr});
    if (imem_wpend && (core_imem_free || !running)) assert (imem_wgnt);             // G3
    if (running && !imem_wgnt) assert (!store_we && store_re == core_imem_re
                                        && store_addr == core_imem_addr);           // G4
    cover (running && imem_wgnt);                                                  // a live write
  end
`endif

  // ------------------------------------------------------------------ tape
  // The core has the port in the cycles it uses it (tape_busy: an LDT/STT commit or a
  // capture drain); the host has it in every other cycle, during a run too. A
  // host write happens only while stopped, and a drain waits for it (tape_host_we).
  wire [7:0]  core_tape_addr;
  wire        core_tape_we, core_tape_re, tape_busy;
  wire [31:0] core_tape_wdata;
  wire        host_tape_we = host_mem_we & host_mem_space;
  assign      host_tape_gnt = host_tape_req & ~tape_busy;
  escapement_sram u_tape (
      .clk  (clk),
      .rst_n(rst_n_s),
      .addr (tape_busy ? core_tape_addr : host_mem_addr),
      .re   (tape_busy ? core_tape_re : host_tape_gnt),  // D-29: one read per host word
      .we   (tape_busy ? core_tape_we : host_tape_we),
      .wdata(tape_busy ? core_tape_wdata : host_mem_wdata),
      .rdata(tape_rdata));

  // The 13 protocol pins sampled at the falling edge, half a period before the core's
  // rising-edge synchroniser samples them; the core re-times this through two more flops
  // (h2, h3), so the line unit's both-edge decoder sees each pin at two points per tick
  reg [12:0] pin_h;
  always @(negedge clk or negedge rst_n_s)
    if (!rst_n_s) pin_h <= 13'd0;
    else        pin_h <= {ui_in[7:3], uio_in};

  // ------------------------------------------------------------------ core
  /* verilator lint_off PINCONNECTEMPTY */
  esc_core #(.TRIPWIRES(TRIPWIRES)) u_core (
      .clk(clk), .rst_n(rst_n_s), .start(start), .stop(stop),
      .ctx_stop(ctx_stop), .ctx_start(ctx_start),
      .ctx_enable(ctx_enable), .entry_pcs(entry_pcs),
      .pin_owned(pin_owned), .pin_owner(pin_owner), .pin_od(pin_od), .ctx_bank(ctx_bank), .ctx_pair(ctx_pair),
      .ctx_page(ctx_page), .ctx_span(ctx_span),
      .imem_addr(core_imem_addr), .imem_re(core_imem_re), .imem_free(core_imem_free),
      .imem_wr(imem_wgnt), .imem_wpage(host_mem_page), .imem_rdata(imem_rdata),
      .uio_in(uio_in), .uio_out(uio_out), .uio_oe(uio_oe), .ib_in(ui_in[7:3]), .pin_h(pin_h), .ob_out(ob_out),
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
