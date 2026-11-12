#!/usr/bin/env bash
# The proofs' teeth (docs/04-VERIFICATION.md): each tooth plants one known bug in a copy of
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

[ $fail = 0 ] && echo "every tooth bites" || echo "a tooth did not bite: the proof it stands for cannot be trusted"
exit $fail
