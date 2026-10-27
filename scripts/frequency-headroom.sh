#!/usr/bin/env bash
# Frequency headroom of one harden run (docs/03-PHYSICAL-DESIGN.md). Inside WSL/Linux:
#
#   scripts/frequency-headroom.sh RUN_DIR [CORNER:PERIOD ...]
#
# RUN_DIR        a LibreLane run (runs/<tag>) with final/nl and final/spef
# CORNER:PERIOD  slow, typ or fast and a clock period in ns (default: the sweep behind the
#                table in docs/03, around each corner's limit)
#
# OpenSTA in the pinned LibreLane container reads the final netlist and its extracted
# parasitics with the sign-off constraints (src/escapement.sdc: latches modelled, time
# borrowing allowed), the period shortened and the 20% I/O delays scaled with it. Setup
# holds when the worst slack is >= 0; a latch input that borrows reports exactly 0 (printed
# as 0.000 or -0.000). On the v1.4 run (v14i) this gives the docs/03 table: setup holds at
# 35, 22 and 14.3 ns and fails at 34.5, 21.5 and 14 ns.
# Prints one RESULT line per corner and period; reports go to RUN_DIR/frequency/.
set -euo pipefail

RUN=$(realpath "$1"); shift
POINTS=("$@")
[[ ${#POINTS[@]} -eq 0 ]] && POINTS=(slow:36 slow:35.5 slow:35 slow:34.5
                                      typ:22.5 typ:22 typ:21.5 typ:21
                                      fast:15 fast:14.6 fast:14.3 fast:14)

REPO=$(realpath "$(dirname "$0")/..")
EDA="${EDA:-$HOME/eda}"
export PDK_ROOT="${PDK_ROOT:-$EDA/pdk-ihp-2bbec755dc67ca3db0261c3d6163e15735d66710}"
IMAGE=ghcr.io/librelane/librelane:3.1.0.dev3
LIB=$PDK_ROOT/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib
MAC=$REPO/macro/RM_IHPSG13_1P_256x32_c2_bm_bist
ART=$(ls "$REPO"/macro/escapement_art/lib/*.lib 2>/dev/null | head -n 1 || true)
OUT=$RUN/frequency
mkdir -p "$OUT"
declare -A STD=([slow]=slow_1p08V_125C [typ]=typ_1p20V_25C [fast]=fast_1p32V_m40C)
declare -A SRAM=([slow]=slow_1p08V_125C [typ]=typ_1p20V_25C [fast]=fast_1p32V_m55C)

for pt in "${POINTS[@]}"; do
  c=${pt%%:*} P=${pt#*:}
  tcl=$OUT/sta_${c}_$P.tcl
  {
    echo "read_liberty $LIB/sg13cmos5l_stdcell_${STD[$c]}.lib"
    echo "read_liberty $MAC/RM_IHPSG13_1P_256x32_c2_bm_bist_${SRAM[$c]}.lib"
    [[ -n "$ART" ]] && echo "read_liberty $ART"
    echo "read_verilog $RUN/final/nl/tt_um_escapement.nl.v"
    echo "link_design tt_um_escapement"
    echo "read_spef $RUN/final/spef/nom/tt_um_escapement.nom.spef"
    echo "read_sdc $REPO/src/escapement.sdc"
    echo "set_propagated_clock [all_clocks]"
    echo "puts SETUP; report_worst_slack -max -digits 3"
    echo "puts HOLD; report_worst_slack -min -digits 3"
    echo "report_checks -path_delay max -digits 3"
  } > "$tcl"
  docker run --rm -v "$RUN:$RUN" -v "$REPO:$REPO" -v "$PDK_ROOT:$PDK_ROOT" \
    -e CLOCK_PORT=clk -e CLOCK_PERIOD="$P" -e IO_DELAY_CONSTRAINT=20 \
    -e MAX_FANOUT_CONSTRAINT=10 -e CLOCK_UNCERTAINTY_CONSTRAINT=0.25 \
    -e CLOCK_TRANSITION_CONSTRAINT=0.15 -e TIME_DERATING_CONSTRAINT=5 \
    -e SYNTH_DRIVING_CELL=sg13cmos5l_buf_4/X -e OUTPUT_CAP_LOAD=6 \
    "$IMAGE" sta -no_splash -exit "$tcl" > "$OUT/sta_${c}_$P.rpt" 2>&1 || true
  s=$(grep -A1 SETUP "$OUT/sta_${c}_$P.rpt" | grep -oE 'worst slack (max )?[-0-9.]+' | awk '{print $NF}' || true)
  h=$(grep -A1 HOLD "$OUT/sta_${c}_$P.rpt" | grep -oE 'worst slack (min )?[-0-9.]+' | awk '{print $NF}' || true)
  echo "RESULT $c period $P ns setup ${s:-?} hold ${h:-?}"
done
