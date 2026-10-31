"""Infrared remote receivers: Sony SIRC and Philips RC5, from the carrier on a pin.

Written from the protocol descriptions (SB-Projects: www.sbprojects.net/knowledge/ir/
sirc.php and rc5.php), not from any firmware: the pin's carrier pulses are grouped into
bursts (a gap longer than three carrier periods ends a burst), the carrier period of
each burst is measured, and frames are decoded from the burst and space durations in
microseconds with a +/-25% tolerance.
"""

from __future__ import annotations

from .board import Device


class _Bursts(Device):
    """Collects (start, end, carrier period) of the carrier bursts on `pin`, in ticks."""

    def __init__(self, pin: int, tick_ns: float):
        self.pin, self.tick = pin, tick_ns
        self.bursts: list[tuple[int, int, float]] = []
        self._prev = 0
        self._rises: list[int] = []
        self._last_fall = None

    def step(self, t, bus):
        lv = (bus >> self.pin) & 1
        if lv and not self._prev:
            if self._rises and t - self._rises[-1] > 3 * self._period():
                self._close()
            self._rises.append(t)
        if not lv and self._prev:
            self._last_fall = t
        self._prev = lv
        return 0, 0

    def _period(self):
        if len(self._rises) >= 2:
            return (self._rises[-1] - self._rises[0]) / (len(self._rises) - 1)
        return 10 ** 9

    def _close(self):
        if self._rises:
            per = self._period() if len(self._rises) >= 2 else 0.0
            end = self._rises[-1] + max(1, int(round(per)) if per else 1)
            self.bursts.append((self._rises[0], end, per))
        self._rises = []

    def finish(self):
        self._close()

    def us(self, ticks):
        return ticks * self.tick / 1000.0


def _near(x, want, tol=0.25):
    return abs(x - want) <= tol * want


class SircReceiver(_Bursts):
    """Sony SIRC, 12-bit: 40 kHz; a 2.4 ms start burst, then per bit a 0.6 ms space and a
    1.2 ms (1) or 0.6 ms (0) burst; 7 command bits then 5 address bits, LSB first."""

    def frames(self):
        out, b = [], self.bursts
        i = 0
        while i < len(b):
            s, e, per = b[i]
            if not _near(self.us(e - s), 2400):
                i += 1
                continue
            bits, j, ok = [], i + 1, True
            while j < len(b) and len(bits) < 12:
                space = self.us(b[j][0] - b[j - 1][1])
                w = self.us(b[j][1] - b[j][0])
                if not _near(space, 600):
                    ok = False
                    break
                bits.append(1 if _near(w, 1200) else 0 if _near(w, 600) else None)
                j += 1
            if ok and len(bits) == 12 and None not in bits:
                v = sum(bit << k for k, bit in enumerate(bits))
                out.append({"command": v & 0x7F, "address": v >> 7,
                            "carrier_khz": 1e6 / (per * self.tick) if per else 0})
            i = j
        return out


class Rc5Receiver(_Bursts):
    """Philips RC5: 36 kHz; 14 Manchester bits of 1.778 ms, MSB first (S1, S2/field,
    toggle, 5 address bits, 6 command bits); a 1 is a space then a burst, each 889 us."""

    def frames(self):
        out = []
        half = 889.0
        # the line as half-bit levels: burst (1) or space (0), from the first burst
        if not self.bursts:
            return out
        groups, cur = [], [self.bursts[0]]
        for bu in self.bursts[1:]:
            if self.us(bu[0] - cur[-1][1]) > 4 * half:
                groups.append(cur)
                cur = [bu]
            else:
                cur.append(bu)
        groups.append(cur)
        for g in groups:
            levels = []
            t = g[0][0]
            for k, (s, e, per) in enumerate(g):
                if k:
                    n = round(self.us(s - t) / half)
                    levels += [0] * n
                n = round(self.us(e - s) / half)
                if n not in (1, 2):
                    break
                levels += [1] * n
                t = e
            levels = [0] + levels                 # the first bit (S1 = 1) starts with a space
            if len(levels) % 2:
                levels.append(0)
            bits = []
            for k in range(0, len(levels), 2):
                a, b2 = levels[k], levels[k + 1]
                bits.append(1 if (a, b2) == (0, 1) else 0 if (a, b2) == (1, 0) else None)
            if len(bits) >= 14 and None not in bits[:14]:
                v = int("".join(map(str, bits[:14])), 2)
                out.append({"start": v >> 12, "toggle": (v >> 11) & 1,
                            "address": (v >> 6) & 0x1F, "command": v & 0x3F})
        return out
