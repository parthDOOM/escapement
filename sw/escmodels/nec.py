"""NEC infrared remote protocol: a receiver that demodulates and checks the carrier and
the frame timing, a TSOP-style demodulator, and a transmitter for testing receivers.

The NEC format (Vishay, "Data Formats for IR Remote Control", doc. 80071; SB-Projects,
"NEC Protocol"): a 38 kHz carrier with 1/4 to 1/3 duty; a frame is a 9 ms leader
burst, a 4.5 ms space, 32 pulse-distance bits sent LSB first (address, ~address,
command, ~command; a 562.5 us burst, then the next burst after 1.125 ms for a 0 or
2.25 ms for a 1) and a 562.5 us stop burst. A repeat code is a 9 ms burst, a 2.25 ms
space and a 562.5 us burst. One transmission per 108 ms slot. If the second byte is
not the inverse of the first, the address is a 16-bit extended address.

The format documents give nominal values only, so two kinds of check are made:

* acceptance: each mark and space must fall inside the window a real decoder accepts.
  The windows are those of the Linux kernel decoder (drivers/media/rc/ir-nec-decoder.c):
  leader mark 16 units +/- 2, leader space 8 +/- 1, repeat space 4 +/- 0.5, bit and
  stop marks 1 +/- 0.5, bit spaces 1 or 3 +/- 0.5 (unit 562.5 us; open intervals).
* transmitter accuracy: every mark, and every burst-to-burst distance, must be within
  `tx_tol` (default 1%) of nominal, the carrier frequency within `carrier_tol` of
  38 kHz and its duty between 1/4 and 1/3 (with `duty_slack`), and leader starts at
  least one 108 ms slot apart.
"""

from __future__ import annotations

from .board import Device

UNIT_US = 562.5
LEADER_US = 16 * UNIT_US                 # 9 ms
HDR_SPACE_US = 8 * UNIT_US               # 4.5 ms
RPT_SPACE_US = 4 * UNIT_US               # 2.25 ms
BIT_MARK_US = UNIT_US
BIT_SPACE_US = (UNIT_US, 3 * UNIT_US)    # 0, 1
SLOT_US = 108_000.0
CARRIER_HZ = 38_000.0


def nec_raw(addr: int, cmd: int) -> int:
    """32-bit frame, bit 0 sent first: address, ~address, command, ~command."""
    return (addr & 0xFF) | ((~addr & 0xFF) << 8) | ((cmd & 0xFF) << 16) | ((~cmd & 0xFF) << 24)


def _within(x, nominal, margin):
    return nominal - margin < x < nominal + margin


class _Demod:
    """Carrier detector: a mark starts at a rising edge after silence and ends at the
    last falling edge before `gap` ticks pass with no rising edge."""

    def __init__(self, gap):
        self.gap = gap
        self.prev = None
        self.first_rise = self.last_rise = self.last_fall = None
        self.rises: list[int] = []
        self.highs: list[int] = []

    def step(self, t, lvl):
        """Returns a completed mark (start, end, rises, highs) or None."""
        done = None
        if self.prev is not None:
            if lvl and not self.prev:
                if self.first_rise is None:
                    self.first_rise, self.rises, self.highs = t, [], []
                self.rises.append(t)
                self.last_rise = t
            elif not lvl and self.prev and self.first_rise is not None:
                self.highs.append(t - self.last_rise)
                self.last_fall = t
        if (self.first_rise is not None and not lvl and self.last_fall is not None
                and self.last_fall >= self.last_rise and t - self.last_rise > self.gap):
            done = (self.first_rise, self.last_fall, self.rises, self.highs)
            self.first_rise = self.last_fall = None
        self.prev = lvl
        return done


class NecReceiver(Device):
    """Receives NEC on `pin`. `modulated`: the pin carries the carrier (an IR LED drive,
    active high); otherwise it is a demodulated envelope (`active_low` like a TSOP)."""

    def __init__(self, pin: int, tick_ns: float = 40.0, modulated: bool = True,
                 active_low: bool = False, tx_tol: float = 0.01, carrier_tol: float = 0.02,
                 duty_slack: float = 0.01):
        self.pin, self.tick = pin, tick_ns
        self.modulated, self.active_low = modulated, active_low
        self.tx_tol, self.carrier_tol, self.duty_slack = tx_tol, carrier_tol, duty_slack
        self.events: list[dict] = []          # decoded frames and repeats, in order
        self.violations: list[str] = []
        self.carrier_periods: list[int] = []  # ticks, rise to rise within bursts
        self.duties: list[float] = []
        self.pulses: list[int] = []           # carrier pulses per burst
        self.marks: list[tuple[int, int]] = []
        self._demod = _Demod(gap=2 * 1e9 / CARRIER_HZ / tick_ns)
        self._prev = None
        self._env_start = None
        self._state = "idle"
        self._last_leader = None

    def us(self, ticks):
        return ticks * self.tick / 1000.0

    def _v(self, t, msg):
        self.violations.append(f"t={t}: {msg}")

    # ------------------------------------------------------------ physical layer
    def step(self, t, bus):
        lvl = (bus >> self.pin) & 1
        if self.modulated:
            mark = self._demod.step(t, lvl)
            if mark is not None:
                start, end, rises, highs = mark
                self._carrier(start, rises, highs)
                self._mark(start, end)
        else:
            active = lvl ^ self.active_low
            if self._prev is not None:
                if active and not self._prev:
                    self._env_start = t
                elif not active and self._prev and self._env_start is not None:
                    self._mark(self._env_start, t)
            self._prev = active
        return 0, 0

    def _carrier(self, t, rises, highs):
        self.pulses.append(len(rises))
        periods = [b - a for a, b in zip(rises, rises[1:])]
        self.carrier_periods += periods
        nominal = 1e9 / CARRIER_HZ / self.tick
        for p in set(periods):
            if abs(p - nominal) > self.carrier_tol * nominal:
                self._v(t, f"carrier {1e6 / (p * self.tick):.2f} kHz")
        for p, h in zip(periods, highs):
            d = h / p
            self.duties.append(d)
            if not 0.25 - self.duty_slack <= d <= 1 / 3 + self.duty_slack:
                self._v(t, f"carrier duty {d:.3f}")

    # ------------------------------------------------------------ frame layer
    def _accurate(self, t, what, got_us, nominal_us):
        if abs(got_us - nominal_us) > self.tx_tol * nominal_us:
            self._v(t, f"{what} {got_us:.1f} us, nominal {nominal_us:.1f} us")

    def _burst(self, start, mark):
        """A bit or stop burst: accepted, and accurate."""
        if not _within(mark, BIT_MARK_US, UNIT_US / 2):
            self._v(start, f"burst {mark:.1f} us")
            return False
        self._accurate(start, "burst", mark, BIT_MARK_US)
        return True

    def _leader(self, start, mark):
        self._state = "idle"
        if not _within(mark, LEADER_US, 2 * UNIT_US):
            self._v(start, f"burst {mark:.1f} us where a leader was expected")
            return
        self._accurate(start, "leader", mark, LEADER_US)
        since = self.us(start - self._last_leader) if self._last_leader is not None else None
        if since is not None and since < SLOT_US * (1 - self.tx_tol):
            self._v(start, f"leader {since:.0f} us after the previous one (slot 108 ms)")
        self._last_leader = start
        self._state = "header"

    def _mark(self, start, end):
        self.marks.append((start, end))
        mark = self.us(end - start)
        st = self._state
        if st != "idle":
            space = self.us(start - self._prev_mark[1])
            dist = self.us(start - self._prev_mark[0])
        self._prev_mark = (start, end)
        if st == "idle":
            self._leader(start, mark)
        elif st == "header":
            if _within(space, HDR_SPACE_US, UNIT_US):
                self._accurate(start, "leader to first bit", dist, LEADER_US + HDR_SPACE_US)
                if self._burst(start, mark):
                    self._state, self._bits, self._n = "bits", 0, 0
                else:
                    self._state = "idle"
            elif _within(space, RPT_SPACE_US, UNIT_US / 2):
                self._accurate(start, "leader to repeat burst", dist, LEADER_US + RPT_SPACE_US)
                if self._burst(start, mark):
                    self.events.append({"kind": "repeat", "t": self._last_leader})
                self._state = "idle"
            else:
                self._v(start, f"space after leader {space:.1f} us")
                self._leader(start, mark)
        elif st == "bits":
            if _within(space, BIT_SPACE_US[1], UNIT_US / 2):
                b = 1
            elif _within(space, BIT_SPACE_US[0], UNIT_US / 2):
                b = 0
            else:
                self._v(start, f"bit {self._n} space {space:.1f} us")
                self._leader(start, mark)
                return
            self._accurate(start, f"bit {self._n} distance", dist, BIT_MARK_US + BIT_SPACE_US[b])
            if not self._burst(start, mark):
                self._state = "idle"
                return
            self._bits |= b << self._n
            self._n += 1
            if self._n == 32:                  # this burst was the stop burst
                self._frame(self._bits)
                self._state = "idle"

    def _frame(self, raw):
        addr, naddr, cmd, ncmd = (raw >> s & 0xFF for s in (0, 8, 16, 24))
        ok = cmd ^ ncmd == 0xFF
        if not ok:
            self._v(self._last_leader, f"command 0x{cmd:02x} not followed by its inverse")
        ext = addr ^ naddr != 0xFF
        self.events.append({"kind": "frame", "t": self._last_leader, "raw": raw,
                            "addr": raw & 0xFFFF if ext else addr, "ext": ext,
                            "cmd": cmd, "ok": ok})

    def frames(self):
        """[(addr, cmd)] for frames, "R" for repeat codes, in order."""
        return [(e["addr"], e["cmd"]) if e["kind"] == "frame" else "R" for e in self.events]

    def errors(self):
        return [f"NEC: {v}" for v in self.violations[:10]]


class Tsop(Device):
    """IR receiver module: demodulates the carrier on `ir` and drives `out` low while a
    burst is present (open-collector with the board pull-up), delayed by the
    detection time `gap` (two carrier periods), like a TSOP output."""

    def __init__(self, ir: int, out: int, tick_ns: float = 40.0):
        self.ir, self.out = ir, out
        self.gap = int(2 * 1e9 / CARRIER_HZ / tick_ns)
        self._demod = _Demod(self.gap)
        self._marks: list[tuple[int, int]] = []

    def step(self, t, bus):
        mark = self._demod.step(t, (bus >> self.ir) & 1)
        if mark is not None:
            self._marks.append((mark[0] + self.gap, mark[1] + self.gap))
        while self._marks and self._marks[0][1] <= t:
            self._marks.pop(0)
        cur = self._demod.first_rise                    # a burst still in progress
        low = ((bool(self._marks) and self._marks[0][0] <= t)
               or (cur is not None and cur + self.gap <= t))
        return ((1 << self.out) if low else 0), 0


class NecSource(Device):
    """A remote control from the specification: sends `items` [(start_tick, raw32 or
    None for a repeat code)] on `pin`, either as the carrier (active high) or as a
    TSOP-style demodulated output (low during a burst). `scale` stretches every
    duration (a remote whose oscillator is off by scale - 1); `mark_bias_us` lengthens
    each burst at the expense of the following space (TSOP pulse-width distortion)."""

    def __init__(self, pin: int, items, tick_ns: float = 40.0, modulated: bool = False,
                 scale: float = 1.0, mark_bias_us: float = 0.0):
        self.pin, self.tick, self.modulated = pin, tick_ns, modulated
        self.marks: list[tuple[float, float]] = []      # (start, end) in ticks
        u = UNIT_US * 1000 / tick_ns * scale
        bias = mark_bias_us * 1000 / tick_ns
        for t0, raw in sorted(items, key=lambda x: x[0]):
            seq = [(16 * u, 4 * u if raw is None else 8 * u)]
            if raw is not None:
                seq += [(u, (3 if raw >> i & 1 else 1) * u) for i in range(32)]
            seq += [(u, 0)]
            t = t0
            for mark, space in seq:
                self.marks.append((t, t + mark + bias))
                t += mark + space
        self.end = self.marks[-1][1] if self.marks else 0
        self._period = 1e9 / CARRIER_HZ / tick_ns
        self._i = 0

    def step(self, t, bus):
        while self._i < len(self.marks) and self.marks[self._i][1] <= t:
            self._i += 1
        on = self._i < len(self.marks) and self.marks[self._i][0] <= t
        if self.modulated:
            on = on and (t - self.marks[self._i][0]) % self._period < self._period / 3
            return (0, 1 << self.pin) if on else (1 << self.pin, 0)
        return ((1 << self.pin) if on else 0), 0
