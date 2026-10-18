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


TWO_BANKS_IDCODE = 0x4BA00477


def two_banks_world(mode=3):
    """combo_two_banks.asm's surroundings: PS/2 host and JTAG TAP on bank A, SPI target
    and UART receiver on bank B, and one host script for all four mailboxes."""
    from escmodels import JtagTap, Ps2Host, SplitBoard
    ps2 = Ps2Host(0, 1)
    tap = JtagTap(4, 5, 6, 7, idcode=TWO_BANKS_IDCODE)
    script = HostScript([(20, 2, mode), (100, 2, 0x1A5), (110, 2, 0x05A), (30, 0, 0x1C),
                         (40, 1, 0), (2000, 2, 0x0F0), (9000, 0, 0x5A), (9100, 1, 0)],
                        drain=[0, 1, 2], poll=300)
    spi = SpiTarget(sck=0, mosi=1, miso=2, cs=3, mode=mode, reply=b"\x3C\xC3\x81")
    uart = UartRx(4, 25e6 / 115200)
    board_a = Board(ps2, tap, script)
    board_b = SplitBoard(spi, uart, outputs=0b11011)          # MISO (B2) is the input
    return board_a, board_b, script, ps2, spi, uart


@pytest.mark.parametrize("mode", [0, 3])
def test_two_banks_four_protocols_at_once(mode):
    """v1.2: PS/2 and JTAG on bank A while SPI and UART run on bank B, eleven signals,
    each checked by its own independent model; nobody's timing moves (fixed slots)."""
    from esciss import Machine
    board_a, board_b, script, ps2, spi, uart = two_banks_world(mode)
    m = Machine(assemble_file(FW / "combo_two_banks.asm"), board=board_a, board_b=board_b)
    script.machine = m
    m.run(60_000, stop_when_idle=False)
    assert ps2.bytes == [0x1C, 0x5A], ps2.bytes
    assert script.received[0] == [1, 1]
    assert script.received[1] == [TWO_BANKS_IDCODE] * 2
    assert spi.frames == [[0xA5, 0x5A], [0xF0]], spi.frames
    assert script.received[2] == [0x3C, 0xC3, 0x81]
    assert uart.bytes == [0x55, 0x4B, 0x21, 0x0A]
    assert not board_a.errors() and not board_b.errors(), board_a.errors() + board_b.errors()
    assert not m.ff.valid
    # bank B carried SPI and UART while bank A carried PS/2 and JTAG, in the same run
    assert any(p["ob"] != 0x1F for p in m.pins[:2000])
    assert any(p["uio_oe"] & 0x03 for p in m.pins[:5000])
    # MISO (B2) is an input: its output side stays released (high) throughout
    assert all(p["ob"] & 0b00100 for p in m.pins)


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


# --------------------------------------------------------------------------- SWD

SWD = 2                                    # context of swd_idcode.asm
SWD_CONNECT_DPIDR = 0x80000004             # connect, then DP read of DPIDR (A = 0x0)
SWD_DPIDR, SWD_CTRLSTAT, SWD_RDBUFF = 0x04, 0x0C, 0x1C     # DP reads: RnW, A[2:3]
SWD_ABORT, SWD_SELECT = 0x00, 0x10                          # DP writes
SWD_AP_RD_C = 0x1E                         # AP read, A = 0xC (IDR with APBANKSEL 0xF)


def swd_session(target, commands, source=None, per_cmd=60_000):
    """Run swd_idcode.asm against `target`, one command at a time: each command is a
    tuple of host words (the command, and the write data for a write). Returns the
    (status, data) replies, the board and the machine."""
    from escasm import assemble
    from esciss import Machine
    board = Board(target)
    image = assemble(source, base_dir=FW) if source else assemble_file(FW / "swd_idcode.asm")
    m = Machine(image, board=board)
    replies = []
    for words in commands:
        for w in words:
            m.host_rx_push(SWD, w)
            m.run(1, stop_when_idle=False)
        got = []
        for _ in range(per_cmd // 50):
            m.run(50, stop_when_idle=False)
            v = m.host_tx_pop(SWD)
            m.run(1, stop_when_idle=False)
            if v is not None:
                got.append(v)
            if len(got) == 2:
                break
        replies.append(tuple(got))
    return replies, board, m


def swclk_phases(board, pin=4):
    """(shortest high, shortest low) SWCLK phase in ticks, after the first edge."""
    lv = board.level(pin)
    edges = [t for t in range(1, len(lv)) if lv[t] != lv[t - 1]]
    hi = [b - a for a, b in zip(edges, edges[1:]) if lv[a] == 1]
    lo = [b - a for a, b in zip(edges, edges[1:]) if lv[a] == 0]
    return min(hi), min(lo)


@pytest.mark.parametrize("dpidr", [0x2BA01477, 0x0BB11477, 0x0BC12477, 0x6BA02477])
def test_swd_connect_and_read_dpidr(dpidr):
    """Line reset, JTAG-to-SWD (0xE79E), line reset, idle, DP read of DPIDR, on an SWJ-DP
    that powers up in JTAG; then a plain second read. Waveform: SWCLK phases >= H."""
    from escmodels import SwdTarget
    tgt = SwdTarget(4, 5, dpidr=dpidr)
    replies, board, m = swd_session(tgt, [(SWD_CONNECT_DPIDR,), (SWD_DPIDR,)])
    assert replies == [(1, dpidr), (1, dpidr)], [tuple(map(hex, r)) for r in replies]
    assert tgt.switches == 1 and tgt.line_resets == 1 and tgt.mode == "swd"
    assert [(r["apndp"], r["rnw"], r["a"], r["ack"]) for r in tgt.log] == [(0, 1, 0, 1)] * 2
    assert not board.errors(), board.errors()
    assert swclk_phases(board) >= (16, 16)
    assert not m.ff.valid and m.ctx[SWD].min_lead >= 3            # escert: >= 3


def test_swd_ap_read_through_select_with_waits():
    """Writes (ABORT, SELECT), a WAITed access retried until OK, a posted AP read and
    RDBUFF: reads the AP's IDR."""
    from escmodels import SwdTarget
    tgt = SwdTarget(4, 5, waits=3, ap_regs={(0, 0xFC): 0x24770011})
    replies, board, m = swd_session(tgt, [
        (SWD_CONNECT_DPIDR,), (SWD_ABORT, 0x1E), (SWD_ABORT, 0x10),
        (SWD_SELECT, 0x000000F0), (SWD_AP_RD_C,), (SWD_RDBUFF,)])
    assert replies == [(1, 0x2BA01477), (1, 0), (1, 0), (0x301, 0), (1, 0), (1, 0x24770011)], \
        [tuple(map(hex, r)) for r in replies]
    assert tgt.writes == [(0, 0x0, 0x1E), (0, 0x0, 0x10), (0, 0x8, 0xF0)]
    assert [r["ack"] for r in tgt.log] == [1, 1, 1, 2, 2, 2, 1, 1, 1]
    assert not any(r.get("wdata_parity_error") for r in tgt.log)
    assert not board.errors(), board.errors()
    assert not m.ff.valid


def test_swd_wait_gives_up_after_wait_max():
    from escmodels import SwdTarget
    tgt = SwdTarget(4, 5, waits=1000)
    replies, board, m = swd_session(tgt, [(SWD_CONNECT_DPIDR,), (SWD_RDBUFF,)], per_cmd=120_000)
    assert replies == [(1, 0x2BA01477), ((64 << 8) | 2, 0)]
    assert [r["ack"] for r in tgt.log].count(2) == 64
    assert not board.errors(), board.errors()


def test_swd_fault_reported_and_cleared_by_abort():
    """A sticky STICKYERR makes the target FAULT (DPIDR and CTRL/STAT still answer OK);
    an ABORT write with STKERRCLR clears it."""
    from escmodels import SwdTarget
    tgt = SwdTarget(4, 5, sticky=1 << 5)
    replies, board, m = swd_session(tgt, [
        (SWD_CONNECT_DPIDR,), (SWD_RDBUFF,), (SWD_CTRLSTAT,), (SWD_ABORT, 1 << 2),
        (SWD_RDBUFF,), (SWD_CTRLSTAT,)])
    assert replies[:2] == [(1, 0x2BA01477), (4, 0)]
    assert replies[2][0] == 1 and replies[2][1] & (1 << 5)
    assert replies[3:5] == [(1, 0), (1, 0)]
    assert replies[5][0] == 1 and not replies[5][1] & (1 << 5)
    assert not board.errors(), board.errors()


def test_swd_read_parity_error_reported():
    from escmodels import SwdTarget
    tgt = SwdTarget(4, 5, bad_parity=1)
    replies, board, m = swd_session(tgt, [(SWD_CONNECT_DPIDR,), (SWD_DPIDR,)])
    assert replies == [(1 | 8, 0x2BA01477), (1, 0x2BA01477)]
    assert not board.errors(), board.errors()


def test_swd_no_response_backs_off_then_connects():
    """Without the switch sequence an SWJ-DP stays in JTAG and never answers: the line
    reads 0b111 (pull-up), the host stays off the line for a data phase, line resets,
    and reports; a connect then works."""
    from escmodels import SwdTarget
    tgt = SwdTarget(4, 5)
    replies, board, m = swd_session(tgt, [(SWD_DPIDR,), (SWD_CONNECT_DPIDR,)])
    assert replies == [(0x10 | 7, 0), (1, 0x2BA01477)]
    assert not board.errors(), board.errors()


def test_swd_back_to_back_commands():
    from escmodels import SwdTarget
    from esciss import Machine
    tgt = SwdTarget(4, 5)
    board = Board(tgt)
    m = Machine(assemble_file(FW / "swd_idcode.asm"), board=board)
    m.host_rx_push(SWD, SWD_CONNECT_DPIDR)
    m.run(1, stop_when_idle=False)
    m.host_rx_push(SWD, SWD_DPIDR)
    got = []
    for _ in range(400):
        m.run(50, stop_when_idle=False)
        v = m.host_tx_pop(SWD)
        m.run(1, stop_when_idle=False)
        if v is not None:
            got.append(v)
    assert got == [1, 0x2BA01477] * 2
    assert not board.errors() and not m.ff.valid


def test_swd_fastest_clock_h14():
    """H = 14 (SWCLK 893 kHz), the fastest certified setting."""
    from escasm import assemble
    from escert import certify
    from escmodels import SwdTarget
    src = (FW / "swd_idcode.asm").read_text().replace(".default H, 16", ".default H, 14")
    assert certify(assemble(src, base_dir=FW))[0].status == "CHECKED"
    tgt = SwdTarget(4, 5, dpidr=0x0BC12477)
    replies, board, m = swd_session(tgt, [(SWD_CONNECT_DPIDR,)], source=src)
    assert replies == [(1, 0x0BC12477)] and not board.errors()
    assert swclk_phases(board) >= (14, 14)


def test_swd_target_model_flags_contention():
    """Negative check of the model: a host that drives SWDIO high through the turnaround
    instead of releasing it fights the target's ACK."""
    from escmodels import SwdTarget
    src = (FW / "swd_idcode.asm").read_text()
    assert src.count("EMIT  DIO, 0, 0 ") == 1
    src = src.replace("EMIT  DIO, 0, 0 ", "EMIT  DIO, DIO, DIO ")
    tgt = SwdTarget(4, 5)
    replies, board, m = swd_session(tgt, [(SWD_CONNECT_DPIDR,)], source=src)
    assert any("contention" in e for e in board.errors()), board.errors()
# --------------------------------------------------------------------------- NEC infrared


def nec_image(clk_khz=None):
    """nec_tx.asm, optionally at another core clock (all its timings derive from it)."""
    from escasm import assemble
    if clk_khz is None:
        return assemble_file(FW / "nec_tx.asm")
    return assemble(f".equ nec_tx.CLK_KHZ, {clk_khz}\n" + '.include "nec_tx.asm"\n', base_dir=FW)


def nec_run(image, words, ticks, board, chunk=10_000, at=None):
    """Feed host words to context 0 as its RX mailbox has room (or at the ticks in `at`),
    collect its acks. Observation records are dropped per chunk: a 108 ms slot is
    2.7 million ticks."""
    from esciss import Machine
    m = Machine(image, board=board)
    queue, acks, pushed = list(words), [], []
    while m.now < ticks:
        while queue and len(m.ctx[0].rx) < 2 and (at is None or at[len(pushed)] <= m.now):
            pushed.append(m.now)
            m.host_rx_push(0, queue.pop(0))
            m.run(1, stop_when_idle=False)
        m.run(chunk, stop_when_idle=False)
        v = m.host_tx_pop(0)
        if v is not None:
            acks.append(v)
        m.run(1, stop_when_idle=False)
        m.pins.clear()
        m.commits.clear()
        board.history.clear()
    return m, acks, pushed


def test_nec_tx_full_length_frame_and_repeat():
    """The real timebase (25 MHz): carrier frequency and duty, every burst position to
    the tick, and a repeat code exactly one 108 ms slot after the frame."""
    from escmodels import NecReceiver
    rx = NecReceiver(0, TICK_NS)
    board = Board(rx)
    m, acks, _ = nec_run(nec_image(), [0xC35A, 1 << 31], 3_050_000, board, chunk=100_000)
    assert rx.frames() == [(0x5A, 0xC3), "R"], rx.events
    assert not board.errors(), board.errors()
    assert set(rx.carrier_periods) == {658}                    # 37.99 kHz
    assert abs(25e6 / 658 - 38_000) / 38_000 < 0.001
    assert {round(d, 4) for d in rx.duties} == {round(219 / 658, 4)}   # 1/3 duty
    assert rx.pulses == [342] + [22] * 33 + [342, 22]
    starts = [s for s, _ in rx.marks]
    assert starts[1] - starts[0] == 337_500                     # 9 ms + 4.5 ms, exact
    raw = rx.events[0]["raw"]
    for i in range(32):                                         # 1.125 / 2.25 ms, exact
        assert starts[i + 2] - starts[i + 1] == (56_250 if raw >> i & 1 else 28_125)
    assert 0 <= starts[34] - starts[0] - 2_700_000 <= 3          # one 108 ms slot
    assert starts[35] - starts[34] == 281_250                   # 9 ms + 2.25 ms
    assert acks == [1, 1]
    assert not m.ff.valid and m.ctx[0].min_lead >= 9


NEC_WORDS = [0x0000, 0xFFFF, 1 << 31, 1 << 31, 0x8001, 1 << 30,
             0x1234 | (0x5A << 16) | (0xA5 << 24), 0x5AA5]
NEC_EXPECT = [(0x00, 0x00), (0xFF, 0xFF), "R", "R", (0x01, 0x80), (0x1234, 0x5A), (0xA5, 0x5A)]


def test_nec_tx_values_back_to_back():
    """All-zeros and all-ones address and command, repeat codes, an extended (16-bit
    address) raw frame, queued back to back: each starts exactly one slot after the
    previous one. Run at a 2.5 MHz timebase (the model's tick is 400 ns) to keep the
    seven 108 ms slots fast; the firmware is the same, only its constants differ."""
    from escert import certify
    from escmodels import NecReceiver
    image = nec_image(2500)
    rx = NecReceiver(0, tick_ns=400.0)
    board = Board(rx)
    m, acks, _ = nec_run(image, NEC_WORDS, 6 * 270_000 + 175_000, board)
    assert rx.frames() == NEC_EXPECT, rx.events
    assert [e["ext"] for e in rx.events if e["kind"] == "frame"] == [False] * 3 + [True, False]
    assert not board.errors(), board.errors()
    leaders = [e["t"] for e in rx.events]
    assert all(0 <= b - a - 270_000 <= 3 for a, b in zip(leaders, leaders[1:])), leaders
    assert acks == [1] * 7 and not m.ff.valid
    c = certify(image)[0]
    assert c.status == "CHECKED" and m.ctx[0].min_lead >= c.min_lead, str(c)


def test_nec_tx_late_host_starts_at_once():
    """A command that arrives after its slot has passed is sent at once, not aligned to
    a slot grid; the first one too."""
    from escmodels import NecReceiver
    rx = NecReceiver(0, tick_ns=400.0)
    board = Board(rx)
    m, acks, pushed = nec_run(nec_image(2500), [0x2010, 0x4030], 900_000, board,
                              at=[1_000, 600_000])
    assert rx.frames() == [(0x10, 0x20), (0x30, 0x40)] and not board.errors()
    assert all(0 < e["t"] - p < 500 for e, p in zip(rx.events, pushed)), (rx.events, pushed)


def nec_rx_run(items, ticks, clk_khz=2500, scale=1.0, bias_us=0.0):
    """nec_rx.asm against a remote-control model driving a TSOP-style (active low) line."""
    from escasm import assemble
    from escmodels import NecSource
    from esciss import Machine
    tick_ns = 1e6 / clk_khz
    src = NecSource(1, items, tick_ns=tick_ns, scale=scale, mark_bias_us=bias_us)
    board = Board(src)
    image = assemble(f".equ nec_rx.CLK_KHZ, {clk_khz}\n" + '.include "nec_rx.asm"\n', base_dir=FW)
    m = Machine(image, board=board)
    got = []
    while m.now < ticks:
        m.run(20_000, stop_when_idle=False)
        v = m.host_tx_pop(1)
        if v is not None:
            got.append(v)
        m.run(1, stop_when_idle=False)
        m.pins.clear()
        m.commits.clear()
        board.history.clear()
    assert not m.ff.valid and not board.errors()
    return got


def nec_report(addr, cmd, ext=False):
    return (cmd << 16) | addr | (1 << 24 if ext else 0)


def test_nec_rx_full_length():
    """The receiver at 25 MHz: a frame, then a repeat code one 108 ms slot later."""
    from escmodels import nec_raw
    got = nec_rx_run([(1000, nec_raw(0x5A, 0xC3)), (1000 + 2_700_000, None)], 3_050_000,
                     clk_khz=25000)
    assert got == [nec_report(0x5A, 0xC3), 1 << 31], [hex(v) for v in got]


NEC_RX_ITEMS = [(0x00, 0x00), (0xFF, 0xFF), None, (0x1234, 0x5A), (0xA5, 0x5A)]


@pytest.mark.parametrize("scale,bias_us", [(1.0, 0.0), (0.9, 0.0), (1.1, 0.0),
                                           (1.0, 150.0), (1.0, -150.0)])
def test_nec_rx_tolerances(scale, bias_us):
    """All-zeros and all-ones values, a repeat code and an extended address, from a
    remote whose clock is 10% fast or slow, or through a receiver module that
    lengthens or shortens every burst by 150 us (2.5 MHz timebase)."""
    from escmodels import nec_raw
    slot = 200_000                                       # 80 ms at 2.5 MHz
    items, want = [], []
    for i, v in enumerate(NEC_RX_ITEMS):
        if v is None:
            items.append((1000 + i * slot, None))
            want.append(1 << 31)
        elif v[0] > 0xFF:                                # extended: 16-bit address
            items.append((1000 + i * slot, v[0] | v[1] << 16 | (~v[1] & 0xFF) << 24))
            want.append(nec_report(*v, ext=True))
        else:
            items.append((1000 + i * slot, nec_raw(*v)))
            want.append(nec_report(*v))
    got = nec_rx_run(items, 1000 + len(items) * slot, scale=scale, bias_us=bias_us)
    assert got == want, [hex(v) for v in got]


def test_nec_rx_drops_bad_frames_and_recovers():
    """A command without its inverse, and a frame 20% too slow (leader 10.8 ms), are
    dropped; the frames after each are received."""
    from escasm import assemble
    from escmodels import NecSource, nec_raw
    from esciss import Machine
    bad_inv = nec_raw(0x10, 0x20) ^ (1 << 30)
    fast = NecSource(1, [(1000, bad_inv), (200_000, nec_raw(0x11, 0x22)),
                         (650_000, nec_raw(0x33, 0x44))], tick_ns=400.0)
    slow = NecSource(1, [(400_000, nec_raw(0x55, 0x66))], tick_ns=400.0, scale=1.2)
    board = Board(fast, slow)
    image = assemble('.equ nec_rx.CLK_KHZ, 2500\n.include "nec_rx.asm"\n', base_dir=FW)
    m = Machine(image, board=board)
    got = []
    while m.now < 850_000:
        m.run(20_000, stop_when_idle=False)
        v = m.host_tx_pop(1)
        if v is not None:
            got.append(v)
        m.run(1, stop_when_idle=False)
        m.pins.clear()
        m.commits.clear()
    assert got == [nec_report(0x11, 0x22), nec_report(0x33, 0x44)], [hex(v) for v in got]
    assert not m.ff.valid


def test_nec_loopback_tx_carrier_through_tsop_into_rx():
    """Both programs in one image: context 0 sends with the carrier on pin 0, a
    receiver-module model demodulates it onto pin 1, context 1 decodes it."""
    from escasm import assemble
    from escmodels import NecReceiver, Tsop
    from esciss import Machine
    image = assemble(".equ nec_tx.CLK_KHZ, 2500\n.equ nec_rx.CLK_KHZ, 2500\n"
                     '.include "nec_tx.asm"\n.include "nec_rx.asm"\n', base_dir=FW)
    rx = NecReceiver(0, tick_ns=400.0)
    board = Board(rx, Tsop(0, 1, tick_ns=400.0))
    words = [0x0000, 0xFFFF, 1 << 31, 1 << 30, 0x1234 | (0x5A << 16) | (0xA5 << 24), 0x5AA5]
    m = Machine(image, board=board)
    queue, got = list(words), []
    while m.now < 4 * 270_000 + 175_000:
        while queue and len(m.ctx[0].rx) < 2:
            m.host_rx_push(0, queue.pop(0))
            m.run(1, stop_when_idle=False)
        m.run(10_000, stop_when_idle=False)
        m.host_tx_pop(0)                                 # TX acks
        v = m.host_tx_pop(1)                             # decoded by the receiver
        if v is not None:
            got.append(v)
        m.run(1, stop_when_idle=False)
        m.pins.clear()
        m.commits.clear()
        board.history.clear()
    assert rx.frames() == [(0, 0), (0xFF, 0xFF), "R", (0x1234, 0x5A), (0xA5, 0x5A)]
    want = [nec_report(0, 0), nec_report(0xFF, 0xFF), 1 << 31,
            nec_report(0x1234, 0x5A, ext=True), nec_report(0xA5, 0x5A)]
    assert got == want, [hex(v) for v in got]
    assert not board.errors() and not m.ff.valid
# --------------------------------------------------------------------------- SPI target

SPIT_MEM = [(a * 7 + 3) & 0xFF for a in range(256)]
SPIT_RELEASE = 100               # MISO must be released this soon after CS_n rises (4 us)
# At the rated limits: SCK period 60 ticks (416 kHz), CS_n high 4 us, CS_n setup 1 us.
SPIT_FAST = dict(t_high=30, t_low=30, t_css=25, t_idle=100)
SPIT_FRAMES = [
    ([0x02, 0x10, 0xDE, 0xAD, 0xBE, 0xEF], None),                     # WRITE
    ([0x03, 0x10, 0, 0, 0, 0], [None, None, 0xDE, 0xAD, 0xBE, 0xEF]),  # READ it back
    ([0x03, 0xFE, 0, 0, 0], [None, None, SPIT_MEM[0xFE], SPIT_MEM[0xFF], SPIT_MEM[0]]),  # wrap
    ([0x9F, 0x00, 0x00], [0xFF, 0xFF, 0xFF]),                          # unknown: MISO released
    ([0x02, 0x20, 0x11, 0x22], None, 28),                              # WRITE cut mid-byte
    ([0x03, 0x20, 0, 0], [None, None, 0x11, SPIT_MEM[0x21]]),          # ... 0x22 was dropped
    ([0x03, 0x11, 0, 0], [None, None, 0xAD, 0xBE], 20),                # READ cut mid-byte
    ([0x03, 0x12], None),                                              # READ, no data bytes
    ([0x03, 0x13, 0], [None, None, 0xEF]),
]


def spit_run(frames, mode, **timing):
    """spi_target.asm (memory preloaded) against the SPI controller model, to completion."""
    from escmodels.spi import SpiController
    from esciss import Machine
    image = assemble_file(FW / "spi_target.asm")
    image.tape[:] = SPIT_MEM
    ctl = SpiController(4, 5, 6, 7, frames, mode=mode, **timing)
    board = Board(ctl)
    m = Machine(image, board=board)
    while not ctl.done and len(m.pins) < 500_000:
        m.run(2000, stop_when_idle=False)
    m.run(200, stop_when_idle=False)
    return m, ctl, board


def spit_miso_errors(m, ctl):
    """MISO (pin 6) may be driven only inside a READ frame, after its address byte, and
    must be released within SPIT_RELEASE ticks of CS_n rising."""
    oe = [(p["uio_oe"] >> 6) & 1 for p in m.pins]
    allowed = [False] * len(oe)
    for (data, *_), fall, rise in zip(ctl.frames, ctl.cs_fall, ctl.cs_rise):
        if data[0] == 0x03:
            for t in range(fall, min(rise + SPIT_RELEASE, len(oe))):
                allowed[t] = True
    bad = [t for t in range(len(oe)) if oe[t] and not allowed[t]]
    return [f"MISO driven at t={bad[0]} ({len(bad)} ticks)"] if bad else []


@pytest.mark.parametrize("mode", [0, 3])
def test_spi_target_memory(mode):
    """25xx-style memory in firmware at the rated SCK limit: WRITE, READ back, address
    wrap, an ignored command, frames cut mid-byte, all back to back with the minimum CS_n
    high time. The controller model checks MISO setup/hold at every sampling edge."""
    m, ctl, board = spit_run(SPIT_FRAMES, mode, **SPIT_FAST)
    assert not board.errors(), board.errors()
    assert not spit_miso_errors(m, ctl), spit_miso_errors(m, ctl)
    assert m.tape[0x10:0x14] == [0xDE, 0xAD, 0xBE, 0xEF]
    assert m.tape[0x20:0x22] == [0x11, SPIT_MEM[0x21]]
    assert ctl.rx[1][2:] == [0xDE, 0xAD, 0xBE, 0xEF]
    assert not m.ff.valid and m.ctx[2].min_lead == 1      # every edge admitted, lead 1


def test_spi_target_certificate_matches_telemetry():
    """escert proves lead >= 1 for every input; the simulated chip's telemetry agrees."""
    from escert import certify
    certs = certify(assemble_file(FW / "spi_target.asm"))
    assert [(c.ctx, c.status, c.min_lead) for c in certs] == [(2, "CHECKED", 1)]
    m, _, _ = spit_run(SPIT_FRAMES, 3, **SPIT_FAST)
    assert m.ctx[2].min_lead >= certs[0].min_lead


@pytest.mark.parametrize("mode", [0, 3])
@pytest.mark.parametrize("high,low", [(30, 30), (12, 48), (36, 24)])
def test_spi_target_rated_period_every_phase(mode, high, low):
    """SCK period 60 ticks with the shortest high (12) and low (24) phases, at every
    alignment of the controller's edges with the 4-tick slots and the 12-tick poll."""
    for phase in range(12):
        m, ctl, board = spit_run(SPIT_FRAMES[:4], mode, t_high=high, t_low=low, t_css=25,
                                 t_idle=100, start_at=100 + phase)
        assert not board.errors(), (phase, board.errors())
        assert not m.ff.valid


@pytest.mark.parametrize("high,low", [(29, 29), (11, 49), (37, 23)])
def test_spi_target_limits_are_tight(high, low):
    """Just below the limits (period 58, high 11, low 23) some alignment fails: the
    rated numbers are the machine's real bound, not a margin."""
    fails = 0
    for mode in (0, 3):
        for phase in range(12):
            _, _, board = spit_run(SPIT_FRAMES[:2], mode, t_high=high, t_low=low, t_css=25,
                                   t_idle=100, start_at=100 + phase)
            fails += bool(board.errors())
    assert fails > 0


@pytest.mark.parametrize("mode", [0, 3])
def test_spi_target_cs_rises_at_every_bit(mode):
    """CS_n rises after every possible bit count of a READ and a WRITE frame: complete
    WRITE bytes are stored, a cut byte is not, and the next frame is answered correctly."""
    for cmd in (0x02, 0x03):
        for cut in range(1, 33):
            frames = [([cmd, 0x40, 0x5A, 0xA5], None, cut),
                      ([0x03, 0x40, 0, 0], [None, None, None, None])]
            m, ctl, board = spit_run(frames, mode, **SPIT_FAST)
            stored = [0x5A, 0xA5] if cmd == 0x02 else []
            want = [stored[i] if cut >= 24 + 8 * i and i < len(stored) else SPIT_MEM[0x40 + i]
                    for i in range(2)]
            assert m.tape[0x40:0x42] == want, (cmd, cut, m.tape[0x40:0x42])
            assert ctl.rx[1][2:] == want, (cmd, cut, ctl.rx[1])
            assert not board.errors() and not spit_miso_errors(m, ctl), (cmd, cut, board.errors())


def test_spi_target_irregular_clock():
    """An irregular controller: random phase lengths (high >= 12, low >= 48, so every
    period is >= 60 in either mode), long pauses mid-byte and gaps between bytes. SPI is
    static, so nothing may time out."""
    import random
    rng = random.Random(7)
    hi = [rng.choice([12, 20, 33, 90, 400]) for _ in range(97)]
    lo = [rng.choice([48, 75, 200, 1500]) for _ in range(89)]
    frames = [([0x02, 0x80] + list(range(1, 9)), None),
              ([0x03, 0x80, 0, 0, 0, 0, 0, 0, 0, 0], [None, None] + list(range(1, 9)))]
    for mode in (0, 3):
        m, ctl, board = spit_run(frames, mode, t_high=hi, t_low=lo, byte_gap=777, t_css=25,
                                 t_idle=100)
        assert not board.errors() and not spit_miso_errors(m, ctl), board.errors()
        assert m.tape[0x80:0x88] == list(range(1, 9)) and not m.ff.valid


# ---------------------------------------------------------------- Westminster Quarters
WM_BELLS = {"GS4": 415.30, "FS4": 369.99, "E4": 329.63, "B3": 246.94, "E3": 164.81}
WM_PERIODS = {"GS4": 208, "FS4": 185, "E4": 165, "B3": 123, "E3": 412}
WM_TUNE = ("E4 GS4 FS4 B3 | E4 FS4 GS4 E4 | GS4 E4 FS4 B3 | B3 FS4 GS4 E4 | E3").split()


def westminster_notes(clk_khz):
    """Play westminster.asm on the ISS at clk_khz; return its notes as (start tick,
    periods, half periods, end tick) from the B0 edges, and the machine."""
    from escasm import assemble
    from esciss import Machine

    class Quiet(Machine):                     # millions of ticks: keep no traces
        def _record_commit(self, cm, t):
            pass

    class Edges(list):
        def append(self, rec):
            pass

    edges = []

    def board_b(t, ob):
        b0 = ob & 1
        if not edges or edges[-1][1] != b0:
            edges.append((t, b0))
        return 0

    m = Quiet(assemble(f".equ westminster.CLK_KHZ, {clk_khz}\n"
                       '.include "westminster.asm"\n', base_dir=FW), board_b=board_b)
    m.pins = Edges()
    m.run(16 * clk_khz * 1000, stop_when_idle=True)
    falls = [t for t, v in edges if v == 0]
    rises = [t for t, v in edges if v == 1]
    notes, cur = [], None
    gap = 10 * clk_khz * 1000 // 400          # far longer than any half period
    for f, r in zip(falls, rises[1:]):        # rises[0] is B0 driven high at start
        if cur is None or f - cur[3] > gap:
            if cur is not None:
                notes.append(cur)
            cur = [f, 0, set(), r]
        else:
            cur[2].add(f - cur[3])            # high half period
        cur[1] += 1
        cur[2].add(r - f)                     # low half period
        cur[3] = r
    notes.append(cur)
    return notes, m


def test_westminster_chimes_the_hour():
    """The hour chime of Big Ben, at a hundredth of real time (250 kHz): 17 notes in the
    order of changes 2, 3, 4 and 5 and the hour bell, each at its exact pitch and
    length, 0.15 s apart, with 0.8 s between changes; then the program halts."""
    clk = 250
    notes, m = westminster_notes(clk)
    tune = [n for n in WM_TUNE if n != "|"]
    assert len(notes) == len(tune)
    half = []
    for (start, periods, halves, end), bell in zip(notes, tune):
        assert periods == WM_PERIODS[bell]
        assert len(halves) == 1, (bell, halves)   # a constant half period
        half.append(halves.pop())
        f = clk * 1000 / (2 * half[-1])
        assert abs(f - WM_BELLS[bell]) / WM_BELLS[bell] < 0.002, (bell, f)
    # silence: the last (high) half period of a note, then GAP, and REST after a change
    for i in range(1, len(notes)):
        want = half[i - 1] + clk * 150 + (clk * 650 if i % 4 == 0 else 0)
        assert notes[i][0] - notes[i - 1][3] == want, i
    assert m.ctx[0].state == 2 and not m.ff.valid       # HALTED, no fault


def test_seven_seg_animation():
    """The demo board display animation on bank B: f, g, e, d, dp, each lit alone for
    exactly one frame, repeating, with every other bank B segment dark. At 25 kHz a
    frame is 4000 ticks."""
    from escasm import assemble
    from esciss import Machine
    src = '.equ seven_seg.CLK_KHZ, 25\n.include "seven_seg.asm"\n'
    m = Machine(assemble(src, base_dir=FW))
    m.run(60_000, stop_when_idle=False)
    changes, prev = [], None
    for p in m.pins:
        if p["ob"] != prev:
            changes.append((p["t"], p["ob"]))
            prev = p["ob"]
    assert changes[0] == (0, 0x1F)                     # released: all high until the first frame
    frames = changes[1:]
    want = [0x04, 0x08, 0x02, 0x01, 0x10]              # f, g, e, d, dp
    assert [ob for _, ob in frames] == (want * 3)[:len(frames)] and len(frames) >= 14
    assert all(b[0] - a[0] == 4000 for a, b in zip(frames, frames[1:]))
    assert not m.ff.valid
