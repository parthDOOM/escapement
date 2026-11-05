/*
 * esc_ni_env.v - non-interference between contexts (v1.7, S3), formal only.
 *
 * Two cores with the same configuration and the same inputs on every channel context j
 * (any context, an anyconst) can see: the pins, the tape's read data, run start and stop,
 * j's own CTX_STOP / CTX_START, mailboxes and IRQ clear, and the program words j fetches.
 * Copy B may differ arbitrarily in everything aimed at the other contexts: their CTX_STOP
 * and CTX_START (so their live reloads), their mailboxes and IRQ clears, and the program
 * words only they fetch. Then j's commits, its architectural state and the pins it owns are
 * equal in both copies, every cycle.
 *
 * Channels shared by design are equal by construction (the pins, the tape's contents as
 * read data) or excluded: j does not use the capture unit (CAPT, GETC) or the line unit
 * (LINE, SEND, GETL), whose single instance other contexts may own or configure; the global
 * digest, the first-fault register and the debug reads are not compared.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none
module esc_ni_env (
    input wire         clk,
    input wire         rst_n,
    input wire         start,
    input wire         stop,
    input wire [7:0]   uio_in,
    input wire [4:0]   ib_in,
    input wire [31:0]  tape_rdata,
    input wire         tape_host_we,
    input wire         tape_ack,
    input wire [7:0]   tape_ack_v,
    input wire [1:0]   dbg_sel_ctx,
    input wire [4:0]   dbg_sel,
    // per copy: the program store's read data, and the commands aimed at contexts
    input wire [31:0]  rd_a, rd_b,
    input wire [3:0]   cstop_a, cstop_b, cstart_a, cstart_b, irqc_a, irqc_b,
    input wire [3:0]   push_a, push_b, pop_a, pop_b,
    input wire [127:0] pdata_a, pdata_b
);
  (* anyconst *) reg [1:0]  j;
  (* anyconst *) reg [3:0]  ctx_enable, ctx_bank, ctx_pair;
  (* anyconst *) reg [31:0] entry_pcs;
  (* anyconst *) reg [12:0] pin_owned;
  (* anyconst *) reg [25:0] pin_owner;
  (* anyconst *) reg [7:0]  pin_od;

  wire [3:0]   jb = 4'd1 << j;
  wire [127:0] jl = 128'hFFFFFFFF << (32 * j);
  // copy B: j's lanes are copy A's, the others free
  wire [3:0]   cstop_bj  = (cstop_a & jb)  | (cstop_b & ~jb);
  wire [3:0]   cstart_bj = (cstart_a & jb) | (cstart_b & ~jb);
  wire [3:0]   irqc_bj   = (irqc_a & jb)   | (irqc_b & ~jb);
  wire [3:0]   push_bj   = (push_a & jb)   | (push_b & ~jb);
  wire [3:0]   pop_bj    = (pop_a & jb)    | (pop_b & ~jb);
  wire [127:0] pdata_bj  = (pdata_a & jl)  | (pdata_b & ~jl);

  wire [7:0]    ia, ib;
  wire          rea, reb;
  wire [1279:0] oa, ob;
  wire [25:0]   pa, pb;
  wire [1:0]    fqa, fqb;
  wire          cva, cvb;
  wire [1:0]    cca, ccb, coa, cob;
  wire [7:0]    cpa, cpb;
  wire [31:0]   cia, cib;

`define NI_CORE(N, RD, CSTOP, CSTART, IRQC, PUSH, PDATA, POP, IA, RE, O, P, FQ, CV, CC, CP, CI, CO) \
  esc_core N ( \
      .clk(clk), .rst_n(rst_n), .start(start), .stop(stop), .ctx_stop(CSTOP), .ctx_start(CSTART), \
      .ctx_enable(ctx_enable), .entry_pcs(entry_pcs), .pin_owned(pin_owned), .pin_owner(pin_owner), \
      .pin_od(pin_od), .ctx_bank(ctx_bank), .ctx_pair(ctx_pair), \
      .imem_addr(IA), .imem_re(RE), .imem_free(), .imem_rdata(RD), \
      .uio_in(uio_in), .uio_out(), .uio_oe(), .ib_in(ib_in), .ob_out(), \
      .running(), .ctx_state(), .irq_pending(), .fault_valid(), .irq_clear(IRQC), \
      .mbx_rx_push(PUSH), .mbx_rx_data(PDATA), .mbx_rx_full(), .mbx_tx_pop(POP), .mbx_tx_data(), \
      .mbx_tx_empty(), \
      .dbg_commit_valid(CV), .dbg_commit_ctx(CC), .dbg_commit_pc(CP), .dbg_commit_insn(CI), \
      .dbg_commit_outcome(CO), \
      .dbg_post_r0(), .dbg_post_r1(), .dbg_post_r2(), .dbg_post_r3(), .dbg_post_sh(), \
      .dbg_post_lc(), .dbg_post_tmr(), .dbg_post_z(), .dbg_post_c(), .dbg_post_pc(), \
      .dbg_sel_ctx(dbg_sel_ctx), .dbg_sel(dbg_sel), .dbg_rdata(), \
      .tape_addr(), .tape_re(), .tape_we(), .tape_wdata(), .tape_rdata(tape_rdata), .tape_busy(), \
      .tape_host_we(tape_host_we), .tape_ack(tape_ack), .tape_ack_v(tape_ack_v), \
      .f_ctx_obs(O), .f_pins(P), .f_fq(FQ));
  `NI_CORE(u_a, rd_a, cstop_a, cstart_a, irqc_a, push_a, pdata_a, pop_a, ia, rea, oa, pa, fqa, cva, cca, cpa, cia, coa)
  `NI_CORE(u_b, rd_b, cstop_bj, cstart_bj, irqc_bj, push_bj, pdata_bj, pop_bj, ib, reb, ob, pb, fqb, cvb, ccb, cpb, cib, cob)

  // The program store: a word fetched for j at the same address in both copies is the same
  // word (j's words are the same in both); every other read is free in each copy.
  reg       fj_a, fj_b;
  reg [7:0] fa_a, fa_b;
  always @(posedge clk) begin
    fj_a <= rea & (fqa == j); fa_a <= ia;
    fj_b <= reb & (fqb == j); fa_b <= ib;
  end
  always @(*) if (fj_a && fj_b && fa_a == fa_b) assume (rd_a == rd_b);
  // the exclusions: j's own words use neither the capture unit nor the line unit
  wire [7:0] op = rd_a[31:24];
  always @(*) if (fj_a) assume (op != 8'h36 && op != 8'h37 && op != 8'h2D && op != 8'h3C && op != 8'h3D);

  reg init = 1'b1;
  always @(posedge clk) init <= 1'b0;
  always @(*) if (init) assume (!rst_n);

  // j's physical pins
  wire [12:0] own_j;
  genvar gp;
  generate
    for (gp = 0; gp < 13; gp = gp + 1) begin : g_own
      assign own_j[gp] = pin_owned[gp] & (pin_owner[2*gp +: 2] == j);
    end
  endgenerate

  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;
  always @(*) if (f_past && rst_n && !init) begin
    ni_state: assert (oa[320*j +: 320] == ob[320*j +: 320]);
    ni_pins:  assert (((pa ^ pb) & {own_j, own_j}) == 26'd0);
    ni_commit: assert ((cva && cca == j) == (cvb && ccb == j));
    if (cva && cca == j) ni_record: assert ({cpa, cia, coa} == {cpb, cib, cob});
  end
endmodule
