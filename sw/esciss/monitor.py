# SPDX-FileCopyrightText: © 2026 Escapement contributors
# SPDX-License-Identifier: Apache-2.0
"""v3 bus timing monitor (src/esc_mon.v): the reference model.

RULES interval rules on the synchronised pins (both banks, pins 0..12). A rule starts on an
edge of its from-pin and ends on the next edge of its to-pin; the interval is the ticks
between the two edges (the counter is 1 in the cycle after the from-edge). At the to-edge an
interval below MIN is a violation (early); MAX ticks with no to-edge is a violation (late),
so a good interval is MIN..MAX ticks. MIN = 0 and MAX = 0 disable the two checks.
Each rule counts its violations (saturating at 255); the first violation since start or a
clear is recorded: rule, kind, tick, interval. The lowest-numbered rule wins a tie.

MON rd, rs writes one rule at E_c:
  R[rd]  [31] enable, [30:29] rule, [28:25] from pin, [24:23] from edge,
         [22:19] to pin, [18:17] to edge, [16] keep (write no rule: a clear alone),
         [15:0] MIN   (edges: 1 rise, 2 fall, 3 any, 0 none)
  R[rs]  [31] clear every count and the record, [15:0] MAX
GETM rd, sel reads the state during cycle c: sel 0 the four counts (rule k in [8k+7:8k]),
1 the record {[31] valid, [30] late, [17:16] rule, [15:0] interval}, 2 its tick, else 0.

The unit evaluates at every edge E_t of a run except a stop edge; its previous-pin register
follows the pins at every edge, so after a start it holds the pins of the start cycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

RULES = 4
W = 16
WMAX = (1 << W) - 1


@dataclass
class Rule:
    en: int = 0
    frm: int = 0
    fe: int = 0
    to: int = 0
    te: int = 0
    mn: int = 0
    mx: int = 0
    armed: int = 0
    cnt: int = 0
    nv: int = 0


def _ev(rise: int, fall: int, pin: int, kind: int) -> bool:
    return bool(((kind & 1) and (rise >> pin) & 1) or ((kind & 2) and (fall >> pin) & 1))


@dataclass
class Monitor:
    rules: list = field(default_factory=lambda: [Rule() for _ in range(RULES)])
    prev: int = 0
    rec_v: int = 0
    rec_rule: int = 0
    rec_late: int = 0
    rec_tick: int = 0
    rec_ivl: int = 0
    owner: Optional[int] = None    # the first context whose MON commits in a run (like the line unit)

    def read(self, sel: int) -> int:
        """GETM: the state during the commit cycle."""
        if sel == 0:
            return sum((r.nv & 0xFF) << (8 * k) for k, r in enumerate(self.rules))
        if sel == 1:
            return ((self.rec_v << 31) | (self.rec_late << 30) | (self.rec_rule << 16)
                    | (self.rec_ivl & WMAX))
        if sel == 2:
            return self.rec_tick
        return 0

    def start(self) -> None:
        """At a start edge: rules disabled and idle, counts and the record cleared."""
        for r in self.rules:
            r.en, r.armed, r.cnt, r.nv = 0, 0, 0, 0
        self.rec_v = 0
        self.owner = None

    def edge(self, tm: int, psync: int, run: bool, mon=None) -> None:
        """Everything at E_t. psync: the pins during cycle t; run: the unit evaluates at this
        edge (a run, not its stop edge); mon: (R[rd], R[rs]) of a MON committing at E_t."""
        rise, fall = psync & ~self.prev & 0x1FFF, ~psync & self.prev & 0x1FFF
        early, late = [], []
        for r in self.rules:
            f_ev = bool(r.en) and _ev(rise, fall, r.frm, r.fe)
            t_ev = bool(r.en) and _ev(rise, fall, r.to, r.te)
            e = run and r.armed and t_ev and r.mn != 0 and r.cnt < r.mn
            l = run and r.armed and not t_ev and r.mx != 0 and r.cnt == r.mx
            early.append(e)
            late.append(l)
            r._f, r._t = f_ev, t_ev
        any_v = any(early) or any(late)
        v_rule = next((k for k in range(RULES) if early[k] or late[k]), 0)
        rec_cnt = self.rules[v_rule].cnt
        clr = mon is not None and (mon[1] >> 31) & 1
        for k, r in enumerate(self.rules):
            if run:
                if r._f:
                    r.armed, r.cnt = 1, 1            # the ticks since the from-edge, next cycle
                elif r.armed and (r._t or late[k]):
                    r.armed = 0
                elif r.armed and r.cnt != WMAX:
                    r.cnt += 1
                if (early[k] or late[k]) and r.nv != 0xFF:
                    r.nv += 1
            if clr:
                r.nv = 0
            del r._f, r._t
        if mon is not None and not (mon[0] >> 16) & 1:
            a, b = mon
            r = self.rules[(a >> 29) & 3]
            r.en, r.frm, r.fe = (a >> 31) & 1, (a >> 25) & 0xF, (a >> 23) & 3
            r.to, r.te, r.mn = (a >> 19) & 0xF, (a >> 17) & 3, a & WMAX
            r.mx, r.armed, r.cnt = b & WMAX, 0, 0
        if clr:
            self.rec_v = 0
        elif any_v and not self.rec_v:
            self.rec_v, self.rec_rule, self.rec_late = 1, v_rule, int(late[v_rule])
            self.rec_tick, self.rec_ivl = tm & 0xFFFFFFFF, rec_cnt
        self.prev = psync & 0x1FFF
