#!/usr/bin/env python3
"""Line unit lockstep: src/esc_line.v against the ISS model, every output, every cycle.

    python3 run.py [--seeds N] [--seed0 S] [--edges E] [--jobs J] [--no-cg]

For each seed, lockstep.py drives sw/esciss/line.py with random stimulus that follows the
core's rules (configurations from USB-like, CAN-like and random profiles, packets encoded
at offset clocks with jitter and glitches, noise, the unit's own pins read back, owner
faults, stop and start, tape writes from elsewhere) and writes the expected outputs per
edge; tb_line.v replays the same inputs into the RTL and compares. Needs Icarus Verilog
(iverilog, vvp) on PATH. --no-cg checks the plain enable flip-flop build (ESC_NO_CG).
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1] / "src"


def one(seed: int, edges: int, work: Path, vvp: Path) -> tuple[int, int, int, dict, str]:
    d = work / f"s{seed}"
    gen = subprocess.run([sys.executable, str(HERE / "lockstep.py"), str(seed), str(edges), str(d)],
                         capture_output=True, text=True)
    if gen.returncode:
        return seed, 0, 1, {}, gen.stderr[-2000:]
    cov = dict(kv.split("=") for kv in gen.stdout.split())
    r = subprocess.run(["vvp", "-n", str(vvp), f"+vec={d / 'vec.txt'}", f"+tape={d / 'tape.hex'}"],
                       capture_output=True, text=True)
    m = re.search(r"(\d+) edges, (\d+) errors", r.stdout)
    if not m:
        return seed, 0, 1, cov, (r.stdout + r.stderr)[-2000:]
    errs = int(m.group(2))
    return seed, int(m.group(1)), errs, cov, r.stdout[-2000:] if errs else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seeds", type=int, default=int(os.environ.get("LINE_SEEDS", "8")))
    ap.add_argument("--seed0", type=int, default=int(os.environ.get("LINE_SEED0", "1")))
    ap.add_argument("--edges", type=int, default=int(os.environ.get("LINE_EDGES", "200000")))
    ap.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    ap.add_argument("--no-cg", action="store_true")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="line_") as tmp:
        work = Path(tmp)
        vvp = work / "tb.vvp"
        defs = ["-DESC_NO_CG"] if a.no_cg else []
        c = subprocess.run(["iverilog", "-g2012", *defs, "-o", str(vvp), str(HERE / "tb_line.v"),
                            str(SRC / "esc_greg.v"), str(SRC / "esc_line.v")],
                           capture_output=True, text=True)
        if c.returncode:
            print(c.stdout + c.stderr)
            return 1
        seeds = range(a.seed0, a.seed0 + a.seeds)
        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            res = list(pool.map(lambda s: one(s, a.edges, work, vvp), seeds))
    total, bad, cov = 0, [], {}
    for seed, n, errs, c, tail in res:
        total += n
        for k, v in c.items():
            cov[k] = cov.get(k, 0) + int(v)
        if errs:
            bad.append(seed)
            print(f"seed {seed}: {errs} errors\n{tail}")
    names = {"end1": "CONFIG", "end3": "SEND", "end5": "RECV", "end6": "CRC"}
    causes = ["OK", "VIOL", "SE0", "STUFF", "FULL", "NOSYNC", "MISMATCH"]
    ends = [f"{names[k[:4]]} {causes[int(k[-1])]} {v}" for k, v in sorted(cov.items())
            if k.startswith("end")]
    print("jobs ended: " + ", ".join(ends) + f"; receive buffer writes {cov.get('rx_write', 0)}")
    print(f"{len(res)} seeds, {total} edges{' (ESC_NO_CG)' if a.no_cg else ''}: "
          f"{'PASS' if not bad else f'FAIL {bad}'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
