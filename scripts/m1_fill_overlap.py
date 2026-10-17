"""Do signal Metal1 wires run over cells that the router never saw? (D-30 forensics)

    python3 scripts/m1_fill_overlap.py FILLED.def STDCELL.lef

LibreLane inserts filler and decap cells AFTER detailed routing. If signal routing is
allowed on Metal1, a wire laid over empty row space can end up on top of a decap whose
internal Metal1 (tied to VDD/VSS) the router never knew about. This counts signal-net
Metal1 segments per master of the cell underneath them.
"""

from __future__ import annotations

import collections
import re
import sys


def lef_sizes(path):
    sizes, cur = {}, None
    for line in open(path):
        t = line.split()
        if not t:
            continue
        if t[0] == "MACRO":
            cur = t[1]
        elif t[0] == "SIZE" and cur:
            sizes[cur] = (float(t[1]), float(t[3]))
    return sizes


def main():
    def_path, lef_path = sys.argv[1], sys.argv[2]
    sizes = lef_sizes(lef_path)
    text = open(def_path).read()
    dbu = int(re.search(r"UNITS DISTANCE MICRONS (\d+)", text).group(1))
    comps = text[text.index("\nCOMPONENTS"):text.index("END COMPONENTS")]
    cells = []                                 # (x0, y0, x1, y1, master)
    for m in re.finditer(r"- (\S+) (\S+) .*?\+ (?:PLACED|FIXED) \( (-?\d+) (-?\d+) \) (\w+)", comps):
        master = m.group(2)
        if master not in sizes:
            continue
        w, h = sizes[master]
        x, y = int(m.group(3)) / dbu, int(m.group(4)) / dbu
        if m.group(5) in ("E", "W", "FE", "FW"):
            w, h = h, w
        cells.append((x, y, x + w, y + h, master))
    # bucket cells by row (y) for fast lookup
    rows = collections.defaultdict(list)
    for c in cells:
        rows[round(c[1], 2)].append(c)
    nets = text[text.index("\nNETS"):text.index("END NETS")]
    hits = collections.Counter()
    nseg = 0
    for seg in re.finditer(r"(?:ROUTED|NEW) Metal1 (?:(\d+) )?\( (\S+) (\S+)(?: \S+)? \) \( (\S+) (\S+)(?: \S+)? \)", nets):
        x1, y1 = float(seg.group(2)) / dbu, float(seg.group(3)) / dbu
        x2 = x1 if seg.group(4) == "*" else float(seg.group(4)) / dbu
        y2 = y1 if seg.group(5) == "*" else float(seg.group(5)) / dbu
        nseg += 1
        lo, hi = min(x1, x2), max(x1, x2)
        for ry, lst in rows.items():
            if ry - 0.01 <= y1 <= ry + 3.79:
                for (cx0, cy0, cx1, cy1, master) in lst:
                    if cx0 < hi and lo < cx1 and cy0 < y1 < cy1:
                        hits[master] += 1
    print(f"signal Metal1 segments: {nseg}")
    for master, n in hits.most_common(20):
        print(f"{n:7d}  over {master}")


if __name__ == "__main__":
    main()
