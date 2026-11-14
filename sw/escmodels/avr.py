"""An ATmega328P in serial programming mode, for testing a programmer (ATmega328P datasheet,
27.8 Serial Downloading, Table 27-17).

Written from the datasheet, not from any firmware. RESET low enables the interface; data is
clocked in on SCK's rising edge and out on its falling edge; every instruction is 4 bytes,
and while one comes in, the byte sent back is the previous one received (so the second byte
of Programming Enable, 0x53, echoes during the third), or for a read instruction the data
in the fourth. Out of programming mode, the 0x53 echoes only when the device's own frame
began with 0xAC ("when in sync", step 3): a device out of step does not echo it. Programming Enable (0xAC 0x53) works only 20 ms or more after RESET fell. The
flash is loaded a word at a time into the page buffer (the low byte, then the high byte, at
the 6 LSBs of the address) and written by Write Program Memory Page; the device is then
busy for tWD_FLASH (4.5 ms), which Poll RDY/BSY reports in bit 0, and any other instruction
before it ends is a violation. SCK high and low must each last more than 2 CPU cycles.
"""

from __future__ import annotations

from .board import Device

SIGNATURE = (0x1E, 0x95, 0x0F)
FUSES = {(0x50, 0x00): 0x62, (0x58, 0x08): 0xD9, (0x50, 0x08): 0xFF, (0x58, 0x00): 0xFF}


class AvrIspTarget(Device):
    def __init__(self, sck, mosi, miso, rst, tick_ns, f_cpu_khz=1000, desync=0, busy_ms=4.5):
        self.sck, self.mosi, self.miso, self.rst = sck, mosi, miso, rst
        self.tick, self.cpu_ns = tick_ns, 1e6 / f_cpu_khz
        self.desync, self.busy_ticks = desync, busy_ms * 1e6 / tick_ns
        self.flash = {}
        self.buffer = {}
        self.enabled = False
        self.enables = 0                      # Programming Enable frames seen
        self.instructions: list[bytes] = []
        self.violations: list[str] = []
        self._p = 0
        self._rst = 1
        self._t_rst = None
        self._bits = 0
        self._rx = 0
        self._frame: list[int] = []
        self._out = 0xFF                     # byte being shifted out
        self._obit = 7
        self._miso = 1
        self._busy_until = -1
        self._t_edge = None
        self._skip = 0

    def _phase_ok(self, t):
        if self._t_edge is not None and (t - self._t_edge) * self.tick <= 2 * self.cpu_ns:
            self.violations.append(f"t={t}: SCK phase {(t - self._t_edge) * self.tick / 1000:.2f} us")
        self._t_edge = t

    def _instruction(self, t, f):
        self.instructions.append(bytes(f))
        if f[0] == 0xAC and f[1] == 0x53:
            self.enables += 1
            if (t - self._t_rst) * self.tick >= 20e6:
                self.enabled = True
            return None
        if not self.enabled:
            return None
        if t < self._busy_until and f[0] != 0xF0:
            self.violations.append(f"t={t}: instruction {bytes(f).hex()} while busy")
        a = f[1] << 8 | f[2]
        if f[0] in (0x40, 0x48):
            w = self.buffer.get(f[2] & 0x3F, 0xFFFF)
            self.buffer[f[2] & 0x3F] = (w & 0xFF00 | f[3]) if f[0] == 0x40 else (w & 0x00FF | f[3] << 8)
        elif f[0] == 0x4C:
            page = a & ~0x3F
            for k, w in self.buffer.items():
                self.flash[page | k] = w
            self.buffer = {}
            self._busy_until = t + self.busy_ticks
        elif f[0] == 0xAC and f[1] == 0x80:
            self.flash = {}
            self._busy_until = t + 2 * self.busy_ticks
        return None

    def _data_out(self, t, f3):
        """The fourth byte out, for an instruction whose first three bytes are f3."""
        if not self.enabled:
            return f3[2]
        op = f3[0]
        if op == 0x30:
            return SIGNATURE[f3[2] & 3]
        if (op, f3[1]) in FUSES:
            return FUSES[(op, f3[1])]
        if op in (0x20, 0x28):
            w = self.flash.get(f3[1] << 8 | f3[2], 0xFFFF)
            return w & 0xFF if op == 0x20 else w >> 8
        if op == 0xF0:
            return 1 if t < self._busy_until else 0
        return f3[2]

    def step(self, t, bus):
        sck, mosi, rst = (bus >> self.sck) & 1, (bus >> self.mosi) & 1, (bus >> self.rst) & 1
        if rst and not self._rst:
            self.enabled = False
        if not rst and self._rst or self._t_rst is None and not rst:
            self._t_rst = t                   # RESET fell: the interface starts in frame
            self._bits, self._rx, self._frame = 0, 0, []
            self._skip = self.desync          # a target out of step by this many bits
            self.desync = 0
        self._rst = rst
        if rst:
            self._p, self._miso = sck, 1
            return 0, 0
        if self._p == 0 and sck == 1:         # rising: in
            self._phase_ok(t)
            if self._skip:
                self._skip -= 1
            else:
                self._rx = self._rx << 1 | mosi
                self._bits += 1
                if self._bits == 8:
                    b = self._rx & 0xFF
                    self._frame.append(b)
                    self._bits, self._rx = 0, 0
                    if len(self._frame) == 3:
                        self._out = self._data_out(t, self._frame)
                    elif len(self._frame) == 2 and not self.enabled and self._frame[0] != 0xAC:
                        self._out = 0xFF      # out of step: no echo of Programming Enable
                    else:
                        self._out = b         # the previous byte goes back out
                    self._obit = 8
                    if len(self._frame) == 4:
                        self._instruction(t, self._frame)
                        self._frame = []
        elif self._p == 1 and sck == 0:       # falling: out
            self._phase_ok(t)
            if self._obit:
                self._obit -= 1
                self._miso = (self._out >> self._obit) & 1 if self._obit < 8 else 1
            else:
                self._miso = 1
        self._p = sck
        return (0 if self._miso else 1 << self.miso), 0
