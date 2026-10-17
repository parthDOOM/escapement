"""WS2812(B) LED chain input decoder with datasheet timing checks."""

from __future__ import annotations

from .board import Device


class Ws2812Decoder(Device):
    """Decodes high-pulse widths on `pin` into bits; a low gap >= reset_us latches.

    Datasheet (WS2812B): T0H 0.4 us, T1H 0.8 us (each +/-150 ns), bit period
    1.25 us +/-600 ns, reset >= 50 us (newer parts: >= 280 us).
    """

    def __init__(self, pin: int, tick_ns: float = 40.0, reset_us: float = 50.0):
        self.pin, self.tick = pin, tick_ns
        self.reset_ticks = reset_us * 1000 / tick_ns
        self.frames: list[list[int]] = []     # each frame: list of 24-bit GRB words
        self.violations: list[str] = []
        self._bits: list[int] = []
        self._prev = None          # first sample only records the level (no edge)
        self._t_rise = self._t_fall = None
        self._last_rise = None

    def _ns(self, ticks):
        return ticks * self.tick

    def step(self, t, bus):
        lvl = (bus >> self.pin) & 1
        if self._prev is None:
            self._prev = lvl
            return 0, 0
        if lvl and not self._prev:                      # rising edge
            if self._t_fall is not None and t - self._t_fall >= self.reset_ticks:
                self._latch()
            if self._last_rise is not None and self._bits:
                period = self._ns(t - self._last_rise)
                if not 650 <= period <= 1850:
                    self.violations.append(f"t={t}: bit period {period:.0f} ns")
            self._t_rise = self._last_rise = t
        elif not lvl and self._prev and self._t_rise is not None:   # falling edge
            high = self._ns(t - self._t_rise)
            if 250 <= high <= 550:
                self._bits.append(0)
            elif 650 <= high <= 950:
                self._bits.append(1)
            else:
                self.violations.append(f"t={t}: high time {high:.0f} ns is neither 0 nor 1")
            self._t_fall = t
        self._prev = lvl
        return 0, 0

    def _latch(self):
        if self._bits:
            words = [int("".join(map(str, self._bits[i:i + 24])), 2)
                     for i in range(0, len(self._bits) - len(self._bits) % 24, 24)]
            if len(self._bits) % 24:
                self.violations.append(f"frame of {len(self._bits)} bits is not a multiple of 24")
            self.frames.append(words)
        self._bits = []

    def finish(self):
        self._latch()

    def errors(self):
        return [f"WS2812: {v}" for v in self.violations[:10]]
