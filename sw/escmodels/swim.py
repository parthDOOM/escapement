"""An STM8's single wire interface module (SWIM), the target side, written from ST's UM0470
("STM8 SWIM communication protocol and debug module", rev. 4), not from any firmware.

The SWIM pin is open drain with a pull-up. Off, the target watches for the entry
sequence: the pin low for 16 us, then four pulses at 1 kHz and four at 2 kHz (it detects
the ratio, so any LSI frequency works), ending high. It then turns its HSI on and, after
SYNC_DELAY_US, sends the synchronization frame: 128 SWIM clocks low. The SWIM clock is
HSI / 2 = 8 MHz here (125 ns).

Low speed bit format: 22 SWIM clocks a bit; a 1 is 2 clocks low then 20 high, a 0 is 20
low then 2 high; a receiver counts the low samples, 8 or fewer a 1, 9 or more a 0. Each
packet is a header bit (0 from the host, 1 from the target), the bits MSB first, an XOR
parity bit, and an acknowledge from the receiver (1 = ACK, 0 = NACK).

Host commands, each with its 3-bit code: SRST 000, ROTF 001, WOTF 010. ROTF and WOTF are
followed by N (1-255) and a 24-bit address (@E, @H, @L), each a host packet; ROTF then has
the target send N data packets, each re-sent while the host NACKs it; WOTF has the host send
N data packets. Without SWIM_CSR.SWIM_DM (bit 5) set, ROTF and WOTF reach only the SWIM's
own registers (SWIM_CSR at 0x7F80 here).

The model drives the pin itself when it answers. It records every host packet it decoded
(`host_packets`), the memory it was asked to read (`reads`) and written (`mem`), and
checks every host bit's low and total lengths against the format (`violations`).
"""

from __future__ import annotations

from .board import Device

SWIM_CSR = 0x7F80
CLK_NS = 125.0                         # SWIM clock: HSI / 2 = 8 MHz
SYNC_DELAY_US = 50.0                   # from the end of the entry sequence to the sync frame


class SwimTarget(Device):
    def __init__(self, pin: int, tick_ns: float, mem: dict[int, int] | None = None):
        self.pin, self.tick = pin, tick_ns
        self.mem = dict(mem or {})
        self.mem.setdefault(SWIM_CSR, 0x00)
        self.violations: list[str] = []
        self.host_packets: list[tuple[str, int]] = []
        self.reads: list[tuple[int, int]] = []
        self.state = "off"
        self._lv, self._fall, self._fall_ours = 1, None, False
        self._edges: list[tuple[float, int]] = []   # (us, level) while off
        self._drive: list[tuple[int, int]] = []     # (start tick, end tick) the target holds low
        self._rx: list[int] = []                    # bits of the packet being received
        self._expect = 0                            # bits expected in it (header .. parity)
        self._cmd = None
        self._args: list[int] = []
        self._out: list[int] = []                   # bytes still to send (ROTF)
        self._awaiting_ack = False                  # our data packet sent, the host's ack next
        self.entered_at: float | None = None        # us: the end of the entry sequence

    # ---- time helpers
    def us(self, ticks: float) -> float:
        return ticks * self.tick / 1000.0

    def t_of(self, ns: float) -> int:
        return int(round(ns / self.tick))

    def _v(self, msg: str) -> None:
        if len(self.violations) < 50:
            self.violations.append(msg)

    def _send_bits(self, t: int, bits: list[int]) -> int:
        """Queue our bits from tick t (each 22 clocks); returns the tick after the last."""
        for b in bits:
            low = (2 if b else 20) * CLK_NS
            self._drive.append((t, t + self.t_of(low)))
            t += self.t_of(22 * CLK_NS)
        return t

    def _packet(self, header: int, value: int, n: int) -> list[int]:
        bits = [(value >> (n - 1 - k)) & 1 for k in range(n)]
        return [header] + bits + [sum(bits) & 1]

    # ---- the pin
    def errors(self) -> list[str]:          # (Board.errors() collects them)
        return [f"SWIM: {v}" for v in self.violations[:10]]

    def step(self, t, bus):
        if t < 0:
            return 0, 0
        lv = (bus >> self.pin) & 1
        if self._drive and self._drive[0][1] <= t:   # drop the low intervals that are over
            self._drive = [(a, b) for a, b in self._drive if b > t]
        ours = any(a <= t < b for a, b in self._drive)
        if lv != self._lv:
            if self._lv == 1 and lv == 0:
                self._fall, self._fall_ours = t, ours     # (our own pulses are not host bits)
            elif self._lv == 0 and lv == 1 and self._fall is not None:
                if not self._fall_ours:
                    self._low(t - self._fall, t)
            self._lv = lv
        return ((1 << self.pin) if ours else 0), 0

    def _low(self, ticks: int, t: int) -> None:
        """A low pulse from the host ended at tick t."""
        us = self.us(ticks)
        if self.state == "off":
            self._edges.append((self.us(t), us))
            self._entry(t)
            return
        if us > 64 * CLK_NS / 1000:                 # a communication reset: 64 clocks or more
            self._rx, self._cmd, self._args = [], None, []
            self._out, self._awaiting_ack = [], False # (a read under way is abandoned)
            self._drive = [(a, b) for a, b in self._drive if a <= t]
            self._drive.append((t + self.t_of(1000), t + self.t_of(1000 + 128 * CLK_NS)))
            return
        bit = 1 if us <= 8 * CLK_NS / 1000 + 1e-9 else 0
        nominal = (2 if bit else 20) * CLK_NS / 1000
        if abs(us - nominal) > 0.15:
            self._v(f"t={t}: a host bit low for {us:.3f} us, not {nominal:.3f}")
        self._host_bit(bit, t)

    def _entry(self, t: int) -> None:
        """The entry sequence: a 16 us low, then 4 periods of ~1 ms and 4 of ~0.5 ms."""
        e = self._edges
        lows = [i for i, (_, d) in enumerate(e) if 14 <= d <= 40]
        if not lows or len(e) - lows[-1] < 9:
            return
        k = lows[-1]
        ends = [x for x, _ in e[k + 1:k + 9]]
        starts = [x - d for x, d in e[k + 1:k + 9]]
        per = [b - a for a, b in zip(starts, starts[1:])]
        slow, fast = per[:3], per[4:7]
        if all(800 <= p <= 1200 for p in slow) and all(400 <= p <= 600 for p in fast):
            t0 = t + self.t_of(SYNC_DELAY_US * 1000) + self.t_of(500)    # after the last high half
            self._drive.append((t0, t0 + self.t_of(128 * CLK_NS)))
            self.state, self._rx = "idle", []
            self.entered_at = ends[-1]

    def _host_bit(self, bit: int, t: int) -> None:
        if self._awaiting_ack:                       # the host's ACK to our data packet
            self._awaiting_ack = False
            if bit and self._out:
                self._out.pop(0)
            # the next packet follows the host's acknowledge bit, a whole bit after it fell
            self._next_out(self._fall + self.t_of(22 * CLK_NS))
            return
        self._rx.append(bit)
        if len(self._rx) == 1:
            if bit != 0:
                self._v(f"t={t}: a host header of 1")
                self._rx = []
                return
            self._expect = 5 if self._cmd is None else 10
        if len(self._rx) < self._expect:
            return
        bits, self._rx = self._rx, []
        data = bits[1:-1]
        val = sum(b << (len(data) - 1 - k) for k, b in enumerate(data))
        ok = (sum(data) & 1) == bits[-1]
        t_ack = t + self.t_of(22 * CLK_NS - (2 if bits[-1] else 20) * CLK_NS) + self.t_of(250)
        self._send_bits(t_ack, [1 if ok else 0])
        if not ok:
            self._v(f"t={t}: host parity error")
            return
        if self._cmd is None:
            name = {0: "SRST", 1: "ROTF", 2: "WOTF"}.get(val, f"cmd{val}")
            self.host_packets.append(("cmd", val))
            if val == 0:
                self._cmd = None
            else:
                self._cmd, self._args = name, []
            return
        self.host_packets.append(("byte", val))
        self._args.append(val)
        if len(self._args) == 4:
            n, addr = self._args[0], self._args[1] << 16 | self._args[2] << 8 | self._args[3]
            self._addr, self._n = addr, n
            if self._cmd == "ROTF":
                if not self._allowed(addr, n):
                    self._v(f"ROTF of 0x{addr:06X} without SWIM_DM")
                    self._cmd = None
                    return
                self._out = [self.mem.get(addr + k, 0) for k in range(n)]
                for k in range(n):
                    self.reads.append((addr + k, self.mem.get(addr + k, 0)))
                self._cmd = None
                self._next_out(t_ack + self.t_of(22 * CLK_NS))
        elif len(self._args) > 4 and self._cmd == "WOTF":
            k = len(self._args) - 5
            if self._allowed(self._addr + k, 1):
                self.mem[self._addr + k] = val
            else:
                self._v(f"WOTF of 0x{self._addr + k:06X} without SWIM_DM")
            if k + 1 == self._n:
                self._cmd = None

    def _allowed(self, addr: int, n: int) -> bool:
        if self.mem.get(SWIM_CSR, 0) & 0x20:
            return True
        return all(0x7F80 <= a <= 0x7F8F for a in range(addr, addr + n))

    def _next_out(self, t: int) -> None:
        if not self._out:
            return
        start = t + self.t_of(500)
        self._send_bits(start, self._packet(1, self._out[0], 8))
        self._awaiting_ack = True
