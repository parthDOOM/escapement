"""At the rated clocks (D-5): 33 MHz (v1.5) and 50 MHz (v1.6), as the Tiny Tapeout board
makes them without jitter (RP clock 132 MHz divided by 4, and 100 MHz divided by 2): the
programs whose timing derives from CLK_KHZ, rebuilt for each, run on the ISS against their
bus models with the models' tick at 30.3 or 20 ns, so every protocol time is checked in
real time, not in ticks. test_escert certifies all of them at 33, 33.333, 40 and 50 MHz;
here those with a bus model run. westminster (a 14 s tune) runs at a hundredth of real
time in test_firmware; at these clocks it is certified only.
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

# v1.6: each program at both rated steps, 33 MHz (v1.5) and 50 MHz (v1.6)
CLOCKS = [33000, 50000]


@pytest.fixture(params=CLOCKS, ids=lambda k: f"{k // 1000}MHz")
def khz(request):
    return request.param


@pytest.mark.parametrize("name, baud", [("uart_tx_gen", 115200), ("midi_tx", 31250)])
def test_serial_bytes(name, baud, khz):
    KHZ, TICK, MS = khz, 1e6 / khz, khz                # ticks per ms
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


def test_dmx512_packet(khz):
    """A whole packet (break, mark after break, start code and 512 slots, 23 ms)."""
    KHZ, TICK, MS = khz, 1e6 / khz, khz                # ticks per ms
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


def test_ws2812_frames(khz):
    KHZ, TICK, MS = khz, 1e6 / khz, khz                # ticks per ms
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


def test_nec_frame_from_a_table(khz):
    KHZ, TICK, MS = khz, 1e6 / khz, khz                # ticks per ms
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


def test_nec_tx_frame_and_repeat(khz):
    """A frame, then a repeat code one 108 ms slot later: the repeat path drains the
    queue before its 2.25 ms space, which at this clock is over 2^16 ticks."""
    KHZ, TICK, MS = khz, 1e6 / khz, khz                # ticks per ms
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
def test_nec_rx(scale, khz):
    """A frame then a repeat code, from a remote exact or 10% fast or slow: the receiver
    measures in 8-tick units at this clock."""
    KHZ, TICK, MS = khz, 1e6 / khz, khz                # ticks per ms
    slot = 108 * MS
    got = nec_rx_run([(1000, nec_raw(0x5A, 0xC3)), (1000 + slot, None)], slot + 15 * MS,
                     clk_khz=KHZ, scale=scale)
    assert got == [nec_report(0x5A, 0xC3), 1 << 31], [hex(v) for v in got]


def test_nec_loopback_tx_carrier_through_tsop_into_rx(khz):
    KHZ, TICK, MS = khz, 1e6 / khz, khz                # ticks per ms
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


def test_dshot(khz):
    """DShot600: three bits are 5 us (165 ticks at 33 MHz), high 0.625 or 1.25 us, 8 kHz
    frames. Times are in microseconds, so the run ends between frames at every clock."""
    KHZ, TICK, MS = khz, 1e6 / khz, khz                # ticks per ms
    src = f'.equ dshot_tx.CLK_KHZ, {KHZ}\n.include "dshot_tx.asm"\n'
    m, board, escs = dshot_run([(121 * KHZ // 1000, (1 << 14) | (1234 << 1))], 606 * KHZ // 1000,
                               src=src, tick_ns=TICK)
    assert not board.errors(), board.errors()
    assert all(e.frames[-1][:3] == (1234, 0, True) for e in escs)
    t0h, t1h = (KHZ * 625 + 500000) // 1000000, (KHZ * 1250 + 500000) // 1000000
    dshot_check_timing(board, escs, KHZ // 8, KHZ // 200, t0h, t1h)
    assert not m.ff.valid


def test_sent(khz):
    """A unit is 3 us (99 ticks at 33 MHz), inside SENT's tolerance; frames decode with good
    CRCs. Times are in microseconds, so the pushed word's frame is sent at every clock."""
    KHZ, TICK, MS = khz, 1e6 / khz, khz                # ticks per ms
    m, board, rx = sent_run([(91 * KHZ // 1000, 0x2468ACE)], 1667 * KHZ // 1000, clk_khz=KHZ)
    assert not board.errors(), board.errors()
    f = rx.frames[-1]
    assert f["unit"] == KHZ * 3 // 1000 and (f["status"], f["data"]) == word_nibbles(0x2468ACE)
    assert f["crc_ok"] and not m.ff.valid


def test_can_rx(khz):
    """250 kbit/s: a bit is 132 ticks."""
    KHZ, TICK, MS = khz, 1e6 / khz, khz                # ticks per ms
    lv = [encode_frame(i, d, rtr=r, ext=e, dlc=n) for i, d, r, e, n in CAN_RX_FRAMES]
    src = f'.equ can_rx.CLK_KHZ, {KHZ}\n.include "can_rx.asm"\n'
    m, words = can_rx_run(lv, period=KHZ / 250, src=src)
    got, errs = can_rx_parse(words)
    assert errs == 0 and got == [can_rx_want(*f) for f in CAN_RX_FRAMES]
    assert not m.ff.valid


def test_cec_initiator_and_follower(khz):
    """The chip sends CEC Version to a TV model, which acknowledges every block within its
    receive windows; then the TV sends Give Physical Address, which the chip acknowledges."""
    KHZ, TICK, MS = khz, 1e6 / khz, khz                # ticks per ms
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
def test_dali(scale, khz):
    """QUERY CONTROL GEAR PRESENT, RECALL MAX LEVEL and QUERY ACTUAL LEVEL, from a
    controller exact or 10% fast or slow: each query answered 7.5 ms after its frame."""
    KHZ, TICK, MS = khz, 1e6 / khz, khz                # ticks per ms
    script = [(0x0191, 0xFF), (0x0105, None), (0x01A0, 0xFE)]
    src = f'.equ dali.CLK_KHZ, {KHZ}\n.include "dali.asm"\n'
    frames = [(5 * MS + i * 40 * MS, f) for i, (f, _) in enumerate(script)]
    m, board, ctl, out = dali_run(frames, 130 * MS, scale=scale, src=src, tick_ns=TICK)
    assert not board.errors(), board.errors()
    assert [a[0] if a else None for a in ctl.answers] == [want for _, want in script]
    assert all(abs(a[1] - 7.5) < (0.01 if scale == 1.0 else 0.06) for a in ctl.answers if a)
    assert not m.ff.valid


def test_seven_seg(khz):
    """Each segment lit alone for exactly one 160 ms frame (5,280,000 ticks)."""
    KHZ, TICK, MS = khz, 1e6 / khz, khz                # ticks per ms
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


# --------------------------------------------------------------------------- v1.6
# The hand-written programs that took their timing from 25 MHz ticks up to v1.5, now from
# CLK_KHZ (their 25 MHz images unchanged, except i2c_ctrl's two waits): each at both rated
# steps against a model that times every edge in real nanoseconds.

def build(name, khz, extra=""):
    return assemble(f'.equ {name}.CLK_KHZ, {khz}\n{extra}.include "{name}.asm"\n', base_dir=FW)


def edges_of(board, pin):
    lv = board.level(pin)
    return [t for t in range(1, len(lv)) if lv[t] != lv[t - 1]]


def test_uart_tx_and_rx(khz):
    from escmodels import UartTx
    rx = UartRx(0, khz * 1000 / 115200)
    board = Board(rx)
    m = Machine(build("uart_tx", khz), board=board)
    m.run(khz * 2, stop_when_idle=False)
    assert rx.bytes == [0x55, 0x4B, 0x21, 0x0A] and not board.errors() and not m.ff.valid
    for err in (0.0, 0.02, -0.02):
        data = bytes(range(256))
        tx = UartTx(1, khz * 1000 / 115200 * (1 + err), data, start=500, gap_bits=0.0)
        script = HostScript([], drain=[1], poll=1400)
        board = Board(tx, script)
        m = Machine(build("uart_rx", khz), board=board)
        script.machine = m
        m.run(tx.end + 4000, stop_when_idle=False)
        assert script.received[1] == list(data) and not m.ff.valid, err


def test_ws2812_chain(khz):
    dec = Ws2812Decoder(0, 1e6 / khz)
    script = HostScript([(30, 3, 0x102030), (40, 3, 0x010203), (2000, 3, 6)], drain=[3], poll=97)
    board = Board(dec, script)
    m = Machine(build("ws2812", khz), board=board)
    script.machine = m
    m.run(khz // 2, stop_when_idle=False)
    dec.finish()
    assert dec.frames == [[(0x102030 + i * 0x010203) & 0xFFFFFF for i in range(6)]]
    assert script.received[3] == [1] and not board.errors() and not m.ff.valid


def test_onewire_read_rom(khz):
    from escmodels import OneWireDevice, crc8_maxim
    rom = bytes([0x28, 0xAA, 0x01, 0x02, 0x03, 0x04, 0x05])
    rom += bytes([crc8_maxim(rom)])
    dev = OneWireDevice(1, rom, 1e6 / khz)
    ms = khz
    script = HostScript([(30, 3, 0x80000033)] + [(2 * ms + ms * 4 // 5 * i, 3, 0xFF) for i in range(8)],
                        drain=[3], poll=500)
    board = Board(dev, script)
    m = Machine(build("onewire", khz), board=board)
    script.machine = m
    m.run(10 * ms, stop_when_idle=False)
    got = script.received[3]
    assert got[0] >> 8 == 1 and bytes(v & 0xFF for v in got[1:9]) == rom
    assert dev.written[0] == 0x33 and not board.errors() and not m.ff.valid


def test_ps2_device(khz):
    from escmodels import Ps2Host
    host = Ps2Host(4, 5, tick_ns=1e6 / khz)
    board = Board(host)
    m = Machine(build("ps2_device", khz), board=board)
    acks = []
    for code in (0x1C, 0x5A, 0x00, 0xFF):
        m.host_rx_push(1, code)
        m.run(khz * 26 // 25, stop_when_idle=False)               # 1.04 ms
        acks.append(m.host_tx_pop(1))
        m.run(1, stop_when_idle=False)
    assert acks == [1] * 4 and host.bytes == [0x1C, 0x5A, 0x00, 0xFF]
    assert not board.errors() and not m.ff.valid


@pytest.mark.parametrize("stretch", [0, 37, 400])
def test_i2c_fast_mode(stretch, khz):
    """fSCL <= 400 kHz and every Fast-mode minimum, in nanoseconds, at the rated clock."""
    from escmodels import I2cTarget
    from test_firmware import i2c_cmd, scl_periods
    tick = 1e6 / khz
    target = I2cTarget(scl=2, sda=3, addr7=0x42, regs={5: 0x00, 6: 0x5C}, mode="fast",
                       tick_ns=tick, stretch_ticks=stretch)
    gap = (3500 + 40 * stretch) * khz // 25000
    script = HostScript([(50, 0, i2c_cmd(0x42, 5, 0xA5, write=True)),
                         (50 + gap, 0, i2c_cmd(0x42, 5, read=True)),
                         (50 + 2 * gap, 0, i2c_cmd(0x42, 6, read=True))], drain=[0], poll=500)
    board = Board(target, script)
    m = Machine(build("i2c_ctrl", khz), board=board)
    script.machine = m
    m.run(50 + 3 * gap + 2000, stop_when_idle=False)
    assert script.received[0] == [0x0000, 0x00A5, 0x005C] and target.regs[5] == 0xA5
    assert not board.errors(), board.errors()
    assert min(scl_periods(board, 2)) * tick >= 2500
    assert not m.ff.valid


@pytest.mark.parametrize("mode", ["standard", "fast"])
def test_i2c_eeprom(mode, khz):
    from escmodels import I2cController
    from test_firmware import EEPROM_SCRIPT
    image = build("i2c_eeprom", khz)
    for a in range(256):
        image.tape[a] = (a * 7 + 3) & 0xFF
    ctl = I2cController(2, 3, EEPROM_SCRIPT, mode=mode, tick_ns=1e6 / khz)
    board = Board(ctl)
    m = Machine(image, board=board)
    m.run((330_000 if mode == "standard" else 110_000) * khz // 25000, stop_when_idle=False)
    r = ctl.results
    assert [x["acks"] for x in r] == [[True] * 6, [True] * 3, [False], [True], [True] * 5, [True] * 3]
    assert r[1]["data"] == [0xDE, 0xAD, 0xBE, 0xEF] and r[5]["data"] == [3]
    assert not board.errors(), board.errors()
    assert not m.ff.valid


def test_can_tx(khz):
    """250 kbit/s exactly: the receiver hard-synchronises once per frame, so any rate error
    would show over the frame."""
    from escmodels import CanBus
    from test_firmware import CAN_FRAMES
    bus = CanBus(0, 1, khz * 1000 / 250000)
    board = Board(bus)
    m = Machine(build("can_tx", khz), board=board)
    acks = []
    for ident, dlc, w1, w2 in CAN_FRAMES:
        m.run(200, stop_when_idle=False)
        for w in ((ident << 16) | dlc, w1, w2):
            m.host_rx_push(0, w)
            m.run(50, stop_when_idle=False)
        m.run(40_000 * khz // 25000, stop_when_idle=False)
        acks.append(m.host_tx_pop(0))
        m.run(1, stop_when_idle=False)
    assert acks == [1, 1, 1, 1] and len(bus.frames) == 4 and all(f["crc_ok"] for f in bus.frames)
    assert not board.errors() and not m.ff.valid


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_spi_ctrl(mode, khz):
    """Every mode, SCK at its configured 500 kHz (1 us half periods) at any clock."""
    from escmodels import SpiTarget
    tgt = SpiTarget(sck=4, mosi=5, miso=6, cs=7, mode=mode, reply=b"\x3C\xC3\x81")
    script = HostScript([(20, 2, mode), (100, 2, 0x1A5), (110, 2, 0x05A),
                         (2000 * khz // 25000, 2, 0x0F0)], drain=[2], poll=300)
    board = Board(tgt, script)
    m = Machine(build("spi_ctrl", khz), board=board)
    script.machine = m
    m.run(6000 * khz // 25000, stop_when_idle=False)
    assert tgt.frames == [[0xA5, 0x5A], [0xF0]] and script.received[2] == [0x3C, 0xC3, 0x81]
    # SCK inside frames (CS_n low); with CPOL = 1 the move to the idle level after the config
    # word is one edge with CS_n high, which no target clocks on
    cs = board.level(7)
    e = [t for t in edges_of(board, 4) if cs[t] == 0]
    assert min(b - a for a, b in zip(e, e[1:])) * 1e6 / khz >= 1000
    assert not board.errors() and not m.ff.valid


def test_jtag_idcode(khz):
    from escmodels import JtagTap
    tap = JtagTap(4, 5, 6, 7, idcode=0x4BA00477)
    board = Board(tap)
    m = Machine(build("jtag_idcode", khz), board=board)
    got = []
    for _ in range(2):
        m.host_rx_push(2, 0)
        m.run(6000 * khz // 25000, stop_when_idle=False)
        got.append(m.host_tx_pop(2))
        m.run(1, stop_when_idle=False)
    e = edges_of(board, 4)
    assert got == [0x4BA00477] * 2 and tap.state == "RTI"
    assert min(b - a for a, b in zip(e, e[1:])) * 1e6 / khz >= 799     # TCK <= 625 kHz
    assert not board.errors() and not m.ff.valid


def test_swd_connect_and_read_dpidr(khz):
    from escmodels import SwdTarget
    from test_firmware import SWD, SWD_CONNECT_DPIDR, SWD_DPIDR, swclk_phases, swd_session
    tgt = SwdTarget(4, 5, dpidr=0x2BA01477)
    src = f'.equ swd_idcode.CLK_KHZ, {khz}\n.include "swd_idcode.asm"\n'
    replies, board, m = swd_session(tgt, [(SWD_CONNECT_DPIDR,), (SWD_DPIDR,)], source=src,
                                    per_cmd=60_000 * khz // 25000)
    assert replies == [(1, 0x2BA01477)] * 2 and tgt.mode == "swd"
    assert min(swclk_phases(board)) * 1e6 / khz >= 640                 # SWCLK <= 781 kHz
    assert not board.errors() and not m.ff.valid and m.ctx[SWD].min_lead >= 3


# The programs the co-simulation runs at a low clock of their own (test/cosim/fw_cases.py:
# the line unit's CAN and WS2812, and the newer buses), here at each rated clock against
# the same bus models with the same checks. The models time in real time from the clock,
# the host's pushes keep their real time, and each run lasts as long in real time as the
# co-simulation's. (combo_uart_i2c and combo_two_banks only include modules run above.)
def _rated_ir(model, code, want):
    def case(khz):
        import escmodels
        rx = getattr(escmodels, model)(0, tick_ns=1e6 / khz)

        def check(board, h):
            rx.finish()
            got = [want(f) for f in rx.frames()]
            return [] if got == [code] else [f"{model} {got}"]
        return Board(rx, pullups=0), [(100, 0, code)], check
    return case


def _rated_ev1527(khz):
    from escmodels import Ev1527Receiver
    rx = Ev1527Receiver(0, tick_ns=1e6 / khz)
    return (Board(rx, pullups=0), [(100, 0, 0xABCDE5)],
            lambda board, h: [] if rx.codes()[:1] == [0xABCDE5] else [f"ev1527 {rx.codes()}"])


def _rated_dcc(khz):
    from escmodels import DccDecoder
    dec = DccDecoder(0, tick_ns=1e6 / khz)

    def check(board, h):
        got = dec.packets()
        ok = got and got[0]["bytes"] == [0x03, 0x74, 0x77] and got[0]["check_ok"] and not dec.violations
        return [] if ok else [f"dcc {got} {dec.violations[:2]}"]
    return Board(dec, pullups=0), [(100, 0, 0x03 | 0x74 << 8 | (0x03 ^ 0x74) << 16)], check


def _rated_wiegand(khz):
    from escmodels import WiegandReceiver
    rx = WiegandReceiver(0, 1, tick_ns=1e6 / khz)
    v = 0x12 << 16 | 0x3456
    word = (bin(v >> 12).count("1") & 1) << 25 | v << 1 | (1 - (bin(v & 0xFFF).count("1") & 1))

    def check(board, h):
        rx.finish()
        got = [WiegandReceiver.card26(b) for b in rx.frames]
        return [] if got == [(0x12, 0x3456)] and not rx.violations else [f"wiegand {got} {rx.violations[:2]}"]
    return Board(rx), [(100, 0, word)], check


def _rated_lin(khz):
    from escmodels import LinReceiver
    rx = LinReceiver(0, tick_ns=1e6 / khz)

    def check(board, h):
        rx.finish()
        got = [(f["id"], f["data"]) for f in rx.frames]
        ok = got == [(0x2C, [0x12, 0x34])] and not rx.violations and h.received[0] == [1]
        return [] if ok else [f"lin {got} {rx.violations[:2]} {h.received}"]
    return Board(rx), [(100, 0, 0x2C | 2 << 8), (104, 0, 0x3412), (2 * khz, 0, 0)], check


def _rated_lin_resp(khz):
    """The LIN responder: published ID 0x10 (two bytes) answered, 0x11 left alone."""
    from escmodels import LinCommander, LinReceiver, lin_pid
    words = [1 << 30 | 0x10 << 0 | 2 << 8, 2 << 30 | 0 << 8 | 0x12, 2 << 30 | 1 << 8 | 0x34, 3 << 30]
    pushes = [(200 + k * khz * 5 // 4, 0, w) for k, w in enumerate(words)]
    start = 200 + 6 * khz * 5 // 4
    cmd = LinCommander(0, 1e6 / khz, [lin_pid(0x10), lin_pid(0x11)], start=start, slot_bits=90)
    rx = LinReceiver(0, tick_ns=1e6 / khz)

    def check(board, h):
        rx.finish()
        got = [(f["id"], f["data"]) for f in rx.frames]
        unanswered = [v for v in rx.violations if "frame [85, " not in v]
        ok = (got == [(0x10, [0x12, 0x34])] and not unanswered and len(rx.violations) == 1
              and h.received[0] == [lin_pid(0x10) | 0x100, lin_pid(0x11)])
        return ([] if ok else [f"lin_resp {got} {rx.violations[:2]} {h.received}"])
    return Board(cmd, rx), pushes, check


def _rated_em4100(khz):
    """The EM4100 tag: one whole 64-bit repetition of ID 0x0123456789, read back."""
    from escmodels.em4100 import Em4100Reader, words
    rd = Em4100Reader(0, khz * 64 // 125 / 2)
    w0, w1 = words(0x0123456789)

    def check(board, h):
        ids = rd.ids()
        return [] if ids and set(ids) == {0x0123456789} and not rd.bad_runs else [f"em4100 {ids} {rd.bad_runs[:2]}"]
    return Board(rd), [(100, 0, w0), (110, 0, w1)], check


def _rated_wiegand_rx(khz):
    """The Wiegand panel receiver: one 26-bit frame at 200 us intervals."""
    from escmodels import WiegandSender
    bits = [0, 0, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1]
    want = [26, 0, int("".join(map(str, bits)), 2)]
    snd = WiegandSender(0, 1, 1e6 / khz, [bits], interval_us=200, start=200)

    def check(board, h):
        return [] if h.received[0] == want else [f"wiegand_rx {h.received[0]}"]
    return Board(snd), [], check


def _rated_usbpd(khz):
    """USB PD: a Source_Capabilities message (5 V 3 A), header, PDO and CRC read back."""
    from escmodels.usbpd import UsbPdSink
    sink = UsbPdSink(0, 1e6 / khz)
    hdr, pdo = 0x11A1, 100 << 10 | 300

    def check(board, h):
        sink.finish()
        got = [(x["header"], x["data"], x["crc_ok"], x["eop"]) for x in sink.messages]
        ok = got == [(hdr, [pdo], True, True)] and not sink.violations and h.received[0] == [1]
        return [] if ok else [f"usbpd {got} {sink.violations[:2]} {h.received}"]
    return Board(sink, pullups=0), [(100, 0, hdr), (150, 0, pdo)], check


def _rated_sd_spi(khz):
    """The SD host at 400 kHz SCK: start-up, the OCR, one block with its CRC-16 good."""
    from escmodels.sdcard import OCR_READY, SdCardSpi
    sd = SdCardSpi(4, 5, 6, 7)

    def check(board, h):
        r = h.received[0]
        ok = r[:3] == [4 << 16, OCR_READY, 0] and not sd.violations
        return [] if ok else [f"sd_spi {[hex(x) for x in r]} {sd.violations[:2]}"]
    return Board(sd), [(khz * 40, 0, 0x1234)], check


def _rated_ws2812_lu(khz):
    dec = Ws2812Decoder(0, tick_ns=1e6 / khz, reset_us=280)
    grb = [0x00FF00, 0x123456, 0xABCDEF]
    data = b"".join(c.to_bytes(3, "big") for c in grb) + b"\0\0\0"
    words = [int.from_bytes(data[i:i + 4], "big") for i in range(0, 9, 4)]

    def check(board, h):
        dec.finish()
        ok = dec.frames == [grb] and not dec.violations and h.received[0] == [1]
        return [] if ok else [f"ws2812_lu {dec.frames} {dec.violations[:2]} {h.received}"]
    return Board(dec, pullups=0), [(100 + 200 * i, 0, w) for i, w in enumerate([3] + words)], check


def _rated_can_tx_lu(khz):
    from escmodels import CanBus
    us = khz // 1000
    bus = CanBus(0, 1, us)                              # 1 Mbit/s: a bit is a microsecond
    pushes = [(24 * us, 0, (0x123 << 16) | 2), (26 * us, 0, 0xDEAD0000), (28 * us, 0, 0),
              (160 * us, 0, (0x7FF << 16) | 8), (162 * us, 0, 0x01234567), (164 * us, 0, 0x89ABCDEF)]

    def check(board, h):
        f = bus.frames
        ok = ([(x["id"], x["data"]) for x in f] == [(0x123, [0xDE, 0xAD]),
                                                     (0x7FF, [1, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF])]
              and all(x["crc_ok"] for x in f) and h.received[0] == [1, 1])
        return [] if ok else [f"can_tx_lu {f} {h.received}"]
    return Board(bus), pushes, check


def _rated_modbus(khz):
    from escmodels.modbus import ModbusClient, adu
    reqs = [(adu(1, bytes([16, 0, 4, 0, 2, 4, 0xBE, 0xEF, 0x12, 0x34])), True),
            (adu(1, bytes([3, 0, 3, 0, 4])), True), (adu(1, bytes([5, 0, 0, 0xFF, 0])), True)]
    cl = ModbusClient(ro=1, di=0, de=2, ticks_per_bit=khz * 1000 / 19200, requests=reqs,
                      start=khz * 12 // 5, turn=khz // 5)       # 2.4 ms, 0.2 ms

    def check(board, h):
        out = [bytes(r["bytes"]) for r in cl.replies]
        ok = (len(out) == 3 and out[1][:3] == bytes([1, 3, 8])
              and out[1][3:11] == bytes.fromhex("1003beef12341006") and out[2][:3] == bytes([1, 0x85, 1])
              and h.received[0] == [4 | 2 << 16])
        return [] if ok else [f"modbus {[o.hex() for o in out]} {h.received}"]
    return Board(cl), [], check


def _rated_sbus(khz):
    from escmodels.sbus import SbusSource, encode
    ch = [[(37 * k + 172 * f) & 0x7FF for k in range(16)] for f in range(2)]
    src = SbusSource(0, khz / 100, [(khz // 5, encode(ch[0])), (7 * khz, encode(ch[1], flags=0x08))])
    return (Board(src, pullups=0), [],
            lambda board, h: [] if h.received[0] == [1, 2 | 0x08 << 16] else [f"sbus {h.received}"])


class _Levels:
    """Bank A levels every tick."""

    def __init__(self):
        self.lv = []

    def step(self, t, bus):
        self.lv.append(bus)
        return 0, 0

    def errors(self):
        return []


def _rated_servo(khz):
    pins = _Levels()

    def check(board, h):
        lv, errs = pins.lv, []
        for ch in range(8):
            ups = [t for t in range(1, len(lv)) if (lv[t] >> ch) & 1 and not (lv[t - 1] >> ch) & 1]
            widths = [next((t for t in range(u, len(lv)) if not (lv[t] >> ch) & 1), u) - u for u in ups]
            want = [1500 * khz // 1000, (2000 if ch == 3 else 1500) * khz // 1000]
            if [abs(w - x) <= 2 for w, x in zip(widths, want)] != [True, True]:
                errs.append(f"servo ch{ch} widths {widths} want {want}")
        return errs
    return Board(pins, pullups=0), [(100, 0, 3 << 16 | (2000 - 500))], check


def _modbus_tape(image):
    for k in range(64):
        image.tape[k] = 0x1000 + k


# name -> (case, run length in microseconds, tape preparation)
RATED_CASES = {
    "sirc_tx": (_rated_ir("SircReceiver", 0x15 | 0x01 << 7, lambda f: f["address"] << 7 | f["command"]),
                30_000, None),
    "rc5_tx": (_rated_ir("Rc5Receiver", 0b11 << 12 | 0x05 << 6 | 0x0C,
                         lambda f: f["start"] << 12 | f["toggle"] << 11 | f["address"] << 6 | f["command"]),
               30_000, None),
    "rc6_tx": (_rated_ir("Rc6Receiver", 0b1000 | 1 << 4 | 0xA5 << 13 | 0x0C << 5,
                         lambda f: 0b1000 | f["mode"] | f["toggle"] << 4 | f["address"] << 13 | f["command"] << 5),
               27_000, None),
    "jvc_tx": (_rated_ir("JvcReceiver", 0x59 | 0x35 << 8, lambda f: f["address"] | f["command"] << 8),
               42_500, None),
    "ev1527_tx": (_rated_ev1527, 45_000, None),
    "dcc_tx": (_rated_dcc, 10_000, None),
    "wiegand_tx": (_rated_wiegand, 60_000, None),
    "lin_tx": (_rated_lin, 8_000, None),
    "lin_resp": (_rated_lin_resp, 18_000, None),
    "em4100_tag": (_rated_em4100, 40_000, None),
    "wiegand_rx": (_rated_wiegand_rx, 30_000, None),
    "usbpd_tx": (_rated_usbpd, 1_500, None),
    "sd_spi": (_rated_sd_spi, 68_000, None),
    "ws2812_lu": (_rated_ws2812_lu, 700, None),
    "can_tx_lu": (_rated_can_tx_lu, 320, None),
    "modbus_rtu": (_rated_modbus, 50_000, _modbus_tape),
    "sbus_rx": (_rated_sbus, 13_000, None),
    "servo": (_rated_servo, 50_000, None),
}


@pytest.mark.parametrize("name", sorted(RATED_CASES))
def test_bus_model_at_the_rated_clock(name, khz):
    case, run_us, prep = RATED_CASES[name]
    image = build(name, khz)
    if prep:
        prep(image)
    board, pushes, check = case(khz)
    host = HostScript(pushes, drain=[0], poll=khz // 50)  # the TX mailbox every 20 us
    board.devices.append(host)
    m = Machine(image, board=board)
    host.machine = m
    m.run(run_us * khz // 1000, stop_when_idle=False)
    errs = check(board, host) + board.errors()
    assert not errs, errs
    assert not m.ff.valid, m.ff
