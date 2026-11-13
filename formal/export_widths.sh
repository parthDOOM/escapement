#!/bin/bash
# The formal exports (f_core, f_lu, f_ctx_all, ...) are long concatenations that the formal
# environments unpack field by field. Verilog truncates or pads a mismatched assign or port
# without a word, so a field that grows shifts every field above it and the environment
# compares the wrong bits (v3: d_op grew 53 -> 55 bits for MON/GETM, f_core stayed 337 bits,
# and esc_ni_ind failed on misread fields). Verilator's width check sees it: fail on any
# width warning on an assign or a port. Run before the non-interference proofs (formal-v2).
cd "$(dirname "$0")/../src" || exit 2
out=$(verilator --lint-only -Wno-fatal -Wall -DFORMAL -DESC_NI -DESC_NI_IND --top-module esc_ni_env \
      esc_greg.v esc_ctx.v esc_cap.v esc_mon.v esc_line.v esc_core.v ../formal/esc_ni_env.v 2>&1)
# the only error expected is in formal-only code (a symbolic index Verilator cannot fold), which
# Verilator reports after its width pass; any other error means the check never ran
other=$(echo "$out" | grep -E '%Error' | grep -vE 'Selection index is constantly unknown|Exiting due to')
[ -z "$other" ] || { echo "export_widths: verilator did not get through"; echo "$other" | head -5; exit 2; }
bad=$(echo "$out" | grep -E '%Warning-WIDTH(TRUNC|EXPAND).*Operator (ASSIGNW|PIN) ')
if [ -n "$bad" ]; then
  echo "export_widths: FAIL"; echo "$bad"; exit 1
fi
echo "export_widths: PASS (no width mismatch on the formal exports)"
