"""PS/2 host model: receives device-to-host frames and checks them.

The DEVICE (the chip) drives CLK and DATA open-drain. A frame is 11 bits, each sampled
by the host on a falling CLK edge: start (0), 8 data bits LSB first, odd parity, stop (1).
Timing checked (IBM PS/2 technical reference): clock 10-16.7 kHz (period 60-100 us),
CLK low and high phases 30-50 us, DATA stable for >= 5 us before each falling edge; and
(Adam Chapweske's PS/2 protocol notes) CLK continuously high for at least 50 us before
the device begins a frame, and a DATA transition at least 5 us after CLK rises and at
most 25 us before CLK falls.

hold: act as a PC that inhibits after every frame, as a real one does (test_real_devices:
53 us after the 11th falling edge, for 250 or 525 us): CLK held low from hold[0] to
hold[1] us after each frame's last falling edge. The host knows its own edges.

inhibits: for a recording of a real host, which holds CLK low to inhibit the device (for
at least 100 us, at any time). A CLK low of 100 us or more is then the host's, not a
clock: it is counted (`inhibits`), its falling edge is no bit (the checks made there are
withdrawn), and a frame it cuts short is dropped (`cut`; the device sends it again). A
host that starts inhibiting as a frame's 11th clock ends can make that last falling edge
itself, or leave a glitch of a microsecond before its own. The strict default, for the
chip, flags any long CLK low.
"""

from __future__ import annotations

from .board import Device


class Ps2Host(Device):
    def __init__(self, clk: int, data: int, tick_ns: float = 40.0, inhibits: bool = False,
                 hold: tuple[float, float] | None = None):
        self.clk, self.data, self.inhibit_ok, self.hold = clk, data, inhibits, hold
        self.inhibits = 0
        self.cut: list[list[int]] = []
        self.us = 1000.0 / tick_ns            # ticks per microsecond
        self.bytes: list[int] = []
        self.violations: list[str] = []
        self._bits: list[int] = []
        self._pclk, self._pdata = 1, 1
        self._t_fall = self._t_rise = None
        self._t_data = -10**9
        self._t_low, self._mark = None, 0
        self._held, self._drove = None, False   # hold: this host's own CLK low

    def _chk(self, t, what, ticks, lo_us, hi_us):
        us = ticks / self.us
        if hi_us is None and us < lo_us - 0.1:
            self.violations.append(f"t={t}: {what} {us:.1f} us, under {lo_us} us")
        elif hi_us is not None and not (lo_us - 0.1 <= us <= hi_us + 0.1):
            self.violations.append(f"t={t}: {what} {us:.1f} us outside {lo_us}-{hi_us} us")

    def step(self, t, bus):
        clk, data = (bus >> self.clk) & 1, (bus >> self.data) & 1
        drive = self._held is not None and self._held[0] <= t < self._held[1]
        if self._held is not None and t >= self._held[1]:
            self._held = None
        own, self._drove = self._drove, drive      # the bus shows last step's drive
        if data != self._pdata:
            if clk and not own and self._t_rise is not None:
                if self._bits:
                    self._chk(t, "CLK rise to DATA", t - self._t_rise, 5, None)
                elif not data:                     # a start bit: the device begins
                    self._chk(t, "CLK high before the start bit", t - self._t_rise, 50, None)
            self._t_data = t
        if clk != self._pclk and own:              # this host's inhibit: no clock
            pass
        elif clk != self._pclk:
            if clk == 0:                                   # falling edge: sample
                self._t_low, self._mark = t, len(self.violations)
                if self._t_rise is not None and self._bits:
                    self._chk(t, "CLK high", t - self._t_rise, 30, 50)
                if self._t_fall is not None and self._bits:
                    self._chk(t, "clock period", t - self._t_fall, 60, 100)
                if (t - self._t_data) / self.us < 5:
                    self.violations.append(f"t={t}: DATA changed {(t - self._t_data) / self.us:.1f} us before the falling edge")
                if self._t_rise is not None and self._t_data > self._t_rise:
                    self._chk(t, "DATA to CLK fall", t - self._t_data, 5, 25)
                self._t_fall = t
                self._bits.append(data)
                if len(self._bits) == 11:
                    self._frame(t)
            else:
                low = t - self._t_fall if self._t_fall is not None else None
                if self.inhibit_ok and self._t_low is not None and (t - self._t_low) / self.us >= 100:
                    self.inhibits += 1                     # the host's, not a clock
                    if self._bits:                         # its falling edge was no bit
                        del self.violations[self._mark:]
                        if self._bits[:-1]:
                            self.cut.append(self._bits[:-1])
                    self._bits, self._t_fall = [], None
                elif low is not None:
                    self._chk(t, "CLK low", low, 30, 50)
                self._t_rise = t
        self._pclk, self._pdata = clk, data
        return (1 << self.clk if drive else 0), 0

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
        if self.hold:
            self._held = (t + self.hold[0] * self.us, t + self.hold[1] * self.us)

    def errors(self):
        return list(self.violations[:5])
