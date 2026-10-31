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


@pytest.mark.parametrize("khz", [4000, 25000])
def test_ev1527_codes(khz):
    from escmodels import Ev1527Receiver
    codes = [0xABCDE5, 0x000001, 0xFFFFFF, 0x123456]
    if khz == 25000:
        codes = codes[:2]
    _, image = escgen.build(table("ev1527_tx"), CLK_KHZ=khz)
    rx = Ev1527Receiver(0, tick_ns=1e6 / khz)
    m = Machine(image, board=Board(rx, pullups=0))
    acks = []
    for c in codes:
        m.host_rx_push(0, c)
        m.run(int(60 * khz), stop_when_idle=False)
        v = m.host_tx_pop(0)
        if v is not None:
            acks.append(v)
    assert rx.codes() == codes and acks == [1] * len(codes) and not m.ff.valid


@pytest.mark.parametrize("khz", [4000, 25000])
def test_dcc_packets(khz):
    from escmodels import DccDecoder
    pkts = [(0x03, 0x74), (0x7F, 0x3F), (0x01, 0x00)]        # loco 3 speed, ...
    _, image = escgen.build(table("dcc_tx"), CLK_KHZ=khz)
    dec = DccDecoder(0, tick_ns=1e6 / khz)
    m = Machine(image, board=Board(dec, pullups=0))
    for a, i in pkts:
        m.host_rx_push(0, a | i << 8 | (a ^ i) << 16)
        m.run(int(8 * khz), stop_when_idle=False)
        m.host_tx_pop(0)
    got = dec.packets()
    assert [p["bytes"] for p in got] == [[a, i, a ^ i] for a, i in pkts]
    assert all(p["check_ok"] for p in got) and not dec.violations and not m.ff.valid
