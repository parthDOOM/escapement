"""Morse keyer (firmware/morse.asm) against a listener written from ITU-R M.1677-1
(escmodels/morse.py): every key-down and gap measured in dots, to 1%, and the text decoded.
At 2 MHz with 2 ms dots, so whole messages run in the ISS."""

from __future__ import annotations

from pathlib import Path

from escasm import assemble
from escmodels import Board
from escmodels.board import Device
from escmodels.morse import MorseListener
from esciss import Machine
from esciss.machine import MBX_DEPTH

FW = Path(__file__).resolve().parents[2] / "firmware"
KHZ = 2000
MS = KHZ


class Feeder(Device):
    """A host that keeps context 0's mailbox fed: the next word goes in when there is room."""

    def __init__(self, words, start=100):
        self.words, self.start, self.machine = list(words), start, None

    def step(self, t, bus):
        if self.machine and self.words and t >= self.start and len(self.machine.ctx[0].rx) < MBX_DEPTH:
            self.machine.host_rx_push(0, self.words.pop(0))
        return 0, 0


def text_words(s):
    s += "\0" * (-len(s) % 3)
    return [2 << 28 | ord(s[i]) << 16 | ord(s[i + 1]) << 8 | ord(s[i + 2]) for i in range(0, len(s), 3)]


def run(words, ms, dot_ms=2):
    im = assemble(f'.equ morse.CLK_KHZ, {KHZ}\n.include "morse.asm"\n', base_dir=FW)
    ear = MorseListener(0, 1e6 / KHZ, dot_ms)
    feed = Feeder([1 << 28 | dot_ms] + words)
    board = Board(ear, feed, pullups=0)
    m = Machine(im, board=board)
    feed.machine = m
    m.run(int(ms * MS), stop_when_idle=False)
    return m, ear, board


def test_a_message_across_host_words():
    """Five host words, kept fed: the spacing runs on exactly from one word to the next."""
    m, ear, board = run(text_words("CQ CQ DE ESC 73"), 420)
    assert ear.text() == "CQ CQ DE ESC 73" and not ear.violations, (ear.text(), ear.violations[:3])
    assert not board.errors() and not m.ff.valid


def test_case_punctuation_and_skipped_characters():
    """Lower case keys as capitals; '.', '/', '?', '@' from Part 1; ';' and '#' (not in the
    Recommendation) are skipped without any time of their own."""
    m, ear, board = run(text_words("a.b/c?;#x@"), 400)
    assert ear.text() == "A.B/C?X@" and not ear.violations, (ear.text(), ear.violations[:3])


def test_dot_length():
    m, ear, board = run(text_words("PARIS"), 400, dot_ms=5)
    assert ear.text() == "PARIS" and not ear.violations, (ear.text(), ear.violations[:3])
    downs = [b[0] - a[0] for a, b in zip(ear.edges, ear.edges[1:]) if a[1] == 1]
    assert min(downs) == 5000.0 and max(downs) == 15000.0


def test_certificate_matches_telemetry():
    from escert import certify
    im = assemble('.equ morse.CLK_KHZ, 25000\n.include "morse.asm"\n', base_dir=FW)
    assert [(c.status, c.min_lead) for c in certify(im)] == [("CHECKED", 148)]
