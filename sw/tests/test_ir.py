"""Sony SIRC and Philips RC5 transmitters generated from protocols/sirc_tx.yaml and
rc5_tx.yaml (v1.6), against the receiver models of escmodels/ir.py, which decode the
carrier bursts in microseconds. Most runs use a 4 MHz clock (the tables are written in
CLK_KHZ, and the models time in microseconds), SIRC also at 25 MHz."""

from __future__ import annotations

import pytest

import escgen
from escmodels import Board, Rc5Receiver, SircReceiver
from esciss import Machine

from test_escgen import table


def run(name, model, codes, khz, frame_ms):
    _, image = escgen.build(table(name), CLK_KHZ=khz)
    rx = model(0, tick_ns=1e6 / khz)
    m = Machine(image, board=Board(rx, pullups=0))      # an LED driver: low when undriven
    acks = []
    for c in codes:
        m.host_rx_push(0, c)
        m.run(int(frame_ms * khz), stop_when_idle=False)
        v = m.host_tx_pop(0)
        if v is not None:
            acks.append(v)
    m.run(int(frame_ms * khz), stop_when_idle=False)      # the last frame's gap and ack
    v = m.host_tx_pop(0)
    if v is not None:
        acks.append(v)
    rx.finish()
    assert not m.ff.valid
    return rx, acks


@pytest.mark.parametrize("khz", [4000, 25000])
def test_sirc_frames(khz):
    codes = [(0x15 | 0x01 << 7), (0x7F | 0x1F << 7), 0, (0x2A | 0x0A << 7)]
    if khz == 25000:
        codes = codes[:1]
    rx, acks = run("sirc_tx", SircReceiver, codes, khz, 46)
    got = [(f["address"] << 7) | f["command"] for f in rx.frames()]
    assert got == codes and acks == [1] * len(codes)
    assert all(abs(f["carrier_khz"] - 40) < 0.5 for f in rx.frames())


@pytest.mark.parametrize("khz", [4000, 8000])
def test_rc5_frames(khz):
    # S1 = 1, S2 = 1, toggle, address, command
    codes = [0b11 << 12 | t << 11 | a << 6 | c for t, a, c in
             [(0, 0x00, 0x0C), (1, 0x1F, 0x3F), (0, 0x05, 0x01), (1, 0x10, 0x20)]]
    if khz == 8000:
        codes = codes[:1]
    rx, acks = run("rc5_tx", Rc5Receiver, codes, khz, 115)
    got = [f["start"] << 12 | f["toggle"] << 11 | f["address"] << 6 | f["command"] for f in rx.frames()]
    assert got == codes and acks == [1] * len(codes)
