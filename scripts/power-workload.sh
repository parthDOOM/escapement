#!/usr/bin/env bash
# Workload power of one harden run (docs/15-POWER.md, D-29). Inside WSL/Linux:
#
#   scripts/power-workload.sh RUN_DIR [STEP_DIR] [WORKLOAD ...]
#
# RUN_DIR   a LibreLane run (runs/<tag>) whose netlist and database are measured
# STEP_DIR  step whose .nl.v/.odb to use (default: the post-CTS resizer step, so runs
#           stopped with --to OpenROAD.ResizerTimingPostCTS can be compared), or `final`
#           for the signed-off layout with its extracted parasitics
# WORKLOAD  uart_tx | logic_analyzer | idle (default: all three)
#
# For each workload: a gate-level cocotb simulation of the netlist (test/power.py) records
# a VCD of the design for the measurement window, then OpenSTA reads the run's database
# and the VCD and reports power at the typical corner (scripts/power_vcd.tcl). A static
# (default-activity) report is written too, for comparison with LibreLane's power.rpt.
# Power is reported at the 25 MHz rating (POWER_PERIOD=40 ns, the default), whatever period
# the layout was signed off at. Results: RUN_DIR/power/<workload>.rpt, and <workload>.sram
# with the macro energy counted edge by edge (scripts/sram_energy.py), which replaces the
# report's Macro row.
set -euo pipefail

RUN=$(realpath "$1"); shift
STEP=""
if [[ $# -gt 0 && -d "$RUN/$1" ]]; then STEP="$1"; shift; fi
[[ -z "$STEP" ]] && STEP=$(basename "$(ls -d "$RUN"/*-openroad-resizertimingpostcts | tail -n 1)")
# STEP final: the signed-off layout (final/: its netlist, database and constraints, and the
# typical-corner parasitics extracted from its routed wires); the flow settings from the
# last step that wrote them
if [[ "$STEP" == final ]]; then
  NL="$RUN/final/nl/tt_um_escapement.nl.v"; ODB="$RUN/final/odb/tt_um_escapement.odb"
  SDC="$RUN/final/sdc/tt_um_escapement.sdc"
  SPEF="$RUN/final/spef/nom/tt_um_escapement.nom.spef"
  ENV_TCL=$(ls "$RUN"/*/_env.tcl | sort -V | tail -n 1)
else
  NL="$RUN/$STEP/tt_um_escapement.nl.v"; ODB="$RUN/$STEP/tt_um_escapement.odb"
  SDC="$RUN/$STEP/tt_um_escapement.sdc"; SPEF=""; ENV_TCL="$RUN/$STEP/_env.tcl"
fi
for f in "$NL" "$ODB" "$SDC" "$ENV_TCL" ${SPEF:+"$SPEF"}; do
  [[ -f "$f" ]] || { echo "missing $f" >&2; exit 2; }
done
WORKLOADS=("$@")
[[ ${#WORKLOADS[@]} -eq 0 ]] && WORKLOADS=(uart_tx logic_analyzer idle)

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
# LibreLane's own OpenROAD scripts (io.tcl, set_rc.tcl), wherever the container keeps them
LL=$(docker run --rm "$IMAGE" python3 -c \
  'import librelane, os; print(os.path.join(os.path.dirname(librelane.__file__), "scripts"))')
OUT="$RUN/power"
mkdir -p "$OUT"
# shellcheck disable=SC1091
source "$EDA/env.sh"

# A private copy of the test bench so concurrent runs do not share sim_build.
SIM=$(mktemp -d)
cp -r "$REPO/test" "$SIM/test"
ln -s "$REPO/src" "$SIM/src"; ln -s "$REPO/macro" "$SIM/macro"
ln -s "$REPO/sw" "$SIM/sw"; ln -s "$REPO/firmware" "$SIM/firmware"
cp "$NL" "$SIM/test/gate_level_netlist.v"

pw() {   # $1 = VCD path or "" ; $2 = report
  docker run --rm -v "$HOME:$HOME" -v /tmp:/tmp -v "$REPO:$REPO" -v "$PDK_ROOT:$PDK_ROOT" \
    -e SCRIPTS_DIR="$LL" -e _TCL_ENV_IN="$ENV_TCL" \
    -e PDK_LIB="$PDK_ROOT/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib" \
    -e MACRO_DIR="$REPO/macro" -e ODB="$ODB" -e SPEF="$SPEF" \
    -e SDC="$SDC" -e SET_RC="$LL/openroad/common/set_rc.tcl" \
    -e VCD="$1" -e OUT="$2" -e POWER_PERIOD="${POWER_PERIOD:-40}" "$IMAGE" \
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
