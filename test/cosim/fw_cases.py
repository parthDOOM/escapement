"""Firmware cases for the bus-model co-simulation (test_cosim.py) and the proof table
(proof_table.py): name -> (firmware, ticks, factory). A factory returns fresh bus models
(board, host script, check[, bank B board]); call it once per implementation.
"""

from __future__ import annotations

from pathlib import Path

from escmodels import (Board, CanBus, Device, DmxRx, HostScript, I2cController, I2cTarget,
                       JtagTap, OneWireDevice, Ps2Host, SpiTarget, SplitBoard, UartRx, UartTx,
                       Ws2812Decoder, crc8_maxim)
from escmodels.usb import UsbHost, bus_reset, control, in_txn, setup_bytes

ROM = bytes([0x28, 0xAA, 0x01, 0x02, 0x03, 0x04, 0x05])
ROM += bytes([crc8_maxim(ROM)])
UART_TPB = 25e6 / 115200


def i2c_cmd(addr7, reg, data=0, write=False, read=False):
    return (addr7 << 24) | (reg << 16) | (data << 8) | (1 if write else 0) | (2 if read else 0)


def case_uart_rx():
    tx = UartTx(1, UART_TPB, b"Esc\x00\xff", start=300, gap_bits=0.0)
    host = HostScript([], drain=[1], poll=1400)

    def check(board, h):
        return [] if h.received[1] == list(b"Esc\x00\xff") else [f"uart_rx got {h.received[1]}"]
    return Board(tx, host), host, check


def case_i2c():
    tgt = I2cTarget(2, 3, 0x42, {5: 0}, mode="fast", stretch_ticks=37)
    host = HostScript([(50, 0, i2c_cmd(0x42, 5, 0xA5, write=True)),
                       (4000, 0, i2c_cmd(0x42, 5, read=True))], drain=[0], poll=500)

    def check(board, h):
        e = [] if h.received[0] == [0x0000, 0x00A5] else [f"i2c results {h.received[0]}"]
        return e + board.errors()
    return Board(tgt, host), host, check


def case_spi3():
    tgt = SpiTarget(4, 5, 6, 7, mode=3, reply=b"\x3C\xC3")
    host = HostScript([(20, 2, 3), (100, 2, 0x1A5), (110, 2, 0x05A)], drain=[2], poll=300)

    def check(board, h):
        ok = tgt.frames == [[0xA5, 0x5A]] and h.received[2] == [0x3C, 0xC3]
        return ([] if ok else [f"spi {tgt.frames} {h.received[2]}"]) + board.errors()
    return Board(tgt, host), host, check


def case_ws2812():
    dec = Ws2812Decoder(0)
    host = HostScript([(30, 3, 0x102030), (40, 3, 0x010203), (2000, 3, 4)], drain=[3], poll=97)

    def check(board, h):
        dec.finish()
        ok = dec.frames == [[0x102030, 0x112233, 0x122436, 0x132639]]
        return ([] if ok else [f"ws2812 {dec.frames}"]) + board.errors()
    return Board(dec, host), host, check


def case_onewire():
    dev = OneWireDevice(1, ROM)
    host = HostScript([(30, 3, 0x80000033), (40000, 3, 0xFF)], drain=[3], poll=500)

    def check(board, h):
        r = h.received[3]
        ok = dev.written[:1] == [0x33] and len(r) >= 2 and (r[0] >> 8) == 1 and (r[1] & 0xFF) == ROM[0]
        return ([] if ok else [f"1-wire {dev.written} {r}"]) + board.errors()
    return Board(dev, host), host, check


def case_combo():
    tx_dec = UartRx(0, UART_TPB)
    peer = UartTx(1, UART_TPB, b"hello", start=700, gap_bits=0.0)
    i2c = I2cTarget(2, 3, 0x42, {9: 0x77}, mode="fast", stretch_ticks=50)
    host = HostScript([(100, 0, i2c_cmd(0x42, 9, 0x33, write=True)),
                       (4500, 0, i2c_cmd(0x42, 9, read=True))], drain=[0, 1], poll=700)

    def check(board, h):
        e = []
        if tx_dec.bytes != [0x55, 0x4B, 0x21, 0x0A]:
            e.append(f"uart tx {tx_dec.bytes}")
        if h.received.get(1) != list(b"hello"):
            e.append(f"uart rx {h.received.get(1)}")
        if h.received.get(0) != [0x0000, 0x0033]:
            e.append(f"i2c {h.received.get(0)}")
        return e + board.errors()
    return Board(tx_dec, peer, i2c, host), host, check


EEPROM_SCRIPT = [("write", 0x50, 0x10, [0xDE, 0xAD, 0xBE, 0xEF]), ("read", 0x50, 0x10, 4),
                 ("probe", 0x51), ("cur", 0x50, 2), ("write", 0x50, 0x1E, [1, 2, 3]),
                 ("read", 0x50, 0x18, 1)]


def case_eeprom():
    """v1.1: 24C02 emulator, memory in the tape, Fast-mode controller with timing checks."""
    ctl = I2cController(2, 3, EEPROM_SCRIPT, mode="fast")

    def check(board, h):
        r = ctl.results
        ok = (len(r) == 6 and r[1]["data"] == [0xDE, 0xAD, 0xBE, 0xEF] and r[2]["acks"] == [False]
              and r[5]["data"] == [3])
        return ([] if ok else [f"eeprom {[(x['acks'], x['data']) for x in r]}"]) + board.errors()
    return Board(ctl), None, check


def case_can():
    """CAN 2.0A transmitter in firmware (CRC-15, stuffing) against a receiving node."""
    bus = CanBus(0, 1, 100)
    host = HostScript([(100, 0, (0x7FF << 16) | 8), (150, 0, 0x01020304), (200, 0, 0x05060708),
                       (40000, 0, (0x555 << 16) | 8), (40050, 0, 0xFFFFFFFF), (40100, 0, 0)],
                      drain=[0], poll=1000)

    def check(board, h):
        f = bus.frames
        ok = (len(f) == 2 and f[0]["id"] == 0x7FF and f[0]["data"] == [1, 2, 3, 4, 5, 6, 7, 8]
              and f[1]["id"] == 0x555 and all(x["crc_ok"] for x in f) and h.received[0] == [1, 1])
        return ([] if ok else [f"can {f} {h.received}"]) + board.errors()
    return Board(bus, host), host, check


def case_ps2():
    """PS/2 device in firmware (chip-generated clock, odd parity), scancodes back to back."""
    ps2 = Ps2Host(4, 5)
    host = HostScript([(50, 1, 0x1C), (70, 1, 0xF0)], drain=[1], poll=1000)

    def check(board, h):
        ok = ps2.bytes == [0x1C, 0xF0] and h.received[1] == [1, 1]
        return ([] if ok else [f"ps2 {ps2.bytes} {h.received}"]) + board.errors()
    return Board(ps2, host), host, check


def case_jtag():
    """JTAG controller reading an IDCODE, two requests back to back."""
    tap = JtagTap(4, 5, 6, 7, idcode=0x4BA00477)
    host = HostScript([(20, 2, 0), (40, 2, 0)], drain=[2], poll=500)

    def check(board, h):
        ok = h.received[2] == [0x4BA00477] * 2 and tap.state == "RTI"
        return ([] if ok else [f"jtag {h.received} {tap.state}"]) + board.errors()
    return Board(tap, host), host, check


def case_two_banks():
    """v1.2: PS/2 and JTAG on bank A while SPI and UART run on bank B, at once."""
    ps2 = Ps2Host(0, 1)
    tap = JtagTap(4, 5, 6, 7, idcode=0x4BA00477)
    spi = SpiTarget(sck=0, mosi=1, miso=2, cs=3, mode=3, reply=bytes((0x3C, 0xC3)))
    uart = UartRx(4, UART_TPB)
    host = HostScript([(20, 2, 3), (100, 2, 0x1A5), (110, 2, 0x05A), (30, 0, 0x1C), (40, 1, 0)],
                      drain=[0, 1, 2], poll=300)
    board_b = SplitBoard(spi, uart, outputs=0b11011)

    def check(board, h):
        ok = (ps2.bytes == [0x1C] and h.received[0] == [1] and h.received[1] == [0x4BA00477]
              and spi.frames == [[0xA5, 0x5A]] and h.received[2] == [0x3C, 0xC3]
              and uart.bytes == [0x55, 0x4B, 0x21, 0x0A])
        return (([] if ok else [f"two banks {ps2.bytes} {h.received} {spi.frames} {uart.bytes}"])
                + board.errors() + board_b.errors())
    return Board(ps2, tap, host), host, check, board_b


def case_swd():
    """SWD host: connect (JTAG-to-SWD) and DPIDR read, a SELECT write that is WAITed
    twice, a posted AP read and RDBUFF (the AP's IDR)."""
    from escmodels import SwdTarget
    tgt = SwdTarget(4, 5, dpidr=0x2BA01477, waits=2, ap_regs={(0, 0xFC): 0x24770011})
    host = HostScript([(20, 2, 0x80000004), (40, 2, 0x10), (50, 2, 0xF0),
                       (8000, 2, 0x1E), (8010, 2, 0x1C)], drain=[2], poll=300)

    def check(board, h):
        ok = (h.received[2] == [1, 0x2BA01477, 0x201, 0, 1, 0, 1, 0x24770011]
              and tgt.switches == 1 and tgt.writes == [(0, 0x8, 0xF0)])
        return ([] if ok else [f"swd {[hex(v) for v in h.received[2]]}"]) + board.errors()
    return Board(tgt, host), host, check
def case_nec_ir():
    """NEC IR: the transmitter's 38 kHz carrier through a receiver-module model into the
    receiver program, a frame and a repeat code one slot later, at a 2.5 MHz timebase
    (nec_ir_cosim.asm; the models' tick is 400 ns)."""
    from escmodels import NecReceiver, Tsop
    rx = NecReceiver(0, tick_ns=400.0)
    host = HostScript([(50, 0, 0x5AA5), (100, 0, 1 << 31)], drain=[0, 1], poll=500)

    def check(board, h):
        ok = (rx.frames() == [(0xA5, 0x5A), "R"] and h.received[0] == [1, 1]
              and h.received[1] == [0x5A00A5, 1 << 31])
        return ([] if ok else [f"nec {rx.events} {h.received}"]) + board.errors()
    return Board(rx, Tsop(0, 1, tick_ns=400.0), host), host, check


def eeprom_tape(image):
    for a in range(256):
        image.tape[a] = (a * 7 + 3) & 0xFF


def spi_target_case(mode):
    """SPI target (25xx-style memory in the tape) at its rated limit, 416 kHz SCK, against
    a controller model that checks MISO setup/hold: WRITE, READ back across a frame
    boundary, a READ and a WRITE cut mid-byte, frames back to back (CS_n high 4 us)."""
    from escmodels.spi import SpiController
    mem = [(a * 7 + 3) & 0xFF for a in range(256)]

    def factory():
        frames = [([0x02, 0x10, 0xDE, 0xAD], None),
                  ([0x03, 0x0F, 0, 0, 0, 0], [None, None, mem[0x0F], 0xDE, 0xAD, mem[0x12]]),
                  ([0x03, 0x10, 0, 0], [None, None, 0xDE, 0xAD], 21),
                  ([0x02, 0x11, 0x55, 0x66], None, 27),
                  ([0x03, 0x11, 0], [None, None, 0x55])]
        ctl = SpiController(4, 5, 6, 7, frames, mode=mode, t_high=30, t_low=30, t_css=25,
                            t_idle=100)

        def check(board, h):
            return ([] if ctl.done else ["spi_target: controller script not finished"]) + board.errors()
        return Board(ctl), None, check
    return factory


# name -> (firmware, ticks, factory() -> (board, host, check(board, host) -> list[str])
#          [, board_b] for programs on pin bank B)
def serial_case(baud, msg):
    """Generated UART-framed transmitters (escgen): one byte per host word."""
    def factory():
        rx = UartRx(0, 25e6 / baud)
        frame = int(11 * 25e6 / baud)
        host = HostScript([(100 + frame * i, 0, b) for i, b in enumerate(msg)])

        def check(board, h):
            return ([] if rx.bytes == list(msg) else [f"bytes {rx.bytes}"]) + board.errors()
        return Board(rx, host), host, check
    return factory


DMX_SLOTS = [0] + [(7 * i + 1) & 0xFF for i in range(1, 512)]
WS_COLOURS = [0x00FF00, 0xFF0000, 0x123456]


def dmx_tape(image):
    for w in range(128):
        image.tape[w] = sum(DMX_SLOTS[4 * w + j] << (8 * j) for j in range(4))


def ws_tape(image):
    image.tape[0] = len(WS_COLOURS)
    for i, c in enumerate(WS_COLOURS):
        image.tape[1 + i] = c


def case_gen_slow():
    """dmx512_tx at 5 MHz on bank A pin 0 and the display animation on bank B."""
    rx = DmxRx(0, clock_hz=5e6)
    seen = []

    def board_b(t, ob):
        if not seen or seen[-1][1] != ob:
            seen.append((t, ob))
        return 0

    def check(board, h):
        errs = list(rx.violations)
        if not rx.packets or rx.packets[0]["slots"] != DMX_SLOTS:
            errs.append(f"dmx packets {[len(p['slots']) for p in rx.packets]}")
        obs = [ob for _, ob in seen]          # from reset: released (and the RTL's t=-1)
        frames = obs[next(i for i, ob in enumerate(obs) if ob not in (0, 0x1F)):]
        if frames[:5] != [0x04, 0x08, 0x02, 0x01, 0x10]:
            errs.append(f"display frames {frames[:6]}")
        return errs + board.errors()
    return Board(rx), None, check, board_b


def case_ws2812_gen():
    dec = Ws2812Decoder(0, reset_us=280)

    def check(board, h):
        dec.finish()
        ok = dec.frames[:1] == [WS_COLOURS]
        return ([] if ok and not dec.violations else [f"ws2812 {dec.frames} {dec.violations}"])
    return Board(dec), None, check


class UsbBoard:
    """v1.4: the USB keyboard's world, a reactive USB host (escmodels.usb) on the pins and a
    host that pushes key reports into context 1's mailbox."""

    def __init__(self, script, pushes):
        self.out: dict = {}
        self.usb = UsbHost()
        self.usb.start(lambda h: script(h, self.out))
        self.host = HostScript(pushes)

    def __call__(self, t, uio_out, uio_oe):
        self.host.step(t, None)
        return self.usb(t, uio_out, uio_oe)


def case_usb_hid():
    """firmware/usb_hid.asm (line unit, v1.4): reset, descriptors, SET_ADDRESS (the token
    CRC-5 from a CRC job), SET_CONFIGURATION, then key reports on endpoint 1."""
    def script(h, out):
        yield from bus_reset(h, 1000)
        out["dev"] = yield from control(h, 0, setup_bytes(0x80, 6, 0x0100, 0, 18))
        out["setaddr"] = yield from control(h, 0, setup_bytes(0, 5, 3, 0, 0))
        yield ("idle", 2000)
        out["cfg"] = yield from control(h, 3, setup_bytes(0x80, 6, 0x0200, 0, 34))
        out["setcfg"] = yield from control(h, 3, setup_bytes(0, 9, 1, 0, 0))
        out["reports"] = []
        for _ in range(4):
            yield ("idle", 3000)
            r = yield from in_txn(h, 3, 1)
            out["reports"].append((r.packet.name, r.packet.body[:-2].hex()) if r else None)

    reports = [bytes([0, 0, 4 + i, 0, 0, 0, 0, 0]) for i in range(2)]
    pushes = [(30000 + 4000 * j + i, 1, int.from_bytes(r[4 * i:4 * i + 4], "little"))
              for j, r in enumerate(reports) for i in range(2)]
    board = UsbBoard(script, pushes)

    def check(b, h):
        o, errs = b.out, list(b.usb.errors)
        if not b.usb.done:
            errs.append("usb script unfinished")
        elif (len(o["dev"]) != 18 or o["dev"][8:12] != bytes((0x09, 0x12, 0x01, 0x00))
              or len(o["cfg"]) != 34 or o["setaddr"] != b"" or o["setcfg"] != b""):
            errs.append(f"usb enumeration {o}")
        elif o["reports"] != [("DATA0", "0000040000000000"), ("DATA1", "0000050000000000"),
                              ("NAK", ""), ("NAK", "")]:
            errs.append(f"usb reports {o['reports']}")
        if any(r.turnaround != 75 for r in b.usb.replies):
            errs.append(f"turnarounds {sorted({r.turnaround for r in b.usb.replies})}")
        return errs
    return board, board.host, check


def case_nec_gen():
    """One generated NEC frame at 2.5 MHz into the receiver model, acknowledged."""
    from escmodels import NecReceiver
    rx = NecReceiver(0, tick_ns=400.0)
    raw = 0x5A | (0xA5 << 8) | (0xC3 << 16) | (0x3C << 24)
    host = HostScript([(100, 0, raw)], drain=[0], poll=997)

    def check(board, h):
        ok = rx.frames() == [(0x5A, 0xC3)] and h.received[0] == [1]
        return ([] if ok else [f"nec {rx.events} acks {h.received}"]) + board.errors()
    return Board(rx, host), host, check


def case_dshot():
    """DShot600 to four ESC models from one context; throttle values from the host in
    the other (all four, then motor 1 with telemetry), both in the same frame. The run
    ends between frames."""
    from escmodels import DshotEsc
    escs = [DshotEsc(i) for i in range(4)]
    host = HostScript([(2000, 1, (1 << 14) | (48 << 1)), (2004, 1, (1 << 12) | (1000 << 1) | 1)])

    def check(board, h):
        for e in escs:
            e.flush()
        got = [e.frames[-1][:3] if e.frames else None for e in escs]
        ok = got == [(48, 0, True), (1000, 1, True), (48, 0, True), (48, 0, True)]
        return ([] if ok else [f"dshot {got}"]) + board.errors()
    return Board(*escs, host), host, check


def case_sent():
    """SENT sensor frames back to back; a new value from the host mid-run."""
    from escmodels import SentReceiver
    rx = SentReceiver(0)
    host = HostScript([(2000, 1, 0x3ABCDEF)])

    def check(board, h):
        got = [(f["status"], f["data"], f["crc_ok"]) for f in rx.frames]
        ok = (0, [0] * 6, True) in got and got[-1] == (3, [10, 11, 12, 13, 14, 15], True)
        return ([] if ok else [f"sent {got}"]) + board.errors()
    return Board(rx, host), host, check


def case_can_rx():
    """CAN receiver on the line unit: standard, extended and remote frames back to back
    from other nodes, CRC-15 checked by the line unit's CRC engine."""
    from escmodels import CanSource, encode_frame
    frames = [encode_frame(0x123, bytes((0x11, 0x22))), encode_frame(0x1ABCDE12, bytes(range(1, 9)), ext=True),
              encode_frame(0x7FF, b"", rtr=1, dlc=3)]
    plays, t0 = [], 1000
    for lv in frames:
        plays.append((t0, lv))
        t0 += (len(lv) + 3) * 100
    host = HostScript([], drain=[1], poll=1000)

    def check(board, h):
        want = [0x123, 2, 0x11220000, 0x9ABCDE12, 8, 0x01020304, 0x05060708, 0x400007FF, 3]
        ok = h.received[1] == want
        return ([] if ok else [f"can_rx {[hex(w) for w in h.received[1]]}"]) + board.errors()
    return Board(CanSource(1, plays, 100), host), host, check


def case_cec():
    """HDMI-CEC at a 250 kHz timebase: the chip sends CEC Version to a TV model, which
    acknowledges it; then the TV sends Give Physical Address to the chip, which
    acknowledges it and reports both blocks."""
    from escmodels import CecDevice
    tv = CecDevice(0, 0, tick_ns=4000.0, send=[(7500, [0x04, 0x83])])
    host = HostScript([(100, 0, 3), (200, 0, 0x40), (4000, 0, 0x9E), (4100, 0, 0x05)], drain=[0], poll=500)

    def check(board, h):
        ok = ([f["bytes"] for f in tv.frames] == [[0x40, 0x9E, 0x05], [0x04, 0x83]]
              and tv.frames[0]["acks"] == [0, 0, 0] and [r for r, _ in tv.results] == ["ack"]
              and h.received[0] == [0x40000000, 0x400 | (0x04 << 2), (0x83 << 2) | 2])
        return ([] if ok else [f"cec {tv.frames} {tv.results} {h.received}"]) + board.errors()
    return Board(tv, host), host, check


def case_dali():
    """DALI control gear at a 250 kHz timebase: QUERY CONTROL GEAR PRESENT, direct arc
    power, QUERY ACTUAL LEVEL; the answers come 7.5 ms after each query."""
    from escmodels import DaliController
    ctl = DaliController(0, [(1000, 0x0191), (11000, 0x0042), (21000, 0x01A0)], tick_ns=4000.0)
    host = HostScript([], drain=[0], poll=500)

    def check(board, h):
        ans = [a[0] if a else None for a in ctl.answers]
        ok = ans == [0xFF, None, 0x42] and h.received[0] == [0x30191, 0x10042, 0x301A0]
        return ([] if ok else [f"dali {ctl.answers} {h.received}"]) + board.errors()
    return Board(ctl, host), host, check


def case_can_tx_lu():
    """v1.6: CAN at 1 Mbit/s on the line unit (25 ticks a bit at the default clock), two
    frames, acknowledged by the bus model."""
    bus = CanBus(0, 1, 25)
    host = HostScript([(600, 0, (0x123 << 16) | 2), (650, 0, 0xDEAD0000), (700, 0, 0),
                       (4000, 0, (0x7FF << 16) | 8), (4050, 0, 0x01234567), (4100, 0, 0x89ABCDEF)],
                      drain=[0], poll=500)

    def check(board, h):
        f = bus.frames
        ok = ([(x["id"], x["data"]) for x in f] == [(0x123, [0xDE, 0xAD]),
                                                     (0x7FF, [1, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF])]
              and all(x["crc_ok"] for x in f) and h.received[0] == [1, 1])
        return ([] if ok else [f"can_tx_lu {f} {h.received}"]) + board.errors()
    return Board(bus, host), host, check


def case_spi_tx():
    """v1.6: write-only SPI through the line unit's clock pattern, 4 ticks a bit."""
    from escmodels import SpiTarget
    tgt = SpiTarget(4, 5, 6, 7, 0)
    host = HostScript([(100, 0, 3), (110, 0, 0xA53C0F00), (1500, 0, 5), (1510, 0, 0x01020304),
                       (1520, 0, 0x05000000)], drain=[0], poll=300)

    def check(board, h):
        ok = (tgt.frames == [[0xA5, 0x3C, 0x0F], [1, 2, 3, 4, 5]] and not tgt.violations
              and h.received[0] == [1, 1])
        return ([] if ok else [f"spi_tx {tgt.frames} {tgt.violations} {h.received}"]) + board.errors()
    return Board(tgt, host), host, check


class _TdRecorder:
    """TD+ and TD- (pins 0, 1) every tick, for the 10BASE-T case."""

    def __init__(self):
        self.lv = []

    def step(self, t, bus):
        self.lv.append(bus & 3)
        return 0, 0

    def errors(self):
        return []


def _td_frame(lv):
    """The bytes of the 10BASE-T frame after the first link pulse: the second time the pins
    leave idle (both high, released); a bit is two 2-tick cells, its value the second
    cell's TD+."""
    bits = []
    starts = [k for k in range(1, len(lv)) if lv[k] in (1, 2) and lv[k - 1] == 3]
    t = starts[1] if len(starts) > 1 else None
    while (t is not None and t + 4 <= len(lv) and lv[t] in (1, 2) and lv[t + 2] in (1, 2)
           and lv[t] != lv[t + 2]):
        bits.append(lv[t + 2] & 1)
        t += 4
    return bytes(sum(bits[8 * i + j] << j for j in range(8)) for i in range(len(bits) // 8))


def case_eth_tx():
    """v1.6: a 10BASE-T link pulse, then a frame from the host: preamble, SFD, frame and
    its FCS (checked against zlib's CRC-32) from one SEND, at 2 ticks a Manchester cell."""
    import zlib
    rec = _TdRecorder()
    frame = bytes(range(60))
    words = [int.from_bytes(frame[i:i + 4], "little") for i in range(0, 60, 4)]
    host = HostScript([(300 + 20 * i, 0, w) for i, w in enumerate([60] + words)], drain=[0], poll=400)

    def check(board, h):
        data = _td_frame(rec.lv)
        ok = (data[:8] == b"\x55" * 7 + b"\xd5" and data[8:68] == frame
              and int.from_bytes(data[68:72], "little") == zlib.crc32(frame) and len(h.received[0]) == 1)
        return ([] if ok else [f"eth {data[:12].hex()} {h.received}"]) + board.errors()
    return Board(rec, host), host, check


def case_udp_tx():
    """v1.6: a UDP datagram, headers and IPv4 checksum built on chip, padded to 60 bytes."""
    import struct
    import zlib
    rec = _TdRecorder()
    pay = b"escapement says hi"
    ip = bytearray(struct.pack("!BBHHHBBH4s4s", 0x45, 0, 28 + len(pay), 0, 0x4000, 64, 17, 0,
                               bytes([192, 168, 1, 100]), b"\xff" * 4))
    s_ = sum(struct.unpack("!10H", ip))
    s_ = (s_ & 0xFFFF) + (s_ >> 16)
    ip[10:12] = struct.pack("!H", ~s_ & 0xFFFF)
    frame = (b"\xff" * 6 + bytes.fromhex("020000000001") + b"\x08\x00" + bytes(ip)
             + struct.pack("!HHHH", 5000, 5000, 8 + len(pay), 0) + pay)
    padded = pay + b"\0" * (-len(pay) % 4)
    words = [len(pay)] + [int.from_bytes(padded[i:i + 4], "little") for i in range(0, len(padded), 4)]
    # (the store loop takes a word per ~40 ticks; the host model does not wait for room)
    host = HostScript([(300 + 100 * i, 0, w) for i, w in enumerate(words)], drain=[0], poll=400)

    def check(board, h):
        data = _td_frame(rec.lv)
        ok = (len(frame) == 60 and data[:8] == b"\x55" * 7 + b"\xd5" and data[8:68] == frame
              and int.from_bytes(data[68:72], "little") == zlib.crc32(frame) and len(h.received[0]) == 1)
        return ([] if ok else [f"udp {data[8:20].hex()} {h.received}"]) + board.errors()
    return Board(rec, host), host, check


class _Encoder(Device):
    """A quadrature encoder on pins 0 (A) and 1 (B): steps [(tick, +1 / -1)]."""

    def __init__(self, steps):
        self.steps, self.k, self.ph = steps, 0, 0

    def step(self, t, bus):
        while self.k < len(self.steps) and self.steps[self.k][0] <= t:
            self.ph = (self.ph + self.steps[self.k][1]) % 4
            self.k += 1
        g = [0, 1, 3, 2][self.ph]
        return (~g & 3, g)


def case_quadrature():
    """v1.6: 30 steps forward and 10 back from capture records; the host asks once."""
    steps = [(500 + 120 * k, 1 if k < 30 else -1) for k in range(40)]
    host = HostScript([(6000, 0, 0)], drain=[0], poll=200)

    def check(board, h):
        ok = h.received[0] == [20]
        return ([] if ok else [f"quadrature {h.received}"]) + board.errors()
    return Board(_Encoder(steps), host), host, check


def modbus_tape(image):
    for k in range(64):
        image.tape[k] = 0x1000 + k


def case_modbus():
    """v1.6: Modbus RTU server: Write Multiple Registers, a read across them, an
    exception; the write's notice to the host."""
    from escmodels.modbus import ModbusClient, adu
    reqs = [(adu(1, bytes([16, 0, 4, 0, 2, 4, 0xBE, 0xEF, 0x12, 0x34])), True),
            (adu(1, bytes([3, 0, 3, 0, 4])), True), (adu(1, bytes([5, 0, 0, 0xFF, 0])), True)]
    cl = ModbusClient(ro=1, di=0, de=2, ticks_per_bit=3e6 / 19200, requests=reqs,
                      start=7200, turn=600)
    host = HostScript([], drain=[0], poll=500)

    def check(board, h):
        out = [bytes(r["bytes"]) for r in cl.replies]
        ok = (len(out) == 3 and out[1][:3] == bytes([1, 3, 8])
              and out[1][3:11] == bytes.fromhex("1003beef12341006") and out[2][:3] == bytes([1, 0x85, 1])
              and h.received[0] == [4 | 2 << 16])
        return ([] if ok else [f"modbus {[o.hex() for o in out]} {h.received}"]) + board.errors()
    return Board(cl, host), host, check


def case_sbus():
    """v1.6: S.BUS receiver: two frames, the second with the failsafe flag, decoded into
    the tape under the sequence count; a notice per frame."""
    from escmodels.sbus import SbusSource, encode
    ch = [[(37 * k + 172 * f) & 0x7FF for k in range(16)] for f in range(2)]
    src = SbusSource(0, 50, [(1000, encode(ch[0])), (35000, encode(ch[1], flags=0x08))])
    host = HostScript([], drain=[0], poll=300)

    def check(board, h):
        ok = h.received[0] == [1, 2 | 0x08 << 16]
        return ([] if ok else [f"sbus {h.received}"]) + board.errors()
    return Board(src, host, pullups=0), host, check


class _Square(Device):
    """A square wave on one pin from tick `start`: `period` ticks, high for `high`."""

    def __init__(self, pin, period, high, start):
        self.m, self.period, self.high, self.start = 1 << pin, period, high, start

    def step(self, t, bus):
        up = t >= self.start and (t - self.start) % self.period < self.high
        return (0, self.m) if up else (self.m, 0)


def case_freq_meter():
    """v1.6: reciprocal counting from capture timestamps, 8 periods a gate: exact."""
    host = HostScript([], drain=[0], poll=300)

    def check(board, h):
        r = [v & 0xFFFFFF for v in h.received[0]]
        ok = len(r) >= 6 and set(zip(r[::2], r[1::2])) == {(2000, 600)}
        return ([] if ok else [f"freq_meter {r}"]) + board.errors()
    return Board(_Square(3, 250, 75, 400), host), host, check


# v1.6 transmitters, each at a low clock (FW_EQU) so a frame fits a short run; the models
# time in real time from the same clock, and the co-simulation compares every tick.
def _ir_case(model, khz, code, want):
    def case():
        import escmodels
        rx = getattr(escmodels, model)(0, tick_ns=1e6 / khz)
        host = HostScript([(100, 0, code)], drain=[0], poll=500)

        def check(board, h):
            rx.finish()
            got = [want(f) for f in rx.frames()]
            return ([] if got == [code] else [f"{model} {got}"]) + board.errors()
        return Board(rx, host, pullups=0), host, check
    return case


def case_ev1527():
    """v1.6: a 24-bit EV1527 code through the OOK receiver model, at 2 MHz."""
    from escmodels import Ev1527Receiver
    rx = Ev1527Receiver(0, tick_ns=500)
    host = HostScript([(100, 0, 0xABCDE5)], drain=[0], poll=500)

    def check(board, h):
        return ([] if rx.codes()[:1] == [0xABCDE5] else [f"ev1527 {rx.codes()}"]) + board.errors()
    return Board(rx, host, pullups=0), host, check


def case_dcc():
    """v1.6: one DCC packet (address, instruction, check byte) to the decoder, at 4 MHz."""
    from escmodels import DccDecoder
    dec = DccDecoder(0, tick_ns=250)
    host = HostScript([(100, 0, 0x03 | 0x74 << 8 | (0x03 ^ 0x74) << 16)], drain=[0], poll=500)

    def check(board, h):
        got = dec.packets()
        ok = (got and got[0]["bytes"] == [0x03, 0x74, 0x77] and got[0]["check_ok"]
              and not dec.violations)
        return ([] if ok else [f"dcc {got} {dec.violations[:2]}"]) + board.errors()
    return Board(dec, host, pullups=0), host, check


def case_wiegand():
    """v1.6: one Wiegand-26 card (facility 0x12, card 0x3456) on D0/D1, at 1 MHz."""
    from escmodels import WiegandReceiver
    rx = WiegandReceiver(0, 1, tick_ns=1000)
    v = 0x12 << 16 | 0x3456
    word = (bin(v >> 12).count("1") & 1) << 25 | v << 1 | (1 - (bin(v & 0xFFF).count("1") & 1))
    host = HostScript([(100, 0, word)], drain=[0], poll=500)

    def check(board, h):
        rx.finish()
        got = [WiegandReceiver.card26(b) for b in rx.frames]
        ok = got == [(0x12, 0x3456)] and not rx.violations
        return ([] if ok else [f"wiegand {got} {rx.violations[:2]}"]) + board.errors()
    return Board(rx, host), host, check


def case_lin():
    """v1.6: one LIN frame (identifier 0x2C, two bytes, enhanced checksum) at 19200 baud,
    at 1 MHz, against the responder-side model."""
    from escmodels import LinReceiver
    rx = LinReceiver(0, tick_ns=1000)
    host = HostScript([(100, 0, 0x2C | 2 << 8), (104, 0, 0x3412), (2000, 0, 0)],
                      drain=[0], poll=500)

    def check(board, h):
        rx.finish()
        got = [(f["id"], f["data"]) for f in rx.frames]
        ok = got == [(0x2C, [0x12, 0x34])] and not rx.violations and h.received[0] == [1]
        return ([] if ok else [f"lin {got} {rx.violations[:2]} {h.received}"]) + board.errors()
    return Board(rx, host), host, check


def case_lin_resp():
    """The LIN responder at 4 MHz (its lowest certified clock and the 64-tick bit floor):
    a commander's headers for a published ID (answered) and an unpublished one."""
    khz = 4000
    from escmodels import LinCommander, LinReceiver, lin_pid
    words = [1 << 30 | 0x10 << 0 | 2 << 8, 2 << 30 | 0 << 8 | 0x12, 2 << 30 | 1 << 8 | 0x34, 3 << 30]
    host_pushes = [(200 + k * khz * 5 // 4, 0, w) for k, w in enumerate(words)]
    start = 200 + 6 * khz * 5 // 4
    cmd = LinCommander(0, 1e6 / khz, [lin_pid(0x10), lin_pid(0x11)], start=start, slot_bits=90)
    rx = LinReceiver(0, tick_ns=1e6 / khz)

    def check(board, h):
        rx.finish()
        got = [(f["id"], f["data"]) for f in rx.frames]
        unanswered = [v for v in rx.violations if "frame [85, " not in v]
        ok = (got == [(0x10, [0x12, 0x34])] and not unanswered and len(rx.violations) == 1
              and h.received[0] == [lin_pid(0x10) | 0x100, lin_pid(0x11)])
        return ([] if ok else [f"lin_resp {got} {rx.violations[:2]} {h.received}"]) + board.errors()
    host = HostScript(host_pushes, drain=[0], poll=500)
    return Board(cmd, rx, host), host, check


def case_em4100():
    """The EM4100 tag at 4 MHz: one whole repetition of ID 0x0123456789 read back."""
    from escmodels.em4100 import Em4100Reader, words
    rd = Em4100Reader(0, 4000 * 64 // 125 / 2)
    w0, w1 = words(0x0123456789)
    host = HostScript([(100, 0, w0), (110, 0, w1)], drain=[0], poll=500)

    def check(board, h):
        ids = rd.ids()
        return ([] if ids and set(ids) == {0x0123456789} and not rd.bad_runs
                else [f"em4100 {ids} {rd.bad_runs[:2]}"]) + board.errors()
    return Board(rd, host), host, check


def case_wiegand_rx():
    """The Wiegand panel receiver at 4 MHz: one 26-bit frame at 200 us intervals."""
    khz = 4000
    from escmodels import WiegandSender
    bits = [0, 0, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1]
    want = [26, 0, int("".join(map(str, bits)), 2)]
    snd = WiegandSender(0, 1, 1e6 / khz, [bits], interval_us=200, start=200)

    def check(board, h):
        return ([] if h.received[0] == want else [f"wiegand_rx {h.received[0]}"]) + board.errors()
    host = HostScript([], drain=[0], poll=500)
    return Board(snd, host), host, check


def case_usbpd():
    """USB PD at 25 MHz: a Source_Capabilities message (header, one PDO, the CRC-32 the
    chip computes) through the BMC sink model."""
    from escmodels.usbpd import UsbPdSink
    sink = UsbPdSink(0, 40.0)
    hdr, pdo = 0x11A1, 100 << 10 | 300
    host = HostScript([(100, 0, hdr), (150, 0, pdo)], drain=[0], poll=500)

    def check(board, h):
        sink.finish()
        got = [(x["header"], x["data"], x["crc_ok"], x["eop"]) for x in sink.messages]
        ok = got == [(hdr, [pdo], True, True)] and not sink.violations and h.received[0] == [1]
        return ([] if ok else [f"usbpd {got} {sink.violations[:2]} {h.received}"]) + board.errors()
    return Board(sink, host, pullups=0), host, check


def case_dcf77():
    """DCF77 at a 10 kHz clock: one whole minute (23:59, 31 Dec '99), the parity bits and
    bits 0 and 20 set by the chip, and the minute marker that ends it."""
    from escmodels.dcf77 import Dcf77Receiver, encode
    bits = encode(minute=59, hour=23, day=31, weekday=7, month=12, year=99)
    bits[0], bits[20], bits[28], bits[35], bits[58] = 1, 0, bits[28] ^ 1, bits[35] ^ 1, bits[58] ^ 1
    v = sum(b << k for k, b in enumerate(bits))
    rx = Dcf77Receiver(0, 1e5)
    host = HostScript([(50, 0, v & 0xFFFFFFFF), (60, 0, v >> 32)], drain=[0], poll=5000)

    def check(board, h):
        rx.finish()
        want = [dict(minute=59, hour=23, day=31, weekday=7, month=12, year=99, cest=False)]
        return ([] if rx.minutes[:1] == want and not rx.violations
                else [f"dcf77 {rx.minutes} {rx.violations[:2]}"]) + board.errors()
    return Board(rx, host), host, check


def case_sd_spi():
    """The SD host at 25 MHz, 400 kHz SCK: start-up (CRC7 on every command), the OCR, one
    block read with its CRC-16 checked."""
    from escmodels.sdcard import OCR_READY, SdCardSpi
    sd = SdCardSpi(4, 5, 6, 7)

    def check(board, h):
        r = h.received[0]
        ok = r[:3] == [4 << 16, OCR_READY, 0] and not sd.violations
        return ([] if ok else [f"sd_spi {[hex(x) for x in r]} {sd.violations[:2]}"]) + board.errors()
    host = HostScript([(1_000_000, 0, 0x1234)], drain=[0], poll=2500)
    return Board(sd, host), host, check


def case_onewire_dev():
    """The 1-Wire device at 25 MHz: reset and presence, Read ROM, Search ROM (AN187)."""
    khz = 25000
    from escmodels import OneWireMaster, crc8_maxim
    rom = bytes([0x28, 1, 2, 3, 4, 5, 6])
    rom += bytes([crc8_maxim(rom)])

    def prog(mm):
        yield from mm.reset()
        yield from mm.write(0x33)
        mm.results.append((yield from mm.read_n(8)))
        mm.results.append((yield from mm.search()))
    mst = OneWireMaster(0, 1e6 / khz, prog)

    def check(board, h):
        ok = mst.done and mst.results == [rom, [int.from_bytes(rom, "little")]]
        return ([] if ok else [f"onewire_dev {mst.done} {mst.results}"]) + board.errors()
    host = HostScript([], drain=[0], poll=5000)
    return Board(mst, host), host, check


def case_avr_isp():
    """The AVR programmer at 25 MHz, SCK 125 kHz, against an ATmega328P in serial
    programming mode: 20 ms after RESET, Programming Enable with its echo, the signature,
    then a raw Read Fuse Low."""
    from escmodels.avr import AvrIspTarget
    avr = AvrIspTarget(4, 5, 6, 7, 40.0)

    def check(board, h):
        r = h.received[0]
        ok = r[:2] == [0, 0x1E950F] and len(r) == 3 and r[2] & 0xFF == 0x62
        ok = ok and avr.enables == 1 and not avr.violations
        return ([] if ok else [f"avr_isp {[hex(x) for x in r]} {avr.violations[:2]}"]) + board.errors()
    host = HostScript([(100, 0, 1 << 28), (575_000, 0, 2 << 28), (575_010, 0, 0x50000000)],
                      drain=[0], poll=2500)
    return Board(avr, host), host, check


def case_qi_rx():
    """The Qi receiver at 2 MHz: the Power Signal at 1 ms, SIG at tWake, ID, CFG and the
    first CE, every bit, parity, checksum and spacing checked by the transmitter model."""
    from escmodels.qi import QiTransmitter
    qtx = QiTransmitter(0, 1, 500.0, power=((1000, None),))

    def check(board, h):
        qtx.finish()
        got = [(p["name"], p["message"]) for p in qtx.packets]
        ok = (got == [("SIG", [0x80]), ("ID", [0x12, 0, 0, 0, 0, 0, 1]), ("CFG", [10, 0, 0, 0x12, 0]),
                      ("CE", [0xFB])] and not qtx.violations and h.received[0] == [1])
        return ([] if ok else [f"qi_rx {got} {qtx.violations[:2]} {h.received}"]) + board.errors()
    host = HostScript([(100, 0, 2 << 28 | 0xFB)], drain=[0], poll=2000)
    return Board(qtx, host, pullups=0), host, check


def case_spi_flash():
    """The flash programmer at 25 MHz, SCK 500 kHz, against a W25Q80DV model: the JEDEC ID,
    Sector Erase and Page Program with BUSY polled (tSE and tPP shortened), Read Data back."""
    from escmodels.spiflash import SpiFlashW25
    fl = SpiFlashW25(4, 5, 6, 7, 40.0, t_pp_ms=0.3, t_se_ms=1.0)
    fl.mem.update({a: 0x5A for a in range(0x1000, 0x1010)})

    def check(board, h):
        r = h.received[0]
        ok = r == [0xEF4014, 0, 0, 0x01020304, 0xFFFFFFFF] and not fl.violations
        return ([] if ok else [f"spi_flash {[hex(x) for x in r]} {fl.violations[:2]}"]) + board.errors()
    host = HostScript([(100, 0, 1 << 28), (2000, 0, 4 << 28 | 0x001000),
                       (40_000, 0, 3 << 28 | 0x001000), (40_010, 0, 1), (42_000, 0, 0x01020304),
                       (70_000, 0, 2 << 28 | 0x001000), (70_010, 0, 2)], drain=[0], poll=2500)
    return Board(fl, host), host, check


def case_max7219():
    """The MAX7219 driver at 25 MHz, CLK 500 kHz: set up a code B display, then a number
    divided into decimal digits on the chip, leading zeros blank."""
    from escmodels.max7219 import Max7219Chain
    ch = Max7219Chain(4, 5, 7, 40.0)

    def check(board, h):
        ok = ch.text() == "  904561" and not ch.violations
        return ([] if ok else [f"max7219 {ch.text()!r} {ch.violations[:2]}"]) + board.errors()
    host = HostScript([(100, 0, 1 << 28 | 1 << 11 | 7 << 8 | 8), (25_000, 0, 3 << 28 | 904561)],
                      drain=[0], poll=2500)
    return Board(ch, host), host, check


def case_stepper():
    """Stepper motion at 25 MHz: 20 steps at 500,000 steps/s^2 (AVR446 ramp computed on the
    chip, then played), STEP and DIR at A4988 timing, the stop ramp mirroring the start."""
    from escmodels.stepper import StepDirDriver
    drv = StepDirDriver(0, 1, 40.0)

    def check(board, h):
        t = [s[0] for s in drv.steps]
        iv = [b - a for a, b in zip(t, t[1:])]
        ok = (drv.position == 20 and not drv.violations and h.received[0] == [20]
              and all(abs(iv[i] - iv[-1 - i]) < 0.01 for i in range(9)))
        return ([] if ok else [f"stepper {drv.position} {drv.violations[:2]} {h.received}"]) + board.errors()
    host = HostScript([(100, 0, 1 << 28 | 1352), (200, 0, 2 << 28 | 100), (300, 0, 3 << 28 | 1 << 24 | 20)],
                      drain=[0], poll=2500)
    return Board(drv, host, pullups=0), host, check


def case_nrf24_tx():
    """The nRF24L01+ driver at 25 MHz: set up (Tpd2stby kept), then a packet lost once and
    retransmitted ARD later, acknowledged: TX_DS read and cleared."""
    from escmodels.nrf24 import Nrf24Ptx
    radio = Nrf24Ptx(4, 5, 6, 7, 3, 2, 40.0, acks=[False, True])

    def check(board, h):
        ok = (h.received[0] == [0x0E, 0x20] and [s["payload"] for s in radio.sent] == [b"cosim!!"] * 2
              and not radio.violations)
        return ([] if ok else [f"nrf24_tx {h.received} {radio.violations[:2]}"]) + board.errors()
    # (the mailbox holds two words: the second payload word goes after the set-up's 1.5 ms)
    host = HostScript([(100, 0, 1 << 28 | 0x06 << 16 | 0x13 << 8 | 2), (110, 0, 3 << 28 | 7),
                       (2000, 0, int.from_bytes(b"cosi", "big")), (60_000, 0, int.from_bytes(b"m!! ", "big"))],
                      drain=[0], poll=2500)
    return Board(radio, host), host, check


def case_morse():
    """The Morse keyer at 25 MHz: SOS with 1 ms dots, against a listener written from ITU-R
    M.1677-1 (every signal and gap to 1% of its length)."""
    from escmodels.morse import MorseListener
    ear = MorseListener(0, 40.0, 1)

    def check(board, h):
        ok = ear.text() == "SOS" and not ear.violations
        return ([] if ok else [f"morse {ear.text()!r} {ear.violations[:2]}"]) + board.errors()
    host = HostScript([(100, 0, 1 << 28 | 1), (110, 0, 2 << 28 | ord("S") << 16 | ord("O") << 8 | ord("S"))],
                      drain=[0], poll=2500)
    return Board(ear, host, pullups=0), host, check


def case_ws2812_lu():
    """v1.6: three LEDs from one line unit SEND, at 12 MHz (cells of 5 ticks)."""
    from escmodels import Ws2812Decoder
    dec = Ws2812Decoder(0, tick_ns=1e6 / 12000, reset_us=280)
    grb = [0x00FF00, 0x123456, 0xABCDEF]
    data = b"".join(c.to_bytes(3, "big") for c in grb) + b"\0\0\0"
    words = [int.from_bytes(data[i:i + 4], "big") for i in range(0, 9, 4)]
    host = HostScript([(100 + 200 * i, 0, w) for i, w in enumerate([3] + words)], drain=[0], poll=500)

    def check(board, h):
        dec.finish()
        ok = dec.frames == [grb] and not dec.violations and h.received[0] == [1]
        return ([] if ok else [f"ws2812_lu {dec.frames} {dec.violations[:2]} {h.received}"]) + board.errors()
    return Board(dec, host, pullups=0), host, check


class _Pins(Device):
    """Records the bank A levels every tick (an undriven pin reads low: pulled down)."""

    def __init__(self):
        self.lv = []

    def step(self, t, bus):
        self.lv.append(bus)
        return 0, 0

    def errors(self):
        return []


def case_servo():
    """v1.6: eight servo channels at 1.5 MHz; channel 3 set to 2000 us from the host takes
    effect in the second frame. Every pulse width is checked to the tick."""
    pins = _Pins()
    host = HostScript([(100, 0, 3 << 16 | (2000 - 500))])
    khz = 1500

    def check(board, h):
        lv, errs = pins.lv, []
        for ch in range(8):
            ups = [t for t in range(1, len(lv)) if (lv[t] >> ch) & 1 and not (lv[t - 1] >> ch) & 1]
            widths = []
            for u in ups:
                d = next((t for t in range(u, len(lv)) if not (lv[t] >> ch) & 1), None)
                if d is not None:
                    widths.append(d - u)
            want = [1500 * khz // 1000, (2000 if ch == 3 else 1500) * khz // 1000]
            if [abs(w - x) <= 2 for w, x in zip(widths, want)] != [True, True]:
                errs.append(f"servo ch{ch} widths {widths} want {want}")
        return errs + board.errors()
    return Board(pins, host, pullups=0), host, check


FIRMWARE_CASES = {
    "uart_rx": ("uart_rx.asm", 13500, case_uart_rx),
    "i2c_ctrl": ("i2c_ctrl.asm", 9000, case_i2c),
    "spi_mode3": ("spi_ctrl.asm", 2500, case_spi3),
    "ws2812": ("ws2812.asm", 9000, case_ws2812),
    "onewire": ("onewire.asm", 60000, case_onewire),
    "combo_uart_i2c": ("combo_uart_i2c.asm", 13000, case_combo),
    "i2c_eeprom": ("i2c_eeprom.asm", 110000, case_eeprom),
    "can_tx": ("can_tx.asm", 80000, case_can),
    "ps2_device": ("ps2_device.asm", 55000, case_ps2),
    "jtag_idcode": ("jtag_idcode.asm", 9000, case_jtag),
    "swd_idcode": ("swd_idcode.asm", 17000, case_swd),
    "nec_ir": ("../test/cosim/nec_ir_cosim.asm", 304000, case_nec_ir),
    "spi_target": ("spi_target.asm", 11000, spi_target_case(0)),
    "spi_target_mode3": ("spi_target.asm", 11000, spi_target_case(3)),
    "combo_two_banks": ("combo_two_banks.asm", 30000, case_two_banks),
    # v1.3 protocols as data (escgen) and the demo board display
    "uart_tx_gen": ("uart_tx_gen.asm", 9000, serial_case(115200, b"Esc")),
    "midi_tx": ("midi_tx.asm", 30000, serial_case(31250, bytes((0x90, 0x3C, 0x7F)))),
    "ws2812_gen": ("ws2812_gen.asm", 9000, case_ws2812_gen),   # ends inside the latch
    "gen_slow": ("../test/cosim/gen_cosim.asm", 130000, case_gen_slow),
    "nec_tx_gen": ("../test/cosim/nec_gen_cosim.asm", 300000, case_nec_gen),
    # v1.4 line unit
    "usb_hid": ("usb_hid.asm", 70000, case_usb_hid),
    "can_rx": ("can_rx.asm", 36000, case_can_rx),
    # motor, sensor, consumer and lighting buses (firmware only)
    "dshot": ("dshot_tx.asm", 12500, case_dshot),
    "sent": ("sent_tx.asm", 36000, case_sent),
    "cec": ("../test/cosim/cec_cosim.asm", 46000, case_cec),
    "dali": ("../test/cosim/dali_cosim.asm", 33000, case_dali),
    # v1.6: the line unit's extended configuration
    "can_tx_lu": ("can_tx_lu.asm", 8000, case_can_tx_lu),
    "spi_tx": ("spi_tx.asm", 3000, case_spi_tx),
    "eth_tx": ("eth_tx.asm", 4500, case_eth_tx),
    "freq_meter": ("freq_meter.asm", 8000, case_freq_meter),
    "udp_tx": ("udp_tx.asm", 4500, case_udp_tx),
    "quadrature": ("quadrature.asm", 7000, case_quadrature),
    "modbus": ("../test/cosim/modbus_cosim.asm", 150000, case_modbus),
    "sbus": ("../test/cosim/sbus_cosim.asm", 65000, case_sbus),
    "sirc_tx": ("sirc_tx.asm", 60000, _ir_case("SircReceiver", 2000, 0x15 | 0x01 << 7,
                                                lambda f: f["address"] << 7 | f["command"])),
    "rc5_tx": ("rc5_tx.asm", 60000, _ir_case("Rc5Receiver", 2000, 0b11 << 12 | 0x05 << 6 | 0x0C,
                                              lambda f: f["start"] << 12 | f["toggle"] << 11
                                              | f["address"] << 6 | f["command"])),
    # RC-6 at 8 MHz: escert proves rc6_tx from 8 MHz up (2 MHz would run unproven)
    "rc6_tx": ("rc6_tx.asm", 215000, _ir_case("Rc6Receiver", 8000, 0b1000 | 1 << 4 | 0xA5 << 13 | 0x0C << 5,
                                               lambda f: 0b1000 | f["mode"] | f["toggle"] << 4
                                               | f["address"] << 13 | f["command"] << 5)),
    "jvc_tx": ("jvc_tx.asm", 85000, _ir_case("JvcReceiver", 2000, 0x59 | 0x35 << 8,
                                              lambda f: f["address"] | f["command"] << 8)),
    "ev1527_tx": ("ev1527_tx.asm", 90000, case_ev1527),
    "dcc_tx": ("dcc_tx.asm", 40000, case_dcc),
    "wiegand_tx": ("wiegand_tx.asm", 60000, case_wiegand),
    "lin_tx": ("lin_tx.asm", 8000, case_lin),
    "lin_resp": ("lin_resp.asm", 60000, case_lin_resp),
    "em4100_tag": ("em4100_tag.asm", 140000, case_em4100),
    "wiegand_rx": ("wiegand_rx.asm", 115000, case_wiegand_rx),
    "usbpd_tx": ("usbpd_tx.asm", 45000, case_usbpd),
    "dcf77_tx": ("dcf77_tx.asm", 625000, case_dcf77),
    "sd_spi": ("sd_spi.asm", 1_700_000, case_sd_spi),
    "onewire_dev": ("onewire_dev.asm", 640_000, case_onewire_dev),
    "avr_isp": ("avr_isp.asm", 612_000, case_avr_isp),
    "qi_rx": ("qi_rx.asm", 940_000, case_qi_rx),
    "spi_flash": ("spi_flash.asm", 85_000, case_spi_flash),
    "max7219": ("max7219.asm", 46_000, case_max7219),
    "stepper": ("stepper.asm", 380_000, case_stepper),
    "nrf24_tx": ("nrf24_tx.asm", 125_000, case_nrf24_tx),
    "morse": ("morse.asm", 760_000, case_morse),
    "ws2812_lu": ("ws2812_lu.asm", 8000, case_ws2812_lu),
    "servo": ("servo.asm", 75000, case_servo),
}
TAPE_PREP = {"i2c_eeprom": eeprom_tape, "ws2812_gen": ws_tape, "gen_slow": dmx_tape,
             "modbus": modbus_tape}
TAPE_PREP["spi_target"] = TAPE_PREP["spi_target_mode3"] = eeprom_tape
# parameters a case sets on its firmware's .defaults (a short gate for the meter)
FW_EQU = {"freq_meter": {"N": 8, "PIN": 3},
          "sirc_tx": {"CLK_KHZ": 2000}, "rc5_tx": {"CLK_KHZ": 2000}, "rc6_tx": {"CLK_KHZ": 8000}, "jvc_tx": {"CLK_KHZ": 2000},
          "ev1527_tx": {"CLK_KHZ": 2000}, "dcc_tx": {"CLK_KHZ": 4000},
          "wiegand_tx": {"CLK_KHZ": 1000}, "lin_tx": {"CLK_KHZ": 1000}, "lin_resp": {"CLK_KHZ": 4000}, "em4100_tag": {"CLK_KHZ": 4000}, "wiegand_rx": {"CLK_KHZ": 4000}, "dcf77_tx": {"CLK_KHZ": 10}, "qi_rx": {"CLK_KHZ": 2000},
          "ws2812_lu": {"CLK_KHZ": 12000}, "servo": {"CLK_KHZ": 1500}}


def fw_image(name, fw, firmware_dir):
    """The image a case runs: its firmware, with FW_EQU parameters and TAPE_PREP applied."""
    from escasm import assemble, assemble_file
    if name in FW_EQU:
        mod = fw.rsplit(".", 1)[0]
        equ = "".join(f".equ {mod}.{k}, {v}\n" for k, v in FW_EQU[name].items())
        image = assemble(equ + f'.include "{fw}"\n', base_dir=firmware_dir, name=fw)
    else:
        image = assemble_file(Path(firmware_dir) / fw)
    if name in TAPE_PREP:
        TAPE_PREP[name](image)
    return image
