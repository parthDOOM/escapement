"""Modbus RTU client on an RS-485 bus, as the server's transceiver pins see it.

Written from Modbus over Serial Line V1.02 (sec. 2.5: 11-bit characters, 8 data bits LSB
first, even, odd or no parity (then two stop bits); frames separated by 3.5 character
times of silence; CRC-16 polynomial 0xA001 reflected from 0xFFFF, low byte first) and
the Modbus Application Protocol V1.1b3 (the PDUs). The client drives the server's RO
pin; it decodes the server's DI pin while DE is high, one reply per DE pulse, and flags
any character the server starts with DE low.
"""

from __future__ import annotations

from .board import Device


def crc16(data: bytes) -> int:
    c = 0xFFFF
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ (0xA001 if c & 1 else 0)
    return c


def adu(addr: int, pdu: bytes) -> bytes:
    body = bytes([addr]) + pdu
    return body + crc16(body).to_bytes(2, "little")


def char_bits(b: int, parity: str) -> list[int]:
    d = [(b >> k) & 1 for k in range(8)]
    if parity == "N":
        return [0] + d + [1, 1]
    p = sum(d) & 1 if parity == "E" else 1 - (sum(d) & 1)
    return [0] + d + [p, 1]


class ModbusClient(Device):
    """requests: [(raw ADU bytes, reply expected[, wait])], sent one after another: each
    `turn` ticks after the reply to the one before ends (DE falls) and at least `silence`
    after the last frame; after a request that expects no reply, `wait` ticks after it
    (a response timeout, default `noreply`) instead. `gaps` (bit times between characters) per request, or 0.
    `bad_bit`: {request index: (character, bit)} flips that bit on the wire."""

    def __init__(self, ro: int, di: int, de: int, ticks_per_bit: float, requests,
                 parity: str = "E", start: int = 2000, turn: int = 2000, gaps=None,
                 bad_bit=None, timeout: int = 400_000, silence: int | None = None,
                 noreply: int | None = None):
        self.ro, self.di, self.de, self.tpb = ro, di, de, ticks_per_bit
        # the next request waits `turn` after the reply, and at least `silence` after the
        # end of the last frame on the bus (3.5 characters by default, sec. 2.5.1.1)
        self.silence = round(4 * 11 * ticks_per_bit) if silence is None else silence
        self.last_end = 0
        self.noreply = 2 * self.silence if noreply is None else noreply
        self.requests, self.parity, self.turn = list(requests), parity, turn
        self.gaps, self.bad_bit, self.timeout = gaps or {}, bad_bit or {}, timeout
        self.k = 0                       # the request being sent or answered
        self.edges: list[tuple[int, int]] = []
        self.ei, self.level = 0, 1
        self.start, self.next_at, self.req_end = start, start, None
        self.sent: list[tuple[int, int]] = []          # (first start bit, end of last stop bit)
        self.replies: list[dict] = []
        self.violations: list[str] = []
        self._de, self._di = 0, 1
        self._cur = None                 # the reply being decoded
        self._ch = None                  # the start tick of the character being decoded
        self._ch_bits: list[int] = []
        self._deadline = None

    def _schedule(self, t):
        raw = self.requests[self.k][0]
        gap = self.gaps.get(self.k, 0.0)
        x, edges = float(t), []
        for i, b in enumerate(raw):
            bits = char_bits(b, self.parity)
            if self.bad_bit.get(self.k, (None,))[0] == i:
                bits[self.bad_bit[self.k][1]] ^= 1
            for bit in bits:
                edges.append((round(x), bit))
                x += self.tpb
            x += gap * self.tpb
        self.edges, self.ei = edges, 0
        self.sent.append((round(t), round(x - gap * self.tpb)))
        self.req_end = round(x)
        self.last_end = round(x - gap * self.tpb)

    def step(self, t, bus):
        de, di = (bus >> self.de) & 1, (bus >> self.di) & 1
        if t < self.start:               # (the pins before the server's first writes)
            self._de, self._di = de, di
            return 0, 1 << self.ro
        # requests
        if self.next_at is not None and t >= self.next_at and self.k < len(self.requests):
            self._schedule(t)
            self.next_at = None
        while self.ei < len(self.edges) and self.edges[self.ei][0] <= t:
            self.level = self.edges[self.ei][1]
            self.ei += 1
        if self.req_end is not None and t >= self.req_end and self.ei == len(self.edges):
            self.req_end = None
            if not self.requests[self.k][1]:
                req = self.requests[self.k]
                wait = req[2] if len(req) > 2 else self.noreply
                self.k += 1
                self.next_at = self.last_end + wait if self.k < len(self.requests) else None
            else:
                self._deadline = t + self.timeout
        if self._deadline is not None and self._cur is None and t > self._deadline:
            self.violations.append(f"no reply to request {self.k}")
            self._deadline = None
            self.k += 1
            self.next_at = (max(t + self.turn, self.last_end + self.silence)
                                if self.k < len(self.requests) else None)
        # replies
        if de and not self._de:
            self._cur = {"de_rise": t, "bytes": [], "starts": [], "errors": [], "req": self.k}
            self._deadline = None
        if self._cur is not None and de:
            if self._ch is None and self._di and not di:
                self._ch = t
            elif self._ch is not None and t >= self._ch + (len(self._ch_bits) + 0.5) * self.tpb:
                self._ch_bits.append(di)                # each bit at its centre
                if len(self._ch_bits) == 11:
                    self._finish_char(t)
        elif not de and di == 0 and self._di == 1:
            self.violations.append(f"server drives DI low with DE low at t={t}")
        if self._de and not de and self._cur is not None:
            if self._ch is not None:
                self._cur["errors"].append("DE fell inside a character")
            self._cur["de_fall"] = t
            self.replies.append(self._cur)
            self._cur, self._ch, self._ch_bits = None, None, []
            if self.k < len(self.requests) and self.requests[self.k][1]:
                self.k += 1
                self.next_at = (max(t + self.turn, self.last_end + self.silence)
                                if self.k < len(self.requests) else None)
        self._de, self._di = de, di
        return (0, 1 << self.ro) if self.level else (1 << self.ro, 0)

    def _finish_char(self, t):
        b = self._ch_bits
        d = sum(b[1 + k] << k for k in range(8))
        if b[0] != 0:
            self._cur["errors"].append("start bit")
        if self.parity == "N":
            ok = b[9] == 1 and b[10] == 1
        else:
            want = sum(b[1:9]) & 1 if self.parity == "E" else 1 - (sum(b[1:9]) & 1)
            ok = b[9] == want and b[10] == 1
        if not ok:
            self._cur["errors"].append(f"parity or stop, byte {len(self._cur['bytes'])}")
        self._cur["bytes"].append(d)
        self._cur["starts"].append(self._ch)
        self._cur["end"] = round(self._ch + 11 * self.tpb)
        self.last_end = self._cur["end"]
        self._ch, self._ch_bits = None, []

    def errors(self):
        out = list(self.violations)
        for i, r in enumerate(self.replies):
            out += [f"reply {i}: {e}" for e in r["errors"]]
            data = bytes(r["bytes"])
            if len(data) < 4 or crc16(data) != 0:
                out.append(f"reply {i}: bad CRC or short: {data.hex()}")
        return out
