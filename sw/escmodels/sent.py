"""SENT (SAE J2716) receiver, as an engine controller decodes a sensor.

Written from NXP AN4219 and Microchip's SENT module documentation: every pulse starts
with a falling edge and is low for at least 4 unit times (5 nominal); the time between
falling edges carries it. A frame is a 56-unit sync and calibration pulse, a status
nibble, six data nibbles and a CRC nibble, each 12 + value units, optionally followed
by a pause pulse (12 to 768 units). The receiver measures the unit time from the sync
pulse, so a sensor clock may be off by up to 20%. CRC: x^4 + x^3 + x^2 + 1, seed 5, over
the data nibbles, then one zero nibble (the recommended method).
"""

from __future__ import annotations

from .board import Device

SENT_CRC_TABLE = (0, 13, 7, 10, 14, 3, 9, 4, 1, 12, 6, 11, 15, 2, 8, 5)


def sent_crc(nibbles) -> int:
    c = 5
    for n in nibbles:
        c = n ^ SENT_CRC_TABLE[c]
    return SENT_CRC_TABLE[c]          # one zero nibble


class SentReceiver(Device):
    """Decodes frames on `pin`. `frames` holds dicts: status, data (six nibbles), crc_ok,
    sync (tick of the sync pulse's falling edge), unit (measured, ticks), pause (units or
    None). An interval more than `max_err` units from a whole number is a violation.
    `pause`: the sensor sends a pause pulse after each frame (a receiver is configured for
    it: a pause may be as long as a sync pulse)."""

    def __init__(self, pin: int, tick_ns: float = 40.0, ut_us: float = 3.0, max_err: float = 0.1,
                 pause: bool = False):
        self.pin, self.tick, self.pause = pin, tick_ns, pause
        self.ut_nom = ut_us * 1000 / tick_ns
        self.max_err = max_err
        self.frames: list[dict] = []
        self.violations: list[str] = []
        self._prev = None
        self._falls: list[int] = []
        self._fall = None
        self._unit = None
        self._cur = None               # (sync tick, unit, nibbles so far)

    def step(self, t, bus):
        lvl = (bus >> self.pin) & 1
        if self._prev is None:
            self._prev = lvl
            return 0, 0
        if self._prev and not lvl:
            if self._fall is not None:
                self._interval(self._fall, t)
            self._fall = t
        elif lvl and not self._prev and self._fall is not None and self._unit:
            low = (t - self._fall) / self._unit
            if low < 4 - self.max_err:
                self.violations.append(f"t={t}: low for {low:.2f} units")
        self._prev = lvl
        return 0, 0

    def _interval(self, a, b):
        d = b - a
        done = self._cur is not None and len(self._cur[2]) == 8
        if done and self.pause:
            pass                       # the pause pulse (below)
        elif 56 * self.ut_nom * 0.8 <= d <= 56 * self.ut_nom * 1.2 and (self._cur is None or done):
            if done:                   # a sync pulse right after a frame: no pause
                self._finish(None)
            self._unit = d / 56
            self._cur = (a, self._unit, [])
            return
        if self._cur is None:
            return                     # not synchronised yet
        units = d / self._cur[1]
        n = round(units)
        if abs(units - n) > self.max_err:
            self.violations.append(f"t={b}: interval of {units:.2f} units")
        if len(self._cur[2]) < 8:
            if not 12 <= n <= 27:
                self.violations.append(f"t={b}: nibble pulse of {n} units")
                self._cur = None
                return
            self._cur[2].append(n - 12)
        else:                          # a pause pulse after the CRC nibble
            if not 12 <= n <= 768:
                self.violations.append(f"t={b}: pause of {n} units")
            self._finish(n)

    def _finish(self, pause):
        sync, unit, nib = self._cur
        self._cur = None
        self.frames.append({"status": nib[0], "data": nib[1:7], "crc_ok": nib[7] == sent_crc(nib[1:7]),
                            "sync": sync, "unit": unit, "pause": pause})

    def errors(self):
        return [f"sent: {v}" for v in self.violations[:5]] + \
               [f"sent: CRC error in frame {i}" for i, f in enumerate(self.frames) if not f["crc_ok"]][:5]
