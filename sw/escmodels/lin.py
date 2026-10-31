"""LIN (Local Interconnect Network) frame receiver, as a responder sees the bus.

Written from the LIN 2.x frame description (LIN Specification Package 2.2A, as quoted by
the sources cited in protocols docs): a break of at least 13 bit times low (a responder
detects it from 11) and a delimiter of at least one bit high, the sync byte 0x55, the
protected identifier (ID[5:0], P0 = ID0^ID1^ID2^ID4, P1 = not(ID1^ID3^ID4^ID5)), the
data and the checksum: the inverted eight-bit sum with carry over the protected
identifier and the data (enhanced), or the data only (classic, diagnostic IDs 0x3C and
0x3D); every byte 8N1, LSB first.
"""

from __future__ import annotations

from .board import Device


def pid(ident: int) -> int:
    b = [(ident >> k) & 1 for k in range(6)]
    p0 = b[0] ^ b[1] ^ b[2] ^ b[4]
    p1 = 1 - (b[1] ^ b[3] ^ b[4] ^ b[5])
    return ident | p0 << 6 | p1 << 7


def checksum(data, pid_byte=None) -> int:
    s = 0
    for x in ([pid_byte] if pid_byte is not None else []) + list(data):
        s += x
        if s > 255:
            s -= 255
    return ~s & 0xFF


class LinReceiver(Device):
    def __init__(self, pin: int, tick_ns: float, baud: int = 19200):
        self.pin, self.bit = pin, 1e9 / baud / tick_ns          # ticks per bit
        self.levels: list[int] = []
        self.frames: list[dict] = []
        self.violations: list[str] = []

    def step(self, t, bus):
        self.levels.append((bus >> self.pin) & 1)
        return 0, 0

    def finish(self):
        lv, b, t = self.levels, self.bit, 0
        n = len(lv)
        while t < n:
            if lv[t] == 1:
                t += 1
                continue
            s = t
            while t < n and lv[t] == 0:
                t += 1
            if (t - s) < 11 * b:
                continue                               # not a break
            if (t - s) < 13 * b - 1:
                self.violations.append(f"t={s}: break {(t - s) / b:.2f} bits")
            d = t
            while t < n and lv[t] == 1:
                t += 1
            if (t - d) < b - 1:
                self.violations.append(f"t={d}: delimiter {(t - d) / b:.2f} bits")
            got = []
            while True:                                # bytes until 4 bit times of quiet
                h = t
                while t < n and lv[t] == 1 and t - h < 4 * b:
                    t += 1
                if t >= n or lv[t] == 1:
                    break
                at = lambda k: lv[min(n - 1, round(t + k * b))]   # noqa: E731
                if at(0.5) != 0:
                    self.violations.append(f"t={t}: start bit")
                    break
                got.append(sum(at(k + 1.5) << k for k in range(8)))
                if at(9.5) != 1:
                    self.violations.append(f"t={t}: stop bit")
                t = round(t + 9.5 * b)
            if len(got) < 3 or got[0] != 0x55:
                self.violations.append(f"t={s}: frame {got}")
                continue
            p, data, cs = got[1], got[2:-1], got[-1]
            ident = p & 0x3F
            if pid(ident) != p:
                self.violations.append(f"t={s}: PID parity {p:02x}")
            classic = ident in (0x3C, 0x3D)
            if cs != checksum(data, None if classic else p):
                self.violations.append(f"t={s}: checksum {cs:02x}")
            self.frames.append({"id": ident, "data": data, "classic": classic})
