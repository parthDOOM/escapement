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


class OneWireMaster(Device):
    """A 1-Wire bus master at standard speed (Maxim AN126 recommended timings, in us: A 6,
    B 64, C 60, D 10, E 9, F 55, H 480, I 70, J 410), for testing a device. `program(m)` is a
    generator built from m.reset(), m.write(byte), m.read() and m.search(); results land in
    m.results. Pulls DQ low or releases it; reads happen at the AN126 sampling points."""

    T = dict(A=6, B=64, C=60, D=10, E=9, F=55, H=480, I=70, J=410)

    def __init__(self, pin: int, tick_ns: float, program, start_us: float = 100, **timing):
        self.pin, self.us = pin, 1000.0 / tick_ns
        self.T = dict(self.T, **timing)            # e.g. A=15: a write-1 low for 15 us
        self.results: list = []
        self._gen = self._run(program, start_us)
        self._drive = False
        self._wait = 0
        self._level = 1
        self.done = False

    def _t(self, us):
        return max(1, round(us * self.us))

    def _run(self, program, start_us):
        yield ("rel", self._t(start_us))
        yield from program(self)

    # primitives (generators)
    def reset(self):
        T = self.T
        yield ("low", self._t(T["H"]))
        yield ("rel", self._t(T["I"]))
        present = self._level == 0
        yield ("rel", self._t(T["J"]))
        return present

    def write_bit(self, b):
        T = self.T
        if b:
            yield ("low", self._t(T["A"]))
            yield ("rel", self._t(T["B"]))
        else:
            yield ("low", self._t(T["C"]))
            yield ("rel", self._t(T["D"]))

    def read_bit(self):
        """Low for RL us (default A), sampled RS us after the edge (default A + E)."""
        T = self.T
        low, at = T.get("RL", T["A"]), T.get("RS", T["A"] + T["E"])
        yield ("low", self._t(low))
        yield ("rel", self._t(at - low))
        b = self._level
        yield ("rel", self._t(T["A"] + T["E"] + T["F"] - at))
        return b

    def write(self, byte):
        for k in range(8):
            yield from self.write_bit((byte >> k) & 1)

    def read(self):
        v = 0
        for k in range(8):
            v |= (yield from self.read_bit()) << k
        return v

    def read_n(self, n):
        out = []
        for _ in range(n):
            out.append((yield from self.read()))
        return bytes(out)

    def search(self):
        """All ROMs on the bus by Maxim AN187's search: at each bit position the devices
        send the bit and its complement; 1 1 means none, differing values the bit, 0 0 a
        discrepancy, resolved as AN187 does (the last discrepancy goes 1, earlier ones
        repeat the previous path, later ones go 0)."""
        found, last_disc = [], 0
        rom = [0] * 65                              # rom[1..64], 1-based as in AN187
        while True:
            if not (yield from self.reset()):
                return found
            yield from self.write(0xF0)
            last_zero = 0
            for i in range(1, 65):
                a = yield from self.read_bit()
                b = yield from self.read_bit()
                if a and b:
                    return found
                if a != b:
                    d = a
                else:
                    d = rom[i] if i < last_disc else int(i == last_disc)
                    if d == 0:
                        last_zero = i
                rom[i] = d
                yield from self.write_bit(d)
            found.append(sum(rom[k + 1] << k for k in range(64)))
            last_disc = last_zero
            if last_disc == 0:
                return found

    def step(self, t, bus):
        self._level = (bus >> self.pin) & 1
        while self._wait <= 0 and not self.done:
            try:
                op, n = next(self._gen)
            except StopIteration:
                self.done, self._drive = True, False
                break
            self._drive, self._wait = op == "low", n
        self._wait -= 1
        return (1 << self.pin if self._drive else 0), 0
