"""A Nordic nRF24L01+ as a primary transmitter, for testing a driver program (nRF24L01+ Product
Specification 1.0: 6.1 operational modes and Table 16, 7.4-7.7 Enhanced ShockBurst, 8.3 SPI
commands (Table 20) and timing (Table 22), 9.1 register map).

Written from the specification, not from any firmware. SPI: mode 0, a command starts on CSN
falling; the first byte is the command word, and STATUS is shifted out on MISO while it is
shifted in; data bytes LSByte first, each MSBit first. Commands: R_REGISTER 000AAAAA,
W_REGISTER 001AAAAA (only in power down or standby: not while transmitting), W_TX_PAYLOAD
1010 0000 (1-32 bytes into a 3-deep TX FIFO), FLUSH_TX 1110 0001, NOP 1111 1111.
CONFIG (00h) PWR_UP starts the crystal: standby Tpd2stby (1.5 ms) later. A CE pulse of more
than 10 us (Thce) in PTX mode with a payload in the FIFO starts a transmission Tstby2a
(130 us) later, on air TOA = (8 (1 + AW + payload + CRC) + 9) / rate. With auto-ack on pipe 0
the PTX listens for the ACK (Tstby2a + TACK); an ACK sets TX_DS and drops the payload; none
retransmits ARD after the end of the transmission, up to ARC times, then sets MAX_RT, the
payload staying in the FIFO. IRQ is low while an unmasked STATUS bit 6-4 is set (write 1 to
clear). The link is a test knob: `acks` lists, attempt by attempt, whether an ACK comes back.
"""

from __future__ import annotations

from .board import Device

RATES = {0b00: 1e6, 0b01: 2e6, 0b10: 250e3}         # [RF_DR_LOW, RF_DR_HIGH] -> bit/s
RESET = {0x00: 0x08, 0x01: 0x3F, 0x02: 0x03, 0x03: 0x03, 0x04: 0x03, 0x05: 0x02, 0x06: 0x0E,
         0x07: 0x0E, 0x11: 0x00, 0x17: 0x11}


class Nrf24Ptx(Device):
    def __init__(self, sck, mosi, miso, csn, ce, irq, tick_ns, acks=None):
        self.p = dict(sck=sck, mosi=mosi, miso=miso, csn=csn, ce=ce, irq=irq)
        self.tick = tick_ns
        self.reg = dict(RESET)
        self.reg[0x0A] = bytes([0xE7] * 5)
        self.reg[0x10] = bytes([0xE7] * 5)
        self.fifo: list[bytes] = []
        self.acks = list(acks) if acks is not None else None   # None: every attempt is ACKed
        self.sent: list[dict] = []                             # every attempt on air
        self.commands: list[tuple[int, bytes]] = []
        self.violations: list[str] = []
        self._first = True
        self._lv = {}
        self._t_pu = None
        self._t_ce = None
        self._t_csn_hi = None
        self._busy_until = -1.0                                # us: transmitting
        self._events: list[tuple[float, str]] = []
        self._rx = []
        self._bits = 0
        self._byte = 0
        self._out: list[int] = []
        self._ob = 0
        self._obit = 0
        self._miso = 1

    def _us(self, t):
        return t * self.tick / 1000.0

    def _status(self):
        return (self.reg[0x07] & 0x70) | 0x0E | (1 if len(self.fifo) >= 3 else 0)

    def step(self, t, bus):
        lv = {k: (bus >> v) & 1 for k, v in self.p.items()}
        us = self._us(t)
        if self._first:
            self._first = False
            self._lv = lv
        while self._events and self._events[0][0] <= us:
            self._event(*self._events.pop(0))
        if lv["csn"] != self._lv["csn"]:
            if lv["csn"] == 0:
                if self._t_csn_hi is not None and (t - self._t_csn_hi) * self.tick < 50:
                    self.violations.append(f"t={t}: CSN high {(t - self._t_csn_hi) * self.tick:.0f} ns (Tcwh 50)")
                if self._t_ce is not None and us - self._t_ce < 4:
                    self.violations.append(f"t={t}: CSN low {us - self._t_ce:.1f} us after CE rose (Tpece2csn 4)")
                self._rx, self._bits, self._byte = [], 0, 0
                self._out, self._obit = [self._status()], 0
            else:
                self._t_csn_hi = t
                self._end(us)
        if lv["ce"] != self._lv["ce"]:
            if lv["ce"]:
                self._t_ce = us
            else:
                self._ce_pulse(us)
        if lv["csn"] == 0:
            if lv["sck"] and not self._lv["sck"]:
                self._byte = self._byte << 1 | lv["mosi"]
                self._bits += 1
                if self._bits == 8:
                    self._rx.append(self._byte)
                    self._bits, self._byte = 0, 0
                    self._more()
            elif not lv["sck"] and self._lv["sck"]:
                if self._obit == 0 and self._out:
                    self._ob, self._obit = self._out.pop(0), 8
                if self._obit:
                    self._obit -= 1
                    self._miso = (self._ob >> self._obit) & 1
            if self._bits == 0 and len(self._rx) == 0 and self._obit == 0 and self._out:
                self._ob, self._obit = self._out.pop(0), 8      # STATUS from CSN falling
                self._obit -= 1
                self._miso = (self._ob >> self._obit) & 1
        else:
            self._miso = 1
        self._lv = lv
        irq = (self.reg[0x07] & 0x70 & ~self.reg[0x00]) != 0
        low = (0 if self._miso else 1 << self.p["miso"]) | ((1 << self.p["irq"]) if irq else 0)
        return low, 0

    def _more(self):
        c = self._rx[0]
        if len(self._rx) == 1 and c >> 5 == 0:                # R_REGISTER: its bytes, LSByte first
            v = self.reg.get(c & 0x1F, 0)
            self._out += list(v) if isinstance(v, bytes) else [v]

    def _end(self, us):
        if not self._rx:
            return
        c, data = self._rx[0], bytes(self._rx[1:])
        self.commands.append((c, data))
        if self._bits:
            self.violations.append(f"{us:.1f} us: CSN rose after {self._bits} bits of a byte")
        if c >> 5 == 1:                                        # W_REGISTER
            if us < self._busy_until:
                self.violations.append(f"{us:.1f} us: W_REGISTER {c & 0x1F:02X}h while transmitting")
            a = c & 0x1F
            if a in (0x0A, 0x10):
                old = bytearray(self.reg[a])
                old[:len(data)] = data[:5]
                self.reg[a] = bytes(old)
            elif a == 0x07 and data:
                self.reg[0x07] &= ~(data[0] & 0x70)
            elif data:
                if a == 0x00 and data[0] & 2 and not self.reg[0] & 2:
                    self._t_pu = us
                self.reg[a] = data[0]
        elif c == 0xA0:
            if not 1 <= len(data) <= 32:
                self.violations.append(f"{us:.1f} us: W_TX_PAYLOAD of {len(data)} bytes")
            elif len(self.fifo) >= 3:
                self.violations.append(f"{us:.1f} us: W_TX_PAYLOAD into a full TX FIFO")
            else:
                self.fifo.append(data)
        elif c == 0xE1:
            self.fifo.clear()
        elif c in (0xFF,) or c >> 5 == 0:
            pass
        else:
            self.violations.append(f"{us:.1f} us: command {c:02X}h not modelled")

    def _ce_pulse(self, us):
        if self._t_ce is None:                             # CE fell from its power-up level
            return
        width = us - self._t_ce
        cfg = self.reg[0x00]
        if not cfg & 2 or cfg & 1 or not self.fifo:
            return
        if width < 10:
            self.violations.append(f"{us:.1f} us: CE high {width:.1f} us (Thce 10)")
            return
        if self._t_pu is None or self._t_ce - self._t_pu < 1500:
            self.violations.append(f"{self._t_ce:.1f} us: CE before Tpd2stby (1.5 ms) after PWR_UP")
        if us < self._busy_until:
            return
        if self.reg[0x07] & 0x10:
            self.violations.append(f"{us:.1f} us: CE with MAX_RT set (no transmission until cleared)")
            return
        self._attempt(self._t_ce + 130, 0)

    def _toa(self, payload):
        rate = RATES[(self.reg[0x06] >> 4 & 2) | (self.reg[0x06] >> 3 & 1)]
        aw = (self.reg[0x03] & 3) + 2
        crc = (2 if self.reg[0x00] & 4 else 1) if self.reg[0x00] & 8 or self.reg[0x01] else 0
        return (8 * (1 + aw + payload + crc) + 9) / rate * 1e6, (8 * (1 + aw + crc) + 9) / rate * 1e6

    def _attempt(self, start, k):
        pl = self.fifo[0]
        toa, tack = self._toa(len(pl))
        self.sent.append(dict(start=start, payload=pl, ch=self.reg[0x05], addr=self.reg[0x10], attempt=k))
        end = start + toa
        aa = self.reg[0x01] & 1
        if not aa:
            self._busy_until = end
            self._events.append((end, "sent"))
        else:
            ok = True if self.acks is None else (self.acks.pop(0) if self.acks else False)
            done = end + 130 + tack
            self._busy_until = done
            if ok:
                self._events.append((done, "sent"))
            elif k < (self.reg[0x04] & 0xF):                 # ARD: from the end of this
                ard = 250 * ((self.reg[0x04] >> 4) + 1)          # transmission to the next start
                self._busy_until = end + ard
                self._events.append((end + ard, f"retry {k + 1}"))
            else:
                self._events.append((done, "max_rt"))
        self._events.sort()

    def _event(self, us, what):
        if what == "sent":
            self.fifo.pop(0)
            self.reg[0x07] |= 0x20
        elif what == "max_rt":
            self.reg[0x07] |= 0x10
        else:
            self._attempt(us, int(what.split()[1]))
