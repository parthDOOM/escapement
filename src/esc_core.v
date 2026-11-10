/*
 * esc_core.v - Escapement core: 4-context fixed-slot barrel, shared execute /
 * admission in the commit stage (decode and operand reads a stage earlier, v1.6), pins,
 * synchronisers, first-fault register and the verification observation ports.
 *
 * Copyright (c) 2026 Escapement contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Normative semantics: docs/11-SEMANTICS.md v1.6.0 (section numbers below refer
 * to it). Encodings: isa/isa.yaml (decoder constants hand-copied).
 *
 * v1.1: the tape port (LDT/STT in the commit cycle; the capture unit esc_cap drains
 * into free cycles), the execution digest (sec. 10.1) and the wait-class GETI.
 * v1.2: pin bank B (sec. 5). Every context works in 8 logical pins; the mapping onto the
 * 13 physical pins happens only at the input view, the ownership masks, DRIVE and fault
 * updates of the pin registers, and the outputs.
 * v1.3: streaming capture. tape_busy marks only the cycles the core uses the tape port,
 * so the host can read during a run; TAPE_ACK sets the capture unit's rptr (sec. 6.5).
 * v1.4: the line unit esc_line (sec. 6.6, 7.7): SEND queues a LINE entry that starts a
 * packet at its deadline (dropped with Z = 0 when late), LINE and GETL control it, and
 * its tape accesses and pin writes go through the ports here.
 *
 * Pipeline (each stage holds a different context, so no same-context hazards):
 *   F  cycle t   : imem_addr = {page, PC} of ctx (now mod 4) (combinational); slot valid if the
 *                  context is RUNNING during t. SRAM samples the address at E_t.
 *   D  cycle t+1 : imem_rdata valid; registered into p2_insn at E_(t+1).
 *   X  cycle t+2 : p2_insn -> p3_insn at E_(t+2); beside it (v1.6) the decode (d_op,
 *                  illegal) and the operands of the context committing next (opa, opb).
 *   C  cycle t+3 : the instruction is evaluated combinationally from the state
 *                  during cycle t+3 and commits at E_(t+3). The committing
 *                  context is (now + 1) mod 4 (= (t+3) - 3).
 * The PC of the committing context is unchanged since its fetch (only its own
 * commit writes it), so the commit stage reads it from the context state.
 *
 * Observation: dbg_commit_* are registered at the commit edge (valid during
 * c+1); dbg_post_* are a mux over the committed context's registers indexed by
 * the registered dbg_commit_ctx.
 */
`default_nettype none
module esc_core (
    input  wire         clk,
    input  wire         rst_n,
    input  wire         start,
    input  wire         stop,
    input  wire [3:0]   ctx_stop,      // v1.7 (S3): stop these contexts at this edge
    input  wire [3:0]   ctx_start,     // v1.7 (S3): restart these contexts (at their first slot)
    // configuration (static during a run)
    input  wire [3:0]   ctx_enable,
    input  wire [31:0]  entry_pcs,
    input  wire [12:0]  pin_owned,     // physical pins: 0..7 bank A, 8..12 bank B
    input  wire [25:0]  pin_owner,
    input  wire [7:0]   pin_od,        // bank A only
    input  wire [3:0]   ctx_bank,      // v1.2: context k's logical pins are bank B
    input  wire [3:0]   ctx_pair,      // v1.7: context k also takes slot k^2 when context k^2 is off
    input  wire [7:0]   ctx_page,      // v2 (S-A): context k fetches from program page [2k+1:2k]
    // instruction memory
    output wire [9:0]   imem_addr,     // v2 (S-A): {page, PC}
    output wire         imem_re,       // D-29: this slot's fetch will be used (else no read)
    output wire         imem_free,     // v1.7 (S3): no fetch in this cycle (a host write may go)
    input  wire [31:0]  imem_rdata,
    // pins
    input  wire [7:0]   uio_in,
    output wire [7:0]   uio_out,
    output wire [7:0]   uio_oe,
    input  wire [4:0]   ib_in,         // v1.2 bank B inputs (ui_in[7:3])
    output wire [4:0]   ob_out,        // v1.2 bank B outputs (uo_out[7:3])
    // status
    output wire         running,
    output wire [7:0]   ctx_state,
    output wire [3:0]   irq_pending,
    output wire         fault_valid,
    input  wire [3:0]   irq_clear,
    // host mailboxes
    input  wire [3:0]   mbx_rx_push,
    input  wire [127:0] mbx_rx_data,
    output wire [3:0]   mbx_rx_full,
    input  wire [3:0]   mbx_tx_pop,
    output wire [127:0] mbx_tx_data,
    output wire [3:0]   mbx_tx_empty,
    // observation
    output reg          dbg_commit_valid,
    output reg  [1:0]   dbg_commit_ctx,
    output reg  [7:0]   dbg_commit_pc,
    output reg  [31:0]  dbg_commit_insn,
    output reg  [1:0]   dbg_commit_outcome,
    output wire [31:0]  dbg_post_r0,
    output wire [31:0]  dbg_post_r1,
    output wire [31:0]  dbg_post_r2,
    output wire [31:0]  dbg_post_r3,
    output wire [31:0]  dbg_post_sh,
    output wire [15:0]  dbg_post_lc,
    output wire [31:0]  dbg_post_tmr,
    output wire         dbg_post_z,
    output wire         dbg_post_c,
    output wire [7:0]   dbg_post_pc,
    input  wire [1:0]   dbg_sel_ctx,
    input  wire [4:0]   dbg_sel,
    output reg  [31:0]  dbg_rdata,
    // tape (v1.1): same timing contract as the instruction memory
    output wire [7:0]   tape_addr,
    output wire         tape_re,       // D-29: a read is needed (LDT); otherwise the macro idles
    output wire         tape_we,
    output wire [31:0]  tape_wdata,
    input  wire [31:0]  tape_rdata,
    output wire         tape_busy,     // v1.3: the core uses the port this cycle; else the host may
    input  wire         tape_host_we,  // v1.3: the host writes the tape this cycle (stopped only)
    input  wire         tape_ack,      // v1.3: TAPE_ACK, the host's new capture rptr
    input  wire [7:0]   tape_ack_v
`ifdef ESC_NI
    // formal only (formal/esc_ni.sby): each context's architectural state, the pins and the
    // fetch slot owner, for the non-interference miter of two cores
    , output wire [1279:0] f_ctx_obs,
    output wire [25:0]   f_pins,
    output wire [1:0]    f_fq,
    // and for the inductive invariant (formal/esc_ni_ind.sby): every flip-flop of each
    // context, and the core's schedule, input synchronisers, pipeline stages and deferred
    // tape load (layout: the concatenation at the end of this file)
    output wire [3463:0] f_ctx_all,
    output wire [336:0]  f_core,
    output wire [32:0]   f_lu             // {lu_el, lu_port, lu_rel, lu_drv, lu_mode, lu_bank}
`endif
);

  // ------------------------------------------------------------------ constants
  localparam [1:0] ST_RUN = 2'd1;   // context state encoding: 0 IDLE, 1 RUNNING, 2 HALTED, 3 FAULTED
  localparam [1:0] OC_C = 2'd0, OC_W = 2'd1, OC_F = 2'd2;
  localparam [1:0] K_DRIVE = 2'd1, K_EXPECT = 2'd2, K_SAMPLE = 2'd3;
  localparam [2:0] FC_ILLEGAL = 3'd1, FC_PC_RANGE = 3'd2, FC_ADMIT = 3'd3, FC_LATE = 3'd4;
  localparam [2:0] AS_MASK = 3'd1, AS_OWNER = 3'd2, AS_PAST = 3'd3, AS_ORDER = 3'd4,
                   AS_OVERLAP = 3'd5, AS_CONFIG = 3'd6;
  localparam [1:0] K_LINE = 2'd0;

  // opcodes (isa/isa.yaml)
  localparam [7:0] OP_NOP = 8'h01, OP_HALT = 8'h02, OP_IRQ = 8'h03, OP_RET = 8'h04,
                   OP_MOV = 8'h08, OP_MOVI = 8'h09, OP_MOVHI = 8'h0A, OP_ADD = 8'h0B,
                   OP_SUB = 8'h0C, OP_AND = 8'h0D, OP_OR = 8'h0E, OP_XOR = 8'h0F,
                   OP_CMP = 8'h10, OP_TSTI = 8'h11, OP_SHL = 8'h12, OP_SHR = 8'h13,
                   OP_ADDI = 8'h14, OP_CMPI = 8'h15, OP_LDSH = 8'h16, OP_STSH = 8'h17,
                   OP_JMP = 8'h18, OP_JZ = 8'h19, OP_JNZ = 8'h1A, OP_JC = 8'h1B,
                   OP_JNC = 8'h1C, OP_DJNZ = 8'h1D, OP_LDLC = 8'h1E, OP_CALL = 8'h1F,
                   OP_RDTIME = 8'h20, OP_SETT = 8'h21, OP_ADDT = 8'h22, OP_WAITT = 8'h23,
                   OP_ADDTR = 8'h24, OP_EMIT = 8'h28, OP_EMITR = 8'h29, OP_EMITX = 8'h2A,
                   OP_EXPECT = 8'h2B, OP_SAMPLE = 8'h2C, OP_GETP = 8'h30, OP_WAITP = 8'h31,
                   OP_GETI = 8'h32, OP_GETX = 8'h33, OP_LDT = 8'h34, OP_STT = 8'h35,
                   OP_CAPT = 8'h36, OP_GETC = 8'h37, OP_POP = 8'h38, OP_PUSH = 8'h39,
                   OP_RXREADY = 8'h3A, OP_TXREADY = 8'h3B,
                   OP_SEND = 8'h2D, OP_LINE = 8'h3C, OP_GETL = 8'h3D;          // v1.4
  // v1.6: bit I_x of the registered decode d_op is set iff the opcode is OP_x
  localparam integer I_NOP = 0, I_HALT = 1, I_IRQ = 2, I_RET = 3, I_MOV = 4, I_MOVI = 5,
                     I_MOVHI = 6, I_ADD = 7, I_SUB = 8, I_AND = 9, I_OR = 10, I_XOR = 11,
                     I_CMP = 12, I_TSTI = 13, I_SHL = 14, I_SHR = 15, I_ADDI = 16,
                     I_CMPI = 17, I_LDSH = 18, I_STSH = 19, I_JMP = 20, I_JZ = 21,
                     I_JNZ = 22, I_JC = 23, I_JNC = 24, I_DJNZ = 25, I_LDLC = 26,
                     I_CALL = 27, I_RDTIME = 28, I_SETT = 29, I_ADDT = 30, I_WAITT = 31,
                     I_ADDTR = 32, I_EMIT = 33, I_EMITR = 34, I_EMITX = 35, I_EXPECT = 36,
                     I_SAMPLE = 37, I_GETP = 38, I_WAITP = 39, I_GETI = 40, I_GETX = 41,
                     I_LDT = 42, I_STT = 43, I_CAPT = 44, I_GETC = 45, I_POP = 46,
                     I_PUSH = 47, I_RXREADY = 48, I_TXREADY = 49, I_SEND = 50, I_LINE = 51,
                     I_GETL = 52;

  // reserved payload bits per format (payload bits not named by the format)
  localparam [23:0] RSV_N = 24'hFFFFFF, RSV_I8 = 24'hFFFF00, RSV_R = 24'h0FFFFF,
                    RSV_RD = 24'h3FFFFF, RSV_RS = 24'hCFFFFF, RSV_I16 = 24'h3F0000,
                    RSV_SH = 24'h3FFFE0, RSV_B = 24'hFFFF00, RSV_L16 = 24'hFF0000,
                    RSV_T24 = 24'h000000, RSV_P = 24'h000000, RSV_PR = 24'h003F00,
                    RSV_PX = 24'h007F00, RSV_PE = 24'h000000, RSV_PS = 24'hF87FFF,
                    RSV_PW = 24'h0000FF, RSV_TM = 24'h0FFF00;
  localparam [31:0] DG_POLY = 32'h04C11DB7;

  // ------------------------------------------------------------------ global state
  reg        run;
  reg [31:0] now;
  wire [31:0] now_n = now + 32'd1;     // the counter's next value during a run
`ifdef FORMAL_ANYNOW
  // Formal only: the run starts at an arbitrary tick, so 2^32 wrap is within BMC reach.
  // Every property below is modular, so it must hold for any origin.
  (* anyconst *) reg [31:0] f_now0;
  wire [31:0] START_NOW = f_now0;
`else
  wire [31:0] START_NOW = 32'd0;
`endif
  reg [12:0] pin_val, pin_oe;          // physical pins (sec. 5)
  reg [12:0] s1, s2;
  reg        p1v, p2v, p3v;
  reg  [7:0] p1_spc, p2_spc;           // v1.7: the PC each instruction was fetched from
  reg        p3ok;                     // v1.7: ... equal to its context's PC in X
  reg [31:0] p2_insn, p3_insn;
  reg [52:0] d_op;                     // v1.6: decode of p3_insn, registered (below)
  reg        illegal;
  reg        ff_valid;
  reg [2:0]  ff_cause, ff_sub;
  reg [1:0]  ff_ctx;
  reg [7:0]  ff_pc;
  reg [31:0] ff_tick;

  wire       stop_q = stop & ~start;     // start wins over a simultaneous stop

  // ------------------------------------------------------------------ context state buses
  wire [127:0] c_r0, c_r1, c_r2, c_r3, c_sh, c_tmr, c_ish, c_tail, c_ffick, c_lee, c_rxh;
  wire [63:0]  c_lc, c_minl, c_wfull;
  wire [31:0]  c_pc, c_lr, c_pass, c_fail, c_ffobs, c_ffmask, c_fflvl, c_irqc;
  wire [31:0]  c_dmask, c_dval, c_doe;
  wire [11:0]  c_cnt, c_cnta, c_maxo;
  wire [7:0]   c_state;
  wire [3:0]   c_z, c_c, c_late, c_act, c_acta, c_failed, c_eseen, c_irqp;
  wire [3:0]   c_rxe, c_rxf, c_txe, c_txf, c_drv, c_hsmp, c_lret;
  // capture unit (v1.1)
  wire        cap_drain;
  wire [7:0]  cap_ptr, cap_rptr;
  wire [31:0] cap_head, cap_status, cap_cfg, cap_ovf_tick;

  // commit-stage write port (broadcast)
  reg         w_rd_we, w_z_we, w_z, w_c_we, w_c, w_sh_we, w_lc_we, w_tmr_we, w_lr_we;
  reg         w_halt, w_irq, w_geti, w_push, w_exp_admit, w_rx_pop, w_tx_push, waitfull;
  reg  [31:0] w_rd_data, w_sh, w_tmr;
  reg  [15:0] w_lc;
  reg  [7:0]  w_pc, w_lr;
  reg  [1:0]  outcome;
  reg  [2:0]  f_cause, f_sub;

  // ------------------------------------------------------------------ commit stage selection
  // The committing context, (now + 1) mod 4 during a run, is a register that counts with
  // now. While stopped no commit happens, and it holds the context the host's debug reads
  // of selects 0-8 select instead (v1.4, D-41); `run` stays off this path.
  // v1.7 pair mode: slot s belongs to context s, except that it belongs to context s^2 (its
  // partner) when that one is paired and s is off. sq counts the slots as cq did; cq, nq
  // and fq hold the owners of the commit, X and fetch slots, each a register, so the commit
  // stage's context selects start from flip-flops as before. With no pair, cq = sq,
  // nq = sq + 1 and fq = now mod 4, as in v1.6.
  wire [3:0]  pair_on = ctx_pair & ~{ctx_enable[1:0], ctx_enable[3:2]};
  function [1:0] own(input [1:0] s, input [3:0] pr);
    own = pr[s ^ 2'd2] ? (s ^ 2'd2) : s;
  endfunction
  reg  [1:0]  sq, cq, nq, fq;
  wire [1:0]  cctx    = cq;
  wire [31:0] insn    = p3_insn;

  wire [31:0] cs_r0   = c_r0[32*cctx +: 32];
  wire [31:0] cs_r1   = c_r1[32*cctx +: 32];
  wire [31:0] cs_r2   = c_r2[32*cctx +: 32];
  wire [31:0] cs_r3   = c_r3[32*cctx +: 32];
  wire [31:0] cs_sh   = c_sh[32*cctx +: 32];
  wire [31:0] cs_tmr  = c_tmr[32*cctx +: 32];
  wire [31:0] cs_ish  = c_ish[32*cctx +: 32];
  wire [31:0] cs_rxh  = c_rxh[32*cctx +: 32];
  wire [15:0] cs_lc   = c_lc[16*cctx +: 16];
  wire [7:0]  cs_pc   = c_pc[8*cctx +: 8];
  wire [7:0]  cs_lr   = c_lr[8*cctx +: 8];
  wire [7:0]  cs_pass = c_pass[8*cctx +: 8];
  wire [7:0]  cs_fail = c_fail[8*cctx +: 8];
  wire [2:0]  cs_cnta = c_cnta[3*cctx +: 3];
  wire [1:0]  cs_state = c_state[2*cctx +: 2];
  wire        cs_z     = c_z[cctx];
  wire        cs_c     = c_c[cctx];
  wire        cs_late  = c_late[cctx];
  wire        cs_act   = c_act[cctx];
  wire        cs_acta  = c_acta[cctx];
  wire        cs_failed = c_failed[cctx];
  wire        cs_eseen = c_eseen[cctx];
  wire        cs_rxe   = c_rxe[cctx];
  wire        cs_txf   = c_txf[cctx];
  wire        cs_hsmp  = c_hsmp[cctx];

  // Pin views (sec. 5). pown[13k+p]: physical pin p owned by ctx k. Context k's logical
  // pin j is physical pin j (bank A) or 8+j for j < 5 (bank B; logical 5..7 do not
  // exist there). owned_by[8k+j] and vsync[8k+j] are context k's logical views.
  wire [51:0] pown;
  wire [31:0] owned_by, vsync;
  genvar go, gp;
  generate
    for (go = 0; go < 4; go = go + 1) begin : g_own
      for (gp = 0; gp < 13; gp = gp + 1) begin : g_pin
        assign pown[13*go + gp] = pin_owned[gp] & (pin_owner[2*gp +: 2] == go[1:0]);
      end
      assign owned_by[8*go +: 8] = ctx_bank[go] ? {3'd0, pown[13*go + 8 +: 5]} : pown[13*go +: 8];
      assign vsync[8*go +: 8]    = ctx_bank[go] ? {3'd0, s2[12:8]} : s2[7:0];
    end
  endgenerate
  wire [7:0] owned_c = owned_by[8*cctx +: 8];
  wire [7:0] sync    = vsync[8*cctx +: 8];      // the committing context's input view

  // Line unit (v1.4, sec. 6.6): its state during this cycle, as commits see it.
  wire        lu_owned, lu_conf, lu_engaged, lu_busy, lu_port, lu_pwe, lu_flto, lu_fltc;
  wire        lu_doe;
  wire        lu_srd;                  // v1.6: a SEND with this R[rs] reads stream word 0
  wire [1:0]  lu_owner;
  wire [2:0]  lu_mode;
  wire [7:0]  lu_claimed, lu_paddr, lu_rel, lu_drv, lu_dval;
  // v1.7 (timing): the owner's bank, registered. ctx_bank is static during a run and the
  // owner changes only at a CONFIG that takes the unit (and at start); the unit uses its
  // view and pins no earlier than that CONFIG's completion, 9 cycles on, so a copy one
  // cycle behind is never seen (formal: lu_bank below). Off the high-fanout ctx_bank and
  // lu_owner nets, the line unit's input view and pin writes start from one flop.
  reg         lu_bank;
`ifdef ESC_NI
  wire [11:0] lu_el;
`endif
  wire [31:0] lu_status, lu_e, lu_pwdata;
  wire        lu_mine   = lu_owned & (lu_owner == cctx);     // the committing context owns it
  wire [7:0]  claimed_c = lu_mine ? lu_claimed : 8'd0;
  wire        lu_cfgf   = lu_flto | lu_fltc;                 // a CONFIG completing now faults

  // ------------------------------------------------------------------ fields
  wire [1:0]  f_rd    = insn[23:22];
  wire [15:0] f_imm16 = insn[15:0];
  wire [23:0] f_imm24 = insn[23:0];
  wire [4:0]  f_n     = insn[4:0];
  wire [7:0]  f_tgt   = insn[7:0];
  wire [7:0]  f_mask  = insn[23:16];
  wire [7:0]  f_val   = insn[15:8];
  wire        f_dir   = insn[15];
  wire [6:0]  f_win   = insn[6:0];

  reg [31:0] opa, opb;                 // v1.6: registered in the cycle before (below)
  function [31:0] pick(input [1:0] i, input [31:0] a0, input [31:0] a1, input [31:0] a2,
                       input [31:0] a3);
    pick = i[1] ? (i[0] ? a3 : a2) : (i[0] ? a1 : a0);
  endfunction

  // ------------------------------------------------------------------ decode
  // Legality (sec. 4): an unknown opcode, or a payload bit its format reserves.
  // One-hot opcode and reserved-bit mask as compare logic: a case table of constants
  // becomes a ROM in Yosys, whose read port can absorb the address register, a hidden
  // state that k-induction starts anywhere.
  function [52:0] onehot(input [7:0] o);
    onehot = {o == OP_GETL, o == OP_LINE, o == OP_SEND, o == OP_TXREADY, o == OP_RXREADY,
              o == OP_PUSH, o == OP_POP, o == OP_GETC, o == OP_CAPT, o == OP_STT,
              o == OP_LDT, o == OP_GETX, o == OP_GETI, o == OP_WAITP, o == OP_GETP,
              o == OP_SAMPLE, o == OP_EXPECT, o == OP_EMITX, o == OP_EMITR, o == OP_EMIT,
              o == OP_ADDTR, o == OP_WAITT, o == OP_ADDT, o == OP_SETT, o == OP_RDTIME,
              o == OP_CALL, o == OP_LDLC, o == OP_DJNZ, o == OP_JNC, o == OP_JC,
              o == OP_JNZ, o == OP_JZ, o == OP_JMP, o == OP_STSH, o == OP_LDSH,
              o == OP_CMPI, o == OP_ADDI, o == OP_SHR, o == OP_SHL, o == OP_TSTI,
              o == OP_CMP, o == OP_XOR, o == OP_OR, o == OP_AND, o == OP_SUB,
              o == OP_ADD, o == OP_MOVHI, o == OP_MOVI, o == OP_MOV, o == OP_RET,
              o == OP_IRQ, o == OP_HALT, o == OP_NOP};
  endfunction
  function [23:0] rsv_of(input [52:0] h);
    rsv_of = ({24{h[I_NOP] | h[I_HALT] | h[I_RET] | h[I_WAITT] | h[I_RXREADY] | h[I_TXREADY]}} & RSV_N)
           | ({24{h[I_IRQ]}} & RSV_I8)
           | ({24{h[I_MOV] | h[I_ADD] | h[I_SUB] | h[I_AND] | h[I_OR] | h[I_XOR] | h[I_CMP]}} & RSV_R)
           | ({24{h[I_MOVI] | h[I_MOVHI] | h[I_TSTI] | h[I_ADDI] | h[I_CMPI]}} & RSV_I16)
           | ({24{h[I_SHL] | h[I_SHR]}} & RSV_SH)
           | ({24{h[I_LDSH] | h[I_SETT] | h[I_ADDTR] | h[I_PUSH] | h[I_CAPT] | h[I_SEND] | h[I_LINE]}} & RSV_RS)
           | ({24{h[I_STSH] | h[I_RDTIME] | h[I_GETP] | h[I_GETI] | h[I_GETX] | h[I_POP] | h[I_GETC] | h[I_GETL]}} & RSV_RD)
           | ({24{h[I_LDT] | h[I_STT]}} & RSV_TM)
           | ({24{h[I_JMP] | h[I_JZ] | h[I_JNZ] | h[I_JC] | h[I_JNC] | h[I_DJNZ] | h[I_CALL]}} & RSV_B)
           | ({24{h[I_LDLC]}} & RSV_L16)
           | ({24{h[I_ADDT]}} & RSV_T24)
           | ({24{h[I_EMIT]}} & RSV_P)
           | ({24{h[I_EMITR]}} & RSV_PR)
           | ({24{h[I_EMITX]}} & RSV_PX)
           | ({24{h[I_EXPECT]}} & RSV_PE)
           | ({24{h[I_SAMPLE]}} & RSV_PS)
           | ({24{h[I_WAITP]}} & RSV_PW);
  endfunction
  function illegal_of(input [31:0] w);
    illegal_of = ~(|onehot(w[31:24])) | (|(w[23:0] & rsv_of(onehot(w[31:24]))));
  endfunction
  // v1.6: the instruction is decoded in the X stage, from p2_insn, and registered beside
  // p3_insn (both load at every edge), so the commit cycle starts from flip-flops instead
  // of the opcode compare and legality trees. Equal to the decode of p3_insn in every
  // cycle (formal, B6 and B7).
  wire [52:0] d_op_n    = onehot(p2_insn[31:24]);
  wire        illegal_n = illegal_of(p2_insn);

  // ------------------------------------------------------------------ datapath
  // Main adder: ADD/ADDI (A + B), SUB/CMP/CMPI (A + ~B + 1; borrow = ~carry).
  wire        is_sub  = d_op[I_SUB] | d_op[I_CMP] | d_op[I_CMPI];
  wire        is_immb = d_op[I_ADDI] | d_op[I_CMPI];
  wire [31:0] alu_b   = is_immb ? {16'd0, f_imm16} : opb;
  wire [32:0] sum     = {1'b0, opa} + {1'b0, (is_sub ? ~alu_b : alu_b)} + {32'd0, is_sub};

  // Shifter: SHR is done as SHL of the bit-reversed operand.
  function [31:0] rev32;
    input [31:0] x;
    integer i;
    begin
      for (i = 0; i < 32; i = i + 1) rev32[i] = x[31-i];
    end
  endfunction
  wire        is_shr = d_op[I_SHR];
  wire [31:0] shl_in = is_shr ? rev32(opa) : opa;
  wire [32:0] shl_t  = {1'b0, shl_in} << f_n;   // [32] = last bit shifted out (0 if n=0)
  wire [31:0] sh_res = is_shr ? rev32(shl_t[31:0]) : shl_t[31:0];

  // Timer adder: ADDT/ADDTR new tmr, EXPECT end-of-window d + window.
  wire [31:0] t_add  = d_op[I_ADDT]  ? {8'd0, f_imm24} :
                       d_op[I_ADDTR] ? opb : {25'd0, f_win};
  wire [31:0] t_sum  = cs_tmr + t_add;

  // Deadline arithmetic. d = tmr during the commit cycle; c = now. v1.5: computed in the
  // cycle before, for the context that commits next, (cq + 1) mod 4: its timer, its FIFO
  // tail and its EXPECT window end change only at its own commit, four edges back, so they
  // are the same in the commit cycle, and c is then now + 1 (a commit needs a run and no
  // start or stop at the edge between). Registered here; asserted equal to the definitions
  // in every commit cycle (formal, below).
  wire [31:0] n_tmr   = c_tmr[32*nq +: 32];
  // v1.6: the operands likewise, from p2_insn's register fields: a context's registers
  // change only at its own commit, four edges back, or (an LDT's load) at the edge after
  // it, three back. Asserted equal to the commit cycle's reads in every commit cycle
  // (formal, B8 and B9).
  wire [1:0]  n_rs    = d_op_n[I_EMITR] ? p2_insn[15:14] : p2_insn[21:20];
  wire [31:0] opa_n   = pick(p2_insn[23:22], c_r0[32*nq +: 32], c_r1[32*nq +: 32],
                             c_r2[32*nq +: 32], c_r3[32*nq +: 32]);
  wire [31:0] opb_n   = pick(n_rs, c_r0[32*nq +: 32], c_r1[32*nq +: 32],
                             c_r2[32*nq +: 32], c_r3[32*nq +: 32]);
  wire [31:0] nd_now  = n_tmr - now_n;
  wire [31:0] nd_tail = n_tmr - c_tail[32*nq +: 32];
  wire [31:0] nd_lee  = n_tmr - c_lee[32*nq +: 32];
  reg         ahd_now, rch_tmr, ahd_tail, ahd_lee;
  reg  [15:0] lead16;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      ahd_now <= 1'b0; rch_tmr <= 1'b0; ahd_tail <= 1'b0; ahd_lee <= 1'b0; lead16 <= 16'd0;
    end else begin
      ahd_now  <= (nd_now != 32'd0) & ~nd_now[31];                                 // ahead(d, c)
      rch_tmr  <= (nd_now == 32'd0) | (nd_now[31] & (nd_now[30:0] != 31'd0));      // reached(c, tmr)
      ahd_tail <= (nd_tail != 32'd0) & ~nd_tail[31];
      ahd_lee  <= (nd_lee != 32'd0) & ~nd_lee[31];
      lead16   <= (nd_now[31:16] != 16'd0) ? 16'hFFFF : nd_now[15:0];             // min(lead, 0xFFFF)
    end
  end

  wire        x_bit   = f_dir ? cs_sh[31] : cs_sh[0];          // EMITX bit
  wire [7:0]  ev_val  = d_op[I_EMITR] ? opb[7:0] :
                        d_op[I_EMITX] ? (x_bit ? f_mask : 8'd0) : f_val;
  wire [1:0]  ev_kind = d_op[I_EXPECT] ? K_EXPECT : d_op[I_SAMPLE] ? K_SAMPLE :
                        d_op[I_SEND] ? K_LINE : K_DRIVE;
  wire [3:0]  l_op    = opb[31:28];                            // LINE operation (sec. 7.7)
  wire [57:0] w_entry = {ev_kind, cs_tmr, f_mask, ev_val, insn[7:0]};
  wire        p_match = ((sync & f_mask) == f_val);            // WAITP
  wire [15:0] lc_dec  = cs_lc - 16'd1;
  wire [7:0]  pc_inc  = cs_pc + 8'd1;

  // ------------------------------------------------------------------ execute / outcome
  // z_src: 0 none, 1 rd_data, 2 sum (compare), 3 TSTI, 4 explicit (w_z preset)
  reg [2:0] z_src;
  reg       seq;        // COMPLETE would use a sequential next PC (or CALL): PC_RANGE check
  always @* begin
    outcome   = OC_C;
    f_cause   = 3'd0;
    f_sub     = 3'd0;
    seq       = 1'b1;
    w_pc      = pc_inc;
    w_rd_we   = 1'b0;
    w_rd_data = sum[31:0];
    z_src     = 3'd0;
    w_z       = 1'b0;
    w_c_we    = 1'b0;
    w_c       = 1'b0;
    w_sh_we   = 1'b0;
    w_sh      = opb;
    w_lc_we   = 1'b0;
    w_lc      = f_imm16;
    w_tmr_we  = 1'b0;
    w_tmr     = t_sum;
    w_lr_we   = 1'b0;
    w_lr      = pc_inc;
    w_halt    = 1'b0;
    w_irq     = 1'b0;
    w_geti    = 1'b0;
    w_push    = 1'b0;
    w_exp_admit = 1'b0;
    w_rx_pop  = 1'b0;
    w_tx_push = 1'b0;
    waitfull  = 1'b0;

    if (illegal) begin
      outcome = OC_F;
      f_cause = FC_ILLEGAL;
    end else begin
      (* parallel_case *)
      case (1'b1)
        d_op[I_NOP]: ;
        d_op[I_HALT]: begin
          seq  = 1'b0;
          w_pc = cs_pc;
          // v1.4: the owner of an engaged line unit waits too (sec. 7.2)
          if (cs_cnta == 3'd0 && !cs_acta && !(lu_mine && lu_engaged)) w_halt = 1'b1;
          else                                                         outcome = OC_W;
        end
        d_op[I_IRQ]: w_irq = 1'b1;
        d_op[I_RET]: begin
          seq  = 1'b0;
          w_pc = cs_lr;
        end
        d_op[I_MOV]:   begin w_rd_we = 1'b1; w_rd_data = opb;                     z_src = 3'd1; end
        d_op[I_MOVI]:  begin w_rd_we = 1'b1; w_rd_data = {16'd0, f_imm16};        z_src = 3'd1; end
        d_op[I_MOVHI]: begin w_rd_we = 1'b1; w_rd_data = {f_imm16, opa[15:0]};    z_src = 3'd1; end
        d_op[I_ADD], d_op[I_ADDI]: begin
          w_rd_we = 1'b1; w_rd_data = sum[31:0]; z_src = 3'd1;
          w_c_we  = 1'b1; w_c = sum[32];
        end
        d_op[I_SUB]: begin
          w_rd_we = 1'b1; w_rd_data = sum[31:0]; z_src = 3'd1;
          w_c_we  = 1'b1; w_c = ~sum[32];
        end
        d_op[I_CMP], d_op[I_CMPI]: begin
          z_src  = 3'd2;
          w_c_we = 1'b1; w_c = ~sum[32];
        end
        d_op[I_AND]: begin w_rd_we = 1'b1; w_rd_data = opa & opb; z_src = 3'd1; end
        d_op[I_OR]:  begin w_rd_we = 1'b1; w_rd_data = opa | opb; z_src = 3'd1; end
        d_op[I_XOR]: begin w_rd_we = 1'b1; w_rd_data = opa ^ opb; z_src = 3'd1; end
        d_op[I_TSTI]: z_src = 3'd3;
        d_op[I_SHL], d_op[I_SHR]: begin
          w_rd_we = 1'b1; w_rd_data = sh_res; z_src = 3'd1;
          w_c_we  = 1'b1; w_c = shl_t[32];
        end
        d_op[I_LDSH]: begin w_sh_we = 1'b1; w_sh = opb; end
        d_op[I_STSH]: begin w_rd_we = 1'b1; w_rd_data = cs_sh; end
        d_op[I_JMP]: begin seq = 1'b0; w_pc = f_tgt; end
        d_op[I_JZ]:  if (cs_z)  begin seq = 1'b0; w_pc = f_tgt; end
        d_op[I_JNZ]: if (!cs_z) begin seq = 1'b0; w_pc = f_tgt; end
        d_op[I_JC]:  if (cs_c)  begin seq = 1'b0; w_pc = f_tgt; end
        d_op[I_JNC]: if (!cs_c) begin seq = 1'b0; w_pc = f_tgt; end
        d_op[I_DJNZ]: begin
          w_lc_we = 1'b1; w_lc = lc_dec;
          if (lc_dec != 16'd0) begin seq = 1'b0; w_pc = f_tgt; end
        end
        d_op[I_LDLC]: begin w_lc_we = 1'b1; w_lc = f_imm16; end
        d_op[I_CALL]: begin
          // seq stays 1: CALL at PC 255 faults PC_RANGE (sec. 7.2)
          w_pc = f_tgt; w_lr_we = 1'b1; w_lr = pc_inc;
        end
        d_op[I_RDTIME]: begin w_rd_we = 1'b1; w_rd_data = now; end
        d_op[I_SETT]:   begin w_tmr_we = 1'b1; w_tmr = opb; end
        d_op[I_ADDT], d_op[I_ADDTR]: begin w_tmr_we = 1'b1; w_tmr = t_sum; end
        d_op[I_WAITT]:  if (!rch_tmr) outcome = OC_W;
        d_op[I_GETP]:   begin w_rd_we = 1'b1; w_rd_data = {24'd0, sync}; z_src = 3'd1; end
        d_op[I_WAITP]: begin
          if (p_match)      begin z_src = 3'd4; w_z = 1'b1; end
          else if (rch_tmr) begin z_src = 3'd4; w_z = 1'b0; end
          else              outcome = OC_W;
        end
        d_op[I_GETI]: begin
          // v1.1 (D-20): wait while a SAMPLE is in the FIFO during the commit cycle
          if (cs_hsmp) outcome = OC_W;
          else begin
            w_rd_we = 1'b1; w_rd_data = cs_ish; z_src = 3'd1; w_geti = 1'b1;
          end
        end
        d_op[I_GETC]: begin
          w_rd_we = 1'b1; w_rd_data = cap_status; z_src = 3'd1;
        end
        // LDT: the tape reads at this edge; the register is written at the next one
        // (ld_* below). STT: tape_we at this edge. CAPT: esc_cap. v1.4: LDT and STT wait
        // in a cycle the line unit uses the port (always one of its owner's cycles).
        d_op[I_LDT], d_op[I_STT]: if (lu_port) outcome = OC_W;
        d_op[I_CAPT]: ;
        // v1.4 line unit (sec. 7.7)
        d_op[I_LINE]: begin
          if (l_op > 4'd3) begin
            outcome = OC_F; f_cause = FC_ILLEGAL;
          end else if ((l_op == 4'd3) ? (lu_owned && !lu_mine)
                       : (!lu_mine || ((l_op == 4'd1 || l_op == 4'd2) && !lu_conf))) begin
            outcome = OC_F; f_cause = FC_ADMIT; f_sub = AS_OWNER;
          end else if (l_op != 4'd0 && lu_engaged) begin
            outcome = OC_W;
          end
        end
        d_op[I_GETL]: begin
          if (!lu_mine) begin
            outcome = OC_F; f_cause = FC_ADMIT; f_sub = AS_OWNER;
          end else if (!lu_busy || rch_tmr) begin
            w_rd_we = 1'b1; w_rd_data = lu_status; z_src = 3'd4; w_z = ~lu_busy;
            if (!lu_busy) begin w_tmr_we = 1'b1; w_tmr = lu_e; end
          end else begin
            outcome = OC_W;
          end
        end
        d_op[I_SEND]: begin
          // sec. 6.2 for a LINE entry: no mask check; a late packet is dropped, not faulted
          if (cs_cnta == 3'd4) begin
            outcome  = OC_W;
            waitfull = 1'b1;
          end else if (lu_claimed == 8'd0 || !lu_mine) begin   // unconfigured or no tx_pins
            outcome = OC_F; f_cause = FC_ADMIT; f_sub = AS_OWNER;
          end else if (lu_engaged) begin
            outcome = OC_W;
          end else if (!ahd_now) begin
            z_src = 3'd4; w_z = 1'b0;
          end else if (cs_cnta != 3'd0 && !ahd_tail) begin
            outcome = OC_F; f_cause = FC_ADMIT; f_sub = AS_ORDER;
          end else begin
            w_push = 1'b1; z_src = 3'd4; w_z = 1'b1;
          end
        end
        d_op[I_GETX]: begin
          w_rd_we = 1'b1; z_src = 3'd1;
          w_rd_data = {cs_fail, cs_pass, 14'd0, cs_act, cs_failed};
        end
        d_op[I_POP]: begin
          if (cs_rxe) outcome = OC_W;
          else begin w_rd_we = 1'b1; w_rd_data = cs_rxh; w_rx_pop = 1'b1; end
        end
        d_op[I_PUSH]: begin
          if (cs_txf) outcome = OC_W;
          else        w_tx_push = 1'b1;
        end
        d_op[I_RXREADY]: begin z_src = 3'd4; w_z = cs_rxe; end
        d_op[I_TXREADY]: begin z_src = 3'd4; w_z = cs_txf; end
        d_op[I_EMIT], d_op[I_EMITR], d_op[I_EMITX], d_op[I_EXPECT], d_op[I_SAMPLE]: begin
          // sec. 6.2 admission, checked in order on Q = FIFO after retirement at E_c
          if (cs_cnta == 3'd4) begin
            outcome  = OC_W;
            waitfull = 1'b1;
          end else if (ev_kind != K_SAMPLE && f_mask == 8'd0) begin
            outcome = OC_F; f_cause = FC_ADMIT; f_sub = AS_MASK;
          end else if (ev_kind == K_DRIVE && (f_mask & ~(owned_c & ~claimed_c)) != 8'd0) begin
            outcome = OC_F; f_cause = FC_ADMIT; f_sub = AS_OWNER;
          end else if (!ahd_now) begin
            outcome = OC_F; f_cause = FC_ADMIT; f_sub = AS_PAST;
          end else if (cs_cnta != 3'd0 && !ahd_tail) begin
            outcome = OC_F; f_cause = FC_ADMIT; f_sub = AS_ORDER;
          end else if (ev_kind == K_EXPECT && cs_eseen && !ahd_lee) begin
            outcome = OC_F; f_cause = FC_ADMIT; f_sub = AS_OVERLAP;
          end else begin
            w_push      = 1'b1;
            w_exp_admit = (ev_kind == K_EXPECT);
            if (d_op[I_EMITX]) begin
              w_sh_we = 1'b1;
              w_sh    = f_dir ? {cs_sh[30:0], 1'b0} : {1'b0, cs_sh[31:1]};
              w_c_we  = 1'b1;
              w_c     = x_bit;
            end
          end
        end
        default: ;  // unreachable: illegal opcodes handled above
      endcase

      // PC_RANGE (sec. 7): COMPLETE with a sequential next PC (or CALL) at PC 255
      if (outcome == OC_C && seq && cs_pc == 8'hFF) begin
        outcome = OC_F;
        f_cause = FC_PC_RANGE;
      end
    end

    // LATE_HEAD of the committing context at the same edge suppresses the commit
    // (unreachable by construction; see ESC_CORE_SPEC_QUESTIONS.md Q4)
    if (cs_late && outcome != OC_F) begin
      outcome = OC_F;
      f_cause = FC_LATE;
      f_sub   = 3'd0;
    end

    case (z_src)
      3'd1:    w_z = (w_rd_data == 32'd0);
      3'd2:    w_z = (sum[31:0] == 32'd0);
      3'd3:    w_z = ((opa & {16'd0, f_imm16}) == 32'd0);
      default: ;
    endcase
    w_z_we = (z_src != 3'd0);
  end

  // v1.7: p3ok, registered in X, says the instruction was fetched from its context's PC
  // as of X, which no commit changes before this one (pair mode: a guessed fetch).
  wire commit_valid = run & p3v & p3ok & (cs_state == ST_RUN) & ~start & ~stop & ~ctx_stop[cctx];
  wire cm_ok        = commit_valid & (outcome == OC_C);

  // ------------------------------------------------------------------ tape port (v1.1)
  // Busy in a cycle whose observed commit has opcode LDT/STT, whatever its outcome
  // (sec. 6.5); otherwise the capture unit may drain into it. v1.3: the host has the
  // port in every cycle the core does not use (tape_busy low), during a run too.
  wire        is_tm    = d_op[I_LDT] | d_op[I_STT];
  wire        tp_busy  = commit_valid & is_tm;
  // v1.4 (sec. 6.6): the line unit has the port for its scheduled accesses (an owner LDT
  // or STT then waits), and in the commit cycle of every SEND and LINE, whose operation
  // reads word R[rs][7:0] when it starts a CONFIG, a CRC job or a packet with data.
  // At a start or stop edge the unit makes no access.
  wire        lu_cmc   = commit_valid & (d_op[I_SEND] | d_op[I_LINE]);
  wire        lu_rd_c  = cm_ok & ((d_op[I_SEND] & w_push & lu_srd)
                                | (d_op[I_LINE] & ((l_op == 4'd3)
                                                     | ((l_op == 4'd2) & (opb[15:8] != 8'd0)))));
  wire        lu_acc   = lu_port & ~start & ~stop;
  wire        lu_tb    = lu_port | lu_cmc;
  assign tape_addr  = lu_port ? lu_paddr : lu_cmc ? opb[7:0]
                    : tp_busy ? (opb[7:0] + f_imm16[7:0]) : cap_ptr;
  assign tape_we    = lu_port ? (lu_acc & lu_pwe) : lu_cmc ? 1'b0
                    : tp_busy ? (cm_ok & d_op[I_STT]) : cap_drain;
  assign tape_re    = lu_port ? (lu_acc & ~lu_pwe) : lu_cmc ? lu_rd_c : (tp_busy & d_op[I_LDT]);
  assign tape_wdata = lu_port ? lu_pwdata : tp_busy ? opa : cap_head;
  assign tape_busy  = tp_busy | lu_tb | cap_drain;

  esc_cap u_cap (
      .clk(clk), .rst_n(rst_n), .start(start), .stop(stop_q), .run(run), .now(now),
      .psync(s2), .busy(tp_busy | lu_tb | tape_host_we), .ack(tape_ack), .ack_v(tape_ack_v),
      .capt_we(cm_ok & d_op[I_CAPT]), .capt_v(opb),
      .drain(cap_drain), .ptr_o(cap_ptr), .head_o(cap_head),
      .status(cap_status), .cfg(cap_cfg), .rptr(cap_rptr), .ovf_tick(cap_ovf_tick));

  // deferred LDT register write: committed at E_c, written at E_(c+1) (sec. 7.6)
  reg       ld_v;
  reg [1:0] ld_ctx, ld_rd;
  wire cm_wf        = commit_valid & (outcome == OC_W) & waitfull;
  wire cm_fault     = commit_valid & (outcome == OC_F);

  // per-context fault at this edge: commit fault, LATE_HEAD or (v1.4) a CONFIG of the
  // context's line unit completing invalid (not on start/stop)
  wire [3:0] cm_sel, lu_sel;
  wire [3:0] fault_k;
  genvar gk;
  generate
    for (gk = 0; gk < 4; gk = gk + 1) begin : g_sel
      assign cm_sel[gk]  = (cctx == gk[1:0]);
      assign lu_sel[gk]  = lu_owned & (lu_owner == gk[1:0]);
      assign fault_k[gk] = ~start & ~stop & ((cm_fault & cm_sel[gk]) | c_late[gk]
                                             | (lu_cfgf & lu_sel[gk]));
    end
  endgenerate

  // v1.7 (S3) live reload. CTX_STOP k: k's context sees a stop at this edge (RUN becomes
  // HALT, FIFO and checker killed), a commit of k at it is suppressed, k's pins release and
  // a line-unit job of k ends, as on a fault but with no record and no IRQ. CTX_START k
  // waits for the edge whose commit slot is k's first slot (k, or min(k, k^2) when paired):
  // nothing commits there (k is not RUNNING), so the shared latch write flops carry zeros
  // and the frame base to k's banks as at a run start, and the next cycle is k's fetch slot.
  // k then sees what a run started at that frame shows it (tmr = the frame base), so its
  // certificate holds unchanged.
  // A strobe for a context not enabled, or RUNNING in its cycle, is dropped. The host refuses
  // both, but the core does not rely on it: a disabled context's first slot may be its
  // partner's (pair mode), where the partner commits; and the host checks a cycle before its
  // strobe, so a pending restart of k can land on the very edge where the host accepts a
  // second CTX_START for k, and k would restart again mid-program.
  reg  [3:0]  rs_pend;
  wire [3:0]  c_runv;                  // k RUNNING in this cycle
  wire [3:0]  rs_want = rs_pend | (ctx_start & ctx_enable & ~c_runv);
  wire [3:0]  rs_k;                    // k restarts at this edge
  wire [3:0]  cs_k;                    // k stops at this edge (a restart or a run start wins)
  wire        rs_any  = |rs_k;
  wire [31:0] rs_tmr  = {now_n[31:2], 2'b00};
  function [1:0] first(input [1:0] k, input [3:0] pr);
    first = pr[k] ? {1'b0, k[0]} : k;
  endfunction
  generate
    for (gk = 0; gk < 4; gk = gk + 1) begin : g_rs
      assign c_runv[gk] = c_state[2*gk +: 2] == ST_RUN;
      assign rs_k[gk] = run & ~start & ~stop & rs_want[gk] & (sq == first(gk[1:0], pair_on));
      assign cs_k[gk] = ctx_stop[gk] & ~start & ~stop & ~rs_k[gk];
    end
  endgenerate

  always @(posedge clk or negedge rst_n)
    if (!rst_n)              rs_pend <= 4'd0;
    else if (start || stop)  rs_pend <= 4'd0;
    else                     rs_pend <= rs_want & ~rs_k & ~ctx_stop;

  // v1.7 (S3): the fetch slot of the coming cycle belongs to a context that is not RUNNING
  // (states become RUN only at a start or a restart), so that cycle reads nothing and a
  // program word written during a run may take the port. A register: the top's port select
  // is this flop and the host's, nothing from the fetch logic.
  reg        free_q;
  wire [1:0] fq_n = own(now_n[1:0], pair_on);
  always @(posedge clk or negedge rst_n)
    if (!rst_n) free_q <= 1'b0;
    else        free_q <= run & ~start & ~stop & (c_state[2*fq_n +: 2] != ST_RUN) & ~rs_k[fq_n];
  assign imem_free = free_q;

  // ------------------------------------------------------------------ latch write data
  // v1.4 (D-40): most of the contexts' banks are latches (esc_lreg). Each copies, while
  // its gated clock is high, what was written at the last edge, from these flip-flops:
  // one per write port, shared by all four contexts. No commit writes a register and SH
  // or a TX word together (asserted below), so those three share one; EXPECT's lee is
  // the timer adder's sum, which is w_tmr then. A start writes zeros.
  wire [31:0] q_wr, q_tmr, q_ld;
  wire [57:0] q_entry;
  reg  [3:0]  q_ldk;                   // the last edge's deferred LDT write, per context
  esc_greg #(32) g_qwr (.clk(clk), .rst_n(rst_n),
      .en(start | rs_any | (commit_valid & (w_rd_we | w_sh_we | w_tx_push))),
      .d((start | rs_any) ? 32'd0 : w_sh_we ? w_sh : w_tx_push ? opb : w_rd_data), .q(q_wr));
  esc_greg #(32) g_qtmr (.clk(clk), .rst_n(rst_n),
      .en(start | rs_any | (commit_valid & (w_tmr_we | w_exp_admit))),
      .d(start ? 32'd0 : rs_any ? rs_tmr : w_tmr), .q(q_tmr));
  esc_greg #(58) g_qent (.clk(clk), .rst_n(rst_n), .en(commit_valid & w_push),
      .d(w_entry), .q(q_entry));
  esc_greg #(32) g_qld (.clk(clk), .rst_n(rst_n), .en(ld_v), .d(tape_rdata), .q(q_ld));

  // ------------------------------------------------------------------ line unit (v1.4)
  wire lu_cm = cm_ok & d_op[I_LINE];
  esc_line u_line (
      .clk(clk), .rst_n(rst_n), .start(start), .now(now),
      .view(lu_bank ? {3'd0, s2[12:8]} : s2[7:0]), .owns(owned_by[8*lu_owner +: 8]),
      .op_send(cm_ok & d_op[I_SEND] & w_push),
      .op_off(lu_cm & (l_op == 4'd0)), .op_recv(lu_cm & (l_op == 4'd1)),
      .op_crc(lu_cm & (l_op == 4'd2)), .op_cfg(lu_cm & (l_op == 4'd3)),
      .op_ctx(cctx), .op_v(opb[29:0]),
      .line_ret(c_lret[lu_owner] & lu_owned), .halt_done(cm_ok & w_halt & lu_mine),
      .kill(stop_q | (|((fault_k | cs_k) & lu_sel))), .rdata(tape_rdata),
      .owned(lu_owned), .owner(lu_owner), .conf(lu_conf), .claimed(lu_claimed),
      .engaged(lu_engaged), .busy(lu_busy), .status(lu_status), .e(lu_e), .mode(lu_mode),
      .flt_owner(lu_flto), .flt_config(lu_fltc), .send_rd(lu_srd),
      .port(lu_port), .port_we(lu_pwe), .port_addr(lu_paddr), .port_wdata(lu_pwdata),
      .rel_m(lu_rel), .drv_m(lu_drv), .drv_val(lu_dval), .drv_oe(lu_doe)
`ifdef ESC_NI
      , .f_el(lu_el)
`endif
      );

  // ------------------------------------------------------------------ contexts
  generate
    for (gk = 0; gk < 4; gk = gk + 1) begin : g_ctx
      esc_ctx u_ctx (
          .clk         (clk),
          .rst_n       (rst_n),
          .start_i     (start | rs_k[gk]),
          .stop_i      (stop_q | cs_k[gk]),
          .start_tmr   (start ? 32'd0 : rs_tmr),
          .run         (run),
          .now         (now),
          .now_n       (now_n),
          .sync        (vsync[8*gk +: 8]),
          .cfg_enable  (ctx_enable[gk]),
          .cfg_entry_pc(entry_pcs[8*gk +: 8]),
          .cm_ok       (cm_ok & cm_sel[gk]),
          .cm_waitfull (cm_wf & cm_sel[gk] & ~fault_k[gk]),
          .fault       (fault_k[gk]),
          .w_rd_we     (w_rd_we),
          .w_rd_idx    (f_rd),
          .w_rd_data   (w_rd_data),
          .w_z_we      (w_z_we),
          .w_z         (w_z),
          .w_c_we      (w_c_we),
          .w_c         (w_c),
          .w_sh_we     (w_sh_we),
          .w_sh        (w_sh),
          .w_lc_we     (w_lc_we),
          .w_lc        (w_lc),
          .w_tmr_we    (w_tmr_we),
          .w_tmr       (w_tmr),
          .w_pc        (w_pc),
          .w_lr_we     (w_lr_we),
          .w_lr        (w_lr),
          .w_halt      (w_halt),
          .w_irq       (w_irq),
          .w_irq_code  (f_tgt),
          .w_geti      (w_geti),
          .w_push      (w_push),
          .w_entry     (w_entry),
          .w_lead16    (lead16),
          .w_exp_admit (w_exp_admit),
          .w_lee       (t_sum),
          .w_rx_pop    (w_rx_pop),
          .w_tx_push   (w_tx_push),
          .w_tx_data   (opb),
          .ld_we       (ld_v & (ld_ctx == gk[1:0]) & ~start),
          .ld_idx      (ld_rd),
          .ld_data     (tape_rdata),
          .q_wr        (q_wr),
          .q_tmr       (q_tmr),
          .q_entry     (q_entry),
          .q_ld        (q_ld),
          .q_ld_we     (q_ldk[gk]),
          .h_rx_push   (mbx_rx_push[gk]),
          .h_rx_data   (mbx_rx_data[32*gk +: 32]),
          .h_tx_pop    (mbx_tx_pop[gk]),
          .h_irq_clear (irq_clear[gk]),
          .o_r0        (c_r0[32*gk +: 32]),
          .o_r1        (c_r1[32*gk +: 32]),
          .o_r2        (c_r2[32*gk +: 32]),
          .o_r3        (c_r3[32*gk +: 32]),
          .o_sh        (c_sh[32*gk +: 32]),
          .o_lc        (c_lc[16*gk +: 16]),
          .o_tmr       (c_tmr[32*gk +: 32]),
          .o_pc        (c_pc[8*gk +: 8]),
          .o_lr        (c_lr[8*gk +: 8]),
          .o_z         (c_z[gk]),
          .o_c         (c_c[gk]),
          .o_state     (c_state[2*gk +: 2]),
          .o_ish       (c_ish[32*gk +: 32]),
          .o_cnt       (c_cnt[3*gk +: 3]),
          .o_cnt_after (c_cnta[3*gk +: 3]),
          .o_tail_dl   (c_tail[32*gk +: 32]),
          .o_late      (c_late[gk]),
          .o_act       (c_act[gk]),
          .o_act_after (c_acta[gk]),
          .o_pass      (c_pass[8*gk +: 8]),
          .o_fail      (c_fail[8*gk +: 8]),
          .o_failed    (c_failed[gk]),
          .o_ff_tick   (c_ffick[32*gk +: 32]),
          .o_ff_obs    (c_ffobs[8*gk +: 8]),
          .o_ff_mask   (c_ffmask[8*gk +: 8]),
          .o_ff_level  (c_fflvl[8*gk +: 8]),
          .o_exp_seen  (c_eseen[gk]),
          .o_lee       (c_lee[32*gk +: 32]),
          .o_min_lead  (c_minl[16*gk +: 16]),
          .o_max_occ   (c_maxo[3*gk +: 3]),
          .o_wait_full (c_wfull[16*gk +: 16]),
          .o_irq_p     (c_irqp[gk]),
          .o_irq_code  (c_irqc[8*gk +: 8]),
          .o_rx_empty  (c_rxe[gk]),
          .o_rx_full   (c_rxf[gk]),
          .o_rx_head   (c_rxh[32*gk +: 32]),
          .o_tx_empty  (c_txe[gk]),
          .o_tx_full   (c_txf[gk]),
          .o_tx_head   (mbx_tx_data[32*gk +: 32]),
          .o_drv_valid (c_drv[gk]),
          .o_drv_mask  (c_dmask[8*gk +: 8]),
          .o_drv_val   (c_dval[8*gk +: 8]),
          .o_drv_oe    (c_doe[8*gk +: 8]),
          .o_has_smp   (c_hsmp[gk]),
          .o_line_ret  (c_lret[gk])
`ifdef FORMAL
          , .f_owned   (owned_by[8*gk +: 8])
`endif
`ifdef ESC_NI
          , .f_all     (f_ctx_all[866*gk +: 866])
`endif
      );
    end
  endgenerate

  assign mbx_rx_full  = c_rxf;
  assign mbx_tx_empty = c_txe;
  assign running      = run;
  assign ctx_state    = c_state;
  assign irq_pending  = c_irqp;
  assign fault_valid  = ff_valid;

  // ------------------------------------------------------------------ fetch
  // v1.7: a paired context fetches its next instruction while the one before it is in X
  // (fq == nq). The X instruction's state is final then (its predecessor committed at the
  // edge before), so its branch resolves exactly; a wait-class instruction is guessed to
  // complete, except HALT (its own PC). (WAITT too: its reached test, a 32-bit compare on
  // now, would sit on the program store's address path.) A wrong guess costs a
  // bubble (p3ok), never a wrong commit. After an LDT, whose load lands an edge after it
  // commits, the slot is skipped so X never reads a register before the load.
  wire [7:0]  x_pc   = c_pc[8*nq +: 8];
  wire        x_ok    = p2v & (p2_spc == x_pc);
  wire        x_z     = c_z[nq], x_c = c_c[nq];
  wire [15:0] x_lc    = c_lc[16*nq +: 16];
  wire [7:0]  x_lr    = c_lr[8*nq +: 8];
  wire        x_jump  = d_op_n[I_JMP] | d_op_n[I_CALL] | (d_op_n[I_JZ] & x_z)
                      | (d_op_n[I_JNZ] & ~x_z) | (d_op_n[I_JC] & x_c) | (d_op_n[I_JNC] & ~x_c)
                      | (d_op_n[I_DJNZ] & (x_lc != 16'd1));
  wire        x_stay  = d_op_n[I_HALT];
  wire [7:0]  x_npc   = x_jump ? p2_insn[7:0] : d_op_n[I_RET] ? x_lr : x_stay ? x_pc
                      : x_pc + 8'd1;
  wire        f_chain = x_ok & (nq == fq);           // only a paired context, 2 slots apart
  wire [7:0]  f_pc    = f_chain ? x_npc : c_pc[8*fq +: 8];
  // v2 (S-A): the page is the fetching context's. f_chain means nq == fq, so f_pc is always
  // fq's PC; fq is a register and ctx_page static, so the page bits are off the f_pc path.
  assign imem_addr = {ctx_page[2*fq +: 2], f_pc};
  wire   f_valid   = run & (c_state[2*fq +: 2] == ST_RUN) & ~(f_chain & d_op_n[I_LDT]);
  // A slot whose context is not RUNNING is never committed (p3v), so its instruction need
  // not be read: a program with one context leaves the program store idle 3 cycles in 4,
  // and a halted chip leaves it idle. p2_insn then reloads the held read data, unused.
  assign imem_re = f_valid;

  // lowest-numbered context with LATE_HEAD (for the first-fault record)
  reg [1:0] late_ctx;
  always @* begin
    if (c_late[0])      late_ctx = 2'd0;
    else if (c_late[1]) late_ctx = 2'd1;
    else if (c_late[2]) late_ctx = 2'd2;
    else                late_ctx = 2'd3;
  end

  // pin register next state (sec. 5, 6.3, 8, 9). A retiring DRIVE's logical mask, value
  // and enable land on its context's bank; a fault releases the physical pins it owns.
  function [12:0] to_phys;
    input       bank;
    input [7:0] v;
    to_phys = bank ? {v[4:0], 8'd0} : {5'd0, v};
  endfunction
  // v1.4: a DRIVE leaves the pins its line unit claims during this cycle unchanged, and
  // the unit's own writes come after DRIVE retirement (and after fault release; the unit
  // of a faulting owner writes nothing).
  reg [12:0] val_n, oe_n, dm, dv, de, lm;
  integer pk, pi;
  always @* begin
    val_n = pin_val;
    oe_n  = pin_oe;
    for (pk = 0; pk < 4; pk = pk + 1) begin
      dm = to_phys(ctx_bank[pk], c_dmask[8*pk +: 8] & ~(lu_sel[pk] ? lu_claimed : 8'd0));
      dv = to_phys(ctx_bank[pk], c_dval[8*pk +: 8]);
      de = to_phys(ctx_bank[pk], c_doe[8*pk +: 8]);
      for (pi = 0; pi < 13; pi = pi + 1)
        if (c_drv[pk] && dm[pi]) begin
          val_n[pi] = dv[pi];
          oe_n[pi]  = de[pi];
        end
    end
    for (pk = 0; pk < 4; pk = pk + 1)
      if (fault_k[pk] | cs_k[pk]) oe_n = oe_n & ~pown[13*pk +: 13];
    oe_n  = oe_n & ~to_phys(lu_bank, lu_rel);
    lm    = to_phys(lu_bank, lu_drv);
    val_n = (val_n & ~lm) | (to_phys(lu_bank, lu_dval) & lm);
    oe_n  = lu_doe ? (oe_n | lm) : (oe_n & ~lm);
  end

  // ------------------------------------------------------------------ digest (sec. 10.1)
  // Bank B outputs fold in at bits 12:8 as ~ob (0 when released), so a run that never
  // drives bank B keeps its v1.1 digest.
  reg  [31:0] dg;
  reg  [20:0] dg_prev;                 // outputs during the previous cycle (bank B as ~ob)
  wire [4:0]  ob_low = ~ob_out;
  wire        dg_act = commit_valid | ld_v | ({ob_low, uio_oe, uio_out} != dg_prev);
  wire [31:0] dg_cw = commit_valid ? {16'd0, outcome, cctx, 4'd0, cs_pc} : 32'd0;
  wire [31:0] dg_wv = ((cm_ok & w_rd_we) ? w_rd_data : 32'd0) ^ (ld_v ? tape_rdata : 32'd0);
  wire [31:0] dg_x  = {uio_oe, uio_out, 3'd0, ob_low, 8'd0} ^ dg_cw ^ dg_wv;
  wire [31:0] dg_n  = {dg[30:0], 1'b0} ^ (dg[31] ? DG_POLY : 32'd0) ^ dg_x;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      dg <= 32'hFFFF_FFFF; dg_prev <= 21'd0;
      ld_v <= 1'b0; ld_ctx <= 2'd0; ld_rd <= 2'd0; q_ldk <= 4'd0;
    end else begin
      ld_v   <= ~start & cm_ok & d_op[I_LDT];
      ld_ctx <= cctx;
      ld_rd  <= f_rd;
      q_ldk  <= (ld_v & ~start) ? (4'd1 << ld_ctx) : 4'd0;
      if (start) begin
        dg <= 32'hFFFF_FFFF; dg_prev <= 21'd0;
      end else if (run && !stop) begin
        dg_prev <= {ob_low, uio_oe, uio_out};
        if (dg_act) dg <= dg_n;       // only active edges fold (sec. 10.1)
      end
    end
  end

  // ------------------------------------------------------------------ sequential
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      run <= 1'b0; now <= 32'd0; sq <= 2'd1; cq <= 2'd1; nq <= 2'd2; fq <= 2'd0;
      p1_spc <= 8'd0; p2_spc <= 8'd0; p3ok <= 1'b1; lu_bank <= 1'b0;
      pin_val <= 13'd0; pin_oe <= 13'd0;
      s1 <= 13'd0; s2 <= 13'd0;
      p1v <= 1'b0; p2v <= 1'b0; p3v <= 1'b0;
      p2_insn <= 32'd0; p3_insn <= 32'd0;
      d_op <= 53'd0; illegal <= 1'b1;          // the decode of 0: no opcode, illegal
      opa <= 32'd0; opb <= 32'd0;
      ff_valid <= 1'b0; ff_cause <= 3'd0; ff_sub <= 3'd0; ff_ctx <= 2'd0;
      ff_pc <= 8'd0; ff_tick <= 32'd0;
      dbg_commit_valid <= 1'b0; dbg_commit_ctx <= 2'd0; dbg_commit_pc <= 8'd0;
      dbg_commit_insn <= 32'd0; dbg_commit_outcome <= 2'd0;
    end else begin
      // synchronisers run continuously (not cleared by start)
      s1 <= {ib_in, uio_in};
      s2 <= s1;

      // instruction pipeline
      p2_insn <= imem_rdata;
      p3_insn <= p2_insn;
      d_op    <= d_op_n;
      illegal <= illegal_n;
      opa     <= opa_n;
      opb     <= opb_n;
      p1_spc  <= f_pc;
      lu_bank <= ctx_bank[lu_owner];
      p2_spc  <= p1_spc;
      p3ok    <= x_ok;
      if (start || stop) begin
        p1v <= 1'b0; p2v <= 1'b0; p3v <= 1'b0;
      end else begin
        p1v <= f_valid; p2v <= p1v; p3v <= p2v;
      end

      // observation
      dbg_commit_valid   <= commit_valid;
      dbg_commit_ctx     <= cctx;
      dbg_commit_pc      <= cs_pc;
      dbg_commit_insn    <= insn;
      dbg_commit_outcome <= outcome;

      if (start) begin
        sq <= START_NOW[1:0] + 2'd1;
        cq <= own(START_NOW[1:0] + 2'd1, pair_on);
        nq <= own(START_NOW[1:0] + 2'd2, pair_on);
        fq <= own(START_NOW[1:0], pair_on);
      end else if (run && !stop) begin
        sq <= sq + 2'd1;
        cq <= own(sq + 2'd1, pair_on);
        nq <= own(sq + 2'd2, pair_on);
        fq <= own(now_n[1:0], pair_on);
      end else begin
        sq <= dbg_sel_ctx;
        cq <= dbg_sel_ctx;
        nq <= dbg_sel_ctx + 2'd1;
        fq <= now[1:0];
      end
      if (start) begin
        run <= 1'b1; now <= START_NOW;
        pin_val <= 13'd0; pin_oe <= 13'd0;
        ff_valid <= 1'b0; ff_cause <= 3'd0; ff_sub <= 3'd0; ff_ctx <= 2'd0;
        ff_pc <= 8'd0; ff_tick <= 32'd0;
      end else if (stop) begin
        run    <= 1'b0;          // now holds
        pin_oe <= 13'd0;
      end else begin
        if (run) now <= now_n;
        pin_val <= val_n;
        pin_oe  <= oe_n;
        if (!ff_valid) begin
          // a commit fault and a line unit CONFIG fault (v1.4, never the owner's commit
          // edge) at one edge: the lower-numbered context is recorded (sec. 8)
          if (cm_fault && !(lu_cfgf && lu_owner < cctx)) begin
            ff_valid <= 1'b1; ff_cause <= f_cause; ff_sub <= f_sub;
            ff_ctx <= cctx; ff_pc <= cs_pc; ff_tick <= now;
          end else if (lu_cfgf) begin
            ff_valid <= 1'b1; ff_cause <= FC_ADMIT; ff_sub <= lu_flto ? AS_OWNER : AS_CONFIG;
            ff_ctx <= lu_owner; ff_pc <= c_pc[8*lu_owner +: 8]; ff_tick <= now;
          end else if (c_late != 4'd0) begin
            ff_valid <= 1'b1; ff_cause <= FC_LATE; ff_sub <= 3'd0;
            ff_ctx <= late_ctx; ff_pc <= c_pc[8*late_ctx +: 8]; ff_tick <= now;
          end
        end
      end
    end
  end

  // ------------------------------------------------------------------ pins
  assign uio_out = pin_val[7:0] & ~pin_od;
  assign uio_oe  = (pin_oe[7:0] & ~pin_od) | (pin_oe[7:0] & ~pin_val[7:0] & pin_od);
  // split pins: released means high, the level bank A floats to on its pull-up
  assign ob_out  = pin_val[12:8] | ~pin_oe[12:8];

  // ------------------------------------------------------------------ observation muxes
  // An LDT's register is written one edge after its commit: the observation record
  // shows the loaded value (sec. 7.6), forwarded from the tape output here.
  wire [1:0] pk_ = dbg_commit_ctx;
  assign dbg_post_r0  = (ld_v && ld_rd == 2'd0) ? tape_rdata : c_r0[32*pk_ +: 32];
  assign dbg_post_r1  = (ld_v && ld_rd == 2'd1) ? tape_rdata : c_r1[32*pk_ +: 32];
  assign dbg_post_r2  = (ld_v && ld_rd == 2'd2) ? tape_rdata : c_r2[32*pk_ +: 32];
  assign dbg_post_r3  = (ld_v && ld_rd == 2'd3) ? tape_rdata : c_r3[32*pk_ +: 32];
  assign dbg_post_sh  = c_sh[32*pk_ +: 32];
  assign dbg_post_lc  = c_lc[16*pk_ +: 16];
  assign dbg_post_tmr = c_tmr[32*pk_ +: 32];
  assign dbg_post_z   = c_z[pk_];
  assign dbg_post_c   = c_c[pk_];
  assign dbg_post_pc  = c_pc[8*pk_ +: 8];

  // v1.4 (D-41): a context's registers (selects 0-8) are read through the commit stage's
  // multiplexers, which select that context in its commit slot during a run and in every
  // cycle while stopped; dbg_cs holds the value from the last such cycle. Selects 9-31
  // read their sources directly.
  wire [1:0] sk = dbg_sel_ctx;
  reg  [31:0] dcs;
  always @* begin
    case (dbg_sel[3:0])
      4'd0:    dcs = cs_r0;
      4'd1:    dcs = cs_r1;
      4'd2:    dcs = cs_r2;
      4'd3:    dcs = cs_r3;
      4'd4:    dcs = cs_sh;
      4'd5:    dcs = {16'd0, cs_lc};
      4'd6:    dcs = cs_tmr;
      4'd7:    dcs = {8'd0, cs_lr, cs_pc, 4'd0, cs_state, cs_z, cs_c};
      default: dcs = cs_ish;
    endcase
  end
  // While stopped the registers hold still, so dbg_cs loads once after the stop and again
  // only when the host selects another field or context (dcs_tag): no clock otherwise.
  wire [31:0] dbg_cs;
  wire [5:0]  dcs_tag;
  reg         dcs_ok;
  wire        dcs_en = (cctx == sk) & (run | ~dcs_ok | (dcs_tag != {dbg_sel[3:0], sk}));
  esc_greg #(32) g_dcs (.clk(clk), .rst_n(rst_n), .en(dcs_en), .d(dcs), .q(dbg_cs));
  esc_greg #(6) g_dtag (.clk(clk), .rst_n(rst_n), .en(dcs_en), .d({dbg_sel[3:0], sk}), .q(dcs_tag));
  always @(posedge clk or negedge rst_n)
    if (!rst_n) dcs_ok <= 1'b0;
    else        dcs_ok <= ~run & (dcs_ok | dcs_en);
  always @* begin
    case (dbg_sel)
      5'd0, 5'd1, 5'd2, 5'd3, 5'd4, 5'd5, 5'd6, 5'd7, 5'd8: dbg_rdata = dbg_cs;
      5'd9:  dbg_rdata = {c_minl[16*sk +: 16], c_wfull[16*sk +: 16]};
      5'd10: dbg_rdata = {c_fail[8*sk +: 8], c_pass[8*sk +: 8], 5'd0, c_maxo[3*sk +: 3],
                          1'b0, c_cnt[3*sk +: 3], 2'd0, c_act[sk], c_failed[sk]};
      5'd11: dbg_rdata = c_ffick[32*sk +: 32];
      5'd12: dbg_rdata = {8'd0, c_ffmask[8*sk +: 8], c_fflvl[8*sk +: 8], c_ffobs[8*sk +: 8]};
      5'd13: dbg_rdata = {23'd0, c_irqp[sk], c_irqc[8*sk +: 8]};
      5'd14: dbg_rdata = {ff_valid, 3'd0, 1'b0, ff_cause, 1'b0, ff_sub, 2'd0, ff_ctx,
                          8'd0, ff_pc};
      5'd15: dbg_rdata = ff_tick;
      5'd16: dbg_rdata = dg;
      5'd17: dbg_rdata = cap_status;
      5'd18: dbg_rdata = cap_cfg;
      5'd19: dbg_rdata = cap_ovf_tick;
      5'd20: dbg_rdata = {24'd0, cap_rptr};
      5'd21: dbg_rdata = lu_status;                                            // v1.4
      5'd25: dbg_rdata = lu_e;
      5'd26: dbg_rdata = {lu_owned, 1'b0, lu_owner, 1'b0, lu_mode, 3'd0, lu_conf, 20'd0};
      default: dbg_rdata = 32'd0;
    endcase
  end

`ifdef FORMAL
  // ===================================================================== formal
  // Properties from docs/04-VERIFICATION.md, proved by SymbiYosys (formal/esc_core.sby).
  // imem_rdata, uio_in, host and debug inputs are unconstrained: every instruction
  // stream and every external behaviour is explored, not only our firmware.
  reg f_past_valid = 1'b0;
  always @(posedge clk) f_past_valid <= 1'b1;
  always @(*) if (!f_past_valid) assume (!rst_n);
  // Configuration is static during a run (docs/11-SEMANTICS.md §10).
  always @(posedge clk)
    if (f_past_valid && run) begin
      assume ($stable(ctx_enable));
      assume ($stable(entry_pcs));
      assume ($stable(pin_owned));
      assume ($stable(pin_owner));
      assume ($stable(pin_od));
      assume ($stable(ctx_bank));
      assume ($stable(ctx_pair));
      assume ($stable(ctx_page));    // v2 (S-A)
    end

  // v1.5 B1-B5: the deadline tests registered in the cycle before a commit are their
  // definitions (sec. 6.2) on the state of the commit cycle. Proved by k-induction
  // (esc_core.sby task `ahead`, which keeps only these labelled assertions): a commit
  // follows at least three edges of an uninterrupted run (p1v-p3v clear on start and
  // stop), over which the registers are a delayed copy of their definitions. Every other
  // task assumes them: bit-level PDR cannot learn a 32-bit compare as an invariant (B1
  // alone ran past an hour), and asserted at every step they double the bounded check's
  // time by depth 15.
  // the line unit's op inputs, as u_line is connected (for LI below)
  wire        f_lsend    = cm_ok & d_op[I_SEND] & w_push;
  wire        f_loff     = lu_cm & (l_op == 4'd0), f_lrecv = lu_cm & (l_op == 4'd1);
  wire        f_lcrc     = lu_cm & (l_op == 4'd2), f_lcfg  = lu_cm & (l_op == 4'd3);
  wire        f_lany     = f_lsend | f_loff | f_lrecv | f_lcrc | f_lcfg;
  wire [31:0] cs_tail    = c_tail[32*cctx +: 32];
  wire [31:0] cs_lee     = c_lee[32*cctx +: 32];
  wire [31:0] f_d_now    = cs_tmr - now;
  wire [31:0] f_d_tail   = cs_tmr - cs_tail;
  wire [31:0] f_d_lee    = cs_tmr - cs_lee;
  wire        f_ahd_now  = (f_d_now != 32'd0) & ~f_d_now[31];
  wire        f_ahd_tail = (f_d_tail != 32'd0) & ~f_d_tail[31];
`ifdef ESC_AHEAD_CHECK
  `define ESC_AHEAD assert
`else
  `define ESC_AHEAD assume
`endif
  always @(*) if (rst_n && commit_valid) begin
    ahead_b1: `ESC_AHEAD (ahd_now == f_ahd_now);
    ahead_b2: `ESC_AHEAD (rch_tmr == ((f_d_now == 32'd0) | (f_d_now[31] & (f_d_now[30:0] != 31'd0))));
    ahead_b3: `ESC_AHEAD (ahd_tail == f_ahd_tail);
    ahead_b4: `ESC_AHEAD (ahd_lee == ((f_d_lee != 32'd0) & ~f_d_lee[31]));
    ahead_b5: `ESC_AHEAD (lead16 == ((f_d_now[31:16] != 16'd0) ? 16'hFFFF : f_d_now[15:0]));
  end
  wire [7:0] op = insn[31:24];
  always @(*) if (rst_n) begin
    ahead_b6: `ESC_AHEAD (d_op == onehot(op));
    ahead_b7: `ESC_AHEAD (illegal == illegal_of(insn));
  end
  wire [1:0] f_rs = d_op[I_EMITR] ? insn[15:14] : insn[21:20];
  always @(*) if (rst_n && commit_valid) begin
    ahead_b8: `ESC_AHEAD (opa == pick(f_rd, cs_r0, cs_r1, cs_r2, cs_r3));
    ahead_b9: `ESC_AHEAD (opb == pick(f_rs, cs_r0, cs_r1, cs_r2, cs_r3));
  end
  // D-41: the committing-context register counts with now during a run; v1.7: the slot
  // counter does, and the commit, X and fetch registers hold their slots' owners. B0 is
  // proved with B1-B9 (the `ahead` task); B8-B9 are not 8-inductive without it.
  always @(*) if (rst_n && run)
    ahead_b0: `ESC_AHEAD (sq == now[1:0] + 2'd1 && cq == own(sq, pair_on)
                         && nq == own(sq + 2'd1, pair_on) && fq == own(now[1:0], pair_on));
`ifdef ESC_NI
  generate
    for (gk = 0; gk < 4; gk = gk + 1) begin : g_ni
      assign f_ctx_obs[320*gk +: 320] = {7'd0,
          c_state[2*gk +: 2], c_pc[8*gk +: 8], c_lr[8*gk +: 8], c_z[gk], c_c[gk],
          c_lc[16*gk +: 16], c_r0[32*gk +: 32], c_r1[32*gk +: 32], c_r2[32*gk +: 32],
          c_r3[32*gk +: 32], c_sh[32*gk +: 32], c_tmr[32*gk +: 32], c_ish[32*gk +: 32],
          c_cnt[3*gk +: 3], c_tail[32*gk +: 32], c_act[gk], c_irqp[gk],
          c_pass[8*gk +: 8], c_fail[8*gk +: 8]};
    end
  endgenerate
  assign f_pins = {pin_oe, pin_val};
  assign f_fq   = fq;
  assign f_core = {lu_owner, lu_owned, q_ld, rs_pend, q_ldk, ld_rd, ld_ctx, ld_v, lead16,
                   ahd_lee, ahd_tail, rch_tmr, ahd_now, opb, opa, illegal, d_op, p3ok, p3_insn,
                   p3v, p2_insn, p2_spc, p2v, p1_spc, p1v, s2, s1, fq, nq, cq, sq, now, run};
  assign f_lu   = {lu_el, lu_port, lu_rel, lu_drv, lu_mode, lu_bank};
`endif
  // v1.7 S3: a restart edge has no commit (the shared latch write flops are free for the
  // restarted context's zeros), and a cycle given to a host program write reads nothing.
  always @(*) if (rst_n) begin
    assert (!rs_any || !commit_valid);
    assert (!free_q || !imem_re);
    // v2 (S-A): a context fetches only from its own page
    if (imem_re) assert (imem_addr[9:8] == ctx_page[2*fq +: 2]);
  end
`undef ESC_AHEAD

  // driven(p): physical pin p is away from its released level (bank A: output enabled;
  // bank B: low, since a released split pin is high)
  wire [12:0] f_driven = {~ob_out, uio_oe};
  integer fk;
  always @(*) if (rst_n) begin
    // P1 electrical: an open-drain pin never drives high (§5).
    assert ((pin_od & uio_oe & uio_out) == 8'd0);
    // P2 NEVER LATE: no FIFO head is ever past its deadline (§6.3 LATE_HEAD invariant):
    // every admitted event retires exactly at its deadline or is cancelled. Proved for
    // all time compositionally: formal/esc_ctx.sby proves it per context from P6 + P8,
    // which the esc_core.sby `pdr` task proves here (ESC_CORE_PDR leaves P2 to that).
`ifndef ESC_CORE_PDR
    assert (c_late == 4'd0);
`endif
    // v1.7: with no context paired, the fetch is v1.6's (never a guessed PC)
    if (run && pair_on == 4'd0) assert (!f_chain && fq == now[1:0]);
    for (fk = 0; fk < 4; fk = fk + 1)
      // P4 FIFO occupancy bounded (§6).
      assert (c_cnt[3*fk +: 3] <= 3'd4);
    // P3 an unowned pin is never driven (§6.2 OWNER), and P5 a FAULTED context drives
    // none of its pins (§8 release), in either bank. Each is proved in two parts, every
    // part for all time: a pin away from its released level has its OE register set
    // (here), and the OE register never covers an unowned pin (P3) or a faulted
    // context's pin (P5) (the strengthenings below). Split into one task per assertion,
    // PDR proves no lemmas across tasks; stated directly on f_driven, P3 and P5 must
    // rediscover the second part, and their run times swung from 6 min to over an hour.
    assert ((f_driven & ~pin_oe) == 13'd0);
    // P6 admission: an accepted event is strictly ahead of its commit tick and of the
    // FIFO tail, within the half-range horizon (§6.2 steps 4-5).
    // Register-level strengthening (makes P3/P5 inductive): nothing is enabled while
    // stopped; the OE register never covers an unowned pin; a faulted context's pins
    // stay released (its FIFO is empty, H3, and other contexts drive only their own
    // pins, H4 in esc_ctx). Both OE parts rest on H4, so they are proved in one task
    // with H3 and H4; alone, the P3 part ran past three hours.
    if (!run) assert (pin_oe == 13'd0);
    for (fk = 0; fk < 4; fk = fk + 1)                 // stop turns RUNNING into HALTED
      if (!run) assert (c_state[2*fk +: 2] != ST_RUN);
    assert ((pin_oe & ~pin_owned) == 13'd0);                                     // lemma-group: faulted
    for (fk = 0; fk < 4; fk = fk + 1)
      if (c_state[2*fk +: 2] == 2'd3) assert ((pin_oe & pown[13*fk +: 13]) == 13'd0);  // lemma-group: faulted
    // v1.4 the line unit writes only pins it claims (and, at a CONFIG, pins the CONFIG
    // checked): it claims only pins its owner owns, and it is idle and unconfigured while
    // stopped and once its owner has faulted (that owner never commits again).
    if (lu_owned) assert ((lu_claimed & ~owned_by[8*lu_owner +: 8]) == 8'd0);  // lemma-group: faulted
    if (!run) assert (lu_mode == 3'd0 && !lu_conf);                             // lemma-group: faulted
    if (lu_owned && c_state[2*lu_owner +: 2] == 2'd3) assert (lu_mode == 3'd0 && !lu_conf);  // lemma-group: faulted
    // v1.7 lu_bank: unowned, the unit claims, drives and releases nothing
    if (!lu_owned) assert (lu_claimed == 8'd0 && lu_drv == 8'd0 && lu_rel == 8'd0);  // lemma-group: lubank
    // LI (v1.6) what formal/esc_line_env.v assumes about the line unit's inputs, from
    // which esc_line.sby proves the unit's registered values (D-44): one op at an edge,
    // only a running context's commit (so in its commit cycle, D-41), the owner's (or a
    // CONFIG taking an unowned unit), never while engaged (OFF excepted), RECV and CRC
    // only when configured, SEND only with claimed pins; no fault kills the unit at an op
    // edge, and a CONFIG completing invalid kills it unless start or stop does.
    if (f_lany) begin
      assert ({2'd0, f_lsend} + f_loff + f_lrecv + f_lcrc + f_lcfg == 3'd1);  // lemma-group: lineio
      assert (run && !start && !stop && cctx == own(now[1:0] + 2'd1, pair_on));  // lemma-group: lineio
      if (f_lcfg) assert (!lu_owned || lu_owner == cctx);  // lemma-group: lineio
      else        assert (lu_owned && lu_owner == cctx);  // lemma-group: lineio
      if (!f_loff) assert (!lu_engaged);  // lemma-group: lineio
      if (f_lrecv || f_lcrc) assert (lu_conf);  // lemma-group: lineio
      if (f_lsend) assert (lu_claimed != 8'd0);  // lemma-group: lineio
      assert ((fault_k & lu_sel) == 4'd0);  // lemma-group: commit
    end
    if (lu_owned && lu_cfgf && !start && !stop) assert ((fault_k & lu_sel) != 4'd0);  // lemma-group: lineio
    if ((fault_k & lu_sel) != 4'd0) assert (run);  // lemma-group: lineio
    if (cm_ok && w_push) begin
      assert (f_ahd_now);
      if (cs_cnta != 3'd0) assert (f_ahd_tail);
      // P8 the rest of what formal/esc_ctx_env.v assumes about a push
      assert (cs_cnta != 3'd4);
      if (ev_kind == K_DRIVE) assert ((f_mask & ~owned_c) == 8'd0);
    end
    // P8 a COMPLETE commit is a RUNNING context's, never on a start/stop/fault edge;
    // HALT completes only on an empty FIFO; faults happen only during a run.
    // A late FIFO head is a fault too (fault_k includes c_late), so "no fault on a
    // commit" contains P2. The pdr task proves the rest, faults other than lateness;
    // esc_ctx.sby assumes no more than that and proves P2, which completes the clause.
    // v1.4: the committing context's own fault; another context's CONFIG may fault it at
    // the same edge (esc_ctx_env.v assumes the per-context clause only).
`ifdef ESC_CORE_PDR
    if (cm_ok) assert (run && cs_state == ST_RUN && !start && !stop             // lemma-group: commit
                       && (fault_k & cm_sel & ~c_late) == 4'd0);
`else
    if (cm_ok) assert (run && cs_state == ST_RUN && !start && !stop
                       && (fault_k & cm_sel) == 4'd0);
`endif
    if (cm_ok && w_halt) assert (cs_cnta == 3'd0 && !w_push);
    if (fault_k != 4'd0) assert (run && !start && !stop);
  end

  // v1.7 lu_bank: the registered bank is the owner's once the unit has been owned for a
  // cycle of the run; in the cycle after a CONFIG takes the unit (the only other owned
  // cycle), the unit is loading that CONFIG and uses neither its view nor its pins.
  always @(posedge clk)
    if (f_past_valid && rst_n && $past(rst_n) && run && lu_owned) begin
      if ($past(run) && $past(lu_owned) && !$past(start))
        assert (lu_bank == ctx_bank[lu_owner]);  // lemma-group: lubank
      else
        assert (lu_mode == 3'd1 && lu_drv == 8'd0 && lu_rel == 8'd0);  // lemma-group: lubank
    end

  // P7 fixed slot timing: a commit observed during cycle c+1 belongs to the owner of slot
  // (c+1) mod 4 (v1.6: context (c+1) mod 4; v1.7: its partner when that one is paired and
  // it is off), and only enabled contexts ever commit (§3).
  always @(posedge clk)
    if (f_past_valid && rst_n && $past(rst_n) && dbg_commit_valid && run && $past(run)) begin
      assert (dbg_commit_ctx == own(now[1:0], pair_on));
      assert (ctx_enable[dbg_commit_ctx]);
    end

  // Reachability (vacuity guards): each property must be exercised, not hold vacuously.
  always @(*) if (rst_n && run) begin
    cover (pin_oe[7:0] != 8'd0);                  // a DRIVE retired onto a bank A pin
    cover (ob_out != 5'h1F);                      // ... and a bank B pin driven low
    cover (cm_wf);                                // EMIT waited on a full FIFO
    cover (cm_fault && f_cause == FC_ADMIT);      // an event was rejected
    cover (c_cnt[2:0] == 3'd4);                   // a FIFO is full
    cover (c_pass != 32'd0);                      // an EXPECT passed
    cover (c_fail != 32'd0);                      // an EXPECT failed
    cover (c_ish != 128'd0);                      // a SAMPLE shifted a 1 in
    cover (now == 32'hFFFF_FFFF);                 // tick counter wraps during a run
    cover (lu_drv != 8'd0);                       // v1.4: the line unit drives its pins
    cover (lu_mode == 3'd5);                      // ... receives a packet
    cover (lu_fltc);                              // ... a CONFIG faults its owner
    cover (cm_ok && f_chain && pair_on != 4'd0);  // v1.7: a commit while a guessed fetch is made
  end
  always @(posedge clk)                           // v1.7: a commit in a partner's slot
    if (f_past_valid && rst_n && run && $past(run))
      cover (dbg_commit_valid && dbg_commit_ctx != now[1:0]);
`endif

endmodule
`default_nettype wire
