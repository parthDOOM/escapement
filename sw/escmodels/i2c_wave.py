"""I2C traffic between two other devices, with planted timing faults (the bus timing
monitor's tests).

`I2cWave` builds the waveform of controller writes (START, address and data bytes each
acknowledged, STOP) on SCL and SDA as open-drain lines, every phase timed in ticks, and
plays it. `plant` overrides the timing of single clock pulses (bit numbers count from 0
across the whole script): `low` (the SCL low phase before the rise, a stretch when long),
`high` (the high phase after it) or `hold` (when SDA changes after SCL falls; the setup
time is low - hold). `violations` judges the edges themselves against interval rules in
the plain sense (an edge of B at least MIN and at most MAX ticks after the last edge of
A), so a test compares the chip's monitor with the waveform, not with another model of
the monitor.
"""

from __future__ import annotations

from dataclasses import dataclass

from .board import Device

RISE, FALL, ANY = 1, 2, 3


@dataclass
class Timing:
    low: int = 250          # 5 us low and 5 us high at 50 MHz: 100 kHz, inside standard
    high: int = 250         # mode's tLOW >= 4.7 us, tHIGH >= 4.0 us, period >= 10 us
    hold: int = 15          # SDA changes 300 ns after SCL falls (setup = low - hold)
    start: int = 220        # START: SDA falls, then SCL falls this much later (tHD;STA)
    stop: int = 220         # STOP: SCL rises, then SDA rises this much later (tSU;STO)
    gap: int = 300          # bus free between transactions (tBUF)


class I2cWave(Device):
    def __init__(self, scl: int, sda: int, t0: int, script: list[bytes],
                 timing: Timing = Timing(), plant: dict[int, dict[str, int]] | None = None):
        self.scl, self.sda = scl, sda
        plant = plant or {}
        tm = timing
        ev: list[tuple[int, int, int]] = []          # (tick, pin, level)
        t, k = t0, 0
        for msg in script:
            ev.append((t, sda, 0))                   # START
            t += tm.start
            ev.append((t, scl, 0))
            sda_lv = 0
            for byte in msg:
                for i in range(9):                   # eight bits MSB first, then ACK (0)
                    b = (byte >> (7 - i)) & 1 if i < 8 else 0
                    p = {"low": tm.low, "high": tm.high, "hold": tm.hold, **plant.get(k, {})}
                    if b != sda_lv:
                        ev.append((t + p["hold"], sda, b))
                        sda_lv = b
                    t += p["low"]
                    ev.append((t, scl, 1))
                    t += p["high"]
                    ev.append((t, scl, 0))
                    k += 1
            if sda_lv != 0:                          # STOP: SDA low, SCL high, SDA high
                ev.append((t + tm.hold, sda, 0))
            t += tm.low
            ev.append((t, scl, 1))
            t += tm.stop
            ev.append((t, sda, 1))
            t += tm.gap
        self.end = t
        ev.sort()
        ticks = [e[0] for e in ev]
        assert len(set(zip(ticks, (e[1] for e in ev)))) == len(ev), "two edges of a pin at once"
        self.events = ev
        self._i = 0
        self._lv = {scl: 1, sda: 1}

    def edges(self, pin: int, kind: int) -> list[int]:
        """Ticks of `pin`'s edges of `kind` (RISE, FALL or ANY)."""
        out = []
        lv = 1
        for t, p, v in self.events:
            if p == pin and v != lv:
                if (kind & RISE and v == 1) or (kind & FALL and v == 0):
                    out.append(t)
                lv = v
        return out

    def violations(self, frm, fe, to, te, mn, mx):
        """(kind, tick of the end, interval) per violation of the rule, in time order: an
        interval ends at the first to-edge after the last from-edge (a to-edge at the same
        tick as a from-edge ends the interval before it); `late` ends MAX ticks after the
        from-edge with no to-edge."""
        fs, ts = self.edges(frm, fe), self.edges(to, te)
        out = []
        for i, a in enumerate(fs):
            nxt = fs[i + 1] if i + 1 < len(fs) else None
            b = next((x for x in ts if x > a), None)
            if b is not None and nxt is not None and b > nxt:
                b = None                             # a new from-edge came first
            # late: no to-edge by a + MAX, and no new from-edge before a + MAX
            if mx and (b is None or b - a > mx) and (nxt is None or nxt - a >= mx):
                out.append(("late", a + mx, mx))
            elif b is not None and mn and b - a < mn:
                out.append(("early", b, b - a))
        return out

    def step(self, t, bus):
        while self._i < len(self.events) and self.events[self._i][0] <= t:
            _, p, v = self.events[self._i]
            self._lv[p] = v
            self._i += 1
        low = sum(1 << p for p, v in self._lv.items() if v == 0)
        return low, 0
