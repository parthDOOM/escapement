"""10BASE-T on the wire, as the chip's receive comparator sees it (through the both-edge sampler).

`EthSource` plays 802.3 frames on one pin: preamble (seven 0x55), SFD (0xD5), the frame,
its CRC-32 frame check sequence, then TP_IDL (the line held high). Bits go least
significant first, each as two Manchester cells: a 1 is low then high (the level after
the mid-bit edge is the bit), a 0 high then low. Between frames the comparator reads 1
(it is biased toward TP_IDL's level, so the idle line and link pulses read high).

Edges are placed in continuous time and sampled twice per tick, as the chip samples its
pins: `level(h)` is the pin at half tick h, the core's rising-edge sample in cycle t is
level(2t), and its falling-edge sample in cycle t (project.v pin_h) is level(2t - 1).
`half` is that sample in the form Machine(half=...) and the co-simulation take. The
transmitter's clock may be off by `ppm` and each edge may move by up to `jitter` half
ticks (uniform, seeded): a fixed waveform, so the ISS and RTL twins see the same pins.
"""

from __future__ import annotations

import bisect
import random
import zlib

from .board import Device

PREAMBLE = b"\x55" * 7 + b"\xd5"


def wire_bits(frame: bytes, fcs: bool = True) -> list[int]:
    """The bits on the wire for one frame, in order: preamble, SFD, frame, FCS."""
    data = PREAMBLE + frame + (zlib.crc32(frame).to_bytes(4, "little") if fcs else b"")
    return [(b >> i) & 1 for b in data for i in range(8)]


class EthSource(Device):
    """Plays `frames`, a list of (start tick, wire bits), on `pin`; `bit_ht` is the bit
    time in half ticks (10 at 50 MHz: 100 ns)."""

    def __init__(self, pin: int, frames: list[tuple[int, list[int]]], bit_ht: float,
                 ppm: float = 0.0, jitter: float = 0.0, seed: int = 1, tp_idl: float = 3.0):
        self.pin = pin
        rng = random.Random(seed)
        cell = bit_ht * (1 + ppm * 1e-6) / 2
        times, levels = [], []           # an edge at half tick times[i] makes the pin levels[i]
        for t0, bits in sorted(frames):
            cells = [c for b in bits for c in (1 - b, b)]
            h0 = 2 * t0
            lv = 1                       # idle
            for i, c in enumerate(cells):
                if c != lv:
                    at = h0 + i * cell
                    if i:                # (the frame's first edge is its start: no jitter)
                        at += rng.uniform(-jitter, jitter)
                    times.append(at)
                    levels.append(c)
                    lv = c
            if lv != 1:                  # TP_IDL: back to high at the end of the last bit
                times.append(h0 + len(cells) * cell)
                levels.append(1)
            self.end = h0 + (len(cells) + 2 * tp_idl) * cell
        order = sorted(range(len(times)), key=times.__getitem__)
        self._t = [times[i] for i in order]
        self._v = [levels[i] for i in order]

    def level(self, h: float) -> int:
        """The pin at half tick h: the last edge at or before h, else idle (1)."""
        i = bisect.bisect_right(self._t, h)
        return self._v[i - 1] if i else 1

    def step(self, t, bus):
        return ((1 << self.pin) if t >= 0 and self.level(2 * t) == 0 else 0), 0

    def half(self, t: int, before: int, during: int) -> int:
        """Machine.half: this pin at the falling edge in cycle t, the other pins as they
        were in cycle t - 1 (they change after that edge, as every bench drives them)."""
        if t <= 0:
            return before
        m = 1 << self.pin
        return (before & ~m) | (self.level(2 * t - 1) << self.pin)
