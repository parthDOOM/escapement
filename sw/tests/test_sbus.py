"""firmware/sbus_rx.asm (v1.6): S.BUS frames decoded into the tape."""

from __future__ import annotations

import random
from pathlib import Path

from escasm import assemble
from esciss import Machine
from escmodels import Board
from escmodels.sbus import SbusSource, decode, encode

FW = Path(__file__).resolve().parents[2] / "firmware"
TPB = 250                                   # 25 MHz, 100000 baud
TB = 0xC0


def run(frames, ticks, equ="", drain=True, **kw):
    img = assemble(equ + '.include "sbus_rx.asm"\n', base_dir=FW)
    m = Machine(img, board=Board(SbusSource(0, TPB, frames, **kw), pullups=0))
    notes = []
    while m.now < ticks:
        m.run(1000, stop_when_idle=False)
        v = m.host_tx_pop(0) if drain else None
        if v is not None:
            notes.append(v)
    return m, notes


def published(m):
    return [m.tape[TB + k] for k in range(16)], m.tape[TB + 16], m.tape[TB + 17], m.tape[TB + 18]


def test_frames_decoded():
    rng = random.Random(7)
    chans = [[rng.randrange(2048) for _ in range(16)] for _ in range(3)]
    chans[1] = [172, 1811, 992, 0, 2047] + [1024] * 11            # the FrSky range ends
    frames = [(2000 + 175_000 * i, encode(c, flags=i)) for i, c in enumerate(chans)]
    seen = []
    img = assemble('.include "sbus_rx.asm"\n', base_dir=FW)
    m = Machine(img, board=Board(SbusSource(0, TPB, frames), pullups=0))
    notes = []
    for i in range(3):
        m.run(frames[i][0] + 25 * 12 * TPB + 20_000 - m.now, stop_when_idle=False)
        seen.append(published(m))
        v = m.host_tx_pop(0)
        notes.append(v)
        m.run(1, stop_when_idle=False)
    for i, (ch, fl, seq, errs) in enumerate(seen):
        assert ch == chans[i] and fl == i and seq == 2 * (i + 1) and errs == 0
    assert notes == [1 | 0 << 16, 2 | 1 << 16, 3 | 2 << 16]


def test_flags_and_sbus2_footer():
    frames = [(2000, encode([5] * 16, flags=0x0C)), (100_000, encode([6] * 16, footer=0x14)),
              (200_000, encode([7] * 16, footer=0x24, flags=0x03))]
    m, notes = run(frames, 300_000)
    ch, fl, seq, errs = published(m)
    assert ch == [7] * 16 and fl == 0x03 and seq == 6 and errs == 0
    assert [n >> 16 for n in notes] == [0x0C, 0, 0x03]


def test_bad_frames_dropped():
    good = encode(list(range(100, 1700, 100)))
    bad_head = bytes([0x0E]) + good[1:]
    bad_foot = good[:24] + bytes([0x01])
    frames = [(2000, good), (100_000, good), (200_000, bad_head), (300_000, bad_foot),
              (400_000, good[:24]), (500_000, good + b"\x0f"), (600_000, good), (700_000, good)]
    m, notes = run(frames, 800_000, bad_bit={1: (5, 9), 6: (20, 10)})   # parity, stop
    ch, fl, seq, errs = published(m)
    assert decode(good)[0] == ch and seq == 4 and errs == 6
    assert [n & 0xFFFF for n in notes] == [1, 2]


def test_not_inverted_and_back_to_back():
    """INV 0 (a line inverted back), frames every 7 ms (high speed), small gaps between
    characters."""
    rng = random.Random(3)
    chans = [[rng.randrange(2048) for _ in range(16)] for _ in range(6)]
    frames = [(2000 + 175_000 * i, encode(c)) for i, c in enumerate(chans)]
    m, notes = run(frames, 2000 + 175_000 * 6, equ=".equ sbus_rx.INV, 0\n", inverted=False,
                   gap_bits=1.0)
    ch, fl, seq, errs = published(m)
    assert ch == chans[-1] and seq == 12 and errs == 0
    assert [n & 0xFFFF for n in notes] == [1, 2, 3, 4, 5, 6]


def test_mailbox_full_skips_the_notice():
    frames = [(2000 + 100_000 * i, encode([i] * 16)) for i in range(4)]
    m, _ = run(frames, 420_000, drain=False)
    assert published(m)[0] == [3] * 16 and published(m)[2] == 8
    assert list(m.ctx[0].tx) == [1, 2] and m.irq_pending == 0
