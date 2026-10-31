"""firmware/ws2812_lu.asm (v1.6): a WS2812 chain from one line unit SEND, every pulse
checked against the datasheet by the decoder model."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from esciss import Machine
from escmodels import Board, Ws2812Decoder

FW = Path(__file__).resolve().parents[2] / "firmware"


def chain(khz, grb, frames=1):
    img = assemble(f'.equ ws2812_lu.CLK_KHZ, {khz}\n.include "ws2812_lu.asm"\n', base_dir=FW)
    dec = Ws2812Decoder(0, tick_ns=1e6 / khz, reset_us=280)
    m = Machine(img, board=Board(dec, pullups=0))
    data = b"".join(c.to_bytes(3, "big") for c in grb) + b"\0\0\0"
    words = [int.from_bytes(data[i:i + 4], "big") for i in range(0, 3 * len(grb), 4)]
    done = []
    for _ in range(frames):
        for w in [len(grb)] + words:
            while len(m.ctx[0].rx) >= 2:
                m.run(20, stop_when_idle=False)
            m.host_rx_push(0, w)
            m.run(1, stop_when_idle=False)
        while not m.ctx[0].tx:
            m.run(500, stop_when_idle=False)
        done.append(m.host_tx_pop(0))
        m.run(1, stop_when_idle=False)
    dec.finish()
    return dec, done


@pytest.mark.parametrize("khz", [12000, 25000, 33000, 40000])
def test_colours_and_timing(khz):
    grb = [0x00FF00, 0x123456, 0xABCDEF, 0x000001, 0x800000]
    dec, done = chain(khz, grb, frames=2)
    assert dec.frames == [grb, grb] and not dec.violations, dec.violations[:3]
    assert done == [1, 1]


def test_a_full_chain_of_330_leds():
    grb = [(k * 0x010203) & 0xFFFFFF for k in range(330)]
    dec, done = chain(40000, grb)
    assert dec.frames == [grb] and not dec.violations
