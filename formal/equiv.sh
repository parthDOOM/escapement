#!/usr/bin/env bash
# Sequential equivalence of the RTL and a gate-level netlist (make equiv).
#
# Both designs are flattened, their SRAM macros cut out (the macro's outputs become shared
# free inputs, its inputs become compared outputs), and placed side by side in a miter that
# drives them from the same pins and the same free-running clock. After a reset the miter
# compares every chip output and every SRAM input on every step; ABC's dprove then proves
# that no input sequence ever makes them differ (bounded search, then signal correspondence
# and k-induction). Register-to-register matching is found by ABC, not by name, so the
# netlist's renamed, duplicated and inverted registers need no mapping file.
#
# Usage: formal/equiv.sh [NETLIST] [OUT_DIR]
#   NETLIST  default test/gate_level_netlist.v (the tt_submission netlist, as for gl-test)
#   OUT_DIR  default formal/equiv_build
# Needs yosys and yosys-abc (OSS CAD Suite) and PDK_ROOT pointing at the IHP PDK.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
NET=$(realpath "${1:-$ROOT/test/gate_level_netlist.v}")
OUT=${2:-$ROOT/formal/equiv_build}
[ -n "${LIB:-}" ] || [ -n "${PDK_ROOT:-}" ] || { echo "equiv.sh: set PDK_ROOT (or LIB to the stdcell liberty)" >&2; exit 2; }
[ -f "$NET" ] || { echo "equiv.sh: no netlist at $NET" >&2; exit 2; }
LIB=${LIB:-$PDK_ROOT/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib}
SRC=$ROOT/src
RTL="$SRC/project.v $SRC/escapement_host.v $SRC/escapement_sram.v $SRC/esc_core.v $SRC/esc_ctx.v $SRC/esc_cap.v $SRC/esc_greg.v"
STUB=$SRC/RM_IHPSG13_1P_256x32_c2_bm_bist.v
PREP='hierarchy -top tt_um_escapement; proc; flatten; memory_map; opt_clean;
expose -evert t:RM_IHPSG13_1P_256x32_c2_bm_bist; opt_clean'
mkdir -p "$OUT"; cd "$OUT"

# The RTL is read with the standard-cell clock gate (esc_icg's latch and AND cells), as hardened.
yosys -q -l gold.log -p "
read_liberty -ignore_miss_func -ignore_miss_dir $LIB
read_verilog -lib $STUB
read_verilog -DSCL_sg13cmos5l_stdcell $RTL
$PREP
rename tt_um_escapement gold
write_rtlil gold.il"

yosys -q -l gate.log -p "
read_liberty -ignore_miss_func -ignore_miss_dir $LIB
read_verilog -lib $STUB
read_verilog $NET
$PREP
rename tt_um_escapement gate
write_rtlil gate.il"

# The miter: reset held for the first three steps, outputs compared from the fifth on (a
# register the netlist stores inverted only matches its RTL twin once reset has been seen).
# The clock toggles every step; reset and all other inputs are free afterwards.
python3 - <<'EOF'
import re
def ports(path, top):
    text = open(path).read()
    body = text[text.index(f"module \\{top}\n"):]          # the top module only, not the
    body = body[:body.index("\nend\n")]                     # SRAM blackbox's definition
    return [(m.group(3), int(m.group(1) or 1), m.group(2)) for m in
            re.finditer(r"^\s*wire (?:width (\d+) )?(input|output) \d+ \\(\S+)$", body, re.M)]
g, t = ports("gold.il", "gold"), ports("gate.il", "gate")
assert sorted(g) == sorted(t), set(g) ^ set(t)
ins = [p for p in g if p[2] == "input"]
outs = [p for p in g if p[2] == "output"]
esc = lambda n: "\\" + n + " "
L = ["module top("]
L += [f"  input wire [{w-1}:0] {esc(n)}," for n, w, _ in ins]
L += ["  output wire bad);",
      "  reg [2:0] steps = 3'd0;",
      "  always @($global_clock) if (steps != 3'd7) steps <= steps + 3'd1;",
      "  reg clk_r = 1'b0;",
      "  always @($global_clock) clk_r <= ~clk_r;",
      "  wire in_reset = (steps < 3'd3);",
      "  wire compare = (steps > 3'd3);"]
for n, w, _ in outs:
    L += [f"  wire [{w-1}:0] {esc('g.' + n)}, {esc('t.' + n)};"]
def conns(pfx):
    c = []
    for n, w, _ in ins:
        src = {"rst_n": "(" + esc("rst_n") + "& ~in_reset)", "clk": "clk_r"}.get(n, esc(n))
        c.append(f"    .{esc(n)}({src})")
    for n, w, _ in outs:
        c.append(f"    .{esc(n)}({esc(pfx + n)})")
    return ",\n".join(c)
L += ["  gold u_gold (", conns("g."), "  );", "  gate u_gate (", conns("t."), "  );"]
diff = " | ".join(f"({esc('g.' + n)} != {esc('t.' + n)})" for n, w, _ in outs)
L += [f"  assign bad = compare & ({diff});", "endmodule"]
open("miter.v", "w").write("\n".join(L) + "\n")
bits = sum(w for n, w, _ in outs)
print(f"miter: {len(outs)} outputs ({bits} bits) compared")
EOF

yosys -q -l aig.log -p "
read_rtlil gold.il
read_rtlil gate.il
read_verilog -formal miter.v
hierarchy -top top
proc; flatten
clk2fflogic
setundef -undriven -anyseq
setundef -zero -init
opt -fast; techmap; opt -fast
dffunmap
abc -g AND; aigmap; opt_clean
write_aiger -zinit miter.aig" 2>/dev/null

yosys-abc -c "read_aiger miter.aig; print_stats; dprove -v" | tee dprove.log
if grep -q "Networks are equivalent" dprove.log; then
  echo "PASS: $(basename "$NET") is sequentially equivalent to the RTL"
else
  echo "FAIL: see $OUT/dprove.log"; exit 1
fi
