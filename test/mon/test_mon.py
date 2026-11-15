# SPDX-FileCopyrightText: © 2026 Escapement contributors
# SPDX-License-Identifier: Apache-2.0
"""esc_mon against sw/esciss/monitor.py, every cycle: counts, record and its tick.
(v3, 11-14: the unit sends one word by rsel and a strobe, rec_load, for the tick, which the
core keeps: the bench reads both words and keeps the tick as the core does.)

Random pins (both slow and fast toggling, so intervals land on both sides of the limits),
MON writes with MIN/MAX drawn near the intervals the pins produce, clears, starts, and runs
broken by stops. The test also counts what it covered (early and late violations, records,
saturated counts) and fails if any kind never happened.
"""
import os
import random
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sw"))
from esciss.monitor import Monitor  # noqa: E402

CYCLES = int(os.environ.get("MON_CYCLES", "200000"))
STORM = min(20000, CYCLES // 2)
# a directed stretch after the storm: one interval of LONG_IVL ticks on pin 1, longer than the
# 16-bit count, against MIN 0xFFFF (11-15: a mutant whose count wrapped instead of saturating
# passed every suite). Only when the run is long enough to hold it.
LONG, LONG_IVL = STORM + 6000, 70000
LONG_ON = CYCLES >= LONG + LONG_IVL + 100


def rand_rule(rng):
    k = rng.randint(0, 3)
    fp, tp = rng.randint(0, 15), rng.randint(0, 15)
    if rng.random() < 0.7:                       # mostly pins 0..3, where the activity is
        fp, tp = rng.randint(0, 3), rng.randint(0, 3)
    fe, te = rng.randint(0, 3), rng.randint(0, 3)
    mn = rng.choice([0, 0, rng.randint(1, 12), rng.randint(1, 60), rng.getrandbits(16)])
    mx = rng.choice([0, rng.randint(1, 12), rng.randint(1, 80), rng.getrandbits(16)])
    a = (rng.choice([1, 1, 1, 0]) << 31 | k << 29 | fp << 25 | fe << 23 | tp << 19 | te << 17
         | int(rng.random() < 0.1) << 16 | mn)          # (v3: [16] keep, a clear alone)
    b = (int(rng.random() < 0.05) << 31 | rng.getrandbits(15) << 16 | mx)
    return a, b


@cocotb.test()
async def test_mon_lockstep(dut):
    rng = random.Random(int(os.environ.get("MON_SEED", "1")))
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    for s in ("start", "run", "mon_we", "mon_a", "mon_b", "psync", "rsel"):
        getattr(dut, s).value = 0
    dut.rst_n.value = 0
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    m = Monitor()
    pins, rate, running, now = 0, 0.1, False, 0
    tick = 0                                     # the core's copy (esc_core mon_tick)
    cov = {"early": 0, "late": 0, "record": 0, "sat": 0, "clear": 0}
    if LONG_ON:
        cov["cnt_sat"] = 0                       # the long interval's count reached 0xFFFF
    errors = 0
    for cyc in range(CYCLES):
        await FallingEdge(dut.clk)
        # ---- inputs during this cycle
        # a directed stretch: one rule that every pulse on pin 0 breaks, with no start, stop
        # or clear for 4,000 cycles, so its count saturates (random runs rarely get there)
        storm = STORM <= cyc < STORM + 4000
        long_ = LONG_ON and LONG <= cyc < LONG + LONG_IVL + 50
        if rng.random() < 0.002:
            rate = rng.choice([0.01, 0.05, 0.2, 0.5])
        for p in range(13):
            if rng.random() < (0.5 if storm and p == 0 else rate if p < 4 else rate / 8):
                pins ^= 1 << p
        if long_:                                # pin 1: low, then high for LONG_IVL ticks, then low
            hi = LONG + 10 <= cyc < LONG + 10 + LONG_IVL
            pins = (pins & ~2) | (int(hi) << 1)
        start = int(rng.random() < 0.0005 or cyc in (5, STORM) or (LONG_ON and cyc == LONG))
        if (storm and cyc != STORM) or (long_ and cyc != LONG):
            start = 0
        if start:
            running = True
        elif running and not storm and not long_ and rng.random() < 0.0003:
            running = False                      # this is the stop edge: no evaluation
        stop_edge = not start and not running and dut.run.value == 1
        run = int(running and not start)
        mon = rand_rule(rng) if (run and not long_ and rng.random() < 0.01) else None
        if storm:                                # rule 0: pin 0 rise -> fall, MIN 0xFFFF
            mon = ((1 << 31 | 1 << 23 | 2 << 17 | 0xFFFF, 0) if cyc == STORM + 1 else None)
        if long_ and cyc == LONG + 1:            # rule 1: pin 1 rise -> pin 1 fall, MIN 0xFFFF
            mon = (1 << 31 | 1 << 29 | 1 << 25 | 1 << 23 | 1 << 19 | 2 << 17 | 0xFFFF, 0)
        dut.psync.value = pins
        dut.start.value = start
        dut.run.value = run
        dut.mon_we.value = int(mon is not None)
        dut.mon_a.value, dut.mon_b.value = mon if mon else (0, 0)
        # ---- the state during this cycle must match
        got = {}
        for sel in (0, 1):
            dut.rsel.value = sel
            await Timer(1, unit="ps")
            got[sel] = int(dut.rdata.value)
        got[2] = tick
        for sel in (0, 1, 2):
            want = m.read(sel)
            if got[sel] != want:
                errors += 1
                if errors <= 10:
                    dut._log.error(f"cycle {cyc} sel {sel}: got {got[sel]:08x} want {want:08x}")
        if int(dut.rec_load.value):                  # the core loads now at this edge
            tick = now
        # ---- the model's edge
        before = (m.read(0), m.rec_v)
        if start:
            m.start()
            m.prev = pins
        else:
            m.edge(now, pins, bool(run), mon)
        if mon is not None and (mon[1] >> 31):
            cov["clear"] += 1
        if m.rec_v and not before[1]:
            cov["record"] += 1
            cov["late" if m.rec_late else "early"] += 1
        if any(r.nv == 0xFF for r in m.rules):
            cov["sat"] += 1
        if long_ and m.rules[1].armed and m.rules[1].cnt == 0xFFFF:
            cov["cnt_sat"] += 1
        now = (now + 1) & 0xFFFFFFFF
        _ = stop_edge
    dut._log.info(f"{CYCLES} cycles, coverage {cov}")
    assert errors == 0, f"{errors} mismatches"
    assert all(v > 0 for v in cov.values()), f"a kind never happened: {cov}"
