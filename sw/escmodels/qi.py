"""A Qi Power Transmitter's side of the communications, for testing a Power Receiver
(Qi Specification v1.3, Communications Protocol: tables 11, 12, 16 and 17, sections 4.2,
5.2, 7.3 and 8; the bit coding as in the Communications Physical Layer, restated in NXP
AN4701 section 2).

Written from the specification, not from any firmware. The transmitter applies the Power
Signal (drives PWR high) and watches the receiver's load modulation on MOD:
  bits at fCLK = 2 kHz +-4% (tCLK 480 to 520 us), differential bi-phase: a transition at
  the start of every bit, and one more half way through a ONE;
  a byte: a ZERO start bit, 8 data bits least significant first, a parity bit (odd parity:
  ONE if the data has an even number of ONEs), a ONE stop bit;
  a packet: a preamble of 11 to 25 ONEs, the header, the message (its size set by the
  header, table 17) and the checksum (XOR of the header and message bytes).
It checks the receiver's timing: the first packet tWake 19 to 64 ms after the Power Signal
appears; in the ping and configuration phases each next packet tStart 11.5 to 19 ms after
the end of the one before; then CE packets at most 350 ms apart (start to start), the first
at most 350 ms after the end of CFG; a packet after a CE no sooner than tDelay + tControl
(5 + 24 ms; for an RP8 also + tWindow + tOffset - n x 0.5 ms, n its preamble bits); any
other packet at least tSilent (6 ms) after the one before; RP8 packets at most 4,050 ms
apart. After an EPT it removes the Power Signal (t_off_after_ept, within tTerminate 28 ms)
unless told to ignore EPT.
"""

from __future__ import annotations

from functools import reduce

from .board import Device

# Table 17: header -> message size, for the packets a Baseline Protocol receiver sends
SIZES = {0x01: 1, 0x02: 1, 0x03: 1, 0x04: 1, 0x05: 1, 0x06: 1, 0x51: 5, 0x71: 7, 0x81: 8}
NAMES = {0x01: "SIG", 0x02: "EPT", 0x03: "CE", 0x04: "RP8", 0x05: "CHS", 0x06: "PCH",
         0x51: "CFG", 0x71: "ID", 0x81: "XID"}


class QiTransmitter(Device):
    def __init__(self, mod, pwr, tick_ns, power=((1000, None),), t_off_after_ept_ms=10.0,
                 ignore_ept=False, t_delay_ms=5.0, t_window_ms=8.0, t_offset_ms=8.0):
        """power: (on_us, off_us or None) intervals in which the Power Signal is applied."""
        self.mod, self.pwr, self.tick = mod, pwr, tick_ns
        self.power = [list(p) for p in power]
        self.t_off_after_ept = t_off_after_ept_ms
        self.ignore_ept = ignore_ept
        self.t_delay, self.t_window, self.t_offset = t_delay_ms, t_window_ms, t_offset_ms
        self.edges: list[tuple[float, int]] = []    # (us, new level)
        self.packets: list[dict] = []
        self.violations: list[str] = []
        self._lv = 0
        self._pending = False                       # edges since the last packet ended

    # ---- the Power Signal
    def _on(self, us):
        return any(a <= us and (b is None or us < b) for a, b in self.power)

    def step(self, t, bus):
        us = t * self.tick / 1000.0
        lv = (bus >> self.mod) & 1
        if lv != self._lv:
            self._lv = lv
            self.edges.append((us, lv))
            self._pending = True
        elif self._pending and us - self.edges[-1][0] > 1200:
            self._pending = False                   # a packet just ended (2 bits idle)
            self._ended()
        on = self._on(us)
        return (0, 1 << self.pwr) if on else (1 << self.pwr, 0)

    def _ended(self):
        """React to an EPT as soon as it ends."""
        if self.ignore_ept:
            return
        k = len(self.edges) - 1
        while k > 0 and self.edges[k][0] - self.edges[k - 1][0] <= 1200:
            k -= 1
        p = self._packet(self.edges[k:])
        if p.get("header") == 0x02 and p["ok"]:
            self._remove_power(p["end"] + self.t_off_after_ept)

    def _remove_power(self, at_us):
        for p in self.power:
            if p[0] <= at_us and (p[1] is None or at_us < p[1]):
                p[1] = at_us

    # ---- decoding
    def _decode(self):
        edges = self.edges
        bursts, cur = [], []
        for e in edges:
            if cur and e[0] - cur[-1][0] > 1200:          # more than 2 bit times idle
                bursts.append(cur)
                cur = []
            cur.append(e)
        if cur:
            bursts.append(cur)
        return [self._packet(b) for b in bursts]

    def _packet(self, edges):
        ts = [e[0] for e in edges]
        iv = [b - a for a, b in zip(ts, ts[1:])]
        bits, durs, errs, i = [], [], [], 0
        while i < len(iv):
            if 200 <= iv[i] <= 300:                     # a half: the first half of a ONE
                if i + 1 < len(iv) and 200 <= iv[i + 1] <= 300:
                    bits.append(1)
                    durs.append(iv[i] + iv[i + 1])
                    if not (240 <= iv[i] <= 260 and 240 <= iv[i + 1] <= 260):
                        errs.append(f"ONE halves {iv[i]:.1f}/{iv[i + 1]:.1f} us")
                    i += 2
                elif i + 1 == len(iv):                  # the last bit: a ONE, no edge after
                    bits.append(1)
                    i += 1
                else:
                    errs.append(f"bad interval {iv[i + 1]:.1f} us after a half")
                    break
            elif 400 <= iv[i] <= 600:
                bits.append(0)
                durs.append(iv[i])
                i += 1
            else:
                errs.append(f"bad interval {iv[i]:.1f} us")
                break
        for d in durs:
            if not 480 <= d <= 520:
                errs.append(f"bit {d:.1f} us (tCLK 480-520)")
                break
        p = {"start": ts[0], "end": None, "bits": bits, "errors": errs, "ok": False}
        # the end of the packet: the end of its last bit
        n = 0
        while n < len(bits) and bits[n] == 1:
            n += 1
        p["preamble"] = n
        if not 11 <= n <= 25:
            errs.append(f"preamble {n} bits (11-25)")
        rest = bits[n:]
        data = []
        while len(rest) >= 11:
            byte, rest = rest[:11], rest[11:]
            v = sum(b << k for k, b in enumerate(byte[1:9]))
            par = 1 if bin(v).count("1") % 2 == 0 else 0
            if byte[0] != 0:
                errs.append("start bit")
            if byte[9] != par:
                errs.append(f"parity of {v:#04x}")
            if byte[10] != 1:
                errs.append("stop bit")
            data.append(v)
        if rest:
            errs.append(f"{len(rest)} bits left over")
        p["end"] = ts[0] + 500.0 * len(bits)
        if not data:
            errs.append("no bytes")
            return p
        h = data[0]
        p["header"] = h
        p["name"] = NAMES.get(h, f"{h:#04x}")
        size = SIZES.get(h)
        if size is None:
            errs.append(f"header {h:#04x} not in table 17 (Baseline)")
        elif len(data) != size + 2:
            errs.append(f"{p['name']}: {len(data)} bytes, want {size + 2}")
        p["message"] = data[1:-1]
        if reduce(lambda a, b: a ^ b, data[:-1]) != data[-1]:
            errs.append("checksum")
        p["ok"] = not errs
        return p

    # ---- after the run
    def finish(self):
        self.packets = self._decode()
        v = self.violations
        for p in self.packets:
            for e in p["errors"]:
                v.append(f"{p['start'] / 1000:.1f} ms {p.get('name', '?')}: {e}")
        for p in self.packets:
            if not self._on(p["start"]):
                v.append(f"{p['start'] / 1000:.1f} ms {p.get('name', '?')}: sent with no Power Signal")
        # group the packets by Power Signal interval
        for a, b in self.power:
            seq = [p for p in self.packets if p["start"] >= a and (b is None or p["start"] < b)]
            self._timing(a, seq)

    def _timing(self, on_us, seq):
        """seq: the packets sent while the Power Signal was on from on_us."""
        v = self.violations
        if not seq:
            return
        dt = (seq[0]["start"] - on_us) / 1000
        if not 19 <= dt <= 64:
            v.append(f"first packet {dt:.2f} ms after the Power Signal (tWake 19-64)")
        names = [p.get("name") for p in seq]
        # the order: SIG, ID, CFG (Count 0, no XID), then CE / RP8; or EPT at any point,
        # and only EPT after it
        want = ["SIG", "ID", "CFG"]
        k = 0
        while k < len(names) and k < 3 and names[k] == want[k]:
            k += 1
        if k < len(names) and names[k] != "EPT" and k < 3:
            v.append(f"packet {k} is {names[k]}, want {want[k]} or EPT")
        for j in range(k, len(names)):
            if names[j] not in ("CE", "RP8", "EPT") or "EPT" in names[k:j] and names[j] != "EPT":
                v.append(f"packet {j} is {names[j]} in the power transfer phase")
                break
        if k == 3 and len(names) > 3 and names[3] != "CE" and names[3] != "EPT":
            v.append(f"the first packet after CFG is {names[3]}, want CE")
        last_ce = last_rp = None
        for prev, p in zip(seq, seq[1:]):
            gap = (p["start"] - prev["end"]) / 1000
            name, pname = p.get("name"), prev.get("name")
            if pname == "CE":                           # 7.3.1
                need = self.t_delay + 24
                if name == "RP8":
                    need += self.t_window + self.t_offset - 0.5 * p["preamble"]
                if gap < need:
                    v.append(f"{name} {gap:.2f} ms after a CE (at least {need:.1f})")
            elif gap < 6:
                v.append(f"{name} {gap:.2f} ms after {pname} (tSilent 6)")
            if pname in ("SIG", "ID") or pname == "EPT":  # 5.2.2; EPT repeats as 4.2.4
                if not 11.5 <= gap <= 19:
                    v.append(f"{name} {gap:.2f} ms after {pname} (tStart 11.5-19)")
            if pname == "CFG" and name == "CE" and gap > 350:
                v.append(f"first CE {gap:.2f} ms after CFG (tInterval at most 350)")
            if name == "CE":
                if last_ce is not None and (p["start"] - last_ce) / 1000 > 350:
                    v.append(f"CE {(p['start'] - last_ce) / 1000:.2f} ms after the last (at most 350)")
                last_ce = p["start"]
            if name == "RP8":
                if last_rp is not None and (p["start"] - last_rp) / 1000 > 4050:
                    v.append(f"RP8 {(p['start'] - last_rp) / 1000:.2f} ms after the last (at most 4050)")
                last_rp = p["start"]

    def errors(self):
        return []
