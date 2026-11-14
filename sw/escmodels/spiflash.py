"""A Winbond W25Q80DV serial flash on SPI, for testing a programmer (W25Q80DV datasheet,
Revision C: 7.1 Status Register, 8.1 identification, 8.5 instructions, 9.6 AC
characteristics).

Written from the datasheet, not from any firmware. Mode 0 or 3: the flash samples DI on
SCK's rising edge and shifts DO out on the falling edge, MSB first; /CS low starts an
instruction and its first byte is the instruction code. Implemented:
  9Fh Read JEDEC ID: EFh, 40h, 14h;  05h Read Status Register-1 (S0 BUSY, S1 WEL), repeated
  while /CS stays low;  06h / 04h Write Enable / Disable (8 bits, then /CS high);
  03h Read Data: a 24-bit address, then bytes from there on, wrapping at the end of the array;
  02h Page Program: needs WEL; a 24-bit address and at least one byte; within the 256-byte
  page the address wraps; programmed only if /CS goes high after the eighth bit of a byte
  (bits can only go from 1 to 0); BUSY for tPP;
  20h Sector Erase: needs WEL; the 4 KB sector of a 24-bit address to FFh, only if /CS goes
  high after the 32nd bit; BUSY for tSE.
After a program or erase, WEL clears. While BUSY every instruction but Read Status Register
is ignored by the flash (a violation here: a programmer must wait). /CS must stay high for
tSHSL (50 ns) between instructions.
"""

from __future__ import annotations

from .board import Device

SIZE = 1 << 20                          # 8 Mbit
JEDEC = bytes([0xEF, 0x40, 0x14])


class SpiFlashW25(Device):
    def __init__(self, sck, mosi, miso, cs, tick_ns, t_pp_ms=0.8, t_se_ms=45.0):
        self.sck, self.mosi, self.miso, self.cs = sck, mosi, miso, cs
        self.tick = tick_ns
        self.t_pp, self.t_se = t_pp_ms * 1e6 / tick_ns, t_se_ms * 1e6 / tick_ns
        self.mem: dict[int, int] = {}
        self.wel = False
        self.busy_until = -1
        self.instructions: list[tuple[int, int]] = []   # (code, bits clocked)
        self.violations: list[str] = []
        self._cs = 1
        self._sck = 0
        self._t_cs_high = None
        self._bits = 0
        self._rx = 0
        self._out = []                                   # bytes to shift out
        self._obyte = 0xFF
        self._obit = 0
        self._miso = 1
        self._code = None
        self._addr = 0
        self._data: list[int] = []

    def _busy(self, t):
        return t < self.busy_until

    def read(self, a):
        return self.mem.get(a % SIZE, 0xFF)

    def step(self, t, bus):
        cs, sck, mosi = (bus >> self.cs) & 1, (bus >> self.sck) & 1, (bus >> self.mosi) & 1
        if cs == 0 and self._cs == 1:                   # /CS falls: an instruction begins
            if self._t_cs_high is not None and (t - self._t_cs_high) * self.tick < 50:
                self.violations.append(f"t={t}: /CS high {(t - self._t_cs_high) * self.tick:.0f} ns (tSHSL 50)")
            self._bits, self._rx, self._code, self._data = 0, 0, None, []
            self._out, self._obit = [], 0
            if sck:
                self.violations.append(f"t={t}: /CS fell with SCK high (mode 0 wants SCK low)")
        elif cs == 1 and self._cs == 0:                 # /CS rises: an instruction ends
            self._t_cs_high = t
            self._end(t)
        self._cs = cs
        if cs == 0:
            if sck and not self._sck:                   # rising: in
                self._rx = self._rx << 1 | mosi
                self._bits += 1
                if self._bits % 8 == 0:
                    self._byte(t, self._rx & 0xFF)
            elif not sck and self._sck:                 # falling: out
                if self._obit == 0 and self._out:
                    self._obyte, self._obit = self._out.pop(0), 8
                if self._obit:
                    self._obit -= 1
                    self._miso = (self._obyte >> self._obit) & 1
                else:
                    self._miso = 1
        else:
            self._miso = 1
        self._sck = sck
        return (0 if self._miso else 1 << self.miso), 0

    def _byte(self, t, b):
        n = self._bits // 8
        if n == 1:
            self._code = b
            self.instructions.append((b, 0))
            if self._busy(t) and b != 0x05:
                self.violations.append(f"t={t}: instruction {b:02X}h while BUSY")
            if b == 0x9F:
                self._out = list(JEDEC)
            elif b == 0x05:
                self._out = [self._sr(t)] * 64
            return
        c = self._code
        if c in (0x03, 0x02, 0x20) and n <= 4:
            self._addr = (self._addr << 8 | b) & 0xFFFFFF if n > 2 else b
            if c == 0x03 and n == 4:
                self._out = [self.read(self._addr + k) for k in range(512)]
            return
        if c == 0x02:
            self._data.append(b)
        elif c == 0x05:
            self._out.append(self._sr(t))

    def _sr(self, t):
        return (1 if self._busy(t) else 0) | (2 if self.wel else 0)

    def _end(self, t):
        c, bits = self._code, self._bits
        if c is None:
            return
        self.instructions[-1] = (c, bits)
        if self._busy(t) and c != 0x05:
            return                                      # ignored (and reported above)
        if c == 0x06 and bits == 8:
            self.wel = True
        elif c == 0x04 and bits == 8:
            self.wel = False
        elif c == 0x02:
            if not self.wel:
                self.violations.append(f"t={t}: Page Program with WEL 0 (ignored)")
            elif bits % 8 or bits < 40:
                self.violations.append(f"t={t}: Page Program ended after {bits} bits (not executed)")
            else:
                page, off = self._addr & ~0xFF, self._addr & 0xFF
                for k, v in enumerate(self._data):
                    a = page | (off + k) & 0xFF
                    self.mem[a] = self.read(a) & v
                self.busy_until = t + self.t_pp
                self.wel = False
        elif c == 0x20:
            if not self.wel:
                self.violations.append(f"t={t}: Sector Erase with WEL 0 (ignored)")
            elif bits != 32:
                self.violations.append(f"t={t}: Sector Erase ended after {bits} bits (not executed)")
            else:
                base = self._addr & ~0xFFF
                for a in [a for a in self.mem if base <= a < base + 0x1000]:
                    del self.mem[a]
                self.busy_until = t + self.t_se
                self.wel = False
