"""Die imagery for the site, from the final GDS (needs the `klayout` Python package).

    python site/tools/die_assets.py GDS OUT_DIR [--max 16384]

Writes into OUT_DIR:
  layer_<name>.png   one coverage mask per layer (white = metal), 2048 px wide, for the
                     3D layer stack
  die_<z>_<x>_<y>.jpg  the composite die, as tiles of 1024 px: level 0 is one tile across,
                     each further level doubles the width, up to --max px
  die.json           die size, tile levels, macro outlines and the layer stack

The layout-art cell is removed before anything is drawn.
"""
import argparse
import json
import math
import os
import tempfile

import klayout.db as db
import klayout.lay as lay
import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

HIDE = {"escapement_art"}
TILE = 1024
BG = (10, 12, 20)
# (layer, datatype), key, label, colour, opacity in the composite, z and thickness in um
# (z and thickness from the PDK's sg13cmos5l_tech.lef; front end drawn at z = 0)
LAYERS = [
    ((1, 0), "activ", "Active", (46, 140, 84), 0.55, 0.0, 0.10),
    ((5, 0), "poly", "Gate poly", (214, 82, 60), 0.60, 0.10, 0.16),
    ((8, 0), "metal1", "Metal1", (70, 140, 236), 0.50, 0.93, 0.40),
    ((10, 0), "metal2", "Metal2", (196, 198, 214), 0.40, 1.88, 0.45),
    ((30, 0), "metal3", "Metal3", (236, 122, 64), 0.45, 2.88, 0.45),
    ((50, 0), "metal4", "Metal4", (152, 226, 96), 0.45, 3.88, 0.45),
    ((126, 0), "topmetal1", "TopMetal1", (232, 196, 92), 0.35, 6.16, 2.00),
]


def strip_hidden(gds):
    """A copy of the GDS without instances of the hidden cells; also returns macro boxes."""
    ly = db.Layout()
    ly.read(gds)
    top = ly.top_cell()
    macros = []
    for inst in list(top.each_inst()):
        name = inst.cell.name
        if name in HIDE:
            inst.delete()
        elif name.startswith("RM_IHPSG13"):
            b = inst.dbbox()
            macros.append({"name": inst.property("NAME") or name,
                           "box": [round(b.left, 2), round(b.bottom, 2),
                                   round(b.right, 2), round(b.top, 2)]})
    for name in HIDE:
        c = ly.cell(name)
        if c is not None:
            ly.prune_cell(c.cell_index(), -1)
    out = os.path.join(tempfile.mkdtemp(), "die.gds")
    ly.write(out)
    return out, top.dbbox(), macros


class Masks:
    """Coverage masks of one layer at a time, white on black, any window and size."""

    def __init__(self, gds):
        v = self.view = lay.LayoutView()
        v.set_config("background-color", "#000000")
        v.set_config("grid-visible", "false")
        v.set_config("text-visible", "false")
        v.load_layout(gds, True)
        v.max_hier()
        v.clear_layers()
        for (l, d), *_ in LAYERS:
            p = lay.LayerPropertiesNode()
            p.source = "%d/%d@1" % (l, d)
            p.fill_color = p.frame_color = 0xFFFFFF
            p.dither_pattern = 0
            p.width = 0
            p.visible = False
            v.insert_layer(v.end_layers(), p)
        self.nodes = []
        it = v.begin_layers()
        while not it.at_end():
            self.nodes.append(it.dup())
            it.next()
        self.tmp = os.path.join(tempfile.mkdtemp(), "m.png")

    def mask(self, i, box, w, h):
        for j, n in enumerate(self.nodes):
            q = n.current().dup()
            q.visible = i == j
            self.view.set_layer_properties(n, q)
        self.view.save_image_with_options(self.tmp, w, h, 0, 1, 0, box, False)
        return np.asarray(Image.open(self.tmp).convert("L"))


def composite(m, box, w, h):
    acc = np.empty((h, w, 3), np.float32)
    acc[:] = BG
    for i, (_, _, _, col, alpha, _, _) in enumerate(LAYERS):
        a = m.mask(i, box, w, h).astype(np.float32)[:, :, None] * (alpha / 255.0)
        acc *= 1.0 - a
        acc += a * np.array(col, np.float32)
    return acc.clip(0, 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gds")
    ap.add_argument("out")
    ap.add_argument("--max", type=int, default=16384, help="widest level, px")
    ap.add_argument("--layer-width", type=int, default=2048)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    gds, die, macros = strip_hidden(a.gds)
    m = Masks(gds)
    W, H = die.width(), die.height()

    lw = a.layer_width
    lh = round(lw * H / W)
    drawn = []                                      # layers with any shape in the design
    for i, (_, key, *_rest) in enumerate(LAYERS):
        mask = m.mask(i, die, lw, lh)
        if not mask.any():                          # TopMetal1: a TT block has none
            print("layer", key, "empty, left out", flush=True)
            continue
        Image.fromarray(mask).save(os.path.join(a.out, f"layer_{key}.png"), optimize=True)
        drawn.append(key)
        print("layer", key, flush=True)

    levels = []
    z, w = 0, TILE
    while w <= a.max:
        h = round(w * H / W)
        nx, ny = math.ceil(w / TILE), math.ceil(h / TILE)
        s = w / W                                   # px per um
        for ty in range(ny):
            for tx in range(nx):
                tw, th = min(TILE, w - tx * TILE), min(TILE, h - ty * TILE)
                x0 = die.left + tx * TILE / s
                y1 = die.top - ty * TILE / s
                box = db.DBox(x0, y1 - th / s, x0 + tw / s, y1)
                Image.fromarray(composite(m, box, tw, th)).save(
                    os.path.join(a.out, f"die_{z}_{tx}_{ty}.jpg"), quality=86, subsampling=0)
        levels.append({"z": z, "w": w, "h": h, "nx": nx, "ny": ny})
        print("level", z, w, "x", h, flush=True)
        z, w = z + 1, w * 2

    info = {
        "die_um": [round(W, 2), round(H, 2)],
        "tile": TILE,
        "levels": levels,
        "macros": macros,
        "layers": [{"key": k, "label": lab, "color": "#%02x%02x%02x" % c, "z": zz, "t": t}
                   for (_, k, lab, c, _, zz, t) in LAYERS if k in drawn],
        "layer_px": [lw, lh],
    }
    with open(os.path.join(a.out, "die.json"), "w") as f:
        json.dump(info, f, indent=1)


if __name__ == "__main__":
    main()
