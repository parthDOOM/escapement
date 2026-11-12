#!/usr/bin/env bash
# Frequency headroom of one harden run (docs/03-PHYSICAL-DESIGN.md). Inside WSL/Linux:
#
#   [DUTY=40] scripts/frequency-headroom.sh RUN_DIR [CORNER:PERIOD ...]
#
# RUN_DIR        a LibreLane run (runs/<tag>) with final/nl and final/spef
# CORNER:PERIOD  slow, typ or fast and a clock period in ns (default: the sweep behind the
#                table in docs/03, around each corner's limit)
# DUTY           the clock's high time in percent of the period (default 50): a board that
#                cannot make an exact 50% clock, checked at the rated period with slow:20 typ:20 fast:20
#
# OpenSTA in the pinned LibreLane container (FLOW, default ttihp27a) reads the final netlist and its extracted
# parasitics with the sign-off constraints (src/escapement.sdc: latches modelled, time
# borrowing allowed), the period shortened and the 20% I/O delays scaled with it. Setup
# holds when the worst slack is >= 0; a latch input that borrows reports exactly 0 (printed
# as 0.000 or -0.000). On the v1.6 run (v16s) this gives the docs/03 table: setup holds at
# 19.7, 12.4 and 8.3 ns and fails at 19.6, 12.3 and 8.2 ns (v1.5, v15c: holds at 25.9, 16.3 and
# 10.6 ns, with slow:25.9 slow:25.8 typ:16.3 typ:16.2 fast:10.6 fast:10.5).
# Prints one RESULT line per corner and period, with the number of minimum pulse width
# violators; reports go to RUN_DIR/frequency/.
set -euo pipefail

RUN=$(realpath "$1"); shift
POINTS=("$@")
[[ ${#POINTS[@]} -eq 0 ]] && POINTS=(slow:19.8 slow:19.7 slow:19.6
                                      typ:12.5 typ:12.4 typ:12.3
                                      fast:8.4 fast:8.3 fast:8.2)
DUTY=${DUTY:-50}

REPO=$(realpath "$(dirname "$0")/..")
EDA="${EDA:-$HOME/eda}"
# FLOW as for scripts/local-harden.sh (default ttihp27a): the PDK and container the run used
case "${FLOW:-ttihp27a}" in
  pre)      export PDK_ROOT="${PDK_ROOT:-$EDA/pdk-ihp-2bbec755dc67ca3db0261c3d6163e15735d66710}"
            IMAGE=ghcr.io/librelane/librelane:3.1.0.dev3 ;;
  ttihp27a) export PDK_ROOT="${PDK_ROOT:-$EDA/pdk-ciel/ciel/ihp-sg13/versions/89d42ee6d2fd9b50b86a1aada6fee6673c3e6008}"
            IMAGE=ghcr.io/librelane/librelane:3.1.0.dev4 ;;
  *) echo "FLOW must be pre or ttihp27a" >&2; exit 2 ;;
esac
LIB=$PDK_ROOT/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib
ART=$(ls "$REPO"/macro/escapement_art/lib/*.lib 2>/dev/null | head -n 1 || true)
OUT=$RUN/frequency
mkdir -p "$OUT"
declare -A STD=([slow]=slow_1p08V_125C [typ]=typ_1p20V_25C [fast]=fast_1p32V_m40C)
declare -A SRAM=([slow]=slow_1p08V_125C [typ]=typ_1p20V_25C [fast]=fast_1p32V_m55C)

for pt in "${POINTS[@]}"; do
  c=${pt%%:*} P=${pt#*:}
  tag=${c}_$P$([[ $DUTY == 50 ]] || echo _duty$DUTY)
  tcl=$OUT/sta_$tag.tcl sdc=$REPO/src/escapement.sdc
  if [[ $DUTY != 50 ]]; then
    hi=$(python3 -c "print(round($P * $DUTY / 100, 4))")
    sdc=$OUT/escapement_$tag.sdc
    sed "s/-period \$::env(CLOCK_PERIOD)\$/-period \$::env(CLOCK_PERIOD) -waveform {0 $hi}/" \
      "$REPO/src/escapement.sdc" > "$sdc"
    grep -q "waveform {0 $hi}" "$sdc" || { echo "duty cycle not applied to $sdc"; exit 1; }
  fi
  {
    echo "read_liberty $LIB/sg13cmos5l_stdcell_${STD[$c]}.lib"
    # every SRAM macro's liberty at the corner (v2: the 1024x32 store and the 256x32 tape);
    # one the netlist does not use is never linked
    for m in "$REPO"/macro/RM_IHPSG13_1P_*x32_c2_bm_bist; do
      echo "read_liberty $m/$(basename "$m")_${SRAM[$c]}.lib"
    done
    [[ -n "$ART" ]] && echo "read_liberty $ART"
    echo "read_verilog $RUN/final/nl/tt_um_escapement.nl.v"
    echo "link_design tt_um_escapement"
    echo "read_spef $RUN/final/spef/nom/tt_um_escapement.nom.spef"
    echo "read_sdc $sdc"
    echo "set_propagated_clock [all_clocks]"
    echo "puts SETUP; report_worst_slack -max -digits 3"
    echo "puts HOLD; report_worst_slack -min -digits 3"
    echo "report_checks -path_delay max -digits 3"
    echo "puts PULSE; report_check_types -min_pulse_width -violators -digits 3"
  } > "$tcl"
  docker run --rm -v "$RUN:$RUN" -v "$REPO:$REPO" -v "$PDK_ROOT:$PDK_ROOT" \
    -e CLOCK_PORT=clk -e CLOCK_PERIOD="$P" -e IO_DELAY_CONSTRAINT=20 \
    -e MAX_FANOUT_CONSTRAINT=10 -e CLOCK_UNCERTAINTY_CONSTRAINT=0.25 \
    -e CLOCK_TRANSITION_CONSTRAINT=0.15 -e TIME_DERATING_CONSTRAINT=5 \
    -e SYNTH_DRIVING_CELL=sg13cmos5l_buf_4/X -e OUTPUT_CAP_LOAD=6 \
    "$IMAGE" sta -no_splash -exit "$tcl" > "$OUT/sta_$tag.rpt" 2>&1 || true
  s=$(grep -A1 SETUP "$OUT/sta_$tag.rpt" | grep -oE 'worst slack (max )?[-0-9.]+' | awk '{print $NF}' || true)
  h=$(grep -A1 HOLD "$OUT/sta_$tag.rpt" | grep -oE 'worst slack (min )?[-0-9.]+' | awk '{print $NF}' || true)
  w=$(sed -n '/^PULSE/,$p' "$OUT/sta_$tag.rpt" | grep -c VIOLATED || true)
  echo "RESULT $c period $P ns duty $DUTY% setup ${s:-?} hold ${h:-?} pulse-width violators $w"
done
