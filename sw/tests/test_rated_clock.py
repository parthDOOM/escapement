"""At the rated maximum clock (D-5), 33 MHz exactly, as the Tiny Tapeout board makes it
without jitter (RP2040 at 132 MHz, PWM divider 4): the programs whose timing derives from
CLK_KHZ, rebuilt for it, run on the ISS against their bus models with the models' tick at
30.3 ns, so every protocol time is checked in real time, not in ticks. test_escert
certifies all fourteen at 33 and 33.333 MHz; here each runs. westminster (a 14 s tune)
runs at a hundredth of real time in test_firmware; at this clock it is certified only.
"""

from __future__ import annotations

import pytest

import escgen
from escasm import assemble
from escmodels import (Board, CecDevice, DaliController, DmxRx, HostScript, NecReceiver,
                       Tsop, UartRx, Ws2812Decoder, encode_frame, nec_raw)
from esciss import Machine

from test_escgen import table
from test_firmware import (CAN_RX_FRAMES, FW, can_rx_parse, can_rx_run, can_rx_want,
                           cec_blocks, cec_frame, cec_run, dali_run, dshot_check_timing,
                           dshot_run, nec_image, nec_report, nec_run, nec_rx_run, sent_run,
                           word_nibbles)

KHZ = 33000
TICK = 1e6 / KHZ                                  # 30.303 ns
MS = KHZ                                          # ticks per millisecond


@pytest.mark.parametrize("name, baud", [("uart_tx_gen", 115200), ("midi_tx", 31250)])
def test_serial_bytes(name, baud):
    _, image = escgen.build(table(name), CLK_KHZ=KHZ)
    msg = [0x90, 0x3C, 0x7F, 0x80, 0x3C, 0x00, 0x55, 0xAA]
    frame = int(11 * KHZ * 1000 / baud)
    host = HostScript([(100 + frame * i, 0, b) for i, b in enumerate(msg)])
    rx = UartRx(0, KHZ * 1000 / baud)
    m = Machine(image, board=Board(rx, host))
    host.machine = m
    m.run(frame * (len(msg) + 2), stop_when_idle=False)
    assert rx.bytes == msg and not rx.framing_errors
    assert not m.ff.valid


def test_dmx512_packet():
    """A whole packet (break, mark after break, start code and 512 slots, 23 ms)."""
    _, image = escgen.build(table("dmx512_tx"), CLK_KHZ=KHZ)
    slots = [0] + [(7 * i + 1) & 0xFF for i in range(1, 512)]
    for w in range(128):
        image.tape[w] = sum(slots[4 * w + j] << (8 * j) for j in range(4))
    rx = DmxRx(0, clock_hz=KHZ * 1e3)
    m = Machine(image, board=Board(rx))
    m.run(30 * MS, stop_when_idle=False)
    assert len(rx.packets) >= 1 and not rx.violations, rx.violations
    p = rx.packets[0]
    assert p["slots"] == slots and p["break_us"] >= 92 and p["mab_us"] >= 12
    assert not m.ff.valid


def test_ws2812_frames():
    _, image = escgen.build(table("ws2812_gen"), CLK_KHZ=KHZ)
    colours = [0x00FF00, 0xFF0000, 0x0000FF, 0x123456, 0xABCDEF]
    image.tape[0] = len(colours)
    for i, c in enumerate(colours):
        image.tape[1 + i] = c
    dec = Ws2812Decoder(0, tick_ns=TICK, reset_us=280)
    m = Machine(image, board=Board(dec))
    m.run(60_000, stop_when_idle=False)
    assert dec.frames[:2] == [colours, colours] and not dec.violations, dec.violations
    assert not m.ff.valid


def test_nec_frame_from_a_table():
    _, image = escgen.build(table("nec_tx_gen"), CLK_KHZ=KHZ)
    a, c = 0x5A, 0xC3
    raw = a | ((a ^ 0xFF) << 8) | (c << 16) | ((c ^ 0xFF) << 24)
    host = HostScript([(100, 0, raw)], drain=[0], poll=1000)
    rx = NecReceiver(0, tick_ns=TICK)
    board = Board(rx, host)
    m = Machine(image, board=board)
    host.machine = m
    m.run(100 * MS, stop_when_idle=False)
    assert rx.frames() == [(a, c)] and not board.errors(), (rx.events, board.errors())
    starts = [s for s, _ in rx.marks]
    assert starts[1] - starts[0] == KHZ * 27 // 2                 # leader to first bit, exact
    assert not m.ff.valid


def test_nec_tx_frame_and_repeat():
    """A frame, then a repeat code one 108 ms slot later: the repeat path drains the
    queue before its 2.25 ms space, which at this clock is over 2^16 ticks."""
    rx = NecReceiver(0, TICK)
    board = Board(rx)
    m, acks, _ = nec_run(nec_image(KHZ), [0xC35A, 1 << 31], 108 * MS + 15 * MS, board,
                         chunk=100_000)
    assert rx.frames() == [(0x5A, 0xC3), "R"], rx.events
    assert not board.errors(), board.errors()
    period = (KHZ * 10 + 190) // 380
    assert set(rx.carrier_periods) == {period}                  # 868 ticks: 38.02 kHz
    assert rx.pulses == [342] + [22] * 33 + [342, 22]
    starts = [s for s, _ in rx.marks]
    assert starts[1] - starts[0] == KHZ * 27 // 2
    assert 0 <= starts[34] - starts[0] - KHZ * 108 <= 3
    assert starts[35] - starts[34] == KHZ * 45 // 4               # 9 ms + 2.25 ms
    assert acks == [1, 1] and not m.ff.valid and m.ctx[0].min_lead >= 9


@pytest.mark.parametrize("scale", [1.0, 0.9, 1.1])
def test_nec_rx(scale):
    """A frame then a repeat code, from a remote exact or 10% fast or slow: the receiver
    measures in 8-tick units at this clock."""
    slot = 108 * MS
    got = nec_rx_run([(1000, nec_raw(0x5A, 0xC3)), (1000 + slot, None)], slot + 15 * MS,
                     clk_khz=KHZ, scale=scale)
    assert got == [nec_report(0x5A, 0xC3), 1 << 31], [hex(v) for v in got]


def test_nec_loopback_tx_carrier_through_tsop_into_rx():
    image = assemble(f".equ nec_tx.CLK_KHZ, {KHZ}\n.equ nec_rx.CLK_KHZ, {KHZ}\n"
                     '.include "nec_tx.asm"\n.include "nec_rx.asm"\n', base_dir=FW)
    rx = NecReceiver(0, tick_ns=TICK)
    board = Board(rx, Tsop(0, 1, tick_ns=TICK))
    m = Machine(image, board=board)
    queue, got = [0xA55A], []
    while m.now < 100 * MS:
        while queue and len(m.ctx[0].rx) < 2:
            m.host_rx_push(0, queue.pop(0))
            m.run(1, stop_when_idle=False)
        m.run(20_000, stop_when_idle=False)
        m.host_tx_pop(0)
        v = m.host_tx_pop(1)
        if v is not None:
            got.append(v)
        m.run(1, stop_when_idle=False)
        m.pins.clear()
        m.commits.clear()
        board.history.clear()
    assert rx.frames() == [(0x5A, 0xA5)] and got == [nec_report(0x5A, 0xA5)], [hex(v) for v in got]
    assert not board.errors() and not m.ff.valid


def test_dshot():
    """DShot600: three bits are 165 ticks (55 each), high 0.625 or 1.25 us, 8 kHz frames."""
    src = f'.equ dshot_tx.CLK_KHZ, {KHZ}\n.include "dshot_tx.asm"\n'
    m, board, escs = dshot_run([(4000, (1 << 14) | (1234 << 1))], 20_000, src=src, tick_ns=TICK)
    assert not board.errors(), board.errors()
    assert all(e.frames[-1][:3] == (1234, 0, True) for e in escs)
    t0h, t1h = (KHZ * 625 + 500000) // 1000000, (KHZ * 1250 + 500000) // 1000000
    dshot_check_timing(board, escs, KHZ // 8, KHZ // 200, t0h, t1h)
    assert not m.ff.valid


def test_sent():
    """A unit is 99 ticks (2.97 us), inside SENT's tolerance; frames decode with good CRCs."""
    m, board, rx = sent_run([(3000, 0x2468ACE)], 55_000, clk_khz=KHZ)
    assert not board.errors(), board.errors()
    f = rx.frames[-1]
    assert f["unit"] == KHZ * 3 // 1000 and (f["status"], f["data"]) == word_nibbles(0x2468ACE)
    assert f["crc_ok"] and not m.ff.valid


def test_can_rx():
    """250 kbit/s: a bit is 132 ticks."""
    lv = [encode_frame(i, d, rtr=r, ext=e, dlc=n) for i, d, r, e, n in CAN_RX_FRAMES]
    src = f'.equ can_rx.CLK_KHZ, {KHZ}\n.include "can_rx.asm"\n'
    m, words = can_rx_run(lv, period=KHZ / 250, src=src)
    got, errs = can_rx_parse(words)
    assert errs == 0 and got == [can_rx_want(*f) for f in CAN_RX_FRAMES]
    assert not m.ff.valid


def test_cec_initiator_and_follower():
    """The chip sends CEC Version to a TV model, which acknowledges every block within its
    receive windows; then the TV sends Give Physical Address, which the chip acknowledges."""
    src = f'.equ cec.CLK_KHZ, {KHZ}\n.include "cec.asm"\n'
    tv = CecDevice(0, 0, tick_ns=TICK, send=[(120 * MS, [0x04, 0x83])], retries=0)
    m, board, out = cec_run([tv], cec_frame(0x40, 0x9E, 0x05), 200 * MS, src=src, chunk=2000)
    assert not board.errors(), board.errors()
    assert [f["bytes"] for f in tv.frames] == [[0x40, 0x9E, 0x05], [0x04, 0x83]]   # the bus
    assert tv.frames[0]["acks"] == [0, 0, 0] and [r for r, _ in tv.results] == ["ack"]
    frames, res = cec_blocks(out)
    assert res == [0] and frames == [([0x04, 0x83], [0, 0])]
    assert not m.ff.valid


@pytest.mark.parametrize("scale", [1.0, 0.9, 1.1])
def test_dali(scale):
    """QUERY CONTROL GEAR PRESENT, RECALL MAX LEVEL and QUERY ACTUAL LEVEL, from a
    controller exact or 10% fast or slow: each query answered 7.5 ms after its frame."""
    script = [(0x0191, 0xFF), (0x0105, None), (0x01A0, 0xFE)]
    src = f'.equ dali.CLK_KHZ, {KHZ}\n.include "dali.asm"\n'
    frames = [(5 * MS + i * 40 * MS, f) for i, (f, _) in enumerate(script)]
    m, board, ctl, out = dali_run(frames, 130 * MS, scale=scale, src=src, tick_ns=TICK)
    assert not board.errors(), board.errors()
    assert [a[0] if a else None for a in ctl.answers] == [want for _, want in script]
    assert all(abs(a[1] - 7.5) < (0.01 if scale == 1.0 else 0.06) for a in ctl.answers if a)
    assert not m.ff.valid


def test_seven_seg():
    """Each segment lit alone for exactly one 160 ms frame (5,280,000 ticks)."""
    from escasm import assemble as asm
    m = Machine(asm(f'.equ seven_seg.CLK_KHZ, {KHZ}\n.include "seven_seg.asm"\n', base_dir=FW))
    frame = KHZ * 160
    changes, prev = [], None
    while m.now < 3 * frame + 1000:
        m.run(100_000, stop_when_idle=False)
        for p in m.pins:
            if p["ob"] != prev:
                changes.append((p["t"], p["ob"]))
                prev = p["ob"]
        m.pins.clear()
        m.commits.clear()
    frames = changes[1:]
    assert [ob for _, ob in frames][:3] == [0x04, 0x08, 0x02]
    assert all(b[0] - a[0] == frame for a, b in zip(frames, frames[1:]))
    assert not m.ff.valid
