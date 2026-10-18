#!/usr/bin/env bash
# Reports which parts of the toolchain are present. See docs/05-TOOLCHAIN.md.
set -u

ok=0; missing=0
check() {
  printf '  %-12s ' "$1"
  if command -v "$1" >/dev/null 2>&1; then
    printf 'OK   %s\n' "$(command -v "$1")"; ok=$((ok+1))
  else
    printf 'MISSING  (%s)\n' "$2"; missing=$((missing+1))
  fi
}

echo "Core:"
check git      "version control"
check python3  "ISS, assembler, checker, cocotb (3.11)"
check make     "build glue"
check docker   "Tiny Tapeout flow"

echo "RTL simulation and synthesis (OSS CAD Suite):"
check yosys     "synthesis, area tracking"
check iverilog  "cocotb backend"
check verilator "fast simulation"
check sby       "formal properties"
check yosys-abc "RTL to netlist equivalence (make equiv)"
check nextpnr-ice40 "optional FPGA validation"

echo "Physical flow (pinned in scripts/versions.txt):"
check librelane "LibreLane 3.1.0.dev3 (pip, Python 3.11)"
check klayout   "DRC / precheck"
check magic     "LVS extraction; >= 8.3.657 for CMOS5L"

echo
echo "$ok present, $missing missing."
[ "$missing" -gt 0 ] && echo "See docs/05-TOOLCHAIN.md — WSL2 + OSS CAD Suite is the recommended fix."
exit 0
