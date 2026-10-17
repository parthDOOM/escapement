"""PS/2 host model: receives device-to-host frames and checks them.

The DEVICE (the chip) drives CLK and DATA open-drain. A frame is 11 bits, each sampled
by the host on a falling CLK edge: start (0), 8 data bits LSB first, odd parity, stop (1).
Timing checked (IBM PS/2 technical reference): clock 10-16.7 kHz (period 60-100 us),
CLK low and high phases 30-50 us, DATA stable for >= 5 us before each falling edge.
"""

from __future__ import annotations

from .board import Device


class Ps2Host(Device):
    def __init__(self, clk: int, data: int, tick_ns: float = 40.0):
        self.clk, self.data = clk, data
        self.us = 1000.0 / tick_ns            # ticks per microsecond
        self.bytes: list[int] = []
        self.violations: list[str] = []
        self._bits: list[int] = []
        self._pclk, self._pdata = 1, 1
        self._t_fall = self._t_rise = None
        self._t_data = -10**9

    def _chk(self, t, what, ticks, lo_us, hi_us):
        us = ticks / self.us
        if not (lo_us - 0.1 <= us <= hi_us + 0.1):
            self.violations.append(f"t={t}: {what} {us:.1f} us outside {lo_us}-{hi_us} us")

    def step(self, t, bus):
        clk, data = (bus >> self.clk) & 1, (bus >> self.data) & 1
        if data != self._pdata:
            self._t_data = t
        if clk != self._pclk:
            if clk == 0:                                   # falling edge: sample
                if self._t_rise is not None and self._bits:
                    self._chk(t, "CLK high", t - self._t_rise, 30, 50)
                if self._t_fall is not None and self._bits:
                    self._chk(t, "clock period", t - self._t_fall, 60, 100)
                if (t - self._t_data) / self.us < 5:
                    self.violations.append(f"t={t}: DATA changed {(t - self._t_data) / self.us:.1f} us before the falling edge")
                self._t_fall = t
                self._bits.append(data)
                if len(self._bits) == 11:
                    self._frame(t)
            else:
                if self._t_fall is not None:
                    self._chk(t, "CLK low", t - self._t_fall, 30, 50)
                self._t_rise = t
        self._pclk, self._pdata = clk, data
        return 0, 0

    def _frame(self, t):
        b = self._bits
        self._bits = []
        byte = sum(bit << i for i, bit in enumerate(b[1:9]))
        if b[0] != 0:
            self.violations.append(f"t={t}: start bit not 0")
        if (sum(b[1:9]) + b[9]) % 2 != 1:
            self.violations.append(f"t={t}: parity error on 0x{byte:02x}")
        if b[10] != 1:
            self.violations.append(f"t={t}: stop bit not 1")
        self.bytes.append(byte)
        self._t_fall = self._t_rise = None

    def errors(self):
        return list(self.violations[:5])
