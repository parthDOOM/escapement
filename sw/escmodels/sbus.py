"""S.BUS source: an RC receiver's serial output, as a flight controller's input pin sees it.

Futaba S.BUS as the open implementations decode it (bolderflight/sbus): 100000 baud,
8 data bits LSB first, even parity, two stop bits, inverted (idle low); 25-byte frames:
header 0x0F, 16 channels of 11 bits packed LSB first, the flags byte (channel 17, 18,
frame lost, failsafe in bits 0-3), footer 0x00 (S.BUS2: low nibble 0x4).
"""

from __future__ import annotations

from .board import Device


def encode(channels, flags: int = 0, footer: int = 0x00) -> bytes:
    acc = 0
    for k, v in enumerate(channels):
        acc |= (v & 0x7FF) << (11 * k)
    return bytes([0x0F]) + acc.to_bytes(22, "little") + bytes([flags & 0xFF, footer])


def decode(frame: bytes):
    acc = int.from_bytes(frame[1:23], "little")
    return [(acc >> (11 * k)) & 0x7FF for k in range(16)], frame[23]


class SbusSource(Device):
    """Sends frames [(tick, bytes)] on `pin`; `inverted` as the raw S.BUS line. `bad_bit`:
    {frame index: (byte, bit)} flips one bit of a character (0 start, 1-8 data, 9 parity,
    10-11 stop)."""

    def __init__(self, pin: int, ticks_per_bit: float, frames, inverted: bool = True,
                 bad_bit=None, gap_bits: float = 0.0):
        self.pin, self.inv = pin, inverted
        self.edges: list[tuple[int, int]] = []
        for i, (t, data) in enumerate(frames):
            x = float(t)
            for j, b in enumerate(data):
                d = [(b >> k) & 1 for k in range(8)]
                bits = [0] + d + [sum(d) & 1, 1, 1]
                if bad_bit and bad_bit.get(i, (None,))[0] == j:
                    bits[bad_bit[i][1]] ^= 1
                for bit in bits:
                    self.edges.append((round(x), bit))
                    x += ticks_per_bit
                x += gap_bits * ticks_per_bit
        self.ei, self.level = 0, 1

    def step(self, t, bus):
        while self.ei < len(self.edges) and self.edges[self.ei][0] <= t:
            self.level = self.edges[self.ei][1]
            self.ei += 1
        line = self.level ^ self.inv
        m = 1 << self.pin
        return (0, m) if line else (m, 0)
