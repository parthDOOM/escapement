"""ARM Serial Wire Debug target: an SW-DP (SWD protocol version 1, DPv1 register map)
behind an SWJ-DP, from the Arm Debug Interface Architecture Specification ADIv5.0 to
ADIv5.2, ARM IHI 0031G:

  B4.1    request/ACK/data phases, one-cycle turnaround, LSB first, even parity
  B4.2    OK / WAIT / FAULT responses and protocol errors; Tables B4-2 to B4-5 decide
          the ACK for every DP and AP access (DPIDR and CTRL/STAT reads and ABORT writes
          are always OK; anything else FAULTs while a sticky flag is set and WAITs
          while the AP is busy); AP reads are posted, RDBUFF returns the last one
  B4.3.1  the target samples SWDIO on the rising SWCLK edge and starts or stops driving
          it on the rising edge
  B4.3.3  line reset: >= 50 cycles high then >= 2 idle cycles, recognised while waiting
          for a header or in the error state; the first access after it must be a
          DPIDR read
  B5.2.2  an SWJ-DP starts in JTAG; >= 50 cycles high then 0xE79E (LSB first) selects SWD

Electrically the target drives SWDIO push-pull only in its own phases and otherwise
leaves it to the board's pull-up. Contention is reported when the line does not show
the level the target is driving (the host is fighting it; the board also records a
host-high/target-low fight), and when the line reads low on a turnaround edge, when
neither side may drive and the pull-up holds it high. Two drivers at the same level
carry no current and cannot be told apart from one on the line levels.

Configuration: `waits` WAIT responses (a busy AP) before the next WAIT-able access
completes, `sticky` initial CTRL/STAT sticky flags (FAULT until an ABORT clears them),
`bad_parity` read responses sent with the parity bit inverted, `ap_regs` maps
(APSEL, address) to AP register values.
"""

from __future__ import annotations

from .board import Device

SWITCH = [(0xE79E >> i) & 1 for i in range(16)]     # JTAG-to-SWD select, wire order
OK, WAIT, FAULT = 0b001, 0b010, 0b100
STICKYORUN, STICKYCMP, STICKYERR, READOK, WDATAERR = 1 << 1, 1 << 4, 1 << 5, 1 << 6, 1 << 7
STICKY = STICKYORUN | STICKYCMP | STICKYERR | WDATAERR
ORUNDETECT = 1 << 0
PWRUP_REQ = (1 << 28) | (1 << 30)                   # CDBGPWRUPREQ, CSYSPWRUPREQ


def parity(x: int) -> int:
    return bin(x).count("1") & 1


class SwdTarget(Device):
    def __init__(self, swclk: int, swdio: int, dpidr: int = 0x2BA01477, swj: bool = True,
                 waits: int = 0, sticky: int = 0, bad_parity: int = 0,
                 ap_regs: dict | None = None):
        self.clk, self.dio = swclk, swdio
        self.dpidr = dpidr | 1                        # DPIDR bit 0 reads as one
        self.mode = "jtag" if swj else "swd"
        # SWD side: idle (waiting for a start bit), header, plan (response phases),
        # error (protocol error or lockout: only a line reset leaves it). A bare SW-DP
        # needs a line reset before its first header (B4.3.3).
        self.state = "error"
        self.in_reset = False                          # after a line reset, before DPIDR
        self.waits, self.bad_parity = waits, bad_parity
        self.ctrl = sticky & STICKY
        self.select = 0
        self.ap_regs = dict(ap_regs or {})
        self.ap_result = 0                             # posted AP read result (RDBUFF)
        self.last_read = 0                             # for RESEND
        self.switches = 0
        self.line_resets = 0
        self.protocol_errors: list[int] = []           # ticks of headers not answered
        self.log: list[dict] = []                      # every answered request
        self.writes: list[tuple] = []                  # (APnDP, address, value) applied
        self.violations: list[str] = []
        self._hdr: list[int] = []
        self._plan: list[tuple] = []
        self._wbits: list[int] = []
        self._hist: list[int] = []                     # host bits, JTAG mode
        self._ones = 0
        self._lr_zeros = 0
        self._out = None                               # level driven this cycle
        self._drove = None                             # level driven last cycle
        self._pclk = None

    # ------------------------------------------------------------------ helpers

    def _violation(self, t, msg):
        self.violations.append(f"t={t}: {msg}")

    def _line_reset_watch(self, t, b):
        """B4.3.3: >= 50 ones, then two zeros, puts the SWD interface in reset."""
        if b:
            self._ones += 1
            self._lr_zeros = 0
            return
        if self._ones >= 50:
            self._lr_zeros = 1
        elif self._lr_zeros == 1:
            self._lr_zeros = 0
            if self.mode == "swd" and self.state in ("idle", "header", "error"):
                self.state, self.in_reset = "idle", True
                self._hdr = []
                self.line_resets += 1
        else:
            self._lr_zeros = 0
        self._ones = 0

    def _ack_for(self, apndp, rnw, a):
        """Tables B4-2 to B4-5."""
        if not apndp and ((rnw and a == 0x0) or (rnw and a == 0x4 and not self.select & 0xF)
                          or (not rnw and a == 0x0)):
            return OK                                  # DPIDR, CTRL/STAT, ABORT
        if self.ctrl & STICKY:
            return FAULT
        if self.waits > 0:
            self.waits -= 1
            return WAIT
        return OK

    def _read(self, apndp, a):
        if apndp:
            value = self.ap_result                     # posted: the previous AP read
            self.ap_result = self.ap_regs.get((self.select >> 24, (self.select & 0xF0) | a), 0)
            self.ctrl |= READOK
        elif a == 0x0:
            value = self.dpidr
        elif a == 0x4:
            ack_bits = (self.ctrl & PWRUP_REQ) << 1    # power-up ACK follows REQ
            value = self.ctrl | ack_bits if not self.select & 0xF else 0
        elif a == 0x8:
            value = self.last_read                     # RESEND
        else:
            value = self.ap_result                     # RDBUFF
            self.ctrl |= READOK
        self.last_read = value
        return value

    def _write(self, t, apndp, a, value):
        self.writes.append((apndp, a, value))
        if apndp:
            self.ap_regs[(self.select >> 24, (self.select & 0xF0) | a)] = value
        elif a == 0x0:                                 # ABORT
            clr = 0
            if value & (1 << 1):
                clr |= STICKYCMP
            if value & (1 << 2):
                clr |= STICKYERR
            if value & (1 << 3):
                clr |= WDATAERR
            if value & (1 << 4):
                clr |= STICKYORUN
            self.ctrl &= ~clr
        elif a == 0x4:
            if self.select & 0xF:
                return
            if value & ORUNDETECT:
                self._violation(t, "overrun detection enabled: not modelled")
            self.ctrl = (self.ctrl & (STICKY | READOK)) | (value & PWRUP_REQ)
        elif a == 0x8:
            self.select = value

    def _header(self, t):
        h = self._hdr
        self._hdr = []
        apndp, rnw = h[1], h[2]
        a = (h[3] << 2) | (h[4] << 3)
        if h[5] != h[1] ^ h[2] ^ h[3] ^ h[4] or h[6] != 0 or h[7] != 1:
            self.protocol_errors.append(t)             # B4.2.5: no reply, line not driven
            self.state = "error"
            return
        if self.in_reset and not (not apndp and rnw and a == 0):
            self._violation(t, "first access after a line reset is not a DPIDR read")
            self.protocol_errors.append(t)
            self.state = "error"
            return
        self.in_reset = False
        ack = self._ack_for(apndp, rnw, a)
        if ack != OK and rnw and (apndp or a == 0xC):
            self.ctrl &= ~READOK
        rec = {"t": t, "apndp": apndp, "rnw": rnw, "a": a, "ack": ack, "data": None}
        self.log.append(rec)
        plan = [("drive", (ack >> i) & 1) for i in range(3)]          # ACK[0:2]
        if ack == OK and rnw:
            value = self._read(apndp, a)
            p = parity(value)
            if self.bad_parity > 0:
                self.bad_parity -= 1
                p ^= 1
            rec["data"] = value
            plan += [("drive", (value >> i) & 1) for i in range(32)] + [("drive", p)]
            plan += [("release",), ("trn",)]
        elif ack == OK:
            plan += [("release",), ("trn",)]
            plan += [("wbit", i) for i in range(33)] + [("wdone", rec, apndp, a)]
        else:
            plan += [("release",), ("trn",)]
        self._plan = plan
        self.state = "plan"
        self._ones = self._lr_zeros = 0                # a line reset cannot span a packet

    # ------------------------------------------------------------------ edges

    def _edge(self, t, b):
        """A rising SWCLK edge; b = SWDIO as sampled on it."""
        if self.mode == "jtag":
            self._hist = (self._hist + [b])[-66:]
            if len(self._hist) == 66 and self._hist[-16:] == SWITCH and all(self._hist[:50]):
                self.mode, self.state, self.in_reset = "swd", "idle", True   # B5.2.2
                self.switches += 1
                self._ones = self._lr_zeros = 0
            return
        if self.state == "plan":
            step = self._plan.pop(0)
            kind = step[0]
            if kind == "drive":
                if self._out is None and b == 0:
                    self._violation(t, "SWDIO low in the turnaround before the target drives")
                self._out = step[1]
            elif kind == "release":
                self._out = None
            elif kind == "trn":
                if b == 0:
                    self._violation(t, "SWDIO low in the turnaround after the target released")
            elif kind == "wbit":
                self._wbits.append(b)
            if self._plan and self._plan[0][0] == "wdone":       # after WDATA parity
                _, rec, apndp, a = self._plan.pop(0)
                bits, self._wbits = self._wbits, []
                value = sum(v << i for i, v in enumerate(bits[:32]))
                rec["data"] = value
                if parity(value) != bits[32]:
                    self.ctrl |= WDATAERR                # B4.1.6: discarded, flagged
                    rec["wdata_parity_error"] = True
                else:
                    self._write(t, apndp, a, value)
            if not self._plan:
                self.state = "idle"
            return
        self._line_reset_watch(t, b)
        if self.state == "idle":
            if b:
                self._hdr = [1]
                self.state = "header"
        elif self.state == "header":
            self._hdr.append(b)
            if len(self._hdr) == 8:
                self._header(t)

    def step(self, t, bus):
        clk = (bus >> self.clk) & 1
        dio = (bus >> self.dio) & 1
        if self._drove is not None and dio != self._drove:
            self._violation(t, f"contention: target drives SWDIO {self._drove}, line is {dio}")
        if self._pclk is not None and clk and not self._pclk:
            self._edge(t, dio)
        self._pclk = clk
        self._drove = self._out
        if self._out is None:
            return 0, 0
        return (0, 1 << self.dio) if self._out else (1 << self.dio, 0)

    def errors(self):
        return [f"SWD: {v}" for v in self.violations[:10]]
