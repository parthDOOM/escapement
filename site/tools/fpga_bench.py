"""The hardware bench page's data (public/data/fpga.json), from the committed evidence of the latest
run in fpga/tangprimer25k/results/<date>/ and the bench sources.

    python site/tools/fpga_bench.py [out.json]

Nothing here is typed in by hand: stages and firmware results come from the bring-up log, the
wiring check from its output, build facts from the manifests and the place-and-route log, blocks
from hierarchy.json, and the wire list from tangprimer25k.cst and barepico.py (checked against the
Dock's connector tables from Sipeed's schematic; a mismatch stops the build).
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "fpga" / "tangprimer25k"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "site" / "public" / "data" / "fpga.json"

# Sipeed Tang Primer 25K Dock schematic (v1.0 2023, Rev v1.1 2025-08-28): Pmod socket pin -> ball.
# Pins 1, 2 are 3V3 and 3, 4 GND; odd pins form the top row. J3: pin -> ball (11 = +5V, 12 = GND).
PMOD = {
    "J4": {5: "C11", 6: "C10", 7: "B11", 8: "B10", 9: "D11", 10: "D10", 11: "G11", 12: "G10"},
    "J5": {5: "L5", 6: "K5", 7: "K11", 8: "L11", 9: "E11", 10: "E10", 11: "A11", 12: "A10"},
    "J6": {5: "H5", 6: "J5", 7: "H8", 8: "H7", 9: "G7", 10: "G8", 11: "F5", 12: "G5"},
}
J3 = ["K2", "K1", "L1", "L2", "K4", "J4", "G1", "G2", "L3", "L4", "+5V", "GND", "C2", "B2", "F1", "F2",
      "A1", "E1", "D1", "E3", "J2", "J1", "H4", "G4", "H2", "H1", "J7", "K7", "L8", "L7", "K10", "L10",
      "K9", "L9", "K8", "J8", "F6", "F7", "J10", "J11"]
PICO = ["GP0", "GP1", "GND", "GP2", "GP3", "GP4", "GP5", "GND", "GP6", "GP7", "GP8", "GP9", "GND", "GP10",
        "GP11", "GP12", "GP13", "GND", "GP14", "GP15", "GP16", "GP17", "GND", "GP18", "GP19", "GP20", "GP21",
        "GND", "GP22", "RUN", "GP26", "GP27", "AGND", "GP28", "ADC_VREF", "3V3(OUT)", "3V3_EN", "GND", "VSYS",
        "VBUS"]
GND_WIRES = {"J4": {3: 8}, "J5": {3: 13, 4: 18}, "J6": {3: 23, 4: 28}, "J3": {12: 3}}


def wiring():
    cst = (BENCH / "tangprimer25k.cst").read_text()
    loc = dict(re.findall(r'IO_LOC "([^"]+)" (\w+);', cst))
    bp = (BENCH / "barepico.py").read_text()
    clk = int(re.search(r"^CLK_GPIO = (\d+)", bp, re.M).group(1))
    rst = int(re.search(r"^RST_GPIO = (\d+)", bp, re.M).group(1))
    ui = int(re.search(r"^UI_SHIFT = (\d+)", bp, re.M).group(1))
    uo = int(re.search(r"^UO_SHIFT = (\d+)", bp, re.M).group(1))
    uio = [int(x) for x in re.search(r"^UIO_GPIO = \(([^)]*)\)", bp, re.M).group(1).split(",")]
    gpio = {"clk": clk, "rst_n": rst}
    for k in range(8):
        gpio[f"ui_in[{k}]"], gpio[f"uo_out[{k}]"], gpio[f"uio[{k}]"] = ui + k, uo + k, uio[k]
    pin_of = {int(n[2:]): i + 1 for i, n in enumerate(PICO) if n.startswith("GP")}
    ball_to_hole = {}
    for s, t in PMOD.items():
        for sp, b in t.items():
            c = (sp + 1) // 2
            ball_to_hole[b] = (s, "top" if sp % 2 else "bottom", c, sp)
    for i, b in enumerate(J3):
        if b not in ("+5V", "GND"):
            ball_to_hole[b] = ("J3", "inner" if (i + 1) % 2 else "outer", (i + 2) // 2, i + 1)
    wires = []
    for sig in sorted(gpio, key=lambda s: pin_of[gpio[s]]):
        ball = loc[sig]
        if ball not in ball_to_hole:
            raise SystemExit(f"{sig}: ball {ball} is on no Dock connector the bench uses")
        dock, row, col, sp = ball_to_hole[ball]
        bus = sig.split("[")[0]
        want = {"ui_in": "J4", "uo_out": "J5", "uio": "J6"}.get(bus, "J3")
        if dock != want:
            raise SystemExit(f"{sig}: ball {ball} is on {dock}, the bench expects {want}")
        wires.append({"sig": sig, "gpio": gpio[sig], "pico": pin_of[gpio[sig]], "dock": dock, "row": row,
                      "col": col, "pin": sp, "ball": ball})
    for dock, m in GND_WIRES.items():
        for sp, pico in m.items():
            assert PICO[pico - 1] == "GND"
            if dock == "J3":
                assert J3[sp - 1] == "GND"
                wires.append({"sig": "GND", "gpio": None, "pico": pico, "dock": dock, "row": "outer",
                              "col": (sp + 1) // 2, "pin": sp, "ball": "GND"})
            else:
                wires.append({"sig": "GND", "gpio": None, "pico": pico, "dock": dock,
                              "row": "top" if sp % 2 else "bottom", "col": 2, "pin": sp, "ball": "GND"})
    return {"wires": wires, "pico": PICO, "j3": J3, "pmod": {k: {str(a): b for a, b in v.items()} for k, v in PMOD.items()}}


STAGE_NAMES = {}


def bringup(text):
    stages, fw = [], []
    for line in text.splitlines():
        m = re.match(r"\[(\d)\] (.+?) \.\.\.$", line)
        if m:
            STAGE_NAMES[int(m.group(1))] = m.group(2)
            continue
        m = re.match(r"\[(\d)\] (PASS|FAIL)\s+(.*)$", line)
        if m:
            n = int(m.group(1))
            stages.append({"n": n, "name": STAGE_NAMES.get(n, ""), "pass": m.group(2) == "PASS", "detail": m.group(3)})
            continue
        m = re.match(r"\s{4}(\S+)\s+(steady|halt|none)\s+(ok|FAIL\S*)\s+(.*)$", line)
        if m:
            name, kind, ok, rest = m.groups()
            d = {"name": name, "kind": kind, "ok": ok == "ok", "detail": rest}
            mm = re.search(r"digest (0x[0-9a-f]{8}) (?:= model at tick (\d+)|model (0x[0-9a-f]{8}))", rest)
            if mm:
                d["digest"] = mm.group(1)
                d["model"] = mm.group(3) or mm.group(1)
                if mm.group(2):
                    d["tick"] = int(mm.group(2))
            leads = re.findall(r"(\d):(\d+|none) \(cert (\d+)\)", rest)
            d["leads"] = [[int(c), None if l == "none" else int(l), int(k)] for c, l, k in leads]
            fw.append(d)
    verdict = re.search(r"^bring-up (PASSED|FAILED)", text, re.M)
    return stages, fw, verdict.group(1) if verdict else None


def pnr(text):
    util = {}
    for name, used, avail in re.findall(r"^Info:\s+(\w+):\s+(\d+)/\s*(\d+)\s+\d+%", text, re.M):
        util[name] = [int(used), int(avail)]
    fmax = re.findall(r"Max frequency for clock 'chip.clk': ([\d.]+) MHz", text)
    holds = re.search(r"^Info: (\d+) Hold/min time violations", text, re.M)
    return {"util": util, "fmax_mhz": float(fmax[-1]) if fmax else None, "hold_flags": int(holds.group(1)) if holds else 0}


def manifest(path):
    t = path.read_text()
    m = {"text": t}
    r = re.search(r"^repo: ([0-9a-f]{40}) \(([^)]*)\)(.*)$", t, re.M)
    if r:
        m["commit"], m["branch"], m["dirty"] = r.group(1), r.group(2), bool(r.group(3).strip())
    b = re.search(r"^built: (\S+)", t, re.M)
    if b:
        m["built"] = b.group(1)
    tools = re.search(r"^tools: (.*)$", t, re.M)
    if tools:
        m["tools"] = tools.group(1)
    fs = re.search(r"^([0-9a-f]{64})\s+\S*chip\.fs$", t, re.M)
    if fs:
        m["bitstream_sha256"] = fs.group(1)
    sk = re.search(r"clock skew ([\d.]+) ns", t)
    if sk:
        m["skew_ns"] = float(sk.group(1))
    return m


def main():
    runs = sorted(p for p in (BENCH / "results").iterdir() if p.is_dir())
    run = runs[-1]
    stages, fw, verdict = bringup((run / "bringup.log").read_text())
    pin = (run / "pinid.txt").read_text()
    pinmap = re.findall(r"^GP(\d+)\s+(\S+)$", pin, re.M)
    data = {
        "date": run.name,
        "files": f"fpga/tangprimer25k/results/{run.name}",
        "pinid": {"pass": "WIRING: PASS" in pin, "map": [[int(g), s] for g, s in pinmap]},
        "stages": stages,
        "verdict": verdict,
        "firmware": fw,
        "chip": manifest(run / "chip.MANIFEST"),
        "bundles": manifest(run / "bundles.MANIFEST"),
        "pnr": pnr((run / "chip-pnr.log").read_text()),
        "hierarchy": json.loads((run / "hierarchy.json").read_text()),
        **wiring(),
    }
    m = re.search(r"chip clock ([\d.]+) MHz", (run / "bringup.log").read_text())
    data["clock_mhz"] = float(m.group(1)) if m else None
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, separators=(",", ":")))
    ok = sum(f["ok"] for f in fw)
    print(f"fpga.json: run {run.name}, {len(stages)} stages, {ok}/{len(fw)} firmware ok, "
          f"{len(data['wires'])} wires, verdict {verdict}")


if __name__ == "__main__":
    main()
