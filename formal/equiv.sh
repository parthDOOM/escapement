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
# Two steps since v1.6. Synthesis recodes the line unit's three small state machines (sk,
# noted, es0_q in esc_line.v) one-hot, and with v1.6's extra registers signal correspondence
# no longer closes that on the whole chip. So: (1) esc_line alone equals esc_line after
# synthesis's FSM pass (yosys opt; fsm; opt), proved at module level with the same miter;
# (2) the netlist equals the RTL with that esc_line in place. A module equivalent at every
# output from reset changes nothing around it, so together the netlist equals the RTL.
#
# The reset is cut at the synchroniser (3.1, D-63, D-68). Every register's asynchronous
# reset comes from rst_n_s, the output of the synchroniser's second flip-flop, and with that
# net inside the miter signal correspondence matched almost none of the registers: the 3.1
# netlist ended undecided after 2 h 21 min. So in both designs that flip-flop drives a new
# output, rst_n_s_drv, which is compared like any chip output, and rst_n_s, which resets
# everything else, becomes a shared free input, held low during the miter's reset like
# rst_n. The synchroniser depends only on rst_n, never on rst_n_s, and the free input can do
# everything the real net can and more, so the cut loses nothing; the 3.1 netlist proves in
# under five minutes, and a one-flop synchroniser planted in it fails. The cut is made on copies
# of project.v and of the netlist (cut_rtl, cut_net below), at the flip-flop itself: yosys's
# expose -cut on the rst_n_s wire follows the wire's aliases (`wire rst_n_s = rst_q[1]`, the
# netlist's reset buffers) and cut the compared output off from the flip-flop too.
#
# Usage: formal/equiv.sh [NETLIST] [OUT_DIR]
#   NETLIST  default test/gate_level_netlist.v (the tt_submission netlist, as for gl-test);
#            a directory instead is read as a second RTL tree (the mutation campaign uses
#            this to prove that a mutant no test kills changes no output); RTL against RTL
#            is one direct proof, without step (1)
#   OUT_DIR  default formal/equiv_build (step 1 in OUT_DIR/line)
# Needs yosys and yosys-abc (OSS CAD Suite) and PDK_ROOT pointing at the IHP PDK.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
NET=$(realpath "${1:-$ROOT/test/gate_level_netlist.v}")
OUT=${2:-$ROOT/formal/equiv_build}
[ -n "${LIB:-}" ] || [ -n "${PDK_ROOT:-}" ] || { echo "equiv.sh: set PDK_ROOT (or LIB to the stdcell liberty)" >&2; exit 2; }
[ -e "$NET" ] || { echo "equiv.sh: no netlist at $NET" >&2; exit 2; }
LIB=${LIB:-$PDK_ROOT/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib}
SRC=$ROOT/src
RTL="$SRC/project.v $SRC/escapement_host.v $SRC/escapement_sram.v $SRC/escapement_sram_big.v $SRC/escapement_pstore.v $SRC/esc_core.v $SRC/esc_ctx.v $SRC/esc_cap.v $SRC/esc_mon.v $SRC/esc_line.v $SRC/esc_greg.v"
# every program-store build's macro (v2: the build is project.v's STORE_MACROS; 11-12: the 512x32 and
# 1024x32 were missing, so build 1 did not elaborate here)
STUB="$SRC/RM_IHPSG13_1P_256x32_c2_bm_bist.v $SRC/RM_IHPSG13_1P_512x32_c2_bm_bist.v $SRC/RM_IHPSG13_1P_1024x32_c2_bm_bist.v $SRC/escapement_art.v"
PREP='hierarchy -top tt_um_escapement; proc; flatten; expose w:rst_n_s_drv; expose -input w:rst_n_s;
memory_map; opt_clean; delete t:escapement_art; expose -evert t:RM_IHPSG13_1P_*_c2_bm_bist; opt_clean'
mkdir -p "$OUT"; OUT=$(realpath "$OUT")

# cut_rtl SRC DST: DST/project.v is SRC/project.v with the synchroniser's output split in two,
# rst_n_s_drv driven by the flip-flop and rst_n_s left undriven
cut_rtl() {
  mkdir -p "$2"
  python3 - "$1/project.v" "$2/project.v" <<'EOF'
import sys
s = open(sys.argv[1]).read()
a = "wire rst_n_s = rst_q[1];"
assert s.count(a) == 1, f"equiv.sh: '{a}' is not in {sys.argv[1]} exactly once"
open(sys.argv[2], "w").write(s.replace(a, "wire rst_n_s; wire rst_n_s_drv = rst_q[1];"))
EOF
}
# cut_net NETLIST DST: the flip-flop whose Q is rst_n_s drives rst_n_s_drv instead
cut_net() {
  python3 - "$1" "$2" <<'EOF'
import sys
s = open(sys.argv[1]).read()
q, decl = ".Q(rst_n_s)", "\n wire rst_n_s;\n"
assert s.count(q) == 1, f"equiv.sh: {s.count(q)} cells drive rst_n_s from a Q pin in {sys.argv[1]}, not 1"
assert s.count(decl) == 1, f"equiv.sh: 'wire rst_n_s;' is not declared once in {sys.argv[1]}"
s = s.replace(q, ".Q(rst_n_s_drv)").replace(decl, decl + " wire rst_n_s_drv;\n")
open(sys.argv[2], "w").write(s)
EOF
}
cut_rtl "$SRC" "$OUT/cut/gold"
RTL_CUT=${RTL//$SRC\/project.v/$OUT/cut/gold/project.v}

# prove DIR: the miter of DIR/gold.il and DIR/gate.il (modules gold and gate, same ports), then
# dprove. The reset (rst_n, and the cut rst_n_s) is held for the first three steps and
# outputs are compared from the fifth on (a register the netlist stores inverted only matches
# its RTL twin once reset has been seen). The clock toggles every step; reset and all other
# inputs are free afterwards.
prove() {
  cd "$1"
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
        if n == "clk":
            src = "clk_r"
        elif n in ("rst_n", "rst_n_s"):       # the pin and the cut synchroniser output
            src = "(" + esc(n) + "& ~in_reset)"
        else:
            src = esc(n)
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
  grep -q "Networks are equivalent" dprove.log
}

# The RTL is read with the standard-cell clock gate (esc_icg's latch and AND cells), as hardened.
READ_RTL="read_liberty -ignore_miss_func -ignore_miss_dir $LIB
read_verilog -lib $STUB"

if [ -d "$NET" ]; then
  cut_rtl "$NET" "$OUT/cut/gate"
  GATE_RTL=${RTL//$SRC/$NET}
  GOLD_RTL="read_verilog -DSCL_sg13cmos5l_stdcell $RTL_CUT"
  READ_GATE="read_verilog -DSCL_sg13cmos5l_stdcell ${GATE_RTL//$NET\/project.v/$OUT/cut/gate/project.v}"
else
  # Step 1: esc_line against esc_line after synthesis's FSM pass (the passes before it in
  # yosys synth that the FSM extraction needs, then fsm, then opt).
  mkdir -p "$OUT/line"; cd "$OUT/line"
  LINE="read_liberty -ignore_miss_func -ignore_miss_dir $LIB
read_verilog -DSCL_sg13cmos5l_stdcell $SRC/esc_line.v $SRC/esc_greg.v
hierarchy -top esc_line; proc; flatten"
  yosys -q -l gold.log -p "$LINE; memory_map; opt_clean; rename esc_line gold; write_rtlil gold.il"
  yosys -q -l gate.log -p "$LINE; opt_expr; opt_clean; opt -nodffe -nosdff; fsm; opt
memory_map; opt_clean; rename esc_line gate; write_rtlil gate.il"
  echo "step 1: esc_line against its FSM pass ($(grep -c 'Recoding FSM' gate.log) state machines recoded)"
  if prove "$OUT/line"; then
    echo "PASS: esc_line after the FSM pass is sequentially equivalent to esc_line"
  else
    echo "FAIL: see $OUT/line/dprove.log"; exit 1
  fi
  echo "step 2: the netlist against the RTL with that esc_line"
  GOLD_RTL="read_rtlil $OUT/line/gate.il
rename gate esc_line
read_verilog -DSCL_sg13cmos5l_stdcell ${RTL_CUT//$SRC\/esc_line.v/}"
  cut_net "$NET" "$OUT/cut/netlist.v"
  READ_GATE="read_verilog $OUT/cut/netlist.v"
fi

cd "$OUT"
yosys -q -l gold.log -p "
$READ_RTL
$GOLD_RTL
$PREP
rename tt_um_escapement gold
write_rtlil gold.il"

yosys -q -l gate.log -p "
$READ_RTL
$READ_GATE
$PREP
rename tt_um_escapement gate
write_rtlil gate.il"

if prove "$OUT"; then
  echo "PASS: $(basename "$NET") is sequentially equivalent to the RTL"
else
  echo "FAIL: see $OUT/dprove.log"; exit 1
fi
