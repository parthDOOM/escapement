#!/usr/bin/env python3
"""Outside judges for the firmware (make judges): sigrok's protocol decoders read the pins of
every co-simulation case that has one, and must decode what the case asked for.

    python test/cosim/judges.py [--jobs J] [--show] [CASE ...]

The bus models that check the firmware in the co-simulation were written here, from the same
specifications as the firmware, so a misreading shared by both would pass. libsigrokdecode's
decoders were written by other people (sigrok-cli 0.7.2, the version CI installs). Each judge
runs one co-simulation case (fw_cases.FIRMWARE_CASES) on the ISS, records the level of every
pin in every tick as the bus models saw it, writes those levels as a VCD and runs a decoder
stack on it. The decoded payload must equal what the case's host or bus model asked for,
written into the judge below; the judge never reads a bus model's own decoding.

Every judge has a negative control. The same waveform with one pulse of its data line
inverted, in the middle of the traffic, must fail the same comparison: a judge that passes
whatever the pins do proves nothing.

--show prints each case's annotations, for writing a judge.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(REPO / "sw"), str(HERE)]

from esciss import Machine  # noqa: E402
from esciss.machine import no_board_b  # noqa: E402
from fw_cases import FIRMWARE_CASES, FW_EQU, fw_image  # noqa: E402

SIGROK = shutil.which("sigrok-cli")
# Decoders newer than 0.7.2 (ir_sirc, ir_irmp, sbus_futaba) and community ones (dcc), each at a
# pinned commit: test/cosim/outside_decoders.sh fetches them into OUTSIDE_PD
OUTSIDE_PD = Path(os.environ.get("OUTSIDE_PD", REPO / "build" / "outside-decoders"))


class Rec:
    """A board (t, out, oe) -> levels that keeps the levels it returns, one per tick."""

    def __init__(self, inner, b: bool = False):
        self.inner, self.b, self.levels = inner, b, []

    def __call__(self, t, *a):
        v = self.inner(t, *a)
        if t >= 0:
            self.levels.append(v)
        return v


@dataclass
class Judge:
    case: str
    stack: list[str]                       # -P arguments, decoder:options, bottom first
    channels: dict[str, int]               # VCD name -> pin (bank A 0-7, bank B 8-12)
    want: Callable[[list[str]], bool]      # the annotation lines -> pass
    flip: str                              # the channel whose pulse the control inverts
    ann: str = ""                          # -A filter (decoder=class:class,...); "" = all
    note: str = ""
    # channel -> levels -> levels, applied before decoding (an IR receiver's envelope)
    transform: dict = field(default_factory=dict)
    # channels -> [channels, ...]: decoded one by one and the lines joined (see ps2_frames)
    pieces: Callable | None = None
    flip_at: float = 0.5                   # where in the line's edges the control's pulse is
    # "" = sigrok-cli 0.7.2's own decoders; "upstream" = libsigrokdecode's decoders at the commit
    # outside_decoders.sh pins, on sigrok-cli 0.7.2's runtime, with the community decoders beside
    tree: str = ""
    # instead of a sigrok stack: (channels, tick_ns) -> annotation lines, an outside program
    # (tshark) or a published table (VESA) doing the reading
    tool: Callable | None = None
    ticks: int = 0                         # run the case this long instead (0 = its own length)
    # decode from a sigrok session file made from the VCD: a decoder that reads the sample
    # rate in start() (swim) gets it only from a session file's header
    sr: bool = False


def clock_khz(name: str, image) -> int:
    if "CLK_KHZ" in FW_EQU.get(name, {}):
        return FW_EQU[name]["CLK_KHZ"]
    ks = [v for s, v in (getattr(image, "symbols", {}) or {}).items() if s.split(".")[-1] == "CLK_KHZ"]
    return ks[0] if ks else 25000


def run_case(name: str, run_ticks: int = 0) -> tuple[dict[int, list[int]], float]:
    """The case on the ISS: every pin's level per tick (bank B as pins 8-12), and the tick.
    `run_ticks` runs it longer than the co-simulation does (a judge that needs the next frame)."""
    fw, ticks, factory = FIRMWARE_CASES[name]
    ticks = run_ticks or ticks
    image = fw_image(name, fw, REPO / "firmware")
    tick_ns = 1e6 / clock_khz(name, image)
    board, host, check, *bb = factory()
    ra = Rec(board)
    rb = Rec(bb[0]) if bb else None
    m = Machine(image, board=ra, board_b=rb or no_board_b, half=getattr(board, "half", None),
                record=False)
    if host is not None:
        host.machine = m
    m.run(ticks, stop_when_idle=False)
    pins = {p: [(v >> p) & 1 for v in ra.levels] for p in range(8)}
    if rb is not None:
        pins.update({8 + p: [(v >> p) & 1 for v in rb.levels] for p in range(5)})
    return pins, tick_ns


def vcd(channels: dict[str, list[int]], tick_ns: float) -> str:
    """Per-tick levels as a VCD in 10 ns units (a tick that is not a multiple of 10 ns is
    rounded per edge, under 5 ns of jitter); idle padding at the end, so a decoder that
    closes a packet on idle sees the last one end."""
    ids = {name: chr(33 + k) for k, name in enumerate(channels)}
    out = ["$timescale 10 ns $end", "$scope module chip $end"]
    out += [f"$var wire 1 {ids[n]} {n} $end" for n in channels]
    out += ["$upscope $end", "$enddefinitions $end"]
    n = max(len(v) for v in channels.values())
    prev: dict = {}
    for t in range(n):
        ch = [(k, v[t]) for k, v in channels.items() if prev.get(k) != v[t]]
        if ch:
            out.append(f"#{round(t * tick_ns / 10)}")
            out += [f"{v}{ids[k]}" for k, v in ch]
            prev.update(dict(ch))
    out.append(f"#{round((n + n // 10 + 1000) * tick_ns / 10)}")
    return "\n".join(out) + "\n"


def decode(text: str, j: Judge) -> list[str]:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "run.vcd"
        p.write_text(text, newline="\n")
        cmd = [SIGROK, "-I", "vcd", "-i", str(p)]
        if j.sr:
            srf = Path(d) / "run.sr"
            subprocess.run(cmd + ["-o", str(srf)], capture_output=True, text=True, timeout=600)
            cmd = [SIGROK, "-i", str(srf)]
        cmd += ["-P", ",".join(j.stack)]
        if j.ann:
            cmd += ["-A", j.ann]
        envv = None
        if j.tree == "upstream":
            envv = dict(os.environ, SIGROKDECODE_DIR=str(OUTSIDE_PD / "decoders"),
                        LD_LIBRARY_PATH=str(OUTSIDE_PD / "lib"))
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=envv)
    if r.returncode != 0:
        return [f"ERROR sigrok-cli: {r.stderr.strip()[:300]}"]
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def flipped(levels: list[int], at: float = 0.5) -> list[int]:
    """The control: one pulse of the line inverted, the one between the two edges at
    fraction `at` of its edges (the middle of the traffic by default)."""
    edges = [t for t in range(1, len(levels)) if levels[t] != levels[t - 1]]
    if len(edges) < 3:
        return [1 - v for v in levels]
    i = min(int(len(edges) * at), len(edges) - 2)
    a, b = edges[i], edges[i + 1]
    return levels[:a] + [1 - v for v in levels[a:b]] + levels[b:]


def judge(i: int, show: bool = False):
    """JUDGES[i] (by index: a judge's lambdas do not cross to a worker process)."""
    j = JUDGES[i]
    pins, tick_ns = run_case(j.case, j.ticks)
    chans = {n: pins[p] for n, p in j.channels.items()}
    for n, f in j.transform.items():
        chans[n] = f(chans[n])

    def run(ch):
        if j.tool:
            return j.tool(ch, tick_ns)
        parts = j.pieces(ch) if j.pieces else [ch]
        return [x for p in parts for x in decode(vcd(p, tick_ns), j)]
    lines = run(chans)
    if show:
        return j.case, lines, None, None
    ok = not any(x.startswith("ERROR") for x in lines) and j.want(lines)
    ctl = dict(chans)
    ctl[j.flip] = flipped(chans[j.flip], j.flip_at)
    clines = run(ctl)
    caught = not j.want(clines)
    return j.case, lines, ok, caught


# ------------------------------------------------------------------- outside tools, not sigrok
RTL_433 = shutil.which("rtl_433")


def pulses_us(levels: list[int], tick_ns: float) -> list[tuple[float, float]]:
    """The line as (high, low) pairs in microseconds, from its first rising edge: an OOK
    transmitter's baseband, the carrier on while high. The last gap runs to the end."""
    runs, t, v = [], 0, levels[0]
    for k in range(1, len(levels) + 1):
        if k == len(levels) or levels[k] != v:
            runs.append((v, (k - t) * tick_ns / 1e3))
            if k < len(levels):
                t, v = k, levels[k]
    while runs and runs[0][0] == 0:
        runs.pop(0)
    out = []
    for i in range(0, len(runs), 2):
        hi = runs[i][1]
        lo = runs[i + 1][1] if i + 1 < len(runs) else 0.0
        out.append((hi, lo))
    return out


def rtl433(channel: str, decoders: list[int], keys: tuple = (), gap_us: float = 20000):
    """rtl_433's decoders (-R n) on the channel's pulses, read from a pulse-data file (.ook,
    rtl_433's own text format for demodulated OOK); each JSON event a line of key=value,
    sorted (or only `keys`, in that order: rtl_433's versions differ in the extra keys they
    print), without its time. The last gap is stretched to `gap_us`, so the
    final packet ends."""
    def tool(ch: dict, tick_ns: float) -> list[str]:
        import json
        ps = pulses_us(ch[channel], tick_ns)
        if not ps:
            return []
        ps[-1] = (ps[-1][0], max(ps[-1][1], gap_us))
        body = ";pulse data\n;version 1\n;timescale 1us\n"
        body += "".join(f"{round(h)} {round(lo)}\n" for h, lo in ps) + ";end\n"
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "run.ook"
            f.write_text(body, newline="\n")
            cmd = [RTL_433, "-q", "-F", "json", "-M", "level:no", "-M", "protocol"]
            for r in decoders:
                cmd += ["-R", str(r)]
            r = subprocess.run(cmd + ["-r", str(f)], capture_output=True, text=True, timeout=600)
        if r.returncode != 0 and not r.stdout.strip():
            return [f"ERROR rtl_433: {r.stderr.strip()[:300]}"]
        out = []
        for line in r.stdout.splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            ks = [k for k in keys if k in e] if keys else sorted(k for k in e if k not in ("time", "mod"))
            out.append(" ".join(f"{k}={e[k]}" for k in ks))
        return out
    return tool


def rcswitch(channel: str):
    """rc-switch's receiver (OUTSIDE_PD/bin/rcswitch_rx, from test/cosim/rcswitch_rx.cpp) on the
    channel's edges, in microseconds. The library takes a code when the gap after it ends; the
    line idles low after the trailing sync, so its gap is closed by one edge after as long as the
    leading sync's gap, as the next transmission's sync would."""
    def tool(ch: dict, tick_ns: float) -> list[str]:
        lv = ch[channel]
        edges = [k for k in range(1, len(lv)) if lv[k] != lv[k - 1]]
        if len(edges) < 3:
            return []
        edges.append(edges[-1] + (edges[2] - edges[1]))
        us = "".join(f"{round(k * tick_ns / 1e3)}\n" for k in edges)
        r = subprocess.run([str(OUTSIDE_PD / "bin" / "rcswitch_rx")], input=us, capture_output=True,
                           text=True, timeout=60)
        if r.returncode != 0:
            return [f"ERROR rcswitch_rx: {r.stderr.strip()[:300]}"]
        return r.stdout.splitlines()
    return tool


TSHARK = shutil.which("tshark")
TSHARK_FIELDS = ["eth.dst", "eth.src", "eth.type", "eth.fcs.status", "ip.src", "ip.dst", "ip.len",
                 "ip.checksum.status", "udp.srcport", "udp.dstport", "udp.length", "data.data"]


def manchester_frames(channel: str, invert: bool = True):
    """10BASE-T frames off a line: sigrok's ook decoder undoes the Manchester code (IEEE 802.3's
    sense, a 1 rising mid-bit, is its inverted one), each frame up to its end mark; the bits
    LSB first into bytes, the 7 preamble bytes and the SFD checked and dropped. Each frame's
    bytes (with its FCS) as hex, or an error line."""
    j = Judge("eth", [f"ook:data={channel}:decodeas=Manchester:preamble=1010:invert={'yes' if invert else 'no'}"],
              {}, None, "", "")

    def frames(ch: dict, tick_ns: float) -> list[bytes | str]:
        out, bits = [], []
        for x in text(decode(vcd({channel: ch[channel]}, tick_ns), j)) + ["end"]:
            if x in ("0", "1"):
                bits.append(int(x))
                continue
            if x.startswith("ERROR"):
                return [x]
            if bits:
                n = len(bits) // 8
                bs = bytes(sum(bits[8 * i + k] << k for k in range(8)) for i in range(n))
                out.append(bs[8:] if bs[:8] == b"\x55" * 7 + b"\xd5" else f"no preamble: {bs[:8].hex()}")
                bits = []
        return out
    return frames


def tshark_frames(channel: str):
    """Wireshark's dissectors (tshark) on each frame manchester_frames reads, written to a pcap
    with its FCS; tshark checks the FCS and the IPv4 header checksum. One line per frame of
    the fields TSHARK_FIELDS, as name=value."""
    read = manchester_frames(channel)

    def tool(ch: dict, tick_ns: float) -> list[str]:
        import struct
        fs = read(ch, tick_ns)
        bad = [f for f in fs if isinstance(f, str)]
        if bad:
            return [f"ERROR {b}" if not b.startswith("ERROR") else b for b in bad]
        pcap = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
        for k, f in enumerate(fs):
            pcap += struct.pack("<IIII", k, 0, len(f), len(f)) + f
        with tempfile.TemporaryDirectory() as d:
            pf = Path(d) / "run.pcap"
            pf.write_bytes(pcap)
            cmd = [TSHARK, "-r", str(pf), "-o", "eth.fcs:always", "-o", "eth.check_fcs:TRUE",
                   "-o", "ip.check_checksum:TRUE", "-T", "fields", "-E", "separator=|"]
            for f in TSHARK_FIELDS:
                cmd += ["-e", f]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            return [f"ERROR tshark: {r.stderr.strip()[:300]}"]
        return [" ".join(f"{n}={v}" for n, v in zip(TSHARK_FIELDS, line.split("|")) if v)
                for line in r.stdout.splitlines()]
    return tool


# --------------------------------------------------------------------------- the judges
def text(lines: list[str]) -> list[str]:
    """Each annotation line without its decoder prefix ("uart-1: 45" -> "45")."""
    return [x.split(": ", 1)[1] if ": " in x else x for x in lines]


def eq(want: list[str]):
    """The annotations are exactly `want`, in order."""
    return lambda lines: text(lines) == want


def hexes(bs) -> list[str]:
    return [f"{b:02X}" for b in bs]


def i2c_lines(transactions) -> list[str]:
    """i2c's address and data annotations for write transactions [(addr7, [bytes])]."""
    out = []
    for a, bs in transactions:
        out += ["Write", f"Address write: {a:02X}"] + [f"Data write: {b:02X}" for b in bs]
    return out


def env(gap: int):
    """An IR receiver module's output for the carrier on the pin (active low), as
    sw/tests/sigrok_judge.py makes it for the RC-6 judge."""
    def f(levels):
        sys.path.insert(0, str(REPO / "sw" / "tests"))
        from sigrok_judge import envelope
        return envelope(levels, gap)
    return f


def env_idle(gap: int, ticks: int):
    """env(gap), then `ticks` of idle: IRMP tells some protocols apart (JVC from NEC) only by
    the pause after the last bit, longer than the case runs."""
    def f(levels):
        e = env(gap)(levels)
        return e + [e[-1]] * ticks
    return f


def undriven_low(levels: list[int]) -> list[int]:
    """A WS2812 data line has no pull-up, but the case's board pulls every pin up: until the
    chip first drives the line low, take it as low (else the decoder starts on a false bit)."""
    k = levels.index(0, 1) if 0 in levels[1:] else 0
    return [0] * k + levels[k:]


def ps2_frames(ch: dict) -> list[dict]:
    """libsigrokdecode 0.5.3's ps2 emits a frame only at the next frame's first falling clock
    edge, and drops that edge: back to back frames never all decode, and the last one never
    does. So each frame (split where the clock idles high past 1.5 bit times) is decoded on its
    own, followed by one more clock pulse with the data line high."""
    clk, dat = ch["clk"], ch["data"]
    falls = [t for t in range(1, len(clk)) if clk[t - 1] and not clk[t]]
    if not falls:
        return [ch]
    bit = min(b - a for a, b in zip(falls, falls[1:])) if len(falls) > 1 else 1000
    starts = [falls[0]] + [b for a, b in zip(falls, falls[1:]) if b - a > 1.5 * bit]
    out = []
    for i, s in enumerate(starts):
        a = max(0, s - bit)
        e = (starts[i + 1] - bit) if i + 1 < len(starts) else len(clk)
        c, d = clk[a:e], dat[a:e]
        c = c + [1] * bit + [0] * (bit // 2) + [1] * bit
        d = d + [1] * (2 * bit + bit // 2)
        out.append({"clk": c, "data": d})
    return out


def ssd1306_picture(lines: list[str], setup=None):
    """The display RAM that the decoded I2C writes leave on an SSD1306 (the datasheet's
    addressing, escmodels.ssd1306), from `setup` (a display already set up) on."""
    from escmodels.ssd1306 import Ssd1306
    oled = setup or Ssd1306(2, 3, 0x3C)
    cur = None
    for x in text(lines):
        if x == "Write":
            cur = None
        elif x.startswith("Address write: "):
            cur = ["S", ("addr", int(x.split(": ")[1], 16) << 1, True)]
            oled.transactions.append(cur)
        elif x.startswith("Data write: ") and cur is not None:
            cur.append(("w", int(x.split(": ")[1], 16)))
    for tr in oled.transactions:
        if tr[-1] != "P":
            tr.append("P")
    return oled.state()[0]


def oled_title_ok(lines: list[str]) -> bool:
    import fw_cases as F
    om, _ = F._oled_models()
    title, ram = om.title().ram, ssd1306_picture(lines)
    bad = any(x.startswith(("NACK", "Warning")) or "warn" in x.lower() for x in text(lines))
    drawn = sum(1 for p in range(8) for x in range(128) if title[p][x] and ram[p][x] == title[p][x])
    wrong = sum(1 for p in range(8) for x in range(128) if ram[p][x] and ram[p][x] != title[p][x])
    return not bad and drawn >= 40 and wrong == 0


def oled_game_ok(lines: list[str]) -> bool:
    import copy
    import fw_cases as F
    om, _ = F._oled_models()
    start = F._oled_start()
    if any(x.startswith("NACK") for x in text(lines)):
        return False
    ram = ssd1306_picture(lines, F._oled_display(om.court(start)))
    model = copy.deepcopy(start)
    for _ in range(20):
        model.step(F.OLED_PRESS)
        if model.frame >= start.frame + 5 and om.court(model).ram == ram:
            return True
    return False


def can_ok(frames, allowed=("Identifier bits 10..4 must not be all recessive",)):
    """CAN frames [(id line, [data bytes])]; the only warning allowed is the decoder's on
    identifier 0x7FF, which the cases send (ISO 11898-1 forbids standard identifiers whose
    seven high bits are all recessive; a controller does not check it)."""
    def f(lines):
        t = [x for x in text(lines) if not x.startswith("CRC-15 sequence")]
        want = []
        for ident, data in frames:
            want.append(ident)
            if ident.endswith("(0x7ff)"):
                want.append(allowed[0])
            want += [f"Data byte {i}: 0x{b:02x}" for i, b in enumerate(data)]
        return t == want
    return f


def can_rx_ok(lines):
    """can_rx's three frames from the bus model: the identifiers, lengths and data. (0.5.3
    shows a remote frame's CRC bits as data bytes: after the remote frame's length the
    rest is not compared.)"""
    t = text(lines)
    want = ["Identifier: 291 (0x123)", "Remote transmission request: data frame", "Data length code: 2",
            "Data byte 0: 0x11", "Data byte 1: 0x22",
            "Identifier: 1711 (0x6af)", "Full Identifier: 448585234 (0x1abcde12)",
            "Remote transmission request: data frame", "Data length code: 8"]
    want += [f"Data byte {i}: 0x{i + 1:02x}" for i in range(8)]
    want += ["Identifier: 2047 (0x7ff)", "Identifier bits 10..4 must not be all recessive",
             "Remote transmission request: remote frame", "Data length code: 3"]
    return t[:len(want)] == want


def onewire_ok(lines) -> bool:
    """Reset and presence, Read ROM (0x33, LSB first), then the device's ROM bits as read,
    at least its family code."""
    import fw_cases as F
    t = text(lines)
    if t[:1] != ["Reset/presence: true"] or "ROM command: 0x33 'Read ROM'" not in t:
        return False
    bits = [x.split(": ")[1] for x in t if x.startswith("Bit: ")]
    cmd = [str((0x33 >> i) & 1) for i in range(8)]
    rom = [str((b >> i) & 1) for b in F.ROM for i in range(8)]
    rest = bits[8:]
    return bits[:8] == cmd and len(rest) >= 8 and rest == rom[:len(rest)]


def wiegand_bits(word: int, n: int = 26) -> list[str]:
    return [str((word >> (n - 1 - i)) & 1) for i in range(n)]


def lin_checksum(pid: int, data: list[int]) -> int:
    s = pid
    for b in data:
        s += b
        s = (s & 0xFF) + (s >> 8)
    return ~s & 0xFF


def _wiegand26():
    v = 0x12 << 16 | 0x3456
    return (bin(v >> 12).count("1") & 1) << 25 | v << 1 | (1 - (bin(v & 0xFFF).count("1") & 1))


def _mem(a):                         # spi_target's tape memory (fw_cases.eeprom_tape)
    return (a * 7 + 3) & 0xFF


def _burst():
    import fw_cases as F
    data = [(F.burst_word(a) >> s) & 0xFF for a in range(10, 13) for s in (24, 16, 8, 0)]
    return i2c_lines([(0x3C, [0x00, 0x20, 0x00]), (0x3C, [0x00, 0x21, 40]), (0x3C, [0x00, 51]),
                      (0x3C, [0x00, 0x22, 2]), (0x3C, [0x00, 2]), (0x3C, [0x40] + data)])


def _dmx_ok(lines):
    """DMX512 as UART frames at 250 kbit/s (8N2): after the break, the start code 0 and the
    511 slots the tape holds. (libsigrokdecode 0.5.3's dmx512 decoder misreads frames sent
    back to back, so the UART decoder reads them.)"""
    import fw_cases as F
    t = [x for x in text(lines) if len(x) == 2]
    want = hexes(F.DMX_SLOTS)
    return any(t[i:i + len(want)] == want for i in range(len(t) - len(want) + 1))


U = "baudrate=115200"
UART = "uart=rx-data:rx-warnings"
I2C = (["i2c:scl=scl:sda=sda"], {"scl": 2, "sda": 3})
I2C_ANN = "i2c=address-read:address-write:data-read:data-write:nack:warnings"
I2C_WRITES = "i2c=address-write:data-write:nack:warnings"
SPI4 = {"sck": 4, "mosi": 5, "miso": 6, "cs": 7}
SPI3 = "spi:clk=sck:mosi=mosi:miso=miso:cs=cs:cpol=1:cpha=1"
CAN = "can=id:data:crc-sequence:warnings"
WS = "rgb_led_ws281x=rgb"


def rgb(grb: list[int]) -> list[str]:
    """The decoder's #rrggbb for each LED's 24 bits, sent G, R, B."""
    return [f"#{(v >> 8) & 0xFF:02x}{(v >> 16) & 0xFF:02x}{v & 0xFF:02x}" for v in grb]


def _i2c_rw(addr, reg, val):
    """A register write, then the register read back: write, then write + read (the
    controller NACKs the last byte it reads, as I2C asks)."""
    return ["Write", f"Address write: {addr:02X}", f"Data write: {reg:02X}", f"Data write: {val:02X}",
            "Write", f"Address write: {addr:02X}", f"Data write: {reg:02X}",
            "Read", f"Address read: {addr:02X}", f"Data read: {val:02X}", "NACK"]


def _dcc_ok(lines):
    """dcc_tx: one packet, address 3, 0x74 (speed and direction, forward), check byte 0x77:
    the preamble's 1s, the start bit, then each byte's bits MSB first and its separator."""
    bits = "".join(f"{b:08b}" + s for b, s in ((0x03, "0"), (0x74, "0"), (0x77, "1")))
    got = text([x for x in lines if not x.startswith("Sampling")])   # the decoder's own print
    want = (["1"] * 14 + ["0", "Preamble", "Start"] + list(bits)
            + ["LocoAddr:3", "Speed and Direction Instruction for forward operation", "CHECK: OK"])
    return got == want


def _servo_tool(ch: dict, tick_ns: float) -> list[str]:
    """sigrok's timing decoder on each servo channel alone; every other interval from the
    channel's first edge (a rising one: the lines idle low) is a pulse, "s3 1.500 ms"."""
    j = Judge("servo", ["timing:data=s:edge=any"], {}, None, "", "timing=time")
    out = []
    for k in range(8):
        lines = text(decode(vcd({"s": ch[f"s{k}"]}, tick_ns), j))
        out += [x if x.startswith("ERROR") else f"s{k} {x.split(' (')[0]}" for x in lines[::2]]
    return out


def _servo_ok(lines):
    """servo: every pulse on every channel 1.500 ms, the 1.5 MHz tick's exact 2250 ticks,
    except channel 3's after the host's change: 2.000 ms."""
    for k in range(8):
        h = [x.split(" ", 1)[1] for x in lines if x.startswith(f"s{k} ")]
        rest = "2.000 ms" if k == 3 else "1.500 ms"
        if len(h) < 2 or h[0] != "1.500 ms" or any(x != rest for x in h[1:]):
            return False
    return not any(x.startswith("ERROR") for x in lines)


def _quad_ok(lines):
    """quadrature: 30 steps up, then 10 down, to the 20 the chip reported. graycode annotates
    the count held between two edges, so its 40 lines are the count before each step: 0 up to
    30, then back to 21; the 40th step, the tenth down, leaves 20."""
    c = [int(x) for x in text(lines)]
    return c == list(range(0, 31)) + list(range(29, 20, -1))


def _eth_lines(frames) -> list[str]:
    """tshark's line for each (frame bytes without FCS, FCS good): an IPv4 frame's header
    fields and payload, else the Ethernet header and the rest as data."""
    import struct
    out = []
    for f, good in frames:
        mac = lambda b: ":".join(f"{x:02x}" for x in b)  # noqa: E731
        typ = struct.unpack("!H", f[12:14])[0]
        line = f"eth.dst={mac(f[0:6])} eth.src={mac(f[6:12])} eth.type=0x{typ:04x} eth.fcs.status={int(good)}"
        rest = f[14:]
        if typ == 0x0800:
            ip = rest
            line += (f" ip.src={'.'.join(map(str, ip[12:16]))} ip.dst={'.'.join(map(str, ip[16:20]))}"
                     f" ip.len={struct.unpack('!H', ip[2:4])[0]} ip.checksum.status=1")
            rest = ip[20:struct.unpack("!H", ip[2:4])[0]]
            if ip[9] == 17:
                sp, dp, ul = struct.unpack("!HHH", rest[:6])
                line += f" udp.srcport={sp} udp.dstport={dp} udp.length={ul}"
                rest = rest[8:ul]
        out.append(line + (f" data.data={rest.hex()}" if rest else ""))
    return out


def _sbus_want() -> list[str]:
    """sbus: the case's two frames, header, 16 channels (37 k + 172 f), digital channels 17
    and 18, frame lost, failsafe (set in the second), footer."""
    out = []
    for f in range(2):
        out += ["0x0f"] + [str((37 * k + 172 * f) & 0x7FF) for k in range(16)]
        out += ["0", "0", "0", "1" if f else "0", "0x00"]
    return out


def _freq_ok(lines):
    """freq_meter: every whole period 10.0 us at 30 % duty (250 ticks, 75 high at 25 MHz), the
    chip's 2000 and 600 ticks per 8 periods; at least the two gates' 16 periods."""
    t = text(lines)
    return len(t) >= 32 and t == ["30.000000%", "10.0 μs"] * (len(t) // 2)


JUDGES: list[Judge] = [
    Judge("uart_rx", [f"uart:rx=rx:{U}"], {"rx": 1}, eq(hexes(b"Esc\x00\xff")), "rx", UART,
          "receiver: the bytes on the line it read"),
    Judge("uart_tx_gen", [f"uart:rx=tx:{U}"], {"tx": 0}, eq(hexes(b"Esc")), "tx", UART),
    Judge("midi_tx", ["uart:rx=tx:baudrate=31250", "midi"], {"tx": 0},
          eq(["Channel 1: note on (note = 60 'C4', velocity = 127)"]), "tx", "midi"),
    Judge("combo_uart_i2c", [f"uart:rx=tx:{U}"], {"tx": 0}, eq(hexes(b"UK!\n")), "tx", UART,
          "uart_tx"),
    Judge("combo_uart_i2c", [f"uart:rx=rx:{U}"], {"rx": 1}, eq(hexes(b"hello")), "rx", UART,
          "uart_rx, beside the others"),
    Judge("combo_uart_i2c", *I2C, eq(_i2c_rw(0x42, 9, 0x33)), "sda", I2C_ANN, "i2c_ctrl, beside the others"),
    Judge("i2c_ctrl", *I2C, eq(_i2c_rw(0x42, 5, 0xA5)), "sda", I2C_ANN),
    Judge("i2c_eeprom", ["i2c:scl=scl:sda=sda", "eeprom24xx"], {"scl": 2, "sda": 3},
          eq(["Data", "Page write (addr=10, 4 bytes): DE AD BE EF",
              "Data", "Sequential random read (addr=10, 4 bytes): DE AD BE EF",
              "Warning: No reply from slave!",            # the probe of 0x51, which is not there
              "Data", "Page write (addr=1E, 3 bytes): 01 02 03",
              "Warning: Page write crossed page boundary from page 3 to 4!",   # the script's roll-over
              "Data", "Random access read (addr=18, 1 byte): 03"]), "sda",
          "eeprom24xx=byte-write:page-write:random-read:seq-random-read:cur-addr-read:seq-cur-addr-read:data:warnings",
          "target: its replies, after a page roll-over"),
    Judge("i2c_burst", *I2C, eq(_burst()), "sda", I2C_ANN),
    Judge("oled_pong", *I2C, oled_title_ok, "sda", I2C_WRITES,
          "the decoded writes, on an SSD1306, draw only the title's pixels"),
    Judge("oled_game", *I2C, oled_game_ok, "sda", I2C_WRITES,
          "the decoded writes, on an SSD1306, draw the game model's court",
          flip_at=0.95),    # earlier writes are redrawn by later steps
    Judge("spi_mode3", [SPI3], SPI4, eq(["A5", "5A"]), "mosi", "spi=mosi-data", "spi_ctrl, MOSI"),
    Judge("spi_mode3", [SPI3], SPI4, eq(["3C", "C3"]), "miso", "spi=miso-data", "spi_ctrl, MISO read"),
    Judge("spi_target", ["spi:clk=sck:mosi=mosi:miso=miso:cs=cs"], SPI4,
          eq(["FF"] * 6 + hexes([_mem(0x0F), 0xDE, 0xAD, _mem(0x12)]) + ["FF"] * 7 + ["55"]), "miso",
          "spi=miso-data", "target: MISO (FF while it does not drive)"),
    Judge("spi_tx", ["spi:clk=sck:mosi=mosi:cs=cs"], {"sck": 4, "mosi": 5, "cs": 7},
          eq(["A5 3C 0F", "01 02 03 04 05"]), "mosi", "spi=mosi-transfer"),
    Judge("spi_sniff", ["spi:clk=sck:mosi=mosi:cs=cs:cpha=1"], {"sck": 0, "mosi": 1, "cs": 3},
          eq(["C5 01 FF 80", "3A", " ".join(hexes(range(0x10, 0x20)))]), "mosi", "spi=mosi-transfer",
          "listener: the frames on the bus it logged"),
    Judge("can_tx", ["can:can_rx=can:nominal_bitrate=250000"], {"can": 1},
          can_ok([("Identifier: 2047 (0x7ff)", list(range(1, 9))), ("Identifier: 1365 (0x555)", [255] * 4 + [0] * 4)]),
          "can", CAN),
    Judge("can_tx_lu", ["can:can_rx=can:nominal_bitrate=1000000"], {"can": 1},
          can_ok([("Identifier: 291 (0x123)", [0xDE, 0xAD]),
                  ("Identifier: 2047 (0x7ff)", [0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF])]), "can", CAN),
    Judge("can_rx", ["can:can_rx=can:nominal_bitrate=250000"], {"can": 1}, can_rx_ok, "can",
          "can=id:full-id:data:rtr:dlc:warnings", "receiver: the frames it read"),
    Judge("ps2_device", ["ps2:clk=clk:data=data"], {"clk": 4, "data": 5}, eq(["Data: 1c", "Data: f0"]),
          "data", "ps2=word:parity-err", "each frame decoded alone (ps2_frames)", pieces=ps2_frames,
          flip_at=0.2),     # inside the first frame (the middle is the idle between)
    Judge("jtag_idcode", ["jtag:tck=tck:tms=tms:tdi=tdi:tdo=tdo"], {"tck": 4, "tms": 5, "tdi": 6, "tdo": 7},
          eq(["DR TDO: 01001011101000000000010001110111 (0x4ba00477), 32 bits"] * 2), "tck",
          "jtag=bitstring-tdo", "the controller's TAP walk and the IDCODE it shifted out",
          flip_at=0.25),    # inside the first request (the middle is the gap between two)
    Judge("swd_idcode", ["swd:swclk=swclk:swdio=swdio"], {"swclk": 4, "swdio": 5},
          eq(["IDCODE", "OK", "0x2ba01477", "W SELECT", "WAIT", "W SELECT", "WAIT", "W SELECT", "OK",
              "0x000000f0", "R APc", "OK", "0x00000000", "RDBUFF", "OK", "0x24770011"]), "swdio",
          "swd=read:write:ack:data:parity"),
    Judge("nec_ir", ["ir_nec:ir=ir"], {"ir": 1}, eq(["Address: 0xA5", "Command: 0x5A", "Repeat code"]), "ir",
          "ir_nec=addr:cmd:repeat-code:warnings", "nec_tx through the IR receiver model, read by nec_rx"),
    Judge("nec_tx_gen", ["ir_nec:ir=ir"], {"ir": 0}, eq(["Address: 0x5A", "Command: 0xC3"]), "ir",
          "ir_nec=addr:cmd:repeat-code:warnings", "the carrier through an IR receiver's envelope",
          transform={"ir": env(150)}),
    Judge("rc5_tx", ["ir_rc5:ir=ir"], {"ir": 0},
          eq(["Togglebit: 0", "Address: 5 (Video cassette recorder 1)", "Command: 12 (Standby)"]), "ir",
          "ir_rc5=address:command:togglebit-0:togglebit-1", "the carrier through an IR receiver's envelope",
          transform={"ir": env(150)}),
    Judge("ev1527_tx", [], {"rf": 0}, eq([f"value={0xABCDE5} bits=24 protocol=1 delay=350"]), "rf", "",
          "rc-switch's receiver (its protocol 1 is the timing the table cites), off its microcontroller",
          tool=rcswitch("rf")),
    Judge("eth_tx", [], {"td": 0}, eq(_eth_lines([(bytes(range(60)), True)])), "td", "",
          "sigrok's ook decoder undoes the Manchester code, Wireshark's dissectors (tshark) read the "
          "frame and check its FCS", tool=tshark_frames("td")),
    Judge("udp_tx", [], {"td": 0}, eq(["eth.dst=ff:ff:ff:ff:ff:ff eth.src=02:00:00:00:00:01 eth.type=0x0800 "
                                       "eth.fcs.status=1 ip.src=192.168.1.100 ip.dst=255.255.255.255 ip.len=46 "
                                       "ip.checksum.status=1 udp.srcport=5000 udp.dstport=5000 udp.length=26 "
                                       f"data.data={b'escapement says hi'.hex()}"]), "td", "",
          "ook, then tshark: the FCS and the IPv4 header checksum the chip built", tool=tshark_frames("td")),
    Judge("sirc_tx", ["ir_sirc:ir=ir"], {"ir": 0}, eq(["Command: 0x15", "Address: 0x01"]), "ir",
          "ir_sirc=command:address", "the carrier through an IR receiver's envelope",
          transform={"ir": env(150)}, tree="upstream"),
    # IRMP (libirmp, in libsigrokdecode's tree) reads SIRC-12's 12 bits as one command, 0x95
    # = command 0x15 | address 0x01 << 7, and JVC's 16 as 4 + 12 bits: 0x9, 0x355 = 0x3559,
    # address 0x59 and command 0x35
    Judge("sirc_tx", ["ir_irmp:ir=ir"], {"ir": 0},
          eq(["Protocol: SIRCS (1), Address 0x0000, Command: 0x0095, Flags: -"]), "ir", "",
          "IRMP, a second reading", transform={"ir": env(150)}, tree="upstream"),
    Judge("jvc_tx", ["ir_irmp:ir=ir"], {"ir": 0},
          eq(["Protocol: JVC (20), Address 0x0009, Command: 0x0355, Flags: -"]), "ir", "",
          "IRMP; it tells JVC from NEC by the pause after the last bit, so the envelope idles on",
          transform={"ir": env_idle(150, 60000)}, tree="upstream"),
    # Samsung32: custom code 0x0707, then the data byte 0x02 and its complement 0xFD
    Judge("samsung_tx", ["ir_irmp:ir=ir"], {"ir": 0},
          eq(["Protocol: SAMSG32 (10), Address 0x0707, Command: 0xfd02, Flags: -"]), "ir", "", "IRMP",
          transform={"ir": env_idle(150, 60000)}, tree="upstream"),
    # Kaseikyo with Panasonic's vendor code: IRMP gives the vendor as the address and frame
    # bits 28 to 39 as the command, here our address's top nibble 0x1 under command 0x3D;
    # it reports a frame only when both parity checks hold
    Judge("panasonic_tx", ["ir_irmp:ir=ir"], {"ir": 0},
          eq(["Protocol: KASEIKYO (5), Address 0x2002, Command: 0x03d1, Flags: -"]), "ir", "",
          "IRMP, which checks both parities", transform={"ir": env_idle(150, 60000)},
          tree="upstream"),
    # Sharp, which IRMP files under DENON and reads MSB first: address 1 over 5 bits is 0x10;
    # command 0x16, expansion 1 and check 0 over 10 bits are 0x1A2. IRMP reports it only once
    # the partner frame, inverted, has followed.
    Judge("sharp_tx", ["ir_irmp:ir=ir"], {"ir": 0},
          eq(["Protocol: DENON (8), Address 0x0010, Command: 0x01a2, Flags: -"]), "ir", "",
          "IRMP: the pair, the second frame inverted", transform={"ir": env_idle(150, 60000)},
          tree="upstream"),
    # the default square, six frames an axis, each with its parity OK and no warning
    Judge("xy2_100", ["xy2-100:clk=clk:sync=sync:data=data"], {"clk": 0, "sync": 1, "x": 2, "y": 3},
          eq([z for a in ((-16384, 16384, 16384, -16384, -16384, 16384),
                          (-16384, -16384, 16384, 16384, -16384, -16384)) for v in a for z in ("OK", str(v))]),
          "x", "xy2-100=position:parity:warnings", "each axis decoded alone",
          pieces=lambda ch: [{"clk": ch["clk"], "sync": ch["sync"], "data": ch[a]} for a in ("x", "y")],
          tree="upstream"),
    # the OBD-II request 68 6A F1 01 00 and its CRC-8/SAE-J1850, 0x17 (the frame as scan
    # tools quote it); the decoder reads the fields and leaves the CRC unchecked
    Judge("j1850_vpw", ["sae_j1850_vpw:data=vpw"], {"vpw": 0},
          eq(["1x SOF", "68", "6a", "f1", "01", "00", "17", "EOF"]), "vpw",
          "sae_j1850_vpw=sof:ifs:prio:dest:src:mode:data:csum:warn", "every symbol, at 4 MHz",
          tree="upstream"),
    # a THGR122NX reading, 23.5 degC and 47 %, sent twice: rtl_433's Oregon Scientific decoder,
    # and sigrok's ook and ook_oregon, each of which checks the checksum
    Judge("oregon_tx", [], {"rf": 0}, eq(["battery_ok=1 channel=1 humidity=47 id=187 "
                                          "model=Oregon-THGR122N protocol=12 temperature_C=23.5"] * 2),
          "rf", "", "rtl_433", tool=rtl433("rf", [12], ("battery_ok", "channel", "humidity", "id", "model",
                                                      "protocol", "temperature_C")), flip_at=0.25),
    Judge("oregon_tx", ["ook:data=rf", "ook_oregon"], {"rf": 0},
          eq(["Oregon v2.1 Preamble", "Sync", "SensorID: 1d20", "Ch: 1", "RollingCode: bb", "Flags1: 0",
              "5", "3", "2", "0", "7", "4", "0", "c", "3", "0", "0",
              "THGN123N,THGR122NX,THGN123N,THGR228N - Temp_Hum", "Ch 1", "Batt OK", "+23.5℃",
              "Hum 47%", "Checksum OK Calc 3C Rx 3C"] * 2), "rf", "ook_oregon", "sigrok, a second reading",
          flip_at=0.25),     # inside the first copy (the middle is the gap between the two)
    # frame 0x123, cycle 5, payload 00..07: the decoder checks the header CRC and the channel A
    # frame CRC itself
    Judge("flexray_tx", ["flexray:channel=fr:channel_type=A:bitrate=2500000"], {"fr": 0},
          eq(["Transmission start sequence", "FSS", "Null frame indicator: data frame", "Sync frame indicator: 0",
              "Startup frame indicator: 0", "Frame ID: 291", "Payload length: 4", "Header CRC: 0x3B4 (OK)",
              "Cycle: 5"] + [f"Data byte {k}: 0x{k:02X}" for k in range(8)] + ["Frame CRC: 0xDD64FA (OK)"]), "fr",
          "flexray=tss:fss:null-frame:sync-frame:startup-frame:id:length:header-crc:cycle:data-byte:frame-crc:warnings",
          "2.5 Mbit/s from 25 MHz", flip_at=0.4),
    Judge("ws2801_tx", ["spi:clk=ci:mosi=di:wordsize=8", "rgb_led_spi"], {"ci": 0, "di": 1},
          eq(["#102030", "#a5c30f"] * 2), "di", "rgb_led_spi", "two latches of the two colours"),
    # the activation (both synchronization frames: the host's 16 us low and the target's
    # answer), SWIM_CSR = 0xA0 by WOTF, then a 4-byte ROTF at 0x004865
    Judge("swim_host", ["swim:swim=swim"], {"swim": 0},
          eq(["synchronization frame", "synchronization frame", "write on-the-fly", "byte count 0x01",
              "address 0x007f80", "0xa0", "read on-the-fly", "byte count 0x04", "address 0x004865",
              "0x00", "0x5a", "0x00", "0x3c"]), "swim", "swim=enterseq:cmd:cmd-unknown:bytes:address:data-write:data-read:nack",
          "every packet's start, parity and acknowledge decoded", sr=True, flip_at=0.97),
    Judge("sbus", ["uart:rx=sbus:baudrate=100000:parity=even:stop_bits=2.0:invert_rx=yes", "sbus_futaba"],
          {"sbus": 0}, eq(_sbus_want()), "sbus", "sbus_futaba", "receiver: the two frames it read",
          tree="upstream"),
    Judge("dcc_tx", ["dcc:data=data"], {"data": 0}, _dcc_ok, "data", "",
          "a community decoder (FrightRisk/sigrok-DCC-Protocoll): the packet and its bits", tree="upstream"),
    Judge("servo", [], {f"s{k}": k for k in range(8)}, _servo_ok, "s3", "",
          "timing: each channel's pulses, one channel at a time", tool=_servo_tool),
    Judge("quadrature", ["graycode:d0=a:d1=b"], {"a": 0, "b": 1}, _quad_ok, "a", "graycode=count",
          "receiver: the count the decoder reads off A and B, against the 20 the chip reported"),
    Judge("freq_meter", ["pwm:data=f"], {"f": 3}, _freq_ok, "f", "",
          "receiver: every period and duty cycle, against the chip's 2000 and 600 ticks per 8 periods"),
    Judge("lin_tx", ["uart:rx=lin:baudrate=19200", "lin"], {"lin": 0},
          eq(["Break condition", "Sync", "ID: 2C Parity: 3 (ok)", "Data: 0x12", "Data: 0x34",
              f"Checksum: 0x{lin_checksum(0x2C | 3 << 6, [0x12, 0x34]):02X}"]), "lin", "lin"),
    Judge("ws2812", ["rgb_led_ws281x:din=din"], {"din": 0}, eq(rgb([0x102030, 0x112233, 0x122436, 0x132639])),
          "din", WS, "the line as undriven-low until the chip drives it", transform={"din": undriven_low}),
    Judge("ws2812_gen", ["rgb_led_ws281x:din=din"], {"din": 0}, eq(rgb([0x00FF00, 0xFF0000, 0x123456])), "din", WS,
          transform={"din": undriven_low}),
    Judge("ws2812_lu", ["rgb_led_ws281x:din=din"], {"din": 0}, eq(rgb([0x00FF00, 0x123456, 0xABCDEF])), "din", WS,
          transform={"din": undriven_low}),
    Judge("wiegand_tx", ["wiegand:d0=d0:d1=d1"], {"d0": 0, "d1": 1}, eq(wiegand_bits(_wiegand26())), "d0",
          "wiegand=bits"),
    Judge("wiegand_rx", ["wiegand:d0=d0:d1=d1"], {"d0": 0, "d1": 1},
          eq([str(b) for b in (0, 0, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1)]), "d0",
          "wiegand=bits", "receiver: the frame it read"),
    Judge("cec", ["cec:cec=cec"], {"cec": 0}, eq(["40:9e:05", "04:83"]), "cec", "cec=frames:warnings",
          "the frame it sent, and the frame it acknowledged"),
    Judge("dali", ["dali:dali=dali"], {"dali": 0},
          eq(["Startbit: 1", "Raw data: 01", "Raw data: 91", "Startbit: 1", "Reply: FF", "Reply: 255",
              "Startbit: 1", "Raw data: 00", "Raw data: 42", "Startbit: 1", "Raw data: 01", "Raw data: A0",
              "Startbit: 1", "Reply: 42", "Reply: 66"]), "dali", "dali=raw:reply",
          "control gear: its replies to the controller's queries"),
    Judge("onewire", ["onewire_link:owr=owr", "onewire_network"], {"owr": 1}, onewire_ok, "owr",
          "onewire_link=bit,onewire_network", "Read ROM, and the ROM bits the master read"),
    Judge("gen_slow", ["uart:rx=dmx:baudrate=250000"], {"dmx": 0}, _dmx_ok, "dmx", UART,
          "dmx512_tx: the 512 slots, read as 8N2 frames"),
    Judge("modbus", ["uart:rx=ro:tx=di:baudrate=19200:parity=even", "modbus:scchannel=TX:cschannel=RX"],
          {"ro": 1, "di": 0},
          eq(["Slave ID: 1", "Function 16: Write Multiple Registers", "Start at address 0x4 / 30005",
              "Write 2 Registers", "Byte count: 4", "Value 0xBE", "Value 0xEF", "Value 0x12", "Value 0x34",
              "CRC correct",
              "Slave ID: 1", "Function 16: Write Multiple Registers", "Start at address 0x4 / 30005",
              "Write 2 Registers", "CRC correct",
              "Slave ID: 1", "Function 3: Read Holding Registers", "Start at address 0x3 / 30004",
              "Read 4 units of data", "CRC correct",
              "Slave ID: 1", "Function 3: Read Holding Registers", "Byte count: 8", "0x1003 / 4099",
              "0xBEEF / 48879", "0x1234 / 4660", "0x1006 / 4102", "CRC correct",
              "Slave ID: 1", "Function 5: Write Single Coil", "Address 0x0 / 10000", "Coil Value ON",
              "CRC correct",
              "Slave ID: 1", "Error for function 5: Write Single Coil", "Error 1: Illegal Function",
              "CRC correct"]), "di", "modbus", "server: the client's requests and its replies, CRCs checked"),
    Judge("usb_hid", ["usb_signalling:dp=dp:dm=dm:signalling=low-speed", "usb_packet:signalling=low-speed"],
          {"dp": 0, "dm": 1},
          eq(["DATA0 [ 80 06 00 01 00 00 12 00 ]", "DATA1 [ 12 01 10 01 00 00 00 08 ]",
              "DATA0 [ 09 12 01 00 04 01 00 02 ]", "DATA1 [ 00 01 ]", "DATA1 [ ]",
              "DATA0 [ 00 05 03 00 00 00 00 00 ]", "DATA1 [ ]",
              "DATA0 [ 80 06 00 02 00 00 22 00 ]", "DATA1 [ 09 02 22 00 01 01 00 80 ]",
              "DATA0 [ 32 09 04 00 00 01 03 01 ]", "DATA1 [ 01 00 09 21 11 01 00 01 ]",
              "DATA0 [ 22 3F 00 07 05 81 03 08 ]", "DATA1 [ 00 0A ]", "DATA1 [ ]",
              "DATA0 [ 00 09 01 00 00 00 00 00 ]", "DATA1 [ ]",
              "DATA0 [ 00 00 04 00 00 00 00 00 ]", "DATA1 [ 00 00 05 00 00 00 00 00 ]"]), "dp",
          "usb_packet=packet-data0:packet-data1:crc16-err:crc5-err",
          "the host's SETUPs and the device's descriptors and reports, every CRC good"),
    Judge("combo_two_banks", [f"uart:rx=tx:{U}"], {"tx": 12}, eq(hexes(b"UK!\n")), "tx", UART, "uart_tx on bank B"),
    Judge("combo_two_banks", [SPI3], {"sck": 8, "mosi": 9, "miso": 10, "cs": 11}, eq(["3C", "C3"]), "miso",
          "spi=miso-data", "spi_ctrl on bank B"),
]


def main() -> int:
    ap = argparse.ArgumentParser(prog="judges")
    ap.add_argument("cases", nargs="*")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--show", action="store_true", help="print each case's annotations")
    args = ap.parse_args()
    if SIGROK is None:
        print("judges: sigrok-cli is not on the PATH")
        return 2
    idx = [i for i, j in enumerate(JUDGES) if not args.cases or j.case in args.cases]
    js = [JUDGES[i] for i in idx]
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        res = list(pool.map(judge, idx, [args.show] * len(idx)))
    if args.show:
        for case, lines, _, _ in res:
            print(f"== {case} ({len(lines)} lines)")
            print("\n".join(lines[:80]))
        return 0
    print("| Case | Decoders | Judge | Control |")
    print("| --- | --- | :---: | :---: |")
    bad = []
    for j, (case, lines, ok, caught) in zip(js, res):
        stack = ", ".join(f"`{s.split(':')[0]}`" for s in j.stack)
        print(f"| {case} | {stack} | {'pass' if ok else 'FAIL'} | {'fails' if caught else 'PASSES'} |")
        if not ok or not caught:
            bad.append((case, ok, caught, lines[:6]))
    for case, ok, caught, lines in bad:
        print(f"- {case}: judge {'pass' if ok else 'FAIL'}, control {'fails' if caught else 'PASSES'}: {lines}")
    print(f"\n{len(js)} judges; {sum(1 for _, _, ok, c in res if ok and c)} pass with a control that fails")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
