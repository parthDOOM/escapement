"""Asynchronous serial (UART-style NRZ): a character is a start bit away from idle, data
bits LSB first, an optional parity bit and stop bits at idle; every run is a whole number
of bit times. UART, MIDI (31250 baud), DMX512 (250 kbaud, 8N2, a break and a mark after
break before each packet).

The bit time comes from the run lengths; the format is the first of FORMATS that decodes
every character with a stop bit at idle and the right parity. Messages are split at long
idle stretches, or at breaks (a run away from idle longer than a character) when the
recording has them."""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

from .core import (GAP_RATIO, MIN_SAMPLES, RAW_BITS, LearnError, Learned, Recording, frames,
                   gap_threshold, ticks, us)

FORMATS = [(8, "N"), (8, "E"), (8, "O"), (7, "E"), (7, "O"), (7, "N"), (9, "N"), (6, "N"),
           (5, "N")]
BAUDS = [300, 600, 1200, 2400, 4800, 9600, 14400, 19200, 31250, 38400, 57600, 76800, 115200,
         230400, 250000, 460800, 500000, 921600, 1000000, 2000000, 3000000]
MIN_CHARS = 4
TOL = 0.2            # a run is k bit times give or take this many bit times


def _parity(v, kind):
    ones = bin(v).count("1") & 1
    return ones if kind == "E" else ones ^ 1


def _timeline(fr):
    """[(start tick, level)] of a frame's runs from its first mark (level 1 = away from
    idle), and its end."""
    out, t = [], 0
    for m, s in fr:
        out.append((t, 1))
        t += m
        if s is not None:
            out.append((t, 0))
            t += s
    out.append((t, 0))
    return out, t


def _chars(fr, bit, nd, par, slack):
    """[(value, start tick, idle bits after it or None)] of a frame (a message), and the
    breaks in it, or None at a framing or parity error, or if an edge inside a character
    is more than `slack` ticks off a bit boundary (a UART changes the line only there, so
    a pulse at an arbitrary time is not a data bit)."""
    tl, end = _timeline(fr)
    times = [t for t, _ in tl]

    def level(t):
        return tl[max(0, bisect.bisect_right(times, t) - 1)][1]

    nb = nd + (par != "N")
    out, breaks, i = [], [], 0
    while i < len(tl):
        t0, lv = tl[i]
        if lv != 1:
            i += 1
            continue
        nxt = tl[i + 1][0] if i + 1 < len(tl) else end
        if nxt - t0 > (nb + 1.5) * bit:     # away from idle for longer than a character
            breaks.append(nxt - t0)
            i += 1
            continue
        vals = [1 - level(t0 + (k + 1.5) * bit) for k in range(nb)]   # data: idle = 1
        if level(t0 + (nb + 1.5) * bit) != 0:
            return None                     # no stop bit
        stop_at = t0 + (nb + 1) * bit
        j = i + 1
        while j < len(tl) and tl[j][0] <= stop_at + slack:
            off = (tl[j][0] - t0) % bit
            if min(off, bit - off) > slack:
                return None
            j += 1
        v = sum(b << k for k, b in enumerate(vals[:nd]))
        if par != "N" and vals[nd] != _parity(v, par):
            return None
        j = bisect.bisect_right(times, stop_at + 0.5 * bit)
        while j < len(tl) and tl[j][1] != 1:
            j += 1
        after = (tl[j][0] - stop_at) / bit if j < len(tl) else None
        out.append((v, t0, after))
        i = j
    return out, breaks


@dataclass
class Serial(Learned):
    name: str
    idle: int
    bit: float                       # ticks per bit
    baud: int | None                 # the standard rate within 1% of the measured one
    nd: int
    parity: str                      # N, E, O
    stops: int
    brk: int | None                  # break length, ticks (None: no breaks)
    mab: int | None                  # mark after break, ticks
    gap_thr: float | None
    gap: int
    frames: list[list[int]]
    other: int
    bit_lo: float
    bit_hi: float
    source: str = "capture"
    notes: list[str] = field(default_factory=list)
    quant: float = 1
    carrier = None
    coding: str = "serial"

    @property
    def unit(self):
        return self.nd

    @property
    def slack(self):
        """How far off a bit boundary an edge may lie (as when learning)."""
        return TOL * self.bit + self.quant

    def regroup(self, unit=None, msb=False):
        raise LearnError("serial: a character is a word (LSB first, as UARTs send)")

    def to_bytes(self, words):
        return list(words) if self.nd == 8 else None

    def from_bytes(self, data):
        return list(data)

    @property
    def default_source(self) -> str:
        """tape for packets after a break (the host writes them), else the mailbox."""
        return "tape" if self.brk else "mailbox"

    def frame_list(self, records, pin=0):
        from .core import edges, envelope, runs_of
        env = envelope(runs_of(edges(records, pin)), self.idle, None)
        return _split(env, self.gap_thr, self.bit, self.nd + (self.parity != "N"),
                      self.brk is not None)

    def words(self, fr):
        r = _chars(fr, self.bit, self.nd, self.parity, self.slack)
        if r is None or not r[0]:
            return None
        chars, breaks = r
        if (self.brk is not None) != bool(breaks):
            return None
        return [v for v, _, _ in chars]

    def timings(self):
        return [self.bit, self.brk, self.mab, self.gap]

    def run_bits(self, d) -> float:
        k = round(d / self.bit)
        whole = 1 <= k <= 12 and abs(d - k * self.bit) <= TOL * self.bit + self.quant
        return math.log2(13) if whole else RAW_BITS

    def frame_bits(self, fr) -> float:
        """Each character's data bits, and where the next one starts: the idle bits after
        it (log2: one bit for back to back) and the length of each break."""
        chars, breaks = _chars(fr, self.bit, self.nd, self.parity, self.slack)
        return (self.nd * len(chars)
                + sum(math.log2(2 + a) for _, _, a in chars if a is not None)
                + sum(math.log2(2 + b / self.bit) for b in breaks))

    def compare(self, gen, rec, slack):
        """The bit time of the generated message (from its runs) within the recording's
        spread of bit times."""
        rs = [d for m, s in gen for d in (m, s) if d is not None and d < 12 * self.bit]
        if not rs:
            return ["no runs generated"], 0.0, 0
        k = [max(1, round(d / self.bit)) for d in rs]
        b = sum(rs) / sum(k)
        err = abs(b - self.bit)
        fails = []
        if not self.bit_lo - slack / 2 <= b <= self.bit_hi + slack / 2:   # one run-clock tick
            fails.append(f"bit time {b:.1f} ticks generated, recorded {self.bit_lo:.1f} "
                         f"to {self.bit_hi:.1f}")
        return fails, err / self.bit, round(err)

    def fmt(self) -> str:
        return f"{self.nd}{self.parity}{self.stops}"

    def describe(self) -> str:
        rate = f"{self.baud} baud" if self.baud else f"{25e6 / self.bit:.0f} baud (measured)"
        return f"serial {self.fmt()}, {rate}" + (", break before each packet" if self.brk else "")

    def summary(self) -> list[str]:
        lines = [f"{self.describe()}, idle {'high' if self.idle else 'low'}",
                 f"bit {us(self.bit)} (recorded {us(self.bit_lo)} to {us(self.bit_hi)})"]
        if self.brk:
            lines.append(f"break {us(self.brk)}, mark after break {us(self.mab)}")
        lines += self.check_lines()
        lines += [f"message {' '.join(f'{v:02x}' for v in fr)}" for fr in self.frames[:20]]
        if len(self.frames) > 20:
            lines.append(f"... {len(self.frames)} messages")
        if self.other:
            lines.append(f"{self.other} message(s) did not decode (raw replay sends them)")
        return lines + self.notes

    def stored(self, v):
        """A character as the firmware takes it: the parity bit above the data, all
        inverted on an idle-low pin (the data phase drives the bit's level)."""
        w = v | (_parity(v, self.parity) << self.nd if self.parity != "N" else 0)
        nb = self.nd + (self.parity != "N")
        return w if self.idle else ~w & ((1 << nb) - 1)

    def table(self, source="mailbox", words=None, context=0, pin=0, notes=None) -> dict:
        consts = {}
        notes = {} if notes is None else notes
        if self.baud:
            consts["BIT"] = f"(CLK_KHZ * 1000 + {self.baud // 2}) // {self.baud}"
            notes["BIT"] = f"{self.baud} baud; measured {us(self.bit)}"
        else:
            consts["BIT"] = ticks(self.bit)
            notes["BIT"] = f"bit {us(self.bit)}, {25e6 / self.bit:.0f} baud"
        act, quiet = 1 - self.idle, self.idle
        sym = {"start": [[{"OUT": act}, "BIT"]], "bit": [[{"OUT": "data"}, "BIT"]],
               "stop": [[{"OUT": quiet}, f"{self.stops} * BIT" if self.stops > 1 else "BIT"]]}
        packet = {}
        if self.brk:
            consts["BREAK"], consts["MAB"] = ticks(self.brk), ticks(self.mab)
            notes["BREAK"], notes["MAB"] = f"break {us(self.brk)}", f"mark after break {us(self.mab)}"
            sym["break"] = [[{"OUT": act}, "BREAK"]]
            sym["mab"] = [[{"OUT": quiet}, "MAB"]]
            packet["before"] = ["break", "mab"]
        nb = self.nd + (self.parity != "N")
        spec = ["start", {"bits": nb, "order": "lsb", "symbol": "bit"}, "stop"]
        if source == "mailbox":
            src = {"kind": "mailbox"}
        elif source == "constant":
            src = {"kind": "constant",
                   "words": [self.stored(v) for v in (words if words is not None else self.frames[0])]}
        elif source == "tape":
            n = len(words) if words is not None else max(len(f) for f in self.frames)
            src = {"kind": "tape", "base": 0, "count": "tape", "units_per_word": _units(n, nb)}
            if not self.brk:                # packets apart, as recorded
                consts["GAP"] = ticks(self.gap)
                notes["GAP"] = f"{us(self.gap)} between messages"
                sym["gap"] = [[{"OUT": quiet}, "GAP"]]
                packet["after"] = ["gap"]
        else:
            raise LearnError(f"source {source!r}: mailbox, constant or tape")
        t = self.header(consts, sym, spec, src, packet, pin, context)
        if self.parity != "N":
            t["refs"].append(f"{self.fmt()}: each word holds the parity bit above the data "
                             "(esclearn's Serial.stored adds it)")
        if not self.idle:
            t["refs"].append("idle low: the words hold the inverted bits")
        return t

    def tape_words(self, chars):
        """The tape for the tape source: the word count, then the characters packed
        units_per_word to a word, from bit 0 up."""
        nb = self.nd + (self.parity != "N")
        u = _units(len(chars), nb)
        words = [sum(self.stored(c) << (nb * j) for j, c in enumerate(chars[k:k + u]))
                 for k in range(0, len(chars), u)]
        return [len(words)] + words

    def replay(self, chars):
        """(table, tape) that sends one recorded message: from the tape (a long packet
        does not fit the program as constants), repeated after the gap or the break."""
        return self.table("tape", chars), self.tape_words(chars)


def _units(n, nb):
    """Characters per tape word for an n-character packet: as many as fit 32 bits and
    divide n, with the packet in at most 255 words."""
    return next((u for u in (4, 3, 2, 1) if u * nb <= 32 and n % u == 0 and -(-n // u) <= 255), 1)


def _split(env, thr, bit, nb, by_break):
    """Messages: at breaks if the recording has them, else at idle stretches of thr."""
    if not by_break:
        return frames(env, thr)
    out, cur = [], []
    for k, (m, d, _) in enumerate(env):
        if m and d == math.inf:            # the recording ends inside a mark
            break
        if m and d > (nb + 1.5) * bit:     # a break opens a message
            if cur:
                out.append(cur)
            cur = []
        if m:
            sp = env[k + 1][1] if k + 1 < len(env) else None
            cur.append((d, None if sp == math.inf else sp))
    if cur:
        out.append(cur)
    return [fr for fr in out if fr and fr[0][0] > (nb + 1.5) * bit]


def _attempt(env, bit, nd, par, slack):
    """(messages, decoded messages, gap threshold, breaks?) if the format (nd, par)
    decodes at least 80% of the messages and MIN_CHARS characters, else None."""
    nb = nd + (par != "N")
    by_break = any(m and d != math.inf and d > (nb + 1.5) * bit for m, d, _ in env)
    thr = None if by_break else gap_threshold_serial(env, bit, nb)
    msgs = _split(env, thr, bit, nb, by_break)
    good = [(fr, r) for fr in msgs if (r := _chars(fr, bit, nd, par, slack)) is not None and r[0]]
    if not msgs or len(good) < max(1, 0.8 * len(msgs)) or sum(len(r[0]) for _, r in good) < MIN_CHARS:
        return None
    return msgs, good, thr, by_break


def learn_serial(rec: Recording, name, source) -> Serial:
    if rec.carrier:
        raise LearnError("a carrier")
    env = rec.env
    # marks (a start bit and the zeros after it) are whole bit times; the idle between
    # characters need not be
    durs = [d for k, (m, d, _) in enumerate(env) if m and 0 < k < len(env) - 1]
    if len(durs) < 10:
        raise LearnError("too few marks")
    shortest = sorted(durs)[max(0, len(durs) // 50)]          # ignore the odd glitch
    inner = [d for d in durs if d < 12 * shortest]
    ks = [max(1, round(d / shortest)) for d in inner]
    bit = sum(inner) / sum(ks)
    if bit < MIN_SAMPLES * rec.quant:
        raise LearnError(f"a bit time of {bit / rec.quant:.1f} samples: the recording does not "
                         "resolve it")
    slack = TOL * bit + rec.quant
    off = sum(abs(d - k * bit) > slack for d, k in zip(inner, ks))
    if off > 0.05 * len(inner):
        raise LearnError("marks are not whole numbers of one bit time")
    for nd, par in FORMATS:
        got = _attempt(env, bit, nd, par, slack)
        if got:
            break
    else:
        raise LearnError("no character format decodes the recording")
    if (nd, par) == (8, "N"):               # 8N1 also reads 7E1, 7O1 and 7N1 (idle between)
        vals = [v for _, r in got[1] for v, _, _ in r[0]]
        top = {v >> 7 for v in vals}
        for p7 in ("E", "O"):
            if top == {0, 1} and all(v >> 7 == _parity(v & 0x7F, p7) for v in vals):
                nd, par = 7, p7
        if top == {1} and (alt := _attempt(env, bit, 7, "N", slack)):
            nd, par, got = 7, "N", alt
        if nd == 7 and par != "N":
            got = _attempt(env, bit, nd, par, slack) or got
    msgs, good, thr, by_break = got
    afters = [a for _, r in good for _, _, a in r[0][:-1] if a is not None]
    stops = 2 if afters and 1.75 <= min(afters) < 2.5 else 1
    per = [d / max(1, round(d / bit)) for d in inner]
    baud = next((b for b in BAUDS if abs(25e6 / b - bit) <= 0.01 * bit), None)
    brks = [b for _, r in good for b in r[1]]
    mab = None
    if by_break:
        mabs = [fr[0][1] for fr, _ in good if fr[0][1] is not None]
        mab = round(sorted(mabs)[len(mabs) // 2]) if mabs else round(3 * bit)
    gaps = [d for k, (m, d, _) in enumerate(env)
            if not m and thr is not None and d != math.inf and d >= thr and 0 < k < len(env) - 1]
    gap = round(sorted(gaps)[len(gaps) // 2]) if gaps else round(GAP_RATIO * 12 * bit)
    return Serial(name=name, idle=rec.idle, bit=bit, baud=baud, nd=nd, parity=par,
                  stops=stops, brk=round(sorted(brks)[len(brks) // 2]) if brks else None,
                  mab=mab, gap_thr=thr, gap=gap,
                  frames=[[v for v, _, _ in r[0]] for _, r in good],
                  other=len(msgs) - len(good), bit_lo=min(per), bit_hi=max(per),
                  source=source, quant=rec.quant)


def gap_threshold_serial(env, bit, nb):
    """Messages end at an idle stretch of more than two characters (or the recording's
    own gap jump, if longer)."""
    thr = gap_threshold(env)
    least = 2 * (nb + 2) * bit
    return max(thr or 0, least)
