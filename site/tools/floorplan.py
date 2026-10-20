"""Which logic sits where: a block map of the placed design, from the final DEF.

    python site/tools/floorplan.py final.def stdcell.lef site/public/data/floorplan.json

Synthesis flattens the design and renames most cells (_12345_), but many nets keep
their RTL names (u_core.g_ctx[2].u_ctx.r0[5], u_host.state, ...). A cell that drives
a named net belongs to that net's block. Every other cell takes the block that most of
the cells it is wired to belong to, repeated until nothing changes. Filler, tap and
decap cells and the layout-art macro are skipped. The result is a grid of bins, each
labelled with the block that has the most cell area in it; it is an approximation, and
the page says so.
"""
import base64
import json
import re
import sys
from collections import Counter, defaultdict

BIN_UM = 5.0
SKIP = re.compile(r"^(FILLER|TAP|PHY|DECAP|ANTENNA|u_art)|fill|decap|tap|antenna", re.I)

BLOCKS = [  # key, label, colour, what it is
    ("ctx0", "Context 0", "#e0a43a", "registers, queue, checker and mailboxes of context 0"),
    ("ctx1", "Context 1", "#4fb3a9", "the same for context 1"),
    ("ctx2", "Context 2", "#8c7ae6", "the same for context 2"),
    ("ctx3", "Context 3", "#e0675a", "the same for context 3"),
    ("core", "Shared pipeline", "#6d9be8", "fetch, decode, execute, the timing unit, pins and digest"),
    ("cap", "Capture unit", "#c4d65a", "records pin changes into the tape"),
    ("line", "Line unit", "#5ac8e0", "packet line coding: NRZI, stuffing, CRC, clock recovery (v1.4)"),
    ("host", "Host interface", "#d77fc0", "the SPI link, configuration and memory ports"),
    ("clock", "Clock tree", "#9aa3ad", "clock buffers and clock gates"),
]
KEYS = [b[0] for b in BLOCKS]


def block_of(net):
    n = net.replace("\\", "")
    if n.startswith(("clknet", "delaynet")) or ".icg." in n:
        return "clock"
    m = re.match(r"u_core\.g_ctx\[(\d)\]", n)
    if m:
        return "ctx" + m.group(1)
    if n.startswith("u_core.u_cap"):
        return "cap"
    if n.startswith("u_core.u_line"):
        return "line"
    if n.startswith("u_core.") or n in ("imem_rdata", "tape_rdata") or n.startswith(("imem_", "tape_")):
        return "core"
    if n.startswith(("u_host", "mbx_", "host_", "entry_pcs", "pin_own", "ctx_", "cfg")):
        return "host"
    return None


def read_lef(path):
    size, outputs, cell = {}, defaultdict(set), None
    pin, direction = None, None
    for line in open(path):
        t = line.split()
        if not t:
            continue
        if t[0] == "MACRO":
            cell = t[1]
        elif t[0] == "SIZE" and cell:
            size[cell] = (float(t[1]), float(t[3]))
        elif t[0] == "PIN" and cell:
            pin = t[1]
        elif t[0] == "DIRECTION" and cell and pin and t[1].startswith("OUTPUT"):
            outputs[cell].add(pin)
        elif t[0] == "END" and len(t) > 1 and t[1] == cell:
            cell = None
    return size, outputs


def read_def(path):
    comps, nets, dbu, die = {}, [], 1000, None
    section = None
    cur = None
    for line in open(path):
        s = line.strip()
        if s.startswith("UNITS DISTANCE MICRONS"):
            dbu = int(s.split()[3])
        elif s.startswith("DIEAREA"):
            v = [int(x) for x in re.findall(r"-?\d+", s)]
            die = (v[2] / dbu, v[3] / dbu)
        elif s.startswith("COMPONENTS"):
            section = "c"
        elif s.startswith("END COMPONENTS") or s.startswith("END NETS"):
            section = None
        elif s.startswith("NETS"):
            section = "n"
        elif section == "c" and s.startswith("- "):
            m = re.match(r"- (\S+) (\S+) .*?(PLACED|FIXED) \( (-?\d+) (-?\d+) \)", s)
            if m:
                comps[m.group(1)] = (m.group(2), int(m.group(4)) / dbu, int(m.group(5)) / dbu)
        elif section == "n":
            if s.startswith("- "):
                cur = [s.split()[1], []]
                nets.append(cur)
            if cur is not None and not s.startswith(("+ ROUTED", "NEW")):
                cur[1] += re.findall(r"\( (\S+) (\S+) \)", s)
    return comps, nets, die


def main(defp, lefp, out):
    size, outputs = read_lef(lefp)
    comps, nets, die = read_def(defp)
    cells = {k: v for k, v in comps.items() if not SKIP.search(k) and not SKIP.search(v[0])
             and v[0] in size}
    label = {}
    adj = defaultdict(list)
    # a net named after one context with no twin in the other three was merged by
    # synthesis with logic the contexts share (it keeps the first name, context 0's)
    ctx_net = re.compile(r"u_core\.g_ctx\[(\d)\](.*)")
    suffix = defaultdict(set)
    for name, _ in nets:
        m = ctx_net.match(name.replace("\\", ""))
        if m:
            suffix[m.group(2)].add(m.group(1))
    for name, pins in nets:
        insts = [(i, p) for i, p in pins if i in cells]
        b = block_of(name)
        m = ctx_net.match(name.replace("\\", ""))
        if m and len(suffix[m.group(2)]) < 4:
            b = "core"
        if b:
            for i, p in insts:
                if p in outputs[cells[i][0]]:
                    label[i] = b
        if len(insts) <= 40:                     # skip huge nets (resets) for propagation
            members = [i for i, _ in insts]
            for i in members:
                adj[i].extend(x for x in members if x != i)
    seeded = len(label)
    for _ in range(60):                         # synchronous rounds: no order bias
        new = {}
        for i in cells:
            if i in label:
                continue
            votes = Counter(label[x] for x in adj[i] if x in label)
            if votes:
                top = votes.most_common(2)
                b = top[0][0]
                # logic that mixes several contexts (the slot multiplexers) is shared
                if (len(top) > 1 and b.startswith("ctx") and top[1][0].startswith("ctx")
                        and 2 * top[1][1] >= top[0][1]):
                    b = "core"
                new[i] = b
        if not new:
            break
        label.update(new)
    gw, gh = int(die[0] / BIN_UM) + 1, int(die[1] / BIN_UM) + 1
    area = [defaultdict(float) for _ in range(gw * gh)]
    stats = {k: [0, 0.0] for k in KEYS}
    for i, (cell, x, y) in cells.items():
        b = label.get(i)
        if b is None:
            continue
        w, h = size[cell]
        stats[b][0] += 1
        stats[b][1] += w * h
        gx, gy = int((x + w / 2) / BIN_UM), int((y + h / 2) / BIN_UM)
        area[gy * gw + gx][b] += w * h
    dens = {k: bytearray(gw * gh) for k in KEYS}   # cell-area fraction of each bin, 0..255
    for k, a in enumerate(area):
        for b, v in a.items():
            dens[b][k] = min(255, int(round(255 * v / (BIN_UM * BIN_UM))))
    grid = bytearray(gw * gh)
    for k, a in enumerate(area):
        if a:
            grid[k] = KEYS.index(max(a, key=a.get)) + 1
    data = {"die_um": list(die), "bin_um": BIN_UM, "w": gw, "h": gh,
            "blocks": [{"key": k, "label": l, "color": c, "about": d, "cells": stats[k][0],
                        "area_um2": round(stats[k][1])} for k, l, c, d in BLOCKS],
            "grid": base64.b64encode(bytes(grid)).decode(),
            "dens": {k: base64.b64encode(bytes(v)).decode() for k, v in dens.items()}}
    with open(out, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"{len(cells)} cells, {seeded} named, {sum(s[0] for s in stats.values())} placed in blocks")
    for k in KEYS:
        print(f"  {k:6s} {stats[k][0]:6d} cells {stats[k][1]:10.0f} um2")


if __name__ == "__main__":
    main(*sys.argv[1:4])
