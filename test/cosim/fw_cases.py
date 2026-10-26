"""Firmware cases for the bus-model co-simulation (test_cosim.py) and the proof table
(proof_table.py): name -> (firmware, ticks, factory). A factory returns fresh bus models
(board, host script, check[, bank B board]); call it once per implementation.
"""

from __future__ import annotations

from escmodels import (Board, CanBus, DmxRx, HostScript, I2cController, I2cTarget, JtagTap,
                       OneWireDevice, Ps2Host, SpiTarget, SplitBoard, UartRx, UartTx,
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
}
TAPE_PREP = {"i2c_eeprom": eeprom_tape, "ws2812_gen": ws_tape, "gen_slow": dmx_tape}
TAPE_PREP["spi_target"] = TAPE_PREP["spi_target_mode3"] = eeprom_tape
