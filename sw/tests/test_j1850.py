"""j1850_vpw.asm (an SAE J1850 VPW transmitter) against escmodels.j1850.VpwReceiver, a
node written from the standard's timing that checks every symbol's length, the SOF, the
CRC-8 (computed bit by bit there, by nibble table on the chip) and the space between
frames; at 4, 25, 33 and 50 MHz."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import Board, HostScript
from escmodels.j1850 import VpwReceiver, crc8_j1850
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"


def image(khz, src=None):
    if src is None:
        return assemble(f".equ j1850_vpw.CLK_KHZ, {khz}\n" + '.include "j1850_vpw.asm"\n', base_dir=FW)
    return assemble(f".equ CLK_KHZ, {khz}\n" + src, base_dir=FW)       # a planted copy, as the main file


def host_words(frames, khz, apart_ms=13):
    """Each frame's bytes, the last flagged, 100 ticks apart; frames `apart_ms` apart (13 ms:
    a SOF, up to 12 bytes at no more than 128 us a bit, and the space after)."""
    out = []
    for i, f in enumerate(frames):
        t0 = 100 + int(i * apart_ms * khz)
        out += [(t0 + 100 * k, 0, b | (0x100 if k == len(f) - 1 else 0)) for k, b in enumerate(f)]
    return out


def run(frames, khz=25000, src=None, apart_ms=13, ms=None):
    rx = VpwReceiver(0, tick_ns=1e6 / khz)
    host = HostScript(host_words(frames, khz, apart_ms), drain=[0], poll=500)
    m = Machine(image(khz, src), board=Board(rx, host, pullups=0))
    host.machine = m
    m.run(int((ms or len(frames) * 13 + 1) * khz), stop_when_idle=False)
    assert not m.ff.valid
    return rx, host.received[0]


OBD = bytes([0x68, 0x6A, 0xF1, 0x01, 0x00])         # OBD-II mode 1, PID 0, to the engine


def test_crc_is_the_catalogue_crc8_sae_j1850():
    assert crc8_j1850(b"123456789") == 0x4B and crc8_j1850(OBD) == 0x17


@pytest.mark.parametrize("khz", [4000, 25000, 33000, 50000])
def test_an_obd_request(khz):
    rx, acks = run([OBD], khz)
    assert rx.violations == [] and acks == [1]
    assert [(d, ok) for d, ok, _ in rx.frames] == [(OBD, True)]


def test_frames_back_to_back_and_every_bit_pattern():
    frames = [OBD, bytes([0x00, 0x00]), bytes([0xFF, 0xFF, 0xFF]), bytes(range(0x10, 0x1B)),
              bytes([0x48, 0x6B, 0x10, 0x41, 0x00, 0xBE, 0x1F, 0xB8, 0x10])]
    rx, acks = run(frames)
    assert rx.violations == [] and acks == [1] * len(frames)
    assert [(d, ok) for d, ok, _ in rx.frames] == [(f, True) for f in frames]


def test_the_eleventh_byte_ends_the_frame():
    long = bytes(range(1, 15))                        # 14 bytes, none flagged until the last
    khz = 25000
    rx = VpwReceiver(0, tick_ns=40)
    # the last three after the first frame: the mailbox holds two words, and a host waits
    words = [(100 + 100 * k, 0, b | (0x100 if k == 13 else 0)) for k, b in enumerate(long)]
    host = HostScript([(t if k < 11 else t + 13 * khz, c, w) for k, (t, c, w) in enumerate(words)],
                      drain=[0], poll=500)
    m = Machine(image(khz), board=Board(rx, host, pullups=0))
    host.machine = m
    m.run(30 * khz, stop_when_idle=False)
    acks = host.received[0]
    assert rx.violations == [] and acks == [1, 1] and not m.ff.valid
    assert [(d, ok) for d, ok, _ in rx.frames] == [(long[:11], True), (long[11:], True)]


def _planted(old, new):
    src = (FW / "j1850_vpw.asm").read_text()
    assert old in src
    return src.replace(old, new)


def test_planted_wrong_crc_start_is_caught():
    rx, _ = run([OBD], src=_planted("    MOVI  r1, 0xFF               ; the CRC's start value",
                                    "    MOVI  r1, 0"))
    assert [(d, ok) for d, ok, _ in rx.frames] == [(OBD, False)]


def test_planted_swapped_active_lengths_are_caught():
    bad = _planted("    JNC   a_long\n    ADDT  T_SHORT", "    JC    a_long\n    ADDT  T_SHORT")
    rx, _ = run([OBD], src=bad)
    assert [(d, ok) for d, ok, _ in rx.frames] != [(OBD, True)]


# two-byte frames, the second waiting in the mailbox while the first is sent: back to back
BACK = dict(apart_ms=1, ms=8)


def test_frames_waiting_follow_after_the_full_interframe_space():
    rx, acks = run([b"\x00\x00", b"\xff\xff"], **BACK)
    assert rx.violations == [] and acks == [1, 1]
    assert [(d, ok) for d, ok, _ in rx.frames] == [(b"\x00\x00", True), (b"\xff\xff", True)]


def test_planted_short_interframe_space_is_caught():
    bad = _planted(".equ T_IFS, CLK_KHZ * 300 // 1000", ".equ T_IFS, CLK_KHZ * 250 // 1000")
    rx, _ = run([b"\x00\x00", b"\xff\xff"], src=bad, **BACK)
    assert any("280 us" in v for v in rx.violations)
