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


def compress_idle(records: list, cap: int) -> list:
    """The same changes with every stretch of more than `cap` ticks without one cut to cap:
    an idle bus, shortened (a target sees no difference between long and longer idle)."""
    out, prev, nt = [], None, 0
    for t, v in records:
        if prev is not None:
            nt += min(t - prev, cap)
        out.append((nt, v))
        prev = t
    return out


def i2c_transactions(records: list, scl: int, sda: int) -> list[dict]:
    """I2C on two channels of (tick, levels) records: per START (or repeated START), its
    bytes as (byte, ack bit, [(SCL fall before, SCL rise, SCL fall after) per bit]) and
    the tick of its STOP (None if a repeated START or the end follows)."""
    out, cur, bits, pscl, psda, fall = [], None, [], 1, 1, None
    for t, v in records:
        c, d = (v >> scl) & 1, (v >> sda) & 1
        if c and pscl and d != psda:
            if d == 0:                                   # START or repeated START
                if cur is not None:
                    out.append(cur)
                cur, bits = {"t": t, "rs": cur is not None, "bytes": [], "stop": None}, []
            elif cur is not None:                        # STOP
                cur["stop"] = t
                out.append(cur)
                cur = None
        elif c and not pscl and cur is not None:         # a bit, sampled at SCL's rise
            bits.append([fall, t, None, d])
        elif not c and pscl:
            if cur is not None and bits and bits[-1][2] is None:
                bits[-1][2] = t
                if len(bits) == 9:
                    cur["bytes"].append((sum(b[3] << (7 - i) for i, b in enumerate(bits[:8])),
                                         bits[8][3], [tuple(b[:3]) for b in bits]))
                    bits = []
            fall = t
        pscl, psda = c, d
    if cur is not None:
        out.append(cur)
    return out


def i2c_target_slots(transactions: list[dict], address: int) -> list[tuple[int, int]]:
    """The bit slots the target at 7-bit `address` drives SDA in, (SCL fall before, SCL
    fall after]: the ACK of every byte it receives and the 8 data bits of every byte it
    sends."""
    slots = []
    for tr in transactions:
        bs = tr["bytes"]
        if not bs or bs[0][0] >> 1 != address:
            continue
        read = bs[0][0] & 1
        for k, (_, _, bit) in enumerate(bs):
            if k == 0 or not read:
                slots.append((bit[8][0], bit[8][2]))
            else:
                slots += [(b[0], b[2]) for b in bit[:8]]
    return sorted(slots)


class I2cReplay(Device):
    """A recorded I2C bus with one target taken out: drives SCL and SDA low wherever the
    recording has them low (open drain, so the chip can still pull either low and stretch
    the clock), except SDA in that target's bit slots, which it leaves to the chip."""

    def __init__(self, records: list, scl: int, sda: int, scl_pin: int, sda_pin: int, slots):
        self.records, self.k, self.v = records, 0, records[0][1]
        self.c, self.d, self.cp, self.dp = scl, sda, scl_pin, sda_pin
        self.slots, self.s = list(slots), 0
        self.end = records[-1][0]

    def step(self, t, bus):
        while self.k < len(self.records) and self.records[self.k][0] <= t:
            self.v = self.records[self.k][1]
            self.k += 1
        while self.s < len(self.slots) and self.slots[self.s][1] < t:
            self.s += 1
        mine = self.s < len(self.slots) and self.slots[self.s][0] < t <= self.slots[self.s][1]
        lo = 0 if (self.v >> self.c) & 1 else 1 << self.cp
        if not (self.v >> self.d) & 1 and not mine:
            lo |= 1 << self.dp
        return lo, 0


def bus_records(history: list[int], pins: list[int]) -> list[tuple[int, int]]:
    """A board's bus levels as (tick, levels) records, pins[k] on channel k."""
    out, prev = [], None
    for t, b in enumerate(history):
        v = sum(((b >> p) & 1) << k for k, p in enumerate(pins))
        if v != prev:
            out.append((t, v))
            prev = v
    return out
