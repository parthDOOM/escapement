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


def frame_bits(ident: int, data: bytes, rtr: int = 0, ext: bool = False, dlc: int | None = None) -> list[int]:
    """A classic data or remote frame from SOF through the CRC sequence, unstuffed: 2.0A
    (11-bit identifier), or 2.0B with ext (29-bit: base identifier, SRR, IDE, extension,
    RTR, r1, r0). A remote frame carries no data; its DLC is `dlc`, or len(data)."""
    dlc = len(data) if dlc is None else dlc
    if ext:
        bits = [0] + [(ident >> (28 - i)) & 1 for i in range(11)] + [1, 1]
        bits += [(ident >> (17 - i)) & 1 for i in range(18)] + [rtr, 0, 0]
    else:
        bits = [0] + [(ident >> (10 - i)) & 1 for i in range(11)] + [rtr, 0, 0]
    bits += [(dlc >> (3 - i)) & 1 for i in range(4)]
    for byte in (b"" if rtr else data):
        bits += [(byte >> (7 - i)) & 1 for i in range(8)]
    crc = crc15(bits)
    return bits + [(crc >> (14 - i)) & 1 for i in range(15)]


def stuff_can(bits: list[int]) -> list[int]:
    """After five equal bits the complement is inserted; it starts the next run."""
    out: list[int] = []
    last, run = None, 0
    for b in bits:
        out.append(b)
        run = run + 1 if b == last else 1
        last = b
        if run == 5:
            out.append(1 - b)
            last, run = 1 - b, 1
    return out


def encode_frame(ident: int, data: bytes, ack: bool = True, rtr: int = 0, ext: bool = False,
                 dlc: int | None = None, bad_crc: bool = False) -> list[int]:
    """Bus levels of a whole frame: stuffed SOF..CRC, CRC delimiter, ACK slot (dominant if
    a receiver acknowledged), ACK delimiter, seven recessive bits of end of frame.
    bad_crc flips the last CRC bit before stuffing."""
    bits = frame_bits(ident, data, rtr, ext, dlc)
    if bad_crc:
        bits[-1] ^= 1
    return stuff_can(bits) + [1, 0 if ack else 1, 1] + [1] * 7


class CanSource(Device):
    """Other nodes on the bus: plays `frames`, a list of (start tick, levels), one level
    per bit of `period` ticks (fractional for an offset clock), on `pin` as an
    open-drain bus (dominant 0 pulls it low, recessive leaves the pull-up)."""

    def __init__(self, pin: int, frames: list[tuple[int, list[int]]], period: float):
        self.pin, self.period = pin, period
        self.frames = sorted(frames)

    def step(self, t, bus):
        for t0, lv in self.frames:
            if t < t0:
                break
            k = int((t - t0) / self.period)
            if k < len(lv):
                return ((1 << self.pin) if lv[k] == 0 else 0), 0
        return 0, 0


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
