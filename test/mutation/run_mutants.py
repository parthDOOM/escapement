#!/usr/bin/env python3
"""Mutation testing of the RTL (docs/04-VERIFICATION.md).

Each mutant is one change to a copy of src/: the curated mutants below, each aimed at a
specific feature, and the generated ones (generate.py), one classic fault at a sampled
site of every synthesisable line. The test suites that can see the mutated file run
against the copy, cheapest first, and the first that fails kills it:

    esc_*.v            cosim (ISS lockstep), rtl_unit, chip tests, pin tests
    escapement_host.v  host, chip tests, pin tests
    escapement_sram.v  host, cosim, chip tests
    project.v          chip tests, pin tests

A suite that hangs past its time limit counts as a kill. A mutant that no suite kills is
then checked formally against the original RTL (formal/equiv.sh with the mutated tree):
EQUIVALENT means no input sequence ever makes any chip output differ, so no test could
kill it; SURVIVED means a test hole, or a proof that did not finish.

    python3 test/mutation/run_mutants.py [--jobs J] [--seeds N] [--only ID ...]
        [--curated | --generated] [--shard I/N] [--no-equiv] [--json FILE] [--list]

Run under WSL/Linux with the simulation environment (scripts/setup-wsl.sh) active; the
equivalence check also needs PDK_ROOT (or LIB) as for make equiv.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate  # noqa: E402

# Curated mutants: name -> (file, original text, mutated text, what it breaks)
MUTANTS = {
    # ---- v1.1 execution digest (sec. 10.1)
    "dg_no_poly": ("esc_core.v", "(dg[31] ? DG_POLY : 32'd0)", "32'd0",
                   "digest feedback polynomial dropped"),
    "dg_no_ldt": ("esc_core.v", " ^ (ld_v ? tape_rdata : 32'd0)", "",
                  "LDT loaded value not folded into the digest"),
    "dg_no_pins": ("esc_core.v", "{uio_oe, uio_out, 3'd0, ob_low, 8'd0} ^ dg_cw", "dg_cw",
                   "pin state not folded into the digest"),
    # ---- v1.2 pin bank B (sec. 5)
    "b_drive_shifted": ("esc_core.v", "to_phys = bank ? {v[4:0], 8'd0} : {5'd0, v};",
                        "to_phys = bank ? {v[3:0], 9'd0} : {5'd0, v};",
                        "a bank B DRIVE lands one pin off"),
    "b_released_low": ("esc_core.v", "assign ob_out  = pin_val[12:8] | ~pin_oe[12:8];",
                       "assign ob_out  = pin_val[12:8] & pin_oe[12:8];",
                       "a released bank B output is low (selects an SPI device at reset)"),
    "b_view_bank_a": ("esc_core.v", "assign vsync[8*go +: 8]    = ctx_bank[go] ? {3'd0, s2[12:8]} : s2[7:0];",
                      "assign vsync[8*go +: 8]    = s2[7:0];",
                      "a bank B context reads bank A's inputs"),
    "b_owner_bank_a": ("esc_core.v",
                       "assign owned_by[8*go +: 8] = ctx_bank[go] ? {3'd0, pown[13*go + 8 +: 5]} : pown[13*go +: 8];",
                       "assign owned_by[8*go +: 8] = pown[13*go +: 8];",
                       "bank B ownership checked against bank A pins"),
    "b_fault_keeps_pins": ("esc_core.v", "if (fault_k[pk]) oe_n = oe_n & ~pown[13*pk +: 13];",
                           "if (fault_k[pk]) oe_n = oe_n & ~{5'd0, pown[13*pk +: 8]};",
                           "a fault does not release the context's bank B pins"),
    "b_sync_missing": ("esc_core.v", "s1 <= {ib_in, uio_in};", "s1 <= {5'd0, uio_in};",
                       "bank B inputs never reach the synchronisers"),
    "b_digest_blind": ("esc_core.v", "{ob_low, uio_oe, uio_out} != dg_prev", "{5'd0, uio_oe, uio_out} != dg_prev",
                       "a bank B output change alone is not an active digest edge"),
    "b_capture_bank_a": ("esc_cap.v", "wire [7:0] sync = bank ? {3'd0, psync[12:8]} : psync[7:0];",
                         "wire [7:0] sync = psync[7:0];",
                         "capture ignores the CAPT bank bit"),
    # ---- v1.1 tape port and LDT/STT (sec. 7.6)
    "tape_no_imm": ("esc_core.v", "(opb[7:0] + f_imm16[7:0])", "opb[7:0]",
                    "tape address ignores imm8"),
    "stt_ignores_outcome": ("esc_core.v", "(cm_ok & d_op[I_STT])", "(commit_valid & d_op[I_STT])",
                            "STT writes even when it faults (ILLEGAL/PC_RANGE)"),
    "ldt_no_forward": ("esc_core.v", "(ld_v && ld_rd == 2'd0) ? tape_rdata : ", "",
                       "observation record of LDT r0 shows the stale value"),
    "ldt_wrong_ctx": ("esc_core.v", ".ld_we       (ld_v & (ld_ctx == gk[1:0]) & ~start)",
                      ".ld_we       (ld_v & (cctx == gk[1:0]) & ~start)",
                      "deferred LDT write lands in the wrong context"),
    # ---- v1.1 GETI wait (sec. 7.3, D-20)
    "geti_no_wait": ("esc_core.v", "if (cs_hsmp) outcome = OC_W;", "if (1'b0) outcome = OC_W;",
                     "GETI no longer waits for in-flight SAMPLEs"),
    # ---- v1.1 capture unit (sec. 6.5)
    "cap_ignores_busy": ("esc_cap.v", "(n != 2'd0) & ~busy & ~start & ~stop",
                         "(n != 2'd0) & ~start & ~stop", "capture drains into a busy port"),
    "cap_no_overflow": ("esc_cap.v", "gen & (n_after == 2'd2)", "1'b0",
                        "staging overflow not detected"),
    "cap_ring_no_wrap": ("esc_cap.v", "(hit_lim & wrap) ? base : ptr_inc", "ptr_inc",
                         "ring mode does not wrap to base"),
    "cap_no_snapshot": ("esc_cap.v", "en <= 1'b1; snap <= 1'b1;", "en <= 1'b1; snap <= 1'b0;",
                        "arming does not take a snapshot"),
    "cap_mask_ignored": ("esc_cap.v", "((sync ^ prev) & mask) != 8'd0", "(sync ^ prev) != 8'd0",
                         "unwatched pins trigger records"),
    "cap_capt_no_override": ("esc_cap.v", "& ~set_full & ~capt_we &", "& ~set_full &",
                             "a record is generated at the CAPT edge"),
    # ---- v1.3 streaming capture (sec. 6.5, sec. 10)
    "st_no_hold": ("esc_cap.v", "wire       held     = stream & (ptr_nxt == rptr);",
                   "wire       held     = 1'b0;", "stream mode writes over unacknowledged records"),
    "st_hold_off_by_one": ("esc_cap.v", "wire       held     = stream & (ptr_nxt == rptr);",
                           "wire       held     = stream & (ptr == rptr);",
                           "stream mode holds one record late (overwrites the oldest)"),
    "st_no_ack": ("esc_cap.v", "if (ack) rptr <= ack_v;", "if (1'b0) rptr <= ack_v;",
                  "TAPE_ACK ignored"),
    "st_capt_keeps_rptr": ("esc_cap.v", "ptr <= capt_v[7:0]; rptr <= capt_v[7:0];",
                           "ptr <= capt_v[7:0];", "arming does not reset rptr to base"),
    "st_no_wrap": ("esc_cap.v", "wire       wrap     = ring | stream;", "wire       wrap     = ring;",
                   "stream mode stops at lim instead of wrapping"),
    "st_host_beats_drain": ("esc_core.v", "assign tape_busy  = tp_busy | lu_tb | cap_drain;",
                            "assign tape_busy  = tp_busy | lu_tb;",
                            "a capture drain does not claim the port from the host"),
    "st_rptr_hidden": ("esc_core.v", "5'd20: dbg_rdata = {24'd0, cap_rptr};",
                       "5'd20: dbg_rdata = 32'd0;", "sel 20 does not show rptr"),
    # ---- D-29 clock-gated banks: an enable that misses one condition holds a stale value
    "cg_r0_no_ldt": ("esc_ctx.v", "en_r0 = st | (live & ((rdw & (w_rd_idx == 2'd0)) | ldk0));",
                     "en_r0 = st | (live & ((rdw & (w_rd_idx == 2'd0))));",
                     "r0 bank not clocked by a deferred LDT write"),
    "cg_tmr_no_start": ("esc_ctx.v", "en_tmr = st | (live & cm_ok & w_tmr_we);",
                        "en_tmr = live & cm_ok & w_tmr_we;", "START does not clear tmr"),
    "cg_ish_no_geti": ("esc_ctx.v", ".en(st | (live & (sample_ret | (cm_ok & w_geti)))),",
                       ".en(st | (live & sample_ret)),", "GETI does not clear ish"),
    # ---- v1.4 D-40 latch banks: the shared write-data registers and each bank's input
    "lr_wr_no_tx": ("esc_core.v", "w_sh_we ? w_sh : w_tx_push ? opb : w_rd_data",
                    "w_sh_we ? w_sh : w_rd_data", "a TX mailbox word latches the register write data"),
    "lr_wr_no_sh": ("esc_core.v", "w_sh_we ? w_sh : w_tx_push ? opb : w_rd_data",
                    "w_tx_push ? opb : w_rd_data", "SH latches the register write data"),
    "lr_wr_no_start": ("esc_core.v", ".d(start ? 32'd0 : w_sh_we ? w_sh", ".d(w_sh_we ? w_sh",
                       "START latches stale write data into r0-r3 and SH"),
    "lr_tmr_no_start": ("esc_core.v", ".d(start ? 32'd0 : w_tmr), .q(q_tmr)", ".d(w_tmr), .q(q_tmr)",
                        "START latches a stale timer into tmr and lee"),
    "lr_ent_late": ("esc_core.v", ".en(commit_valid & w_push),", ".en(commit_valid & w_push & ~w_exp_admit),",
                    "an EXPECT's FIFO entry latches the previous entry"),
    "lr_ld_wrong_ctx": ("esc_core.v", "q_ldk  <= (ld_v & ~start) ? (4'd1 << ld_ctx) : 4'd0;",
                        "q_ldk  <= (ld_v & ~start) ? (4'd1 << cctx) : 4'd0;",
                        "a deferred LDT write latches the commit data in its context"),
    "lr_dq_swapped": ("esc_ctx.v", "wire [31:0] dq_r = q_ld_we ? q_ld : q_wr;",
                      "wire [31:0] dq_r = q_ld_we ? q_wr : q_ld;", "register latches take the other port's data"),
    "lr_lee_from_wr": ("esc_ctx.v", ".d(d_lee), .dq(q_tmr), .q(o_lee)", ".d(d_lee), .dq(q_wr), .q(o_lee)",
                       "lee latches the register write data"),
    "lr_rx_from_wr": ("esc_ctx.v", ".d(h_rx_data), .dq(h_rx_data), .q(rx1)", ".d(h_rx_data), .dq(q_wr), .q(rx1)",
                      "an RX mailbox slot latches the commit data instead of the host's word"),
    "lr_w0_from_rdata": ("esc_line.v", ".dq({buf_q[31:24], buf_q[20:0]}), .q(w0)",
                         ".dq({rdata[31:24], rdata[20:0]}), .q(w0)", "the line unit's W0 latches the tape data"),
    # ---- v1.4 two-slot mailbox rings
    "mb_rx_no_advance": ("esc_ctx.v", "rx_rp  <= rx_rp ^ rx_popped;", "rx_rp  <= rx_rp;",
                         "RX ring head does not advance on POP"),
    "mb_tx_same_slot": ("esc_ctx.v", "wire       tx_wi     = tx_rp ^ tx_popped ^ tx_cnt_p[0];",
                        "wire       tx_wi     = tx_rp ^ tx_cnt_p[0];",
                        "a TX push in the cycle of a host pop overwrites the queued word"),
    "mb_rx_head_fixed": ("esc_ctx.v", "assign o_rx_head  = rx_rp ? rx1 : rx0;", "assign o_rx_head  = rx0;",
                         "POP always reads slot 0"),
    # ---- v1.4 D-41 debug reads through the commit stage
    "dbg_any_slot": ("esc_core.v", "wire        dcs_en = (cctx == sk) &", "wire        dcs_en = 1'b1 &",
                     "selects 0-8 during a run show whichever context commits"),
    "dbg_no_retag": ("esc_core.v", "(run | ~dcs_ok | (dcs_tag != {dbg_sel[3:0], sk}))", "(run | ~dcs_ok)",
                     "a new select while stopped returns the previous field"),
    "dbg_sel7_swapped": ("esc_core.v", "4'd7:    dcs = {8'd0, cs_lr, cs_pc,", "4'd7:    dcs = {8'd0, cs_pc, cs_lr,",
                         "select 7 has LR and PC swapped"),
    "cq_start_off": ("esc_core.v", "if (start)            cq <= START_NOW[1:0] + 2'd1;",
                     "if (start)            cq <= START_NOW[1:0];", "the commit slots start one context off"),
    "cq_stop_keeps": ("esc_core.v", "else                  cq <= dbg_sel_ctx;", "else                  cq <= cq;",
                      "while stopped selects 0-8 read a fixed context"),
    # ---- v1.0 core (regression of the original campaign)
    "admit_past_off_by_one": ("esc_core.v", "ahd_now  <= (nd_now != 32'd0) & ~nd_now[31];",
                              "ahd_now  <= ~nd_now[31];", "deadline == commit tick admitted"),
    "retire_not_suppressed": ("esc_ctx.v", "wire       ret_ok     = due & ~kill;",
                              "wire       ret_ok     = due;", "retirement at a fault edge"),
    # ---- v1.5 tests decided one cycle early
    "due_one_late": ("esc_ctx.v", "wire [31:0] n_diff  = now_n - n_dl;", "wire [31:0] n_diff  = now - n_dl;",
                     "every event retires one tick after its deadline"),
    "due_wrong_head": ("esc_ctx.v", "case (head + {1'b0, due})\n      2'd0:    n_dl",
                       "case (head)\n      2'd0:    n_dl",
                       "the entry behind a retiring head is never tested"),
    "due_pushed_lead": ("esc_ctx.v", "(push & (w_lead16 == 16'd1))", "(push & (w_lead16 == 16'd0))",
                        "an event pushed into an empty FIFO with lead 1 never retires"),
    "pre_wrong_ctx": ("esc_core.v", "wire [1:0]  nq      = cq + 2'd1;", "wire [1:0]  nq      = cq;",
                      "admission tests computed for the context committing now, not next"),
    "pre_lead_now": ("esc_core.v", "wire [31:0] nd_now  = n_tmr - now_n;", "wire [31:0] nd_now  = n_tmr - now;",
                     "admission tests one tick stale"),
    # v1.5 boundaries: the half-range horizon, the 2^32 wrap, lead saturation, equal
    # deadlines, the context whose state is read, a FIFO emptied or killed at a retirement.
    "rch_half_point": ("esc_core.v", "rch_tmr  <= (nd_now == 32'd0) | (nd_now[31] & (nd_now[30:0] != 31'd0));",
        "rch_tmr  <= (nd_now == 32'd0) | nd_now[31];",
        "a timer exactly half the range ahead counts as reached"),
    "ahd_range_edge": ("esc_core.v", "ahd_now  <= (nd_now != 32'd0) & ~nd_now[31];",
        "ahd_now  <= (nd_now != 32'd0) & (nd_now < 32'h7FFFFFFF);",
        "a deadline 2^31 - 1 ticks ahead refused"),
    "ahd_unsigned": ("esc_core.v", "ahd_now  <= (nd_now != 32'd0) & ~nd_now[31];",
        "ahd_now  <= (n_tmr > now_n);",
        "ahead by unsigned compare: wrong only across the 2^32 wrap of now"),
    "rch_unsigned": ("esc_core.v", "rch_tmr  <= (nd_now == 32'd0) | (nd_now[31] & (nd_now[30:0] != 31'd0));",
        "rch_tmr  <= (n_tmr <= now_n);",
        "reached by unsigned compare: wrong only across the 2^32 wrap of now"),
    "lead_sat_17": ("esc_core.v", "lead16   <= (nd_now[31:16] != 16'd0) ? 16'hFFFF : nd_now[15:0];",
        "lead16   <= (nd_now[31:17] != 15'd0) ? 16'hFFFF : nd_now[15:0];",
        "a lead of 65,536 to 131,071 wraps instead of saturating"),
    "lead_sat_fffe": ("esc_core.v", "lead16   <= (nd_now[31:16] != 16'd0) ? 16'hFFFF : nd_now[15:0];",
        "lead16   <= (nd_now[31:16] != 16'd0) ? 16'hFFFE : nd_now[15:0];",
        "a saturated lead reads 0xFFFE"),
    "admit_tail_equal": ("esc_core.v", "ahd_tail <= (nd_tail != 32'd0) & ~nd_tail[31];",
        "ahd_tail <= ~nd_tail[31];",
        "a deadline equal to the FIFO tail admitted"),
    "expect_lee_equal": ("esc_core.v", "ahd_lee  <= (nd_lee != 32'd0) & ~nd_lee[31];",
        "ahd_lee  <= ~nd_lee[31];",
        "an EXPECT deadline equal to the open window's end admitted"),
    "pre_tail_ctx": ("esc_core.v", "wire [31:0] nd_tail = n_tmr - c_tail[32*nq +: 32];",
        "wire [31:0] nd_tail = n_tmr - c_tail[32*cq +: 32];",
        "admission compares with the FIFO tail of the context committing now"),
    "pre_lee_ctx": ("esc_core.v", "wire [31:0] nd_lee  = n_tmr - c_lee[32*nq +: 32];",
        "wire [31:0] nd_lee  = n_tmr - c_lee[32*cq +: 32];",
        "EXPECT window end of the context committing now"),
    "due_keep_stale": ("esc_ctx.v", "wire        n_keep  = (o_cnt_after != 3'd0);",
        "wire        n_keep  = (o_cnt != 3'd0);",
        "the next head test ignores a retirement that empties the FIFO"),
    "due_empty_no_push": ("esc_ctx.v", "(push & (w_lead16 == 16'd1))",
        "(w_lead16 == 16'd1)",
        "an empty FIFO is tested against a lead nobody pushed"),
    "kill_keeps_due": ("esc_ctx.v", "        o_cnt <= 3'd0;\n        due_q <= 1'b0; late_q <= 1'b0;",
        "        o_cnt <= 3'd0;\n        late_q <= 1'b0;",
        "a stop or fault leaves the retire test set"),
}


# Mutants no test can kill and no equivalence proof here decides, each with the argument
# that it changes no behaviour. They are reported as EXCLUDED, never counted as killed.
P2 = ("acts only when a FIFO head is past its deadline (LATE_HEAD), which P2 proves never "
      "happens in any reachable state (docs/04-VERIFICATION.md), so the mutated logic is dead")
EXCLUDED = {
    "esc_core_rel_b808cdef": P2,       # esc_core.v:491 cs_late suppression
    "esc_core_cond_6ee4183d": P2,      # esc_core.v:666 late_ctx priority
    "esc_core_const_f8680ed1": P2,     # esc_core.v:776 first-fault record of LATE_HEAD
    "esc_ctx_stuck_d10249cd": P2,      # esc_ctx.v:328 late_q never set (it is never set anyway)
    "escapement_sram_const_6c8bc178": "A_DLY is the SRAM macro's delay-select tie (must be 1); it has no "
                                      "function in any model, and the macro model flags any other value",
    "escapement_sram_const_8907efe9": "A_BIST_REN is read only in the macro's BIST mode, which A_BIST_EN = 0 "
                                      "never enables",
    # reset and idle values of state that is always loaded before anything reads it
    "esc_core_const_ed962f97": "s2 reloads from s1 at every edge, so its reset value lives one cycle; nothing "
                               "reads the synchronised pins before a START, which takes a whole SPI frame",
    "esc_ctx_const_90bb8e65": "the checker's active flag is recomputed at every edge from eval_en, which is 0 "
                              "while not running: it clears at the first edge after reset",
    "esc_cap_const_9f9b6767": "snap acts only while en is set, and every CAPT that sets en also sets snap",
    "esc_cap_const_27c100a8": "snap acts only while en is set, and every CAPT that sets en also sets snap",
    "esc_cap_const_bda4e745": "snap acts only while en is set, and every CAPT that sets en also sets snap",
    "esc_cap_const_e24728af": "prev is read only to detect a change while en is set, and arming takes a "
                              "snapshot first, which loads prev",
    "esc_cap_const_f94d1ea0": "st1 is read only when two records are staged, and a record is written to it "
                              "before n counts it",
    "escapement_host_const_6c0e07d1": "mem_wdata reaches a memory only with mem_we, and every write loads it "
                                      "first; with mem_we low the SRAM ignores its data pins",
    "escapement_host_const_4f254f37": "READ_DBG sets dbg_ctx in its first payload byte, before the word is "
                                      "read in its second",
    "escapement_host_const_ba0e3c3f": "an extra tape read after reset whose data no command uses: READ_TAPE "
                                      "requests a fetch of its own for every word",
    "escapement_host_stuck_5c77ffc7": "cmd is C_IGNORE at every command byte (deselecting sets it), so "
                                      "holding it equals setting it",
    "escapement_host_const_28d0a987": "MBX_PUSH pushes at byte 5; a byte 6 would only shift word again, and "
                                      "every command that sends word loads it first",
    "escapement_host_stuck_44a212d9": "mctx is read only by MBX_PUSH, which sets it in its own first payload "
                                      "byte",
    # v1.4
    "esc_core_not_6edf7a77": "dcs_ok only stops dbg_cs reloading while stopped, when the registers hold still: "
                             "with it never set, dbg_cs reloads the value it holds (power, not function)",
    # an extra tape read: the SRAM holds its read data until the next read, and every consumer
    # takes it only in the cycle after a read of its own (an LDT's result through ld_v, the line
    # unit's words through ld and cfg_done); a write wins over a read in the same cycle
    "esc_core_bit_7f724999": "an extra tape read in the commit cycle of a SEND or LINE that reads nothing "
                             "(OFF, RECV, an empty CRC job): read data is taken only after a read of its own",
    "esc_core_bit_e2dd9694": "an extra tape read in STT cycles (the write wins) and in cycles that decode an "
                             "LDT without committing it: read data is taken only after a read of its own",
    "esc_core_bit_76bf3f7c": "an extra tape read in SEND and LINE commit cycles that do not complete (WAIT): read "
                             "data is taken only after a read of its own",
    "esc_core_const_3b43f805": "ld_ctx is read only with ld_v (the LDT write-back and q_ldk), and every cycle that "
                               "sets ld_v loads ld_ctx: its reset value is never read",
    "esc_core_const_ab23e9a0": "with dcs_ok set at reset the first read of select 0 of context 0 returns the "
                               "cached 0 (dbg_cs and its tag reset to 0) instead of reloading r0, which is 0 from "
                               "reset until the first START; a run clears dcs_ok",
    "esc_line_const_7268434e": "buf_q is loaded before every read: from the tape when a CONFIG, a CRC job or a "
                               "packet with data starts, and cleared when a reception starts",
    "esc_line_const_19efdba1": "req is read only in TX (tx_rd) and RX (rx_wr); TX is entered only through PEND, "
                               "whose SEND clears req, and a reception start clears it: START's value is unread",
    "esc_line_const_2b577159": "req after OFF: read only in TX and RX, both entered through a SEND or a reception "
                               "start, which clear it",
    "esc_core_const_e2fc5716": "cq reloads from dbg_sel_ctx at every edge while stopped, so its reset value lives "
                               "one cycle; a debug cache load in it loads only the selected context (cctx == sk), "
                               "whose fields are all 0 after reset, as the next cycle's load is",
    "esc_core_rel_ff822406": "a line unit CONFIG fault is at E_(c+9), one cycle after an owner commit slot: the "
                             "committing context is never the owner then, so < and <= never differ",
    "esc_core_stuck_6acae8c8": "dcs_ok never set: dbg_cs reloads at every stopped cycle instead of on a new "
                               "select, loading the value it holds (power, not function)",
    "esc_line_const_dee5a8c3": "req at reset: read only in TX and RX, entered through a SEND or a reception start, "
                               "which clear it",
    "esc_line_const_1f89dc5f": "req after a packet ends: read only in TX and RX, entered through a SEND or a "
                               "reception start, which clear it",
    "esc_line_const_2fbce3b2": "seg is read only in TX (eseg), and the SEND that enters PEND sets it to 0",
    "esc_line_const_4bca0710": "mon is read only in TX (tx_mon), and the SEND that enters PEND clears it",
    "esc_line_const_c59b623f": "mon left set after a monitor sample: el reaches the sample point once per bit, and "
                               "the next boundary sets mon again (a line bit) or clears it (the end); EOP symbols, "
                               "which keep it, exist only with diff, where the monitor is off",
    "esc_line_const_575cb32a": "stor is read only in RX mode (through rx_smp), and every entry to RX sets it to 1, "
                               "so its reset value is never read",
    "esc_line_const_77af8c64": "rb is read only while the stuffing run rn is nonzero (push(), and sb through due, "
                               "which needs rn >= N > 0); SEND sets rn to 0, and the first bit loads rb",
    "esc_line_const_437dd9b7": "rb is read only while the stuffing run rn is nonzero (push(), and sb through due, "
                               "which needs rn >= N > 0); the start of a reception sets rn to 0, and the first "
                               "bit loads rb",
    # v1.6 core and context (used only where the equivalence proof is undecided)
    "esc_core_const_f4a3ecbd": "z_src 5 is no case of the Z mux: as with 4, w_z keeps its preset and w_z_we "
                               "(z_src != 0) is set",
    "esc_core_const_2755ed32": "z_src 5 is no case of the Z mux: as with 4, w_z keeps its preset and w_z_we "
                               "(z_src != 0) is set",
    "esc_core_bit_33d58375": "an extra tape read in the commit cycle of a SEND that reads no stream word: read "
                             "data is taken only after a read of its own (lu_rd_c acts only with lu_cmc)",
    "esc_core_bit_ef1d2c41": "an extra tape read in the commit cycle of a SEND or LINE that reads nothing: read "
                             "data is taken only after a read of its own (lu_rd_c acts only with lu_cmc)",
    "esc_core_const_936aafc6": "the decode registers reload at every edge, so their reset values live one cycle; "
                               "they are read only through commit_valid (run, p3v), clear until a START, which "
                               "takes a whole SPI frame",
    "esc_core_const_26218697": "the decode registers reload at every edge, so their reset values live one cycle; "
                               "they are read only through commit_valid (run, p3v), clear until a START, which "
                               "takes a whole SPI frame",
    "esc_core_const_91f32f2c": "the operand registers reload at every edge, so their reset values live one cycle; "
                               "they are read only through commit_valid (run, p3v), clear until a START, which "
                               "takes a whole SPI frame",
    "esc_core_const_5ca4b979": "the operand registers reload at every edge, so their reset values live one cycle; "
                               "they are read only through commit_valid (run, p3v), clear until a START, which "
                               "takes a whole SPI frame",
    "esc_ctx_const_d6ce0679": "h_q is read only while the FIFO is not empty (D3), and the push into an empty FIFO "
                              "loads it (h_n takes the pushed entry); reset leaves the FIFO empty",
    "esc_ctx_const_cff87bc8": "h_q is read only while the FIFO is not empty (D3), and the push into an empty FIFO "
                              "loads it (h_n takes the pushed entry); START empties the FIFO",
    # v1.6 reset values in the line unit (the clock gate and START leave these): no op reaches
    # the unit before a run, and START clears ld, ldn, req, reqn, fin, crc, job, cause and count
    "esc_line_const_a2b612e3": "ld set at reset opens the unit's clock for one idle edge, which clears it and "
                               "rewrites es_q, pb_q, sl_q and dq_*: those are read only in TX, and the SEND edge "
                               "that leads there rewrites them",
    "esc_line_const_f6e9ae9b": "crc's reset value lives until START clears it; before a run only status shows crc, as "
                               "crc[31:16], which 32'd1 does not change",
    "esc_line_const_056a859a": "lv is read only in TX (levels, monitor) and RX (NRZI reference), and both entries set "
                               "it: the SEND that enters PEND and the start of a reception",
    "esc_line_const_259e56f7": "pos is read only in PEND and TX (bit index, segment ends, the TX read address), "
                               "directly or through es_q/pb_q/sl_q, and the SEND that enters PEND sets it to 0",
    "esc_line_const_f2ede909": "cel is read only in TX (lastc, the next cell's levels, dq_val, which drives only "
                               "after a TX edge set dq_go), and the SEND that enters PEND sets it to 0",
    "esc_line_const_75ae91da": "sk is read only in TX (the next cell's levels, dq_val, which drives only after a TX "
                               "edge set dq_go), and the PEND to TX edge (tx_start, a boundary) loads it from k_s",
    "esc_line_const_4a93e9bf": "reqn acts only with tx_rd (ldn), which needs TX and req, and the SEND that enters "
                               "PEND clears it, as START does",
    "esc_line_const_fd86d304": "abt is read only at TX boundaries and in TX's dq_val (tx_start, tx_bnd, the monitor), "
                               "and the SEND that enters PEND clears it",
    "esc_line_const_7a29a77f": "pbx_q is read only through v0, the pins at tx_start (PEND), and the SEND that enters "
                               "PEND loads it (pbx_v)",
    "esc_line_const_4da28994": "es0_q is read only through b0, k0 and v0, the pins at tx_start (PEND), and the SEND "
                               "that enters PEND loads it (es0_v)",
    "esc_line_const_4cd3abe2": "ld set by START: START leaves the unit IDLE, where ld acts only on pb_q (through sbit) "
                               "and the clock enable (an edge that rewrites es_q, pb_q, sl_q, dq_*); the CONFIG words, "
                               "buf_q and nxt_q load only in CFG, CRCM, PEND, TX; pb_q and the rest are read only in "
                               "TX, and the edge that leaves IDLE rewrites ld, as in a2b612e3",
    "esc_line_const_0aaa2d36": "reqn set by START: reqn acts only with tx_rd (ldn), which needs TX, and the SEND "
                               "that enters PEND (the only way to TX) clears it, as in 4a93e9bf",
    "esc_line_const_1e1a90fe": "crc set to 1 by START: status shows crc[31:16], 0 either way; the other bits are read "
                               "only in CRCM (crc_b), TX (crcx, crc_nx) and RX (crc_nx), and every entry loads crc "
                               "first (op_crc: crc_start or crc_w, which in IDLE comes from crc_start; tx_start; "
                               "arm_go)",
    "esc_line_const_6d12954f": "cel set to 1 by the SEND: cel is read only in TX (lastc, and through lc_n and sym_n "
                               "only dq_*, which drive only after a TX edge set dq_go), and the PEND to TX edge "
                               "(tx_start, a boundary) sets it to 0; only the formal PEND lemma (cel == 0) sees it",
    "esc_line_stuck_b6827db4": "mode kept at rx_none (a RECV with cells > 1): a RECV commits only while the unit is "
                               "not engaged (LI, proved in esc_core), so in IDLE or ARM; ARM is entered only by a "
                               "RECV with cells = 0, and cells (W3, with ext in W2) changes only in CFG, which "
                               "leaves ARM, so at rx_none mode is already IDLE",
    "esc_line_const_b48f6a1b": "reqn set at the end of a packet (t_end, mode IDLE): as 0aaa2d36 (only tx_rd, in TX, "
                               "reads it, and the SEND that enters PEND clears it)",
    "esc_line_const_51138270": "mon set at the end of a packet (t_end, mode IDLE): mon is read only in TX (tx_mon), "
                               "and the SEND that enters PEND clears it",
    "esc_line_const_18738a78": "xf's low half 1 instead of 0: xf feeds only crc_w, which only writes crc (a CRC job "
                               "with the wire flag); crc[15:0] is never seen: status shows crc[31:16], and CRCM, TX "
                               "and RX load crc on entry (as 1e1a90fe)",
    "esc_line_bit_620516ec": "tln = ~abt_n & due & (es_c <= 3): tln only selects kn = tln ? 0 : k_s, and k_s is 0 "
                             "whenever t_line holds. dq_* drive only after a TX cycle set dq_go, so they come from "
                             "the cycle before a boundary, where es_q == es_c (L2) and tx_start is 0 (PEND sets no "
                             "dq_go), and abt_n implies abt. The mutated tln differs only with tln = 1 (a line or "
                             "stuff bit next, no abort), where t_line = 1, k_s = 0 and kn = 0 either way",
    "esc_line_bit_eaccbc7a": "send_rd set for every ext SEND, also one with no stream bits: the core then reads "
                             "the tape in that SEND's commit cycle, which is busy for the SEND anyway (lu_tb), and the "
                             "read data is taken only after a read of its own (as esc_core_bit_33d58375); in the unit "
                             "ld = 1 in PEND loads buf_q, which a packet without stream bits never reads, and every "
                             "next job reloads it (RECV clears it, CONFIG, CRC and SEND load it from their own reads)",
    "esc_line_not_53d6b93e": "tln = abt_n & (...): as 620516ec, tln only selects kn = tln ? 0 : k_s; kn reaches "
                             "dq_* only at a symbol boundary with ten = 0, so abt_n = 0, where the mutated tln is 0 "
                             "and the original tln equals t_line (es_q == es_c, L2), and then k_s = 0: kn = 0 "
                             "either way",
    "esc_line_const_99e92063": "seg = eseg + 2 (6) after the last end-of-packet symbol: seg is read only through "
                               "first_seg(seg, sen), which gives 5 (the end) for any seg above 4, as for 5",
    "esc_line_const_bc3559fc": "pos = 1 after the last end-of-packet symbol: the next boundary ends the packet (or "
                               "inserts a stuff bit, which reads no pos), nothing reads pos in IDLE, and the SEND "
                               "that enters PEND sets it to 0",
    "esc_line_const_dbb16f8e": "crc set to 1 by a CONFIG without init: as 1e1a90fe (status shows crc[31:16], 0 either "
                               "way; CRCM, TX and RX load crc on entry)",
    # the kill branch leaves the unit IDLE, as START does, keeping the rest of its state
    "esc_line_const_76aabe1d": "reqn set by a kill: as 0aaa2d36 (only tx_rd reads it, and the SEND that enters "
                               "PEND clears it)",
    "esc_line_const_f61f5137": "req set by a kill: req is read only in TX (tx_rd) and RX (rx_wr); the SEND that "
                               "enters PEND (the only way to TX) and arm_go (the only way to RX) clear it, and "
                               "ARM, CFG and CRCM do not read it",
    "esc_line_const_1f631023": "ld set by a kill: as 4cd3abe2 (IDLE, one more clock edge that rewrites only es_q, "
                               "pb_q, sl_q, dq_* and ld, ldn; dq_* drive only in TX after dq_go, the others are read "
                               "only at TX boundaries, and the edge that leaves IDLE rewrites ld)",
    # v1.6 next-word buffer: nxt_q is read only at bit 31 of an ext word with bits to come
    # (buf_q <= nxt_q), and such a word had its prefetch at bit 15 (pos + 17 < nb), whose read
    # loads nxt_q within 5 cycles, at least 32 before bit 31 (P >= 2): any other load of
    # nxt_q is overwritten before it is read. (The write enable's other mutants die in the
    # line suite since tb_line.v makes stray tape reads, v1.6.)
    "esc_line_const_b76e9314": "nxt_q's reset value: overwritten by the bit-15 prefetch before bit 31 reads it",
    "esc_line_rel_2d72ab9c": "mode != PEND also loads nxt_q after a prefetch read whose edge ended the packet "
                             "(OFF, CONFIG, RECV, CRC): the next read needs a new packet's own prefetch",
    "esc_line_bit_9ab735b4": "ld | ldn also loads nxt_q with a packet's first word, with every word of a packet "
                             "without ext (which never reads it), and after a prefetch read whose edge ended the "
                             "packet: bit 31 reads it only after the bit-15 prefetch",
    # v1.5 (undecided by the 30-minute equivalence proof since the head tests became registers)
    "esc_ctx_const_8ab6a6d1": "head's reset value lives until START sets it: nothing is pushed before a run, and "
                              "with the FIFO empty every read of a slot is gated off by the count",
    "esc_ctx_const_3be0d007": "the FIFO is a ring addressed only relative to head (wr_idx, tail_idx, GETI's rel, "
                              "the next head's tests), START empties it, and every read of a slot is gated by the "
                              "count (n_keep, rel < cnt, due, the core's tail tests under cs_cnta != 0): the slot "
                              "a run starts from changes no output",
}


@dataclass
class Mutant:
    id: str
    file: str
    old: str
    new: str
    desc: str
    origin: str               # curated or generated
    op: str = "curated"
    line: int | None = None   # generated mutants replace one whole line


def all_mutants(rate: float = generate.RATE) -> list[Mutant]:
    ms = [Mutant(n, f, o, w, d, "curated") for n, (f, o, w, d) in MUTANTS.items()]
    ms += [Mutant(g.id, g.file, g.old, g.new, g.desc, "generated", g.op, g.line)
           for g in generate.generate(rate)]
    return ms


# suite -> (directory, extra make arguments, time limit in seconds)
SUITES = {
    "cosim": ("test/cosim", [], 1800),
    "unit": ("test/rtl_unit", [], 900),
    "host": ("test/host", [], 900),
    "chip": ("test", [], 2400),
    "pins": ("test", ["COCOTB_TEST_MODULES=test_firmware_pins"], 2400),
    "line": ("test/line", [], 900),          # run.py, not make (v1.4)
}
PIPELINE = {
    "esc_line.v": ["line", "cosim", "unit", "chip", "pins"],
    "escapement_host.v": ["host", "chip", "pins"],
    "escapement_sram.v": ["host", "cosim", "chip"],
    "project.v": ["chip", "pins"],
}
CORE_PIPELINE = ["cosim", "unit", "chip", "pins"]
# A suite that runs out of time on a mutant counts as a kill, so its limit must be far
# above what the suite takes on the same machine under the same load: each limit is the
# larger of the one above and TIMEOUT_FACTOR times the suite's time in the baseline run.
# (v1.6: with the fixed limits alone, the v1.5 CI runs scored about 20 timeouts as kills,
# and several of those mutants were proved equivalent in the other run: a busy runner,
# not a detection.)
TIMEOUT_FACTOR = 3
BASE_SECS: dict[str, float] = {}


def apply(m: Mutant, src: Path) -> str | None:
    f = src / m.file
    text = f.read_text()
    if m.line is not None:
        lines = text.split("\n")
        if lines[m.line - 1] != m.old:
            return "line moved"
        lines[m.line - 1] = m.new
        f.write_text("\n".join(lines))
        return None
    if text.count(m.old) != 1:
        return f"{text.count(m.old)} matches"
    f.write_text(text.replace(m.old, m.new))
    return None


def run_suite(suite: str, src: Path, work: Path, seeds: int, limit: float | None = None) -> str:
    """'pass', 'fail', 'timeout' or 'compile-error' for one suite on the mutated tree."""
    d, extra, fixed = SUITES[suite]
    limit = limit or max(fixed, TIMEOUT_FACTOR * BASE_SECS.get(suite, 0))
    cwd = REPO / d
    if suite == "line":
        # as `make line-test` runs it: 8 seeds and the profiled runs (v1.6: it ran 4 seeds and no
        # profiled run, which let esc_line_const_68ee4056 and esc_line_stuck_8d8db8fe through)
        cmd = [sys.executable, str(cwd / "run.py"), "--src", str(src), "--seeds", "8", "--jobs", "1"]
        try:
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=limit)
        except subprocess.TimeoutExpired:
            return "timeout"
        out = r.stdout + r.stderr
        (work / f"{suite}.log").write_text(out)
        if ": PASS" in out:
            return "pass"
        return "fail" if " edges" in out or "errors" in out else "compile-error"
    env = dict(os.environ, PWD=str(cwd), COCOTB_PLUSARGS="+nodump",
               COCOTB_RESULTS_FILE=str(work / f"results_{suite}.xml"),
               COSIM_SEEDS=str(seeds), COSIM_OUT=str(work / "failures"))
    cmd = ["make", "-s", "-B", f"SRC_DIR={src}", f"SIM_BUILD={work / ('build_' + suite)}", *extra]
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env, timeout=limit)
    except subprocess.TimeoutExpired:
        subprocess.run(["pkill", "-f", str(work)], capture_output=True)
        return "timeout"
    out = r.stdout + r.stderr
    (work / f"{suite}.log").write_text(out)
    if "TESTS=" not in out:
        # no summary: either the mutant did not build (a broken mutant, not a kill) or the
        # simulation crashed or aborted after it started (a kill)
        started = "Running on" in out or "running " in out.lower() and "test_" in out
        return "fail" if started else "compile-error"
    return "pass" if "FAIL=0" in out else "fail"


EQUIV_LIMIT = int(os.environ.get("EQUIV_LIMIT", "1800"))   # seconds per equivalence proof


def _lift_memory_cap():
    """The proof needs more memory than one simulation: raise a soft address-space cap to
    the hard limit (run the campaign under `ulimit -S -v`, not `ulimit -v`)."""
    try:
        import resource
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        resource.setrlimit(resource.RLIMIT_AS, (hard, hard))
    except (ImportError, ValueError, OSError):
        pass


def equivalent(src: Path, work: Path) -> tuple[bool, str]:
    """(proved, note): the mutant changes no chip output, or why that was not shown."""
    try:
        r = subprocess.run(["bash", str(REPO / "formal/equiv.sh"), str(src), str(work / "equiv")],
                           capture_output=True, text=True, timeout=EQUIV_LIMIT, preexec_fn=_lift_memory_cap)
    except subprocess.TimeoutExpired:
        subprocess.run(["pkill", "-f", str(work / "equiv")], capture_output=True)
        return False, f"equivalence: undecided in {EQUIV_LIMIT // 60} min"
    if r.returncode == 0 and "is sequentially equivalent to the RTL" in r.stdout:
        return True, "equivalence: proved"
    log = work / "equiv" / "dprove.log"
    text = log.read_text(errors="replace") if log.exists() else ""
    if "NOT EQUIVALENT" in text.upper() or "not equivalent" in text:
        return False, "equivalence: outputs differ (a test hole)"
    if text:
        return False, "equivalence: undecided"
    return False, f"equivalence: check did not run (exit {r.returncode})"


def kill_evidence(suite: str, work: Path) -> str:
    """What a failing suite reported, so a kill can be audited: the failing tests and the
    first assertion (cocotb), or the failing seeds and first mismatch (line lockstep). A kill
    with no assertion and no mismatch (a crash) says so."""
    log = work / f"{suite}.log"
    text = log.read_text(errors="replace") if log.exists() else ""
    if suite == "line":
        keep = [ln.strip() for ln in text.splitlines()
                if re.match(r"seed \d+: \d+ errors", ln.strip()) or re.match(r"line \d+ t=", ln.strip())]
    else:
        fails = [ln.split()[1] for ln in text.splitlines()
                 if ln.strip().startswith("**") and " FAIL " in ln and "TESTS=" not in ln]
        first = next((ln.strip() for ln in text.splitlines()
                      if "AssertionError" in ln or "assert " in ln.lower() and "Error" in ln), "")
        keep = ([f"failed: {', '.join(fails)}"] if fails else []) + ([first] if first else [])
    return " | ".join(keep)[:400] or "no assertion or mismatch in the log (a crash or abort)"


# Canaries (v1.6). Before any mutant is scored, each suite must catch a fault it is known
# to see, and the equivalence check must prove a rewrite that changes nothing and refute one
# that changes an output. The baseline shows a suite can pass; a canary shows it can fail
# for the right reason, and the proof can say both yes and no. (2026-11: the proof's tools
# were off PATH for a whole local run; every survivor read "check did not run" and nothing
# else complained.)
CANARY = {   # suite -> (file, old, new): a fault that suite must catch
    "line": ("esc_line.v", "drv_m = claimed; drv_val = dq_val;", "drv_m = claimed; drv_val = ~dq_val;"),
    "cosim": ("esc_core.v", "w_rd_data = {16'd0, f_imm16};", "w_rd_data = {16'd0, ~f_imm16};"),
    "unit": ("esc_core.v", "w_rd_data = {16'd0, f_imm16};", "w_rd_data = {16'd0, ~f_imm16};"),
    "host": ("escapement_host.v", "localparam [3:0] VERSION = 4'h9;", "localparam [3:0] VERSION = 4'h8;"),
    "chip": ("escapement_host.v", "localparam [3:0] VERSION = 4'h9;", "localparam [3:0] VERSION = 4'h8;"),
    "pins": ("project.v", "assign uo_out = {ob_out, running, |irq_pending | fault_valid, miso};",
             "assign uo_out = {ob_out, running, |irq_pending | fault_valid, ~miso};"),
}
CANARY_SAME = ("esc_line.v", "else if (!lu_hold && nx_we)  nxt_q <= rdata;",
               "else if (nx_we && !lu_hold)  nxt_q <= rdata;")        # must be proved equivalent
CANARY_DIFF = CANARY["pins"]                                           # must be refuted


def canaries(suites: set[str], seeds: int, root: Path, jobs: int, do_equiv: bool) -> bool:
    def one(name, f, old, new, check):
        work = root / f"canary_{name}"
        src = work / "src"
        shutil.copytree(REPO / "src", src)
        bad = apply(Mutant(name, f, old, new, name, "curated"), src)
        if bad:
            return name, False, f"the canary did not apply ({bad})"
        return (name,) + check(src, work)

    def caught(s):
        return lambda src, work: ((r := run_suite(s, src, work, seeds)) == "fail",
                                  f"{r}: {kill_evidence(s, work) if r == 'fail' else ''}")
    tasks = [(f"suite_{s}",) + CANARY[s] + (caught(s),) for s in sorted(suites)]
    if do_equiv:
        tasks.append(("equiv_same",) + CANARY_SAME + (lambda src, work: equivalent(src, work),))
        tasks.append(("equiv_diff",) + CANARY_DIFF +
                     (lambda src, work: ((n := equivalent(src, work)[1]).endswith("(a test hole)"), n),))
    with ThreadPoolExecutor(max(1, min(jobs, len(tasks)))) as pool:
        got = list(pool.map(lambda t: one(*t), tasks))
    for name, ok, note in got:
        print(f"canary {name:12s} {'ok' if ok else 'FAILED'}  {note}", flush=True)
    return all(ok for _, ok, _ in got)


def reconcile(ids: list[str], rows: list[dict]) -> list[str]:
    """Problems that would make a campaign's counts wrong: a mutant with no final result or
    with two, a result for a mutant not in the list, a kill with no suite, an exclusion with
    no written argument, a survivor."""
    probs, seen = [], Counter(r["id"] for r in rows)
    probs += [f"{i}: {n} results" for i, n in seen.items() if n > 1]
    probs += [f"{i}: no result" for i in ids if i not in seen]
    probs += [f"{i}: not in the mutant list" for i in seen if i not in set(ids)]
    for r in rows:
        if r["result"] == "KILLED" and not r.get("killed_by"):
            probs.append(f"{r['id']}: killed by no suite")
        elif r["result"] == "EXCLUDED" and r["id"] not in EXCLUDED:
            probs.append(f"{r['id']}: excluded with no argument in EXCLUDED")
        elif r["result"] not in ("KILLED", "EQUIVALENT", "EXCLUDED"):
            probs.append(f"{r['id']}: {r['result']} {r.get('note') or ''}")
    return probs


# Seconds each mutant took in CI (refreshed with --costs-from), used only to deal the mutants
# to shards. A mutant no suite kills runs every suite and the proof, ~1.5 h against minutes
# for most kills, so dealing every N-th mutant left v1.6's sixteen shards 96 to 389 minutes
# apart (modelled on CI runs 41296786251 and 41825920334), past the job limit at the top.
COSTS = Path(__file__).with_name("costs.json")


def shards(ms: list[Mutant], n: int) -> list[list[Mutant]]:
    """Deal the mutants to n shards, longest first to the least-loaded shard. A mutant with
    no recorded time counts as the mean of its file's. Deterministic, so every shard deals
    the same way; the summary job checks that each mutant has exactly one result."""
    cost = json.loads(COSTS.read_text()) if COSTS.exists() else {}
    byfile: dict[str, list[int]] = {}
    for m in ms:
        if m.id in cost:
            byfile.setdefault(m.file, []).append(cost[m.id])
    known = [c for cs in byfile.values() for c in cs]
    est = {m.id: cost.get(m.id) or (sum(byfile[m.file]) // len(byfile[m.file]) if m.file in byfile
                                    else (sum(known) // len(known) if known else 1)) for m in ms}
    out: list[list[Mutant]] = [[] for _ in range(n)]
    load = [0] * n
    for m in sorted(ms, key=lambda m: (-est[m.id], m.id)):
        k = load.index(min(load))
        out[k].append(m)
        load[k] += est[m.id]
    return out


def run_one(m: Mutant, seeds: int, root: Path, do_equiv: bool) -> dict:
    t0 = time.time()
    work = root / m.id
    src = work / "src"
    shutil.copytree(REPO / "src", src)
    res = {"id": m.id, "file": m.file, "line": m.line, "op": m.op, "origin": m.origin, "desc": m.desc}
    bad = apply(m, src)
    if bad:
        return res | {"result": "BADPATCH", "note": bad, "secs": 0}
    killed_by, result = None, "SURVIVED"
    for suite in PIPELINE.get(m.file, CORE_PIPELINE):
        r = run_suite(suite, src, work, seeds)
        if r == "compile-error":
            result = "COMPILE-ERROR"
            killed_by = suite
            break
        if r in ("fail", "timeout"):
            result, killed_by = "KILLED", suite + (" (timeout)" if r == "timeout" else "")
            res["evidence"] = kill_evidence(suite, work) if r == "fail" else "timeout"
            break
    note = None
    if result == "SURVIVED" and m.id in EXCLUDED:
        shutil.rmtree(work, ignore_errors=True)
        return res | {"result": "EXCLUDED", "killed_by": None, "note": EXCLUDED[m.id],
                      "secs": round(time.time() - t0)}
    if result == "SURVIVED" and do_equiv:
        proved, note = equivalent(src, work)
        if proved:
            result = "EQUIVALENT"
    shutil.rmtree(work, ignore_errors=True)
    return res | {"result": result, "killed_by": killed_by, "note": note, "secs": round(time.time() - t0)}


def baseline(suites: set[str], seeds: int, root: Path, jobs: int) -> bool:
    """Every suite the mutants need passes on the unmutated RTL. Without this, a suite that
    cannot run at all (a tool or model problem) fails for every mutant and reads as kills."""
    src = root / "baseline" / "src"
    shutil.copytree(REPO / "src", src)
    def timed(s):
        # the baseline only measures, so it gets TIMEOUT_FACTOR times the fixed limit: the
        # pins suite takes ~1,840 s on a quiet machine, and a busy one ran it past 2,400 s
        t0 = time.time()
        r = run_suite(s, src, root / "baseline", seeds, limit=TIMEOUT_FACTOR * SUITES[s][2])
        BASE_SECS[s] = time.time() - t0
        return r
    with ThreadPoolExecutor(max(1, min(jobs, len(suites)))) as pool:
        got = dict(zip(suites, pool.map(timed, suites)))
    for s, r in sorted(got.items()):
        limit = max(SUITES[s][2], TIMEOUT_FACTOR * BASE_SECS[s])
        print(f"baseline {s:6s} {r}  {BASE_SECS[s]:.0f} s, limit per mutant {limit:.0f} s", flush=True)
        log = root / "baseline" / f"{s}.log"
        if r != "pass":              # a suite that timed out wrote no log
            print("".join(log.read_text().splitlines(True)[-60:]) if log.exists()
                  else f"(no {s}.log: the suite {r})", flush=True)
    return all(r == "pass" for r in got.values())


def recheck_one(m: Mutant, root: Path) -> dict:
    """The equivalence check alone, for a mutant the tests already let through."""
    work = root / m.id
    src = work / "src"
    shutil.copytree(REPO / "src", src)
    bad = apply(m, src)
    if bad:
        return {"result": "BADPATCH", "note": bad}
    proved, note = equivalent(src, work)
    shutil.rmtree(work, ignore_errors=True)
    return {"result": "EQUIVALENT" if proved else "SURVIVED", "note": note}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=60)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--only", nargs="*")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--curated", action="store_true")
    g.add_argument("--generated", action="store_true")
    ap.add_argument("--shard", help="I/N: run shard I (0-based) of N, dealt by cost (see shards())")
    ap.add_argument("--costs-from", nargs="+", metavar="JSON",
                    help="rewrite costs.json from these result files (--json output, or CI's merged one)")
    ap.add_argument("--no-equiv", action="store_true", help="skip the equivalence check of survivors")
    ap.add_argument("--no-baseline", action="store_true",
                    help="skip the check that every suite passes on the unmutated RTL")
    ap.add_argument("--json", help="write every result to this file")
    ap.add_argument("--progress", help="append each result to this JSON-lines file as it finishes, and skip "
                                       "mutants it already holds (resume an interrupted run)")
    ap.add_argument("--recheck", action="store_true",
                    help="with --progress: rerun only the equivalence check of the survivors it holds")
    ap.add_argument("--list", action="store_true", help="list the mutants and exit")
    ap.add_argument("--no-canary", action="store_true", help="skip the canaries after the baseline")
    ap.add_argument("--canaries-only", action="store_true",
                    help="run the baseline and the canaries of the selected mutants' suites, then exit")
    ap.add_argument("--reconcile", action="store_true",
                    help="check that every selected mutant has exactly one final result in --progress")
    ap.add_argument("--rate", type=float, default=generate.RATE,
                    help=f"fraction of the generated sites taken (default {generate.RATE}; 1 takes every site)")
    args = ap.parse_args()

    if args.recheck:
        prog = Path(args.progress)
        rows = [json.loads(line) for line in prog.read_text().splitlines() if line.strip()]
        byid = {m.id: m for m in all_mutants(args.rate)}
        # a proof first; an argued exclusion only where the proof is undecided (as in a run).
        # Survivors whose proof already ran out of time are not proved again.
        tried = [r for r in rows if r["result"] == "SURVIVED" and r["id"] in EXCLUDED
                 and (r.get("note") or "").startswith("equivalence: undecided")]
        for r in tried:
            r.update({"result": "EXCLUDED", "note": EXCLUDED[r["id"]]})
            print(f"{'EXCLUDED':13s} {r['id']:34s} (proof undecided earlier)  {r['desc']}", flush=True)
        if tried:
            prog.write_text("".join(json.dumps(x) + "\n" for x in rows))
        todo = [r for r in rows if r["result"] == "SURVIVED" and r["id"] in byid and not r.get("rechecked")
                and not (r.get("note") or "").startswith("equivalence: undecided")]
        print(f"rechecking {len(todo)} survivors, {args.jobs} at a time", flush=True)
        with tempfile.TemporaryDirectory(prefix="escmut_") as td, ThreadPoolExecutor(max(1, args.jobs)) as pool:
            futs = {pool.submit(recheck_one, byid[r["id"]], Path(td)): r for r in todo}
            for f in as_completed(futs):
                r = futs[f]
                r.update(f.result())
                if r["result"] == "SURVIVED" and r["id"] in EXCLUDED \
                        and r["note"].startswith("equivalence: undecided"):
                    r.update({"result": "EXCLUDED", "note": EXCLUDED[r["id"]]})
                r["rechecked"] = True
                print(f"{r['result']:13s} {r['id']:34s} {r['note']}  {r['desc']}", flush=True)
                prog.write_text("".join(json.dumps(x) + "\n" for x in rows))   # keep each result
        return 0

    ms = all_mutants(args.rate)
    if args.curated:
        ms = [m for m in ms if m.origin == "curated"]
    if args.generated:
        ms = [m for m in ms if m.origin == "generated"]
    if args.only:
        ms = [m for m in ms if m.id in args.only]
    if args.costs_from:
        cost = json.loads(COSTS.read_text()) if COSTS.exists() else {}
        for f in args.costs_from:
            cost |= {r["id"]: r["secs"] for r in json.loads(Path(f).read_text())["results"] if r.get("secs")}
        ids = {m.id for m in ms}
        cost = {k: v for k, v in sorted(cost.items()) if k in ids}
        COSTS.write_text(json.dumps(cost, indent=0) + "\n")
        print(f"{COSTS}: {len(cost)} of {len(ids)} mutants timed")
        return 0
    if args.shard:
        i, n = map(int, args.shard.split("/"))
        ms = shards(ms, n)[i]
    if args.reconcile:
        rows = [json.loads(line) for line in Path(args.progress).read_text().splitlines() if line.strip()]
        probs = reconcile([m.id for m in ms], rows)
        print("\n".join(probs) if probs else f"{len(ms)} mutants, each with one final result")
        print(Counter(r["result"] for r in rows if r["id"] in {m.id for m in ms}))
        return 1 if probs else 0
    if args.list:
        for m in ms:
            print(f"{m.id:34s} {m.desc}")
        print(len(ms), "mutants")
        return 0

    results = []
    if args.progress and Path(args.progress).exists():
        done = {}
        for line in Path(args.progress).read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["id"]] = r
        results = [done[m.id] for m in ms if m.id in done]
        ms = [m for m in ms if m.id not in done]
        print(f"{len(results)} mutants already done ({args.progress})", flush=True)
    jobs = max(1, min(args.jobs, len(ms) or 1))
    print(f"{len(ms)} mutants, {jobs} at a time", flush=True)
    with tempfile.TemporaryDirectory(prefix="escmut_") as td, ThreadPoolExecutor(jobs) as pool:
        need = {s for m in (ms or all_mutants(args.rate)) for s in PIPELINE.get(m.file, CORE_PIPELINE)}
        if (ms or args.canaries_only) and not args.no_baseline and not baseline(need, args.seeds, Path(td), jobs):
            print("the unmutated RTL fails a suite: no mutant is scored")
            return 2
        if (ms or args.canaries_only) and not args.no_canary \
                and not canaries(need, args.seeds, Path(td), jobs, not args.no_equiv):
            print("a canary was not caught: the suites or the proof cannot be trusted, no mutant is scored")
            return 2
        if args.canaries_only:
            return 0
        futs = [pool.submit(run_one, m, args.seeds, Path(td), not args.no_equiv) for m in ms]
        for f in as_completed(futs):
            r = f.result()
            results.append(r)
            if args.progress:
                with open(args.progress, "a") as fh:
                    fh.write(json.dumps(r) + "\n")
            print(f"{r['result']:13s} {r.get('killed_by') or '':16s} {r['secs']:5d} s  {r['id']:34s} {r['desc']}",
                  flush=True)
    counts = Counter(r["result"] for r in results)
    killed, eq, ex = counts["KILLED"], counts["EQUIVALENT"], counts["EXCLUDED"]
    invalid = counts["BADPATCH"] + counts["COMPILE-ERROR"]
    scored = len(results) - invalid - eq - ex
    print(f"\n{killed} of {scored} killed; {eq} equivalent (proved); {ex} excluded (argued, see EXCLUDED); "
          f"{invalid} invalid; {counts['SURVIVED']} survived")
    if args.json:
        Path(args.json).write_text(json.dumps({"counts": dict(counts), "seeds": args.seeds,
                                               "results": results}, indent=1) + "\n")
    return 0 if counts["SURVIVED"] == 0 and invalid == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
