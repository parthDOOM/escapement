"""firmware/freq_meter.asm (v1.6): reciprocal frequency, period and duty measurement from
the capture unit's timestamps, exact to one tick per gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"


def square(edges_hi, edges_lo, pin=0, start=500):
    """A board whose input `pin` rises at each tick in edges_hi and falls at each in
    edges_lo (sorted), low before the first rise."""
    ev = sorted([(t, 1) for t in edges_hi] + [(t, 0) for t in edges_lo])
    times = [t for t, _ in ev]

    def board(t, out, oe):
        import bisect
        k = bisect.bisect_right(times, t) - 1
        lv = ev[k][1] if k >= 0 else 0
        return (0xFF & ~(1 << pin)) | (lv << pin)
    return board


def meter(n=10, pin=0, extra=""):
    return assemble(f".equ freq_meter.N, {n}\n.equ freq_meter.PIN, {pin}\n{extra}"
                    '.include "freq_meter.asm"\n', base_dir=FW)


def drained(m, ticks, out=None):
    """Run for `ticks`, the host taking each word as it comes; the (period, high) pairs."""
    out = [] if out is None else out
    for _ in range(ticks // 20):
        m.run(19, stop_when_idle=False)
        if m.ctx[0].tx:
            out.append(m.host_tx_pop(0) & 0xFFFFFF)
        m.run(1, stop_when_idle=False)
    return out


def pairs(out):
    return list(zip(out[::2], out[1::2]))


@pytest.mark.parametrize("period,high,pin", [(1000, 250, 0), (777, 400, 5), (130, 65, 2)])
def test_exact_period_and_duty(period, high, pin):
    rises = [600 + k * period for k in range(80)]
    m = Machine(meter(10, pin), board=square(rises, [r + high for r in rises], pin))
    r = pairs(drained(m, 600 + 80 * period))
    assert len(r) >= 5
    assert all(x == (10 * period, 10 * high) for x in r)
    assert m.irq_pending == 0


def test_resolution_below_one_tick():
    """Periods of 1000.3 ticks: each gate of 10 is 10003 ticks, to the tick, and gates
    abut (no edge is lost between them)."""
    rises = [600 + (k * 10003) // 10 for k in range(201)]
    m = Machine(meter(10), board=square(rises, [r + 300 for r in rises]))
    r = pairs(drained(m, rises[-1] + 400))
    assert [p for p, _ in r] == [rises[10 * (g + 1)] - rises[10 * g] for g in range(len(r))]
    assert len(r) == 20
    assert {p for p, _ in r} == {10003}


def test_gate_across_an_epoch_boundary():
    """A gate across tick 2^24 and its marker record (the run starts just before it,
    by the ISS's test hook, keeping slot alignment)."""
    n0, period = (1 << 24) - 40_000, 9_000
    rises = [n0 + 600 + k * period for k in range(14)]
    m = Machine(meter(3), board=square(rises, [r + 1_000 for r in rises]))
    m.now = n0
    assert pairs(drained(m, 14 * period)) == [(3 * period, 3_000)] * 4


def windows(rises, falls, n):
    """Every (ticks of n periods, ticks high in them) the signal holds."""
    return {(rises[i + n] - rises[i], sum(f - r for r, f in zip(rises[i:i + n], falls[i:i + n])))
            for i in range(len(rises) - n)}


def test_fast_signal_overruns_and_restarts():
    """A 16-tick period (an edge every 8 cycles) outruns the reader: IRQ 1, and the
    program restarts rather than misreport. Every gate it does report is true: n whole
    periods of the signal (bursts it caught up with, the change of speed, the slow one)."""
    fast = [600 + 16 * k for k in range(1000)]
    slow = [20_000 + 900 * k for k in range(60)]
    rises, falls = fast + slow, [r + 8 for r in fast] + [r + 300 for r in slow]
    m = Machine(meter(5), board=square(rises, falls))
    out = drained(m, 20_000)
    assert m.irq_pending & 1 and m.ctx[0].irq_code == 1
    m.host_irq_clear(1)
    r = pairs(drained(m, 60 * 900, out))
    assert set(r) <= windows(rises, falls, 5)
    assert r[-8:] == [(4500, 1500)] * 8


def test_fastest_sustained_rate():
    """Bursts within the 128-record margin are absorbed, and a 100-tick period
    (400 kHz at 40 MHz) is sustained with no overrun."""
    rises = [600 + 100 * k for k in range(1000)]
    m = Machine(meter(20), board=square(rises, [r + 50 for r in rises]))
    r = pairs(drained(m, 100_000))
    assert len(r) >= 48 and all(x == (2_000, 1_000) for x in r)
    assert m.irq_pending == 0


def test_full_mailbox_drops_a_gate():
    rises = [600 + 200 * k for k in range(200)]
    m = Machine(meter(2), board=square(rises, [r + 50 for r in rises]))
    m.run(600 + 200 * 20, stop_when_idle=False)              # the host reads nothing
    assert m.irq_pending & 1 and m.ctx[0].irq_code == 2
    assert pairs([v & 0xFFFFFF for v in m.ctx[0].tx]) == [(400, 100)]
