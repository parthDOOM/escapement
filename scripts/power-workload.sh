#!/usr/bin/env bash
# Workload power of one harden run (docs/15-POWER.md, D-29). Inside WSL/Linux:
#
#   scripts/power-workload.sh RUN_DIR [STEP_DIR] [WORKLOAD ...]
#
# RUN_DIR   a LibreLane run (runs/<tag>) whose netlist and database are measured
# STEP_DIR  step whose .nl.v/.odb to use (default: the post-CTS resizer step, so runs
#           stopped with --to OpenROAD.ResizerTimingPostCTS can be compared)
# WORKLOAD  uart_tx | logic_analyzer | idle (default: all three)
#
# For each workload: a gate-level cocotb simulation of the netlist (test/power.py) records
# a VCD of the design for the measurement window, then OpenSTA reads the run's database
# and the VCD and reports power at the typical corner (scripts/power_vcd.tcl). A static
# (default-activity) report is written too, for comparison with LibreLane's power.rpt.
# Results: RUN_DIR/power/<workload>.rpt, and <workload>.sram with the macro energy counted
# edge by edge (scripts/sram_energy.py), which replaces the report's Macro row.
set -euo pipefail

RUN=$(realpath "$1"); shift
STEP=""
if [[ $# -gt 0 && -d "$RUN/$1" ]]; then STEP="$1"; shift; fi
[[ -z "$STEP" ]] && STEP=$(basename "$(ls -d "$RUN"/*-openroad-resizertimingpostcts | tail -n 1)")
WORKLOADS=("$@")
[[ ${#WORKLOADS[@]} -eq 0 ]] && WORKLOADS=(uart_tx logic_analyzer idle)

REPO=$(realpath "$(dirname "$0")/..")
EDA="${EDA:-$HOME/eda}"
export PDK_ROOT="${PDK_ROOT:-$EDA/pdk-ihp-2bbec755dc67ca3db0261c3d6163e15735d66710}"
IMAGE=ghcr.io/librelane/librelane:3.1.0.dev3
LL=/nix/store/6a08ghhhzl5a07nr0ly762yh1ah6m5bj-python3-3.13.13-env/lib/python3.13/site-packages/librelane/scripts
OUT="$RUN/power"
mkdir -p "$OUT"
# shellcheck disable=SC1091
source "$EDA/env.sh"

# A private copy of the test bench so concurrent runs do not share sim_build.
SIM=$(mktemp -d)
cp -r "$REPO/test" "$SIM/test"
ln -s "$REPO/src" "$SIM/src"; ln -s "$REPO/macro" "$SIM/macro"
ln -s "$REPO/sw" "$SIM/sw"; ln -s "$REPO/firmware" "$SIM/firmware"
cp "$RUN/$STEP/tt_um_escapement.nl.v" "$SIM/test/gate_level_netlist.v"

pw() {   # $1 = VCD path or "" ; $2 = report
  docker run --rm -v "$HOME:$HOME" -v /tmp:/tmp -v "$REPO:$REPO" \
    -e SCRIPTS_DIR="$LL" -e _TCL_ENV_IN="$RUN/$STEP/_env.tcl" \
    -e PDK_LIB="$PDK_ROOT/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib" \
    -e MACRO_DIR="$REPO/macro" -e ODB="$RUN/$STEP/tt_um_escapement.odb" \
    -e SDC="$RUN/$STEP/tt_um_escapement.sdc" -e SET_RC="$LL/openroad/common/set_rc.tcl" \
    -e VCD="$1" -e OUT="$2" "$IMAGE" \
    openroad -exit -no_splash "$REPO/scripts/power_vcd.tcl" > "$2.log" 2>&1
}

pw "" "$OUT/static.rpt"
for w in "${WORKLOADS[@]}"; do
  ( cd "$SIM/test" && rm -rf sim_build results.xml power.vcd &&
    make GATES=yes FST= COMPILE_ARGS=-DPOWER_VCD COCOTB_TEST_MODULES=power POWER_WORKLOAD="$w" \
      > "$OUT/$w.sim.log" 2>&1 )
  grep -q "PASS=1" "$OUT/$w.sim.log" || { echo "simulation failed: $OUT/$w.sim.log"; exit 1; }
  cp "$SIM/test/power.vcd" "/tmp/power_$$_$w.vcd"
  pw "/tmp/power_$$_$w.vcd" "$OUT/$w.rpt"
  # OpenSTA misprices the macros once A_MEN and A_REN are correlated; count them exactly.
  python3 "$REPO/scripts/sram_energy.py" "/tmp/power_$$_$w.vcd" > "$OUT/$w.sram"
  rm -f "/tmp/power_$$_$w.vcd"
  echo "== $w"; sed -n '/^Group/,/^Total/p' "$OUT/$w.rpt" | head -9; cat "$OUT/$w.sram"
done
rm -rf "$SIM"
