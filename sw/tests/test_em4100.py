"""EM4100 tag (firmware/em4100_tag.asm) against the reader model of escmodels/em4100.py,
with the frame layout checked against a vector worked by hand from the datasheet."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import Board
from escmodels.em4100 import Em4100Reader, frame, words
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"


def test_frame_by_hand():
    """ID 0x0123456789: the header, rows 0..9 with even parity (0000 0, 0001 1, 0010 1,
    0011 0, 0100 1, 0101 0, 0110 0, 0111 1, 1000 1, 1001 0), the column parity
    0^1^...^9 = 0001 and the stop bit 0, written out bit for bit."""
    want = ("111111111" "00000" "00011" "00101" "00110" "01001" "01010" "01100" "01111"
            "10001" "10010" "0001" "0")
    assert len(want) == 64 and frame(0x0123456789) == int(want, 2)


def run(khz, idents, frames_each=3):
    """The tag at `khz` (bit = 64 periods of a 125 kHz field), loaded with each ID in turn."""
    im = assemble(f'.equ em4100_tag.CLK_KHZ, {khz}\n.include "em4100_tag.asm"\n', base_dir=FW)
    bit = khz * 64 // 125
    rd = Em4100Reader(0, bit / 2)
    board = Board(rd)
    m = Machine(im, board=board)
    for ident in idents:
        for w in words(ident):
            m.host_rx_push(0, w)
            m.run(200, stop_when_idle=False)
        m.run(frames_each * 64 * bit, stop_when_idle=False)
    # a new ID is taken only after the repetition under way (up to one frame late), and the
    # run's last frame is cut: one frame more lets the last ID repeat in full
    m.run(64 * bit, stop_when_idle=False)
    return m, rd, board


@pytest.mark.parametrize("khz", [4000, 25000])
def test_reader_decodes_the_ids_continuously(khz):
    """Every repetition decodes, the bit rate is exactly RF/64, and a new ID taken between
    repetitions replaces the old one. (25 MHz: one ID, two repetitions.)"""
    ids = [0x0123456789, 0xFFFFFFFFFF, 0x1A00FF3C2D] if khz == 4000 else [0x06001259E1]
    m, rd, board = run(khz, ids, frames_each=3 if khz == 4000 else 2)
    got = rd.ids()
    assert not rd.bad_runs, rd.bad_runs[:3]
    # each ID repeats; the first repetition after a change may start mid-frame in the reader
    seq = [g for k, g in enumerate(got) if k == 0 or g != got[k - 1]]
    assert seq == ids, [hex(g) for g in got]
    assert all(got.count(i) >= 2 for i in ids)
    assert not board.errors() and not m.ff.valid


def test_edges_on_the_half_bit_grid():
    """Every edge of a repetition lands a whole number of half bits after the first, to the
    tick: the schedule is in events, so it does not drift with the instructions."""
    khz = 4000
    m, rd, board = run(khz, [0x0123456789], frames_each=2)
    half = khz * 64 // 125 // 2
    lv = board.level(0)
    edges = [t for t in range(1, len(lv)) if lv[t] != lv[t - 1]][1:]   # after the first OFF
    assert edges and all((e - edges[0]) % half == 0 for e in edges)


def test_certificate_matches_telemetry():
    from escert import certify
    im = assemble('.equ em4100_tag.CLK_KHZ, 4000\n.include "em4100_tag.asm"\n', base_dir=FW)
    certs = certify(im)
    assert [(c.status, c.min_lead) for c in certs] == [("CHECKED", 9)]
    m, *_ = run(4000, [0x0123456789], frames_each=1)
    assert m.ctx[0].min_lead >= certs[0].min_lead
