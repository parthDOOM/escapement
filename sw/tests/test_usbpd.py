"""USB PD transmitter (firmware/usbpd_tx.asm) against the CC-wire receiver model of
escmodels/usbpd.py: messages with 0, 1 and 7 data objects, the CRC the chip computes
checked against zlib's CRC-32, the bit rate, and the line driven low at the end."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import Board, HostScript
from escmodels.usbpd import UsbPdSink
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"


def header(msg_type, n, msg_id=0, source=1, dfp=1, rev=2):
    """A message header: type [4:0], data role [5], spec revision [7:6], power role [8],
    message ID [11:9], number of data objects [14:12]."""
    return msg_type | dfp << 5 | rev << 6 | source << 8 | msg_id << 9 | n << 12


# Source_Capabilities (data message 1) with one fixed-supply PDO: 5 V (100 x 50 mV), 3 A (300 x 10 mA)
SRC_CAPS = (header(1, 1), [100 << 10 | 300])
GOODCRC = (header(1, 0, msg_id=3), [])                  # control message 1: GoodCRC
SEVEN = (header(15, 7, msg_id=7), [0xFFFFFFFF, 0, 0xDEADBEEF, 0x12345678, 0x80000001, 0x0F0F0F0F, 0xA5A5A5A5])


def run(khz, messages, per_ms=2):
    im = assemble(f'.equ usbpd_tx.CLK_KHZ, {khz}\n.include "usbpd_tx.asm"\n', base_dir=FW)
    sink = UsbPdSink(0, 1e6 / khz)
    pushes, t = [], 200
    for h, data in messages:
        for w in [h] + data:
            pushes.append((t, 0, w))
            t += 50
        t += per_ms * khz
    host = HostScript(pushes, drain=[0], poll=khz // 20)
    board = Board(sink, host, pullups=0)                 # CC idles low here (released)
    m = Machine(im, board=board)
    host.machine = m
    m.run(t + khz, stop_when_idle=False)
    sink.finish()
    return m, sink, host, board


@pytest.mark.parametrize("khz", [25000, 50000])
def test_messages(khz):
    msgs = [SRC_CAPS, GOODCRC, SEVEN] if khz == 25000 else [SRC_CAPS]
    m, sink, host, board = run(khz, msgs)
    got = [(x["header"], x["data"]) for x in sink.messages]
    assert got == msgs and all(x["crc_ok"] and x["eop"] for x in sink.messages), sink.violations
    assert not sink.violations, sink.violations
    assert host.received[0] == [1] * len(msgs)
    assert not board.errors() and not m.ff.valid


def test_bit_stream_length_and_end_level():
    """Preamble 64 + SOP 20 + header 20 + 40 per object + CRC 40 + EOP 5 bits, then the
    line low: after EOP at most the hold-low edge (read as one 0) and nothing else."""
    for msg, n in ((SRC_CAPS, 1), (GOODCRC, 0), (SEVEN, 7)):
        m, sink, host, board = run(25000, [msg])
        x = sink.messages[0]
        assert x["eop_end"] == 64 + 20 + 20 + 40 * n + 40 + 5 and x["bits"] - x["eop_end"] <= 1, x
        assert board.level(0)[-1] == 0


def test_bit_rate_at_25_mhz():
    """84 ticks a bit at 25 MHz: 297.6 kbit/s, every whole bit exactly 84 ticks and every
    half bit 42 (no jitter: every edge is an event)."""
    m, sink, host, board = run(25000, [SEVEN])
    lv = board.level(0)
    edges = [t for t in range(1, len(lv)) if lv[t] != lv[t - 1]]
    gaps = {b - a for a, b in zip(edges, edges[1:]) if b - a < 200}
    assert gaps <= {42, 84}, gaps


def test_certificate_and_clock_floor():
    from escert import certify
    im = assemble('.equ usbpd_tx.CLK_KHZ, 25000\n.include "usbpd_tx.asm"\n', base_dir=FW)
    certs = certify(im)
    assert [(c.status, c.min_lead) for c in certs] == [("CHECKED", 9)]
    with pytest.raises(Exception, match="20 MHz"):
        assemble('.equ usbpd_tx.CLK_KHZ, 16000\n.include "usbpd_tx.asm"\n', base_dir=FW)
    m, *_ = run(25000, [SRC_CAPS])
    assert m.ctx[0].min_lead >= certs[0].min_lead
