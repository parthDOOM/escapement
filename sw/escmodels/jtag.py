"""IEEE 1149.1 JTAG TAP model: the full 16-state controller, a 4-bit IR (IDCODE 0001
after reset, BYPASS 1111), a 32-bit IDCODE register and a 1-bit BYPASS register.
TMS/TDI are sampled on the rising TCK edge; TDO changes on the falling edge (it is
driven only in Shift-IR/Shift-DR, pulled up otherwise). The model also checks that TMS
and TDI are stable at each rising edge (set at least one tick before it).
"""

from __future__ import annotations

from .board import Device

# state -> (next if TMS=0, next if TMS=1)
NEXT = {
    "TLR": ("RTI", "TLR"), "RTI": ("RTI", "SDR"),
    "SDR": ("CDR", "SIR"), "CDR": ("SHDR", "E1DR"), "SHDR": ("SHDR", "E1DR"),
    "E1DR": ("PDR", "UDR"), "PDR": ("PDR", "E2DR"), "E2DR": ("SHDR", "UDR"),
    "UDR": ("RTI", "SDR"),
    "SIR": ("CIR", "TLR"), "CIR": ("SHIR", "E1IR"), "SHIR": ("SHIR", "E1IR"),
    "E1IR": ("PIR", "UIR"), "PIR": ("PIR", "E2IR"), "E2IR": ("SHIR", "UIR"),
    "UIR": ("RTI", "SDR"),
}
IDCODE, BYPASS = 0b0001, 0b1111


class JtagTap(Device):
    def __init__(self, tck: int, tms: int, tdi: int, tdo: int, idcode: int = 0x4BA00477):
        self.tck, self.tms, self.tdi, self.tdo = tck, tms, tdi, tdo
        self.idcode = idcode | 1                 # bit 0 of an IDCODE is always 1
        self.state = "TLR"
        self.ir = IDCODE
        self.sr = 0
        self.tdo_val = 1
        self.states: list[str] = []
        self.violations: list[str] = []
        self.dr_out: list[int] = []              # values shifted in through Shift-DR
        self._ptck = None
        self._last_in_change = -10**9
        self._pin = 0
        self._shifted = 0

    def step(self, t, bus):
        tck = (bus >> self.tck) & 1
        ins = (bus >> self.tms) & 1 | (((bus >> self.tdi) & 1) << 1)
        if self._ptck is None:                    # first observation: initial levels
            self._ptck, self._pin = tck, ins
            return 0, 0
        if ins != self._pin:
            self._last_in_change = t
            self._pin = ins
        if tck and not self._ptck:                                   # rising edge
            if t - self._last_in_change < 1:
                self.violations.append(f"t={t}: TMS/TDI changed at the rising TCK edge")
            tms, tdi = ins & 1, ins >> 1
            st = self.state
            if st == "SHDR":
                n = 32 if self.ir == IDCODE else 1
                self.sr = (self.sr >> 1) | (tdi << (n - 1))
                self._shifted += 1
            elif st == "SHIR":
                self.sr = (self.sr >> 1) | (tdi << 3)
            nxt = NEXT[st][tms]
            if nxt == "CDR":
                self.sr = self.idcode if self.ir == IDCODE else 0
                self._shifted = 0
            elif nxt == "CIR":
                self.sr = 0b0001                                     # capture pattern xx01
            elif nxt == "UIR":
                self.ir = self.sr & 0xF
            elif nxt == "UDR":
                self.dr_out.append(self.sr)
            elif nxt == "TLR":
                self.ir = IDCODE
            if st == "SHIR" and nxt == "E1IR":
                pass
            self.state = nxt
            self.states.append(nxt)
        elif self._ptck and not tck:                                 # falling edge
            self.tdo_val = (self.sr & 1) if self.state in ("SHDR", "SHIR") else 1
        self._ptck = tck
        return (0 if self.tdo_val else (1 << self.tdo)), 0

    def errors(self):
        return list(self.violations[:5])
