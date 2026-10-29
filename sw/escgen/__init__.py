"""Transmitter firmware from timing tables (protocols as data).

A table (YAML, `protocols/*.yaml`) describes a transmit-side protocol as pins, timed
symbols and a frame; `generate()` writes commented assembly in the idioms of the
hand-written firmware, and `build()` assembles it and refuses it unless every context is
CHECKED by escert. The generated program is ordinary firmware: it runs on the ISS and the
RTL, carries a timing certificate, and can be edited by hand afterwards.

Table keys:

    name        module name; the generated file is <name>.asm
    title       one line for the header; `refs` a list of specification citations
    context     default context (`.default CTX`), `bank` A (default) or B
    pins        NAME: {pin: n, idle: 0|1, od: true}   (od: bank A only)
    constants   NAME: expression in assembler syntax; CLK_KHZ (default 25000) is defined,
                so timings can be written in clock-independent form
    carrier     {period: expr, high: expr}: the carrier for `carrier` phases, a pulse of
                `high` ticks every `period` (one carrier per table), away from the pin's
                idle level: high pulses on an idle-low pin, low pulses on an idle-high one
    symbols     NAME: [[levels, duration], ...], a list of phases. `levels` maps pin
                names to 0, 1, `data` or `carrier`; a phase sets its pins at the current
                deadline and lasts `duration` ticks (an expression).
                  data     the pins take the next bit of the unit (EMITX); the phase may
                           set no other pin
                  carrier  one pin sends carrier pulses for the whole phase,
                           which must be a whole number of periods, and is idle after it
    frame       the items of one data unit, in order: a symbol name, or a field:
                  {bits: n, order: lsb|msb, symbol: NAME}   each bit sent as the symbol,
                           which has exactly one data phase
                  {bits: n, order: lsb, choose: [S0, S1]}   each bit sends S0 or S1
                           (pulse-distance and pulse-width codes; may contain bursts)
    source      where units come from:
                  {kind: mailbox}                      POP one word per packet
                  {kind: tape, base: a, count: n}      words a .. a+n-1 make one packet
                  {kind: tape, base: a, count: tape}   word a holds n; a+1 .. a+n follow
                  {kind: constant, words: [w, ...]}    sent once, then HALT
                `units_per_word` (default 1) frames are sent from each word, each taking
                the next `bits` of it from bit 0 up
    packet      {before: [symbols], after: [symbols], ack: true|false}: symbols sent
                around each packet (a mailbox word, a pass over the tape); ack PUSHes 1
                after each packet

Registers: r1 holds the source word (r0 directly when a word is one plain field), r0 the
unit being shifted (or the constant 1), r2 the tape address, r3 the re-anchoring
arithmetic and the bit count of a `choose` field. The shift register sh and the loop
counter lc carry a plain field's bits; lc also counts a burst's carrier pulses. Every
edge is a DRIVE event at an absolute deadline (`tmr`), so the timing of the output does
not depend on instruction timing; escert proves the program always schedules ahead of
the deadlines.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from escasm import Image, assemble

ROOT = Path(__file__).resolve().parents[2]
TABLES = ROOT / "protocols"
LEAD = 16                          # first deadline after the first commit, ticks
ANCHOR = 64                        # a re-anchored schedule starts this far ahead, ticks
LONG = 16384                       # a wait longer than this drains the queue first


class TableError(ValueError):
    pass


def load(path) -> dict:
    t = yaml.safe_load(Path(path).read_text())
    t.setdefault("source_file", Path(path).name)
    return t


def _value(t, expr, clk_khz=25000):
    """An expression's value at the default clock (for checks, not for the program)."""
    names = {"CLK_KHZ": clk_khz}
    consts = dict(t.get("constants") or {})
    for _ in range(len(consts) + 1):
        for n, e in consts.items():
            if n not in names:
                try:
                    names[n] = _eval(str(e), names)
                except NameError:
                    pass
    return _eval(str(expr), names)


def _eval(expr, names):
    if not re.fullmatch(r"[\w\s+\-*/()<>|&~]*", expr):
        raise TableError(f"expression {expr!r}")
    return int(eval(expr, {"__builtins__": {}}, names))          # noqa: S307


def _phases(t, name):
    sym = t["symbols"].get(name)
    if sym is None:
        raise TableError(f"unknown symbol {name!r}")
    out = []
    for ph in sym:
        if not (isinstance(ph, list) and len(ph) == 2 and isinstance(ph[0], dict)):
            raise TableError(f"symbol {name}: a phase is [levels, duration]")
        levels, dur = ph
        for p, v in levels.items():
            if p not in t["pins"]:
                raise TableError(f"symbol {name}: unknown pin {p!r}")
            if v not in (0, 1, "data", "carrier"):
                raise TableError(f"symbol {name}: pin {p} level must be 0, 1, data or carrier")
        kinds = {("data" if v == "data" else "carrier" if v == "carrier" else "const")
                 for v in levels.values()}
        if len(kinds) > 1:
            raise TableError(f"symbol {name}: a data or carrier phase may set only its pins")
        kind = kinds.pop() if kinds else "const"
        if kind == "carrier":
            if len(levels) != 1 or "carrier" not in t:
                raise TableError(f"symbol {name}: a carrier phase drives one pin, and the "
                                 "table needs `carrier: {period, high}`")
            per = _value(t, t["carrier"]["period"])
            if _value(t, dur) % per:
                raise TableError(f"symbol {name}: a burst must be whole carrier periods")
        out.append((levels, str(dur), kind))
    return out


def _mask(t, names):
    return " | ".join(f"P_{n}" for n in names) or "0"


def _apply(known, phases, idle=None):
    """Pin levels known after `phases`, from `known` (a data phase makes its pins
    unknown; a burst ends at the pin's `idle` level, default low)."""
    k = dict(known)
    for levels, _, kind in phases:
        for p, v in levels.items():
            if kind == "data":
                k.pop(p, None)
            elif kind == "carrier":
                k[p] = (idle or {}).get(p, 0)
            else:
                k[p] = v
    return k


def _meet(a, b):
    return {p: v for p, v in a.items() if b.get(p) == v}


class _Gen:
    def __init__(self, t):
        self.t, self.lines, self.n = t, [], 0
        self.pins = list(t["pins"])
        self.known = {}                # pin levels known at this point of the program
        self.burst_pin = None
        self.idle = {n: int(p.get("idle", 0)) for n, p in t["pins"].items()}

    def _apply(self, known, phases):
        return _apply(known, phases, self.idle)

    def emit(self, s="", note=""):
        code = s.ljust(34) if len(s) < 34 else s + " "
        self.lines.append(f"    {code}; {note}" if note else (f"    {s}" if s else ""))

    def label(self, s, known=None):
        """A label; `known` is what holds on every path into it (default: nothing)."""
        self.lines.append(f"{s}:")
        self.known = dict(known or {})

    def fresh(self, base):
        self.n += 1
        return f"{base}{self.n}"

    def wait(self, dur, note=""):
        """ADDT dur; a long wait lets the queue drain first, which moves no edge."""
        if _value(self.t, dur) > LONG:
            self.emit(f"ADDT  {dur} - {ANCHOR}", (note + "; " if note else "") +
                      "a long wait: let the queue drain")
            self.emit("WAITT")
            self.emit(f"ADDT  {ANCHOR}")
        else:
            self.emit(f"ADDT  {dur}", note)

    def phase(self, levels, dur, kind, order=None, note=""):
        m = _mask(self.t, levels)
        if kind == "data":
            self.emit(f"EMITX {m}, {order.upper()}, {m}", note or "data bit")
            self.emit(f"ADDT  {dur}")
        elif kind == "carrier":
            pin = next(iter(levels))
            if self.burst_pin not in (None, pin):
                raise TableError("one carrier pin per table")
            self.burst_pin = pin
            self.emit(f"LDLC  ({dur}) // C_PERIOD", (note + ": " if note else "") + "carrier pulses")
            self.emit("CALL  burst")
        elif all(self.known.get(p) == v for p, v in levels.items()):
            self.wait(dur, (note + ": " if note else "") + "no change, no event")
        else:
            v = _mask(self.t, [p for p, lv in levels.items() if lv == 1])
            self.emit(f"EMIT  {m}, {v}, {m}", note)
            self.emit(f"ADDT  {dur}")
        self.known = self._apply(self.known, [(levels, dur, kind)])

    def symbol(self, name, note=None):
        for i, (levels, dur, kind) in enumerate(_phases(self.t, name)):
            if kind == "data":
                raise TableError(f"symbol {name} has a data phase; use it in a field")
            self.phase(levels, dur, kind, note=(note or name) if i == 0 else "")

    def field(self, f, last):
        bits, order = int(f["bits"]), f.get("order", "lsb")
        if not 1 <= bits <= 32 or order not in ("lsb", "msb"):
            raise TableError(f"field {f}: bits 1..32, order lsb or msb")
        if "choose" in f:
            return self.choose(f, bits, order)
        name = f["symbol"]
        ph = _phases(self.t, name)
        if sum(k == "data" for _, _, k in ph) != 1 or any(k == "carrier" for _, _, k in ph):
            raise TableError(f"field symbol {name} needs exactly one data phase and no burst")
        note = f"{bits} bits, {order.upper()} first"
        if not self.direct:
            self.emit("MOV   r0, r1", note)
            note = ""
        if order == "msb" and bits < 32:
            self.emit(f"SHL   r0, {32 - bits}", "the field's first bit to bit 31")
        self.emit("LDSH  r0", note)
        self.emit(f"LDLC  {bits}")
        entry = self.known                       # levels known at the loop head: those
        head = _meet(entry, self._apply(entry, ph))   # that every iteration leaves unchanged
        head = _meet(head, self._apply(head, ph))
        lbl = self.fresh("bit")
        self.label(lbl, head)
        for levels, dur, kind in ph:
            self.phase(levels, dur, kind, order)
        self.emit(f"DJNZ  {lbl}")
        if not last and bits < 32:
            self.emit(f"SHR   r1, {bits}", "next field")

    def choose(self, f, bits, order):
        s0, s1 = f["choose"]
        p0, p1 = _phases(self.t, s0), _phases(self.t, s1)
        if order != "lsb":
            raise TableError("a choose field is sent LSB first")
        if any(k == "data" for _, _, k in p0 + p1):
            raise TableError("choose symbols have no data phase: the bit picks the symbol")
        self.emit(f"MOVI  r3, {bits}", f"{bits} bits, LSB first, each {s0} or {s1}")
        entry = self.known
        body = _meet(self._apply(entry, p0), self._apply(entry, p1))
        head = _meet(entry, body)
        head = _meet(head, _meet(self._apply(head, p0), self._apply(head, p1)))
        lbl, one, nxt = self.fresh("bit"), self.fresh("one"), self.fresh("next")
        self.label(lbl, head)
        self.emit("SHR   r1, 1", "C = the next bit")
        self.emit(f"JC    {one}")
        self.symbol(s0)
        after0 = self.known
        self.emit(f"JMP   {nxt}")
        self.label(one, head)
        self.symbol(s1)
        self.label(nxt, _meet(after0, self.known))
        self.emit("MOVI  r0, 1")
        self.emit("SUB   r3, r0", "Z after the last bit")
        self.emit(f"JNZ   {lbl}")

    def word(self):
        k = int(self.t["source"].get("units_per_word", 1))
        items = [(u, it) for u in range(k) for it in self.t["frame"]]
        nf = sum(1 for _, it in items if isinstance(it, dict))
        seen = 0
        for _, item in items:
            if isinstance(item, str):
                self.symbol(item)
            elif isinstance(item, dict) and "bits" in item:
                seen += 1
                self.field(item, seen == nf)
            else:
                raise TableError(f"frame item {item!r}: a symbol name or a field")

    def anchor(self, note):
        """Wait for the last deadline, then restart the schedule a lead ahead of now."""
        self.emit("WAITT", note)
        self.emit("RDTIME r3")
        self.emit(f"ADDI  r3, {ANCHOR}")
        self.emit("SETT  r3")


def generate(t: dict) -> str:
    """Assembly text for table `t`."""
    for key in ("name", "pins", "symbols", "frame", "source"):
        if key not in t:
            raise TableError(f"table needs '{key}'")
    bank_b = str(t.get("bank", "A")).upper() == "B"
    g = _Gen(t)
    src = t["source"]
    kind = src.get("kind")
    pk = t.get("packet", {}) or {}
    fields = [it for it in t["frame"] if isinstance(it, dict)]
    # one plain field per word: the word goes straight into r0, the field's shift source
    g.direct = (int(src.get("units_per_word", 1)) == 1 and len(fields) == 1
                and "choose" not in fields[0])
    if any("choose" in f for f in fields) and kind == "tape":
        raise TableError("choose fields need the mailbox or constant source (r3 counts bits)")
    wreg = "r0" if g.direct else "r1"
    head = [f"; {t.get('title', t['name'])}",
            f"; Generated by escgen from protocols/{t['source_file']}: edit the table and",
            "; regenerate (python -m escgen), or take this file over by hand."]
    for r in t.get("refs", []):
        head.append(f"; Reference: {r}")
    head.append("; Every edge is a DRIVE event at an absolute deadline; escert certifies it.")
    L = head + ["", f".default CTX, {int(t.get('context', 0))}",
                ".default CLK_KHZ, 25000                ; core clock in kHz"]
    for n, c in (t.get("constants") or {}).items():
        L.append(f".equ {n}, {c}")
    if "carrier" in t:
        L.append(f".equ C_PERIOD, {t['carrier']['period']}")
        L.append(f".equ C_HIGH, {t['carrier']['high']}")
    for n, p in t["pins"].items():
        L.append(f".default PIN_{n}, {int(p['pin'])}")
        L.append(f".equ P_{n}, 1 << PIN_{n}")
    if kind == "tape":
        base, count = int(src["base"]), src["count"]
        if count == "tape":                     # the first word holds the count
            if not 0 <= base < 255:
                raise TableError("tape source: base 0..254")
        elif not (0 <= base < 256 and 1 <= int(count) <= 256 - base):
            raise TableError("tape source: base 0..255, count 1..256 - base, or tape")
        else:
            L.append(f".equ TAPE_END, {base + int(count)}")
    L.append("")
    if bank_b:
        L.append(".bank CTX")
    for n, p in t["pins"].items():
        L.append(f".own  PIN_{n}, CTX")
        if p.get("od"):
            if bank_b:
                raise TableError("bank B pins cannot be open-drain")
            L.append(f".od   PIN_{n}")
    L.append(".entry CTX, main")
    g.lines = L + [""]
    g.label("main")
    g.emit(f"ADDT  {LEAD}", "first deadline after the first commit")
    all_m = _mask(t, g.pins)
    idle_v = _mask(t, [n for n, p in t["pins"].items() if int(p.get("idle", 0))])
    g.emit(f"EMIT  {all_m}, {idle_v}, {all_m}", "idle levels")
    g.emit("ADDT  1")

    def after():
        for s in pk.get("after", []):
            g.symbol(s)
        if pk.get("ack"):
            g.emit("MOVI  r3, 1")
            g.emit("PUSH  r3", "packet sent")

    if kind == "mailbox":
        g.label("packet")
        g.emit(f"POP   {wreg}", "one word per packet (waits for the host)")
        g.anchor("the last packet's final phase has ended")
        for s in pk.get("before", []):
            g.symbol(s)
        g.word()
        after()
        g.emit("JMP   packet")
    elif kind == "tape":
        g.label("packet")
        g.anchor("the last packet's final phase has ended")
        for s in pk.get("before", []):
            g.symbol(s)
        g.emit(f"MOVI  r2, {base}", "tape address")
        if count == "tape":
            g.emit("LDT   r3, r2, 0", "words in this packet")
            g.emit("ADDI  r2, 1")
            g.emit("CMPI  r3, 0")
            g.emit("JZ    packet", "an empty packet: look again")
        g.label("next_word")
        g.emit(f"LDT   {wreg}, r2, 0")
        g.word()
        g.emit("ADDI  r2, 1")
        if count == "tape":
            g.emit("MOVI  r0, 1")
            g.emit("SUB   r3, r0", "Z after the last word")
        else:
            g.emit("CMPI  r2, TAPE_END", "Z after the last word")
        g.emit("JNZ   next_word")
        after()
        g.emit("JMP   packet")
    elif kind == "constant":
        words = [int(w) for w in src["words"]]
        g.anchor("start the schedule")
        for s in pk.get("before", []):
            g.symbol(s)
        for w in words:
            g.emit(f"MOVI  {wreg}, {w & 0xFFFF}")
            g.emit(f"MOVHI {wreg}, {(w >> 16) & 0xFFFF}")
            g.word()
        after()
        g.emit("HALT", "completes once the last edge has gone out")
    else:
        raise TableError("source kind: mailbox, tape or constant")
    if g.burst_pin:
        p = f"P_{g.burst_pin}"
        on, off = ("0", p) if g.idle[g.burst_pin] else (p, "0")    # pulses away from idle
        g.lines.append("")
        g.lines.append("; ---- burst: lc carrier pulses from tmr; returns with tmr = start + lc * C_PERIOD")
        g.label("burst")
        g.emit(f"EMIT  {p}, {on}, {p}")
        g.emit("ADDT  C_HIGH")
        g.emit(f"EMIT  {p}, {off}, {p}")
        g.emit("ADDT  C_PERIOD - C_HIGH")
        g.emit("DJNZ  burst")
        g.emit("RET")
    return "\n".join(g.lines) + "\n"


def build(t: dict, check: bool = True, **equ) -> tuple[str, Image]:
    """(assembly, image); with check, refuse a program that is not CHECKED by escert.
    `equ` overrides module parameters (e.g. CLK_KHZ=2500) for the image only."""
    text = generate(t)
    image = assemble(_override(text, equ) if equ else text, name=f"{t['name']}.asm")
    if check:
        from escert import certify
        bad = [c for c in certify(image) if c.status != "CHECKED"]
        if bad:
            raise TableError(f"{t['name']}: not CHECKED: " + "; ".join(str(c) for c in bad))
    return text, image


def _override(text: str, equ: dict) -> str:
    """The text with `.default K, ...` replaced for each K in equ."""
    out = []
    for line in text.splitlines():
        for k, v in equ.items():
            if line.startswith(f".default {k},"):
                line = f".default {k}, {v}"
        out.append(line)
    return "\n".join(out) + "\n"
