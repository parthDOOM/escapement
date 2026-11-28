// ISA checker: each committed instruction does what docs/11-SEMANTICS.md section 7 says.
//
// Verification only. esc_core instantiates it under `ifdef ESC_ISA_CHECK, which the isa
// tasks of formal/esc_core.sby define (with FORMAL) and the co-simulation defines (without):
// there each assertion is checked on every cycle of every co-simulated program, and the
// simulator prints each failure, which test/cosim/isa_fail.py finds for the harnesses that
// fail on one (test/cosim/run_parallel.py, the mutation campaign). It changes
// nothing that is synthesised. What an instruction does is written here from the
// specification alone; only the encodings are generated (esc_isa_enc.vh, from isa/isa.yaml).
//
// When. In a proof each check holds combinationally in every cycle (`ISA_AT is *). In a
// simulation the same expressions are sampled once a cycle, at the falling edge, when the
// registers that changed at the rising edge have settled: a combinational always block
// there would also run on the passing mixtures of old and new values within the edge.
//
// Timing. A commit happens at the clock edge E_c that ends cycle c.
//   During cycle c (the cm_* and pre_* inputs, valid when cm_valid is high): the
//   instruction that commits at E_c, its context, and that context's architectural state
//   before the commit.
//   During cycle c+1 (the dbg_* and post_* inputs, valid when dbg_commit_valid is high):
//   the commit as the observation ports report it (section 10) and the same context's
//   state after E_c.
// So an expected result is computed from cm_*/pre_* in cycle c, registered at E_c, and
// compared in cycle c+1. Every commit is seen: cm_valid in cycle c is exactly
// dbg_commit_valid in cycle c+1.
//
// Labels: assertions isa_<name>, covers isa_cv_<name>. The isa task keeps only the isa_*
// assertions; the lemmas ahead_b0-b9 (proved by the ahead task) and the configuration
// assumptions stay assumed, as in every task of esc_core.sby.
`default_nettype none
`ifdef FORMAL
`define ISA_AT *
`else
`define ISA_AT negedge clk
`endif
module esc_isa_check (
    input wire         clk,
    input wire         rst_n,
    // ---- cycle c: the commit at E_c and the state before it -------------------------
    input wire         cm_valid,     // a commit at E_c (any outcome: COMPLETE, WAIT, FAULT)
    input wire  [1:0]  cm_ctx,       // its context
    input wire  [31:0] cm_insn,      // the instruction word that commits (the word executed)
    input wire         cm_span,      // the context spans two pages (3.1): 9-bit PC, 0..511
    input wire  [31:0] pre_r0, pre_r1, pre_r2, pre_r3,
    input wire  [31:0] pre_sh,
    input wire  [15:0] pre_lc,
    input wire  [31:0] pre_tmr,
    input wire         pre_z, pre_c,
    input wire  [8:0]  pre_pc,       // the committing instruction's address
    input wire  [8:0]  pre_lr,       // the link register
    input wire  [31:0] cm_now,       // the tick c itself (section 2)
    input wire         pre_irq_p,    // the context's irq_pending and irq_code
    input wire  [7:0]  pre_irq_code,
    input wire         cm_irq_clear, // the host's irq_clear for this context, at E_c (section 10)
    input wire         pre_ff_valid, // the first-fault register (shared by all contexts) is full
    input wire  [2:0]  pre_ff_cause, // ... and its contents during cycle c
    input wire  [2:0]  pre_ff_sub,
    input wire  [1:0]  pre_ff_ctx,
    input wire  [8:0]  pre_ff_pc,
    input wire  [31:0] pre_ff_tick,
    input wire         cm_paired,    // the context is paired in this run (section 3)
    input wire         pre_lu_owned, // the line unit during cycle c (section 6.6; dbg_sel 26):
    input wire  [1:0]  pre_lu_owner, //   owned, its owner, and its mode: 0 IDLE, 1 CFG, 2 PEND,
    input wire  [2:0]  pre_lu_mode,  //   3 TX, 4 ARM, 5 RX, 6 CRC
    input wire         pre_lu_conf,  //   configured (dbg_sel 26 bit 20)
    input wire  [7:0]  pre_lu_claimed, // the line unit's tx_pins as its owner's logical pins
                                     // (0 when unconfigured or without tx_pins; section 6.6)
    // the run's configuration (section 10, "Core ports"; static during a run): pin p owned
    // and its owner at [2p+1:2p], and each context's bank (section 5, logical pins)
    input wire  [12:0] cfg_pin_owned,
    input wire  [25:0] cfg_pin_owner,
    input wire  [3:0]  cfg_ctx_bank,
    // the context's event FIFO during cycle c, in order: entry i (0 = the head) at
    // [58i+57:58i] as {kind [57:56] (LINE 0, DRIVE 1, EXPECT 2, SAMPLE 3), deadline [55:24],
    // payload [23:0]}; only the first pre_q_cnt entries are in the FIFO. The payload holds an
    // entry's fields at the bit positions of its instruction format in isa.yaml: DRIVE
    // mask [23:16], value [15:8], oe [7:0] (format P); EXPECT mask, level, mode [7], window
    // [6:0] (format PE); SAMPLE pin [18:16], dir [15] (format PS), the other bits 0. A LINE
    // entry's command is held by the line unit, not in the payload: check its kind and
    // deadline only.
    input wire  [2:0]  pre_q_cnt,
    input wire  [231:0] pre_q,
    input wire  [31:0] pre_lee,      // last_expect_end (section 6.2 step 6)
    input wire         pre_eseen,    // an EXPECT has been admitted since start
    input wire  [15:0] pre_min_lead, // telemetry (section 7.4; dbg_sel 9 and 10)
    input wire  [2:0]  pre_max_occ,
    input wire  [15:0] pre_wait_full,
    // ---- cycle c+1: the commit as reported, and the state after E_c ----------------
    input wire         dbg_commit_valid,
    input wire  [1:0]  dbg_commit_ctx,
    input wire  [8:0]  dbg_commit_pc,
    input wire  [31:0] dbg_commit_insn,
    input wire  [1:0]  dbg_commit_outcome,   // 0 COMPLETE, 1 WAIT, 2 FAULT
    input wire  [31:0] post_r0, post_r1, post_r2, post_r3,
    input wire  [31:0] post_sh,
    input wire  [15:0] post_lc,
    input wire  [31:0] post_tmr,
    input wire         post_z, post_c,
    input wire  [8:0]  post_pc,      // the context's next PC
    input wire  [8:0]  post_lr,
    input wire  [1:0]  post_state,   // 0 IDLE, 1 RUNNING, 2 HALTED, 3 FAULTED (section 10, sel 7)
    input wire         post_irq_p,
    input wire  [7:0]  post_irq_code,
    // the first-fault register after E_c (section 8; dbg_sel 14 and 15). It records the first
    // fault since the run started, of any context, so a commit's fault is recorded only if
    // it was empty before (pre_ff_valid low), and another context may fault at the same edge.
    input wire         post_ff_valid,
    input wire  [2:0]  post_ff_cause,
    input wire  [2:0]  post_ff_sub,
    input wire  [1:0]  post_ff_ctx,
    input wire  [8:0]  post_ff_pc,
    input wire  [31:0] post_ff_tick,
    input wire  [2:0]  post_fifo_cnt,   // the context's event FIFO count after E_c (dbg_sel 10)
    input wire         post_chk_active, // its checker is active after E_c (dbg_sel 10 bit 1)
    input wire  [2:0]  post_q_cnt,      // the FIFO after E_c, as pre_q
    input wire  [231:0] post_q,
    input wire  [31:0] post_lee,
    input wire         post_eseen,
    input wire  [15:0] post_min_lead,
    input wire  [2:0]  post_max_occ,
    input wire  [15:0] post_wait_full,
    // ---- every cycle (not tied to a commit), for following a context from one commit to
    // its next: the run, its start and stop pulses (section 9), and every context's state
    // during the cycle (2 bits each, context k at [2k+1:2k], encoded as post_state)
    input wire         run_now,
    input wire         start_now,
    input wire         stop_now,
    input wire  [3:0]  ctx_stop_now, // the host's CTX_STOP for each context at this cycle's edge (section 9)
    input wire  [7:0]  state_all,
    // ---- tier 4 (7.3 input, 7.5 mailboxes, 7.6 tape and capture, 7.7 line unit, 7.8
    // monitor). The pre_*/post_* rule is as above; the unit ports below have no pre/post
    // pair: they are read in cycle c for "during cycle c" and in cycle c+1 for "after E_c".
    // the committing context's (pre) and the committed context's (post) input history
    // ish (section 6.4, SAMPLE; GETI; dbg_sel 8)
    input wire  [31:0] pre_ish,
    input wire  [31:0] post_ish,
    // GETX's sources during cycle c (dbg_sel 10): fail_count, pass_count, checker active,
    // failed since start
    input wire  [7:0]  pre_pass,
    input wire  [7:0]  pre_fail,
    input wire         pre_chk_active,
    input wire         pre_failed,
    // the context's two host mailboxes (7.5, 12-HOST-PROTOCOL), each two words deep:
    // {tx_cnt [131:130], tx 2nd [129:98], tx head [97:66], rx_cnt [65:64], rx 2nd [63:32],
    // rx head [31:0]}; a word at or past its mailbox's count is not in it. pre_mbx is the
    // committing context's during cycle c, post_mbx the committed context's in cycle c+1.
    input wire  [131:0] pre_mbx,
    input wire  [131:0] post_mbx,
    // the host's side at E_c, for the committing context: an RX push of cm_h_rx_data
    // (taken only if RX is not full during cycle c) and a TX pop (taken only if TX is not
    // empty during cycle c), which may meet a POP or PUSH at the same edge (7.5: "same-edge
    // host access never changes what the commit at that edge observed")
    input wire         cm_h_rx_push,
    input wire  [31:0] cm_h_rx_data,
    input wire         cm_h_tx_pop,
    // the line unit's owner after E_c (as pre_lu_owned, pre_lu_owner: a CONFIG with no owner
    // takes ownership at E_c, 7.7)
    input wire         post_lu_owned,
    input wire  [1:0]  post_lu_owner,
    // ---- tier 4, every cycle
    // the synchronised physical pins (section 5): [7:0] bank A, [12:8] bank B; a context's
    // sync_c is its logical view of these by its bank (cfg_ctx_bank)
    input wire  [12:0] phys_sync,
    // the tape port (7.6: T is the tape, 256 words of 32 bits, outside the core): in a cycle
    // with tape_re high the tape is read at that cycle's edge at tape_addr and tape_rdata is
    // the word read, during the next cycle; tape_we high writes tape_wdata at tape_addr at that
    // edge. The port is shared with the line unit (lu_port: it has the port this cycle, in
    // one of its owner's cycles, 6.6), the capture unit's drain (6.5) and the host.
    input wire  [7:0]  tape_addr,
    input wire         tape_re,
    input wire         tape_we,
    input wire  [31:0] tape_wdata,
    input wire  [31:0] tape_rdata,
    input wire         lu_port,
    // the capture unit (6.5): GETC's status word (dbg_sel 17), its configuration (dbg_sel 18)
    // and rptr (dbg_sel 20), and the host's TAPE_ACK at this cycle's edge with its new rptr
    input wire  [31:0] cap_status,
    input wire  [31:0] cap_cfg,
    input wire  [7:0]  cap_rptr,
    input wire         tape_ack,
    input wire  [7:0]  tape_ack_v,
    // the line unit (6.6; engaged follows from pre_lu_mode): busy, the status word GETL
    // returns (dbg_sel 21), the end tick e (dbg_sel 25), and its CRC register and whole bit
    // count {crc [45:14], count [13:0]} (GETM 4 and 3)
    input wire         lu_busy,
    input wire  [31:0] lu_status,
    input wire  [31:0] lu_e,
    input wire  [45:0] lu_aux,
    // the bus timing monitor (6.7): its owner, one-hot by context (0: no owner), its two
    // words {record [63:32], counts [31:0]} (GETM 1 and 0) and the record's tick (GETM 2)
    input wire  [3:0]  mon_owner,
    input wire  [63:0] mon_words,
    input wire  [31:0] mon_tick
);
`include "esc_isa_enc.vh"

  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;

  // ---- the interface itself (not a semantic check): what was seen in cycle c is what the
  // ports report in cycle c+1
  reg        q_valid;
  reg [1:0]  q_ctx;
  reg [31:0] q_insn;
  reg [8:0]  q_pc;
  always @(posedge clk) begin
    q_valid <= rst_n & cm_valid;
    q_ctx   <= cm_ctx;
    q_insn  <= cm_insn;
    q_pc    <= pre_pc;
  end
  always @(`ISA_AT) if (f_past && rst_n) begin
    isa_if_valid: assert (dbg_commit_valid == q_valid);
    isa_if_ctx:   assert (!q_valid || dbg_commit_ctx == q_ctx);
    isa_if_insn:  assert (!q_valid || dbg_commit_insn == q_insn);
    isa_if_pc:    assert (!q_valid || dbg_commit_pc == q_pc);
    isa_cv_commit: cover (dbg_commit_valid);
  end

  // ---- the instructions, from docs/11-SEMANTICS.md (written separately; see the brief)
  // TIER 1: all of 7.1; all of 7.2 but HALT; PC_RANGE; ILLEGAL for every opcode word; the
  // first-fault record (8); irq_pending / irq_code with the host's clear (10, "IRQ clear").
  //
  // Written from docs/11-SEMANTICS.md sections 2, 3, 4, 7 (preamble, 7.1, 7.2), 8, 9 and 10,
  // isa/isa.yaml and esc_isa_enc.vh only. A commit is *checked* when its word is ILLEGAL
  // (any opcode) or is a legal word of a tier-1 instruction; a legal word of a later-tier
  // opcode is not checked. For a checked commit in cycle c, every field the checker sees
  // afterwards is computed here (s_*), registered at E_c (e_*), and compared with the post_*
  // ports in cycle c+1, one assertion per field (isa_<field>). A field an instruction does
  // not list is expected unchanged (7: "Flags are unchanged unless listed"; 3: on FAULT "no
  // architectural effect of the instruction").
  //
  // TIER 4 adds the sixteen instructions of 7.3 (input), 7.5, 7.6, 7.7 and 7.8, so every
  // listed opcode is now checked. Their hand-offs are checked as far as section 7 states
  // them: ish, the mailboxes with the host's same-edge access, the tape port during cycle c
  // (LDT, STT), the capture unit after a CAPT, and the line unit's and the monitor's owners.
  // What a unit then does with the hand-off (6.5, 6.6, 6.7) is not checked here.

  localparam [1:0] OUT_COMPLETE = 2'd0, OUT_WAIT = 2'd1, OUT_FAULT = 2'd2;
  localparam [1:0] ST_IDLE = 2'd0, ST_RUNNING = 2'd1, ST_HALTED = 2'd2, ST_FAULTED = 2'd3;
  localparam [2:0] CA_ILLEGAL   = ENC_CAUSE_ILLEGAL[2:0];
  localparam [2:0] CA_PC_RANGE  = ENC_CAUSE_PC_RANGE[2:0];
  localparam [2:0] CA_ADMIT     = ENC_CAUSE_ADMIT[2:0];
  localparam [2:0] CA_LATE_HEAD = ENC_CAUSE_LATE_HEAD[2:0];

  // ---- decode (cycle c)
  wire [7:0]  d_op = cm_insn[31:24];
  wire [23:0] d_pl = cm_insn[23:0];

  wire d_mov   = (d_op == ENC_OP_MOV);
  wire d_movi  = (d_op == ENC_OP_MOVI);
  wire d_movhi = (d_op == ENC_OP_MOVHI);
  wire d_add   = (d_op == ENC_OP_ADD);
  wire d_addi  = (d_op == ENC_OP_ADDI);
  wire d_sub   = (d_op == ENC_OP_SUB);
  wire d_cmp   = (d_op == ENC_OP_CMP);
  wire d_cmpi  = (d_op == ENC_OP_CMPI);
  wire d_and   = (d_op == ENC_OP_AND);
  wire d_or    = (d_op == ENC_OP_OR);
  wire d_xor   = (d_op == ENC_OP_XOR);
  wire d_tsti  = (d_op == ENC_OP_TSTI);
  wire d_shl   = (d_op == ENC_OP_SHL);
  wire d_shr   = (d_op == ENC_OP_SHR);
  wire d_ldsh  = (d_op == ENC_OP_LDSH);
  wire d_stsh  = (d_op == ENC_OP_STSH);
  wire d_nop   = (d_op == ENC_OP_NOP);
  wire d_jmp   = (d_op == ENC_OP_JMP);
  wire d_jz    = (d_op == ENC_OP_JZ);
  wire d_jnz   = (d_op == ENC_OP_JNZ);
  wire d_jc    = (d_op == ENC_OP_JC);
  wire d_jnc   = (d_op == ENC_OP_JNC);
  wire d_djnz  = (d_op == ENC_OP_DJNZ);
  wire d_ldlc  = (d_op == ENC_OP_LDLC);
  wire d_call  = (d_op == ENC_OP_CALL);
  wire d_ret   = (d_op == ENC_OP_RET);
  wire d_irq   = (d_op == ENC_OP_IRQ);

  wire d_data = d_mov | d_movi | d_movhi | d_add | d_addi | d_sub | d_cmp | d_cmpi |
                d_and | d_or | d_xor | d_tsti | d_shl | d_shr | d_ldsh | d_stsh;
  wire d_ctrl = d_nop | d_jmp | d_jz | d_jnz | d_jc | d_jnc | d_djnz | d_ldlc |
                d_call | d_ret | d_irq;
  wire d_t1   = d_data | d_ctrl;

  // TIER 2: 7.3's time instructions, HALT (7.2), WAIT, the retry grid (3, 7.3)
  wire d_rdtime = (d_op == ENC_OP_RDTIME);
  wire d_sett   = (d_op == ENC_OP_SETT);
  wire d_addt   = (d_op == ENC_OP_ADDT);
  wire d_addtr  = (d_op == ENC_OP_ADDTR);
  wire d_waitt  = (d_op == ENC_OP_WAITT);
  wire d_halt   = (d_op == ENC_OP_HALT);
  wire d_t2     = d_rdtime | d_sett | d_addt | d_addtr | d_waitt | d_halt;

  // TIER 3: the event instructions (7.4) through admission (6.2), the FIFO across every
  // checked commit's edge (6.3, 8), telemetry (7.4)
  wire d_emit   = (d_op == ENC_OP_EMIT);
  wire d_emitr  = (d_op == ENC_OP_EMITR);
  wire d_emitx  = (d_op == ENC_OP_EMITX);
  wire d_expect = (d_op == ENC_OP_EXPECT);
  wire d_sample = (d_op == ENC_OP_SAMPLE);
  wire d_send   = (d_op == ENC_OP_SEND);
  wire d_drive  = d_emit | d_emitr | d_emitx;
  wire d_t3     = d_drive | d_expect | d_sample | d_send;

  // TIER 4: 7.3's input instructions, 7.5 mailboxes, 7.6 tape and capture, 7.7 line unit,
  // 7.8 bus timing monitor, each with its hand-off to the unit it uses as section 7 states it
  wire d_getp   = (d_op == ENC_OP_GETP);
  wire d_waitp  = (d_op == ENC_OP_WAITP);
  wire d_geti   = (d_op == ENC_OP_GETI);
  wire d_getx   = (d_op == ENC_OP_GETX);
  wire d_pop    = (d_op == ENC_OP_POP);
  wire d_push   = (d_op == ENC_OP_PUSH);
  wire d_rxrdy  = (d_op == ENC_OP_RXREADY);
  wire d_txrdy  = (d_op == ENC_OP_TXREADY);
  wire d_ldt    = (d_op == ENC_OP_LDT);
  wire d_stt    = (d_op == ENC_OP_STT);
  wire d_capt   = (d_op == ENC_OP_CAPT);
  wire d_getc   = (d_op == ENC_OP_GETC);
  wire d_line   = (d_op == ENC_OP_LINE);
  wire d_getl   = (d_op == ENC_OP_GETL);
  wire d_mon    = (d_op == ENC_OP_MON);
  wire d_getm   = (d_op == ENC_OP_GETM);
  wire d_mbxop  = d_pop | d_push | d_rxrdy | d_txrdy;
  wire d_t4     = d_getp | d_waitp | d_geti | d_getx | d_mbxop | d_ldt | d_stt | d_capt |
                  d_getc | d_line | d_getl | d_mon | d_getm;
  // the mailboxes during cycle c (two words each, 7.5): a count of 3 is not a mailbox state,
  // so a mailbox instruction seen with one is not checked, and no commit's mailboxes are
  wire [1:0]  p_rxn = pre_mbx[65:64];
  wire [1:0]  p_txn = pre_mbx[131:130];
  wire [31:0] p_rx0 = pre_mbx[31:0],  p_rx1 = pre_mbx[63:32];
  wire [31:0] p_tx0 = pre_mbx[97:66], p_tx1 = pre_mbx[129:98];
  wire        d_msane = (p_rxn != 2'd3) & (p_txn != 2'd3);

  // 6.1 kinds; an entry on the FIFO ports is {kind [57:56], deadline [55:24], payload [23:0]}
  localparam [1:0] K_LINE = 2'd0, K_DRIVE = 2'd1, K_EXPECT = 2'd2, K_SAMPLE = 2'd3;
  // 6.3: the head h retires at E_c if h.deadline == c. A head with reached(c, h.deadline)
  // and h.deadline != c faults LATE_HEAD at E_c; admission makes that unreachable (P2, proved
  // by the design's own proofs), so a commit in a cycle with a late head, or with a FIFO
  // count above 4, is not checked here at all (d_qsane low).
  wire [31:0] p_hd     = pre_q[55:24];
  wire [31:0] p_hdiff  = cm_now - p_hd;
  wire        d_retire = (pre_q_cnt != 3'd0) & (p_hd == cm_now);
  wire        d_late   = (pre_q_cnt != 3'd0) & ~p_hdiff[31] & (p_hd != cm_now);
  wire        d_qsane  = (pre_q_cnt <= 3'd4) & ~d_late;
  // Q: the FIFO after the retirement at E_c (6.2), in order, d_qn entries
  wire [2:0]   d_qn = pre_q_cnt - {2'b00, d_retire};
  wire [231:0] d_qq = d_retire ? {58'd0, pre_q[231:58]} : pre_q;

  // 7 / isa.yaml: an unlisted opcode, a nonzero reserved bit (a payload bit the format does
  // not name), or format B's target bit 8 when the context does not span is ILLEGAL. Format B
  // is the one whose named bits are exactly target [8:0].
  // The generated functions are tabulated for all 256 opcodes with constant arguments, so
  // they are evaluated at elaboration. Called on d_op, Yosys's proc_rom turns their case
  // statements into ROMs ($mem with init), whose contents k-induction leaves unconstrained.
  wire [256*24-1:0] t_used;
  wire [255:0]      t_listed;
  genvar g_op;
  generate for (g_op = 0; g_op < 256; g_op = g_op + 1) begin : g_enc
    assign t_used[g_op*24 +: 24] = enc_used(g_op);
    assign t_listed[g_op]        = enc_listed(g_op);
  end endgenerate
  wire [23:0] d_used    = t_used[d_op*24 +: 24];
  wire        d_listed  = t_listed[d_op];
  wire        d_fmt_b   = d_listed & (d_used == ENC_FMT_B_USED);
  wire        d_unl     = ~d_listed;
  wire        d_rsv_bad = d_listed & (|(d_pl & ~d_used));
  wire        d_b8_bad  = d_fmt_b & d_pl[8] & ~cm_span;
  wire        d_illegal = d_unl | d_rsv_bad | d_b8_bad;
  wire        d_chk     = (d_illegal | d_t1 | d_t2 | d_t3 | d_t4) & d_qsane & (d_msane | ~d_mbxop);

  // fields (isa.yaml)
  wire [1:0]  d_rd   = d_pl[23:22];
  wire [1:0]  d_rs   = d_pl[21:20];
  wire [15:0] d_imm  = d_pl[15:0];
  wire [31:0] d_imm32 = {16'd0, d_pl[15:0]};                 // zext(imm)
  wire [4:0]  d_n    = d_pl[4:0];
  wire [7:0]  d_code = d_pl[7:0];
  wire [31:0] d_imm24 = {8'd0, d_pl[23:0]};                  // T24 imm, zext

  // PC arithmetic (3, Span): 9 bits when the context spans, else 8 bits (PC[8] = lr[8] = 0)
  wire [8:0] d_pc1  = cm_span ? (pre_pc + 9'd1) : {1'b0, pre_pc[7:0] + 8'd1};
  wire [8:0] d_tgt  = cm_span ? d_pl[8:0] : {1'b0, d_pl[7:0]};
  wire [8:0] d_lrv  = cm_span ? pre_lr : {1'b0, pre_lr[7:0]};
  // 7: the context's last address is 255, or 511 when it spans
  wire       d_last = cm_span ? (pre_pc == 9'd511) : (pre_pc[7:0] == 8'd255);

  // operands
  reg [31:0] d_rdv, d_rsv;
  always @(*) begin
    case (d_rd)
      2'd0: d_rdv = pre_r0;
      2'd1: d_rdv = pre_r1;
      2'd2: d_rdv = pre_r2;
      default: d_rdv = pre_r3;
    endcase
    case (d_rs)
      2'd0: d_rsv = pre_r0;
      2'd1: d_rsv = pre_r1;
      2'd2: d_rsv = pre_r2;
      default: d_rsv = pre_r3;
    endcase
  end

  // 7.1 arithmetic
  wire [32:0] d_add33  = {1'b0, d_rdv} + {1'b0, d_rsv};       // ADD: C = carry out of bit 31
  wire [32:0] d_addi33 = {1'b0, d_rdv} + {1'b0, d_imm32};     // ADDI: C = carry
  wire [31:0] d_subv   = d_rdv - d_rsv;                       // SUB, CMP
  wire        d_subb   = (d_rdv < d_rsv);                     // borrow, unsigned
  wire [31:0] d_cmpiv  = d_rdv - d_imm32;                     // CMPI
  wire        d_cmpib  = (d_rdv < d_imm32);
  // SHL: n=0: no change, C=0; else C = R[rd][32-n], R[rd] <<= n
  wire [31:0] d_shlv   = d_rdv << d_n;                        // n = 0: unchanged
  wire [31:0] d_shl_m1 = d_rdv << (d_n - 5'd1);               // bit 31 is R[rd][32-n], n > 0
  wire        d_shlc   = (d_n != 5'd0) & d_shl_m1[31];
  // SHR: n=0: no change, C=0; else C = R[rd][n-1], R[rd] >>= n (logical)
  wire [31:0] d_shrv   = d_rdv >> d_n;
  wire [31:0] d_shr_m1 = d_rdv >> (d_n - 5'd1);               // bit 0 is R[rd][n-1], n > 0
  wire        d_shrc   = (d_n != 5'd0) & d_shr_m1[0];
  // 7.2 DJNZ: lc = lc - 1 (16-bit wrap); PC = t if the new lc != 0
  wire [15:0] d_lc_dec = pre_lc - 16'd1;
  // 7.3: ADDT tmr = tmr + zext(imm), ADDTR tmr = tmr + R[rs] (mod 2^32)
  wire [32:0] d_addt33  = {1'b0, pre_tmr} + {1'b0, d_imm24};
  wire [32:0] d_addtr33 = {1'b0, pre_tmr} + {1'b0, d_rsv};
  // WAITT: COMPLETE if reached(c, tmr), that is (c - tmr) mod 2^32 < 2^31 (notation, top)
  wire [31:0] d_tdiff   = cm_now - pre_tmr;
  wire        d_reached = ~d_tdiff[31];
  wire        d_twait   = d_waitt & ~d_reached;   // WAITT waits

  // ---- 6.2 admission, in its order, for EMIT, EMITR, EMITX, EXPECT, SAMPLE, SEND
  // ahead(a, b): 1 <= (a - b) mod 2^32 < 2^31 (notation, top)
  function ahead;
    input [31:0] a, b;
    reg   [31:0] x;
    begin
      x = a - b;
      ahead = (x != 32'd0) & ~x[31];
    end
  endfunction
  wire [31:0] d_dl   = pre_tmr;                         // 6.1: deadline = tmr during c
  wire [7:0]  d_mask = d_pl[23:16];
  reg  [31:0] d_tail;                                   // tail(Q).deadline
  always @(*) begin
    case (d_qn)
      3'd1:    d_tail = d_qq[55:24];
      3'd2:    d_tail = d_qq[113:82];
      3'd3:    d_tail = d_qq[171:140];
      default: d_tail = d_qq[229:198];
    endcase
  end
  // 5: the logical pins context k owns (phys(k, j) exists, is owned, and its owner is k)
  reg [7:0] d_own;
  integer   i_pin;
  always @(*) begin
    for (i_pin = 0; i_pin < 8; i_pin = i_pin + 1) begin
      if (!cfg_ctx_bank[cm_ctx])
        d_own[i_pin] = cfg_pin_owned[i_pin] & (cfg_pin_owner[2*i_pin +: 2] == cm_ctx);
      else if (i_pin < 5)
        d_own[i_pin] = cfg_pin_owned[8+i_pin] & (cfg_pin_owner[2*(8+i_pin) +: 2] == cm_ctx);
      else
        d_own[i_pin] = 1'b0;
    end
  end
  // 6.6: the line unit's claimed pins are its owner's logical pins, so they bear on the
  // owner's own masks; any other context does not own them anyway (5)
  wire       d_lu_mine = pre_lu_owned & (pre_lu_owner == cm_ctx);
  wire [7:0] d_claim   = d_lu_mine ? pre_lu_claimed : 8'd0;
  wire d_full     = (d_qn == 3'd4);                                         // step 1
  wire d_a_mask   = (d_drive | d_expect) & (d_mask == 8'd0);                // step 2
  wire d_a_unown  = d_drive & (|(d_mask & ~d_own));                         // step 3
  wire d_a_claim  = d_drive & (|(d_mask & d_claim));
  // SEND: not the owner, unconfigured, or no tx_pins (a configuration with tx_pins claims
  // at least its data pin, so "has tx_pins" is a nonzero claimed vector)
  wire d_s_notown = d_send & ~d_lu_mine;
  wire d_s_unconf = d_send & d_lu_mine & ~pre_lu_conf;
  wire d_s_notx   = d_send & d_lu_mine & pre_lu_conf & (pre_lu_claimed == 8'd0);
  wire d_a_owner  = d_a_unown | d_a_claim | d_s_notown | d_s_unconf | d_s_notx;
  wire d_s_eng    = d_send & lu_engaged(pre_lu_mode);                       // SEND: WAIT
  wire d_a_past   = ~ahead(d_dl, cm_now);                                   // step 4
  wire d_a_order  = (d_qn != 3'd0) & ~ahead(d_dl, d_tail);                  // step 5
  wire d_a_ovl    = d_expect & pre_eseen & ~ahead(d_dl, pre_lee);           // step 6
  wire d_ev       = d_t3 & ~d_illegal;
  wire d_ev_wait  = d_ev & (d_full | (~d_a_mask & ~d_a_owner & d_s_eng));
  reg  [2:0] d_asub;          // the ADMIT subcode of a refusal, 0 if none
  always @(*) begin
    d_asub = 3'd0;
    if (d_ev & ~d_full) begin
      if (d_a_mask)              d_asub = ENC_ADMIT_MASK[2:0];
      else if (d_a_owner)        d_asub = ENC_ADMIT_OWNER[2:0];
      else if (d_s_eng)          d_asub = 3'd0;
      else if (d_a_past)         d_asub = d_send ? 3'd0 : ENC_ADMIT_PAST[2:0];
      else if (d_a_order)        d_asub = ENC_ADMIT_ORDER[2:0];
      else if (d_a_ovl)          d_asub = ENC_ADMIT_OVERLAP[2:0];
    end
  end
  wire d_adm_fault = (d_asub != 3'd0);
  // SEND past its deadline is dropped: COMPLETE, no entry, Z = 0, no other effect
  wire d_drop  = d_ev & d_send & ~d_full & ~d_a_owner & ~d_s_eng & d_a_past;
  // admitted (before PC_RANGE, which can still turn it into a fault)
  wire d_admit = d_ev & ~d_ev_wait & ~d_adm_fault & ~d_drop;
  // EMITR's rs is format PR's, [15:14]
  wire [31:0] d_prv = (d_pl[15:14] == 2'd0) ? pre_r0 : (d_pl[15:14] == 2'd1) ? pre_r1 :
                      (d_pl[15:14] == 2'd2) ? pre_r2 : pre_r3;
  // 7.4: the new entry
  wire        d_xb = d_pl[15] ? pre_sh[31] : pre_sh[0];          // EMITX: dir ? sh[31] : sh[0]
  wire [1:0]  d_nk = d_drive ? K_DRIVE : d_expect ? K_EXPECT : d_sample ? K_SAMPLE : K_LINE;
  wire [23:0] d_np = d_emit   ? d_pl :
                     d_emitr  ? {d_mask, d_prv[7:0], d_pl[7:0]} :
                     d_emitx  ? {d_mask, (d_xb ? d_mask : 8'd0), d_pl[7:0]} :
                     d_expect ? d_pl :
                     d_sample ? {5'd0, d_pl[18:16], d_pl[15], 15'd0} :
                                24'd0;                            // LINE: command not on the ports
  wire [57:0] d_new  = {d_nk, d_dl, d_np};
  // 7.4 telemetry on COMPLETE (admitted): lead = (d - c) mod 2^32
  wire [31:0] d_lead   = d_dl - cm_now;
  wire [15:0] d_lead16 = (d_lead > 32'h0000FFFF) ? 16'hFFFF : d_lead[15:0];
  wire [2:0]  d_occ    = d_qn + 3'd1;                              // |Q| + 1

  // ---- TIER 4 semantics (cycle c)
  // 5: sync_c, the context's logical view of the synchronised pins: bank 0, pins 0..7; bank 1,
  // pins 8..12 as logical 0..4 and 0 for logical 5..7
  wire [7:0]  d_sync = cfg_ctx_bank[cm_ctx] ? {3'd0, phys_sync[12:8]} : phys_sync[7:0];
  // 7.3 WAITP (format PW): a match completes with Z = 1, else reached(c, tmr) with Z = 0,
  // else WAIT
  wire        d_wp_match = ((d_sync & d_pl[23:16]) == d_pl[15:8]);
  wire        d_wp_wait  = d_waitp & ~d_wp_match & ~d_reached;
  // 7.3 GETI: WAIT while the FIFO during cycle c holds any SAMPLE entry, one retiring at E_c
  // included
  wire        d_qs0 = (pre_q_cnt > 3'd0) & (pre_q[57:56]   == K_SAMPLE);
  wire        d_qs1 = (pre_q_cnt > 3'd1) & (pre_q[115:114] == K_SAMPLE);
  wire        d_qs2 = (pre_q_cnt > 3'd2) & (pre_q[173:172] == K_SAMPLE);
  wire        d_qs3 = (pre_q_cnt > 3'd3) & (pre_q[231:230] == K_SAMPLE);
  wire        d_gi_wait = d_geti & (d_qs0 | d_qs1 | d_qs2 | d_qs3);
  // a SAMPLE retiring at E_c changes ish (6.4), which this checker does not model
  wire        d_sret = d_retire & (pre_q[57:56] == K_SAMPLE);
  // 7.5: POP waits on an empty RX mailbox, PUSH on a full TX mailbox, as during cycle c
  wire        d_pop_wait  = d_pop  & (p_rxn == 2'd0);
  wire        d_push_wait = d_push & (p_txn == 2'd2);
  // 7.6: a = (R[rb][7:0] + imm8) mod 256 (format TM: ra [23:22] as rd, rb [21:20] as rs);
  // LDT and STT WAIT in a cycle the line unit has the tape port (6.6)
  wire [7:0]  d_ta      = d_rsv[7:0] + d_pl[7:0];
  wire        d_tp_wait = (d_ldt | d_stt) & lu_port;
  // 7.7 LINE: v = R[rs], op = v[31:28]. In order: op 4..15 ILLEGAL; OWNER for a CONFIG
  // when another context is the owner, any other op by a context that is not the owner
  // (no owner included), RECV or CRC while unconfigured; then WAIT if engaged, except OFF
  wire [3:0]  d_lop    = d_rsv[31:28];
  wire        d_l_ill  = d_line & (d_lop[3] | d_lop[2]);
  wire        d_l_ocfg = d_line & (d_lop == 4'd3) & pre_lu_owned & (pre_lu_owner != cm_ctx);
  wire        d_l_onot = d_line & (d_lop[3:2] == 2'b00) & (d_lop != 4'd3) & ~d_lu_mine;
  wire        d_l_ounc = d_line & ((d_lop == 4'd1) | (d_lop == 4'd2)) & d_lu_mine & ~pre_lu_conf;
  wire        d_l_own  = ~d_l_ill & (d_l_ocfg | d_l_onot | d_l_ounc);
  wire        d_l_wait = d_line & ~d_l_ill & ~d_l_own & (d_lop != 4'd0) & lu_engaged(pre_lu_mode);
  wire        d_l_take = d_line & (d_lop == 4'd3) & ~pre_lu_owned;     // a CONFIG with no owner
  // 7.7 GETL: OWNER if not the owner; else not busy: COMPLETE, R[rd] = status, Z = 1,
  // tmr = e; else reached(c, tmr): COMPLETE, R[rd] = status, Z = 0; else WAIT
  wire        d_gl_own  = d_getl & ~d_lu_mine;
  wire        d_gl_wait = d_getl & ~d_gl_own & lu_busy & ~d_reached;
  // 7.8 MON: OWNER if the monitor has an owner and it is not this context
  wire [3:0]  d_k1     = 4'd1 << cm_ctx;
  wire        d_m_mine = |(mon_owner & d_k1);
  wire        d_m_own  = d_mon & (mon_owner != 4'd0) & ~d_m_mine;
  // 7.8 GETM sel = imm[15:0]: 0..2 the monitor for its owner, 3 and 4 the line unit's whole
  // count and crc for its owner, 0 otherwise
  reg  [31:0] d_gmv;
  always @(*) begin
    case (d_imm)
      16'd0:   d_gmv = d_m_mine  ? mon_words[31:0]  : 32'd0;
      16'd1:   d_gmv = d_m_mine  ? mon_words[63:32] : 32'd0;
      16'd2:   d_gmv = d_m_mine  ? mon_tick         : 32'd0;
      16'd3:   d_gmv = d_lu_mine ? {18'd0, lu_aux[13:0]} : 32'd0;
      16'd4:   d_gmv = d_lu_mine ? lu_aux[45:14]    : 32'd0;
      default: d_gmv = 32'd0;
    endcase
  end
  // the outcome before PC_RANGE: a fault (ILLEGAL by LINE's op, ADMIT/OWNER), a WAIT, or a
  // completion (d_t4go)
  wire d_t4own   = ~d_illegal & (d_l_own | d_gl_own | d_m_own);
  wire d_t4fault = (~d_illegal & d_l_ill) | d_t4own;
  wire d_t4wait  = ~d_illegal & ~d_t4fault &
                   (d_wp_wait | d_gi_wait | d_pop_wait | d_push_wait | d_tp_wait | d_l_wait | d_gl_wait);
  wire d_t4go    = d_t4 & ~d_illegal & ~d_t4fault & ~d_t4wait;

  // the data result, whether it is written (wr), and the flags it sets
  reg [31:0] d_res;
  reg        d_wr, d_zset, d_zv, d_cset, d_cv;
  always @(*) begin
    d_res = d_rdv; d_wr = 1'b0;
    d_zset = 1'b0; d_cset = 1'b0; d_cv = 1'b0;
    case (1'b1)
      d_mov:   begin d_res = d_rsv;                     d_wr = 1'b1; d_zset = 1'b1; end
      d_movi:  begin d_res = d_imm32;                   d_wr = 1'b1; d_zset = 1'b1; end
      d_movhi: begin d_res = {d_imm, d_rdv[15:0]};      d_wr = 1'b1; d_zset = 1'b1; end
      d_add:   begin d_res = d_add33[31:0];  d_cv = d_add33[32];
                     d_wr = 1'b1; d_zset = 1'b1; d_cset = 1'b1; end
      d_addi:  begin d_res = d_addi33[31:0]; d_cv = d_addi33[32];
                     d_wr = 1'b1; d_zset = 1'b1; d_cset = 1'b1; end
      d_sub:   begin d_res = d_subv;  d_cv = d_subb;  d_wr = 1'b1; d_zset = 1'b1; d_cset = 1'b1; end
      d_cmp:   begin d_res = d_subv;  d_cv = d_subb;               d_zset = 1'b1; d_cset = 1'b1; end
      d_cmpi:  begin d_res = d_cmpiv; d_cv = d_cmpib;              d_zset = 1'b1; d_cset = 1'b1; end
      d_and:   begin d_res = d_rdv & d_rsv;             d_wr = 1'b1; d_zset = 1'b1; end
      d_or:    begin d_res = d_rdv | d_rsv;             d_wr = 1'b1; d_zset = 1'b1; end
      d_xor:   begin d_res = d_rdv ^ d_rsv;             d_wr = 1'b1; d_zset = 1'b1; end
      d_tsti:  begin d_res = d_rdv & d_imm32;                        d_zset = 1'b1; end
      d_shl:   begin d_res = d_shlv; d_cv = d_shlc;  d_wr = 1'b1; d_zset = 1'b1; d_cset = 1'b1; end
      d_shr:   begin d_res = d_shrv; d_cv = d_shrc;  d_wr = 1'b1; d_zset = 1'b1; d_cset = 1'b1; end
      d_stsh:  begin d_res = pre_sh;                    d_wr = 1'b1; end
      d_rdtime: begin d_res = cm_now;                   d_wr = 1'b1; end   // R[rd] = c; no flags
      // tier 4 (applied only on COMPLETE): GETP zext(sync_c), Z; GETI ish, Z; GETX the
      // checker's word, Z; POP the RX head, no flags; GETC the capture status, Z; GETL the
      // line unit's status (Z below); GETM the selected word, Z (7.8 as corrected)
      d_getp:  begin d_res = {24'd0, d_sync};           d_wr = 1'b1; d_zset = 1'b1; end
      d_geti:  begin d_res = pre_ish;                   d_wr = 1'b1; d_zset = 1'b1; end
      d_getx:  begin d_res = {pre_fail, pre_pass, 14'd0, pre_chk_active, pre_failed};
                                                        d_wr = 1'b1; d_zset = 1'b1; end
      d_pop:   begin d_res = p_rx0;                     d_wr = 1'b1; end
      d_getc:  begin d_res = cap_status;                d_wr = 1'b1; d_zset = 1'b1; end
      d_getl:  begin d_res = lu_status;                 d_wr = 1'b1; end
      d_getm:  begin d_res = d_gmv;                     d_wr = 1'b1; d_zset = 1'b1; end
      default: ;
    endcase
    // Z = (result == 0); for TSTI, Z = (R[rd] & zext(imm)) == 0, the same thing
    d_zv = (d_res == 32'd0);
  end

  // 7.2 conditions and the next PC
  reg d_tk;                   // a conditional branch (JZ JNZ JC JNC DJNZ) is taken
  always @(*) begin
    case (1'b1)
      d_jz:    d_tk = pre_z;
      d_jnz:   d_tk = ~pre_z;
      d_jc:    d_tk = pre_c;
      d_jnc:   d_tk = ~pre_c;
      d_djnz:  d_tk = (d_lc_dec != 16'd0);
      default: d_tk = 1'b0;
    endcase
  end
  wire d_cond = d_jz | d_jnz | d_jc | d_jnc | d_djnz;
  // sequential next PC: everything but JMP, CALL, RET and a taken conditional branch; of
  // tier 2, RDTIME, SETT, ADDT, ADDTR and a WAITT that completes. HALT's next PC is PC
  // (3, the pair-mode table) and a WAIT is not COMPLETE, so neither is PC_RANGE.
  wire d_seq  = (d_t1 | d_rdtime | d_sett | d_addt | d_addtr | (d_waitt & d_reached) |
                 d_admit | d_drop | d_t4go) &
                ~(d_jmp | d_call | d_ret | (d_cond & d_tk));
  wire [8:0] d_next = (d_jmp | d_call | (d_cond & d_tk)) ? d_tgt :
                      d_ret                              ? d_lrv :
                                                           d_pc1;

  // 7 preamble: PC_RANGE at the last address for a COMPLETE with a sequential next PC, or a
  // CALL there. An ILLEGAL word's outcome is never COMPLETE, so ILLEGAL takes precedence.
  wire d_pcr   = ~d_illegal & d_last & (d_seq | d_call);
  wire d_fault = d_illegal | d_pcr | d_adm_fault | d_t4fault;
  wire d_ok    = ~d_fault;           // a checked commit that is not a fault completes
  wire d_t4c   = d_t4go & ~d_pcr;    // a tier-4 instruction that completes

  // 7.5, the mailboxes after E_c: the commit's POP (removes the RX head) or PUSH (appends
  // R[rs] to TX), and the host's same-edge RX push (taken if RX is not full during cycle c)
  // and TX pop (taken if TX is not empty during cycle c). The commit observes the mailboxes
  // during cycle c, so a host push lands behind whatever the POP leaves, and a host pop takes
  // the head before the PUSH's word is appended.
  reg [1:0]  s_rxn, s_txn;
  reg [31:0] s_rx0, s_rx1, s_tx0, s_tx1;
  always @(*) begin
    s_rxn = p_rxn; s_rx0 = p_rx0; s_rx1 = p_rx1;
    if (d_pop & d_t4c) begin
      s_rxn = p_rxn - 2'd1; s_rx0 = p_rx1;
    end
    if (cm_h_rx_push & (p_rxn != 2'd2)) begin
      if (s_rxn == 2'd0) s_rx0 = cm_h_rx_data;
      else               s_rx1 = cm_h_rx_data;
      s_rxn = s_rxn + 2'd1;
    end
    s_txn = p_txn; s_tx0 = p_tx0; s_tx1 = p_tx1;
    if (cm_h_tx_pop & (p_txn != 2'd0)) begin
      s_txn = p_txn - 2'd1; s_tx0 = p_tx1;
    end
    if (d_push & d_t4c) begin
      if (s_txn == 2'd0) s_tx0 = d_rsv;
      else               s_tx1 = d_rsv;
      s_txn = s_txn + 2'd1;
    end
  end

  // expected state after E_c
  reg [31:0] s_r0, s_r1, s_r2, s_r3, s_sh, s_tmr;
  reg [15:0] s_lc;
  reg        s_z, s_c, s_irq_p;
  reg [7:0]  s_irq_code;
  reg [8:0]  s_pc, s_lr;
  reg [1:0]  s_state, s_out;
  reg [2:0]  s_qcnt, s_maxo;
  reg [231:0] s_q;
  reg [15:0] s_minl, s_wf;
  reg [31:0] s_lee;
  reg        s_eseen;
  always @(*) begin
    // nothing changes unless listed
    s_r0 = pre_r0; s_r1 = pre_r1; s_r2 = pre_r2; s_r3 = pre_r3;
    s_sh = pre_sh; s_lc = pre_lc; s_tmr = pre_tmr;
    s_z = pre_z; s_c = pre_c;
    s_pc = pre_pc; s_lr = pre_lr;
    // 10, IRQ clear: the host's clear at E_c clears irq_pending, unless an IRQ of this
    // context commits (completes) at that edge; the set wins and its code is recorded
    s_irq_p = cm_irq_clear ? 1'b0 : pre_irq_p;
    s_irq_code = pre_irq_code;
    // the FIFO after E_c is Q unless the instruction appends to it or faults (8: flushed);
    // telemetry and last_expect_end change only as 7.4 and 6.2 say
    s_qcnt = d_qn; s_q = d_qq;
    s_minl = pre_min_lead; s_maxo = pre_max_occ; s_wf = pre_wait_full;
    s_lee = pre_lee; s_eseen = pre_eseen;
    if (d_fault) begin
      s_qcnt = 3'd0;
      // 8: the context is FAULTED at E_c; no effect of the instruction, PC stays at it
      s_out = OUT_FAULT;
      s_state = ST_FAULTED;
    end else if (d_twait | d_t4wait) begin
      // 3, WAIT: no architectural effect except the telemetry counter of 7.4 (not on these
      // ports); PC unchanged, still RUNNING
      s_out = OUT_WAIT;
      s_state = ST_RUNNING;
    end else if (d_ev_wait) begin
      // 6.2 step 1 (full; 7.4: wait_full, 16 bits, saturating) or a SEND while the line
      // unit is engaged: WAIT, nothing else
      s_out = OUT_WAIT;
      s_state = ST_RUNNING;
      if (d_full) s_wf = (pre_wait_full == 16'hFFFF) ? 16'hFFFF : pre_wait_full + 16'd1;
    end else if (d_halt) begin
      // 7.2 HALT: HALTED with PC unchanged, or WAIT; which one depends on the state after
      // E_c and is decided in cycle c+1 (h_go below). Either way nothing else changes.
      s_out = OUT_COMPLETE;
      s_state = ST_HALTED;
    end else begin
      s_out = OUT_COMPLETE;
      s_state = ST_RUNNING;
      s_pc = d_next;
      if (d_sett)  s_tmr = d_rsv;
      // 7.4 EMITX on COMPLETE: sh = dir ? sh<<1 : sh>>1, C = b; SEND: Z = admitted
      if (d_emitx) begin
        s_sh = d_pl[15] ? {pre_sh[30:0], 1'b0} : {1'b0, pre_sh[31:1]};
        s_c = d_xb;
      end
      if (d_send) s_z = d_admit;
      if (d_admit) begin
        s_qcnt = d_occ;
        case (d_qn)
          3'd0:    s_q[57:0]    = d_new;
          3'd1:    s_q[115:58]  = d_new;
          3'd2:    s_q[173:116] = d_new;
          default: s_q[231:174] = d_new;
        endcase
        s_minl = (d_lead16 < pre_min_lead) ? d_lead16 : pre_min_lead;
        s_maxo = (d_occ > pre_max_occ) ? d_occ : pre_max_occ;
        if (d_expect) begin
          s_lee = d_dl + {25'd0, d_pl[6:0]};       // last_expect_end = d + window
          s_eseen = 1'b1;
        end
      end
      if (d_addt)  s_tmr = d_addt33[31:0];
      if (d_addtr) s_tmr = d_addtr33[31:0];
      if (d_wr) begin
        case (d_rd)
          2'd0: s_r0 = d_res;
          2'd1: s_r1 = d_res;
          2'd2: s_r2 = d_res;
          default: s_r3 = d_res;
        endcase
      end
      if (d_zset) s_z = d_zv;
      if (d_cset) s_c = d_cv;
      // tier 4 flags: WAITP Z = matched; RXREADY Z = RX empty; TXREADY Z = TX full; GETL
      // Z = not busy, and then tmr = e
      if (d_waitp) s_z = d_wp_match;
      if (d_rxrdy) s_z = (p_rxn == 2'd0);
      if (d_txrdy) s_z = (p_txn == 2'd2);
      if (d_getl) begin
        s_z = ~lu_busy;
        if (!lu_busy) s_tmr = lu_e;
      end
      if (d_ldsh) s_sh = d_rsv;
      if (d_ldlc) s_lc = d_imm;
      if (d_djnz) s_lc = d_lc_dec;
      if (d_call) s_lr = d_pc1;
      if (d_irq) begin
        s_irq_p = 1'b1;
        s_irq_code = d_code;
      end
    end
  end

  // ---- registered at E_c
  reg        q_chk, e_fault, e_wr;
  reg [31:0] e_r0, e_r1, e_r2, e_r3, e_sh, e_tmr, e_res;
  reg [15:0] e_lc;
  reg        e_z, e_c, e_irq_p;
  reg [7:0]  e_irq_code;
  reg [8:0]  e_pc, e_lr;
  reg [1:0]  e_state, e_out, q_rd;
  reg [2:0]  e_cause;
  reg [31:0] q_now;
  reg        q_ffpre;
  // for the covers
  reg [7:0]  q_op;
  reg [23:0] q_used;
  reg        q_tk, q_lc0, q_n0, q_lo_nz, q_clr, q_unl, q_rsv, q_b8, q_pcr;
  reg        q_span, q_tgt8, q_data;
  reg        e_halt, q_lu_eng, q_twrap;
  reg [2:0]  e_qcnt, e_maxo, e_sub;
  reg [231:0] e_q;
  reg [15:0] e_minl, e_wf;
  reg [31:0] e_lee;
  reg        e_eseen;
  // for the tier-3 covers
  reg        q_full, q_retire, q_drop, q_admit, q_seng, q_xdir, q_xb, q_bank, q_leadbig, q_ev;
  reg        q_unown, q_claim, q_notown, q_unconf, q_notx, q_wfmax;
  reg [2:0]  q_asub, q_pcnt;
  reg [15:0] q_minl_pre;
  reg [2:0]  q_ff_cause, q_ff_sub;
  reg [1:0]  q_ff_ctx;
  reg [8:0]  q_ff_pc;
  reg [31:0] q_ff_tick, q_tdiff;
  always @(posedge clk) begin
    q_chk   <= rst_n & cm_valid & d_chk;
    e_fault <= d_fault;
    e_wr    <= d_wr & d_ok & ~d_t4wait;
    e_r0 <= s_r0; e_r1 <= s_r1; e_r2 <= s_r2; e_r3 <= s_r3;
    e_sh <= s_sh; e_lc <= s_lc; e_tmr <= s_tmr;
    e_z <= s_z; e_c <= s_c;
    e_pc <= s_pc; e_lr <= s_lr;
    e_irq_p <= s_irq_p; e_irq_code <= s_irq_code;
    e_state <= s_state; e_out <= s_out;
    e_res <= d_res; q_rd <= d_rd;
    e_cause <= (d_illegal | d_l_ill) ? CA_ILLEGAL : (d_adm_fault | d_t4own) ? CA_ADMIT : CA_PC_RANGE;
    e_sub   <= (d_illegal | d_l_ill) ? 3'd0 : d_t4own ? ENC_ADMIT_OWNER[2:0] : d_asub;
    e_qcnt <= s_qcnt; e_q <= s_q;
    e_minl <= s_minl; e_maxo <= s_maxo; e_wf <= s_wf;
    e_lee  <= s_lee;  e_eseen <= s_eseen;
    q_ev     <= d_ev;
    q_full   <= d_full;
    q_retire <= d_retire;
    q_pcnt   <= pre_q_cnt;
    q_drop   <= d_drop & ~d_fault;
    q_admit  <= d_admit & ~d_fault;
    q_seng   <= d_s_eng;
    q_asub   <= d_asub;
    q_unown  <= d_a_unown;  q_claim <= d_a_claim & ~d_a_unown;
    q_notown <= d_s_notown; q_unconf <= d_s_unconf; q_notx <= d_s_notx;
    q_xdir   <= d_pl[15];   q_xb <= d_xb;
    q_bank   <= cfg_ctx_bank[cm_ctx];
    q_leadbig <= (d_lead > 32'h0000FFFF);
    q_minl_pre <= pre_min_lead;
    q_wfmax  <= (pre_wait_full == 16'hFFFF);
    q_now   <= cm_now;
    q_ffpre <= pre_ff_valid;
    q_op    <= d_op;
    q_used  <= d_used;
    q_data  <= d_data;
    q_tk    <= d_tk;
    q_lc0   <= (pre_lc == 16'd0);
    q_n0    <= (d_n == 5'd0);
    q_lo_nz <= (d_rdv[15:0] != 16'd0);
    q_clr   <= cm_irq_clear;
    q_unl   <= d_unl;
    q_rsv   <= d_rsv_bad;
    q_b8    <= d_b8_bad;
    q_pcr   <= d_pcr;
    q_span  <= cm_span;
    q_tgt8  <= d_pl[8];
    // tier 2
    e_halt  <= d_halt & ~d_fault;
    q_lu_eng <= pre_lu_owned & (pre_lu_owner == cm_ctx) & lu_engaged(pre_lu_mode);
    q_ff_cause <= pre_ff_cause; q_ff_sub <= pre_ff_sub; q_ff_ctx <= pre_ff_ctx;
    q_ff_pc    <= pre_ff_pc;    q_ff_tick <= pre_ff_tick;
    q_tdiff  <= d_tdiff;
    q_twrap  <= (d_addt & d_addt33[32]) | (d_addtr & d_addtr33[32]) |
                (d_sett & (d_rsv < pre_tmr));
  end

  // 6.6: the line unit is engaged in CFG, PEND, TX, RX and CRC (not IDLE, not ARM)
  function lu_engaged;
    input [2:0] mode;
    lu_engaged = (mode == 3'd1) | (mode == 3'd2) | (mode == 3'd3) | (mode == 3'd5) |
                 (mode == 3'd6);
  endfunction

  // 7.2 HALT, decided on the state after E_c (the FIFO after retirement at E_c, which a HALT
  // pushes nothing into, and the checker after E_c) and on the line unit during cycle c
  wire       h_go    = (post_fifo_cnt == 3'd0) & ~post_chk_active & ~q_lu_eng;
  wire [1:0] x_out   = e_halt ? (h_go ? OUT_COMPLETE : OUT_WAIT)   : e_out;
  wire [1:0] x_state = e_halt ? (h_go ? ST_HALTED    : ST_RUNNING) : e_state;

  // the written register (data instructions that write R[rd]) as reported
  reg [31:0] post_rd;
  always @(*) begin
    case (q_rd)
      2'd0: post_rd = post_r0;
      2'd1: post_rd = post_r1;
      2'd2: post_rd = post_r2;
      default: post_rd = post_r3;
    endcase
  end

  wire o_ok    = (dbg_commit_outcome == OUT_COMPLETE);
  wire o_fault = (dbg_commit_outcome == OUT_FAULT);
  // the first-fault record names this commit's context
  wire ff_ours = (post_ff_ctx == q_ctx);

  // ---- tier 4, registered at E_c
  reg        e_ldt, q_msane, q_sret, e_luo, e_cap_en, e_cap_dis, q_tack;
  reg [1:0]  e_luown, e_rxn, e_txn;
  reg [3:0]  e_mono;
  reg [31:0] e_ish, e_rx0, e_rx1, e_tx0, e_tx1, q_cap_status, q_cap_cfg;
  reg [26:0] q_cap_v;
  reg [7:0]  q_cap_rptr, q_tack_v;
  // for the tier-4 covers
  reg        q_t4own, q_lill, q_locfg, q_lonot, q_lounc, q_lwait, q_ltake;
  reg        q_mmine, q_lumine, q_mnone, q_wpm, q_lbusy, q_hpush_tk, q_hpop_tk, q_capen;
  reg [3:0]  q_lop;
  reg [15:0] q_sel;
  always @(posedge clk) begin
    // LDT: R[ra] after E_c is the word the tape returns in cycle c+1
    e_ldt   <= d_ldt & d_t4c;
    // ish: 0 after a GETI that completes; else unchanged, unless a SAMPLE retires at E_c
    // (a fault retires nothing, 8)
    e_ish   <= (d_geti & d_t4c) ? 32'd0 : pre_ish;
    q_sret  <= d_sret & ~d_fault;
    q_msane <= d_msane;
    e_rxn <= s_rxn; e_rx0 <= s_rx0; e_rx1 <= s_rx1;
    e_txn <= s_txn; e_tx0 <= s_tx0; e_tx1 <= s_tx1;
    // 7.7: a CONFIG with no owner makes this context the owner at E_c; nothing else in a
    // run changes the owner (6.6, 8)
    e_luo   <= pre_lu_owned | (d_l_take & d_t4c);
    e_luown <= (d_l_take & d_t4c) ? cm_ctx : pre_lu_owner;
    // 7.8: a MON with no owner makes this context the owner at E_c; nothing else in a run does
    e_mono  <= (d_mon & d_t4c & (mon_owner == 4'd0)) ? d_k1 : mon_owner;
    // 7.6 CAPT, v = R[rs]
    e_cap_en  <= d_capt & d_t4c &  d_rsv[31];
    e_cap_dis <= d_capt & d_t4c & ~d_rsv[31];
    q_cap_v   <= d_rsv[26:0];
    q_cap_status <= cap_status; q_cap_cfg <= cap_cfg; q_cap_rptr <= cap_rptr;
    q_tack    <= tape_ack;      q_tack_v  <= tape_ack_v;
    // covers
    q_t4own  <= d_t4own;
    q_lill   <= d_l_ill;
    q_locfg  <= d_l_ocfg; q_lonot <= d_l_onot; q_lounc <= d_l_ounc;
    q_lwait  <= d_l_wait;
    q_ltake  <= d_l_take;
    q_lop    <= d_lop;
    q_sel    <= d_imm;
    q_mmine  <= d_m_mine;
    q_lumine <= d_lu_mine;
    q_mnone  <= (mon_owner == 4'd0);
    q_wpm    <= d_wp_match;
    q_lbusy  <= lu_busy;
    q_hpush_tk <= cm_h_rx_push & (p_rxn != 2'd2);
    q_hpop_tk  <= cm_h_tx_pop  & (p_txn != 2'd0);
    q_capen  <= d_rsv[31];
  end
  wire [31:0] x_r0 = (e_ldt && q_rd == 2'd0) ? tape_rdata : e_r0;
  wire [31:0] x_r1 = (e_ldt && q_rd == 2'd1) ? tape_rdata : e_r1;
  wire [31:0] x_r2 = (e_ldt && q_rd == 2'd2) ? tape_rdata : e_r2;
  wire [31:0] x_r3 = (e_ldt && q_rd == 2'd3) ? tape_rdata : e_r3;

  always @(`ISA_AT) if (f_past && rst_n && q_chk) begin
    // the outcome: WAIT only for a WAITT not reached or a HALT whose conditions fail; an
    // ILLEGAL word of any opcode (wait-class included) is FAULT
    isa_out:      assert (dbg_commit_outcome == x_out);
    isa_state:    assert (post_state == x_state);
    isa_pc:       assert (post_pc == e_pc);
    isa_lr:       assert (post_lr == e_lr);
    isa_r0:       assert (post_r0 == x_r0);
    isa_r1:       assert (post_r1 == x_r1);
    isa_r2:       assert (post_r2 == x_r2);
    isa_r3:       assert (post_r3 == x_r3);
    isa_rd:       assert (!e_wr || post_rd == e_res);
    isa_sh:       assert (post_sh == e_sh);
    isa_lc:       assert (post_lc == e_lc);
    isa_tmr:      assert (post_tmr == e_tmr);

    // the FIFO after E_c (6.2, 6.3, 8), entry by entry in order; a LINE entry's command is
    // not on the ports, so only its kind and deadline are compared
    isa_q_cnt:    assert (post_q_cnt == e_qcnt);
    isa_q0:       assert (e_qcnt <= 3'd0 || ((post_q[57:0] ^ e_q[57:0]) &
                          (e_q[57:56] == K_LINE ? {2'b11, 32'hFFFFFFFF, 24'd0} : {58{1'b1}})) == 58'd0);
    isa_q1:       assert (e_qcnt <= 3'd1 || ((post_q[115:58] ^ e_q[115:58]) &
                          (e_q[115:114] == K_LINE ? {2'b11, 32'hFFFFFFFF, 24'd0} : {58{1'b1}})) == 58'd0);
    isa_q2:       assert (e_qcnt <= 3'd2 || ((post_q[173:116] ^ e_q[173:116]) &
                          (e_q[173:172] == K_LINE ? {2'b11, 32'hFFFFFFFF, 24'd0} : {58{1'b1}})) == 58'd0);
    isa_q3:       assert (e_qcnt <= 3'd3 || ((post_q[231:174] ^ e_q[231:174]) &
                          (e_q[231:230] == K_LINE ? {2'b11, 32'hFFFFFFFF, 24'd0} : {58{1'b1}})) == 58'd0);
    // 7.4 telemetry and 6.2 step 6's last_expect_end
    isa_tel_wait_full: assert (post_wait_full == e_wf);
    isa_tel_min_lead:  assert (post_min_lead == e_minl);
    isa_tel_max_occ:   assert (post_max_occ == e_maxo);
    isa_lee:      assert (post_lee == e_lee);
    isa_eseen:    assert (post_eseen == e_eseen);
    isa_z:        assert (post_z == e_z);
    isa_c:        assert (post_c == e_c);
    isa_irq_p:    assert (post_irq_p == e_irq_p);
    isa_irq_code: assert (post_irq_code == e_irq_code);

    // 8, the first-fault register: one for all contexts, cleared only at start (9), so once
    // full it stays full. If it was empty and this commit faults, it records {cause,
    // subcode 0, ctx, PC of the faulting instruction, tick}. Another context's fault at the
    // same edge (LATE_HEAD, or an ADMIT CONFIG completing at E_c) may be the one recorded;
    // no other commit can be at this edge, so a record naming another context is never
    // ILLEGAL or PC_RANGE.
    isa_ff_keep:  assert (!q_ffpre || post_ff_valid);
    if (!q_ffpre && e_fault) begin
      isa_ff_valid: assert (post_ff_valid);
      isa_ff_tick:  assert (post_ff_tick == q_now);
      isa_ff_ctx:   assert (ff_ours || post_ff_cause == CA_LATE_HEAD || post_ff_cause == CA_ADMIT);
      isa_ff_cause: assert (!ff_ours || post_ff_cause == e_cause);
      isa_ff_sub:   assert (!ff_ours || post_ff_sub == e_sub);
      isa_ff_pc:    assert (!ff_ours || post_ff_pc == q_pc);
    end
    // a commit that completes records no ILLEGAL or PC_RANGE (only another context's
    // LATE_HEAD or ADMIT CONFIG can fill the register at this edge)
    if (!q_ffpre && !e_fault)
      isa_ff_none:  assert (!post_ff_valid || post_ff_cause == CA_LATE_HEAD || post_ff_cause == CA_ADMIT);
  end

  // 8: the first-fault register keeps the first fault since start; once full, nothing
  // changes it during the run (start clears it, 9). Stated at every commit (any opcode).
  reg q_ffpre_any;
  always @(posedge clk) q_ffpre_any <= pre_ff_valid;
  always @(`ISA_AT) if (f_past && rst_n && q_valid && q_ffpre_any) begin
    isa_ff_same: assert (post_ff_valid && post_ff_cause == q_ff_cause && post_ff_sub == q_ff_sub &&
                         post_ff_ctx == q_ff_ctx && post_ff_pc == q_ff_pc &&
                         post_ff_tick == q_ff_tick);
  end

  // ---- tier 4, after E_c (cycle c+1). Registers, Z, tmr, PC, outcome, state, the record,
  // the FIFO and telemetry are the assertions above; these are the units' side.
  always @(`ISA_AT) if (f_past && rst_n && q_chk) begin
    // ish (6.4, 7.3 GETI)
    isa_ish:      assert (q_sret || post_ish == e_ish);
    // 7.5 the mailboxes after E_c, with the host's same-edge push and pop
    if (q_msane) begin
      isa_mbx_rx_cnt: assert (post_mbx[65:64] == e_rxn);
      isa_mbx_rx0:    assert (e_rxn < 2'd1 || post_mbx[31:0]   == e_rx0);
      isa_mbx_rx1:    assert (e_rxn < 2'd2 || post_mbx[63:32]  == e_rx1);
      isa_mbx_tx_cnt: assert (post_mbx[131:130] == e_txn);
      isa_mbx_tx0:    assert (e_txn < 2'd1 || post_mbx[97:66]  == e_tx0);
      isa_mbx_tx1:    assert (e_txn < 2'd2 || post_mbx[129:98] == e_tx1);
    end
    // 7.7 the line unit's owner, 7.8 the monitor's owner
    isa_lu_owned: assert (post_lu_owned == e_luo);
    isa_lu_owner: assert (!e_luo || post_lu_owner == e_luown);
    isa_mon_owner: assert (mon_owner == e_mono);
    // 7.6 CAPT. Enable: the configuration from v, en = 1, ovf = full = wrapped = 0, staging
    // empty, ptr = rptr = base (overriding a same-edge TAPE_ACK). Disable: en = 0; the
    // configuration kept; ovf kept (only 6.5 step 2 sets it, and a CAPT suppresses that
    // step); with nothing staged during cycle c step 1 does nothing, so the rest of the
    // status is kept; rptr follows a same-edge TAPE_ACK (6.5)
    if (e_cap_en) begin
      isa_capt_cfg:    assert (cap_cfg == {5'd0, q_cap_v});
      isa_capt_status: assert (cap_status == {1'b1, 23'd0, q_cap_v[7:0]});
      isa_capt_rptr:   assert (cap_rptr == q_cap_v[7:0]);
    end
    if (e_cap_dis) begin
      isa_capt_off_cfg:    assert (cap_cfg == q_cap_cfg);
      isa_capt_off_status: assert (!cap_status[31] && cap_status[30] == q_cap_status[30] &&
                                   (q_cap_status[9:8] != 2'd0 ||
                                    cap_status == {1'b0, q_cap_status[30:0]}));
      isa_capt_off_rptr:   assert (cap_rptr == (q_tack ? q_tack_v : q_cap_rptr));
    end
  end

  // ---- tier 4, the tape hand-off during cycle c (7.6). An LDT that completes reads T[a] at
  // E_c (and nothing writes the tape in that cycle: the port is the core's, 6.5, the host
  // writes only while stopped and the line unit does not have it); an STT that completes
  // writes R[ra] at a. A faulting STT writes nothing (no effect), unless the line unit has
  // the port in that cycle.
  always @(`ISA_AT) if (f_past && rst_n && cm_valid && d_chk) begin
    isa_tape_ld:   assert (!(d_ldt && d_t4c) || (tape_re && tape_addr == d_ta && !tape_we));
    isa_tape_st:   assert (!(d_stt && d_t4c) || (tape_we && tape_addr == d_ta && tape_wdata == d_rdv));
    isa_tape_none: assert (!(d_stt && d_fault && !lu_port) || !tape_we);
  end

  // ---- the retry grid (3, "Each commit has one outcome"; 7.3 note). After a WAIT at E_c,
  // PC is unchanged, the same instruction is fetched again and commits again at E_(c+4);
  // paired, the fetch made in cycle c-1 is discarded at c+2, except after HALT, which it
  // repeats, at c+2. So the next commit of that context is at c+4 (a paired HALT: c+2), at
  // the same PC, and none comes before it, as long as the context stays RUNNING and the run
  // is neither stopped nor started. The word at that PC is not compared: a host program
  // write during the run may change it (9).
  //
  // Stated for the wait-class instructions this checker decides: a legal WAITT that is not
  // reached (WAIT, known in cycle c) and a legal HALT (WAIT or HALTED, known in cycle c+1).
  // A retry is armed in cycle c itself, from the commit and the checker's own decode, so it
  // always starts from a commit inside the proof's window. A HALT that halts instead leaves
  // the context not RUNNING in cycle c+1, which cancels it.
  //
  // Per context k: rt_act<k>, rt_cnt<k> (cycles until the retry's own commit cycle: armed
  // at E_c with 3, or 1 for a paired HALT, so it is 0 in cycle c+4, or c+2), rt_two<k>
  // (paired HALT), rt_pc<k>. The retry's commit is checked as reported in the cycle after
  // that (rt_dchk<k>, rt_dpc<k>, kept apart so that a retry that waits again and re-arms does
  // not hide it). rt_ok<k>: the context RUNNING, and no stop or start pulse, in this cycle.
  // Tier 4 adds every WAIT it decides: WAITP, GETI, POP, PUSH, LINE, GETL, and LDT and STT
  // on the line unit's port (not wait-class in isa.yaml, but a WAIT like any other, 3).
  wire rt_arm = rst_n & cm_valid & d_qsane & ~d_illegal &
                (d_twait | d_halt | d_ev_wait | d_t4wait);
  wire rt_arm_two = cm_paired & d_halt;

  reg       rt_act0 = 1'b0, rt_two0 = 1'b0, rt_dchk0 = 1'b0;
  reg [2:0] rt_cnt0 = 3'd0;
  reg [8:0] rt_pc0, rt_dpc0;
  wire rt_ok0   = run_now & ~stop_now & ~start_now & ~ctx_stop_now[0] &
                  (state_all[1:0] == ST_RUNNING);
  wire rt_seen0 = dbg_commit_valid & (dbg_commit_ctx == 2'd0);
  wire rt_cm0   = cm_valid & (cm_ctx == 2'd0);
  wire rt_set0  = rt_arm & (cm_ctx == 2'd0);
  wire rt_due0  = rt_act0 & (rt_cnt0 == 3'd0);
  always @(posedge clk) begin
    rt_dchk0 <= rst_n & rt_due0 & rt_ok0;
    rt_dpc0  <= rt_pc0;
    if (!rst_n) begin
      rt_act0 <= 1'b0;
    end else if (rt_set0) begin
      rt_act0 <= 1'b1;
      rt_two0 <= rt_arm_two;
      rt_cnt0 <= rt_arm_two ? 3'd1 : 3'd3;
      rt_pc0  <= pre_pc;
    end else if (rt_act0) begin
      if (rt_due0 || !rt_ok0) rt_act0 <= 1'b0;
      rt_cnt0 <= rt_cnt0 - 3'd1;
    end
  end
  always @(`ISA_AT) if (f_past && rst_n) begin
    // the grid: no commit of the context between the WAIT and its retry (cycles c+1 to
    // c+3; c+1 for a paired HALT), and the retry at c+4 (c+2) at the same PC, as
    // reported in the cycle after. A run stop or a context stop (ctx_stop_now) at the
    // retry's own edge discards it (9), so the check is armed only without one.
    isa_retry_gap0: assert (!(rt_act0 && rt_cnt0 != 3'd0 && rt_ok0) || !rt_cm0);
    isa_retry_at0:  assert (!rt_dchk0 || (rt_seen0 && dbg_commit_pc == rt_dpc0));
  end
  // helper (k-induction starts the checker's registers anywhere, f_past included, so it is
  // not gated): an armed retry is at most 3 cycles away, 1 for a paired HALT
  always @(`ISA_AT) isa_retry_cnt0: assert (!rt_act0 || rt_cnt0 <= (rt_two0 ? 3'd1 : 3'd3));

  reg       rt_act1 = 1'b0, rt_two1 = 1'b0, rt_dchk1 = 1'b0;
  reg [2:0] rt_cnt1 = 3'd0;
  reg [8:0] rt_pc1, rt_dpc1;
  wire rt_ok1   = run_now & ~stop_now & ~start_now & ~ctx_stop_now[1] &
                  (state_all[3:2] == ST_RUNNING);
  wire rt_seen1 = dbg_commit_valid & (dbg_commit_ctx == 2'd1);
  wire rt_cm1   = cm_valid & (cm_ctx == 2'd1);
  wire rt_set1  = rt_arm & (cm_ctx == 2'd1);
  wire rt_due1  = rt_act1 & (rt_cnt1 == 3'd0);
  always @(posedge clk) begin
    rt_dchk1 <= rst_n & rt_due1 & rt_ok1;
    rt_dpc1  <= rt_pc1;
    if (!rst_n) begin
      rt_act1 <= 1'b0;
    end else if (rt_set1) begin
      rt_act1 <= 1'b1;
      rt_two1 <= rt_arm_two;
      rt_cnt1 <= rt_arm_two ? 3'd1 : 3'd3;
      rt_pc1  <= pre_pc;
    end else if (rt_act1) begin
      if (rt_due1 || !rt_ok1) rt_act1 <= 1'b0;
      rt_cnt1 <= rt_cnt1 - 3'd1;
    end
  end
  always @(`ISA_AT) if (f_past && rst_n) begin
    // the grid: no commit of the context between the WAIT and its retry (cycles c+1 to
    // c+3; c+1 for a paired HALT), and the retry at c+4 (c+2) at the same PC, as
    // reported in the cycle after. A run stop or a context stop (ctx_stop_now) at the
    // retry's own edge discards it (9), so the check is armed only without one.
    isa_retry_gap1: assert (!(rt_act1 && rt_cnt1 != 3'd0 && rt_ok1) || !rt_cm1);
    isa_retry_at1:  assert (!rt_dchk1 || (rt_seen1 && dbg_commit_pc == rt_dpc1));
  end
  // helper (k-induction starts the checker's registers anywhere, f_past included, so it is
  // not gated): an armed retry is at most 3 cycles away, 1 for a paired HALT
  always @(`ISA_AT) isa_retry_cnt1: assert (!rt_act1 || rt_cnt1 <= (rt_two1 ? 3'd1 : 3'd3));

  reg       rt_act2 = 1'b0, rt_two2 = 1'b0, rt_dchk2 = 1'b0;
  reg [2:0] rt_cnt2 = 3'd0;
  reg [8:0] rt_pc2, rt_dpc2;
  wire rt_ok2   = run_now & ~stop_now & ~start_now & ~ctx_stop_now[2] &
                  (state_all[5:4] == ST_RUNNING);
  wire rt_seen2 = dbg_commit_valid & (dbg_commit_ctx == 2'd2);
  wire rt_cm2   = cm_valid & (cm_ctx == 2'd2);
  wire rt_set2  = rt_arm & (cm_ctx == 2'd2);
  wire rt_due2  = rt_act2 & (rt_cnt2 == 3'd0);
  always @(posedge clk) begin
    rt_dchk2 <= rst_n & rt_due2 & rt_ok2;
    rt_dpc2  <= rt_pc2;
    if (!rst_n) begin
      rt_act2 <= 1'b0;
    end else if (rt_set2) begin
      rt_act2 <= 1'b1;
      rt_two2 <= rt_arm_two;
      rt_cnt2 <= rt_arm_two ? 3'd1 : 3'd3;
      rt_pc2  <= pre_pc;
    end else if (rt_act2) begin
      if (rt_due2 || !rt_ok2) rt_act2 <= 1'b0;
      rt_cnt2 <= rt_cnt2 - 3'd1;
    end
  end
  always @(`ISA_AT) if (f_past && rst_n) begin
    // the grid: no commit of the context between the WAIT and its retry (cycles c+1 to
    // c+3; c+1 for a paired HALT), and the retry at c+4 (c+2) at the same PC, as
    // reported in the cycle after. A run stop or a context stop (ctx_stop_now) at the
    // retry's own edge discards it (9), so the check is armed only without one.
    isa_retry_gap2: assert (!(rt_act2 && rt_cnt2 != 3'd0 && rt_ok2) || !rt_cm2);
    isa_retry_at2:  assert (!rt_dchk2 || (rt_seen2 && dbg_commit_pc == rt_dpc2));
  end
  // helper (k-induction starts the checker's registers anywhere, f_past included, so it is
  // not gated): an armed retry is at most 3 cycles away, 1 for a paired HALT
  always @(`ISA_AT) isa_retry_cnt2: assert (!rt_act2 || rt_cnt2 <= (rt_two2 ? 3'd1 : 3'd3));

  reg       rt_act3 = 1'b0, rt_two3 = 1'b0, rt_dchk3 = 1'b0;
  reg [2:0] rt_cnt3 = 3'd0;
  reg [8:0] rt_pc3, rt_dpc3;
  wire rt_ok3   = run_now & ~stop_now & ~start_now & ~ctx_stop_now[3] &
                  (state_all[7:6] == ST_RUNNING);
  wire rt_seen3 = dbg_commit_valid & (dbg_commit_ctx == 2'd3);
  wire rt_cm3   = cm_valid & (cm_ctx == 2'd3);
  wire rt_set3  = rt_arm & (cm_ctx == 2'd3);
  wire rt_due3  = rt_act3 & (rt_cnt3 == 3'd0);
  always @(posedge clk) begin
    rt_dchk3 <= rst_n & rt_due3 & rt_ok3;
    rt_dpc3  <= rt_pc3;
    if (!rst_n) begin
      rt_act3 <= 1'b0;
    end else if (rt_set3) begin
      rt_act3 <= 1'b1;
      rt_two3 <= rt_arm_two;
      rt_cnt3 <= rt_arm_two ? 3'd1 : 3'd3;
      rt_pc3  <= pre_pc;
    end else if (rt_act3) begin
      if (rt_due3 || !rt_ok3) rt_act3 <= 1'b0;
      rt_cnt3 <= rt_cnt3 - 3'd1;
    end
  end
  always @(`ISA_AT) if (f_past && rst_n) begin
    // the grid: no commit of the context between the WAIT and its retry (cycles c+1 to
    // c+3; c+1 for a paired HALT), and the retry at c+4 (c+2) at the same PC, as
    // reported in the cycle after. A run stop or a context stop (ctx_stop_now) at the
    // retry's own edge discards it (9), so the check is armed only without one.
    isa_retry_gap3: assert (!(rt_act3 && rt_cnt3 != 3'd0 && rt_ok3) || !rt_cm3);
    isa_retry_at3:  assert (!rt_dchk3 || (rt_seen3 && dbg_commit_pc == rt_dpc3));
  end
  // helper (k-induction starts the checker's registers anywhere, f_past included, so it is
  // not gated): an armed retry is at most 3 cycles away, 1 for a paired HALT
  always @(`ISA_AT) isa_retry_cnt3: assert (!rt_act3 || rt_cnt3 <= (rt_two3 ? 3'd1 : 3'd3));

  // ---- covers: so a pass is not vacuous
  wire cv_ok = q_chk && o_ok;
  always @(`ISA_AT) if (f_past && rst_n) begin
    isa_cv_mov_ok:           cover (cv_ok && q_op == ENC_OP_MOV);
    isa_cv_movi_ok:          cover (cv_ok && q_op == ENC_OP_MOVI);
    isa_cv_movhi_ok:         cover (cv_ok && q_op == ENC_OP_MOVHI);
    isa_cv_add_ok:           cover (cv_ok && q_op == ENC_OP_ADD);
    isa_cv_addi_ok:          cover (cv_ok && q_op == ENC_OP_ADDI);
    isa_cv_sub_ok:           cover (cv_ok && q_op == ENC_OP_SUB);
    isa_cv_cmp_ok:           cover (cv_ok && q_op == ENC_OP_CMP);
    isa_cv_cmpi_ok:          cover (cv_ok && q_op == ENC_OP_CMPI);
    isa_cv_and_ok:           cover (cv_ok && q_op == ENC_OP_AND);
    isa_cv_or_ok:            cover (cv_ok && q_op == ENC_OP_OR);
    isa_cv_xor_ok:           cover (cv_ok && q_op == ENC_OP_XOR);
    isa_cv_tsti_ok:          cover (cv_ok && q_op == ENC_OP_TSTI);
    isa_cv_shl_ok:           cover (cv_ok && q_op == ENC_OP_SHL);
    isa_cv_shr_ok:           cover (cv_ok && q_op == ENC_OP_SHR);
    isa_cv_ldsh_ok:          cover (cv_ok && q_op == ENC_OP_LDSH);
    isa_cv_stsh_ok:          cover (cv_ok && q_op == ENC_OP_STSH);
    isa_cv_nop_ok:           cover (cv_ok && q_op == ENC_OP_NOP);
    isa_cv_jmp_ok:           cover (cv_ok && q_op == ENC_OP_JMP);
    isa_cv_jz_ok:            cover (cv_ok && q_op == ENC_OP_JZ);
    isa_cv_jnz_ok:           cover (cv_ok && q_op == ENC_OP_JNZ);
    isa_cv_jc_ok:            cover (cv_ok && q_op == ENC_OP_JC);
    isa_cv_jnc_ok:           cover (cv_ok && q_op == ENC_OP_JNC);
    isa_cv_djnz_ok:          cover (cv_ok && q_op == ENC_OP_DJNZ);
    isa_cv_ldlc_ok:          cover (cv_ok && q_op == ENC_OP_LDLC);
    isa_cv_call_ok:          cover (cv_ok && q_op == ENC_OP_CALL);
    isa_cv_ret_ok:           cover (cv_ok && q_op == ENC_OP_RET);
    isa_cv_irq_ok:           cover (cv_ok && q_op == ENC_OP_IRQ);
    isa_cv_jz_taken:         cover (cv_ok && q_op == ENC_OP_JZ &&  q_tk);
    isa_cv_jz_not:           cover (cv_ok && q_op == ENC_OP_JZ && !q_tk);
    isa_cv_jnz_taken:        cover (cv_ok && q_op == ENC_OP_JNZ &&  q_tk);
    isa_cv_jnz_not:          cover (cv_ok && q_op == ENC_OP_JNZ && !q_tk);
    isa_cv_jc_taken:         cover (cv_ok && q_op == ENC_OP_JC &&  q_tk);
    isa_cv_jc_not:           cover (cv_ok && q_op == ENC_OP_JC && !q_tk);
    isa_cv_jnc_taken:        cover (cv_ok && q_op == ENC_OP_JNC &&  q_tk);
    isa_cv_jnc_not:          cover (cv_ok && q_op == ENC_OP_JNC && !q_tk);
    isa_cv_djnz_taken:       cover (cv_ok && q_op == ENC_OP_DJNZ &&  q_tk);
    isa_cv_djnz_not:         cover (cv_ok && q_op == ENC_OP_DJNZ && !q_tk);
    isa_cv_djnz_wrap:        cover (cv_ok && q_op == ENC_OP_DJNZ && q_lc0);
    isa_cv_mov_z1:           cover (cv_ok && q_op == ENC_OP_MOV &&  post_z);
    isa_cv_mov_z0:           cover (cv_ok && q_op == ENC_OP_MOV && !post_z);
    isa_cv_movi_z1:          cover (cv_ok && q_op == ENC_OP_MOVI &&  post_z);
    isa_cv_movi_z0:          cover (cv_ok && q_op == ENC_OP_MOVI && !post_z);
    isa_cv_movhi_z1:         cover (cv_ok && q_op == ENC_OP_MOVHI &&  post_z);
    isa_cv_movhi_z0:         cover (cv_ok && q_op == ENC_OP_MOVHI && !post_z);
    isa_cv_add_z1:           cover (cv_ok && q_op == ENC_OP_ADD &&  post_z);
    isa_cv_add_z0:           cover (cv_ok && q_op == ENC_OP_ADD && !post_z);
    isa_cv_addi_z1:          cover (cv_ok && q_op == ENC_OP_ADDI &&  post_z);
    isa_cv_addi_z0:          cover (cv_ok && q_op == ENC_OP_ADDI && !post_z);
    isa_cv_sub_z1:           cover (cv_ok && q_op == ENC_OP_SUB &&  post_z);
    isa_cv_sub_z0:           cover (cv_ok && q_op == ENC_OP_SUB && !post_z);
    isa_cv_cmp_z1:           cover (cv_ok && q_op == ENC_OP_CMP &&  post_z);
    isa_cv_cmp_z0:           cover (cv_ok && q_op == ENC_OP_CMP && !post_z);
    isa_cv_cmpi_z1:          cover (cv_ok && q_op == ENC_OP_CMPI &&  post_z);
    isa_cv_cmpi_z0:          cover (cv_ok && q_op == ENC_OP_CMPI && !post_z);
    isa_cv_and_z1:           cover (cv_ok && q_op == ENC_OP_AND &&  post_z);
    isa_cv_and_z0:           cover (cv_ok && q_op == ENC_OP_AND && !post_z);
    isa_cv_or_z1:            cover (cv_ok && q_op == ENC_OP_OR &&  post_z);
    isa_cv_or_z0:            cover (cv_ok && q_op == ENC_OP_OR && !post_z);
    isa_cv_xor_z1:           cover (cv_ok && q_op == ENC_OP_XOR &&  post_z);
    isa_cv_xor_z0:           cover (cv_ok && q_op == ENC_OP_XOR && !post_z);
    isa_cv_tsti_z1:          cover (cv_ok && q_op == ENC_OP_TSTI &&  post_z);
    isa_cv_tsti_z0:          cover (cv_ok && q_op == ENC_OP_TSTI && !post_z);
    isa_cv_shl_z1:           cover (cv_ok && q_op == ENC_OP_SHL &&  post_z);
    isa_cv_shl_z0:           cover (cv_ok && q_op == ENC_OP_SHL && !post_z);
    isa_cv_shr_z1:           cover (cv_ok && q_op == ENC_OP_SHR &&  post_z);
    isa_cv_shr_z0:           cover (cv_ok && q_op == ENC_OP_SHR && !post_z);
    isa_cv_add_c1:           cover (cv_ok && q_op == ENC_OP_ADD &&  post_c);
    isa_cv_add_c0:           cover (cv_ok && q_op == ENC_OP_ADD && !post_c);
    isa_cv_addi_c1:          cover (cv_ok && q_op == ENC_OP_ADDI &&  post_c);
    isa_cv_addi_c0:          cover (cv_ok && q_op == ENC_OP_ADDI && !post_c);
    isa_cv_sub_c1:           cover (cv_ok && q_op == ENC_OP_SUB &&  post_c);
    isa_cv_sub_c0:           cover (cv_ok && q_op == ENC_OP_SUB && !post_c);
    isa_cv_cmp_c1:           cover (cv_ok && q_op == ENC_OP_CMP &&  post_c);
    isa_cv_cmp_c0:           cover (cv_ok && q_op == ENC_OP_CMP && !post_c);
    isa_cv_cmpi_c1:          cover (cv_ok && q_op == ENC_OP_CMPI &&  post_c);
    isa_cv_cmpi_c0:          cover (cv_ok && q_op == ENC_OP_CMPI && !post_c);
    isa_cv_shl_c1:           cover (cv_ok && q_op == ENC_OP_SHL &&  post_c);
    isa_cv_shl_c0:           cover (cv_ok && q_op == ENC_OP_SHL && !post_c);
    isa_cv_shr_c1:           cover (cv_ok && q_op == ENC_OP_SHR &&  post_c);
    isa_cv_shr_c0:           cover (cv_ok && q_op == ENC_OP_SHR && !post_c);
    isa_cv_shl_n0:           cover (cv_ok && q_op == ENC_OP_SHL && q_n0);
    isa_cv_shr_n0:           cover (cv_ok && q_op == ENC_OP_SHR && q_n0);
    isa_cv_movhi_lo:         cover (cv_ok && q_op == ENC_OP_MOVHI && q_lo_nz);
    isa_cv_irq_clr:          cover (cv_ok && q_op == ENC_OP_IRQ &&  q_clr);
    isa_cv_irq_noclr:        cover (cv_ok && q_op == ENC_OP_IRQ && !q_clr);
    isa_cv_unlisted:         cover (q_chk && o_fault && q_unl);
    isa_cv_rsv_n:            cover (q_chk && o_fault && q_rsv && q_used == ENC_FMT_N_USED);
    isa_cv_rsv_i8:           cover (q_chk && o_fault && q_rsv && q_used == ENC_FMT_I8_USED);
    isa_cv_rsv_r:            cover (q_chk && o_fault && q_rsv && q_used == ENC_FMT_R_USED);
    isa_cv_rsv_rd:           cover (q_chk && o_fault && q_rsv && q_used == ENC_FMT_RD_USED);
    isa_cv_rsv_rs:           cover (q_chk && o_fault && q_rsv && q_used == ENC_FMT_RS_USED);
    isa_cv_rsv_i16:          cover (q_chk && o_fault && q_rsv && q_used == ENC_FMT_I16_USED);
    isa_cv_rsv_sh:           cover (q_chk && o_fault && q_rsv && q_used == ENC_FMT_SH_USED);
    isa_cv_rsv_b:            cover (q_chk && o_fault && q_rsv && q_used == ENC_FMT_B_USED);
    isa_cv_rsv_l16:          cover (q_chk && o_fault && q_rsv && q_used == ENC_FMT_L16_USED);
    isa_cv_rsv_pr:           cover (q_chk && o_fault && q_rsv && q_used == ENC_FMT_PR_USED);
    isa_cv_rsv_px:           cover (q_chk && o_fault && q_rsv && q_used == ENC_FMT_PX_USED);
    isa_cv_rsv_ps:           cover (q_chk && o_fault && q_rsv && q_used == ENC_FMT_PS_USED);
    isa_cv_rsv_pw:           cover (q_chk && o_fault && q_rsv && q_used == ENC_FMT_PW_USED);
    isa_cv_rsv_tm:           cover (q_chk && o_fault && q_rsv && q_used == ENC_FMT_TM_USED);
    isa_cv_b8_nospan:        cover (q_chk && o_fault && q_b8);
    isa_cv_b8_span_jmp:      cover (cv_ok && q_op == ENC_OP_JMP && q_span && q_tgt8);
    isa_cv_b8_span_call:     cover (cv_ok && q_op == ENC_OP_CALL && q_span && q_tgt8);
    isa_cv_pcr_data:         cover (q_chk && o_fault && q_pcr && q_data);
    isa_cv_pcr_call:         cover (q_chk && o_fault && q_pcr && q_op == ENC_OP_CALL);
    isa_cv_pcr_span:         cover (q_chk && o_fault && q_pcr && q_span);
    isa_cv_ret_hi:           cover (cv_ok && q_op == ENC_OP_RET && q_span && post_pc[8]);
    isa_cv_ff_rec:           cover (q_chk && o_fault && !q_ffpre && post_ff_valid && ff_ours);
    isa_cv_ff_rec_pcr:       cover (q_chk && o_fault && !q_ffpre && ff_ours && post_ff_cause == CA_PC_RANGE);
    isa_cv_ff_full:          cover (q_chk && o_fault && q_ffpre);
    isa_cv_rdtime_ok:        cover (cv_ok && q_op == ENC_OP_RDTIME);
    isa_cv_sett_ok:          cover (cv_ok && q_op == ENC_OP_SETT);
    isa_cv_addt_ok:          cover (cv_ok && q_op == ENC_OP_ADDT);
    isa_cv_addtr_ok:         cover (cv_ok && q_op == ENC_OP_ADDTR);
    isa_cv_waitt_ok:         cover (cv_ok && q_op == ENC_OP_WAITT);
    isa_cv_halt_ok:          cover (cv_ok && q_op == ENC_OP_HALT && post_state == ST_HALTED);
    isa_cv_waitt_wait:       cover (q_chk && q_op == ENC_OP_WAITT && dbg_commit_outcome == OUT_WAIT);
    isa_cv_waitt_edge_ok:    cover (cv_ok && q_op == ENC_OP_WAITT && q_tdiff == 32'h7FFFFFFF);
    isa_cv_waitt_edge_wait:  cover (q_chk && q_op == ENC_OP_WAITT && dbg_commit_outcome == OUT_WAIT && q_tdiff == 32'h80000000);
    isa_cv_waitt_eq:         cover (cv_ok && q_op == ENC_OP_WAITT && q_tdiff == 32'd0);
    isa_cv_halt_wait_fifo:   cover (q_chk && e_halt && dbg_commit_outcome == OUT_WAIT && post_fifo_cnt != 3'd0 && !post_chk_active && !q_lu_eng);
    isa_cv_halt_wait_chk:    cover (q_chk && e_halt && dbg_commit_outcome == OUT_WAIT && post_fifo_cnt == 3'd0 && post_chk_active && !q_lu_eng);
    isa_cv_halt_wait_lu:     cover (q_chk && e_halt && dbg_commit_outcome == OUT_WAIT && post_fifo_cnt == 3'd0 && !post_chk_active && q_lu_eng);
    isa_cv_halt_wait_any_lu: cover (q_chk && e_halt && dbg_commit_outcome == OUT_WAIT && q_lu_eng);
    isa_cv_retry4_0:         cover (rt_due0 && !rt_two0 && rt_cm0 && pre_pc == rt_pc0);
    isa_cv_retry4_1:         cover (rt_due1 && !rt_two1 && rt_cm1 && pre_pc == rt_pc1);
    isa_cv_retry4_2:         cover (rt_due2 && !rt_two2 && rt_cm2 && pre_pc == rt_pc2);
    isa_cv_retry4_3:         cover (rt_due3 && !rt_two3 && rt_cm3 && pre_pc == rt_pc3);
    isa_cv_retry2_0:         cover (rt_due0 &&  rt_two0 && rt_cm0 && pre_pc == rt_pc0);
    isa_cv_retry2_1:         cover (rt_due1 &&  rt_two1 && rt_cm1 && pre_pc == rt_pc1);
    isa_cv_retry2_2:         cover (rt_due2 &&  rt_two2 && rt_cm2 && pre_pc == rt_pc2);
    isa_cv_retry2_3:         cover (rt_due3 &&  rt_two3 && rt_cm3 && pre_pc == rt_pc3);
    isa_cv_retry_at:         cover ((rt_dchk0 && rt_seen0) || (rt_dchk1 && rt_seen1) || (rt_dchk2 && rt_seen2) || (rt_dchk3 && rt_seen3));
    isa_cv_retry4_paired:    cover (cm_paired && ((rt_due0 && !rt_two0 && rt_cm0 && pre_pc == rt_pc0) || (rt_due1 && !rt_two1 && rt_cm1 && pre_pc == rt_pc1) || (rt_due2 && !rt_two2 && rt_cm2 && pre_pc == rt_pc2) || (rt_due3 && !rt_two3 && rt_cm3 && pre_pc == rt_pc3)));
    isa_cv_sett_wrap:        cover (cv_ok && q_op == ENC_OP_SETT && q_twrap);
    isa_cv_addt_wrap:        cover (cv_ok && q_op == ENC_OP_ADDT && q_twrap);
    isa_cv_addtr_wrap:       cover (cv_ok && q_op == ENC_OP_ADDTR && q_twrap);
    isa_cv_pcr_tier2:        cover (q_chk && o_fault && q_pcr && (q_op == ENC_OP_RDTIME || q_op == ENC_OP_WAITT || q_op == ENC_OP_ADDT));
    isa_cv_ff_same:          cover (q_valid && q_ffpre_any && post_ff_valid);
    isa_cv_emit_adm:         cover (q_chk && o_ok && q_admit && q_op == ENC_OP_EMIT);
    isa_cv_emitr_adm:        cover (q_chk && o_ok && q_admit && q_op == ENC_OP_EMITR);
    isa_cv_emitx_adm:        cover (q_chk && o_ok && q_admit && q_op == ENC_OP_EMITX);
    isa_cv_expect_adm:       cover (q_chk && o_ok && q_admit && q_op == ENC_OP_EXPECT);
    isa_cv_sample_adm:       cover (q_chk && o_ok && q_admit && q_op == ENC_OP_SAMPLE);
    isa_cv_send_adm:         cover (q_chk && o_ok && q_admit && q_op == ENC_OP_SEND);
    isa_cv_ev_wfull:         cover (q_chk && q_ev && q_full && dbg_commit_outcome == OUT_WAIT);
    isa_cv_adm_mask:         cover (q_chk && o_fault && q_asub == ENC_ADMIT_MASK[2:0]);
    isa_cv_adm_unown:        cover (q_chk && o_fault && q_asub == ENC_ADMIT_OWNER[2:0] && q_unown);
    isa_cv_adm_claim:        cover (q_chk && o_fault && q_asub == ENC_ADMIT_OWNER[2:0] && q_claim);
    isa_cv_adm_past:         cover (q_chk && o_fault && q_asub == ENC_ADMIT_PAST[2:0]);
    isa_cv_adm_order:        cover (q_chk && o_fault && q_asub == ENC_ADMIT_ORDER[2:0]);
    isa_cv_adm_overlap:      cover (q_chk && o_fault && q_asub == ENC_ADMIT_OVERLAP[2:0]);
    isa_cv_send_notown:      cover (q_chk && o_fault && q_op == ENC_OP_SEND && q_notown);
    isa_cv_send_unconf:      cover (q_chk && o_fault && q_op == ENC_OP_SEND && q_unconf);
    isa_cv_send_notx:        cover (q_chk && o_fault && q_op == ENC_OP_SEND && q_notx);
    isa_cv_send_eng:         cover (q_chk && q_op == ENC_OP_SEND && q_seng && !q_full && dbg_commit_outcome == OUT_WAIT);
    isa_cv_send_drop:        cover (q_chk && o_ok && q_op == ENC_OP_SEND && q_drop);
    isa_cv_full_room:        cover (q_chk && o_ok && q_admit && q_pcnt == 3'd4 && q_retire);
    isa_cv_emitx_d0_b0:      cover (q_chk && o_ok && q_admit && q_op == ENC_OP_EMITX && q_xdir == 1'b0 && q_xb == 1'b0);
    isa_cv_emitx_d0_b1:      cover (q_chk && o_ok && q_admit && q_op == ENC_OP_EMITX && q_xdir == 1'b0 && q_xb == 1'b1);
    isa_cv_emitx_d1_b0:      cover (q_chk && o_ok && q_admit && q_op == ENC_OP_EMITX && q_xdir == 1'b1 && q_xb == 1'b0);
    isa_cv_emitx_d1_b1:      cover (q_chk && o_ok && q_admit && q_op == ENC_OP_EMITX && q_xdir == 1'b1 && q_xb == 1'b1);
    isa_cv_min_lead_sat:     cover (q_chk && o_ok && q_admit && q_leadbig && q_minl_pre == 16'hFFFF && post_min_lead == 16'hFFFF);
    isa_cv_bank_b:           cover (q_chk && o_ok && q_admit && q_bank && q_op == ENC_OP_EMIT);
    isa_cv_retry_ev:         cover ((rt_due0 || rt_due1 || rt_due2 || rt_due3) && cm_valid && enc_wait_class(d_op) && d_t3);
    isa_cv_adm_ff:           cover (q_chk && o_fault && !q_ffpre && ff_ours && post_ff_cause == CA_ADMIT);
    isa_cv_fault_flush:      cover (q_chk && o_fault && q_pcnt != 3'd0 && post_q_cnt == 3'd0);
    // tier 4: each instruction completing, waiting (those that can) and faulting
    isa_cv_getp_ok:          cover (cv_ok && q_op == ENC_OP_GETP);
    isa_cv_waitp_ok:         cover (cv_ok && q_op == ENC_OP_WAITP);
    isa_cv_geti_ok:          cover (cv_ok && q_op == ENC_OP_GETI);
    isa_cv_getx_ok:          cover (cv_ok && q_op == ENC_OP_GETX);
    isa_cv_pop_ok:           cover (cv_ok && q_op == ENC_OP_POP);
    isa_cv_push_ok:          cover (cv_ok && q_op == ENC_OP_PUSH);
    isa_cv_rxready_ok:       cover (cv_ok && q_op == ENC_OP_RXREADY);
    isa_cv_txready_ok:       cover (cv_ok && q_op == ENC_OP_TXREADY);
    isa_cv_ldt_ok:           cover (cv_ok && q_op == ENC_OP_LDT);
    isa_cv_stt_ok:           cover (cv_ok && q_op == ENC_OP_STT);
    isa_cv_capt_ok:          cover (cv_ok && q_op == ENC_OP_CAPT);
    isa_cv_getc_ok:          cover (cv_ok && q_op == ENC_OP_GETC);
    isa_cv_line_ok:          cover (cv_ok && q_op == ENC_OP_LINE);
    isa_cv_getl_ok:          cover (cv_ok && q_op == ENC_OP_GETL);
    isa_cv_mon_ok:           cover (cv_ok && q_op == ENC_OP_MON);
    isa_cv_getm_ok:          cover (cv_ok && q_op == ENC_OP_GETM);
    isa_cv_waitp_wait:       cover (q_chk && q_op == ENC_OP_WAITP && dbg_commit_outcome == OUT_WAIT);
    isa_cv_geti_wait:        cover (q_chk && q_op == ENC_OP_GETI  && dbg_commit_outcome == OUT_WAIT);
    isa_cv_pop_wait:         cover (q_chk && q_op == ENC_OP_POP   && dbg_commit_outcome == OUT_WAIT);
    isa_cv_push_wait:        cover (q_chk && q_op == ENC_OP_PUSH  && dbg_commit_outcome == OUT_WAIT);
    isa_cv_ldt_wait:         cover (q_chk && q_op == ENC_OP_LDT   && dbg_commit_outcome == OUT_WAIT);
    isa_cv_stt_wait:         cover (q_chk && q_op == ENC_OP_STT   && dbg_commit_outcome == OUT_WAIT);
    isa_cv_line_wait:        cover (q_chk && q_op == ENC_OP_LINE  && dbg_commit_outcome == OUT_WAIT);
    isa_cv_getl_wait:        cover (q_chk && q_op == ENC_OP_GETL  && dbg_commit_outcome == OUT_WAIT);
    isa_cv_getp_fault:       cover (q_chk && o_fault && q_op == ENC_OP_GETP);
    isa_cv_waitp_fault:      cover (q_chk && o_fault && q_op == ENC_OP_WAITP);
    isa_cv_geti_fault:       cover (q_chk && o_fault && q_op == ENC_OP_GETI);
    isa_cv_getx_fault:       cover (q_chk && o_fault && q_op == ENC_OP_GETX);
    isa_cv_pop_fault:        cover (q_chk && o_fault && q_op == ENC_OP_POP);
    isa_cv_push_fault:       cover (q_chk && o_fault && q_op == ENC_OP_PUSH);
    isa_cv_rxready_fault:    cover (q_chk && o_fault && q_op == ENC_OP_RXREADY);
    isa_cv_txready_fault:    cover (q_chk && o_fault && q_op == ENC_OP_TXREADY);
    isa_cv_ldt_fault:        cover (q_chk && o_fault && q_op == ENC_OP_LDT);
    isa_cv_stt_fault:        cover (q_chk && o_fault && q_op == ENC_OP_STT);
    isa_cv_capt_fault:       cover (q_chk && o_fault && q_op == ENC_OP_CAPT);
    isa_cv_getc_fault:       cover (q_chk && o_fault && q_op == ENC_OP_GETC);
    isa_cv_line_fault:       cover (q_chk && o_fault && q_op == ENC_OP_LINE);
    isa_cv_getl_fault:       cover (q_chk && o_fault && q_op == ENC_OP_GETL);
    isa_cv_mon_fault:        cover (q_chk && o_fault && q_op == ENC_OP_MON);
    isa_cv_getm_fault:       cover (q_chk && o_fault && q_op == ENC_OP_GETM);
    isa_cv_pcr_tier4:        cover (q_chk && o_fault && q_pcr && q_op == ENC_OP_STT);
    // 7.3 details
    isa_cv_getp_bank_b:      cover (cv_ok && q_op == ENC_OP_GETP && q_bank);
    isa_cv_waitp_match:      cover (cv_ok && q_op == ENC_OP_WAITP &&  q_wpm && post_z);
    isa_cv_waitp_timeout:    cover (cv_ok && q_op == ENC_OP_WAITP && !q_wpm && !post_z);
    isa_cv_geti_ish:         cover (cv_ok && q_op == ENC_OP_GETI && e_ish == 32'd0 && post_r0 != 32'd0 && q_rd == 2'd0);
    // 7.5
    isa_cv_rxready_z1:       cover (cv_ok && q_op == ENC_OP_RXREADY &&  post_z);
    isa_cv_rxready_z0:       cover (cv_ok && q_op == ENC_OP_RXREADY && !post_z);
    isa_cv_txready_z1:       cover (cv_ok && q_op == ENC_OP_TXREADY &&  post_z);
    isa_cv_txready_z0:       cover (cv_ok && q_op == ENC_OP_TXREADY && !post_z);
    isa_cv_pop_hpush:        cover (cv_ok && q_op == ENC_OP_POP  && q_hpush_tk);
    isa_cv_push_hpop:        cover (cv_ok && q_op == ENC_OP_PUSH && q_hpop_tk);
    isa_cv_pop_two:          cover (cv_ok && q_op == ENC_OP_POP  && e_rxn == 2'd1 && !q_hpush_tk);
    isa_cv_push_full_hpop:   cover (q_chk && q_op == ENC_OP_PUSH && dbg_commit_outcome == OUT_WAIT && q_hpop_tk);
    // 7.6
    isa_cv_capt_en:          cover (cv_ok && q_op == ENC_OP_CAPT &&  q_capen);
    isa_cv_capt_dis:         cover (cv_ok && q_op == ENC_OP_CAPT && !q_capen);
    isa_cv_capt_dis_ack:     cover (cv_ok && q_op == ENC_OP_CAPT && !q_capen && q_tack);
    // 7.7
    isa_cv_line_ill:         cover (q_chk && o_fault && q_op == ENC_OP_LINE && q_lill);
    isa_cv_line_own_cfg:     cover (q_chk && o_fault && q_op == ENC_OP_LINE && q_locfg);
    isa_cv_line_own_not:     cover (q_chk && o_fault && q_op == ENC_OP_LINE && q_lonot);
    isa_cv_line_own_unconf:  cover (q_chk && o_fault && q_op == ENC_OP_LINE && q_lounc);
    isa_cv_line_eng_wait:    cover (q_chk && q_op == ENC_OP_LINE && q_lwait && dbg_commit_outcome == OUT_WAIT);
    isa_cv_line_off_ok:      cover (cv_ok && q_op == ENC_OP_LINE && q_lop == 4'd0);
    isa_cv_line_recv_ok:     cover (cv_ok && q_op == ENC_OP_LINE && q_lop == 4'd1);
    isa_cv_line_crc_ok:      cover (cv_ok && q_op == ENC_OP_LINE && q_lop == 4'd2);
    isa_cv_line_cfg_ok:      cover (cv_ok && q_op == ENC_OP_LINE && q_lop == 4'd3 && !q_ltake);
    isa_cv_line_cfg_take:    cover (cv_ok && q_op == ENC_OP_LINE && q_ltake && post_lu_owned);
    isa_cv_getl_idle:        cover (cv_ok && q_op == ENC_OP_GETL && !q_lbusy &&  post_z);
    isa_cv_getl_reached:     cover (cv_ok && q_op == ENC_OP_GETL &&  q_lbusy && !post_z);
    isa_cv_getl_own:         cover (q_chk && o_fault && q_op == ENC_OP_GETL && q_t4own);
    // 7.8
    isa_cv_mon_take:         cover (cv_ok && q_op == ENC_OP_MON && q_mnone && mon_owner != 4'd0);
    isa_cv_mon_keep:         cover (cv_ok && q_op == ENC_OP_MON && !q_mnone);
    isa_cv_mon_own:          cover (q_chk && o_fault && q_op == ENC_OP_MON && q_t4own);
    isa_cv_getm_s0_own:      cover (cv_ok && q_op == ENC_OP_GETM && q_sel == 16'd0 &&  q_mmine);
    isa_cv_getm_s0_not:      cover (cv_ok && q_op == ENC_OP_GETM && q_sel == 16'd0 && !q_mmine);
    isa_cv_getm_s1_own:      cover (cv_ok && q_op == ENC_OP_GETM && q_sel == 16'd1 &&  q_mmine);
    isa_cv_getm_s1_not:      cover (cv_ok && q_op == ENC_OP_GETM && q_sel == 16'd1 && !q_mmine);
    isa_cv_getm_s2_own:      cover (cv_ok && q_op == ENC_OP_GETM && q_sel == 16'd2 &&  q_mmine);
    isa_cv_getm_s2_not:      cover (cv_ok && q_op == ENC_OP_GETM && q_sel == 16'd2 && !q_mmine);
    isa_cv_getm_s3_own:      cover (cv_ok && q_op == ENC_OP_GETM && q_sel == 16'd3 &&  q_lumine);
    isa_cv_getm_s3_not:      cover (cv_ok && q_op == ENC_OP_GETM && q_sel == 16'd3 && !q_lumine);
    isa_cv_getm_s4_own:      cover (cv_ok && q_op == ENC_OP_GETM && q_sel == 16'd4 &&  q_lumine);
    isa_cv_getm_s4_not:      cover (cv_ok && q_op == ENC_OP_GETM && q_sel == 16'd4 && !q_lumine);
    isa_cv_getm_other:       cover (cv_ok && q_op == ENC_OP_GETM && q_sel > 16'd4);
    isa_cv_adm_ff_t4:        cover (q_chk && o_fault && !q_ffpre && ff_ours && post_ff_cause == CA_ADMIT && q_t4own);
    isa_cv_retry_t4:         cover ((rt_due0 || rt_due1 || rt_due2 || rt_due3) && cm_valid && d_t4);
  end

endmodule
`default_nettype wire
`undef ISA_AT
