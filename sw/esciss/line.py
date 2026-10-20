"""The line unit (docs/11-SEMANTICS.md v1.4.0 §6.6), as the ISS models it.

Written from the semantics document only, like the rest of the ISS. For every edge E_t of
a run the machine calls

  plan phase   `access_scheduled(t)`  the LU uses the tape port in cycle t for an access
                                      requested at an earlier edge
               `config_fault(t, ...)` a CONFIG completing at E_t that faults its owner
  apply phase  `step(...)`            what the LU does at E_t, after the contexts' DRIVE
                                      retirements; returns pin writes and a tape write

and the commit semantics (machine.py) read `owner`, `configured`, `engaged()`, `busy()`,
`status()` and `e`, all as they are during the commit cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

M32 = 0xFFFFFFFF

IDLE, CFG, PEND, TX, ARM, RX, CRC = 0, 1, 2, 3, 4, 5, 6     # §10 sel 26 mode encoding
JOB_RECV, JOB_CRC, JOB_CONFIG, JOB_SEND = 1, 2, 3, 4
OK, VIOL, SE0, STUFF, FULL, NOSYNC, MISMATCH = 0, 1, 2, 3, 4, 5, 6
OP_OFF, OP_RECV, OP_CRC, OP_CONFIG = 0, 1, 2, 3


def owner_cycle(t: int, owner: Optional[int]) -> bool:
    """§6.6: the owner's commit cycles are the cycles u with u mod 4 == (owner + 3) mod 4."""
    return owner is not None and t % 4 == (owner + 3) % 4


def crc_step(crc: int, b: int, poly: int) -> int:
    """§6.6: crc <= ((crc << 1) mod 2^16) XOR ((crc[15] XOR b) ? poly : 0)."""
    return ((crc << 1) & 0xFFFF) ^ (poly if ((crc >> 15) & 1) ^ b else 0)


@dataclass(frozen=True)
class Cfg:
    """The fields of W0, W1, W2 (§6.6 configuration table)."""
    w0: int = 0
    w1: int = 0
    w2: int = 0

    @property
    def d(self): return self.w0 & 7
    @property
    def n(self): return (self.w0 >> 3) & 7
    @property
    def r(self): return (self.w0 >> 6) & 7
    @property
    def diff(self): return (self.w0 >> 9) & 1
    @property
    def nrzi(self): return (self.w0 >> 10) & 1
    @property
    def lsb(self): return (self.w0 >> 11) & 1
    @property
    def N(self): return (self.w0 >> 12) & 7
    @property
    def any(self): return (self.w0 >> 15) & 1
    @property
    def eop(self): return (self.w0 >> 16) & 3
    @property
    def idle_drive(self): return (self.w0 >> 18) & 1
    @property
    def sync_skip(self): return (self.w0 >> 19) & 1
    @property
    def tx_pins(self): return (self.w0 >> 20) & 1
    @property
    def prefix(self): return (self.w0 >> 24) & 0xFF
    @property
    def P_int(self): return self.w1 & 0xFFF
    @property
    def P_frac(self): return (self.w1 >> 12) & 0xFF
    @property
    def S(self): return (self.w1 >> 20) & 0xFFF
    @property
    def poly(self): return self.w2 & 0xFFFF
    @property
    def init_ones(self): return (self.w2 >> 16) & 1
    @property
    def inv(self): return (self.w2 >> 17) & 1
    @property
    def hdr(self): return (self.w2 >> 18) & 1
    @property
    def fall(self): return (self.w2 >> 19) & 1
    @property
    def abort(self): return (self.w2 >> 20) & 1
    @property
    def idle_min(self): return (self.w2 >> 21) & 0xF

    def valid(self) -> bool:
        return self.P_int >= 8 and 4 <= self.S < self.P_int

    def width(self) -> int:
        """CRC width w = 16 - ctz(poly); 0 when poly == 0."""
        p = self.poly
        return 16 - ((p & -p).bit_length() - 1) if p else 0

    def crc_start(self) -> int:
        """M = -(poly & -poly) mod 2^16 if init_ones, else 0 (and 0 when poly == 0)."""
        p = self.poly
        return (-(p & -p)) & 0xFFFF if (p and self.init_ones) else 0

    def claimed(self) -> int:
        """Logical mask of the pins this configuration claims."""
        if not self.tx_pins:
            return 0
        return (1 << self.d) | ((1 << self.n) if self.diff else 0)

    def byte_bit(self, v: int, i: int) -> int:
        """Bit i (0 first on the line) of a byte field."""
        return (v >> i) & 1 if self.lsb else (v >> (7 - i)) & 1

    def word_pos(self, j: int) -> int:
        """Position of stream bit j inside its tape word."""
        return j % 32 if self.lsb else 31 - (j % 32)

    def bit_len(self, alpha: int) -> int:
        """Length of a bit whose boundary carries accumulator alpha."""
        return self.P_int + (1 if alpha + self.P_frac >= 256 else 0)


@dataclass
class Stuffer:
    """The run of the last line bits (§6.6 stuffing), the same for transmit and receive."""
    run: int = 0
    bit: int = 0

    def due(self, cfg: Cfg) -> bool:
        return cfg.N > 0 and self.run >= cfg.N

    def stuff_bit(self, cfg: Cfg) -> int:
        return (self.bit ^ 1) if cfg.any else 0

    def push(self, b: int, cfg: Cfg) -> None:
        if cfg.any:
            if self.run > 0 and b == self.bit:
                self.run += 1
            else:
                self.run, self.bit = 1, b
        else:
            self.run = self.run + 1 if b else 0


@dataclass
class Request:
    """A tape access made in the first owner commit cycle u >= earliest."""
    earliest: int
    addr: int
    write: bool


@dataclass
class PinWrite:
    """The owner's logical pins in `mask` get val (None: unchanged) and oe at this edge."""
    mask: int
    val: Optional[int]
    oe: int


@dataclass
class LineUnit:
    owner: Optional[int] = None
    configured: bool = False
    cfg: Cfg = field(default_factory=Cfg)
    claimed: int = 0
    mode: int = IDLE
    buf: int = 0
    crc: int = 0
    last_job: int = 0
    cause: int = 0
    count: int = 0
    e: int = 0
    req: Optional[Request] = None
    # CONFIG
    cfg_c: int = 0
    cfg_addr: int = 0
    cfg_words: list[int] = field(default_factory=list)
    # CRC job
    crc_c: int = 0
    crc_addr: int = 0
    crc_nb: int = 0
    crc_wire: bool = False
    # SEND
    tx_cmd: int = 0
    tx_nxt: int = 0              # next boundary at which a symbol (or the idle state) goes
    tx_alpha: int = 0
    tx_L: int = 1
    tx_stf: Stuffer = field(default_factory=Stuffer)
    tx_segs: list[tuple[str, int]] = field(default_factory=list)
    tx_seg: int = 0
    tx_pos: int = 0
    tx_k: int = 0                # line bits driven so far
    tx_tail: list[str] = field(default_factory=list)
    mon_at: Optional[int] = None
    mon_L: int = 0
    mon_k: int = 0
    tx_abort: bool = False
    # RECV
    rx_c: int = 0
    rx_addr: int = 0
    rx_limit: int = 0
    idle_run: int = 0
    rx_t0: int = 0
    rx_alpha: int = 0            # accumulator of the current bit's boundary
    rx_nxt: int = 0              # next timed boundary
    rx_sample: Optional[int] = None
    x_prev: int = 1
    ref: int = 1
    rx_stf: Stuffer = field(default_factory=Stuffer)
    hunt: bool = False
    decoded: bool = False
    storing: bool = True
    noted: int = OK
    eop_s: Optional[int] = None
    finishing: bool = False

    # ------------------------------------------------------------------ views

    def engaged(self) -> bool:
        return self.mode in (CFG, PEND, TX, RX, CRC)

    def busy(self) -> bool:
        return self.mode != IDLE

    def status(self) -> int:
        """§6.6: [31] busy, [30:28] last job, [27:25] cause, [24:16] count, [15:0] crc."""
        return ((int(self.busy()) << 31) | (self.last_job << 28) | (self.cause << 25)
                | ((self.count & 0x1FF) << 16) | (self.crc & 0xFFFF))

    def dbg26(self) -> int:
        """§10 sel 26."""
        own = self.owner if self.owner is not None else 0
        return ((int(self.owner is not None) << 31) | (own << 28) | (self.mode << 24)
                | (int(self.configured) << 20))

    # ------------------------------------------------------------------ plan phase

    def access_scheduled(self, t: int) -> bool:
        """The LU uses the tape port in cycle t for an access requested at an earlier edge:
        CONFIG words 1 and 2, CRC words from 1, SEND words from 1, RECV writes."""
        if not owner_cycle(t, self.owner):
            return False
        if self.mode == CFG:
            return t in (self.cfg_c + 4, self.cfg_c + 8)
        if self.mode == CRC:
            m, rem = divmod(t - self.crc_c, 32)
            return rem == 0 and m >= 1 and 32 * m < self.crc_nb
        if self.mode in (TX, RX):
            return self.req is not None and t >= self.req.earliest
        return False

    def config_fault(self, t: int, owns) -> Optional[str]:
        """The ADMIT subcode with which a CONFIG completing at E_t faults the owner, if any.
        owns(j) tells whether the owner owns its logical pin j (§5)."""
        if self.mode != CFG or t != self.cfg_c + 9:
            return None
        new = Cfg(*self.cfg_words)
        if new.tx_pins and (not owns(new.d) or (new.diff and (not owns(new.n)
                                                                or new.d == new.n))):
            return "OWNER"
        if not new.valid():
            return "CONFIG"
        return None

    # ------------------------------------------------------------------ helpers

    def _abort(self) -> None:
        """End any job and any armed receiver at once; no pending access happens."""
        self.mode = IDLE
        self.req = None
        self.finishing = False

    def _scheduled_write(self, t: int) -> Optional[tuple[int, int]]:
        """A write scheduled in cycle t still happens when the job ends at E_t (a scheduled
        read would only load the buffer, which nothing reads afterwards)."""
        if self.access_scheduled(t) and self.req is not None and self.req.write:
            return self.req.addr, self.buf
        return None

    def _unconfigure(self) -> None:
        self.claimed = 0
        self.configured = False

    def _drive(self, L: int, oe: int = 1) -> PinWrite:
        c, m = self.cfg, self.claimed
        val = L << c.d
        if c.diff:
            val |= (L ^ 1) << c.n
        return PinWrite(m, val & m, m if oe else 0)

    def _se0(self) -> PinWrite:
        return PinWrite(self.claimed, 0, self.claimed)

    def _idle(self) -> Optional[PinWrite]:
        return self._drive(1, self.cfg.idle_drive) if self.claimed else None

    def _end(self, cause: int, count: int, e: int) -> None:
        self.cause, self.count, self.e = cause, count, e & M32

    def _line(self, sync: int) -> tuple[int, bool]:
        """(x_t, se0_t) from the owner's view during cycle t."""
        c = self.cfg
        if c.diff:
            dv, nv = (sync >> c.d) & 1, (sync >> c.n) & 1
            return dv, (dv == 0 and nv == 0)
        return (sync >> c.r) & 1, False

    # ------------------------------------------------------------------ apply phase

    def step(self, t: int, tape: list[int], sync: int, *, owner_faulted: bool = False,
             op: Optional[tuple[str, int, int]] = None, line_retired: bool = False,
             halt_completed: bool = False
             ) -> tuple[list[PinWrite], Optional[tuple[int, int]], bool]:
        """Everything the LU does at E_t.

        sync           the owner's logical input view during cycle t (0 with no owner)
        owner_faulted  the owner faults at E_t (§8)
        op             a COMPLETE commit at E_t acting on the LU: ("LINE", v, k) or
                       ("SEND", v, k), k the committing context
        line_retired   the owner's LINE entry retires at E_t
        halt_completed the owner's HALT completes at E_t
        Returns (pin writes, tape write (addr, word) or None, port used in cycle t).
        """
        if owner_faulted:                        # §8: as OFF; the fault releases the pins
            tw = self._scheduled_write(t)
            self._abort()
            self._unconfigure()
            return [], tw, tw is not None

        if halt_completed and self.mode == ARM:  # §7.2
            self.mode = IDLE

        if op is not None:
            return self._op(t, tape, op)

        pins: list[PinWrite] = []
        if self.mode == CFG:
            return self._cfg_edge(t, tape)
        if self.mode == CRC:
            return pins, None, self._crc_edge(t, tape)
        if line_retired and self.mode == PEND:
            self._tx_start(t)
        if self.mode == TX:
            return pins, None, self._tx_edge(t, tape, sync, pins)
        if self.mode == ARM:
            self._arm_edge(t, sync)
            return pins, None, False
        if self.mode == RX:
            return (pins,) + self._rx_edge(t, sync)
        return pins, None, False

    def _op(self, t: int, tape: list[int], op: tuple[str, int, int]):
        kind, v, k = op
        if kind == "SEND":                       # §6.6 SEND admission at E_c
            self.mode = PEND
            self.tx_cmd = v & 0xFFFFFF
            if (v >> 8) & 0x7F:
                self.buf = tape[v & 0xFF]
                return [], None, True
            return [], None, False
        o = (v >> 28) & 0xF
        if o == OP_OFF:
            tw = self._scheduled_write(t)        # an access scheduled in cycle c happens
            self._abort()
            pins = [PinWrite(self.claimed, None, 0)] if self.claimed else []
            self._unconfigure()
            return pins, tw, tw is not None
        if o == OP_CONFIG:
            if self.owner is None:
                self.owner = k
            self.mode, self.last_job, self.cause, self.count = CFG, JOB_CONFIG, 0, 0
            self.cfg_c, self.cfg_addr = t, v & 0xFF
            self.cfg_words = [tape[self.cfg_addr]]
            return [], None, True
        if o == OP_RECV:
            n = (v >> 8) & 0x1F
            self.mode, self.rx_c, self.rx_addr = ARM, t, v & 0xFF
            self.rx_limit = 8 * (n if n else 32)
            self.idle_run = 0
            return [], None, False
        if o == OP_CRC:
            self.mode, self.last_job, self.cause, self.count = CRC, JOB_CRC, 0, 0
            self.crc = self.cfg.crc_start()
            self.crc_c, self.crc_addr, self.crc_nb = t, v & 0xFF, (v >> 8) & 0xFF
            self.crc_wire = bool((v >> 16) & 1)
            if self.crc_nb == 0:
                self._crc_done(t)
                return [], None, False
            self.buf = tape[self.crc_addr]
            return [], None, True
        raise AssertionError("LINE ops 4..15 fault at the commit")

    # ------------------------------------------------------------------ CONFIG, CRC

    def _cfg_edge(self, t: int, tape: list[int]):
        if t in (self.cfg_c + 4, self.cfg_c + 8):
            self.cfg_words.append(tape[(self.cfg_addr + len(self.cfg_words)) & 0xFF])
            return [], None, True
        if t != self.cfg_c + 9:
            return [], None, False
        pins: list[PinWrite] = []                # (a faulting CONFIG is handled by the owner fault)
        new = Cfg(*self.cfg_words)
        newc = new.claimed()
        if self.claimed & ~newc:
            pins.append(PinWrite(self.claimed & ~newc, None, 0))
        self.cfg, self.claimed, self.configured = new, newc, True
        idle = self._idle()
        if idle:
            pins.append(idle)
        self.crc = new.crc_start()
        self._end(OK, 0, t)
        self.mode = IDLE
        return pins, None, False

    def _crc_edge(self, t: int, tape: list[int]) -> bool:
        j = t - self.crc_c - 1
        if 0 <= j < self.crc_nb:
            b = (self.buf >> self.cfg.word_pos(j)) & 1
            self.crc = crc_step(self.crc, b, self.cfg.poly)
            if j == self.crc_nb - 1:
                self._crc_done(t)
                return False
        m, rem = divmod(t - self.crc_c, 32)
        if rem == 0 and m >= 1 and 32 * m < self.crc_nb:
            self.buf = tape[(self.crc_addr + m) & 0xFF]
            return True
        return False

    def _crc_done(self, t: int) -> None:
        """The CRC job ends at E_t; with the wire flag crc becomes its transmitted form."""
        c = self.cfg
        if self.crc_wire:
            x = self.crc ^ (0xFFFF if c.inv else 0)
            self.crc = sum(((x >> (15 - i)) & 1) << i for i in range(c.width()))
        self.mode = IDLE
        self._end(OK, self.crc_nb, t)

    # ------------------------------------------------------------------ transmit

    def _tx_start(self, d: int) -> None:
        c, v = self.cfg, self.tx_cmd
        self.mode, self.last_job, self.cause, self.count = TX, JOB_SEND, 0, 0
        self.crc = c.crc_start()
        segs = []
        if c.prefix:
            segs.append(("pre", 8))
        if c.hdr:
            segs.append(("hdr", 8))
        if (v >> 8) & 0x7F:
            segs.append(("dat", (v >> 8) & 0x7F))
        if (v >> 15) & 1 and c.poly:
            segs.append(("crc", c.width()))
        self.tx_segs, self.tx_seg, self.tx_pos = segs, 0, 0
        self.tx_tail = (["se0"] * c.eop + ["j"]) if (c.diff and c.eop) else []
        self.tx_stf, self.tx_L, self.tx_k = Stuffer(), 1, 0
        self.tx_nxt, self.tx_alpha = d, 0
        self.mon_at, self.tx_abort, self.req = None, False, None

    def _packet_bit(self) -> Optional[tuple[int, Optional[int]]]:
        """Next packet bit and its stream index (None if not a stream bit); None at the end."""
        c, v = self.cfg, self.tx_cmd
        while self.tx_seg < len(self.tx_segs):
            kind, n = self.tx_segs[self.tx_seg]
            if self.tx_pos == n:
                self.tx_seg, self.tx_pos = self.tx_seg + 1, 0
                continue
            i = self.tx_pos
            self.tx_pos += 1
            if kind == "pre":
                return c.byte_bit(c.prefix, i), None
            if kind == "hdr":
                return c.byte_bit((v >> 16) & 0xFF, i), None
            if kind == "dat":
                return (self.buf >> c.word_pos(i)) & 1, i
            w = self.crc ^ (0xFFFF if c.inv else 0)
            return (w >> (15 - i)) & 1, None
        return None

    def _symbol(self, B: int) -> Optional[PinWrite]:
        """The symbol driven at boundary E_B, or None once the packet and its EOP are done."""
        c = self.cfg
        b: Optional[int] = None
        if self.tx_stf.due(c):
            b = self.tx_stf.stuff_bit(c)
        else:
            pb = self._packet_bit()
            if pb is not None:
                b, j = pb
                if j is not None:
                    self.crc = crc_step(self.crc, b, c.poly)
                    nb = (self.tx_cmd >> 8) & 0x7F
                    if j % 32 == 31 and j + 1 < nb:
                        self.req = Request(B + 1, ((self.tx_cmd & 0xFF) + (j + 1) // 32) & 0xFF,
                                           False)
        if b is not None:                        # a line bit
            self.tx_stf.push(b, c)
            if c.nrzi:
                if b == 0:
                    self.tx_L ^= 1
            else:
                self.tx_L = b
            if c.abort and not c.diff:
                self.mon_at, self.mon_L, self.mon_k = B + c.S, self.tx_L, self.tx_k
            self.tx_k += 1
            return self._drive(self.tx_L)
        if self.tx_tail:
            s = self.tx_tail.pop(0)
            return self._se0() if s == "se0" else self._drive(1)
        return None

    def _tx_edge(self, t: int, tape: list[int], sync: int, pins: list[PinWrite]) -> bool:
        c = self.cfg
        if t == self.tx_nxt:
            sym = None if self.tx_abort else self._symbol(t)
            if sym is None:                      # the idle state; the job ends here
                idle = self._idle()
                if idle:
                    pins.append(idle)
                if self.tx_abort:
                    self._end(MISMATCH, min(self.mon_k, 511), t)
                else:
                    self._end(OK, 0, t)
                self.mode, self.req, self.mon_at = IDLE, None, None
                return False
            pins.append(sym)
            self.tx_nxt = t + c.bit_len(self.tx_alpha)
            self.tx_alpha = (self.tx_alpha + c.P_frac) & 0xFF
        if self.mon_at is not None and t == self.mon_at:
            self.mon_at = None
            if ((sync >> c.r) & 1) != self.mon_L:
                self.tx_abort = True
        if self.req is not None and owner_cycle(t, self.owner) and t >= self.req.earliest:
            self.buf = tape[self.req.addr]
            self.req = None
            return True
        return False

    # ------------------------------------------------------------------ receive

    def _arm_edge(self, t: int, sync: int) -> None:
        c = self.cfg
        x, se0 = self._line(sync)
        k_need = max(1, c.idle_min * c.P_int)
        if x == 0 and self.idle_run >= k_need:
            self._rx_start(t, x)
            return
        self.idle_run = self.idle_run + 1 if (x == 1 and not se0) else 0

    def _rx_start(self, t0: int, x: int) -> None:
        c = self.cfg
        self.mode, self.last_job, self.cause, self.count = RX, JOB_RECV, 0, 0
        self.crc = c.crc_start()
        self.rx_t0, self.rx_alpha = t0, 0
        self.rx_nxt = t0 + c.bit_len(0)
        self.rx_sample = t0 + c.S
        self.x_prev, self.ref = x, 1
        self.rx_stf = Stuffer()
        self.hunt, self.decoded, self.storing = bool(c.sync_skip), False, True
        self.noted, self.eop_s, self.finishing, self.req = OK, None, False, None
        self.buf = 0

    def _rx_edge(self, t: int, sync: int) -> tuple[Optional[tuple[int, int]], bool]:
        c = self.cfg
        tw: Optional[tuple[int, int]] = None
        used = False
        if self.req is not None and owner_cycle(t, self.owner) and t >= self.req.earliest:
            tw, used = (self.req.addr, self.buf), True
            self.buf, self.req = 0, None
            if self.finishing:
                self.mode, self.finishing = IDLE, False
                return tw, used
        if self.finishing:
            return tw, used
        x, se0 = self._line(sync)
        if self.eop_s is not None:               # waiting for the end of SE0
            if not se0 or t == self.eop_s + 255:
                self._eop_end(t)
            self.x_prev = x
            return tw, used
        if x != self.x_prev and (not c.fall or x == 0):          # resync boundary
            self.rx_alpha = 0
            self.rx_nxt = t + c.bit_len(0)
            self.rx_sample = t + c.S
        elif t == self.rx_nxt:                                   # timed boundary
            self.rx_alpha = (self.rx_alpha + c.P_frac) & 0xFF
            self.rx_nxt = t + c.bit_len(self.rx_alpha)
            self.rx_sample = t + c.S
        self.x_prev = x
        if self.rx_sample == t:
            assert not used, "a sample never meets a buffer write (S >= 4)"
            self.rx_sample = None
            self._sample(t, x, se0)
        return tw, used

    def _sample(self, t: int, x: int, se0: bool) -> None:
        c = self.cfg
        if se0:                                  # 1. end of packet
            self.eop_s = t
            return
        if not self.storing:
            return
        if c.nrzi:                               # 2. decode
            b = 1 if x == self.ref else 0
            self.ref = x
        else:
            b = x
        self.decoded = True
        if self.rx_stf.due(c):                   # 3. destuff
            if b == self.rx_stf.stuff_bit(c):
                self.rx_stf.push(b, c)
                return
            if c.diff:
                self.noted, self.storing = STUFF, False
            else:
                self._finish(VIOL, self.count, t)
            return
        self.rx_stf.push(b, c)
        if self.hunt:                            # 4. sync pattern
            if b == 1:
                self.hunt = False
            return
        n = self.count                           # 5. store
        self.buf |= b << c.word_pos(n)
        if not c.hdr or n >= 8:
            self.crc = crc_step(self.crc, b, c.poly)
        self.count = n + 1
        if self.count % 32 == 0:
            self.req = Request(t + 1, (self.rx_addr + self.count // 32 - 1) & 0xFF, True)
        if self.count == self.rx_limit:
            if c.diff:
                self.noted, self.storing = FULL, False
            else:
                self._finish(FULL, self.count, t)

    def _eop_end(self, e: int) -> None:
        if self.hunt and not self.decoded:
            self._finish(SE0, min(e - self.rx_t0, 255), e)
        elif self.hunt:
            self._finish(NOSYNC, 0, e)
        else:
            self._finish(self.noted, self.count, e)

    def _finish(self, cause: int, count: int, e: int) -> None:
        """The job ends at E_e; a last word, if any, goes in the first owner cycle > e."""
        self._end(cause, count, e)
        self.eop_s = None
        self.rx_sample = None
        if self.req is None and cause not in (SE0, NOSYNC) and self.count % 32:
            self.req = Request(e + 1, (self.rx_addr + (self.count - 1) // 32) & 0xFF, True)
        if self.req is not None:
            self.req.earliest = max(self.req.earliest, e + 1)
            self.finishing = True
        else:
            self.mode = IDLE
