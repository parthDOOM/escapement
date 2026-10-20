#!/usr/bin/env python3
"""Run the ISS vs RTL co-simulation on several cores (make cosim-parallel).

    python3 test/cosim/run_parallel.py [--jobs N] [--seeds S] [--seed0 K]

The random programs are split into N seed ranges and the bus-model test into one run per
firmware; the directed tests run once. The testbench is compiled once (SIM=icarus or
SIM=verilator) and every job runs from that build. The result is the same set of
comparisons as `make cosim` with COSIM_SEEDS=S, in roughly 1/N of the time.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
# One job per fixed test: cocotb puts the filter into a shell command unquoted, so a
# regex alternation ("a|b") would become a pipe. The bus-model test runs one job per
# firmware (COSIM_FIRMWARE), since it is the longest.
FIXED = ["test_directed_firmware", "test_v11_directed", "test_v12_bank_b",
         "test_v13_host_reads_during_runs", "test_v13_streaming_capture", "test_v14_line_unit"]
BUS = "test_firmware_with_bus_models"
RANDOM = "test_random_programs"


def firmware_cases() -> list[str]:
    """Names in test_cosim.FIRMWARE_CASES (read from the source: importing it needs cocotb)."""
    src = (HERE / "test_cosim.py").read_text()
    block = src[src.index("FIRMWARE_CASES = {"):]
    block = block[:block.index("\n}\n")]
    return re.findall(r'^\s+"(\w+)":', block, re.M)


SIM = os.environ.get("SIM", "icarus")
BUILD = f"sim_build/shared_{SIM}"
# The simulator's build product in BUILD (cocotb's Makefile.<sim> target): compiled
# once, then every job runs from it. Verilator's C++ build takes longer than most jobs.
BUILD_TARGET = {"verilator": "Vtop", "icarus": "sim.vvp"}.get(SIM)


def build() -> str:
    """Compile the testbench once; '' on success, else the build log's tail."""
    if BUILD_TARGET is None:
        return ""
    r = subprocess.run(["make", "-s", f"SIM_BUILD={BUILD}", f"{BUILD}/{BUILD_TARGET}"],
                       cwd=HERE, env=dict(os.environ, PWD=str(HERE)), capture_output=True,
                       text=True)
    return "" if r.returncode == 0 else (r.stdout + r.stderr)[-3000:]


def run(job: str, test_filter: str, seed0: int, seeds: int,
        firmware: str = "") -> tuple[str, int, int, str]:
    env = dict(os.environ, PWD=str(HERE), COCOTB_TEST_FILTER=test_filter,
               COCOTB_PLUSARGS="+nodump", COSIM_SEED0=str(seed0), COSIM_SEEDS=str(seeds),
               COSIM_FIRMWARE=firmware, COSIM_OUT=str(HERE / "cosim_failures" / job))
    sim_build = BUILD if BUILD_TARGET else f"sim_build/{job}"
    cmd = ["make", "-s", f"SIM_BUILD={sim_build}", f"COCOTB_RESULTS_FILE=results_{job}.xml"]
    r = subprocess.run(cmd, cwd=HERE, env=env, capture_output=True, text=True)
    out = r.stdout + r.stderr
    m = re.search(r"TESTS=(\d+) PASS=(\d+) FAIL=(\d+)", out)
    if not m:
        return job, 0, 1, out[-3000:]
    return job, int(m.group(2)), int(m.group(3)), out[-3000:] if int(m.group(3)) else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    ap.add_argument("--seeds", type=int, default=int(os.environ.get("COSIM_SEEDS", "200")))
    ap.add_argument("--seed0", type=int, default=int(os.environ.get("COSIM_SEED0", "1")))
    args = ap.parse_args()

    n = max(1, min(args.jobs, args.seeds))
    work = [(name.removeprefix("test_"), name, args.seed0, 0) for name in FIXED]
    work += [(f"fw_{fw}", BUS, args.seed0, 0, fw) for fw in firmware_cases()]
    base, extra = divmod(args.seeds, n)
    s = args.seed0
    for k in range(n):
        count = base + (k < extra)
        if count:
            work.append((f"rand{k}", RANDOM, s, count))
            s += count

    err = build()
    if err:
        print(f"testbench build failed ({SIM}):\n{err}")
        return 1
    with ThreadPoolExecutor(max_workers=max(args.jobs, len(FIXED))) as pool:
        results = list(pool.map(lambda w: run(*w), work))

    failed = 0
    for job, passed, fails, tail in results:
        print(f"{job:28s} pass={passed} fail={fails}")
        if fails:
            failed += fails
            print(tail)
    print(f"\n{args.seeds} random programs in {n} jobs + directed and firmware tests: "
          f"{'PASS' if not failed else 'FAIL'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
