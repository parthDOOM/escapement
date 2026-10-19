"""A small window of the routed chip as layered polygons, for the 3D close-up.

    python site/tools/slice_model.py GDS OUT.json [--box X0,Y0,X1,Y1]   (also writes OUT.js)

Every drawing layer from active to TopMetal1, vias included, is flattened, clipped to the
window and merged. Standard-cell outlines and names come along for labels. Coordinates
are in um relative to the window's lower-left corner; z and thickness are from the PDK's
technology LEF (front end drawn at z = 0).
"""
import argparse
import json

import klayout.db as db

# (layer, datatype), key, z, thickness (um)
LAYERS = [
    ((1, 0), "activ", 0.00, 0.10),
    ((5, 0), "poly", 0.10, 0.16),
    ((6, 0), "cont", 0.26, 0.67),
    ((8, 0), "metal1", 0.93, 0.40),
    ((19, 0), "via1", 1.33, 0.55),
    ((10, 0), "metal2", 1.88, 0.45),
    ((29, 0), "via2", 2.33, 0.55),
    ((30, 0), "metal3", 2.88, 0.45),
    ((49, 0), "via3", 3.33, 0.55),
    ((50, 0), "metal4", 3.88, 0.45),
    ((125, 0), "topvia1", 4.33, 1.83),
    ((126, 0), "topmetal1", 6.16, 2.00),
]


def poly_json(p, x0, y0):
    def pts(it):
        return [[round(q.x - x0, 3), round(q.y - y0, 3)] for q in it]
    return {"o": pts(p.each_point_hull()),
            "h": [pts(p.each_point_hole(i)) for i in range(p.holes())]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gds")
    ap.add_argument("out")
    ap.add_argument("--box", default="600,330,640,354")
    a = ap.parse_args()
    x0, y0, x1, y1 = [float(t) for t in a.box.split(",")]
    ly = db.Layout()
    ly.read(a.gds)
    top = ly.top_cell()
    dbu = ly.dbu
    box = db.Box(round(x0 / dbu), round(y0 / dbu), round(x1 / dbu), round(y1 / dbu))
    layers = []
    for (l, d), key, z, t in LAYERS:
        li = ly.find_layer(l, d)
        if li is None:
            continue
        r = db.Region(top.begin_shapes_rec_touching(li, box)) & db.Region(box)
        r.merge()
        polys = [poly_json(p.to_dtype(dbu), x0, y0) for p in r.each()]
        layers.append({"key": key, "z": z, "t": t, "polys": polys})
        print(key, len(polys))
    cells = []
    dbox = db.DBox(x0, y0, x1, y1)
    for inst in top.each_inst():
        b = inst.dbbox()
        if not b.overlaps(dbox) or not inst.cell.name.startswith("sg13cmos5l_"):
            continue
        cb = b & dbox
        name = inst.cell.name[len("sg13cmos5l_"):]
        if name.startswith(("fill", "decap", "tap")):
            continue
        cells.append({"cell": name, "box": [round(cb.left - x0, 3), round(cb.bottom - y0, 3),
                                             round(cb.right - x0, 3), round(cb.top - y0, 3)]})
    out = {"box_um": [x0, y0, x1, y1], "size_um": [round(x1 - x0, 3), round(y1 - y0, 3)],
           "layers": layers, "cells": cells}
    text = json.dumps(out, separators=(",", ":"))
    with open(a.out, "w") as f:
        f.write(text)
    with open(a.out[:-5] + ".js", "w") as f:              # for pages opened from disk
        f.write("window.ESC = window.ESC || {};\nESC.slice = " + text + ";\n")


if __name__ == "__main__":
    main()
