"""A FlexRay receiver on one channel, written from the FlexRay Communications System Protocol
Specification (v2.1 rev. A / v3.0.1: coding and decoding, frame format), not from any
firmware or decoder.

The line idles high; a frame starts with the transmission start sequence (TSS, low, 3 to
15 bits here), then the frame start sequence (FSS, one high bit), then every byte as its
byte start sequence (BSS: high, then low) and 8 bits MSB first, and ends with the frame
end sequence (FES: low, then high). Bits are taken at their middle; the receiver
resynchronises on the falling edge in each BSS, as the specification's bit strobing does.

The frame: a 5-byte header (reserved bit, payload preamble, null frame, sync frame and
startup frame indicators, 11-bit frame ID, 7-bit payload length in 2-byte words, 11-bit
header CRC, 6-bit cycle count), the payload, and a 24-bit frame CRC. The header CRC
(polynomial 0x385, start 0x01A) covers the sync and startup indicators, the frame ID and
the payload length; the frame CRC (polynomial 0x5D6DCB, start 0xFEDCBA on channel A,
0xABCDEF on B) the header and the payload; both MSB first, computed here bit by bit.

The model also holds the transmitter to its bit time: every edge within a tenth of a bit of
a bit boundary. Frames go to `frames` as dicts; everything wrong goes to `violations`.
"""

from __future__ import annotations

from .board import Device


def crc_bits(bits, poly: int, width: int, init: int) -> int:
    reg = init
    for b in bits:
        fb = ((reg >> (width - 1)) & 1) ^ b
        reg = (reg << 1) & ((1 << width) - 1)
        if fb:
            reg ^= poly
    return reg


def to_bits(value: int, n: int) -> list[int]:
    return [(value >> (n - 1 - k)) & 1 for k in range(n)]


class FlexRayReceiver(Device):
    def __init__(self, pin: int, tick_ns: float, bit_ns: float, channel: str = "A"):
        self.pin, self.tick, self.bit = pin, tick_ns, bit_ns / tick_ns     # bit in ticks
        self.init = 0xFEDCBA if channel == "A" else 0xABCDEF
        self.frames: list[dict] = []
        self.violations: list[str] = []
        self._lv = 1
        self._edges: list[tuple[int, int]] = []     # (tick, new level) in the current frame
        self._last_edge: int | None = None

    def _v(self, msg: str) -> None:
        if len(self.violations) < 50:
            self.violations.append(msg)

    def step(self, t, bus):
        if t < 0:
            return 0, 0
        lv = (bus >> self.pin) & 1
        if lv != self._lv:
            self._edges.append((t, lv))
            self._lv, self._last_edge = lv, t
        elif self._edges and lv and t - self._last_edge > 12 * self.bit:
            self._decode(self._edges)                # idle after a frame: decode it
            self._edges = []
        return 0, 0

    def level(self, edges, at: float) -> int:
        lv = 1
        for t, v in edges:
            if t > at:
                break
            lv = v
        return lv

    def _decode(self, edges) -> None:
        t0 = edges[0][0]
        if edges[0][1] != 0 or len(edges) < 2:
            self._v(f"t={t0}: a frame that does not start low")
            return
        for t, _ in edges:                          # every edge on a bit boundary
            k = (t - t0) / self.bit
            if abs(k - round(k)) > 0.1:
                self._v(f"t={t}: an edge {k:.2f} bits after the TSS began")
                break
        fss = edges[1][0]
        tss = round((fss - t0) / self.bit)
        if not 3 <= tss <= 15:
            self._v(f"t={t0}: TSS of {tss} bits")
        bits, ref = [], fss + self.bit               # the first BSS starts after the FSS
        while True:
            # BSS: high for a bit, then low; resynchronise on its falling edge
            fall = next((t for t, v in edges if v == 0 and abs(t - (ref + self.bit)) <= self.bit / 2), None)
            if self.level(edges, ref + self.bit / 2) != 1 or fall is None:
                break
            ref = fall
            byte = [self.level(edges, ref + (k + 1.5) * self.bit) for k in range(8)]
            bits += byte
            ref += 9 * self.bit                      # the next BSS (or the FES) starts here
        # FES: low, then high
        if not (self.level(edges, ref + self.bit / 2) == 0 and self.level(edges, ref + 1.5 * self.bit) == 1):
            self._v(f"t={t0}: no FES after {len(bits) // 8} bytes")
            return
        if len(bits) < 64:
            self._v(f"t={t0}: a frame of {len(bits) // 8} bytes")
            return
        val = lambda a, n: sum(b << (n - 1 - k) for k, b in enumerate(bits[a:a + n]))  # noqa: E731
        # bits 0-4 indicators, 5-15 frame ID, 16-22 payload length, 23-33 header CRC, 34-39 cycle
        plen = val(16, 7)
        hcrc_ok = crc_bits(bits[3:23], 0x385, 11, 0x01A) == val(23, 11)
        nbytes = 5 + 2 * plen
        fcrc_ok = len(bits) == 8 * (nbytes + 3) and crc_bits(bits[:8 * nbytes], 0x5D6DCB, 24, self.init) \
            == val(8 * nbytes, 24)
        self.frames.append({
            "tss": tss, "indicators": val(0, 5), "id": val(5, 11), "plen": plen,
            "cycle": val(34, 6),
            "payload": bytes(val(40 + 8 * k, 8) for k in range(2 * plen)) if len(bits) >= 8 * nbytes else b"",
            "header_crc_ok": hcrc_ok, "frame_crc_ok": fcrc_ok, "start": t0})
