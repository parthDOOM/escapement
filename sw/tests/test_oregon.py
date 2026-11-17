"""oregon_tx.asm (an Oregon Scientific v2.1 THGR122NX sensor) against
escmodels.oregon.OregonV21Receiver, a weather station's receiver written from the protocol
description: every RF interval against the 1024 Hz clock, the doubled bits, the preamble
and sync, the checksum, the repeat 10.9 ms later, and the reading's fields. The model times
in microseconds, so low clocks keep the runs short; escert certifies the program at 33 to
50 MHz."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import Board, HostScript
from escmodels.oregon import OregonV21Receiver
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
MSG_MS = 400                       # two 187.5 ms copies and the gap, with room


def image(khz, src=None, equ=""):
    """The program at `khz`; `src` is a planted copy, assembled as the main file (its names
    then have no module prefix)."""
    if src is None:
        return assemble(f".equ oregon_tx.CLK_KHZ, {khz}\n" + equ + '.include "oregon_tx.asm"\n', base_dir=FW)
    return assemble(f".equ CLK_KHZ, {khz}\n" + equ.replace("oregon_tx.", "") + src, base_dir=FW)


def word(tenths, hum, batt_low=False):
    t = abs(tenths)
    bcd = (t % 10) | (t // 10 % 10) << 4 | (t // 100 % 10) << 8
    h = (hum % 10) | (hum // 10 % 10) << 4
    return bcd | (tenths < 0) << 12 | h << 16 | batt_low << 24


def run(words, khz=500, src=None, equ=""):
    rx = OregonV21Receiver(0, tick_ns=1e6 / khz)
    host = HostScript([(100 + int(k * MSG_MS * khz), 0, w) for k, w in enumerate(words)],
                      drain=[0], poll=500)
    m = Machine(image(khz, src, equ), board=Board(rx, host, pullups=0))
    host.machine = m
    m.run(int((len(words) * MSG_MS + 10) * khz), stop_when_idle=False)
    assert not m.ff.valid
    return rx, host.received[0]


@pytest.mark.parametrize("khz", [500, 4000])
def test_a_reading_twice(khz):
    rx, acks = run([word(235, 47)], khz)
    assert rx.violations == [] and acks == [1]
    assert rx.readings() == [(1, 0xBB, False, 235, 47)] * 2
    (_, _, s0, e0), (_, _, s1, _) = rx.messages
    gap_ms = (s1 - e0) / khz
    assert abs(gap_ms - 10.9) < 0.6          # from the first copy's last edge to the second's first


def test_negative_low_battery_and_extremes():
    cases = [(-91, 5, True), (999, 99, False), (0, 0, False), (-1, 100 - 1, False)]
    rx, acks = run([word(*c) for c in cases])
    assert rx.violations == [] and acks == [1] * len(cases)
    assert rx.readings() == [(1, 0xBB, b, t, h) for t, h, b in cases for _ in (0, 1)]


@pytest.mark.parametrize("ch, rc", [(2, 0x5A), (3, 0x01)])
def test_channel_and_rolling_code_are_parameters(ch, rc):
    rx, _ = run([word(178, 60)], equ=f".equ oregon_tx.CH, {ch}\n.equ oregon_tx.RC, {rc}\n")
    assert rx.readings() == [(ch, rc, False, 178, 60)] * 2


def _planted(old, new):
    src = (FW / "oregon_tx.asm").read_text()
    assert old in src
    return src.replace(old, new)


def test_planted_checksum_missing_a_nibble_is_caught():
    bad = _planted("    MOVI  r2, ID3\n", "    MOVI  r2, ID3 - 1\n")
    rx, _ = run([word(235, 47)], src=bad)
    assert rx.messages and not any(ok for _, ok, _, _ in rx.messages) and rx.readings() == []


def test_planted_undoubled_bit_is_caught():
    """A 1 sent with the halves of a 0's inverse (off, on, off, on) breaks the doubling."""
    bad = _planted("""a_one:
    EMIT  RF, 0, RF                         ; 1: off, on, on, off
    ADDT  H
    EMIT  RF, RF, RF
    ADDT  H2
    EMIT  RF, 0, RF
    ADDT  H""", """a_one:
    EMIT  RF, 0, RF
    ADDT  H
    EMIT  RF, RF, RF
    ADDT  H
    EMIT  RF, 0, RF
    ADDT  H
    EMIT  RF, RF, RF
    ADDT  H""")
    rx, _ = run([word(235, 47)], src=bad)
    assert rx.readings() == []
