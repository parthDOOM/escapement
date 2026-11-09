"""The bus models against real devices: logic analyser recordings from sigrok-dumps
(tests/data/sigrok, README there), played onto the pins. A model that refuses a working
device is too strict, or the device is out of specification; the tests say which.

Recordings start in the middle of a session and carry the line as a real receiver sees
it: noise between packets and edges moved by a demodulator. The models that judge our
firmware's clean output have a real-line mode for them (Qi: `hunt`)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from recording import DATA, meta, play, play_edges, sample_ns

from escmodels.qi import QiTransmitter
from escmodels.board import Device
from escmodels.usbpd import UsbPdSink


def _qi(name):
    q = QiTransmitter(0, 1, 1000.0, power=((0, None),), ignore_ept=True, hunt=True, from_start=False)
    play(name, 1000.0, {0: 0}, q)
    q.finish()
    return q


def test_qi_nexus5():
    """A Nexus 5 on a GPMQ8005A transmitter, 5 s of power transfer: every packet decodes
    with good parity and checksum, a CE every 241.7 ms (at most 350), an RP8 64.3 ms after
    a CE, and no timing rule broken."""
    q = _qi("qi_nexus5.sr")
    assert len(q.packets) == 24 and all(p["ok"] for p in q.packets), q.violations[:5]
    assert [p["name"] for p in q.packets].count("RP8") == 3 and not q.violations, q.violations[:5]
    ce = [p["start"] for p in q.packets if p["name"] == "CE"]
    assert max(b - a for a, b in zip(ce, ce[1:])) < 350_000


def test_qi_aftermarket_receiver_out_of_spec():
    """A no-name Qi receiver for a Samsung phone, 4 s: it keys its bits at 545 us, outside
    fCLK = 2 kHz +-4% (tCLK 480 to 520 us), and the model refuses every packet for it."""
    q = _qi("qi_noname_samsung5.sr")
    assert q.packets and not any(p["ok"] for p in q.packets)
    assert all(any(e.startswith("mean bit 54") for e in p["errors"]) for p in q.packets), \
        [p["errors"] for p in q.packets][:3]


# USB Power Delivery on the CC wire, recorded through a Twinkie interposer (or a logic
# analyser on CC1/CC2): (file, channel, messages, damaged bursts). Every message decodes
# with a good CRC-32 and EOP. "Damaged" are bursts that are no message: a flip of the plug
# during the capture (both_orientations, CC1) and glitches that split a message. One ZY12PDS
# capture has a message whose falling edges all come 0.67 us early (a threshold offset);
# the receiver takes the skew out (UsbPdSink, real_line), which also recovers a GoodCRC that
# sigrok's decoder misses on the ThinkPad's CC1 (SIGROK_READS).
PD = [
    ("pd_apple_av_hdmi.sr", 0, 84, 6), ("pd_apple_power_brick.sr", 0, 61, 0),
    ("pd_hdmi_dongle.sr", 1, 54, 0), ("pd_power_supply_20V.sr", 0, 42, 0),
    ("pd_thinkpad_anker_powerbank-both_orientations.sr", 2, 21, 24),
    ("pd_thinkpad_anker_powerbank-both_orientations.sr", 3, 21, 0),
    ("pd_thinkpad_aukey_45w.sr", 1, 8, 0),
    ("pd_thinkpad_passtrough_dongle-anker_powerbank.sr", 1, 138, 0),
    ("pd_zy12pds_65w_noname_supply.sr", 2, 10, 0), ("pd_zy12pds_anker_powerbank.sr", 2, 53, 0),
]


def _pd(name, ch):
    tick = sample_ns(name)
    s = UsbPdSink(0, tick, real_line=True)
    play(name, tick, {ch: 0}, s)
    s.finish()
    return s


@pytest.mark.parametrize("name,ch,n,damaged", PD)
def test_usb_pd_real_sources_and_sinks(name, ch, n, damaged):
    """MacBook, Pixel, ThinkPad and a ZY12PDS sink with their chargers and adapters: 492
    messages (85 of them SOP' to the cable), every CRC-32 good and every EOP present."""
    s = _pd(name, ch)
    assert len(s.messages) == n and all(m["crc_ok"] and m["eop"] for m in s.messages)
    assert len(s.violations) == damaged, s.violations[:5]


SIGROK = shutil.which("sigrok-cli")
# where sigrok-cli 0.7.2 reads fewer good messages than the model: the GoodCRC afd6a8a2
# (header 0x441, acknowledging the PS_RDY before it), skewed like the ZY12PDS message
SIGROK_READS = {("pd_thinkpad_anker_powerbank-both_orientations.sr", 2): 20}


@pytest.mark.skipif(SIGROK is None, reason="sigrok-cli not installed")
@pytest.mark.parametrize("name,ch,n,damaged", PD)
def test_usb_pd_against_sigrok(name, ch, n, damaged):
    """sigrok's usb_power_delivery decoder reads the same recording: as many messages with
    a good CRC as the model (a separate implementation on the same samples), but where
    SIGROK_READS says it misses one."""
    cc = meta(name)[f"probe{ch + 1}"]
    r = subprocess.run([SIGROK, "-i", str(DATA / name), "-P", f"usb_power_delivery:cc1={cc}",
                        "-A", "usb_power_delivery=crc"], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr
    ok = [ln for ln in r.stdout.splitlines() if "CRC" in ln and "!" not in ln]
    assert len(ok) == SIGROK_READS.get((name, ch), n), r.stdout[-2000:]


def test_wiegand_rx_firmware_on_a_real_reader():
    """The chip's own wiegand_rx firmware (on the ISS at 4 MHz) on a real 13.56 MHz reader's
    D0/D1 output, sampled at 20 kHz: both frames read. The recording's notes say 36-bit
    output; the reader sends 34 bits: even parity over the first 17, odd over the last 17,
    32 data bits 0x45320488 (the file is named after the raw frame, 0x8a640910)."""
    from pathlib import Path
    from escasm import assemble
    from escmodels import Board, HostScript
    from esciss import Machine
    from recording import Recording
    khz = 4000
    fw = Path(__file__).resolve().parents[2] / "firmware"
    im = assemble(f'.equ wiegand_rx.CLK_KHZ, {khz}\n.include "wiegand_rx.asm"\n', base_dir=fw)
    rec = Recording("wiegand36_8a640910.sr", 1e6 / khz, {0: 0, 1: 1})
    host = HostScript([], drain=[0], poll=khz // 10)
    m = Machine(im, board=Board(rec, host))
    host.machine = m
    m.run(rec.end + khz * 30, stop_when_idle=False)
    assert not m.ff.valid
    out = host.received[0]
    frames = [(out[i], out[i + 1] << 32 | out[i + 2]) for i in range(0, len(out) - 2, 3)]
    assert frames == [(34, 0x8A640910)] * 2, [(hex(n), hex(v)) for n, v in frames]
    bits = [(0x8A640910 >> (33 - k)) & 1 for k in range(34)]
    assert sum(bits[:17]) % 2 == 0 and sum(bits[17:]) % 2 == 1
    assert int("".join(map(str, bits[1:33])), 2) == 0x45320488


# EM4100 tags read by an RDM630 module (its RFID pin: the demodulated tag signal, high
# while the modulator is ON) with the module's own 9600 8N1 output beside it, and one tag
# on a reader coil.
EM4100 = ["em4100_010784f221_card.sr", "em4100_01092ade55_keyfob.sr", "em4100_041815e864_glass.sr",
          "em4100_19004f03d7_card.sr", "em4100_19004f246f_card.sr", "em4100_29000c2c34_glass.sr",
          "em4100_3b0033aaf2_keyfob.sr", "em4100_3b00344ce7_keyfob.sr", "em4100_3b0035c693_keyfob.sr",
          "em4100_8400043916_keyfob.sr", "em4100_0200f5ed8d_coil.sr"]


@pytest.mark.parametrize("name", EM4100)
def test_em4100_real_tags(name):
    """Eleven real tags (cards, key fobs, glass tags): the reader model reads every one,
    header and every parity right, as the RDM630 itself reads it (its UART: STX, ten hex
    digits, the XOR of the five bytes, ETX) and as the file is named."""
    from escmodels import Board
    from escmodels.em4100 import Em4100Reader
    from escmodels.uart import UartRx
    from recording import Recording
    tick = 2000.0                                          # 2 us; a half bit is 256 us
    probes = {v: int(k[5:]) - 1 for k, v in meta(name).items() if k.startswith("probe")}
    ident = int(name.split("_")[1], 16)
    rd = Em4100Reader(0, 256e3 / tick, real_line=True)
    pins = {probes.get("RFID", probes.get("1")): 0}
    uart = None
    if "RX" in probes:                                     # the RDM630's own output
        uart = UartRx(1, 1e9 / 9600 / tick)
        pins[probes["RX"]] = 1
    rec = Recording(name, tick, pins)
    board = Board(rec, rd, *([uart] if uart else []))
    for t in range(rec.end + 1):
        board(t, 0, 0)
    rd.levels = [1 - x for x in rd.levels]                 # the modulator is ON while high
    ids = rd.ids()
    assert ids and set(ids) == {ident}, [hex(v) for v in ids]
    if uart:
        msg = bytes(uart.bytes)
        k = msg.index(2)
        hexid, chk = msg[k + 1:k + 11].decode(), int(msg[k + 11:k + 13], 16)
        assert int(hexid, 16) == ident and msg[k + 13] == 3, msg
        x = 0
        for b in ident.to_bytes(5, "big"):
            x ^= b
        assert chk == x


@pytest.mark.parametrize("name,first,n", [("dcf77_pollin_120s.sr", (2012, 1, 9, 23, 49), 1),
                                          ("dcf77_pollin_1800s.sr", (2012, 1, 10, 1, 30), 16)])
def test_dcf77_real_receiver_module(name, first, n):
    """A Pollin DCF1 receiver module (DATA low while the carrier is reduced, so inverted
    here), recorded off the air: the minutes the model reads are consecutive, each with
    every parity right, and the weekday it carries is the calendar's for that date."""
    import datetime
    from escmodels.dcf77 import Dcf77Receiver
    probes = {v: int(k[5:]) - 1 for k, v in meta(name).items() if k.startswith("probe")}
    r = Dcf77Receiver(0, 1e6, real_line=True)                # 1 ms ticks
    play(name, 1e6, {probes["DATA"]: 0}, r)
    r.levels = [1 - x for x in r.levels]
    r.finish()
    t0 = datetime.datetime(*first)
    got = [datetime.datetime(2000 + m["year"], m["month"], m["day"], m["hour"], m["minute"])
           for m in r.minutes]
    assert got == [t0 + datetime.timedelta(minutes=k) for k in range(n)], got
    assert all(m["weekday"] == datetime.date(2000 + m["year"], m["month"], m["day"]).isoweekday()
               and not m["cest"] for m in r.minutes)


@pytest.mark.parametrize("axis,step,dirp", [("X", 5, 6), ("Y", 3, 4)])
def test_stepper_smoothieware_runs_its_gcode(axis, step, dirp):
    """Smoothieware (12 MHz capture) running test.gcode from the recording's directory, at
    80 steps/mm: X 0 -> 200 -> 190 -> 0 mm and Y 0 -> 200 -> 0 mm are 400 mm, 32,000 steps
    each, one reversal at 200 mm (16,000 steps), both home at the end; every STEP and DIR
    edge within the A4988's tA, tB, tC and tD. (This controller's DIR is low for +.)"""
    from recording import play_edges
    from escmodels.stepper import StepDirDriver
    name = "stepper_smoothieware.sr"
    d = StepDirDriver(0, 1, sample_ns(name))
    play_edges(name, sample_ns(name), {step: 0, dirp: 1}, d)
    assert len(d.steps) == 32000 and d.position == 0 and not d.violations, d.violations[:3]
    pos, turns = 0, []
    for k, (_, s) in enumerate(d.steps):
        if k and s != d.steps[k - 1][1]:
            turns.append(pos)
        pos += s
    assert turns == [-16000]


def test_stepper_grbl_timing():
    """GRBL on a CNC's Y axis (STEP only, 2 MHz capture), 56,903 steps: every pulse within
    the A4988's high and low times."""
    from recording import play_edges
    from escmodels.stepper import StepDirDriver
    name = "stepper_grbl_3.sr"
    d = StepDirDriver(0, 1, sample_ns(name))
    play_edges(name, sample_ns(name), {1: 0}, d)
    assert len(d.steps) == 56903 and not d.violations, d.violations[:3]


def test_cec_firmware_follows_a_real_tv_and_amplifier():
    """The chip's own cec firmware (ISS, 1 MHz, logical address 14, which nothing on this
    bus uses, so it only listens) on a Sony TV and a Denon amplifier while the TV is
    switched off: it reports every frame block for block (byte, EOM, the ACK bit as
    sampled) exactly as the CEC device model reads the same line, with no bit outside the
    cec-pin.c windows. Among them the TV's broadcast Standby (0F 36) and its Vendor
    Command With ID carrying Sony's vendor ID 08-00-46."""
    from pathlib import Path
    from escasm import assemble
    from escmodels import Board
    from escmodels.cec import CecDevice
    from esciss import Machine
    from recording import Recording
    name, khz = "cec_sony_denon_off.sr", 1000
    ch = {v: int(k[5:]) - 1 for k, v in meta(name).items() if k.startswith("probe")}["CEC"]
    tick = 1e6 / khz
    rec = Recording(name, tick, {ch: 0})
    ear = CecDevice(0, 14, tick, ack=False)
    board = Board(rec, ear)
    fw = Path(__file__).resolve().parents[2] / "firmware"
    m = Machine(assemble(f'.equ cec.CLK_KHZ, {khz}\n.equ cec.LA, 14\n.include "cec.asm"\n', base_dir=fw),
                board=board)
    out = []
    while m.now < rec.end:
        m.run(2000, stop_when_idle=False)
        while (v := m.host_tx_pop(0)) is not None:
            out.append(v)
            m.run(1, stop_when_idle=False)
        m.pins.clear()
        m.commits.clear()
        board.history.clear()
    chip = []
    for w in out:
        if w & 0x400:
            chip.append(([], []))
        chip[-1][0].append((w >> 2) & 0xFF)
        chip[-1][1].append(w & 1)
    model = [(f["bytes"], f["acks"]) for f in ear.frames]
    assert not m.ff.valid and not board.contention and not ear.violations
    assert len(chip) == 13 and chip == model, (chip, model)
    assert ([0x0F, 0x36], [1, 1]) in chip
    assert any(b[:5] == [0x0F, 0xA0, 0x08, 0x00, 0x46] for b, _ in chip)


def _dcc_accessory(b):
    """A basic accessory packet {10AAAAAA} {1AAACDDD} (S-9.2.1): (output address, C, D0),
    the upper address bits sent inverted."""
    if len(b) == 3 and b[0] >> 6 == 0b10 and b[1] >> 7 == 1:
        a = (b[0] & 0x3F) | ((~b[1] >> 4) & 0x7) << 6
        return (a - 1) * 4 + ((b[1] >> 1) & 3) + 1, (b[1] >> 3) & 1, b[1] & 1
    return None


@pytest.mark.parametrize("name,accessories", [("dcc_easycontrol_120_121.sr", {120, 121}),
                                              ("dcc_easycontrol_133.sr", {133}),
                                              ("dcc_easycontrol_140.sr", {140}),
                                              ("dcc_easycontrol_310.sr", {310})])
def test_dcc_real_command_station_accessories(name, accessories):
    """A tams EasyControl command station through a B-2 booster (1 MHz capture through a
    clamp): every packet's check byte right (but the last, which the end of the recording
    cuts), and the basic accessory packets address exactly the decoders the recording's
    notes name, switched on and off."""
    from recording import play_edges
    from escmodels.ir import DccDecoder
    d = DccDecoder(0, sample_ns(name))
    play_edges(name, sample_ns(name), {0: 0}, d)
    pk = d.packets()[:-1]
    assert len(pk) > 800 and all(p["check_ok"] for p in pk), [p for p in pk if not p["check_ok"]][:3]
    acc = {_dcc_accessory(p["bytes"]) for p in pk}
    acc.discard(None)
    assert {a for a, c, _ in acc} == accessories and {c for _, c, _ in acc} == {0, 1}


def test_dcc_real_command_station_loco_light():
    """The same station switching the light of loco 2: function group one (100 FL F4-F1)
    to address 2 goes 0x80, 0x90 (FL on), 0x80, 0x90; every check byte right."""
    from itertools import groupby
    from recording import play_edges
    from escmodels.ir import DccDecoder
    name = "dcc_easycontrol_2_light.sr"
    d = DccDecoder(0, sample_ns(name))
    play_edges(name, sample_ns(name), {0: 0}, d)
    pk = d.packets()
    assert pk and all(p["check_ok"] for p in pk)
    fg = [p["bytes"][1] for p in pk if p["bytes"][0] == 0x02 and p["bytes"][1] & 0xE0 == 0x80]
    assert [v for v, _ in groupby(fg)] == [0x80, 0x90, 0x80, 0x90]


def _dali_frames(records, te):
    """Independent of the firmware: Manchester frames in a recording, as (first falling
    edge, bits incl. the start bit); a frame ends at an idle of more than 3 Te."""
    seg, s = [], [records[0]]
    for a, b in zip(records, records[1:]):
        if b[0] - a[0] > 3 * te:
            seg.append(s)
            s = []
        s.append(b)
    seg.append(s)
    out = []
    for s in seg:
        if len(s) < 4:
            continue
        halves = []
        for (t0, v0), (t1, _) in zip(s, s[1:]):
            halves += [v0 & 1] * max(1, round((t1 - t0) / te))
        halves += [s[-1][1] & 1] * 2
        bits = [{(0, 1): 1, (1, 0): 0}.get((halves[2 * i], halves[2 * i + 1])) for i in range(len(halves) // 2)]
        out.append((s[0][0], bits))
    return out


def test_dali_firmware_hears_a_real_controller():
    """A Rako RSR-DALI controller querying a ballast at short address 0 (100 kHz capture):
    the chip's dali firmware (ISS, 1 MHz, short address 63, so it only listens) receives
    all nine forward frames exactly as an independent Manchester decode reads them, takes
    none as its own and answers none. The real ballast answers each 4.3 to 4.7 ms after
    the forward frame's last bit, earlier than the 5.5 ms of IEC 62386-101:2014 that the
    controller model (escmodels.dali) holds the chip's gear to."""
    from pathlib import Path
    from escasm import assemble
    from escmodels import Board, HostScript
    from esciss import Machine
    from recording import Recording
    khz = 1000
    te = 416.67 * khz / 1000
    rec = Recording("dali_rako_query_ballast.sr", 1e6 / khz, {0: 0})
    host = HostScript([(10, 0, 1 << 31 | 63)], drain=[0], poll=khz)
    fw = Path(__file__).resolve().parents[2] / "firmware"
    m = Machine(assemble(f'.equ dali.CLK_KHZ, {khz}\n.include "dali.asm"\n', base_dir=fw), board=Board(rec, host))
    host.machine = m
    m.run(rec.end + 20 * khz, stop_when_idle=False)
    assert not m.ff.valid
    frames = _dali_frames(rec.records, te)
    fwd = [(t, b) for t, b in frames if len(b) >= 17 and None not in b[:17]]
    want = [int("".join(map(str, b[1:17])), 2) for _, b in fwd]
    assert want == [0x0191, 0x01C0, 0x01C1, 0x01A3, 0x01A4, 0x01A5, 0x01A1, 0x01A2, 0x0199]
    assert host.received[0] == want                    # [16], [17] clear: not ours, not answered
    back = [(t, b) for t, b in frames if len(b) < 17]
    assert len(back) == 9 and all(None not in b[:9] for _, b in back)
    settle = [(tb - (tf + 17 * 2 * te)) / khz for (tf, _), (tb, _) in zip(fwd, back)]
    assert all(4.2 < s < 4.75 for s in settle), settle


def test_sent_real_motor_position_sensor():
    """A motor position sensor's SENT output (SAE J2716 2010, 3 us ticks, six data nibbles,
    pause pulse; 100 MHz capture): every frame's CRC right, no pulse outside the receiver's
    limits, the unit time measured from the sync pulse 2.982 us (0.6% slow)."""
    from recording import play_edges
    from escmodels.sent import SentReceiver
    tick = 100.0                                            # 0.1 us
    r = SentReceiver(0, tick, ut_us=3.0, pause=True)
    play_edges("sent_motor_position_3us.sr", tick, {0: 0}, r)
    assert len(r.frames) == 11 and all(f["crc_ok"] for f in r.frames) and not r.violations
    assert all(abs(f["unit"] * tick / 1000 - 2.982) < 0.002 for f in r.frames)
    assert all(f["pause"] for f in r.frames[:-1])


# An Olimex STM32-H103 (STM32F103) sending "Hello World!\r\n" over and over, 8N1, at every
# rate from 1200 to 921600 baud; its USART starts each byte straight after the stop bit.
# (rate, chip clock in kHz): 1200 to 19200 at 1 MHz, 38400 and 57600 at 4 MHz, the rest at
# 25 MHz and 921600 at 50 MHz, so 54 to 833 ticks per bit.
STM32_UART = [(1200, 1000), (2400, 1000), (4800, 1000), (9600, 1000), (19200, 1000),
              (38400, 4000), (57600, 4000), (115200, 25000), (230400, 25000), (460800, 25000),
              (921600, 50000)]


def _uart_rx_firmware(name, baud, khz, tail_ms=0):
    """uart_rx on the ISS at khz with the recording on its RX pin (probe 1): the words it
    pushed (a byte, bit 8 set on a framing error) and whether it faulted."""
    from pathlib import Path
    from escasm import assemble
    from escmodels import Board, HostScript
    from esciss import Machine
    from recording import Recording
    fw = Path(__file__).resolve().parents[2] / "firmware"
    im = assemble(f".equ uart_rx.CLK_KHZ, {khz}\n.equ uart_rx.BAUD, {baud}\n"
                  f'.include "uart_rx.asm"\n', base_dir=fw)
    rec = Recording(name, 1e6 / khz, {0: 1})
    host = HostScript([], drain=[1], poll=max(1, khz * 8000 // baud))
    m = Machine(im, board=Board(rec, host))
    host.machine = m
    m.run(rec.end + tail_ms * khz, stop_when_idle=False)
    return host.received[1], m.ff.valid


@pytest.mark.parametrize("baud,khz", STM32_UART)
def test_uart_rx_firmware_reads_a_real_stm32(baud, khz):
    """The chip's own uart_rx firmware on the ISS reads every byte of the STM32's output,
    with no framing error. This recording found a bug: the firmware pushed each byte before
    watching the line again, so below about 100 ticks per bit it missed the start of a byte
    sent straight after a stop bit (57600 at 4 MHz, 460800 at 25 MHz and 921600 at 50 MHz
    failed)."""
    got, fault = _uart_rx_firmware(f"uart_stm32_8n1_{baud}.sr", baud, khz)
    assert not fault
    assert all(b <= 0xFF for b in got), "framing errors"
    k = bytes(got).index(b"H")                             # the recording starts mid-message
    assert k < 14 and len(got) - k >= 40
    assert bytes(got[k:]) == (b"Hello World!\r\n" * 5)[:len(got) - k], bytes(got)


def test_uart_rx_firmware_reads_a_real_gps_module():
    """An MTK3339 GPS module (Adafruit breakout) sending NMEA at 9600 8N1, sampled at
    200 kHz, uart_rx at 1 MHz. The capture starts mid-sentence (it triggered on a low
    line) inside a burst sent back to back, so a receiver first locks on a data bit and
    finds its framing a sentence later; the receiver model does too. From the first CR LF
    on: no framing error, every sentence's checksum (the XOR of the bytes between $ and *)
    right, and the very bytes the receiver model reads."""
    from escmodels import Board
    from escmodels.uart import UartRx
    from recording import Recording
    got, fault = _uart_rx_firmware("uart_gps_mtk3339_9600.sr", 9600, 1000, tail_ms=5)
    assert not fault
    k = next(i for i in range(len(got) - 2) if got[i:i + 3] == [13, 10, ord("$")]) + 2
    assert all(b <= 0xFF for b in got[k:]), "framing errors after the first CR LF"
    text = bytes(got[k:])
    model = UartRx(1, 1e6 / 9600)
    rec = Recording("uart_gps_mtk3339_9600.sr", 1000.0, {0: 1})
    board = Board(rec, model)
    for t in range(rec.end + 5000):                       # as long as the firmware ran
        board(t, 0, 0)
    mb = bytes(model.bytes)
    assert mb[mb.index(text[:40]):] == text
    sentences = text.split(b"\r\n")[:-1]
    assert len(sentences) >= 19
    for s in sentences:
        body, ck = s[1:].split(b"*")
        x = 0
        for c in body:
            x ^= c
        assert s[:1] == b"$" and int(ck, 16) == x, s
    assert {s[:6] for s in sentences} == {b"$GPGGA", b"$GPGSA", b"$GPGSV", b"$GPRMC", b"$GPVTG"}


def _frames_8e1(lv, tpb):
    """8E1 characters on a list of line levels, grouped into frames by 3.5 characters of
    silence: [[(byte, parity ok, stop ok), ...], ...]."""
    frames, cur, last, t = [], [], None, 1
    while t < len(lv) - 11 * tpb:
        if lv[t - 1] == 1 and lv[t] == 0:
            bits = [lv[round(t + (k + 0.5) * tpb)] for k in range(11)]
            if last is not None and t - last > 3.5 * 11 * tpb:
                frames.append(cur)
                cur = []
            cur.append((sum(bits[1 + i] << i for i in range(8)), sum(bits[1:10]) % 2 == 0,
                        bits[10] == 1))
            last, t = t, round(t + 10.5 * tpb)
        else:
            t += 1
    return frames + [cur] if cur else frames


def test_modbus_rtu_firmware_answers_a_real_client_as_a_real_server_does():
    """A PC (minimalmodbus, through USB-RS232 and RS232-RS485 converters) polls a Brainchild
    IO-16DO output module, server 1, at 19200 8E1: functions 1, 2, 3, 4, 5, 6, 15 and 16
    in turn, recorded on the RS-232 side at 1 MHz. The PC's requests go to the chip's
    modbus_rtu firmware (ISS, 4 MHz, server 1, 100 registers, register 99 = 0x0201 as the
    module has it). The firmware implements 3, 6 and 16: its replies to those are the
    module's own, byte for byte; to the other five, exception 01 (illegal function). Its
    writes to register 1 reach the tape and the host."""
    from pathlib import Path
    from escasm import assemble
    from escmodels import Board, HostScript
    from escmodels.modbus import adu, crc16
    from esciss import Machine
    from recording import Recording
    khz = 4000
    fw = Path(__file__).resolve().parents[2] / "firmware"
    im = assemble(f".equ modbus_rtu.CLK_KHZ, {khz}\n.equ modbus_rtu.NREG, 100\n"
                  f'.include "modbus_rtu.asm"\n', base_dir=fw)
    im.tape[99] = 0x0201
    rec = Recording("modbus_brainchild_io16do.sr", 1e6 / khz, {1: 1, 0: 3})
    rec.records = [(t, v ^ 3) for t, v in rec.records]   # RS-232: idle is the negative level
    rec.level = rec.records[0][1]
    host = HostScript([], drain=[0], poll=khz)
    board = Board(rec, host)
    m = Machine(im, board=board)
    host.machine = m
    m.run(rec.end + 40 * khz, stop_when_idle=False)
    assert not m.ff.valid
    tpb = khz * 1000 / 19200
    req, real, ours = (_frames_8e1(board.level(p), tpb) for p in (1, 3, 0))
    for f in req + real + ours:
        assert all(p and s for _, p, s in f), f
    req, real, ours = ([bytes(b for b, _, _ in f) for f in x] for x in (req, real, ours))
    assert len(req) == 15 and [r[1] for r in req[:8]] == [1, 2, 3, 4, 5, 6, 15, 16]
    assert all(crc16(f) == 0 for f in req + real)        # a frame with its CRC: residue 0
    assert len(real) == len(ours) == 15
    for q, r, o in zip(req, real, ours):
        if q[1] in (3, 6, 16):
            assert o == r, (q.hex(), r.hex(), o.hex())
        else:
            assert o == adu(1, bytes([q[1] | 0x80, 1])), (q.hex(), o.hex())
    assert host.received[0] == [1 << 16 | 1] * 3 and m.tape[1] == 0x55


def _i2c_eeprom_beside(name, khz, idle_us, memory):
    """The chip's i2c_eeprom firmware (target 0x50, SCL pin 2, SDA pin 3) on a recorded I2C
    bus in place of the recorded 0x50: the recording drives the bus except SDA in 0x50's
    bit slots, which the firmware must drive itself. Returns the recorded transactions, the
    ones on the resulting bus, the ticks where the bus's SCL differs from the recording's
    (the firmware stretching the clock where the real target did not), and the machine."""
    from pathlib import Path
    from escasm import assemble
    from escmodels import Board
    from esciss import Machine
    from esctape.export import read_sr
    from recording import DATA, I2cReplay, bus_records, compress_idle, i2c_target_slots, \
        i2c_transactions
    probes = {v: int(k[5:]) - 1 for k, v in meta(name).items() if k.startswith("probe")}
    scl, sda = probes["SCL"], probes["SDA"]
    recs = compress_idle(read_sr(DATA / name, clock_hz=khz * 1000)[0], idle_us * khz // 1000)
    want = i2c_transactions(recs, scl, sda)
    fw = Path(__file__).resolve().parents[2] / "firmware"
    im = assemble(f'.equ i2c_eeprom.CLK_KHZ, {khz}\n.include "i2c_eeprom.asm"\n', base_dir=fw)
    for a, b in memory.items():
        im.tape[a] = b
    rp = I2cReplay(recs, scl, sda, 2, 3, i2c_target_slots(want, 0x50))
    board = Board(rp)
    m = Machine(im, board=board)
    m.run(rp.end + 100, stop_when_idle=False)
    got = i2c_transactions(bus_records(board.history, [2, 3]), 0, 1)
    k, v, differ = 0, recs[0][1], 0
    for t, b in enumerate(board.history):
        while k < len(recs) and recs[k][0] <= t:
            v = recs[k][1]
            k += 1
        differ += ((v >> scl) & 1) != ((b >> 2) & 1)
    return want, got, differ, m


def _i2c_view(tr):
    return (tr["rs"], [(b, a) for b, a, _ in tr["bytes"]], tr["stop"] is not None)


def test_i2c_eeprom_firmware_boots_a_real_fx2():
    """The Cypress FX2 in a Hantek 6022BE oscilloscope reading its boot EEPROM, a 24LC02B,
    at power-up (about 87 kHz, recorded at 8 MHz). i2c_eeprom at 25 MHz, holding the
    header the 24LC02B sent (C0, then VID 04B4, PID 6022 and DID 0000 low byte first,
    config 00), stands in for it: every bit the FX2 saw is the same, but one. The FX2
    first reads one byte at the current address, which the 24LC02B data sheet leaves
    undefined at power-up: the real one sent 0x00, the firmware's pointer starts at word 0.
    Its clock stretching never shows: SCL is the FX2's at every tick."""
    hdr = [0xC0, 0xB4, 0x04, 0x22, 0x60, 0x00, 0x00, 0x00]
    want, got, differ, m = _i2c_eeprom_beside("i2c_24lc02b_fx2_hantek6022be.sr", 25000, 2000,
                                              dict(enumerate(hdr)))
    assert not m.ff.valid and differ == 0
    assert [_i2c_view(t) for t in want] == [
        (False, [(0xA1, 0), (0x00, 1)], False), (True, [(0xA0, 0), (0x00, 0)], False),
        (True, [(0xA1, 0)] + [(b, 0) for b in hdr[:-1]] + [(hdr[-1], 1)], True)]
    first = _i2c_view(got[0])
    assert first == (False, [(0xA1, 0), (hdr[0], 1)], False)     # word 0, not the 0x00
    assert [_i2c_view(t) for t in got[1:]] == [_i2c_view(t) for t in want[1:]]


def test_i2c_eeprom_firmware_beside_a_real_eeprom():
    """An internal bus of a Tektronix TDS 744A oscilloscope, about 1.7 kHz (recorded at
    2 MHz), with two X24C02 EEPROMs at 0x50 and 0x51: random reads of both, six probes
    of an absent 0x52, then block reads of 248 bytes from 0x50 and 196 from 0x51.
    i2c_eeprom at 500 kHz, holding what the real 0x50 sent, takes 0x50's place beside the
    real 0x51: all 14 transactions on the bus are bit for bit the recorded ones, so it
    answers its address with the right timing, keeps the pointer across transactions,
    and leaves the bus alone while 0x51 and 0x52 are addressed. At this clock the 320 ns
    SDA setup is under a tick: before the 7-tick floor the firmware faulted here (PAST)."""
    from recording import DATA, i2c_transactions
    from esctape.export import read_sr
    name = "i2c_x24c02_dual_tds744a.sr"
    probes = {v: int(k[5:]) - 1 for k, v in meta(name).items() if k.startswith("probe")}
    trs = i2c_transactions(read_sr(DATA / name, clock_hz=500_000)[0], probes["SCL"], probes["SDA"])
    mem, ptr = {}, None                                  # what the real 0x50 sent, by word
    for tr in trs:
        bs = tr["bytes"]
        if bs and bs[0][0] == 0xA0 and len(bs) > 1:
            ptr = bs[1][0]
        elif bs and bs[0][0] == 0xA1:
            for b, _, _ in bs[1:]:
                mem.setdefault(ptr, b)
                ptr = (ptr + 1) & 0xFF
    assert sorted(mem) == list(range(8, 256))
    want, got, differ, m = _i2c_eeprom_beside(name, 500, 3000, mem)
    assert not m.ff.valid and differ == 0
    assert len(want) == 14 and [t["bytes"][0][0] for t in want].count(0xA4) == 6
    assert [_i2c_view(t) for t in got] == [_i2c_view(t) for t in want]


def _can_rx_firmware(name, until_s=None):
    """can_rx (listen-only) at 12.5 MHz, 125 kbit/s, on an MCP2515's CAN_RX pin: the
    frames it pushed as (extended, identifier, data), "CRC" for a frame that failed."""
    from pathlib import Path
    from escasm import assemble
    from escmodels import Board, HostScript
    from esciss import Machine
    from recording import Recording, compress_idle
    khz = 12500                                          # 100 ticks per bit
    fw = Path(__file__).resolve().parents[2] / "firmware"
    im = assemble(f".equ can_rx.CLK_KHZ, {khz}\n.equ can_rx.KBPS, 125\n"
                  f'.include "can_rx.asm"\n', base_dir=fw)
    rec = Recording(name, 1e6 / khz, {2: 1})             # probe 3: CAN_RX
    recs = rec.records if until_s is None else [r for r in rec.records if r[0] < until_s * khz * 1000]
    rec.records = compress_idle(recs, khz // 10)          # idle cut to 100 us, 12.5 bits
    rec.end, rec.level = rec.records[-1][0], rec.records[0][1]
    host = HostScript([], drain=[1], poll=khz // 10)
    m = Machine(im, board=Board(rec, host))
    host.machine = m
    m.run(rec.end + khz, stop_when_idle=False)
    assert not m.ff.valid
    w, out, i = host.received[1], [], 0
    while i < len(w):
        if w[i] == 0x20000000:
            out.append("CRC")
            i += 1
            continue
        rtr, dlc = (w[i] >> 30) & 1, w[i + 1]
        n = 0 if rtr or dlc == 0 else 1 if dlc <= 4 else 2
        data = b"".join(x.to_bytes(4, "big") for x in w[i + 2:i + 2 + n])[:min(dlc, 8)]
        out.append((w[i] >> 31, w[i] & 0x1FFFFFFF, data))
        i += 2 + n
    return out


CAN_CYCLE = [(1, 0x14611234, bytes(range(4))), (0, 0x110, b"\x00\x11"),
             (0, 0x550, bytes.fromhex("aabbccddeeff0a0b"))]


@pytest.mark.parametrize("name,want", [
    ("can_mcp2515_125k_msg_222_5bytes.sr", [(0, 0x222, bytes.fromhex("0011223344"))] * 3),
    ("can_mcp2515_125k_extmsg_11223344_7bytes.sr",
     [(1, 0x11223344, bytes.fromhex("00112233445566"))] * 5),
    ("can_mcp2515_125k_bus_load_25percent.sr", (CAN_CYCLE * 5)[:14])])
def test_can_rx_firmware_on_a_real_bus(name, want):
    """A Microchip MCP2515DM-BM demo board at 125 kbit/s (its MCP2515's CAN_RX pin, 4 MHz):
    can_rx reads every frame, standard and extended, each passing its CRC-15, as the
    files are named (0x222 with five bytes, 0x11223344 with seven) and, at 25% bus load,
    the board's three messages in turn."""
    assert _can_rx_firmware(name) == want


def test_can_rx_firmware_at_full_bus_load():
    """The same board at 100% bus load: frames back to back, three bits of intermission
    apart. The first 0.6 s: every frame read, none failing its CRC, the three messages
    in unbroken turn (a missed frame would break it)."""
    got = _can_rx_firmware("can_mcp2515_125k_bus_load_100percent.sr", until_s=0.6)
    assert len(got) >= 50 and "CRC" not in got
    k = CAN_CYCLE.index(got[0])
    assert got == (CAN_CYCLE * 40)[k:k + len(got)]


@pytest.mark.parametrize("name,ch,frames,repeats", [
    ("nec_enter.sr", 0, 5, 0), ("nec_all.sr", 0, 21, 30), ("nec_extended.sr", 0, 7, 4),
    ("nec_matsui.sr", 6, None, None)])
def test_nec_rx_firmware_on_real_remotes(name, ch, frames, repeats):
    """nec_rx (ISS, 1 MHz) on real remotes' demodulated IR (a Joy-IT remote, every button
    and one button pressed five times; a ceiling light's remote with a 16-bit address; a
    Hama universal remote as a Matsui TV's, outside the 5% that sigrok's decoder allows):
    the frames and repeat codes it reports are the ones esclearn reads from the same
    recording (sw/esclearn, a separate decoder that learns the coding from the timings)."""
    import test_esclearn
    from pathlib import Path
    from escasm import assemble
    from escmodels import Board, HostScript
    from esciss import Machine
    from recording import Recording, compress_idle
    khz = 1000
    fw = Path(__file__).resolve().parents[2] / "firmware"
    im = assemble(f'.equ nec_rx.CLK_KHZ, {khz}\n.include "nec_rx.asm"\n', base_dir=fw)
    rec = Recording(name, 1e6 / khz, {ch: 1})
    rec.records = compress_idle(rec.records, 120 * khz)     # idle cut to 120 ms
    rec.end = rec.records[-1][0]
    host = HostScript([], drain=[1], poll=khz)
    m = Machine(im, board=Board(rec, host))
    host.machine = m
    m.run(rec.end + 200 * khz, stop_when_idle=False)
    assert not m.ff.valid
    got = host.received[1]
    learned = test_esclearn.dump(name, ch)[2]
    want = []
    for (w,) in learned.frames:
        a, a2, cmd = w & 0xFF, w >> 8 & 0xFF, w >> 16 & 0xFF
        want.append(cmd << 16 | (a if a2 == a ^ 0xFF else 1 << 24 | a2 << 8 | a))
    assert [x for x in got if x != 0x80000000] == want
    assert got.count(0x80000000) == learned.other
    if frames is not None:
        assert (len(want), learned.other) == (frames, repeats)


@pytest.mark.skipif(SIGROK is None, reason="sigrok-cli not installed")
@pytest.mark.parametrize("name", ["can_mcp2515_125k_msg_222_5bytes.sr",
                                  "can_mcp2515_125k_extmsg_11223344_7bytes.sr",
                                  "can_mcp2515_125k_bus_load_25percent.sr"])
def test_can_rx_against_sigrok(name):
    """sigrok's can decoder finds as many start-of-frames in the recording as frames can_rx
    reads, and the same extended identifiers. (On the 100% bus-load file, too long to run
    here: 286 and 286, 96 of them 0x14611234.)"""
    def ann(cls):
        r = subprocess.run([SIGROK, "-i", str(DATA / name), "-P",
                            "can:can_rx=CAN_RX:nominal_bitrate=125000", "-A", f"can={cls}"],
                           capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, r.stderr
        return r.stdout.splitlines()
    got = _can_rx_firmware(name)
    assert len(ann("sof")) == len(got) and not ann("warnings")
    ext = [int(ln.split("(0x")[1].rstrip(")"), 16) for ln in ann("full-id") if "(0x" in ln]
    assert ext == [i for e, i, _ in got if e]


# A custom 1-Wire master on an STM32F103 with two DS18B20s (1 MHz capture): Search ROM
# four times, then for each sensor Match ROM and Read Scratchpad (and a Write and a Copy
# Scratchpad sent with no reset first, which the sensors ignore), Convert T for both, and
# Read Scratchpad again.
DS18B20_A = bytes.fromhex("28ee94f72716018d")            # as the recording's notes give them
DS18B20_B = bytes.fromhex("28ee875425160233")


def _onewire_beside_b(khz, chip=True):
    """onewire_dev in sensor A's place, beside the real B: the recording drives DQ (the
    master's pulses, and B wherever B pulls low) except where only A would pull it low: its
    presence after each reset, the Search ROM bits where A's bit, not B's, is 0, and A's
    scratchpad. Returns (checks, failures): each check is the level at the master's sample
    point, 13 us into a slot, or 70 us after a reset for the presence."""
    import bisect
    from pathlib import Path
    from escasm import assemble
    from escmodels import Board
    from esciss import Machine
    from esctape.export import read_sr
    from recording import DATA, compress_idle
    us = khz // 1000
    recs = compress_idle(read_sr(DATA / "onewire_2xds18b20_stm32.sr", clock_hz=khz * 1000)[0], 5000 * us)
    lows, prev, t0 = [], 1, None
    for t, v in recs:
        if not v & 1 and prev:
            t0 = t
        if v & 1 and not prev:
            lows.append((t0, t - t0))
        prev = v & 1
    tx = []
    for s, d in lows:
        if d >= 400 * us:
            tx.append({"reset": (s, d), "slots": []})
        elif tx and s - sum(tx[-1]["reset"]) > 80 * us:        # not the presence pulse
            tx[-1]["slots"].append((s, d))
    bit = lambda d: 0 if d > 15 * us else 1
    rom_bit = lambda r, k: (r[k // 8] >> k % 8) & 1
    free, checks, alone = [], [], []        # windows where A, or the chip, drives; checks
    for tr in tx:
        rs, rd = tr["reset"]
        free.append((rs + rd, 300 * us))
        checks.append((rs + rd + 70 * us, 0, "presence"))
        sl = tr["slots"]
        bits = [bit(d) for _, d in sl]
        cmd = sum(bits[j] << j for j in range(8))
        if cmd == 0xF0:
            a_on = b_on = True
            for k in range(64):
                for j, comp in ((8 + 3 * k, 0), (9 + 3 * k, 1)):
                    a0, b0 = a_on and rom_bit(DS18B20_A, k) == comp, b_on and rom_bit(DS18B20_B, k) == comp
                    s, d = sl[j]
                    if not a0 and not b0:
                        alone.append(d)
                        checks.append((s + 13 * us, 1, "search"))
                    elif not b0:
                        free.append((s, 70 * us))
                        checks.append((s + 13 * us, 0, "search"))
                a_on &= rom_bit(DS18B20_A, k) == bits[10 + 3 * k]
                b_on &= rom_bit(DS18B20_B, k) == bits[10 + 3 * k]
        elif cmd == 0x55:
            rom = bytes(sum(bits[8 + 8 * n + j] << j for j in range(8)) for n in range(8))
            mine = rom == DS18B20_A and sum(bits[72 + j] << j for j in range(8)) == 0xBE
            for j in range(80, len(sl)):
                s, d = sl[j]
                if mine and j < 152:
                    free.append((s, 70 * us))
                checks.append((s + 13 * us, bit(d), "scratchpad" if j < 152 else "after"))
    pulse = sorted(alone)[len(alone) // 2]                     # the master's own read pulse
    starts = sorted([tr["reset"][0] for tr in tx] + [s for tr in tx for s, _ in tr["slots"]])
    win = []
    for s, n in sorted(free):
        k = bisect.bisect_right(starts, s)
        end = min(s + n, starts[k] if k < len(starts) else s + n)
        win.append((s + (pulse if n == 70 * us else 0), end))   # keep the master's pulse

    class Replay(Device):
        def __init__(self):
            self.k, self.v, self.w = 0, recs[0][1], 0

        def step(self, t, bus):
            while self.k < len(recs) and recs[self.k][0] <= t:
                self.v = recs[self.k][1]
                self.k += 1
            while self.w < len(win) and win[self.w][1] <= t:
                self.w += 1
            off = self.w < len(win) and win[self.w][0] <= t < win[self.w][1]
            return (0 if self.v & 1 or off else 1), 0

    board = Board(Replay())
    end = recs[-1][0] + 1000 * us
    if chip:
        fw = Path(__file__).resolve().parents[2] / "firmware"
        im = assemble(f'.equ onewire_dev.CLK_KHZ, {khz}\n.include "onewire_dev.asm"\n', base_dir=fw)
        rom, sp = im.symbols["onewire_dev.ROM"], im.symbols["onewire_dev.SP"]
        for n, b in enumerate(DS18B20_A[:7] + b"\0"):           # the chip computes the CRC
            im.tape[rom + n] = b
        for n, b in enumerate(bytes.fromhex("82014b467fff0c1000")):
            im.tape[sp + n] = b
        m = Machine(im, board=board)
        m.run(end, stop_when_idle=False)
        assert not m.ff.valid
    else:
        for t in range(end):
            board(t, 0, 0)
    lv = board.level(0)
    return checks, [(t, w, what) for t, w, what in checks if lv[t] != w]


def test_onewire_dev_firmware_in_a_real_sensors_place():
    """onewire_dev (ISS, 25 MHz) as DS18B20 A, beside the real B, under the STM32's own
    timing: its presence at every reset, its half of four Search ROMs (the bits where A
    alone pulls DQ low), and its scratchpad twice (24.125 C, TH 4B, TL 46, 12-bit, and the
    CRC-8 the chip computes: E1), bit for bit at the master's sample points; and DQ left
    alone while B is read and while the master sends the Write and Copy Scratchpad the sensors ignore. Without
    the chip the same checks fail (presence and A's zeros missing)."""
    checks, bad = _onewire_beside_b(25000)
    kinds = [w for _, _, w in checks]
    assert (kinds.count("presence"), kinds.count("scratchpad")) == (10, 288)   # A and B, twice
    assert bad == [], bad[:5]
    _, without = _onewire_beside_b(25000, chip=False)
    assert {w for _, _, w in without} == {"presence", "search", "scratchpad"}


def _spi_bytes(name):
    """MOSI and MISO bytes of an SD card in SPI mode 0 (sampled on SCK's rising edge while
    CS is low), from the recording's probe names."""
    from esctape.export import read_sr
    from recording import DATA
    pr = {v.rstrip("#"): int(k[5:]) - 1 for k, v in meta(name).items() if k.startswith("probe")}
    clk, cs = pr.get("CLK", pr.get("SCK")), pr["CS"]
    mo, mi, prev, bo, bi, n = [], [], None, 0, 0, 0
    for _, v in read_sr(DATA / name)[0]:
        if (v >> cs) & 1:
            bo = bi = n = 0
        elif prev is not None and (v >> clk) & 1 and not (prev >> clk) & 1:
            bo, bi, n = bo << 1 | (v >> pr["MOSI"]) & 1, bi << 1 | (v >> pr["MISO"]) & 1, n + 1
            if n == 8:
                mo.append(bo)
                mi.append(bi)
                bo = bi = n = 0
        prev = v
    return mo, mi


def test_sd_card_model_checks_as_real_cards_do():
    """The SD card model's CRCs, by which sd_spi's on-chip CRC7 and CRC-16 are judged,
    against two real cards and their hosts (24 and 4 MHz captures): CMD0's CRC7 (0x95,
    the one a host in SPI mode must get right; the others send placeholders, as SPI mode
    allows), an xmore 512 MB card's CSD (its CRC7 field and its CRC-16) and four 512-byte
    blocks with their CRC-16, one of them starting "Sigrok rocks"."""
    import binascii
    from escmodels.sdcard import crc7
    mo, mi = _spi_bytes("sd_spi_xmore_512mb_read_3blocks.sr")
    k = mo.index(0x40)
    assert mo[k:k + 6] == [0x40, 0, 0, 0, 0, crc7(bytes([0x40, 0, 0, 0, 0])) << 1 | 1] == [0x40, 0, 0, 0, 0, 0x95]
    blocks = []
    for name in ("sd_spi_xmore_512mb_read_3blocks.sr", "sd_spi_cmd17_sigrok_rocks.sr"):
        mo, mi = _spi_bytes(name)
        tokens = [j for j in range(1, len(mi)) if mi[j] == 0xFE and mi[j - 1] == 0xFF]
        for j in tokens:
            n = 16 if name.startswith("sd_spi_xmore") and j == tokens[0] else 512   # CMD9: the CSD
            data = bytes(mi[j + 1:j + 1 + n])
            assert mi[j + 1 + n] << 8 | mi[j + 2 + n] == binascii.crc_hqx(data, 0)
            if n == 16:
                assert data[15] == crc7(data[:15]) << 1 | 1
            else:
                blocks.append(data)
    assert len(blocks) == 4 and blocks[-1].startswith(b"Sigrok rocks")


def test_max7219_model_on_an_arduino_driving_four_chips():
    """An Arduino UNO bit-banging four cascaded MAX7219s (2 MHz capture), running
    Sebski123/MAX72xx-Sigrok's Sigrok_max7219_test_data.ino: the chain model (which judges
    the max7219 firmware) latches every packet the sketch sends into the chip it is for,
    and flags exactly the sketch's three intentional errors: a write of three packets and
    one of five to a chain of four, and registers Dh and Eh, which Table 2 does not have.
    (At 500 ns a sample, the 25 to 100 ns timing limits are not exercised.)"""
    from escmodels.max7219 import Max7219Chain
    name = "max7219_arduino_4x_cascaded.sr"
    c = Max7219Chain(0, 1, 2, sample_ns(name), n=4)
    play_edges(name, sample_ns(name), {3: 0, 2: 1, 1: 2}, c, idle=1)   # CLK, MOSI, CS#
    four = lambda reg, d: [reg << 8 | d] * 4
    want = [four(0, 0),                                    # CS raised at power-up: no clock
            four(15, 1), four(9, 0), four(10, 7), four(11, 7), four(15, 0)] \
        + [four(r, 0) for r in range(1, 9)] + [four(12, 1)] \
        + [[0, 0, 0, 0x0C01], [0, 0, 0, 0]] \
        + [[0x0D06, 0x0E09, 0x0D06, 0x0E09], [0x0101, 0x0202, 0x0304, 0x0408],
           [0x0100, 0x0200, 0x0300, 0x0400]]
    assert c.latches == want
    assert [v.split(": ", 1)[1] for v in c.violations] == [
        "LOAD after 48 clocks (packets of 16 per device)", "LOAD after 80 clocks (packets of 16 per device)",
        "device 0 register Dh (not in Table 2)", "device 1 register Eh (not in Table 2)",
        "device 2 register Dh (not in Table 2)", "device 3 register Eh (not in Table 2)"]
    assert all(r["shutdown"] == 1 and r["intensity"] == 7 and r["scan"] == 7 and
               r["digits"] == [0] * 8 for r in c.regs)
