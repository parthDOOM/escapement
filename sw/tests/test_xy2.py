"""xy2_100.asm (an XY2-100 galvanometer controller) against escmodels.xy2.Xy2Scanner, a
scanner written from the XY2-100 interface description: every clock period, data steady at
each falling edge, 20-bit frames with control bits 001 and even parity, and the positions
of both axes, frame by frame, at 40, 48 and 50 MHz."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import Board
from escmodels.xy2 import Xy2Scanner
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
T0 = 16384


def image(khz, src=None):
    if src is None:
        return assemble(f'.equ xy2_100.CLK_KHZ, {khz}\n.include "xy2_100.asm"\n', base_dir=FW)
    return assemble(f".equ CLK_KHZ, {khz}\n" + src, base_dir=FW)      # a planted copy, as the main file


def signed(v):
    return v - 65536 if v & 0x8000 else v


def run(khz, points=None, n=None, frames=12, src=None):
    im = image(khz, src)
    if points is not None:
        im.tape[0] = len(points) if n is None else n
        for k, (x, y) in enumerate(points):
            im.tape[1 + k] = (x & 0xFFFF) << 16 | (y & 0xFFFF)
    sc = Xy2Scanner(0, 1, {"x": 2, "y": 3}, tick_ns=1e6 / khz)
    m = Machine(im, board=Board(sc, pullups=0))
    bit = khz // 2000
    m.run(T0 + (frames * 20 + 2) * bit, stop_when_idle=False)
    assert not m.ff.valid, m.ff
    return sc, bit


def check(sc, bit, want_x, want_y, frames=12):
    assert sc.violations == []
    xs, ys = sc.frames["x"], sc.frames["y"]
    assert [v for v, _ in xs] == [want_x[k % len(want_x)] for k in range(frames)]
    assert [v for v, _ in ys] == [want_y[k % len(want_y)] for k in range(frames)]
    # frames back to back, 20 bits apart; the first parity bit taken at T0 + 19 bits + the
    # clock's high time (plus the model's fixed view of the pins, 2 ticks)
    ts = [t for _, t in xs]
    assert ts == [ts[0] + 20 * bit * k for k in range(frames)]
    assert ts[0] - (T0 + 19 * bit + bit // 2) in (0, 1, 2)
    assert [t for _, t in ys] == ts


@pytest.mark.parametrize("khz", [40000, 48000, 50000])
def test_the_default_square(khz):
    sc, bit = run(khz)
    q = 0x4000
    check(sc, bit, [-q, q, q, -q], [-q, -q, q, q])


@pytest.mark.parametrize("khz", [40000, 50000])
def test_a_host_figure_with_every_parity(khz):
    pts = [(0x7FFF, -0x8000), (0, 1), (-1, 3), (0x1234, -0x1234), (0x5555, 0x2AAA),
           (-0x8000, 0x7FFF), (7, 0)]
    sc, bit = run(khz, pts, frames=15)
    check(sc, bit, [signed(x & 0xFFFF) for x, _ in pts], [signed(y & 0xFFFF) for _, y in pts], 15)


def test_n_zero_is_64_points_and_n_wraps_at_64():
    pts = [(k * 512 - 0x4000, 0x4000 - k * 512) for k in range(64)]
    sc, bit = run(40000, pts, n=0, frames=66)
    check(sc, bit, [x for x, _ in pts], [y for _, y in pts], 66)
    sc, bit = run(40000, pts[:3], n=65, frames=4)          # 65 is taken as 1
    check(sc, bit, [pts[0][0]], [pts[0][1]], 4)


def test_planted_parity_bug_is_caught():
    src = (FW / "xy2_100.asm").read_text()
    bad = src.replace("    MOVI  r0, 1 << 12            ; even position + the control 1: parity 1",
                      "    MOVI  r0, 0")
    assert bad != src
    sc, _ = run(40000, [(2, 3)], src=bad, frames=4)        # 2 has one bit set, 3 two
    assert any("axis y: odd parity" in v for v in sc.violations)
    assert [v for v, _ in sc.frames["x"]] == [2] * 4 and sc.frames["y"] == []


def test_planted_late_data_is_caught():
    """A data change half a bit late (at the falling edge) is a violation."""
    src = (FW / "xy2_100.asm").read_text()
    bad = src.replace("    ADDT  LEADIN - 16\nx_frame:", "    ADDT  LEADIN - 16 + BIT // 2\nx_frame:")
    assert bad != src
    sc, _ = run(40000, src=bad, frames=4)
    assert any("not at a rising edge" in v for v in sc.violations)
