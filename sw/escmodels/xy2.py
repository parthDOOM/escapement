"""An XY2-100 galvanometer scanner: the receiving side of the protocol, written from its
interface description (SCANLAB, "XY2-100 Standard"; Raylase, "XY2-100 protocol"), not from
any firmware or decoder.

CLK runs at 2 MHz. The controller sets SYNC and each axis's data line at the clock's rising
edge, and the scanner takes them at its falling edge. A frame is 20 bits, MSB first: SYNC
is high for the first 19 and low for the 20th. The bits are a control field (001 for a
16-bit position), the position in two's complement, and a parity bit that makes the 20
bits even.

The model checks every clock period against 500 ns (+/- 1 %), that data and SYNC change
only at a rising edge of the clock (never while it is high, so they are steady at the
falling edge), that each frame is 20 bits, and its control bits and parity. Frames for
each axis go to `frames[axis]` as signed positions, with the tick of their last bit;
everything wrong goes to `violations`.
"""

from __future__ import annotations

from .board import Device


class Xy2Scanner(Device):
    def __init__(self, clk: int, sync: int, axes: dict[str, int], tick_ns: float):
        self.clk, self.sync, self.axes, self.tick = clk, sync, dict(axes), tick_ns
        self.frames: dict[str, list[tuple[int, int]]] = {a: [] for a in axes}
        self.violations: list[str] = []
        self._prev = None                       # the last bus value
        self._rise: int | None = None           # the last rising edge of CLK
        self._bits: dict[str, list[int]] = {a: [] for a in axes}
        self._syncs: list[int] = []

    def _v(self, msg: str) -> None:
        if len(self.violations) < 50:
            self.violations.append(msg)

    def errors(self) -> list[str]:          # (Board.errors() collects them)
        return [f"XY2-100: {v}" for v in self.violations[:10]]

    def step(self, t, bus):
        prev, self._prev = self._prev, bus
        if prev is None or t < 0:
            return 0, 0
        clk, pclk = (bus >> self.clk) & 1, (prev >> self.clk) & 1
        watched = (1 << self.sync) | sum(1 << p for p in self.axes.values())
        if (bus ^ prev) & watched and not (clk and not pclk) and self._rise is not None:
            when = "at a falling edge" if pclk and not clk else "while CLK was high" if clk else "while CLK was low"
            self._v(f"t={t}: data or SYNC changed {when}, not at a rising edge")
        if clk and not pclk:
            if self._rise is not None:
                per = (t - self._rise) * self.tick
                if abs(per - 500.0) > 5.0:
                    self._v(f"t={t}: clock period {per:.1f} ns")
            self._rise = t
        if pclk and not clk and self._rise is not None:     # falling edge: take the bits
            s = (bus >> self.sync) & 1
            self._syncs.append(s)
            for a, p in self.axes.items():
                self._bits[a].append((bus >> p) & 1)
            if not s:
                self._frame(t)
        return 0, 0

    def _frame(self, t: int) -> None:
        syncs, self._syncs = self._syncs, []
        for a in self.axes:
            bits, self._bits[a] = self._bits[a], []
            if len(bits) != 20 or syncs[:-1] != [1] * 19:
                if self.frames[a] or len(bits) > 20:     # (the first may start mid-frame)
                    self._v(f"t={t}: axis {a}: a frame of {len(bits)} bits")
                continue
            if bits[:3] != [0, 0, 1]:
                self._v(f"t={t}: axis {a}: control bits {bits[:3]}")
                continue
            if sum(bits) % 2:
                self._v(f"t={t}: axis {a}: odd parity")
                continue
            v = sum(b << (15 - k) for k, b in enumerate(bits[3:19]))
            self.frames[a].append((v - 65536 if v & 0x8000 else v, t))
