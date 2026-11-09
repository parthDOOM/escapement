"""Remote-control and pulse-coded receivers: Sony SIRC, Philips RC5, RC-6 and JVC from the
carrier on a pin, EV1527 433 MHz codes from an OOK data pin, NMRA DCC packets from a track signal.

SIRC and RC5:
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


class Rc6Receiver(_Bursts):
    """Philips RC-6 mode 0 (SB-Projects rc6.php): 36 kHz; unit time t = 444 us; a 6t burst
    and a 2t space, the start bit (1), three mode bits, the trailer bit (the toggle) twice as
    wide, then 8 control and 8 information bits MSB first. Manchester with a 1 a burst then a
    space. The line is rebuilt as levels in units of t from the start bit on (each burst and
    space must be a whole 1 to 3 units within +/-25%) and read two units a bit, four for the
    trailer. A burst more than 4.5t after the last one starts a new frame."""

    T_US = 444.44

    def frames(self):
        out, b, t = [], self.bursts, self.T_US
        groups, cur = [], []
        for bu in b:
            if cur and self.us(bu[0] - cur[-1][1]) > 4.5 * t:
                groups.append(cur)
                cur = []
            cur.append(bu)
        if cur:
            groups.append(cur)
        for g in groups:
            s0, e0, per = g[0]
            if len(g) < 2 or not _near(self.us(e0 - s0), 6 * t) or not _near(self.us(g[1][0] - e0), 2 * t):
                continue
            levels, ok = [], True
            for k, (s, e, _) in enumerate(g[1:], start=1):
                if k > 1:
                    space = self.us(s - g[k - 1][1]) / t
                    n = round(space)
                    if n not in (1, 2, 3) or not _near(space, n):
                        ok = False
                        break
                    levels += [0] * n
                mark = self.us(e - s) / t
                n = round(mark)
                if n not in (1, 2, 3) or not _near(mark, n):
                    ok = False
                    break
                levels += [1] * n
            need = 2 + 6 + 4 + 32
            if not ok or len(levels) > need:
                continue
            levels += [0] * (need - len(levels))   # a frame ending in a 1 ends with a space

            def bit(seg):
                h = len(seg) // 2
                a, c = seg[:h], seg[h:]
                if a == [1] * h and c == [0] * h:
                    return 1
                if a == [0] * h and c == [1] * h:
                    return 0
                return None

            start = bit(levels[0:2])
            mode = [bit(levels[2 + 2 * i:4 + 2 * i]) for i in range(3)]
            toggle = bit(levels[8:12])
            data = [bit(levels[12 + 2 * i:14 + 2 * i]) for i in range(16)]
            if start != 1 or None in mode or toggle is None or None in data:
                continue
            v = int("".join(map(str, data)), 2)
            out.append({"mode": mode[0] << 2 | mode[1] << 1 | mode[2], "toggle": toggle,
                        "address": v >> 8, "command": v & 0xFF,
                        "carrier_khz": 1e6 / (per * self.tick) if per else 0})
        return out


class JvcReceiver(_Bursts):
    """JVC: 38 kHz; an 8.4 ms burst and a 4.2 ms space, then 16 bits LSB first (address,
    command), each a 526 us burst whose distance to the next burst is 1.05 ms (0) or
    2.10 ms (1); the last bit is closed by a burst (SB-Projects jvc.php, +/-25% here)."""

    def frames(self):
        out, b, i = [], self.bursts, 0
        while i < len(b):
            s, e, per = b[i]
            if not (_near(self.us(e - s), 8400) and i + 17 < len(b)
                    and _near(self.us(b[i + 1][0] - e), 4200)):
                i += 1
                continue
            bits = []
            for j in range(i + 1, i + 17):
                if not _near(self.us(b[j][1] - b[j][0]), 526):
                    break
                d = self.us(b[j + 1][0] - b[j][0])
                bits.append(0 if _near(d, 1050) else 1 if _near(d, 2100) else None)
            if len(bits) == 16 and None not in bits and _near(self.us(b[i + 17][1] - b[i + 17][0]), 526):
                v = sum(bit << k for k, bit in enumerate(bits))
                out.append({"address": v & 0xFF, "command": v >> 8,
                            "carrier_khz": 1e6 / (per * self.tick) if per else 0})
                i += 18
            else:
                i += 1
        return out


class Ev1527Receiver(Device):
    """EV1527 / rc-switch protocol 1 on an OOK data pin: a sync (1 high, 31 low) gives T,
    then 24 bits MSB first, 0 = 1T high + 3T low, 1 = 3T high + 1T low (+/-30%)."""

    def __init__(self, pin: int, tick_ns: float):
        self.pin, self.tick = pin, tick_ns
        self.levels: list[tuple[int, int]] = []        # (level, duration in ticks)
        self._prev, self._since = 0, 0

    def step(self, t, bus):
        lv = (bus >> self.pin) & 1
        if lv != self._prev:
            self.levels.append((self._prev, t - self._since))
            self._prev, self._since = lv, t
        return 0, 0

    def codes(self):
        out, L = [], self.levels
        for i in range(len(L) - 1):
            (l0, d0), (l1, d1) = L[i], L[i + 1]
            if not (l0 == 1 and l1 == 0 and 25 <= d1 / max(d0, 1) <= 37):
                continue
            T, bits, j = d0, [], i + 2
            while j + 1 < len(L) and len(bits) < 24:
                (h, dh), (lo, dl) = L[j], L[j + 1]
                if h != 1 or lo != 0:
                    break
                r = (round(dh / T), round(dl / T))
                bits.append(0 if r == (1, 3) else 1 if r == (3, 1) else None)
                j += 2
            if len(bits) == 24 and None not in bits:
                out.append(int("".join(map(str, bits)), 2))
        return out


class DccDecoder(Device):
    """NMRA DCC from a track (or booster input) signal: half-periods of 52-64 us are a 1,
    95-9900 us a 0 (S-9.1); both halves of a bit must be the same kind. Packets (S-9.2): a
    preamble of at least 10 ones, a 0, then bytes each followed by a 0 or by the end bit 1;
    the last byte is the XOR of the others."""

    def __init__(self, pin: int, tick_ns: float):
        self.pin, self.tick = pin, tick_ns
        self.halves: list[str] = []
        self.violations: list[str] = []
        self._prev, self._since = 0, None

    def step(self, t, bus):
        lv = (bus >> self.pin) & 1
        if lv != self._prev:
            if self._since is not None:
                us = (t - self._since) * self.tick / 1000
                k = "1" if 52 <= us <= 64 else "0" if 95 <= us <= 9900 else "?"
                if k == "?":
                    self.violations.append(f"t={t}: half-period {us:.1f} us")
                self.halves.append(k)
            self._prev, self._since = lv, t
        return 0, 0

    def packets(self):
        bits, h = [], self.halves
        i = 0
        while i + 1 < len(h):                     # pair halves into bits (resync on a mismatch)
            if h[i] == h[i + 1] and h[i] != "?":
                bits.append(int(h[i]))
                i += 2
            else:
                i += 1
        out, i = [], 0
        while i < len(bits):
            run = 0
            while i < len(bits) and bits[i] == 1:
                run += 1
                i += 1
            if run < 10 or i >= len(bits):
                i += 1
                continue
            i += 1                                # the packet start bit 0
            data = []
            while i + 8 < len(bits):
                byte = int("".join(map(str, bits[i:i + 8])), 2)
                data.append(byte)
                end = bits[i + 8]
                i += 9
                if end == 1:
                    break
            x = 0
            for b in data[:-1]:
                x ^= b
            out.append({"bytes": data, "check_ok": len(data) >= 2 and x == data[-1]})
        return out
