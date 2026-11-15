#!/bin/bash
# Block-level view of the FPGA build: the chip synthesised for the GW5A-25 with its hierarchy kept
# at the same blocks as the silicon floorplan (host, core, contexts, capture, monitor, line,
# program store, SRAMs), then each block's LUTs, flip-flops and block RAM, and the buses between
# blocks, as JSON.
#
#   fpga/tangprimer25k/hierarchy.sh [out.json]     # needs yosys and python3 on PATH
#
# The counts differ slightly from the flattened bitstream build (no optimisation across the
# kept boundaries); the bitstream's own totals are in results/*/timing.txt.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
out=${1:-$here/hierarchy.json}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$repo"
SRC="src/project.v src/escapement_host.v src/escapement_pstore.v src/esc_core.v src/esc_ctx.v src/esc_cap.v src/esc_mon.v src/esc_line.v
     src/esc_greg.v src/escapement_art.v fpga/escapement_sram_fpga.v fpga/escapement_board.v"
# The blocks to keep. A module with parameters is renamed '$paramod...\name' when the hierarchy
# is elaborated (the copy does not inherit an attribute set before), so each name is also
# matched in that form; the form a module does not have matches nothing (-w keeps that quiet).
KEEP=""
for m in tt_um_escapement escapement_host esc_core esc_ctx esc_cap esc_mon esc_line escapement_pstore \
         escapement_sram escapement_sram512 escapement_sram1024; do
  KEEP="$KEEP $m \$paramod*$m*"
done
yosys -q -w "did not match any module" -l "$work/synth.log" -p "
  read_verilog -DESC_NO_CG $(echo $SRC)
  hierarchy -top escapement_board
  delete tt_um_escapement/t:escapement_art
  setattr -mod -set keep_hierarchy 1 $KEEP
  synth_gowin -family gw5a -top escapement_board -json $work/blocks.json"
python3 "$here/hierarchy.py" "$work/blocks.json" "$out"
