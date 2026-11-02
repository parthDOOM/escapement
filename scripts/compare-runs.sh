#!/usr/bin/env bash
# Two harden runs compared, for the reproduction check in docs/03-PHYSICAL-DESIGN.md:
#
#   scripts/compare-runs.sh RUN_A RUN_B
#
# RUN_A, RUN_B  LibreLane runs (runs/<tag>) with final/nl, final/gds and final/metrics.json
#
# Prints the sha256 of each final netlist and GDS, every flow metric that differs (runtimes
# excluded), and whether the two GDS files hold the same geometry, cell by cell and layer
# by layer (KLayout's LayoutDiff; a GDS file's own sha256 differs between runs because the
# writer stamps the time into it). Needs python3 with the klayout package
# (pip install klayout). Exits 0 only if the netlists, the metrics and the geometry all match.
set -euo pipefail

A=$(realpath "$1")/final B=$(realpath "$2")/final
sha256sum "$A/nl/tt_um_escapement.nl.v" "$B/nl/tt_um_escapement.nl.v" "$A"/gds/*.gds "$B"/gds/*.gds
python3 - "$A" "$B" <<'PY'
import hashlib, json, sys
from pathlib import Path
import klayout.db as db

a, b = map(Path, sys.argv[1:3])
nl = [hashlib.sha256((p / "nl/tt_um_escapement.nl.v").read_bytes()).hexdigest() for p in (a, b)]
ma, mb = (json.loads((p / "metrics.json").read_text()) for p in (a, b))
diff = [k for k in sorted(set(ma) | set(mb)) if ma.get(k) != mb.get(k) and "runtime" not in k.lower()]
print("netlists identical:", nl[0] == nl[1])
print("metrics differing (runtimes excluded):", len(diff))
for k in diff[:20]:
    print(" ", k, ma.get(k), "->", mb.get(k))
la, lb = db.Layout(), db.Layout()
la.read(str(a / "gds/tt_um_escapement.gds"))
lb.read(str(b / "gds/tt_um_escapement.gds"))
n = [0]
d = db.LayoutDiff()
for ev in ("polygon", "box", "path", "text", "cell", "instance", "layer"):
    for side in ("a", "b"):
        getattr(d, f"on_{ev}_in_{side}_only").add(lambda *_: n.__setitem__(0, n[0] + 1))
same = d.compare(la, lb, db.LayoutDiff.SmartCellMapping)
print("GDS geometry identical:", same, "differences:", n[0])
sys.exit(0 if nl[0] == nl[1] and not diff and same else 1)
PY
