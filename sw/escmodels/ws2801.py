"""A chain of WS2801 LED drivers, written from the Worldsemi WS2801 datasheet, not from any
firmware.

Each IC samples SDI on the rising edge of CKI, keeps the first 24 bits it sees after a
latch (R, G, B, MSB first) and passes the rest on, so LED k gets bits 24k to 24k + 23 of
what the controller sends. CKI held low for 500 us latches every IC's bits into its
outputs. The model records each latch as the list of colours (0xRRGGBB) and checks that
SDI never changes in the same tick as a rising edge of CKI (it must be steady when
sampled) and that no latch catches a partial colour.
"""

from __future__ import annotations

from .board import Device


class Ws2801Chain(Device):
    def __init__(self, ci: int, di: int, n: int, tick_ns: float):
        self.ci, self.di, self.n, self.tick = ci, di, n, tick_ns
        self.latches: list[list[int]] = []
        self.violations: list[str] = []
        self._prev = 0
        self._bits: list[int] = []
        self._low_since: int | None = None

    def step(self, t, bus):
        if t < 0:
            return 0, 0
        prev, self._prev = self._prev, bus
        ci, pci = (bus >> self.ci) & 1, (prev >> self.ci) & 1
        if ci and not pci:
            if (bus ^ prev) >> self.di & 1:
                self.violations.append(f"t={t}: SDI changed at a rising edge of CKI")
            self._bits.append((bus >> self.di) & 1)
            self._low_since = None
        elif not ci and pci:
            self._low_since = t
        elif not ci and self._low_since is not None and self._bits and \
                (t - self._low_since) * self.tick >= 500_000:
            bits, self._bits = self._bits, []
            if len(bits) % 24:
                self.violations.append(f"t={t}: latched after {len(bits)} bits, not whole colours")
            cols = [sum(b << (23 - k) for k, b in enumerate(bits[24 * i:24 * i + 24]))
                    for i in range(min(self.n, len(bits) // 24))]
            self.latches.append(cols)
        return 0, 0
