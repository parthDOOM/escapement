"""Microwire serial EEPROM model: 93AA46 / 93LC46 / 93C46 (1 Kbit), as the device on the bus.

Source: Microchip DS21749H (2010), "93AA46A/B/C, 93LC46A/B/C, 93C46A/B/C 1K Microwire
Compatible Serial EEPROM". The chip under test is the Microwire controller: it drives CS, CLK
and DI; this model drives DO push-pull or releases it (High-Z, the board's pull-up shows).

Organisation (constructor `org16`): x16 = 64 words, 6 address bits, 16 data bits (Table 1-3,
93XX46B or 46C with ORG = 1); x8 = 128 bytes, 7 address bits, 8 data bits (Table 1-4, 93XX46A
or 46C with ORG = 0). Required clock cycles, start bit included: x16 ERASE/ERAL/EWEN/EWDS 9,
READ/WRITE/WRAL 25; x8 10 and 18. Opcodes (after the start bit 1): READ 10, WRITE 01, ERASE 11,
00 + two address MSBs: EWDS 00, WRAL 01, ERAL 10, EWEN 11 (rest X). Address and data MSB first.

Limits (Table 1-2, chosen by `grade` and `vcc`; VCC must lie in the grade's range from the
Device Selection Table: AA 1.8-5.5 V, LC 2.5-5.5 V, C 4.5-5.5 V, else ValueError). At a
boundary voltage the higher-voltage row is used (4.5 V uses the 4.5-5.5 V row, 2.5 V the
2.5-5.5 V row). The A1/A2/A3/A7/A8 rows marked "93XX46C only" (3 MHz, 200/100 ns, 50 ns)
apply only with `orgpin=True` (the C-suffix part with an ORG pin) and VCC >= 4.5 V; the
default is the conservative 2.5-5.5 V row (2 MHz, 250/200 ns, 100 ns). `self.lim` holds the
values in use (ns, MHz, us).

Ticks: a check uses the tick at which the bus first shows a new level (the board's history
index; step(t) sees the level of tick t-1). Every message is "EEPROM t=<tick>: <param>: ...".
Durations compare as ticks * tick_ns against the limit. The pin levels of tick 0 are the
starting state (the level step(0) sees is the board's pull-up default, not a tick: ignored).

Checks (each failure appends to `violations`):
  A1  FCLK   rising CLK to rising CLK while CS high >= 1/FCLK.
  A2  TCKH   CLK high (rise to fall, CS high) >= TCKH.
  A3  TCKL   CLK low (fall to rise, CS high) >= TCKL.
  A4  TCSS   CS rise to the first rising CLK edge >= TCSS.
  A5  TCSH   0 ns: CS must not fall while CLK is still high (TCSH runs from the CLK fall,
             Figure 1-1). Checked only once a rising CLK edge has been seen in that CS-high
             period, so the pulled-up pins before the chip drives them raise nothing.
  A6  TCSL   CS low (fall to rise) >= 250 ns, every CS low (3.1, 2.4-2.9 status check).
  A7  TDIS   DI unchanged for TDIS before each rising CLK edge on which DI is sampled
             (waiting for a start bit and every start/opcode/address/data bit; not the READ
             data-out clocks or clocks after a complete instruction, where DI is "don't care").
  A8  TDIH   DI unchanged for TDIH after such a rising edge (a DI change on the same tick as
             the rising edge counts as an A7 setup failure only).
  2.1 note   CLK or DI must be low when CS goes high (both high at the CS rise = violation).
  2.1/3.2    an instruction needs exactly its required clocks. CS falling before that: not
             executed (op recorded with executed False) plus a violation. Exception: a lone
             start bit then CS low, when a ready status is latched, is the status-clear of
             the 2.4-2.9 / 3.4 notes and is neither an op nor a violation (a lone start bit
             with nothing to clear is a cut-short instruction). Extra rising CLK edges after a
             complete non-READ instruction while CS is still high: one violation per
             instruction ("extra clock"); the device ignores them (3.2: CLK and DI become
             don't care), the instruction still executes, and no new start bit is accepted
             until CS has gone low (3.1, TCSL between consecutive instructions).
  2.7        READ may run on (sequential read); a READ cut before its first word is complete
             is not executed (violation); a sequential READ ending part-way through a later
             word is a violation, executed stays True.
  2.4-2.9    an instruction whose start bit arrives while a self-timed cycle runs is a
             violation and is not executed (recorded executed False). Clocking with DI low
             while busy (polling) is fine (3.2).
  2.5/2.9    ERAL/WRAL with VCC < 4.5 V: violation, not executed (VCC must be 4.5 V).
  DO         contention: when the model drives DO, the bus level of the next tick must equal
             what it drove; a mismatch means another driver (the chip) overrides DO and is
             reported once per episode. A chip driving the same level, or driving DO while the
             model is High-Z, cannot be seen this way (the board itself records chip-high vs
             device-low contention).
  finish()   a run ending mid-instruction, or with an AA/LC program instruction clocked in
             but CS never taken low, is a violation (not executed). An open READ is closed.

Not violations, just recorded: ERASE/WRITE/ERAL/WRAL refused because the part is in EWDS
(2.3, 2.6: power-up state is EWDS; EWEN stays until EWDS) are executed False with no message.
READ works in either state (2.6).

DO timing modelled (Table 1-2 maxima rounded up to whole ticks, so a controller that samples
early sees the old level):
  READ (2.7, 3.4): DO leaves High-Z with the dummy zero TPD (A9) after the rising edge that
    clocks in A0; each further rising edge puts the next data bit (MSB first) on DO TPD later.
    Sequential read continues into the next address with no dummy bit between words, wrapping
    from the last address to 0 (model choice; 2.7 says the data cycles to the next register).
  CS fall: DO keeps its level for TCZ (A10), then High-Z (2.0, Figure 1-1).
  Status (2.4-2.9, 3.4): once a self-timed cycle has started, the status is latched; on each
    later CS rise DO shows it TSV (A11) after CS rises (High-Z before that): low while busy,
    high once ready. With CS held low throughout, DO stays High-Z. A start bit clocked in while
    ready clears the latch ("issuing a Start bit and then taking CS low will clear the
    Ready/Busy status"); DO keeps driving high until CS falls, or until a READ drives data.
    If CS stays high when the cycle starts (93C case), status appears only after CS goes low
    and high again (Figures 2-2, 2-9: TCSL then status).

Self-timed cycle start (2.4, 2.5, 2.8, 2.9):
  AA, LC: the falling edge of CS after a complete ERASE/ERAL/WRITE/WRAL starts the cycle.
  C:      the rising CLK edge of the last required bit starts it (2.8/2.9 "rising edge of CLK
          on the last data bit"; 2.4/2.5 say "before the last address/data bit", read here as
          the edge that clocks that bit in, since the address must be known).
  The memory array changes when the cycle starts (3.1: a started cycle completes regardless
  of CS); the part is busy for the cycle time. Defaults: TWC = 6 ms (A12, AA/LC) or 2 ms (A13,
  93C), TEC = 6 ms (A14, ERAL), TWL = 15 ms (A15, WRAL). `twc_us` overrides TWC; `tec_us` and
  `twl_us` override ERAL/WRAL, and when they are not given while `twc_us` is, they scale by
  the same factor (twc_us / datasheet TWC). Busy intervals are kept in `cycles`.

Records:
  mem        list of ints (64 words or 128 bytes), default all ones (erased); `init` fills it
             from address 0 (masked; shorter lists are padded with erased cells).
  ops        one dict per instruction: {"t", "op", "addr", "data", "executed"}. op is READ,
             WRITE, ERASE, ERAL, WRAL, EWEN, EWDS, or None when CS fell before the opcode was
             decodable. addr: READ/WRITE/ERASE address, else None. data: WRITE/WRAL word,
             READ the list of complete words output (sequential included), else None.
             t: EWEN/EWDS the last clock edge; READ the A0 edge (output start); ERASE/WRITE/
             ERAL/WRAL the cycle start (or where it would have started, if refused);
             cut-short instructions the CS fall; refused-while-busy the last edge or CS fall.
  cycles     list of (start_tick, end_tick, op) for each self-timed cycle.
  violations list of strings; errors() returns them.
"""

from __future__ import annotations

import bisect
from math import ceil

from .board import Board, Device

_RANGES = {"AA": (1.8, 5.5), "LC": (2.5, 5.5), "C": (4.5, 5.5)}
_OFF, _IDLE, _SHIFT, _READ, _DONE = range(5)
_EPS = 1e-6


def _limits(grade: str, vcc: float, orgpin: bool) -> dict:
    """Table 1-2 values (ns unless noted) for this grade and VCC."""
    hi = vcc >= 4.5
    fast = orgpin and hi                       # rows marked "93XX46C only"
    if vcc >= 2.5:
        fclk, tckh, tckl, tdi = (3.0, 200, 100, 50) if fast else (2.0, 250, 200, 100)
    else:
        fclk, tckh, tckl, tdi = 1.0, 450, 450, 250
    return {
        "FCLK_MHz": fclk,                                        # A1
        "TCKH": tckh,                                            # A2
        "TCKL": tckl,                                            # A3
        "TCSS": 50 if hi else (100 if vcc >= 2.5 else 250),      # A4
        "TCSH": 0,                                               # A5
        "TCSL": 250,                                             # A6
        "TDIS": tdi,                                             # A7
        "TDIH": tdi,                                             # A8
        "TPD": 200 if hi else (250 if vcc >= 2.5 else 400),      # A9
        "TCZ": 100 if hi else 200,                               # A10
        "TSV": 200 if hi else (300 if vcc >= 2.5 else 500),      # A11
        "TWC_us": 2000.0 if grade == "C" else 6000.0,            # A13 / A12
        "TEC_us": 6000.0,                                        # A14
        "TWL_us": 15000.0,                                       # A15
    }


class Eeprom93(Device):
    def __init__(self, cs=0, clk=1, di=2, do=3, tick_ns=40.0, org16=True, grade="LC", vcc=5.0,
                 twc_us=None, init=None, tec_us=None, twl_us=None, orgpin=False):
        grade = str(grade).upper()
        if grade not in _RANGES:
            raise ValueError(f"grade {grade!r}: use 'AA', 'LC' or 'C'")
        lo, hi = _RANGES[grade]
        if not lo <= vcc <= hi:
            raise ValueError(f"VCC {vcc} V outside the 93{grade}46 range {lo}-{hi} V")
        if len({cs, clk, di, do}) != 4:
            raise ValueError("cs, clk, di and do must be different pins")
        self.cs, self.clk, self.di, self.do = cs, clk, di, do
        self.tick_ns, self.org16, self.grade, self.vcc, self.orgpin = tick_ns, org16, grade, vcc, orgpin
        self.lim = lim = _limits(grade, vcc, orgpin)
        self.na = 6 if org16 else 7
        self.nd = 16 if org16 else 8
        self.size = 1 << self.na
        self.full = (1 << self.nd) - 1
        self.mem = [self.full] * self.size
        if init is not None:
            if len(init) > self.size:
                raise ValueError(f"init has {len(init)} cells, the array has {self.size}")
            for i, v in enumerate(init):
                self.mem[i] = v & self.full
        scale = 1.0 if twc_us is None else twc_us / lim["TWC_us"]
        self.twc_us = lim["TWC_us"] if twc_us is None else float(twc_us)
        self.tec_us = lim["TEC_us"] * scale if tec_us is None else float(tec_us)
        self.twl_us = lim["TWL_us"] * scale if twl_us is None else float(twl_us)
        tk = lambda ns: max(1, ceil(ns / tick_ns - 1e-9))  # noqa: E731
        self._cyc = {"ERASE": tk(self.twc_us * 1000), "WRITE": tk(self.twc_us * 1000),
                     "ERAL": tk(self.tec_us * 1000), "WRAL": tk(self.twl_us * 1000)}
        self._dpd, self._dcz, self._dsv = tk(lim["TPD"]), tk(lim["TCZ"]), tk(lim["TSV"])
        self._tper = 1000.0 / lim["FCLK_MHz"]
        self._inmask = (1 << cs) | (1 << clk) | (1 << di)
        self._dom = 1 << do

        self.ops: list[dict] = []
        self.cycles: list[tuple[int, int, str]] = []
        self.violations: list[str] = []
        self._ewen = False                      # 2.3 / 2.6: power-up EWDS
        self._busy_end = -1                     # ready when t >= _busy_end
        self._latch = False                     # Ready/Busy status latched (3.4)
        self._prev = None
        self._lcs = self._lclk = self._ldi = 0
        self._cs_rise_t = self._cs_fall_t = None
        self._rise_t = self._fall_t = self._samp_rise = self._di_t = None
        self._st = _OFF
        self._n = 0
        self._sr = 0
        self._op = None
        self._addr = None
        self._refused = False
        self._cleared = False
        self._extra = 0
        self._pending = None
        self._raddr = self._rk = self._read_t = 0
        self._words: list[int] = []
        self._q: list[tuple[int, int, object]] = []   # (tick, seq, DO value) sorted
        self._seq = 0
        self._do = None                          # None (High-Z), 0, 1 or "S" (status)
        self._drove = None
        self._contending = False
        self._last_t = 0

    # ------------------------------------------------------------------ helpers
    def _v(self, T, msg):
        self.violations.append(f"EEPROM t={T}: {msg}")

    def _short(self, T, dt, lim_ns, what):
        ns = dt * self.tick_ns
        if ns < lim_ns - _EPS:
            self._v(T, f"{what} {ns:.0f} ns < {lim_ns:.0f} ns")

    def _sched(self, tick, val):
        self._seq += 1
        bisect.insort(self._q, (tick, self._seq, val))

    def _rec(self, T, op, addr, data, executed):
        self.ops.append({"t": T, "op": op, "addr": addr, "data": data, "executed": executed})

    def _req(self, op):
        if op in ("READ", "WRITE", "WRAL"):
            return 3 + self.na + self.nd
        return 3 + self.na if op else None

    # ------------------------------------------------------------------ step
    def step(self, t, bus):
        if t <= 0 and self._prev is None:
            return 0, 0              # bus at t=0 is the board's pre-run default, not a tick
        s = bus & self._inmask
        if s != self._prev:
            self._change(t - 1, s, bus)
        self._last_t = t - 1
        dm = self._dom
        d = self._drove
        if d is not None and ((bus >> self.do) & 1) != d:
            if not self._contending:
                self._v(t - 1, f"DO contention: model drives DO {d}, bus shows {d ^ 1} "
                               "(another driver on DO, 3.4)")
                self._contending = True
        else:
            self._contending = False
        q = self._q
        while q and q[0][0] <= t:
            self._do = q.pop(0)[2]
        v = self._do
        if v == "S":
            v = 1 if t >= self._busy_end else 0
        self._drove = v
        if v is None:
            return 0, 0
        return (0, dm) if v else (dm, 0)

    def _change(self, T, s, bus):
        cs = (s >> self.cs) & 1
        clk = (s >> self.clk) & 1
        di = (s >> self.di) & 1
        if self._prev is None:
            self._prev = s
            self._lcs, self._lclk, self._ldi = cs, clk, di
            self._st = _IDLE if cs else _OFF
            return
        self._prev = s
        pcs, pclk, pdi = self._lcs, self._lclk, self._ldi
        self._lcs, self._lclk, self._ldi = cs, clk, di
        lim = self.lim
        if di != pdi:
            if pcs and cs and self._samp_rise is not None:
                self._short(T, T - self._samp_rise, lim["TDIH"], "A8 TDIH: DI hold after rising CLK")
            self._di_t = T
        if cs != pcs:
            if cs:
                self._cs_up(T, clk, di)
            else:
                self._cs_down(T, clk)
        if cs and clk != pclk:
            if clk:
                self._rise(T, di)
            else:
                if self._rise_t is not None:
                    self._short(T, T - self._rise_t, lim["TCKH"], "A2 TCKH: CLK high")
                self._fall_t = T

    def _cs_up(self, T, clk, di):
        if self._cs_fall_t is not None:
            self._short(T, T - self._cs_fall_t, self.lim["TCSL"], "A6 TCSL: CS low")
        if clk and di:
            self._v(T, "2.1 note: CLK and DI both high as CS goes high (one must be low)")
        self._cs_rise_t = T
        self._rise_t = self._fall_t = self._samp_rise = None
        self._st = _IDLE
        if self._latch:
            self._sched(T + self._dsv, "S")

    def _cs_down(self, T, clk):
        if clk and self._rise_t is not None:
            self._v(T, "A5 TCSH: CS fell while CLK still high (hold from CLK fall >= 0 ns)")
        self._cs_fall_t = T
        self._close(T, end=False)
        tz = T + self._dcz
        self._q = [e for e in self._q if e[0] < tz]
        self._sched(tz, None)
        self._st = _OFF
        self._samp_rise = None

    # ------------------------------------------------------------------ protocol
    def _rise(self, T, di):
        lim = self.lim
        if self._rise_t is None:
            if self._cs_rise_t is not None:
                self._short(T, T - self._cs_rise_t, lim["TCSS"], "A4 TCSS: CS setup to first rising CLK")
        else:
            dt = T - self._rise_t
            if dt * self.tick_ns < self._tper - _EPS:
                self._v(T, f"A1 FCLK: CLK period {dt * self.tick_ns:.0f} ns < {self._tper:.0f} ns "
                           f"({lim['FCLK_MHz']:g} MHz max)")
        if self._fall_t is not None:
            self._short(T, T - self._fall_t, lim["TCKL"], "A3 TCKL: CLK low")
        self._rise_t = T
        st = self._st
        if st == _IDLE or st == _SHIFT:
            if self._di_t is not None:
                self._short(T, T - self._di_t, lim["TDIS"], "A7 TDIS: DI setup before rising CLK")
            self._samp_rise = T
        else:
            self._samp_rise = None
        if st == _IDLE:
            if di:
                self._start(T)
        elif st == _SHIFT:
            self._shift(T, di)
        elif st == _READ:
            self._read_bit(T)
        elif st == _DONE:
            if not self._extra:
                self._v(T, f"2.1/3.2: extra CLK after the {self._req(self._op)} required for "
                           f"{self._op} (ignored)")
            self._extra += 1

    def _start(self, T):
        self._st = _SHIFT
        self._n, self._sr, self._op, self._addr = 1, 0, None, None
        self._extra, self._cleared = 0, False
        self._refused = T < self._busy_end
        if self._refused:
            self._v(T, "2.4-2.9: start bit while a self-timed cycle is busy; instruction not executed")
        elif self._latch:
            self._latch = False                # cleared once CS goes low (2.4-2.9 notes)
            self._cleared = True

    def _shift(self, T, di):
        self._sr = (self._sr << 1) | di
        self._n += 1
        n, na = self._n, self.na
        if n == 3 + na:
            opc, a = self._sr >> na, self._sr & (self.size - 1)
            if opc == 2:
                self._op, self._addr = "READ", a
                if not self._refused:
                    self._st = _READ
                    self._raddr, self._rk, self._read_t, self._words = a, 0, T, []
                    self._sched(T + self._dpd, 0)        # dummy zero (2.7)
            elif opc == 3:
                self._op, self._addr = "ERASE", a
                self._complete(T, None)
            elif opc == 1:
                self._op, self._addr = "WRITE", a
            else:
                self._op = ("EWDS", "WRAL", "ERAL", "EWEN")[a >> (na - 2)]
                if self._op != "WRAL":
                    self._complete(T, None)
        elif n == 3 + na + self.nd:
            self._complete(T, self._sr & self.full)

    def _read_bit(self, T):
        nd = self.nd
        k = self._rk
        i = k % nd
        a = (self._raddr + k // nd) % self.size
        w = self.mem[a]
        self._sched(T + self._dpd, (w >> (nd - 1 - i)) & 1)
        if i == nd - 1:
            self._words.append(w)
        self._rk = k + 1

    def _complete(self, T, data):
        op = self._op
        self._st, self._extra = _DONE, 0
        addr = self._addr if op in ("READ", "WRITE", "ERASE") else None
        if self._refused:
            self._rec(T, op, addr, [] if op == "READ" else data, False)
        elif op in ("EWEN", "EWDS"):
            self._ewen = op == "EWEN"
            self._rec(T, op, None, None, True)
        elif self.grade == "C":
            self._initiate(T, op, addr, data)
        else:
            self._pending = (op, addr, data)

    def _initiate(self, T, op, addr, data):
        if not self._ewen:
            self._rec(T, op, addr, data, False)
            return
        if op in ("ERAL", "WRAL") and self.vcc < 4.5:
            self._v(T, f"2.5/2.9: {op} needs VCC >= 4.5 V (VCC {self.vcc} V); not executed")
            self._rec(T, op, addr, data, False)
            return
        if op == "ERASE":
            self.mem[addr] = self.full
        elif op == "WRITE":
            self.mem[addr] = data
        else:
            v = self.full if op == "ERAL" else data
            self.mem[:] = [v] * self.size
        end = T + self._cyc[op]
        self._busy_end = end
        self._latch = True
        self.cycles.append((T, end, op))
        self._rec(T, op, addr, data, True)

    def _partial_op(self):
        n, sr = self._n, self._sr
        if n >= 3:
            opc = sr >> (n - 3)
            if opc:
                return ("WRITE", "READ", "ERASE")[opc - 1]
            if n >= 5:
                return ("EWDS", "WRAL", "ERAL", "EWEN")[(sr >> (n - 5)) & 3]
        return None

    def _close(self, T, end):
        st = self._st
        why = "run ended" if end else "CS fell"
        if st == _SHIFT:
            op = self._op or self._partial_op()
            if self._n == 1 and self._cleared:
                pass                                   # status clear (2.4-2.9 notes)
            else:
                req = self._req(op) or "?"
                self._v(T, f"2.1/3.2: {why} after {self._n} of {req} clocks of {op or 'an instruction'}; "
                           "not executed")
                addr = self._addr if op in ("READ", "WRITE", "ERASE") else None
                self._rec(T, op, addr, [] if op == "READ" else None, False)
        elif st == _READ:
            w = self._words
            if not w:
                self._v(T, f"2.1/3.2: {why} after {self._n + self._rk} of {self._req('READ')} "
                           "clocks of READ; not executed")
            elif self._rk % self.nd:
                self._v(T, f"2.7: sequential READ ended {self._rk % self.nd} bits into word {len(w) + 1}")
            self._rec(self._read_t, "READ", self._raddr, list(w), bool(w))
        elif st == _DONE and self._pending is not None:
            op, addr, data = self._pending
            if end:
                self._v(T, f"2.4-2.9: {why} with {op} clocked in but CS never fell; cycle not started")
                self._rec(T, op, addr, data, False)
            else:
                self._initiate(T, op, addr, data)
        self._pending = None

    def finish(self, t=None):
        """Close anything open at the end of a run (t: the last tick; default the last seen)."""
        if self._st != _OFF:
            self._close(self._last_t if t is None else t, end=True)
            self._st = _OFF if not self._lcs else _DONE
            self._extra = 1                    # no extra-clock message after the end

    def errors(self):
        return list(self.violations)


# ---------------------------------------------------------------------- self-test
class _Ctl:
    """Synthetic bit-banged Microwire controller driving CS/CLK/DI push-pull (self-test only).

    A generator: each yield hands the board one tick of (out, oe) and receives that tick's
    bus level. Clock high/low phases come from the model's limits unless overridden."""

    def __init__(self, m: Eeprom93, period=None, high=None, setup=None, tcsl=None, tcss=None,
                 rz_hold=None):
        tk = lambda ns: max(1, ceil(ns / m.tick_ns - 1e-9))  # noqa: E731
        lim = m.lim
        self.m = m
        self.high = high or max(tk(lim["TCKH"]), m._dpd + 1, tk(lim["TDIH"]))
        per = period or max(tk(1000.0 / lim["FCLK_MHz"]), self.high + tk(lim["TCKL"]))
        self.low = per - self.high
        self.setup = self.low if setup is None else setup
        self.tcsl = tk(lim["TCSL"]) if tcsl is None else tcsl
        self.tcss = tk(lim["TCSS"]) if tcss is None else tcss
        self.rz_hold = rz_hold                 # DI returns to 0 this many ticks after the rise
        self.cs = self.clk = self.di = 0
        self.drive_do = None
        self.lvl = 0xFF
        self.t = 0
        self.last_rise = self.last_csfall = None

    def out(self):
        m = self.m
        o = (self.cs << m.cs) | (self.clk << m.clk) | (self.di << m.di)
        oe = (1 << m.cs) | (1 << m.clk) | (1 << m.di)
        if self.drive_do is not None:
            o |= self.drive_do << m.do
            oe |= 1 << m.do
        return o, oe

    def tick(self, n=1):
        for _ in range(n):
            self.lvl = yield self.out()
            self.t += 1

    def do(self):
        return (self.lvl >> self.m.do) & 1

    def bit(self, b):
        su = min(self.setup, self.low)
        yield from self.tick(self.low - su)
        self.di = b
        yield from self.tick(su)
        self.clk = 1
        self.last_rise = self.t
        if self.rz_hold is not None and self.rz_hold < self.high:
            yield from self.tick(self.rz_hold)
            self.di = 0
            yield from self.tick(self.high - self.rz_hold)
        else:
            yield from self.tick(self.high)
        s = self.do()                          # level at the last high tick
        self.clk = 0
        return s

    def cs_on(self):
        self.cs = 1
        yield from self.tick(self.tcss)

    def cs_off(self):
        yield from self.tick(1)                # CLK falls
        self.cs = 0
        self.last_csfall = self.t
        yield from self.tick(self.tcsl)

    def op(self, bits, extra=0):
        yield from self.cs_on()
        smp = []
        for b in list(bits) + [0] * extra:
            s = yield from self.bit(b)
            smp.append(s)
        yield from self.cs_off()
        return smp

    def poll(self, limit=200000):
        """CS high, wait TSV, return (saw_busy, ready) after DO goes high."""
        self.cs = 1
        yield from self.tick(self.m._dsv + 1)
        busy = False
        n = 0
        while self.do() == 0 and n < limit:
            busy = True
            yield from self.tick(1)
            n += 1
        ready = self.do() == 1
        yield from self.tick(1)
        self.cs = 0
        yield from self.tick(self.tcsl)
        return busy, ready


def _bits(v, n):
    return [(v >> (n - 1 - i)) & 1 for i in range(n)]


def _ins(m, op, addr=0, data=0):
    na, nd = m.na, m.nd
    if op == "READ":
        return [1, 1, 0] + _bits(addr, na)
    if op == "WRITE":
        return [1, 0, 1] + _bits(addr, na) + _bits(data, nd)
    if op == "ERASE":
        return [1, 1, 1] + _bits(addr, na)
    sub = {"EWDS": 0, "WRAL": 1, "ERAL": 2, "EWEN": 3}[op]
    b = [1, 0, 0] + _bits(sub, 2) + [0] * (na - 2)
    return b + _bits(data, nd) if op == "WRAL" else b


def _words(m, op_bits, smp):
    """Dummy bit and the words read back from the samples of a READ."""
    k = len(op_bits)
    dummy = smp[k - 1]
    d = smp[k:]
    nd = m.nd
    return dummy, [int("".join(map(str, d[i:i + nd])), 2) for i in range(0, len(d) - nd + 1, nd)]


def _run(m, gen):
    b = Board(m)
    t = 0
    try:
        out, oe = next(gen)
        while True:
            lvl = b(t, out, oe)
            t += 1
            out, oe = gen.send(lvl)
    except StopIteration:
        pass
    m.finish()
    return b


def _legal(m: Eeprom93, ctl: _Ctl, log: list):
    """Generator: the legal sequence. Appends failure strings to log."""
    nd, full = m.nd, m.full
    x, y, z = 0xBEEF & full, 0x1234 & full, 0xA5A5 & full

    def chk(cond, what):
        if not cond:
            log.append(what)

    def prog(bits, op):
        yield from ctl.op(bits)
        o = m.ops[-1]
        want_t = ctl.last_rise if m.grade == "C" else ctl.last_csfall
        chk(o["op"] == op and o["executed"] and o["t"] == want_t,
            f"{op} start: {o} (expected t={want_t})")
        busy, ready = yield from ctl.poll()
        chk(busy and ready, f"{op} status busy={busy} ready={ready}")

    def read(addr, nwords):
        b = _ins(m, "READ", addr)
        smp = yield from ctl.op(b, extra=nwords * nd)
        dummy, w = _words(m, b, smp)
        chk(dummy == 0, f"READ dummy bit {dummy}")
        return w

    yield from ctl.tick(20)
    yield from ctl.op(_ins(m, "EWEN"))
    chk(m.ops[-1]["op"] == "EWEN" and m.ops[-1]["executed"], f"EWEN {m.ops[-1]}")
    yield from prog(_ins(m, "WRITE", 5, x), "WRITE")
    w = yield from read(5, 1)
    chk(w == [x], f"READ 5 -> {w}")
    chk(m.ops[-1]["data"] == [x], f"READ op {m.ops[-1]}")
    yield from prog(_ins(m, "WRITE", 6, y), "WRITE")
    w = yield from read(5, 3)
    chk(w == [x, y, full], f"sequential READ -> {w}")
    chk(m.ops[-1]["data"] == [x, y, full] and m.ops[-1]["executed"], f"seq READ op {m.ops[-1]}")
    w = yield from read(m.size - 1, 2)                       # wraps to address 0
    chk(w == [m.mem[-1], m.mem[0]], f"wrap READ -> {w}")
    yield from prog(_ins(m, "ERASE", 5), "ERASE")
    w = yield from read(5, 1)
    chk(w == [full], f"READ after ERASE -> {w}")
    if m.vcc >= 4.5:                                        # 2.5 / 2.9: VCC >= 4.5 V
        yield from prog(_ins(m, "WRAL", 0, z), "WRAL")
        chk(m.mem == [z] * m.size, "WRAL memory")
        w = yield from read(10, 1)
        chk(w == [z], f"READ after WRAL -> {w}")
        yield from prog(_ins(m, "ERAL"), "ERAL")
        chk(m.mem == [full] * m.size, "ERAL memory")
    yield from ctl.op(_ins(m, "EWDS"))
    chk(m.ops[-1]["op"] == "EWDS" and m.ops[-1]["executed"], f"EWDS {m.ops[-1]}")
    yield from ctl.op(_ins(m, "WRITE", 3, 0))
    o = m.ops[-1]
    chk(o["op"] == "WRITE" and not o["executed"] and o["addr"] == 3, f"refused WRITE {o}")
    yield from ctl.tick(m._cyc["WRITE"] + 10)
    chk(m.mem[3] == full, "refused WRITE changed memory")
    w = yield from read(3, 1)
    chk(w == [full], f"READ after refused WRITE -> {w}")
    yield from ctl.tick(20)


def _illegal_cases():
    """(name, model kwargs, controller kwargs, script(m, ctl), expected tag)."""
    def fast(m, ctl):
        yield from ctl.op(_ins(m, "EWEN"))

    def ewen_write(m, ctl):
        yield from ctl.op(_ins(m, "EWEN"))
        yield from ctl.op(_ins(m, "WRITE", 1, 0x55))

    def busy(m, ctl):
        yield from ctl.op(_ins(m, "EWEN"))
        yield from ctl.op(_ins(m, "WRITE", 1, 0x55))
        yield from ctl.op(_ins(m, "READ", 1), extra=m.nd)   # while still busy

    def cut(m, ctl):
        yield from ctl.op(_ins(m, "EWEN"))
        yield from ctl.op(_ins(m, "WRITE", 1, 0x55)[:10])

    def extra(m, ctl):
        yield from ctl.op(_ins(m, "EWEN"), extra=2)

    def contention(m, ctl):
        yield from ctl.cs_on()
        for b in _ins(m, "READ", 0):
            yield from ctl.bit(b)
        ctl.drive_do = 0                       # the chip drives DO low against the data 1s
        for _ in range(m.nd):
            yield from ctl.bit(0)
        ctl.drive_do = None
        yield from ctl.cs_off()

    def eral_low_vcc(m, ctl):
        yield from ctl.op(_ins(m, "EWEN"))
        yield from ctl.op(_ins(m, "ERAL"))

    return [
        ("clock too fast", {}, {"period": 8, "high": 4}, fast, "A1 FCLK"),
        ("clock high too short", {}, {"period": 14, "high": 3}, fast, "A2 TCKH"),
        ("DI setup too short", {}, {"setup": 1}, fast, "A7 TDIS"),
        ("DI hold too short", {}, {"rz_hold": 1}, fast, "A8 TDIH"),
        ("CS low too short", {}, {"tcsl": 3}, ewen_write, "A6 TCSL"),
        ("instruction while busy", {}, {}, busy, "busy"),
        ("instruction cut short", {}, {}, cut, "not executed"),
        ("extra clocks", {}, {}, extra, "extra CLK"),
        ("DO contention", {}, {}, contention, "DO contention"),
        ("ERAL at 3.3 V", {"grade": "AA", "vcc": 3.3}, {}, eral_low_vcc, "2.5/2.9"),
    ]


def _selftest(verbose=True) -> bool:
    ok = True

    def say(s):
        if verbose:
            print(s)

    configs = [
        ("x16 93LC46 5.0 V", {"org16": True, "grade": "LC", "vcc": 5.0}),
        ("x8 93LC46 5.0 V", {"org16": False, "grade": "LC", "vcc": 5.0}),
        ("x16 93C46 5.0 V", {"org16": True, "grade": "C", "vcc": 5.0}),
        ("x8 93C46 5.0 V", {"org16": False, "grade": "C", "vcc": 5.0}),
        ("x16 93AA46 2.0 V", {"org16": True, "grade": "AA", "vcc": 2.0}),
        ("x8 93LC46C 5.0 V ORG, 3 MHz rows", {"org16": False, "grade": "LC", "vcc": 5.0, "orgpin": True}),
    ]
    for name, kw in configs:
        m = Eeprom93(twc_us=20, **kw)
        ctl = _Ctl(m)
        log: list[str] = []
        b = _run(m, _legal(m, ctl, log))
        errs = m.errors() + [f"board: {e}" for e in b.errors() if not e.startswith("EEPROM")]
        n_ops = len(m.ops)
        good = not log and not errs
        ok &= good
        say(f"{'PASS' if good else 'FAIL'} legal {name}: {n_ops} ops, {ctl.t} ticks")
        for e in (log + errs)[:10]:
            say(f"    {e}")

    for name, mkw, ckw, script, tag in _illegal_cases():
        m = Eeprom93(twc_us=20, **mkw)
        ctl = _Ctl(m, **ckw)
        _run(m, script(m, ctl))
        errs = m.errors()
        hit = any(tag in e for e in errs)
        extra = ""
        if name == "instruction while busy":
            o = m.ops[-1]
            hit &= o["op"] == "READ" and not o["executed"]
            extra = f" op={o}"
        if name == "instruction cut short":
            o = m.ops[-1]
            hit &= o["op"] == "WRITE" and not o["executed"] and m.mem[1] == m.full
        if name == "extra clocks":
            hit &= m.ops[-1]["op"] == "EWEN" and m.ops[-1]["executed"]
        ok &= hit
        say(f"{'PASS' if hit else 'FAIL'} illegal {name}: {len(errs)} violation(s){extra}")
        for e in errs[:3]:
            say(f"    {e}")
    say("eeprom93 selftest " + ("PASSED" if ok else "FAILED"))
    return ok
