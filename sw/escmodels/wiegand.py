"""Wiegand reader interface receiver (access control panels).

Written from the limits of SIA AC-01-1996.10 as vendors quote them (the standard is not
freely available): DATA0 and DATA1 idle high; each bit is a low pulse on DATA0 (0) or
DATA1 (1) of 20 to 100 us, pulse starts 200 us to 20 ms apart; a quiet line ends the frame.
The 26-bit format: an even parity bit over bits 2..13, the facility code (8 bits) and card
number (16 bits), an odd parity bit over bits 14..25, MSB first.
"""

from __future__ import annotations

from .board import Device


class WiegandReceiver(Device):
    def __init__(self, d0: int, d1: int, tick_ns: float, end_ms: float = 20.0):
        self.d0, self.d1, self.tick = d0, d1, tick_ns
        self.end = end_ms * 1e6 / tick_ns
        self.frames: list[list[int]] = []
        self.violations: list[str] = []
        self._bits: list[int] = []
        self._prev = 3
        self._fall = None
        self._last = None

    def _us(self, ticks):
        return ticks * self.tick / 1000

    def step(self, t, bus):
        lv = ((bus >> self.d0) & 1) | ((bus >> self.d1) & 1) << 1
        if self._bits and self._last is not None and t - self._last > self.end and lv == 3:
            self.frames.append(self._bits)
            self._bits = []
        if lv != self._prev:
            if lv == 0:
                self.violations.append(f"t={t}: both lines low")
            elif self._prev == 3:                          # a pulse starts
                if self._last is not None and self._bits:
                    gap = self._us(t - self._last)
                    if not 200 <= gap <= 20_000:
                        self.violations.append(f"t={t}: pulse interval {gap:.0f} us")
                self._fall, self._last = t, t
                self._bits.append(0 if lv == 2 else 1)    # DATA0 low: a 0
            elif lv == 3 and self._fall is not None:       # it ends
                w = self._us(t - self._fall)
                if not 20 <= w <= 100:
                    self.violations.append(f"t={t}: pulse width {w:.1f} us")
            self._prev = lv
        return 0, 0

    def finish(self):
        if self._bits:
            self.frames.append(self._bits)
            self._bits = []

    @staticmethod
    def card26(bits):
        """(facility, card) of a 26-bit frame whose parity bits check, else None."""
        if len(bits) != 26 or sum(bits[0:13]) % 2 or sum(bits[13:26]) % 2 != 1:
            return None
        v = int("".join(map(str, bits)), 2)
        return (v >> 17) & 0xFF, (v >> 1) & 0xFFFF
