#!/usr/bin/env python3
"""Certificates against measurements: the proof table.

    python test/cosim/proof_table.py [--rtl FILE] [--board FILE] [--ticks N]

escert proves, for every context of a firmware, a lower bound on the chip's own
`min_lead` telemetry (READ_DBG sel 9 [31:16]): the least time between queuing an event
and its deadline. Each table puts that bound next to measurements, so a certificate is
tested, not trusted: a measured lead below the certified one is a bug in escert or in
the implementation that measured it.

Table 1, every shipped firmware, run on the ISS in the demo board's
environment (nothing on the Pmods, pull-ups on uio; the environment of the bring-up
bundles), for --ticks ticks. A context that queued no event reads 0xFFFF, shown as "-".
With --board (the proof.json bringup.py stage 5 writes on the demo board), the chip's
own lead and its digest verdict are added.

Table 2, the co-simulation cases (fw_cases.py): the same programs with their bus models
and host traffic, on the ISS. With --rtl (the file test_cosim.py appends to when
COSIM_PROOF_OUT is set), the RTL's lead and digest are added and must equal the ISS's.

Exit status 1 if any measurement is below its certificate, any certificate is not
CHECKED, or an RTL value differs from the ISS.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sw"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from escasm import assemble_file  # noqa: E402
from escert import certify  # noqa: E402
from esciss import Machine  # noqa: E402
from esciss.machine import no_board_b  # noqa: E402
from fw_cases import FIRMWARE_CASES, TAPE_PREP  # noqa: E402

SKIP = {"replay.asm"}          # a template: sw/esctape fills in its pins and trace
NONE = 0xFFFF                  # min_lead after a run that queued no event


def lead(v: int) -> str:
    return "-" if v == NONE else str(v)


def bound(c) -> str:
    return str(int(c.min_lead)) if c.min_lead != float("inf") else "inf"


def judge(c, measured: int) -> str | None:
    """The problem with one certificate and one measurement, if any."""
    if c.status != "CHECKED":
        return f"ctx {c.ctx} certificate {c.status}"
    if measured != NONE and measured < c.min_lead:
        return f"ctx {c.ctx} measured lead {measured} below certified {bound(c)}"
    return None


def shipped_table(ticks: int, board: dict) -> tuple[list[str], list[str]]:
    head = "| Firmware | Ctx | Certified lead >= | ISS lead |"
    rule = "| --- | --- | --- | --- |"
    if board:
        head += " Chip lead | Chip digest |"
        rule += " --- | --- |"
    rows, bad = [head, rule], []
    for fw in sorted((REPO / "firmware").glob("*.asm")):
        if fw.name in SKIP:
            continue
        image = assemble_file(fw)
        m = Machine(image)
        m.run(ticks, stop_when_idle=True)
        chip = board.get(fw.name)
        for c in certify(image):
            got = m.ctx[c.ctx].min_lead
            row = f"| `{fw.stem}` | {c.ctx} | {bound(c)} | {lead(got)} |"
            problems = [judge(c, got)]
            if board:
                if chip is None:
                    row += " not run | |"
                else:
                    cl = chip["tele"][c.ctx] >> 16
                    verdict = ("not predicted" if chip["kind"] == "none" else
                               "matches model" if chip["digest_ok"] else "DIFFERS")
                    row += f" {lead(cl)} | {verdict} |"
                    problems.append(judge(c, cl))
                    if verdict == "DIFFERS":
                        problems.append("chip digest differs from the model")
            rows.append(row)
            bad += [f"{fw.name}: {p}" for p in problems if p]
    return rows, bad


def cosim_table(rtl: dict) -> tuple[list[str], list[str]]:
    head = "| Case | Ctx | Certified lead >= | ISS lead | ISS digest |"
    rule = "| --- | --- | --- | --- | --- |"
    if rtl:
        head += " RTL lead | RTL digest |"
        rule += " --- | --- |"
    rows, bad = [head, rule], []
    for name, (fw, ticks, factory) in FIRMWARE_CASES.items():
        image = assemble_file(REPO / "firmware" / fw)
        if name in TAPE_PREP:
            TAPE_PREP[name](image)
        board, host, check, *bb = factory()
        m = Machine(image, board=board, board_b=bb[0] if bb else no_board_b)
        if host is not None:
            host.machine = m
        m.run(ticks, stop_when_idle=False)
        bad += [f"{name}: protocol check: {e}" for e in check(board, host)]
        dg = m.dbg_read(0, 16)
        r = rtl.get(name)
        if rtl and r is None:
            bad.append(f"{name}: no RTL record")
        for c in certify(image):
            got = m.ctx[c.ctx].min_lead
            row = f"| {name} | {c.ctx} | {bound(c)} | {lead(got)} | {dg:08x} |"
            problems = [judge(c, got)]
            if r is not None:
                rl = r["tele"][c.ctx] >> 16
                row += f" {lead(rl)} | {r['digest']:08x} |"
                if r["tele"][c.ctx] != m.dbg_read(c.ctx, 9) or r["digest"] != dg:
                    problems.append(f"ctx {c.ctx} RTL telemetry or digest differs from the ISS")
                if not r["ok"]:
                    problems.append("co-simulation reported a mismatch")
            rows.append(row)
            bad += [f"{name}: {p}" for p in problems if p]
    return rows, bad


def main() -> int:
    ap = argparse.ArgumentParser(prog="proof_table")
    ap.add_argument("--rtl", help="JSON lines from test_cosim.py (COSIM_PROOF_OUT)")
    ap.add_argument("--board", help="proof.json from bringup.py stage 5")
    ap.add_argument("--ticks", type=int, default=400_000,
                    help="ISS ticks per shipped firmware (default 400,000: 16 ms at 25 MHz)")
    args = ap.parse_args()
    rtl = {}
    if args.rtl:
        for line in Path(args.rtl).read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                rtl[rec["name"]] = rec
    board = json.loads(Path(args.board).read_text()) if args.board else {}
    t1, b1 = shipped_table(args.ticks, board)
    t2, b2 = cosim_table(rtl)
    print(f"## Shipped firmware, demo-board environment, {args.ticks:,} ticks\n")
    print("\n".join(t1))
    print("\n## Co-simulation cases, with bus models\n")
    print("\n".join(t2))
    bad = b1 + b2
    print(f"\n{len(bad)} problem(s)" + "".join(f"\n- {b}" for b in bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
