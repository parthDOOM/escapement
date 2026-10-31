"""firmware/servo.asm (v1.6): eight RC servo pulses per 22 ms frame, widths from the host
in microseconds, each built from constant delays so escert certifies any value."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"


def pulses(m, pin):
    lv = [(p["uio_out"] >> pin) & 1 if (p["uio_oe"] >> pin) & 1 else 0 for p in m.pins]
    out, t = [], 1
    while t < len(lv):
        if lv[t] and not lv[t - 1]:
            s = t
            while t < len(lv) and lv[t]:
                t += 1
            if t < len(lv):                       # (one still high at the end is cut short)
                out.append((s, t - s))
        t += 1
    return out


@pytest.mark.parametrize("khz", [8000, 25000])
def test_widths_and_frame(khz):
    m = Machine(assemble(f'.equ servo.CLK_KHZ, {khz}\n.include "servo.asm"\n', base_dir=FW))
    us = {0: 1000, 3: 2000, 7: 1500 + 1047, 5: 500}          # values 500.. -> width 500 + v
    m.run(1000, stop_when_idle=False)
    for ch, w in us.items():                               # one update a frame (see servo.asm)
        while len(m.ctx[0].rx) >= 2:
            m.run(1000, stop_when_idle=False)
        m.host_rx_push(0, ch << 16 | (w - 500))
        m.run(200, stop_when_idle=False)
    m.run(6 * 22 * khz, stop_when_idle=False)              # all applied, then steady frames
    for ch in range(8):
        p = pulses(m, ch)
        want = us.get(ch, 1500)
        assert p[-1][1] == pytest.approx(want * khz / 1000, abs=12)   # bitwise rounding
        assert p[-1][0] - p[-2][0] == 22 * khz                          # 22 ms frames
    # in one frame: one pin at a time, in channel order, each starting as the last ends
    f0 = pulses(m, 0)[-2][0]
    frame = [next(p for p in pulses(m, ch) if f0 <= p[0] < f0 + 22 * khz) for ch in range(8)]
    assert all(b[0] == a[0] + a[1] for a, b in zip(frame, frame[1:]))
    assert not m.ff.valid
