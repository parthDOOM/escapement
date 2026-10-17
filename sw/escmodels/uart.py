"""UART models: a receiver that decodes and checks frames, and a transmitter."""

from __future__ import annotations

from .board import Device


class UartRx(Device):
    """Decodes 8N1 frames on `pin` (idle high). Samples each bit at its centre,
    measured from the observed falling edge of the start bit."""

    def __init__(self, pin: int, ticks_per_bit: float):
        self.pin, self.tpb = pin, ticks_per_bit
        self.bytes: list[int] = []
        self.framing_errors: list[int] = []
        self._start = None
        self._prev = 1
        self._seen_idle = False

    def step(self, t, bus):
        lvl = (bus >> self.pin) & 1
        if not self._seen_idle:
            self._seen_idle = lvl == 1   # ignore the undriven/low line before idle
        elif self._start is None:
            if self._prev == 1 and lvl == 0:
                self._start = t
        else:
            k = (t - self._start) / self.tpb   # bit position
            idx = int(k)
            # sample once, at the first tick past the centre of each bit
            centre = self._start + (idx + 0.5) * self.tpb
            if centre <= t < centre + 1:
                if idx == 0 and lvl != 0:
                    self._start = None          # glitch, not a start bit
                elif 1 <= idx <= 8:
                    self._acc = (getattr(self, "_acc", 0) >> 1) | (lvl << 7)
                elif idx == 9:
                    if lvl == 1:
                        self.bytes.append(self._acc)
                    else:
                        self.framing_errors.append(t)
                    self._acc = 0
                    self._start = None
        self._prev = lvl
        return 0, 0

    def errors(self):
        return [f"UART framing error at t={t}" for t in self.framing_errors]


class UartTx(Device):
    """Drives 8N1 frames on `pin` (push-pull from the device side) starting at `start`."""

    def __init__(self, pin: int, ticks_per_bit: float, data: bytes, start: int = 100,
                 gap_bits: float = 1.0):
        self.pin, self.tpb = pin, ticks_per_bit
        self.edges: list[tuple[int, int]] = []   # (tick, level) schedule
        t = float(start)
        for b in data:
            bits = [0] + [(b >> i) & 1 for i in range(8)] + [1]
            for bit in bits:
                self.edges.append((round(t), bit))
                t += self.tpb
            t += gap_bits * self.tpb
        self.end = round(t)

    def step(self, t, bus):
        lvl = 1
        for et, bit in self.edges:
            if et > t:
                break
            lvl = bit
        m = 1 << self.pin
        return (0, m) if lvl else (m, 0)
