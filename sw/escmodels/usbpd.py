"""USB Power Delivery receiver for the CC wire, at logic level (USB PD 3.x physical layer).

Written from the specification's description of the physical layer, not from any firmware:
Biphase Mark Coding at 270 to 330 kbit/s (a transition at every bit boundary, one more in
the middle of a 1); a preamble of 64 alternating bits starting with 0; the SOP ordered set
Sync-1 Sync-1 Sync-1 Sync-2; the header and data objects as 4b5b symbols, each byte least
significant first and each byte's low nibble first, every symbol least significant bit first;
the CRC-32 of IEEE 802.3 over the header and data objects (computed here with zlib, the
reference implementation); and EOP. The 4b5b table is Table 5.1 of the specification.
"""

from __future__ import annotations

import struct
import zlib

from .board import Device

DATA_5B = {0b11110: 0x0, 0b01001: 0x1, 0b10100: 0x2, 0b10101: 0x3, 0b01010: 0x4,
           0b01011: 0x5, 0b01110: 0x6, 0b01111: 0x7, 0b10010: 0x8, 0b10011: 0x9,
           0b10110: 0xA, 0b10111: 0xB, 0b11010: 0xC, 0b11011: 0xD, 0b11100: 0xE,
           0b11101: 0xF}
SYNC1, SYNC2, EOP = 0b11000, 0b10001, 0b01101
SYNC3, RST1, RST2 = 0b00110, 0b00111, 0b11001
ORDERED_SETS = {(SYNC1, SYNC1, SYNC1, SYNC2): "SOP", (SYNC1, SYNC1, SYNC3, SYNC3): "SOP'",
                (SYNC1, SYNC3, SYNC1, SYNC3): "SOP''", (RST1, RST1, RST1, RST2): "Hard Reset",
                (RST1, SYNC1, RST1, SYNC3): "Cable Reset"}


class UsbPdSink(Device):
    def __init__(self, pin: int, tick_ns: float, real_line: bool = False):
        """real_line: for a logic analyser recording of a real CC wire, tell half bits from
        whole bits at 3/4 of a nominal UI (2.5 us) instead of the narrow windows that judge
        the chip's own output, since sampling moves every edge by up to one sample; lock on
        wherever the burst starts; accept a preamble of 32 or more alternating bits ending in
        a 1 (a receiver locks on during it; the first bit can merge into the idle line); and
        count the ordered sets other than SOP (`other`) and bursts of under 20 edges, which
        are the CC level changing, not messages (`line_events`). The bit rate is still
        checked over each message."""
        self.pin, self.tick, self.real_line = pin, tick_ns, real_line
        self.other: dict[str, int] = {}             # real_line: SOP', SOP'', resets seen
        self.line_events = 0                         # real_line: bursts of under 20 edges
        self.levels: list[int] = []
        self.messages: list[dict] = []
        self.violations: list[str] = []

    def step(self, t, bus):
        self.levels.append((bus >> self.pin) & 1)
        return 0, 0

    def _us(self, ticks):
        return ticks * self.tick / 1000.0

    def finish(self):
        lv = self.levels
        edges = [t for t in range(1, len(lv)) if lv[t] != lv[t - 1]]
        # messages are separated by idle time far longer than a bit (> 20 us)
        bursts, cur = [], []
        for e in edges:
            if cur and self._us(e - cur[-1]) > 20:
                bursts.append(cur)
                cur = []
            cur.append(e)
        if cur:
            bursts.append(cur)
        for b in bursts:
            self._message(b)

    def _bits(self, ivs):
        if self.real_line:
            half = lambda x: 0.8 <= x < 2.5
            whole = lambda x: 2.5 <= x < 5.0
        else:
            half = lambda x: 1.4 <= x <= 1.95
            whole = lambda x: 1e3 / 330 <= x <= 1e3 / 270
        bits, i, uis = [], 0, []
        while i < len(ivs):
            if half(ivs[i]) and i + 1 < len(ivs) and half(ivs[i + 1]):
                bits.append(1)
                uis.append(ivs[i] + ivs[i + 1])
                i += 2
            elif whole(ivs[i]):
                bits.append(0)
                uis.append(ivs[i])
                i += 1
            else:
                break                                   # the end: hold low, then idle
        if i == len(ivs) and ivs:
            # the last edge starts a bit with no edge in its middle or at its end (the line
            # holds after it): a 0. (EOP ends with a 0; a frame that ended in a 1 would show
            # its middle edge.)
            bits.append(0)
        return bits, uis

    @staticmethod
    def _sets(bits):
        """(index, name) of the first ordered set in bits (SOP, or with real_line the others)."""
        def sym(k):
            return sum(bits[k + j] << j for j in range(5)) if k + 5 <= len(bits) else None
        for k in range(len(bits) - 19):
            four = (sym(k), sym(k + 5), sym(k + 10), sym(k + 15))
            if four in ORDERED_SETS:
                return k, ORDERED_SETS[four]
        return None, None

    def _message(self, edges):
        if self.real_line and len(edges) < 20:          # a CC level change, not a message
            self.line_events += 1
            return
        ivs = [self._us(b - a) for a, b in zip(edges, edges[1:])]
        # real_line: a receiver locks on wherever the recording's burst starts (an edge of
        # the driver turning on, or the middle of a bit): try the first edges as the start
        bits, uis, start, kind = [], [], None, None
        for off in (range(min(6, len(ivs))) if self.real_line else (0,)):
            bits, uis = self._bits(ivs[off:])
            start, kind = self._sets(bits)
            if start is not None:
                break
        if uis:
            rate = 1e3 / (sum(uis) / len(uis))
            if not 270 <= rate <= 330:
                self.violations.append(f"bit rate {rate:.1f} kbit/s")

        def sym(k):
            return sum(bits[k + j] << j for j in range(5)) if k + 5 <= len(bits) else None

        if start is None or (kind != "SOP" and not self.real_line):
            self.violations.append(f"no SOP in {len(bits)} bits")
            return
        if kind != "SOP":                               # SOP', SOP'', resets: counted
            self.other[kind] = self.other.get(kind, 0) + 1
            if kind in ("Hard Reset", "Cable Reset"):
                return
        pre = bits[:start]
        if self.real_line:
            if len(pre) < 32 or pre[-1] != 1 or any(a == b for a, b in zip(pre, pre[1:])):
                self.violations.append(f"preamble of {len(pre)} bits: {pre[:8]}...")
        elif pre != [k & 1 for k in range(64)]:
            self.violations.append(f"preamble of {len(pre)} bits: {pre[:8]}...")
        k = start + 20

        def value(nibbles):
            nonlocal k
            v = 0
            for n in range(nibbles):
                s = sym(k)
                if s not in DATA_5B:
                    raise ValueError(f"symbol {s} at bit {k}")
                v |= DATA_5B[s] << (4 * n)
                k += 5
            return v

        try:
            header = value(4)
            n = (header >> 12) & 7
            data = [value(8) for _ in range(n)]
            crc = value(8)
        except ValueError as e:
            self.violations.append(str(e))
            return
        eop = sym(k) == EOP
        want = zlib.crc32(struct.pack("<H" + "I" * n, header, *data))
        self.messages.append({"header": header, "data": data, "crc": crc, "crc_ok": crc == want,
                              "eop": eop, "eop_end": k + 5, "bits": len(bits)})
        if not eop:
            self.violations.append("no EOP")
        if crc != want:
            self.violations.append(f"CRC {crc:08x}, want {want:08x}")
