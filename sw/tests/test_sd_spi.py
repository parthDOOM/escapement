"""SD card host in SPI mode (firmware/sd_spi.asm) against the card model of
escmodels/sdcard.py: the start-up sequence with the CRC7 the chip computes for every
command, the OCR, blocks read into the tape with the chip's CRC-16 check, and the failures
it reports. At 25 MHz (escert proves the program from 14 MHz up at 400 kHz SCK)."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import Board, HostScript
from escmodels.sdcard import OCR_READY, SdCardSpi, block_bytes
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
KHZ = 25000


def run(blocks=(), ticks=None, extra="", **card):
    im = assemble(f'.equ sd_spi.CLK_KHZ, {KHZ}\n{extra}.include "sd_spi.asm"\n', base_dir=FW)
    sd = SdCardSpi(4, 5, 6, 7, **card)
    pushes = [(1_000_000 + k * 700_000, 0, a) for k, a in enumerate(blocks)]
    host = HostScript(pushes, drain=[0], poll=2500)
    board = Board(sd, host)
    m = Machine(im, board=board)
    host.machine = m
    m.run(ticks or 1_000_000 + 700_000 * max(1, len(blocks)), stop_when_idle=False)
    return m, sd, host, board


def tape_block(m):
    return b"".join(m.tape[0x40 + k].to_bytes(4, "little") for k in range(128))


def test_start_up_and_two_blocks():
    m, sd, host, board = run(blocks=[0x1234, 7])
    assert not sd.violations, sd.violations
    assert [i for i, _ in sd.commands] == [0, 8] + [55, 41] * 4 + [58, 17, 17]
    assert sd.commands[1] == (8, 0x1AA) and sd.commands[3] == (41, 0x40000000)
    r = host.received[0]
    assert r[0] == 4 << 16 and r[1] == OCR_READY        # good, after 4 ACMD41s; the OCR
    assert r[2:] == [0, 0], r                            # both blocks good
    assert tape_block(m) == block_bytes(7)               # the last block in the tape
    assert not board.errors() and not m.ff.valid


def test_crc16_error_is_reported():
    m, sd, host, board = run(blocks=[3], bad_crc=True)
    r = host.received[0]
    assert r[2] & 0xFF == 7, [hex(x) for x in r]


def test_card_that_stays_idle():
    """The card never leaves the idle state within TRIES attempts: code 3, then a halt."""
    m, sd, host, board = run(extra=".equ sd_spi.TRIES, 5\n", busy=100, ticks=800_000)
    r = host.received[0]
    assert r == [0x01 << 8 | 3], [hex(x) for x in r]
    assert sd.acmd41s == 5


def test_certificate_matches_telemetry():
    from escert import certify
    im = assemble(f'.equ sd_spi.CLK_KHZ, {KHZ}\n.include "sd_spi.asm"\n', base_dir=FW)
    certs = certify(im)
    assert [(c.status, c.min_lead) for c in certs] == [("CHECKED", 21)]
    with pytest.raises(Exception, match="18 ticks"):
        assemble('.equ sd_spi.CLK_KHZ, 12000\n.include "sd_spi.asm"\n', base_dir=FW)
    m, *_ = run(blocks=[1])
    assert m.ctx[0].min_lead >= certs[0].min_lead
