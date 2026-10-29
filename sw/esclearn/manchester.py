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

import math
from collections import Counter
from dataclasses import dataclass, field
from statistics import median

from .core import (MIN_BITS, MIN_SAMPLES, RAW_BITS, Carrier, Cluster, LearnError, Learned,
                   Recording, carrier_consts, clusters, nearest, ticks, us, word_size)


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
    on: float = 0                    # mean ticks of a half-bit away from idle (with a
                                     # carrier, whole periods, as the envelope counts)

    def on_off(self):
        """(pulses or 0, ticks on, ticks off) of the half-bits to send. A burst is whole
        carrier periods, and a transmitter whose bit timer is not locked to its carrier
        sends n or n + 1 of them: n is the one (nearest the mean first) whose bursts of one
        and two half-bits both lie within the recorded runs. The idle half-bit takes what
        the bit time leaves, so the bits keep the recorded rate."""
        n, on = 0, self.on
        if self.carrier:
            p, f = self.carrier.period, self.on / self.carrier.period
            q = 2 * self.quant
            fits = [k for k in sorted({math.floor(f), math.ceil(f)}, key=lambda k: abs(k - f))
                    if k >= 1 and all(c.lo - q <= j * k * p <= c.hi + q
                                      for j, c in zip((1, 2), self.runs))]
            n = fits[0] if fits else max(1, round(f))
            on = n * p
        return n, on, 2 * self.half - on

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
        return self.pack(r[0])

    def timings(self):
        _, on, off = self.on_off()
        return [on, off, self.gap]

    def run_bits(self, d) -> float:
        return math.log2(3) if nearest(self.runs, d) is not None else RAW_BITS

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
        if not self.carrier and self.idle and self.frames:
            # DALI's line and an IR receiver's output look alike: idle high, no carrier.
            # The receiver is low while the carrier is on, so RC-5's 1 falls mid-bit.
            w = ' '.join(f"0x{~v & ((1 << self.unit) - 1):0{(self.unit + 3) // 4}x}"
                         for v in self.frames[0])
            lines.append(f"(from an IR receiver, which is low while the carrier is on, "
                         f"every bit inverts: the first frame reads {w})")
        return lines + self.check_lines() + self.frame_lines() + self.notes

    def table(self, source="mailbox", words=None, context=0, pin=0, notes=None) -> dict:
        consts = {}
        notes = {} if notes is None else notes
        carrier_consts(self.carrier, consts, notes)
        n, on, off = self.on_off()
        if n:
            consts["ON"] = f"{n} * PERIOD"
            notes["ON"] = f"a half-bit of carrier: {n} pulses, {us(on)}"
        else:
            consts["ON"] = ticks(on)
            notes["ON"] = f"a half-bit away from idle: {us(on)}"
        consts["OFF"] = ticks(off)
        notes["OFF"] = (f"an idle half-bit: {us(off)} (the bit is {us(2 * self.half)}); runs "
                        + ", ".join(c.seen() for c in self.runs))
        gap = self.gap - (off if self.tail_idle else 0)
        consts["GAP"] = ticks(max(1, gap))
        notes["GAP"] = f"{us(max(1, gap))} of quiet after a frame" + (
            "; the last idle half-bit comes first" if self.tail_idle else "")
        active = [{"OUT": "carrier"} if self.carrier else {"OUT": 1 - self.idle}, "ON"]
        quiet = [{"OUT": self.idle}, "OFF"]
        first, last = (quiet, active) if self.mark_last else (active, quiet)
        sym = {"zero": [last, first], "one": [first, last], "gap": [[{"OUT": self.idle}, "GAP"]]}
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
        spec = [{"bits": self.unit, "order": self.order, "choose": ["zero", "one"]}]
        return self.header(consts, sym, spec, src, packet, pin, context)


def _half_runs(durs, q):
    """(one, two): the runs one and two half-bits long, for the half-bit T that most runs
    fit (within T/3 of T, within T/2 of 2T, split at 1.5 T; each cluster's center is tried
    as T), or None. A ragged receiver output spreads the runs into more clusters than two,
    and a bogus frame adds its own; the runs that fit neither leave their frames unread.
    T must span MIN_SAMPLES samples: coarser, any run-length code fits (WS2812 at 6 MHz)."""
    def fit(t):
        return ([d for d in durs if d < 1.5 * t and abs(d - t) <= t / 3 + q],
                [d for d in durs if d >= 1.5 * t and abs(d - 2 * t) <= t / 2 + q])

    best = None
    for c in clusters(durs, q):
        one, two = fit(c.center)
        if not (one and two):
            continue
        t = (sum(one) + sum(two)) / (len(one) + 2 * len(two))              # the mean T
        if t < MIN_SAMPLES * q:
            continue
        one, two = fit(t)
        if one and two and (best is None or len(one) + len(two) > sum(map(len, best))):
            best = one, two
    return best


def learn_manchester(rec: Recording, name, source) -> Manchester:
    frs = rec.frames
    if not frs:
        raise LearnError("no complete frame")
    durs = [d for fr in frs for m, s in fr for d in (m, s) if d is not None]
    fit = _half_runs(durs, rec.quant)
    if fit is None or sum(map(len, fit)) < 0.9 * len(durs):
        raise LearnError("runs are not one and two half-bits")
    runs = [Cluster(min(g), max(g), round(median(g)), len(g), rec.quant) for g in fit]
    half = round((sum(fit[0]) + sum(fit[1])) / (len(fit[0]) + 2 * len(fit[1])))
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
    # The marks apart from the spaces: a receiver stretches its marks
    ms = [(m, round(runs[i].center / half)) for fr in frs for m, _ in fr
          if (i := nearest(runs, m)) is not None]
    model.on = sum(m for m, _ in ms) / sum(k for _, k in ms) if ms else half
    for fr in frs:
        w = model.words(fr)
        if w is None:
            model.other += 1
        else:
            model.frames.append(w)
    model.check_bits()
    return model
