"""SNES standard controller model: two cascaded CD4021B shift registers plus the cable.

The chip under test is the console. It drives LATCH (wired to PARALLEL/SERIAL CONTROL,
"P/S", of both registers) and CLOCK (wired to CLOCK of both registers), and reads DATA
(Q8 of the first register). The model drives DATA push-pull, as a CMOS output does, while
it is connected. Everything is at logic level, one evaluation per chip clock tick.

Sources
  [DS]  TI (Harris) CD4014B/CD4021B data sheet, SCHS024C, revised October 2003: Fig. 2
        (logic diagram for CD4021B), the CD4021B truth table, Recommended Operating
        Conditions and Dynamic Electrical Characteristics (TA = 25 C, CL = 50 pF), VDD = 5 V.
  [PAT] Nintendo, US patent 5,207,426, "Controller for a game machine" (1993),
        description of FIG. 7: signal processing circuit 18 is "a shift register capable of
        parallel input and serial output"; a parallel write signal from the CPU stores the
        key states, then a serial read signal shifts them out "in synchronization with a
        clock signal CL".
  [MAN] Nintendo SNES Development Manual, Book I (1993): 13.2 (2-13-1), "Up to 16 bits can
        be read automatically for one standard controller"; Programming Cautions 24.8
        (2-24-2) signature codes 0000B standard controller, 0001B mouse, 1111B Super Scope
        in D3-D0 of 4218H/421AH; 24.12 (2-24-3) "use all 16 bits of data including the input
        device's signature", Start+Select+L+R alone = key data 3030H, and "corrupt data may
        be sent by the controller if the controller is unplugged during a game"; register
        page 2-28-9: 4219H (high) = B, Y, Select, Start, Up, Down, Left, Right (D7..D0),
        4218H (low) = A, X, L, R in D7..D4; NOTE: whether a standard controller is connected
        is read from the 17th bit of 4016H/4017H, 1 = connected, 0 = not connected.
  [BRIEF] The test brief: a pressed button is a low level on the wire, 1 in the console
        register (the register is the inverted line).

Structure (what is stated and what is inferred)
  * A parallel-in, serial-out shift register loaded by the console and clocked out [PAT].
    16 bits [MAN 13.2, 24.12] from 8-stage CD4021B parts means two cascaded registers:
    that is an inference ("as the documents imply"), not a stated part list.
  * Register value (`buttons` attribute and record) = 16 bits laid out as 4219H:4218H
    [MAN 2-28-9]: bit 15 B, 14 Y, 13 Select, 12 Start, 11 Up, 10 Down, 9 Left, 8 Right,
    7 A, 6 X, 5 L, 4 R, bits 3..0 the signature [MAN 24.8].
  * Serial order: register bit 15 (B) comes out first, bit 0 (signature D0) sixteenth.
    INFERENCE: the manual gives the register layout and says the serial data "will be
    converted to parallel data"; that the first bit lands in D7 of the high byte is my
    reading (MSB first), not a sentence in the manual.
  * Line level = inverted register bit [BRIEF]. So a released button is a high line; the
    standard signature 0000 is four HIGH bits on the wire (positions 12..15); mouse 0001
    makes only the 16th bit LOW; Super Scope 1111 makes the last four LOW. That the
    signature levels come from parallel inputs strapped to VDD/VSS inside the device, and
    that the buttons are switches to ground with pull-ups, is INFERENCE.
  * After the 16th bit: the second register's SERIAL INPUT is assumed tied LOW, so every
    later bit is a low line (register 1). INFERENCE from [MAN 2-28-9] NOTE (17th bit
    reads 1 = connected) and the inverted register: the 17th bit must be a low line.
  * Unplugged (`connected=False`): DATA is not driven at all. For the 17th bit to read
    0 = "not connected" [MAN 2-28-9], an undriven DATA must read HIGH at the console, i.e.
    the console has a pull-up on DATA. That pull-up is INFERENCE; the model drives
    nothing and board.py decides the level. With Board's default pullups=0xFF the line
    reads high throughout, so every register bit reads 0 (no buttons, signature 0000) and
    the 17th bit reads 0 = not connected. `connected` is a plain attribute read every
    tick, so a test may unplug the pad mid-read.

CD4021B behaviour used [DS Fig. 2, truth table]
  * P/S high: asynchronous parallel load, Qn = PI-n, CLOCK is don't-care (Fig. 2 gates
    CLOCK with P/S; the data sheet text: the internal clock is "forced" during asynchronous
    parallel entry). The model loads at the P/S rising edge; the buttons are constant for
    the whole pulse (a list is consumed one value per pulse, at its rising edge).
  * P/S low, CLOCK rising: Q1 = SERIAL IN, Qn = Qn-1. Falling CLOCK: no change.
  * Q8 of register 1 drives DATA; Q8 of register 2 feeds SERIAL IN of register 1.
  * The internal cascade is modelled as an atomic shift. [DS] serial-input set-up 120 ns
    and hold 0 ns (ref. to CL) concern that internal connection, and the footnote to the
    Dynamic table (cascaded units: tr,tf of CL <= transition time + propagation delay of
    the driving stage) is a clock-edge-rate rule; neither is visible at logic level.
  * Power-up contents are unspecified by [DS]; the model starts as if loaded with the
    first `buttons` value (matters only before the first latch pulse).

Output timing (`timing`) [DS Dynamic Electrical Characteristics, VDD = 5 V]
  The output changes D ticks after the tick the CLOCK rising edge (shift) or the P/S
  rising edge (parallel load) appears on the bus; until then DATA holds the old value
  (the data sheet's "unknown / changing" window is modelled as the old value).
    "max": tPLH/tPHL max 320 ns + tTHL/tTLH max 200 ns = 520 ns -> 13 ticks at 40 ns
    "typ": tPLH/tPHL typ 160 ns + tTHL/tTLH typ 100 ns = 260 ns -> 7 ticks at 40 ns
    "min": 1 tick (the board's one-tick reaction; no minimum delay is specified)
  D = max(1, ceil(ns / tick_ns)). The data sheet gives one propagation delay figure; it is
  applied to both the CLOCK->Q8 and the P/S->Q8 (parallel load) paths (assumption).

Checks on the console's drive of LATCH and CLOCK, VDD = 5 V [DS]
  Each failure appends a message "t=<tick>: SNES pad: ... (<reference>)" to `violations`.
  1. P/S pulse width >= 160 ns: Recommended Operating Conditions, "Parallel/Serial Pulse
     Width tW (CD4021B)" min 160 ns (= Dynamic "Minimum P/S Pulse Width tWH" max).
  2. P/S removal time >= 280 ns: Recommended Operating Conditions "Parallel/Serial Removal
     Time tREM (CD4021B)" min 280 ns (= Dynamic "Minimum P/S Removal Time tREM, ref. to
     CL" max): measured from P/S falling to each CLOCK rising edge while P/S is low (only
     the first such edge can fail).
  3. CLOCK high and low pulse widths >= 180 ns: Recommended Operating Conditions "Clock
     Pulse Width tW" min 180 ns (= Dynamic "Minimum Clock Pulse Width" max). High width is
     checked for pulses whose rising edge shifts (P/S low); low width for low phases that
     begin with P/S low and end in a shifting rising edge. Clock activity during P/S high
     is ignored by the CD4021B and not checked (a low phase that began during P/S high is
     covered by check 2, 280 ns > 180 ns).
  4. Clock frequency <= 3 MHz (period >= 333.3 ns) between consecutive shifting rising
     edges: Recommended Operating Conditions "Clock Frequency fCL" max 3 MHz (= Dynamic
     "Maximum Clock Input Frequency" min). With check 3 (2 x 180 ns = 360 ns) this cannot
     fail alone at 5 V; it is kept as stated.
  Not checkable at logic level, so not checked: clock rise/fall time <= 15 us
  (Recommended Operating Conditions tr,tf CL) and the cascade footnote; input levels
  VIL <= 1.5 V / VIH >= 3.5 V (Static characteristics); parallel-input set-up 50 ns ref.
  to P/S and hold 0 ns (the parallel inputs are the pad's own buttons, constant through a
  pulse, and check 1 covers 160 ns > 50 ns); serial-input set-up/hold (internal cascade).
  When the console samples DATA is not checked: the model cannot see it; a console that
  samples inside the output delay simply reads the old level.
  The number of bits read (fewer or more than 16) is recorded, never flagged; what the
  console reports is for the tests to decide.

Records
  `latches`: one dict per observed P/S pulse (its rising edge seen):
     {"t": tick P/S rose, "t_fall": tick P/S fell (None while high), "width_ns",
      "buttons": 16-bit register value loaded (buttons | signature, 1 = pressed),
      "clocks": rising CLOCK edges with P/S low until the next P/S rise,
      "bits": DATA line levels the pad put out, in order: bits[0] is the level after the
      load, then one per shifting edge (so len(bits) == clocks + 1 when connected; levels
      are appended only while connected)}.
  Ticks are the bus tick at which the edge was seen (`step` is called at t with the
  level of tick t-1, so the edge tick is t-1); widths are differences of edge ticks.
  `violations`: list of messages; `errors()` returns them (first 20, then a count);
  `finish()` returns errors() at the end of a run.

Faults (`faults` dict, default none)
  {"stuck": (pos, level)} or {"stuck": [(pos, level), ...]}: serial position pos (0 = the
      first bit on DATA after the load, 15 = the 16th) always carries line level `level`
      (0/1), as a shorted or open parallel input would; applied after "swap".
  {"swap": True}: the two registers' cascade order swapped: the register holding A, X,
      L, R and the signature drives DATA, so the wire order is the low byte (bits 7..0)
      then the high byte (bits 15..8).
  {"serial_in": level}: the second register's SERIAL INPUT level (default 0, see above);
      1 makes every bit after the 16th a high line (reads as "not connected").
"""

from __future__ import annotations

import math
from collections import deque

from .board import Board, Device

# CD4021B at VDD = 5 V, ns [DS]
PS_PULSE_MIN_NS = 160.0      # P/S pulse width tW (CD4021B), Recommended Operating Conditions
PS_REMOVAL_MIN_NS = 280.0    # P/S removal time tREM (CD4021B), ref. to CL
CLK_PULSE_MIN_NS = 180.0     # clock pulse width tW
CLK_FREQ_MAX_HZ = 3.0e6      # clock frequency fCL
TPD_NS = {"typ": 160.0, "max": 320.0}    # propagation delay tPLH, tPHL
TT_NS = {"typ": 100.0, "max": 200.0}     # transition time tTHL, tTLH

_REF_ROC = "CD4021B SCHS024C Recommended Operating Conditions, VDD=5 V"

BUTTON_BITS = {"b": 15, "y": 14, "select": 13, "start": 12, "up": 11, "down": 10,
               "left": 9, "right": 8, "a": 7, "x": 6, "l": 5, "r": 4}


def button_mask(v) -> int:
    """An int (register layout, bits 15..4) or an iterable of button names -> mask."""
    if isinstance(v, int):
        return v & 0xFFF0
    m = 0
    for name in v:
        key = str(name).strip().lower()
        if key not in BUTTON_BITS:
            raise ValueError(f"unknown SNES button {name!r}")
        m |= 1 << BUTTON_BITS[key]
    return m


class SnesPad(Device):
    def __init__(self, latch: int = 0, clock: int = 1, data: int = 2, tick_ns: float = 40.0,
                 buttons=0, signature: int = 0b0000, timing: str = "max",
                 connected: bool = True, faults: dict | None = None):
        self.latch, self.clock, self.data = latch, clock, data
        self.tick_ns = float(tick_ns)
        if isinstance(buttons, list):
            if not buttons:
                raise ValueError("buttons list is empty")
            self._seq = [button_mask(b) for b in buttons]
        else:
            self._seq = [button_mask(buttons)]
        self.signature = signature & 0xF
        if timing == "min":
            self.delay_ns = 0.0
        elif timing in TPD_NS:
            self.delay_ns = TPD_NS[timing] + TT_NS[timing]
        else:
            raise ValueError(f"timing must be 'max', 'typ' or 'min', not {timing!r}")
        self.timing = timing
        self.delay = max(1, math.ceil(self.delay_ns / self.tick_ns - 1e-9))
        self.connected = connected
        f = dict(faults or {})
        unknown = set(f) - {"stuck", "swap", "serial_in"}
        if unknown:
            raise ValueError(f"unknown fault(s) {sorted(unknown)}")
        st = f.get("stuck")
        if st is None:
            st = []
        elif isinstance(st, tuple):
            st = [st]
        self._stuck_set = self._stuck_clr = 0
        for pos, level in st:
            if not 0 <= pos <= 15:
                raise ValueError("stuck position must be 0..15")
            if level:
                self._stuck_set |= 1 << (15 - pos)
            else:
                self._stuck_clr |= 1 << (15 - pos)
        self._swap = bool(f.get("swap", False))
        self._ser = 1 if f.get("serial_in", 0) else 0
        self.faults = f

        self.latches: list[dict] = []
        self.violations: list[str] = []
        self._mask = 1 << data
        self._n = 0                          # latch pulses seen (index into the list)
        self._sr = self._wire(self._seq[0] | self.signature)   # bit 15 = Q8 -> DATA
        self._out = (self._sr >> 15) & 1
        self._q: deque = deque()             # (tick, level) pending output changes
        self._pps = self._pck = None
        self._rec = None
        self._t_ps_fall = None
        self._t_rise = None                  # last shifting rising edge
        self._t_fall = None                  # last falling edge that began with P/S low
        self._ps_min_t = PS_PULSE_MIN_NS
        self._period_min = 1e9 / CLK_FREQ_MAX_HZ

    # register value -> 16 line levels, bit 15 = first serial position
    def _wire(self, reg: int) -> int:
        s = ~reg & 0xFFFF
        if self._swap:
            s = ((s & 0xFF) << 8) | (s >> 8)
        return (s | self._stuck_set) & ~self._stuck_clr & 0xFFFF

    def _v(self, t, msg):
        self.violations.append(f"t={t}: SNES pad: {msg}")

    def step(self, t, bus):
        ps = (bus >> self.latch) & 1
        ck = (bus >> self.clock) & 1
        if ps != self._pps or ck != self._pck:
            if self._pps is None:            # first tick: take the levels as they are
                if ps:                       # loading (no pulse record: its rise unseen)
                    self._sr = self._wire(self._seq[0] | self.signature)
                    self._out = (self._sr >> 15) & 1
            else:
                te = t - 1
                if ps != self._pps:
                    self._ps_edge(te, ps)
                if ck != self._pck:
                    self._ck_edge(te, ck, ps)
            self._pps, self._pck = ps, ck
        q = self._q
        while q and q[0][0] <= t:
            self._out = q.popleft()[1]
        if not self.connected:
            return 0, 0
        m = self._mask
        return (0, m) if self._out else (m, 0)

    def _ps_edge(self, te, ps):
        if ps:                               # parallel load (asynchronous)
            reg = self._seq[min(self._n, len(self._seq) - 1)] | self.signature
            self._n += 1
            self._sr = self._wire(reg)
            lvl = (self._sr >> 15) & 1
            self._q.append((te + self.delay, lvl))
            self._rec = {"t": te, "t_fall": None, "width_ns": None, "buttons": reg,
                         "clocks": 0, "bits": [lvl] if self.connected else []}
            self.latches.append(self._rec)
            self._t_rise = None              # the next read starts a new clock train
        else:
            rec = self._rec
            if rec is not None and rec["t_fall"] is None:
                rec["t_fall"] = te
                w = (te - rec["t"]) * self.tick_ns
                rec["width_ns"] = w
                if w < PS_PULSE_MIN_NS:
                    self._v(te, f"P/S (LATCH) pulse width {w:.0f} ns < {PS_PULSE_MIN_NS:.0f} ns "
                                f"({_REF_ROC}, P/S pulse width tW)")
                self._t_ps_fall = te

    def _ck_edge(self, te, ck, ps):
        tn = self.tick_ns
        if not ck:                           # falling edge: no change in the register
            if self._t_rise is not None and not ps:
                w = (te - self._t_rise) * tn
                if w < CLK_PULSE_MIN_NS:
                    self._v(te, f"CLOCK high pulse {w:.0f} ns < {CLK_PULSE_MIN_NS:.0f} ns "
                                f"({_REF_ROC}, clock pulse width tW)")
            self._t_fall = None if ps else te
            return
        if ps:                               # gated off while P/S is high
            self._t_rise = None
            self._t_fall = None
            return
        if self._t_ps_fall is not None:
            d = (te - self._t_ps_fall) * tn
            if d < PS_REMOVAL_MIN_NS:
                self._v(te, f"CLOCK rose {d:.0f} ns after P/S (LATCH) fell, < "
                            f"{PS_REMOVAL_MIN_NS:.0f} ns ({_REF_ROC}, P/S removal time tREM)")
        if self._t_fall is not None:
            w = (te - self._t_fall) * tn
            if w < CLK_PULSE_MIN_NS:
                self._v(te, f"CLOCK low pulse {w:.0f} ns < {CLK_PULSE_MIN_NS:.0f} ns "
                            f"({_REF_ROC}, clock pulse width tW)")
        if self._t_rise is not None:
            p = (te - self._t_rise) * tn
            if p < self._period_min:
                self._v(te, f"CLOCK period {p:.0f} ns = {1e3 / p:.2f} MHz > "
                            f"{CLK_FREQ_MAX_HZ / 1e6:.0f} MHz ({_REF_ROC}, clock frequency fCL)")
        self._sr = ((self._sr << 1) & 0xFFFF) | self._ser
        lvl = (self._sr >> 15) & 1
        self._q.append((te + self.delay, lvl))
        rec = self._rec
        if rec is not None:
            rec["clocks"] += 1
            if self.connected:
                rec["bits"].append(lvl)
        self._t_rise = te
        self._t_fall = None

    def errors(self):
        v = self.violations
        if len(v) <= 20:
            return list(v)
        return v[:20] + [f"SNES pad: ... and {len(v) - 20} more violations"]

    def finish(self):
        return self.errors()


# ---------------------------------------------------------------------------------------
def _console(pad, n_reads, latch, removal, hi, lo, nbits=16, gap=40):
    """Bit-banged console: CLOCK idles high; LATCH high for `latch` ticks; first CLOCK
    rising edge `removal` ticks after LATCH falls; CLOCK low `lo` / high `hi` ticks; DATA
    sampled on the last tick before each rising edge; `nbits` rising edges per read."""
    L, C = 1 << pad.latch, 1 << pad.clock
    b = Board(pad)
    t = 0

    def run(lat, clk, n):
        nonlocal t
        out, lvl = (L if lat else 0) | (C if clk else 0), 0
        for _ in range(n):
            lvl = b(t, out, L | C)
            t += 1
        return lvl

    reads = []
    run(0, 1, gap)
    for _ in range(n_reads):
        run(1, 1, latch)
        run(0, 1, removal - lo)
        bits = []
        for _ in range(nbits):
            bits.append((run(0, 0, lo) >> pad.data) & 1)
            run(0, 1, hi)
        run(0, 1, gap)
        reads.append(bits)
    return reads, b


def _expect(reg, nbits=16, connected=True):
    if not connected:
        return [1] * nbits
    return [1 - ((reg >> (15 - k)) & 1) for k in range(16)] + [0] * (nbits - 16)


def _selftest() -> bool:
    fails: list[str] = []

    def ok(cond, what):
        if not cond:
            fails.append(what)

    slow = (300, 150, 150, 150)              # 12 us latch, 6 us half periods (40 ns ticks)
    near = (4, 7, 5, 5)                      # 160 ns latch, 280 ns removal, 200/200 ns clock
    safe = (4, 10, 5, 9)                     # near the limits, yet valid after 520 ns at max
    combos = [0, 0xFFF0] + [1 << i for i in range(4, 16)]
    for timing in ("max", "typ", "min"):
        consoles = [("slow", slow), ("safe", safe)] + ([("near", near)] if timing != "max" else [])
        for cname, cons in consoles:
            for reg in combos:
                pad = SnesPad(buttons=reg, timing=timing)
                reads, b = _console(pad, 2, *cons, nbits=17)
                exp = _expect(reg, 17)
                tag = f"{timing}/{cname}/0x{reg:04x}"
                ok(reads == [exp, exp], f"{tag}: read {reads} != {exp}")
                ok(not pad.errors() and not b.contention, f"{tag}: {pad.errors()} {b.contention}")
                r = pad.latches
                ok(len(r) == 2 and all(x["clocks"] == 17 and x["buttons"] == reg and
                                       x["bits"] == exp + [0] and
                                       x["t_fall"] - x["t"] == cons[0] and
                                       x["width_ns"] == cons[0] * 40.0 for x in r),
                   f"{tag}: records {r}")
        # signatures (mouse, Super Scope) with buttons, names, list per latch
        for sig in (0b0001, 0b1111):
            pad = SnesPad(buttons={"A", "Start"}, signature=sig, timing=timing)
            reads, _ = _console(pad, 1, *slow, nbits=17)
            reg = 0x1080 | sig
            ok(reads == [_expect(reg, 17)], f"{timing}: signature {sig:04b}: {reads}")
        pad = SnesPad(buttons={"Start", "Select", "L", "R"}, timing=timing)
        _console(pad, 1, *slow)
        ok(pad.latches[0]["buttons"] == 0x3030, f"{timing}: 3030H [MAN 24.12]")
        seq = [0x8000, 0x0010, 0x0800]
        pad = SnesPad(buttons=seq, timing=timing)
        reads, _ = _console(pad, 4, *slow)
        ok(reads == [_expect(v) for v in seq + seq[-1:]], f"{timing}: button list {reads}")
        ok([x["buttons"] for x in pad.latches] == seq + seq[-1:], f"{timing}: list records")
        # unplugged: nothing driven, the pull-up reads high, 17th bit = not connected
        pad = SnesPad(buttons=0xFFF0, timing=timing, connected=False)
        reads, b = _console(pad, 2, *slow, nbits=17)
        ok(reads == [[1] * 17] * 2 and not pad.errors(), f"{timing}: unplugged {reads}")
        ok(len(pad.latches) == 2 and pad.latches[0]["bits"] == [] and
           pad.latches[0]["clocks"] == 17, f"{timing}: unplugged records {pad.latches}")
        # short and long reads are recorded, not flagged
        pad = SnesPad(buttons=0x4000, timing=timing)
        _console(pad, 1, *slow, nbits=8)
        ok(len(pad.latches) == 1 and pad.latches[0]["clocks"] == 8 and not pad.errors(),
           f"{timing}: 8-bit read")
        # faults
        pad = SnesPad(buttons=0x8000, timing=timing, faults={"stuck": (0, 1)})
        reads, _ = _console(pad, 1, *slow)
        ok(reads[0] == [1] * 16, f"{timing}: stuck {reads}")
        pad = SnesPad(buttons=0x0080, timing=timing, faults={"swap": True})
        reads, _ = _console(pad, 1, *slow)
        ok(reads[0] == [0] + [1] * 15, f"{timing}: swap {reads}")
        pad = SnesPad(timing=timing, faults={"serial_in": 1})
        reads, _ = _console(pad, 1, *slow, nbits=17)
        ok(reads[0][16] == 1, f"{timing}: serial_in {reads}")

    # at "max" (520 ns) a 400 ns clock sampled just before the edge reads one bit late
    for reg in (0x8000, 0x0010, 0xA5A0):
        pad = SnesPad(buttons=reg, timing="max")
        reads, _ = _console(pad, 2, *near)
        exp = _expect(reg)
        ok(reads[1][1:] == exp[:-1] and not pad.errors(), f"max/near: {reads} vs {exp}")

    # illegal consoles
    bad = [("latch too short", (3, 150, 150, 150), "P/S (LATCH) pulse width"),
           ("clock too fast", (300, 150, 4, 4), "CLOCK period"),
           ("clock high too short", (300, 150, 4, 150), "CLOCK high pulse"),
           ("clock low too short", (300, 150, 150, 4), "CLOCK low pulse"),
           ("edge too soon after latch", (300, 6, 150, 5), "P/S removal time")]
    for name, cons, key in bad:
        pad = SnesPad(buttons=0x8000)
        _console(pad, 1, *cons)
        errs = pad.finish()
        ok(errs and any(key in e for e in errs), f"illegal '{name}': {errs}")
    pad = SnesPad()
    _console(pad, 1, 300, 150, 150, 4)
    ok(not any("high pulse" in e or "removal" in e or "P/S (LATCH)" in e
               for e in pad.errors()), f"only low pulses flagged: {pad.errors()}")

    for f in fails:
        print("FAIL", f)
    if not fails:
        print("snes selftest: all pass")
    return not fails
