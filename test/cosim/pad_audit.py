#!/usr/bin/env python3
"""Pad and board timing audit (make pad-audit): does each firmware still work when the pins
are slower than the simulation's ideal ones?

    python test/cosim/pad_audit.py [--jobs J] [--max K] [CASE ...]

The simulations give every pin zero delay: an output reaches the bus model in the tick it is
driven, and a model's answer reaches the chip's two-flop synchroniser in the tick it changes.
A real chip adds its pads and the board on both ways, about 10 ns each through Tiny Tapeout's
pads and wiring. A protocol that drives a pin and then samples the answer it causes (SPI's
MISO after an SCK edge, I2C's ACK, a 1-Wire read slot, a USB turnaround) loses that round
trip from its margin; one with no margin left works in simulation and fails on a board.

Each co-simulation case (fw_cases.FIRMWARE_CASES, with its bus models and host) is run on the
ISS with every pin delayed by k ticks in each direction: the bus models see the chip's outputs
k ticks late, and the chip sees the models' outputs k ticks after they change, so a round
trip costs 2k ticks more than in simulation. A tick is 20 ns at the rated 50 MHz and longer
at every lower clock, so k = 1 already covers 10 ns each way at any clock the chip runs;
k = 2 and 3 measure the margin beyond that. A case passes at k when its protocol check
passes, its board reports no errors and no context faults.

The table also gives, per case, the shortest time between two changes of any one output pin
(driven level or release), from the run with k = 0: the highest rate the pads must carry.
"""

from __future__ import annotations

import argparse
import sys
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(REPO / "sw"), str(HERE)]

from esciss import Machine  # noqa: E402
from esciss.machine import no_board_b  # noqa: E402
from fw_cases import FIRMWARE_CASES, FW_EQU, fw_image  # noqa: E402


class Delayed:
    """A board (t, out, oe) -> inputs whose pins take k ticks each way. Called once per tick
    in increasing t, as the ISS calls its board; the inner board is called the same way."""

    def __init__(self, inner, k: int, width: int = 8):
        self.inner, self.k = inner, k
        self.outs: deque = deque()
        self.ins: deque = deque()
        self.last = [None] * width             # per pin: (oe, out) and the tick it took it
        self.min_gap = None                    # shortest interval between two changes, ticks
        self.width = width

    def _edges(self, t: int, out: int, oe: int):
        for p in range(self.width):
            st = ((oe >> p) & 1, (out >> p) & 1 if (oe >> p) & 1 else 0)
            prev = self.last[p]
            if prev is None or prev[0] != st:
                if prev is not None and prev[1] is not None and t >= 0:
                    gap = t - prev[1]
                    self.min_gap = gap if self.min_gap is None else min(self.min_gap, gap)
                self.last[p] = (st, t if prev is not None else None)

    def __call__(self, t: int, out: int, oe: int) -> int:
        self._edges(t, out, oe)
        # for the first k ticks the far end sees the pins as they were before the run (the
        # first call's values, from reset), and the chip sees the models' first outputs
        self.outs.append((out, oe))
        o, e = self.outs[0]
        if len(self.outs) > self.k:
            self.outs.popleft()
        v = self.inner(t, o, e)
        self.ins.append(v)
        r = self.ins[0]
        if len(self.ins) > self.k:
            self.ins.popleft()
        return r


class DelayedB(Delayed):
    """The same for bank B, a board (t, ob) -> inputs."""

    def __init__(self, inner, k: int):
        super().__init__(lambda t, o, e: inner(t, o), k, width=5)

    def __call__(self, t: int, ob: int) -> int:
        return super().__call__(t, ob, 0x1F)


def clock_khz(name: str, image) -> int:
    if "CLK_KHZ" in FW_EQU.get(name, {}):
        return FW_EQU[name]["CLK_KHZ"]
    syms = getattr(image, "symbols", {}) or {}
    ks = [v for s, v in syms.items() if s.split(".")[-1] == "CLK_KHZ"]
    return ks[0] if ks else 25000


def run(job):
    name, k = job
    fw, ticks, factory = FIRMWARE_CASES[name]
    image = fw_image(name, fw, REPO / "firmware")
    board, host, check, *bb = factory()
    da = Delayed(board, k)
    db = DelayedB(bb[0], k) if bb else no_board_b
    try:
        board.pad_delay = k                # a check that measures exact timing allows for it
    except AttributeError:
        pass
    half = getattr(board, "half", None)
    if half is not None and k:
        inner_half = half
        half = lambda t, before, cur: inner_half(t - k, before, cur)   # noqa: E731
    m = Machine(image, board=da, board_b=db, half=half, record=False)
    if host is not None:
        host.machine = m
    m.run(ticks, stop_when_idle=False)
    errs = list(check(board, host))
    if m.ff.valid:
        errs.append(f"fault ctx {m.ff.ctx} {m.ff}")
    gaps = [g for g in (da.min_gap, getattr(db, "min_gap", None)) if g is not None]
    return name, k, errs, min(gaps) if gaps else None, clock_khz(name, image)


def main() -> int:
    ap = argparse.ArgumentParser(prog="pad_audit")
    ap.add_argument("cases", nargs="*", help="case names (default: every co-simulation case)")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--max", type=int, default=3, help="largest delay k, ticks each way")
    args = ap.parse_args()
    names = args.cases or list(FIRMWARE_CASES)
    jobs = [(n, k) for n in names for k in range(args.max + 1)]
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        res = list(pool.map(run, jobs))
    by = {}
    for name, k, errs, gap, khz in res:
        by.setdefault(name, {})[k] = (errs, gap, khz)

    print("| Case | Clock (MHz) | Fastest pin change (ns) | "
          + " | ".join(f"k={k}" for k in range(args.max + 1)) + " | Round-trip slack (ns) |")
    print("| --- | ---: | ---: |" + " :---: |" * (args.max + 1) + " ---: |")
    flagged, broken = [], []
    for name in names:
        r = by[name]
        khz = r[0][2]
        tick_ns = 1e6 / khz
        gap = r[0][1]
        ok = [not r[k][0] for k in range(args.max + 1)]
        if not ok[0]:
            broken.append(name)
        slack = -1
        for k in range(args.max + 1):
            if ok[k]:
                slack = k
            else:
                break
        if slack < 1:
            flagged.append(name)
        s = "none" if slack < 0 else (f"at least {2 * slack * tick_ns:,.0f}" if slack == args.max
                                      else f"{2 * slack * tick_ns:,.0f}")
        print(f"| {name} | {khz / 1000:g} | {'-' if gap is None else f'{gap * tick_ns:,.0f}'} | "
              + " | ".join("pass" if x else "FAIL" for x in ok) + f" | {s} |")
    for name in names:
        for k in range(args.max + 1):
            if by[name][k][0]:
                print(f"- {name} k={k}: {'; '.join(by[name][k][0][:2])[:240]}")
    print(f"\n{len(names)} cases; fail without delay: {broken or 'none'}; "
          f"fail with 1 tick each way: {flagged or 'none'}")
    return 1 if broken or flagged else 0


if __name__ == "__main__":
    sys.exit(main())
