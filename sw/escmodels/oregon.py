"""An Oregon Scientific weather station's receiver for version 2.1 sensors, written from the
"Oregon Scientific RF Protocol Description" (versions 1.0, 2.1 and 3.0, June 2011), its
"Decoding Using Time Intervals" method, not from any firmware or decoder.

The pin is the RF state (1 = carrier on). Each interval between two RF transitions is
classified, by the document's averaged thresholds for that RF state, as short (half a
clock period of 1/1024 s) or long (a whole one); anything outside them ends the message.
A half-time counter starts at the message's first transition (the middle of its first
bit) and advances by 1 for a short interval and 2 for a long one; at even half-times the
transition is mid-bit and the bit is the RF state before it. Version 2.1 sends each data
bit as its inverse, then itself: every pair must be complementary, and the second is kept.
The data bits then hold 16 ones (the preamble), the sync nibble A, and the payload's
nibbles, least significant bit first.

On top of that the model holds the transmitter to the clock: every interval within 2 % of
its nominal 488.28 or 976.56 us. Messages go to `messages` as (nibbles of the payload,
checksum good, first tick, last tick); everything wrong goes to `violations`. `readings()`
turns THGR122NX messages (sensor ID 1D20, as transmitted) into fields.
"""

from __future__ import annotations

from .board import Device

HALF_US = 1e6 / 2048
# (short min, short max = long min, long max) in us, by RF state: the document's table
THRESH = {0: (400, 850, 1400), 1: (200, 615, 1100)}


class OregonV21Receiver(Device):
    def __init__(self, pin: int, tick_ns: float):
        self.pin, self.tick = pin, tick_ns
        self.messages: list[tuple[list[int], bool, int, int]] = []
        self.violations: list[str] = []
        self._lv = 0
        self._edge: int | None = None
        self._start: int | None = None
        self._ht = 0
        self._bits: list[int] = []

    def _v(self, msg: str) -> None:
        if len(self.violations) < 50:
            self.violations.append(msg)

    def step(self, t, bus):
        if t < 0:
            return 0, 0
        lv = (bus >> self.pin) & 1
        if self._start is not None and lv == self._lv and (t - self._edge) * self.tick / 1000 > 1400:
            self._end(t)
        if lv != self._lv:
            if self._start is None:
                self._start, self._ht, self._bits = t, 0, [self._lv]   # mid-bit: the state before
            else:
                self._interval(self._lv, (t - self._edge) * self.tick / 1000, t)
            self._edge, self._lv = t, lv
        return 0, 0

    def _interval(self, state: int, us: float, t: int) -> None:
        smin, mid, lmax = THRESH[state]
        if smin <= us < mid:
            n, nominal = 1, HALF_US
        elif mid <= us <= lmax:
            n, nominal = 2, 2 * HALF_US
        else:
            self._v(f"t={t}: an interval of {us:.0f} us with the RF {'on' if state else 'off'}")
            self._end(t)
            return
        if abs(us - nominal) > 0.02 * nominal:
            self._v(f"t={t}: an interval of {us:.1f} us, not {nominal:.1f}")
        self._ht += n
        if self._ht % 2 == 0:
            self._bits.append(state)

    def _end(self, t: int) -> None:
        bits, start = self._bits, self._start
        self._start, self._bits = None, []
        if len(bits) < 8:
            return
        if len(bits) % 2:
            bits = bits[:-1]
        pairs = [(bits[i], bits[i + 1]) for i in range(0, len(bits), 2)]
        if any(a == b for a, b in pairs):
            self._v(f"t={t}: a bit pair that is not an inverse and its bit")
            return
        data = [b for _, b in pairs]
        if data[:16] != [1] * 16:
            self._v(f"t={t}: preamble {data[:16]}")
            return
        nib = [sum(data[16 + 4 * k + j] << j for j in range(4)) for k in range((len(data) - 16) // 4)]
        if not nib or nib[0] != 0xA:
            self._v(f"t={t}: sync {nib[:1]}")
            return
        pay = nib[1:]
        ok = len(pay) >= 17 and (sum(pay[:15]) & 0xFF) == (pay[15] | pay[16] << 4)
        self.messages.append((pay, ok, start, self._edge))

    def readings(self):
        """(channel, rolling code, battery low, temperature in tenths, humidity) of each
        THGR122NX message with a good checksum."""
        out = []
        for p, ok, _, _ in self.messages:
            if not ok or p[:4] != [0x1, 0xD, 0x2, 0x0]:
                continue
            ch = {1: 1, 2: 2, 4: 3}.get(p[4])
            t = p[8] + 10 * p[9] + 100 * p[10]
            out.append((ch, p[5] | p[6] << 4, bool(p[7] & 4), -t if p[11] else t, p[12] + 10 * p[13]))
        return out
