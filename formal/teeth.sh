#!/usr/bin/env bash
# The proofs' teeth (docs/04-VERIFICATION.md): each tooth plants a known bug in a copy of
# src/ and runs the proof that must catch it. A tooth bites when its edit applied exactly once
# and the proof FAILS; a proof that passes with the bug in place shows nothing.
#
#   bash formal/teeth.sh [NAME ...]       (default: every tooth; needs sby, as make formal)
#
#   l2-late      v1.6 L2: the boundary flag registered a cycle late          esc_line.sby bmc
#   l2-cell      v1.6 L2: the pin value from the current cell, not the next  esc_line.sby bmc
#   l2-prefix    v1.6 L2: the first bit from the prefix's other end          esc_line.sby bmc
#   cfg-sel      v2 sel_def: the one-hot owner register stuck at context 0    esc_line.sby bmc
#   cfg-early    v2 cfg_pre: the CONFIG fault terms sampled at step 0 only    esc_line.sby bmc
#   ni-stop      v1.7 NI: any context's CTX_STOP holds every context's commit  esc_ni_ind.sby base
#   bmc-s3       v1.7 S3: both 11-10 restart bugs put back                    split_bmc.py, depth 8
#   c6-grant     v2 C6: a live write takes the store port at once (G1)       esc_top.sby prove
#   c6-page      v2 C6: IMEM_PAGE moves the page under a waiting word (P1)   esc_host.sby prove
#   c6-wait      v2 C6: imem_free only ever in slot 0 (the 4-cycle wait)     split_bmc.py, depth 12
#   m-keep       v3 M3: MON's keep bit ignored (a clear rewrites a rule)      esc_mon.sby kind
#   m-count      v3 M1: the interval counter starts at 0 (the off-by-one)    esc_mon.sby kind
#   m-dq         v3 M7: the rule latches' shared write word drops a bit      esc_mon.sby kind
#   m-own-read   v3: GETM 0/1 read the monitor for any context                esc_ni_ind.sby kind (scored on the induction failure, below)
#   m-own-mon    v3: a MON of another context than the monitor's owner completes  split_bmc.py, depth 12
#   p-guard      D-73: a context parked while a live write may be in flight  esc_core.sby park_bmc
#   e-stage      D-74: the held X select loaded from the wrong stage (p2v)   esc_core.sby ahead
#   c-cm .. c-fi D-75: a gated context bank's enable misses a term            esc_ctx.sby kind
#   d-x, d-f     D-76: a pipeline bank loads on the wrong stage's valid bit   esc_core.sby ahead
#   i-add-c .. i-ret-pc  ISA: one wrong result per spike instruction (ADD's C, SHR's C one
#                bit off, DJNZ's lc saturating, JZ on C, CALL's lr, RET to lr+1)  esc_core.sby isa
#   i-sub-c .. i-ff-tick  ISA tier 1: SUB's borrow, CMP leaving Z, MOVHI's low half, AND, the
#                immediates sign-extended, SHL's C, LDSH's source, JNC, LDLC, IRQ's code, an
#                unlisted opcode legal, a reserved R bit ignored, the fault record's tick  esc_core.sby isa
#   i-rdtime .. i-grid  ISA tier 2: RDTIME's tick, SETT's source, ADDT sign-extended, WAITT not
#                done at tmr, HALT ignoring each of its three conditions, HALT's PC, the paired
#                HALT's 2-tick retry  esc_core.sby isa
#   i-full .. i-minlead  ISA tier 3: the full test off by one, EXPECT's zero mask, a claimed pin
#                driven, ORDER skipped for a single entry, OVERLAP before any EXPECT, PAST's
#                subcode, EMITX's C and shift, EMITR's byte, a DRIVE's oe bit 0, last_expect_end
#                without the window, a dropped SEND's Z, wait_full and min_lead  esc_core.sby isa
# Prints one line per tooth; exit status 1 if any tooth did not bite.
set -u
cd "$(dirname "$0")/.."
REPO=$PWD
W=$(mktemp -d "${TMPDIR:-/tmp}/escteeth.XXXXXX")
[ -n "${TEETH_KEEP:-}" ] && echo "work dir: $W (kept)" || trap 'rm -rf "$W"' EXIT
fail=0

# tooth NAME FILE SED PLANTED_REGEX SBY TASK TIMEOUT_S
tooth() {
  local name=$1 file=$2 expr=$3 pat=$4 sby=$5 task=$6 to=$7 d="$W/$1"
  if [ -n "$SEL" ] && ! [[ " $SEL " == *" $name "* ]]; then return; fi
  mkdir -p "$d"; cp -r "$REPO/src" "$REPO/formal" "$d/"
  local before after
  before=$(grep -c -- "$pat" "$d/src/$file")
  sed -i "$expr" "$d/src/$file"
  after=$(grep -c -- "$pat" "$d/src/$file")
  if [ $((after - before)) -ne 1 ]; then
    printf "NO-BITE %-10s the edit applied %d times (expected once): %s\n" "$name" $((after - before)) "$expr"
    fail=1; return
  fi
  local s; s=$(date +%s)
  (cd "$d/formal" && timeout "$to" sby -f "$sby" "$task") > "$d/sby.log" 2>&1
  local done; done=$(grep -E 'DONE \(' "$d/sby.log" | tail -1 | sed 's/.*DONE/DONE/')
  # the step and the assertion that failed: a tooth must fail on an assertion, not on an error
  local step why
  step=$(grep -oE 'Checking assertions in step [0-9]+|failed in frame [0-9]+|Output [0-9]+ of miter .* was asserted in frame [0-9]+' "$d/sby.log" | tail -1 | grep -oE '[0-9]+$')
  why=$(grep -oE 'Assert failed in [^ ]+ [^ ]+' "$d/sby.log" | head -1)
  if [[ "$done" == "DONE (FAIL"* && -n "$why$step" ]]; then
    why="step ${step:-?}, ${why:-a failed assertion}"
    printf "BITES   %-10s %s in %ss: %s\n" "$name" "$done" $(( $(date +%s) - s )) "$why"
  else
    printf "NO-BITE %-10s %s in %ss (the proof did not fail on the planted bug)\n" "$name" "${done:-no verdict}" $(( $(date +%s) - s ))
    tail -5 "$d/sby.log"; fail=1
  fi
}
SEL="${*:-}"

tooth l2-late   esc_line.v 's/dq_go <= tb_n;/dq_go <= tb_hit;/' 'dq_go <= tb_hit;' esc_line.sby bmc 3600
tooth l2-cell   esc_line.v "s/levels(sk, lv, cel + 3'd1, c_p0/levels(sk, lv, cel, c_p0/" 'levels(sk, lv, cel, c_p0' esc_line.sby bmc 3600
tooth l2-prefix esc_line.v '/pbx_v *=/s/(c_lsb ? c_pre\[0\] : c_pre\[7\])$/(c_lsb ? c_pre[7] : c_pre[0])/' 'c_pre\[7\] : c_pre\[0\])$' esc_line.sby bmc 3600
tooth cfg-sel   esc_line.v "s/sel <= 4'd1 << op_ctx;/sel <= 4'd1;/" "sel <= 4'd1; end" esc_line.sby bmc 3600
tooth cfg-early esc_line.v "s/    else if (!lu_hold) {own_bad_q, v_norm_q, v_ext_q, w3x_q} <=/    else if (!lu_hold \&\& el == 12'd0) {own_bad_q, v_norm_q, v_ext_q, w3x_q} <=/" "!lu_hold && el == 12'd0) {own_bad_q" esc_line.sby bmc 3600
tooth ni-stop   esc_core.v 's/& ~ctx_stop\[cctx\];/\& ~(|ctx_stop);/' '& ~(|ctx_stop);' esc_ni_ind.sby base 10800
tooth c6-grant  project.v 's/assign     imem_wgnt = imem_wpend & (core_imem_free | ~running);/assign     imem_wgnt = imem_wpend;/' 'imem_wgnt = imem_wpend;' esc_top.sby prove 600
tooth c6-page   escapement_host.v "s/C_IMEM_PAGE: if (idx == 5'd1 \&\& wslot) mem_page <= rx_byte\[1:0\];/C_IMEM_PAGE: if (idx == 5'd1) mem_page <= rx_byte[1:0];/" "C_IMEM_PAGE: if (idx == 5'd1) mem_page <= rx_byte" esc_host.sby prove 1800
tooth m-keep    esc_mon.v 's/wr    = ~start \& mon_we \& ~mon_a\[16\];/wr    = ~start \& mon_we;/' 'wr    = ~start & mon_we;' esc_mon.sby kind 600
# m-dq: the latch banks' shared write word drops a bit (only M7, esc_lreg's contract, sees it: the logic
# proofs model each bank as the enable flip-flop it replaces)
tooth m-dq      esc_mon.v "s/\.d(cfg_d), \.q(cfg_q)/.d({cfg_d[CW-1:1], 1'b0}), .q(cfg_q)/" "cfg_d\[CW-1:1\], 1'b0" esc_mon.sby kind 600
tooth m-count   esc_mon.v "s/cnt_d = {{(W-1){1'b0}}, 1'b1}; end/cnt_d = {W{1'b0}}; end/" 'cnt_d = {W{1.b0}}; end' esc_mon.sby kind 600
tooth p-guard   esc_core.v 's/cm_wait & cm_sel\[gk\] & ~(wr_hit\[gk\] | wq0\[gk\] | wq1\[gk\]);/cm_wait \& cm_sel[gk] \& ~(wr_hit[gk] | wq0[gk]);/' 'cm_sel\[gk\] & ~(wr_hit\[gk\] | wq0\[gk\]);' esc_core.sby park_bmc 3600
tooth e-stage   esc_core.v 's/if (p1v) nq_e <= own(sq + 2.d2, pair_on);/if (p2v) nq_e <= own(sq + 2'"'"'d2, pair_on);/' 'if (p2v) nq_e <= own' esc_core.sby ahead 3600
# D-75: each gated bank of esc_ctx loses one term of its enable; the shadow proof must fail
tooth c-cm      esc_ctx.v 's/wire en_cm = start_i | cm_ok;/wire en_cm = cm_ok;/' 'wire en_cm = cm_ok;' esc_ctx.sby kind 600
tooth c-wf      esc_ctx.v 's/wire en_wf = start_i | cm_waitfull;/wire en_wf = cm_waitfull;/' 'wire en_wf = cm_waitfull;' esc_ctx.sby kind 600
tooth c-ck      esc_ctx.v 's/wire en_ck = start_i | o_act | due_q;/wire en_ck = start_i | o_act;/' 'wire en_ck = start_i | o_act;' esc_ctx.sby kind 600
# D-76: a pipeline bank loads on the wrong stage's valid bit; ahead_s2a-c must fail
tooth d-x       esc_core.v 's/assign {ge_p1, ge_p2, ge_p3} = {f_valid, p1v, p2v};/assign {ge_p1, ge_p2, ge_p3} = {f_valid, p1v, p1v};/' '= {f_valid, p1v, p1v};' esc_core.sby ahead 3600
tooth d-f       esc_core.v 's/assign {ge_p1, ge_p2, ge_p3} = {f_valid, p1v, p2v};/assign {ge_p1, ge_p2, ge_p3} = {p1v, p1v, p2v};/' '= {p1v, p1v, p2v};' esc_core.sby ahead 3600
tooth c-fi      esc_ctx.v 's/| (o_cnt != 3.d0) | push;/| (o_cnt != 3'"'"'d0);/' "| (o_cnt != 3'd0);" esc_ctx.sby kind 600

# bmc-s3: the bounded check's own tooth (split_bmc.py at depth 8): the two v1.7 S3 bugs of 11-10
# put back (d_lee loaded with 0 at a restart; CTX_START taken for a disabled or running context)
# must fail in the D-40 lee group of esc_ctx and the restart-edge group of esc_core.
if [ -z "$SEL" ] || [[ " $SEL " == *" bmc-s3 "* ]]; then
  d="$W/bmc-s3"; mkdir -p "$d"; cp -r "$REPO/src" "$REPO/formal" "$d/"
  sed -i "s/d_lee = st ? start_tmr : w_lee;/d_lee = st ? 32'd0 : w_lee;/" "$d/src/esc_ctx.v"
  sed -i 's/wire \[3:0\]  rs_want = rs_pend | (ctx_start & ctx_enable & ~c_runv);/wire [3:0]  rs_want = rs_pend | ctx_start;/' "$d/src/esc_core.v"
  n1=$(grep -c "d_lee = st ? 32'd0 : w_lee;" "$d/src/esc_ctx.v"); n2=$(grep -c 'rs_want = rs_pend | ctx_start;' "$d/src/esc_core.v")
  l1=$(grep -n 'if ($past(en_lee))  *assert' "$d/src/esc_ctx.v" | cut -d: -f1)
  l2=$(grep -n 'assert (!rs_any || !commit_valid)' "$d/src/esc_core.v" | cut -d: -f1)
  if [ "$n1$n2" != 11 ] || [ -z "$l1" ] || [ -z "$l2" ]; then
    echo "NO-BITE bmc-s3     the edits applied $n1 and $n2 times (expected once each)"; fail=1
  else
    s=$(date +%s)
    (cd "$d/formal" && timeout 3600 python3 split_bmc.py --depth 8 "esc_ctx_$l1" "esc_core_$l2") > "$d/split.log" 2>&1
    f1=$(grep -cE "^FAIL .*FAILED esc_ctx.v:$l1\." "$d/split.log"); f2=$(grep -cE "^FAIL .*FAILED esc_core.v:$l2\." "$d/split.log")
    if [ "$f1$f2" = 11 ]; then
      printf "BITES   %-10s both groups FAIL in %ss: %s\n" bmc-s3 $(( $(date +%s) - s )) \
        "$(grep -E '^FAIL' "$d/split.log" | sed -E 's/ +/ /g; s/^FAIL [0-9]+ s //' | cut -c1-60 | tr '\n' ';')"
    else
      printf "NO-BITE %-10s esc_ctx.v:%s failed %s, esc_core.v:%s failed %s (each must be 1)\n" bmc-s3 "$l1" "$f1" "$l2" "$f2"
      cat "$d/split.log"; fail=1
    fi
  fi
fi

# c6-wait: the core's 4-cycle wait for a live write (split_bmc.py at depth 12): with a free
# slot only ever in slot 0, an idle context 1, 2 or 3 never frees its slot, and the count passes 4.
if [ -z "$SEL" ] || [[ " $SEL " == *" c6-wait "* ]]; then
  d="$W/c6-wait"; mkdir -p "$d"; cp -r "$REPO/src" "$REPO/formal" "$d/"
  sed -i 's/free_q <= run & ~start & ~stop & (c_state\[2\*fq_n +: 2\] != ST_RUN) & ~rs_k\[fq_n\];/free_q <= run \& ~start \& ~stop \& (c_state[2*fq_n +: 2] != ST_RUN) \& ~rs_k[fq_n] \& (fq_n == 2'"'"'d0);/' "$d/src/esc_core.v"
  n=$(grep -c "~rs_k\[fq_n\] & (fq_n == 2'd0);" "$d/src/esc_core.v")
  l=$(grep -n 'assert (f_nofree' "$d/src/esc_core.v" | cut -d: -f1)
  if [ "$n" != 1 ] || [ -z "$l" ]; then
    echo "NO-BITE c6-wait    the edit applied $n times (expected once), assertion line '$l'"; fail=1
  else
    s=$(date +%s)
    (cd "$d/formal" && timeout 3600 python3 split_bmc.py --depth 12 "esc_core_$l") > "$d/split.log" 2>&1
    if grep -qE "^FAIL .*FAILED esc_core.v:$l\." "$d/split.log"; then
      printf "BITES   %-10s FAIL in %ss: %s\n" c6-wait $(( $(date +%s) - s )) \
        "$(grep -E '^FAIL' "$d/split.log" | sed -E 's/ +/ /g; s/^FAIL [0-9]+ s //' | cut -c1-60)"
    else
      printf "NO-BITE %-10s esc_core.v:%s did not fail\n" c6-wait "$l"; cat "$d/split.log"; fail=1
    fi
  fi
fi

# m-own-mon: monitor ownership (split_bmc.py at depth 12): with the MON owner check gone, a
# second context's MON completes, and the monown assertion on the MON commit fails.
if [ -z "$SEL" ] || [[ " $SEL " == *" m-own-mon "* ]]; then
  d="$W/m-own-mon"; mkdir -p "$d"; cp -r "$REPO/src" "$REPO/formal" "$d/"
  sed -i "s/if ((|mon_sel) \&\& !mon_sel\[cctx\]) begin/if (1'b0) begin/" "$d/src/esc_core.v"
  n=$(grep -c "if (1'b0) begin" "$d/src/esc_core.v")
  l=$(grep -n 'if (cm_ok && d_op\[I_MON\]) assert (mon_sel' "$d/src/esc_core.v" | cut -d: -f1)
  if [ "$n" != 1 ] || [ -z "$l" ]; then
    echo "NO-BITE m-own-mon  the edit applied $n times (expected once), assertion line '$l'"; fail=1
  else
    s=$(date +%s)
    (cd "$d/formal" && timeout 3600 python3 split_bmc.py --depth 12 monown) > "$d/split.log" 2>&1
    if grep -qE "^FAIL .*FAILED esc_core.v:$l\." "$d/split.log"; then
      printf "BITES   %-10s FAIL in %ss: %s\n" m-own-mon $(( $(date +%s) - s )) \
        "$(grep -E '^FAIL' "$d/split.log" | sed -E 's/ +/ /g; s/^FAIL [0-9]+ s //' | cut -c1-60)"
    else
      printf "NO-BITE %-10s esc_core.v:%s did not fail\n" m-own-mon "$l"; cat "$d/split.log"; fail=1
    fi
  fi
fi

# m-own-read: a context that does not own the monitor reads it (GETM 0/1 without the mon_sel check).
# It stands for the NI induction (esc_ni_ind.sby kind). A bounded check from reset does not reach it:
# the base task (depth 10) passed on this mutant (11-14), since the monitor must first hold an owner's
# data. And sby's prove mode never ends FAIL on an induction failure alone (the basecase holds; an
# induction FAIL only withholds PASS, sby_engine_smtbmc.py): at best UNKNOWN, and yosys-smtbmc 0.69
# then ends ERROR, writing the witness of the induction trace (it wraps --unroll Bool terms in
# (_ extract 0 0), which yices rejects). So it bites when the solver's own lines say: the basecase
# passed, temporal induction failed on an NI assertion, and the proof did not pass. The unmutated
# design passes the same task (make formal-v2).
if [ -z "$SEL" ] || [[ " $SEL " == *" m-own-read "* ]]; then
  d="$W/m-own-read"; mkdir -p "$d"; cp -r "$REPO/src" "$REPO/formal" "$d/"
  pat="(f_imm16\[15:1\] == 15'd0) ? lu_getm"
  before=$(grep -c -- "$pat" "$d/src/esc_core.v")
  sed -i "s/(f_imm16\[15:1\] == 15'd0 \&\& mon_sel\[cctx\])/(f_imm16[15:1] == 15'd0)/" "$d/src/esc_core.v"
  after=$(grep -c -- "$pat" "$d/src/esc_core.v")
  if [ $((after - before)) -ne 1 ]; then
    printf "NO-BITE %-10s the edit applied %d times (expected once)\n" m-own-read $((after - before)); fail=1
  else
    s=$(date +%s)
    (cd "$d/formal" && timeout 7200 sby -f esc_ni_ind.sby kind) > "$d/sby.log" 2>&1
    done=$(grep -E 'DONE \(' "$d/sby.log" | tail -1 | sed 's/.*DONE/DONE/')
    why=$(grep -oE 'Assert failed in esc_ni_env: [a-z_0-9]+' "$d/sby.log" | head -1)
    if grep -q 'returned pass for basecase' "$d/sby.log" && grep -q 'Temporal induction failed' "$d/sby.log" \
       && [ -n "$why" ] && [[ "$done" != "DONE (PASS"* ]]; then
      printf "BITES   %-10s %s in %ss: basecase passed, induction failed, %s\n" m-own-read "$done" \
        $(( $(date +%s) - s )) "${why#Assert failed in }"
    else
      printf "NO-BITE %-10s %s in %ss (the proof did not fail on the planted bug)\n" m-own-read \
        "${done:-no verdict}" $(( $(date +%s) - s ))
      tail -5 "$d/sby.log"; fail=1
    fi
  fi
fi

# The ISA checker's teeth (esc_core.sby isa): each plants a wrong result for one instruction in the
# execute stage. The induction's base case (4 steps) ends before the first commit, so, as for
# m-own-read, a tooth bites when the proof does not pass and induction fails on an assertion
# whose label matches WANT, an extended regular expression for the field the bug breaks (with
# or without the instruction in the label: isa_<instr>_<field> or isa_<field>). The unmutated
# design passes the same task. A wrong branch may show first as a fault rather than a wrong PC
# (at the last address, a branch that wrongly falls through faults PC_RANGE), so the branch
# teeth accept the outcome and state as well.
# isa_tooth NAME SED PLANTED_REGEX WANT [TASK, default isa] [FILE, default esc_core.v]
isa_tooth() {
  local name=$1 expr=$2 pat=$3 want=$4 task=${5:-isa} f=${6:-esc_core.v} d="$W/$1"
  if [ -n "$SEL" ] && ! [[ " $SEL " == *" $name "* ]]; then return; fi
  mkdir -p "$d"; cp -r "$REPO/src" "$REPO/formal" "$d/"
  local before after
  before=$(grep -c -- "$pat" "$d/src/$f")
  sed -i "$expr" "$d/src/$f"
  after=$(grep -c -- "$pat" "$d/src/$f")
  if [ $((after - before)) -ne 1 ]; then
    printf "NO-BITE %-10s the edit applied %d times (expected once): %s\n" "$name" $((after - before)) "$expr"
    fail=1; return
  fi
  local s; s=$(date +%s)
  (cd "$d/formal" && timeout 3600 sby -f esc_core.sby "$task") > "$d/sby.log" 2>&1
  local done; done=$(grep -E 'DONE \(' "$d/sby.log" | tail -1 | sed 's/.*DONE/DONE/')
  local got; got=$(grep -oE 'failed assertion esc_core\.u_isa\.isa_[a-z0-9_]+' "$d/sby.log" | sed 's/.*u_isa\.//' | sort -u)
  if [[ "$done" != "DONE (PASS"* ]] && printf '%s\n' "$got" | grep -qxE "$want"; then
    printf "BITES   %-10s %s in %ss: induction fails on %s\n" "$name" "$done" $(( $(date +%s) - s )) "$(echo $got)"
  else
    printf "NO-BITE %-10s %s in %ss (failed: %s; wanted %s)\n" "$name" "${done:-no verdict}" \
      $(( $(date +%s) - s )) "$(echo ${got:-none})" "$want"
    tail -5 "$d/sby.log"; fail=1
  fi
}
# the spike: ADD, SHR, DJNZ, JZ, CALL, RET
isa_tooth i-add-c   "s/w_c = sum\[32\];/w_c = 1'b0;/" "w_c = 1'b0;" 'isa_(add_)?c'
isa_tooth i-shr-c   "s/w_c_we  = 1'b1; w_c = shl_t\[32\];/w_c_we  = 1'b1; w_c = is_shr ? shl_t[31] : shl_t[32];/" 'is_shr ? shl_t\[31\] : shl_t\[32\]' 'isa_(shr_)?c'
isa_tooth i-djnz-lc "s/wire \[15:0\] lc_dec  = cs_lc - 16'd1;/wire [15:0] lc_dec  = (cs_lc == 16'd0) ? 16'd0 : cs_lc - 16'd1;/" "(cs_lc == 16'd0) ? 16'd0" 'isa_(djnz_)?(lc|pc|out|state)'
isa_tooth i-jz-cond "s/d_op\[I_JZ\]:  if (cs_z)  begin/d_op[I_JZ]:  if (cs_c)  begin/" 'I_JZ\]:  if (cs_c)' 'isa_(jz_)?(pc|out|state)'
isa_tooth i-call-lr "s/w_pc = f_tgt; w_lr_we = 1'b1; w_lr = pc_inc;/w_pc = f_tgt; w_lr_we = 1'b1; w_lr = cs_pc;/" 'w_lr = cs_pc;' 'isa_(call_)?lr'
isa_tooth i-ret-pc  "s/          w_pc = cs_lr;/          w_pc = cs_lr + 9'd1;/" "w_pc = cs_lr + 9'd1;" 'isa_(ret_)?(pc|out|state)'
# tier 1: the rest of 7.1 and 7.2, ILLEGAL and the first-fault record
isa_tooth i-sub-c   "/d_op\[I_SUB\]: begin/,/end/s/w_c = ~sum\[32\];/w_c = sum[32];/" 'w_c = sum\[32\];' 'isa_(sub_)?c'
# (CMP's Z from the written data would be no bug: w_rd_data defaults to the adder's sum)
isa_tooth i-cmp-z   "s/z_src  = 3'd2;/z_src  = 3'd0;/" "z_src  = 3'd0;" 'isa_(cmp_)?z'
isa_tooth i-movhi   "s/w_rd_data = {f_imm16, opa\[15:0\]};/w_rd_data = {f_imm16, 16'd0};/" "w_rd_data = {f_imm16, 16'd0};" 'isa_(movhi_)?(rd|r[0-3]|z)'
isa_tooth i-and     "s/w_rd_data = opa & opb;/w_rd_data = opa \& ~opb;/" 'w_rd_data = opa & ~opb;' 'isa_(and_)?(rd|r[0-3]|z)'
isa_tooth i-imm     "s/alu_b   = is_immb ? {16'd0, f_imm16} : opb;/alu_b   = is_immb ? {{16{f_imm16[15]}}, f_imm16} : opb;/" '{{16{f_imm16\[15\]}}, f_imm16}' 'isa_(addi_|cmpi_)?(rd|r[0-3]|c|z)'
isa_tooth i-shl-c   "s/w_c_we  = 1'b1; w_c = shl_t\[32\];/w_c_we  = 1'b1; w_c = is_shr ? shl_t[32] : shl_t[31];/" 'is_shr ? shl_t\[32\] : shl_t\[31\]' 'isa_(shl_)?c'
isa_tooth i-ldsh    "s/d_op\[I_LDSH\]: begin w_sh_we = 1'b1; w_sh = opb; end/d_op[I_LDSH]: begin w_sh_we = 1'b1; w_sh = opa; end/" 'w_sh = opa; end' 'isa_(ldsh_)?sh'
isa_tooth i-jnc     "s/d_op\[I_JNC\]: if (!cs_c)/d_op[I_JNC]: if (cs_c)/" 'd_op\[I_JNC\]: if (cs_c)' 'isa_(jnc_)?(pc|out|state)'
isa_tooth i-ldlc    "s/d_op\[I_LDLC\]: begin w_lc_we = 1'b1; w_lc = f_imm16; end/d_op[I_LDLC]: begin w_lc_we = 1'b1; w_lc = {f_imm16[15:1], 1'b0}; end/" 'w_lc = {f_imm16\[15:1\], 1.b0}' 'isa_(ldlc_)?lc'
isa_tooth i-irq     "s/\.w_irq_code  (insn\[7:0\])/.w_irq_code  (insn[8:1])/" '\.w_irq_code  (insn\[8:1\])' 'isa_[a-z0-9_]*irq[a-z0-9_]*'
isa_tooth i-unlisted "s/illegal_of = ~(|onehot(w\[31:24\]))/illegal_of = (~(|onehot(w[31:24])) \& (w[31:24] != 8'h05))/" "w\[31:24\] != 8'h05" 'isa_[a-z0-9_]*(out|state|ff[a-z0-9_]*)'
isa_tooth i-rsv     "s/RSV_R = 24'h0FFFFF,/RSV_R = 24'h0FFFFE,/" "RSV_R = 24'h0FFFFE," 'isa_[a-z0-9_]*(out|state|ff[a-z0-9_]*)'
isa_tooth i-ff-tick "s/ff_cm   ? {f_cause, f_sub, cctx, cs_pc, now} :/ff_cm   ? {f_cause, f_sub, cctx, cs_pc, now + 32'd1} :/" 'cs_pc, now + 32.d1}' 'isa_[a-z0-9_]*(ff|tick)[a-z0-9_]*'

# tier 2: time, HALT, WAIT and the retry grid. reached() itself is registered and pinned by
# the ahead lemma B2, which the isa task assumes, so a bug there would contradict an
# assumption rather than show; the teeth sit at the commit, after it.
isa_tooth i-rdtime  "s/d_op\[I_RDTIME\]: begin w_rd_we = 1'b1; w_rd_data = now; end/d_op[I_RDTIME]: begin w_rd_we = 1'b1; w_rd_data = now + 32'd1; end/" 'w_rd_data = now + 32.d1; end' 'isa_(rdtime_)?(rd|r[0-3])'
isa_tooth i-sett    "s/d_op\[I_SETT\]:   begin w_tmr_we = 1'b1; w_tmr = opb; end/d_op[I_SETT]:   begin w_tmr_we = 1'b1; w_tmr = opa; end/" 'w_tmr = opa; end' 'isa_(sett_)?tmr'
isa_tooth i-addt    "s/t_add  = d_op\[I_ADDT\]  ? {8'd0, f_imm24} :/t_add  = d_op[I_ADDT]  ? {{8{f_imm24[23]}}, f_imm24} :/" '{{8{f_imm24\[23\]}}, f_imm24}' 'isa_(addt_)?tmr'
isa_tooth i-waitt   "s/d_op\[I_WAITT\]:  if (!rch_tmr) outcome = OC_W;/d_op[I_WAITT]:  if (!rch_tmr || cs_tmr == now) outcome = OC_W;/" 'if (!rch_tmr || cs_tmr == now)' 'isa_(waitt_)?(out|pc|state)'
# i-halt-q: HALT halts with events still queued. The isa task cannot see it, by design: it
# assumes the design's proved invariants, and this bug breaks one, H3 (esc_ctx.v: a context
# that is not RUNNING has an empty FIFO), so it is scored where H3 is proved, the faulted
# group, bounded from reset (split_bmc.py at depth 12). A bug that breaks an assumed invariant
# must bite that invariant's own proof; the isa pass then covers everything else.
if [ -z "$SEL" ] || [[ " $SEL " == *" i-halt-q "* ]]; then
  d="$W/i-halt-q"; mkdir -p "$d"; cp -r "$REPO/src" "$REPO/formal" "$d/"
  sed -i "s/if (cs_cnta == 3'd0 \&\& !cs_acta \&\& !(lu_mine \&\& lu_engaged)) w_halt = 1'b1;/if (!cs_acta \&\& !(lu_mine \&\& lu_engaged)) w_halt = 1'b1;/" "$d/src/esc_core.v"
  n=$(grep -c 'if (!cs_acta && !(lu_mine && lu_engaged)) w_halt' "$d/src/esc_core.v")
  l=$(grep -n 'if (o_state != ST_RUN) assert (o_cnt == 3.d0);' "$d/src/esc_ctx.v" | cut -d: -f1)
  if [ "$n" != 1 ] || [ -z "$l" ]; then
    echo "NO-BITE i-halt-q   the edit applied $n times (expected once), H3 line '$l'"; fail=1
  else
    s=$(date +%s)
    (cd "$d/formal" && timeout 3600 python3 split_bmc.py --depth 12 faulted) > "$d/split.log" 2>&1
    if grep -qE "^FAIL .*FAILED esc_ctx.v:$l\." "$d/split.log"; then
      printf "BITES   %-10s FAIL in %ss: H3 fails (%s)\n" i-halt-q $(( $(date +%s) - s )) \
        "$(grep -E '^FAIL' "$d/split.log" | sed -E 's/ +/ /g; s/^FAIL [0-9]+ s //' | cut -c1-60)"
    else
      printf "NO-BITE %-10s esc_ctx.v:%s (H3) did not fail\n" i-halt-q "$l"; cat "$d/split.log"; fail=1
    fi
  fi
fi
isa_tooth i-halt-ck "s/if (cs_cnta == 3'd0 \&\& !cs_acta \&\& !(lu_mine \&\& lu_engaged)) w_halt = 1'b1;/if (cs_cnta == 3'd0 \&\& !(lu_mine \&\& lu_engaged)) w_halt = 1'b1;/" "if (cs_cnta == 3'd0 && !(lu_mine" 'isa_(halt_)?(out|state|pc)'
isa_tooth i-halt-lu "s/if (cs_cnta == 3'd0 \&\& !cs_acta \&\& !(lu_mine \&\& lu_engaged)) w_halt = 1'b1;/if (cs_cnta == 3'd0 \&\& !cs_acta) w_halt = 1'b1;/" "if (cs_cnta == 3'd0 && !cs_acta) w_halt" 'isa_(halt_)?(out|state|pc)'
isa_tooth i-halt-pc "s/          w_pc = cs_pc;/          w_pc = pc_inc;/" '          w_pc = pc_inc;' 'isa_(halt_)?pc'
# the grid: a paired context's next fetch after HALT is PC+1 instead of the HALT, so a waiting
# paired HALT is retried at c+4, not c+2
isa_tooth i-grid    "s/wire        x_stay  = d_op_n\[I_HALT\];/wire        x_stay  = 1'b0;/" "x_stay  = 1'b0;" 'isa_retry_(at|gap|cnt)[0-3]' isa_grid

# tier 3: the event instructions, admission (6.2), the FIFO across the edge and telemetry (7.4).
# The admission tests ahd_now, ahd_tail and ahd_lee are registered and pinned by the ahead
# lemmas, which the isa task assumes, so these teeth sit where the commit uses them.
isa_tooth i-full    "/d_op\[I_EMIT\], d_op\[I_EMITR\]/,/end else if/s/if (cs_cnta == 3'd4) begin/if (cs_cnta >= 3'd3) begin/" "if (cs_cnta >= 3'd3) begin" 'isa_(out|state|pc|q_cnt|q[0-3]|ff_[a-z]+|lee|eseen|tel_[a-z_]+)'
isa_tooth i-mask    "s/ev_kind != K_SAMPLE \&\& f_mask == 8'd0/ev_kind == K_DRIVE \&\& f_mask == 8'd0/" "ev_kind == K_DRIVE && f_mask == 8'd0" 'isa_(out|state|pc|q_cnt|q[0-3]|ff_[a-z]+|lee|eseen|tel_[a-z_]+)'
isa_tooth i-claimed "s/(f_mask \& ~(owned_c \& ~claimed_c))/(f_mask \& ~owned_c)/" '(f_mask & ~owned_c)' 'isa_(out|state|pc|q_cnt|q[0-3]|ff_[a-z]+|lee|eseen|tel_[a-z_]+)'
isa_tooth i-order   "/d_op\[I_EMIT\], d_op\[I_EMITR\]/,/w_push      = 1'b1;/s/end else if (cs_cnta != 3'd0 \&\& !ahd_tail) begin/end else if (cs_cnta > 3'd1 \&\& !ahd_tail) begin/" "cs_cnta > 3'd1 && !ahd_tail" 'isa_(out|state|pc|q_cnt|q[0-3]|ff_[a-z]+|lee|eseen|tel_[a-z_]+)'
isa_tooth i-overlap "s/ev_kind == K_EXPECT \&\& cs_eseen \&\& !ahd_lee/ev_kind == K_EXPECT \&\& !ahd_lee/" "ev_kind == K_EXPECT && !ahd_lee) begin" 'isa_(out|state|pc|q_cnt|q[0-3]|ff_[a-z]+|lee|eseen|tel_[a-z_]+)'
isa_tooth i-psub    "s/f_sub = AS_PAST;/f_sub = AS_ORDER;/" "f_sub = AS_ORDER;" 'isa_ff_sub'
isa_tooth i-emitx-c "s/w_c     = x_bit;/w_c     = ~x_bit;/" "w_c     = ~x_bit;" 'isa_c'
isa_tooth i-emitx-sh "s/w_sh    = f_dir ? {cs_sh\[30:0\], 1'b0} : {1'b0, cs_sh\[31:1\]};/w_sh    = f_dir ? {cs_sh[30:0], 1'b1} : {1'b0, cs_sh[31:1]};/" "{cs_sh\[30:0\], 1'b1}" 'isa_sh'
isa_tooth i-emitr   "s/d_op\[I_EMITR\] ? opb\[7:0\] :/d_op[I_EMITR] ? opb[15:8] :/" 'd_op\[I_EMITR\] ? opb\[15:8\] :' 'isa_q[0-3]'
isa_tooth i-entry   "s/{ev_kind, cs_tmr, f_mask, ev_val, insn\[7:0\]}/{ev_kind, cs_tmr, f_mask, ev_val, insn[7:1], 1'b0}/" "insn\[7:1\], 1'b0}" 'isa_q[0-3]'
# i-lee: an admitted EXPECT records lee = d instead of d + window. lee shares tmr's write
# register (D-40), and esc_ctx.v asserts that the register holds what lee takes; the isa task
# assumes that check, which with this bug can hold only when the window is 0, where d and
# d + window agree. So, like i-halt-q, it is scored where that check is proved: its own group,
# bounded from reset (split_bmc.py at depth 12). Measured 11-27: fails at step 7 in 4 s; the
# isa task with the bug passed in 2572 s, as expected.
if [ -z "$SEL" ] || [[ " $SEL " == *" i-lee "* ]]; then
  d="$W/i-lee"; mkdir -p "$d"; cp -r "$REPO/src" "$REPO/formal" "$d/"
  sed -i "s/\.w_lee       (t_sum)/.w_lee       (cs_tmr)/" "$d/src/esc_core.v"
  n=$(grep -c '\.w_lee       (cs_tmr)' "$d/src/esc_core.v")
  l=$(grep -n 'if ($past(en_lee)) assert (q_tmr == $past(d_lee));' "$d/src/esc_ctx.v" | cut -d: -f1)
  if [ "$n" != 1 ] || [ -z "$l" ]; then
    echo "NO-BITE i-lee      the edit applied $n times (expected once), D-40 line '$l'"; fail=1
  else
    s=$(date +%s)
    (cd "$d/formal" && timeout 3600 python3 split_bmc.py --depth 12 "esc_ctx_$l") > "$d/split.log" 2>&1
    if grep -qE "^FAIL .*FAILED esc_ctx.v:$l\." "$d/split.log"; then
      printf "BITES   %-10s FAIL in %ss: D-40 lee check fails (%s)\n" i-lee $(( $(date +%s) - s )) \
        "$(grep -E '^FAIL' "$d/split.log" | sed -E 's/ +/ /g; s/^FAIL [0-9]+ s //' | cut -c1-60)"
    else
      printf "NO-BITE %-10s esc_ctx.v:%s (D-40 lee) did not fail\n" i-lee "$l"; cat "$d/split.log"; fail=1
    fi
  fi
fi
isa_tooth i-send-z  "/d_op\[I_SEND\]: begin/,/w_push = 1'b1;/s/z_src = 3'd4; w_z = 1'b0;/z_src = 3'd4; w_z = 1'b1;/" "w_z = 1'b1;" 'isa_z'
isa_tooth i-wfull   "s/else if (cm_waitfull \&\& o_wait_full != 16'hFFFF) o_wait_full <= o_wait_full + 16'd1;/else if (cm_waitfull) o_wait_full <= o_wait_full + 16'd1;/" "else if (cm_waitfull) o_wait_full" 'isa_tel_wait_full' isa esc_ctx.v
isa_tooth i-minlead "s/if (w_lead16 < o_min_lead) o_min_lead <= w_lead16;/if (w_lead16 < o_min_lead) o_min_lead <= w_lead16 + 16'd1;/" "o_min_lead <= w_lead16 + 16'd1;" 'isa_tel_min_lead' isa esc_ctx.v

# tier 4: input (7.3), mailboxes (7.5), tape and capture (7.6), line unit (7.7), monitor (7.8):
# each instruction's effect on the context and its hand-off to the unit it uses
isa_tooth i-getp     "s/w_rd_data = {24'd0, sync}; z_src/w_rd_data = {24'd0, sync[7:1], 1'b0}; z_src/" "sync\[7:1\], 1'b0}" 'isa_(rd|r[0-3]|z)'
isa_tooth i-waitp    "s/if (p_match)      begin z_src = 3'd4; w_z = 1'b1; end/if (p_match)      begin z_src = 3'd4; w_z = 1'b0; end/" "if (p_match)      begin z_src = 3'd4; w_z = 1'b0;" 'isa_z'
isa_tooth i-geti     "s/if (cs_hsmp) outcome = OC_W;/if (1'b0) outcome = OC_W;/" "if (1'b0) outcome = OC_W;" 'isa_(out|state|pc|rd|r[0-3]|z|ish)'
isa_tooth i-getx     "s/w_rd_data = {cs_fail, cs_pass, 14'd0, cs_act, cs_failed};/w_rd_data = {cs_pass, cs_fail, 14'd0, cs_act, cs_failed};/" "{cs_pass, cs_fail, 14'd0" 'isa_(rd|r[0-3])'
isa_tooth i-pop-wait "s/if (cs_rxe) outcome = OC_W;/if (1'b0) outcome = OC_W;/" "if (1'b0) outcome = OC_W;" 'isa_(out|state|pc|rd|r[0-3]|z|mbx_[a-z0-9_]+)'
isa_tooth i-pop-keep "s/w_rd_data = cs_rxh; w_rx_pop = 1'b1;/w_rd_data = cs_rxh; w_rx_pop = 1'b0;/" "w_rd_data = cs_rxh; w_rx_pop = 1'b0;" 'isa_mbx_[a-z0-9_]+'
isa_tooth i-push     "s/if (cs_txf) outcome = OC_W;/if (1'b0) outcome = OC_W;/" "if (1'b0) outcome = OC_W;" 'isa_(out|state|pc|mbx_[a-z0-9_]+)'
isa_tooth i-rxready  "s/w_z = cs_rxe; end/w_z = ~cs_rxe; end/" "w_z = ~cs_rxe;" 'isa_z'
isa_tooth i-txready  "s/w_z = cs_txf; end/w_z = ~cs_txf; end/" "w_z = ~cs_txf;" 'isa_z'
isa_tooth i-tape-a   "s/tp_busy ? (opb\[7:0\] + f_imm16\[7:0\])/tp_busy ? (opb[7:0] + f_imm16[7:0] + 8'd1)/" "f_imm16\[7:0\] + 8'd1)" 'isa_tape_[a-z]+'
isa_tooth i-stt-d    "s/tp_busy ? opa : cap_head;/tp_busy ? opb : cap_head;/" "tp_busy ? opb : cap_head;" 'isa_tape_[a-z]+'
isa_tooth i-tape-w   "s/d_op\[I_LDT\], d_op\[I_STT\]: if (lu_port) outcome = OC_W;/d_op[I_LDT], d_op[I_STT]: if (1'b0) outcome = OC_W;/" "d_op\[I_STT\]: if (1'b0)" 'isa_(out|state|pc|rd|r[0-3]|tape_[a-z]+)'
isa_tooth i-capt     "s/\.capt_v(opb)/.capt_v(opa)/" "\.capt_v(opa)" 'isa_capt_[a-z0-9_]+'
isa_tooth i-getc     "s/w_rd_data = cap_status; z_src = 3'd1;/w_rd_data = cap_status; z_src = 3'd0;/" "cap_status; z_src = 3'd0;" 'isa_z'
isa_tooth i-line-ill "s/if (l_op > 4'd3) begin/if (l_op > 4'd4) begin/" "if (l_op > 4'd4) begin" 'isa_(out|state|pc|ff_[a-z]+)'
isa_tooth i-line-own "s/(l_op == 4'd3) ? (lu_owned \&\& !lu_mine)/(l_op == 4'd3) ? 1'b0/" "(l_op == 4'd3) ? 1'b0" 'isa_(out|state|pc|ff_[a-z]+|lu_owne[rd])'
isa_tooth i-line-w   "s/end else if (l_op != 4'd0 \&\& lu_engaged) begin/end else if (l_op > 4'd1 \&\& lu_engaged) begin/" "l_op > 4'd1 && lu_engaged" 'isa_(out|state|pc)'
isa_tooth i-getl-z   "s/z_src = 3'd4; w_z = ~lu_busy;/z_src = 3'd4; w_z = lu_busy;/" "w_z = lu_busy;" 'isa_z'
isa_tooth i-getl-t   "s/w_tmr_we = 1'b1; w_tmr = lu_e; end/w_tmr_we = 1'b1; w_tmr = lu_e + 32'd1; end/" "w_tmr = lu_e + 32'd1" 'isa_tmr'
isa_tooth i-getm-own "s/(f_imm16\[15:1\] == 15'd0 \&\& mon_sel\[cctx\]) ? lu_getm/(f_imm16[15:1] == 15'd0) ? lu_getm/" "(f_imm16\[15:1\] == 15'd0) ? lu_getm" 'isa_(rd|r[0-3]|z)'
isa_tooth i-getm-lu  "s/\&\& lu_owned \&\& lu_owner == cctx)/\&\& lu_owned)/" "f_imm16 == 16'd4) && lu_owned)" 'isa_(rd|r[0-3]|z)'
isa_tooth i-mon-own  "s/mon_sel <= 4'd1 << cctx;/mon_sel <= 4'd1;/" "mon_sel <= 4'd1;" 'isa_(mon_owner|out|state|pc|ff_[a-z]+)'

# and one for each instruction the teeth above leave without one of its own, so every one
# of the 55 has a planted bug its proof must catch
isa_tooth i-mov      "s/d_op\[I_MOV\]:   begin w_rd_we = 1'b1; w_rd_data = opb;/d_op[I_MOV]:   begin w_rd_we = 1'b1; w_rd_data = opa;/" "d_op\[I_MOV\]:   begin w_rd_we = 1'b1; w_rd_data = opa;" 'isa_(rd|r[0-3]|z)'
isa_tooth i-movi     "s/w_rd_data = {16'd0, f_imm16};/w_rd_data = {f_imm16, 16'd0};/" "w_rd_data = {f_imm16, 16'd0};" 'isa_(rd|r[0-3]|z)'
isa_tooth i-or       "s/w_rd_data = opa | opb; z_src/w_rd_data = opa ^ opb; z_src/" "d_op\[I_OR\]:  begin w_rd_we = 1'b1; w_rd_data = opa ^ opb;" 'isa_(rd|r[0-3]|z)'
isa_tooth i-xor      "s/d_op\[I_XOR\]: begin w_rd_we = 1'b1; w_rd_data = opa ^ opb;/d_op[I_XOR]: begin w_rd_we = 1'b1; w_rd_data = opa | opb;/" "d_op\[I_XOR\]: begin w_rd_we = 1'b1; w_rd_data = opa | opb;" 'isa_(rd|r[0-3]|z)'
isa_tooth i-tsti     "s/d_op\[I_TSTI\]: z_src = 3'd3;/d_op[I_TSTI]: z_src = 3'd2;/" "d_op\[I_TSTI\]: z_src = 3'd2;" 'isa_z'
isa_tooth i-stsh     "s/d_op\[I_STSH\]: begin w_rd_we = 1'b1; w_rd_data = cs_sh; end/d_op[I_STSH]: begin w_rd_we = 1'b1; w_rd_data = ~cs_sh; end/" "w_rd_data = ~cs_sh; end" 'isa_(rd|r[0-3])'
isa_tooth i-nop      "s/d_op\[I_NOP\]: ;/d_op[I_NOP]: begin z_src = 3'd4; w_z = 1'b1; end/" "d_op\[I_NOP\]: begin z_src = 3'd4; w_z = 1'b1; end" 'isa_z'
isa_tooth i-jmp      "s/d_op\[I_JMP\]: begin seq = 1'b0; w_pc = f_tgt; end/d_op[I_JMP]: begin seq = 1'b0; w_pc = f_tgt + 9'd1; end/" "w_pc = f_tgt + 9'd1; end" 'isa_(pc|out|state)'
isa_tooth i-jnz      "s/d_op\[I_JNZ\]: if (!cs_z) begin/d_op[I_JNZ]: if (cs_z) begin/" "d_op\[I_JNZ\]: if (cs_z) begin" 'isa_(pc|out|state)'
isa_tooth i-jc       "s/d_op\[I_JC\]:  if (cs_c)  begin/d_op[I_JC]:  if (cs_z)  begin/" "d_op\[I_JC\]:  if (cs_z)  begin" 'isa_(pc|out|state)'
isa_tooth i-addtr    "s/d_op\[I_ADDTR\] ? opb : {25'd0, f_win};/d_op[I_ADDTR] ? opa : {25'd0, f_win};/" "d_op\[I_ADDTR\] ? opa :" 'isa_tmr'
isa_tooth i-sample   "s/end else if (ev_kind != K_SAMPLE \&\& f_mask == 8'd0) begin/end else if (f_mask == 8'd0) begin/" "end else if (f_mask == 8'd0) begin" 'isa_(out|state|pc|q_cnt|q[0-3]|ff_[a-z]+|tel_[a-z_]+)'

[ $fail = 0 ] && echo "every tooth bites" || echo "a tooth did not bite: the proof it stands for cannot be trusted"
exit $fail
