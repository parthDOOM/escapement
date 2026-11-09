"""Wiegand panel receiver (firmware/wiegand_rx.asm) against a Wiegand reader model
(escmodels.wiegand.WiegandSender), at the edges of the interface's limits: 20 and 100 us
pulses, 200 us and 20 ms intervals, 26- and 34-bit frames; and against the chip's own
generated transmitter, wiegand_tx, on the other pins."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import Board, HostScript, WiegandReceiver, WiegandSender
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"


def card26(facility, card):
    """26-bit frame: even parity over the first 12 data bits, odd over the last 12."""
    data = [(facility >> (7 - k)) & 1 for k in range(8)] + [(card >> (15 - k)) & 1 for k in range(16)]
    return [sum(data[:12]) % 2] + data + [1 - sum(data[12:]) % 2]


def run(khz, frames, **timing):
    im = assemble(f'.equ wiegand_rx.CLK_KHZ, {khz}\n.include "wiegand_rx.asm"\n', base_dir=FW)
    snd = WiegandSender(0, 1, 1e6 / khz, frames, **timing)
    host = HostScript([], drain=[0], poll=khz // 10)  # the host reads every 100 us
    m = Machine(im, board=Board(snd, host))
    host.machine = m
    last = snd.pulses[-1][0] if snd.pulses else 0
    m.run(last + khz * 30, stop_when_idle=False)     # past the last frame's 22 ms timeout
    assert not m.ff.valid
    out = host.received[0]
    return [(out[i], out[i + 1] << 32 | out[i + 2]) for i in range(0, len(out) - 2, 3)]


def value(bits):
    return int("".join(map(str, bits)), 2)


@pytest.mark.parametrize("khz", [4000, 25000])
def test_26_and_34_bit_frames(khz):
    frames = [card26(0x2A, 0xBEEF), [1, 0] * 17, card26(0, 0)]
    if khz == 25000:
        frames = frames[:1]
    got = run(khz, frames)
    assert got == [(len(f), value(f)) for f in frames], got


@pytest.mark.parametrize("width,interval", [(20, 200), (100, 200), (20, 20_000), (100, 20_000)])
def test_limits_of_the_interface(width, interval):
    """The shortest and longest pulses at the shortest and longest intervals: every bit,
    and a 20 ms interval does not end the frame (the timeout is 22 ms)."""
    f = card26(0x81, 0x1234)
    gap = max(25.0, interval / 1000 + 25)
    got = run(4000, [f, f], width_us=width, interval_us=interval, gap_ms=gap)
    assert got == [(26, value(f))] * 2, got


def test_stuck_line_is_flagged():
    """A line held low for 2 ms is a bit with flag [8]; the frame still completes."""
    f = card26(1, 2)
    got = run(4000, [f], width_us=2000, interval_us=4000)
    assert got and got[0][0] == 26 | 0x100 and got[0][1] == value(f), got


def test_long_frames():
    """40 and 64 bits whole; past 64 the last 64 are kept and [7] is set."""
    f40 = [1] + [0] * 3 + [1, 1] * 18
    f64 = [1, 0, 0, 1] * 16
    f70 = [1] * 6 + f64
    got = run(4000, [f40, f64, f70])
    assert got == [(40, value(f40)), (64, value(f64)), (70 | 0x80, value(f64))], got


def test_chip_to_chip_with_wiegand_tx():
    """The generated transmitter on one context and the receiver on another of the same
    image: the frames the host gives one come out of the other."""
    src = ('.equ wiegand_tx.CLK_KHZ, 4000\n.equ wiegand_tx.CTX, 0\n.include "wiegand_tx.asm"\n'
           '.equ wiegand_rx.CLK_KHZ, 4000\n.equ wiegand_rx.CTX, 1\n.include "wiegand_rx.asm"\n')
    im = assemble(src, base_dir=FW)
    mon = WiegandReceiver(0, 1, 250.0)
    frames = [card26(0x2A, 0xBEEF), card26(0x7F, 0x0001)]
    host = HostScript([(100 + k * 4000 * 60, 0, value(f)) for k, f in enumerate(frames)],
                      drain=[0, 1], poll=400)
    m = Machine(im, board=Board(mon, host))
    host.machine = m
    m.run(4000 * 60 * len(frames) + 4000 * 30, stop_when_idle=False)
    out = host.received[1]
    assert [(out[i], out[i + 1] << 32 | out[i + 2]) for i in range(0, len(out), 3)] == [(26, value(f)) for f in frames]
    mon.finish()
    assert mon.frames == frames and not mon.violations and not m.ff.valid
