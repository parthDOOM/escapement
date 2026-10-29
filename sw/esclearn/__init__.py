"""Protocols from recordings (sw/esclearn): a captured waveform in, a timing table out.

learn() takes one pin of a recording (esctape records: a chip capture, a VCD or a sigrok
session) and works out the protocol's timing table in escgen's format: the carrier if
there is one, the gaps between frames, the mark and space lengths, the two symbols that
carry the bits, and the leader and trailer around them. It decodes every captured frame
into words, so the generated firmware can send them again, or new ones. Nothing is
guessed silently: a recording that fits no model raises LearnError, and raw replay
(esctape.build_replay) is the way to re-send it unchanged.

check() closes the loop: it builds the table with escgen, runs it on the ISS once per
captured frame, and compares every mark and space with the recording; the model's own
decoder reads the generated frames back.

Scope: one pin; two-symbol codes (pulse distance, pulse width, or any other pair of
mark/space symbols) with an optional carrier, leader and trailer. A mark is a run at the
non-idle level; the idle level is the one the recording starts and ends at. Durations are
ticks of the 25 MHz chip clock (40 ns), as in esctape's records.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from statistics import median

CLOCK_HZ = 25_000_000
SPLIT = 1.25         # sorted durations further apart than this ratio are different symbols
SPREAD = 1.5         # and one symbol spans at most this ratio (jitter up to about 20%)
GAP_RATIO = 4.0      # a space this much longer than every shorter one ends a frame
MIN_BITS = 4         # a frame carries at least this many bits
MIN_PULSES = 16      # carrier: at least this many pulses in the recording...
MIN_BURST = 8        # ...a median burst of at least this many, and bursts apart by at least
BURST_SPACE = 2.5    # this many periods (IR: NEC bits 22 pulses, 21 periods apart)


class LearnError(ValueError):
    pass


# --------------------------------------------------------------------------- runs

def edges(records, pin=0) -> list[tuple[int, int]]:
    """(tick, level) of one pin: the first record's level, then every change."""
    out = []
    for t, lv in records:
        v = (lv >> pin) & 1
        if not out or v != out[-1][1]:
            out.append((t, v))
    return out


def _runs(ed):
    """[(level, ticks)] between consecutive edges, then the run after the last edge,
    which is open: math.inf ticks."""
    closed = [(a[1], b[0] - a[0]) for a, b in zip(ed, ed[1:])]
    return closed + ([(ed[-1][1], math.inf)] if ed else [])


@dataclass
class Cluster:
    lo: int
    hi: int
    center: int
    n: int
    q: float = 1         # the recording's resolution, ticks

    def holds(self, v) -> bool:
        return self.lo / SPLIT - self.q <= v <= self.hi * SPLIT + self.q


def clusters(values, q=1) -> list[Cluster]:
    """1-D clusters, shortest first: sorted values split where neighbours differ by more
    than SPLIT, or where a cluster would span more than SPREAD; differences of up to one
    sample (q ticks) never split, nor spans of up to two (a coarsely sampled recording)."""
    vs = sorted(values)
    out, cur = [], []
    for v in vs:
        if cur and ((v > cur[-1] * SPLIT and v - cur[-1] > q)
                    or (v > cur[0] * SPREAD and v - cur[0] > 2 * q)):
            out.append(cur)
            cur = []
        cur.append(v)
    if cur:
        out.append(cur)
    return [Cluster(c[0], c[-1], round(median(c)), len(c), q) for c in out]


def _nearest(cl: list[Cluster], v):
    best = [c for c in cl if c.holds(v)]
    if not best:
        return None
    return min(range(len(cl)), key=lambda i: abs(math.log(v / cl[i].center))
               if cl[i] in best else math.inf)


# --------------------------------------------------------------------------- carrier

@dataclass
class Carrier:
    period: int          # ticks
    high: int            # ticks of each pulse

    @property
    def khz(self) -> float:
        return CLOCK_HZ / self.period / 1000


def find_carrier(runs, idle) -> Carrier | None:
    """A carrier: the shortest mark-to-mark period dominates, the marks fill part of it,
    and the pulses come in bursts separated by spaces several periods long."""
    per, highs = [], []
    for (lv, d), (lv2, d2) in zip(runs, runs[1:]):
        if d2 == math.inf:
            break
        if lv != idle:
            per.append(d + d2)
            highs.append(d)
    if len(per) < MIN_PULSES:
        return None
    small = clusters(per)[0]
    if small.n < 0.6 * len(per):
        return None
    p = small.center
    inside = [h for h, q in zip(highs, per) if small.lo <= q <= small.hi]
    widths = clusters(inside)
    if max(c.n for c in widths) < 0.9 * len(inside):
        return None                        # two pulse widths: data (pulse width), not a carrier
    hi = round(median(inside))
    if not 0.1 * p <= hi <= 0.9 * p:
        return None
    env = envelope(runs, idle, Carrier(p, hi))
    marks = [n for is_mark, _, n in env if is_mark]
    spaces = [d for k, (is_mark, d, _) in enumerate(env)      # between bursts: not the lead-in
              if not is_mark and d != math.inf and k > 0] or [math.inf]
    if len(marks) < 2 or median(marks) < MIN_BURST or min(spaces) < BURST_SPACE * p:
        return None
    return Carrier(p, hi)


def envelope(runs, idle, carrier: Carrier | None):
    """[(is_mark, ticks, pulses)]: with a carrier, each burst of pulses closer than 1.5
    periods becomes one mark of `pulses` whole periods (escgen's burst), and the space
    after it runs to the next burst."""
    if carrier is None:
        return [(lv != idle, d, 0) for lv, d in runs]
    p = carrier.period
    out, i = [], 0
    while i < len(runs):
        lv, d = runs[i]
        if lv == idle:
            out.append((False, d, 0))
            i += 1
            continue
        span, n = 0, 0                     # a burst: marks and the short spaces inside it
        while i < len(runs):
            lv, d = runs[i]
            if lv != idle:
                n += 1
                span += d
                if d == math.inf:          # the recording ends inside a burst
                    return out + [(True, math.inf, n)]
            elif d < 1.5 * p - (runs[i - 1][1] if i else 0) and i + 1 < len(runs):
                span += d
            else:
                break
            i += 1
        out.append((True, n * p, n))
        rest = span - n * p                # the burst's last space ends at a whole period
        if i < len(runs):
            lv, d = runs[i]
            out.append((False, d + rest, 0))
            i += 1
    return out


# --------------------------------------------------------------------------- frames

def gap_threshold(env) -> float | None:
    """The space length that ends a frame: the middle of the first jump of at least
    GAP_RATIO between sorted space lengths (longer pauses, between button presses say,
    are gaps too). Without such a jump (noise between frames fills every length),
    GAP_RATIO times the 90th percentile space, if some space reaches it; else None."""
    sp = sorted(d for k, (m, d, _) in enumerate(env)       # not the lead-in: its length
                if not m and d != math.inf and k > 0)      # is only when recording began
    for a, b in zip(sp, sp[1:]):
        if b >= GAP_RATIO * a:
            return math.sqrt(a * b)
    if len(sp) >= 10 and sp[-1] >= GAP_RATIO * sp[int(0.9 * (len(sp) - 1))]:
        return GAP_RATIO * sp[int(0.9 * (len(sp) - 1))]
    return None


def frames(env, thr):
    """[[(mark, space), ...]] split at spaces of at least thr; a frame's last pair has
    space None. A frame cut by the start or the end of the recording is dropped."""
    out, cur, i = [], [], 0
    while i < len(env):
        m, d, _ = env[i]
        if not m:
            i += 1
            continue
        if d == math.inf:                  # the recording ends inside a mark
            break
        sp = env[i + 1][1] if i + 1 < len(env) else math.inf
        if sp == math.inf or (thr is not None and sp >= thr):
            cur.append((d, None))
            out.append(cur)
            cur = []
        else:
            cur.append((d, sp))
        i += 2
    if env and env[0][0] and out:          # the recording starts inside a frame
        out = out[1:]
    return out


# --------------------------------------------------------------------------- model

@dataclass
class Model:
    """A learned protocol: its symbols as clusters of recorded lengths, the frame shape,
    and the words of every recorded frame. Decodes recordings and writes timing tables."""
    name: str
    idle: int
    carrier: Carrier | None
    coding: str                      # pulse distance / pulse width / pulse length / two symbols
    marks: list[Cluster]
    spaces: list[Cluster]
    zero: tuple[int, int]            # (mark cluster, space cluster)
    one: tuple[int, int]
    leader: list[tuple[int, int]]    # (mark cluster, space cluster) pairs before the bits
    trailer: list[tuple[int, int | None]]   # pairs after the bits; the last has no space
    nbits: int                       # bits per frame
    unit: int                        # bits per word
    last_bit_open: bool              # the last bit's space runs into the gap (marks differ)
    gap: int                         # ticks from a frame's last mark end to the next frame
    gap_thr: float | None            # a space at least this long ends a frame
    period: int                      # pulse width: ticks per bit
    frames: list[list[int]]          # words of every modelled frame, in capture order
    other: int                       # frames that did not fit the model
    source: str = "capture"
    notes: list[str] = field(default_factory=list)
    quant: float = 1                 # the recording's resolution, ticks

    # ---- decoding with the model (no re-clustering)

    def _pairs(self, fr):
        out = []
        for m, s in fr:
            mi = _nearest(self.marks, m)
            si = None if s is None else _nearest(self.spaces, s)
            if mi is None or (s is not None and si is None):
                return None
            out.append((mi, si))
        return out

    def _bits(self, pairs):
        """The frame's bits if it has the model's shape, else None."""
        nl, nt = len(self.leader), len(self.trailer)
        if [tuple(p) for p in pairs[:nl]] != [tuple(p) for p in self.leader]:
            return None
        body = pairs[nl:len(pairs) - nt] if nt else pairs[nl:]
        tail = pairs[len(pairs) - nt:] if nt else []
        if [tuple(p) for p in tail] != [tuple(p) for p in self.trailer]:
            return None
        if len(body) != self.nbits:
            return None
        bits = []
        for k, (mi, si) in enumerate(body):
            if si is None and self.last_bit_open and k == len(body) - 1:
                b = 0 if mi == self.zero[0] else 1 if mi == self.one[0] else None
            else:
                b = 0 if (mi, si) == self.zero else 1 if (mi, si) == self.one else None
            if b is None:
                return None
            bits.append(b)
        return bits

    def words_of(self, bits):
        return [sum(b << i for i, b in enumerate(bits[k:k + self.unit]))
                for k in range(0, len(bits), self.unit)]

    def decode(self, records, pin=0) -> list[list[int]]:
        """Words of every frame in `records` that has the model's shape."""
        ed = edges(records, pin)
        env = envelope(_runs(ed), self.idle, self.carrier)
        out = []
        for fr in frames(env, self.gap_thr):
            pairs = self._pairs(fr)
            bits = pairs and self._bits(pairs)
            if bits:
                out.append(self.words_of(bits))
        return out

    # ---- the timing table

    def summary(self) -> list[str]:
        """What was found, one line per item, then every frame's words."""
        us = lambda t: f"{t / 25:.1f} us"           # noqa: E731
        m = lambda i: us(self.marks[i].center)       # noqa: E731
        s = lambda i: us(self.spaces[i].center)      # noqa: E731
        car = ""
        if self.carrier:
            duty = self.carrier.high / self.carrier.period
            car = f", carrier {self.carrier.khz:.2f} kHz (duty {duty:.2f})"
        idle = "high" if self.idle else "low"
        lines = [f"{self.coding}, {self.nbits} bits per frame{car}, idle {idle}"]
        if self.leader:
            lines.append("leader " + ", ".join(f"mark {m(a)} space {s(b)}" for a, b in self.leader))
        lines.append(f"zero: mark {m(self.zero[0])} space {s(self.zero[1])}; "
                     f"one: mark {m(self.one[0])} space {s(self.one[1])}")
        if self.trailer:
            lines.append("trailer " + ", ".join(f"mark {m(a)}" + ("" if b is None else f" space {s(b)}")
                                                for a, b in self.trailer))
        lines.append(f"gap {us(self.gap)}")
        for fr in self.frames:
            lines.append("frame " + " ".join(f"0x{w:0{(self.unit + 3) // 4}x}" for w in fr))
        if self.other:
            lines.append(f"{self.other} frame(s) of another shape not modelled "
                         "(raw replay sends them)")
        return lines + self.notes

    def table(self, source="mailbox", words=None, context=0, pin=0, notes=None) -> dict:
        """escgen table. source: mailbox (one word per packet, from the host), constant
        (`words`, default the first captured frame's, sent once) or tape (pulse width
        only: the host writes the packet to the tape; word 0 holds the count). `notes`,
        if given, receives a comment per constant (what was measured)."""
        consts, names = {}, {}
        notes = {} if notes is None else notes
        us = lambda t: f"{t / 25:.1f} us"           # noqa: E731

        def seen(c: Cluster):
            return f"{c.n} seen, {us(c.lo)} to {us(c.hi)}"

        def ticks(t):
            return f"CLK_KHZ * {t} // 25000"

        def const(prefix, value, expr, note):
            key = (prefix, value)
            if key not in names:
                names[key] = f"{prefix}{sum(1 for k in names if k[0] == prefix) + 1}"
                consts[names[key]] = expr
                notes[names[key]] = note
            return names[key]

        mk = 1 - self.idle
        if self.carrier:
            consts["PERIOD"] = ticks(self.carrier.period)
            consts["HIGH"] = ticks(self.carrier.high)
            notes["PERIOD"] = f"{self.carrier.period / 25:.2f} us: {self.carrier.khz:.2f} kHz"
            notes["HIGH"] = f"{self.carrier.high / 25:.2f} us per pulse"

        def mark(i):
            c = self.marks[i].center
            if self.carrier:
                n = max(1, round(c / self.carrier.period))
                note = f"{n} pulses, {us(n * self.carrier.period)}; " + seen(self.marks[i])
                return [{"OUT": "carrier"}, const("M", n, f"{n} * PERIOD", note)]
            return [{"OUT": mk}, const("M", c, ticks(c), f"mark {us(c)}; " + seen(self.marks[i]))]

        def space(i):
            c = self.spaces[i].center
            return [{"OUT": self.idle},
                    const("S", c, ticks(c), f"space {us(c)}; " + seen(self.spaces[i]))]

        sym = {}
        if self.leader:
            sym["leader"] = [ph for a, b in self.leader for ph in (mark(a), space(b))]
        data_phase = source == "tape" or (self.coding == "pulse width" and not self.carrier)
        if data_phase:
            if self.coding != "pulse width" or self.carrier:
                raise LearnError("the tape source needs a pulse-width code without a carrier")
            h0, h1 = self.marks[self.zero[0]].center, self.marks[self.one[0]].center
            consts["H0"], consts["H1"], consts["T"] = ticks(h0), ticks(h1), ticks(self.period)
            notes["H0"] = f"zero: mark {us(h0)}; " + seen(self.marks[self.zero[0]])
            notes["H1"] = f"one: mark {us(h1)}; " + seen(self.marks[self.one[0]])
            notes["T"] = f"bit period {us(self.period)}"
            sym["bit"] = [[{"OUT": mk}, "H0"], [{"OUT": "data"}, "H1 - H0"],
                          [{"OUT": self.idle}, "T - H1"]]
            fieldspec = {"bits": self.unit, "order": "lsb", "symbol": "bit"}
        else:
            sym["zero"] = [mark(self.zero[0]), space(self.zero[1])]
            sym["one"] = [mark(self.one[0]), space(self.one[1])]
            fieldspec = {"bits": self.unit, "order": "lsb", "choose": ["zero", "one"]}
        if self.trailer:
            ph = []
            for a, b in self.trailer:
                ph.append(mark(a))
                if b is not None:
                    ph.append(space(b))
            sym["stop"] = ph
        gap = self.gap
        if self.last_bit_open:          # the last bit's own space is part of the gap
            gap -= (self.spaces[self.zero[1]].center + self.spaces[self.one[1]].center) // 2
        consts["GAP"] = ticks(max(1, gap))
        notes["GAP"] = f"{us(max(1, gap))} of quiet after a frame" + (
            "; the last bit's space comes first" if self.last_bit_open else "")
        sym["gap"] = [[{"OUT": self.idle}, "GAP"]]
        packet = {}
        if self.leader:
            packet["before"] = ["leader"]
        packet["after"] = (["stop"] if self.trailer else []) + ["gap"]
        per = self.nbits // self.unit
        if source == "mailbox":
            if per != 1:
                raise LearnError(f"a frame is {per} words: the mailbox source sends one per packet")
            src = {"kind": "mailbox"}
            packet["ack"] = True
        elif source == "constant":
            ws = list(words if words is not None else self.frames[0])
            src = {"kind": "constant", "words": [self._stored(w) for w in ws]}
        elif source == "tape":
            src = {"kind": "tape", "base": 0, "count": "tape"}
        else:
            raise LearnError(f"source {source!r}: mailbox, constant or tape")
        t = {"name": self.name,
             "title": f"Learned from {self.source}: {self.coding}, {self.nbits} bits per frame"
                      + (f", {self.carrier.khz:.1f} kHz carrier" if self.carrier else ""),
             "refs": [f"inferred by esclearn from {self.source} ({len(self.frames)} frames)"],
             "context": context,
             "pins": {"OUT": {"pin": pin, "idle": self.idle}},
             "constants": consts}
        if self.carrier:
            t["carrier"] = {"period": "PERIOD", "high": "HIGH"}
        t.update({"symbols": sym, "frame": [fieldspec], "source": src, "packet": packet,
                  "source_file": f"{self.name}.yaml"})
        if data_phase and self.idle:
            t["refs"].append("idle high: the data phase drives the inverse of each bit, "
                             "so the words hold the inverted bits")
        return t

    def table_yaml(self, source="mailbox", words=None, context=0, pin=0) -> str:
        """The table as a commented YAML file, as in protocols/ (escgen.load reads it)."""
        notes = {}
        t = self.table(source, words, context, pin, notes)
        L = [f"# {t['title']}", "# Learned by esclearn (python -m esclearn). What it found:"]
        lines = self.summary()
        L += [f"#   {s}" for s in lines[:12]] + (["#   ..."] if len(lines) > 12 else [])
        L += [f"name: {t['name']}", f"title: {_flow(t['title'])}", f"refs: {_flow(t['refs'])}",
              f"context: {t['context']}", "pins:"]
        L += [f"  {n}: {_flow(p)}" for n, p in t["pins"].items()]
        L.append("constants:")
        width = max(len(f"  {k}: {v}") for k, v in t["constants"].items()) + 2
        for k, v in t["constants"].items():
            line = f"  {k}: {_flow(v)}"
            L.append(f"{line.ljust(width)}# {notes[k]}" if notes.get(k) else line)
        if "carrier" in t:
            L.append(f"carrier: {_flow(t['carrier'])}")
        L.append("symbols:")
        width = max(len(k) for k in t["symbols"]) + 1
        L += [f"  {(k + ':').ljust(width)} {_flow(v)}" for k, v in t["symbols"].items()]
        L += [f"{k}: {_flow(t[k])}" for k in ("frame", "source", "packet")]
        return "\n".join(L) + "\n"

    def _stored(self, w):
        """A word as the firmware takes it (inverted for an idle-high data phase)."""
        if self.idle and self.coding == "pulse width" and not self.carrier:
            return ~w & ((1 << self.unit) - 1)
        return w


def _flow(v) -> str:
    """A value in YAML flow style; expressions stay plain, other strings are quoted."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}: {_flow(x)}" for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(_flow(x) for x in v) + "]"
    s = str(v)
    plain = re.fullmatch(r"[A-Za-z_(][\w\s*/+\-()]*", s) and s.lower() not in (
        "true", "false", "null", "yes", "no", "on", "off", "y", "n")
    return s if plain else json.dumps(s)


def learn(records, pin=0, name="learned", source="capture", quant=1) -> Model:
    """The model of one pin's recording, sampled every `quant` ticks (1 for a chip
    capture; 25e6 / sample rate for a logic analyser's). Raises LearnError if it fits
    none."""
    ed = edges(records, pin)
    if len(ed) < 4:
        raise LearnError("fewer than three edges")
    runs = _runs(ed)
    first, last = ed[0][1], ed[-1][1]
    idle = first if first == last else max(runs[:-1], key=lambda r: r[1])[0]
    car = find_carrier(runs, idle)
    if car is not None and idle:
        raise LearnError("a carrier on an idle-high pin (escgen bursts end low)")
    env = envelope(runs, idle, car)
    thr = gap_threshold(env)
    frs = frames(env, thr)
    if not frs:
        raise LearnError("no complete frame")
    marks = clusters([m for fr in frs for m, _ in fr], quant)
    spaces = clusters([s for fr in frs for _, s in fr if s is not None], quant)
    ids = [[(_nearest(marks, m), None if s is None else _nearest(spaces, s)) for m, s in fr]
           for fr in frs]
    count = Counter(p for fr in ids for p in fr[:-1])
    if len(count) < 2:
        raise LearnError("fewer than two symbols: nothing carries bits")
    (a, na), (b, nb) = count.most_common(2)
    if na + nb < 0.6 * sum(count.values()):
        raise LearnError("no pair of symbols dominates: not a two-symbol code")
    mc = lambda i: marks[i].center     # noqa: E731
    sc = lambda i: spaces[i].center    # noqa: E731
    if a[0] == b[0]:
        coding = "pulse distance"
        zero, one = (a, b) if sc(a[1]) < sc(b[1]) else (b, a)
    else:
        zero, one = (a, b) if mc(a[0]) < mc(b[0]) else (b, a)
        t0, t1 = mc(zero[0]) + sc(zero[1]), mc(one[0]) + sc(one[1])
        if abs(t0 - t1) <= 0.1 * max(t0, t1):
            coding = "pulse width"
        elif zero[1] == one[1]:
            coding = "pulse length"
        else:
            coding = "two symbols"
    bitset = {zero: 0, one: 1}
    shapes = []
    for fr in ids:
        body_end = len(fr) - 1
        i = 0
        while i < body_end and fr[i] not in bitset:
            i += 1
        j = i
        while j < body_end and fr[j] in bitset:
            j += 1
        leader, rest = fr[:i], fr[j:]
        nb_, open_ = j - i, False
        if zero[0] != one[0] and len(rest) == 1 and rest[0][0] in (zero[0], one[0]):
            nb_, open_, rest = nb_ + 1, True, []
        shapes.append((tuple(leader), nb_, tuple(rest), open_))
    tally = Counter(shapes)
    ranked = [s for s, _ in tally.most_common() if s[1] >= MIN_BITS]
    if not ranked:
        raise LearnError(f"no frame carries {MIN_BITS} bits or more")
    shape = ranked[0]                      # the commonest shape with bits (not repeat codes)
    if tally[shape] < 2 and len(shapes) > 2:
        raise LearnError(f"no frame shape repeats among {len(shapes)} frames")
    leader, nbits, trailer, open_ = shape
    if nbits < len(leader) + len(trailer):
        raise LearnError(f"{nbits} bits in a frame of {len(leader) + len(trailer) + nbits} "
                         "symbols: the bits do not carry the frame")
    unit = nbits if nbits <= 32 else next((d for d in range(32, 7, -1) if nbits % d == 0), 0)
    if not unit:
        raise LearnError(f"{nbits} bits per frame: no word size from 8 to 32 divides it")
    bitpairs = [p for fr in ids for p in fr[:-1] if p in bitset]
    period = round(median(mc(m) + sc(s) for m, s in bitpairs))
    gaps = _gaps(env, thr)
    notes = []
    if gaps:
        gap = round(median(gaps))
    else:
        gap = round(GAP_RATIO * max(spaces[i].hi for i in range(len(spaces))))
        notes.append("one frame and no gap recorded: the gap is a guess, "
                     f"{gap / 25:.0f} us (GAP_RATIO times the longest space)")
    model = Model(name=name, idle=idle, carrier=car, coding=coding, marks=marks, spaces=spaces,
                  zero=zero, one=one, leader=list(leader), trailer=list(trailer), nbits=nbits,
                  unit=unit, last_bit_open=open_, gap=gap, gap_thr=thr, period=period,
                  frames=[], other=0, source=source, notes=notes, quant=quant)
    for fr, shp in zip(ids, shapes):
        if shp == shape:
            model.frames.append(model.words_of(model._bits(fr)))
        else:
            model.other += 1
    return model


def _gaps(env, thr):
    """The frame-ending spaces between two marks."""
    if thr is None:
        return []
    return [d for k, (m, d, _) in enumerate(env)
            if not m and d >= thr and 0 < k < len(env) - 1]


# --------------------------------------------------------------------------- check

@dataclass
class Check:
    frames: int
    worst_rel: float          # largest |generated - recorded| / recorded over all durations
    worst_ticks: int
    decoded: bool             # the model reads every generated frame back as its words
    carrier_ok: bool
    failures: list[str]

    @property
    def ok(self) -> bool:
        return not self.failures


def check(model: Model, records, pin=0, clk_khz=25000, max_frames=3) -> Check:
    """Run the table on the ISS once per recorded frame (source constant, that frame's
    words; at clk_khz, rescaled to 25 MHz ticks) and compare it with the recording,
    duration by duration. The firmware sends each symbol's typical length and a real
    transmitter varies, so a generated duration passes if it lies within the lengths the
    recording shows for that symbol, give or take two ticks of the run clock or two
    samples of the recording; worst_rel reports the largest difference all the same."""
    import escgen
    from esciss import Machine
    from escmodels import Board

    ed = edges(records, pin)
    env = envelope(_runs(ed), model.idle, model.carrier)
    rec_frames = [fr for fr in frames(env, model.gap_thr)
                  if (p := model._pairs(fr)) and model._bits(p)]
    scale = 25000 / clk_khz
    slack = 2 * max(scale, model.quant)
    worst_rel, worst_t, failures, decoded, car_ok = 0.0, 0, [], True, True
    for k, fr in enumerate(rec_frames[:max_frames]):
        words = model.words_of(model._bits(model._pairs(fr)))
        _, image = escgen.build(model.table("constant", words), check=False, CLK_KHZ=clk_khz)
        board = Board(pullups=0xFF if model.idle else 0)
        m = Machine(image, board=board)
        length = sum(a + (b or 0) for a, b in fr) + model.gap
        m.run(int(length / scale) + 2_000, stop_when_idle=False)
        gen = [(round(t * scale), lv) for t, lv in enumerate(board.history)]
        gen_frames = frames(envelope(_runs(edges(gen, pin)), model.idle, model.carrier),
                            model.gap_thr)
        if len(gen_frames) != 1:
            failures.append(f"frame {k}: the program sent {len(gen_frames)} frames")
            continue
        g = gen_frames[0]
        if len(g) != len(fr):
            failures.append(f"frame {k}: {len(g)} marks generated, {len(fr)} recorded")
            continue
        for (gm, gs), (rm, rs) in zip(g, fr):
            for gv, rv, cl in ((gm, rm, model.marks), (gs, rs, model.spaces)):
                if rv is None or gv is None:
                    continue
                err = abs(gv - rv)
                worst_rel, worst_t = max(worst_rel, err / rv), max(worst_t, err)
                c = cl[_nearest(cl, rv)]
                if not c.lo - slack <= gv <= c.hi + slack:
                    failures.append(f"frame {k}: {gv} ticks generated, the recording's "
                                    f"symbol spans {c.lo} to {c.hi}")
        dec = model.decode(gen, pin)
        if dec != [words]:
            decoded = False
            failures.append(f"frame {k}: decoded {dec}, sent {words}")
        if model.carrier:
            got = find_carrier(_runs(edges(gen, pin)), model.idle)
            if got is None or abs(got.period - model.carrier.period) > slack:
                car_ok = False
                failures.append(f"frame {k}: carrier {got} for {model.carrier}")
    if not rec_frames:
        failures.append("no recorded frame has the model's shape")
    return Check(min(len(rec_frames), max_frames), worst_rel, worst_t, decoded, car_ok, failures)
