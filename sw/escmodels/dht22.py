"""AM2302 / DHT22 temperature and humidity sensor: the SENSOR side of its single-wire bus.

Specification: Aosong, "Temperature and humidity module AM2302 Product Manual" (sections
cited as "AM2302 x.y"). The chip is the host; this model is the sensor on one open-drain
data line (SDA, `pin`) with the board's pull-up, so the line idles high (4.3, 7.2 "Single
bus Description"). The sensor only ever pulls the line low or releases it; it never drives
it high.

One read (7.3 Pic 6, 7.4 steps 2-3), every duration from Table 6:

  host:   SDA low for Tbe (0.8 / 1 / 20 ms), then releases the line
  sensor: waits Tgo (20 / 30 / 200 us) after the release ("bus master has released time"),
          pulls low Trel (75 / 80 / 85 us), releases for Treh (75 / 80 / 85 us),
          then 40 bits MSB first, each TLOW low (48 / 50 / 55 us) followed by a release
          of TH0 (22 / 26 / 30 us) for a 0 or TH1 (68 / 70 / 75 us) for a 1,
          then pulls low Ten (45 / 50 / 55 us) and releases the bus (end of 7.4 step 3).

The 40 bits (7.2 Table 5): humidity x10 (16 bit), temperature x10 (16 bit, bit 15 = 1 for a
negative temperature, bits 14..0 the magnitude), parity byte = low 8 bits of the sum of the
four data bytes; sent humidity high, humidity low, temperature high, temperature low,
parity (7.3). Worked example 1 of 7.2: 65.8 %RH, 26.9 C -> 02 92 01 0D A2; the special
instruction: -10.1 C -> temperature 0x8065.

Constructor
-----------
Dht22(pin=0, tick_ns=40.0, humidity=65.8, temperature=26.9, raw=None, timing="typ",
      seed=1, startup_s=2.0, min_interval_s=2.0, faults=None)

  humidity, temperature : the reading (%RH, deg C), encoded as above. Each may be a number
      or a list; with lists, reading k is (humidity[k], temperature[k]), the last element
      repeating once a list runs out. One reading is consumed per read the sensor answers.
  raw : 5 bytes sent as they are (parity NOT recomputed), or a list of such 5-byte items
      consumed one per answered read; overrides humidity/temperature.
  timing : "typ", "min" or "max" puts every sensor-driven duration (Tgo, Trel, Treh, TLOW,
      TH0, TH1, Ten) at that column of Table 6; "random" draws each duration, every time it
      is used, uniformly within min..max (in whole ticks, seeded by `seed`). Durations are
      whole ticks: min rounds up, max rounds down, typ rounds to nearest, so a duration
      never leaves the Table 6 range.
  startup_s : the sensor does not answer a start signal that ends before this time after
      tick 0 (7.4 step 1: 2 s after power-up). Tests pass small values to keep runs short.
  min_interval_s : start signals closer than this are a violation (7.1 item 3; Table 4
      sampling period 2 s). Small values keep test runs short.
  faults : dict making chosen reads bad, for host tests. Keys and values (a value may also
      be a list of such entries, one per read to break); read_index is the position of the
      read in `reads` (every valid start signal counts, answered or not):
        {"checksum": read_index}                parity byte XOR 0x10 (7.2 example 2's error)
        {"no_response": read_index}             the sensor ignores that start signal
        {"truncate": (read_index, nbits)}       only the first nbits (0..39) bits are sent,
                                                then the line is released: no Ten
        {"stretch": (read_index, us[, bit])}    bit `bit` (default 0, the first, MSB of the
                                                humidity high byte) has a high time of `us`
                                                microseconds in total instead of TH0/TH1
      A fault is not a violation; the read's record gets "fault": the kind(s).

Records
-------
  reads : one dict per valid start signal (low >= Tbe min):
      {"t": tick at which the host released the line (the start signal's end),
       "start_us": the start low's duration in us,
       "data": bytes(5) actually sent (fault applied) or None if not answered,
       "answered": True once the sensor begins its answer,
       "bits": number of data bits sent (40 unless truncated),
       "segments": [(level, ticks), ...] the answer on the line from "t": Tgo (1), Trel (0),
                   Treh (1), then per bit TLOW (0) and TH (1), then Ten (0),
       "end": tick at which the sensor releases the line for good,
       "fault": fault kind(s), "aborted": True if the host cut the read during Tgo}
      ("bits"/"segments"/"end" only on answered reads; "fault"/"aborted" only when set.)
  violations : list of messages "t=<tick>: <what> (<section/symbol>)".
  errors() : the violations. finish() : end-of-run checks, then errors().

Ticks are those of the line level: a change the model sees at step t happened at tick t-1
(Board shows each device the previous tick's level), and all times reported are t-1.

Checks (each failure appends a violation; durations are compared with 0.1 us tolerance)
------
  1. Host start low shorter than Tbe min (0.8 ms; Table 6 Tbe, 7.2 Table 5 "at least
     800 us", 7.4 step 2): not a start signal. The sensor ignores it and records no read;
     it is reported as noise on the line. This is every low pulse on an idle line that
     ends before 0.8 ms (glitches included). Before the line has first been seen high, a
     low is ignored (pin not yet idle, like UartRx).
  2. Host start low longer than Tbe max (20 ms; Table 6 Tbe): flagged the moment it passes
     20 ms (so a line stuck low is caught); the sensor still answers when it ends.
  3. Host pulls SDA low during Tgo, after its start signal and before the sensor's
     response (7.4 step 2: the host releases the bus and the sensor answers; Table 6 Tgo
     is the sensor's time): the read is aborted ("aborted", not answered) and the new low
     is treated as a candidate start signal.
  4. Host pulls SDA low while the sensor releases it, from the response until the end of
     Ten (7.3 Pic 6; 7.4 steps 2-3; 4.3 / 7.2 single-bus: the host must leave the bus
     released while the sensor sends). Seen as the line low on a tick the sensor released
     it. One message per episode; the sensor carries on sending.
  5. Line high while the sensor pulls it low: the host drives SDA high push-pull
     (contention; 4.3 / 7.2: devices on the single bus are open-drain). One message per
     episode.
  6. Two start signals (ends) closer than min_interval_s (7.1 item 3, 7.3 note, Table 4
     sampling period 2 s). The sensor still answers.
  7. A start signal ending before startup_s (7.4 step 1): the sensor does not answer; the
     read is recorded with answered False.
  8. finish(): a start low still in progress, or the run ending during the sensor's
     answer (the host stopped before the read completed).
"""

from __future__ import annotations

import math
import random

from .board import Board, Device

# AM2302 Table 6: symbol -> (min, typ, max) in microseconds.
TABLE6 = {
    "Tbe": (800.0, 1000.0, 20000.0),   # host start signal low time (0.8 / 1 / 20 ms)
    "Tgo": (20.0, 30.0, 200.0),        # bus master released time
    "Trel": (75.0, 80.0, 85.0),        # response low time
    "Treh": (75.0, 80.0, 85.0),        # response high time
    "TLOW": (48.0, 50.0, 55.0),        # signal "0", "1" low time
    "TH0": (22.0, 26.0, 30.0),         # signal "0" high time
    "TH1": (68.0, 70.0, 75.0),         # signal "1" high time
    "Ten": (45.0, 50.0, 55.0),         # sensor release the bus time
}
TOL_US = 0.1
FAULT_KINDS = ("checksum", "no_response", "truncate", "stretch")


def encode(humidity: float, temperature: float) -> bytes:
    """The 5 bytes of AM2302 7.2 Table 5 for a reading (x10, sign bit 15, parity sum)."""
    h = math.floor(humidity * 10 + 0.5)
    tm = math.floor(abs(temperature) * 10 + 0.5)
    if not 0 <= h <= 0xFFFF:
        raise ValueError(f"humidity {humidity} does not fit 16 bits x10")
    if tm > 0x7FFF:
        raise ValueError(f"temperature {temperature} does not fit 15 bits x10")
    if temperature < 0 and tm:
        tm |= 0x8000
    b = [h >> 8, h & 0xFF, tm >> 8, tm & 0xFF]
    return bytes(b + [sum(b) & 0xFF])


def decode(data: bytes) -> tuple[float, float, bool]:
    """(humidity %RH, temperature C, parity ok) from 5 bytes (7.2 Table 5)."""
    h = (data[0] << 8) | data[1]
    tm = (data[2] << 8) | data[3]
    temp = (tm & 0x7FFF) / 10.0
    if tm & 0x8000:
        temp = -temp
    return h / 10.0, temp, (sum(data[:4]) & 0xFF) == data[4]


_IDLE, _START, _ANSWER = 0, 1, 2


class Dht22(Device):
    def __init__(self, pin: int = 0, tick_ns: float = 40.0, humidity=65.8, temperature=26.9,
                 raw=None, timing: str = "typ", seed: int = 1, startup_s: float = 2.0,
                 min_interval_s: float = 2.0, faults: dict | None = None):
        if timing not in ("typ", "min", "max", "random"):
            raise ValueError(f"timing {timing!r}: use typ, min, max or random")
        self.pin, self.mask = pin, 1 << pin
        self.tick_ns, self.timing = tick_ns, timing
        self.us = 1000.0 / tick_ns                   # ticks per microsecond
        self.startup_s, self.min_interval_s = startup_s, min_interval_s
        self._startup_t = startup_s * 1e6 * self.us
        self._interval_t = min_interval_s * 1e6 * self.us
        self._tol = TOL_US * self.us
        self._rng = random.Random(seed)
        self._ticks = {}                             # symbol -> (min, typ, max) in ticks
        for sym, (lo, typ, hi) in TABLE6.items():
            lo_t = max(1, math.ceil(lo * self.us - 1e-9))
            hi_t = max(lo_t, math.floor(hi * self.us + 1e-9))
            self._ticks[sym] = (lo_t, min(max(round(typ * self.us), lo_t), hi_t), hi_t)
        self._readings = self._make_readings(humidity, temperature, raw)
        self._nread = 0                              # readings consumed
        self._fault_at = self._parse_faults(faults)
        self.reads: list[dict] = []
        self.violations: list[str] = []
        self._state = _IDLE
        self._seen_idle = False
        self._drv = 0                                # driving low this tick
        self._pdrv = 0                               # ... and on the previous tick
        self._t_fall = 0
        self._long = False
        self._last_start = None
        self._sched: list[tuple[int, int]] = []      # (tick, drive_low) edges of the answer
        self._si = 0
        self._t_resp = 0
        self._rec = None
        self._lo_ep = self._hi_ep = False            # interference / contention episodes
        self._finished = False
        self._t = 0

    # ---- configuration helpers -------------------------------------------------------
    @staticmethod
    def _make_readings(humidity, temperature, raw):
        if raw is not None:
            items = [raw] if isinstance(raw[0], int) else list(raw)
            out = [bytes(r) for r in items]
            if any(len(r) != 5 for r in out):
                raise ValueError("raw readings must be 5 bytes each")
            return out
        hs = list(humidity) if isinstance(humidity, (list, tuple)) else [humidity]
        ts = list(temperature) if isinstance(temperature, (list, tuple)) else [temperature]
        n = max(len(hs), len(ts))
        return [encode(hs[min(k, len(hs) - 1)], ts[min(k, len(ts) - 1)]) for k in range(n)]

    @staticmethod
    def _parse_faults(faults):
        out: dict[int, dict] = {}
        for kind, spec in (faults or {}).items():
            if kind not in FAULT_KINDS:
                raise ValueError(f"unknown fault {kind!r}; use one of {FAULT_KINDS}")
            specs = spec if isinstance(spec, list) else [spec]
            for s in specs:
                if kind in ("checksum", "no_response"):
                    idx, args = int(s), ()
                else:
                    s = tuple(s)
                    idx, args = int(s[0]), tuple(s[1:])
                    if kind == "truncate" and not (len(args) == 1 and 0 <= args[0] < 40):
                        raise ValueError("truncate: (read_index, nbits) with 0 <= nbits < 40")
                    if kind == "stretch":
                        if len(args) == 1:
                            args = (args[0], 0)
                        if not (len(args) == 2 and args[0] > 0 and 0 <= args[1] < 40):
                            raise ValueError("stretch: (read_index, us[, bit]) with us > 0, 0 <= bit < 40")
                out.setdefault(idx, {})[kind] = args
        return out

    def _dur(self, sym: str) -> int:
        lo, typ, hi = self._ticks[sym]
        if self.timing == "typ":
            return typ
        if self.timing == "min":
            return lo
        if self.timing == "max":
            return hi
        return self._rng.randint(lo, hi)

    def _v(self, t, msg):
        self.violations.append(f"t={t}: {msg}")

    # ---- the bus -----------------------------------------------------------------------
    def step(self, t, bus):
        self._t = t
        lvl = (bus >> self.pin) & 1
        tl = t - 1                                   # the tick this level belongs to
        st = self._state
        if st == _IDLE:
            if lvl:
                self._seen_idle = True
            elif self._seen_idle:
                self._state, self._t_fall, self._long = _START, tl, False
            return 0, 0
        if st == _START:
            if lvl == 0:
                if not self._long and tl - self._t_fall > self._ticks["Tbe"][2] + self._tol:
                    self._long = True
                    self._v(tl, f"start signal low for more than Tbe max 20 ms, since "
                                f"t={self._t_fall} (AM2302 Table 6 Tbe)")
                return 0, 0
            self._start_end(tl, tl - self._t_fall)
            return 0, 0
        # _ANSWER: check the line against the sensor's own drive on the previous tick
        pd = self._pdrv
        if not pd:
            if lvl == 0:
                if tl < self._t_resp:                # check 3: host pulls low during Tgo
                    rec = self._rec
                    self._v(tl, f"host pulled SDA low {(tl - rec['t']) / self.us:.1f} us after "
                                f"releasing it, during Tgo, before the sensor's response: read "
                                f"{len(self.reads) - 1} aborted (AM2302 7.4 step 2, Table 6 Tgo)")
                    rec.update(answered=False, data=None, aborted=True)
                    for k in ("bits", "segments", "end"):
                        rec.pop(k, None)
                    self._drv = self._pdrv = 0
                    self._state, self._t_fall, self._long = _START, tl, False
                    return 0, 0
                if not self._lo_ep:                  # check 4: interference
                    self._lo_ep = True
                    self._v(tl, f"host pulled SDA low while the sensor released it, in "
                                f"{self._phase()} of read {len(self.reads) - 1}; the host must "
                                f"keep the bus released until the end of Ten (AM2302 7.3 Pic 6, "
                                f"7.4 steps 2-3)")
            else:
                self._lo_ep = False
        else:
            if lvl:
                if not self._hi_ep:                  # check 5: push-pull contention
                    self._hi_ep = True
                    self._v(tl, f"SDA high while the sensor pulls it low, in {self._phase()} of "
                                f"read {len(self.reads) - 1}: the host drives the line high "
                                f"(contention; AM2302 4.3, 7.2 single bus is open-drain)")
            else:
                self._hi_ep = False
        sched, i = self._sched, self._si
        while i < len(sched) and sched[i][0] <= t:
            self._drv = sched[i][1]
            i += 1
        self._si = i
        if i == len(sched):
            self._state = _IDLE
            self._rec = None
        d = self._drv
        self._pdrv = d
        return (self.mask if d else 0), 0

    def _phase(self) -> str:
        seg = self._si - 1                           # segment in force on the previous tick
        nb = self._rec.get("bits", 40) if self._rec else 40
        if seg <= 0:
            return "Tgo"
        if seg == 1:
            return "the response low Trel"
        if seg == 2:
            return "the response high Treh"
        k, part = divmod(seg - 3, 2)
        if k < nb:
            return f"bit {k} {'TLOW' if part == 0 else 'TH'}"
        return "Ten"

    def _start_end(self, end: int, dur: int):
        """The host released the line after a low of `dur` ticks (its first high tick: end)."""
        us = dur / self.us
        self._state = _IDLE
        if dur < self._ticks["Tbe"][0] - self._tol:   # check 1: noise, not a start
            self._v(end, f"SDA low for {us:.1f} us from t={self._t_fall}: shorter than Tbe min "
                         f"0.8 ms, not a start signal; ignored as noise (AM2302 Table 6 Tbe, "
                         f"7.2 Table 5, 7.4 step 2)")
            return
        idx = len(self.reads)
        rec = {"t": end, "start_us": us, "data": None, "answered": False}
        self.reads.append(rec)
        if us > TABLE6["Tbe"][2] + TOL_US and not self._long:   # check 2 (normally caught live)
            self._v(end, f"start signal low {us:.1f} us, over Tbe max 20 ms (AM2302 Table 6 Tbe)")
        if self._last_start is not None and end - self._last_start < self._interval_t:
            gap = (end - self._last_start) / self.us / 1e6        # check 6
            self._v(end, f"read {idx} starts {gap:.6f} s after read {idx - 1}; reads must be at "
                         f"least {self.min_interval_s} s apart (AM2302 7.1 item 3, 7.3 note, "
                         f"Table 4 sampling period)")
        self._last_start = end
        if end < self._startup_t:                                 # check 7
            self._v(end, f"read {idx} at {end / self.us / 1e6:.6f} s after power-up, before "
                         f"startup {self.startup_s} s: the sensor does not answer (AM2302 7.4 "
                         f"step 1)")
            return
        faults = self._fault_at.get(idx, {})
        if faults:
            rec["fault"] = sorted(faults) if len(faults) > 1 else next(iter(faults))
        if "no_response" in faults:
            return
        data = self._readings[min(self._nread, len(self._readings) - 1)]
        self._nread += 1
        if "checksum" in faults:
            data = data[:4] + bytes([data[4] ^ 0x10])
        nbits = faults["truncate"][0] if "truncate" in faults else 40
        stretch = faults.get("stretch")
        segs = [(1, self._dur("Tgo")), (0, self._dur("Trel")), (1, self._dur("Treh"))]
        for k in range(nbits):
            bit = (data[k >> 3] >> (7 - (k & 7))) & 1
            segs.append((0, self._dur("TLOW")))
            th = self._dur("TH1" if bit else "TH0")
            if stretch and stretch[1] == k:
                th = max(1, round(stretch[0] * self.us))
            segs.append((1, th))
        if nbits == 40:
            segs.append((0, self._dur("Ten")))
        sched, tk = [], end
        for level, n in segs:
            sched.append((tk, 1 - level))
            tk += n
        sched.append((tk, 0))
        self._sched, self._si = sched, 0
        self._t_resp = end + segs[0][1]
        self._drv = self._pdrv = 0
        self._lo_ep = self._hi_ep = False
        rec.update(data=bytes(data), answered=True, bits=nbits, segments=segs, end=tk)
        self._rec = rec
        self._state = _ANSWER

    # ---- results -----------------------------------------------------------------------
    def finish(self) -> list[str]:
        if not self._finished:
            self._finished = True
            if self._state == _START:
                self._v(self._t, f"run ended with SDA held low since t={self._t_fall} "
                                 f"(start signal never released; AM2302 Table 6 Tbe)")
            elif self._state == _ANSWER:
                self._v(self._t, f"run ended during the sensor's answer to read "
                                 f"{len(self.reads) - 1} (in {self._phase()}; AM2302 7.3, 7.4 step 3)")
        return self.errors()

    def errors(self) -> list[str]:
        return list(self.violations)


# ---- self-test ---------------------------------------------------------------------------
def _run(dev: Dht22, n: int, low=(), high=()):
    """Run a synthetic host (the chip side of pin dev.pin) for n ticks. `low`/`high` are
    (t0, t1) tick ranges in which the chip drives SDA low / high push-pull."""
    mode = bytearray(n)
    for a, b in low:
        mode[a:b] = b"\x01" * (b - a)
    for a, b in high:
        mode[a:b] = b"\x02" * (b - a)
    m = dev.mask
    board = Board(dev, pullups=0xFF)
    for t in range(n):
        md = mode[t]
        board(t, m if md == 2 else 0, m if md else 0)
    return board


def _runs(levels, a, b):
    out = []
    for v in levels[a:b]:
        if out and out[-1][0] == v:
            out[-1][1] += 1
        else:
            out.append([v, 1])
    return [tuple(r) for r in out]


def _symbols(nbits, full=True):
    syms = ["Tgo", "Trel", "Treh"]
    return syms + ["TLOW", "TH"] * nbits + (["Ten"] if full else [])


def _host_decode(board, dev, rec, fails, tag, mode=None):
    """Decode one answered read from the bus as a host would and check it against the
    model's record and Table 6."""
    lv = board.level(dev.pin)
    segs = rec["segments"]
    runs = _runs(lv, rec["t"], rec["end"] + 5)
    n = len(segs)
    if rec["bits"] == 40:       # ends with Ten low, then the released line
        ok = runs[:n] == [tuple(s) for s in segs] and runs[n][0] == 1
    else:                       # truncated: the last bit's high merges with the idle line
        ok = runs[:n - 1] == [tuple(s) for s in segs[:-1]] and runs[n - 1][0] == 1 \
            and runs[n - 1][1] >= segs[-1][1] and len(runs) == n
    if not ok:
        fails.append(f"{tag}: bus runs differ from the model's segments")
        return None
    bits = []
    for k in range(rec["bits"]):
        bits.append(1 if segs[4 + 2 * k][1] / dev.us > 48 else 0)
    if mode is not None:
        k = 0
        for sym, (_, n) in zip(_symbols(rec["bits"]), segs):
            if sym == "TH":
                sym = "TH1" if bits[k] else "TH0"
                k += 1
            lo, typ, hi = dev._ticks[sym]
            want = {"min": lo, "typ": typ, "max": hi}.get(mode)
            if want is not None and n != want:
                fails.append(f"{tag}: {sym} {n} ticks, want {want} ({mode})")
            if not lo <= n <= hi:
                fails.append(f"{tag}: {sym} {n} ticks outside Table 6 {lo}..{hi}")
            umin, _, umax = TABLE6[sym]
            if not umin - TOL_US <= n / dev.us <= umax + TOL_US:
                fails.append(f"{tag}: {sym} {n / dev.us:.2f} us outside Table 6")
    data = bytes(sum(bits[8 * i + j] << (7 - j) for j in range(8)) for i in range(len(bits) // 8))
    return data


def _selftest() -> bool:
    fails: list[str] = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    # 7.2 worked examples
    ex1 = bytes([0b00000010, 0b10010010, 0b00000001, 0b00001101, 0b10100010])
    check(encode(65.8, 26.9) == ex1, "7.2 example 1 encoding")
    check(decode(ex1) == (65.8, 26.9, True), "7.2 example 1 decoding")
    ex2 = ex1[:4] + bytes([0b10110010])
    check(decode(ex2)[2] is False, "7.2 example 2 parity error")
    neg = encode(45.0, -10.1)
    check(neg[2:4] == bytes([0x80, 0x65]), "7.2 special instruction -10.1 C")
    check(decode(neg)[:2] == (45.0, -10.1), "negative temperature decode")

    # legal host, all four timing modes (1 us ticks keep the runs short)
    hums, temps = [65.8, 45.0, 0.0], [26.9, -10.1, -0.5]
    want = [encode(h, tm) for h, tm in zip(hums, temps)]
    want.append(want[-1])
    lows = [1000, 800, 20000, 1000]
    for mode in ("typ", "min", "max", "random"):
        dev = Dht22(pin=3, tick_ns=1000.0, humidity=hums, temperature=temps, timing=mode,
                    seed=7, startup_s=0.002, min_interval_s=0.01)
        script, s = [], 2000
        for lw in lows:
            script.append((s, s + lw))
            s += lw + 10000
        board = _run(dev, s + 1000, low=script)
        errs = dev.finish()
        check(not errs and not board.errors(), f"legal {mode}: {errs[:3]}")
        check(len(dev.reads) == 4 and all(r["answered"] for r in dev.reads), f"legal {mode}: reads")
        for k, rec in enumerate(dev.reads[:4]):
            check(rec["t"] == script[k][1] and abs(rec["start_us"] - lows[k]) < 1e-6,
                  f"legal {mode}: read {k} start record")
            got = _host_decode(board, dev, rec, fails, f"legal {mode} read {k}", mode)
            check(got == want[k] and rec["data"] == want[k], f"legal {mode} read {k}: {got!r}")

    # 40 ns ticks (the chip clock), typ and random, raw 7.2 example bytes
    for mode in ("typ", "random"):
        dev = Dht22(pin=0, tick_ns=40.0, raw=ex1, timing=mode, seed=3, startup_s=0.0002,
                    min_interval_s=0.01)
        a = 5000
        board = _run(dev, a + 25000 + 6000 * 25, low=[(a, a + 25000)])
        check(not dev.finish(), f"40ns {mode}: {dev.errors()[:3]}")
        rec = dev.reads[0]
        got = _host_decode(board, dev, rec, fails, f"40ns {mode}", mode)
        check(got == ex1, f"40ns {mode}: decoded {got!r}")

    def one(tag, n, low=(), high=(), want_sub=(), **kw):
        args = dict(pin=1, tick_ns=1000.0, startup_s=0.001, min_interval_s=0.01)
        args.update(kw)
        dev = Dht22(**args)
        board = _run(dev, n, low=low, high=high)
        errs = dev.finish()
        for sub in want_sub:
            check(any(sub in e for e in errs), f"{tag}: no violation containing {sub!r}: {errs}")
        if not want_sub:
            check(not errs, f"{tag}: unexpected {errs}")
        return dev, board

    # illegal hosts
    dev, _ = one("short start", 8000, low=[(2000, 2500)], want_sub=["shorter than Tbe min"])
    check(dev.reads == [], "short start recorded as a read")
    dev, _ = one("glitch", 8000, low=[(3000, 3002)], want_sub=["not a start signal"])
    dev, _ = one("long start", 34000, low=[(2000, 27000)], want_sub=["Tbe max"])
    check(dev.reads and dev.reads[0]["answered"], "long start: sensor should still answer")
    dev, _ = one("low during data", 12000, low=[(2000, 3000), (3400, 3700)],
                 want_sub=["while the sensor released it"])
    dev, _ = one("low during Tgo", 12000, low=[(2000, 3000), (3010, 3015)],
                 want_sub=["during Tgo", "not a start signal"])
    check(dev.reads[0].get("aborted") and not dev.reads[0]["answered"], "Tgo abort record")
    dev, board = one("high during response", 12000, low=[(2000, 3000)], high=[(3040, 3060)],
                     want_sub=["contention"])
    check(board.contention, "board saw no contention")
    dev, _ = one("reads too close", 24000, low=[(2000, 3000), (10000, 11000)],
                 want_sub=["7.1 item 3"])
    check(len(dev.reads) == 2, "too-close reads not both recorded")
    dev, _ = one("before startup", 12000, low=[(500, 1500)], startup_s=0.005,
                 want_sub=["does not answer"])
    check(dev.reads and not dev.reads[0]["answered"] and dev.reads[0]["data"] is None,
          "before-startup read answered")
    dev, _ = one("stuck low", 30000, low=[(2000, 30000)], want_sub=["more than Tbe max", "held low"])
    dev, _ = one("cut short", 4000, low=[(2000, 3000)], want_sub=["run ended during"])

    # faults
    dev, board = one("checksum fault", 12000, low=[(2000, 3000)], faults={"checksum": 0})
    got = _host_decode(board, dev, dev.reads[0], fails, "checksum fault")
    check(got is not None and got[:4] == ex1[:4] and not decode(got)[2], "checksum fault: parity ok")
    dev, board = one("no_response fault", 12000, low=[(2000, 3000)], faults={"no_response": 0})
    check(not dev.reads[0]["answered"] and set(board.level(1)[3000:]) == {1}, "no_response answered")
    dev, board = one("truncate fault", 12000, low=[(2000, 3000)], faults={"truncate": (0, 12)})
    rec = dev.reads[0]
    runs = _runs(board.level(1), rec["t"], 12000)
    check(rec["bits"] == 12 and len(runs) == 3 + 24 and runs[-1][0] == 1, "truncate: shape")
    got = _host_decode(board, dev, rec, fails, "truncate fault")
    check(got == ex1[:1], f"truncate: first byte {got!r}")
    dev, board = one("stretch fault", 12000, low=[(2000, 3000)], faults={"stretch": [(0, 120, 5)]})
    rec = dev.reads[0]
    check(rec["segments"][4 + 2 * 5] == (1, 120), "stretch: bit 5 high time")
    check(_runs(board.level(1), rec["t"], rec["end"] + 5)[4 + 2 * 5] == (1, 120), "stretch on bus")

    for f in fails:
        print("dht22 selftest FAIL:", f)
    return not fails
