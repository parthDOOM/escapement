"""1-Wire device model (DS18B20-like ROM behaviour), open-drain, standard speed.

Timing (Maxim AN126 recommended values, standard speed): reset low >= 480 us;
device waits 15-60 us then pulls low 60-240 us (presence); write/read slots start
with a host low pulse; the device samples 15-60 us after the falling edge for writes
and, for read slots of a 0 bit, holds the line low for ~30 us after the edge.
"""

from __future__ import annotations

from .board import Device


class OneWireDevice(Device):
    def __init__(self, pin: int, rom: bytes, tick_ns: float = 40.0):
        assert len(rom) == 8
        self.pin, self.rom = pin, rom
        self.us = 1000.0 / tick_ns            # ticks per microsecond
        self.written: list[int] = []
        self.presences = 0
        self.violations: list[str] = []
        self._prev = 1
        self._t_fall = None
        self._drive_until = -1
        self._drive_from = -1
        self._sample_at = None
        self._wbits = []
        self._tx: list[int] = []              # bits to return in read slots

    def _queue_bytes(self, data):
        for b in data:
            self._tx += [(b >> i) & 1 for i in range(8)]   # LSB first

    def step(self, t, bus):
        lvl = (bus >> self.pin) & 1
        if not lvl and self._prev:                        # falling edge (host or us)
            if t >= self._drive_until or t < self._drive_from:
                self._t_fall = t
                self._sample_at = t + round(30 * self.us)
                if self._tx:                               # read slot
                    if self._tx[0] == 0:
                        self._drive_from, self._drive_until = t, t + round(30 * self.us)
        if lvl and not self._prev and self._t_fall is not None and t >= self._drive_until:
            low = (t - self._t_fall) / self.us
            if low >= 480:                                 # reset pulse
                self._wbits, self._tx = [], []
                self._sample_at = None
                self._drive_from = t + round(30 * self.us)
                self._drive_until = self._drive_from + round(120 * self.us)
                self.presences += 1
            elif low > 120:
                self.violations.append(f"t={t}: low pulse {low:.0f} us is neither slot nor reset")
            self._t_fall = None
        if self._sample_at is not None and t == self._sample_at:
            self._sample_at = None
            if self._tx:
                self._tx.pop(0)                            # we answered a read slot
            else:
                self._wbits.append(lvl)
                if len(self._wbits) == 8:
                    byte = sum(b << i for i, b in enumerate(self._wbits))
                    self.written.append(byte)
                    self._wbits = []
                    if byte == 0x33:                       # READ ROM
                        self._queue_bytes(self.rom)
        self._prev = lvl
        if self._drive_from <= t < self._drive_until:
            return 1 << self.pin, 0
        return 0, 0

    def errors(self):
        return [f"1-Wire: {v}" for v in self.violations[:10]]


def crc8_maxim(data: bytes) -> int:
    crc = 0
    for b in data:
        for _ in range(8):
            mix = (crc ^ b) & 1
            crc >>= 1
            if mix:
                crc ^= 0x8C
            b >>= 1
    return crc
