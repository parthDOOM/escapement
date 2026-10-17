"""I2C target model with a register file, optional clock stretching, and a UM10204
timing checker for the controller's waveform (Standard- or Fast-mode).

Bus is open-drain: the model only ever pulls SCL/SDA low. Timing limits (UM10204
rev. 7, Table 10), converted to ticks with `tick_ns`:
  Fast-mode: tLOW >= 1.3 us, tHIGH >= 0.6 us, tSU;DAT >= 100 ns, tHD;STA >= 0.6 us,
             tSU;STA >= 0.6 us, tSU;STO >= 0.6 us, tBUF >= 1.3 us, fSCL <= 400 kHz
  Standard:  tLOW >= 4.7 us, tHIGH >= 4.0 us, tSU;DAT >= 250 ns, tHD;STA >= 4.0 us,
             tSU;STA >= 4.7 us, tSU;STO >= 4.0 us, tBUF >= 4.7 us, fSCL <= 100 kHz
The model sees the bus one cycle late (Board semantics), which makes measured high
times at most one tick short: the checker therefore allows one tick of slack.
"""

from __future__ import annotations

from .board import Device

LIMITS_NS = {
    "fast":     dict(tLOW=1300, tHIGH=600, tSU_DAT=100, tHD_STA=600, tSU_STA=600,
                     tSU_STO=600, tBUF=1300, period=2500),
    "standard": dict(tLOW=4700, tHIGH=4000, tSU_DAT=250, tHD_STA=4000, tSU_STA=4700,
                     tSU_STO=4000, tBUF=4700, period=10000),
}


class I2cTarget(Device):
    def __init__(self, scl: int, sda: int, addr7: int, regs: dict[int, int] | None = None,
                 mode: str = "fast", tick_ns: float = 40.0, stretch_ticks: int = 0,
                 check_timing: bool = True):
        self.scl, self.sda, self.addr7 = scl, sda, addr7
        self.regs = dict(regs or {})
        self.lim = {k: v / tick_ns for k, v in LIMITS_NS[mode].items()}
        self.stretch_ticks, self.check_timing = stretch_ticks, check_timing
        self.violations: list[str] = []
        self.transactions: list[list] = []   # decoded: [("S"|"Sr"|"P"), (byte, ack)...]
        # bus edge tracking
        self._pscl = self._psda = 1
        self._t_scl_fall = self._t_scl_rise = None
        self._t_sda_change = None
        self._t_start = self._t_stop = None
        # protocol state
        self._state = "idle"     # idle | addr | data_w | data_r
        self._bits = 0
        self._shift = 0
        self._ack_phase = False
        self._sda_drive_low = False
        self._ptr = 0
        self._first_write = True
        self._tx_byte = 0
        self._stretch_until = -1

    # ------------------------------------------------------------ timing checks
    def _viol(self, t, name, got, need):
        if self.check_timing and got + 1 < need:   # one tick of observation slack
            self.violations.append(f"t={t}: {name} {got:.1f} ticks < {need:.1f}")

    # ------------------------------------------------------------ main step
    def step(self, t, bus):
        scl = (bus >> self.scl) & 1
        sda = (bus >> self.sda) & 1
        scl_rise = scl and not self._pscl
        scl_fall = (not scl) and self._pscl
        sda_rise = sda and not self._psda
        sda_fall = (not sda) and self._psda

        if sda != self._psda:
            if scl and self._pscl:
                # SDA change while SCL high: START or STOP
                if sda_fall:
                    if self._t_stop is not None:
                        self._viol(t, "tBUF", t - self._t_stop, self.lim["tBUF"])
                    if self._t_scl_rise is not None and self._state != "idle":
                        self._viol(t, "tSU;STA", t - self._t_scl_rise, self.lim["tSU_STA"])
                    self._on_start(t)
                else:
                    if self._t_scl_rise is not None:
                        self._viol(t, "tSU;STO", t - self._t_scl_rise, self.lim["tSU_STO"])
                    self._on_stop(t)
            else:
                self._t_sda_change = t

        if scl_rise:
            if self._t_scl_fall is not None:
                self._viol(t, "tLOW", t - self._t_scl_fall, self.lim["tLOW"])
                if self._t_sda_change is not None and self._t_sda_change > self._t_scl_fall:
                    self._viol(t, "tSU;DAT", t - self._t_sda_change, self.lim["tSU_DAT"])
            self._t_scl_rise = t
            if self._state != "idle":
                self._on_scl_rise(t, sda)
        if scl_fall:
            if self._t_scl_rise is not None:
                self._viol(t, "tHIGH", t - self._t_scl_rise, self.lim["tHIGH"])
            # first SCL fall after a (repeated) START: hold time of the START
            if self._t_start is not None and (self._t_scl_fall is None
                                              or self._t_start > self._t_scl_fall):
                self._viol(t, "tHD;STA", t - self._t_start, self.lim["tHD_STA"])
            self._t_scl_fall = t
            if self._state != "idle":
                self._on_scl_fall(t)

        self._pscl, self._psda = scl, sda
        low = 0
        if self._sda_drive_low:
            low |= 1 << self.sda
        if t < self._stretch_until:
            low |= 1 << self.scl
        return low, 0

    # ------------------------------------------------------------ protocol
    def _on_start(self, t):
        kind = "Sr" if self._state != "idle" else "S"
        self.transactions.append([kind]) if kind == "S" else self.transactions[-1].append("Sr")
        self._t_start = t
        self._state = "addr"
        self._bits = self._shift = 0
        self._ack_phase = False
        self._sda_drive_low = False

    def _on_stop(self, t):
        if self.transactions:
            self.transactions[-1].append("P")
        self._t_stop = t
        self._state = "idle"
        self._sda_drive_low = False
        self._t_start = None

    def _on_scl_rise(self, t, sda):
        if self._ack_phase:
            if self._state == "data_r":
                # controller ACK (0) continues the read; NACK (1) ends it
                self.transactions[-1].append(("rdack", sda))
                if sda:
                    self._state = "wait_stop"
            return
        if self._state in ("addr", "data_w"):
            self._shift = ((self._shift << 1) | sda) & 0xFF
        self._bits += 1

    def _on_scl_fall(self, t):
        if self._ack_phase:
            # end of the 9th clock
            self._ack_phase = False
            self._sda_drive_low = False
            self._bits = 0
            if self._state == "data_r":
                self._tx_byte = self.regs.get(self._ptr, 0)
                self._ptr = (self._ptr + 1) & 0xFF
                self._drive_bit(7)
            if self.stretch_ticks:
                self._stretch_until = t + self.stretch_ticks
            return
        if self._state == "data_r":
            if self._bits < 8:
                self._drive_bit(7 - self._bits)
            else:
                self._sda_drive_low = False      # release for the controller's ACK
                self._ack_phase = True
            return
        if self._bits == 8:
            byte = self._shift
            if self._state == "addr":
                match = (byte >> 1) == self.addr7
                self.transactions[-1].append(("addr", byte, match))
                if match:
                    self._sda_drive_low = True   # ACK
                    if byte & 1:
                        self._state = "data_r"
                    else:
                        self._state = "data_w"
                        self._first_write = True
                else:
                    self._state = "wait_stop"
            elif self._state == "data_w":
                self.transactions[-1].append(("w", byte))
                if self._first_write:
                    self._ptr = byte
                    self._first_write = False
                else:
                    self.regs[self._ptr] = byte
                    self._ptr = (self._ptr + 1) & 0xFF
                self._sda_drive_low = True       # ACK
            self._ack_phase = True
            self._shift = 0

    def _drive_bit(self, i):
        self._sda_drive_low = not ((self._tx_byte >> i) & 1)

    def errors(self):
        return [f"I2C timing: {v}" for v in self.violations[:10]]
