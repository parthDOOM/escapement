"""Sony SIRC, Philips RC5 and RC-6 transmitters generated from protocols/sirc_tx.yaml,
rc5_tx.yaml (v1.6) and rc6_tx.yaml, against the receiver models of escmodels/ir.py, which decode the
carrier bursts in microseconds. Most runs use a 4 MHz clock (the tables are written in
CLK_KHZ, and the models time in microseconds), SIRC also at 25 MHz."""

from __future__ import annotations

import pytest

import escgen
from escmodels import Board, Rc5Receiver, Rc6Receiver, SircReceiver
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


@pytest.mark.parametrize("khz", [4000, 25000])
def test_jvc_frames(khz):
    from escmodels import JvcReceiver
    codes = [0x59 | 0x35 << 8, 0xFFFF, 0x0000, 0x01 | 0x80 << 8]
    if khz == 25000:
        codes = codes[:1]
    rx, acks = run("jvc_tx", JvcReceiver, codes, khz, 58)
    got = [f["address"] | f["command"] << 8 for f in rx.frames()]
    assert got == codes and acks == [1] * len(codes)
    assert all(abs(f["carrier_khz"] - 38) < 0.5 for f in rx.frames())


def wiegand26(facility, card):
    v = facility << 16 | card
    even = bin(v >> 12).count("1") & 1            # over the first 12 data bits
    odd = 1 - (bin(v & 0xFFF).count("1") & 1)     # over the last 12
    return even << 25 | v << 1 | odd


@pytest.mark.parametrize("khz", [4000, 25000])
def test_wiegand_frames(khz):
    from escmodels import WiegandReceiver
    cards = [(0x12, 0x3456), (0xFF, 0xFFFF), (0, 0), (0x81, 0x0102)]
    if khz == 25000:
        cards = cards[:2]
    _, image = escgen.build(table("wiegand_tx"), CLK_KHZ=khz)
    rx = WiegandReceiver(0, 1, tick_ns=1e6 / khz)
    m = Machine(image, board=Board(rx))
    acks = []
    for f, c in cards:
        m.host_rx_push(0, wiegand26(f, c))
        m.run(int(52 * khz), stop_when_idle=False)
        v = m.host_tx_pop(0)
        if v is not None:
            acks.append(v)
    rx.finish()
    assert [WiegandReceiver.card26(b) for b in rx.frames] == cards
    assert not rx.violations and acks == [1] * len(cards) and not m.ff.valid


def rc6_word(toggle, address, command, mode=0):
    """The host word of rc6_tx: start bit and mode bits (MSB first), toggle, address, command."""
    return (0b1000 | mode) | toggle << 4 | address << 13 | command << 5


@pytest.mark.parametrize("khz", [8000, 25000])
def test_rc6_frames(khz):
    """RC-6 mode 0 from rc6_tx against the RC-6 receiver model: every field, both toggle
    values, all-zero and all-one data, the 36 kHz carrier. (escert proves rc6_tx at 8 MHz and
    above; below that its bound on the lead widens to [0, 2^16].)"""
    frames = [(0, 0x00, 0x0C), (1, 0xFF, 0xFF), (0, 0x00, 0x00), (1, 0xA5, 0x5A)]
    if khz == 25000:
        frames = frames[:2]
    rx, acks = run("rc6_tx", Rc6Receiver, [rc6_word(*f) for f in frames], khz, 30)
    got = [(f["toggle"], f["address"], f["command"]) for f in rx.frames()]
    assert got == frames and acks == [1] * len(frames)
    assert all(f["mode"] == 0 and abs(f["carrier_khz"] - 36) < 0.5 for f in rx.frames())


def test_rc6_receiver_rejects_rc5():
    """The RC-6 model decodes nothing from RC5 frames (and the RC5 model nothing from RC-6),
    so neither test can pass on the other protocol's waveform."""
    rx, _ = run("rc5_tx", Rc6Receiver, [0b11 << 12 | 0x05 << 6 | 0x0C], 8000, 115)
    assert rx.frames() == []
    rx, _ = run("rc6_tx", Rc5Receiver, [rc6_word(0, 0x05, 0x0C)], 8000, 30)
    assert rx.frames() == []
