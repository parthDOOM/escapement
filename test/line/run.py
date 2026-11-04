#!/usr/bin/env python3
"""Line unit lockstep: src/esc_line.v against the ISS model, every output, every cycle.

    python3 run.py [--seeds N] [--seed0 S] [--edges E] [--jobs J] [--no-cg] [--src DIR]

For each seed, lockstep.py drives sw/esciss/line.py with random stimulus that follows the
core's rules (configurations from USB-like, CAN-like and random profiles, packets encoded
at offset clocks with jitter and glitches, noise, the unit's own pins read back, owner
faults, stop and start, tape writes from elsewhere) and writes the expected outputs per
edge; tb_line.v replays the same inputs into the RTL and compares, with stray tape reads
from elsewhere in cycles the unit leaves the port free. Needs Icarus Verilog
(iverilog, vvp) on PATH. --no-cg checks the plain enable flip-flop build (ESC_NO_CG);
CELLS=yes in the environment the standard-cell build (clock gates and W0's latches, D-40),
with PDK_ROOT. --src takes the RTL from another tree (the mutation campaign).

After the random seeds, PROFILED runs give every configuration block one profile, for
cases the mix reaches too rarely: the eth profile's end-of-packet symbols of two cells
after a 0 bit (found by the mutant esc_line_stuck_8d8db8fe, which seeds 1-24 missed but
for one), and the spi profile's empty packets in a differential configuration with no
end-of-packet symbols (esc_line_bit_19a2ed90), and the can profile's monitored bits whose
sample falls in the cycle before a boundary, S = P - 1 (esc_line_rel_b5d0c5bf, which 4 of
40 random seeds kill). --no-profiled skips them.
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
PROFILED = [(1001, "eth"), (1002, "spi"), (1003, "canmon")]   # (seed, profile) runs after the random seeds


def one(seed: int, edges: int, work: Path, vvp: Path, profile: str = "") -> tuple[int, int, int, dict, str]:
    d = work / f"s{seed}{profile}"
    gen = subprocess.run([sys.executable, str(HERE / "lockstep.py"), str(seed), str(edges), str(d)]
                         + ([profile] if profile else []), capture_output=True, text=True)
    if gen.returncode:
        return seed, 0, 1, {}, gen.stderr[-2000:]
    cov = dict(kv.split("=") for kv in gen.stdout.split())
    r = subprocess.run(["vvp", "-n", str(vvp), f"+vec={d / 'vec.txt'}", f"+tape={d / 'tape.hex'}",
                        f"+stray={seed + 1}"],
                       capture_output=True, text=True)
    m = re.search(r"(\d+) edges, (\d+) errors, (\d+) stray reads", r.stdout)
    if not m:
        return seed, 0, 1, cov, (r.stdout + r.stderr)[-2000:]
    n, errs, strays = map(int, m.groups())
    # the run checks itself: every generated edge replayed, and stray reads really made
    if n != edges:
        return seed, n, max(errs, 1), cov, f"replayed {n} of {edges} edges\n" + r.stdout[-2000:]
    if strays == 0:
        return seed, n, max(errs, 1), cov, "no stray reads were made\n" + r.stdout[-2000:]
    return seed, n, errs, cov, r.stdout[-2000:] if errs else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seeds", type=int, default=int(os.environ.get("LINE_SEEDS", "8")))
    ap.add_argument("--seed0", type=int, default=int(os.environ.get("LINE_SEED0", "1")))
    ap.add_argument("--edges", type=int, default=int(os.environ.get("LINE_EDGES", "200000")))
    ap.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    ap.add_argument("--no-cg", action="store_true")
    ap.add_argument("--src", type=Path, default=SRC)
    ap.add_argument("--no-profiled", action="store_true", help="skip the PROFILED runs")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="line_") as tmp:
        work = Path(tmp)
        vvp = work / "tb.vvp"
        defs = ["-DESC_NO_CG"] if a.no_cg else []
        cells = []
        if os.environ.get("CELLS") == "yes":
            lib = Path(os.environ["PDK_ROOT"]) / "ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog"
            defs.append("-DSCL_sg13cmos5l_stdcell")
            cells = [str(lib / "sg13cmos5l_stdcell.v"), str(lib / "sg13cmos5l_udp.v")]
        c = subprocess.run(["iverilog", "-g2012", *defs, "-o", str(vvp), str(HERE / "tb_line.v"),
                            str(a.src / "esc_greg.v"), str(a.src / "esc_line.v"), *cells],
                           capture_output=True, text=True)
        if c.returncode:
            print(c.stdout + c.stderr)
            return 1
        runs = [(s, "") for s in range(a.seed0, a.seed0 + a.seeds)]
        runs += [] if a.no_profiled else PROFILED
        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            res = list(pool.map(lambda r: one(r[0], a.edges, work, vvp, r[1]), runs))
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
    print(f"{a.seeds} seeds + {len(res) - a.seeds} profiled, {total} edges{' (ESC_NO_CG)' if a.no_cg else ''}: "
          f"{'PASS' if not bad else f'FAIL {bad}'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
