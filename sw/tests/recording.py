"""A logic analyser recording played onto the board's pins, so the models in escmodels, and
the firmware on the ISS or the RTL, meet real devices (sigrok-dumps, tests/data/sigrok)."""

from __future__ import annotations

from pathlib import Path

import zipfile

from esctape.export import _parse_rate, read_sr
from escmodels.board import Device

DATA = Path(__file__).parent / "data" / "sigrok"


def meta(name: str) -> dict[str, str]:
    """The [device 1] section of a session's metadata (samplerate, probe names, ...)."""
    out = {}
    for line in zipfile.ZipFile(DATA / name).read("metadata").decode().splitlines():
        k, eq, v = line.partition("=")
        if eq:
            out[k.strip()] = v.strip()
    return out


def sample_ns(name: str) -> float:
    """The recording's sample period: playing it at this tick needs no resampling."""
    return 1e9 / _parse_rate(meta(name)["samplerate"])


class Recording(Device):
    """Drives pins from a recording: `pins` maps a recording channel to a board pin. A pin
    is driven low or high as the recording has it, so a device on it hears the recorded
    line; resampled to the board's tick (tick_ns). Past the end the pins are released."""

    def __init__(self, name: str, tick_ns: float, pins: dict[int, int]):
        self.records, self.names = read_sr(DATA / name, clock_hz=round(1e9 / tick_ns))
        self.pins, self.k, self.level = pins, 0, self.records[0][1]
        self.end = self.records[-1][0]

    def step(self, t, bus):
        while self.k < len(self.records) and self.records[self.k][0] <= t:
            self.level = self.records[self.k][1]
            self.k += 1
        if t > self.end:
            return 0, 0
        lo = hi = 0
        for c, p in self.pins.items():
            if (self.level >> c) & 1:
                hi |= 1 << p
            else:
                lo |= 1 << p
        return lo, hi


def play(name: str, tick_ns: float, pins: dict[int, int], *devices: Device) -> int:
    """Steps `devices` over the whole recording as the board would; returns the ticks run."""
    from escmodels import Board
    rec = Recording(name, tick_ns, pins)
    board = Board(rec, *devices)
    for t in range(rec.end + 1):
        board(t, 0, 0)
    return rec.end


def play_edges(name: str, tick_ns: float, pins: dict[int, int], *devices: Device, idle: int = 0) -> int:
    """As play(), but steps the devices only at the ticks where the recording changes: the
    same result for a device that acts only on a change of its pins (it is given the time
    of every change), and far faster on long recordings at high sample rates. Unmapped
    pins read `idle`."""
    rec = Recording(name, tick_ns, pins)
    for t, v in rec.records:
        bus = idle
        for c, p in pins.items():
            bus = (bus & ~(1 << p)) | (((v >> c) & 1) << p)
        for d in devices:
            d.step(t, bus)
    return rec.end
