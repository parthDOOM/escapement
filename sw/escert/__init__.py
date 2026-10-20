"""escert: static timing certificates for Escapement firmware (D-11).

For each context, prove that no admission fault (ADMIT/MASK, OWNER, PAST, ORDER,
OVERLAP) can occur for ANY input behaviour, and give a lower bound on the
hardware's own `min_lead` telemetry. The chip measures `min_lead`, so every certificate
is falsifiable on RTL, gates and silicon: a measurement below the bound is a checker bug.

Line unit (v1.4). The zone also holds `lue`, the end tick of the unit's current or last
job, `lua`, the tick the receiver was last armed, and `ge`, the end tick the last GETL
returned. The unit is abstracted as idle, armed (a reception may be under way: its end
is an input), own (a job the program started: CONFIG ends at c + 9, CRC at c + nb, a
packet after its deadline) or unknown. GETL completes in the first slot after the job's
end, or at its timeout. SEND is an event that is dropped (Z = 0) instead of faulting
when late, so a late reply is never a fault; escert reports reply timing separately:

    for every SEND, on paths where the line was quiet while the firmware was busy
    (the packet being answered ended after its GETL began waiting), the bound on
    deadline - ge (the reply time) and on the lead;
    for every GETL that waits on a reception, the most ticks from the arm to the
    start of the wait (W): a packet that ends more than W after the arm is answered
    on time.

Paths where a packet ended, or bits arrived, while the firmware was not waiting are
followed too (they may drop a reply, never fault), and are counted apart. To know which
LINE operation runs, a context that uses the line unit reads tape words that no STT can
write and no receive buffer covers as constants (the host must not write them during
the run).

Method: the firmware of one context is a timed automaton, analysed with ZONES
(difference-bound matrices, as in UPPAAL; Dill 1989, Bengtsson & Yi 2004). Contexts
cannot affect each other's timing (fixed barrel slots); pins, tape and mailbox data are
unknown inputs. Time points tracked in one DBM, all in ticks:

    now  the commit tick of the instruction about to execute
    tmr  the deadline register
    r0..r3 when they hold a time (RDTIME, ADDI/MOV/ADD of one; SUB of two -> constant)
    f0..f3 queued deadlines (the FIFO is a queue of these, oldest first)
    lee  the end of the last EXPECT window

Each committing instruction moves `now` by exactly 4. Wait-class instructions move it by
the semantics: WAITT to the first slot >= tmr; WAITP to a match (any time) or that slot;
POP/PUSH unboundedly; an event with a full FIFO to 0..3 ticks after the head's deadline;
GETI until its SAMPLEs have retired. Where an entry may or may not have retired, the
state is split on it. Branches on unknown flags take both directions; loop counters and
constant registers are tracked exactly, so counted loops are unrolled; uncounted loop
heads are widened. A register that cannot affect timing or control flow (never a time,
never compared, never feeding a Z flag that a branch reads, never copied into one that
does; e.g. a tape address) is held as unknown, so a loop that only advances such a
register converges like any uncounted loop instead of being unrolled. Admission checks are entailment queries on the zone:

    PAST   now - tmr <= -1 (and the lead stays below 2^31)
    ORDER  ftail - tmr <= -1
    OVERLAP lee - tmr <= -1

CHECKED is a proof (sound over-approximation). POSSIBLE means a fault could not be
excluded; certify(..., witness_trials=N) then searches the ISS for concrete inputs that
fault (REFUTED, with a reproducer) or reports NOT_PROVEN. The bound printed with CHECKED
is max over states of the zone's guaranteed lead, min over event sites.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from escasm import Image, load_isa

ISA = load_isa()
INF = float("inf")
SLOT = 4
HALF = 1 << 31
WIDEN_AFTER = 4
WIDEN_AFTER_LINE = 32       # line unit contexts: reply bounds meet at loops entered from many
                            # sites, and joins there need more visits to settle exactly
MAX_KEYS_PER_PC = 48
THRESHOLDS = (-HALF, -65536, -1024, -256, -64, -16, 0, 16, 64, 256, 1024, 65536, HALF - 1)

NOW, TMR = 0, 1
R = [2, 3, 4, 5]
F = [6, 7, 8, 9]
LEE = 10
LUE, LUA, GE = 11, 12, 13     # line unit: job end, receiver armed, end returned by GETL
SCR = 14                      # scratch: the commit tick before a wait (see _align)
NV = 15
EVENTS = {"EMIT", "EMITR", "EMITX", "EXPECT", "SAMPLE"}
LINE_OPS = {"LINE", "SEND", "GETL"}

# --------------------------------------------------------------------------- DBM


class Zone:
    """d[i][j] = upper bound on x_i - x_j. Unconstrained = INF."""

    __slots__ = ("d",)

    def __init__(self, d=None):
        self.d = d if d is not None else [[0 if i == j else INF for j in range(NV)]
                                          for i in range(NV)]

    def copy(self) -> "Zone":
        return Zone([row[:] for row in self.d])

    def canon(self) -> bool:
        """Floyd-Warshall closure; False if empty (inconsistent)."""
        d = self.d
        for k in range(NV):
            dk = d[k]
            for i in range(NV):
                dik = d[i][k]
                if dik == INF:
                    continue
                di = d[i]
                for j in range(NV):
                    v = dik + dk[j]
                    if v < di[j]:
                        di[j] = v
        return all(d[i][i] >= 0 for i in range(NV))

    def constrain(self, i: int, j: int, c: float) -> bool:
        """Add x_i - x_j <= c and close incrementally; False if empty."""
        d = self.d
        if c >= d[i][j]:
            return True
        if c + d[j][i] < 0:
            return False
        d[i][j] = c
        for a in range(NV):
            dai = d[a][i]
            if dai == INF:
                continue
            for b in range(NV):
                v = dai + c + d[j][b]
                if v < d[a][b]:
                    d[a][b] = v
        return d[i][i] >= 0 and d[j][j] >= 0

    def free(self, x: int) -> None:
        for k in range(NV):
            if k != x:
                self.d[x][k] = INF
                self.d[k][x] = INF

    def assign(self, x: int, y: int, k: float) -> None:
        """x := y + k (y must not be x)."""
        self.free(x)
        d = self.d
        for z in range(NV):
            if z == x:
                continue
            d[x][z] = d[y][z] + k if d[y][z] < INF else INF
            d[z][x] = d[z][y] - k if d[z][y] < INF else INF
        d[x][y], d[y][x] = k, -k
        d[x][x] = 0

    def shift(self, x: int, k: float) -> None:
        """x := x + k."""
        d = self.d
        for z in range(NV):
            if z == x:
                continue
            if d[x][z] < INF:
                d[x][z] += k
            if d[z][x] < INF:
                d[z][x] -= k

    def diff(self, i: int, j: int) -> tuple[float, float]:
        """Bounds on x_i - x_j."""
        return -self.d[j][i], self.d[i][j]

    def join(self, o: "Zone") -> "Zone":
        return Zone([[max(a, b) for a, b in zip(r1, r2)] for r1, r2 in zip(self.d, o.d)])

    def widen(self, o: "Zone") -> "Zone":
        """Widening with thresholds: a bound that grew jumps to the next landmark, not
        straight to infinity. Iteration continues until the state is stable, so this is
        still sound; it lets e.g. a FIFO-full wait pin a lead that a loop would otherwise
        widen away."""
        def up(a, b):
            if b <= a:
                return a
            for t in THRESHOLDS:
                if t >= b:
                    return t
            return INF
        return Zone([[up(a, b) for a, b in zip(r1, r2)] for r1, r2 in zip(self.d, o.d)])

    def leq(self, o: "Zone") -> bool:
        return all(a <= b for r1, r2 in zip(self.d, o.d) for a, b in zip(r1, r2))

# --------------------------------------------------------------------------- state


@dataclass
class St:
    z: Zone
    reg: list                        # per register: ("time",) | ("const", lo, hi) | ("top",)
    fifo: list                       # [(slot, kind)] oldest first
    lc: Optional[int]
    zf: Optional[int]                # Z flag if known
    lr: frozenset
    lee: bool                        # an EXPECT has been admitted
    lu: str = "idle"                 # line unit: idle | armed | own | top
    stale: bool = False              # the line was busy while the firmware was not waiting

    def copy(self) -> "St":
        return St(self.z.copy(), list(self.reg), list(self.fifo), self.lc, self.zf,
                  self.lr, self.lee, self.lu, self.stale)

    def key(self, pc: int):
        consts = tuple(r if (r[0] == "const" and r[1] == r[2]) else (r[0],) for r in self.reg)
        return (pc, self.lc, self.zf, tuple(k for _, k in self.fifo), consts, self.lee,
                self.lu, self.stale)

    def coarse_key(self, pc: int):
        return (pc, tuple(k for _, k in self.fifo), self.lee, self.lu, self.stale)

    def free_slot(self) -> int:
        used = {s for s, _ in self.fifo}
        return next(s for s in F if s not in used)


def join_reg(a, b):
    if a == b:
        return a
    if a[0] == b[0] == "const":
        return ("const", min(a[1], b[1]), max(a[2], b[2]))
    if a[0] == b[0] == "time":
        return a
    return ("top",)


def join_st(a: St, b: St, widen: bool) -> St:
    # FIFOs are aligned by kind sequence (part of both keys); slots are renamed to match
    za, zb = a.z.copy(), b.z.copy()
    if [s for s, _ in a.fifo] != [s for s, _ in b.fifo]:
        zb = rename_fifo(b, [s for s, _ in a.fifo])
    z = za.widen(za.join(zb)) if widen else za.join(zb)
    reg = [join_reg(x, y) for x, y in zip(a.reg, b.reg)]
    for i, r in enumerate(reg):
        if r[0] != "time":
            z.free(R[i])
    return St(z, reg, list(a.fifo), a.lc if a.lc == b.lc else None,
              a.zf if a.zf == b.zf else None, a.lr | b.lr, a.lee or b.lee,
              a.lu if a.lu == b.lu else "top", a.stale or b.stale)


def rename_fifo(s: St, slots: list) -> Zone:
    z = Zone([[INF] * NV for _ in range(NV)])
    perm = list(range(NV))
    for (src, _), dst in zip(s.fifo, slots):
        perm[dst] = src
    for i in range(NV):
        for j in range(NV):
            z.d[i][j] = s.z.d[perm[i]][perm[j]]
    return z

# --------------------------------------------------------------------------- results


@dataclass
class Finding:
    pc: int
    kind: str
    note: str = ""


@dataclass
class Cert:
    ctx: int
    status: str                      # CHECKED | POSSIBLE | REFUTED | NOT_PROVEN
    min_lead: float = INF
    events: int = 0
    states: int = 0
    findings: list = field(default_factory=list)
    witness: Optional[dict] = None
    sends: dict = field(default_factory=dict)   # pc -> SendSite
    getls: dict = field(default_factory=dict)   # pc -> W (arm to wait, quiet line), ticks

    def line_report(self) -> list[str]:
        out = []
        for pc in sorted(self.sends):
            x = self.sends[pc]
            if x.lead == INF:
                out.append(f"  SEND pc {pc}: only after the line was busy unwaited"
                           + (" (may be dropped)" if x.drop_stale else ""))
                continue
            turn = (_fmt(x.turn[0]) if x.turn[0] == x.turn[1]
                    else f"[{_fmt(x.turn[0])}, {_fmt(x.turn[1])}]")
            state = "MAY DROP on a quiet line" if x.drop else "on time"
            out.append(f"  SEND pc {pc}: deadline = ge + {turn}, lead >= {_fmt(x.lead)}: {state}"
                       + ("; may be dropped if the line was busy unwaited" if x.drop_stale else ""))
        for pc in sorted(self.getls):
            w = self.getls[pc]
            out.append(f"  GETL pc {pc}: waits within {_fmt(w)} ticks of the arm or its last timeout")
        return out

    @property
    def replies_on_time(self) -> bool:
        return all(not x.drop for x in self.sends.values())

    def __str__(self) -> str:
        head = f"ctx {self.ctx}: {self.status}"
        if self.status == "CHECKED":
            ml = int(self.min_lead) if self.min_lead < INF else "inf"
            text = f"{head}, min_lead >= {ml} ({self.events} event sites, {self.states} states)"
            return "\n".join([text] + self.line_report())
        f = self.findings[0] if self.findings else None
        w = f" witness {self.witness}" if self.witness else ""
        return head + (f" at pc {f.pc}: {f.kind} {f.note}" if f else "") + w


def _fmt(v: float) -> str:
    return str(int(v)) if abs(v) < INF else ("inf" if v > 0 else "-inf")


@dataclass
class SendSite:
    lead: float = INF                 # min lead over quiet-line paths
    turn: tuple = (INF, -INF)         # deadline - ge over quiet-line paths
    drop: bool = False                # a quiet-line path may drop the reply
    drop_stale: bool = False          # a path where the line was busy unwaited may

# --------------------------------------------------------------------------- analysis


def _decode(word: int):
    opc = ISA.by_op.get((word >> 24) & 0xFF)
    if opc is None or word & ISA.reserved_mask(opc):
        return None, {}
    return opc.name, {n: (word >> lsb) & ((1 << (msb - lsb + 1)) - 1)
                      for n, (msb, lsb) in opc.fields.items()}


_ZW = {"MOVI", "MOVHI", "MOV", "ADD", "SUB", "ADDI", "AND", "OR", "XOR", "SHL", "SHR",
       "CMP", "CMPI", "TSTI", "STSH", "GETP", "GETX", "GETC", "GETI", "WAITP", "RXREADY",
       "TXREADY"}                                 # instructions that write Z (as modelled)


def _succ(words, pc, name, f, returns):
    if name == "JMP":
        return [f["target"]]
    if name in ("JZ", "JNZ", "JC", "JNC", "DJNZ"):
        return [f["target"], pc + 1]
    if name == "CALL":
        return [f["target"]]
    if name == "RET":
        return returns
    if name == "HALT" or name is None:
        return []
    return [pc + 1]


def timing_regs(image: Image) -> set:
    """Registers whose value can affect timing or control flow; escert tracks only these.
    Sound by construction: forgetting a register's value only loses precision."""
    words = image.words
    code = [_decode(w) if w else (None, {}) for w in words]
    returns = [pc + 1 for pc, (n, _) in enumerate(code) if n == "CALL"]
    rel = set()
    for pc, (n, f) in enumerate(code):
        if n == "RDTIME":
            rel.add(f["rd"])
        elif n in ("SETT", "ADDTR", "LINE"):
            rel.add(f["rs"])
        elif n in ("CMP", "CMPI", "TSTI"):
            rel.add(f["rd"])
            if n == "CMP":
                rel.add(f["rs"])
    # a Z written from rd that some JZ/JNZ can read before Z is written again
    for pc, (n, f) in enumerate(code):
        if n not in _ZW or "rd" not in f:
            continue
        seen, stack, live = set(), list(_succ(words, pc, n, f, returns)), False
        while stack and not live:
            q = stack.pop()
            if q in seen or not 0 <= q < len(code):
                continue
            seen.add(q)
            qn, qf = code[q]
            if qn in ("JZ", "JNZ"):
                live = True
            elif qn not in _ZW:
                stack.extend(_succ(words, q, qn, qf, returns))
        if live:
            rel.add(f["rd"])
            if n in ("ADD", "SUB", "MOV"):
                rel.add(f["rs"])
    changed = True                                # values copied or added into them
    while changed:
        changed = False
        for n, f in code:
            if n in ("MOV", "ADD", "SUB") and f["rd"] in rel and f["rs"] not in rel:
                rel.add(f["rs"])
                changed = True
    return rel


def _retire_split(s: St) -> list[St]:
    """States at a commit with every queued entry either surely in Q (deadline >= now+1)
    or retired (deadline <= now). Entries are ordered, so the queued ones are a suffix."""
    out = []
    n = len(s.fifo)
    for k in range(n + 1):                  # the first k entries have retired
        t = s.copy()
        ok = True
        for idx, (slot, _) in enumerate(t.fifo):
            if idx < k:
                ok = t.z.constrain(slot, NOW, 0)        # f - now <= 0
            else:
                ok = t.z.constrain(NOW, slot, -1)       # now - f <= -1
            if not ok:
                break
        if ok:
            for slot, _ in t.fifo[:k]:
                t.z.free(slot)
            t.fifo = t.fifo[k:]
            out.append(t)
    return out


def _advance(s: St, lo: float, hi: float) -> Optional[St]:
    """now += s for s in [lo, hi]: now' with now' - now in [lo, hi]."""
    z = s.z
    # x' = now + delta: express by shifting now then relaxing
    z.shift(NOW, lo)
    if hi > lo:
        span = hi - lo
        d = z.d
        for k in range(NV):
            if k != NOW:
                d[NOW][k] = d[NOW][k] + span if d[NOW][k] < INF else INF
    for i, r in enumerate(s.reg):
        if r[0] != "time":
            z.free(R[i])
    return s


def _signed(v: float) -> float:
    """A 32-bit constant added to a time: arithmetic is modulo 2^32, so 0xFFFFFFF0 is -16."""
    return v - (1 << 32) if HALF <= v <= 0xFFFFFFFF else v


def _writes(name: str, f: dict) -> Optional[int]:
    """The register an instruction writes, if any."""
    if name in ("CMP", "CMPI", "TSTI", "STT") or name is None:
        return None
    if name == "LDT":
        return f["ra"]
    return f.get("rd")


def _reach(image: Image, ctx: int) -> set:
    """Instructions reachable in the control-flow graph (every branch both ways, RET to
    every CALL's return address)."""
    rets = {pc + 1 for pc, w in enumerate(image.words) if _decode(w)[0] == "CALL"}
    seen, todo = set(), [image.entry_pcs[ctx]]
    while todo:
        pc = todo.pop()
        if pc in seen or not 0 <= pc <= 255:
            continue
        seen.add(pc)
        name, f = _decode(image.words[pc])
        if name is None or name == "HALT":
            continue
        if name == "JMP":
            todo.append(f["target"])
        elif name in ("JZ", "JNZ", "JC", "JNC", "DJNZ"):
            todo += [f["target"], pc + 1]
        elif name == "CALL":
            todo.append(f["target"])
        elif name == "RET":
            todo += list(rets)
        else:
            todo.append(pc + 1)
    return seen


_PURE_WRITE = {"MOVI", "MOV", "LDT", "POP", "GETL", "GETI", "GETC", "GETP", "GETX", "RDTIME",
               "STSH"}


def _reads(name: str, f: dict) -> set:
    """Registers an instruction reads."""
    r = {f[k] for k in ("rs", "rb") if k in f}
    if name == "STT":
        r.add(f["ra"])
    if "rd" in f and name not in _PURE_WRITE:
        r.add(f["rd"])
    return r


def _liveness(image: Image, ctx: int) -> dict:
    """Registers live on entry to each reachable instruction (read before written on
    some path). A dead register cannot affect anything, so the analysis forgets it."""
    reach = _reach(image, ctx)
    code = {pc: _decode(image.words[pc]) for pc in reach}
    rets = [pc + 1 for pc, (n, _) in code.items() if n == "CALL"]
    succ = {pc: [q for q in _succ(image.words, pc, n, f, rets) if q in reach]
            for pc, (n, f) in code.items()}
    live = {pc: set() for pc in reach}
    changed = True
    while changed:
        changed = False
        for pc in sorted(reach, reverse=True):
            n, f = code[pc]
            out = set().union(*(live[q] for q in succ[pc])) if succ[pc] else set()
            w = _writes(n, f)
            new = _reads(n, f) | (out - ({w} if w is not None else set()))
            if new != live[pc]:
                live[pc] = new
                changed = True
    return live


def _loop_heads(image: Image, ctx: int) -> set:
    """Targets of back edges in a depth-first walk of the control-flow graph. Every
    cycle contains one, so widening only there still makes the analysis terminate,
    and joins elsewhere stay exact."""
    reach = _reach(image, ctx)
    code = {pc: _decode(image.words[pc]) for pc in reach}
    rets = [pc + 1 for pc, (n, _) in code.items() if n == "CALL"]
    succ = {pc: [q for q in _succ(image.words, pc, n, f, rets) if q in reach]
            for pc, (n, f) in code.items()}
    heads, state = set(), {}
    stack = [(image.entry_pcs[ctx], iter(succ.get(image.entry_pcs[ctx], [])))]
    state[image.entry_pcs[ctx]] = 1                      # 1 on the stack, 2 done
    while stack:
        pc, it = stack[-1]
        q = next(it, None)
        if q is None:
            state[pc] = 2
            stack.pop()
        elif state.get(q) == 1:
            heads.add(q)
        elif q not in state:
            state[q] = 1
            stack.append((q, iter(succ[q])))
    return heads


def _uses_line(image: Image, ctx: int) -> bool:
    return any(_decode(image.words[pc])[0] in LINE_OPS for pc in _reach(image, ctx))


def _fixed_regs(image: Image, ctx: int) -> set:
    """Registers no reachable instruction of the context writes: they hold 0 (sec. 9)."""
    written = {_writes(*_decode(image.words[pc])) for pc in _reach(image, ctx)}
    return {r for r in range(4) if r not in written}


def const_tape(image: Image) -> dict:
    """Tape words that keep their image value for the whole run: set by .tape, never the
    target of an STT (every STT's base register must be fixed, so its address is its
    immediate), outside every receive buffer a constant RECV word names, and with no
    capture unit in use. Empty if any of that cannot be shown. The host is assumed not
    to write the tape during the run."""
    written: set = set()
    for k, en in enumerate(image.ctx_enable):
        if not en:
            continue
        fixed = _fixed_regs(image, k)
        for pc in _reach(image, k):
            name, f = _decode(image.words[pc])
            if name == "CAPT":
                return {}
            if name == "STT":
                if f["rb"] not in fixed:
                    return {}
                written.add(f["imm"])
    cand = {a: image.tape[a] for a in image.tape_used if a not in written}
    for a, v in list(cand.items()):
        if (v >> 28) == 1:                       # a RECV command: its buffer may change
            nbytes = ((v >> 8) & 31) or 32
            for i in range((8 * nbytes + 31) // 32):
                written.add(((v & 0xFF) + i) & 0xFF)
    return {a: v for a, v in cand.items() if a not in written}


def _mark(t: St) -> St:
    t.z.assign(SCR, NOW, 0)
    return t


def _align(t: St) -> bool:
    """A context commits only in its own slots, 4 ticks apart, so the time a wait took is
    a multiple of 4: round its bounds inward. False if no multiple fits."""
    lo, hi = t.z.diff(NOW, SCR)
    ok = True
    if hi < INF:
        ok = t.z.constrain(NOW, SCR, (hi // SLOT) * SLOT)
    if ok and lo > -INF:
        ok = t.z.constrain(SCR, NOW, -(-(-lo // SLOT) * SLOT))
    t.z.free(SCR)
    return ok


def _lu_wait(s: St) -> list:
    """A LINE (not OFF) or SEND waits while the line unit is engaged. Returns the states
    at its commit (now = the commit tick)."""
    if s.lu == "idle":
        return [s.copy()]
    out = []
    if s.lu == "own":
        t = s.copy()
        if t.z.constrain(LUE, NOW, -1):          # ended before this cycle
            out.append(t)
        t = _mark(s.copy())
        if t.z.constrain(NOW, LUE, 0):           # still running: the first slot after it
            t.z.assign(NOW, LUE, 1)
            _advance(t, 0, 3)
            if _align(t):
                out.append(t)
        return out
    out.append(s.copy())                         # armed but not receiving: no wait
    t = s.copy()                                 # a reception under way: wait for its end
    _advance(t, 0, INF)
    if t.z.constrain(LUE, NOW, -1):
        if s.lu == "armed":
            t.stale = True
        out.append(t)
    return out


def _port_waits(lu: str) -> list:
    """(lo, hi, stale) waits of the owner's LDT/STT on line unit tape accesses: CONFIG
    reads in its next two slots, a packet or CRC job reads one word at a time, and a
    reception writes one word per 32 bits received."""
    if lu == "idle":
        return [(0, 0, False)]
    if lu == "armed":
        return [(0, 0, False), (0, SLOT, True)]
    return [(0, 2 * SLOT, False)]


def analyse(image: Image, ctx: int, max_states: int = 60_000) -> Cert:
    words = image.words
    z0 = Zone()
    # Context k fetches first in run cycle k and commits at tick k + 3 (sec. 3), while
    # tmr starts at 0: the first instruction sees lead tmr - now = -(k + 3).
    first = ctx + 3
    z0.d[TMR][NOW], z0.d[NOW][TMR] = -first, first
    tracked = timing_regs(image)
    live_at = _liveness(image, ctx)
    heads = _loop_heads(image, ctx)
    lu_ctx = _uses_line(image, ctx)
    widen_after = WIDEN_AFTER_LINE if lu_ctx else WIDEN_AFTER
    if lu_ctx:
        z0.d[LUE][NOW], z0.d[NOW][LUE] = -first, first  # e = 0 until a job ends
        # tape walks over constant tables: their base registers matter
        tracked |= {_decode(image.words[pc])[1]["rb"] for pc in _reach(image, ctx)
                    if _decode(image.words[pc])[0] == "LDT"}
    fixed = _fixed_regs(image, ctx)
    consts = const_tape(image) if lu_ctx else {}
    lu_users = [k for k, en in enumerate(image.ctx_enable) if en and _uses_line(image, k)]
    claimed = [0, False]                         # pins a CONFIG claims; unknown config seen
    emit_masks: set = set()
    init = St(z0, [("const", 0, 0) if i in tracked else ("top",) for i in range(4)], [], 0, 0,
              frozenset({0}), False)
    for i in range(4):
        init.z.free(R[i])
    cert = Cert(ctx, "CHECKED")
    if lu_ctx and len(lu_users) > 1:
        cert.findings.append(Finding(-1, "OWNER", f"contexts {lu_users} all use the line unit"))
    seen: dict = {}
    visits: dict = {}
    keys_at: dict = {}
    work = [(image.entry_pcs[ctx], init)]
    visited_events: set = set()
    # the context's owned logical pins (v1.2: bank B contexts see B0..B4 as pins 0..4)
    phys = [8 + j for j in range(5)] if image.ctx_bank[ctx] else list(range(8))
    owned = sum(1 << j for j, p in enumerate(phys)
                if image.pin_owned[p] and image.pin_owner[p] == ctx)

    def flag(pc, kind, note=""):
        if not any(f.pc == pc and f.kind == kind for f in cert.findings):
            cert.findings.append(Finding(pc, kind, note))

    def push(pc: int, s: St) -> None:
        if not 0 <= pc <= 255:
            flag(pc, "RANGE", "control leaves memory")
            return
        live = live_at.get(pc, range(4))
        for i in range(4):
            if i not in live and s.reg[i] != ("top",):
                s.reg[i] = ("top",)
                s.z.free(R[i])
        for t in _retire_split(s):
            key = t.key(pc)
            ks = keys_at.setdefault(pc, set())
            if key not in ks and len(ks) >= MAX_KEYS_PER_PC:
                key = t.coarse_key(pc)
                t.lc, t.zf = None, None
                t.reg = [r if r[0] == "time" else ("top",) for r in t.reg]
            ks.add(key)
            old = seen.get(key)
            if old is not None:
                tz = t.z if [a for a, _ in t.fifo] == [a for a, _ in old.fifo] \
                    else rename_fifo(t, [a for a, _ in old.fifo])
                if tz.leq(old.z) and all(join_reg(a, b) == b for a, b in zip(t.reg, old.reg)) \
                        and t.lr <= old.lr:
                    continue
                visits[key] = visits.get(key, 0) + 1
                t = join_st(old, t, widen=visits[key] > widen_after and pc in heads)
                if not t.z.canon():
                    continue
            seen[key] = t
            work.append((pc, t))

    while work:
        cert.states += 1
        if cert.states > max_states:
            flag(-1, "UNBOUNDED", "state limit")
            break
        pc, st = work.pop()
        name, f = _decode(words[pc])
        if name is None:
            flag(pc, "ILLEGAL", "illegal word reachable")
            continue
        s = st.copy()
        nxt = pc + 1

        def setreg(i, v):
            s.reg[i] = v if i in tracked else ("top",)
            if s.reg[i][0] != "time":
                s.z.free(R[i])

        def setz_const(v):
            s.zf = int(v == 0) if v is not None else None

        def go(s2: St, to: int, wlo: float = 0, whi: float = 0) -> None:
            t = s2.copy()
            _advance(t, wlo + SLOT, whi + SLOT)
            push(to, t)

        # ------------------------------------------------------------- data
        if name == "MOVI":
            setreg(f["rd"], ("const", f["imm"], f["imm"]))
            setz_const(f["imm"])
        elif name == "MOVHI":
            r = s.reg[f["rd"]]
            if r[0] == "const" and r[1] == r[2]:
                v = (f["imm"] << 16) | (int(r[1]) & 0xFFFF)
                setreg(f["rd"], ("const", v, v))
                setz_const(v)
            else:
                setreg(f["rd"], ("top",))
                s.zf = None
        elif name == "MOV":
            src = s.reg[f["rs"]]
            if src[0] == "time":
                s.z.assign(R[f["rd"]], R[f["rs"]], 0)
            setreg(f["rd"], src)
            s.zf = None if src[0] != "const" or src[1] != src[2] else int(src[1] == 0)
        elif name in ("ADDI", "ADD", "SUB"):
            a = s.reg[f["rd"]]
            b = ("const", f["imm"], f["imm"]) if name == "ADDI" else s.reg[f["rs"]]
            sign = -1 if name == "SUB" else 1
            rd = f["rd"]
            if a[0] == "time" and b[0] == "const" and b[1] == b[2]:
                s.z.shift(R[rd], sign * _signed(b[1]))
                s.zf = None
            elif name == "SUB" and a[0] == b[0] == "time":
                lo, hi = s.z.diff(R[rd], R[f["rs"]])
                setreg(rd, ("const", lo, hi))
                s.zf = int(lo == hi == 0) if lo == hi else None
            elif name != "SUB" and a[0] == "const" and b[0] == "time" and a[1] == a[2]:
                s.z.assign(R[rd], R[f["rs"]], _signed(a[1]))
                setreg(rd, ("time",))
                s.zf = None
            elif a[0] == b[0] == "const":
                if sign > 0:
                    v = ("const", a[1] + b[1], a[2] + b[2])
                else:
                    v = ("const", a[1] - b[2], a[2] - b[1])
                if v[1] == v[2] and 0 <= v[1] <= 0xFFFFFFFF:
                    setreg(rd, v)
                    setz_const(v[1])
                else:
                    setreg(rd, ("top",) if (v[1] < 0 or v[2] > 0xFFFFFFFF) else v)
                    s.zf = None
            else:
                setreg(rd, ("top",))
                s.zf = None
        elif name in ("CMPI", "CMP"):
            a = s.reg[f["rd"]]
            b = ("const", f["imm"], f["imm"]) if name == "CMPI" else s.reg[f["rs"]]
            s.zf = (int(a[1] == b[1]) if (a[0] == b[0] == "const" and a[1] == a[2]
                                          and b[1] == b[2]) else None)
        elif name in ("AND", "OR", "XOR", "SHL", "SHR", "TSTI"):
            if name != "TSTI":
                setreg(f["rd"], ("top",))
            s.zf = None
        elif name in ("STSH", "GETP", "GETX", "GETC"):
            setreg(f["rd"], ("top",))
            s.zf = None
        elif name == "LDT":
            base = ("const", 0, 0) if f["rb"] in fixed else s.reg[f["rb"]]
            v = (consts.get((int(base[1]) + f["imm"]) & 0xFF)
                 if base[0] == "const" and base[1] == base[2] else None)
            setreg(f["ra"], ("top",) if v is None else ("const", v, v))
        elif name in ("WAITP", "RXREADY", "TXREADY"):
            s.zf = None                              # Z is written from an input
        elif name == "RDTIME":
            s.z.assign(R[f["rd"]], NOW, 0)
            setreg(f["rd"], ("time",))

        # ------------------------------------------------------------- control / time
        if name == "JMP":
            go(s, f["target"])
        elif name in ("JZ", "JNZ"):
            want = 1 if name == "JZ" else 0
            if s.zf is None:
                go(s, f["target"])
                go(s, nxt)
            else:
                go(s, f["target"] if s.zf == want else nxt)
        elif name in ("JC", "JNC"):
            go(s, f["target"])
            go(s, nxt)
        elif name == "LDLC":
            s.lc = f["imm"]
            go(s, nxt)
        elif name == "DJNZ":
            if s.lc is not None:
                s.lc = (s.lc - 1) & 0xFFFF
                go(s, f["target"] if s.lc else nxt)
            else:
                go(s, f["target"])
                go(s, nxt)
        elif name == "CALL":
            s.lr = frozenset({pc + 1})
            go(s, f["target"])
        elif name == "RET":
            for t in s.lr:
                go(s, t)
        elif name == "SETT":
            r = s.reg[f["rs"]]
            if r[0] == "time":
                s.z.assign(TMR, R[f["rs"]], 0)
            else:
                s.z.free(TMR)
                s.z.constrain(NOW, TMR, HALF)       # unknown, but a 32-bit value
            go(s, nxt)
        elif name == "ADDT":
            s.z.shift(TMR, f["imm"])
            go(s, nxt)
        elif name == "ADDTR":
            r = s.reg[f["rs"]]
            if r[0] == "const" and r[1] == r[2]:
                s.z.shift(TMR, _signed(r[1]))
            else:
                s.z.free(TMR)
            go(s, nxt)
        elif name == "WAITT":
            lo, hi = s.z.diff(TMR, NOW)              # lead
            # completes at the first slot c' >= tmr: c' - tmr in [0, 3], or now if passed
            t = s.copy()
            if t.z.constrain(TMR, NOW, 0):           # tmr <= now: immediate
                go(t, nxt)
            t = s.copy()
            if t.z.constrain(NOW, TMR, -1):          # tmr > now: wait until tmr..tmr+3
                t.z.assign(NOW, TMR, 0)
                _advance(t, 0, 3)
                for i, r in enumerate(t.reg):
                    pass
                go(t, nxt)
        elif name == "WAITP":
            # a match at any time, or the timeout slot (tmr..tmr+3) if tmr is ahead
            t = s.copy()
            if t.z.constrain(TMR, NOW, 0):
                go(t, nxt)
            t = s.copy()
            if t.z.constrain(NOW, TMR, -1):
                # now' in [now, tmr + 3]
                hi_lead = t.z.diff(TMR, NOW)[1]
                _advance(t, 0, hi_lead + 3 if hi_lead < INF else INF)
                t.z.constrain(NOW, TMR, 3)
                go(t, nxt)
        elif name in ("POP", "PUSH"):
            if name == "POP":
                setreg(f["rd"], ("top",))
            go(s, nxt, 0, INF)
        elif name == "GETI":
            setreg(f["rd"], ("top",))
            smp = [slot for slot, k in s.fifo if k == "SAMPLE"]
            if smp:
                last = smp[-1]
                t = s.copy()
                # completes at the first slot after the last SAMPLE retired: now' > f_last
                hi = t.z.diff(last, NOW)[1]
                _advance(t, 1, hi + 4 if hi < INF else INF)
                t.z.constrain(last, NOW, -1)
                go(t, nxt)
            else:
                go(s, nxt)
            s.zf = None
        elif name == "HALT":
            continue
        elif name in ("LDT", "STT") and lu_ctx:
            for lo, hi, stale in _port_waits(s.lu):
                t = s.copy()
                t.stale = t.stale or stale
                go(t, nxt, lo, hi)
        elif name == "LINE":
            r = s.reg[f["rs"]]
            v = int(r[1]) if r[0] == "const" and r[1] == r[2] else None
            op = None if v is None else (v >> 28) & 0xF
            if op is not None and op > 3:
                flag(pc, "ILLEGAL", f"LINE operation {op}")
                continue
            if op == 0:                                  # OFF: no wait
                s.lu = "idle"
                go(s, nxt)
                continue
            for t in _lu_wait(s):
                if op == 1:                              # RECV
                    t.lu = "armed"
                    t.z.assign(LUA, NOW, 0)
                    t.z.free(LUE)
                    t.z.constrain(LUA, LUE, -1)          # its job ends after the arm
                elif op == 2 and (v >> 8) & 0xFF:        # CRC: the last bit at E_(c+nb)
                    t.lu = "own"
                    t.z.assign(LUE, NOW, (v >> 8) & 0xFF)
                elif op == 3:                            # CONFIG: complete at E_(c+9)
                    t.lu = "own"
                    t.z.assign(LUE, NOW, 9)
                    a = v & 0xFF
                    blk = [consts.get((a + i) & 0xFF) for i in range(3)]
                    if None in blk:
                        claimed[1] = True
                    else:
                        from esciss.line import Cfg
                        claimed[0] |= Cfg(*blk).claimed()
                else:
                    t.lu = "top"
                    t.z.free(LUE)
                    if consts:
                        flag(pc, "TAPE", "LINE operand unknown: a receive buffer could "
                                         "overwrite words read as constants")
                go(t, nxt)
            continue
        elif name == "GETL":
            setreg(f["rd"], ("top",))
            if s.lu == "armed" and not s.stale:
                w = s.z.diff(NOW, LUA)[1]
                cert.getls[pc] = max(cert.getls.get(pc, -INF), w)
            outs = []
            if s.lu == "idle":                           # the last job's end again
                t = s.copy()
                t.z.assign(TMR, LUE, 0)
                t.z.assign(GE, LUE, 0)
                outs.append((t, 1))
            else:
                ext = s.lu != "own"                      # its end comes from the line
                # a reception stays busy past e until its last word is written, in the
                # first owner slot after e (sec. 6.6 Finish): up to 4 ticks more
                tail = SLOT if ext else 0
                t = _mark(s.copy())
                if t.z.constrain(LUE, NOW, -1):          # ended before this GETL
                    _advance(t, 0, tail)                 # (its last write may hold it a slot)
                    _align(t)                            # (0 always fits)
                    t.z.assign(TMR, LUE, 0)
                    t.z.assign(GE, LUE, 0)
                    t.stale = t.stale or ext
                    outs.append((t, 1))
                t = _mark(s.copy())
                if t.z.constrain(NOW, LUE, 0):           # ends while it waits
                    t.z.assign(NOW, LUE, 1)
                    _advance(t, 0, 3 + tail)
                    if _align(t) and t.z.constrain(NOW, TMR, 3):   # (no earlier timeout)
                        t.z.assign(TMR, LUE, 0)
                        t.z.assign(GE, LUE, 0)
                        if ext:
                            t.stale = False
                        outs.append((t, 1))
                t = s.copy()                             # timeout now, still busy
                if t.z.constrain(TMR, NOW, 0) and t.z.constrain(NOW, LUE, tail):
                    outs.append((t, 0))
                t = _mark(s.copy())                      # timeout in a later slot
                if t.z.constrain(NOW, TMR, -1):
                    t.z.assign(NOW, TMR, 0)
                    _advance(t, 0, 3)
                    if _align(t) and t.z.constrain(NOW, LUE, tail):
                        outs.append((t, 0))
                for t, zv in outs:                       # it was waiting until now:
                    if not zv:                           # W counts from here on
                        t.z.assign(LUA, NOW, 0)
            for t, zv in outs:
                t.zf = zv
                if zv:
                    t.lu = "idle"
                go(t, nxt)
            continue
        elif name == "SEND":
            cert.events += 1
            visited_events.add(pc)
            site = cert.sends.setdefault(pc, SendSite())
            if len(s.fifo) >= 4:                         # a full FIFO, as for events
                head = s.fifo[0][0]
                s.z.assign(NOW, head, 0)
                _advance(s, 0, 3)
                s.z.free(head)
                s.fifo = s.fifo[1:]
                splits = _retire_split(s)
            else:
                splits = [s]
            for s1 in splits:
                for t in _lu_wait(s1):
                    lo, hi = t.z.diff(TMR, NOW)
                    late = lo < 1 or hi >= HALF
                    if not t.stale:
                        site.lead = min(site.lead, lo)
                        g = t.z.diff(TMR, GE)
                        site.turn = (min(site.turn[0], g[0]), max(site.turn[1], g[1]))
                        site.drop = site.drop or late
                    elif late:
                        site.drop_stale = True
                    if late:                             # dropped: Z = 0, nothing else
                        u = t.copy()
                        if hi >= HALF or u.z.constrain(TMR, NOW, 0):
                            u.zf = 0
                            go(u, nxt)
                    u = t.copy()
                    if not (u.z.constrain(NOW, TMR, -1) and u.z.constrain(TMR, NOW, HALF - 1)):
                        continue
                    cert.min_lead = min(cert.min_lead, u.z.diff(TMR, NOW)[0])
                    if u.fifo:
                        flo, fhi = u.z.diff(TMR, u.fifo[-1][0])
                        if flo < 1:
                            flag(pc, "ORDER", f"tmr - tail in [{flo}, {fhi}]")
                        if not u.z.constrain(u.fifo[-1][0], TMR, -1):
                            continue
                    slot = u.free_slot()
                    u.z.assign(slot, TMR, 0)
                    u.fifo.append((slot, "LINE"))
                    u.lu = "own"
                    u.z.free(LUE)
                    u.z.constrain(TMR, LUE, 0)           # the packet ends at or after d
                    u.zf = 1
                    go(u, nxt)
            continue
        elif name in EVENTS:
            cert.events += 1
            visited_events.add(pc)
            # static admission rules (sec. 6.2 steps 2-3): decided by the image itself
            if name != "SAMPLE" and f["mask"] == 0:
                flag(pc, "MASK", "event with an empty mask")
                continue
            if name in ("EMIT", "EMITR", "EMITX"):
                emit_masks.add((pc, f["mask"]))
            if name in ("EMIT", "EMITR", "EMITX") and f["mask"] & ~owned:
                flag(pc, "OWNER", f"drives pins 0x{f['mask'] & ~owned & 0xFF:02x} not owned by ctx {ctx}")
                continue
            # FIFO full: 4 entries in Q -> complete 0..3 ticks after the head's deadline
            if len(s.fifo) >= 4:
                head = s.fifo[0][0]
                s.z.assign(NOW, head, 0)
                _advance(s, 0, 3)
                s.z.free(head)
                s.fifo = s.fifo[1:]
                # remaining entries may also have retired meanwhile
                splits = _retire_split(s)
            else:
                splits = [s]
            for t in splits:
                lead_lo, lead_hi = t.z.diff(TMR, NOW)
                cert.min_lead = min(cert.min_lead, lead_lo)
                if lead_lo < 1 or lead_hi >= HALF:
                    flag(pc, "PAST", f"lead in [{lead_lo}, {lead_hi}]")
                if t.fifo:
                    lo, hi = t.z.diff(TMR, t.fifo[-1][0])
                    if lo < 1:
                        flag(pc, "ORDER", f"tmr - tail in [{lo}, {hi}]")
                if name == "EXPECT" and t.lee:
                    lo, hi = t.z.diff(TMR, LEE)
                    if lo < 1:
                        flag(pc, "OVERLAP", f"tmr - lee in [{lo}, {hi}]")
                u = t.copy()
                # assume the checks passed (a failing path faults and stops)
                if not (u.z.constrain(NOW, TMR, -1)
                        and (not u.fifo or u.z.constrain(u.fifo[-1][0], TMR, -1))):
                    continue
                slot = u.free_slot()
                u.z.assign(slot, TMR, 0)
                u.fifo.append((slot, "SAMPLE" if name == "SAMPLE" else "DRIVE"
                               if name != "EXPECT" else "EXPECT"))
                if name == "EXPECT":
                    u.z.assign(LEE, TMR, f["window"])
                    u.lee = True
                if name == "EMITX":
                    pass
                go(u, nxt)
            continue
        else:
            go(s, nxt)

    # Guard against vacuous proofs: every event site the control-flow graph can reach must
    # have been analysed, otherwise the analysis lost paths (a bug or a precision limit).
    for epc, mask in sorted(emit_masks):           # sec. 6.2 step 3: claimed pins
        if lu_ctx and (claimed[1] or mask & claimed[0]):
            flag(epc, "OWNER", "may drive a pin the line unit claims")
    missed = sorted(_cfg_events(image, ctx) - visited_events)
    if missed:
        flag(missed[0], "INCOMPLETE", f"event sites never analysed: {missed[:8]}")
    if cert.findings:
        cert.status = "POSSIBLE"
    return cert


def _cfg_events(image: Image, ctx: int) -> set:
    """Event instructions reachable in the control-flow graph (every branch both ways,
    RET to every CALL's return address)."""
    rets = set()
    for pc, w in enumerate(image.words):
        name, f = _decode(w)
        if name == "CALL":
            rets.add(pc + 1)
    seen, todo, ev = set(), [image.entry_pcs[ctx]], set()
    while todo:
        pc = todo.pop()
        if pc in seen or not 0 <= pc <= 255:
            continue
        seen.add(pc)
        name, f = _decode(image.words[pc])
        if name is None or name == "HALT":
            continue
        if name in EVENTS or name == "SEND":
            ev.add(pc)
        if name == "JMP":
            todo.append(f["target"])
        elif name in ("JZ", "JNZ", "JC", "JNC", "DJNZ", "CALL"):
            todo += [f["target"], pc + 1] if name != "CALL" else [f["target"]]
        elif name == "RET":
            todo += list(rets)
        else:
            todo.append(pc + 1)
    return ev


def certify(image: Image, witness_trials: int = 0) -> list[Cert]:
    out = []
    for k, en in enumerate(image.ctx_enable):
        if not en:
            continue
        c = analyse(image, k)
        if c.status == "POSSIBLE" and witness_trials:
            w = witness(image, k, witness_trials)
            c.status, c.witness = ("REFUTED", w) if w else ("NOT_PROVEN", None)
        out.append(c)
    return out


def witness(image: Image, ctx: int, trials: int = 50, ticks: int = 20_000) -> Optional[dict]:
    """Search for inputs that make context `ctx` fault with ADMIT on the ISS. Two input
    strategies alternate: random pin waveforms with sparse host words, and host words in
    back-to-back bursts over a long run (a frame-at-a-time protocol must cope with a host
    that never pauses). Returns a reproducer, or None."""
    from esciss import Machine
    for seed in range(trials):
        rng = random.Random(seed)
        burst = seed % 2 == 1
        period = [rng.choice([1, 3, 7, 40, 300, 5000]) for _ in range(8)]
        phase = [rng.randrange(1000) for _ in range(8)]
        idle_high = burst and rng.random() < 0.7

        def board(t, out, oe, period=period, phase=phase, idle_high=idle_high):
            if idle_high:
                return ((oe & out) | (~oe & 0xFF)) & 0xFF        # pulled-up, quiet bus
            ext = 0
            for i in range(8):
                ext |= (((t + phase[i]) // period[i]) & 1) << i
            return ((oe & out) | (~oe & ext)) & 0xFF
        def board_b(t, ob, period=period, phase=phase, idle_high=idle_high):
            if idle_high:
                return 0x1F
            return sum((((t + phase[i]) // period[i]) & 1) << i for i in range(5))
        m = Machine(image, board=board, board_b=board_b)
        horizon = ticks * (8 if burst else 1)
        step = 23 if burst else 97
        for _ in range(0, horizon, step):
            m.run(step, stop_when_idle=False)
            if burst or rng.random() < 0.5:
                try:
                    m.host_rx_push(ctx, rng.randrange(1 << 32) if not burst else rng.randrange(256))
                except RuntimeError:
                    pass
            if m.ff.valid:
                break
        if m.ff.valid and m.ff.ctx == ctx and m.ff.cause == ISA.fault_causes["ADMIT"]:
            return {"seed": seed, "tick": m.ff.tick, "pc": m.ff.pc, "subcode": m.ff.subcode,
                    "strategy": "burst" if burst else "random"}
    return None
