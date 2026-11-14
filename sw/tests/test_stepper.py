"""Stepper motion (firmware/stepper.asm) against a step/direction driver model with the A4988's
logic timing (escmodels/stepper.py), at 8 MHz. The speed ramp is checked against physics,
not against the firmware's recurrence: a constant acceleration A has v^2 = 2 A s, so over the
ramp (v_j^2 - v_i^2) / (2 (j - i)) must come out as A (the host's c0 = 0.676 x sqrt(2 / A)
seconds is AVR446's start); the stop ramp must mirror the start; top speed is CMIN."""

from __future__ import annotations

import math
from pathlib import Path

from escasm import assemble
from escmodels import Board, HostScript
from escmodels.stepper import StepDirDriver
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
KHZ = 8000
MS = KHZ


def c0_us(accel):
    return round(0.676e6 * math.sqrt(2 / accel))


def run(pushes, ms, khz=KHZ):
    im = assemble(f'.equ stepper.CLK_KHZ, {khz}\n.include "stepper.asm"\n', base_dir=FW)
    drv = StepDirDriver(0, 1, 1e6 / khz)
    host = HostScript(pushes, drain=[0], poll=khz)
    board = Board(drv, host, pullups=0)
    m = Machine(im, board=board)
    host.machine = m
    m.run(int(ms * khz), stop_when_idle=False)
    return m, drv, host, board


def intervals(drv):
    t = [s[0] for s in drv.steps]
    return [b - a for a, b in zip(t, t[1:])]


def accel_estimate(iv, i, j):
    v = [1e6 / x for x in iv]
    return (v[j] ** 2 - v[i] ** 2) / (2 * (j - i))


def move(accel, cmin, n, forward=True):
    return [(100, 0, 1 << 28 | c0_us(accel)), (200, 0, 2 << 28 | cmin),
            (300, 0, 3 << 28 | (1 << 24 if forward else 0) | n)]


def test_short_move_ramps_up_and_down():
    """200 steps at 20,000 steps/s^2, top speed not reached: up for 99 steps, down the same."""
    m, drv, host, board = run(move(20000, 200, 200), 230)
    assert drv.position == 200 and len(drv.steps) == 200 and host.received[0] == [200]
    assert not drv.violations and not board.errors() and not m.ff.valid, drv.violations
    iv = intervals(drv)
    assert abs(iv[0] - c0_us(20000)) < 1
    assert all(abs(iv[i] - iv[-1 - i]) < 0.01 for i in range(99))               # mirrored
    assert abs(accel_estimate(iv, 10, 98) / 20000 - 1) < 0.02


def test_trapezoid_reaches_top_speed():
    """150 steps at 50,000 steps/s^2 to 2,000 steps/s: a cruise of exactly 500 us steps."""
    m, drv, host, board = run(move(50000, 500, 150), 130)
    assert drv.position == 150 and not drv.violations, drv.violations
    iv = intervals(drv)
    cruise = [x for x in iv if abs(x - 500) < 0.01]
    k = next(i for i, x in enumerate(iv) if abs(x - 500) < 0.01)
    assert len(cruise) == 149 - 2 * k and min(iv) > 499.99
    assert abs(accel_estimate(iv, 10, k - 1) / 50000 - 1) < 0.02
    assert all(abs(iv[i] - iv[-1 - i]) < 0.01 for i in range(k))


def test_backwards_with_a_ramp_longer_than_the_table():
    """300 steps back at 20,000 steps/s^2 with a top speed it cannot reach in 128 steps: it
    ramps for 128 steps, cruises at the speed reached, and ramps down."""
    m, drv, host, board = run(move(20000, 50, 300, forward=False), 270)
    assert drv.position == -300 and host.received[0] == [300] and not drv.violations
    iv = intervals(drv)
    mid = iv[128:-128]
    assert len(mid) == 43 and max(mid) - min(mid) < 0.01 and mid[0] <= iv[127]
    assert abs(accel_estimate(iv, 10, 127) / 20000 - 1) < 0.02


def test_one_step_and_the_pmin_clamp():
    """One step; then c0 and CMIN below PMIN are raised to it: 20 steps 50 us apart."""
    pushes = [(100, 0, 3 << 28 | 1 << 24 | 1), (2 * MS, 0, 1 << 28 | 10), (2 * MS + 10, 0, 2 << 28 | 10),
              (2 * MS + 20, 0, 3 << 28 | 1 << 24 | 20)]
    m, drv, host, board = run(pushes, 5)
    assert host.received[0] == [1, 20] and drv.position == 21 and not drv.violations
    assert [round(x, 3) for x in intervals(drv)[1:]] == [50.0] * 19


def test_certificate_matches_telemetry():
    from escert import certify
    im = assemble('.equ stepper.CLK_KHZ, 25000\n.include "stepper.asm"\n', base_dir=FW)
    assert [(c.status, c.min_lead) for c in certify(im)] == [("CHECKED", 40)]
