"""AVR in-system programmer (firmware/avr_isp.asm) against an ATmega328P model in serial
programming mode (escmodels/avr.py), at 25 MHz with SCK at 125 kHz."""

from __future__ import annotations

from pathlib import Path

from escasm import assemble
from escmodels import Board, HostScript
from escmodels.avr import AvrIspTarget
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
KHZ = 25000
MS = KHZ


def run(pushes, ms, **target):
    im = assemble(f'.equ avr_isp.CLK_KHZ, {KHZ}\n.include "avr_isp.asm"\n', base_dir=FW)
    avr = AvrIspTarget(4, 5, 6, 7, 1e6 / KHZ, **target)
    host = HostScript(pushes, drain=[0], poll=2500)
    board = Board(avr, host)
    m = Machine(im, board=board)
    host.machine = m
    m.run(ms * MS, stop_when_idle=False)
    return m, avr, host, board


PAGE = [(0x1234 + 0x0101 * k) & 0xFFFF for k in range(64)]


def test_enter_signature_and_fuses():
    m, avr, host, board = run([(100, 0, 1 << 28), (60 * MS, 0, 2 << 28), (60 * MS + 10, 0, 0x50000000)], 75)
    status, sig, fuse = host.received[0]
    assert status == 0 and sig == 0x1E950F and fuse & 0xFF == 0x62, [hex(x) for x in host.received[0]]
    assert avr.enables == 1 and not avr.violations, avr.violations
    assert not board.errors() and not m.ff.valid


def test_program_and_read_back_a_page():
    pushes = [(100, 0, 1 << 28), (60 * MS, 0, 3 << 28 | 0x0040)]
    # a word every 1 ms: each takes two instructions (0.5 ms at 125 kHz), and the mailbox
    # holds two words (a host checks for room before it pushes)
    pushes += [(60 * MS + MS * (k + 1), 0, w) for k, w in enumerate(PAGE)]
    pushes += [(140 * MS, 0, 4 << 28 | 64 << 16 | 0x0040)]
    m, avr, host, board = run(pushes, 190)
    r = host.received[0]
    assert r[:3] == [0, 0x1E950F, 0], [hex(x) for x in r[:3]]
    assert [avr.flash.get(0x40 + k) for k in range(64)] == PAGE
    assert r[3:] == PAGE, [hex(x) for x in r[3:8]]
    assert not avr.violations, avr.violations[:3]


def test_out_of_sync_target_gets_a_reset_pulse():
    """A target 3 bits out of step: no echo, a RESET pulse, then in sync (datasheet step 3)."""
    m, avr, host, board = run([(100, 0, 1 << 28)], 60, desync=3)
    assert host.received[0][:2] == [0, 0x1E950F]
    assert avr.enables == 1                    # the first attempt was not even a frame


def test_busy_target_is_reported():
    pushes = [(100, 0, 1 << 28), (60 * MS, 0, 3 << 28 | 0x0040)]
    pushes += [(60 * MS + MS * (k + 1), 0, w) for k, w in enumerate(PAGE)]
    m, avr, host, board = run(pushes, 800, busy_ms=10_000)
    assert host.received[0][2] == 2, host.received[0]


def test_certificate_matches_telemetry():
    from escert import certify
    im = assemble(f'.equ avr_isp.CLK_KHZ, {KHZ}\n.include "avr_isp.asm"\n', base_dir=FW)
    certs = certify(im)
    assert [(c.status, c.min_lead) for c in certs] == [("CHECKED", 21)]
