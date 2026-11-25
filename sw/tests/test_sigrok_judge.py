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
    an ATmega328P, its signature bytes and its low fuse, with no warning about the replies.
    sigrok's part table has no ATmega328P (0x95, 0x0F): its decoder stops at that lookup, so
    the fuse read is decoded from a second capture that starts after the signature."""
    from test_avr_isp import MS, run
    m, avr, host, board = run([(100, 0, 1 << 28), (60 * MS, 0, 2 << 28), (60 * MS + 10, 0, 0x50000000)], 75)
    ch = {n: board.level(p) for n, p in (("sck", 4), ("mosi", 5), ("miso", 6))}
    ch["cs"] = [0] * len(board.level(4))          # RESET is not a chip select: always selected
    dec = "spi:clk=sck:mosi=mosi:miso=miso:cs=cs:cs_polarity=active-low,avr_isp"
    ann = decode(vcd(ch, 40), dec, ["-A", "avr_isp"])
    assert ann[:4] == ["Programming enable", "Vendor code: 0x1e (Atmel)",
                       "Part family / memory size: 0x95", "Part number: 0x0f"], ann
    late = decode(vcd({n: v[59 * MS:] for n, v in ch.items()}, 40), dec, ["-A", "avr_isp"])
    assert late == ["Read fuse bits: 0x62"], late
    assert not [a for a in ann + late if a.startswith("Warning")], ann + late


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
    assert not [a for a in ann if "error" in a.lower() and not a.startswith("Control Error")], ann


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
    # sigrok names a Winbond part by its legacy ID (0x13 for the W25Q80DV) but looks it up
    # with RDID's third byte (0x14), so RDID always says "Winbond Unknown"; the bytes are checked above
    rdid = "Read identification (RDID): Device = Winbond Unknown"
    assert not [a for a in ann if ("Warning" in a or "Unknown" in a) and a != rdid], ann


def test_max7219_display():
    """The MAX7219 driver's frames, decoded by sigrok's max7219 decoder (LOAD as an
    active-low select): the set-up registers, then the digits of 40960, units first."""
    from test_max7219 import MS, run
    m, ch, host, board = run([(100, 0, 1 << 28 | 1 << 11 | 7 << 8 | 8), (2 * MS, 0, 3 << 28 | 40960)], 3.5)
    assert not ch.violations, ch.violations
    chs = {"clk": board.level(4), "din": board.level(5), "load": board.level(7)}
    ann = decode(vcd(chs, 40), "spi:clk=clk:mosi=din:cs=load:cs_polarity=active-low:wordsize=8,max7219",
                 ["-A", "max7219"])
    assert ann[:5] == ["Display test: off", "Scan limit: 8", "Decode: 0b11111111", "Intensity: 8",
                       "Digit 1: 0F"], ann[:6]
    assert ann[5:12] == [f"Digit {d}: 0F" for d in range(2, 9)], ann[5:12]   # every digit blank
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


def test_morse_message():
    """The keyer's output, decoded by sigrok's morse decoder (time unit = the 2 ms dot; sigrok
    prints letters in lower case): the words, and every element and gap 1, 3 or 7 dots."""
    from test_morse import run, text_words
    m, ear, board = run(text_words("CQ CQ DE ESC 73"), 420)
    assert not ear.violations, ear.violations
    trace = vcd({"key": board.level(0)}, 500)
    words = decode(trace, "morse:data=key:timeunit=0.002", ["-A", "morse=word"])
    assert words == ["cq", "cq", "de", "esc", "73"], words
    units = decode(trace, "morse:data=key:timeunit=0.002", ["-A", "morse=units"])
    assert set(units) == {"1.0*0.002", "3.0*0.002", "7.0*0.002"}, sorted(set(units))


def test_spdif_tx_frames():
    """Held-out round: spdif_tx.asm at CELL 8. The VCD is written at 20 ns a tick (a UI of
    160 ns, a 48.8 kHz frame rate; the decoder measures the rate itself): every sub-frame's
    24-bit audio word in order, valid, and the preambles B (Z), M (X), W (Y)."""
    from escmodels import Board, HostFeed
    from esciss import Machine
    import test_heldout_spdif_tx as S
    words = S.sample_words(220)           # more than the run uses: no underrun
    host = HostFeed({0: list(words)}, gap=64)
    board = Board(host)
    m = Machine(S.image(8), board=board)
    host.machine = m
    m.run(200 * 1024 + 3000, stop_when_idle=False)
    lv = board.level(0)
    first = next(t for t in range(1, len(lv)) if lv[t] != lv[t - 1])
    # from one UI before the stream, as an analyser triggered on it: the decoder learns its
    # three pulse widths from the first edges, and its first width runs from the start of
    # the capture (sigrok 0.7.2), so a longer idle lead-in would be taken for a pulse
    ann = decode(vcd({"line": lv[first - 8:]}, 20), "spdif:data=line")
    audio = [int(a.split()[1], 16) for a in ann if a.startswith("Audio 0x")]
    want = [x for w in words for x in ((w >> 16) << 8, (w & 0xFFFF) << 8)]
    k = want.index(audio[0])               # the decoder synchronises on the first frames
    assert k <= 6 and len(audio) >= 390 and audio == want[k:k + len(audio)], (k, audio[:6])
    # the first frame's Z goes into the decoder's synchronisation; the next is frame 192's
    pre = [a.split()[1] for a in ann if a.startswith("Preamble ")]
    b = pre.index("B")
    assert pre.count("B") == 1 and pre[b + 1] == "W" and (2 * 192 - b) in (k, k + 1), (b, k)
    rest = pre[:b] + ["M"] + pre[b + 1:]
    assert all(p == ("M" if (i - rest.index("M")) % 2 == 0 else "W")
               for i, p in enumerate(rest) if i >= rest.index("M")), pre[:8]
    assert ann.count("E") == 0 and ann.count("V") >= 390
    assert not [a for a in ann if "Unknown" in a]


def test_microwire_93c46_session():
    """Held-out round: microwire_93c46.asm (x16, 25 MHz, 500 kHz CLK) against the EEPROM
    model; sigrok's microwire decoder with eeprom93xx on top (6 address bits, 16-bit words):
    the write enable, the writes with their addresses and data, and the reads with the
    words the EEPROM sent."""
    import test_heldout_microwire_93c46 as M
    cmds = [M.cmd(M.EWEN), M.cmd(M.WRITE, 5, 0x1234), M.cmd(M.WRITE, 63, 0xBEEF),
            M.cmd(M.READ, 5), M.cmd(M.READ, 63)]
    ee, m, got = M.session(cmds, twc_us=50, board_out=True)
    assert got == [1, 1, 1, 0x1234, 0xBEEF]
    board = ee.board
    lv = {n: board.level(p) for n, p in (("cs", 0), ("sk", 1), ("si", 2), ("so", 3))}
    ann = decode(vcd(lv, 40), "microwire:cs=cs:sk=sk:si=si:so=so,"
                 "eeprom93xx:addresssize=6:wordsize=16")
    ops = [a for a in ann if a in ("Read word", "Write word", "Erase word", "Write enable",
                                   "Write disable")]
    assert ops == ["Write enable", "Write word", "Write word", "Read word", "Read word"], ann
    assert [a for a in ann if a.startswith("Address: ")] == ["Address: 0x0005", "Address: 0x003f",
                                                            "Address: 0x0005", "Address: 0x003f"], ann
    assert [a for a in ann if a.startswith("Data: ")] == ["Data: 0x1234", "Data: 0xbeef",
                                                         "Data: 0x1234", "Data: 0xbeef"], ann
    assert not [a for a in ann if "Not enough" in a], ann


def test_dht22_reads():
    """Held-out round: dht22.asm at 4 MHz reading the sensor model twice (65.8 %RH and
    26.9 C, then 50.0 %RH and -10.1 C); sigrok's am230x decoder on the line: the start, the
    readings and good checksums."""
    import test_heldout_dht22 as D
    from escmodels import Board, Dht22, HostScript
    from esciss import Machine
    khz = 4000
    sensor = Dht22(0, tick_ns=1e6 / khz, humidity=[65.8, 50.0], temperature=[26.9, -10.1],
                   startup_s=0.005, min_interval_s=0.005)
    host = HostScript([(100, 0, 1), (200, 0, 1)], drain=[0], poll=400)
    board = Board(sensor, host)
    m = Machine(D.image(khz), board=board)
    host.machine = m
    m.run(3 * (D.MIN_MS + 7) * khz, stop_when_idle=False)
    assert host.received[0] == [0x0292_010D, 500 << 16 | 0x8065]
    ann = decode(vcd({"sda": board.level(0)}, 250, tail_ticks=4000),
                 "am230x:sda=sda:device=am230x/rht")
    assert ann.count("Start") == 2, ann
    assert [a for a in ann if a.startswith(("Humidity:", "Temperature:", "Checksum:"))] == [
        "Humidity: 65.8 %", "Temperature: 26.9 °C", "Checksum: OK",
        "Humidity: 50.0 %", "Temperature: -10.1 °C", "Checksum: OK"], ann


def test_ps2_host_session():
    """Held-out round: ps2_host.asm at 4 MHz with the keyboard model (random timing): four
    scan codes, then the host's Echo (EE) and Read ID (F2) with the keyboard's answers.
    sigrok's ps2 decoder samples DATA on each CLK fall and closes a byte only on the fall
    after its 11th (on a PC, the controller's inhibit after each byte; this program inhibits
    only when its mailbox is full). So the capture is cut into one window per byte, from 10 us
    before the keyboard's start (or the host's request to send) to 10 us before the next, each
    closed by vcd()'s marker fall; each window is the pins' levels as they were. Host bytes
    decode too: DATA at the 11th fall is the keyboard's line-control bit, read as the stop."""
    import test_heldout_ps2_host as P
    from escmodels import Board, HostScript, Ps2Keyboard
    from esciss import Machine
    khz = 4000
    kbd = Ps2Keyboard(0, 1, tick_ns=1e6 / khz, send=[(100, b) for b in P.SCAN[:4]],
                      timing="random", seed=11, reply_us=300.0)
    host = HostScript([(6 * khz, 0, 0xEE), (9 * khz, 0, 0xF2)], drain=[0], poll=80)
    board = Board(kbd, host)
    m = Machine(P.image(khz), board=board)
    host.machine = m
    m.run(14 * khz, stop_when_idle=False)
    kbd.finish()
    want = P.SCAN[:4] + [0xEE, 0xEE, 0xF2, 0xFA, 0xAB, 0x83]
    assert host.received[0] == P.SCAN[:4] + [P.SENT, 0xEE, P.SENT, 0xFA, 0xAB, 0x83]
    assert not kbd.errors(), kbd.errors()
    starts = sorted([s["t_start"] for s in kbd.sent] + [r["t_rts"] for r in kbd.received])
    assert len(starts) == len(want)
    clk, data = board.level(0), board.level(1)
    cuts = [t - 40 for t in starts] + [len(clk)]
    words = []
    for a, b in zip(cuts, cuts[1:]):
        ann = decode(vcd({"clk": clk[a:b], "data": data[a:b]}, 250, tail_ticks=400),
                     "ps2:clk=clk:data=data")
        assert ann.count("Parity OK") == 1, ann
        words += [x.split(": ")[1] for x in ann if x.startswith("Data: ")]
    assert words == ["%02x" % b for b in want], words


def test_snes_pad_reads():
    """Held-out round: snes_pad.asm (25 MHz, latch and half clock 1 us) reading the pad model
    (5 V maximum delays) three times. sigrok has no SNES decoder (nes_gamepad is the 8-bit
    NES pad), so the judge is its spi decoder: LATCH as an active-low select (the shift phase
    is LATCH low), CLK idle high, DATA as MISO sampled on each rising edge (CPOL 1, CPHA 1),
    8-bit words MSB first: the two key data bytes of each read, as the line levels (a pressed
    button low), then the 17th bit as an incomplete word."""
    import test_heldout_snes_pad as S
    from escmodels import Board, HostScript, SnesPad
    from esciss import Machine
    reads = [S.mask("B", "Start"), S.mask("L", "R", "A"), 0]
    pad = SnesPad(0, 1, 2, tick_ns=40.0, buttons=reads, timing="max")
    host = HostScript([(200, 0, 1), (2200, 0, 1), (4200, 0, 1)], drain=[0], poll=50)
    board = Board(pad, host)
    m = Machine(S.image(25000, 1000, 1000), board=board)
    host.machine = m
    m.run(6500, stop_when_idle=False)
    pad.finish()
    assert host.received[0] == [S.CONNECTED | r for r in reads] and not pad.errors()
    ch = {"clk": board.level(1), "miso": board.level(2), "cs": board.level(0)}
    ann = decode(vcd(ch, 40, tail_ticks=100),
                 "spi:clk=clk:miso=miso:cs=cs:cs_polarity=active-low:cpol=1:cpha=1:wordsize=8",
                 ["-A", "spi=miso-data"])
    want = []
    for r in reads:
        line = ~r & 0xFFFF
        want += ["%02X" % (line >> 8), "%02X" % (line & 0xFF)]
    assert ann == want, ann


def test_mbus_slave_exchanges():
    """Held-out round: mbus_slave.asm at 4 MHz, 9600 baud, address 5, with the master model:
    SND_NKE, then REQ_UD2 with the default CI 72h response. sigrok's uart decoder on both
    lines (8 data bits, even parity, 1 stop bit, M-Bus 5.1): every character of the master's
    telegrams and of the slave's replies, and no parity or frame error."""
    import test_heldout_mbus_slave as T
    from escmodels import Board, HostScript, MbusMaster
    from escmodels.mbus import short_frame
    from esciss import Machine
    tel = [short_frame(0x40, 5), short_frame(0x7B, 5)]
    master = MbusMaster(0, 1, tick_ns=250.0, baud=9600, slave_address=5, start_ms=1.0,
                        telegrams=tel)
    host = HostScript([], drain=[0], poll=100)
    board = Board(master, host)
    m = Machine(T.image(4000, 9600, 5), board=board)
    host.machine = m
    m.run(200_000, stop_when_idle=False)
    master.finish()
    assert not master.errors(), master.errors()
    replies = [x["reply"] for x in master.exchanges]
    assert replies[0] == bytes([0xE5]) and len(replies[1]) == 21
    ann = decode(vcd({"m2s": board.level(0), "s2m": board.level(1)}, 250, tail_ticks=4000),
                 "uart:rx=m2s:tx=s2m:baudrate=9600:parity=even:format=hex")
    assert not [a for a in ann if "error" in a.lower()], ann
    data = [a for a in ann if len(a) == 2 and all(c in "0123456789ABCDEF" for c in a)]
    want = list(tel[0]) + list(replies[0]) + list(tel[1]) + list(replies[1])   # half duplex
    assert data == ["%02X" % b for b in want], (data, want)


JUDGED = [test_mbus_slave_exchanges, test_snes_pad_reads, test_ps2_host_session, test_dht22_reads, test_microwire_93c46_session, test_spdif_tx_frames, test_usbpd_source_capabilities, test_lin_responder_frame, test_rc6_frame, test_em4100_tag,
          test_dcf77_minutes, test_sd_card_session, test_onewire_device_read_and_search_rom,
          test_avr_isp_session, test_qi_receiver_session, test_spi_flash_session,
          test_max7219_display, test_stepper_speed_and_position, test_nrf24_session,
          test_morse_message]


@pytest.mark.parametrize("judged", JUDGED, ids=lambda f: f.__name__[5:])
def test_control_fails(judged, monkeypatch):
    """Each judge's negative control: the same run with the middle pulse of every line it
    decodes inverted (sigrok_judge.FLIP) must fail the judge. A judge that passes whatever
    the pins do proves nothing."""
    import sigrok_judge
    monkeypatch.setattr(sigrok_judge, "FLIP", True)
    with pytest.raises(AssertionError):
        judged()
