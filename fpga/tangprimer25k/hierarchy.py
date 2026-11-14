"""Block counts and inter-block buses from a hierarchy-kept synth_gowin netlist (hierarchy.sh)."""
import json
import re
import sys
from collections import Counter, defaultdict

src, out = sys.argv[1], sys.argv[2]
allmods = json.load(open(src))["modules"]
# the cell library (LUT4, DFFCE, ...) comes along as blackbox/whitebox modules: leave it out
mods = {k: v for k, v in allmods.items()
        if not v.get("attributes", {}).get("blackbox") and not v.get("attributes", {}).get("whitebox")}


def base(name):
    """yosys names parametrised copies '$paramod...\\esc_ctx' or 'esc_ctx'; keep the base name."""
    return re.sub(r"^\$paramod.*?\\", "", name).split("\\")[-1]


by_base = {}
for name in mods:
    by_base.setdefault(base(name), name)


def leaf_counts(modname):
    """Primitive cells inside one module, children excluded (they are counted on their own)."""
    c = Counter()
    for cell in mods[modname]["cells"].values():
        t = cell["type"]
        if t in mods:
            continue
        if t.startswith("LUT") or t.startswith("MUX2_LUT"):
            c["lut_cells"] += 1
        if t.startswith("LUT"):
            c["lut4"] += 1
        elif t.startswith("MUX2_LUT"):
            c["mux"] += 1
        elif t == "ALU":
            c["alu"] += 1
        elif t.startswith("DFF"):
            c["ff"] += 1
        elif t in ("SP", "SPX9", "SDPB", "DPB", "DPX9B", "SDPX9B"):
            c["bram"] += 1
    return c


def instances(modname):
    """Child block instances of a module: name -> (type, {port: bits})."""
    res = {}
    for cname, cell in mods[modname]["cells"].items():
        if cell["type"] in mods:
            res[cname] = (base(cell["type"]), cell["connections"], cell.get("port_directions", {}), cell["type"])
    return res


def edges(modname):
    """Buses between child instances (and the parent's own logic): shared net bits per pair."""
    inst = instances(modname)
    drivers, sinks = {}, defaultdict(set)
    for iname, (_, conns, dirs, full) in inst.items():
        child = mods[full]
        for port, bits in conns.items():
            d = child["ports"].get(port, {}).get("direction", dirs.get(port, "input"))
            for b in bits:
                if not isinstance(b, int):
                    continue
                if d == "output":
                    drivers[b] = (iname, port)
                else:
                    sinks[b].add((iname, port))
    # the parent's own logic and its ports are nodes too
    own = "(" + base(modname) + " logic)"
    for cell in mods[modname]["cells"].values():
        if cell["type"] in mods:
            continue
        for port, bits in cell["connections"].items():
            d = cell.get("port_directions", {}).get(port, "input")
            for b in bits:
                if isinstance(b, int):
                    if d == "output":
                        drivers.setdefault(b, (own, port))
                    else:
                        sinks[b].add((own, port))
    for pname, p in mods[modname]["ports"].items():
        for b in p["bits"]:
            if not isinstance(b, int):
                continue
            if p["direction"] == "input":
                drivers.setdefault(b, ("(pins)", pname))
            else:
                sinks[b].add(("(pins)", pname))
    pair = defaultdict(lambda: {"bits": 0, "ports": set()})
    for b, (src_i, src_p) in drivers.items():
        for dst_i, dst_p in sinks.get(b, ()):
            if dst_i == src_i:
                continue
            e = pair[(src_i, dst_i)]
            e["bits"] += 1
            e["ports"].add(src_p + "->" + dst_p)
    return [{"from": a, "to": b, "bits": v["bits"], "ports": sorted(v["ports"])} for (a, b), v in sorted(pair.items())]


top = by_base["tt_um_escapement"]
core = by_base["esc_core"]
blocks = []
for parent in (top, core):
    for iname, (typ, _, _, full) in sorted(instances(parent).items()):
        if typ == "esc_core":
            continue
        blocks.append({"inst": iname, "type": typ, "parent": base(parent), **leaf_counts(full)})
blocks.append({"inst": "u_core", "type": "esc_core", "parent": "tt_um_escapement", **leaf_counts(core)})
blocks.append({"inst": "(top glue)", "type": "tt_um_escapement", "parent": "escapement_board", **leaf_counts(top)})
total = Counter()
for b in blocks:
    total.update({k: v for k, v in b.items() if isinstance(v, int)})
json.dump({"blocks": blocks, "total": dict(total),
           "edges_top": edges(top), "edges_core": edges(core)}, open(out, "w"), indent=1)
print(f"{len(blocks)} blocks; total {dict(total)}")
