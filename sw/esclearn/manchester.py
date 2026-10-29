"""Manchester (biphase) codes: every bit is two half-bits of opposite level, so the pin
changes in the middle of every bit and each run is one or two half-bits long. RC-5
(889 us half-bits, usually on a 36 kHz carrier), DALI (417 us, idle high). A bit is 1
when the pin rises in its middle (DALI's convention, and IEEE 802.3's); with a carrier,
when the carrier comes on in its middle (RC-5's), whichever level the pin idles at.

A frame starts with a mark, so a first bit whose first half is idle has an invisible
first half; likewise an idle last half runs into the gap. Both are restored when the
half-bits are paired: each frame is read with and without a leading idle half, and the
reading in which every pair has opposite halves is the frame."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .core import (MIN_BITS, Carrier, Cluster, LearnError, Learned, Recording,
                   carrier_consts, clusters, nearest, ticks, us, word_size)


def _bits(halves, lead, mark_last):
    """Bits of a frame's half-bits (1 = mark) read with `lead` idle halves first, or None
    if some bit has two equal halves. A 1 is (idle, mark) if mark_last, else (mark, idle)."""
    seq = [0] * lead + halves
    if len(seq) % 2:
        seq.append(0)                      # the last half is idle: it ran into the gap
    rise = (0, 1) if mark_last else (1, 0)
    out = []
    for a, b in zip(seq[::2], seq[1::2]):
        if a == b:
            return None
        out.append(1 if (a, b) == rise else 0)
    return out


@dataclass
class Manchester(Learned):
    name: str
    idle: int
    carrier: Carrier | None
    half: int                        # ticks per half-bit
    runs: list[Cluster]              # recorded run lengths: one and two half-bits
    nbits: int
    unit: int
    lead: int                        # idle halves usually before the first mark (0 or 1)
    tail_idle: bool                  # the last half is usually idle (part of the gap)
    gap: int
    gap_thr: float | None
    frames: list[list[int]]
    other: int
    source: str = "capture"
    notes: list[str] = field(default_factory=list)
    quant: float = 1
    coding: str = "Manchester"

    @property
    def mark_last(self) -> bool:
        """A 1 ends with the mark: the pin rises (idle low), or the carrier comes on."""
        return bool(self.carrier) or self.idle == 0

    def _halves(self, fr):
        out = []
        for m, s in fr:
            for level, d in ((1, m), (0, s)):
                if d is None:
                    continue
                i = nearest(self.runs, d)
                if i is None:
                    return None
                out += [level] * round(self.runs[i].center / self.half)
        return out

    def _read(self, fr):
        """(bits, lead) of a frame with the model's bit count, or None."""
        halves = self._halves(fr)
        if halves is None:
            return None
        found = [(b, ld) for ld in (self.lead, 1 - self.lead)
                 if (b := _bits(halves, ld, self.mark_last)) is not None and len(b) == self.nbits]
        return found[0] if found else None

    def words(self, fr):
        r = self._read(fr)
        if r is None:
            return None
        bits = r[0]
        return [sum(b << i for i, b in enumerate(bits[k:k + self.unit]))
                for k in range(0, len(bits), self.unit)]

    def compare(self, gen, rec, slack):
        def bounds(is_mark, v):
            c = self.runs[nearest(self.runs, v)]
            return c.lo, c.hi
        return self.compare_runs(gen, rec, bounds, slack)

    def describe(self) -> str:
        return (f"Manchester, {self.nbits} bits per frame, half-bit {us(self.half)}"
                + (f", {self.carrier.khz:.1f} kHz carrier" if self.carrier else ""))

    def summary(self) -> list[str]:
        idle = "high" if self.idle else "low"
        one = "the carrier comes on mid-bit" if self.carrier else "the pin rises mid-bit"
        lines = [f"{self.describe()}, idle {idle}; a 1: {one}",
                 f"gap {us(self.gap)}"]
        return lines + self.check_lines() + self.frame_lines() + self.notes

    def table(self, source="mailbox", words=None, context=0, pin=0, notes=None) -> dict:
        consts = {}
        notes = {} if notes is None else notes
        carrier_consts(self.carrier, consts, notes)
        if self.carrier:
            n = max(1, round(self.half / self.carrier.period))
            consts["HALF"] = f"{n} * PERIOD"
            notes["HALF"] = f"half-bit: {n} carrier periods, {us(n * self.carrier.period)}"
        else:
            consts["HALF"] = ticks(self.half)
            notes["HALF"] = f"half-bit {us(self.half)}; runs {', '.join(c.seen() for c in self.runs)}"
        gap = self.gap - (self.half if self.tail_idle else 0)
        consts["GAP"] = ticks(max(1, gap))
        notes["GAP"] = f"{us(max(1, gap))} of quiet after a frame" + (
            "; the last idle half-bit comes first" if self.tail_idle else "")
        active = {"OUT": "carrier"} if self.carrier else {"OUT": 1 - self.idle}
        quiet = {"OUT": self.idle}
        first, last = (quiet, active) if self.mark_last else (active, quiet)
        sym = {"zero": [[last, "HALF"], [first, "HALF"]],
               "one": [[first, "HALF"], [last, "HALF"]],
               "gap": [[quiet, "GAP"]]}
        packet = {"after": ["gap"]}
        per = self.nbits // self.unit
        if source == "mailbox":
            if per != 1:
                raise LearnError(f"a frame is {per} words: the mailbox source sends one per packet")
            src = {"kind": "mailbox"}
            packet["ack"] = True
        elif source == "constant":
            src = {"kind": "constant", "words": list(words if words is not None else self.frames[0])}
        else:
            raise LearnError(f"source {source!r}: a Manchester table takes mailbox or constant")
        spec = [{"bits": self.unit, "order": "lsb", "choose": ["zero", "one"]}]
        return self.header(consts, sym, spec, src, packet, pin, context)


def learn_manchester(rec: Recording, name, source) -> Manchester:
    frs = rec.frames
    if not frs:
        raise LearnError("no complete frame")
    durs = [d for fr in frs for m, s in fr for d in (m, s) if d is not None]
    runs = clusters(durs, rec.quant)
    if len(runs) != 2 or not 1.6 <= runs[1].center / runs[0].center <= 2.4:
        raise LearnError("runs are not one and two half-bits")
    k = lambda d: 1 if nearest(runs, d) == 0 else 2      # noqa: E731
    half = round(sum(durs) / sum(k(d) for d in durs))
    model = Manchester(name=name, idle=rec.idle, carrier=rec.carrier, half=half, runs=runs,
                       nbits=0, unit=0, lead=0, tail_idle=False, gap=0, gap_thr=rec.thr,
                       frames=[], other=0, source=source, quant=rec.quant)
    halves = [model._halves(fr) for fr in frs]
    readings = [{ld: b for ld in (0, 1) if h is not None and (b := _bits(h, ld, model.mark_last))}
                for h in halves]
    if not any(readings):
        raise LearnError("no frame reads as Manchester (a bit with two equal halves)")
    leads = Counter(ld for r in readings for ld in r)
    model.lead = max((0, 1), key=lambda ld: leads[ld])
    sizes = Counter(len(r.get(model.lead, r.get(1 - model.lead, []))) for r in readings if r)
    ranked = [n for n, _ in sizes.most_common() if n >= MIN_BITS]
    if not ranked:
        raise LearnError(f"no frame carries {MIN_BITS} bits or more")
    model.nbits = ranked[0]
    if sizes[model.nbits] < 2 and len(frs) > 2:
        raise LearnError(f"no frame length repeats among {len(frs)} frames")
    model.unit = word_size(model.nbits)
    if not model.unit:
        raise LearnError(f"{model.nbits} bits per frame: no word size from 8 to 32 divides it")
    tails = Counter((model.lead + len(h)) % 2 == 1 for h in halves if h is not None)
    model.tail_idle = tails[True] >= tails[False]
    model.gap = rec.gap(model.notes, runs[1].hi)
    for fr in frs:
        w = model.words(fr)
        if w is None:
            model.other += 1
        else:
            model.frames.append(w)
    return model
