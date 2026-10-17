"""Scripted I2C controller model: drives a target implemented by Escapement firmware.

Open-drain: the model only ever pulls SCL/SDA low. It honours clock stretching (each
high phase is timed from the moment SCL is actually seen high), and it checks the
TARGET side of UM10204 rev. 7: SDA must not change while SCL is high except as START/
STOP (which a target never generates), and data driven by the target must be set up
tSU;DAT before SCL rises. Timing (ticks at 40 ns):

  standard (100 kHz): tLOW 130, tHIGH 120 (10 us period), tSU;DAT 250 ns
  fast     (397 kHz): tLOW  38, tHIGH  25 (2.52 us period), tSU;DAT 100 ns

Script operations (results appended to .results, one dict per operation):
  ("write", addr7, word, [bytes])  S A|W word data... P
  ("read",  addr7, word, n)        S A|W word Sr A|R n bytes (ACK all but the last) P
  ("cur",   addr7, n)              S A|R n bytes P (current-address read)
  ("probe", addr7)                 S A|W P
"""

from __future__ import annotations

from .board import Device

TIMING = {
    "standard": dict(tLOW=130, tHIGH=120, tHD_STA=110, tSU_STA=125, tSU_STO=110, tBUF=130,
                     tHD_DAT=2, tSU_DAT_ns=250),
    "fast":     dict(tLOW=38, tHIGH=25, tHD_STA=16, tSU_STA=16, tSU_STO=16, tBUF=34,
                     tHD_DAT=2, tSU_DAT_ns=100),
}
STRETCH_LIMIT = 200_000          # ticks of SCL held low before the model gives up


class I2cController(Device):
    def __init__(self, scl: int, sda: int, script: list, mode: str = "standard",
                 start_at: int = 200, tick_ns: float = 40.0):
        self.scl, self.sda = scl, sda
        self.script = list(script)
        self.p = dict(TIMING[mode])
        self.tsu_dat = self.p["tSU_DAT_ns"] / tick_ns
        self.start_at = start_at
        self.results: list[dict] = []
        self.violations: list[str] = []
        self.done = False
        self.max_stretch = 0
        self.t = -1
        self.bus = 0xFF
        self._scl_lo = self._sda_lo = False
        self._last_sda_change = -10**9
        self._prev_sda = 1
        self._gen = self._run()

    # ------------------------------------------------------------ Device interface
    def step(self, t, bus):
        self.t, self.bus = t, bus
        sda = (bus >> self.sda) & 1
        if sda != self._prev_sda:
            self._last_sda_change = t
            self._prev_sda = sda
        if not self.done:
            try:
                next(self._gen)
            except StopIteration:
                self.done = True
        low = ((1 << self.scl) if self._scl_lo else 0) | ((1 << self.sda) if self._sda_lo else 0)
        return low, 0

    def errors(self):
        errs = list(self.violations[:5])
        if not self.done:
            errs.append(f"I2C controller script not finished ({len(self.results)} ops done)")
        return errs

    # ------------------------------------------------------------ bus helpers
    def _lvl(self, pin):
        return (self.bus >> pin) & 1

    def _hold(self, n):
        for _ in range(int(n)):
            yield

    def _clock_high(self, check_target_sda: bool):
        """Release SCL, wait until it is high (stretching), hold tHIGH; return SDA sampled
        at the end of the high phase. Leaves SCL pulled low."""
        self._scl_lo = False
        t0 = self.t
        while not self._lvl(self.scl):
            if self.t - t0 > STRETCH_LIMIT:
                self.violations.append(f"t={self.t}: SCL held low > {STRETCH_LIMIT} ticks")
                return 1
            yield
        rise = self.t
        self.max_stretch = max(self.max_stretch, rise - t0)
        if check_target_sda and rise - self._last_sda_change < self.tsu_dat:
            self.violations.append(f"t={rise}: target tSU;DAT {rise - self._last_sda_change} "
                                   f"ticks < {self.tsu_dat:.1f}")
        first = self._lvl(self.sda)
        for _ in range(self.p["tHIGH"]):
            yield
            if check_target_sda and self._lvl(self.sda) != first:
                self.violations.append(f"t={self.t}: target changed SDA while SCL high")
                first = self._lvl(self.sda)
        sample = self._lvl(self.sda)
        self._scl_lo = True
        return sample

    def _bit_out(self, b):
        yield from self._hold(self.p["tHD_DAT"])
        self._sda_lo = not b
        yield from self._hold(self.p["tLOW"] - self.p["tHD_DAT"])
        yield from self._clock_high(check_target_sda=False)

    def _bit_in(self):
        yield from self._hold(self.p["tHD_DAT"])
        self._sda_lo = False
        yield from self._hold(self.p["tLOW"] - self.p["tHD_DAT"])
        b = yield from self._clock_high(check_target_sda=True)
        return b

    def _byte_out(self, v):
        for i in range(7, -1, -1):
            yield from self._bit_out((v >> i) & 1)
        ack = yield from self._bit_in()
        return ack == 0

    def _byte_in(self, ack):
        v = 0
        for _ in range(8):
            b = yield from self._bit_in()
            v = (v << 1) | b
        yield from self._bit_out(0 if ack else 1)
        return v

    def _start(self):
        # bus idle: both high
        while not (self._lvl(self.scl) and self._lvl(self.sda)):
            yield
        self._sda_lo = True
        yield from self._hold(self.p["tHD_STA"])
        self._scl_lo = True

    def _rstart(self):
        yield from self._hold(self.p["tHD_DAT"])
        self._sda_lo = False
        yield from self._hold(self.p["tLOW"])
        self._scl_lo = False
        while not self._lvl(self.scl):
            yield
        yield from self._hold(self.p["tSU_STA"])
        self._sda_lo = True
        yield from self._hold(self.p["tHD_STA"])
        self._scl_lo = True

    def _stop(self):
        yield from self._hold(self.p["tHD_DAT"])
        self._sda_lo = True
        yield from self._hold(self.p["tLOW"])
        self._scl_lo = False
        while not self._lvl(self.scl):
            yield
        yield from self._hold(self.p["tSU_STO"])
        self._sda_lo = False
        yield from self._hold(self.p["tBUF"])

    # ------------------------------------------------------------ script
    def _run(self):
        while self.t < self.start_at:
            yield
        for op in self.script:
            res = {"op": op, "acks": [], "data": []}
            kind = op[0]
            yield from self._start()
            if kind == "write":
                _, a, word, data = op
                for byte in [a << 1, word] + list(data):
                    ok = yield from self._byte_out(byte)
                    res["acks"].append(ok)
                    if not ok:
                        break
            elif kind == "read":
                _, a, word, n = op
                for byte in (a << 1, word):
                    ok = yield from self._byte_out(byte)
                    res["acks"].append(ok)
                if all(res["acks"]):
                    yield from self._rstart()
                    ok = yield from self._byte_out((a << 1) | 1)
                    res["acks"].append(ok)
                    if ok:
                        for i in range(n):
                            v = yield from self._byte_in(ack=i < n - 1)
                            res["data"].append(v)
            elif kind == "cur":
                _, a, n = op
                ok = yield from self._byte_out((a << 1) | 1)
                res["acks"].append(ok)
                if ok:
                    for i in range(n):
                        v = yield from self._byte_in(ack=i < n - 1)
                        res["data"].append(v)
            elif kind == "probe":
                ok = yield from self._byte_out(op[1] << 1)
                res["acks"].append(ok)
            yield from self._stop()
            self.results.append(res)
        self._scl_lo = self._sda_lo = False
