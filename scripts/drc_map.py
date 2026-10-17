"""Where are the detailed-routing DRC violations? (OpenROAD .drc / KLayout-style text)

    python3 scripts/drc_map.py runs/TAG/44-openroad-detailedrouting/reports/drt.drc

Groups violations by type and layer and prints them on a coarse map of the die, with the
two SRAM macro outlines given for orientation (src/config.json).
"""

from __future__ import annotations

import collections
import re
import sys

W, H, NX, NY = 1289.28, 710.64, 12, 7


def main() -> None:
    text = open(sys.argv[1]).read()
    items = re.findall(
        r"violation type: ([^\n]+)\n\s*srcs: ([^\n]*)\n(?:\s*comment: [^\n]*\n)?\s*bbox = "
        r"\(([\d.]+), ([\d.]+)\) - \(([\d.]+), ([\d.]+)\) on Layer (\S+)", text)
    print(f"{len(items)} violations")
    kinds = collections.Counter((t.strip(), layer) for t, _, _, _, _, _, layer in items)
    for (t, layer), n in kinds.most_common():
        print(f"{n:5d}  {t}  on {layer}")
    grid = [[0] * NX for _ in range(NY)]
    for _, _, x0, y0, x1, y1, _ in items:
        x, y = (float(x0) + float(x1)) / 2, (float(y0) + float(y1)) / 2
        grid[min(int(y / H * NY), NY - 1)][min(int(x / W * NX), NX - 1)] += 1
    print("map (rows top -> bottom, ~107 x 101 um cells):")
    for j in range(NY - 1, -1, -1):
        print(f"y{int(j * H / NY):4d} " + " ".join(f"{v:3d}" for v in grid[j]))
    nets = collections.Counter()
    for _, srcs, *_ in items:
        for n in re.findall(r"net:(\S+)", srcs):
            nets[n] += 1
    print("most involved nets:", ", ".join(f"{n} ({c})" for n, c in nets.most_common(12)))


if __name__ == "__main__":
    main()
