"""firmware/quadrature.asm (v1.6): x4 quadrature decoding from capture records."""

from __future__ import annotations

import bisect
from pathlib import Path

import pytest

from escasm import assemble
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
GRAY = [0b00, 0b01, 0b11, 0b10]                  # B A, going forward


def encoder(steps, pa=0):
    """A board whose A, B (pins pa, pa + 1) follow `steps`: [(tick, +1 / -1 / 2)], 2 being
    both pins at once (illegal). Returns (board, position after each step)."""
    times, states, pos, ph, track = [0], [GRAY[0]], 0, 0, []
    for t, d in steps:
        if d == 2:
            ph = (ph + 2) % 4
        else:
            ph, pos = (ph + d) % 4, pos + d
        times.append(t)
        states.append(GRAY[ph])
        track.append(pos)

    def board(t, out, oe):
        g = states[bisect.bisect_right(times, t) - 1]
        return (0xFF & ~(3 << pa)) | (g << pa)
    return board, track


def ask(m):
    m.host_rx_push(0, 0)
    for _ in range(200):
        m.run(5, stop_when_idle=False)
        if m.ctx[0].tx:
            v = m.host_tx_pop(0)
            m.run(1, stop_when_idle=False)
            return v - (1 << 32) if v >> 31 else v
    raise AssertionError("no reply")


def machine(steps, pa=0):
    board, track = encoder(steps, pa)
    img = assemble(f'.equ quadrature.PA, {pa}\n.include "quadrature.asm"\n', base_dir=FW)
    return Machine(img, board=board), track


@pytest.mark.parametrize("pa", [0, 3, 6])
def test_forward_back_and_queries(pa):
    t, steps = 400, []
    for k in range(300):
        t += 150 + 37 * (k % 5)
        steps.append((t, 1 if (k // 40) % 3 != 2 else -1))
    m, track = machine(steps, pa)
    m.run(steps[99][0] + 120, stop_when_idle=False)       # (the next step is 150+ later)
    assert ask(m) == track[99]
    m.run(steps[-1][0] - m.now + 400, stop_when_idle=False)
    assert ask(m) == track[-1]
    assert m.irq_pending == 0


def test_bursts_and_sustained_rate():
    """120 steps 4 ticks apart (a burst within the ring's margin), then 2000 steps 100
    ticks apart (400k steps/s at 40 MHz): all counted."""
    steps = [(400 + 4 * k, 1) for k in range(120)]
    steps += [(2000 + 100 * k, -1 if k % 7 == 3 else 1) for k in range(2000)]
    m, track = machine(steps)
    m.run(steps[-1][0] + 400, stop_when_idle=False)
    assert ask(m) == track[-1] and m.irq_pending == 0


def test_illegal_step_and_overrun():
    steps = [(400, 1), (600, 1), (800, 2), (1000, 1)]
    m, _ = machine(steps)
    m.run(1400, stop_when_idle=False)
    assert m.irq_pending & 1 and m.ctx[0].irq_code == 3
    assert ask(m) == 3                               # the illegal step is not counted
    m.host_irq_clear(1)
    fast = [(3000 + 2 * k, 1) for k in range(2000)]   # a step every 2 cycles: too fast
    m2, _ = machine(fast)
    m2.run(8000, stop_when_idle=False)
    assert m2.irq_pending & 1 and m2.ctx[0].irq_code == 1
