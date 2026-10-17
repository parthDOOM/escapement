"""Classic CAN 2.0A bus with one receiving node, for checking a firmware transmitter.

The chip drives TXD (push-pull, 1 = recessive) into a modelled transceiver; the bus is
dominant (0) when TXD is 0 or when this receiver drives it (ACK). The bus level is
returned on RXD. The receiver hard-synchronises on the SOF edge after the bus has been
idle (11 recessive bits), samples each bit at 70%, removes stuff bits (and flags a
stuff error after six identical bits), checks the CRC-15 (ISO 11898-1 polynomial
0x4599), checks the fixed-form delimiters, and drives the ACK slot dominant when the
CRC matched. Decoded frames are kept in .frames.
"""

from __future__ import annotations

from .board import Device


def crc15(bits: list[int]) -> int:
    crc = 0
    for b in bits:
        nxt = b ^ ((crc >> 14) & 1)
        crc = (crc << 1) & 0x7FFF
        if nxt:
            crc ^= 0x4599
    return crc


class CanBus(Device):
    def __init__(self, tx: int, rx: int, bit_ticks: int, sample: float = 0.7, ack: bool = True):
        self.tx, self.rx, self.bit = tx, rx, bit_ticks
        self.sp = int(round(sample * bit_ticks))
        self.ack = ack
        self.frames: list[dict] = []
        self.violations: list[str] = []
        self._idle_since = 0
        self._sof = None            # tick of the SOF edge of the frame being received
        self._raw: list[int] = []   # sampled bits (with stuff bits)
        self._bits: list[int] = []  # destuffed bits
        self._run_val, self._run = None, 0
        self._stuffing = True
        self._ack_from = self._ack_to = -1
        self._prev = 1
        self._k = 0

    def step(self, t, bus):
        txd = (bus >> self.tx) & 1
        dominant = (txd == 0) or (self._ack_from <= t < self._ack_to)
        level = 0 if dominant else 1
        if self._sof is None:
            if level == 0 and self._prev == 1 and t - self._idle_since >= 11 * self.bit:
                self._start(t)
            elif level == 0:
                self._idle_since = t
        else:
            if t == self._sof + self._k * self.bit + self.sp:
                self._sample(t, level)
        if level == 0 and self._sof is None:
            self._idle_since = t
        self._prev = level
        return ((1 << self.rx) if dominant else 0), 0

    # ------------------------------------------------------------ receiver
    def _start(self, t):
        self._sof, self._k = t, 0
        self._raw, self._bits = [], []
        self._run_val, self._run, self._stuffing = None, 0, True
        self._crc_done = False

    def _sample(self, t, b):
        self._k += 1
        self._raw.append(b)
        if self._stuffing:
            if self._run == 5:
                if b == self._run_val:
                    self.violations.append(f"t={t}: stuff error (six identical bits)")
                    self._sof = None
                    return
                self._run_val, self._run = b, 1       # stuff bit: dropped
                if self._crc_done:                    # (a stuff bit may follow the CRC)
                    self._stuffing = False
                return
            if self._crc_done:
                self._stuffing = False                # no stuff bit: this is the delimiter
            elif b == self._run_val:
                self._run += 1
            else:
                self._run_val, self._run = b, 1
        self._bits.append(b)
        self._parse(t)

    def _field(self, a, n):
        return int("".join(map(str, self._bits[a:a + n])), 2)

    def _parse(self, t):
        bits = self._bits
        n = len(bits)
        if n < 19:
            return
        dlc = min(self._field(15, 4), 8)
        end_data = 19 + 8 * dlc
        end_crc = end_data + 15
        if n == end_crc:
            self._crc_done = True                       # delimiters are fixed form
            self._crc_ok = crc15(bits[:end_data]) == self._field(end_data, 15)
        elif n == end_crc + 1:
            if bits[-1] != 1:
                self.violations.append(f"t={t}: CRC delimiter not recessive")
            # the ACK slot is the bit after the CRC delimiter: drive it if the CRC matched
            if self.ack and self._crc_ok:
                start = self._sof + self._k * self.bit
                self._ack_from, self._ack_to = start, start + self.bit
        elif n == end_crc + 3 and bits[-1] != 1:
            self.violations.append(f"t={t}: ACK delimiter not recessive")
        elif n == end_crc + 10:
            if any(b != 1 for b in bits[end_crc + 3:]):
                self.violations.append(f"t={t}: end of frame not recessive")
            data = [self._field(19 + 8 * i, 8) for i in range(dlc)]
            self.frames.append({"id": self._field(1, 11), "rtr": bits[12], "ide": bits[13],
                                "dlc": self._field(15, 4), "data": data,
                                "crc_ok": self._crc_ok, "ack_seen": bits[end_crc + 1] == 0,
                                "raw_bits": len(self._raw)})
            self._sof = None
            self._idle_since = t

    def errors(self):
        return list(self.violations[:5])
