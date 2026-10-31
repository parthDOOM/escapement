"""firmware/lin_tx.asm (v1.6): LIN commander frames against the responder-side model:
break and delimiter lengths, sync, protected identifier parity, enhanced and classic
checksums, every bit at 19200 baud."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from esciss import Machine
from escmodels import Board, LinReceiver
from escmodels.lin import checksum, pid

FW = Path(__file__).resolve().parents[2] / "firmware"


def test_reference_values():
    assert pid(0x2C) == 0xEC                  # a worked example quoted for LIN 2.x
    assert pid(0x3C) == 0x3C and pid(0x3D) == 0x7D


@pytest.mark.parametrize("khz", [8000, 25000])
def test_frames(khz):
    frames = [(0x2C, [0x01]), (0x10, [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]),
              (0x3C, [0x7F, 0x06, 0xB2, 0x00, 0xFF, 0x7F, 0xFF, 0xFF]), (0x00, [0x12, 0x34, 0x56])]
    img = assemble(f'.equ lin_tx.CLK_KHZ, {khz}\n.include "lin_tx.asm"\n', base_dir=FW)
    rx = LinReceiver(0, tick_ns=1e6 / khz)
    m = Machine(img, board=Board(rx))
    acks = []
    for ident, data in frames:
        d = bytes(data).ljust(8, b"\0")
        for w in (ident | len(data) << 8, int.from_bytes(d[:4], "little"),
                  int.from_bytes(d[4:], "little")):
            while len(m.ctx[0].rx) >= 2:
                m.run(50, stop_when_idle=False)
            m.host_rx_push(0, w)
            m.run(1, stop_when_idle=False)
        while not m.ctx[0].tx:
            m.run(1000, stop_when_idle=False)
        acks.append(m.host_tx_pop(0))
        m.run(4 * khz, stop_when_idle=False)              # 4 ms between frames
    rx.finish()
    assert not rx.violations, rx.violations[:3]
    assert [(f["id"], f["data"]) for f in rx.frames] == frames
    assert [f["classic"] for f in rx.frames] == [False, False, True, False]
    assert acks == [1] * 4 and not m.ff.valid
    assert checksum([0x01], pid(0x2C)) != checksum([0x01])  # (the two kinds differ)
