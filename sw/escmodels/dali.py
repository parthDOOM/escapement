"""DALI application controller on the bus, for checking control gear firmware.

From Microchip's DALI documentation (after IEC 62386-101): Manchester at 1200 bit/s,
half bit Te = 416.67 us; a 1 is low then high, a 0 high then low; MSB first; the bus
idles high. A forward frame is a start bit, 16 bits and a stop condition; a backward
frame is a start bit and 8 bits, starting 5.5 ms to 10.5 ms after the forward frame.
"""

from __future__ import annotations

from .board import Device

TE_US = 1e6 / 2400


def manchester(bits) -> list[int]:
    """Half-bit levels of a start bit and `bits`."""
    out = [0, 1]
    for b in bits:
        out += [1 - b, b]
    return out


class DaliController(Device):
    """Sends `frames` [(tick, 16-bit frame)] on `pin` with its own half bit `scale` times
    the nominal one, and decodes the backward frame after each. `answers[i]` is None or
    (byte, settling time in ms from the end of the forward frame's last bit)."""

    def __init__(self, pin: int, frames, tick_ns: float = 40.0, scale: float = 1.0, tol: float = 0.1):
        self.pin = pin
        self.tick = tick_ns
        self.te = TE_US * 1000 / tick_ns
        self.te_tx = self.te * scale
        self.tol = tol
        self.frames = sorted(frames)
        self.answers: list = []
        self.violations: list[str] = []
        self._k = 0                    # next frame to send
        self._tx = None                # (start tick, half levels)
        self._listen = None            # (end of forward frame, edges seen)
        self._prev = 1

    def step(self, t, bus):
        lvl = (bus >> self.pin) & 1
        low = 0
        if self._tx is None and self._listen is None and self._k < len(self.frames) \
                and self.frames[self._k][0] <= t:
            f = self.frames[self._k][1]
            self._tx = (t, manchester([(f >> (15 - i)) & 1 for i in range(16)]))
            self._k += 1
        if self._tx is not None:
            t0, halves = self._tx
            h = int((t - t0) / self.te_tx)
            if h < len(halves):
                low = halves[h] == 0
            else:
                self._tx = None
                self._listen = (t0 + len(halves) * self.te_tx, [])
        elif self._listen is not None:
            end, edges = self._listen
            if lvl != self._prev:
                edges.append((t, lvl))
            if t > end + 20e6 / self.tick:               # 10.5 ms + a backward frame
                self._decode(end, edges)
                self._listen = None
        self._prev = lvl
        return (int(low) << self.pin), 0

    def _decode(self, end, edges):
        while edges and edges[0][1] == 1:      # the forward frame's own return to idle
            edges = edges[1:]
        if not edges:
            self.answers.append(None)
            return
        f0 = edges[0][0]
        settle = (f0 - end) * self.tick / 1e6
        if not 5.5 <= settle <= 10.5:
            self.violations.append(f"t={f0}: settling time {settle:.2f} ms")
        level = lambda tt: next((v for t, v in reversed(edges) if t <= tt), 1)   # noqa: E731
        halves = [level(f0 + (h + 0.5) * self.te) for h in range(18)]
        if halves[:2] != [0, 1] or any(halves[i] == halves[i + 1] for i in range(2, 18, 2)):
            self.violations.append(f"t={f0}: not Manchester: {halves}")
        for t, _ in edges:             # every edge on the half-bit grid
            k = round((t - f0) / self.te)
            if abs(t - f0 - k * self.te) > self.tol * self.te:
                self.violations.append(f"t={t}: edge {(t - f0) / self.te:.3f} Te after the start")
        if edges[-1][1] != 1 or edges[-1][0] > f0 + 18.5 * self.te:
            self.violations.append(f"t={f0}: backward frame does not end high after 18 halves")
        byte = 0
        for i in range(8):
            byte = (byte << 1) | halves[3 + 2 * i]
        self.answers.append((byte, settle))

    def errors(self):
        return [f"dali: {v}" for v in self.violations[:5]]
