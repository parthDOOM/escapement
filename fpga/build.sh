#!/usr/bin/env bash
# Bitstream of the whole chip for an FPGA board: fpga/escapement_board.v around
# tt_um_escapement, with plain flip-flops (ESC_NO_CG) and fpga/escapement_sram_fpga.v,
# synthesised, placed and routed with a 25 MHz clock constraint. Needs the OSS CAD Suite
# (yosys, nextpnr-himbaechel + gowin_pack, nextpnr-ecp5 + ecppack).
#
#   fpga/build.sh tangnano20k          # Sipeed Tang Nano 20K -> fpga/build/tangnano20k/escapement.fs
#   fpga/build.sh ulx3s [12k|25k|45k|85k]   # Radiona ULX3S (default 85k) -> .../escapement.bit
#
# Each run ends with nextpnr's utilisation and the clock's maximum frequency (its log is
# next to the bitstream). Load with openFPGALoader -b tangnano20k / -b ulx3s.
set -euo pipefail
cd "$(dirname "$0")/.."
board=${1:?board: tangnano20k or ulx3s}
out=fpga/build/$board
mkdir -p "$out"
SRC="src/project.v src/escapement_host.v src/esc_core.v src/esc_ctx.v src/esc_cap.v src/esc_line.v
     src/esc_greg.v src/escapement_art.v fpga/escapement_sram_fpga.v fpga/escapement_board.v"
SRC=$(echo $SRC)   # one line: a newline ends a yosys -p command
# The layout-art macro (src/escapement_art.v) is a blackbox of metal shapes: no FPGA cell.
DROP_ART="hierarchy -top escapement_board; delete tt_um_escapement/t:escapement_art"
case $board in
  tangnano20k)
    yosys -q -l "$out/synth.log" \
      -p "read_verilog -DESC_NO_CG $SRC; $DROP_ART; synth_gowin -family gw2a -top escapement_board -json $out/synth.json"
    nextpnr-himbaechel -q -l "$out/pnr.log" --json "$out/synth.json" --write "$out/pnr.json" \
      --device GW2AR-LV18QN88C8/I7 --vopt family=GW2A-18C --vopt cst=fpga/tangnano20k.cst --freq 25
    gowin_pack -c -d GW2A-18C -o "$out/escapement.fs" "$out/pnr.json"
    ;;
  ulx3s)
    size=${2:-85k}
    yosys -q -l "$out/synth.log" \
      -p "read_verilog -DESC_NO_CG $SRC; $DROP_ART; synth_ecp5 -top escapement_board -json $out/synth.json"
    nextpnr-ecp5 -q -l "$out/pnr.log" --json "$out/synth.json" --textcfg "$out/escapement.config" \
      --"$size" --package CABGA381 --lpf fpga/ulx3s.lpf
    ecppack --compress "$out/escapement.config" "$out/escapement.bit"
    ;;
  *) echo "unknown board: $board" >&2; exit 2 ;;
esac
echo "== $board ($out/pnr.log)"
grep -E "^Info:\s+\w+:\s+[0-9]+/\s*[0-9]+\s+[0-9]+%|Max frequency for clock" "$out/pnr.log" | grep -v " 0%$"
