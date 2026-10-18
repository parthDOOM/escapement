"""Board: the external world around the 8 protocol pins, as composable device models.

A Board instance is the `board(t, uio_out, uio_oe) -> uio_in` callable used by both the
ISS and the RTL co-simulation, so protocol checks run identically on both.

Electrical model per pin, evaluated once per core clock cycle t:
  * chip drives high (push-pull oe=1, out=1)  -> high (a device pulling low is a CONTENTION)
  * chip drives low  (oe=1, out=0)            -> low
  * otherwise: low if any device drives low, high if a device drives high, else the
    pull-up (1) or pull-down (0) configured for the pin.
Devices see the bus level of the previous cycle (one-cycle reaction, 40 ns at 25 MHz),
which avoids combinational loops and is pessimistic in the right direction.
Board instances are stateful: create a fresh one for every run.
"""

from __future__ import annotations


class Device:
    """Base class. step() returns (drive_low_mask, drive_high_mask) for cycle t."""

    def step(self, t: int, bus: int) -> tuple[int, int]:
        return 0, 0

    def errors(self) -> list[str]:
        return []


class Board:
    def __init__(self, *devices: Device, pullups: int = 0xFF):
        self.devices = list(devices)
        self.pullups = pullups
        self.bus = pullups
        self.contention: list[tuple[int, int]] = []
        self.history: list[int] = []   # bus level per cycle (for checks and plots)

    def __call__(self, t: int, uio_out: int, uio_oe: int) -> int:
        if t < 0:
            return 0
        low = high = 0
        for d in self.devices:
            dl, dh = d.step(t, self.bus)
            low |= dl
            high |= dh
        chip_hi = uio_oe & uio_out
        chip_lo = uio_oe & ~uio_out & 0xFF
        if chip_hi & low:
            self.contention.append((t, chip_hi & low))
        ext = (self.pullups | high) & ~low & 0xFF
        level = (chip_hi | (ext & ~uio_oe)) & ~chip_lo & 0xFF
        self.bus = level
        self.history.append(level)
        return level

    def errors(self) -> list[str]:
        errs = [f"contention at t={t} pins 0x{m:02x}" for t, m in self.contention[:5]]
        for d in self.devices:
            errs += d.errors()
        return errs

    def level(self, pin: int) -> list[int]:
        return [(v >> pin) & 1 for v in self.history]


class SplitBoard(Board):
    """The world around pin bank B (v1.2): the `board_b(t, ob) -> ib` callable.

    A bank B pin is split, an output on uo_out and an input on ui_in, so each logical pin
    is wired in one direction. Pins in `outputs` carry the chip's uo_out level to the
    devices; the others carry the devices' level (or the pull-up) to ui_in. The devices
    are the same models as on bank A and see one level per pin, as on a shared wire.
    """

    def __init__(self, *devices: Device, outputs: int, pullups: int = 0x1F):
        super().__init__(*devices, pullups=pullups & 0x1F)
        self.outputs = outputs & 0x1F

    def __call__(self, t: int, ob: int) -> int:
        if t < 0:
            return 0
        low = high = 0
        for d in self.devices:
            dl, dh = d.step(t, self.bus)
            low |= dl
            high |= dh
        if (ob & self.outputs) & low:
            self.contention.append((t, ob & self.outputs & low))
        ext = (self.pullups | high) & ~low & 0x1F
        level = ((ob & self.outputs) | (ext & ~self.outputs)) & 0x1F
        self.bus = level
        self.history.append(level)
        return level
