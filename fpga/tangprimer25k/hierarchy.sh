#!/bin/bash
# Block-level view of the FPGA build: the chip synthesised for the GW5A-25 with its hierarchy kept
# at the same blocks as the silicon floorplan (host, core, contexts, capture, line, SRAMs), then
# each block's LUTs, flip-flops and block RAM, and the buses between blocks, as JSON.
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
SRC="src/project.v src/escapement_host.v src/esc_core.v src/esc_ctx.v src/esc_cap.v src/esc_line.v
     src/esc_greg.v src/escapement_art.v fpga/escapement_sram_fpga.v fpga/escapement_board.v"
yosys -q -l "$work/synth.log" -p "
  read_verilog -DESC_NO_CG $(echo $SRC)
  hierarchy -top escapement_board
  delete tt_um_escapement/t:escapement_art
  setattr -mod -set keep_hierarchy 1 escapement_host esc_core esc_ctx esc_cap esc_line escapement_sram tt_um_escapement
  synth_gowin -family gw5a -top escapement_board -json $work/blocks.json"
python3 "$here/hierarchy.py" "$work/blocks.json" "$out"
