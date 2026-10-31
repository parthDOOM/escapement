"""firmware/spi_tx.asm: write-only SPI at line rate on the line unit (v1.6), every mode, at
40 MHz (10 Mb/s), against the SPI target model of escmodels (it checks SCK's idle level at
CS_n edges, MOSI setup before each sampling edge, and no SCK edges outside a frame)."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import Board, SpiTarget
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"


def run(mode: int, transfers: list[bytes], cell: int = 2, khz: int = 40000):
    src = (f".equ spi_tx.MODE, {mode}\n.equ spi_tx.CELL, {cell}\n"
           f'.equ spi_tx.CLK_KHZ, {khz}\n.include "spi_tx.asm"\n')
    tgt = SpiTarget(4, 5, 6, 7, mode)
    m = Machine(assemble(src, base_dir=FW), board=Board(tgt))
    done = []
    for data in transfers:
        words = [int.from_bytes(data[i:i + 4].ljust(4, b"\0"), "big") for i in range(0, len(data), 4)]
        for w in [len(data)] + words:
            while len(m.ctx[0].rx) >= 2:
                m.run(20, stop_when_idle=False)
            m.host_rx_push(0, w)
            m.run(1, stop_when_idle=False)
        while not m.ctx[0].tx:
            m.run(50, stop_when_idle=False)
        done.append(m.host_tx_pop(0))
    m.run(50, stop_when_idle=False)
    return m, tgt, done


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_every_mode_at_10_mbit(mode):
    transfers = [bytes([0xA5]), bytes([0x00, 0xFF, 0x3C, 0xC3, 0x81]), bytes(range(1, 40))]
    m, tgt, done = run(mode, transfers)
    assert [bytes(f) for f in tgt.frames] == transfers and not tgt.violations, tgt.violations
    assert done == [1, 1, 1] and not m.ff.valid
    sck = [(p["uio_out"] >> 4) & 1 for p in m.pins]
    rises = [t for t in range(1, len(sck)) if sck[t] and not sck[t - 1]]
    gaps = {b - a for a, b in zip(rises, rises[1:])}
    assert 4 in gaps                                               # 4 ticks a bit


def test_slower_cells():
    m, tgt, done = run(0, [b"\x12\x34\x56"], cell=5, khz=25000)
    assert [bytes(f) for f in tgt.frames] == [b"\x12\x34\x56"] and not tgt.violations
