"""HDMI-CEC device on a one-wire open-drain bus, for checking firmware as follower and
as initiator.

Timing from the Linux kernel's software CEC implementation (drivers/media/cec/core/
cec-pin.c, which follows the HDMI 1.4 CEC specification): start bit low 3.7 ms (3.5 to
3.9), total 4.5 ms (4.3 to 4.7); data bit total 2.4 ms (2.05 to 2.75), a 0 is low
1.5 ms (1.3 to 1.7), a 1 low 0.6 ms (0.4 to 0.8); a follower samples 1.05 ms after the
falling edge (the earliest safe point is 0.85 ms). A block is 8 data bits MSB first,
EOM and ACK. The initiator sends ACK as a 1; a follower that accepts a frame addressed
to it pulls the line low until 1.5 ms (a 0); for broadcast (destination 15) the
meaning is inverted: a follower that rejects it pulls low. Signal free time before a
transmission: 5 bit periods for a new initiator, 7 after one's own frame, 3 to retry.
Arbitration: an initiator that sends a 1 and samples a 0 has lost and stops.
"""

from __future__ import annotations

from .board import Device

class CecDevice(Device):
    """A CEC device with logical address `la` on `pin`. It records every frame it sees
    in `frames` (list of dicts: start tick, bytes, eoms, acks = the ACK bit level per
    block) and acknowledges frames to `la`. It transmits the frames in `send`, each
    (earliest tick, bytes), after the signal free time, with retries; the outcome of
    each is appended to `results` ("ack", "nack", "lost"). Timing violations by any
    initiator are recorded."""

    def __init__(self, pin: int, la: int, tick_ns: float = 40.0, send=(), retries: int = 4,
                 ack: bool = True, force_start: int | None = None):
        self.pin, self.la, self.ack = pin, la, ack
        self.force_start = force_start    # first frame: start at this tick, whatever the bus
        self.tick = tick_ns
        self.send = list(send)
        self.retries = retries
        self.frames: list[dict] = []
        self.results: list[tuple[str, int]] = []
        self.violations: list[str] = []
        u = lambda us: round(us * 1000 / tick_ns)     # noqa: E731
        self.u = u
        self.prev = 1
        self.free_since = 0
        # receiver
        self.fall = None          # tick of the last falling edge
        self.rise = None
        self.rx = None            # frame being received: dict, or None
        self.bit = 0              # bit index within the block (0-9)
        self.byte = 0
        self.ack_until = -1
        # transmitter
        self.tx = None            # (bytes, tries)
        self.tx_bits: list[int] = []
        self.tx_start = None
        self.tx_k = 0
        self.need_free = 5

    # ------------------------------------------------------------------ helpers
    def _bits_of(self, data):
        bits = []
        for i, b in enumerate(data):
            bits += [(b >> (7 - k)) & 1 for k in range(8)] + [int(i == len(data) - 1), 1]
        return bits

    def step(self, t, bus):
        lvl = (bus >> self.pin) & 1
        low = 0
        # ---------------- receive / measure (every device, initiator included)
        if self.prev == 1 and lvl == 0:
            self._falling(t)
        elif self.prev == 0 and lvl == 1:
            self._rising(t)
        if self.rx is not None and self.fall is not None and t == self.fall + self.u(1050):
            self._sample(t, lvl)
        limit = self.u(4700) if self.rx is not None and self.rx["in_start"] else self.u(2750)
        if self.rx is not None and self.fall is not None and lvl == 1 and t - self.fall > limit + 2:
            self._end_rx()            # (an initiator stops after a refused block)
        if lvl == 0:
            self.free_since = None
        elif self.free_since is None:
            self.free_since = t
        # ---------------- transmit
        if self.tx is None and self.send and self.send[0][0] <= t:
            self.tx = (self.send.pop(0)[1], 0)
            self.need_free = 5
        forced = self.force_start is not None and t + 1 == self.force_start
        if self.tx is not None and self.tx_start is None and (forced or (
                self.force_start is None and self.free_since is not None and self.rx is None
                and t - self.free_since >= self.need_free * self.u(2400))):
            self.force_start = None
            self.tx_start = t + 1
            self.tx_bits = self._bits_of(self.tx[0])
            self.tx_k = -1                               # the start bit
        if self.tx_start is not None:
            low = self._tx_level(t, lvl)
        if t < self.ack_until:
            low = 1
        self.prev = lvl
        return (low << self.pin), 0

    # ------------------------------------------------------------------ transmitter
    def _tx_level(self, t, lvl):
        u = self.u
        s = self.tx_start
        if t < s:
            return 0
        if self.tx_k == -1:
            if t < s + u(3700):
                return 1
            if t < s + u(4500):
                return 0
            self.tx_start, self.tx_k = s + u(4500), 0
            s = self.tx_start
        k = self.tx_k
        if k >= len(self.tx_bits):
            return 0
        b = self.tx_bits[k]
        dt = t - s
        if dt == u(1050):                                  # sample our own bit
            ackbit = k % 10 == 9
            if ackbit:
                acked = lvl == 0
                bcast = (self.tx[0][0] & 0xF) == 0xF
                if (not acked) if not bcast else acked:
                    self._tx_done(t, "nack")
                    return 0
                if k == len(self.tx_bits) - 1:
                    self._tx_done(t, "ack")
                    return 0
            elif b == 1 and lvl == 0:
                self._tx_done(t, "lost")
                return 0
        if dt >= u(2400):
            self.tx_k += 1
            self.tx_start = s + u(2400)
            return self._tx_level(t, lvl)
        return int(dt < (u(600) if b else u(1500)))

    def _tx_done(self, t, how):
        data, tries = self.tx
        self.tx_start = None
        if how == "ack" or tries >= self.retries:
            self.results.append((how, t))
            self.tx = None
        else:
            self.tx = (data, tries + 1)
            self.need_free = 3

    # ------------------------------------------------------------------ receiver
    def _falling(self, t):
        if self.rx is not None and self.fall is not None:
            period = (t - self.fall) * self.tick / 1000     # us
            if self.bit == 0 and not self.rx["bytes"] and self.rx.get("in_start"):
                if not 4300 <= period <= 4700:
                    self.violations.append(f"t={t}: start bit {period:.0f} us")
                self.rx["in_start"] = False
            elif not 2050 <= period <= 2750:
                self.violations.append(f"t={t}: bit period {period:.0f} us")
        self.fall = t
        if self.rx is None:
            self.rx = {"start": t, "bytes": [], "eoms": [], "acks": [], "in_start": True, "hdr": None}
            self.bit = 0
            self.byte = 0
            return
        # an ACK bit of a frame to us: pull low until 1.5 ms
        if self.bit == 9 and self.ack:
            hdr = self.rx["hdr"]
            if hdr is not None and (hdr & 0xF) == self.la and (hdr & 0xF) != 0xF and (hdr >> 4) != self.la:
                self.ack_until = t + self.u(1500)

    def _rising(self, t):
        if self.fall is None or self.rx is None:
            return
        low = (t - self.fall) * self.tick / 1000
        if self.rx.get("in_start"):
            if not 3500 <= low <= 3900:
                self.violations.append(f"t={t}: start bit low {low:.0f} us")
        elif t > self.ack_until:
            if not (400 <= low <= 800 or 1300 <= low <= 1700):
                self.violations.append(f"t={t}: bit low {low:.0f} us")

    def _sample(self, t, lvl):
        if self.rx.get("in_start"):
            return
        if self.bit < 8:
            self.byte = (self.byte << 1) | lvl
        elif self.bit == 8:
            self.rx["eoms"].append(lvl)
            self.rx["bytes"].append(self.byte)
            if len(self.rx["bytes"]) == 1:
                self.rx["hdr"] = self.byte
        else:
            self.rx["acks"].append(lvl)
        self.bit += 1
        if self.bit == 10:
            self.bit, self.byte = 0, 0
            if self.rx["eoms"][-1]:
                self._end_rx()

    def _end_rx(self):
        if self.rx is not None and self.rx["bytes"]:
            f = self.rx
            self.frames.append({"start": f["start"], "bytes": f["bytes"], "eoms": f["eoms"], "acks": f["acks"]})
        self.rx = None
        self.fall = None

    def errors(self):
        return [f"cec la {self.la}: {v}" for v in self.violations[:5]]
