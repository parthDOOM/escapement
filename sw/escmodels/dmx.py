"""DMX512 receiver with the timing checks of ANSI E1.11-2008 (DMX512-A).

Receiver limits (E1.11 Table 6): a break is a low of at least 88 us; the mark after
break at least 8 us; a slot is a start bit, 8 data bits LSB first and two stop bits at
250 kbit/s (4 us per bit). A low longer than a slot but shorter than a break is an
invalid break. Each packet is recorded with its measured break and mark-after-break
times, so a test can check the transmitter's figures (break >= 92 us and MAB >= 12 us
transmitted) as well as the data.
"""

from __future__ import annotations

from .board import Device


class DmxRx(Device):
    def __init__(self, pin: int, clock_hz: float = 25e6):
        self.pin = pin
        self.tick_us = 1e6 / clock_hz
        self.bit = 4.0 / self.tick_us                  # ticks per bit
        self.break_min = 88.0 / self.tick_us
        self.mab_min = 8.0 / self.tick_us
        self.packets: list[dict] = []                  # {break_us, mab_us, slots}
        self.violations: list[str] = []
        self._prev = None
        self._low_since = None                         # start of the current low
        self._rise = None                              # end of the last break
        self._pkt = None                               # packet being received
        self._slot = None                              # tick of the current start bit
        self._acc = 0

    def _finish(self):
        if self._pkt is not None:
            self.packets.append(self._pkt)
        self._pkt = None

    def step(self, t, bus):
        lvl = (bus >> self.pin) & 1
        if self._prev is None:
            self._prev = lvl
            return 0, 0
        if self._prev and not lvl:                     # falling edge
            self._low_since = t
            if self._pkt is not None and self._slot is None:
                if not self._pkt["slots"] and self._pkt["mab_us"] is None:
                    mab = t - self._rise
                    self._pkt["mab_us"] = mab * self.tick_us
                    if mab < self.mab_min:
                        self.violations.append(f"t={t}: mark after break {mab * self.tick_us:.1f} us")
                self._slot, self._acc = t, 0
        elif lvl and not self._prev:                   # rising edge
            low = t - self._low_since if self._low_since is not None else 0
            if low >= self.break_min:
                self._finish()
                self._slot = None
                self._rise = t
                self._pkt = {"break_us": low * self.tick_us, "mab_us": None, "slots": []}
            elif low > 9.5 * self.bit and self._pkt is not None:
                self.violations.append(f"t={t}: invalid break, low {low * self.tick_us:.1f} us")
                self._finish()
                self._slot = None
        if self._slot is not None:                     # sample each bit at its centre
            k = int((t - self._slot) / self.bit)
            centre = self._slot + (k + 0.5) * self.bit
            if centre <= t < centre + 1:
                if k == 0 and lvl:
                    self._slot = None                  # a glitch, not a start bit
                elif 1 <= k <= 8:
                    self._acc |= lvl << (k - 1)
                elif k in (9, 10) and not lvl:
                    self._slot = None                  # a break starts, or a framing error
                elif k == 10:
                    self._pkt["slots"].append(self._acc)
                    self._slot = None
        self._prev = lvl
        return 0, 0

    def errors(self):
        return list(self.violations)
