"""A chain of Maxim MAX7219 LED display drivers, for testing a driver program (MAX7219/MAX7221
datasheet: Serial-Addressing Modes, Tables 1-8, Timing Characteristics).

Written from the datasheet, not from any firmware. DIN is shifted into a 16-bit register on
each rising edge of CLK whatever LOAD does; DOUT gives the bit shifted out 16.5 clocks later
on the falling edge, so a chain of n devices is a 16n-bit shift register. LOAD's rising
edge latches each device's last 16 bits: D15 first, D11-D8 the register (0 No-Op, 1-8 digits
0-7, 9 Decode Mode, A Intensity, B Scan Limit, C Shutdown, F Display Test), D7-D0 the data.
Power-up: shutdown, Display Test off, Decode Mode 0, Intensity 0, Scan Limit 0, blank.
Checked: tCH and tCL at least 50 ns, tCP 100 ns, DIN set up 25 ns before a rising edge
(tDS), LOAD high at least 50 ns (tCSW) and 50 ns before the next rising edge (tLDCK), and
LOAD rising only after a whole number of 16-bit packets per device (else a device latches
bits of two packets).
"""

from __future__ import annotations

from .board import Device

CODE_B = "0123456789-EHLP "


class Max7219Chain(Device):
    def __init__(self, clk, din, load, tick_ns, n=1):
        self.clk, self.din, self.load, self.tick, self.n = clk, din, load, tick_ns, n
        self.regs = [dict(digits=[0] * 8, decode=0, intensity=0, scan=0, shutdown=0, test=0)
                     for _ in range(n)]
        self.latches: list[list[int]] = []       # the packets of each LOAD, device 0 first
        self.violations: list[str] = []
        self._sr = 0                              # 16n bits; device 0's packet in [15:0]
        self._edges = 0
        self._c = self._d = 0
        self._ld = 1
        self._t_c = self._t_d = self._t_ldr = self._t_rise = None
        self._first = True

    def _ns(self, a, b):
        return (b - a) * self.tick

    def step(self, t, bus):
        c, d, ld = (bus >> self.clk) & 1, (bus >> self.din) & 1, (bus >> self.load) & 1
        if self._first:                           # the levels before the driver starts
            self._first = False
            self._c, self._d, self._ld = c, d, ld
            return 0, 0
        if d != self._d:
            self._t_d = t
        if c != self._c:
            if self._t_c is not None and self._ns(self._t_c, t) < 50:
                self.violations.append(f"t={t}: CLK {'low' if c else 'high'} {self._ns(self._t_c, t):.0f} ns (50)")
            if c:                                 # rising: shift in
                if self._t_rise is not None and self._ns(self._t_rise, t) < 100:
                    self.violations.append(f"t={t}: tCP {self._ns(self._t_rise, t):.0f} ns (100)")
                if self._t_d is not None and self._ns(self._t_d, t) < 25:
                    self.violations.append(f"t={t}: DIN set up {self._ns(self._t_d, t):.0f} ns (tDS 25)")
                if self._t_ldr is not None and self._ns(self._t_ldr, t) < 50:
                    self.violations.append(f"t={t}: LOAD rise to CLK rise {self._ns(self._t_ldr, t):.0f} ns (tLDCK 50)")
                self._sr = (self._sr << 1 | d) & ((1 << 16 * self.n) - 1)
                self._edges += 1
                self._t_rise = t
            self._t_c = t
        if ld != self._ld:
            if ld:                                # rising: latch
                self._latch(t)
                self._t_ldr = t
            else:
                if self._t_ldr is not None and self._ns(self._t_ldr, t) < 50:
                    self.violations.append(f"t={t}: LOAD high {self._ns(self._t_ldr, t):.0f} ns (tCSW 50)")
        self._c, self._d, self._ld = c, d, ld
        return 0, 0

    def _latch(self, t):
        if self._edges % (16 * self.n):
            self.violations.append(f"t={t}: LOAD after {self._edges} clocks (packets of 16 per device)")
        self._edges = 0
        pk = [(self._sr >> 16 * k) & 0xFFFF for k in range(self.n)]
        self.latches.append(pk)
        for k, p in enumerate(pk):
            a, v = (p >> 8) & 0xF, p & 0xFF
            r = self.regs[k]
            if 1 <= a <= 8:
                r["digits"][a - 1] = v
            elif a == 0x9:
                r["decode"] = v
            elif a == 0xA:
                r["intensity"] = v & 0xF
            elif a == 0xB:
                r["scan"] = v & 7
            elif a == 0xC:
                r["shutdown"] = v & 1
            elif a == 0xF:
                r["test"] = v & 1
            elif a != 0:
                self.violations.append(f"t={t}: device {k} register {a:X}h (not in Table 2)")

    def text(self, k=0):
        """What device k shows, digit 7 (left) to digit 0, code B digits as characters, raw
        digits as [xx], dots as '.'; '' when shut down."""
        r = self.regs[k]
        if r["test"]:
            return "8." * 8
        if not r["shutdown"]:
            return ""
        out = []
        for i in range(r["scan"], -1, -1):
            v = r["digits"][i]
            if r["decode"] >> i & 1:
                out.append(CODE_B[v & 0xF] + ("." if v & 0x80 else ""))
            else:
                out.append(f"[{v:02x}]")
        return "".join(out)
