"""The held-out round's programs judged on the bench (heldout/RESULTS.md, Bench column).

The chip, the FPGA build clocked at 25 MHz by the Pico W, runs each program while the Pico W
pushes its host words (heldoutbench.py). A second Pico running gusmanb's LogicAnalyzer
firmware (v6.0) records two of the uio pins, and this script, on the laptop, judges what it
recorded. No device is on the other end of any bus, except for M-Bus, where the Pico W itself
plays the bus master.

    python fpga/tangprimer25k/heldoutjudge.py --la COM12 --host COM8 --tc TerminalCapture.exe
        [--out DIR] [--only snes_pad,biss_c] [--mw-half A|B|both]

The analyser has headers on two GPIOs only, so two channels: its GP2 (channel 0) and GP3
(channel 1), each spliced onto a uio net (README.md, "Held-out round: two analyser
channels"). Microwire needs four lines, so each of its commands is captured twice, and
channel 0 moves from uio[0] to uio[2] between the two halves.

Each capture is judged in up to three ways:

1. Against the instruction-set simulator (ISS). The ISS runs the same program image with the
   same host words and the pins pulled up, and every edge the analyser saw after the trigger
   must be one the ISS made, at the same time within two samples plus 100 ppm (the chip's
   and the analyser's crystals), with none missing.
2. Against the protocol's own limits, taken from the specification each program cites.
3. By an outside decoder, where one can judge a run with no device: sigrok's `spdif` decodes
   the S/PDIF stream; `microwire` with `eeprom93xx` decodes the Microwire commands, merged
   from their two captures; `uart` reads both M-Bus lines. `am230x` prints nothing for a
   start signal that no sensor answers, and sigrok has no BiSS, SNES or PS/2-request
   decoder, so those programs are judged by the first two alone.

Every capture also has a control, the same capture with one pulse inverted, which each judge
must refuse: a judge that passes whatever the pins do proves nothing.

The 1553B remote terminal waits for a bus controller that is not there and makes no edge, so
bring-up stage 5 alone judges it (its digest against the model's).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (HERE, REPO / "sw", REPO / "sw" / "rp2350", REPO / "test" / "cosim"):
    sys.path.insert(0, str(p))

from judges import flipped, vcd  # noqa: E402

FW = REPO / "firmware"
TICK_NS = 40                     # the chip's clock period at 25 MHz
M32 = 0xFFFFFFFF
T0 = 4000                        # the tick at which the ISS receives the host words

# ------------------------------------------------------------------ what each program does

# S/PDIF frame words: two distinct, nonzero 16-bit samples a frame (left in [31:16]).
SPDIF_WORDS = [(0x1111 * (k + 1) & 0xFFFF | 0x0101) << 16 | (0xA5A5 ^ (k * 0x0F0F)) & 0xFFFF
               for k in range(2)]

# Microwire host words (microwire_93c46.asm): EWEN, WRITE 0x05 0x1234, READ 0x05.
MW_EWEN, MW_WRITE, MW_READ = 6 << 28, 2 << 28 | 5 << 16 | 0x1234, 1 << 28 | 5 << 16

# A plan per program: the analyser's channel names and the uio pins they are on, the sample
# rate, the samples kept before and after the trigger, the trigger (channel, falling edge?),
# the host words and the number of replies. A plan may add a tape patch for the ISS, a wait
# before the request and a reply timeout.
PLANS = {
    # The stream runs from START. The host link pushes a word in about 9.4 ms, far longer
    # than a 41 us frame, so the Pico pushes one word and then pulses uio[1] (a pin the
    # program does not own) low as a trigger marker. The word goes out about two frames after
    # the push, which is before the marker, so the capture keeps 640 us before the trigger,
    # the most the analyser takes (8,000 samples; 20,000 never triggered), and 7.2 ms after.
    # 12.5 MS/s gives 4 samples a 320 ns unit interval. The trigger is the marker's fall:
    # the analyser's rising-edge trigger fires at once on a line that rests high.
    "spdif_tx": dict(names=["line", "marker"], pins=[0, 1], rate=12_500_000, pre=8_000,
                     post=90_000, trig=(1, True), words=SPDIF_WORDS, replies=0),
    # The sensor may be read 2 s after the last read at the earliest, and the firmware's
    # last read is tick 0, so the bench waits 2.2 s after START; the ISS starts at once
    # instead, from a last read of -2 s (the tape word LAST).
    "dht22": dict(names=["sda"], pins=[0], rate=1_000_000, pre=100, post=3_000,
                  trig=(0, True), words=[1], replies=1, timeout_ms=3000,
                  tape={0: (-2000 * 25000) & M32}, settle_ms=2200),
    "snes_pad": dict(names=["latch", "clk"], pins=[0, 1], rate=25_000_000, pre=100,
                     post=8_000, trig=(0, False), words=[1], replies=1),
    "biss_c": dict(names=["ma", "sl"], pins=[0, 1], rate=25_000_000, pre=100, post=8_000,
                   trig=(0, True), words=[1], replies=2),
    "ps2_host": dict(names=["clk", "data"], pins=[0, 1], rate=1_000_000, pre=100,
                     post=16_000, trig=(0, True), words=[0xF4], replies=1, timeout_ms=2000),
    "microwire_93c46": dict(names=["cs", "sk"], pins=[0, 1], rate=25_000_000, pre=100,
                            post=4_000, trig=(0, False),
                            cmds=[("EWEN", MW_EWEN), ("WRITE 0x05 0x1234", MW_WRITE),
                                  ("READ 0x05", MW_READ)], replies=1),
}
# Microwire's second capture of each command: DI and SK, triggered on SK's first rise.
MW_B = dict(names=["di", "sk"], pins=[2, 1], trig=(1, False))

# M-Bus ("The M-Bus: A Documentation", Rev. 4.8). The Pico W is the master at logic level.
# The slave is given its address (5) and its response (CI 72h, a 12-byte header and one
# record), then four telegrams: SND_NKE to 5 (answered E5h), REQ_UD2 to 5 (answered with
# RSP_UD), SND_NKE to 7 (another slave's: no reply) and REQ_UD2 to 5 with a wrong checksum
# (5.4: no reply).
MB_ADDR = 5
MB_RSP = [0x72, 0x78, 0x56, 0x34, 0x12, 0x2D, 0x2C, 0x01, 0x07, 0x00, 0x00, 0x00, 0x00,
          0x04, 0x13, 0x39, 0x30, 0x00, 0x00]


def mb_short(c: int, a: int, bad_cs: bool = False) -> list[int]:
    """A short frame (5.2): 10h C A CS 16h, where CS = C + A mod 256."""
    return [0x10, c, a, (c + a + (1 if bad_cs else 0)) & 0xFF, 0x16]


def mb_long(c: int, a: int, ci_data: list[int]) -> list[int]:
    """A long frame (5.2): 68h L L 68h C A CI data CS 16h, where L counts C to the data."""
    body = [c, a] + list(ci_data)
    return [0x68, len(body), len(body), 0x68] + body + [sum(body) & 0xFF, 0x16]


MB_HOST = [1 << 24 | MB_ADDR, 2 << 24 | len(MB_RSP)] + MB_RSP
MB_TELEGRAMS = [mb_short(0x40, MB_ADDR), mb_short(0x7B, MB_ADDR), mb_short(0x40, 7),
                mb_short(0x7B, MB_ADDR, bad_cs=True)]
MB_REPLIES = [[0xE5], mb_long(0x08, MB_ADDR, MB_RSP), [], []]
# The slave's notes to its host: kind << 24 | A << 16 | C << 8 | data bytes.
MB_NOTES = [1 << 24 | MB_ADDR << 16 | 0x40 << 8, 2 << 24 | MB_ADDR << 16 | 0x7B << 8]
# 50 kS/s is 21 samples a bit at 2400 baud; 80,000 samples hold the 1.3 s session.
PLANS["mbus_slave"] = dict(names=["m2s", "s2m"], pins=[0, 1], rate=50_000, pre=100,
                           post=80_000, trig=(0, True), baud=2400, wait_ms=300)


# ------------------------------------------------------------------ judge 1: the ISS

def iss(name: str, words: list[int], ns: float, tape: dict | None = None):
    """Run the program's image (as make_bundle builds it: its defaults, 25 MHz) on the ISS
    with the uio pins pulled up, push `words` at tick T0 and run `ns` more. Returns
    ({uio pin: level at each tick}, the replies)."""
    from escasm import assemble_file
    from escmodels import Board
    from esciss import Machine
    im = assemble_file(FW / f"{name}.asm")
    for a, v in (tape or {}).items():
        im.tape[a] = v
    board = Board()
    m = Machine(im, board=board)
    m.run(T0, stop_when_idle=False)
    for w in words:
        m.host_rx_push(0, w)
    replies = []
    for _ in range(int(ns / TICK_NS) // 500 + 2):
        m.run(500, stop_when_idle=False)
        v = m.host_tx_pop(0)
        if v is not None:                        # the mailbox holds two; one pop an edge
            replies.append(v)
            m.run(1, stop_when_idle=False)
            v = m.host_tx_pop(0)
            if v is not None:
                replies.append(v)
    if m.ff.valid:
        raise RuntimeError(f"{name} faulted on the ISS: {m.ff}")
    return {p: board.level(p) for p in range(8)}, replies


def edges(levels: list[int], dt: float, start: int = 1) -> list[tuple[float, int]]:
    """(time in ns from index 0, new level) for each change at or after index `start`."""
    return [(i * dt, levels[i]) for i in range(max(start, 1), len(levels))
            if levels[i] != levels[i - 1]]


def trigger_index(levels: list[int], falling: bool, start: int = 1) -> int | None:
    """The index of the first falling (or rising) edge at or after `start`."""
    want = 0 if falling else 1
    for i in range(max(start, 1), len(levels)):
        if levels[i] != levels[i - 1] and levels[i] == want:
            return i
    return None


def against_iss(cap: dict[str, list[int]], rate: int, plan: dict, ref: dict[int, list[int]],
                pre: int) -> list[str]:
    """Every edge after the trigger at the ISS's time, both measured from the trigger edge."""
    dt = 1e9 / rate
    ch, falling = plan["trig"]
    names, pins = plan["names"], plan["pins"]
    ci = trigger_index(cap[names[ch]], falling, max(pre - 2, 1))
    ri = trigger_index(ref[pins[ch]], falling, T0)
    if ci is None or ri is None:
        return [f"no trigger edge (capture {ci}, ISS {ri})"]
    span = (len(cap[names[0]]) - ci - 4) * dt           # the capture's window after the trigger
    bad = []
    for n, p in zip(names, pins):
        got = [(t - ci * dt, v) for t, v in edges(cap[n], dt, ci) if t - ci * dt <= span]
        want = [(t - ri * TICK_NS, v) for t, v in edges(ref[p], TICK_NS, ri)
                if t - ri * TICK_NS <= span]
        if [v for _, v in got] != [v for _, v in want]:
            bad.append(f"{n}: {len(got)} edges, the ISS {len(want)}")
            continue
        worst = max((abs(a - b) - (2 * dt + 1e-4 * b) for (a, _), (b, _) in zip(got, want)),
                    default=-1)
        if worst > 0:
            bad.append(f"{n}: an edge {worst:.0f} ns past the tolerance")
    return bad


# ------------------------------------------------------------------ judge 2: the limits

def runs(levels: list[int], dt: float, start: int) -> list[tuple[int, float, float]]:
    """(level, start in ns, length in ns) of each run of equal levels from `start`; the last
    run is open, ending with the capture."""
    out, a = [], start
    for i in range(start + 1, len(levels) + 1):
        if i == len(levels) or levels[i] != levels[a]:
            out.append((levels[a], a * dt, (i - a) * dt))
            a = i
    return out


def limits_snes(cap, dt, ti, replies):
    """CD4021B at 5 V: P/S pulse at least 160 ns, P/S removal before a clock edge at least
    280 ns, clock pulse at least 180 ns. The firmware latches for 12 us and clocks with 6 us
    phases, 17 clocks (16 key bits and the connection bit). With no pad every bit reads 1,
    which the reply gives as 0: nothing pressed, not connected."""
    bad = []
    hi = [r for r in runs(cap["latch"], dt, ti) if r[0] == 1]
    width = hi[0][2] if hi else 0
    if not 11_900 <= width <= 12_100 + 2 * dt:
        bad.append(f"latch high {width:.0f} ns (12 us)")
    latch_end = ti * dt + width
    ce = edges(cap["clk"], dt, ti)
    if any(t < latch_end for t, _ in ce):
        bad.append("CLK moved while the latch was high")
    rises = [t for t, v in ce if v == 1]
    if not rises or rises[0] - latch_end < 280:
        bad.append("P/S removal before the first clock rise under 280 ns")
    if len(rises) != 17:
        bad.append(f"{len(rises)} clock rises (17)")
    ph = [r[2] for r in runs(cap["clk"], dt, ti)[1:-1]]
    if ph and (min(ph) < 180 or not all(5_900 <= p <= 6_100 + 2 * dt for p in ph)):
        bad.append(f"clock phases {min(ph):.0f} to {max(ph):.0f} ns (6 us)")
    if replies != [0]:
        bad.append(f"reply {replies} (0: nothing pressed, not connected)")
    return bad


def limits_biss(cap, dt, ti, replies):
    """BiSS C: MA rests high and a frame starts with MA falling, at 250 kHz by default.
    With no slave no ACK comes within ACK_MAX clocks: the status says "no ACK" (2), the data
    is 0, and MA is left high."""
    bad = []
    ma = runs(cap["ma"], dt, ti)
    ph = [r[2] for r in ma[:-1]] or [0]
    if not all(1_900 <= p <= 2_100 + 2 * dt for p in ph):
        bad.append(f"MA phases {min(ph):.0f} to {max(ph):.0f} ns (2 us at 250 kHz)")
    if ma[-1][0] != 1:
        bad.append("MA not left high")
    if 0 in cap["sl"][ti:]:
        bad.append("SL low with no slave")
    if replies != [2, 0]:
        bad.append(f"replies {replies} ([2, 0]: no ACK, no data)")
    return bad


def limits_ps2(cap, dt, ti, replies):
    """PS/2 host to device (IBM, Figure 9): the host holds CLK low more than 60 us (here
    RTS_US, 100 us), pulls DATA low (the start bit) and releases CLK 5 us later. No device
    clocks within FIRST_MS (15 ms), so the host releases DATA and holds CLK low for RTS_US
    again, an inhibit that ends the transfer; the reply is 0x80000002 (no device clock)."""
    bad = []
    clk = runs(cap["clk"], dt, ti)
    lows = [r for r in clk if r[0] == 0]
    first_low = lows[0][2] if lows else 0
    if not 60_000 < first_low <= 110_000 + 2 * dt:
        bad.append(f"CLK low {first_low:.0f} ns (105 us, more than 60)")
    de = edges(cap["data"], dt, ti)
    if not de or de[0][1] != 0 or not ti * dt + 60_000 < de[0][0] < ti * dt + first_low:
        bad.append("DATA did not fall while CLK was held low, 60 us or more in")
    elif len(de) < 2 or not 14_900_000 <= de[1][0] - de[0][0] <= 15_100_000 + 2 * dt:
        bad.append("DATA not released 15 ms (FIRST_MS) after the start bit")
    elif (len(lows) != 2 or abs(lows[1][1] - de[1][0]) > 2 * dt
          or not 99_000 <= lows[1][2] <= 101_000 + 2 * dt):
        bad.append("no 100 us inhibit (CLK low) as DATA is released")
    if len(clk) != 4:
        bad.append(f"CLK changed {len(clk) - 1} times after it fell (3: released, then "
                   "the inhibit)")
    if replies != [0x80000002]:
        bad.append(f"reply {[hex(r) for r in replies]} (0x80000002: no device clock)")
    return bad


def limits_dht22(cap, dt, ti, replies):
    """AM2302, 7.2: the host's start signal is low for Tbe, 0.8 to 20 ms (1 ms here), then
    released. No sensor answers, so the reply is 0xFFFF0001 (no response)."""
    bad = []
    dq = runs(cap["sda"], dt, ti)
    if dq[0][0] != 0 or not 990_000 <= dq[0][2] <= 1_010_000 + 2 * dt:
        bad.append(f"start signal low {dq[0][2]:.0f} ns (1 ms)")
    if len(dq) != 2:
        bad.append(f"{len(dq) - 1} edges after the start signal (released, then nothing)")
    if replies != [0xFFFF0001]:
        bad.append(f"reply {[hex(r) for r in replies]} (0xFFFF0001: no response)")
    return bad


LIMITS = {"snes_pad": limits_snes, "biss_c": limits_biss, "ps2_host": limits_ps2,
          "dht22": limits_dht22}


def judge_capture(name: str, cap: dict, plan: dict, rate: int, pre: int, ref, replies,
                  ref_replies) -> list[str]:
    """Judges 1 and 2 for one capture, and the replies against the ISS's."""
    bad = against_iss(cap, rate, plan, ref, pre)
    if replies != ref_replies:
        bad.append(f"replies {[hex(r) for r in replies]}, the ISS "
                   f"{[hex(r) for r in ref_replies]}")
    ch, falling = plan["trig"]
    ti = trigger_index(cap[plan["names"][ch]], falling, max(pre - 2, 1))
    if ti is not None and name in LIMITS:
        bad += LIMITS[name](cap, 1e9 / rate, ti, replies)
    return bad


# ------------------------------------------------------------------ judge 3: sigrok

def sigrok(text: str, decoders: str, work: Path, positions: bool = False,
           ann: str | None = None) -> list:
    """sigrok-cli's annotations for a VCD, the decoder prefix removed; sigrok-cli runs here
    if installed, else in WSL. `ann` selects annotation rows (-A). With `positions`, each is
    (first, last, text), in the VCD's 10 ns steps."""
    f = work / "decode.vcd"
    f.write_text(text, newline="\n")
    local = shutil.which("sigrok-cli")
    if local:
        cmd = [local, "-I", "vcd", "-i", str(f), "-P", decoders]
    else:
        wp = subprocess.run(["wsl.exe", "wslpath", "-a", str(f).replace("\\", "/")],
                            capture_output=True, text=True).stdout.strip()
        cmd = ["wsl.exe", "-e", "sigrok-cli", "-I", "vcd", "-i", wp, "-P", decoders]
    if ann:
        cmd += ["-A", ann]
    if positions:
        cmd.append("--protocol-decoder-samplenum")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode:
        return ["ERROR sigrok-cli: " + r.stderr.strip()[:300]]
    lines = [ln for ln in r.stdout.splitlines() if ": " in ln]
    if not positions:
        return [ln.split(": ", 1)[1] for ln in lines]
    out = []
    for ln in lines:
        a, b = ln.split(" ", 1)[0].split("-")
        out.append((int(a), int(b), ln.split(": ", 1)[1]))
    return out


def spdif(line: list[int], rate: int, pushed: list[int],
          work: Path) -> tuple[list[str], str, int | None]:
    """S/PDIF by sigrok's `spdif` decoder. Each sub-frame carries its validity bit, which the
    decoder marks "V" (valid) or "E" (not valid); spdif_tx.asm sends zeros flagged not valid
    in a frame with no host word. Passes if the valid samples are exactly the pushed words'
    samples, in order, every other sub-frame is a zero, sub-frames 1 (preamble B or M) and 2
    (W) alternate, and the shortest pulse is one unit interval (8 ticks, 320 ns). Returns
    (problems, a summary, the sample index of the first valid sub-frame's middle)."""
    dt = 1e9 / rate
    e = [i for i in range(1, len(line)) if line[i] != line[i - 1]]
    ui = round(8 * TICK_NS / dt)
    k = next((j for j in range(len(e) - 1) if e[j + 1] - e[j] <= 4 * ui), None)
    if k is None:
        return ["no S/PDIF stream in the capture"], "", None
    # The decoder learns its pulse widths from the first edges, so the VCD starts one unit
    # interval before an edge. From some starting points of a running stream it never
    # synchronises ("Unknown Preamble", then a Python ValueError in sigrok 0.7.2); then it
    # starts one sub-frame (64 unit intervals) later, up to five times.
    for tries in range(6):
        k2 = next(j for j in range(k, len(e)) if e[j] >= e[k] + tries * 64 * ui)
        start = e[k2] - ui
        pos = sigrok(vcd({"line": line[start:]}, dt), "spdif:data=line", work, positions=True)
        if pos and isinstance(pos[0], str):
            return pos[:1], "", None
        ann = [t for _, _, t in pos]
        if "Unknown Preamble" not in ann and ann.count("V") + ann.count("E") >= 100:
            break
    subs, audio, valid_at = [], None, None
    for a0, a1, a in pos:
        if a.startswith("Audio 0x"):
            audio = (int(a.split()[1], 16), (a0 + a1) / 2)
        elif a in ("V", "E") and audio is not None:
            subs.append((audio[0], a == "V"))
            if a == "V" and valid_at is None and len(subs) > 4:
                valid_at = start + round(audio[1] * 10 / dt)
            audio = None
    subs = subs[4:]                  # the decoder settles on its first frames
    bad = []
    want = [x for w in pushed for x in ((w >> 16) << 8, (w & 0xFFFF) << 8)]
    got = [s for s, v in subs if v]
    if got != want:
        bad.append(f"valid samples {[hex(a) for a in got[:6]]}, "
                   f"pushed {[hex(a) for a in want[:6]]}")
    if any(s for s, v in subs if not v):
        bad.append("a sub-frame flagged not valid carries a nonzero sample")
    pre = [a.split()[1] for a in ann if a.startswith("Preamble ")]
    if len(pre) < 150:
        bad.append(f"{len(pre)} sub-frames decoded (150 at least)")
    off = [i for i in range(4, len(pre)) if (pre[i] == "W") == (pre[i - 1] == "W")]
    if off:
        bad.append(f"preambles out of turn at sub-frames {off[:4]}")
    short = min(b - a for a, b in zip(e[k:], e[k + 1:]))
    if abs(short - ui) > 1:
        bad.append(f"shortest pulse {short * dt:.0f} ns (the unit interval, 320 ns)")
    return bad, f"{len(got)} valid samples in {len(subs)} sub-frames", valid_at


def merge_microwire(a: dict[str, list[int]], b: dict[str, list[int]]):
    """One Microwire capture from two of the same command, A (CS, SK) and B (DI, SK), lined
    up on their first SK rise. SK must agree edge for edge, within a sample; DO is the
    pull-up's 1, as no EEPROM is fitted. Returns (channels, None) or (None, the problem)."""
    ia, ib = trigger_index(a["sk"], False), trigger_index(b["sk"], False)
    if ia is None or ib is None:
        return None, "no SK in a capture"
    ea = [i - ia for i in range(ia, len(a["sk"])) if a["sk"][i] != a["sk"][i - 1]]
    eb = [i - ib for i in range(ib, len(b["sk"])) if b["sk"][i] != b["sk"][i - 1]]
    n = min(len(ea), len(eb))
    if abs(len(ea) - len(eb)) > 1 or any(abs(x - y) > 1 for x, y in zip(ea[:n], eb[:n])):
        return None, f"SK differs between the two captures ({len(ea)} and {len(eb)} edges)"
    shift = ib - ia
    di = [b["di"][min(max(i + shift, 0), len(b["di"]) - 1)] for i in range(len(a["sk"]))]
    return {"cs": a["cs"], "sk": a["sk"], "si": di, "so": [1] * len(di)}, None


def microwire(merged: list[dict[str, list[int]]], rate: int, work: Path) -> list[str]:
    """The three merged commands, one after another, by sigrok's `microwire` decoder with
    `eeprom93xx` on top (6 address bits, 16-bit words): write enable, the write with its
    address and data, the read with its address and the pull-up's 0xFFFF."""
    ch = {k: sum((m[k] for m in merged), []) for k in ("cs", "sk", "si", "so")}
    ann = sigrok(vcd(ch, 1e9 / rate), "microwire:cs=cs:sk=sk:si=si:so=so,"
                 "eeprom93xx:addresssize=6:wordsize=16", work)
    ops = [a for a in ann if a in ("Read word", "Write word", "Erase word", "Write enable",
                                   "Write disable")]
    addresses = [a for a in ann if a.startswith("Address: ")]
    data = [a for a in ann if a.startswith("Data: ")]
    trouble = [a for a in ann if "Not enough" in a or a.startswith("ERROR")]
    bad = []
    if ops != ["Write enable", "Write word", "Read word"]:
        bad.append(f"operations {ops}")
    if addresses != ["Address: 0x0005", "Address: 0x0005"]:
        bad.append(f"addresses {addresses}")
    if data != ["Data: 0x1234", "Data: 0xffff"]:
        bad.append(f"data {data}")
    if trouble:
        bad.append(f"decoder: {trouble}")
    return bad


def mbus(cap: dict[str, list[int]], rate: int, baud: int, notes: list[int],
         work: Path) -> tuple[list[str], str]:
    """The live M-Bus exchange by sigrok's `uart` decoder (8 data bits, even parity, one
    stop bit: 5.1) on both lines. Passes if the master's characters are the telegrams, the
    slave's are exactly the replies M-Bus asks for (none to another address or to a bad
    checksum), no character has a parity or frame error, each reply starts 11 to 330 bit
    times after its telegram's stop bit (5.4) and ends before the next telegram, and the
    slave's notes to its host name the telegrams it acted on."""
    text = vcd({"m2s": cap["m2s"], "s2m": cap["s2m"]}, 1e9 / rate)
    dec = f"uart:rx=m2s:tx=s2m:baudrate={baud}:parity=even:format=hex"
    chars = {}                                       # line: [(first ns, last ns, byte)]
    for row in ("rx", "tx"):
        pos = sigrok(text, dec, work, positions=True, ann=f"uart={row}-data")
        if pos and isinstance(pos[0], str):
            return pos[:1], ""
        chars[row] = [(a * 10, b * 10, int(t, 16)) for a, b, t in pos
                      if len(t) == 2 and all(c in "0123456789ABCDEF" for c in t)]
    bad = []
    errs = [a for a in sigrok(text, dec, work) if "error" in a.lower()]
    if errs:
        bad.append(f"decoder: {errs[:4]}")
    sent, got = [c for *_, c in chars["rx"]], [c for *_, c in chars["tx"]]
    want_sent = [c for t in MB_TELEGRAMS for c in t]
    want_got = [c for r in MB_REPLIES for c in r]
    if sent != want_sent:
        bad.append(f"master's characters {['%02X' % c for c in sent]}, "
                   f"sent {['%02X' % c for c in want_sent]}")
    if got != want_got:
        bad.append(f"slave's characters {['%02X' % c for c in got]}, "
                   f"want {['%02X' % c for c in want_got]}")
    bit = 1e9 / baud
    delays = []
    if sent == want_sent and got == want_got:
        first = 0                                    # the telegram's first character
        k = 0                                        # the reply's first character
        for tel, reply in zip(MB_TELEGRAMS, MB_REPLIES):
            last = first + len(tel) - 1
            if reply:
                # a data annotation spans the data bits: the stop bit ends two bit times
                # (parity, stop) after the last one, and the start bit begins one before
                d = (chars["tx"][k][0] - bit - (chars["rx"][last][1] + 2 * bit)) / bit
                delays.append(round(d, 1))
                if not 11 <= d <= 330:
                    bad.append(f"a reply {d:.1f} bit times after its telegram (11 to 330)")
                k += len(reply)
                if last + 1 < len(chars["rx"]) and chars["tx"][k - 1][1] > chars["rx"][last + 1][0]:
                    bad.append("a reply still running when the next telegram began")
            first = last + 1
    if notes != MB_NOTES:
        bad.append(f"notes to the host {[hex(x) for x in notes]}, "
                   f"want {[hex(x) for x in MB_NOTES]}")
    return bad, f"{len(sent)} characters sent, {len(got)} answered, reply delays {delays} bit times"


# ------------------------------------------------------------------ the controls

def control(cap: dict) -> dict:
    """The capture with one pulse of its busiest channel inverted (judges.flipped)."""
    busiest = max(cap, key=lambda n: sum(1 for a, b in zip(cap[n], cap[n][1:]) if a != b))
    return dict(cap, **{busiest: flipped(cap[busiest])})


def pulse_flipped(levels: list[int], at: int | None) -> list[int]:
    """The control for a stream: the pulse that holds sample `at` inverted. For S/PDIF that
    is the middle of the first valid sub-frame, where one pulse changes what the judge
    checks; a pulse among the silent sub-frames would not. judges.flipped's middle pulse
    when `at` is None."""
    if at is None:
        return flipped(levels)
    a = max(i for i in range(1, at + 1) if levels[i] != levels[i - 1])
    b = min(i for i in range(at + 1, len(levels)) if levels[i] != levels[i - 1])
    return levels[:a] + [1 - v for v in levels[a:b]] + levels[b:]


# ------------------------------------------------------------------ the bench

def settings(path: Path, names: list[str], rate: int, pre: int, post: int,
             trig: tuple[int, bool]) -> None:
    """A TerminalCapture session (SharedDriver.CaptureSession as JSON): the named channels
    (0 and 1, the analyser's GP2 and GP3) and an edge trigger (inverted: falling)."""
    path.write_text(json.dumps({
        "Frequency": rate, "PreTriggerSamples": pre, "PostTriggerSamples": post,
        "LoopCount": 0, "MeasureBursts": False,
        "CaptureChannels": [{"ChannelNumber": k, "ChannelName": n} for k, n in enumerate(names)],
        "TriggerType": 0, "TriggerChannel": trig[0], "TriggerInverted": trig[1],
        "TriggerBitCount": 0, "TriggerPattern": 0}))


def read_csv(path: Path) -> dict[str, list[int]]:
    """TerminalCapture's CSV: a header of channel names, then one row of levels a sample."""
    lines = path.read_text().split()
    names = [n.strip() for n in lines[0].split(",")]
    cols = list(zip(*(map(int, ln.split(",")) for ln in lines[1:])))
    return {n: list(c) for n, c in zip(names, cols)}


class Bench:
    """The Pico W through mpremote, its interpreter's state kept between commands."""

    def __init__(self, port: str):
        self.mp = shutil.which("mpremote") or "mpremote"
        self.port = port

    def run(self, code: str, resume: bool = True, timeout: int = 60) -> str:
        cmd = [self.mp, "connect", self.port] + (["resume"] if resume else []) + ["exec", code]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode or "Traceback" in r.stdout:
            raise RuntimeError(f"mpremote: {(r.stderr + r.stdout).strip()[-600:]}")
        return r.stdout

    def result(self, code: str):
        """Run `code`, which prints one value as its last line, and return that value."""
        return json.loads(self.run(code).strip().splitlines()[-1])

    def finish(self) -> list[str]:
        """STOP the chip; a first fault or a set tripwire is a problem."""
        ff = self.run("print(b.finish())").strip()
        return [] if ff.startswith("(0, 0)") else [f"first fault, tripwires: {ff}"]


class Analyser:
    """The LogicAnalyzer Pico through TerminalCapture, its command-line client."""

    def __init__(self, tc: str, port: str, work: Path, arm_s: float):
        self.tc, self.port, self.work, self.arm_s = tc, port, work, arm_s
        self.n = 0

    def capture(self, plan: dict, action):
        """Arm for `plan`, run `action()`, and return (the channels, action's result)."""
        self.n += 1
        tcs, csv = self.work / f"c{self.n}.tcs", self.work / f"c{self.n}.csv"
        settings(tcs, plan["names"], plan["rate"], plan["pre"], plan["post"], plan["trig"])
        cap = subprocess.Popen([self.tc, "capture", self.port, str(tcs), str(csv)],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        time.sleep(self.arm_s)
        if cap.poll() is not None:
            raise RuntimeError(f"the analyser exited before the run: {cap.stdout.read()[:300]}")
        res = action()
        try:
            cap.wait(timeout=30)
        except subprocess.TimeoutExpired:
            cap.kill()
            raise RuntimeError("the analyser never triggered") from None
        if not csv.exists():
            raise RuntimeError(f"no capture: {cap.stdout.read()[:300]}")
        return read_csv(csv), res


CONTROL_PASSED = "CONTROL: the capture with a pulse inverted still passes"


def run_request(name, p, bench, la, keep):
    """A program that answers host requests: one capture of one request, judged against the
    ISS and the protocol's limits."""
    bench.run(f"b.setup({name!r})")
    if p.get("settle_ms"):
        time.sleep(p["settle_ms"] / 1000)
    code = f"print(b.request({p['words']!r}, {p['replies']}, {p.get('timeout_ms', 1000)}))"
    cap, replies = la.capture(p, lambda: bench.result(code))
    keep(name, cap, p["rate"])
    window = p["post"] * 1e9 / p["rate"]
    ref, ref_replies = iss(name, p["words"], window + 5e5, p.get("tape"))
    bad = judge_capture(name, cap, p, p["rate"], p["pre"], ref, replies, ref_replies)
    if not judge_capture(name, control(cap), p, p["rate"], p["pre"], ref, replies, ref_replies):
        bad.append(CONTROL_PASSED)
    return bad + bench.finish(), f"replies {[hex(r) for r in replies]}"


def run_spdif(name, p, bench, la, keep, work):
    """S/PDIF: one capture a word, each decoded by sigrok."""
    bench.run(f"b.setup({name!r})")
    bad, notes = [], []
    for k, w in enumerate(p["words"]):
        code = f"print(b.stream_marked([{w}]))"
        cap, pushed = la.capture(p, lambda code=code: bench.result(code))
        keep(f"{name}-{k}", cap, p["rate"])
        problems, summary, at = spdif(cap["line"], p["rate"], pushed, work)
        if not spdif(pulse_flipped(cap["line"], at), p["rate"], pushed, work)[0]:
            problems.append(CONTROL_PASSED)
        bad += [f"word {k}: {x}" for x in problems]
        notes.append(summary)
    return bad + bench.finish(), "; ".join(notes)


def run_mbus(name, p, bench, la, keep, work):
    """M-Bus, live: the Pico W sends the four telegrams; sigrok reads both lines."""
    bench.run(f"b.mbus_prepare({MB_HOST!r})")
    code = f"print(b.mbus_master({MB_TELEGRAMS!r}, {p['baud']}, {p['wait_ms']}))"
    cap, notes = la.capture(p, lambda: bench.result(code))
    bench.run("b.mbus_release()")
    keep(name, cap, p["rate"])
    bad, summary = mbus(cap, p["rate"], p["baud"], notes, work)
    if not mbus(dict(cap, s2m=flipped(cap["s2m"])), p["rate"], p["baud"], notes, work)[0]:
        bad.append(CONTROL_PASSED)
    return bad + bench.finish(), summary


def run_microwire(name, p, bench, la, keep, work, halves="both", out=None):
    """Microwire: each command captured twice, (CS, SK) and then, with channel 0 on uio[2],
    (DI, SK); each capture judged against the ISS, then the two merged and decoded by
    sigrok. With halves "A" the first captures are kept in out/microwire_A.json, and "B"
    reads them back for the merge, so the probe can move between two runs."""
    if halves != "both" and out is None:
        raise RuntimeError("--mw-half A or B needs --out (the first half's captures go there)")
    window = p["post"] * 1e9 / p["rate"]
    kept = out / "microwire_A.json" if out else None
    bad, caps = [], {}
    if halves == "B":
        if not kept.exists():
            raise RuntimeError(f"--mw-half B merges with the first half's captures, and "
                               f"{kept} is missing: run --mw-half A into the same --out first")
        caps = {(what, "A"): cap for what, cap in json.loads(kept.read_text()).items()}
    for half, plan in (("A", p), ("B", dict(p, **MW_B))):
        if halves not in (half, "both"):
            continue
        if half == "B" and halves == "both":
            input("Move the analyser's channel 0 (its GP2) from uio[0] to uio[2] "
                  "(J6 top row, col 4, the Pico W's GP19), then press Enter: ")
        bench.run(f"b.setup({name!r})")
        for what, w in p["cmds"]:
            code = f"print(b.request([{w}], 1))"
            cap, replies = la.capture(plan, lambda code=code: bench.result(code))
            keep(f"{name}-{what.split()[0].lower()}-{half}", cap, p["rate"])
            ref, ref_replies = iss(name, [w], window + 1e6)
            problems = judge_capture(name, cap, plan, p["rate"], p["pre"], ref, replies,
                                     ref_replies)
            if not judge_capture(name, control(cap), plan, p["rate"], p["pre"], ref, replies,
                                 ref_replies):
                problems.append(CONTROL_PASSED)
            bad += [f"{what} ({half}): {x}" for x in problems]
            caps[(what, half)] = cap
        bad += bench.finish()
    if halves == "A":
        kept.write_text(json.dumps({w: caps[(w, "A")] for w, _ in p["cmds"]}), newline="\n")
        return bad, "first half (CS, SK) captured; move channel 0 to uio[2] and run --mw-half B"
    merged = []
    for what, _ in p["cmds"]:
        m, why = merge_microwire(caps[(what, "A")], caps[(what, "B")])
        if why:
            bad.append(f"{what}: {why}")
        else:
            merged.append(m)
    if len(merged) == len(p["cmds"]):
        bad += microwire(merged, p["rate"], work)
        ctl = list(merged)
        ctl[1] = dict(ctl[1], si=flipped(ctl[1]["si"], 0.7))       # a data bit of the write
        if not microwire(ctl, p["rate"], work):
            bad.append("CONTROL: the merged capture with a DI pulse inverted still decodes")
    return bad, "3 commands, each captured twice and merged on SK"


def main() -> int:
    ap = argparse.ArgumentParser(prog="heldoutjudge")
    ap.add_argument("--la", required=True, help="the analyser's serial port (COMx)")
    ap.add_argument("--host", required=True, help="the bench Pico W's serial port (COMx)")
    ap.add_argument("--out", help="keep each capture (VCD) and the log here")
    ap.add_argument("--only", help="comma-separated program names (default: all seven)")
    ap.add_argument("--arm", type=float, default=3.0, help="seconds for the analyser to arm")
    ap.add_argument("--tc", default=os.environ.get("TERMINALCAPTURE", "TerminalCapture"),
                    help="TerminalCapture.exe (default: $TERMINALCAPTURE, else on the PATH)")
    ap.add_argument("--mw-half", choices=("A", "B", "both"), default="both",
                    help="Microwire: A captures (CS, SK) and keeps them in --out; B, run after "
                         "channel 0 moves to uio[2], captures (DI, SK) and judges both; both "
                         "asks for the move at a prompt")
    a = ap.parse_args()
    out = Path(a.out) if a.out else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    names = a.only.split(",") if a.only else list(PLANS)
    log: list[str] = []

    def say(s: str) -> None:
        print(s, flush=True)
        log.append(s)

    def keep(tag: str, cap: dict, rate: int) -> None:
        if out:
            (out / f"{tag}.vcd").write_text(vcd(cap, 1e9 / rate), newline="\n")

    bench = Bench(a.host)
    bench.run("import barepico, heldoutbench\n"
              "io = barepico.open_io(sys_hz=heldoutbench.SYS_HZ)\n"
              "b = heldoutbench.HeldoutBench(io, out=lambda t: None)", resume=False)
    say(f"heldoutjudge: analyser {a.la}, bench {a.host}; programs: {', '.join(names)}")
    failed = []
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        la = Analyser(shutil.which(a.tc) or a.tc, a.la, work, a.arm)
        for name in names:
            p = PLANS[name]
            try:
                if name == "microwire_93c46":
                    bad, notes = run_microwire(name, p, bench, la, keep, work, a.mw_half, out)
                elif name == "mbus_slave":
                    bad, notes = run_mbus(name, p, bench, la, keep, work)
                elif name == "spdif_tx":
                    bad, notes = run_spdif(name, p, bench, la, keep, work)
                else:
                    bad, notes = run_request(name, p, bench, la, keep)
            except (RuntimeError, subprocess.TimeoutExpired) as x:
                bad, notes = [f"bench: {x}"], ""
            say(f"{name:16s} {'PASS' if not bad else 'FAIL'}  {notes}"
                + "".join("\n    " + x for x in bad))
            if bad:
                failed.append(name)
    say("heldoutjudge: " + (f"PASS: {len(names)} of {len(names)}" if not failed
                            else f"FAIL: {', '.join(failed)}"))
    if out:
        (out / "heldoutjudge.log").write_text("\n".join(log) + "\n", newline="\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
