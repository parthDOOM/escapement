"""An SAE J1850 VPW receiver: a node on the bus, written from the standard's timing (as
the Motorola/Freescale and TI VPW application notes give it), not from any firmware.

The bus is passive (low) at rest and active (high) when driven. Every symbol is the time
between two edges. A receiver classifies it by its length: under 96 us short, 96 to
163 us long, and an active 163 to 239 us symbol is the start of frame (SOF). After the
SOF each symbol is a bit: active short or passive long is 1, active long or passive short
is 0, MSB first. A passive stretch of 163 us or more ends the data (EOD), one of 239 us
or more the frame (EOF). The last byte is a CRC-8 (x^8 + x^4 + x^3 + x^2 + 1, start 0xFF,
inverted) over the others, computed here bit by bit.

On top of the receiver's thresholds the model holds the transmitter to its nominal
lengths: 64, 128 and 200 us within 5 %, and the passive bus after a frame at least
280 us before the next SOF. Frames go to `frames` as (bytes without the CRC, CRC good,
tick of the SOF); everything wrong goes to `violations`.
"""

from __future__ import annotations

from .board import Device


def crc8_j1850(data: bytes) -> int:
    c = 0xFF
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ (0x1D if c & 0x80 else 0)) & 0xFF
    return c ^ 0xFF


class VpwReceiver(Device):
    def __init__(self, pin: int, tick_ns: float):
        self.pin, self.tick = pin, tick_ns
        self.frames: list[tuple[bytes, bool, int]] = []
        self.violations: list[str] = []
        self._lv = 0
        self._edge: int | None = None        # the tick of the last edge
        self._sof: int | None = None         # in a frame: its SOF's tick
        self._bits: list[int] = []
        self._eof_at: int | None = None      # the tick the last frame's EOF began

    def _v(self, msg: str) -> None:
        if len(self.violations) < 50:
            self.violations.append(msg)

    def _near(self, us: float, nominal: float) -> bool:
        return abs(us - nominal) <= 0.05 * nominal

    def step(self, t, bus):
        lv = (bus >> self.pin) & 1
        if t < 0:
            return 0, 0
        if self._sof is not None and self._edge is not None and not self._lv:
            if (t - self._edge) * self.tick / 1000 >= 239:
                self._end(t)
        if lv != self._lv:
            if self._edge is not None:
                self._symbol(self._lv, (t - self._edge) * self.tick / 1000, t)
            self._edge, self._lv = t, lv
        return 0, 0

    def _symbol(self, level: int, us: float, t: int) -> None:
        if self._sof is None:
            if level and 163 <= us < 239:
                if not self._near(us, 200):
                    self._v(f"t={t}: SOF {us:.1f} us")
                if self._eof_at is not None and (self._edge - self._eof_at) * self.tick / 1000 < 280 - 239:
                    self._v(f"t={t}: SOF less than 280 us after the last frame")
                self._sof, self._bits = self._edge, []
            return
        if us < 96:
            bit, nominal = (1 if level else 0), 64
        elif us < 163:
            bit, nominal = (0 if level else 1), 128
        else:
            self._v(f"t={t}: a {'n active' if level else ' passive'} symbol of {us:.1f} us in a frame")
            self._sof = None
            return
        if not self._near(us, nominal):
            self._v(f"t={t}: symbol {us:.1f} us, not {nominal}")
        self._bits.append(bit)

    def _end(self, t: int) -> None:
        bits, sof = self._bits, self._sof
        self._sof, self._bits = None, []
        self._eof_at = t
        if len(bits) % 8 or len(bits) < 16:
            self._v(f"t={t}: a frame of {len(bits)} bits")
            return
        data = bytes(sum(b << (7 - k) for k, b in enumerate(bits[i:i + 8])) for i in range(0, len(bits), 8))
        self.frames.append((data[:-1], crc8_j1850(data[:-1]) == data[-1], sof))
