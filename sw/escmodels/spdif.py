"""S/PDIF / AES3 receiver: decodes and checks the digital audio interface signal on one pin.

The DEVICE (the chip) transmits the interface signal at logic level on `pin`; the electrical
layer (section 6.1-6.4: impedance, amplitude, balance, rise times) is not modelled. The
reference is EBU Tech 3250-E, third edition (2004), "Specification of the digital audio
interface (The AES/EBU interface)"; its framing is that of IEC 60958 / S/PDIF.

Units (2.1.13, 2.2.1, 2.3): a unit interval (UI) is one binary state, the shortest nominal
interval of the code. A sub-frame is 32 time slots of two states each (64 UI), a frame is two
sub-frames (128 UI) and is sent once per sample period (2.2.2), so the nominal
UI = 1 / (128 * fs_hz).

Decoding. Every transition ends a run of the line level; the run is counted as
round(duration / nominal UI) states, so the decoder works on a stream of states whose
positions follow the time on the line (a missing or extra transition does not slip it). The
model locks on the first X, Y or Z preamble it finds, of either polarity (2.4, note: the line
may be reversed); before that, and before the first transition, the line may do anything. It
then cuts the state stream into 64-UI sub-frames and decodes each one.

Checks (each failure appends a message with the tick and the section to `violations`):
  * 2.4    time slots 0-3 of every sub-frame hold exactly one of the eight-state preambles
           X, Y, Z of the table, from the set chosen by the preceding state (the second
           state of the parity symbol): 11100010 / 11100100 / 11101000 after a 0, and the
           complements after a 1. A sub-frame boundary where no preamble is found 64 UI after
           the previous one (a sub-frame that is not exactly 64 UI, 2.2.1) is a violation;
           the model then searches for the next preamble and locks again.
  * 2.2.2  sub-frame 1 starts with X or Z, sub-frame 2 with Y (so X/Z and Y alternate);
           once the first Z has been seen, Z appears exactly every 192 frames and nowhere
           else (2.1.11: a block is 192 frames, its start marked by Z).
  * 2.3    time slots 4-31 are biphase-mark: the first state of each symbol differs from
           the second state of the previous one (a transition at every slot boundary), the
           bit is 1 when the two states of the symbol differ. A missing slot-boundary
           transition is a violation (every other coding fault shows up as one, or as a
           timing or continuity fault below).
  * 2.2.1  even parity: time slots 4-31 carry an even number of ones.
  * 6.2.5.1, 2.1.14  jitter: from lock on, every transition must lie on an ideal clock of
           period ui within `jitter_ui` UI peak (6.2.5.1: intrinsic jitter < 0.025 UI). The
           ideal clock is the least-squares line t = t0 + n * ui through all transitions of
           a continuous stretch (n = its state index): the deviation of each transition
           from that line is its jitter. There is no 700 Hz measurement filter here, so the
           check is stricter than the standard (a slow drift counts too).
  * 2.2.2  rate: the frame rate is exactly the sampling frequency, so the fitted mean UI
           must be within `rate_tol_ppm` of 1 / (128 * fs_hz).
  * continuity: after lock the stream must not stop or hiccup: an interval between
           transitions longer than 3 UI (biphase-mark has at most 2 UI, a preamble 3 UI)
           is a violation. A run of up to `gap_ui` (8) UI is decoded on (a coding fault,
           also flagged by the 2.3/2.4 checks); after a longer one (a stream gap) the model
           starts a new continuous stretch (new fit, new lock, the partial frame and block
           are dropped). The stream simply ending (no transition until finish()) is not a
           violation.
  * 2.4    a stream with transitions but no valid preamble at all is a violation.

Records (content is decoded, not judged; that is the tests' job):
  frames  one dict per complete frame after lock:
          {"a", "b": time slots 4..27 of sub-frame 1 / 2 as an int, slot 4 = bit 0
                     (2.2.1: a 24-bit sample's LSB in slot 4, MSB in slot 27, bit 23),
           "v": (va, vb) slot 28 validity (2.5), "u": (ua, ub) slot 29 user data (3),
           "c": (ca, cb) slot 30 channel status (4), "block_start": sub-frame 1 had Z}
  blocks  per complete 192-frame block starting at a Z (4): {"a": bytes(24),
          "b": bytes(24), "crc_ok_a", "crc_ok_b"}; byte k bit j is the channel-status bit
          of frame 8k+j of the block (bit 0 in the Z frame), and crc_ok says whether byte 23
          equals the CRCC of bytes 0-22 (4, byte 23; Appendix 1: G(x) = x^8+x^4+x^3+x^2+1,
          all stages initially 1, LSB first). Consumer blocks carry no CRCC, so crc_ok is
          just a fact for the test to use.
  ui_ns   the fitted mean UI in ns (None before any locked data).

finish() closes the open decode at the end of a run: the last run of the line is counted
if it completes the current sub-frame, an incomplete trailing sub-frame or frame is
dropped, and the timing of the last stretch is checked.
"""

from __future__ import annotations

from bisect import bisect_left

from .board import Device

# 2.4: preambles by preceding state (the second state of the parity symbol).
PREAMBLES = {0: {"X": (1, 1, 1, 0, 0, 0, 1, 0),
                 "Y": (1, 1, 1, 0, 0, 1, 0, 0),
                 "Z": (1, 1, 1, 0, 1, 0, 0, 0)}}
PREAMBLES[1] = {k: tuple(1 - s for s in v) for k, v in PREAMBLES[0].items()}
_PRE_OF = {prev: {v: k for k, v in d.items()} for prev, d in PREAMBLES.items()}

_NONE = (0, 0)


def crcc(data) -> int:
    """Channel-status CRCC (4, byte 23; Appendix 1) over bytes 0-22 of `data`.

    G(x) = x^8 + x^4 + x^3 + x^2 + 1, register initially all ones, the block's bits fed in
    transmission order (byte 0 bit 0 first); the result's bit 0 is block bit 184."""
    crc = 0xFF
    for b in bytes(data)[:23]:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xB8 if crc & 1 else crc >> 1
    return crc


def subframe_states(kind: str, payload: int, prev: int, parity: int | None = None) -> list[int]:
    """The 64 states of one sub-frame: preamble `kind` (2.4) after state `prev`, then slots
    4..30 from `payload` (slot 4 = bit 0) and the parity slot 31, biphase-mark (2.3)."""
    states = list(PREAMBLES[prev][kind])
    bits = [(payload >> k) & 1 for k in range(27)]
    bits.append(sum(bits) & 1 if parity is None else parity)
    s = states[-1]
    for bit in bits:
        a = 1 - s
        b = 1 - a if bit else a
        states += (a, b)
        s = b
    return states


def frame_payload(sample: int, v: int, u: int, c: int) -> int:
    return (sample & 0xFFFFFF) | (v & 1) << 24 | (u & 1) << 25 | (c & 1) << 26


def encode_frames(frames, prev: int = 0) -> list[int]:
    """States for a list of frame dicts in the format SpdifRx records (a, b, v, u, c,
    block_start; an optional "p": (pa, pb) forces the parity bits, for fault tests)."""
    out: list[int] = []
    for f in frames:
        par = f.get("p", (None, None))
        for i, (kind, word) in enumerate((("Z" if f["block_start"] else "X", f["a"]),
                                          ("Y", f["b"]))):
            st = subframe_states(kind, frame_payload(word, f["v"][i], f["u"][i], f["c"][i]),
                                 prev, par[i])
            out += st
            prev = st[-1]
    return out


class SpdifRx(Device):
    def __init__(self, pin: int = 0, tick_ns: float = 20.345, fs_hz: float = 48000.0,
                 rate_tol_ppm: float = 1000.0, jitter_ui: float = 0.025,
                 max_messages: int = 1000, gap_ui: int = 8):
        self.pin, self.tick_ns, self.fs_hz = pin, tick_ns, fs_hz
        self.rate_tol_ppm, self.jitter_ui = rate_tol_ppm, jitter_ui
        self.ui_nom_ns = 1e9 / (128.0 * fs_hz)
        self.ui_nom = self.ui_nom_ns / tick_ns          # nominal UI in ticks
        self.max_messages = max_messages
        self.gap_ui = gap_ui          # a longer run restarts the decode (a stream gap)
        self.frames: list[dict] = []
        self.blocks: list[dict] = []
        self.violations: list[str] = []
        self.locks = 0                # times the decoder locked on a preamble
        self.transitions = 0
        self._suppressed = 0
        self._lvl = None              # line level at the last tick
        self._t = -1                  # last tick seen
        self._t_tr = None             # tick of the last transition (start of the open run)
        self._ever_locked = False
        self._finished = False
        self._fits: list[tuple[float, float]] = []   # closed stretches: (Sxx, Sxy) centred
        self._st: list[int] = []
        self._seg_locked = False

    # ---- helpers -------------------------------------------------------------------
    def _v(self, t, msg):
        if len(self.violations) < self.max_messages:
            self.violations.append(f"t={int(t)}: S/PDIF {msg}")
        else:
            self._suppressed += 1

    def _tick_of(self, n):
        """Approximate tick of state index n of the current stretch."""
        if not self._pn:
            return self._t
        return round(self._pt[-1] - (self._pn[-1] - n) * self.ui_nom)

    def _start_stretch(self, t, lvl):
        self._st = []                 # states of the stretch, from index self._base
        self._base = 0
        self._prev0 = 1 - lvl         # the state before st[0] (the idle level)
        self._t_tr = t
        self._pn = [0]                # timing points: state index, tick of each transition
        self._pt = [t]
        self._seg_locked = False
        self._lock_n = None
        self._locked = False
        self._scan = 0                # next state index to search for a preamble
        self._pos = 0                 # state index of the next sub-frame (when locked)
        self._desync()

    def _desync(self):
        self._sf1 = None              # pending sub-frame 1: (kind, bits)
        self._fi = None               # frames since the last Z
        self._blk = None              # channel-status pairs of the open block

    def _close_stretch(self):
        if self._seg_locked:
            msgs, fit = self._timing(self._pn, self._pt)
            for t, m in msgs:
                self._v(t, m)
            if fit:
                self._fits.append(fit)
        self._seg_locked = False
        self._pn, self._pt = [], []

    # ---- per tick --------------------------------------------------------------------
    def step(self, t, bus):
        lvl = (bus >> self.pin) & 1
        self._t = t
        if lvl == self._lvl:
            return _NONE
        prev, self._lvl = self._lvl, lvl
        if prev is None:              # the first tick: the idle level
            return _NONE
        self.transitions += 1
        if self._t_tr is None:        # the first transition: the stream starts
            self._start_stretch(t, lvl)
            return _NONE
        n = int((t - self._t_tr) / self.ui_nom + 0.5)
        if n > 3:
            gap = n > self.gap_ui
            if self._ever_locked:
                self._v(t, f"{(t - self._t_tr) / self.ui_nom:.2f} UI without a transition since "
                           f"t={self._t_tr}: over 3 UI (continuity; 2.3 at most 2 UI, 2.4 at "
                           f"most 3 UI)" + ("; stream gap, decoder restarts" if gap else ""))
            if gap:
                self._close_stretch()
                self._start_stretch(t, lvl)
                return _NONE
        if n:
            self._st.extend((prev,) * n)
        self._t_tr = t
        self._pn.append(self._base + len(self._st))
        self._pt.append(t)
        self._process(t)
        return _NONE

    def _process(self, t):
        while True:
            if not self._locked:
                if not self._search():
                    return
            elif self._base + len(self._st) - self._pos >= 64:
                self._decode(t)
            else:
                return

    def _search(self):
        st, base = self._st, self._base
        i = max(self._scan - base, 0)
        end = len(st) - 8
        while i <= end:
            s = st[i]
            if s == st[i + 1] == st[i + 2]:
                prev = st[i - 1] if i else self._prev0
                if prev != s:
                    kind = _PRE_OF[prev].get(tuple(st[i:i + 8]))
                    if kind is not None:
                        self._lock(base + i)
                        return True
            i += 1
        self._scan = base + max(end + 1, 0)
        if len(st) > 16384:           # long search through junk: keep the tail only
            self._trim(len(st) - 8)
        return False

    def _lock(self, n):
        self._locked = True
        self._pos = n
        self._first = True
        self.locks += 1
        self._ever_locked = True
        if not self._seg_locked:      # timing is checked from the first lock of a stretch
            self._seg_locked = True
            self._lock_n = n
            k = bisect_left(self._pn, n)
            del self._pn[:k], self._pt[:k]

    def _trim(self, k):
        if k > 0:
            self._prev0 = self._st[k - 1]
            del self._st[:k]
            self._base += k

    def _decode(self, t):
        st = self._st
        p = self._pos - self._base
        prev = st[p - 1] if p else self._prev0
        pre = tuple(st[p:p + 8])
        tick = self._tick_of(self._pos)
        kind = _PRE_OF[prev].get(pre)
        if kind is None:
            self._v(tick, f"no X/Y/Z preamble where a sub-frame should start (64 UI after the "
                          f"previous one): states {''.join(map(str, pre))} after state {prev} "
                          f"(2.4, 2.2.1); searching again")
            self._locked = False
            self._scan = self._pos + 1
            self._desync()
            return
        bits, bad = 0, []
        s = st[p + 7]
        q = p + 8
        for k in range(28):
            a, b = st[q], st[q + 1]
            if a == s:
                bad.append(k + 4)
            if a != b:
                bits |= 1 << k
            s = b
            q += 2
        self._pos += 64
        if p > 8192:
            self._trim(p)
        if bad:
            self._v(tick, f"biphase-mark violation in sub-frame {kind}: no transition at the "
                          f"start of time slot(s) {bad} (2.3)")
        if bin(bits).count("1") & 1:
            self._v(tick, f"parity error in sub-frame {kind}: odd number of ones in time slots "
                          f"4-31 (2.2.1)")
        self._subframe(kind, bits, tick)

    def _subframe(self, kind, bits, tick):
        first, self._first = self._first, False
        if kind != "Y":
            if self._sf1 is not None:
                self._v(tick, f"sub-frame 1 ({self._sf1[0]}) followed by {kind}, not Y (2.2.2)")
                self._desync()
            self._sf1 = (kind, bits)
            return
        if self._sf1 is None:
            if not first:
                self._v(tick, "sub-frame Y where sub-frame 1 (X or Z) is due (2.2.2)")
                self._desync()
            return
        k1, a = self._sf1
        self._sf1 = None
        self._frame(k1, a, bits, tick)

    def _frame(self, k1, a, b, tick):
        fr = {"a": a & 0xFFFFFF, "b": b & 0xFFFFFF,
              "v": ((a >> 24) & 1, (b >> 24) & 1),
              "u": ((a >> 25) & 1, (b >> 25) & 1),
              "c": ((a >> 26) & 1, (b >> 26) & 1),
              "block_start": k1 == "Z"}
        self.frames.append(fr)
        if self._fi is None:
            if k1 == "Z":
                self._fi, self._blk = 0, []
        else:
            self._fi += 1
            due = self._fi % 192 == 0
            if k1 == "Z":
                if not due:
                    self._v(tick, f"preamble Z {self._fi} frames after the previous Z, not 192 "
                                  f"(2.2.2, 2.1.11); block dropped")
                self._fi, self._blk = 0, []
            elif due:
                self._v(tick, f"preamble X where Z is due ({self._fi} frames after the last Z; "
                              f"Z once every 192 frames, 2.2.2)")
                self._blk = None
        if self._blk is not None:
            self._blk.append(fr["c"])
            if len(self._blk) == 192:
                cs = self._blk
                ba = bytes(sum(cs[8 * k + j][0] << j for j in range(8)) for k in range(24))
                bb = bytes(sum(cs[8 * k + j][1] << j for j in range(8)) for k in range(24))
                self.blocks.append({"a": ba, "b": bb,
                                    "crc_ok_a": crcc(ba) == ba[23],
                                    "crc_ok_b": crcc(bb) == bb[23]})
                self._blk = None

    # ---- timing ----------------------------------------------------------------------
    def _timing(self, pn, pt):
        """Least-squares ideal clock through the points; (messages, (Sxx, Sxy) centred)."""
        N = len(pn)
        if N < 3:
            return [], None
        n0, t0 = pn[0], pt[0]
        sx = sy = sxx = sxy = 0
        for n, t in zip(pn, pt):
            x, y = n - n0, t - t0
            sx += x
            sy += y
            sxx += x * x
            sxy += x * y
        dxx = N * sxx - sx * sx
        if dxx <= 0:
            return [], None
        ui = (N * sxy - sx * sy) / dxx               # ticks per UI
        c0 = (sy - ui * sx) / N
        lim = self.jitter_ui * ui
        msgs, bad, peak, peak_t = [], 0, 0.0, None
        for n, t in zip(pn, pt):
            r = (t - t0) - c0 - ui * (n - n0)
            if abs(r) > lim + 1e-9:
                bad += 1
                if bad <= 3:
                    msgs.append((t, f"transition {r / ui:+.3f} UI off the ideal clock (jitter "
                                    f"over {self.jitter_ui} UI peak, 6.2.5.1, 2.1.14)"))
            if abs(r) > peak:
                peak, peak_t = abs(r), t
        if bad > 3:
            msgs.append((peak_t, f"{bad} transitions off the ideal clock by over "
                                 f"{self.jitter_ui} UI, peak {peak / ui:.3f} UI (6.2.5.1)"))
        span = pn[-1] - n0
        if span >= 256:
            ppm = (ui * self.tick_ns / self.ui_nom_ns - 1.0) * 1e6
            if abs(ppm) > self.rate_tol_ppm:
                msgs.append((pt[0], f"mean UI {ui * self.tick_ns:.4f} ns is {ppm:+.0f} ppm from "
                                    f"nominal {self.ui_nom_ns:.4f} ns (fs {self.fs_hz} Hz; "
                                    f"limit {self.rate_tol_ppm} ppm, 2.2.2)"))
        return msgs, (sxx - sx * sx / N, sxy - sx * sy / N)

    @property
    def ui_ns(self):
        fits = list(self._fits)
        if not self._finished and self._seg_locked:
            _, fit = self._timing(self._pn, self._pt)
            if fit:
                fits.append(fit)
        sxx = sum(f[0] for f in fits)
        if sxx <= 0:
            return None
        return sum(f[1] for f in fits) / sxx * self.tick_ns

    # ---- end of run ------------------------------------------------------------------
    def _end_msgs(self):
        msgs = []
        if self._seg_locked:
            msgs += self._timing(self._pn, self._pt)[0]
        if self.transitions > 1 and not self._ever_locked:
            msgs.append((self._t, f"no valid X/Y/Z preamble found in {self.transitions} "
                                  f"transitions (2.4)"))
        return msgs

    def finish(self):
        if self._finished:
            return
        if self._t_tr is not None and self._locked:
            need = self._pos + 64 - (self._base + len(self._st))
            elapsed = (self._t + 1 - self._t_tr) / self.ui_nom
            if 1 <= need <= 2 and elapsed + 0.5 >= need:
                self._st.extend((self._lvl,) * need)
                self._process(self._t)
        for t, m in self._end_msgs():
            self._v(t, m)
        if self._seg_locked:
            _, fit = self._timing(self._pn, self._pt)
            if fit:
                self._fits.append(fit)
        self._seg_locked = False
        self._locked = False
        self._finished = True

    def errors(self):
        errs = list(self.violations)
        if not self._finished:
            errs += [f"t={int(t)}: S/PDIF {m}" for t, m in self._end_msgs()]
        if self._suppressed:
            errs.append(f"S/PDIF: {self._suppressed} more violations not listed")
        return errs


# ---- self-test ----------------------------------------------------------------------
def _selftest(verbose: bool = True) -> bool:
    import random

    ok = True

    def check(cond, what):
        nonlocal ok
        if not cond:
            ok = False
        if verbose:
            print(("PASS " if cond else "FAIL ") + what)

    # Appendix 1 worked examples.
    ex1 = bytearray(23)
    ex1[0], ex1[1], ex1[4] = 0b00111101, 0b00000010, 0b00000010
    ex2 = bytearray(23)
    ex2[0] = 0b00000001
    # Example 1: CRCC bits 0..7 = 1 1 0 1 1 0 0 1; example 2: 0 1 0 0 1 1 0 0
    check(crcc(ex1) == 0b10011011, f"CRCC example 1 = 0x{crcc(ex1):02x} (Appendix 1)")
    check(crcc(ex2) == 0b00110010, f"CRCC example 2 = 0x{crcc(ex2):02x} (Appendix 1)")

    tpu = 8
    tick_ns = 1e9 / (128 * 48000 * tpu)
    rnd = random.Random(3250)

    def cs_block(seed, prof=True):
        r = random.Random(seed)
        b = bytearray(r.randrange(256) for _ in range(23))
        if prof:
            b[0] |= 1
        b.append(crcc(b))
        return bytes(b)

    nblk = 2
    csa = [cs_block(10 + i) for i in range(nblk + 1)]
    csb = [cs_block(20 + i) for i in range(nblk + 1)]
    frames = []
    for i in range(192 * nblk + 9):
        blk, j = divmod(i, 192)
        frames.append({"a": rnd.randrange(1 << 24), "b": rnd.randrange(1 << 24),
                       "v": (rnd.randrange(2), rnd.randrange(2)),
                       "u": (rnd.randrange(2), rnd.randrange(2)),
                       "c": ((csa[blk][j >> 3] >> (j & 7)) & 1, (csb[blk][j >> 3] >> (j & 7)) & 1),
                       "block_start": j == 0})
    clean = encode_frames(frames)

    def levels_of(states, idle=37, invert=False):
        lv = [1 - states[0]] * idle
        for s in states:
            lv += [s] * tpu
        return [1 - x for x in lv] if invert else lv

    def run(lv, pin=3, **kw):
        rx = SpdifRx(pin=pin, tick_ns=tick_ns, **kw)
        for t, x in enumerate(lv):
            rx.step(t, x << pin)
        rx.finish()
        return rx

    def strip(fs):
        return [{k: f[k] for k in ("a", "b", "v", "u", "c", "block_start")} for f in fs]

    rx = run(levels_of(clean))
    check(rx.errors() == [], f"clean stream: no violations {rx.errors()[:3]}")
    check(rx.frames == strip(frames), f"clean stream: {len(rx.frames)} frames decode exactly")
    want_blocks = [{"a": csa[i], "b": csb[i], "crc_ok_a": True, "crc_ok_b": True}
                   for i in range(nblk)]
    check(rx.blocks == want_blocks, f"clean stream: {len(rx.blocks)} channel-status blocks, CRCC ok")
    check(rx.ui_ns is not None and abs(rx.ui_ns - 1e9 / (128 * 48000)) < 1e-6,
          f"clean stream: ui_ns = {rx.ui_ns}")

    rx = run(levels_of(clean, invert=True))
    check(rx.errors() == [] and rx.frames == strip(frames) and rx.blocks == want_blocks,
          "reversed polarity decodes the same (2.4 note)")

    rx = run(levels_of(clean[64 * 5 + 23:]))       # start mid sub-frame: lock on the next preamble
    check(rx.errors() == [] and rx.frames == strip(frames[3:]) and rx.blocks == want_blocks[1:],
          f"mid-stream start: lock and decode from frame 3 ({len(rx.frames)} frames, "
          f"{len(rx.blocks)} block from the first Z)")

    rx = run(levels_of(clean[:128 * 50 + 64 + 30]))  # ends mid sub-frame 2: dropped, no flag
    check(rx.errors() == [] and rx.frames == strip(frames[:50]),
          f"stream ends mid-frame: no violation, {len(rx.frames)} complete frames")

    rx = run(levels_of(clean)[:-tpu * 1] + [clean[-1]] * 400)   # last run held long: ends ok
    check(rx.errors() == [] and len(rx.frames) == len(frames), "stream stops after the last frame")

    bad_ct = sum(1 for f in frames if f["block_start"])
    check(bad_ct == nblk + 1, "test data has a Z every 192 frames")

    def faulty(name, lv, needle, **kw):
        r = run(lv, **kw)
        e = r.errors()
        check(any(needle in m for m in e), f"{name}: flagged ({e[0] if e else 'NOTHING'})")

    fp = [dict(f) for f in frames]
    fp[100]["p"] = (1 - (bin(frame_payload(fp[100]["a"], fp[100]["v"][0], fp[100]["u"][0],
                                           fp[100]["c"][0])).count("1") & 1), None)
    faulty("parity error", levels_of(encode_frames(fp)), "(2.2.1)")

    fz = [dict(f) for f in frames]
    fz[192]["block_start"] = False
    faulty("missing Z", levels_of(encode_frames(fz)), "Z is due")

    fz = [dict(f) for f in frames]
    fz[77]["block_start"] = True
    faulty("extra Z", levels_of(encode_frames(fz)), "not 192")

    fy = list(clean)
    p = 128 * 60 + 64                                 # sub-frame 2 of frame 60: X instead of Y
    fy[p:p + 8] = PREAMBLES[fy[p - 1]]["X"]
    faulty("X in sub-frame 2", levels_of(fy), "(2.2.2)")

    fb = list(clean)
    q = 128 * 40 + 8 + 2 * 10                         # slot 14 of frame 40, sub-frame 1
    fb[q] = fb[q - 1]                                 # no transition at the slot boundary
    faulty("biphase violation", levels_of(fb), "(2.3)")

    lv = levels_of(clean)
    k = 37 + tpu * (128 * 150 + 64 + 20)              # a transition mid-stream
    while lv[k] == lv[k - 1]:
        k += 1
    lv[k] = lv[k - 1]                                 # one tick late: 0.125 UI
    faulty("transition moved by one tick", lv, "6.2.5.1")

    fg = clean[:128 * 30 + 40] + [clean[128 * 30 + 39]] * 20 + clean[128 * 30 + 40:]
    faulty("gap in the stream", levels_of(fg), "continuity")

    fl = clean[:128 * 30 + 64] + [clean[128 * 30 + 63]] + clean[128 * 30 + 64:]
    faulty("sub-frame of 65 UI", levels_of(fl), "(2.4")

    faulty("rate 2000 ppm off", levels_of(clean), "ppm", fs_hz=48000 * 1.002)

    faulty("no preamble at all", [0] * 20 + [1, 0] * 200, "no valid")

    lv = levels_of(clean)                             # wrong CRCC: decoded, not a violation
    fc = [dict(f) for f in frames]
    for j in range(184, 192):
        fc[j]["c"] = (1 - fc[j]["c"][0], fc[j]["c"][1])
    rx = run(levels_of(encode_frames(fc)))
    check(rx.errors() == [] and rx.blocks[0]["crc_ok_a"] is False and rx.blocks[0]["crc_ok_b"],
          "wrong CRCC: crc_ok_a False, no violation")

    if verbose:
        print("selftest", "PASSED" if ok else "FAILED")
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
