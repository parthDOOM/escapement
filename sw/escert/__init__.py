"""escert: static timing certificates for Escapement firmware (D-11).

For each context, prove that no admission fault (ADMIT/MASK, OWNER, PAST, ORDER,
OVERLAP) can occur for ANY input behaviour, and give a lower bound on the
hardware's own `min_lead` telemetry. The chip measures `min_lead`, so every certificate
is falsifiable on RTL, gates and silicon: a measurement below the bound is a checker bug.

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
MAX_KEYS_PER_PC = 48
THRESHOLDS = (-HALF, -65536, -1024, -256, -64, -16, 0, 16, 64, 256, 1024, 65536, HALF - 1)

NOW, TMR = 0, 1
R = [2, 3, 4, 5]
F = [6, 7, 8, 9]
LEE = 10
NV = 11
EVENTS = {"EMIT", "EMITR", "EMITX", "EXPECT", "SAMPLE"}

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

    def copy(self) -> "St":
        return St(self.z.copy(), list(self.reg), list(self.fifo), self.lc, self.zf,
                  self.lr, self.lee)

    def key(self, pc: int):
        consts = tuple(r if (r[0] == "const" and r[1] == r[2]) else (r[0],) for r in self.reg)
        return (pc, self.lc, self.zf, tuple(k for _, k in self.fifo), consts, self.lee)

    def coarse_key(self, pc: int):
        return (pc, tuple(k for _, k in self.fifo), self.lee)

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
              a.zf if a.zf == b.zf else None, a.lr | b.lr, a.lee or b.lee)


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

    def __str__(self) -> str:
        head = f"ctx {self.ctx}: {self.status}"
        if self.status == "CHECKED":
            ml = int(self.min_lead) if self.min_lead < INF else "inf"
            return f"{head}, min_lead >= {ml} ({self.events} event sites, {self.states} states)"
        f = self.findings[0] if self.findings else None
        w = f" witness {self.witness}" if self.witness else ""
        return head + (f" at pc {f.pc}: {f.kind} {f.note}" if f else "") + w

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
        elif n in ("SETT", "ADDTR"):
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


def analyse(image: Image, ctx: int, max_states: int = 60_000) -> Cert:
    words = image.words
    z0 = Zone()
    # Context k fetches first in run cycle k and commits at tick k + 3 (sec. 3), while
    # tmr starts at 0: the first instruction sees lead tmr - now = -(k + 3).
    first = ctx + 3
    z0.d[TMR][NOW], z0.d[NOW][TMR] = -first, first
    tracked = timing_regs(image)
    init = St(z0, [("const", 0, 0) if i in tracked else ("top",) for i in range(4)], [], 0, 0,
              frozenset({0}), False)
    for i in range(4):
        init.z.free(R[i])
    cert = Cert(ctx, "CHECKED")
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
                t = join_st(old, t, widen=visits[key] > WIDEN_AFTER)
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
                s.z.shift(R[rd], sign * b[1])
                s.zf = None
            elif name == "SUB" and a[0] == b[0] == "time":
                lo, hi = s.z.diff(R[rd], R[f["rs"]])
                setreg(rd, ("const", lo, hi))
                s.zf = int(lo == hi == 0) if lo == hi else None
            elif name != "SUB" and a[0] == "const" and b[0] == "time" and a[1] == a[2]:
                s.z.assign(R[rd], R[f["rs"]], a[1])
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
            setreg(f["ra"], ("top",))
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
                s.z.shift(TMR, r[1])
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
        elif name in EVENTS:
            cert.events += 1
            visited_events.add(pc)
            # static admission rules (sec. 6.2 steps 2-3): decided by the image itself
            if name != "SAMPLE" and f["mask"] == 0:
                flag(pc, "MASK", "event with an empty mask")
                continue
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
        if name in EVENTS:
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
