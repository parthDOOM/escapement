#!/usr/bin/env python3
"""Timing bugs planted in the shipped firmware: does escert catch them?

    python test/mutation/fw_mutants.py [--per N] [--trials T] [--jobs J] [FIRMWARE ...]

The RTL campaign (run_mutants.py) plants faults in the chip; this one plants the timing
bugs firmware authors make, one per mutant, in the source of each shipped program:

    waitt   a WAITT deleted: the program no longer waits for its queued events
    addt    an ADDT deleted: two events share a deadline, or one lands in the past
    short   an ADDT's delay cut to a quarter (at least 1)
    branch  a conditional branch inverted (JZ/JNZ, JC/JNC): the wrong path is taken

Each mutant is assembled and certified. escert answers CHECKED (no admission fault for
any input), REFUTED (a fault, reproduced on the ISS with concrete inputs) or NOT_PROVEN
(a fault could not be excluded and the search found none). A CHECKED mutant is then
attacked with the same randomised input search on the ISS: a fault there would mean
escert certified a program that can fault (UNSOUND), and fails the run.

A mutant may also be harmless to timing (a shorter delay that still leaves the next
deadline in the future, a branch whose two paths have the same timing): escert then
certifies it, correctly. Mutants are picked by a hash of the firmware, operator and line
number, and the input search is seeded, so a run on the same sources gives the same table;
editing a firmware changes its set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sw"))

from escasm import AsmError, assemble  # noqa: E402
from escert import certify, witness  # noqa: E402

FW = REPO / "firmware"
SKIP = {"replay.asm",                       # a template
        "combo_two_banks.asm", "combo_uart_i2c.asm"}   # includes of the programs below
SALT = "escapement-fw-mutation-1"
BRANCH = {"JZ": "JNZ", "JNZ": "JZ", "JC": "JNC", "JNC": "JC"}


def mutants(src: str):
    """(op, line number, new source) for every mutation site of a firmware source."""
    lines = src.split("\n")
    for i, line in enumerate(lines):
        code = line.split(";", 1)[0]
        m = re.match(r"^(\s*(?:\w+:\s*)?)([A-Za-z]\w*)(\s*)(.*?)\s*$", code)
        if not m:
            continue
        lead, op, sp, arg = m.groups()
        op_u = op.upper()
        new = None
        if op_u == "WAITT" and not arg:
            yield "waitt", i + 1, lead + "; WAITT (deleted)"
        elif op_u == "ADDT":
            yield "addt", i + 1, lead + "; ADDT (deleted)"
            new = f"{lead}{op}{sp}((({arg}) + 3) // 4)"
            yield "short", i + 1, new
        elif op_u in BRANCH:
            yield "branch", i + 1, f"{lead}{BRANCH[op_u]}{sp}{arg}"


def chosen(key: str, per: int, n_sites: int) -> bool:
    """Up to about `per` sites of each operator per firmware, by hash."""
    h = int(hashlib.sha1((SALT + key).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return h < per / max(n_sites, per)


def run_one(job):
    fw, op, line, new_line, trials = job
    src = (FW / fw).read_text().split("\n")
    src[line - 1] = new_line
    try:
        image = assemble("\n".join(src), base_dir=FW, name=fw)
    except AsmError as e:
        return {"fw": fw, "op": op, "line": line, "result": "INVALID", "detail": str(e)[:120]}
    certs = certify(image, witness_trials=trials)
    bad = [c for c in certs if c.status != "CHECKED"]
    if bad:
        c = bad[0]
        return {"fw": fw, "op": op, "line": line, "result": c.status, "ctx": c.ctx,
                "detail": str(c).split("\n")[0][:160]}
    for c in certs:                       # certified: attack it on the ISS
        w = witness(image, c.ctx, trials)
        if w:
            return {"fw": fw, "op": op, "line": line, "result": "UNSOUND", "ctx": c.ctx,
                    "detail": json.dumps(w)}
    return {"fw": fw, "op": op, "line": line, "result": "CHECKED"}


def main() -> int:
    ap = argparse.ArgumentParser(prog="fw_mutants")
    ap.add_argument("firmware", nargs="*", help="firmware file names (default: all shipped)")
    ap.add_argument("--per", type=int, default=3, help="sites per operator per firmware")
    ap.add_argument("--trials", type=int, default=12, help="ISS input searches per mutant")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--json", help="write every result to this file")
    args = ap.parse_args()
    names = args.firmware or sorted(p.name for p in FW.glob("*.asm") if p.name not in SKIP)
    jobs = []
    for fw in names:
        sites = list(mutants((FW / fw).read_text()))
        for op in ("waitt", "addt", "short", "branch"):
            mine = [s for s in sites if s[0] == op]
            for _, line, new in mine:
                if chosen(f"{fw}|{op}|{line}", args.per, len(mine)):
                    jobs.append((fw, op, line, new, args.trials))
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run_one, jobs))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1))

    kinds = ["REFUTED", "NOT_PROVEN", "CHECKED", "UNSOUND", "INVALID"]
    print("| Firmware | Mutants | " + " | ".join(kinds) + " |")
    print("| --- | --- |" + " --- |" * len(kinds))
    for fw in names:
        rs = [r for r in results if r["fw"] == fw]
        if rs:
            print(f"| `{fw[:-4]}` | {len(rs)} | "
                  + " | ".join(str(sum(r['result'] == k for r in rs)) for k in kinds) + " |")
    tot = {k: sum(r["result"] == k for r in results) for k in kinds}
    print(f"| all | {len(results)} | " + " | ".join(str(tot[k]) for k in kinds) + " |")
    by_op = {op: [r for r in results if r["op"] == op] for op in ("waitt", "addt", "short", "branch")}
    print("\nBy operator: " + "; ".join(
        f"{op} {len(rs)} ({sum(r['result'] == 'REFUTED' for r in rs)} refuted, "
        f"{sum(r['result'] == 'NOT_PROVEN' for r in rs)} not proven, "
        f"{sum(r['result'] == 'CHECKED' for r in rs)} certified)" for op, rs in by_op.items()))
    for r in results:
        if r["result"] in ("UNSOUND", "NOT_PROVEN"):
            print(f"- {r['result']} {r['fw']}:{r['line']} {r['op']}: {r.get('detail', '')}")
    return 1 if tot["UNSOUND"] else 0


if __name__ == "__main__":
    sys.exit(main())
