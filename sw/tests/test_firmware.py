"""Firmware on the ISS against bus models: protocol results AND waveform timing.

The same board models are reused by the RTL co-simulation, so each firmware check
runs on both independent implementations.
"""

from pathlib import Path

import pytest

from escasm import assemble_file
from escmodels import (Board, Device, HostScript, I2cTarget, OneWireDevice, SpiTarget, UartRx, UartTx,
                       Ws2812Decoder, crc8_maxim)

FW = Path(__file__).resolve().parents[2] / "firmware"
TICK_NS = 40.0  # 25 MHz


def run(fw, board, ticks, script=None):
    """Run for exactly `ticks` cycles (never stop early: receivers need the tail)."""
    from esciss import Machine
    m = Machine(assemble_file(FW / fw), board=board)
    if script is not None:
        script.machine = m
    m.run(ticks, stop_when_idle=False)
    return m


def scl_periods(board, pin):
    lv = board.level(pin)
    falls = [t for t in range(1, len(lv)) if lv[t - 1] == 1 and lv[t] == 0]
    return [b - a for a, b in zip(falls, falls[1:])]


def test_uart_tx_115200():
    rx = UartRx(0, 25e6 / 115200)
    board = Board(rx)
    m = run("uart_tx.asm", board, 30000)
    assert rx.bytes == [0x55, 0x4B, 0x21, 0x0A]
    assert not board.errors()
    assert m.dbg_read(0, 7) >> 2 & 3 == 2          # HALTED
    assert m.dbg_read(0, 14) >> 31 == 0             # no fault


def i2c_cmd(addr7, reg, data=0, write=False, read=False):
    return (addr7 << 24) | (reg << 16) | (data << 8) | (1 if write else 0) | (2 if read else 0)


@pytest.mark.parametrize("stretch", [0, 37, 400])
def test_i2c_fast_mode_write_then_read(stretch):
    target = I2cTarget(scl=2, sda=3, addr7=0x42, regs={5: 0x00, 6: 0x5C}, mode="fast",
                       tick_ns=TICK_NS, stretch_ticks=stretch)
    gap = 3500 + 40 * stretch
    script = HostScript([(50, 0, i2c_cmd(0x42, 5, 0xA5, write=True)),
                         (50 + gap, 0, i2c_cmd(0x42, 5, read=True)),
                         (50 + 2 * gap, 0, i2c_cmd(0x42, 6, read=True))], drain=[0], poll=500)
    board = Board(target, script)
    m = run("i2c_ctrl.asm", board, 50 + 3 * gap + 1000, script)
    results = script.received[0]
    assert results == [0x0000, 0x00A5, 0x005C], [hex(r) if r is not None else r for r in results]
    assert target.regs[5] == 0xA5
    assert not board.errors(), board.errors()
    periods = scl_periods(board, 2)
    assert min(periods) * TICK_NS >= 2500, f"fSCL above 400 kHz: min period {min(periods)} ticks"
    if stretch == 0:
        # typical bit period must be close to the 400 kHz ceiling
        typical = sorted(periods)[len(periods) // 2]
        assert typical <= 70, typical
    assert m.dbg_read(0, 14) >> 31 == 0


def test_i2c_address_nack():
    target = I2cTarget(scl=2, sda=3, addr7=0x42, mode="fast", tick_ns=TICK_NS)
    script = HostScript([(50, 0, i2c_cmd(0x17, 1, 0x99, write=True))], drain=[0], poll=500)
    board = Board(target, script)
    m = run("i2c_ctrl.asm", board, 4000, script)
    res = script.received[0][0]
    # address byte NACKed; the controller still clocks out the remaining bytes (no ACK
    # from anyone), so all three ACK bits read as NACK.
    assert res == 0x0700, hex(res)
    assert not board.errors()


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_spi_modes(mode):
    target = SpiTarget(sck=4, mosi=5, miso=6, cs=7, mode=mode, reply=b"\x3C\xC3\x81")
    script = HostScript([(20, 2, mode)] + [(100 + 10 * i, 2, w) for i, w in
                                            enumerate([0x1A5, 0x05A])] +
                        [(2000, 2, 0x0F0)], drain=[2], poll=300)
    board = Board(target, script)
    m = run("spi_ctrl.asm", board, 6000, script)
    got = script.received[2]
    assert target.frames == [[0xA5, 0x5A], [0xF0]], target.frames
    assert got == [0x3C, 0xC3, 0x81], got
    assert not board.errors(), board.errors()


@pytest.mark.parametrize("gap_bits,baud_err", [(1.0, 0.0), (0.0, 0.0), (0.0, +0.02), (0.0, -0.02)])
def test_uart_rx_115200(gap_bits, baud_err):
    """Back-to-back frames and a +/-2 % peer baud mismatch, all 256 byte values."""
    data = bytes(range(256))
    tpb = 25e6 / 115200 * (1 + baud_err)
    tx = UartTx(1, tpb, data, start=500, gap_bits=gap_bits)
    board = Board(tx)
    script = HostScript([], drain=[1], poll=1400)
    board.devices.append(script)
    m = run("uart_rx.asm", board, tx.end + 4000, script)
    got = script.received[1]
    assert got == list(data), f"{len(got)} bytes; first diff at " \
        f"{next((i for i, (a, b) in enumerate(zip(got, data)) if a != b), None)}"


def test_ws2812_chain():
    dec = Ws2812Decoder(0, TICK_NS)
    script = HostScript([(30, 3, 0x102030), (40, 3, 0x010203), (2000, 3, 6)], drain=[3], poll=97)
    board = Board(dec, script)
    m = run("ws2812.asm", board, 12000, script)
    dec.finish()
    expect = [(0x102030 + i * 0x010203) & 0xFFFFFF for i in range(6)]
    assert dec.frames == [expect], (dec.frames, expect)
    assert script.received[3] == [1]
    assert not board.errors(), board.errors()
    assert m.dbg_read(3, 14) >> 31 == 0, "no admission fault: firmware kept up"


def test_onewire_read_rom():
    rom = bytes([0x28, 0xAA, 0x01, 0x02, 0x03, 0x04, 0x05])
    rom += bytes([crc8_maxim(rom)])
    dev = OneWireDevice(1, rom, TICK_NS)
    cmds = [(30, 3, 0x80000033)] + [(40000 + 20000 * i, 3, 0xFF) for i in range(8)]
    script = HostScript(cmds, drain=[3], poll=500)
    board = Board(dev, script)
    m = run("onewire.asm", board, 220000, script)
    got = script.received[3]
    assert got[0] == 0x100 | 0xFF or got[0] >> 8 == 1, hex(got[0])   # presence seen
    data = bytes(v & 0xFF for v in got[1:9])
    assert dev.written[0] == 0x33
    assert data == rom and crc8_maxim(data[:7]) == data[7], data.hex()
    assert not board.errors(), board.errors()


def test_concurrent_uart_tx_rx_and_i2c():
    """V1 must-ship: UART TX + UART RX + I2C controller simultaneously, one image."""
    tx_dec = UartRx(0, 25e6 / 115200)                    # decodes our TX
    peer = UartTx(1, 25e6 / 115200, b"hello", start=700, gap_bits=0.0)  # feeds our RX
    i2c = I2cTarget(2, 3, 0x42, {9: 0x77}, mode="fast", tick_ns=TICK_NS, stretch_ticks=50)
    script = HostScript([(100, 0, i2c_cmd(0x42, 9, 0x33, write=True)),
                         (4500, 0, i2c_cmd(0x42, 9, read=True))], drain=[0, 1], poll=700)
    board = Board(tx_dec, peer, i2c, script)
    m = run("combo_uart_i2c.asm", board, 13000, script)
    assert tx_dec.bytes == [0x55, 0x4B, 0x21, 0x0A]
    assert script.received[1] == list(b"hello")
    assert script.received[0] == [0x0000, 0x0033] and i2c.regs[9] == 0x33
    assert not board.errors(), board.errors()
    assert m.dbg_read(0, 14) >> 31 == 0


# --------------------------------------------------------------------------- v1.1 tape


EEPROM_SCRIPT = [("write", 0x50, 0x10, [0xDE, 0xAD, 0xBE, 0xEF]),   # page write
                 ("read", 0x50, 0x10, 4),                          # random read (Sr)
                 ("probe", 0x51),                                  # foreign address
                 ("cur", 0x50, 2),                                 # current-address read
                 ("write", 0x50, 0x1E, [1, 2, 3]),                 # page roll-over
                 ("read", 0x50, 0x18, 1)]


@pytest.mark.parametrize("mode,ticks", [("standard", 330_000), ("fast", 110_000)])
def test_i2c_eeprom_emulator(mode, ticks):
    """A 24C02 in firmware, its memory in the tape, against a scripted controller that
    checks the target's timing (no SDA change with SCL high, tSU;DAT)."""
    from escmodels import I2cController
    from esciss import Machine
    image = assemble_file(FW / "i2c_eeprom.asm")
    for a in range(256):
        image.tape[a] = (a * 7 + 3) & 0xFF                 # preloaded contents
    ctl = I2cController(2, 3, EEPROM_SCRIPT, mode=mode)
    board = Board(ctl)
    m = Machine(image, board=board)
    m.run(ticks, stop_when_idle=False)
    assert not board.errors(), board.errors()
    r = ctl.results
    assert [x["acks"] for x in r] == [[True] * 6, [True] * 3, [False], [True], [True] * 5, [True] * 3]
    assert r[1]["data"] == [0xDE, 0xAD, 0xBE, 0xEF]
    assert r[3]["data"] == [(0x14 * 7 + 3) & 0xFF, (0x15 * 7 + 3) & 0xFF]   # pointer moved on
    assert m.tape[0x1E:0x20] == [1, 2] and m.tape[0x18] == 3 and r[5]["data"] == [3]
    assert not m.ff.valid and m.ctx[0].min_lead == 2     # every edge admitted, lead 2


CAN_FRAMES = [(0x123, 2, 0xDEAD0000, 0), (0x7FF, 8, 0x01020304, 0x05060708),
              (0x000, 0, 0, 0), (0x555, 8, 0xFFFFFFFF, 0x00000000)]


def test_can_tx_frames_decoded_by_independent_receiver():
    """CAN 2.0A at 250 kbit/s in firmware: CRC-15 and stuffing on-chip; a receiver model
    destuffs, checks the CRC and ACKs; the chip samples its ACK slot."""
    from escmodels import CanBus
    from esciss import Machine
    bus = CanBus(0, 1, 100)
    board = Board(bus)
    m = Machine(assemble_file(FW / "can_tx.asm"), board=board)
    acks = []
    for ident, dlc, w1, w2 in CAN_FRAMES:
        m.run(200, stop_when_idle=False)
        for w in ((ident << 16) | dlc, w1, w2):
            m.host_rx_push(0, w)
            m.run(50, stop_when_idle=False)
        m.run(40_000, stop_when_idle=False)
        acks.append(m.host_tx_pop(0))
        m.run(1, stop_when_idle=False)
    assert not board.errors(), board.errors()
    assert acks == [1, 1, 1, 1]
    got = [(f["id"], f["dlc"], f["data"], f["crc_ok"]) for f in bus.frames]
    want = [(i, d, list(((w1 << 32 | w2) >> (8 * (7 - k))) & 0xFF for k in range(d)), True)
            for i, d, w1, w2 in CAN_FRAMES]
    assert got == want
    assert bus.frames[3]["raw_bits"] > 19 + 64 + 15 + 13      # stuff bits were inserted
    assert not m.ff.valid and m.ctx[0].min_lead == 3


def test_ps2_device_back_to_back_scancodes():
    """PS/2 keyboard side: the chip generates the clock and odd parity; scancodes pushed
    back to back must all arrive (the pre-fix firmware faulted ADMIT on the second)."""
    from escmodels import Ps2Host
    from esciss import Machine
    host = Ps2Host(4, 5)
    board = Board(host)
    m = Machine(assemble_file(FW / "ps2_device.asm"), board=board)
    for code in (0x1C, 0xF0):
        m.host_rx_push(1, code)
        m.run(20, stop_when_idle=False)
    m.run(50_000, stop_when_idle=False)
    acks = [m.host_tx_pop(1)]
    m.run(1, stop_when_idle=False)
    acks.append(m.host_tx_pop(1))
    for code in (0x1C, 0x5A, 0x00, 0xFF):
        m.host_rx_push(1, code)
        m.run(26_000, stop_when_idle=False)
        acks.append(m.host_tx_pop(1))                  # drain the ack, as a host would
        m.run(1, stop_when_idle=False)
    assert acks == [1] * 6
    assert host.bytes == [0x1C, 0xF0, 0x1C, 0x5A, 0x00, 0xFF], host.bytes
    assert not board.errors(), board.errors()
    assert not m.ff.valid


def test_jtag_idcode_back_to_back():
    """JTAG controller: TAP reset, Shift-DR, IDCODE read, back to Run-Test/Idle; two
    requests back to back (the pre-fix firmware re-anchored under queued events)."""
    from escmodels import JtagTap
    from esciss import Machine
    tap = JtagTap(4, 5, 6, 7, idcode=0x4BA00477)
    board = Board(tap)
    m = Machine(assemble_file(FW / "jtag_idcode.asm"), board=board)
    m.host_rx_push(2, 0)
    m.run(10, stop_when_idle=False)
    m.host_rx_push(2, 0)
    got = []
    for _ in range(2):
        m.run(6000, stop_when_idle=False)
        got.append(m.host_tx_pop(2))
        m.run(1, stop_when_idle=False)
    assert got == [0x4BA00477, 0x4BA00477] and tap.state == "RTI"
    assert not board.errors() and not m.ff.valid
