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

from recording import DATA, meta, play, sample_ns

from escmodels.qi import QiTransmitter
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
# during the capture (both_orientations, CC1) and glitches that split a message.
PD = [
    ("pd_apple_av_hdmi.sr", 0, 84, 6), ("pd_apple_power_brick.sr", 0, 61, 0),
    ("pd_hdmi_dongle.sr", 1, 54, 0), ("pd_power_supply_20V.sr", 0, 42, 0),
    ("pd_thinkpad_anker_powerbank-both_orientations.sr", 2, 20, 24),
    ("pd_thinkpad_anker_powerbank-both_orientations.sr", 3, 21, 0),
    ("pd_thinkpad_aukey_45w.sr", 1, 8, 0),
    ("pd_thinkpad_passtrough_dongle-anker_powerbank.sr", 1, 138, 0),
    ("pd_zy12pds_65w_noname_supply.sr", 2, 10, 0), ("pd_zy12pds_anker_powerbank.sr", 2, 52, 1),
]


def _pd(name, ch):
    tick = sample_ns(name)
    s = UsbPdSink(0, tick, real_line=True)
    play(name, tick, {ch: 0}, s)
    s.finish()
    return s


@pytest.mark.parametrize("name,ch,n,damaged", PD)
def test_usb_pd_real_sources_and_sinks(name, ch, n, damaged):
    """MacBook, Pixel, ThinkPad and a ZY12PDS sink with their chargers and adapters: 490
    messages (85 of them SOP' to the cable), every CRC-32 good and every EOP present."""
    s = _pd(name, ch)
    assert len(s.messages) == n and all(m["crc_ok"] and m["eop"] for m in s.messages)
    assert len(s.violations) == damaged, s.violations[:5]


SIGROK = shutil.which("sigrok-cli")


@pytest.mark.skipif(SIGROK is None, reason="sigrok-cli not installed")
@pytest.mark.parametrize("name,ch,n,damaged", PD)
def test_usb_pd_against_sigrok(name, ch, n, damaged):
    """sigrok's usb_power_delivery decoder reads the same recording: as many messages with
    a good CRC as the model (a separate implementation on the same samples)."""
    cc = meta(name)[f"probe{ch + 1}"]
    r = subprocess.run([SIGROK, "-i", str(DATA / name), "-P", f"usb_power_delivery:cc1={cc}",
                        "-A", "usb_power_delivery=crc"], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr
    ok = [ln for ln in r.stdout.splitlines() if "CRC" in ln and "!" not in ln]
    assert len(ok) == n, r.stdout[-2000:]


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
