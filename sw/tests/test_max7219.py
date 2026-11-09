"""MAX7219 display driver (firmware/max7219.asm) against a chain of MAX7219 models written
from the datasheet (escmodels/max7219.py), at 25 MHz with CLK at 500 kHz."""

from __future__ import annotations

from pathlib import Path

from escasm import assemble
from escmodels import Board, HostScript
from escmodels.max7219 import Max7219Chain
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
KHZ = 25000
MS = KHZ


def run(pushes, ms, n=1):
    im = assemble(f'.equ max7219.CLK_KHZ, {KHZ}\n.equ max7219.CHAIN, {n}\n.include "max7219.asm"\n',
                  base_dir=FW)
    chain = Max7219Chain(4, 5, 7, 1e6 / KHZ, n=n)
    host = HostScript(pushes, drain=[0], poll=2500)
    board = Board(chain, host)
    m = Machine(im, board=board)
    host.machine = m
    m.run(int(ms * MS), stop_when_idle=False)
    return m, chain, host, board


SETUP_B = 1 << 28 | 1 << 11 | 7 << 8 | 8      # code B, 8 digits, intensity 8


def test_setup_and_numbers():
    """Set up (code B on 8 digits), then numbers: leading zeros blank, 0 shows as 0, more
    than 8 digits shows dashes."""
    pushes = [(100, 0, SETUP_B)]
    for k, v in enumerate([1234567, 0, 99999999, 100000000, 40960]):
        pushes.append(((2 + k) * MS, 0, 3 << 28 | v))
    m, ch, host, board = run(pushes, 7.5)
    r = ch.regs[0]
    assert (r["decode"], r["scan"], r["intensity"], r["shutdown"], r["test"]) == (0xFF, 7, 8, 1, 0)
    assert ch.text() == "   40960"
    assert not ch.violations and not board.errors() and not m.ff.valid, ch.violations


def _texts(n_values, ms):
    out = []
    for v in n_values:
        m, ch, host, board = run([(100, 0, SETUP_B), (2 * MS, 0, 3 << 28 | v)], ms)
        assert not ch.violations, ch.violations
        out.append(ch.text())
    return out


def test_decimal_digits():
    assert _texts([1234567, 0, 99999999, 100000000, 7], 3.5) == [
        " 1234567", "       0", "99999999", "--------", "       7"]


def test_no_decode_raw_segments():
    """Without decode the data bits are the segments (DP A B C D E F G): 0x7E is a '0'."""
    pushes = [(100, 0, 1 << 28 | 3 << 8 | 0xF), (2 * MS, 0, 2 << 28 | 1 << 8 | 0x7E),
              (3 * MS, 0, 2 << 28 | 4 << 8 | 0x30)]
    m, ch, host, board = run(pushes, 3.2)
    assert ch.text() == "[30][00][00][7e]" and ch.regs[0]["intensity"] == 15
    assert not ch.violations, ch.violations


def test_a_chain_of_four():
    """Four devices: set up all of them in one frame per register; a write to device 2 only
    reaches device 2 (the others get No-Op); every LOAD after 64 clocks."""
    pushes = [(100, 0, SETUP_B), (3 * MS, 0, 2 << 28 | 2 << 24 | 1 << 8 | 5),
              (4 * MS, 0, 3 << 28 | 31)]
    m, ch, host, board = run(pushes, 5.5, n=4)
    assert [r["shutdown"] for r in ch.regs] == [1, 1, 1, 1]
    assert ch.text(2) == "       5" and ch.text(1) == ch.text(3) == "        "
    assert ch.text(0) == "      31"
    assert ch.latches[-9][:2] == [0, 0] and ch.latches[-9][2] == 0x0105 and ch.latches[-9][3] == 0
    assert not ch.violations, ch.violations


def test_certificate_matches_telemetry():
    from escert import certify
    im = assemble(f'.equ max7219.CLK_KHZ, {KHZ}\n.include "max7219.asm"\n', base_dir=FW)
    assert [(c.status, c.min_lead) for c in certify(im)] == [("CHECKED", 28)]
