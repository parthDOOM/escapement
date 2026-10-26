#!/usr/bin/env bash
# FPGA resource estimate of the whole chip: Yosys synthesis of tt_um_escapement with plain
# flip-flops (ESC_NO_CG: no clock gates, no latch banks) and fpga/escapement_sram_fpga.v in
# place of the IHP SRAM macro, for ECP5, iCE40 and Xilinx 7-series. Needs Yosys (OSS CAD Suite).
#
#   fpga/estimate.sh            # prints each family's cell statistics; logs in fpga/build/
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p fpga/build
SRC="src/project.v src/escapement_host.v src/esc_core.v src/esc_ctx.v src/esc_cap.v src/esc_line.v
     src/esc_greg.v src/escapement_art.v fpga/escapement_sram_fpga.v"
for fam in xilinx ecp5 ice40; do
  yosys -q -l "fpga/build/synth_$fam.log" \
    -p "read_verilog -DESC_NO_CG $SRC; synth_$fam -top tt_um_escapement; tee -o fpga/build/stat_$fam.txt stat"
  echo "== $fam (fpga/build/stat_$fam.txt)"
  grep -E "^ +[0-9]+ +(LUT|FD|TRELLIS_FF|SB_LUT4|SB_DFF|CCU2C|L6MUX21|SB_CARRY|CARRY4|RAMB|DP16KD|SB_RAM)" \
    "fpga/build/stat_$fam.txt" | tail -16
done
