"""Firmware waveforms judged by sigrok's protocol decoders (sigrok_judge.py): the chip's
pins from a simulator run, as a VCD, decoded by code that other people wrote from the
protocol specifications. Skipped where sigrok-cli is not installed."""

from __future__ import annotations

import re

import pytest

from sigrok_judge import SIGROK, decode, envelope, vcd

pytestmark = pytest.mark.skipif(SIGROK is None, reason="sigrok-cli not installed")


def test_usbpd_source_capabilities():
    from test_usbpd import SEVEN, SRC_CAPS, run
    m, sink, host, board = run(25000, [SRC_CAPS, SEVEN])
    ann = decode(vcd({"cc": board.level(0)}, 40, tail_ticks=75_000), "usb_power_delivery:cc1=cc")
    assert not [a for a in ann if "Bad" in a or "CRC!" in a], ann
    heads = [a for a in ann if a.startswith("H:")]
    assert heads == [f"H:{SRC_CAPS[0]:04x}", f"H:{SEVEN[0]:04x}"], heads
    assert "[1] [Fixed] 5V 3A (15W)" in ann
    objs = [a for a in ann if re.fullmatch(r"\[\d\][0-9a-f]{8}", a)]
    assert objs == [f"[0]{SRC_CAPS[1][0]:08x}"] + [f"[{k}]{d:08x}" for k, d in enumerate(SEVEN[1])]
    crcs = [a for a in ann if a.startswith("CRC:")]
    assert crcs == [f"CRC:{x['crc']:08x}" for x in sink.messages]


def test_lin_responder_frame():
    import test_lin_resp as L
    from escmodels import lin_pid
    m, rx, host, cmd, board = L.session(4000, L.publish(0, 0x10, [0x12, 0x34]),
                                        [lin_pid(0x10), lin_pid(0x11)])
    ann = decode(vcd({"lin": board.level(0)}, 250, tail_ticks=40_000),
                 "uart:rx=lin:baudrate=19200,lin")
    lin = [a for a in ann if a.startswith(("ID:", "Data:", "Checksum:", "Sync", "Break"))]
    assert "ID: 10 Parity: 1 (ok)" in lin and "ID: 11 Parity: 0 (ok)" in lin, lin
    i = lin.index("ID: 10 Parity: 1 (ok)")
    assert lin[i + 1:i + 4] == ["Data: 0x12", "Data: 0x34", "Checksum: 0x69"], lin
    # the UART layer reports each break (13 dominant bits) as a frame error, and nothing else
    # (both the uart and the lin decoder report each "Break condition")
    assert ann.count("Frame error") == 2 and ann.count("Break condition") == 4, ann
    assert not [a for a in ann if "error" in a.lower() and a != "Frame error"], ann


def test_rc6_frame():
    import escgen
    from esciss import Machine
    from escmodels import Board
    from test_escgen import table
    from test_ir import rc6_word
    _, img = escgen.build(table("rc6_tx"), CLK_KHZ=10000)
    board = Board(pullups=0)
    m = Machine(img, board=board)
    m.host_rx_push(0, rc6_word(1, 0xA5, 0x0C))
    m.run(10000 * 30, stop_when_idle=False)
    # an IR receiver module's output: low while the 36 kHz carrier is on (period 278 ticks)
    ann = decode(vcd({"ir": envelope(board.level(0), 3 * 278)}, 100), "ir_rc6:ir=ir")
    assert "Address: A5" in ann and "Data: 0C" in ann and "Toggle: 1" in ann, ann


def test_em4100_tag():
    """The modulator signal is low while the modulator is ON: sigrok's active-low. The first
    repetition follows the idle (OFF) line, which reads as one more 1 before the header, so
    sigrok syncs from the second: every frame from there decodes with all parities OK."""
    from test_em4100 import run
    m, rd, board = run(4000, [0x0123456789], frames_each=3)
    ann = decode(vcd({"data": board.level(0)}, 250), "em4100:data=data:polarity=active-low")
    assert ann.count("Tag: 0123456789") >= 2, ann
    tags = [i for i, a in enumerate(ann) if a == "Tag: 0123456789"]
    for t in tags:                                   # each whole frame before a tag: all OK
        h = max(i for i, a in enumerate(ann[:t]) if a == "Header")
        assert not [a for a in ann[h:t] if "ERROR" in a], ann[h:t]


def test_dcf77_minutes():
    """Three minutes at 10 kHz (a 100 us tick): sigrok's DCF77 decoder syncs on the first
    minute marker (the minute before it has none, so its bits are "unknown"), then reads both
    following minutes, every parity the chip computed right. (Its data line is high while
    the carrier is reduced, the receiver modules' convention: the chip's pin inverted.)"""
    from escmodels.dcf77 import encode
    from test_dcf77 import T1, T2, run
    m, rx, board = run([encode(**T2), encode(**T1), encode(**T2)])
    data = [1 - v for v in board.level(0)]
    ann = decode(vcd({"data": data}, 100_000), "dcf77:data=data")
    assert not [a for a in ann if "INVALID" in a], [a for a in ann if "INVALID" in a]
    for want in ("Minutes: 59", "Hours: 23", "Day: 31", "Month: 12 (December)", "Year: 99",
                 "Minutes: 0", "Hours: 0", "Day: 1", "Month: 1 (January)", "Year: 0"):
        assert want in ann, (want, [a for a in ann if ":" in a][:40])
    assert ann.count("Minute parity: OK") >= 2 and ann.count("Hour parity: OK") >= 2
    assert ann.count("Date parity: OK") >= 2


def test_sd_card_session():
    """sigrok's sdcard_spi decoder (over its spi decoder) reads the start-up and a block
    read: every command named, its CRC7 the chip computed (CMD0's byte 0x95, CMD8's frame
    48 00 00 01 aa 87, the values the specification's examples give), every R1 as the
    card sent it."""
    from test_sd_spi import run
    m, sd, host, board = run(blocks=[0x1234])
    ch = {n: board.level(p) for n, p in (("sck", 4), ("mosi", 5), ("miso", 6), ("cs", 7))}
    ann = decode(vcd(ch, 40), "spi:clk=sck:mosi=mosi:miso=miso:cs=cs,sdcard_spi")
    cmds = [a for a in ann if a.startswith("Command: ")]
    want = (["CMD0 (GO_IDLE_STATE)", "CMD8 (SEND_IF_COND)"]
            + ["CMD55 (APP_CMD)", "ACMD41 (SD_SEND_OP_COND)"] * 4
            + ["CMD58 (READ_OCR)", "CMD17 (READ_SINGLE_BLOCK)"])
    assert [c[len("Command: "):] for c in cmds] == want, cmds
    assert "CRC7: 0x4a" in ann and "CMD8: 48 00 00 01 aa 87" in ann
    assert not [a for a in ann if "CRC" in a and ("fail" in a.lower() or "error" in a.lower())
                and "No error" not in a], ann


def test_onewire_device_read_and_search_rom():
    """sigrok's 1-Wire link and network decoders follow a Read ROM and a Search ROM with the
    chip as the device: both give its ROM code (family 0x28, the serial, the CRC-8 the chip
    computed). The master leaves 490 us after reset (AN126's 480 is exactly the decoder's
    minimum, which at tick resolution it can read as the first slot's edge)."""
    from escmodels import crc8_maxim
    from test_onewire_dev import rom_bytes, run

    def prog(mm):
        yield from mm.reset()
        yield from mm.write(0x33)
        mm.results.append((yield from mm.read_n(8)))
        mm.results.append((yield from mm.search()))
    m, master, board = run(prog, ms=60, J=420)
    ann = decode(vcd({"owr": board.level(0)}, 40), "onewire_link:owr=owr,onewire_network")
    rom = int.from_bytes(rom_bytes(), "little")
    assert crc8_maxim(rom_bytes()[:7]) == rom_bytes()[7]
    assert "ROM command: 0x33 'Read ROM'" in ann and "ROM command: 0xf0 'Search ROM'" in ann
    assert ann.count(f"ROM: 0x{rom:016x}") == 2, [a for a in ann if a.startswith("ROM")]
    assert not [a for a in ann if "error" in a.lower()], [a for a in ann if "error" in a.lower()]


def test_avr_isp_session():
    """sigrok's avr_isp decoder (over spi) reads the programmer entering programming mode on
    an ATmega328P, its signature and its low fuse, with no warning about the replies."""
    from test_avr_isp import MS, run
    m, avr, host, board = run([(100, 0, 1 << 28), (60 * MS, 0, 2 << 28), (60 * MS + 10, 0, 0x50000000)], 75)
    ch = {n: board.level(p) for n, p in (("sck", 4), ("mosi", 5), ("miso", 6))}
    ch["cs"] = [0] * len(board.level(4))          # RESET is not a chip select: always selected
    ann = decode(vcd(ch, 40), "spi:clk=sck:mosi=mosi:miso=miso:cs=cs:cs_polarity=active-low,avr_isp")
    assert "Programming enable" in ann
    assert "Device: Atmel ATmega328P" in ann and "Read fuse bits: 0x62" in ann, ann
    assert not [a for a in ann if a.startswith("Warning")], [a for a in ann if a.startswith("Warning")]


def test_qi_receiver_session():
    """The Qi receiver's modulation, decoded by sigrok's qi decoder: SIG, ID, CFG, CE, RP8,
    CE, every packet's parity and checksum good. (sigrok reads the power class from bits
    7:6 shifted by 7, so only the other CFG fields are compared.)"""
    from test_qi_rx import run
    pushes = [(100, 0, 6 << 28 | 0x012304), (200, 0, 7 << 28 | 0x56789A),
              (60 * 2000, 0, 1 << 28 | 0x9A), (100 * 2000, 0, 2 << 28 | 0xFB),
              (300 * 2000, 0, 3 << 28 | 0x55)]
    qtx, host, board, m = run(pushes, 720, power=((1000, None),))
    assert not qtx.violations, qtx.violations
    ann = decode(vcd({"qi": board.level(0)}, 500), "qi:qi=qi")
    pk = [a for a in ann if a.split(":")[0] in ("Signal Strength", "Identification", "Configuration",
                                                  "Control Error", "Received Power")]
    assert pk[0] == "Signal Strength: 128", pk          # SIG goes before the host's 3rd word
    assert pk[1] == "Identification: Version = 1.2, Manufacturer = 0123, Device = 0456789a", pk
    assert pk[2].startswith("Configuration: ") and "Maximum Power = 10" in pk[2], pk
    assert "Count = 0, Window Size = 2, Window Offset = 2" in pk[2], pk
    assert pk[3:] == ["Control Error: -5", "Received Power: 85", "Control Error: -5"], pk
    assert ann.count("Checksum OK") == 6, ann
    assert not [a for a in ann if "error" in a.lower()], ann


def test_spi_flash_session():
    """The flash programmer's SPI, decoded by sigrok's spiflash decoder (as a Winbond
    W25Q80DV): the JEDEC ID, Write Enable before the erase and the program (no "WREN might
    be missing"), the sector address, the program and read data."""
    from test_spi_flash import DATA, run
    pushes = [(100, 0, 1 << 28), (2000, 0, 4 << 28 | 0x001000)]
    t = 5 * 25000
    pushes += [(t, 0, 3 << 28 | 0x001010), (t + 10, 0, 16)]
    pushes += [(t + 1000 + 5000 * k, 0, w) for k, w in enumerate(DATA)]
    t = 10 * 25000
    pushes += [(t, 0, 2 << 28 | 0x001010), (t + 10, 0, 16)]
    m, fl, host, board = run(pushes, 13)
    assert not fl.violations, fl.violations
    ch = {"sck": board.level(4), "mosi": board.level(5), "miso": board.level(6), "cs": board.level(7)}
    ann = decode(vcd(ch, 40), "spi:clk=sck:mosi=mosi:miso=miso:cs=cs:cs_polarity=active-low,"
                              "spiflash:chip=winbond_w25q80dv")
    for want in ("Manufacturer ID: 0xef", "Memory type: 0x40", "Device ID: 0x14",
                 "Erase sector 4096 (0x001000)", "Command: Page program (PP)",
                 "Command: Read data (READ)"):
        assert want in ann, (want, ann[:40])
    assert ann.count("Command: Write enable (WREN)") == 2, ann
    assert not [a for a in ann if "Warning" in a or "Unknown" in a], ann


def test_max7219_display():
    """The MAX7219 driver's frames, decoded by sigrok's max7219 decoder (LOAD as an
    active-low select): the set-up registers, then the digits of 40960, units first."""
    from test_max7219 import MS, run
    m, ch, host, board = run([(100, 0, 1 << 28 | 1 << 11 | 7 << 8 | 8), (2 * MS, 0, 3 << 28 | 40960)], 3.5)
    assert not ch.violations, ch.violations
    chs = {"clk": board.level(4), "din": board.level(5), "load": board.level(7)}
    ann = decode(vcd(chs, 40), "spi:clk=clk:mosi=din:cs=load:cs_polarity=active-low:wordsize=8,max7219")
    assert ann[:5] == ["Display test: off", "Scan limit: 8", "Decode: 0b11111111", "Intensity: 8",
                       "Digit 1: 0F"], ann[:6]
    assert ann[12] == "Shutdown: off", ann                    # (sigrok: "off" = normal operation)
    assert ann[13:] == ["Digit 1: 00", "Digit 2: 06", "Digit 3: 09", "Digit 4: 00", "Digit 5: 04",
                        "Digit 6: 0F", "Digit 7: 0F", "Digit 8: 0F"], ann[13:]


def test_stepper_speed_and_position():
    """The stepper's STEP and DIR, decoded by sigrok's stepper_motor decoder: every step's
    speed (from its sample rate) is the model's measured interval, and the position counts up
    to 199 before the 200th step."""
    from test_stepper import intervals, move, run
    m, drv, host, board = run(move(20000, 200, 200), 230, khz=10000)          # 100 ns samples
    assert not drv.violations, drv.violations
    ann = decode(vcd({"step": board.level(0), "dir": board.level(1)}, 100), "stepper_motor:step=step:dir=dir")
    speeds = [a for a in ann if a.endswith("steps/s")]
    pos = [a for a in ann if a.endswith(" steps")]
    assert speeds == ["%0.0f steps/s" % (1e6 / x) for x in intervals(drv)], speeds[:5]
    assert pos[-1] == "199 steps" and len(pos) == 199


def test_nrf24_session():
    """The nRF24L01+ driver's SPI, decoded by sigrok's nrf24l01 decoder: the set-up registers,
    the address, the payload, then the STATUS read and clear."""
    from test_nrf24_tx import run, send, setup
    m, radio, host, board = run(setup() + send(5 * 25000, b"hello"), 9)
    assert not radio.violations, radio.violations
    ch = {"sck": board.level(4), "mosi": board.level(5), "miso": board.level(6), "csn": board.level(7)}
    ann = decode(vcd(ch, 40), "spi:clk=sck:mosi=mosi:miso=miso:cs=csn:cs_polarity=active-low,nrf24l01")
    for want in ('Cmd W_REGISTER: CONFIG = "0E"', 'Cmd W_REGISTER: EN_AA = "01"',
                 'Cmd W_REGISTER: SETUP_RETR = "13"', 'Cmd W_REGISTER: RF_CH = "4C"',
                 'Cmd W_REGISTER: RF_SETUP = "06"', 'Cmd FLUSH_TX', 'TX payload = "hello"'):
        assert want in ann, (want, ann[:30])
    assert [a for a in ann if a.startswith("Cmd W_REGISTER: TX_ADDR")], ann
    assert not [a for a in ann if "unknown" in a.lower() or "warn" in a.lower()], ann
