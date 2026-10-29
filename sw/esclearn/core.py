"""Shared machinery of esclearn: one pin's runs, duration clusters, the carrier, the
frame gaps, and what every learned model has in common (decoding, the YAML table)."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
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


def us(t) -> str:
    return f"{t / 25:.1f} us"


def ticks(t) -> str:
    """A duration of t ticks at 25 MHz as a clock-independent escgen expression."""
    return f"CLK_KHZ * {round(t)} // 25000"


# --------------------------------------------------------------------------- runs

def edges(records, pin=0) -> list[tuple[int, int]]:
    """(tick, level) of one pin: the first record's level, then every change."""
    out = []
    for t, lv in records:
        v = (lv >> pin) & 1
        if not out or v != out[-1][1]:
            out.append((t, v))
    return out


def runs_of(ed):
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

    def seen(self) -> str:
        return f"{self.n} seen, {us(self.lo)} to {us(self.hi)}"


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


def nearest(cl: list[Cluster], v):
    """Index of the cluster that holds v (the closest on a log scale), or None."""
    best = [i for i, c in enumerate(cl) if c.holds(v)]
    if not best:
        return None
    return min(best, key=lambda i: abs(math.log(v / cl[i].center)))


# --------------------------------------------------------------------------- carrier

@dataclass
class Carrier:
    period: int          # ticks
    high: int            # ticks of each pulse

    @property
    def khz(self) -> float:
        return CLOCK_HZ / self.period / 1000


def find_carrier(runs, idle) -> Carrier | None:
    """A carrier: the shortest mark-to-mark period dominates, the marks fill part of it
    with one width, and the pulses come in bursts separated by spaces several periods long."""
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


def gaps_of(env, thr):
    """The frame-ending spaces between two marks."""
    if thr is None:
        return []
    return [d for k, (m, d, _) in enumerate(env)
            if not m and d >= thr and 0 < k < len(env) - 1]


@dataclass
class Recording:
    """One pin of a recording, analysed as far as every model needs: runs, idle level,
    carrier, envelope, frame gap and frames."""
    ed: list
    runs: list
    idle: int
    carrier: Carrier | None
    env: list
    thr: float | None
    frames: list
    quant: float

    @classmethod
    def of(cls, records, pin=0, quant=1) -> Recording:
        ed = edges(records, pin)
        if len(ed) < 4:
            raise LearnError("fewer than three edges")
        runs = runs_of(ed)
        first, last = ed[0][1], ed[-1][1]
        idle = first if first == last else max(runs[:-1], key=lambda r: r[1])[0]
        car = find_carrier(runs, idle)
        env = envelope(runs, idle, car)
        thr = gap_threshold(env)
        return cls(ed, runs, idle, car, env, thr, frames(env, thr), quant)

    def gap(self, notes, longest):
        """The typical gap between frames, or a guess (noted) if none was recorded."""
        g = gaps_of(self.env, self.thr)
        if g:
            return round(median(g))
        guess = round(GAP_RATIO * longest)
        notes.append(f"no gap between two frames recorded: the gap is a guess, {us(guess)}")
        return guess


# --------------------------------------------------------------------------- models

class Learned:
    """What every learned model has: name, idle, carrier, gap_thr, frames (words of each
    modelled frame), other (frames of another shape), source, notes, quant. A model
    implements words(frame) (a frame's words, or None if it does not fit), summary(),
    table() and compare()."""

    order = "lsb"            # the first bit sent is bit 0 of its word; regroup() changes it

    @property
    def default_source(self) -> str:
        """mailbox when a frame is one word (the host sends each), else constant."""
        return "mailbox" if self.nbits == self.unit else "constant"

    def frame_list(self, records, pin=0):
        env = envelope(runs_of(edges(records, pin)), self.idle, self.carrier)
        return frames(env, self.gap_thr)

    def decode(self, records, pin=0) -> list[list[int]]:
        """Words of every frame in `records` that fits the model."""
        return [w for fr in self.frame_list(records, pin) if (w := self.words(fr)) is not None]

    def stored(self, w):
        """A word as the firmware takes it (default: as decoded)."""
        return w

    def replay(self, words):
        """(table, tape) that sends one recorded frame: here its words as constants."""
        return self.table("constant", words), None

    # ---- words of a frame's bits

    def pack(self, bits):
        """Words of `unit` bits from bits as sent: the first sent is bit 0 (order lsb) or
        the top bit (msb)."""
        u, msb = self.unit, self.order == "msb"
        return [sum(b << (u - 1 - i if msb else i) for i, b in enumerate(bits[k:k + u]))
                for k in range(0, len(bits), u)]

    def unpack(self, words):
        u, msb = self.unit, self.order == "msb"
        return [w >> (u - 1 - i if msb else i) & 1 for w in words for i in range(u)]

    def regroup(self, unit=None, msb=False):
        """The same bits numbered another way: words of `unit` bits (it must divide a
        frame's), MSB first if msb (an LED strip's colours, most protocols' bytes)."""
        unit = unit or self.unit
        if not 1 <= unit <= 32 or self.nbits % unit:
            raise LearnError(f"{unit}-bit words: a frame is {self.nbits} bits")
        bits = [self.unpack(fr) for fr in self.frames]
        self.unit, self.order = unit, "msb" if msb else "lsb"
        self.frames = [self.pack(b) for b in bits]

    # ---- check bytes (checks.py)

    def to_bytes(self, words):
        """A frame's bytes in the order sent (words of whole bytes only), or None."""
        if self.unit % 8:
            return None
        per = self.unit // 8
        at = (lambda k: 8 * (per - 1 - k)) if self.order == "msb" else (lambda k: 8 * k)
        return [(w >> at(k)) & 0xFF for w in words for k in range(per)]

    def from_bytes(self, data):
        per = self.unit // 8
        at = (lambda k: 8 * (per - 1 - k)) if self.order == "msb" else (lambda k: 8 * k)
        return [sum(b << at(k) for k, b in enumerate(data[i:i + per]))
                for i in range(0, len(data), per)]

    @property
    def checks(self):
        """[(byte index, rule)] that every recorded frame of the commonest length obeys."""
        from . import checks
        frames = [b for f in self.frames if (b := self.to_bytes(f)) is not None]
        if not frames:
            return []
        n = max({len(f) for f in frames}, key=[len(f) for f in frames].count)
        return checks.find([f for f in frames if len(f) == n])

    def complete(self, words):
        """`words` with the check bytes recomputed (a new frame the device would accept)."""
        from . import checks
        rules = self.checks
        data = self.to_bytes(words)
        if not rules or data is None:
            return list(words)
        return self.from_bytes(checks.fill(data, rules))

    def check_lines(self):
        from . import checks
        return [checks.describe(j, r) for j, r in self.checks]

    def compare_runs(self, gen, rec, bounds, slack):
        """Run by run: each generated mark and space within `bounds(is_mark, recorded)`
        (lo, hi) give or take slack. (failures, worst relative error, worst ticks)."""
        if len(gen) != len(rec):
            return [f"{len(gen)} marks generated, {len(rec)} recorded"], 0.0, 0
        fails, rel, worst = [], 0.0, 0
        for (gm, gs), (rm, rs) in zip(gen, rec):
            for is_mark, gv, rv in ((True, gm, rm), (False, gs, rs)):
                if rv is None or gv is None:
                    continue
                err = abs(gv - rv)
                rel, worst = max(rel, err / rv), max(worst, err)
                lo, hi = bounds(is_mark, rv)
                if not lo - slack <= gv <= hi + slack:
                    fails.append(f"{gv} ticks generated, the recording's symbol spans {lo} to {hi}")
        return fails, rel, worst

    def table_yaml(self, source="mailbox", words=None, context=0, pin=0) -> str:
        """The table as a commented YAML file, as in protocols/ (escgen.load reads it)."""
        notes = {}
        t = self.table(source, words, context, pin, notes)
        L = [f"# {t['title']}", "# Learned by esclearn (python -m esclearn). What it found:"]
        lines = self.summary()
        L += [f"#   {s}" for s in lines[:12]] + (["#   ..."] if len(lines) > 12 else [])
        L += [f"name: {t['name']}", f"title: {flow(t['title'])}", f"refs: {flow(t['refs'])}",
              f"context: {t['context']}", "pins:"]
        L += [f"  {n}: {flow(p)}" for n, p in t["pins"].items()]
        L.append("constants:")
        width = max(len(f"  {k}: {v}") for k, v in t["constants"].items()) + 2
        for k, v in t["constants"].items():
            line = f"  {k}: {flow(v)}"
            L.append(f"{line.ljust(width)}# {notes[k]}" if notes.get(k) else line)
        if "carrier" in t:
            L.append(f"carrier: {flow(t['carrier'])}")
        L.append("symbols:")
        width = max(len(k) for k in t["symbols"]) + 1
        L += [f"  {(k + ':').ljust(width)} {flow(v)}" for k, v in t["symbols"].items()]
        L += [f"{k}: {flow(t[k])}" for k in ("frame", "source", "packet")]
        return "\n".join(L) + "\n"

    def frame_lines(self) -> list[str]:
        out = [f"frame {' '.join(f'0x{w:0{(self.unit + 3) // 4}x}' for w in fr)}"
               for fr in self.frames]
        if self.other:
            out.append(f"{self.other} frame(s) of another shape not modelled "
                       "(raw replay sends them)")
        return out

    def header(self, consts, sym, fieldspec, src, packet, pin, context):
        """The common part of every table."""
        out = {"name": self.name,
               "title": f"Learned from {self.source}: {self.describe()}",
               "refs": [f"inferred by esclearn from {self.source} ({len(self.frames)} frames)"],
               "context": context,
               "pins": {"OUT": {"pin": pin, "idle": self.idle}},
               "constants": consts}
        if self.carrier:
            out["carrier"] = {"period": "PERIOD", "high": "HIGH"}
        out.update({"symbols": sym, "frame": fieldspec, "source": src, "packet": packet,
                    "source_file": f"{self.name}.yaml"})
        return out


def carrier_consts(carrier, consts, notes):
    if carrier:
        consts["PERIOD"] = ticks(carrier.period)
        consts["HIGH"] = ticks(carrier.high)
        notes["PERIOD"] = f"{carrier.period / 25:.2f} us: {carrier.khz:.2f} kHz"
        notes["HIGH"] = f"{carrier.high / 25:.2f} us per pulse"


def flow(v) -> str:
    """A value in YAML flow style; expressions stay plain, other strings are quoted."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}: {flow(x)}" for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(flow(x) for x in v) + "]"
    s = str(v)
    plain = re.fullmatch(r"[A-Za-z_(][\w\s*/+\-()]*", s) and s.lower() not in (
        "true", "false", "null", "yes", "no", "on", "off", "y", "n")
    return s if plain else json.dumps(s)


def word_size(nbits) -> int:
    """Bits per word for a frame of nbits: all of them up to 32, else the largest divisor
    from 32 down to 8 (0 if none)."""
    if nbits <= 32:
        return nbits
    return next((d for d in range(32, 7, -1) if nbits % d == 0), 0)
