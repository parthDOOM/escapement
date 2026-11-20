"""Receiver firmware from the same timing tables (escgen, receive side).

`generate_rx(t)` turns a transmitter's table into a program that listens on the table's
pin and PUSHes, for every packet it hears, the word that the transmitter would have to
be given to send it. So a protocol learned from a recording (sw/esclearn) can be
received as well as sent, including words that were never recorded.

What it reads is a pulse code: every phase boundary is an edge. The packet (`packet:
before`, the frame, and `after` up to its first mark) becomes a list of runs, a mark
(away from the pin's idle level; a carrier burst is read as a mark, from a demodulator
with the same idle level) or a space. Fixed symbols are runs of fixed length; a field is
a run list per bit, the two symbols of a `choose` (or a symbol whose one data phase is the
bit, as for a pulse-width code) with the same levels in the same order, and at least one
run where the two lengths cannot be confused. Each run is measured edge to edge: a WAITP
for the other level (its timeout longer than any accepted run) and RDTIME, in units of 2^U
ticks so that every bound fits a 16-bit compare, and accepted if it lies within
TOL percent and SLACK_US microseconds of its length. The first run where a bit's two
symbols differ decides the bit; the bit's other runs must fit either symbol. A run outside
its window drops the packet, and the receiver waits for the line to idle. A space at the
end of the packet merges with the quiet after it, so it is not measured; if it is the only
run that tells a bit's symbols apart, the table is refused.

Refused, with the reason: two or more pins, a carrier on the pin read, codes whose bits are
not delimited by edges (Manchester and bi-phase codes, asynchronous serial), more than 32
data bits, and several fields unless all are LSB first. The receiver queues no events, so
escert certifies it with nothing to refuse; it runs on any context, reading the pin
whoever owns it.
"""

from __future__ import annotations

from . import TableError, _phases, _value

TOL = 25                   # percent either way
SLACK_US = 60              # and this much more, for receiver-module pulse distortion


def _runs(t, name, data=None):
    """[(mark, duration expression)] of symbol `name`, adjacent phases of one level
    merged; `data` is the bit a data phase carries (None: the symbol may have none)."""
    pin = next(iter(t["pins"]))
    idle = int(t["pins"][pin].get("idle", 0))
    out = []
    for levels, dur, kind in _phases(t, name):
        if kind == "data":
            if data is None:
                raise TableError(f"symbol {name} has a data phase outside a field")
            mark = data != idle
        elif kind == "carrier":
            mark = True
        else:
            mark = int(levels[pin]) != idle
        if out and out[-1][0] == mark:
            out[-1] = (mark, f"{out[-1][1]} + ({dur})")
        else:
            out.append((mark, f"({dur})"))
    return out


def _window(t, dur, tol, slack):
    """(lo, hi) of a run in ticks at the default clock, for the checks made here."""
    d = _value(t, dur)
    s = _value(t, f"CLK_KHZ * {slack} // 1000")
    return d * (100 - tol) // 100 - s, d * (100 + tol) // 100 + s


class _Rx:
    def __init__(self, t):
        self.t = t
        rx = t.get("receive", {}) or {}
        self.tol, self.slack = int(rx.get("tolerance", TOL)), int(rx.get("slack_us", SLACK_US))
        self.lines, self.equ, self.n = [], [], 0
        self.durs: dict[str, str] = {}           # duration expression -> window name
        self.deciders: set = set()               # window pairs a bit is decided between

    def emit(self, s="", note=""):
        code = s.ljust(34) if len(s) < 34 else s + " "
        self.lines.append(f"    {code}; {note}" if note else (f"    {s}" if s else ""))

    def fresh(self, base):
        self.n += 1
        return f"{base}{self.n}"

    def win(self, dur):
        """The name W of a run length's window: W_LO <= measured < W_HI, in 2^U ticks."""
        if dur not in self.durs:
            w = f"W{len(self.durs) + 1}"
            self.durs[dur] = w
            self.equ += [f".equ {w}_T, {dur}",
                         f".equ {w}_LO, (({w}_T) * (100 - TOL) // 100 - SLACK) >> U",
                         f".equ {w}_HI, ((({w}_T) * (100 + TOL) // 100 + SLACK) >> U) + 1"]
        return self.durs[dur]

    def measure(self, mark, dur, note=""):
        """The run that began at the edge in r2 (a mark if `mark`) ends at the next edge:
        r0 = its length; drop the packet unless it fits its window."""
        w = self.win(dur)
        self.emit("CALL  end_mark" if mark else "CALL  end_space", note)
        self.emit(f"CMPI  r0, {w}_LO")
        self.emit("JC    resync", "too short")
        self.emit(f"CMPI  r0, {w}_HI")
        self.emit("JNC   resync", "too long")

    def either(self, mark, d0, d1):
        """A run of a bit that both symbols share (lengths alike enough): either window."""
        if d0 == d1:
            return self.measure(mark, d0)
        w0, w1 = self.win(d0), self.win(d1)
        ok = self.fresh("ok")
        self.emit("CALL  end_mark" if mark else "CALL  end_space")
        for w in (w0, w1):
            nxt = self.fresh("try")
            self.emit(f"CMPI  r0, {w}_LO")
            self.emit(f"JC    {nxt}")
            self.emit(f"CMPI  r0, {w}_HI")
            self.emit(f"JC    {ok}")
            self.lines.append(f"{nxt}:")
        self.emit("JMP   resync", "fits neither symbol")
        self.lines.append(f"{ok}:")

    def decide(self, mark, d0, d1, order):
        """The run that tells the bit's symbols apart: set the bit (already shifted in as
        0) when it is the one's."""
        w0, w1 = self.win(d0), self.win(d1)
        self.deciders.add((w0, w1))
        zero, done = self.fresh("not0_"), self.fresh("got")
        self.emit("CALL  end_mark" if mark else "CALL  end_space", "this run decides the bit")
        self.emit(f"CMPI  r0, {w0}_LO")
        self.emit(f"JC    {zero}")
        self.emit(f"CMPI  r0, {w0}_HI")
        self.emit(f"JC    {done}", "a 0")
        self.lines.append(f"{zero}:")
        self.emit(f"CMPI  r0, {w1}_LO")
        self.emit("JC    resync")
        self.emit(f"CMPI  r0, {w1}_HI")
        self.emit("JNC   resync", "fits neither symbol")
        if order == "lsb":
            self.emit("MOVI  r3, 0", "a 1")
            self.emit("MOVHI r3, 0x8000")
            self.emit("OR    r1, r3")
        else:
            self.emit("ADDI  r1, 1", "a 1")
        self.lines.append(f"{done}:")

    def bit(self, r0, r1, d, order, upto):
        """One bit: runs[:upto] of the symbols r0 (a 0) and r1 (a 1); run d decides."""
        self.emit("SHR   r1, 1" if order == "lsb" else "SHL   r1, 1",
                  "the next bit, 0 until a run says 1")
        for i in range(upto):
            mark = r0[i][0]
            if i == d:
                self.decide(mark, r0[i][1], r1[i][1], order)
            else:
                self.either(mark, r0[i][1], r1[i][1])


def _field_runs(t, f):
    """(runs of a 0, runs of a 1) for a field: a `choose`, or a symbol with a data phase."""
    if "choose" in f:
        s0, s1 = f["choose"]
        return _runs(t, s0), _runs(t, s1)
    name = f["symbol"]
    if sum(k == "data" for _, _, k in _phases(t, name)) != 1:
        raise TableError(f"field symbol {name} needs exactly one data phase")
    return _runs(t, name, 0), _runs(t, name, 1)


def generate_rx(t: dict) -> str:
    """Assembly text of the receiver for table `t` (see the module docstring)."""
    for key in ("name", "pins", "symbols", "frame"):
        if key not in t:
            raise TableError(f"table needs '{key}'")
    if len(t["pins"]) != 1:
        raise TableError("receiver: one pin (this table drives "
                         f"{len(t['pins'])}: {', '.join(t['pins'])})")
    src = t.get("source", {}) or {}
    if int(src.get("units_per_word", 1)) != 1:
        raise TableError("receiver: one frame per word (units_per_word 1)")
    pin = next(iter(t["pins"]))
    idle = int(t["pins"][pin].get("idle", 0))
    g = _Rx(t)
    pk = t.get("packet", {}) or {}

    # the packet as items: ("fixed", runs) and ("field", runs0, runs1, bits, order)
    items = [("fixed", _runs(t, s)) for s in pk.get("before", [])]
    fields = []
    for it in t["frame"]:
        if isinstance(it, str):
            items.append(("fixed", _runs(t, it)))
        elif isinstance(it, dict) and "bits" in it:
            bits, order = int(it["bits"]), it.get("order", "lsb")
            if not 1 <= bits <= 32 or order not in ("lsb", "msb"):
                raise TableError(f"field {it}: bits 1..32, order lsb or msb")
            r0, r1 = _field_runs(t, it)
            items.append(("field", r0, r1, bits, order))
            fields.append((bits, order))
        else:
            raise TableError(f"frame item {it!r}: a symbol name or a field")
    after = [r for s in pk.get("after", []) for r in _runs(t, s)]
    if after:                                    # up to its first mark: it ends the frame
        k = next((i for i, (m, _) in enumerate(after) if m), None)
        items.append(("fixed", after[:k + 1] if k is not None else after))
    total = sum(b for b, _ in fields)
    if not fields:
        raise TableError("receiver: the frame has no field, nothing to report")
    if total > 32:
        raise TableError(f"receiver: {total} data bits, one word holds 32")
    if len(fields) > 1 and any(o != "lsb" for _, o in fields):
        raise TableError("receiver: several fields must all be LSB first")
    order = fields[0][1]

    # leading spaces look like idle, trailing spaces like the quiet after: drop them
    while items and items[0][0] == "fixed" and items[0][1] and not items[0][1][0][0]:
        items[0] = ("fixed", items[0][1][1:])
        if not items[0][1]:
            items.pop(0)
    while items and items[-1][0] == "fixed" and (not items[-1][1] or not items[-1][1][-1][0]):
        items[-1] = ("fixed", items[-1][1][:-1])
        if not items[-1][1]:
            items.pop()
    if not items:
        raise TableError("receiver: nothing to measure")
    first = items[0][1] if items[0][0] == "fixed" else items[0][1]
    if not first[0][0]:
        raise TableError("receiver: the packet must begin with a mark")

    # every field: same levels in both symbols, edges at both ends of every bit
    prev = False                                 # the level before the packet: a space
    for idx, it in enumerate(items):
        if it[0] == "fixed":
            if it[1]:
                prev = it[1][-1][0]
            continue
        _, r0, r1, bits, _ = it
        if [m for m, _ in r0] != [m for m, _ in r1]:
            raise TableError("receiver: a field's two symbols must have the same marks "
                             "and spaces in the same order (in asynchronous serial and "
                             "Manchester codes, equal bits merge into one run: they are "
                             "not pulse codes)")
        if r0[0][0] == r0[-1][0]:
            raise TableError("receiver: a bit must begin and end at different levels, so "
                             "that an edge separates the bits (Manchester and bi-phase "
                             "codes, and asynchronous serial, are not pulse codes)")
        if r0[0][0] == prev:
            raise TableError("receiver: no edge between a field and what comes before it")
        prev = r0[-1][0]
        nxt = next((x for x in items[idx + 1:] if x[0] == "field" or x[1]), None)
        if nxt is not None and (nxt[1] if nxt[0] == "fixed" else nxt[1])[0][0] == prev:
            raise TableError("receiver: no edge between a field and what follows it")

    # the program
    hi_all = []
    for it in items:
        runs = it[1] if it[0] == "fixed" else it[1] + it[2]
        hi_all += [d for _, d in runs]
    for d in hi_all:
        lo, hi = _window(t, d, g.tol, g.slack)
        if lo <= 64:
            raise TableError(f"receiver: a run of {d} ticks is too short to time by "
                             "polling at this clock")
    for it in items:
        if it[0] != "field":
            continue
        _, r0, r1, _, _ = it
        if not any(_disjoint(t, a, b, g) for (_, a), (_, b) in zip(r0, r1)):
            raise TableError("receiver: no run tells a field's two symbols apart within "
                             f"{g.tol}% and {g.slack} us")

    body = []
    g.lines = body
    for idx, it in enumerate(items):
        if it[0] == "fixed":
            for m, d in it[1]:
                g.measure(m, d)
            continue
        _, r0, r1, bits, ford = it
        last = idx == len(items) - 1
        n = len(r0)
        # a field at the very end: its last bit's final space merges with the quiet
        upto_last = n - 1 if last and not r0[-1][0] else n
        dec = next(i for i in range(n) if _disjoint(t, r0[i][1], r1[i][1], g))
        if dec >= upto_last:
            raise TableError("receiver: the last bit's space is the only run that tells "
                             "its symbols apart, and the quiet after the frame hides it")
        loop_bits = bits if upto_last == n else bits - 1
        if loop_bits:
            lbl = g.fresh("bit")
            g.emit(f"LDLC  {loop_bits}", f"{bits} bits, {ford.upper()} first")
            g.lines.append(f"{lbl}:")
            g.bit(r0, r1, dec, ford, n)
            g.emit(f"DJNZ  {lbl}")
        if loop_bits < bits:
            g.bit(r0, r1, dec, ford, upto_last)
    if order == "lsb" and total < 32:
        body_end = [f"SHR   r1, {32 - total}", "the first bit to bit 0"]
    else:
        body_end = None

    L = [f"; Receiver for {t.get('title', t['name'])}",
         f"; Generated by escgen (receive side) from {t.get('source_file', t['name'] + '.yaml')}:",
         "; every mark and space measured edge to edge and checked against its window.",
         "; Host: PUSH one word per packet, the word the transmitter would be given.",
         "; It queues no events: escert has nothing to refuse.",
         "",
         f".default CTX, {int(t.get('context', 0))}",
         ".default CLK_KHZ, 25000                ; core clock in kHz",
         f".default PIN, {int(t['pins'][pin]['pin'])}",
         f".default IDLE, {idle}                     ; the line's level between packets",
         f".default TOL, {g.tol}                     ; percent either way",
         f".default SLACK_US, {g.slack}",
         ".equ RX, 1 << PIN",
         ".equ MARK, RX * (1 - IDLE)",
         ".equ SPACE, RX * IDLE",
         ".equ SLACK, CLK_KHZ * SLACK_US // 1000"]
    for n_, c in (t.get("constants") or {}).items():
        L.append(f".equ {n_}, {c}")
    if "carrier" in t:
        L.append(f".equ C_PERIOD, {t['carrier']['period']}")
    longest = max(g.durs, key=lambda d: _value(t, d))
    L.append(f".equ TOUT, ({longest}) * (100 + TOL) // 100 + SLACK + 64   ; longer than any run")
    L.append(".assert TOUT < 0x1000000, a run too long for one wait")
    L.append(".equ U, " + " + ".join(f"(TOUT > 0x{(1 << (16 + k)) - 1:X})" for k in range(9))
             + "   ; 2^U-tick units: TOUT fits 16 bits")
    L += g.equ
    for w in g.durs.values():
        L.append(f".assert ({w}_T) * (100 + TOL) // 100 + SLACK < TOUT, the timeout covers {w}")
    for w0, w1 in sorted(g.deciders):
        L.append(f".assert ({w0}_HI <= {w1}_LO) | ({w1}_HI <= {w0}_LO), "
                 f"{w0} and {w1} cannot be confused")
    L += ["", ".entry CTX, main", "", "main:", "resync:"]
    g.lines = L
    g.emit("RDTIME r2", "wait for the line to idle")
    g.emit("SETT  r2")
    g.emit("ADDT  0xFFFFFF")
    g.emit("WAITP RX, SPACE")
    g.emit("JNZ   resync")
    g.lines.append("idle:")
    g.emit("RDTIME r2", "wait for a packet's first mark")
    g.emit("SETT  r2")
    g.emit("ADDT  0xFFFFFF")
    g.emit("WAITP RX, MARK")
    g.emit("JNZ   idle")
    g.emit("RDTIME r2", "its start (two instructions after WAITP, as in meas)")
    g.emit("MOVI  r1, 0")
    g.lines += body
    if body_end:
        g.emit(*body_end)
    g.emit("PUSH  r1", "the word")
    g.emit("JMP   idle")
    g.lines += ["",
                "; ---- end_mark / end_space: the run that began at the edge in r2 ends;",
                "; r0 = its length in 2^U-tick units, r2 = the edge that ended it.",
                "end_mark:"]
    g.emit("SETT  r2")
    g.emit("ADDT  TOUT")
    g.emit("WAITP RX, SPACE")
    g.emit("JMP   meas")
    g.lines.append("end_space:")
    g.emit("SETT  r2")
    g.emit("ADDT  TOUT")
    g.emit("WAITP RX, MARK")
    g.emit("JMP   meas")
    g.lines.append("meas:")
    g.emit("RDTIME r0")
    g.emit("MOV   r3, r0")
    g.emit("SUB   r0, r2")
    g.emit("MOV   r2, r3")
    g.emit("SHR   r0, U")
    g.emit("RET")
    return "\n".join(g.lines) + "\n"


def _disjoint(t, d0, d1, g):
    lo0, hi0 = _window(t, d0, g.tol, g.slack)
    lo1, hi1 = _window(t, d1, g.tol, g.slack)
    return hi0 < lo1 or hi1 < lo0
