"""MIL-STD-1553B bus controller model: the world around a REMOTE TERMINAL (the chip).

Written from MIL-STD-1553B, 21 September 1978 (section numbers below). The model plays
the bus controller (BC), the bus and the RT's own transceiver. The RT reaches the bus
through five digital pins: it drives TXA and TXB (TXA=1,TXB=0 drives the bus positive,
TXA=0,TXB=1 negative, TXA == TXB, including both released to the pull-ups, is
transmitter off), and the model drives RXA and RXB every tick (bus positive -> RXA=1,
RXB=0; negative -> RXA=0,RXB=1; idle -> both 0). With echo=True the RX pins show the RT's
own transmission too, one tick late (the bus level of the previous tick), as a real
transceiver hears itself.

The word (4.3.3.2, 4.3.3.4, 4.3.3.5): Manchester II bi-phase, a logic one is a positive
half bit then a negative one, a logic zero the reverse, with a zero crossing at every
mid-bit. A word is 20 bit times: a 3 bit time sync, 16 data bits MSB first, and an odd
parity bit over the 16 (4.3.3.5.1.6). The command and status sync (4.3.3.5.1.1,
4.3.3.5.3.1) is positive for 1.5 bit times then negative for 1.5; the data sync
(4.3.3.5.2.1) is the reverse. Both are invalid Manchester on purpose.

What the BC sends: `messages`, a list of {"at_us", "words", "faults"}. At at_us (from
tick 0; the instant the BC turns on and the first word's sync begins, so its mid-sync
zero crossing is 1.5 us later) the BC sends the words contiguously, with no gaps
(4.3.3.6.1). "cmd" (or "status") words get the command/status sync, "data" words the data
sync; the model computes the parity. 1.0 Mbit/s nominal (4.3.3.3), offset by rate_ppm
(positive = faster; the spec allows +/-1000 ppm). Faults, to check the RT rejects bad
input (4.4.1.1 word validation, 4.4.1.2 continuity, 4.4.3.3 invalid command, 4.4.3.6
invalid data): {"parity": i} inverts word i's parity bit; {"manchester": (i, k)} sends
bit k of word i (0 = first data bit, 16 = parity) with no mid-bit transition (both
halves at the bit's first-half level); {"sync": i} sends word i with the other sync;
{"gap_us": (i, us)} idles the bus for `us` before word i; {"truncate": n} sends only the
first n words. Each fault also takes a list (of ints or of tuples). A message of other
words from another terminal (say RT B's status and data in an RT to RT transfer,
4.3.3.6.3) is sent the same way: words on the bus are words on the bus.

distortion_ns moves every zero crossing the BC transmits (not the turn-on and turn-off
edges, which are no zero crossings) by a pseudo-random offset e_k (random.Random(seed))
that is within [-distortion_ns, +distortion_ns] of its ideal place AND within
distortion_ns of the previous crossing's offset: e_k is uniform in
[max(-D, e_k-1 - D), min(D, e_k-1 + D)], restarting at 0 at each turn-on. So both the
absolute place and, as 4.5.2.1.2.1 measures it, the deviation "from the ideal with
respect to the previous zero crossing" stay within D; distortion_ns = 150 is the
receiver tolerance limit exactly (2.0, 1.5, 1.0, 0.5 us +/-150 ns).
Edge times are computed in nanoseconds from the message start (no accumulation) and
then rounded to the nearest whole tick, ties upward: tick = floor(ns / tick_ns + 0.5).
The rounding adds up to tick_ns / 2 to each crossing (up to one tick to an interval,
on top of D). Distortion must stay under 250 ns so crossings keep their order.

What the model checks about the RT's transmission (TXA/TXB), each failure a string in
`violations` (errors() returns them):
  * Waveform (4.5.2.1.1.2): every zero crossing at the ideal time, 0.5, 1.0, 1.5 or 2.0
    us after the previous one at exactly 1.0 Mbit/s, within +/-25 ns. An interval that
    is no whole number of half bits from 1 to 4 is also invalid Manchester (4.3.3.2).
  * Bit rate (4.3.3.3): over a whole transmission, first to last zero crossing, the
    span is within 1000 ppm (+25 ns) of the nominal number of half bits.
  * Sync shape (4.3.3.5.1.1, 4.3.3.5.2.1): a transmission starts with a full sync, the
    first level 1.5 us (+/-25 ns) from turn-on to the mid-sync crossing; it ends with the
    parity bit's second half, 0.5 us (+/-25 ns) from the parity mid-bit crossing to
    turn-off (4.3.3.4: 20 bit times a word; nothing after it).
  * Word validation (4.4.1.1) of every word: a valid sync, all 17 bits valid Manchester,
    16 bits plus parity (no transmission ending mid-word), odd parity.
  * Fail-safe (4.4.1.3): no transmission longer than 800.0 us.
  * Response time (4.3.3.8): 4.0 to 12.0 us from the mid-bit zero crossing of the parity
    bit of the BC's last word (as it is on the RX pins: the distorted, rounded tick; the
    ideal one if that bit was sent with no mid-bit crossing) to the mid-sync zero
    crossing of the RT's status word. A transmission whose mid-sync crossing comes more
    than 14.0 us after it (the no-response time-out, 4.3.3.9) is no response: the
    message's response is None and the transmission is an unasked one (below).
  * Status and data contiguous (4.3.3.6.2, 4.3.3.6.5): one transmission, no gap; a second
    transmission inside the same window (before the next BC message) is flagged and its
    words are still added to the response's data. The first word must be a valid word
    with the command/status sync (4.3.3.5.3.1), every later one a valid data word with
    the data sync (4.3.3.5.2.1).
  * The RT transmits only in response (4.3.3.6, 4.4.3.1): a transmission that overlaps a
    BC message (from its first edge to its last, gaps included), or that is outside
    every response window, is a violation; a collision (both driving) is counted.

TXA and TXB may switch a tick or two apart (an RT that writes them separately): a dead
time with both equal of at most max_dead_ns (default 50 ns) between opposite levels is
a zero crossing at its middle; one between equal levels is a dropout (a violation);
a longer one ends the transmission.

What the model does NOT decide: what the RT should answer (status bits, broadcast with
no status 4.3.3.6.7, illegal commands 4.4.3.4, the status suppressed after invalid data
4.4.3.6, superseding commands 4.4.3.2). Tests do that from the results:
  responses[i]  None if the RT sent nothing within the no-response time-out after
                message i, else {"status": int, "data": [int, ...], "response_us": float,
                "status_ok": bool}; status_ok = the status word passed word validation
                with a command/status sync.
  words_rt      every word the RT sent: {"tick", "sync" ("cmd", "data" or None),
                "value", "parity_ok", "manchester_ok", "complete", "valid"}; tick is the
                word's mid-sync zero crossing (a float: a dead-time crossing is a half
                tick), in the tick the chip drove it (step(t) sees tick t - 1).
  bc_spans[i]   (first tick, last tick) of message i on the bus, None if nothing sent.
  bc_ref[i]     tick of message i's last parity mid-bit crossing (response time zero).
  bc_end_tick   the last BC edge.
Call finish() at the end of a run to close a transmission still open.
"""

from __future__ import annotations

import math
import random

from .board import Device

HALF_NS = 500.0            # half a bit time at 1.0 Mbit/s (4.3.3.3)
OUT_TOL_NS = 25.0          # 4.5.2.1.1.2
RATE_PPM = 1000.0          # 4.3.3.3
RESP_MIN_US, RESP_MAX_US, NO_RESP_US = 4.0, 12.0, 14.0   # 4.3.3.8, 4.3.3.9
MAX_TX_US = 800.0          # 4.4.1.3


def parity_bit(value: int) -> int:
    """The bit that makes the 16 bits plus parity odd (4.3.3.5.1.6)."""
    return (bin(value & 0xFFFF).count("1") + 1) & 1


def word_halves(cmd: bool, value: int, parity_flip: bool = False, no_mid=()) -> list[int]:
    """The 40 half-bit levels (+1/-1) of one word: sync, 16 bits MSB first, parity."""
    bits = [(value >> (15 - j)) & 1 for j in range(16)]
    bits.append(parity_bit(value) ^ int(parity_flip))
    lv = [1, 1, 1, -1, -1, -1] if cmd else [-1, -1, -1, 1, 1, 1]
    for k, b in enumerate(bits):
        a = 1 if b else -1
        lv += [a, a] if k in no_mid else [a, -a]
    return lv


def _as_list(x):
    if x is None:
        return []
    if isinstance(x, (int, tuple)):
        return [x]
    return list(x)


class _Segment:
    """One transmission: runs of a level from turn-on to turn-off."""

    def __init__(self, tick, lvl):
        self.start = tick
        self.runs: list[list] = [[lvl, float(tick)]]   # [level, start tick]
        self.end = None
        self.dropouts: list[tuple[int, int]] = []
        self.collide = 0
        self.long = False
        self.words: list[dict] = []
        self.problems: list[str] = []


class Decoder:
    """Turns per-tick levels (+1/-1/0) into transmissions and decodes their words.

    tol_ns: the allowed crossing deviation (4.5.2.1.1.2's 25 ns for an RT's output).
    on_segment(seg) is called when a transmission ends (decoded, problems listed).
    """

    def __init__(self, tick_ns, tol_ns=OUT_TOL_NS, max_dead_ns=50.0, ppm=RATE_PPM,
                 on_segment=None):
        self.tick_ns, self.tol, self.max_dead, self.ppm = tick_ns, tol_ns, max_dead_ns, ppm
        self.on_segment = on_segment
        self.seg: _Segment | None = None
        self.cur = 0
        self.off = None
        self.segments: list[_Segment] = []

    def sample(self, tick, lvl):
        s = self.seg
        if s is None:
            if lvl:
                self.seg, self.cur, self.off = _Segment(tick, lvl), lvl, None
            return
        if lvl == 0:
            if self.off is None:
                self.off = tick
            if (tick - self.off + 1) * self.tick_ns > self.max_dead + 1e-9:
                self.close(self.off)
            return
        if self.off is not None:                  # a short dead time, both pins equal
            if lvl == self.cur:
                s.dropouts.append((self.off, tick))
            else:
                s.runs.append([lvl, (self.off + tick) / 2])
            self.off = None
        elif lvl != self.cur:
            s.runs.append([lvl, float(tick)])
        self.cur = lvl

    def close(self, end):
        s = self.seg
        if s is None:
            return
        s.end = float(end)
        self.seg, self.off = None, None
        self._decode(s)
        self.segments.append(s)
        if self.on_segment:
            self.on_segment(s)

    def _decode(self, s):
        tk, tol, p = self.tick_ns, self.tol, s.problems
        for a, b in s.dropouts:
            p.append(f"t={a}: transmitter off for {(b - a) * tk:.0f} ns inside a "
                     f"transmission (no Manchester waveform, 4.3.3.2)")
        runs = s.runs
        bounds = [r[1] for r in runs] + [s.end]
        halves: list[tuple[int, float]] = []
        bad = 0
        span_halves = 0
        last = len(runs) - 1
        for k, (lvl, st) in enumerate(runs):
            dur = bounds[k + 1] - st
            ns = dur * tk
            n = max(1, round(ns / HALF_NS))
            if last == 0:
                p.append(f"t={st:.0f}: {ns / 1000:.2f} us of one level with no zero crossing")
            elif k == 0:
                if abs(ns - 3 * HALF_NS) > tol + 1e-6:
                    p.append(f"t={st:.0f}: sync first half {ns:.0f} ns from turn-on to the "
                             f"mid-sync crossing, not 1500 +/-{tol:.0f} ns "
                             f"(4.3.3.5.1.1, 4.3.3.5.2.1)")
            elif k == last:
                if abs(ns - HALF_NS) > tol + 1e-6:
                    p.append(f"t={st:.0f}: last half bit {ns:.0f} ns from the parity mid-bit "
                             f"crossing to turn-off, not 500 +/-{tol:.0f} ns (4.3.3.4)")
            else:
                span_halves += n
                if abs(ns - n * HALF_NS) > tol + 1e-6 or n > 4:
                    bad += 1
                    p.append(f"t={st:.0f}: zero crossing {ns:.0f} ns after the previous one, "
                             f"not 500/1000/1500/2000 +/-{tol:.0f} ns (4.5.2.1.1.2)")
            for j in range(n):
                halves.append((lvl, st + j * dur / n))
        if last >= 2 and not bad:
            span = (runs[last][1] - runs[1][1]) * tk
            ideal = span_halves * HALF_NS
            if abs(span - ideal) > ideal * self.ppm * 1e-6 + tol + 1e-6:
                p.append(f"t={runs[1][1]:.0f}: bit rate off by {(ideal / span - 1) * 1e6:+.0f} "
                         f"ppm over {span / 1000:.1f} us, over {self.ppm:.0f} ppm (4.3.3.3)")
        for w in range(0, len(halves), 40):
            ch = halves[w:w + 40]
            lv = [h[0] for h in ch]
            sync = None
            if lv[0:3] == [1, 1, 1] and lv[3:6] == [-1, -1, -1]:
                sync = "cmd"
            elif lv[0:3] == [-1, -1, -1] and lv[3:6] == [1, 1, 1]:
                sync = "data"
            bits, man = [], True
            for j in range(6, len(lv) - 1, 2):
                bits.append(1 if lv[j] > 0 else 0)
                man = man and lv[j] != lv[j + 1]
            complete = len(ch) == 40
            value = 0
            for b in bits[:16]:
                value = (value << 1) | b
            par = complete and sum(bits) % 2 == 1
            word = {"tick": ch[3][1] if len(ch) > 3 else ch[0][1], "sync": sync,
                    "value": value, "parity_ok": par, "manchester_ok": man,
                    "complete": complete, "valid": bool(sync and man and par and complete)}
            s.words.append(word)
            if not word["valid"]:
                why = [x for x, ok in (("no valid sync", sync), ("invalid Manchester", man),
                                       ("parity error", par or not complete),
                                       (f"ends after {len(ch)} of 40 half bits", complete))
                       if not ok]
                p.append(f"t={word['tick']:.0f}: word {w // 40} (0x{value:04x}) invalid: "
                         f"{', '.join(why)} (4.4.1.1)")


class Bc1553(Device):
    def __init__(self, txa=0, txb=1, rxa=4, rxb=5, tick_ns=25.0, messages=(), echo=True,
                 distortion_ns=0.0, rate_ppm=0.0, seed=1, max_dead_ns=50.0):
        if not 0 <= distortion_ns < 250:
            raise ValueError("distortion_ns must be in [0, 250)")
        self.txa, self.txb, self.rxa, self.rxb = txa, txb, rxa, rxb
        self.tick_ns, self.echo = tick_ns, echo
        self.us = 1000.0 / tick_ns                 # ticks per microsecond
        self.messages = list(messages)
        self.violations: list[str] = []
        self.responses: list[dict | None] = [None] * len(self.messages)
        self.words_rt: list[dict] = []
        self._build(distortion_ns, rate_ppm, seed)
        self._i, self._bc, self._bc_prev = 0, 0, 0
        self._last_t = 0
        self._resp_seg: dict[int, _Segment] = {}
        self.dec = Decoder(tick_ns, OUT_TOL_NS, max_dead_ns, RATE_PPM, self._on_segment)

    # ---- the BC's transmitter -------------------------------------------------------
    def _build(self, d, ppm, seed):
        rng = random.Random(seed)
        half = HALF_NS / (1 + ppm * 1e-6)

        def tick(ns):
            return math.floor(ns / self.tick_ns + 0.5)

        self.edges: list[tuple[int, int]] = []
        self.bc_spans: list[tuple[int, int] | None] = []
        self.bc_ref: list[int | None] = []
        for mi, msg in enumerate(self.messages):
            f = msg.get("faults") or {}
            words = list(msg["words"])
            if f.get("truncate") is not None:
                words = words[:f["truncate"]]
            par = set(_as_list(f.get("parity")))
            syn = set(_as_list(f.get("sync")))
            man = _as_list(f.get("manchester"))
            gaps = dict(_as_list(f.get("gap_us")))
            t, prev, first, ref, e = msg["at_us"] * 1000.0, 0, None, None, 0.0
            for i, (kind, val) in enumerate(words):
                if kind not in ("cmd", "status", "data") or not 0 <= val <= 0xFFFF:
                    raise ValueError(f"message {mi} word {i}: bad word {(kind, val)}")
                if i in gaps and gaps[i] > 0:
                    if prev:
                        self.edges.append((tick(t), 0))
                        prev = 0
                    t += gaps[i] * 1000.0
                cmd = (kind != "data") != (i in syn)
                lv = word_halves(cmd, val, i in par, {k for (w, k) in man if w == i})
                ref = tick(t + 39 * half)          # ideal parity mid-bit crossing
                for h, x in enumerate(lv):
                    if x != prev:
                        ns = t + h * half
                        if prev and d:               # a zero crossing: distort it
                            e = rng.uniform(max(-d, e - d), min(d, e + d))
                        else:                        # turn-on: undistorted, walk restarts
                            e = 0.0
                        et = tick(ns + (e if prev else 0.0))
                        if h == 39:
                            ref = et
                        if first is None:
                            first = et
                        self.edges.append((et, x))
                        prev = x
                t += 40 * half
            if prev:
                self.edges.append((tick(t), 0))
            self.bc_spans.append((first, tick(t)) if first is not None else None)
            self.bc_ref.append(ref if first is not None else None)
        for a, b in zip(self.edges, self.edges[1:]):
            if b[0] < a[0]:
                raise ValueError(f"BC messages overlap or are out of order at tick {b[0]}")
        self.bc_end_tick = self.edges[-1][0] if self.edges else 0

    # ---- per tick ---------------------------------------------------------------------
    def step(self, t, bus):
        a, b = (bus >> self.txa) & 1, (bus >> self.txb) & 1
        rt = 1 if a > b else -1 if b > a else 0          # RT level at tick t - 1
        if rt and self._bc_prev and self.dec.seg is not None:
            self.dec.seg.collide += 1
        self.dec.sample(t - 1, rt)
        s = self.dec.seg
        if s is not None and not s.long and (t - 1 - s.start) / self.us > MAX_TX_US:
            s.long = True
            self.violations.append(f"t={s.start}: RT transmission longer than "
                                   f"{MAX_TX_US:.1f} us (fail-safe time-out, 4.4.1.3)")
        while self._i < len(self.edges) and self.edges[self._i][0] <= t:
            self._bc = self.edges[self._i][1]
            self._i += 1
        self._bc_prev, self._last_t = self._bc, t
        lvl = self._bc
        if self.echo and rt:
            lvl = rt if not lvl else (lvl if lvl == rt else 0)
        ma, mb = 1 << self.rxa, 1 << self.rxb
        if lvl > 0:
            return mb, ma
        if lvl < 0:
            return ma, mb
        return ma | mb, 0

    def finish(self):
        """Close a transmission still open at the end of the run."""
        if self.dec.seg is not None:
            self.dec.close(self.dec.off if self.dec.off is not None else self._last_t)

    # ---- attribution ------------------------------------------------------------------
    def _v(self, msg):
        self.violations.append(msg)

    def _on_segment(self, s):
        for x in s.problems[:6]:
            self._v(x)
        if len(s.problems) > 6:
            self._v(f"t={s.start}: ... and {len(s.problems) - 6} more in this transmission")
        self.words_rt.extend(s.words)
        m = s.words[0]["tick"]                       # mid-sync crossing of the first word
        for i, sp in enumerate(self.bc_spans):
            if sp and s.start <= sp[1] and s.end > sp[0]:
                col = f", both driving for {s.collide} ticks" if s.collide else ""
                self._v(f"t={s.start}: RT transmitted during BC message {i} (ticks "
                        f"{sp[0]}-{sp[1]}){col}: an RT transmits only in response "
                        f"(4.3.3.6, 4.4.3.1)")
                return
        i = None
        for j, r in enumerate(self.bc_ref):
            if r is not None and r <= m:
                i = j
        if i is None:
            self._v(f"t={s.start}: RT transmitted when not asked (before any BC message, "
                    f"4.3.3.6, 4.4.3.1)")
            return
        nxt = min((sp[0] for sp in self.bc_spans[i + 1:] if sp), default=math.inf)
        rt_us = (m - self.bc_ref[i]) / self.us
        if self.responses[i] is not None and m < nxt:
            prev = self._resp_seg[i]
            self._v(f"t={s.start}: RT transmission after message {i} resumed "
                    f"{(s.start - prev.end) / self.us:.2f} us after it stopped: status and "
                    f"data words must be contiguous (4.3.3.6.2)")
            self._data(self.responses[i], s.words)
            return
        if rt_us > NO_RESP_US + 1e-9 or m >= nxt:
            self._v(f"t={s.start}: RT transmitted when not asked ({rt_us:.2f} us after "
                    f"message {i}, past the {NO_RESP_US} us no-response time-out, 4.3.3.9)")
            return
        if not RESP_MIN_US - 1e-9 <= rt_us <= RESP_MAX_US + 1e-9:
            self._v(f"t={s.start}: response time {rt_us:.3f} us after message {i}, outside "
                    f"{RESP_MIN_US}-{RESP_MAX_US} us (4.3.3.8)")
        st = s.words[0]
        ok = st["valid"] and st["sync"] == "cmd"
        if st["sync"] != "cmd":
            self._v(f"t={st['tick']:.0f}: status word 0x{st['value']:04x} without the "
                    f"command/status sync (4.3.3.5.3.1)")
        r = {"status": st["value"], "data": [], "response_us": rt_us, "status_ok": ok}
        self._data(r, s.words[1:])
        self.responses[i] = r
        self._resp_seg[i] = s

    def _data(self, r, words):
        for w in words:
            if w["sync"] != "data":
                self._v(f"t={w['tick']:.0f}: data word 0x{w['value']:04x} without the "
                        f"data sync (4.3.3.5.2.1)")
            r["data"].append(w["value"])

    def errors(self):
        v = self.violations
        return v[:10] + ([f"... and {len(v) - 10} more"] if len(v) > 10 else [])


# ---- self-check ---------------------------------------------------------------------------

def _rt_wave(words, t0_ns, half_ns=HALF_NS):
    """An RT transmission: [(ns, level)] edges, ending with a turn-off (level 0)."""
    lv = []
    for kind, val, *opt in words:
        o = opt[0] if opt else {}
        lv += word_halves(kind != "data", val, o.get("parity", False), o.get("no_mid", ()))
    edges, prev = [], 0
    for h, x in enumerate(lv):
        if x != prev:
            edges.append([t0_ns + h * half_ns, x])
            prev = x
    edges.append([t0_ns + len(lv) * half_ns, 0])
    return edges


def _drive(model, rt_edges_ns, n_ticks, txa=0, txb=1):
    from .board import Board
    board = Board(model, pullups=0xFF)
    ev = sorted(((math.floor(ns / model.tick_ns + 0.5), x) for ns, x in rt_edges_ns),
                key=lambda e: e[0])
    k, lvl = 0, 0
    for t in range(n_ticks):
        while k < len(ev) and ev[k][0] <= t:
            lvl = ev[k][1]
            k += 1
        oe = ((1 << txa) | (1 << txb)) if lvl else 0
        out = (1 << txa) if lvl > 0 else (1 << txb) if lvl < 0 else 0
        board(t, out, oe)
    model.finish()
    return board


def _rx_decode(board, model, tol_ns):
    """Decode what the model put on RXA/RXB (the BC, plus the RT's echo)."""
    dec = Decoder(model.tick_ns, tol_ns, max_dead_ns=0.0, ppm=2000)
    for t, v in enumerate(board.history):
        a, b = (v >> model.rxa) & 1, (v >> model.rxb) & 1
        dec.sample(t, 1 if a > b else -1 if b > a else 0)
    dec.close(len(board.history))
    return dec.segments


def _selftest(verbose=True):
    fails = []

    def check(name, cond, model=None):
        line = f"{'ok  ' if cond else 'FAIL'} {name}"
        if model is not None and model.violations:
            line += f"\n       -> {model.violations[0]}"
        if verbose:
            print(line)
        if not cond:
            fails.append(name)

    CMD = 0x0C22          # RT 1, transmit, subaddress 1, 2 words
    MSG = [{"at_us": 10.0, "words": [("cmd", CMD)]}]
    END = 10 + 20 - 0.5   # the BC's parity mid-bit crossing, us
    good = [("cmd", 0x0800), ("data", 0x1234), ("data", 0xFFFF)]

    def run(rt, msgs=MSG, us=120.0, tick=25.0, **kw):
        m = Bc1553(tick_ns=tick, messages=msgs, **kw)
        b = _drive(m, rt, int(us * 1000 / tick))
        return m, b

    def sync_at(resp_us):            # an RT start so that the mid-sync crossing lands there
        return (END + resp_us - 1.5) * 1000.0

    # 1. a perfect RT at three clocks
    for tick in (25.0, 20.0, 1000 / 24):
        m, b = run(_rt_wave(good, sync_at(7.0)), tick=tick)
        r = m.responses[0]
        check(f"perfect response at {tick:.3f} ns tick: no violations, decoded",
              not m.errors() and r and r["status"] == 0x0800 and r["data"] == [0x1234, 0xFFFF]
              and r["status_ok"] and abs(r["response_us"] - 7.0) < 0.05, m)
    m, b = run([], msgs=MSG + [{"at_us": 200.0, "words": [("cmd", CMD)]}], us=300)
    check("silent RT: responses are None, no violations",
          m.responses == [None, None] and not m.errors(), m)

    # 2. the BC's own words, read back from RXA/RXB
    m, b = run(_rt_wave(good, sync_at(7.0 + 20.0)),
               msgs=[{"at_us": 10.0, "words": [("cmd", CMD), ("data", 0xA5A5)]}])
    segs = _rx_decode(b, m, 25.0)
    w = [(x["sync"], x["value"], x["valid"]) for s in segs for x in s.words]
    check("BC words on RX, then the RT's echo",
          w == [("cmd", CMD, True), ("data", 0xA5A5, True), ("cmd", 0x0800, True),
                ("data", 0x1234, True), ("data", 0xFFFF, True)]
          and not any(s.problems for s in segs))
    m2 = Bc1553(messages=[{"at_us": 10.0, "words": [("cmd", CMD)]}], echo=False)
    b2 = _drive(m2, _rt_wave(good, sync_at(7.0)), 4800)
    check("echo=False: RX shows the BC only", len(_rx_decode(b2, m2, 25.0)) == 1)
    faults = [({"parity": 1}, lambda ws: not ws[1]["parity_ok"] and ws[0]["valid"]),
              ({"manchester": (0, 3)}, lambda ws: not ws[0]["manchester_ok"]),
              ({"sync": 1}, lambda ws: ws[1]["sync"] == "cmd"),
              ({"truncate": 1}, lambda ws: len(ws) == 1),
              ({"gap_us": (1, 3.0)}, lambda ws: len(ws) == 2 and ws[1]["valid"])]
    for f, ok in faults:
        msg = [{"at_us": 10.0, "words": [("cmd", CMD), ("data", 0x00FF)], "faults": f}]
        mf = Bc1553(messages=msg)
        bf = _drive(mf, [], 2400)
        segs = _rx_decode(bf, mf, 25.0)
        ws = [x for s in segs for x in s.words]
        if "gap_us" in f:
            ok2 = len(segs) == 2 and (segs[1].start - segs[0].end) * 25 == 3000
        else:
            ok2 = True
        check(f"BC fault {f} on the wire", ok(ws) and ok2)
    md = Bc1553(messages=[{"at_us": 5.0, "words": [("cmd", CMD)] + [("data", 0x5555)] * 31}],
                distortion_ns=150.0, rate_ppm=1000.0, seed=7)
    bd = _drive(md, [], int(700 * 40))
    segs = _rx_decode(bd, md, 150.0 + 25.0 + 2.0)
    devs = []
    for s in segs:
        for (l0, a), (l1, c) in zip(s.runs[1:], s.runs[2:]):
            n = round((c - a) * 25 / 500)
            devs.append(abs((c - a) * 25 - n * 500))
    ws = [x for s in segs for x in s.words]
    check(f"BC distortion 150 ns + 1000 ppm: 32 words valid, worst interval deviation "
          f"{max(devs):.0f} ns (bound 150 + one 25 ns tick + rate)",
          len(ws) == 32 and all(x["valid"] for x in ws) and 100 < max(devs) <= 177
          and not any(s.problems for s in segs))

    # 3. each rule broken once
    def has(m, s):
        return any(s in v for v in m.violations)

    wv = _rt_wave(good, sync_at(7.0))
    wv[10][0] += 50.0                           # one crossing 50 ns late
    m, _ = run(wv)
    check("crossing 50 ns off (4.5.2.1.1.2)", has(m, "4.5.2.1.1.2"), m)
    wv = _rt_wave(good, sync_at(7.0))
    wv[10][0] += 25.0                           # 25 ns: inside the limit
    m, _ = run(wv)
    check("crossing 25 ns off is allowed", not m.errors(), m)
    m, _ = run(_rt_wave(good, sync_at(7.0), half_ns=505.0), tick=5.0)
    check("bit rate 1% slow (4.3.3.3) at 5 ns tick", has(m, "4.3.3.3")
          and not has(m, "4.5.2.1.1.2"), m)
    m, _ = run(_rt_wave(good, sync_at(7.0), half_ns=500.5), tick=0.5)
    check("bit rate 0.1% slow is allowed", not m.errors(), m)
    for us, ok in ((3.9, False), (4.0, True), (12.0, True), (12.2, False)):
        m, _ = run(_rt_wave(good, sync_at(us)))
        check(f"response at {us} us {'allowed' if ok else 'caught (4.3.3.8)'}",
              (not m.errors()) if ok else has(m, "4.3.3.8"), m)
    m, _ = run(_rt_wave(good, sync_at(15.0)))
    check("response at 15 us: None and unasked (4.3.3.9)",
          m.responses[0] is None and has(m, "4.3.3.9"), m)
    wv = _rt_wave(good[:1], sync_at(7.0)) + _rt_wave(good[1:], sync_at(7.0) + 22000)
    m, _ = run(wv)
    check("2 us gap between status and data (4.3.3.6.2)",
          has(m, "4.3.3.6.2") and m.responses[0]["data"] == [0x1234, 0xFFFF], m)
    m, _ = run(_rt_wave(good, 60000.0))
    check("unasked transmission long after the message", has(m, "not asked"), m)
    m, _ = run(_rt_wave(good, 2000.0), msgs=[{"at_us": 100.0, "words": [("cmd", CMD)]}],
               us=150)
    check("unasked transmission before any message", has(m, "before any BC message"), m)
    m, _ = run(_rt_wave(good, 15000.0))
    check("transmission during the BC's message (collision)",
          has(m, "during BC message 0") and has(m, "both driving"), m)
    long = [("cmd", 0x0800)] + [("data", 0x0000)] * 40
    m, _ = run(_rt_wave(long, sync_at(7.0)), us=900)
    check("41-word transmission: 820 us (4.4.1.3)", has(m, "4.4.1.3"), m)
    m, _ = run(_rt_wave([("cmd", 0x0800), ("data", 0x1234, {"parity": True})], sync_at(7.0)))
    check("RT data word parity error (4.4.1.1)", has(m, "parity error"), m)
    m, _ = run(_rt_wave([("cmd", 0x0800), ("data", 0x1234, {"no_mid": (4,)})], sync_at(7.0)))
    check("RT data bit with no mid-bit crossing (4.4.1.1)", has(m, "invalid Manchester"), m)
    m, _ = run(_rt_wave([("data", 0x0800), ("data", 0x1234)], sync_at(7.0)))
    check("status word with data sync (4.3.3.5.3.1)",
          has(m, "4.3.3.5.3.1") and not m.responses[0]["status_ok"], m)
    m, _ = run(_rt_wave([("cmd", 0x0800), ("cmd", 0x1234)], sync_at(7.0)))
    check("data word with command sync (4.3.3.5.2.1)", has(m, "4.3.3.5.2.1"), m)
    wv = _rt_wave(good, sync_at(7.0))
    wv[0][0] += 500.0                           # sync's first level only 1.0 us
    m, _ = run(wv)
    check("short sync (4.3.3.5.1.1)", has(m, "4.3.3.5.1.1"), m)
    wv = _rt_wave(good, sync_at(7.0))
    wv[-1][0] += 1000.0                         # held 1 us past the last half bit
    m, _ = run(wv)
    check("tail held past the word (4.3.3.4)", has(m, "4.3.3.4"), m)
    wv = _rt_wave(good[:2], sync_at(7.0))
    cut = sync_at(7.0) + 30000.0                # stops half way through the data word
    m, _ = run([x for x in wv if x[0] < cut] + [[cut, 0]])
    check("transmission ending mid-word (4.4.1.1)", has(m, "of 40 half bits"), m)
    # TXA/TXB switching a tick apart: a one-tick dead time is a crossing at its middle
    wv = _rt_wave(good, sync_at(7.0))
    dead = []
    for ns, x in wv[1:-1]:
        dead += [[ns - 12.5, 0], [ns + 12.5, x]]   # off for one 25 ns tick at each crossing
    m, _ = run([wv[0]] + dead + [wv[-1]])
    check("one-tick dead time at every crossing is allowed", not m.errors()
          and m.responses[0]["data"] == [0x1234, 0xFFFF], m)
    wv = _rt_wave(good, sync_at(7.0))
    wv.insert(1, [wv[0][0] + 300.0, 0])
    wv.insert(2, [wv[0][0] + 325.0, 1])
    m, _ = run(wv)
    check("dropout inside the sync", has(m, "transmitter off"), m)
    if verbose:
        print("self-check:", "PASS" if not fails else f"FAIL ({len(fails)})")
    return not fails


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
