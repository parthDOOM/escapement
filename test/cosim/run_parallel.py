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
BUS = "test_firmware_with_bus_models"
RANDOM = "test_random_programs"


def cocotb_tests() -> list[str]:
    """Every @cocotb.test() in test_cosim.py, in file order (read from the source, as
    firmware_cases does). Until 11-10 this was a hand-kept list, and three v1.7 tests
    (test_pair_random, test_v17_live_reload, test_v17_line_unit_bank_b) were never added:
    `make cosim-parallel`, which CI runs, skipped them while `make cosim` ran them."""
    src = (HERE / "test_cosim.py").read_text()
    found = re.findall(r"^@cocotb\.test\(\)\n(?:@[^\n]*\n)*async def (test_\w+)\(", src, re.M)
    if len(found) != src.count("@cocotb.test("):     # a decorator this pattern misses
        raise SystemExit("run_parallel: %d @cocotb.test decorators, %d tests recognised"
                         % (src.count("@cocotb.test("), len(found)))
    return found


FIXED = [t for t in cocotb_tests() if t not in (BUS, RANDOM)]


def firmware_cases() -> list[str]:
    """Names in fw_cases.FIRMWARE_CASES (read from the source: this runs without sw/ on
    the path)."""
    src = (HERE / "fw_cases.py").read_text()
    block = src[src.index("FIRMWARE_CASES = {"):]
    block = block[:block.index("\n}\n")]
    return re.findall(r'^\s+"(\w+)":', block, re.M)


SIM = os.environ.get("SIM", "icarus")
BUILD = f"sim_build/shared_{SIM}" + ("_cells" if os.environ.get("CELLS") == "yes" else "")
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
        firmware: str = "") -> tuple[str, int, int, str, str]:
    env = dict(os.environ, PWD=str(HERE), COCOTB_TEST_FILTER=test_filter,
               COCOTB_PLUSARGS="+nodump", COSIM_SEED0=str(seed0), COSIM_SEEDS=str(seeds),
               COSIM_FIRMWARE=firmware, COSIM_OUT=str(HERE / "cosim_failures" / job))
    sim_build = BUILD if BUILD_TARGET else f"sim_build/{job}"
    cmd = ["make", "-s", f"SIM_BUILD={sim_build}", f"COCOTB_RESULTS_FILE=results_{job}.xml"]
    r = subprocess.run(cmd, cwd=HERE, env=env, capture_output=True, text=True)
    out = r.stdout + r.stderr
    m = re.search(r"TESTS=(\d+) PASS=(\d+) FAIL=(\d+)", out)
    s = re.search(r"random: (\d+) programs, (\d+) commits, (\d+) cycles, (\d+) skipped", out)
    summary = s.group(0) if s else ""
    if not m:
        return job, 0, 1, out[-3000:], summary
    return job, int(m.group(2)), int(m.group(3)), out[-3000:] if int(m.group(3)) else "", summary


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

    failed, total = 0, [0, 0, 0, 0]
    for job, passed, fails, tail, summary in results:
        print(f"{job:28s} pass={passed} fail={fails}" + (f"  ({summary})" if summary else ""))
        for i, v in enumerate(re.findall(r"\d+", summary)):
            total[i] += int(v)
        if fails:
            failed += fails
            print(tail)
    print(f"\nrandom test: {total[0]} programs, {total[1]} commits, {total[2]} cycles, "
          f"{total[3]} skipped (longer than the 256-word program memory)")
    print(f"{args.seeds} random programs in {n} jobs + directed and firmware tests: "
          f"{'PASS' if not failed else 'FAIL'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
