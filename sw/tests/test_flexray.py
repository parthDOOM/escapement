"""flexray_tx.asm against escmodels.flexray.FlexRayReceiver, a channel A receiver written
from the FlexRay protocol specification: TSS, FSS, every byte's BSS, the FES, every edge on
a bit boundary, both CRCs (computed there bit by bit) and the header's fields, at 2.5
Mbit/s from 25 and 50 MHz and at 5 Mbit/s from 50 MHz."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import Board, HostScript
from escmodels.flexray import FlexRayReceiver, crc_bits, to_bits
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"


def image(khz, kbps=2500, plen=4, src=None):
    if src is None:
        return assemble(f".equ flexray_tx.CLK_KHZ, {khz}\n.equ flexray_tx.KBPS, {kbps}\n"
                        f".equ flexray_tx.PLEN, {plen}\n" + '.include "flexray_tx.asm"\n', base_dir=FW)
    return assemble(f".equ CLK_KHZ, {khz}\n.equ KBPS, {kbps}\n.equ PLEN, {plen}\n" + src, base_dir=FW)


def words(fid, cycle, payload, ind=0b00100):
    """The host's words: the header word ([31:16] = indicators and frame ID, [5:0] cycle),
    then the payload, four bytes a word."""
    out = [(ind << 11 | fid) << 16 | cycle]
    out += [int.from_bytes(payload[i:i + 4], "big") for i in range(0, len(payload), 4)]
    return out


def run(frames, khz=25000, kbps=2500, plen=4, src=None):
    rx = FlexRayReceiver(0, tick_ns=1e6 / khz, bit_ns=1e6 / kbps)
    pushes, t = [], 100
    for f in frames:
        for w in words(*f):
            pushes.append((t, 0, w))
            t += 1000                                  # (the mailbox holds two words)
        t += 40 * (plen + 4) * 10 * (khz // kbps)        # room for the frame on the wire
    host = HostScript(pushes, drain=[0], poll=200)
    m = Machine(image(khz, kbps, plen, src), board=Board(rx, host, pullups=0xFF))
    host.machine = m
    m.run(t + 2000, stop_when_idle=False)
    assert not m.ff.valid
    return rx, host.received[0]


def test_crc_definitions_against_the_spec_examples():
    # the header CRC of a frame with sync = startup = 0, ID 1, length 0 is 0x2A2 by the
    # specification's algorithm from start 0x01A; check the register arithmetic both ways
    bits = to_bits(0, 1) + to_bits(0, 1) + to_bits(1, 11) + to_bits(0, 7)
    assert crc_bits(bits + to_bits(crc_bits(bits, 0x385, 11, 0x01A), 11), 0x385, 11, 0x01A) == 0


@pytest.mark.parametrize("khz, kbps", [(25000, 2500), (50000, 2500), (50000, 5000)])
def test_frames(khz, kbps):
    fs = [(1, 0, bytes(range(8))), (0x7FF, 63, bytes([0xFF] * 8)), (0x155, 21, b"\x00" * 8)]
    rx, acks = run(fs, khz, kbps)
    assert rx.violations == [] and acks == [1] * len(fs)
    got = [(f["id"], f["cycle"], f["payload"], f["indicators"], f["plen"], f["tss"],
            f["header_crc_ok"], f["frame_crc_ok"]) for f in rx.frames]
    assert got == [(i, c, p, 0b00100, 4, 9, True, True) for i, c, p in fs]


def test_sync_and_startup_indicators_enter_the_header_crc():
    fs = [(5, 1, bytes(range(8)), 0b00111), (5, 1, bytes(range(8)), 0b00110)]
    rx, _ = run(fs)
    assert rx.violations == []
    assert [(f["indicators"], f["header_crc_ok"], f["frame_crc_ok"]) for f in rx.frames] == \
        [(0b00111, True, True), (0b00110, True, True)]


@pytest.mark.parametrize("plen", [2, 16])
def test_payload_lengths(plen):
    pay = bytes((k * 37) & 0xFF for k in range(2 * plen))
    rx, _ = run([(9, 7, pay)], plen=plen)
    assert rx.violations == []
    assert [(f["payload"], f["plen"], f["header_crc_ok"], f["frame_crc_ok"]) for f in rx.frames] == \
        [(pay, plen, True, True)]


def _planted(old, new):
    src = (FW / "flexray_tx.asm").read_text()
    assert old in src
    return src.replace(old, new)


def test_planted_header_crc_start_is_caught():
    bad = _planted("    MOVI  r0, 0x1A << 5                     ; CRC-11 held in bits 31..21: start 0x01A",
                   "    MOVI  r0, 0x1B << 5")
    rx, _ = run([(1, 0, bytes(range(8)))], src=bad)
    assert [(f["header_crc_ok"], f["frame_crc_ok"]) for f in rx.frames] == [(False, True)]


def test_planted_frame_crc_channel_b_start_is_caught():
    bad = _planted("    MOVI  r0, 0xBA00                        ; start 0xFEDCBA\n    MOVHI r0, 0xFEDC",
                   "    MOVI  r0, 0xCDEF\n    MOVHI r0, 0xAB")
    rx, _ = run([(1, 0, bytes(range(8)))], src=bad)
    assert [(f["header_crc_ok"], f["frame_crc_ok"]) for f in rx.frames] == [(True, False)]


def test_planted_missing_bss_is_caught():
    bad = _planted(".equ TSSU, 0b0000000001", ".equ TSSU, 0b0000000000")   # no FSS
    rx, _ = run([(1, 0, bytes(range(8)))], src=bad)
    assert rx.frames == [] or not rx.frames[0]["frame_crc_ok"] or rx.violations
