"""Protocols nobody designed, and signals that are not protocols, for test_esclearn.py.

draw() picks a protocol at random: the coding (pulse distance, pulse width, pulse
length, Manchester), a time unit from 2 us to 2 ms, the ratio of the long symbol to the
short (2 to 4), the bits per frame, a leader or none, the idle level, a carrier or none,
jitter and the sampling of the recording. frames() encodes random words in it, and
records() turns frames into one pin's records. Words follow esclearn's numbering: the
first bit sent is bit 0, a Manchester 1 ends with the mark when the pin idles low or has
a carrier, else starts with it. nonprotocol() makes structured signals that carry no
data: a square wave, servo pulses of any width, tone bursts, pulses at random times.
"""
from __future__ import annotations

import math
import random

from esclearn.core import word_size

CODINGS = {"distance": "pulse distance", "width": "pulse width", "length": "pulse length",
           "manchester": "Manchester"}


def draw(rng: random.Random) -> dict:
    p = {"coding": rng.choice(list(CODINGS))}
    p["u"] = 50 * 10 ** rng.uniform(0, 3)                     # ticks: 2 us .. 2 ms
    p["k"] = rng.uniform(2.0, 4.0)
    p["nbits"] = rng.choice([8, 12, 16, 20, 24, 32, 48, 64])
    p["idle"] = rng.randint(0, 1)
    period = 25e6 / rng.uniform(30e3, 60e3)
    p["carrier"] = ((period, period * rng.uniform(0.25, 0.5))
                    if p["u"] >= 12 * period and rng.random() < 0.5 else None)
    p["leader"] = ((rng.uniform(2.5 * p["k"], 16) * p["u"], rng.uniform(1.8 * p["k"], 10) * p["u"])
                   if p["coding"] != "manchester" and rng.random() < 0.6 else None)
    p["frames"] = rng.randint(4, 8)
    p["jitter"] = rng.choice([0.0, 0.02, 0.05])
    q = rng.choice([1, 2.5, 25])                               # exact, 10 MHz, 1 MHz
    p["q"] = q if p["u"] >= 8 * q else 1
    return p


def encode(p: dict, bits: list[int]) -> list[tuple]:
    """(mark, space) pairs of one frame; the last space is None (the gap follows)."""
    u, k = p["u"], p["k"]
    pairs = [p["leader"]] if p["leader"] else []
    if p["coding"] == "manchester":
        one = (0, 1) if (p["carrier"] or p["idle"] == 0) else (1, 0)
        halves = [h for b in bits for h in (one if b else (1 - one[0], 1 - one[1]))]
        while halves and halves[0] == 0:                       # a frame starts at a mark
            halves.pop(0)
        runs = []
        for h in halves:
            if runs and runs[-1][0] == h:
                runs[-1][1] += 1
            else:
                runs.append([h, 1])
        for i in range(0, len(runs), 2):
            pairs.append((runs[i][1] * u, runs[i + 1][1] * u if i + 1 < len(runs) else None))
        return pairs
    for b in bits:
        pairs.append({"distance": (u, k * u if b else u),
                      "width": (k * u, u) if b else (u, k * u),
                      "length": (k * u if b else u, u)}[p["coding"]])
    if p["coding"] == "distance":
        pairs.append((u, None))                                # a trailer ends the last bit
    else:
        pairs[-1] = (pairs[-1][0], None)
    return pairs


def frames(p: dict, rng: random.Random, n=None, scale=lambda i: 1.0):
    """(words sent, frames): n frames of random words; frame i stretched by scale(i)."""
    unit = word_size(p["nbits"])
    sent = [[rng.getrandbits(unit) for _ in range(p["nbits"] // unit)]
            for _ in range(n or p["frames"])]
    out = []
    for i, fr in enumerate(sent):
        bits = [w >> j & 1 for w in fr for j in range(unit)]
        out.append([(m * scale(i), None if s is None else s * scale(i)) for m, s in encode(p, bits)])
    return sent, out


def records(p: dict, frs, rng: random.Random) -> list[tuple[int, int]]:
    """One pin's records: frames with jitter, gaps of 6 to 20 times the longest run,
    the carrier if any, edges moved to the next sample."""
    j = p["jitter"]
    jit = lambda d: d * (1 + rng.uniform(-j, j))               # noqa: E731
    longest = max(max(m, s or 0) for fr in frs for m, s in fr)
    t, marks = 1000.0 + rng.uniform(0, 5) * p["u"], []
    for fr in frs:
        for m, s in fr:
            m = jit(m)
            marks.append((t, t + m))
            t += m + (jit(s) if s is not None else 0)
        t += rng.uniform(6, 20) * longest
    idle, q = p["idle"], p["q"]
    snap = lambda x: round(math.ceil(x / q) * q)               # noqa: E731
    rec = [(0, idle)]
    for a, b in marks:
        if p["carrier"]:
            per, hi = p["carrier"]
            x = a
            while x < b:
                rec += [(snap(x), 1 - idle), (snap(min(x + hi, b)), idle)]
                x += per
        else:
            rec += [(snap(a), 1 - idle), (snap(b), idle)]
    rec.append((snap(t), idle))
    out = []
    for tt, v in rec:
        if out and out[-1][0] == tt:
            out[-1] = (tt, v)
        elif not out or out[-1][1] != v:
            out.append((tt, v))
    return out


def nonprotocol(rng: random.Random, kind: str) -> list[tuple[int, int]]:
    rec, t, lv = [(0, 0)], 1000.0, 0
    if kind == "square":
        half = rng.uniform(50, 50_000)
        for _ in range(400):
            lv ^= 1
            rec.append((round(t), lv))
            t += half * (1 + rng.uniform(-0.01, 0.01))
    elif kind == "servo":                                       # 1-2 ms every 20 ms
        for _ in range(60):
            w = rng.uniform(25_000, 50_000)
            rec += [(round(t), 1), (round(t + w), 0)]
            t += 500_000
    elif kind == "bursts":                                      # tone bursts of any length
        for _ in range(30):
            n, per = rng.randint(3, 40), rng.uniform(100, 3000)
            for _ in range(n):
                rec += [(round(t), 1), (round(t + per / 2), 0)]
                t += per
            t += rng.uniform(20, 200) * per
    else:                                                       # equal pulses at random times
        w = rng.uniform(100, 5000)
        for _ in range(200):
            rec += [(round(t), 1), (round(t + w), 0)]
            t += w + rng.expovariate(1 / (10 * w))
    rec.append((round(t + 1000), 0))
    return rec
