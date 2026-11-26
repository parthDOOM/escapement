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

[ $fail = 0 ] && echo "every tooth bites" || echo "a tooth did not bite: the proof it stands for cannot be trusted"
exit $fail
