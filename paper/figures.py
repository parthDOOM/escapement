"""Raster figures for the paper, from the final GDS and the site's floorplan data.

    python paper/figures.py GDS            # needs klayout, numpy, Pillow, matplotlib

Writes into paper/fig/:
  die.jpg        the whole die, every layer from active to Metal4 (site/tools/die_assets.py's
                 colours), with the layout-art cell removed
  die_zoom.jpg   a 48 x 30 um window of standard cells in the shared pipeline
  blocks.pdf     the block map: each 5 um bin coloured by the part of the design with the
                 most cell area in it (site/public/data/floorplan.json), macros outlined
The drawn figures (architecture, admission, barrel) are TikZ in escapement.tex.
"""
import base64
import json
import sys
from pathlib import Path

import klayout.db as db
import matplotlib
import numpy as np
from PIL import Image

matplotlib.use("Agg")
import matplotlib.pyplot as plt                     # noqa: E402
from matplotlib.patches import Patch, Rectangle     # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "site" / "tools"))
import die_assets as da                             # noqa: E402

OUT = HERE / "fig"


def main(gds):
    OUT.mkdir(exist_ok=True)
    path, die, macros = da.strip_hidden(gds)
    m = da.Masks(path)

    w = 2000                                         # 285 dpi across the 7 in page width
    h = round(w * die.height() / die.width())
    Image.fromarray(da.composite(m, die, w, h)).save(OUT / "die.jpg", quality=90, subsampling=0)
    print("die.jpg", w, "x", h)

    fp = json.loads((ROOT / "site" / "public" / "data" / "floorplan.json").read_text())
    gw, gh, b = fp["w"], fp["h"], fp["bin_um"]
    core = np.frombuffer(base64.b64decode(fp["dens"]["core"]), np.uint8).reshape(gh, gw)
    # the densest 10 x 6 bin window of the shared pipeline
    best, at = -1, (0, 0)
    for y in range(0, gh - 6):
        for x in range(0, gw - 10):
            s = int(core[y:y + 6, x:x + 10].sum())
            if s > best:
                best, at = s, (x, y)
    x0, y0 = die.left + at[0] * b, die.bottom + at[1] * b
    box = db.DBox(x0, y0, x0 + 48, y0 + 30)
    Image.fromarray(da.composite(m, box, 1600, 1000)).save(OUT / "die_zoom.jpg", quality=90, subsampling=0)
    print("die_zoom.jpg at", round(x0, 1), round(y0, 1), "um")

    grid = np.frombuffer(base64.b64decode(fp["grid"]), np.uint8).reshape(gh, gw)
    blocks = fp["blocks"]
    rgb = np.zeros((gh, gw, 3))
    rgb[:] = 0.93
    for i, bl in enumerate(blocks, 1):
        c = tuple(int(bl["color"][k:k + 2], 16) / 255 for k in (1, 3, 5))
        rgb[grid == i] = c
    W, H = fp["die_um"]
    # drawn at the printed size (one column, 3.45 in), so the text is not scaled down
    fig, ax = plt.subplots(figsize=(3.45, 2.55))
    ax.imshow(rgb, origin="lower", extent=(0, gw * b, 0, gh * b), interpolation="nearest")
    for mc in macros:
        l, bt, r, t = mc["box"]
        ax.add_patch(Rectangle((l - die.left, bt - die.bottom), r - l, t - bt, fill=True,
                               facecolor="#d9d9d9", edgecolor="#333333", lw=0.6))
        big = "1024" in mc["name"]
        name = "program store" + chr(10) + "1024 x 32" if big else "tape" + chr(10) + "256 x 32"
        ax.text((l + r) / 2 - die.left, (bt + t) / 2 - die.bottom, name, ha="center",
                va="center", fontsize=6)
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.set_xlabel("x (µm)", fontsize=6.5, labelpad=1)
    ax.set_ylabel("y (µm)", fontsize=6.5, labelpad=1)
    ax.tick_params(labelsize=6, length=2, pad=1)
    handles = [Patch(facecolor=bl["color"], label=f'{bl["label"]} ({bl["cells"]:,})') for bl in blocks]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=3,
              fontsize=5.8, frameon=False, handlelength=1.0, handletextpad=0.4,
              columnspacing=0.8, labelspacing=0.3)
    fig.savefig(OUT / "blocks.pdf", bbox_inches="tight", pad_inches=0.02)
    print("blocks.pdf; macros:", [mc["name"] for mc in macros])


if __name__ == "__main__":
    main(sys.argv[1])
