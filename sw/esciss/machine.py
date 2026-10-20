"""Escapement instruction-set simulator: a cycle-by-cycle model of docs/11-SEMANTICS.md v1.4.0.

v1.1 adds the tape and capture unit (§6.5, §7.6), the execution digest (§10.1) and the
wait-class GETI (§7.3). v1.2 adds pin bank B (§5): five split pins seen by a context as
its logical pins 0..4. Bus models keep the 8-bit `board(t, uio_out, uio_oe) -> uio_in`
interface for bank A; bank B has its own `board_b(t, ob) -> ib` (5 bits, default 0).
v1.3 adds streaming capture (§6.5): CAPT bit 26, the host's read pointer `rptr` set by
TAPE_ACK (`host_tape_ack`), and a drain that is held rather than overwrite an
unacknowledged record. Host tape reads never affect the machine; `tape_log` records
every tape write so a host read at any cycle can be checked. v1.4 adds the line unit
(§6.6, `line.py`) and SEND, LINE and GETL (§6.2, §7.7).

One loop iteration = one run cycle t, ending with edge E_t. State held in the Python
objects is always "state during cycle self.now" (i.e. the register values after the
previous edge). Each edge is computed in two phases, so that every decision reads only
state during cycle t (§3 "What a commit sees"):

  plan   - event engine (§6.3 retirement, §6.4 checker) and the commit (§7, §6.2) are
           evaluated from state during cycle t, without modifying anything;
  apply  - all results are written, giving state during cycle t+1.

Spec section numbers are given in the comments. Choices made where the spec is silent or
ambiguous are marked "SPEC-Q n" and listed in SPEC_QUESTIONS.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Optional

from escasm import Image

from .decode import ADMIT_SUB, EVENT_KIND, FAULT_CAUSE, ISA, decode
from .line import IDLE as LU_IDLE, LineUnit, OP_CONFIG, OP_OFF, OP_RECV, OP_CRC

M32 = 0xFFFFFFFF
HALF = 1 << 31

# §10 sel 7 state encoding
IDLE, RUNNING, HALTED, FAULTED = 0, 1, 2, 3
STATE_NAMES = {IDLE: "IDLE", RUNNING: "RUNNING", HALTED: "HALTED", FAULTED: "FAULTED"}

DRIVE, EXPECT, SAMPLE = EVENT_KIND["DRIVE"], EVENT_KIND["EXPECT"], EVENT_KIND["SAMPLE"]
LINE = EVENT_KIND["LINE"]     # v1.4
HOLD, ARRIVE = 0, 1

FIFO_DEPTH = 4          # §1
MBX_DEPTH = 2           # §1
N_CTX = 4               # §1
N_PINS = 8              # §1 logical pins per context, and bank A
N_PHYS = 13             # §5 physical pins: 0..7 bank A, 8..12 bank B (v1.2)
BANK_B = 8              # first bank B physical pin
TAPE_WORDS = 256        # §1 (v1.1)
CAP_STAGE = 2           # §1 (v1.1) capture staging records
OP_LDT, OP_STT = ISA.by_name["LDT"].op, ISA.by_name["STT"].op
OP_SEND, OP_LINE = ISA.by_name["SEND"].op, ISA.by_name["LINE"].op
DG_POLY = 0x04C11DB7    # §10.1
OUTCOME_CODE = {"C": 0, "W": 1, "F": 2}

Board = Callable[[int, int, int], int]
BoardB = Callable[[int, int], int]


# --------------------------------------------------------------------------- notation


def ahead(a: int, b: int) -> bool:
    """Notation: 1 <= (a - b) mod 2^32 < 2^31."""
    return 1 <= ((a - b) & M32) < HALF


def reached(t: int, d: int) -> bool:
    """Notation: (t - d) mod 2^32 < 2^31."""
    return ((t - d) & M32) < HALF


def shift_in(x: int, b: int, direction: int) -> int:
    """§6.3 SAMPLE: dir 0 LSB-first (shift right, bit enters at 31); dir 1 MSB-first."""
    if direction == 0:
        return ((x >> 1) | (b << 31)) & M32
    return ((x << 1) | b) & M32


def default_board(t: int, uio_out: int, uio_oe: int) -> int:
    """Pull-up loopback: for each bit, uio_in = uio_oe ? uio_out : 1."""
    return ((uio_out & uio_oe) | (~uio_oe & 0xFF)) & 0xFF


def no_board_b(t: int, ob: int) -> int:
    """Bank B inputs (ui_in[7:3]) held low."""
    return 0


def loopback_b(t: int, ob: int) -> int:
    """Bank B wired back: ui_in[3+i] = uo_out[3+i]."""
    return ob & 0x1F


def to_phys(bank: int, v: int) -> int:
    """§5: a context's 8-bit logical vector as a 13-bit physical one."""
    return ((v & 0x1F) << BANK_B) if bank else (v & 0xFF)


def view(bank: int, psync: int) -> int:
    """§5: a context's logical view of the 13 synchronised physical inputs."""
    return ((psync >> BANK_B) & 0x1F) if bank else (psync & 0xFF)


# --------------------------------------------------------------------------- state (§4)


@dataclass
class Entry:
    """§6.1 event FIFO entry."""
    kind: int
    deadline: int
    mask: int = 0
    value: int = 0
    oe: int = 0
    level: int = 0
    mode: int = 0
    window: int = 0
    pin: int = 0
    dir: int = 0
    cmd: int = 0            # LINE (v1.4): the SEND command, R[rs][23:0]


@dataclass
class Checker:
    """§6.4 expectation checker status."""
    active: bool = False
    d: int = 0
    end: int = 0            # d + window (mod 2^32)
    mask: int = 0
    level: int = 0
    mode: int = 0
    pass_count: int = 0
    fail_count: int = 0
    ff_valid: bool = False  # "failed since start"
    ff_tick: int = 0
    ff_obs: int = 0
    ff_mask: int = 0
    ff_level: int = 0


@dataclass
class InFlight:
    """An instruction fetched in cycle t that commits at E_(t+3) (§3)."""
    pc: int
    insn: int
    commit_tick: int


@dataclass
class Context:
    """§4 per-context state."""
    state: int = IDLE
    r: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    sh: int = 0
    lc: int = 0
    tmr: int = 0
    pc: int = 0
    lr: int = 0
    z: int = 0
    c: int = 0
    ish: int = 0
    fifo: list[Entry] = field(default_factory=list)
    chk: Checker = field(default_factory=Checker)
    # telemetry (§7.4)
    min_lead: int = 0xFFFF
    max_occupancy: int = 0
    wait_full: int = 0
    # IRQ (§7.2)
    irq_pending: int = 0
    irq_code: int = 0
    # mailboxes (§7.5)
    rx: list[int] = field(default_factory=list)
    tx: list[int] = field(default_factory=list)
    # EXPECT admission history (§6.2 step 6)
    expect_admitted: bool = False
    last_expect_end: int = 0
    inflight: Optional[InFlight] = None


@dataclass
class Capture:
    """§6.5 capture unit (v1.1)."""
    en: bool = False
    snap: bool = False
    mask: int = 0
    base: int = 0
    lim: int = 0
    ring: bool = False
    stream: bool = False    # v1.3: ring shared with the host
    bank: int = 0           # v1.2: the watched pin bank
    ptr: int = 0
    rptr: int = 0           # v1.3: first record the host has not acknowledged
    prev: int = 0
    ovf: bool = False
    full: bool = False
    wrapped: bool = False
    ovf_tick: int = 0
    stage: list[int] = field(default_factory=list)

    def status(self) -> int:
        """§7.6 GETC / §10 sel 17."""
        return ((int(self.en) << 31) | (int(self.ovf) << 30) | (int(self.full) << 29)
                | (int(self.wrapped) << 28) | (len(self.stage) << 8) | self.ptr)

    def config(self) -> int:
        """§10 sel 18."""
        return ((int(self.stream) << 26) | (self.bank << 25) | (int(self.ring) << 24)
                | (self.mask << 16) | (self.lim << 8) | self.base)

    def succ(self) -> int:
        """The successor of ptr (§6.5)."""
        n = (self.ptr + 1) & 0xFF
        return self.base if (n == self.lim and (self.ring or self.stream)) else n

    def held(self) -> bool:
        """§6.5 v1.3: the drain would make ptr reach rptr."""
        return self.stream and self.succ() == self.rptr


@dataclass
class FirstFault:
    """§8 first-fault register."""
    valid: bool = False
    cause: int = 0
    subcode: int = 0
    ctx: int = 0
    pc: int = 0
    tick: int = 0


# --------------------------------------------------------------------------- plan records


@dataclass
class CheckerEval:
    """Result of the checker evaluating tick t at E_t (§6.4)."""
    start: Optional[Entry]      # EXPECT entry retiring at E_t (checker starts), else None
    result: Optional[str]       # "pass", "fail" or None (keeps checking)
    active_after: bool
    mask: int
    level: int


@dataclass
class EventPlan:
    """Event-engine plan for one context at E_t (§6.3)."""
    retire: Optional[Entry] = None
    late: bool = False
    chk: Optional[CheckerEval] = None


@dataclass
class Commit:
    """Outcome and pending effects of the commit at E_c (§3, §7)."""
    k: int
    pc: int
    insn: int
    outcome: str = "C"                  # C / W / F
    cause: int = 0
    subcode: int = 0
    # effects, applied at E_c only if outcome == "C" (except wait_full_inc on "W")
    r: Optional[list[int]] = None
    sh: int = 0
    lc: int = 0
    tmr: int = 0
    z: int = 0
    c: int = 0
    lr: int = 0
    next_pc: int = 0
    sequential: bool = True             # next PC is PC+1 (for PC_RANGE, §7)
    halt: bool = False
    geti_clear: bool = False
    append: Optional[Entry] = None
    lead: int = 0
    occupancy: int = 0
    expect_end: Optional[int] = None
    irq_code: Optional[int] = None
    rx_pop: bool = False
    tx_push: Optional[int] = None
    wait_full_inc: bool = False
    # v1.1
    rd_val: Optional[int] = None        # §10.1 wv: rd result written at E_c
    ld_val: Optional[int] = None        # §7.6 LDT loaded value (digest folds it at E_(c+1))
    tape_write: Optional[tuple[int, int]] = None   # STT (address, word)
    capt: Optional[int] = None          # CAPT operand
    # v1.4
    lu_op: Optional[tuple[str, int, int]] = None   # ("LINE"|"SEND", R[rs], ctx)


class _Done(Exception):
    """Internal: instruction semantics reached a WAIT or FAULT outcome."""


# --------------------------------------------------------------------------- machine


class Machine:
    def __init__(self, image: Image, board: Optional[Board] = None,
                 board_b: Optional[BoardB] = None):
        if len(image.words) != 256:
            raise ValueError("image must have 256 words")
        self.image = image
        self.board: Board = board or default_board
        self.board_b: BoardB = board_b or no_board_b
        self.imem = [w & M32 for w in image.words]
        # §4 v1.1: the tape is not reset and not cleared by start; it starts with the
        # image's host-loaded contents (WRITE_TAPE before START).
        self.tape = [w & M32 for w in getattr(image, "tape", [0] * TAPE_WORDS)]
        self.image_tape = list(self.tape)
        self.tape_log: list[tuple[int, int, int]] = []   # (edge t, address, word)
        # static configuration (§4)
        self.ctx_enable = [bool(e) for e in image.ctx_enable]
        self.entry_pc = [pc & 0xFF for pc in image.entry_pcs]
        self.pin_owned = [bool(o) for o in image.pin_owned] + [False] * N_PHYS
        self.pin_owner = [o & 3 for o in image.pin_owner] + [0] * N_PHYS
        del self.pin_owned[N_PHYS:], self.pin_owner[N_PHYS:]
        self.pin_od = [bool(o) for o in image.pin_od]
        self.ctx_bank = [int(bool(b)) for b in getattr(image, "ctx_bank", [0] * N_CTX)]
        # observation records (§10)
        self.commits: list[dict] = []
        self.pins: list[dict] = []
        self._stop_pending = False
        self._host_rx_push: list[Optional[int]] = [None] * N_CTX
        self._host_tx_pop: list[bool] = [False] * N_CTX
        self._host_irq_clear = 0
        self._host_tape_ack: Optional[int] = None
        self._reset()
        self._start_cycle()

    # ------------------------------------------------------------------ §9 reset / start

    def _reset(self) -> None:
        """§9 Reset."""
        self.run_flag = False
        self.ctx = [Context() for _ in range(N_CTX)]
        self.val = 0
        self.oe = 0
        self.s1 = 0
        self.s2 = 0
        self.ff = FirstFault()
        self.now = 0
        self.cap = Capture()
        self.lu = LineUnit()
        self._lu_acc = False
        self._lu_busy_port = False
        self.dg = 0xFFFFFFFF
        self._ld_dg: Optional[int] = None
        self._dg_prev = (0, 0, 0)

    def _start_cycle(self) -> None:
        """The start cycle s = -1 (§2) and the start edge E_s (§9 Start).

        The synchronisers are not cleared by start (§5), so we model exactly one pre-start
        cycle: uio_in during cycle -1 = board(-1, outputs during -1) = board(-1, 0, 0)
        (reset pin registers). At E_-1: s1 <= uio_in(-1), s2 <= s1 (= 0, reset value).
        Hence sync during cycle 0 = 0 and sync during cycle 1 = uio_in during cycle -1.
        """
        out, oe = self._pin_outputs()
        self._sync_shift(self._inputs(-1, out, oe, self._ob()))
        self._apply_start()

    def _apply_start(self) -> None:
        """§9 Start at E_s."""
        self.run_flag = True
        self.now = 0
        for k in range(N_CTX):
            ctx = Context()
            ctx.state = RUNNING if self.ctx_enable[k] else IDLE
            ctx.pc = self.entry_pc[k]
            # r0-r3 = sh = tmr = lc = lr = Z = C = ish = 0, FIFO empty, checker idle with
            # counts 0, no fail record, EXPECT history cleared, min_lead 0xFFFF,
            # max_occupancy 0, wait_full 0, IRQ clear, mailboxes emptied: Context defaults.
            self.ctx[k] = ctx
        self.val = 0
        self.oe = 0
        self.ff = FirstFault()
        self.cap = Capture()                       # §6.5 start
        self.lu = LineUnit()                       # §9 v1.4 line unit
        self.dg = 0xFFFFFFFF                       # §10.1
        self._ld_dg = None                         # no pending LDT write
        self._dg_prev = (0, 0, 0)                  # outputs during the previous cycle

    # ------------------------------------------------------------------ public API

    def run(self, max_ticks: int, stop_when_idle: bool = True) -> None:
        """Simulate at most max_ticks run cycles from the current cycle."""
        for _ in range(max_ticks):
            if not self.run_flag:
                return
            idle = self._cycle()
            if stop_when_idle and idle:
                return

    def stop(self) -> None:
        """§9 Stop at the next edge."""
        self._stop_pending = True

    def host_rx_push(self, ctx: int, value: int) -> None:
        """Host pushes into ctx's RX mailbox at the next edge (§7.5, §10 mbx_rx_push).
        Ignored if RX is full during that cycle. One push per context per edge."""
        if self._host_rx_push[ctx] is not None:
            raise RuntimeError("one host RX push per context per edge")
        self._host_rx_push[ctx] = value & M32

    def host_tx_pop(self, ctx: int) -> Optional[int]:
        """Host pops ctx's TX mailbox at the next edge; returns the current TX head
        (mbx_tx_data) or None if TX is empty. One pop per context per edge."""
        if self._host_tx_pop[ctx]:
            raise RuntimeError("one host TX pop per context per edge")
        tx = self.ctx[ctx].tx
        if not tx:
            return None
        self._host_tx_pop[ctx] = True
        return tx[0]

    def host_tape_ack(self, rptr: int) -> None:
        """§6.5 v1.3 TAPE_ACK: rptr <= rptr at the next edge. While stopped the edges are
        not simulated one by one, so it applies at once and the records it releases
        drain at once (the host cannot observe the few cycles this takes)."""
        if self.run_flag:
            self._host_tape_ack = rptr & 0xFF
        else:
            self.cap.rptr = rptr & 0xFF
            self._drain_stopped()

    def tape_at(self, t: int, addr: int) -> int:
        """The tape word at addr during cycle t of this run (for host reads)."""
        v = self.image_tape[addr]
        for te, a, w in self.tape_log:
            if te >= t:
                break
            if a == addr:
                v = w
        return v

    def outputs(self) -> tuple[int, int]:
        """(uio_out, uio_oe) during the current cycle (§5)."""
        return self._pin_outputs()

    def outputs_b(self) -> int:
        """Bank B outputs uo_out[7:3] during the current cycle (§5, v1.2)."""
        return self._ob()

    def host_irq_clear(self, mask: int) -> None:
        """§10 (v1.0.1) irq_clear[3:0], asserted for the next edge."""
        self._host_irq_clear |= mask & 0xF

    @property
    def irq_pending(self) -> int:
        """§10 status port irq_pending[3:0] (current state)."""
        return sum(x.irq_pending << k for k, x in enumerate(self.ctx))

    @property
    def fault_valid(self) -> bool:
        """§10 status port fault_valid (first-fault register valid)."""
        return self.ff.valid

    def dbg_read(self, ctx: int, sel: int) -> int:
        """§10 dbg_sel map, current state."""
        x = self.ctx[ctx & 3]
        ch = x.chk
        sel &= 0x1F
        if sel >= 16:                              # v1.1, ignore ctx
            lu = self.lu
            return {16: self.dg, 17: self.cap.status(), 18: self.cap.config(),
                    19: self.cap.ovf_tick, 20: self.cap.rptr,
                    21: lu.status(), 22: lu.cfg.w0, 23: lu.cfg.w1, 24: lu.cfg.w2,
                    25: lu.e, 26: lu.dbg26()}.get(sel, 0)
        if sel <= 3:
            return x.r[sel]
        if sel == 4:
            return x.sh
        if sel == 5:
            return x.lc & 0xFFFF
        if sel == 6:
            return x.tmr
        if sel == 7:
            return (x.lr << 16) | (x.pc << 8) | (x.state << 2) | (x.z << 1) | x.c
        if sel == 8:
            return x.ish
        if sel == 9:
            return (x.min_lead << 16) | x.wait_full
        if sel == 10:
            return ((ch.fail_count << 24) | (ch.pass_count << 16) | ((x.max_occupancy & 7) << 8)
                    | ((len(x.fifo) & 7) << 4) | (int(ch.active) << 1) | int(ch.ff_valid))
        if sel == 11:
            return ch.ff_tick
        if sel == 12:
            return (ch.ff_mask << 16) | (ch.ff_level << 8) | ch.ff_obs
        if sel == 13:
            return (x.irq_pending << 8) | x.irq_code
        if sel == 14:
            f = self.ff
            return ((int(f.valid) << 31) | ((f.cause & 0xF) << 24) | ((f.subcode & 0xF) << 20)
                    | ((f.ctx & 3) << 16) | (f.pc & 0xFF))
        return self.ff.tick  # sel 15

    # ------------------------------------------------------------------ §5 pins

    def _pin_outputs(self) -> tuple[int, int]:
        """§5 outputs during the current cycle from the pin registers."""
        out = oe = 0
        for i in range(N_PINS):
            v = (self.val >> i) & 1
            e = (self.oe >> i) & 1
            if self.pin_od[i]:
                oe |= (e & (v ^ 1)) << i      # uio_out = 0, uio_oe = oe & ~val
            else:
                out |= v << i
                oe |= e << i
        return out, oe

    def _ob(self) -> int:
        """§5 bank B outputs: uo_out[3+i] = oe ? val : 1 for physical pin 8+i (released
        split pins are high)."""
        return ((self.val >> BANK_B) | ~(self.oe >> BANK_B)) & 0x1F

    def _inputs(self, t: int, out: int, oe: int, ob: int) -> int:
        """The 13 physical inputs during cycle t: {ui_in[7:3], uio_in}."""
        return ((self.board(t, out, oe) & 0xFF)
                | ((self.board_b(t, ob) & 0x1F) << BANK_B))

    def _sync_shift(self, pins_in: int) -> None:
        """§5 two flip-flop synchroniser, at every edge."""
        self.s2 = self.s1
        self.s1 = pins_in

    def _phys_owned(self, k: int, p: int) -> bool:
        return self.pin_owned[p] and self.pin_owner[p] == k

    def _owns(self, k: int, pin: int) -> bool:
        """§5: context k owns logical pin `pin`."""
        if self.ctx_bank[k]:
            return pin < 5 and self._phys_owned(k, BANK_B + pin)
        return self._phys_owned(k, pin)

    # ------------------------------------------------------------------ one run cycle

    def _is_idle(self) -> bool:
        return (all(x.state != RUNNING and not x.fifo and not x.chk.active for x in self.ctx)
                and not self.cap.en and not self.cap.stage and self.lu.mode == LU_IDLE)

    def _cycle(self) -> bool:
        """Run cycle t = self.now, ending with edge E_t. Returns True if the state during
        cycle t was idle (every context IDLE/HALTED/FAULTED, FIFOs empty, no checker)."""
        t = self.now
        tm = t & M32
        out, oe = self._pin_outputs()                          # §5 outputs during t
        ob = self._ob()
        self.pins.append({"t": t, "uio_out": out, "uio_oe": oe, "ob": ob})
        pins_in = self._inputs(t, out, oe, ob)                 # inputs during t
        psync = self.s2                                        # §5 psync during t
        idle = self._is_idle()

        commit = None
        if self._stop_pending:                                 # §9 Stop at E_t
            self._edge_stop()
        else:
            self._fetch(t)                                     # §3
            commit = self._edge(t, tm, psync)
            self._capture_edge(tm, psync, commit)              # §6.5 (v1.1)
            self._digest_edge(out, oe, ob, commit)             # §10.1 (v1.1)
        self._apply_mailboxes(commit)                          # §7.5, §10 host ports
        self._apply_irq_clear(commit)                          # §10 irq_clear (v1.0.1)
        self._sync_shift(pins_in)                              # §5
        self.now = t + 1                                       # §2
        return idle

    # ------------------------------------------------------------------ §3 fetch

    def _fetch(self, t: int) -> None:
        """§3: a RUNNING context k fetches PC_k in cycles t = k (mod 4)."""
        k = t % N_CTX
        x = self.ctx[k]
        if x.state == RUNNING:
            x.inflight = InFlight(x.pc, self.imem[x.pc], t + 3)

    # ------------------------------------------------------------------ edge E_t

    def _edge(self, t: int, tm: int, psync: int) -> Optional[Commit]:
        # Each context sees its own bank's inputs as its logical pins (§5, v1.2).
        views = [view(self.ctx_bank[k], psync) for k in range(N_CTX)]
        # ---- plan: event engine for every context (§6.3, §6.4), state during cycle t
        plans = [self._event_plan(x, tm, views[k]) for k, x in enumerate(self.ctx)]
        # ---- plan: the line unit (v1.4, §6.6): its scheduled access in cycle t (the
        # owner's LDT/STT then waits) and a CONFIG completing at E_t that faults the owner
        lu = self.lu
        lu_owner = lu.owner
        self._lu_acc = lu.access_scheduled(t)
        cfg_fault = (lu.config_fault(t, lambda j: self._owns(lu_owner, j))
                     if lu_owner is not None else None)
        claimed_t = lu.claimed                     # claimed during cycle t (§6.6)

        # ---- plan: the commit at E_t, context j = (t - 3) mod 4 (§3)
        commit: Optional[Commit] = None
        j = (t - 3) % N_CTX
        xj = self.ctx[j]
        fl = xj.inflight
        if fl is not None and fl.commit_tick == t:
            xj.inflight = None
            # SPEC-Q 1: an instruction whose context is no longer RUNNING during its
            # commit cycle (only possible after LATE_HEAD) is discarded, like §9 Stop.
            if xj.state == RUNNING:
                commit = self._commit(j, fl, tm, views[j], plans[j])

        # ---- apply
        new_val, new_oe = self.val, self.oe
        owner_faulted = False
        line_retired = False
        for k, x in enumerate(self.ctx):
            p = plans[k]
            sync, bank = views[k], self.ctx_bank[k]
            my_commit = commit if (commit is not None and commit.k == k) else None
            commit_fault = my_commit is not None and my_commit.outcome == "F"
            pc_during_t = x.pc

            if commit_fault:
                self._apply_checker_eval_on_fault(x, p, tm, sync)
                new_oe = self._fault(k, my_commit.cause, my_commit.subcode, my_commit.pc,
                                     tm, new_oe)
                owner_faulted |= k == lu_owner
                continue
            if cfg_fault is not None and k == lu_owner:
                # §6.6 CONFIG completing at E_t faults the owner (never on its commit edge);
                # like any fault it cancels retirement and the checker at this edge (§8).
                self._apply_checker_eval_on_fault(x, p, tm, sync)
                new_oe = self._fault(k, FAULT_CAUSE["ADMIT"], ADMIT_SUB[cfg_fault],
                                     pc_during_t, tm, new_oe)
                owner_faulted = True
                continue

            # §7.3 GETI reads ish during c; ish is cleared at E_c, then a retiring
            # SAMPLE shifts into the cleared value (§6.3).
            if my_commit is not None and my_commit.outcome == "C" and my_commit.geti_clear:
                x.ish = 0
            # §6.3 retirement (at most one entry per context per edge)
            if p.retire is not None:
                e = x.fifo.pop(0)
                assert e is p.retire
                if e.kind == DRIVE:                # §6.3 onto the context's bank
                    mask = e.mask & ~(claimed_t if k == lu_owner else 0)   # §6.6 v1.4
                    pm = to_phys(bank, mask)
                    new_val = (new_val & ~pm) | (to_phys(bank, e.value) & pm)
                    new_oe = (new_oe & ~pm) | (to_phys(bank, e.oe) & pm)
                elif e.kind == SAMPLE:
                    x.ish = shift_in(x.ish, (sync >> e.pin) & 1, e.dir)
                elif e.kind == LINE:
                    line_retired = True            # §6.3 v1.4: starts the line unit
                # EXPECT: the checker start is part of p.chk
            if p.chk is not None:
                self._apply_checker_eval(x, p.chk, tm, sync)
            if my_commit is not None:
                self._apply_commit(x, my_commit)
            if p.late:
                # SPEC-Q 2: LATE_HEAD (unreachable). Applied after any same-edge commit
                # effects; the flush discards whatever the commit appended. The checker
                # evaluation above still counts (SPEC-Q 4). PC recorded = PC during t.
                new_oe = self._fault(k, FAULT_CAUSE["LATE_HEAD"], 0, pc_during_t, tm, new_oe)
                owner_faulted |= k == lu_owner

        # ---- the line unit at E_t (§6.6), after DRIVE retirement
        op = None
        halt_done = False
        if commit is not None and commit.outcome == "C":
            op = commit.lu_op
            halt_done = commit.halt and commit.k == lu_owner
        lu_sync = views[lu_owner] if lu_owner is not None else 0
        lu_pins, lu_tw, _ = lu.step(t, self.tape, lu_sync, owner_faulted=owner_faulted,
                                    op=op, line_retired=line_retired, halt_completed=halt_done)
        if lu.owner is not None:
            lb = self.ctx_bank[lu.owner]
            for w in lu_pins:
                pm = to_phys(lb, w.mask)
                if w.val is not None:
                    new_val = (new_val & ~pm) | (to_phys(lb, w.val) & pm)
                new_oe = (new_oe & ~pm) | (to_phys(lb, w.oe) & pm)
        if lu_tw is not None:
            a, w = lu_tw
            self.tape[a] = w & M32
            self.tape_log.append((self.now, a, w & M32))
        # §6.6: the port is busy in cycle t for a scheduled access, and in the commit cycle
        # of every SEND and LINE whatever its outcome
        self._lu_busy_port = self._lu_acc or (
            commit is not None and ((commit.insn >> 24) & 0xFF) in (OP_SEND, OP_LINE))

        self.val, self.oe = new_val & 0x1FFF, new_oe & 0x1FFF

        if commit is not None:
            self._record_commit(commit, t)
        return commit

    def _edge_stop(self) -> None:
        """§9 Stop at E_t. SPEC-Q 3: the commit due at E_t is discarded as in flight; no
        checker evaluation, retirement or fault happens at E_t."""
        for x in self.ctx:
            if x.state == RUNNING:
                x.state = HALTED
            x.fifo.clear()
            x.chk.active = False
            x.inflight = None
        self.oe = 0
        self.run_flag = False
        self._stop_pending = False
        # §6.5/§9 v1.1: generation stops; staged records drain in the following cycles
        # (the port is free while stopped), so their effect is applied here.
        self.cap.en = self.cap.snap = False
        self._take_ack()
        self._drain_stopped()
        self._ld_dg = None                         # dg does not change while stopped
        self.lu._abort()                           # §9 v1.4: as OFF, no access at E_t
        self.lu._unconfigure()

    # ------------------------------------------------------------------ §6.5 capture (v1.1)

    def _take_ack(self) -> None:
        """§6.5 v1.3: rptr <= tape_ack_v at this edge, if the host acknowledged."""
        if self._host_tape_ack is not None:
            self.cap.rptr, self._host_tape_ack = self._host_tape_ack, None

    def _drain_stopped(self) -> None:
        """Staged records drain after stop while the drain is not held (§6.5)."""
        while self.cap.stage and not self.cap.held():
            self._capture_drain()

    def _capture_drain(self) -> bool:
        """§6.5 step 1: write the staged head at ptr. Returns True if this set `full`."""
        cp = self.cap
        w = cp.stage.pop(0)
        self.tape[cp.ptr] = w
        self.tape_log.append((self.now, cp.ptr, w))
        nptr = (cp.ptr + 1) & 0xFF
        set_full = False
        if nptr == cp.lim:
            if cp.ring or cp.stream:
                nptr, cp.wrapped = cp.base, True
            else:
                cp.full, cp.en, set_full = True, False, True
                cp.stage.clear()
        cp.ptr = nptr
        return set_full

    def _capture_edge(self, tm: int, psync: int, commit: Optional[Commit]) -> None:
        """§6.5 at E_t (not start/stop): drain, generate, then CAPT."""
        cp = self.cap
        sync = view(cp.bank, psync)                                     # csync (v1.2)
        en_t, snap_t, prev_t, mask_t = cp.en, cp.snap, cp.prev, cp.mask
        busy = ((commit is not None and ((commit.insn >> 24) & 0xFF) in (OP_LDT, OP_STT))
                or self._lu_busy_port)             # v1.4 line unit (§6.6)
        capt = commit.capt if (commit is not None and commit.outcome == "C") else None
        set_full = False
        if cp.stage and not busy and not cp.held():                     # 1 drain
            set_full = self._capture_drain()
        self._take_ack()                                                # v1.3 TAPE_ACK
        if en_t and not set_full and capt is None:                      # 2 generate
            if snap_t or ((sync ^ prev_t) & mask_t) or (tm & 0xFFFFFF) == 0:
                cp.prev, cp.snap = sync & 0xFF, False
                if len(cp.stage) >= CAP_STAGE:
                    cp.ovf, cp.ovf_tick, cp.en = True, tm, False
                else:
                    cp.stage.append(((sync & 0xFF) << 24) | (tm & 0xFFFFFF))
        if capt is not None:                                            # 3 CAPT
            if capt >> 31:
                self.cap = Capture(en=True, snap=True, mask=(capt >> 16) & 0xFF,
                                   base=capt & 0xFF, lim=(capt >> 8) & 0xFF,
                                   ring=bool((capt >> 24) & 1), bank=(capt >> 25) & 1,
                                   stream=bool((capt >> 26) & 1), ptr=capt & 0xFF,
                                   rptr=capt & 0xFF, prev=cp.prev)
            else:
                cp.en = cp.snap = False

    # ------------------------------------------------------------------ §10.1 digest (v1.1)

    def _digest_edge(self, out: int, oe: int, ob: int, commit: Optional[Commit]) -> None:
        # §10.1: only active edges fold (commit observed, LDT write, or outputs changed)
        ob_low = (ob ^ 0x1F) & 0x1F                # bank B folds as ~ob: released is 0
        active = (commit is not None or self._ld_dg is not None
                  or (out, oe, ob_low) != self._dg_prev)
        self._dg_prev = (out, oe, ob_low)
        if not active:
            return
        x = ((oe & 0xFF) << 24) | ((out & 0xFF) << 16) | (ob_low << 8)
        if commit is not None:
            x ^= (OUTCOME_CODE[commit.outcome] << 14) | (commit.k << 12) | commit.pc
            if commit.outcome == "C" and commit.rd_val is not None:
                x ^= commit.rd_val
        if self._ld_dg is not None:                # LDT completed at E_(t-1)
            x ^= self._ld_dg
        self._ld_dg = (commit.ld_val if (commit is not None and commit.outcome == "C")
                       else None)
        d = self.dg
        self.dg = (((d << 1) & M32) ^ (DG_POLY if d >> 31 else 0) ^ x) & M32

    # ------------------------------------------------------------------ §6.3 / §6.4 plan

    def _event_plan(self, x: Context, tm: int, sync: int) -> EventPlan:
        p = EventPlan()
        if x.fifo:
            h = x.fifo[0]
            if h.deadline == tm:
                p.retire = h
            elif reached(tm, h.deadline):
                p.late = True                      # §6.3 LATE_HEAD
        p.chk = self._checker_plan(x, p.retire, tm, sync)
        return p

    @staticmethod
    def _checker_plan(x: Context, retire: Optional[Entry], tm: int, sync: int
                      ) -> Optional[CheckerEval]:
        """§6.4: evaluate tick u = tm at E_u, for an active checker or one starting now."""
        if retire is not None and retire.kind == EXPECT:
            start = retire
            mask, level, mode = retire.mask, retire.level, retire.mode
            end = (retire.deadline + retire.window) & M32
        elif x.chk.active:
            start = None
            mask, level, mode, end = x.chk.mask, x.chk.level, x.chk.mode, x.chk.end
        else:
            return None
        m = (sync & mask) == level
        last = tm == end
        if mode == HOLD:
            result = "fail" if not m else ("pass" if last else None)
        else:  # ARRIVE
            result = "pass" if m else ("fail" if last else None)
        return CheckerEval(start, result, result is None, mask, level)

    def _apply_checker_eval(self, x: Context, ev: CheckerEval, tm: int, sync: int) -> None:
        ch = x.chk
        if ev.start is not None:
            e = ev.start
            ch.d, ch.end = e.deadline, (e.deadline + e.window) & M32
            ch.mask, ch.level, ch.mode = e.mask, e.level, e.mode
        ch.active = ev.active_after
        if ev.result == "pass":
            ch.pass_count = min(ch.pass_count + 1, 0xFF)
        elif ev.result == "fail":
            ch.fail_count = min(ch.fail_count + 1, 0xFF)
            if not ch.ff_valid:                    # first fail since start
                ch.ff_valid = True
                ch.ff_tick, ch.ff_obs = tm, sync & 0xFF
                ch.ff_mask, ch.ff_level = ev.mask, ev.level

    def _apply_checker_eval_on_fault(self, x: Context, p: EventPlan, tm: int, sync: int
                                     ) -> None:
        """Spec v1.0.2 §8 (resolves SPEC-Q 4): a fault at E_t cancels the checker's
        evaluation of tick t, like the retirement it also cancels; the checker is
        deactivated with no pass/fail count or first_fail update at that edge."""
        x.chk.active = False

    # ------------------------------------------------------------------ §8 faults

    def _fault(self, k: int, cause: int, subcode: int, pc: int, tm: int, new_oe: int) -> int:
        x = self.ctx[k]
        x.state = FAULTED
        x.fifo.clear()                            # flushed, nothing retires at this edge
        x.chk.active = False                      # checker deactivated
        for p in range(N_PHYS):                   # release owned physical pins (oe only)
            if self._phys_owned(k, p):
                new_oe &= ~(1 << p)
        if not self.ff.valid:
            self.ff = FirstFault(True, cause, subcode if cause == FAULT_CAUSE["ADMIT"] else 0,
                                 k, pc, tm)
        return new_oe

    # ------------------------------------------------------------------ §7 commit

    def _commit(self, k: int, fl: InFlight, c: int, sync: int, plan: EventPlan) -> Commit:
        x = self.ctx[k]
        cm = Commit(k, fl.pc, fl.insn)
        # Working copies of the state during cycle c; modified by the semantics below.
        cm.r = list(x.r)
        cm.sh, cm.lc, cm.tmr, cm.z, cm.c, cm.lr = x.sh, x.lc, x.tmr, x.z, x.c, x.lr
        cm.next_pc = (fl.pc + 1) & 0xFF
        dec = decode(fl.insn)
        try:
            if dec is None:
                self._fail(cm, "ILLEGAL")
            # Q = FIFO after the retirement at E_c (§6.2, §7.2 HALT)
            q = x.fifo[1:] if plan.retire is not None else list(x.fifo)
            chk_active_after = plan.chk.active_after if plan.chk is not None else x.chk.active
            _SEM[dec.name](self, x, k, cm, dec.f, c, sync, q, chk_active_after)
            # §7 PC_RANGE
            if fl.pc == 255 and (cm.sequential or dec.name == "CALL"):
                self._fail(cm, "PC_RANGE")
        except _Done:
            pass
        return cm

    @staticmethod
    def _fail(cm: Commit, cause: str, sub: str | None = None) -> None:
        cm.outcome = "F"
        cm.cause = FAULT_CAUSE[cause]
        cm.subcode = ADMIT_SUB[sub] if sub else 0
        raise _Done

    @staticmethod
    def _wait(cm: Commit, full: bool = False) -> None:
        cm.outcome = "W"
        cm.wait_full_inc = full
        raise _Done

    def _apply_commit(self, x: Context, cm: Commit) -> None:
        if cm.outcome == "W":
            if cm.wait_full_inc:                       # §7.4 only effect of a WAIT
                x.wait_full = min(x.wait_full + 1, 0xFFFF)
            return
        x.r = [v & M32 for v in cm.r]
        x.sh, x.lc, x.tmr = cm.sh & M32, cm.lc & 0xFFFF, cm.tmr & M32
        x.z, x.c, x.lr = cm.z, cm.c, cm.lr & 0xFF
        x.pc = cm.next_pc & 0xFF
        if cm.halt:
            x.state = HALTED                           # §7.2 HALT, PC unchanged
        if cm.append is not None:                      # §6.2 append at E_c
            x.fifo.append(cm.append)
            x.min_lead = min(x.min_lead, min(cm.lead, 0xFFFF))      # §7.4 telemetry
            x.max_occupancy = max(x.max_occupancy, cm.occupancy)
            if cm.expect_end is not None:
                x.expect_admitted = True
                x.last_expect_end = cm.expect_end
        if cm.irq_code is not None:
            x.irq_pending, x.irq_code = 1, cm.irq_code
        if cm.tape_write is not None:                  # §7.6 STT at E_c
            a, w = cm.tape_write
            self.tape[a] = w & M32
            self.tape_log.append((self.now, a, w & M32))
        # cm.rx_pop / cm.tx_push are applied with the host ports (_apply_mailboxes)

    def _record_commit(self, cm: Commit, t: int) -> None:
        x = self.ctx[cm.k]
        self.commits.append({
            "t": t, "ctx": cm.k, "pc": cm.pc, "insn": cm.insn, "outcome": cm.outcome,
            "post": {"r0": x.r[0], "r1": x.r[1], "r2": x.r[2], "r3": x.r[3], "sh": x.sh,
                     "lc": x.lc, "tmr": x.tmr, "z": x.z, "c": x.c, "pc": x.pc},
        })

    # ------------------------------------------------------------------ §7.5 mailboxes

    def _apply_mailboxes(self, commit: Optional[Commit]) -> None:
        """Mailbox updates at the edge: the committing context's POP/PUSH (COMPLETE only)
        and the host ports. Host full/empty checks use the state during the cycle (§10:
        ignored when full or empty; never visible to a commit at the same edge). SPEC-Q 9."""
        for k, x in enumerate(self.ctx):
            mine = commit if (commit is not None and commit.k == k
                              and commit.outcome == "C") else None
            rx_full_before = len(x.rx) >= MBX_DEPTH
            tx_empty_before = not x.tx
            if mine is not None and mine.rx_pop:
                x.rx.pop(0)
            if self._host_rx_push[k] is not None and not rx_full_before:
                x.rx.append(self._host_rx_push[k])
            if self._host_tx_pop[k] and not tx_empty_before:
                x.tx.pop(0)
            if mine is not None and mine.tx_push is not None:
                x.tx.append(mine.tx_push)
        self._host_rx_push = [None] * N_CTX
        self._host_tx_pop = [False] * N_CTX

    def _apply_irq_clear(self, commit: Optional[Commit]) -> None:
        """§10 v1.0.1: at an edge where irq_clear[k] is high, irq_pending[k] is cleared,
        unless an IRQ instruction of context k commits at that edge (set wins; its code
        was recorded by _apply_commit). SPEC-Q 15: "commits" = COMPLETE (an IRQ at PC 255
        faults PC_RANGE and has no effect, so the clear applies); irq_code is left as is.
        """
        for k, x in enumerate(self.ctx):
            if not (self._host_irq_clear >> k) & 1:
                continue
            irq_set = (commit is not None and commit.k == k and commit.outcome == "C"
                       and commit.irq_code is not None)
            if not irq_set:
                x.irq_pending = 0
        self._host_irq_clear = 0

    # ------------------------------------------------------------------ §6.2 admission

    def _admit(self, x: Context, k: int, cm: Commit, e: Entry, c: int, q: list[Entry]) -> bool:
        if len(q) == FIFO_DEPTH:                                        # 1
            self._wait(cm, full=True)
        if e.kind in (DRIVE, EXPECT) and e.mask == 0:                   # 2
            self._fail(cm, "ADMIT", "MASK")
        claimed = self.lu.claimed if self.lu.owner == k else 0         # v1.4 §6.6
        if e.kind == DRIVE and any((e.mask >> i) & 1 and (not self._owns(k, i)
                                                          or (claimed >> i) & 1)
                                   for i in range(N_PINS)):             # 3
            self._fail(cm, "ADMIT", "OWNER")
        if e.kind == LINE:                                              # 3 (v1.4 SEND)
            lu = self.lu
            if lu.owner != k or not lu.configured or not lu.cfg.tx_pins:
                self._fail(cm, "ADMIT", "OWNER")
            if lu.engaged():
                self._wait(cm)
        if not ahead(e.deadline, c):                                    # 4
            if e.kind == LINE:                                          # v1.4: a late SEND
                cm.z = 0                                                # is dropped
                return False
            self._fail(cm, "ADMIT", "PAST")
        if q and not ahead(e.deadline, q[-1].deadline):                 # 5
            self._fail(cm, "ADMIT", "ORDER")
        if e.kind == EXPECT and x.expect_admitted and not ahead(e.deadline, x.last_expect_end):
            self._fail(cm, "ADMIT", "OVERLAP")                          # 6
        cm.append = e
        cm.lead = (e.deadline - c) & M32                                # §7.4 telemetry
        cm.occupancy = len(q) + 1
        if e.kind == EXPECT:
            cm.expect_end = (e.deadline + e.window) & M32
        return True

    # ------------------------------------------------------------------ trace / summary

    def final_state(self) -> dict:
        ctxs = []
        for x in self.ctx:
            ch = x.chk
            ctxs.append({
                "state": STATE_NAMES[x.state], "r0": x.r[0], "r1": x.r[1], "r2": x.r[2],
                "r3": x.r[3], "sh": x.sh, "lc": x.lc, "tmr": x.tmr, "pc": x.pc, "lr": x.lr,
                "z": x.z, "c": x.c, "ish": x.ish,
                "min_lead": x.min_lead, "max_occupancy": x.max_occupancy,
                "wait_full": x.wait_full, "fifo_count": len(x.fifo),
                "checker": {"active": int(ch.active), "pass_count": ch.pass_count,
                            "fail_count": ch.fail_count, "failed_since_start": int(ch.ff_valid),
                            "first_fail": {"tick": ch.ff_tick, "observed": ch.ff_obs,
                                           "mask": ch.ff_mask, "level": ch.ff_level}},
                "irq_pending": x.irq_pending, "irq_code": x.irq_code,
            })
        f = self.ff
        return {
            "now": self.now,
            "contexts": ctxs,
            "first_fault": {"valid": int(f.valid), "cause": f.cause, "subcode": f.subcode,
                            "ctx": f.ctx, "pc": f.pc, "tick": f.tick},
            "digest": self.dg,
            "capture": {"status": self.cap.status(), "config": self.cap.config(),
                        "ovf_tick": self.cap.ovf_tick},
            "line": {"status": self.lu.status(), "end_tick": self.lu.e,
                     "dbg26": self.lu.dbg26()},
        }

    def write_trace(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for rec in self.commits:
                fh.write(json.dumps(rec) + "\n")
            for rec in self.pins:
                fh.write(json.dumps(rec) + "\n")
            fh.write(json.dumps({"final": self.final_state()}) + "\n")


# =========================================================================== §7 semantics
# Each function: (machine, ctx, k, commit, fields, c, sync, Q, checker_active_after).
# They modify the working copies in `cm` and may raise via _wait/_fail.


def _z(v: int) -> int:
    return 1 if (v & M32) == 0 else 0


def _write(cm: Commit, rd: int, v: int) -> None:
    """Write R[rd] and set Z from the written result (§7.1)."""
    v &= M32
    cm.r[rd] = v
    cm.z = _z(v)
    cm.rd_val = v                                   # §10.1 wv


def _jump(cm: Commit, target: int) -> None:
    cm.next_pc = target & 0xFF
    cm.sequential = False


# ---- §7.1 data

def _MOV(m, x, k, cm, f, c, sync, q, ca):
    _write(cm, f["rd"], cm.r[f["rs"]])


def _MOVI(m, x, k, cm, f, c, sync, q, ca):
    _write(cm, f["rd"], f["imm"])


def _MOVHI(m, x, k, cm, f, c, sync, q, ca):
    _write(cm, f["rd"], (f["imm"] << 16) | (cm.r[f["rd"]] & 0xFFFF))


def _add(cm, rd, b):
    s = cm.r[rd] + b
    _write(cm, rd, s)
    cm.c = 1 if s > M32 else 0                      # carry out of bit 31


def _ADD(m, x, k, cm, f, c, sync, q, ca):
    _add(cm, f["rd"], cm.r[f["rs"]])


def _ADDI(m, x, k, cm, f, c, sync, q, ca):
    _add(cm, f["rd"], f["imm"])


def _sub_flags(cm, a, b):
    cm.z = _z(a - b)
    cm.c = 1 if a < b else 0                         # borrow, unsigned


def _SUB(m, x, k, cm, f, c, sync, q, ca):
    a, b = cm.r[f["rd"]], cm.r[f["rs"]]
    cm.r[f["rd"]] = cm.rd_val = (a - b) & M32
    _sub_flags(cm, a, b)


def _CMP(m, x, k, cm, f, c, sync, q, ca):
    _sub_flags(cm, cm.r[f["rd"]], cm.r[f["rs"]])


def _CMPI(m, x, k, cm, f, c, sync, q, ca):
    _sub_flags(cm, cm.r[f["rd"]], f["imm"])


def _AND(m, x, k, cm, f, c, sync, q, ca):
    _write(cm, f["rd"], cm.r[f["rd"]] & cm.r[f["rs"]])


def _OR(m, x, k, cm, f, c, sync, q, ca):
    _write(cm, f["rd"], cm.r[f["rd"]] | cm.r[f["rs"]])


def _XOR(m, x, k, cm, f, c, sync, q, ca):
    _write(cm, f["rd"], cm.r[f["rd"]] ^ cm.r[f["rs"]])


def _TSTI(m, x, k, cm, f, c, sync, q, ca):
    cm.z = _z(cm.r[f["rd"]] & f["imm"])


def _SHL(m, x, k, cm, f, c, sync, q, ca):
    rd, n = f["rd"], f["n"]
    v = cm.r[rd]
    if n == 0:
        # SPEC-Q 5: "no change, C=0"; Z is still set from the (unchanged) result.
        cm.c = 0
        cm.z = _z(v)
        cm.rd_val = v                                 # §10.1: n = 0 writes the unchanged value
        return
    cm.c = (v >> (32 - n)) & 1
    _write(cm, rd, v << n)


def _SHR(m, x, k, cm, f, c, sync, q, ca):
    rd, n = f["rd"], f["n"]
    v = cm.r[rd]
    if n == 0:
        cm.c = 0                                      # SPEC-Q 5
        cm.z = _z(v)
        cm.rd_val = v                                 # §10.1
        return
    cm.c = (v >> (n - 1)) & 1
    _write(cm, rd, v >> n)


def _LDSH(m, x, k, cm, f, c, sync, q, ca):
    cm.sh = cm.r[f["rs"]]


def _STSH(m, x, k, cm, f, c, sync, q, ca):
    cm.r[f["rd"]] = cm.rd_val = cm.sh


# ---- §7.2 control

def _NOP(m, x, k, cm, f, c, sync, q, ca):
    pass


def _JMP(m, x, k, cm, f, c, sync, q, ca):
    _jump(cm, f["target"])


def _JZ(m, x, k, cm, f, c, sync, q, ca):
    if cm.z:
        _jump(cm, f["target"])


def _JNZ(m, x, k, cm, f, c, sync, q, ca):
    if not cm.z:
        _jump(cm, f["target"])


def _JC(m, x, k, cm, f, c, sync, q, ca):
    if cm.c:
        _jump(cm, f["target"])


def _JNC(m, x, k, cm, f, c, sync, q, ca):
    if not cm.c:
        _jump(cm, f["target"])


def _LDLC(m, x, k, cm, f, c, sync, q, ca):
    cm.lc = f["imm"]


def _DJNZ(m, x, k, cm, f, c, sync, q, ca):
    cm.lc = (cm.lc - 1) & 0xFFFF
    if cm.lc != 0:
        _jump(cm, f["target"])


def _CALL(m, x, k, cm, f, c, sync, q, ca):
    cm.lr = (cm.pc + 1) & 0xFF                        # PC_RANGE at 255 is checked by caller
    _jump(cm, f["target"])


def _RET(m, x, k, cm, f, c, sync, q, ca):
    _jump(cm, cm.lr)


def _IRQ(m, x, k, cm, f, c, sync, q, ca):
    cm.irq_code = f["code"]


def _HALT(m, x, k, cm, f, c, sync, q, ca):
    # v1.4: an owner of an engaged line unit waits too (§7.2)
    if not q and not ca and not (m.lu.owner == k and m.lu.engaged()):
        cm.halt = True
        cm.next_pc = cm.pc                            # PC unchanged (not sequential)
        cm.sequential = False
    else:
        Machine._wait(cm)


# ---- §7.3 time and input

def _RDTIME(m, x, k, cm, f, c, sync, q, ca):
    cm.r[f["rd"]] = cm.rd_val = c


def _SETT(m, x, k, cm, f, c, sync, q, ca):
    cm.tmr = cm.r[f["rs"]]


def _ADDT(m, x, k, cm, f, c, sync, q, ca):
    cm.tmr = (cm.tmr + f["imm"]) & M32


def _ADDTR(m, x, k, cm, f, c, sync, q, ca):
    cm.tmr = (cm.tmr + cm.r[f["rs"]]) & M32


def _WAITT(m, x, k, cm, f, c, sync, q, ca):
    if not reached(c, cm.tmr):
        Machine._wait(cm)


def _GETP(m, x, k, cm, f, c, sync, q, ca):
    _write(cm, f["rd"], sync)


def _WAITP(m, x, k, cm, f, c, sync, q, ca):
    if (sync & f["mask"]) == f["value"]:
        cm.z = 1
    elif reached(c, cm.tmr):
        cm.z = 0
    else:
        Machine._wait(cm)


def _GETI(m, x, k, cm, f, c, sync, q, ca):
    # v1.1 (D-20): wait while the FIFO during cycle c holds a SAMPLE (even one retiring
    # at E_c), so the last sampled bit is never lost into the next word.
    if any(e.kind == SAMPLE for e in x.fifo):
        Machine._wait(cm)
    _write(cm, f["rd"], x.ish)
    cm.geti_clear = True


def _GETX(m, x, k, cm, f, c, sync, q, ca):
    ch = x.chk                                        # checker status during cycle c
    v = (ch.fail_count << 24) | (ch.pass_count << 16) | (int(ch.active) << 1) | int(ch.ff_valid)
    _write(cm, f["rd"], v)


# ---- §7.4 events

def _EMIT(m, x, k, cm, f, c, sync, q, ca):
    m._admit(x, k, cm, Entry(DRIVE, cm.tmr, mask=f["mask"], value=f["value"], oe=f["oe"]), c, q)


def _EMITR(m, x, k, cm, f, c, sync, q, ca):
    m._admit(x, k, cm, Entry(DRIVE, cm.tmr, mask=f["mask"], value=cm.r[f["rs"]] & 0xFF,
                             oe=f["oe"]), c, q)


def _EMITX(m, x, k, cm, f, c, sync, q, ca):
    d = f["dir"]
    b = (cm.sh >> 31) & 1 if d else cm.sh & 1
    m._admit(x, k, cm, Entry(DRIVE, cm.tmr, mask=f["mask"], value=f["mask"] if b else 0,
                             oe=f["oe"]), c, q)
    # only reached on COMPLETE
    cm.sh = ((cm.sh << 1) if d else (cm.sh >> 1)) & M32
    cm.c = b


def _EXPECT(m, x, k, cm, f, c, sync, q, ca):
    m._admit(x, k, cm, Entry(EXPECT, cm.tmr, mask=f["mask"], level=f["level"], mode=f["mode"],
                             window=f["window"]), c, q)


def _SAMPLE(m, x, k, cm, f, c, sync, q, ca):
    m._admit(x, k, cm, Entry(SAMPLE, cm.tmr, pin=f["pin"], dir=f["dir"]), c, q)


def _SEND(m, x, k, cm, f, c, sync, q, ca):
    v = cm.r[f["rs"]]
    if m._admit(x, k, cm, Entry(LINE, cm.tmr, cmd=v & 0xFFFFFF), c, q):
        cm.z = 1
        cm.lu_op = ("SEND", v, k)                     # admitted; Z = 0 if dropped


# ---- §7.7 line unit (v1.4)

def _LINE(m, x, k, cm, f, c, sync, q, ca):
    v = cm.r[f["rs"]]
    op = (v >> 28) & 0xF
    lu = m.lu
    if op > OP_CONFIG:
        Machine._fail(cm, "ILLEGAL")
    if op == OP_CONFIG:
        if lu.owner is not None and lu.owner != k:
            Machine._fail(cm, "ADMIT", "OWNER")
    else:
        if lu.owner != k or (op in (OP_RECV, OP_CRC) and not lu.configured):
            Machine._fail(cm, "ADMIT", "OWNER")
    if op != OP_OFF and lu.engaged():
        Machine._wait(cm)
    cm.lu_op = ("LINE", v, k)


def _GETL(m, x, k, cm, f, c, sync, q, ca):
    lu = m.lu
    if lu.owner != k:
        Machine._fail(cm, "ADMIT", "OWNER")
    st = lu.status()                                  # status during cycle c
    if not lu.busy():
        cm.z, cm.tmr = 1, lu.e
    elif reached(c, cm.tmr):
        cm.z = 0
    else:
        Machine._wait(cm)
    cm.r[f["rd"]] = cm.rd_val = st


# ---- §7.6 tape and capture (v1.1)

def _tape_addr(cm, f):
    return (cm.r[f["rb"]] + f["imm"]) & 0xFF


def _lu_conflict(m, k, cm) -> None:
    """§6.6 v1.4: the owner's LDT/STT waits in a cycle with a scheduled LU access."""
    if m._lu_acc and m.lu.owner == k:
        Machine._wait(cm)


def _LDT(m, x, k, cm, f, c, sync, q, ca):
    _lu_conflict(m, k, cm)
    v = m.tape[_tape_addr(cm, f)]                     # T during cycle c
    cm.r[f["ra"]] = v
    cm.ld_val = v                                     # flags unchanged


def _STT(m, x, k, cm, f, c, sync, q, ca):
    _lu_conflict(m, k, cm)
    cm.tape_write = (_tape_addr(cm, f), cm.r[f["ra"]])


def _CAPT(m, x, k, cm, f, c, sync, q, ca):
    cm.capt = cm.r[f["rs"]]


def _GETC(m, x, k, cm, f, c, sync, q, ca):
    _write(cm, f["rd"], m.cap.status())               # capture state during cycle c


# ---- §7.5 host mailboxes

def _POP(m, x, k, cm, f, c, sync, q, ca):
    if not x.rx:
        Machine._wait(cm)
    cm.r[f["rd"]] = cm.rd_val = x.rx[0]
    cm.rx_pop = True


def _PUSH(m, x, k, cm, f, c, sync, q, ca):
    if len(x.tx) >= MBX_DEPTH:
        Machine._wait(cm)
    cm.tx_push = cm.r[f["rs"]]


def _RXREADY(m, x, k, cm, f, c, sync, q, ca):
    cm.z = 1 if not x.rx else 0


def _TXREADY(m, x, k, cm, f, c, sync, q, ca):
    cm.z = 1 if len(x.tx) >= MBX_DEPTH else 0


_SEM = {name[1:]: fn for name, fn in dict(globals()).items()
        if name.startswith("_") and name[1:].isupper() and callable(fn)}
assert set(_SEM) == set(ISA.by_name), sorted(set(_SEM) ^ set(ISA.by_name))
