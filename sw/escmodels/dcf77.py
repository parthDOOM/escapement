"""DCF77 time code receiver at logic level (low while the carrier is reduced).

Written from PTB's description of the DCF77 time code, not from any firmware: a mark at
the start of every second, 0.1 s for a 0 and 0.2 s for a 1, none in second 59; bit k of
the minute in second k; minutes in bits 21-27, hours 29-34, day of month 36-41, day of
week 42-44 (Monday 1), month 45-49, year 50-57, BCD least significant bit first; even parity
over 21-28 (P1 in 28), 29-35 (P2 in 35) and 36-58 (P3 in 58); bit 0 is 0 and bit 20 is 1.
"""

from __future__ import annotations

from .board import Device

FIELDS = {"minute": (21, 7), "hour": (29, 6), "day": (36, 6), "weekday": (42, 3),
          "month": (45, 5), "year": (50, 8)}
WEIGHTS = [1, 2, 4, 8, 10, 20, 40, 80]


def bcd(bits):
    return sum(w for b, w in zip(bits, WEIGHTS) if b)


def encode(minute, hour, day, weekday, month, year, cest=False):
    """The 59 bits of a minute (bit 0 first), with correct parity, for tests."""
    bits = [0] * 59
    for name, v in (("minute", minute), ("hour", hour), ("day", day), ("weekday", weekday),
                    ("month", month), ("year", year)):
        start, n = FIELDS[name]
        tens, ones = divmod(v, 10)
        val = [(ones >> k) & 1 for k in range(4)] + [(tens >> k) & 1 for k in range(4)]
        bits[start:start + n] = val[:n]
    bits[17], bits[18] = (1, 0) if cest else (0, 1)
    bits[20] = 1
    bits[28] = sum(bits[21:28]) & 1
    bits[35] = sum(bits[29:35]) & 1
    bits[58] = sum(bits[36:58]) & 1
    return bits


class Dcf77Receiver(Device):
    def __init__(self, pin: int, tick_ns: float):
        self.pin, self.tick = pin, tick_ns
        self.levels: list[int] = []
        self.minutes: list[dict] = []
        self.violations: list[str] = []
        self.falls: list[int] = []

    def step(self, t, bus):
        self.levels.append((bus >> self.pin) & 1)
        return 0, 0

    def _ms(self, ticks):
        return ticks * self.tick / 1e6

    def finish(self):
        lv = self.levels
        marks = []                                      # (fall, width in ms)
        t = 1
        while t < len(lv):
            if lv[t - 1] == 1 and lv[t] == 0:
                f = t
                while t < len(lv) and lv[t] == 0:
                    t += 1
                if t < len(lv):
                    marks.append((f, self._ms(t - f)))
            t += 1
        self.falls = [f for f, _ in marks]
        frame: list[int] = []
        for k, (f, w) in enumerate(marks):
            if 70 <= w <= 130:
                b = 0
            elif 170 <= w <= 230:
                b = 1
            else:
                self.violations.append(f"mark of {w:.1f} ms at tick {f}")
                b = None
            if k:
                gap = self._ms(f - marks[k - 1][0])
                if 1900 <= gap <= 2100:                 # second 59: the minute ends
                    self._minute(frame)
                    frame = []
                elif not 990 <= gap <= 1010:
                    self.violations.append(f"seconds {gap:.1f} ms apart at tick {f}")
            frame.append(b)

    def _minute(self, bits):
        if len(bits) != 59 or None in bits:
            self.violations.append(f"a minute of {len(bits)} marks")
            return
        ok = (bits[0] == 0 and bits[20] == 1 and sum(bits[21:29]) % 2 == 0
              and sum(bits[29:36]) % 2 == 0 and sum(bits[36:59]) % 2 == 0)
        if not ok:
            self.violations.append("bit 0, bit 20 or a parity wrong")
            return
        m = {name: bcd(bits[s:s + n]) for name, (s, n) in FIELDS.items()}
        m["cest"] = bits[17] == 1 and bits[18] == 0
        self.minutes.append(m)
