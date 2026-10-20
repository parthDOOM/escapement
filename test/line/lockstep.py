#!/usr/bin/env python3
"""Lockstep vectors for src/esc_line.v: random stimulus on the ISS line unit
(sw/esciss/line.py), written as one line per edge for tb_line.v to replay (run.py).

    python lockstep.py SEED EDGES OUTDIR

Writes OUTDIR/tape.hex (initial tape) and OUTDIR/vec.txt, one line per edge t:

  inputs   now view owns start kill stop op opctx opv lret halt ewe eaddr edata
  expected owned owner conf claimed engaged busy status e mode flto fltc
           port pwe paddr pwdata w0 w1 w2 rel drv dval doe

all hex. op: 0 none, 1 SEND, 2 OFF, 3 RECV, 4 CRC, 5 CONFIG. The stimulus follows the
core's rules: a context commits only in its slot, LINE and SEND only when they would
COMPLETE, a CONFIG fault becomes an owner fault at that edge, retirement at the deadline,
tape writes from elsewhere only in cycles the unit leaves free.
"""
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sw"))
from esciss.line import (LineUnit, Cfg, crc_step, IDLE, CFG, PEND, TX, ARM, RX, CRC,  # noqa: E402
                         OP_OFF, OP_RECV, OP_CRC, OP_CONFIG)

M32 = 0xFFFFFFFF


def rand_cfg(rng):
    """(w0, w1, w2) from a profile."""
    prof = rng.choice(["usb", "usb", "can", "can", "rand", "rand", "rand", "bad"])
    P = rng.choice([8, 8, 9, 10, 12, 16, 16, 20, 33]) if rng.random() < 0.9 else rng.randint(8, 70)
    F = rng.choice([0, 0, rng.randint(0, 255), 255, 128])
    S = rng.randint(4, P - 1)
    if prof == "usb":
        d, n = rng.sample(range(8), 2)
        w0 = (d | n << 3 | 1 << 9 | 1 << 10 | 1 << 11 | 6 << 12 | rng.randint(0, 3) << 16
              | rng.randint(0, 1) << 18 | rng.randint(0, 1) << 19 | 1 << 20
              | rng.choice([0x80, 0x80, 0, rng.randint(0, 255)]) << 24)
        w2 = (rng.choice([0x8005, 0x2800, 0x8005]) | 1 << 16 | 1 << 17 | rng.randint(0, 1) << 18
              | rng.randint(0, 1) << 19 | rng.randint(0, 1) << 21)
    elif prof == "can":
        d, r = rng.sample(range(8), 2)
        w0 = (d | r << 6 | rng.randint(0, 1) << 11 | 5 << 12 | 1 << 15 | 1 << 18
              | rng.randint(0, 1) << 19 | 1 << 20)
        w2 = (0x8B32 | 1 << 19 | 1 << 20 | rng.randint(0, 11) << 21 | rng.randint(0, 1) << 18)
    else:
        w0 = rng.getrandbits(32)
        w2 = rng.getrandbits(32)
        if rng.random() < 0.3:
            w2 &= ~0xFFFF                      # poly 0
        if rng.random() < 0.5:
            w2 &= ~(0xF << 21)
    w1 = P | F << 12 | S << 20
    if prof == "bad":
        w1 = rng.choice([rng.randint(0, 7) | 5 << 20, 10 | 10 << 20, 10 | 3 << 20,
                         10 | 11 << 20 | rng.getrandbits(8) << 12])
    return w0, w1, w2


class Line:
    """The owner's logical input view, tick by tick: idle, noise, encoded packets, or the
    unit's own pins read back."""

    def __init__(self, rng):
        self.rng = rng
        self.segs = []          # [(ticks, view)]
        self.left = 0
        self.cur = 0xFF
        self.loop = False
        self.flip = 0.0

    def _emit(self, ticks, v):
        if ticks > 0:
            self.segs.append((ticks, v & 0xFF))

    def plan(self, cfg: Cfg, configured: bool):
        rng = self.rng
        self.loop = False
        self.flip = rng.choice([0.0, 0.0, 0.0, 0.003, 0.03])
        k = rng.random()
        base = 0xFF
        if not configured or k < 0.12:
            for _ in range(rng.randint(1, 12)):
                self._emit(rng.randint(1, 3 * max(8, cfg.P_int)), rng.getrandbits(8) | (base if rng.random() < 0.3 else 0))
            return
        if k < 0.25:
            self.loop = True
            self._emit(rng.randint(50, 2000), base)
            return
        if k < 0.32:
            self._emit(rng.randint(1, 300), base)
            return
        self._packet(cfg)

    def _level(self, cfg, L, se0=False):
        v = 0xFF
        if cfg.diff:
            v &= ~(1 << cfg.d) & ~(1 << cfg.n)
            if not se0:
                v |= (L << cfg.d) | ((L ^ 1) << cfg.n)
        else:
            v &= ~(1 << cfg.r)
            v |= L << cfg.r
        if self.rng.random() < 0.1:
            others = ~((1 << cfg.d) | (1 << cfg.n) | (1 << cfg.r)) & 0xFF
            v = (v & ~others) | (self.rng.getrandbits(8) & others)
        return v

    def _packet(self, cfg: Cfg):
        rng = self.rng
        P = cfg.P_int + cfg.P_frac / 256
        P *= 1 + rng.choice([0, 0, rng.uniform(-0.05, 0.05)])
        jit = rng.choice([0, 0, 1])
        self._emit(rng.randint(1, max(2, cfg.idle_min * cfg.P_int + rng.randint(-3, 20))), self._level(cfg, 1))
        bits = []
        if cfg.sync_skip:
            bits += [0] * rng.randint(0, 9) + [1]
        nb = rng.choice([0, 1, 7, 8, 16, 31, 32, 33, 64, 72, rng.randint(0, 300)])
        data = [rng.getrandbits(1) for _ in range(nb)]
        if rng.random() < 0.5 and cfg.poly:
            crc = cfg.crc_start()
            start = 8 if cfg.hdr else 0
            for b in data[start:]:
                crc = crc_step(crc, b, cfg.poly)
            w = cfg.width()
            x = crc ^ (0xFFFF if cfg.inv else 0)
            data += [(x >> (15 - i)) & 1 for i in range(w)]
        bits += data
        # stuffing
        line, run, rb = [], 0, 0
        for b in bits:
            line.append(b)
            if cfg.any:
                run, rb = (run + 1, rb) if (run and b == rb) else (1, b)
            else:
                run = run + 1 if b else 0
            if cfg.N and run >= cfg.N and rng.random() > 0.01:
                sb = (rb ^ 1) if cfg.any else 0
                line.append(sb)
                if cfg.any:
                    run, rb = 1, sb
                else:
                    run = 0
        if not cfg.diff and rng.random() < 0.6:
            line += [1] * rng.randint(1, 12)       # CAN-style end by violation
        if rng.random() < 0.03:
            line = line[:rng.randint(0, len(line))]
        L = 1
        t = 0.0
        for b in line:
            if cfg.nrzi:
                if b == 0:
                    L ^= 1
            else:
                L = b
            t2 = t + P
            n = int(round(t2)) - int(round(t)) + (rng.randint(-jit, jit) if jit else 0)
            self._emit(max(1, n), self._level(cfg, L))
            if rng.random() < 0.004:
                self._emit(1, self._level(cfg, L ^ 1))
            t = t2
        if cfg.diff:
            self._emit(rng.choice([1, 2, int(2 * P), int(2 * P), rng.randint(1, 400)]), self._level(cfg, 0, se0=True))
            self._emit(rng.randint(1, int(3 * P)), self._level(cfg, 1))
        else:
            self._emit(rng.randint(1, 40), self._level(cfg, 1))

    def tick(self, cfg, configured, pins_val, pins_oe, claimed, tx=False):
        if self.left == 0:
            if not self.segs:
                self.plan(cfg, configured)
            if self.segs:
                self.left, self.cur = self.segs.pop(0)
            else:
                self.left, self.cur = 1, 0xFF
        self.left -= 1
        v = self.cur
        if self.loop or tx:
            lv = (pins_val & pins_oe) | (~pins_oe & 0xFF)
            v = (v & ~claimed) | (lv & claimed)
            if cfg.r is not None and not cfg.diff:
                r = cfg.r
                src = (lv >> cfg.d) & 1
                if self.rng.random() < self.flip:
                    src ^= 1
                v = (v & ~(1 << r)) | (src << r)
        return v & 0xFF


def scheduled_addr(lu: LineUnit, t: int):
    if lu.mode == CFG:
        return (lu.cfg_addr + len(lu.cfg_words)) & 0xFF, False
    if lu.mode == CRC:
        return (lu.crc_addr + (t - lu.crc_c) // 32) & 0xFF, False
    return lu.req.addr, lu.req.write


def main():
    seed, edges, out = int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    tape = [rng.getrandbits(32) for _ in range(256)]
    lu = LineUnit()
    line = Line(rng)
    owns = 0xFF if rng.random() < 0.7 else rng.getrandbits(8)
    pending = []                 # deadlines of the owner's queued LINE entries
    pval = poe = 0               # the owner's logical pin state, as the unit leaves it
    now = 0
    stats = {}

    def st(k):
        stats[k] = stats.get(k, 0) + 1

    # config blocks in the tape: refreshed now and then
    def new_cfg_block():
        a = rng.randint(0, 255)
        w = rand_cfg(rng)
        for i in range(3):
            tape[(a + i) & 0xFF] = w[i]
        return a

    cfg_blocks = [new_cfg_block() for _ in range(6)]
    (out / "tape.hex").write_text("".join(f"{w:08x}\n" for w in tape))
    rate = rng.choice([0.05, 0.2, 0.6])
    lines = []
    for t in range(edges):
        tm = now
        start = kill = stop = 0
        op = opctx = opv = 0
        lret = halt = 0
        ewe = eaddr = edata = 0

        # ---- expected state during cycle t
        acc = lu.access_scheduled(tm)
        flt = lu.config_fault(tm, lambda j: (owns >> j) & 1) if lu.owner is not None else None
        paddr, pwe = scheduled_addr(lu, tm) if acc else (0, False)
        pwdata = lu.buf if (acc and pwe) else 0
        exp_state = (int(lu.owner is not None), lu.owner or 0, int(lu.configured), lu.claimed,
                     int(lu.engaged()), int(lu.busy()), lu.status(), lu.e, lu.mode,
                     int(flt == "OWNER"), int(flt == "CONFIG"), int(acc), int(pwe), paddr, pwdata,
                     lu.cfg.w0, lu.cfg.w1, lu.cfg.w2)

        # ---- inputs
        r = rng.random()
        if r < 0.00002:
            start = 1
        elif r < 0.00006:
            stop = kill = 1
        elif lu.owner is not None and r < 0.00010:
            kill = 1
        if flt is not None:
            kill = 1
        commit_k = (tm - 3) % 4
        if not (start or kill):
            mode = lu.mode
            engaged = lu.engaged()
            mine = lu.owner == commit_k
            # an owner slot with the unit free: act now and then, as firmware does; an armed
            # receiver is mostly left to find its packet
            act = rng.random() < (rate * 0.02 if mode == ARM else rate)
            if act and not engaged and (lu.owner is None or mine) and (
                    not lu.configured or rng.random() < 0.04):
                op = 5
                opv = (OP_CONFIG << 28) | rng.getrandbits(20) << 8 | rng.choice(cfg_blocks)
            elif act and mine and rng.random() < 0.03:
                op = 2
                opv = (OP_OFF << 28) | rng.getrandbits(24)
            elif act and mine and not engaged and rng.random() < 0.02:
                halt = 1
            elif act and mine and lu.configured and not engaged:
                k = rng.choices(["recv", "send", "crc"], weights=[5, 4, 1])[0]
                if k == "recv":
                    op = 3
                    opv = (OP_RECV << 28) | rng.getrandbits(13) | rng.getrandbits(15) << 13
                    if rng.random() < 0.5:
                        opv = (opv & ~0x1F00) | rng.choice([1, 2, 4, 8, 9, 31, 0]) << 8
                elif k == "crc":
                    op = 4
                    nb = rng.choice([0, 1, 5, 16, 31, 32, 33, 64, 255, rng.randint(0, 255)])
                    opv = (OP_CRC << 28) | rng.getrandbits(8) | nb << 8 | rng.getrandbits(12) << 16
                elif lu.cfg.tx_pins:
                    dmin = max([tm + 1] + [p + 1 for p in pending])
                    d = dmin + rng.choice([0, 0, 1, 3, rng.randint(0, 60), rng.randint(0, 400)])
                    if len(pending) < 4:
                        op = 1
                        nb = rng.choice([0, 0, 1, 8, 16, 31, 32, 33, 63, 64, 65, 127,
                                         rng.randint(0, 127)])
                        opv = (rng.getrandbits(8) | nb << 8 | rng.getrandbits(1) << 15
                               | rng.getrandbits(8) << 16)
                        pending.append(d)
            opctx = commit_k if op else 0
        if pending and pending[0] == tm and not kill and not start:
            lret = 1
        if pending and pending[0] == tm:
            pending.pop(0)
        if kill or start:
            if start or stop or lu.owner is not None:
                pending = []

        view = (line.tick(lu.cfg, lu.configured, pval, poe, lu.claimed, lu.mode in (PEND, TX))
                if lu.owner is not None else rng.getrandbits(8))

        # ---- step the ISS
        tw = None
        pins = []
        if start:
            lu = LineUnit()
            pval = poe = 0
        elif stop:
            lu._abort()
            lu._unconfigure()
            poe = 0
        else:
            opt = None
            if op == 1:
                opt = ("SEND", opv, commit_k)
            elif op:
                opt = ("LINE", opv, commit_k)
            pins, tw, _ = lu.step(tm, tape, view, owner_faulted=bool(kill), op=opt,
                                  line_retired=bool(lret), halt_completed=bool(halt))
            if kill:
                poe = 0
        rel = drv = dval = doe = 0
        for w in pins:
            if w.val is None:
                rel |= w.mask
                poe &= ~w.mask
            else:
                drv |= w.mask
                dval |= w.val & w.mask
                doe = 1 if w.oe else 0
                pval = (pval & ~w.mask) | (w.val & w.mask)
                poe = (poe & ~w.mask) | (w.oe & w.mask)
        if tw is not None:
            if stop:
                tw = None
            else:
                a, w = tw
                tape[a] = w
                st("rx_write")
        # tape writes from elsewhere, in cycles the unit leaves free
        commit_read = op in (1, 4, 5)
        if not acc and not commit_read and rng.random() < 0.03:
            ea = rng.getrandbits(8)
            if all((ea - b) & 0xFF > 2 for b in cfg_blocks):      # keep the config blocks
                ewe, eaddr, edata = 1, ea, rng.getrandbits(32)
                tape[eaddr] = edata
        if not start:
            st(f"mode{exp_state[8]}")
            if lu.mode == IDLE and exp_state[8] in (TX, RX, CRC, CFG):
                st(f"end{exp_state[8]}_cause{lu.cause}")
        lines.append(" ".join(f"{x:x}" for x in (
            tm & M32, view, owns, start, kill, stop, op, opctx, opv & 0xFFFFFF, lret, halt,
            ewe, eaddr, edata) + exp_state + (rel, drv, dval, doe)))
        now = 0 if start else now + 1
    (out / "vec.txt").write_text("\n".join(lines) + "\n")
    print(" ".join(f"{k}={v}" for k, v in sorted(stats.items())))


if __name__ == "__main__":
    main()
