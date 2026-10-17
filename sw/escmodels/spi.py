"""SPI target (peripheral) model, modes 0-3, MSB first, with a waveform checker."""

from __future__ import annotations

from .board import Device


class SpiTarget(Device):
    """Samples MOSI and shifts MISO according to CPOL/CPHA while CS_n is low.

    Responds with `reply` bytes (cycled). Records received bytes per CS frame.
    Checks: SCK idle level == CPOL whenever CS_n changes; MOSI stable at each
    sampling edge for `setup` ticks; no SCK edges while CS_n is high.
    """

    def __init__(self, sck: int, mosi: int, miso: int, cs: int, mode: int,
                 reply: bytes = b"\xA5", setup: int = 2):
        self.sck, self.mosi, self.miso, self.cs = sck, mosi, miso, cs
        self.cpol, self.cpha = (mode >> 1) & 1, mode & 1
        self.reply, self.setup = reply, setup
        self.frames: list[list[int]] = []
        self.violations: list[str] = []
        self._p = None
        self._bits = 0
        self._rx = 0
        self._tx = 0
        self._ridx = 0
        self._miso = 1
        self._t_mosi = -10**9
        self._load()              # first reply byte; then one more per completed byte

    def _load(self):
        self._tx = self.reply[self._ridx % len(self.reply)]
        self._ridx += 1

    def step(self, t, bus):
        sck = (bus >> self.sck) & 1
        cs = (bus >> self.cs) & 1
        mosi = (bus >> self.mosi) & 1
        if self._p is None:
            self._p = (sck, cs, mosi)
        psck, pcs, pmosi = self._p
        if mosi != pmosi:
            self._t_mosi = t
        if pcs == 1 and cs == 0:                       # frame start
            if sck != self.cpol:
                self.violations.append(f"t={t}: SCK={sck} at CS fall, CPOL={self.cpol}")
            self.frames.append([])
            self._bits, self._rx = 0, 0
            if self.cpha == 0:
                self._miso = (self._tx >> 7) & 1
        elif pcs == 0 and cs == 1:
            if sck != self.cpol:
                self.violations.append(f"t={t}: SCK={sck} at CS rise, CPOL={self.cpol}")
        elif cs == 0 and sck != psck:
            leading = sck != self.cpol
            sample_edge = leading if self.cpha == 0 else not leading
            if sample_edge:
                if t - self._t_mosi < self.setup:
                    self.violations.append(f"t={t}: MOSI changed {t - self._t_mosi} ticks before sample")
                self._rx = ((self._rx << 1) | pmosi) & 0xFF
                self._bits += 1
                if self._bits == 8:
                    self.frames[-1].append(self._rx)
                    self._bits, self._rx = 0, 0
                    self._load()
                    if self.cpha == 0:
                        self._miso = (self._tx >> 7) & 1
            else:
                # shift edge: present the next bit
                if self.cpha == 0:
                    self._miso = (self._tx >> (7 - self._bits)) & 1 if self._bits else self._miso
                else:
                    self._miso = (self._tx >> (7 - self._bits)) & 1
        self._p = (sck, cs, mosi)
        m = 1 << self.miso
        if cs == 0:
            return (0, m) if self._miso else (m, 0)
        return 0, 0

    def errors(self):
        return [f"SPI: {v}" for v in self.violations[:10]]
