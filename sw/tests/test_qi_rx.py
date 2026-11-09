"""Qi power receiver communications (firmware/qi_rx.asm) against a Power Transmitter model
(escmodels/qi.py, written from the Qi v1.3 Communications Protocol), at a 2 MHz chip clock
(escert certifies the program at 2 MHz and from 4 to 50 MHz) so that seconds of protocol
run in the ISS."""

from __future__ import annotations

from pathlib import Path

from escasm import assemble
from escmodels import Board, HostScript
from escmodels.qi import QiTransmitter
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
KHZ = 2000
MS = KHZ


def run(pushes, ms, **tx):
    im = assemble(f'.equ qi_rx.CLK_KHZ, {KHZ}\n.include "qi_rx.asm"\n', base_dir=FW)
    qtx = QiTransmitter(0, 1, 1e6 / KHZ, **tx)
    host = HostScript(pushes, drain=[0], poll=MS)
    board = Board(qtx, host, pullups=0)
    m = Machine(im, board=board)
    host.machine = m
    m.run(ms * MS, stop_when_idle=False)
    qtx.finish()
    return qtx, host, board, m


def seq(qtx):
    return [(p["name"], p["message"]) for p in qtx.packets]


def test_ping_configuration_and_power_transfer():
    """SIG 40 ms after the Power Signal, ID, CFG, then CE every 250 ms with an RP8 after the
    first; the host's values in them; every timing rule the transmitter checks met."""
    pushes = [(100, 0, 1 << 28 | 0x9A), (200, 0, 2 << 28 | 0xFB), (300 * MS, 0, 3 << 28 | 0x55),
              (600 * MS, 0, 2 << 28 | 0x07)]
    qtx, host, board, m = run(pushes, 2040, power=((1000, None),))
    assert not qtx.violations, qtx.violations
    names = [n for n, _ in seq(qtx)]
    assert names == ["SIG", "ID", "CFG", "CE", "RP8", "CE", "CE", "CE", "CE", "CE", "CE", "RP8"], names
    msgs = [msg for _, msg in seq(qtx)]
    assert msgs[0] == [0x9A]
    assert msgs[1] == [0x12, 0, 0, 0, 0, 0, 1]                       # ID: version 1.2
    assert msgs[2] == [10, 0, 0, 2 << 3 | 2, 0]                       # CFG: 5 W, 8 ms, 8 ms
    assert msgs[3] == [0xFB] and msgs[4] == [0x55] and msgs[6] == [0x07]
    p = qtx.packets
    assert abs(p[0]["start"] - 1000 - 40_000) < 50                   # tWake 40 ms
    starts = [x["start"] for x in p if x["name"] == "CE"]
    assert all(abs(b - a - 250_000) < 20 for a, b in zip(starts, starts[1:]))
    assert host.received[0] == [1]
    assert not board.errors() and not m.ff.valid


def test_end_power_transfer():
    """EPT (charge complete) asked for before a CE goes after it, no sooner than tDelay +
    tControl (29 ms); the transmitter removes the Power Signal and the chip reports it lost."""
    pushes = [(600 * MS, 0, 4 << 28 | 0x01)]
    qtx, host, board, m = run(pushes, 830, power=((1000, None),))
    assert not qtx.violations, qtx.violations
    assert [n for n, _ in seq(qtx)][-2:] == ["CE", "EPT"] and seq(qtx)[-1] == ("EPT", [0x01])
    ce, ept = qtx.packets[-2:]
    assert 29_000 <= ept["start"] - ce["end"] <= 46_000
    assert host.received[0] == [1, 2]


def test_ept_repeats_while_the_power_stays():
    pushes = [(100, 0, 4 << 28 | 0x03)]
    qtx, host, board, m = run(pushes, 145, power=((1000, None),), ignore_ept=True)
    assert not qtx.violations, qtx.violations
    assert seq(qtx) == [("EPT", [0x03])] * 3                          # at tWake, then every 15 ms


def test_power_loss_and_a_new_digital_ping():
    """The Power Signal goes away before the first CE and comes back: the chip starts again
    with SIG at tWake."""
    qtx, host, board, m = run([], 600, power=((1000, 300_000), (400_000, None)))
    assert not qtx.violations, qtx.violations
    assert [n for n, _ in seq(qtx)] == ["SIG", "ID", "CFG", "SIG", "ID", "CFG"]
    assert abs(qtx.packets[3]["start"] - 440_000) < 1100              # 40 ms (+ the 1 ms poll)
    assert host.received[0] == [1, 2, 1]


def test_identification_from_the_host():
    pushes = [(100, 0, 6 << 28 | 0x012304), (200, 0, 7 << 28 | 0x56789A)]
    qtx, host, board, m = run(pushes, 200, power=((1000, None),))
    assert not qtx.violations, qtx.violations
    assert seq(qtx)[1] == ("ID", [0x12, 0x01, 0x23, 0x04, 0x56, 0x78, 0x9A])


def test_certificate_matches_telemetry():
    from escert import certify
    im = assemble('.equ qi_rx.CLK_KHZ, 25000\n.include "qi_rx.asm"\n', base_dir=FW)
    assert [(c.status, c.min_lead) for c in certify(im)] == [("CHECKED", 13)]
