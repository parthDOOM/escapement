/*
 * esc_core.v - Escapement core: 4-context fixed-slot barrel, shared decode /
 * execute / admission in the commit stage, pins, synchronisers, first-fault
 * register and the verification observation ports.
 *
 * Copyright (c) 2026 Escapement contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Normative semantics: docs/11-SEMANTICS.md v1.3.0 (section numbers below refer
 * to it). Encodings: isa/isa.yaml (decoder constants hand-copied).
 *
 * v1.1: the tape port (LDT/STT in the commit cycle; the capture unit esc_cap drains
 * into free cycles), the execution digest (sec. 10.1) and the wait-class GETI.
 * v1.2: pin bank B (sec. 5). Every context works in 8 logical pins; the mapping onto the
 * 13 physical pins happens only at the input view, the ownership masks, DRIVE and fault
 * updates of the pin registers, and the outputs.
 * v1.3: streaming capture. tape_busy marks only the cycles the core uses the tape port,
 * so the host can read during a run; TAPE_ACK sets the capture unit's rptr (sec. 6.5).
 *
 * Pipeline (each stage holds a different context, so no same-context hazards):
 *   F  cycle t   : imem_addr = PC[now mod 4] (combinational); slot valid if the
 *                  context is RUNNING during t. SRAM samples the address at E_t.
 *   D  cycle t+1 : imem_rdata valid; registered into p2_insn at E_(t+1).
 *   X  cycle t+2 : p2_insn -> p3_insn at E_(t+2).
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
    // configuration (static during a run)
    input  wire [3:0]   ctx_enable,
    input  wire [31:0]  entry_pcs,
    input  wire [12:0]  pin_owned,     // physical pins: 0..7 bank A, 8..12 bank B
    input  wire [25:0]  pin_owner,
    input  wire [7:0]   pin_od,        // bank A only
    input  wire [3:0]   ctx_bank,      // v1.2: context k's logical pins are bank B
    // instruction memory
    output wire [7:0]   imem_addr,
    output wire         imem_re,       // D-29: this slot's fetch will be used (else no read)
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
);

  // ------------------------------------------------------------------ constants
  localparam [1:0] ST_RUN = 2'd1;   // context state encoding: 0 IDLE, 1 RUNNING, 2 HALTED, 3 FAULTED
  localparam [1:0] OC_C = 2'd0, OC_W = 2'd1, OC_F = 2'd2;
  localparam [1:0] K_DRIVE = 2'd1, K_EXPECT = 2'd2, K_SAMPLE = 2'd3;
  localparam [2:0] FC_ILLEGAL = 3'd1, FC_PC_RANGE = 3'd2, FC_ADMIT = 3'd3, FC_LATE = 3'd4;
  localparam [2:0] AS_MASK = 3'd1, AS_OWNER = 3'd2, AS_PAST = 3'd3, AS_ORDER = 3'd4,
                   AS_OVERLAP = 3'd5;

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
                   OP_RXREADY = 8'h3A, OP_TXREADY = 8'h3B;

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
  reg [31:0] p2_insn, p3_insn;
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
  wire [3:0]   c_rxe, c_rxf, c_txe, c_txf, c_drv, c_hsmp;
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
  wire [1:0]  cctx    = now[1:0] + 2'd1;
  wire [31:0] insn    = p3_insn;
  wire [7:0]  op      = insn[31:24];

  wire [31:0] cs_r0   = c_r0[32*cctx +: 32];
  wire [31:0] cs_r1   = c_r1[32*cctx +: 32];
  wire [31:0] cs_r2   = c_r2[32*cctx +: 32];
  wire [31:0] cs_r3   = c_r3[32*cctx +: 32];
  wire [31:0] cs_sh   = c_sh[32*cctx +: 32];
  wire [31:0] cs_tmr  = c_tmr[32*cctx +: 32];
  wire [31:0] cs_ish  = c_ish[32*cctx +: 32];
  wire [31:0] cs_tail = c_tail[32*cctx +: 32];
  wire [31:0] cs_lee  = c_lee[32*cctx +: 32];
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

  // ------------------------------------------------------------------ fields
  wire [1:0]  f_rd    = insn[23:22];
  wire [1:0]  f_rs    = (op == OP_EMITR) ? insn[15:14] : insn[21:20];
  wire [15:0] f_imm16 = insn[15:0];
  wire [23:0] f_imm24 = insn[23:0];
  wire [4:0]  f_n     = insn[4:0];
  wire [7:0]  f_tgt   = insn[7:0];
  wire [7:0]  f_mask  = insn[23:16];
  wire [7:0]  f_val   = insn[15:8];
  wire        f_dir   = insn[15];
  wire [6:0]  f_win   = insn[6:0];

  reg [31:0] opa, opb;
  always @* begin
    case (f_rd)
      2'd0: opa = cs_r0; 2'd1: opa = cs_r1; 2'd2: opa = cs_r2; default: opa = cs_r3;
    endcase
    case (f_rs)
      2'd0: opb = cs_r0; 2'd1: opb = cs_r1; 2'd2: opb = cs_r2; default: opb = cs_r3;
    endcase
  end

  // ------------------------------------------------------------------ decode (legality)
  reg        legal;
  reg [23:0] rsv;
  always @* begin
    legal = 1'b1;
    rsv   = RSV_N;
    case (op)
      OP_NOP, OP_HALT, OP_RET, OP_WAITT, OP_RXREADY, OP_TXREADY: rsv = RSV_N;
      OP_IRQ:                                                    rsv = RSV_I8;
      OP_MOV, OP_ADD, OP_SUB, OP_AND, OP_OR, OP_XOR, OP_CMP:     rsv = RSV_R;
      OP_MOVI, OP_MOVHI, OP_TSTI, OP_ADDI, OP_CMPI:              rsv = RSV_I16;
      OP_SHL, OP_SHR:                                            rsv = RSV_SH;
      OP_LDSH, OP_SETT, OP_ADDTR, OP_PUSH, OP_CAPT:              rsv = RSV_RS;
      OP_STSH, OP_RDTIME, OP_GETP, OP_GETI, OP_GETX, OP_POP,
      OP_GETC:                                                   rsv = RSV_RD;
      OP_LDT, OP_STT:                                            rsv = RSV_TM;
      OP_JMP, OP_JZ, OP_JNZ, OP_JC, OP_JNC, OP_DJNZ, OP_CALL:    rsv = RSV_B;
      OP_LDLC:                                                   rsv = RSV_L16;
      OP_ADDT:                                                   rsv = RSV_T24;
      OP_EMIT:                                                   rsv = RSV_P;
      OP_EMITR:                                                  rsv = RSV_PR;
      OP_EMITX:                                                  rsv = RSV_PX;
      OP_EXPECT:                                                 rsv = RSV_PE;
      OP_SAMPLE:                                                 rsv = RSV_PS;
      OP_WAITP:                                                  rsv = RSV_PW;
      default:                                                   legal = 1'b0;
    endcase
  end
  wire illegal = ~legal | (|(insn[23:0] & rsv));

  // ------------------------------------------------------------------ datapath
  // Main adder: ADD/ADDI (A + B), SUB/CMP/CMPI (A + ~B + 1; borrow = ~carry).
  wire        is_sub  = (op == OP_SUB) | (op == OP_CMP) | (op == OP_CMPI);
  wire        is_immb = (op == OP_ADDI) | (op == OP_CMPI);
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
  wire        is_shr = (op == OP_SHR);
  wire [31:0] shl_in = is_shr ? rev32(opa) : opa;
  wire [32:0] shl_t  = {1'b0, shl_in} << f_n;   // [32] = last bit shifted out (0 if n=0)
  wire [31:0] sh_res = is_shr ? rev32(shl_t[31:0]) : shl_t[31:0];

  // Timer adder: ADDT/ADDTR new tmr, EXPECT end-of-window d + window.
  wire [31:0] t_add  = (op == OP_ADDT)  ? {8'd0, f_imm24} :
                       (op == OP_ADDTR) ? opb : {25'd0, f_win};
  wire [31:0] t_sum  = cs_tmr + t_add;

  // Deadline arithmetic. d = tmr during the commit cycle; c = now.
  wire [31:0] d_now   = cs_tmr - now;                          // lead
  wire        ahd_now = (d_now != 32'd0) & ~d_now[31];         // ahead(d, c)
  wire        rch_tmr = (d_now == 32'd0) | (d_now[31] & (d_now[30:0] != 31'd0)); // reached(c, tmr)
  wire [31:0] d_tail  = cs_tmr - cs_tail;
  wire        ahd_tail = (d_tail != 32'd0) & ~d_tail[31];
  wire [31:0] d_lee   = cs_tmr - cs_lee;
  wire        ahd_lee = (d_lee != 32'd0) & ~d_lee[31];
  wire [15:0] lead16  = (d_now[31:16] != 16'd0) ? 16'hFFFF : d_now[15:0];

  wire        x_bit   = f_dir ? cs_sh[31] : cs_sh[0];          // EMITX bit
  wire [7:0]  ev_val  = (op == OP_EMITR) ? opb[7:0] :
                        (op == OP_EMITX) ? (x_bit ? f_mask : 8'd0) : f_val;
  wire [1:0]  ev_kind = (op == OP_EXPECT) ? K_EXPECT : (op == OP_SAMPLE) ? K_SAMPLE : K_DRIVE;
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
      case (op)
        OP_NOP: ;
        OP_HALT: begin
          seq  = 1'b0;
          w_pc = cs_pc;
          if (cs_cnta == 3'd0 && !cs_acta) w_halt = 1'b1;
          else                             outcome = OC_W;
        end
        OP_IRQ: w_irq = 1'b1;
        OP_RET: begin
          seq  = 1'b0;
          w_pc = cs_lr;
        end
        OP_MOV:   begin w_rd_we = 1'b1; w_rd_data = opb;                     z_src = 3'd1; end
        OP_MOVI:  begin w_rd_we = 1'b1; w_rd_data = {16'd0, f_imm16};        z_src = 3'd1; end
        OP_MOVHI: begin w_rd_we = 1'b1; w_rd_data = {f_imm16, opa[15:0]};    z_src = 3'd1; end
        OP_ADD, OP_ADDI: begin
          w_rd_we = 1'b1; w_rd_data = sum[31:0]; z_src = 3'd1;
          w_c_we  = 1'b1; w_c = sum[32];
        end
        OP_SUB: begin
          w_rd_we = 1'b1; w_rd_data = sum[31:0]; z_src = 3'd1;
          w_c_we  = 1'b1; w_c = ~sum[32];
        end
        OP_CMP, OP_CMPI: begin
          z_src  = 3'd2;
          w_c_we = 1'b1; w_c = ~sum[32];
        end
        OP_AND: begin w_rd_we = 1'b1; w_rd_data = opa & opb; z_src = 3'd1; end
        OP_OR:  begin w_rd_we = 1'b1; w_rd_data = opa | opb; z_src = 3'd1; end
        OP_XOR: begin w_rd_we = 1'b1; w_rd_data = opa ^ opb; z_src = 3'd1; end
        OP_TSTI: z_src = 3'd3;
        OP_SHL, OP_SHR: begin
          w_rd_we = 1'b1; w_rd_data = sh_res; z_src = 3'd1;
          w_c_we  = 1'b1; w_c = shl_t[32];
        end
        OP_LDSH: begin w_sh_we = 1'b1; w_sh = opb; end
        OP_STSH: begin w_rd_we = 1'b1; w_rd_data = cs_sh; end
        OP_JMP: begin seq = 1'b0; w_pc = f_tgt; end
        OP_JZ:  if (cs_z)  begin seq = 1'b0; w_pc = f_tgt; end
        OP_JNZ: if (!cs_z) begin seq = 1'b0; w_pc = f_tgt; end
        OP_JC:  if (cs_c)  begin seq = 1'b0; w_pc = f_tgt; end
        OP_JNC: if (!cs_c) begin seq = 1'b0; w_pc = f_tgt; end
        OP_DJNZ: begin
          w_lc_we = 1'b1; w_lc = lc_dec;
          if (lc_dec != 16'd0) begin seq = 1'b0; w_pc = f_tgt; end
        end
        OP_LDLC: begin w_lc_we = 1'b1; w_lc = f_imm16; end
        OP_CALL: begin
          // seq stays 1: CALL at PC 255 faults PC_RANGE (sec. 7.2)
          w_pc = f_tgt; w_lr_we = 1'b1; w_lr = pc_inc;
        end
        OP_RDTIME: begin w_rd_we = 1'b1; w_rd_data = now; end
        OP_SETT:   begin w_tmr_we = 1'b1; w_tmr = opb; end
        OP_ADDT, OP_ADDTR: begin w_tmr_we = 1'b1; w_tmr = t_sum; end
        OP_WAITT:  if (!rch_tmr) outcome = OC_W;
        OP_GETP:   begin w_rd_we = 1'b1; w_rd_data = {24'd0, sync}; z_src = 3'd1; end
        OP_WAITP: begin
          if (p_match)      begin z_src = 3'd4; w_z = 1'b1; end
          else if (rch_tmr) begin z_src = 3'd4; w_z = 1'b0; end
          else              outcome = OC_W;
        end
        OP_GETI: begin
          // v1.1 (D-20): wait while a SAMPLE is in the FIFO during the commit cycle
          if (cs_hsmp) outcome = OC_W;
          else begin
            w_rd_we = 1'b1; w_rd_data = cs_ish; z_src = 3'd1; w_geti = 1'b1;
          end
        end
        OP_GETC: begin
          w_rd_we = 1'b1; w_rd_data = cap_status; z_src = 3'd1;
        end
        // LDT: the tape reads at this edge; the register is written at the next one
        // (ld_* below). STT: tape_we at this edge. CAPT: esc_cap.
        OP_LDT, OP_STT, OP_CAPT: ;
        OP_GETX: begin
          w_rd_we = 1'b1; z_src = 3'd1;
          w_rd_data = {cs_fail, cs_pass, 14'd0, cs_act, cs_failed};
        end
        OP_POP: begin
          if (cs_rxe) outcome = OC_W;
          else begin w_rd_we = 1'b1; w_rd_data = cs_rxh; w_rx_pop = 1'b1; end
        end
        OP_PUSH: begin
          if (cs_txf) outcome = OC_W;
          else        w_tx_push = 1'b1;
        end
        OP_RXREADY: begin z_src = 3'd4; w_z = cs_rxe; end
        OP_TXREADY: begin z_src = 3'd4; w_z = cs_txf; end
        OP_EMIT, OP_EMITR, OP_EMITX, OP_EXPECT, OP_SAMPLE: begin
          // sec. 6.2 admission, checked in order on Q = FIFO after retirement at E_c
          if (cs_cnta == 3'd4) begin
            outcome  = OC_W;
            waitfull = 1'b1;
          end else if (ev_kind != K_SAMPLE && f_mask == 8'd0) begin
            outcome = OC_F; f_cause = FC_ADMIT; f_sub = AS_MASK;
          end else if (ev_kind == K_DRIVE && (f_mask & ~owned_c) != 8'd0) begin
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
            if (op == OP_EMITX) begin
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

  wire commit_valid = run & p3v & (cs_state == ST_RUN) & ~start & ~stop;
  wire cm_ok        = commit_valid & (outcome == OC_C);

  // ------------------------------------------------------------------ tape port (v1.1)
  // Busy in a cycle whose observed commit has opcode LDT/STT, whatever its outcome
  // (sec. 6.5); otherwise the capture unit may drain into it. v1.3: the host has the
  // port in every cycle the core does not use (tape_busy low), during a run too.
  wire        is_tm    = (op == OP_LDT) | (op == OP_STT);
  wire        tp_busy  = commit_valid & is_tm;
  assign tape_addr  = tp_busy ? (opb[7:0] + f_imm16[7:0]) : cap_ptr;
  assign tape_we    = tp_busy ? (cm_ok & (op == OP_STT)) : cap_drain;
  assign tape_re    = tp_busy & (op == OP_LDT);
  assign tape_wdata = tp_busy ? opa : cap_head;
  assign tape_busy  = tp_busy | cap_drain;

  esc_cap u_cap (
      .clk(clk), .rst_n(rst_n), .start(start), .stop(stop_q), .run(run), .now(now),
      .psync(s2), .busy(tp_busy | tape_host_we), .ack(tape_ack), .ack_v(tape_ack_v),
      .capt_we(cm_ok & (op == OP_CAPT)), .capt_v(opb),
      .drain(cap_drain), .ptr_o(cap_ptr), .head_o(cap_head),
      .status(cap_status), .cfg(cap_cfg), .rptr(cap_rptr), .ovf_tick(cap_ovf_tick));

  // deferred LDT register write: committed at E_c, written at E_(c+1) (sec. 7.6)
  reg       ld_v;
  reg [1:0] ld_ctx, ld_rd;
  wire cm_wf        = commit_valid & (outcome == OC_W) & waitfull;
  wire cm_fault     = commit_valid & (outcome == OC_F);

  // per-context fault at this edge: commit fault or LATE_HEAD (not on start/stop)
  wire [3:0] cm_sel;
  wire [3:0] fault_k;
  genvar gk;
  generate
    for (gk = 0; gk < 4; gk = gk + 1) begin : g_sel
      assign cm_sel[gk]  = (cctx == gk[1:0]);
      assign fault_k[gk] = ~start & ~stop & ((cm_fault & cm_sel[gk]) | c_late[gk]);
    end
  endgenerate

  // ------------------------------------------------------------------ contexts
  generate
    for (gk = 0; gk < 4; gk = gk + 1) begin : g_ctx
      esc_ctx u_ctx (
          .clk         (clk),
          .rst_n       (rst_n),
          .start_i     (start),
          .stop_i      (stop_q),
          .run         (run),
          .now         (now),
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
          .o_has_smp   (c_hsmp[gk])
`ifdef FORMAL
          , .f_owned   (owned_by[8*gk +: 8])
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
  assign imem_addr = c_pc[8*now[1:0] +: 8];
  wire   f_valid   = run & (c_state[2*now[1:0] +: 2] == ST_RUN);
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
  reg [12:0] val_n, oe_n, dm, dv, de;
  integer pk, pi;
  always @* begin
    val_n = pin_val;
    oe_n  = pin_oe;
    for (pk = 0; pk < 4; pk = pk + 1) begin
      dm = to_phys(ctx_bank[pk], c_dmask[8*pk +: 8]);
      dv = to_phys(ctx_bank[pk], c_dval[8*pk +: 8]);
      de = to_phys(ctx_bank[pk], c_doe[8*pk +: 8]);
      for (pi = 0; pi < 13; pi = pi + 1)
        if (c_drv[pk] && dm[pi]) begin
          val_n[pi] = dv[pi];
          oe_n[pi]  = de[pi];
        end
    end
    for (pk = 0; pk < 4; pk = pk + 1)
      if (fault_k[pk]) oe_n = oe_n & ~pown[13*pk +: 13];
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
      ld_v <= 1'b0; ld_ctx <= 2'd0; ld_rd <= 2'd0;
    end else begin
      ld_v   <= ~start & cm_ok & (op == OP_LDT);
      ld_ctx <= cctx;
      ld_rd  <= f_rd;
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
      run <= 1'b0; now <= 32'd0;
      pin_val <= 13'd0; pin_oe <= 13'd0;
      s1 <= 13'd0; s2 <= 13'd0;
      p1v <= 1'b0; p2v <= 1'b0; p3v <= 1'b0;
      p2_insn <= 32'd0; p3_insn <= 32'd0;
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
        run <= 1'b1; now <= START_NOW;
        pin_val <= 13'd0; pin_oe <= 13'd0;
        ff_valid <= 1'b0; ff_cause <= 3'd0; ff_sub <= 3'd0; ff_ctx <= 2'd0;
        ff_pc <= 8'd0; ff_tick <= 32'd0;
      end else if (stop) begin
        run    <= 1'b0;          // now holds
        pin_oe <= 13'd0;
      end else begin
        if (run) now <= now + 32'd1;
        pin_val <= val_n;
        pin_oe  <= oe_n;
        if (!ff_valid) begin
          if (cm_fault) begin
            ff_valid <= 1'b1; ff_cause <= f_cause; ff_sub <= f_sub;
            ff_ctx <= cctx; ff_pc <= cs_pc; ff_tick <= now;
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

  wire [1:0] sk = dbg_sel_ctx;
  always @* begin
    case (dbg_sel)
      5'd0:  dbg_rdata = c_r0[32*sk +: 32];
      5'd1:  dbg_rdata = c_r1[32*sk +: 32];
      5'd2:  dbg_rdata = c_r2[32*sk +: 32];
      5'd3:  dbg_rdata = c_r3[32*sk +: 32];
      5'd4:  dbg_rdata = c_sh[32*sk +: 32];
      5'd5:  dbg_rdata = {16'd0, c_lc[16*sk +: 16]};
      5'd6:  dbg_rdata = c_tmr[32*sk +: 32];
      5'd7:  dbg_rdata = {8'd0, c_lr[8*sk +: 8], c_pc[8*sk +: 8], 4'd0,
                          c_state[2*sk +: 2], c_z[sk], c_c[sk]};
      5'd8:  dbg_rdata = c_ish[32*sk +: 32];
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
    end

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
    // pins, H4 in esc_ctx).
    if (!run) assert (pin_oe == 13'd0);
    for (fk = 0; fk < 4; fk = fk + 1)                 // stop turns RUNNING into HALTED
      if (!run) assert (c_state[2*fk +: 2] != ST_RUN);
    assert ((pin_oe & ~pin_owned) == 13'd0);
    for (fk = 0; fk < 4; fk = fk + 1)
      if (c_state[2*fk +: 2] == 2'd3) assert ((pin_oe & pown[13*fk +: 13]) == 13'd0);  // lemma-group: faulted
    if (cm_ok && w_push) begin
      assert (ahd_now);
      if (cs_cnta != 3'd0) assert (ahd_tail);
      // P8 the rest of what formal/esc_ctx_env.v assumes about a push
      assert (cs_cnta != 3'd4);
      assert (ev_kind != 2'd0);
      if (ev_kind == K_DRIVE) assert ((f_mask & ~owned_c) == 8'd0);
    end
    // P8 a COMPLETE commit is a RUNNING context's, never on a start/stop/fault edge;
    // HALT completes only on an empty FIFO; faults happen only during a run.
    // A late FIFO head is a fault too (fault_k includes c_late), so "no fault on a
    // commit" contains P2. The pdr task proves the rest, faults other than lateness;
    // esc_ctx.sby assumes no more than that and proves P2, which completes the clause.
`ifdef ESC_CORE_PDR
    if (cm_ok) assert (run && cs_state == ST_RUN && !start && !stop
                       && (fault_k & ~c_late) == 4'd0);
`else
    if (cm_ok) assert (run && cs_state == ST_RUN && !start && !stop && fault_k == 4'd0);
`endif
    if (cm_ok && w_halt) assert (cs_cnta == 3'd0 && !w_push);
    if (fault_k != 4'd0) assert (run && !start && !stop);
  end

  // P7 fixed slot timing: a commit observed during cycle c+1 belongs to ctx (c+1) mod 4,
  // and only enabled contexts ever commit (§3).
  always @(posedge clk)
    if (f_past_valid && rst_n && $past(rst_n) && dbg_commit_valid && run && $past(run)) begin
      assert (dbg_commit_ctx == now[1:0]);
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
  end
`endif

endmodule
`default_nettype wire
