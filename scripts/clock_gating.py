#!/usr/bin/env python3
"""Storage cells behind a clock gate in a final netlist (docs/15-POWER.md):

    python3 scripts/clock_gating.py runs/<tag>/final/nl/tt_um_escapement.nl.v

Each flip-flop's and latch's clock pin is traced back through clock buffers, delay cells and
inverters to the clock port (free-running) or to a clock gate's AND (gated). On v16s: 3,292
of 4,756 behind a gate, 927 of the 2,391 flip-flops (v15c: 3,151 of 4,402; 786 of 2,037)."""
import re, sys, collections
start = re.compile(r"^\s*(sg13cmos5l_\w+)\s+(\S+)\s*\(")
pinre = re.compile(r"\.(\w+)\(\s*([^()]*?)\s*\)")
cells, cur = [], None
for line in open(sys.argv[1]):
    m = start.match(line)
    if m:
        cur = [m.group(1), m.group(2), line[m.end():]]
    elif cur is not None:
        cur[2] += line
    if cur is not None and line.rstrip().endswith(");"):
        typ, name, body = cur
        cells.append((typ, name, dict(pinre.findall(body))))
        cur = None
OUTS = ("X", "Y", "Q", "Q_N", "GCLK")
drv = {}
for c in cells:
    for p in OUTS:
        if p in c[2]:
            drv[c[2][p]] = c
storage = [c for c in cells if re.match(r"sg13cmos5l_(df|dlh)", c[0])]
icg_latch = [c for c in cells if c[0].startswith("sg13cmos5l_dllrq")]
print("flip-flops", sum(c[0].startswith("sg13cmos5l_df") for c in storage),
      "latches", sum(c[0].startswith("sg13cmos5l_dlh") for c in storage), "clock-gate latches", len(icg_latch))

def root(net, depth=0):
    """'port', 'gate' (an AND in a clock gate), or what else drives the clock net."""
    if net in ("clk", "\\clk"):
        return "port"
    c = drv.get(net)
    if c is None:
        return "port" if "clk" in net and "clknet" not in net else f"undriven {net}"
    typ = c[0]
    if re.match(r"sg13cmos5l_(buf|clkbuf|dlygate|inv|clkinv)", typ):
        nxt = c[2].get("A")
        return root(nxt, depth + 1) if nxt else f"? {typ}"
    if typ.startswith("sg13cmos5l_and2"):
        return "gate"
    return f"other {typ}"

kinds = collections.Counter()
for c in storage:
    kind = "ff" if c[0].startswith("sg13cmos5l_df") else "latch"
    clkpin = c[2].get("CLK") or c[2].get("GATE")
    kinds[(kind, root(clkpin))] += 1
print(dict(kinds))
gated = sum(v for (k, r), v in kinds.items() if r == "gate")
print("behind a gate:", gated, "of", len(storage), "storage cells;",
      "flip-flops gated", kinds[("ff", "gate")], "free-running", kinds[("ff", "port")])
