"""DShot ESC input: decodes frames from pulse widths and checks the timing and the CRC.

Written from brushlesswhoop.com, "DSHOT - the missing Handbook": DShot600 has a 1.67 us
bit, a 1 is high for 1.25 us and a 0 for 0.625 us; a frame is 16 bits MSB first, an
11-bit value, a telemetry bit and the CRC (v ^ v >> 4 ^ v >> 8) & 0xF of the 12-bit v.
Bidirectional DShot inverts the line and the CRC.
"""

from __future__ import annotations

from .board import Device


def dshot_crc(v12: int, bidir: bool = False) -> int:
    c = (v12 ^ (v12 >> 4) ^ (v12 >> 8)) & 0xF
    return c ^ 0xF if bidir else c


class DshotEsc(Device):
    """One ESC on `pin`. A frame is a burst of 16 active pulses; a gap of more than
    `gap_us` of idle line ends it. Windows: high time within `tol_ns` of 625 or 1250 ns,
    pulse to pulse within `tol_ns` of the bit period. `frames` holds (value, telemetry,
    crc_ok, start tick); `bit_starts` the start tick of every pulse of every frame."""

    def __init__(self, pin: int, rate_kbit: int = 600, tick_ns: float = 40.0, bidir: bool = False,
                 tol_ns: float = 100.0, gap_us: float = 5.0):
        self.pin, self.tick, self.bidir = pin, tick_ns, bidir
        self.bit_ns = 1e6 / rate_kbit
        self.t1h, self.t0h = self.bit_ns * 0.75, self.bit_ns * 0.375
        self.tol = tol_ns
        self.gap = gap_us * 1000 / tick_ns
        self.frames: list[tuple[int, int, bool, int]] = []
        self.bit_starts: list[list[int]] = []
        self.violations: list[str] = []
        self._prev = None
        self._rise = None
        self._last_edge = None
        self._bits: list[int] = []
        self._starts: list[int] = []

    def step(self, t, bus):
        lvl = (bus >> self.pin) & 1
        act = lvl ^ self.bidir
        if self._prev is None:
            self._prev = act
            return 0, 0
        if self._bits and not act and self._last_edge is not None and t - self._last_edge > self.gap:
            self._end()
        if act and not self._prev:                                  # pulse starts
            if self._starts:
                p = (t - self._starts[-1]) * self.tick
                if abs(p - self.bit_ns) > self.tol:
                    self.violations.append(f"t={t}: bit period {p:.0f} ns")
            self._rise = t
            self._starts.append(t)
            self._last_edge = t
        elif not act and self._prev and self._rise is not None:     # pulse ends
            h = (t - self._rise) * self.tick
            if abs(h - self.t0h) <= self.tol:
                self._bits.append(0)
            elif abs(h - self.t1h) <= self.tol:
                self._bits.append(1)
            else:
                self.violations.append(f"t={t}: high time {h:.0f} ns")
                self._bits.append(-1)
            self._rise = None
            self._last_edge = t
        self._prev = act
        return 0, 0

    def _end(self):
        bits, starts = self._bits, self._starts
        self._bits, self._starts = [], []
        if len(bits) != 16 or -1 in bits:
            self.violations.append(f"frame of {len(bits)} bits at t={starts[0] if starts else None}")
            return
        word = int("".join(map(str, bits)), 2)
        v12, crc = word >> 4, word & 0xF
        self.frames.append((v12 >> 1, v12 & 1, crc == dshot_crc(v12, self.bidir), starts[0]))
        self.bit_starts.append(starts)

    def flush(self):
        """End a frame still open at the end of a run."""
        if self._bits:
            self._end()

    def errors(self):
        return [f"dshot pin {self.pin}: {v}" for v in self.violations[:5]] + \
               [f"dshot pin {self.pin}: CRC error in frame {i}" for i, f in enumerate(self.frames) if not f[2]][:5]
