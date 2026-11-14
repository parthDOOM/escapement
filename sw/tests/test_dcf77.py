"""DCF77 transmitter (firmware/dcf77_tx.asm) against the receiver model of escmodels/dcf77.py,
at a 10 kHz clock (a minute is 600,000 ticks; the timing is in CLK_KHZ and escert proves
the program from 10 kHz to 50 MHz). The host sends frames with wrong parity, bit 0 and
bit 20 on purpose: the chip sets them."""

from __future__ import annotations

from pathlib import Path

from escasm import assemble
from escmodels import Board, HostScript
from escmodels.dcf77 import Dcf77Receiver, encode
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
KHZ = 10
SEC = KHZ * 1000


def words(bits, spoil=True):
    """The host's two words; with `spoil`, bit 0 set, bit 20 clear and every parity flipped."""
    b = list(bits)
    if spoil:
        b[0], b[20] = 1, 0
        for p in (28, 35, 58):
            b[p] ^= 1
    v = sum(x << k for k, x in enumerate(b))
    return v & 0xFFFFFFFF, v >> 32


def run(frames, at=None, minutes=None):
    """Frame 0 at start, each next one during the minute before (second 10), unless `at`."""
    im = assemble(f'.equ dcf77_tx.CLK_KHZ, {KHZ}\n.include "dcf77_tx.asm"\n', base_dir=FW)
    pushes = []
    for k, f in enumerate(frames):
        t = 50 if k == 0 else (at[k] if at else (k - 1) * 60 * SEC + 10 * SEC)
        w0, w1 = words(f)
        pushes += [(t, 0, w0), (t + 10, 0, w1)]
    rx = Dcf77Receiver(0, 1e6 / KHZ)
    host = HostScript(pushes, drain=[0], poll=SEC)
    board = Board(rx, host)
    m = Machine(im, board=board)
    host.machine = m
    m.run((minutes or len(frames)) * 60 * SEC + 5 * SEC, stop_when_idle=False)
    rx.finish()
    return m, rx, board


T1 = dict(minute=59, hour=23, day=31, weekday=7, month=12, year=99)
T2 = dict(minute=0, hour=0, day=1, weekday=1, month=1, year=0, cest=True)


def test_two_minutes_decode_with_the_chips_parity():
    m, rx, board = run([encode(**T1), encode(**T2)])
    want = [dict(T1, cest=False), dict(T2)]
    assert rx.minutes[:2] == want, (rx.minutes, rx.violations[:3])
    assert not rx.violations and not board.errors() and not m.ff.valid


def test_seconds_exactly_one_second_apart():
    """Every mark starts a whole number of seconds (to the tick) after the first: the
    minute marker is two seconds, and taking the next frame never moves a second."""
    m, rx, board = run([encode(**T1), encode(**T2)])
    f = rx.falls
    assert len(f) >= 2 * 59
    assert all((x - f[0]) % SEC == 0 for x in f), {(x - f[0]) % SEC for x in f}


def test_missing_frame_repeats_the_last_and_raises_irq():
    """No second frame: the first is sent again and IRQ 0xD7 is raised."""
    m, rx, board = run([encode(**T1)], minutes=2)
    assert rx.minutes[:2] == [dict(T1, cest=False)] * 2, rx.violations[:3]
    assert m.ctx[0].irq_pending and m.ctx[0].irq_code == 0xD7, (m.ctx[0].irq_pending, m.ctx[0].irq_code)


def test_certificate_matches_telemetry():
    from escert import certify
    im = assemble(f'.equ dcf77_tx.CLK_KHZ, {KHZ}\n.include "dcf77_tx.asm"\n', base_dir=FW)
    certs = certify(im)
    assert [(c.status, c.min_lead) for c in certs] == [("CHECKED", 9)]
    m, *_ = run([encode(**T1)])
    assert m.ctx[0].min_lead >= certs[0].min_lead
