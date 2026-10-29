"""Two-symbol pulse codes: every bit is a mark and a space, and the two bit values are
two (mark, space) pairs. Pulse distance (NEC: one mark, two spaces), pulse width
(WS2812: two marks, a constant period), pulse length (SIRC: two marks, one space) or any
other pair; an optional carrier, leader and trailer around the bits."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from statistics import median

from .core import (MIN_BITS, RAW_BITS, SPLIT, Carrier, Cluster, LearnError, Learned, Recording,
                   carrier_consts, clusters, nearest, ticks, us, word_size)


@dataclass
class PulseCode(Learned):
    """A learned pulse code: its symbols as clusters of recorded lengths, the frame
    shape, and the words of every recorded frame."""
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
            mi = nearest(self.marks, m)
            si = None if s is None else nearest(self.spaces, s)
            if mi is None or (s is not None and si is None):
                return None
            out.append((mi, si))
        return out

    def timings(self):
        return [c.center for c in self.marks + self.spaces] + [self.gap]

    def run_bits(self, d) -> float:
        k = len(self.marks) + len(self.spaces)
        hit = nearest(self.marks, d) is not None or nearest(self.spaces, d) is not None
        return math.log2(1 + k) if hit else RAW_BITS

    def unread_bits(self, fr) -> float:
        """A pair that is one of the model's symbols (a bit, a leader or trailer pair)
        costs the choice of symbol; any other pair, its runs."""
        syms = {self.zero, self.one, *map(tuple, self.leader), *map(tuple, self.trailer)}
        out = 0.0
        for m, s in fr:
            mi = nearest(self.marks, m)
            si = None if s is None else nearest(self.spaces, s)
            if mi is not None and (si is not None or s is None) and (mi, si) in syms:
                out += math.log2(1 + len(syms))
            else:
                out += self.run_bits(m) + (self.run_bits(s) if s is not None else 0)
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

    def words(self, fr):
        pairs = self._pairs(fr)
        bits = pairs and self._bits(pairs)
        return self.pack(bits) if bits else None

    def compare(self, gen, rec, slack):
        def bounds(is_mark, v):
            cl = self.marks if is_mark else self.spaces
            c = cl[nearest(cl, v)]
            return c.lo, c.hi
        return self.compare_runs(gen, rec, bounds, slack)

    # ---- description and timing table

    def describe(self) -> str:
        return (f"{self.coding}, {self.nbits} bits per frame"
                + (f", {self.carrier.khz:.1f} kHz carrier" if self.carrier else ""))

    def summary(self) -> list[str]:
        """What was found, one line per item, then every frame's words."""
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
        return lines + self.check_lines() + self.frame_lines() + self.notes

    def table(self, source="mailbox", words=None, context=0, pin=0, notes=None) -> dict:
        """escgen table. source: mailbox (one word per packet, from the host), constant
        (`words`, default the first captured frame's, sent once) or tape (pulse width
        only: the host writes the packet to the tape; word 0 holds the count). `notes`,
        if given, receives a comment per constant (what was measured)."""
        consts, names = {}, {}
        notes = {} if notes is None else notes

        def const(prefix, value, expr, note):
            key = (prefix, value)
            if key not in names:
                names[key] = f"{prefix}{sum(1 for k in names if k[0] == prefix) + 1}"
                consts[names[key]] = expr
                notes[names[key]] = note
            return names[key]

        mk = 1 - self.idle
        carrier_consts(self.carrier, consts, notes)

        def mark(i):
            c = self.marks[i].center
            if self.carrier:
                n = max(1, round(c / self.carrier.period))
                note = f"{n} pulses, {us(n * self.carrier.period)}; " + self.marks[i].seen()
                return [{"OUT": "carrier"}, const("M", n, f"{n} * PERIOD", note)]
            return [{"OUT": mk}, const("M", c, ticks(c), f"mark {us(c)}; " + self.marks[i].seen())]

        def space(i):
            c = self.spaces[i].center
            return [{"OUT": self.idle},
                    const("S", c, ticks(c), f"space {us(c)}; " + self.spaces[i].seen())]

        sym = {}
        if self.leader:
            sym["leader"] = [ph for a, b in self.leader for ph in (mark(a), space(b))]
        data_phase = source == "tape" or (self.coding == "pulse width" and not self.carrier)
        if data_phase:
            if self.coding != "pulse width" or self.carrier:
                raise LearnError("the tape source needs a pulse-width code without a carrier")
            h0, h1 = self.marks[self.zero[0]].center, self.marks[self.one[0]].center
            consts["H0"], consts["H1"], consts["T"] = ticks(h0), ticks(h1), ticks(self.period)
            notes["H0"] = f"zero: mark {us(h0)}; " + self.marks[self.zero[0]].seen()
            notes["H1"] = f"one: mark {us(h1)}; " + self.marks[self.one[0]].seen()
            notes["T"] = f"bit period {us(self.period)}"
            sym["bit"] = [[{"OUT": mk}, "H0"], [{"OUT": "data"}, "H1 - H0"],
                          [{"OUT": self.idle}, "T - H1"]]
            fieldspec = {"bits": self.unit, "order": self.order, "symbol": "bit"}
        else:
            sym["zero"] = [mark(self.zero[0]), space(self.zero[1])]
            sym["one"] = [mark(self.one[0]), space(self.one[1])]
            fieldspec = {"bits": self.unit, "order": self.order, "choose": ["zero", "one"]}
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
            src = {"kind": "constant", "words": [self.stored(w) for w in ws]}
        elif source == "tape":
            src = {"kind": "tape", "base": 0, "count": "tape"}
        else:
            raise LearnError(f"source {source!r}: mailbox, constant or tape")
        t = self.header(consts, sym, [fieldspec], src, packet, pin, context)
        if data_phase and self.idle:
            t["refs"].append("idle high: the data phase drives the inverse of each bit, "
                             "so the words hold the inverted bits")
        return t

    def replay(self, words):
        """(table, tape): a long pulse-width frame (an LED strip) from the tape, as
        constants it would not fit the program."""
        if self.coding == "pulse width" and not self.carrier and len(words) > 2:
            return self.table("tape"), [len(words)] + [self.stored(w) for w in words]
        return self.table("constant", words), None

    def stored(self, w):
        """A word as the firmware takes it (inverted for an idle-high data phase)."""
        if self.idle and self.coding == "pulse width" and not self.carrier:
            return ~w & ((1 << self.unit) - 1)
        return w


def learn_pulse(rec: Recording, name, source) -> PulseCode:
    frs = rec.frames
    if not frs:
        raise LearnError("no complete frame")
    marks = clusters([m for fr in frs for m, _ in fr], rec.quant)
    spaces = clusters([s for fr in frs for _, s in fr if s is not None], rec.quant)
    ids = [[(nearest(marks, m), None if s is None else nearest(spaces, s)) for m, s in fr]
           for fr in frs]
    count = Counter(p for fr in ids for p in fr[:-1])
    if len(count) < 2:
        raise LearnError("fewer than two symbols: nothing carries bits")
    (a, na), (b, nb) = count.most_common(2)
    if na + nb < 0.6 * sum(count.values()):
        raise LearnError("no pair of symbols dominates: not a two-symbol code")
    def apart(cl, i, j):
        """Two clusters with a gap between them (not a range of lengths that clusters()
        cut in two because it spans more than SPREAD)."""
        if i is None or j is None or i == j:
            return False
        lo, hi = sorted((cl[i], cl[j]), key=lambda c: c.center)
        return hi.lo > lo.hi * SPLIT and hi.lo - lo.hi > rec.quant
    if not (apart(marks, a[0], b[0]) or apart(spaces, a[1], b[1])):
        raise LearnError("the two commonest symbols are one range of lengths, not two symbols")
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
    unit = word_size(nbits)
    if not unit:
        raise LearnError(f"{nbits} bits per frame: no word size from 8 to 32 divides it")
    bitpairs = [p for fr in ids for p in fr[:-1] if p in bitset]
    period = round(median(mc(m) + sc(s) for m, s in bitpairs))
    notes = []
    gap = rec.gap(notes, max(c.hi for c in spaces))
    model = PulseCode(name=name, idle=rec.idle, carrier=rec.carrier, coding=coding, marks=marks,
                      spaces=spaces, zero=zero, one=one, leader=list(leader),
                      trailer=list(trailer), nbits=nbits, unit=unit, last_bit_open=open_,
                      gap=gap, gap_thr=rec.thr, period=period, frames=[], other=0,
                      source=source, notes=notes, quant=rec.quant)
    for fr, shp in zip(ids, shapes):
        if shp == shape:
            model.frames.append(model.pack(model._bits(fr)))
        else:
            model.other += 1
    model.check_bits()
    return model
