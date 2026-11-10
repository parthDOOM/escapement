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
 * v2 (S-A): the store has four pages and both copies have the same ctx_page. The fetch
 * address is {page, PC}, so "the words j fetches" are in j's page (ni_page): if no other
 * context shares j's page, no write outside it changes anything j does.
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
  (* anyconst *) reg [7:0]  ctx_page;                 // v2 (S-A)

  wire [3:0]   jb = 4'd1 << j;
  wire [127:0] jl = 128'hFFFFFFFF << (32 * j);
  // copy B: j's lanes are copy A's, the others free
  wire [3:0]   cstop_bj  = (cstop_a & jb)  | (cstop_b & ~jb);
  wire [3:0]   cstart_bj = (cstart_a & jb) | (cstart_b & ~jb);
  wire [3:0]   irqc_bj   = (irqc_a & jb)   | (irqc_b & ~jb);
  wire [3:0]   push_bj   = (push_a & jb)   | (push_b & ~jb);
  wire [3:0]   pop_bj    = (pop_a & jb)    | (pop_b & ~jb);
  wire [127:0] pdata_bj  = (pdata_a & jl)  | (pdata_b & ~jl);

  wire [9:0]    ia, ib;
  wire          rea, reb;
  wire [1279:0] oa, ob;
  wire [25:0]   pa, pb;
  wire [1:0]    fqa, fqb;
  wire          cva, cvb;
  wire [1:0]    cca, ccb, coa, cob;
  wire [7:0]    cpa, cpb;
  wire [31:0]   cia, cib;

`define NI_CORE(N, RD, CSTOP, CSTART, IRQC, PUSH, PDATA, POP, IA, RE, O, P, FQ, CV, CC, CP, CI, CO, CA, FC, LU) \
  esc_core N ( \
      .clk(clk), .rst_n(rst_n), .start(start), .stop(stop), .ctx_stop(CSTOP), .ctx_start(CSTART), \
      .ctx_enable(ctx_enable), .entry_pcs(entry_pcs), .pin_owned(pin_owned), .pin_owner(pin_owner), \
      .pin_od(pin_od), .ctx_bank(ctx_bank), .ctx_pair(ctx_pair), .ctx_page(ctx_page), \
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
      .f_ctx_obs(O), .f_pins(P), .f_fq(FQ), .f_ctx_all(CA), .f_core(FC), .f_lu(LU));
  wire [3463:0] caa, cab;
  wire [336:0]  fca, fcb;
  wire [32:0]   lua, lub;
  `NI_CORE(u_a, rd_a, cstop_a, cstart_a, irqc_a, push_a, pdata_a, pop_a, ia, rea, oa, pa, fqa, cva, cca, cpa, cia, coa, caa, fca, lua)
  `NI_CORE(u_b, rd_b, cstop_bj, cstart_bj, irqc_bj, push_bj, pdata_bj, pop_bj, ib, reb, ob, pb, fqb, cvb, ccb, cpb, cib, cob, cab, fcb, lub)

  // The program store: a word fetched for j at the same address in both copies is the same
  // word (j's words are the same in both); every other read is free in each copy.
  reg       fj_a, fj_b;
  reg [9:0] fa_a, fa_b;
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
    // v2 (S-A): every word j fetches is in j's page, in both copies
    if (fj_a) ni_page: assert (fa_a[9:8] == ctx_page[2*j +: 2]);
    if (fj_b) ni_page_b: assert (fa_b[9:8] == ctx_page[2*j +: 2]);
  end

`ifdef ESC_NI_IND
  // ---- the inductive strengthening (formal/esc_ni_ind.sby) ----------------------------
  // What makes the four lines above hold, stated so that it carries itself from one cycle
  // to the next: the schedule and the input synchronisers are the same in both copies
  // (they depend only on shared inputs); a pipeline stage holding j's instruction holds the
  // same one (stage owners: fetch own(now), p1 own(sq+2), p2 nq, p3 and commit cq, where
  // own() maps a slot to its owner under pair mode); every flip-flop of j's context is the
  // same; j's deferred tape load is the same; and j never owns the line unit (its words
  // use none of its instructions). The core fields, as esc_core.v concatenates them:
  wire [1:0] a_lu_owner;
  wire a_lu_owned;
  wire [31:0] a_q_ld;
  wire [3:0] a_rs_pend;
  wire [3:0] a_q_ldk;
  wire [1:0] a_ld_rd;
  wire [1:0] a_ld_ctx;
  wire a_ld_v;
  wire [15:0] a_lead16;
  wire a_ahd_lee;
  wire a_ahd_tail;
  wire a_rch_tmr;
  wire a_ahd_now;
  wire [31:0] a_opb;
  wire [31:0] a_opa;
  wire a_illegal;
  wire [52:0] a_d_op;
  wire a_p3ok;
  wire [31:0] a_p3_insn;
  wire a_p3v;
  wire [31:0] a_p2_insn;
  wire [7:0] a_p2_spc;
  wire a_p2v;
  wire [7:0] a_p1_spc;
  wire a_p1v;
  wire [12:0] a_s2;
  wire [12:0] a_s1;
  wire [1:0] a_fq;
  wire [1:0] a_nq;
  wire [1:0] a_cq;
  wire [1:0] a_sq;
  wire [31:0] a_now;
  wire a_run;
  assign {a_lu_owner, a_lu_owned, a_q_ld, a_rs_pend, a_q_ldk, a_ld_rd, a_ld_ctx, a_ld_v,
      a_lead16, a_ahd_lee, a_ahd_tail, a_rch_tmr, a_ahd_now, a_opb, a_opa, a_illegal, a_d_op,
      a_p3ok, a_p3_insn, a_p3v, a_p2_insn, a_p2_spc, a_p2v, a_p1_spc, a_p1v, a_s2, a_s1, a_fq,
      a_nq, a_cq, a_sq, a_now, a_run} = fca;
  wire [1:0] b_lu_owner;
  wire b_lu_owned;
  wire [31:0] b_q_ld;
  wire [3:0] b_rs_pend;
  wire [3:0] b_q_ldk;
  wire [1:0] b_ld_rd;
  wire [1:0] b_ld_ctx;
  wire b_ld_v;
  wire [15:0] b_lead16;
  wire b_ahd_lee;
  wire b_ahd_tail;
  wire b_rch_tmr;
  wire b_ahd_now;
  wire [31:0] b_opb;
  wire [31:0] b_opa;
  wire b_illegal;
  wire [52:0] b_d_op;
  wire b_p3ok;
  wire [31:0] b_p3_insn;
  wire b_p3v;
  wire [31:0] b_p2_insn;
  wire [7:0] b_p2_spc;
  wire b_p2v;
  wire [7:0] b_p1_spc;
  wire b_p1v;
  wire [12:0] b_s2;
  wire [12:0] b_s1;
  wire [1:0] b_fq;
  wire [1:0] b_nq;
  wire [1:0] b_cq;
  wire [1:0] b_sq;
  wire [31:0] b_now;
  wire b_run;
  assign {b_lu_owner, b_lu_owned, b_q_ld, b_rs_pend, b_q_ldk, b_ld_rd, b_ld_ctx, b_ld_v,
      b_lead16, b_ahd_lee, b_ahd_tail, b_rch_tmr, b_ahd_now, b_opb, b_opa, b_illegal, b_d_op,
      b_p3ok, b_p3_insn, b_p3v, b_p2_insn, b_p2_spc, b_p2v, b_p1_spc, b_p1v, b_s2, b_s1, b_fq,
      b_nq, b_cq, b_sq, b_now, b_run} = fcb;
  wire [3:0] pair_on = ctx_pair & ~{ctx_enable[1:0], ctx_enable[3:2]};
  function [1:0] own(input [1:0] s, input [3:0] pr);
    own = pr[s ^ 2'd2] ? (s ^ 2'd2) : s;
  endfunction
  // j's context, every flop; but h_q (the head's kind and payload, f_all[727:702]) loads the
  // shared commit entry on every cycle its FIFO is empty (o_cnt, f_all[829:827], is 0), whoever
  // commits, and is read only when the FIFO holds an entry: compared only then
  wire [865:0] ctx_ja = caa[866*j +: 866], ctx_jb = cab[866*j +: 866];
  wire [865:0] ctx_m  = (ctx_ja[829:827] == 3'd0) ? ~({26{1'b1}} << 702) : {866{1'b1}};
  function ni_ok_op(input [7:0] o);
    ni_ok_op = o != 8'h36 && o != 8'h37 && o != 8'h2D && o != 8'h3C && o != 8'h3D;
  endfunction
  wire j_p1 = a_run && own(a_sq + 2'd2, pair_on) == j;
  wire j_p2 = a_run && a_nq == j;
  wire j_p3 = a_run && a_cq == j;
  always @(*) if (f_past && rst_n && !init) begin
    inv_sched: assert ({a_run, a_now, a_sq, a_cq, a_nq, a_fq, a_s1, a_s2, a_ld_ctx}
                    == {b_run, b_now, b_sq, b_cq, b_nq, b_fq, b_s1, b_s2, b_ld_ctx});
    inv_ctx:   assert ((ctx_ja & ctx_m) == (ctx_jb & ctx_m));
    if (j_p1) inv_p1: assert (a_p1v == b_p1v && (!a_p1v || a_p1_spc == b_p1_spc));
    // the program-store assumption's records (fj_*, fa_*: last cycle's fetch was j's, at this
    // address) agree with the stage that holds that fetch now
    if (j_p1 && a_p1v) inv_fetch: assert (fj_a && fj_b && fa_a[7:0] == a_p1_spc && fa_b[7:0] == b_p1_spc);
    // the exclusions hold for j's words in flight too (the assumption above applies at fetch)
    if (j_p2 && a_p2v) inv_op2: assert (ni_ok_op(a_p2_insn[31:24]));
    if (j_p3 && a_p3v) inv_op3: assert (ni_ok_op(a_p3_insn[31:24]));
    if (j_p2) inv_p2: assert (a_p2v == b_p2v && (!a_p2v || {a_p2_spc, a_p2_insn} == {b_p2_spc, b_p2_insn}));
    if (j_p3) inv_p3: assert (a_p3v == b_p3v && (!a_p3v ||
        {a_p3_insn, a_p3ok, a_d_op, a_illegal, a_opa, a_opb, a_ahd_now, a_rch_tmr, a_ahd_tail, a_ahd_lee, a_lead16}
        == {b_p3_insn, b_p3ok, b_d_op, b_illegal, b_opa, b_opb, b_ahd_now, b_rch_tmr, b_ahd_tail, b_ahd_lee, b_lead16}));
    if (a_ld_ctx == j) inv_ld: assert (a_ld_v == b_ld_v && (!a_ld_v || a_ld_rd == b_ld_rd));
    inv_ldk:   assert (a_q_ldk[j] == b_q_ldk[j] && (!a_q_ldk[j] || a_q_ld == b_q_ld));
    inv_rs:    assert (a_rs_pend[j] == b_rs_pend[j]);
    inv_lu:    assert (!(a_lu_owned && a_lu_owner == j) && !(b_lu_owned && b_lu_owner == j));
    // esc_core's lu_bank lemma without $past (which constrains nothing at an induction's
    // first step): an owned unit in a run either has its owner's bank registered or is
    // loading a CONFIG and drives and releases nothing (lu_mode 1); so its logical pins
    // never land on another context's physical ones
    if (a_run && a_lu_owned) inv_lub_a: assert (lua[0] == ctx_bank[a_lu_owner]
        || (lua[3:1] == 3'd1 && lua[19:4] == 16'd0));
    if (b_run && b_lu_owned) inv_lub_b: assert (lub[0] == ctx_bank[b_lu_owner]
        || (lub[3:1] == 3'd1 && lub[19:4] == 16'd0));
    // the line unit takes the tape port only in its owner's commit cycles (TX/RX in own_cyc;
    // CONFIG and CRC words 4k cycles after the owner's op), so never in j's: j's LDT/STT
    // never waits for another context's unit
    if (a_run && lua[20]) inv_port_a: assert (a_lu_owned && a_cq == a_lu_owner);
    if (b_run && lub[20]) inv_port_b: assert (b_lu_owned && b_cq == b_lu_owner);
    // what makes it carry: a CONFIG or CRC job counts el from the edge after its op, which
    // committed in one of the owner's slots, so own(now - el) is the owner; TX/RX use the port
    // in own_cyc, the owner's own slot, which an enabled owner never lends to its partner
    if (a_run && (lua[3:1] == 3'd1 || lua[3:1] == 3'd6))
      inv_el_a: assert (a_lu_owned && own(a_now[1:0] - lua[22:21], pair_on) == a_lu_owner);
    if (b_run && (lub[3:1] == 3'd1 || lub[3:1] == 3'd6))
      inv_el_b: assert (b_lu_owned && own(b_now[1:0] - lub[22:21], pair_on) == b_lu_owner);
    if (a_lu_owned) inv_en_a: assert (ctx_enable[a_lu_owner]);
    if (b_lu_owned) inv_en_b: assert (ctx_enable[b_lu_owner]);
  end
`endif
endmodule
