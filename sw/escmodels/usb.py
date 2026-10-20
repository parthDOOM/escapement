"""USB low-speed signalling, an independent model for testing the line unit.

Written from the USB 2.0 specification (7.1 signalling, 8.3 packet fields, 8.3.5 CRCs), not
from the line unit's code or semantics: packets are built whole, the CRCs use the reflected
table-free form of the CRC catalogue (CRC-5/USB, CRC-16/USB), and the decoder works on run
lengths of line states rather than with a bit clock.

Line states: J (D- high, D+ low, also the idle bus), K, and SE0. A low-speed bit lasts
`period` ticks; at 24 MHz that is 16.

`UsbBus` is a board model for the ISS (D+ on uio[0], D- on uio[1]): the chip's drive wins
where it drives, the host's scheduled packets come next, and the pull-ups make an undriven
bus J.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

J, K, SE0 = "J", "K", "0"

PID = {"OUT": 0xE1, "IN": 0x69, "SOF": 0xA5, "SETUP": 0x2D, "DATA0": 0xC3, "DATA1": 0x4B,
       "ACK": 0xD2, "NAK": 0x5A, "STALL": 0x1E}
PID_NAME = {v: k for k, v in PID.items()}


# ----------------------------------------------------------------------------- CRCs


def crc5_usb(value: int, nbits: int = 11) -> int:
    """CRC-5/USB over the low `nbits` bits of value, LSB first (catalogue: refin, refout,
    poly 0x05 reflected 0x14, init 0x1F, xorout 0x1F). Returns the 5-bit field value."""
    crc = 0x1F
    for i in range(nbits):
        if (crc ^ (value >> i)) & 1:
            crc = (crc >> 1) ^ 0x14
        else:
            crc >>= 1
    return crc ^ 0x1F


def crc16_usb(data: bytes) -> int:
    """CRC-16/USB (catalogue: poly 0x8005 reflected 0xA001, init 0xFFFF, xorout 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


# ----------------------------------------------------------------------------- packets


def lsb_bits(value: int, n: int) -> list[int]:
    return [(value >> i) & 1 for i in range(n)]


SYNC_BITS = [0] * 7 + [1]


def token_field(addr: int, endp: int) -> int:
    """The 16 bits after a token PID: ADDR (7), ENDP (4), CRC5 (5), sent LSB first."""
    v = (addr & 0x7F) | ((endp & 0xF) << 7)
    return v | (crc5_usb(v) << 11)


def token(pid: str, addr: int, endp: int) -> list[int]:
    return SYNC_BITS + lsb_bits(PID[pid], 8) + lsb_bits(token_field(addr, endp), 16)


def data_packet(pid: str, data: bytes) -> list[int]:
    crc = crc16_usb(data)
    body = bytes(data) + bytes((crc & 0xFF, crc >> 8))
    return SYNC_BITS + lsb_bits(PID[pid], 8) + [b for byte in body for b in lsb_bits(byte, 8)]


def handshake(pid: str) -> list[int]:
    return SYNC_BITS + lsb_bits(PID[pid], 8)


def stuff(bits: list[int]) -> list[int]:
    """7.1.9: a 0 after every six consecutive 1s, including at the very end."""
    out, ones = [], 0
    for b in bits:
        out.append(b)
        ones = ones + 1 if b else 0
        if ones == 6:
            out.append(0)
            ones = 0
    return out


def nrzi(bits: list[int], start: str = J) -> list[str]:
    """7.1.8: a 0 is a change of state, a 1 is no change."""
    level, out = start, []
    for b in bits:
        if b == 0:
            level = K if level == J else J
        out.append(level)
    return out


def line(bits: list[int], eop_bits: int = 2) -> list[str]:
    """The line states of a whole packet: NRZI of the stuffed bits, then the EOP."""
    return nrzi(stuff(bits)) + [SE0] * eop_bits + [J]


# ----------------------------------------------------------------------------- decoding


@dataclass
class Packet:
    start: int                     # tick of the first K (J->K transition)
    end: int                       # tick the line left SE0 (SE0->J), or -1
    bits: list[int]                # destuffed bits after SYNC
    error: str = ""                # "", "stuff", "sync", "short"

    @property
    def pid(self) -> int:
        return sum(b << i for i, b in enumerate(self.bits[:8])) if len(self.bits) >= 8 else -1

    @property
    def body(self) -> bytes:
        """Whole bytes after the PID (a trailing partial byte, dribble, is dropped)."""
        n = (len(self.bits) - 8) // 8
        return bytes(sum(self.bits[8 + 8 * i + j] << j for j in range(8)) for i in range(n))

    @property
    def name(self) -> str:
        return PID_NAME.get(self.pid, f"0x{self.pid:02x}")

    def crc16_ok(self) -> bool:
        b = self.body
        return len(b) >= 2 and crc16_usb(b[:-2]) == (b[-2] | (b[-1] << 8))

    def token(self) -> tuple[int, int, bool]:
        """(addr, endp, crc5 ok) of a token packet."""
        f = sum(b << i for i, b in enumerate(self.bits[8:24]))
        v = f & 0x7FF
        return v & 0x7F, v >> 7, (f >> 11) == crc5_usb(v)


def state(dp: int, dm: int) -> str:
    if dp == 0 and dm == 0:
        return SE0
    if dm and not dp:
        return J
    if dp and not dm:
        return K
    return "1"                     # SE1, never valid


def decode(states: list[str], period: float) -> list[Packet]:
    """Packets in a per-tick list of line states, by run lengths: each run of one state
    lasts round(length / period) bits; the first is the J->K that starts the SYNC."""
    runs: list[tuple[str, int, int]] = []          # (state, start, length)
    for t, s in enumerate(states):
        if runs and runs[-1][0] == s:
            st, a, n = runs[-1]
            runs[-1] = (st, a, n + 1)
        else:
            runs.append((s, t, 1))
    packets: list[Packet] = []
    i = 0
    while i < len(runs):
        st, a, n = runs[i]
        if st != K or i == 0 or runs[i - 1][0] != J:
            i += 1
            continue
        raw: list[int] = []
        level = J
        end = -1
        while i < len(runs):
            st, a2, n2 = runs[i]
            if st == SE0:
                # SE0 runs shorter than half a bit are transition skew (7.1.4), not an EOP
                if n2 < period / 2:
                    i += 1
                    continue
                end = a2 + n2 if (i + 1 < len(runs)) else -1
                i += 1
                break
            if st not in (J, K):
                break
            nb = max(1, round(n2 / period))
            raw += [0] + [1] * (nb - 1) if st != level else [1] * nb
            level = st
            i += 1
        # destuff and strip SYNC
        bits, ones, err = [], 0, ""
        skip_next = False
        for b in raw:
            if skip_next:
                skip_next = False
                if b != 0:
                    err = "stuff"
                ones = 0
                continue
            bits.append(b)
            ones = ones + 1 if b else 0
            if ones == 6:
                skip_next = True
        try:
            k = bits.index(1)
        except ValueError:
            packets.append(Packet(a, end, [], "sync"))
            continue
        packets.append(Packet(a, end, bits[k + 1:], err))
    return packets


# ----------------------------------------------------------------------------- the bus


@dataclass
class UsbBus:
    """D+ = uio[DP], D- = uio[DM]. The host drives scheduled line states; the chip's
    output enables win; an undriven bus is J (device pull-up on D-, host pull-down on D+)."""
    period: int = 16
    dp: int = 0
    dm: int = 1
    host: dict[int, str] = field(default_factory=dict)     # tick -> state driven
    trace: list[str] = field(default_factory=list)         # bus state per tick

    def send(self, t0: int, states: list[str], period: Optional[float] = None,
             jitter: Optional[list[int]] = None) -> int:
        """Drive `states`, one per bit, from tick t0; bit i starts at
        t0 + round(i * period) (+ jitter[i]). Returns the tick after the last state."""
        p = self.period if period is None else period
        t = t0
        for i, s in enumerate(states):
            a = t0 + round(i * p) + (jitter[i] if jitter else 0)
            b = t0 + round((i + 1) * p) + (jitter[i + 1] if jitter and i + 1 < len(jitter) else 0)
            for u in range(a, b):
                self.host[u] = s
            t = b
        return t

    def drive_se0(self, t0: int, ticks: int) -> int:
        for u in range(t0, t0 + ticks):
            self.host[u] = SE0
        return t0 + ticks

    def __call__(self, t: int, uio_out: int, uio_oe: int) -> int:
        bus_dp, bus_dm = 0, 1                                  # pull-ups: J
        h = self.host.get(t)
        if h is not None:
            bus_dp, bus_dm = {J: (0, 1), K: (1, 0), SE0: (0, 0)}[h]
        if (uio_oe >> self.dp) & 1:
            bus_dp = (uio_out >> self.dp) & 1
        if (uio_oe >> self.dm) & 1:
            bus_dm = (uio_out >> self.dm) & 1
        if t >= 0:
            while len(self.trace) < t:
                self.trace.append(J)
            self.trace.append(state(bus_dp, bus_dm))
        v = 0xFF & ~((1 << self.dp) | (1 << self.dm))
        return v | (bus_dp << self.dp) | (bus_dm << self.dm)


def chip_states(pins: list[dict], dp: int = 0, dm: int = 1) -> list[str]:
    """The bus states the chip alone produces (released pins pulled to J)."""
    out = []
    for rec in pins:
        o, e = rec["uio_out"], rec["uio_oe"]
        vdp = (o >> dp) & 1 if (e >> dp) & 1 else 0
        vdm = (o >> dm) & 1 if (e >> dm) & 1 else 1
        out.append(state(vdp, vdm))
    return out


# ----------------------------------------------------------------------------- a host


class UsbError(Exception):
    pass


@dataclass
class Reply:
    """A device packet as the host saw it."""
    packet: Packet
    turnaround: int                 # ticks from the host EOP's SE0->J to the device's J->K


class UsbHost:
    """A low-speed host controller as a board model (D+ = uio[dp], D- = uio[dm]).

    A script, a generator taking this host, drives the bus. It yields:
      ("idle", n)        resume n ticks later
      ("send", states)   drive line states from the next tick, one per bit; resume when done
      ("listen", n)      resume with the device's next packet (a Reply), or None if none
                         starts within n ticks
    Helpers below (`in_txn`, `control_read`, ...) are written as such scripts, so tests
    compose them with `yield from`. Every device packet is checked here: its EOP (SE0 of
    1.25-1.5 us, then J), its stuffing, and the turnaround (2 to 6.5 bit times, USB 2.0
    7.1.18.1). The device may drive the bus only to answer: from a listen to the end of
    its packet's EOP. Rule breaks are collected in `errors`; every answer is in `replies`.

    `rate` is the host's own bit time in ticks (default `period`): USB allows a low-speed
    host 1.5% either way (7.1.11).
    """

    def __init__(self, period: int = 16, dp: int = 0, dm: int = 1, gap: int = 2,
                 rate: Optional[float] = None):
        self.p, self.dp, self.dm = period, dp, dm
        self.rate = float(period if rate is None else rate)
        self.gap = gap                     # bit times of J the host leaves before sending
        self.drive: dict[int, str] = {}
        self.states: list[str] = []
        self.errors: list[str] = []
        self.replies: list[Reply] = []
        self.log: list[tuple[int, str, str]] = []
        self._dev_end: Optional[int] = None
        self._rogue = False
        self.done = False
        self.result = None
        self._gen = None
        self._wake: Optional[int] = None
        self._listen: Optional[int] = None  # deadline for a device packet to start
        self._sop: Optional[int] = None
        self._se0_from: Optional[int] = None
        self._host_eop_j: Optional[int] = None
        self._t = -1

    # ------------------------------------------------------------ board interface

    def __call__(self, t: int, uio_out: int, uio_oe: int) -> int:
        bus_dp, bus_dm = 0, 1
        h = self.drive.pop(t, None)
        if h is not None:
            bus_dp, bus_dm = {J: (0, 1), K: (1, 0), SE0: (0, 0)}[h]
        chip = False
        if (uio_oe >> self.dp) & 1:
            bus_dp, chip = (uio_out >> self.dp) & 1, True
        if (uio_oe >> self.dm) & 1:
            bus_dm, chip = (uio_out >> self.dm) & 1, True
        if chip and h is not None:
            self.errors.append(f"t={t}: host and device drive the bus together")
        rogue = (chip and self._listen is None and t >= 0
                 and (self._dev_end is None or t > self._dev_end + self.p + 2))
        if rogue and not self._rogue:                      # once per incident
            self.errors.append(f"t={t}: the device drives the bus unprompted")
        self._rogue = rogue
        if t >= 0:
            self._t = t
            st = state(bus_dp, bus_dm)
            self.states.append(st)
            self._observe(t, st)
            if self._gen is not None and self._wake is not None and t >= self._wake:
                self._wake = None
                self._resume(None)
        v = 0xFF & ~((1 << self.dp) | (1 << self.dm))
        return v | (bus_dp << self.dp) | (bus_dm << self.dm)

    def start(self, script) -> None:
        self._gen = script(self)
        self._resume(None)

    # ------------------------------------------------------------ the script engine

    def _resume(self, value) -> None:
        try:
            cmd = self._gen.send(value)
        except StopIteration as stop:
            self.done, self.result, self._gen = True, stop.value, None
            return
        kind, arg = cmd
        t = self._t
        if kind == "idle":
            self._wake = t + max(1, arg)
        elif kind == "send":
            edge = [t + 1 + round(i * self.rate) for i in range(len(arg) + 1)]
            for i, s in enumerate(arg):
                for u in range(edge[i], edge[i + 1]):
                    self.drive[u] = s
            n = len(arg)
            if n >= 2 and arg[-1] == J and arg[-2] == SE0:
                self._host_eop_j = edge[n - 1]
            self._wake = edge[n]
        elif kind == "listen":
            self._listen = t + arg
            self._sop = None
        else:
            raise ValueError(kind)

    def _observe(self, t: int, st: str) -> None:
        prev = self.states[t - 1] if t >= 1 else J
        if self._listen is None:
            return
        if self._sop is None:
            if prev == J and st == K:
                self._sop = t
            elif t >= self._listen:
                self._listen = None
                self._resume(None)
            return
        if st == SE0 and prev != SE0:
            self._se0_from = t
        if prev == SE0 and st == J:
            # the device's EOP: its SE0 must last 1.25 to 1.5 us (30 to 36 ticks at 24 MHz)
            n = t - self._se0_from
            if not (self.p * 1.875 - 1 <= n <= self.p * 2.25 + 1):
                self.errors.append(f"t={t}: device EOP SE0 lasted {n} ticks")
            seg = self.states[self._sop - 1:t + 1]
            pk = decode(seg, self.p)
            if len(pk) != 1:
                self.errors.append(f"t={t}: device packet not decodable")
                pkt = Packet(self._sop, t, [], "short")
            else:
                pkt = pk[0]
                pkt.start, pkt.end = self._sop, t
            ta = self._sop - (self._host_eop_j if self._host_eop_j is not None else self._sop)
            if self._host_eop_j is not None and not 2 * self.p <= ta <= 6.5 * self.p:
                self.errors.append(f"t={t}: turnaround {ta} ticks ({ta / self.p:.2f} bit times)")
            if pkt.error:
                self.errors.append(f"t={t}: device packet error {pkt.error}")
            self.log.append((self._sop, "dev", pkt.name))
            self._listen = None
            self._dev_end = t
            self.replies.append(Reply(pkt, ta))
            self._resume(self.replies[-1])


# ----------------------------------------------------------------------------- host scripts


def send(h: UsbHost, bits: list[int], name: str):
    gap = h.gap() if callable(h.gap) else h.gap          # bit times; a callable draws each one
    yield ("idle", round(gap * h.p))
    h.log.append((h._t + 1, "host", name))
    yield ("send", line(bits))


def listen(h: UsbHost, bits: float = 18):
    """The device's reply: USB hosts time out after 16 to 18 bit times."""
    r = yield ("listen", round(bits * h.p) + 2 * h.p)
    return r


def in_txn(h: UsbHost, addr: int, ep: int, ack: bool = True):
    """IN transaction: the device's DATA (ACKed here unless `ack` is False, as when the
    host's ACK is lost), NAK, STALL, or None."""
    yield from send(h, token("IN", addr, ep), f"IN {addr}.{ep}")
    r = yield from listen(h)
    if r is not None and r.packet.name in ("DATA0", "DATA1"):
        if not r.packet.crc16_ok():
            h.errors.append(f"t={h._t}: device DATA with a bad CRC")
            return r
        if ack:
            yield from send(h, handshake("ACK"), "ACK")
    return r


def setup_txn(h: UsbHost, addr: int, setup: bytes):
    """SETUP + DATA0; the device must ACK. Returns its handshake (or None)."""
    yield from send(h, token("SETUP", addr, 0), f"SETUP {addr}")
    yield from send(h, data_packet("DATA0", setup), "DATA0")
    r = yield from listen(h)
    return r


def out_txn(h: UsbHost, addr: int, ep: int, data: bytes, pid: str):
    yield from send(h, token("OUT", addr, ep), f"OUT {addr}.{ep}")
    yield from send(h, data_packet(pid, data), pid)
    r = yield from listen(h)
    return r


def control(h: UsbHost, addr: int, setup: bytes, out: Optional[bytes] = None,
            retries: int = 20):
    """A control transfer. Returns the data stage's bytes (device to host), b"" for the
    others, or "STALL". Data toggles and lengths are checked (USB 2.0 8.5.3)."""
    for _ in range(3):
        r = yield from setup_txn(h, addr, setup)
        if r is not None and r.packet.name == "ACK":
            break
    else:
        raise UsbError(f"SETUP {setup.hex()} not acknowledged")
    wlength = setup[6] | (setup[7] << 8)
    to_host = bool(setup[0] & 0x80)
    data = b""
    if to_host and wlength:
        toggle = "DATA1"
        while len(data) < wlength:
            for _ in range(retries):
                r = yield from in_txn(h, addr, 0)
                if r is not None and r.packet.name != "NAK":
                    break
            else:
                raise UsbError("data stage: too many NAKs or timeouts")
            if r.packet.name == "STALL":
                return "STALL"
            if r.packet.name != toggle:
                raise UsbError(f"data stage: {r.packet.name}, expected {toggle}")
            chunk = r.packet.body[:-2]
            if len(chunk) > 8 or len(data) + len(chunk) > wlength:
                raise UsbError("data stage: babble")
            data += chunk
            toggle = "DATA0" if toggle == "DATA1" else "DATA1"
            if len(chunk) < 8:
                break
        r = yield from out_txn(h, addr, 0, b"", "DATA1")            # status stage
        if r is None or r.packet.name != "ACK":
            raise UsbError(f"status stage: {r.packet.name if r else None}")
        return data
    if out:
        r = yield from out_txn(h, addr, 0, out, "DATA1")
        if r is None or r.packet.name != "ACK":
            raise UsbError("OUT data stage not acknowledged")
    for _ in range(retries):                                       # status stage: IN
        r = yield from in_txn(h, addr, 0)
        if r is not None and r.packet.name != "NAK":
            break
    else:
        raise UsbError("status stage: too many NAKs")
    if r.packet.name == "STALL":
        return "STALL"
    if r.packet.name != "DATA1" or r.packet.body != b"\0\0":
        raise UsbError(f"status stage: {r.packet.name} {r.packet.body.hex()}")
    return b""


def setup_bytes(bm: int, req: int, value: int, index: int, length: int) -> bytes:
    return bytes((bm, req, value & 0xFF, value >> 8, index & 0xFF, index >> 8,
                  length & 0xFF, length >> 8))


def bus_reset(h: UsbHost, ticks: int):
    yield ("idle", h.p)
    for _ in range(ticks):
        h.drive[h._t + 1 + _] = SE0
    yield ("idle", ticks + 1)
    yield ("idle", 2 * h.p)
