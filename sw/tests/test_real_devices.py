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
