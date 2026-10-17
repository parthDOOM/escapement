"""ISS tests. Every expected value is derived by hand from docs/11-SEMANTICS.md (v1.0.1);
the derivation is written next to it. Nothing here was produced by running the ISS.

Timing facts used throughout (§3): context k fetches in cycles t = k (mod 4) and commits
at E_(t+3). With no waits, context 0's i-th instruction (i = 0, 1, ...) commits at tick
3 + 4i; context 1 at 4 + 4i; context 2 at 5 + 4i; context 3 at 6 + 4i.
Pins (§5, §6.3): a DRIVE with deadline d retires at E_d, so outputs change in cycle d+1.
Inputs (§5): sync during cycle t = uio_in during cycle t-2.
"""

from pathlib import Path

import pytest

from escasm import assemble, assemble_file
from esciss import Machine, ahead, reached, shift_in

ROOT = Path(__file__).resolve().parents[2]

ILLEGAL, PC_RANGE, ADMIT, LATE_HEAD = 1, 2, 3, 4          # isa.yaml fault_causes
MASK, OWNER, PAST, ORDER, OVERLAP = 1, 2, 3, 4, 5         # isa.yaml admit_subcodes
IDLE, RUNNING, HALTED, FAULTED = 0, 1, 2, 3                # §10 sel 7


def loopback(t, out, oe):           # pure loopback: uio_in = uio_out
    return out


def const(v):
    return lambda t, out, oe: v


def run(src, ticks=500, board=None, idle=True):
    m = Machine(assemble(src), board)
    m.run(ticks, stop_when_idle=idle)
    return m


def at(m, t):
    hits = [c for c in m.commits if c["t"] == t]
    assert len(hits) == 1, f"expected one commit at tick {t}, got {hits}"
    return hits[0]


def of_ctx(m, k):
    return [c for c in m.commits if c["ctx"] == k]


def pin(m, t):
    rec = m.pins[t]
    assert rec["t"] == t
    return rec["uio_out"], rec["uio_oe"]


def ff(cause, sub, ctx, pc):
    """§10 sel 14 encoding of a valid first-fault record."""
    return (1 << 31) | (cause << 24) | (sub << 20) | (ctx << 16) | pc


def state(m, k):
    return (m.dbg_read(k, 7) >> 2) & 3


# --------------------------------------------------------------------------- notation


def test_ahead_reached_half_range():
    # ahead(a,b): 1 <= (a-b) mod 2^32 < 2^31 ; reached(t,d): (t-d) mod 2^32 < 2^31
    assert not ahead(7, 7)                  # diff 0
    assert ahead(8, 7)                      # diff 1
    assert ahead(7 + 2**31 - 1, 7)          # diff 2^31-1
    assert not ahead(7 + 2**31, 7)          # diff 2^31
    assert ahead(3, 0xFFFFFFFE)             # wraps: diff 5
    assert reached(7, 7) and reached(8, 7) and not reached(6, 7)
    assert reached(0, 0xFFFFFFFF)           # diff 1 across the wrap
    assert not reached(7, 8 + 2**31 - 1) and reached(7, 7 + 2**31 + 1)   # diffs 2^31+... / 2^31-1
    # §6.3 shift_in
    assert shift_in(0, 1, 0) == 0x80000000 and shift_in(0, 1, 1) == 1
    assert shift_in(0x80000001, 0, 1) == 2 and shift_in(3, 0, 0) == 1


# --------------------------------------------------------------------------- §11 example


SEC11 = """
.entry 0, c0
.entry 1, c1
.entry 2, c2
.own 0, 0
c0: ADDT 10
    EMIT 0x01, 0x01, 0x01
    HALT
.org 16
c1: GETP r0
    GETP r1
    GETP r2
    HALT
.org 32
c2: GETP r0
    GETP r1
    GETP r2
    HALT
"""


def test_section11_worked_example():
    m = run(SEC11, board=loopback)
    # Fetch PC 0 in cycle 0 -> commit at E_3: tmr = 10.
    c = at(m, 3)
    assert (c["ctx"], c["pc"], c["outcome"], c["post"]["tmr"]) == (0, 0, "C", 10)
    # Fetch PC 1 in cycle 4 -> commit at E_7, d = 10, admitted.
    c = at(m, 7)
    assert (c["ctx"], c["pc"], c["insn"], c["outcome"]) == (0, 1, 0x28010101, "C")
    # DRIVE retires at E_10 -> uio_out[0] = uio_oe[0] = 1 from cycle 11; 0 before.
    for t in range(0, 11):
        assert pin(m, t) == (0, 0)
    for t in range(11, len(m.pins)):
        assert pin(m, t) == (1, 1)
    # min_lead = d - c = 10 - 7 = 3 (sel 9 [31:16]); max_occupancy = |Q|+1 = 1.
    assert m.dbg_read(0, 9) >> 16 == 3
    assert (m.dbg_read(0, 10) >> 8) & 7 == 1
    # Loopback: uio_in(11) = 1 -> sync(13) = 1; sync(12) = uio_in(10) = 0.
    # ctx1 commits at 4, 8, 12 -> its 3rd GETP (r2) sees sync(12) = 0.
    assert at(m, 12)["ctx"] == 1 and at(m, 12)["post"]["r2"] == 0
    # ctx2 commits at 5, 9, 13 -> r1 = sync(9) = 0, r2 = sync(13) = 1.
    assert at(m, 13)["ctx"] == 2
    assert at(m, 13)["post"]["r1"] == 0 and at(m, 13)["post"]["r2"] == 1
    # HALT (PC 2) fetched in cycle 8, commits at 11; FIFO empty after E_10 -> COMPLETE.
    assert at(m, 11)["outcome"] == "C" and state(m, 0) == HALTED
    # ctx1 HALT at 16, ctx2 HALT at 17; all idle during cycle 18 -> last pin record 18.
    assert at(m, 16)["outcome"] == "C" and at(m, 17)["outcome"] == "C"
    assert m.pins[-1]["t"] == 18
    assert of_ctx(m, 3) == []               # ctx3 not enabled


# --------------------------------------------------------------------------- §3 slots


def test_slot_timing_all_contexts():
    src = """
    .entry 0, p0
    .entry 1, p1
    .entry 2, p2
    .entry 3, p3
    p0: NOP
        NOP
        HALT
    p1: NOP
        NOP
        HALT
    p2: NOP
        NOP
        HALT
    p3: NOP
        NOP
        HALT
    """
    m = run(src)
    for k in range(4):
        cs = of_ctx(m, k)
        # ctx k fetches at k, k+4, k+8 -> commits at k+3, k+7, k+11 (= k+3 mod 4)
        assert [c["t"] for c in cs] == [k + 3, k + 7, k + 11]
        assert [c["pc"] for c in cs] == [3 * k, 3 * k + 1, 3 * k + 2]
        assert all(c["outcome"] == "C" for c in cs)
    # globally: one commit per tick 3..14, by ctx (t-3) mod 4
    assert [(c["t"], c["ctx"]) for c in m.commits] == [(t, (t - 3) % 4) for t in range(3, 15)]


def test_only_ctx2_enabled():
    m = run(".entry 2, s\ns: NOP\nNOP\nHALT\n")
    # ctx2 first fetch in cycle 2 -> commits at 5, 9, 13
    assert [c["t"] for c in m.commits] == [5, 9, 13]
    assert {c["ctx"] for c in m.commits} == {2}
    assert state(m, 0) == IDLE and state(m, 2) == HALTED


def test_wait_refetch_timing():
    m = run(".entry 1, s\ns: ADDT 20\nWAITT\nHALT\n")
    cs = of_ctx(m, 1)
    # ADDT at 4 (tmr=20); WAITT at 8,12,16: reached(c,20) false -> W; at 20 reached -> C;
    # each W leaves PC = 1 so the same word is refetched 4 cycles later. HALT at 24.
    assert [(c["t"], c["pc"], c["outcome"]) for c in cs] == [
        (4, 0, "C"), (8, 1, "W"), (12, 1, "W"), (16, 1, "W"), (20, 1, "C"), (24, 2, "C")]
    assert [c["post"]["pc"] for c in cs] == [1, 1, 1, 1, 2, 2]


# --------------------------------------------------------------------------- §7.3 WAITT/WAITP


@pytest.mark.parametrize("n,expected", [
    (0, [(7, "C")]),                        # tmr 0 < c=7: reached
    (2, [(7, "C")]),                        # tmr < c
    (7, [(7, "C")]),                        # tmr == c: reached(7,7)
    (8, [(7, "W"), (11, "C")]),             # tmr == c+1: not reached at 7; reached at 11
    (20, [(7, "W"), (11, "W"), (15, "W"), (19, "W"), (23, "C")]),
])
def test_waitt_edges(n, expected):
    m = run(f".entry 0, s\ns: ADDT {n}\nWAITT\nHALT\n")
    got = [(c["t"], c["outcome"]) for c in m.commits if c["pc"] == 1]
    assert got == expected


def test_waitp_edges():
    src = """
    .entry 0, s
    s: WAITP 0x0F, 0x06     ; c3: sync&0xF = 5 != 6; tmr 0 reached -> C, Z=0
       WAITP 0x0F, 0x05     ; c7: condition holds -> C, Z=1
       ADDT 31              ; c11: tmr = 31
       WAITP 0x01, 0x00     ; c15..27: false, not reached -> W; c31: tmr == c -> C, Z=0
       WAITP 0x0F, 0x05     ; c35: condition AND timeout both true -> condition wins, Z=1
       WAITP 0x01, 0x03     ; c39: value bit outside mask can never match -> timeout, Z=0
       HALT
    """
    # constant board 0x05: uio_in(t) = 5 for all t >= -1, so sync(t) = 5 for t >= 1.
    m = run(src, board=const(0x05))
    assert (at(m, 3)["outcome"], at(m, 3)["post"]["z"]) == ("C", 0)
    assert (at(m, 7)["outcome"], at(m, 7)["post"]["z"]) == ("C", 1)
    for t in (15, 19, 23, 27):
        assert at(m, t)["outcome"] == "W" and at(m, t)["post"]["z"] == 1   # WAIT: no effect
    assert (at(m, 31)["outcome"], at(m, 31)["post"]["z"], at(m, 31)["post"]["pc"]) == ("C", 0, 4)
    assert (at(m, 35)["outcome"], at(m, 35)["post"]["z"]) == ("C", 1)
    assert (at(m, 39)["outcome"], at(m, 39)["post"]["z"]) == ("C", 0)


def test_getp_default_board_and_rdtime():
    # default pull-up loopback, no pin driven -> uio_in = 0xFF -> GETP = 0xFF (Z=0)
    m = run(".entry 0, s\ns: GETP r0\nRDTIME r1\nHALT\n")
    assert at(m, 3)["post"]["r0"] == 0xFF and at(m, 3)["post"]["z"] == 0
    assert at(m, 7)["post"]["r1"] == 7          # RDTIME = commit tick, no flag change


# --------------------------------------------------------------------------- §6.2 admission


def admit_fault(src, tick, sub, pc):
    m = run(src)
    c = at(m, tick)
    assert c["outcome"] == "F"
    assert m.dbg_read(0, 14) == ff(ADMIT, sub, 0, pc)
    assert m.dbg_read(0, 15) == tick
    assert state(m, 0) == FAULTED
    return m


def test_admit_mask_drive_and_expect():
    admit_fault(".entry 0, s\n.own 0, 0\ns: ADDT 10\nEMIT 0x00, 0x00, 0x00\n", 7, MASK, 1)
    admit_fault(".entry 0, s\ns: ADDT 10\nEXPECT 0x00, 0x00, HOLD, 0\n", 7, MASK, 1)
    # step 2 precedes step 4: tmr = 0 (past) at c=3 still reports MASK
    admit_fault(".entry 0, s\ns: EMIT 0x00, 0x00, 0x00\n", 3, MASK, 0)


def test_admit_owner():
    # pin 1 not owned at all
    admit_fault(".entry 0, s\n.own 0, 0\ns: ADDT 10\nEMIT 0x03, 0x03, 0x03\n", 7, OWNER, 1)
    # pin 1 owned by ctx 1, not ctx 0
    admit_fault(".entry 0, s\n.own 0, 0\n.own 1, 1\ns: ADDT 10\nEMIT 0x02, 0, 0x02\n", 7, OWNER, 1)
    # step 3 precedes step 4 (tmr 0 is past at c=3)
    admit_fault(".entry 0, s\ns: EMIT 0x02, 0, 0x02\n", 3, OWNER, 0)


def test_expect_and_sample_need_no_ownership():
    # EXPECT d 20 at c7, SAMPLE d 21 at c15 (both ahead of c); pin 7 owned by nobody.
    m = run(".entry 0, s\ns: ADDT 20\nEXPECT 0x80, 0x00, ARRIVE, 0\nADDT 1\nSAMPLE 7, LSB\nHALT\n")
    assert at(m, 7)["outcome"] == "C" and at(m, 15)["outcome"] == "C"
    assert m.dbg_read(0, 14) == 0           # no fault


def test_admit_past():
    # d == c: ADDT 7, EMIT commits at 7 -> ahead(7,7) false
    admit_fault(".entry 0, s\n.own 0, 0\ns: ADDT 7\nEMIT 1, 1, 1\n", 7, PAST, 1)
    # d < c
    admit_fault(".entry 0, s\n.own 0, 0\ns: EMIT 1, 1, 1\n", 3, PAST, 0)
    # d == c + 2^31: EMIT at PC 3 commits at 15; tmr = 0x8000000F = 15 + 2^31
    admit_fault(".entry 0, s\n.own 0, 0\ns: MOVI r0, 0x000F\nMOVHI r0, 0x8000\nSETT r0\n"
                "EMIT 1, 1, 1\n", 15, PAST, 3)
    # SAMPLE is subject to PAST as well
    admit_fault(".entry 0, s\ns: ADDT 3\nSAMPLE 0, LSB\n", 7, PAST, 1)


def test_admit_past_boundaries_admitted():
    # d == c + 2^31 - 1 = 0x8000000E at c=15: admitted. lead = 0x7FFFFFFF, clamped to
    # 0xFFFF by min(lead, 0xFFFF), so min_lead stays 0xFFFF; max_occupancy = 1.
    # (HALT then WAITs: the entry is due ~2^31 ticks later.)
    m = run(".entry 0, s\n.own 0, 0\ns: MOVI r0, 0x000E\nMOVHI r0, 0x8000\nSETT r0\n"
            "EMIT 1, 1, 1\nHALT\n", ticks=40, idle=False)
    assert at(m, 15)["outcome"] == "C"
    assert m.dbg_read(0, 9) >> 16 == 0xFFFF
    assert (m.dbg_read(0, 10) >> 8) & 7 == 1 and (m.dbg_read(0, 10) >> 4) & 7 == 1
    # d == c + 1: ADDT 8, EMIT at c=7 -> lead 1
    m = run(".entry 0, s\n.own 0, 0\ns: ADDT 8\nEMIT 1, 1, 1\nHALT\n")
    assert at(m, 7)["outcome"] == "C" and m.dbg_read(0, 9) >> 16 == 1


def test_admit_order():
    # equal to tail: both EMITs use tmr = 20
    admit_fault(".entry 0, s\n.own 0, 0\ns: ADDT 20\nEMIT 1, 1, 1\nEMIT 1, 0, 1\n", 11, ORDER, 2)
    # earlier than tail: tail 40, new d = 39 at c=19 (39 is ahead of 19, so not PAST)
    admit_fault(".entry 0, s\n.own 0, 0\ns: ADDT 40\nEMIT 1, 1, 1\nMOVI r0, 39\nSETT r0\n"
                "EMIT 1, 0, 1\n", 19, ORDER, 4)


def test_admit_overlap():
    # first EXPECT d=20 w=5 -> last_expect_end = 25. Second d=25: ORDER passes
    # (ahead(25,20)), OVERLAP fails (ahead(25,25) false).
    admit_fault(".entry 0, s\ns: ADDT 20\nEXPECT 0x01, 0x00, HOLD, 5\nADDT 5\n"
                "EXPECT 0x01, 0x00, HOLD, 0\n", 15, OVERLAP, 3)
    # d = 26 is admitted
    m = run(".entry 0, s\ns: ADDT 20\nEXPECT 0x01, 0x00, HOLD, 5\nADDT 6\n"
            "EXPECT 0x01, 0x00, HOLD, 0\nHALT\n")
    assert at(m, 15)["outcome"] == "C" and m.dbg_read(0, 14) == 0
    # OVERLAP uses last_expect_end even after the first EXPECT has retired and its checker
    # has stopped: d=8, w=100 -> end 108; second EXPECT at c=19 with d=58 (FIFO empty).
    admit_fault(".entry 0, s\ns: ADDT 8\nEXPECT 0x01, 0x00, HOLD, 100\nADDT 50\nNOP\n"
                "EXPECT 0x01, 0x00, HOLD, 0\n", 19, OVERLAP, 4)


FULL = """
.entry 0, s
.own 0, 0
s: ADDT 100         ; c3   tmr 100
   EMIT 1, 1, 1     ; c7   d 100, lead 93
   ADDT 1           ; c11
   EMIT 1, 0, 1     ; c15  d 101, lead 86
   ADDT 1           ; c19
   EMIT 1, 1, 1     ; c23  d 102, lead 79
   ADDT 1           ; c27
   EMIT 1, 0, 1     ; c31  d 103, lead 72, occupancy 4
   ADDT 1           ; c35  tmr 104
   {last}           ; c39..
   HALT
"""


def test_fifo_full_wait_and_wait_full():
    m = run(FULL.format(last="EMIT 1, 1, 1"))
    # c39..c99: FIFO during c holds 4 entries, none due (first deadline 100) -> W.
    # (99-39)/4 + 1 = 16 waits. At c103: 100,101,102 retired at E_100..E_102, 103 retires
    # at E_103 -> |Q| = 0 -> admitted (d=104, lead 1).
    ws = [c for c in m.commits if c["pc"] == 9]
    assert [c["t"] for c in ws if c["outcome"] == "W"] == list(range(39, 100, 4))
    assert all(c["post"]["pc"] == 9 for c in ws if c["outcome"] == "W")
    assert at(m, 103)["outcome"] == "C"
    # sel 9: min_lead 1, wait_full 16
    assert m.dbg_read(0, 9) == (1 << 16) | 16
    assert (m.dbg_read(0, 10) >> 8) & 7 == 4
    # HALT at c107: 104 retired at E_104 -> empty -> C
    assert at(m, 107)["outcome"] == "C"


def test_full_fifo_wait_precedes_mask():
    # step 1 before step 2: EMIT with mask 0 on a full FIFO WAITs (and counts), then MASK.
    m = run(FULL.format(last="EMIT 0, 0, 0"))
    assert m.dbg_read(0, 9) & 0xFFFF == 16
    assert at(m, 103)["outcome"] == "F"
    assert m.dbg_read(0, 14) == ff(ADMIT, MASK, 0, 9)


def test_retirement_then_admission_same_edge():
    src = """
    .entry 0, s
    .own 0, 0
    s: ADDT 39       ; c3
       EMIT 1, 1, 1  ; c7   d 39
       ADDT 20       ; c11
       EMIT 1, 0, 1  ; c15  d 59
       ADDT 1        ; c19
       EMIT 1, 1, 1  ; c23  d 60
       ADDT 1        ; c27
       EMIT 1, 0, 1  ; c31  d 61  (FIFO full)
       ADDT 1        ; c35  tmr 62
       EMIT 1, 1, 1  ; c39: FIFO full during 39, but head 39 retires at E_39 -> |Q| = 3
       HALT
    """
    m = Machine(assemble(src))
    m.run(40, stop_when_idle=False)             # cycles 0..39; state is "during 40"
    assert at(m, 39)["outcome"] == "C"
    assert m.dbg_read(0, 9) & 0xFFFF == 0       # no wait_full
    # sel 10: max_occupancy = |Q|+1 = 4 in [10:8], FIFO count 4 in [6:4]
    assert m.dbg_read(0, 10) == (4 << 8) | (4 << 4)
    assert m.outputs() == (1, 1)                # the d=39 DRIVE did retire at E_39


# --------------------------------------------------------------------------- §7.1 flags


FLAGS = """
.entry 0, s
s:  MOVI r0, 0xFFFF      ; c3
    MOVHI r0, 0xFFFF     ; c7   r0 = 0xFFFFFFFF
    MOVI r1, 1           ; c11
    ADD r0, r1           ; c15  r0 = 0, Z1 C1 (carry out of bit 31)
    ADD r1, r1           ; c19  r1 = 2, Z0 C0
    MOVI r2, 1           ; c23
    SUB r2, r1           ; c27  1 - 2 = 0xFFFFFFFF, Z0 C1 (borrow)
    SUB r1, r1           ; c31  0, Z1 C0
    ADDI r2, 0xFFFF      ; c35  0xFFFFFFFF + 0xFFFF = 0x1_0000FFFE -> 0xFFFE, Z0 C1
    CMPI r2, 0xFFFF      ; c39  0xFFFE < 0xFFFF: C1 Z0, r2 unchanged
    CMPI r2, 0xFFFE      ; c43  Z1 C0
    MOVI r3, 5           ; c47
    CMP r2, r3           ; c51  0xFFFE >= 5: C0 Z0
    CMP r3, r2           ; c55  5 < 0xFFFE: C1 Z0
    MOVI r0, 0x00F0      ; c59
    AND r0, r3           ; c63  0xF0 & 5 = 0: Z1, C stays 1
    OR r0, r3            ; c67  5: Z0, C1
    XOR r0, r3           ; c71  0: Z1, C1
    TSTI r3, 0x0002      ; c75  5 & 2 = 0: Z1
    TSTI r3, 0x0004      ; c79  5 & 4 = 4: Z0
    MOV r1, r3           ; c83  r1 = 5, Z0
    MOV r1, r0           ; c87  r1 = 0, Z1
    MOVHI r1, 0x0001     ; c91  r1 = 0x00010000, Z0
    HALT
"""


def test_flags_arith_logic():
    m = run(FLAGS)
    exp = {  # t: (reg, value, z, c)
        7: ("r0", 0xFFFFFFFF, 0, 0),
        15: ("r0", 0, 1, 1),
        19: ("r1", 2, 0, 0),
        27: ("r2", 0xFFFFFFFF, 0, 1),
        31: ("r1", 0, 1, 0),
        35: ("r2", 0xFFFE, 0, 1),
        39: ("r2", 0xFFFE, 0, 1),
        43: ("r2", 0xFFFE, 1, 0),
        51: ("r2", 0xFFFE, 0, 0),
        55: ("r3", 5, 0, 1),
        63: ("r0", 0, 1, 1),
        67: ("r0", 5, 0, 1),
        71: ("r0", 0, 1, 1),
        75: ("r3", 5, 1, 1),
        79: ("r3", 5, 0, 1),
        83: ("r1", 5, 0, 1),
        87: ("r1", 0, 1, 1),
        91: ("r1", 0x00010000, 0, 1),
    }
    for t, (reg, val, z, c) in exp.items():
        p = at(m, t)["post"]
        assert (p[reg], p["z"], p["c"]) == (val, z, c), t


SHIFTS = """
.entry 0, s
s:  MOVI r0, 0x0001      ; c3
    MOVHI r0, 0x8000     ; c7   r0 = 0x80000001
    SHL r0, 1            ; c11  C = r0[31] = 1, r0 = 2, Z0
    SHR r0, 2            ; c15  C = r0[1] = 1, r0 = 0, Z1
    MOVI r1, 3           ; c19
    SHL r1, 31           ; c23  C = r1[1] = 1, r1 = 0x80000000, Z0
    SHR r1, 31           ; c27  C = r1[30] = 0, r1 = 1, Z0
    SHR r1, 1            ; c31  C = r1[0] = 1, r1 = 0, Z1
    MOVI r2, 7           ; c35  Z0, C stays 1
    SHL r2, 0            ; c39  n=0: r2 unchanged, C = 0
    SHL r2, 1            ; c43  C = 0, r2 = 14
    SHR r2, 2            ; c47  C = r2[1] = 1, r2 = 3
    SHR r2, 0            ; c51  n=0: r2 unchanged, C = 0
    HALT
"""


def test_shifts():
    m = run(SHIFTS)
    exp = {11: ("r0", 2, 0, 1), 15: ("r0", 0, 1, 1), 23: ("r1", 0x80000000, 0, 1),
           27: ("r1", 1, 0, 0), 31: ("r1", 0, 1, 1), 35: ("r2", 7, 0, 1),
           39: ("r2", 7, 0, 0), 43: ("r2", 14, 0, 0), 47: ("r2", 3, 0, 1), 51: ("r2", 3, 0, 0)}
    for t, (reg, val, z, c) in exp.items():
        p = at(m, t)["post"]
        assert (p[reg], p["z"], p["c"]) == (val, z, c), t


def test_shift_n0_sets_z_from_unchanged_value_specq5():
    # SPEC-Q 5 (our reading): SHL/SHR with n=0 still set Z = (R[rd] == 0).
    # r1 = 0 sets Z=1 (c7); SHL r2, 0 with r2 = 7 then gives Z=0 (not "unchanged" Z=1).
    m = run(".entry 0, s\ns: MOVI r2, 7\nMOVI r1, 0\nSHL r2, 0\nMOVI r1, 0\nSHR r2, 0\nHALT\n")
    assert at(m, 7)["post"]["z"] == 1
    assert (at(m, 11)["post"]["z"], at(m, 11)["post"]["c"]) == (0, 0)
    assert (at(m, 19)["post"]["z"], at(m, 19)["post"]["c"]) == (0, 0)


def test_ldsh_stsh_ldlc_irq():
    m = run(".entry 0, s\ns: MOVI r0, 0x1234\nLDSH r0\nSTSH r3\nLDLC 0xBEEF\nIRQ 0x5A\nHALT\n")
    assert at(m, 7)["post"]["sh"] == 0x1234 and at(m, 7)["post"]["z"] == 0
    assert at(m, 11)["post"]["r3"] == 0x1234
    assert at(m, 15)["post"]["lc"] == 0xBEEF
    assert m.dbg_read(0, 13) == (1 << 8) | 0x5A     # sel 13: [8] pending, [7:0] code


# --------------------------------------------------------------------------- §7.2 control


def test_djnz_wrap():
    src = ".entry 0, s\ns: LDLC 0\nDJNZ 3\nHALT\nLDLC 1\nDJNZ 0\nHALT\n"
    m = run(src)
    # c7: lc = 0 - 1 = 0xFFFF (16-bit wrap) != 0 -> PC = 3
    assert (at(m, 7)["post"]["lc"], at(m, 7)["post"]["pc"]) == (0xFFFF, 3)
    # c15: lc = 1 - 1 = 0 -> not taken -> PC = 5; HALT at c19
    assert (at(m, 15)["post"]["lc"], at(m, 15)["post"]["pc"]) == (0, 5)
    assert at(m, 19)["pc"] == 5 and at(m, 19)["outcome"] == "C"


def test_jumps():
    src = """
    .entry 0, s
    s: MOVI r0, 0     ; c3  Z=1
       JNZ 9          ; c7  not taken -> 2
       JZ 4           ; c11 taken -> 4
       HALT
       CMPI r0, 1     ; c15 0 < 1: C=1, Z=0
       JNC 9          ; c19 not taken -> 6
       JC 8           ; c23 taken -> 8
       HALT
       JMP 10         ; c27
       HALT
       HALT           ; c31
    """
    m = run(src)
    assert [at(m, t)["post"]["pc"] for t in (7, 11, 19, 23, 27)] == [2, 4, 6, 8, 10]
    assert at(m, 31)["pc"] == 10 and state(m, 0) == HALTED


def test_call_ret():
    m = Machine(assemble(".entry 0, s\ns: NOP\nCALL 5\nHALT\n.org 5\nRET\n"))
    m.run(8, stop_when_idle=False)
    # CALL at PC 1 commits at 7: lr = 2, PC = 5. sel 7 = lr<<16 | pc<<8 | RUNNING<<2
    assert at(m, 7)["post"]["pc"] == 5
    assert m.dbg_read(0, 7) == (2 << 16) | (5 << 8) | (RUNNING << 2)
    m.run(100)
    # RET at 11 -> PC 2; HALT at 15
    assert at(m, 11)["post"]["pc"] == 2 and at(m, 15)["outcome"] == "C"


@pytest.mark.parametrize("insn,outcome,post_pc", [
    ("NOP", "F", 255),          # sequential next PC at 255 -> PC_RANGE
    ("CALL 0", "F", 255),       # CALL at 255 -> PC_RANGE
    ("IRQ 1", "F", 255),
    ("JMP 0", "C", 0),          # non-sequential: fine
    ("RET", "C", 0),            # PC = lr = 0
    ("HALT", "C", 255),         # PC unchanged, not sequential
    ("JNZ 0", "C", 0),          # Z=0 -> taken
    ("JZ 0", "F", 255),         # Z=0 -> not taken -> sequential -> PC_RANGE
])
def test_pc_range(insn, outcome, post_pc):
    m = Machine(assemble(f".entry 0, s\n.org 255\ns: {insn}\n"))
    m.run(4, stop_when_idle=False)
    c = at(m, 3)
    assert (c["outcome"], c["post"]["pc"]) == (outcome, post_pc)
    if outcome == "F":
        assert m.dbg_read(0, 14) == ff(PC_RANGE, 0, 0, 255) and m.dbg_read(0, 15) == 3
    else:
        assert m.dbg_read(0, 14) == 0


def test_pc_range_wait_and_emit_at_255():
    # WAITT at 255: W is fine (PC unchanged); when it would COMPLETE -> PC_RANGE.
    m = run(".entry 0, s\n.org 254\ns: ADDT 15\nWAITT\n")
    assert [(c["t"], c["outcome"]) for c in m.commits] == [(3, "C"), (7, "W"), (11, "W"),
                                                           (15, "F")]
    assert m.dbg_read(0, 14) == ff(PC_RANGE, 0, 0, 255)
    # EMIT at 255 that would be admitted -> PC_RANGE, no effects (FIFO empty, no telemetry)
    m = run(".entry 0, s\n.own 0, 0\n.org 254\ns: ADDT 15\nEMIT 1, 1, 1\n")
    assert at(m, 7)["outcome"] == "F" and m.dbg_read(0, 14) == ff(PC_RANGE, 0, 0, 255)
    assert m.dbg_read(0, 9) == 0xFFFF << 16 and m.dbg_read(0, 10) == 0
    # EMIT at 255 that faults admission -> ADMIT (outcome is not COMPLETE)
    m = run(".entry 0, s\n.org 254\ns: ADDT 15\nEMIT 0, 0, 0\n")
    assert m.dbg_read(0, 14) == ff(ADMIT, MASK, 0, 255)


@pytest.mark.parametrize("word", [
    0x00000000,                 # opcode 0x00 unlisted
    0xFFFFFFFF,                 # opcode 0xFF unlisted
    0x05000000,                 # unlisted opcode
    0x01000001,                 # NOP (format N) with reserved bit 0
    0x08000001,                 # MOV (R: rd[23:22], rs[21:20]) with bit 0
    0x09010000,                 # MOVI (I16) with reserved bit 16
    0x23800000,                 # WAITT with bit 23
    0x2C080000,                 # SAMPLE (PS: pin[18:16], dir[15]) with bit 19
    0x2A014001,                 # EMITX (PX: dir[15]) with bit 14
    0x12000020,                 # SHL (SH: n[4:0]) with bit 5
])
def test_illegal(word):
    m = run(f".entry 0, s\ns: .word {word:#x}\n")
    assert at(m, 3)["outcome"] == "F" and at(m, 3)["insn"] == word
    assert m.dbg_read(0, 14) == ff(ILLEGAL, 0, 0, 0) and m.dbg_read(0, 15) == 3
    assert state(m, 0) == FAULTED


def test_legal_boundaries_are_not_illegal():
    # MOVI r3, 0xFFFF and SHL r3, 31 use every field bit; no reserved bit set.
    m = run(".entry 0, s\ns: .word 0x09C0FFFF\n.word 0x12C0001F\nHALT\n")
    assert [c["outcome"] for c in m.commits] == ["C", "C", "C"]
    assert at(m, 3)["post"]["r3"] == 0xFFFF


# --------------------------------------------------------------------------- §7.4 events, §5 pins


def test_emitx_lsb_msb():
    src = """
    .entry 0, s
    .own 0, 0
    s: MOVI r0, 5             ; c3
       LDSH r0                ; c7   sh = 5
       ADDT 30                ; c11  tmr 30
       EMITX 0x01, LSB, 0x01  ; c15  b = sh[0] = 1 -> value 1; sh = 2; C = 1
       ADDT 10                ; c19  tmr 40
       EMITX 0x01, LSB, 0x01  ; c23  b = sh[0] = 0 -> value 0; sh = 1; C = 0
       MOVI r1, 1             ; c27
       MOVHI r1, 0x8000       ; c31  r1 = 0x80000001
       LDSH r1                ; c35
       ADDT 10                ; c39  tmr 50
       EMITX 0x01, MSB, 0x01  ; c43  b = sh[31] = 1 -> value 1; sh = 2; C = 1
       ADDT 10                ; c47  tmr 60
       EMITX 0x01, MSB, 0x01  ; c51  b = sh[31] = 0 -> value 0; sh = 4; C = 0
       HALT
    """
    m = run(src)
    for t, sh, c in ((15, 2, 1), (23, 1, 0), (43, 2, 1), (51, 4, 0)):
        assert at(m, t)["outcome"] == "C"
        assert (at(m, t)["post"]["sh"], at(m, t)["post"]["c"]) == (sh, c), t
    # deadlines 30, 40, 50, 60 -> outputs change in cycles 31, 41, 51, 61
    assert pin(m, 30) == (0, 0)
    assert all(pin(m, t) == (1, 1) for t in range(31, 41))
    assert all(pin(m, t) == (0, 1) for t in range(41, 51))
    assert all(pin(m, t) == (1, 1) for t in range(51, 61))
    assert pin(m, 61) == (0, 1)


def test_emitx_wait_does_not_shift():
    # Fill the FIFO, then EMITX WAITs: sh and C unchanged on W (effects only on COMPLETE).
    m = run("""
    .entry 0, s
    .own 0, 0
    s: MOVI r0, 3
       LDSH r0           ; c7 sh = 3
       ADDT 100          ; c11
       EMIT 1, 1, 1      ; c15 d100
       ADDT 1
       EMIT 1, 1, 1      ; c23 d101
       ADDT 1
       EMIT 1, 1, 1      ; c31 d102
       ADDT 1
       EMIT 1, 1, 1      ; c39 d103
       ADDT 1            ; c43 tmr 104
       EMITX 1, LSB, 1   ; c47..c99 W; c103 C (all 4 retired by E_103)
       HALT
    """)
    for c in m.commits:
        if c["pc"] == 11 and c["outcome"] == "W":
            assert (c["post"]["sh"], c["post"]["c"]) == (3, 0)
    assert at(m, 103)["outcome"] == "C"
    assert (at(m, 103)["post"]["sh"], at(m, 103)["post"]["c"]) == (1, 1)
    assert m.dbg_read(0, 9) & 0xFFFF == (99 - 47) // 4 + 1      # 14 waits


def test_emitr():
    src = """
    .entry 0, s
    .own 0, 0
    .own 1, 0
    .own 2, 0
    .own 3, 0
    s: MOVI r1, 0x01A5         ; c3
       ADDT 20                 ; c7
       EMITR 0x0F, r1, 0x0F    ; c11: value = r1[7:0] = 0xA5; pins 0..3 get 0x5, oe 0xF
       HALT
    """
    m = run(src)
    assert pin(m, 20) == (0, 0) and pin(m, 21) == (0x05, 0x0F)


def test_open_drain_encoding():
    src = """
    .entry 0, s
    .entry 1, g
    .own 1, 0
    .od 1
    s: ADDT 10
       EMIT 0x02, 0x00, 0x02   ; c7  d10: val=0 oe=1 -> drive low
       ADDT 10
       EMIT 0x02, 0x02, 0x02   ; c15 d20: val=1 oe=1 -> released
       ADDT 10
       EMIT 0x02, 0x00, 0x00   ; c23 d30: val=0 oe=0 -> released
       ADDT 10
       EMIT 0x02, 0x00, 0x02   ; c31 d40: drive low
       HALT
    .org 32
    g: NOP                     ; ctx1 c4
       NOP                     ; c8
       NOP                     ; c12
       GETP r0                 ; c16: sync(16) = uio_in(14): pin1 driven low -> 0xFD
       NOP                     ; c20
       NOP                     ; c24
       GETP r1                 ; c28: sync(28) = uio_in(26): pin1 released -> 0xFF
       HALT
    """
    m = run(src)
    # open-drain: uio_out[1] is always 0; uio_oe[1] = oe & ~val
    assert pin(m, 10) == (0, 0)
    assert all(pin(m, t) == (0, 0x02) for t in range(11, 21))
    assert all(pin(m, t) == (0, 0x00) for t in range(21, 41))
    assert pin(m, 41) == (0, 0x02)
    assert at(m, 16)["post"]["r0"] == 0xFD and at(m, 28)["post"]["r1"] == 0xFF


# --------------------------------------------------------------------------- SAMPLE / GETI


SAMPLE3 = """
.entry 0, s
s: ADDT 41
   SAMPLE 2, {d}     ; c7   d 41
   ADDT 1
   SAMPLE 2, {d}     ; c15  d 42
   ADDT 1
   SAMPLE 2, {d}     ; c23  d 43
   WAITT             ; c27..39 W, c43 C (tmr 43)
   GETI r0           ; c47
   HALT
"""


@pytest.mark.parametrize("direction,expected", [
    # Board: pin 2 high only in cycle 40 -> sync[2] = 1 only in cycle 42.
    # Samples at 41, 42, 43 see 0, 1, 0.
    ("MSB", 0b010),                         # (x<<1)|b: 0 -> 1 -> 2
    ("LSB", 0x40000000),                    # (x>>1)|(b<<31): 0 -> 0x80000000 -> 0x40000000
])
def test_sample_timing(direction, expected):
    m = run(SAMPLE3.format(d=direction), board=lambda t, o, oe: 0x04 if t == 40 else 0)
    assert at(m, 43)["outcome"] == "C"
    assert at(m, 47)["post"]["r0"] == expected
    assert m.dbg_read(0, 8) == 0            # GETI cleared ish


def test_geti_waits_for_inflight_sample():
    """v1.1 (D-20): in v1.0 a GETI at the edge where the last SAMPLE retired returned the
    word without that bit and pushed the bit into the next word, silently. Now GETI WAITs
    while the FIFO (during its commit cycle) holds any SAMPLE, including one due then."""
    src = """
    .entry 0, s
    s: ADDT 20
       SAMPLE 0, MSB     ; c7   d 20: sync[0] = 1 -> ish = 1
       ADDT 11
       SAMPLE 0, MSB     ; c15  d 31
       NOP               ; c19
       NOP               ; c23
       NOP               ; c27
       GETI r0           ; c31: SAMPLE d 31 still in the FIFO -> WAIT
                         ; c35: ish = (1<<1)|1 = 3, both bits; cleared
       GETI r1           ; c39: 0
       HALT
    """
    m = run(src, board=const(0x01))
    assert at(m, 31)["outcome"] == "W"
    assert (at(m, 35)["outcome"], at(m, 35)["post"]["r0"], at(m, 35)["post"]["z"]) == ("C", 3, 0)
    assert (at(m, 39)["post"]["r1"], at(m, 39)["post"]["z"]) == (0, 1)
    assert m.dbg_read(0, 8) == 0


# --------------------------------------------------------------------------- EXPECT / GETX


GETX3 = """
.entry 0, s
s: ADDT 20
   EXPECT 0x08, 0x08, {mode}, {w}   ; c7 d 20
   NOP
   NOP
   GETX r0                          ; c19
   GETX r1                          ; c23
   GETX r2                          ; c27
   HALT
"""


def getx(fail, passed, active, failed):
    return (fail << 24) | (passed << 16) | (active << 1) | failed


def test_expect_hold_pass():
    # sync[3] = 1 for every tick >= 1 -> ticks 20..23 all match -> pass at E_23.
    m = run(GETX3.format(mode="HOLD", w=3), board=const(0x08))
    assert (at(m, 19)["post"]["r0"], at(m, 19)["post"]["z"]) == (0, 1)    # not started
    assert at(m, 23)["post"]["r1"] == getx(0, 0, 1, 0)   # active during cycle 23 (stops at E_23)
    assert at(m, 27)["post"]["r2"] == getx(0, 1, 0, 0)
    assert m.dbg_read(0, 10) == (1 << 16) | (1 << 8)     # pass 1, max_occupancy 1


def test_expect_hold_fail():
    # board 0x18 except 0x10 in cycle 19 -> sync(21) = 0x10: bit 3 low -> fail at u = 21.
    m = run(GETX3.format(mode="HOLD", w=3), board=lambda t, o, oe: 0x10 if t == 19 else 0x18)
    assert at(m, 19)["post"]["r0"] == 0
    assert at(m, 23)["post"]["r1"] == getx(1, 0, 0, 1)
    assert m.dbg_read(0, 11) == 21
    assert m.dbg_read(0, 12) == (0x08 << 16) | (0x08 << 8) | 0x10
    assert state(m, 0) == HALTED and m.dbg_read(0, 14) == 0      # a fail never faults


def test_expect_arrive_pass():
    # bit 3 rises in uio_in at cycle 23 -> sync from 25 -> ARRIVE (d 20, w 10) passes at E_25.
    m = run(GETX3.format(mode="ARRIVE", w=10), board=lambda t, o, oe: 0x08 if t >= 23 else 0)
    assert at(m, 23)["post"]["r1"] == getx(0, 0, 1, 0)
    assert at(m, 27)["post"]["r2"] == getx(0, 1, 0, 0)


def test_expect_arrive_fail():
    # never arrives: ticks 20..23 no match -> fail at u = d+w = 23
    m = run(GETX3.format(mode="ARRIVE", w=3), board=const(0x00))
    assert at(m, 23)["post"]["r1"] == getx(0, 0, 1, 0)
    assert at(m, 27)["post"]["r2"] == getx(1, 0, 0, 1)
    assert m.dbg_read(0, 11) == 23
    assert m.dbg_read(0, 12) == (0x08 << 16) | (0x08 << 8) | 0x00


def test_first_fail_record_kept_and_counts():
    src = """
    .entry 0, s
    s: ADDT 20
       EXPECT 0x08, 0x08, HOLD, 0   ; c7  d 20, end 20
       ADDT 10
       EXPECT 0x08, 0x08, HOLD, 0   ; c15 d 30 (ahead of last end 20)
       NOP                          ; c19 (first EXPECT fails at E_20, after this)
       GETX r0                      ; c23: one fail so far
       NOP                          ; c27
       GETX r1                      ; c31: second EXPECT failed at E_30 -> two fails
       HALT
    """
    # board uio_in(t) = t & 0xF7 (bit 3 always 0): sync(20) = 18 = 0x12, sync(30) = 28&0xF7 = 0x14
    m = run(src, board=lambda t, o, oe: t & 0xF7)
    assert at(m, 23)["post"]["r0"] == getx(1, 0, 0, 1)
    assert at(m, 31)["post"]["r1"] == getx(2, 0, 0, 1)
    assert m.dbg_read(0, 11) == 20                                   # first fail kept
    assert m.dbg_read(0, 12) == (0x08 << 16) | (0x08 << 8) | 0x12


def test_expect_level_outside_mask_never_matches():
    # m = ((sync & mask) == level); level bit 4 outside mask 0x08 -> never m.
    # HOLD fails at d; board 0xFF.
    m = run(".entry 0, s\ns: ADDT 20\nEXPECT 0x08, 0x18, HOLD, 2\nHALT\n", board=const(0xFF))
    assert m.dbg_read(0, 10) >> 24 == 1 and m.dbg_read(0, 11) == 20


# --------------------------------------------------------------------------- HALT


def test_halt_waits_for_empty_fifo():
    m = run(".entry 0, s\n.own 0, 0\ns: ADDT 30\nEMIT 1, 1, 1\nHALT\n")
    # HALT c11..c27 W (entry d=30 still queued); retires at E_30 -> C at 31
    hs = [(c["t"], c["outcome"]) for c in m.commits if c["pc"] == 2]
    assert hs == [(11, "W"), (15, "W"), (19, "W"), (23, "W"), (27, "W"), (31, "C")]
    assert at(m, 31)["post"]["pc"] == 2 and state(m, 0) == HALTED
    # pins retained after HALT
    assert m.outputs() == (1, 1)


def test_halt_deadline_at_commit_tick():
    # d = 31 retires at E_31; "FIFO after retirement at E_c" is empty -> HALT C at 31
    m = run(".entry 0, s\n.own 0, 0\ns: ADDT 31\nEMIT 1, 1, 1\nHALT\n")
    assert at(m, 27)["outcome"] == "W" and at(m, 31)["outcome"] == "C"


def test_halt_waits_for_checker():
    # HOLD d 20 w 7, all match: active after E_20..E_26, passes at E_27.
    m = run(".entry 0, s\ns: ADDT 20\nEXPECT 0x08, 0x08, HOLD, 7\nHALT\n", board=const(0x08))
    hs = [(c["t"], c["outcome"]) for c in m.commits if c["pc"] == 2]
    assert hs == [(11, "W"), (15, "W"), (19, "W"), (23, "W"), (27, "C")]
    assert m.dbg_read(0, 10) >> 16 & 0xFF == 1


@pytest.mark.parametrize("w,expected", [
    (0, [(11, "W"), (15, "W"), (19, "W"), (23, "C")]),   # starts and stops at E_23
    (1, [(11, "W"), (15, "W"), (19, "W"), (23, "W"), (27, "C")]),  # active after E_23
])
def test_halt_expect_retiring_at_commit_edge(w, expected):
    m = run(f".entry 0, s\ns: ADDT 23\nEXPECT 0x08, 0x08, HOLD, {w}\nHALT\n", board=const(0x08))
    assert [(c["t"], c["outcome"]) for c in m.commits if c["pc"] == 2] == expected


# --------------------------------------------------------------------------- §8 faults


FAULTS = """
.entry 0, a
.entry 1, b
.entry 2, cc
.own 0, 0
.own 1, 0
.own 2, 1
a:  ADDT 10                 ; ctx0 c3
    EMIT 0x03, 0x03, 0x03   ; c7  d 10: pins 0,1 high from 11
    ADDT 13                 ; c11 tmr 23
    EMIT 0x03, 0x00, 0x03   ; c15 d 23 (due exactly at the fault edge)
    NOP                     ; c19
    .word 0                 ; c23 ILLEGAL -> FAULT; the d 23 DRIVE must not retire
.org 16
b:  ADDT 10                 ; ctx1 c4
    EMIT 0x04, 0x04, 0x04   ; c8  d 10: pin 2 high from 11
    ADDT 30                 ; c12 tmr 40
    EMIT 0x04, 0x00, 0x04   ; c16 d 40: pin 2 low from 41
    HALT                    ; c20..36 W, c40 C
.org 32
cc: NOP                     ; ctx2 c5, 9, ..., 33 (8 NOPs)
    NOP
    NOP
    NOP
    NOP
    NOP
    NOP
    NOP
    .word 0xFFFFFFFF        ; c37 ILLEGAL (second fault; first-fault unchanged)
"""


def test_fault_effects():
    m = run(FAULTS)
    assert at(m, 23)["outcome"] == "F" and at(m, 23)["ctx"] == 0
    assert state(m, 0) == FAULTED
    # cycle 23: all three pins driven high. Fault at E_23: oe released on ctx0's pins 0,1
    # (val unchanged = 1, push-pull uio_out = val), DRIVE due at E_23 flushed, not retired.
    assert pin(m, 23) == (0b111, 0b111)
    assert pin(m, 24) == (0b111, 0b100)
    # ctx1 unaffected: its d 40 DRIVE retires -> pin 2 low from 41, still driven
    assert pin(m, 41) == (0b011, 0b100)
    assert [(c["t"], c["outcome"]) for c in m.commits if c["ctx"] == 1 and c["pc"] == 20] == \
        [(20, "W"), (24, "W"), (28, "W"), (32, "W"), (36, "W"), (40, "C")]
    assert state(m, 1) == HALTED
    # ctx2 faults later; the first-fault register keeps ctx0's
    assert at(m, 37)["outcome"] == "F" and state(m, 2) == FAULTED
    assert m.dbg_read(3, 14) == ff(ILLEGAL, 0, 0, 5)      # 14/15 ignore dbg_sel_ctx
    assert m.dbg_read(2, 15) == 23
    assert (m.dbg_read(0, 10) >> 4) & 7 == 0              # FIFO flushed


def test_admit_fault_flushes_due_entry_and_deactivates_checker():
    src = """
    .entry 0, s
    .own 0, 0
    s: ADDT 30
       EXPECT 0x08, 0x08, HOLD, 100   ; c7  d 30, window to 130
       ADDT 1
       EMIT 1, 1, 1                   ; c15 d 31
       NOP                            ; c19
       NOP                            ; c23
       NOP                            ; c27
       EMIT 0, 0, 0                   ; c31 MASK fault; d 31 due at E_31 is flushed
    """
    m = run(src, board=const(0x08))
    assert at(m, 31)["outcome"] == "F"
    assert m.dbg_read(0, 14) == ff(ADMIT, MASK, 0, 7)
    assert all(pin(m, t) == (0, 0) for t in range(len(m.pins)))   # DRIVE never retired
    assert (m.dbg_read(0, 10) >> 1) & 1 == 0                   # checker deactivated
    assert m.dbg_read(0, 10) >> 16 == 0                         # no pass/fail recorded
    assert m.pins[-1]["t"] == 32                                # idle during 32


def test_late_head_invariant_whitebox():
    # LATE_HEAD is unreachable through admission; inject a stale head directly.
    from esciss.machine import DRIVE, Entry
    m = Machine(assemble(".entry 0, s\n.own 0, 0\ns: WAITT\nJMP 0\n"))
    m.run(2, stop_when_idle=False)              # state during cycle 2
    m.ctx[0].fifo.append(Entry(DRIVE, 1, mask=1, value=1, oe=1))   # deadline 1 < 2
    m.run(1, stop_when_idle=False)              # E_2: reached(2,1) and 1 != 2 -> LATE_HEAD
    assert state(m, 0) == FAULTED
    assert m.dbg_read(0, 14) == ff(LATE_HEAD, 0, 0, 0) and m.dbg_read(0, 15) == 2


# --------------------------------------------------------------------------- §7.5 mailboxes


def test_mailbox_pop_push():
    src = """
    .entry 0, s
    s: RXREADY        ; c3: RX empty -> Z=1
       POP r0         ; c7 W (push at E_7 invisible), c11 C
       RXREADY        ; c15: Z=0? no: RX empty again -> Z=1
       PUSH r0        ; c19
       PUSH r0        ; c23 (TX now full)
       TXREADY        ; c27: Z = (TX full) = 1
       PUSH r0        ; c31 W (full) ; host pops at E_31 (invisible) ; c35 C
       HALT
    """
    m = Machine(assemble(src))
    m.run(7, stop_when_idle=False)              # cycles 0..6; next edge is E_7
    m.host_rx_push(0, 0xCAFEF00D)
    m.run(24, stop_when_idle=False)             # through cycle 30; next edge E_31
    assert m.host_tx_pop(0) == 0xCAFEF00D
    m.run(100)
    assert (at(m, 3)["post"]["z"]) == 1
    assert at(m, 7)["outcome"] == "W"
    assert at(m, 11)["outcome"] == "C" and at(m, 11)["post"]["r0"] == 0xCAFEF00D
    assert at(m, 15)["post"]["z"] == 1
    assert at(m, 27)["post"]["z"] == 1
    assert at(m, 31)["outcome"] == "W" and at(m, 35)["outcome"] == "C"
    assert m.host_tx_pop(0) == 0xCAFEF00D


# --------------------------------------------------------------------------- §9 stop


def test_stop():
    src = ".entry 0, s\n.own 0, 0\ns: ADDT 10\nEMIT 1, 1, 1\nADDT 100\nEMIT 1, 0, 1\nl: JMP l\n"
    m = Machine(assemble(src))
    m.run(50, stop_when_idle=False)             # cycles 0..49
    assert m.outputs() == (1, 1)
    m.stop()
    m.run(100, stop_when_idle=False)            # cycle 50, stop at E_50, run ends
    assert m.pins[-1]["t"] == 50
    assert state(m, 0) == HALTED and (m.dbg_read(0, 10) >> 4) & 7 == 0
    assert m.outputs() == (1, 0)                # oe = 0 on every pin; val kept
    assert m.commits[-1]["t"] == 47


# --------------------------------------------------------------------------- trace


def test_write_trace(tmp_path):
    import json
    m = run(SEC11, board=loopback)
    p = tmp_path / "t.jsonl"
    m.write_trace(str(p))
    lines = [json.loads(s) for s in p.read_text().splitlines()]
    assert lines[: len(m.commits)] == m.commits
    assert lines[len(m.commits): -1] == m.pins
    assert lines[-1]["final"]["contexts"][0]["state"] == "HALTED"


# --------------------------------------------------------------------------- firmware


BIT = 217


def line_level(rec):
    # pin 0 with the default pull-up board: driven -> uio_out, released -> 1
    return (rec["uio_out"] & 1) if rec["uio_oe"] & 1 else 1


def decode_uart(pins):
    lvl = [line_level(r) for r in pins]
    # The run stops when idle; pin registers are retained after HALT (§7.2), so the line
    # holds its last level after the final record (the last stop bit's mid-sample is at
    # 6744 + 9*217 + 108 = 8805 > 8700).
    lvl += [lvl[-1]] * (BIT * 10)
    out, starts, i = [], [], 1
    while i < len(pins):
        if lvl[i - 1] == 1 and lvl[i] == 0:
            assert lvl[i + BIT // 2] == 0                    # start bit mid-sample
            byte = 0
            for j in range(8):
                byte |= lvl[i + BIT * (j + 1) + BIT // 2] << j
            assert lvl[i + BIT * 9 + BIT // 2] == 1         # stop bit
            out.append(byte)
            starts.append(i)
            i += BIT * 9 + BIT // 2
        else:
            i += 1
    return out, starts


def test_firmware_uart_tx():
    m = Machine(assemble_file(ROOT / "firmware" / "uart_tx.asm"))
    m.run(20000)
    data, starts = decode_uart(m.pins)
    assert data == [0x55, 0x4B, 0x21, 0x0A]
    # DRIVE deadlines: idle-high 16, then bit n (n = 1..40) at 16 + 217 n.
    # Start bit of byte i has deadline 233 + 2170 i -> visible from cycle 234 + 2170 i.
    assert starts == [234 + 2170 * i for i in range(4)]
    assert line_level(m.pins[16]) == 1 and m.pins[17]["uio_oe"] == 1   # idle drive from 17
    assert state(m, 0) == HALTED and m.dbg_read(0, 14) == 0
    # min_lead: first EMIT (c7, d16) has lead 9; every later EMIT is admitted with a much
    # larger lead (start bit c35 d233 -> 198; then FIFO-full waits keep leads ~3 bits).
    assert m.dbg_read(0, 9) >> 16 == 9
    assert (m.dbg_read(0, 10) >> 8) & 7 == 4
    # Last entry (n=40, d 8696) is admitted at the first commit tick (= 3 mod 4) at or after
    # E_D36 = E_7828 -> c7831. ADDT/SHR/SUB/JNZ at 7835..7847, HALT W from 7851 and C at
    # 8699 (first c = 3 mod 4 at/after the last retirement at E_8696).
    # The context uses every slot 3..8699: 2175 commits. Completes: 7 prologue + 33 per byte
    # x 4 + HALT = 140. HALT waits: (8695-7851)/4+1 = 212. So EMIT-family waits (wait_full)
    # = 2175 - 140 - 212 = 1823.
    assert at(m, 8699)["outcome"] == "C" and at(m, 8699)["pc"] == 19   # HALT is word 19
    assert len(m.commits) == 2175
    assert sum(c["outcome"] == "C" for c in m.commits) == 140
    assert m.dbg_read(0, 9) & 0xFFFF == 1823
    # all idle during 8700 -> last pin record
    assert m.pins[-1]["t"] == 8700


# --------------------------------------------------------------------------- §10 IRQ clear (v1.0.1)


def test_host_irq_clear_and_status_ports():
    m = Machine(assemble(".entry 0, s\n.entry 1, t\ns: IRQ 0x11\nIRQ 0x22\n.word 0\n"
                         ".org 8\nt: IRQ 0x33\nHALT\n"))
    assert m.irq_pending == 0 and m.fault_valid is False
    m.run(3, stop_when_idle=False)          # cycles 0..2; next edge E_3
    m.host_irq_clear(0b0001)                # ctx0's IRQ 0x11 commits at E_3: set wins
    m.run(1, stop_when_idle=False)
    assert m.irq_pending == 0b0001 and m.dbg_read(0, 13) == 0x111
    # E_4: ctx1's IRQ 0x33 commits (ctx1 slot), ctx0 cleared by the host at the same edge
    m.host_irq_clear(0b0011)
    m.run(1, stop_when_idle=False)
    assert m.irq_pending == 0b0010          # ctx1 set wins, ctx0 cleared
    assert m.dbg_read(0, 13) == 0x011       # pending cleared, code left as is (SPEC-Q 15)
    assert m.dbg_read(1, 13) == 0x133
    m.run(3, stop_when_idle=False)          # through cycle 7: IRQ 0x22 at E_7
    assert m.irq_pending == 0b0011 and m.dbg_read(0, 13) == 0x122
    m.run(100)                              # .word 0 at c11 -> ILLEGAL
    assert m.fault_valid is True and m.irq_pending == 0b0011   # fault leaves IRQ alone
    m2 = Machine(assemble(".entry 0, s\ns: IRQ 1\nHALT\n"))
    m2.run(10)
    m2.host_irq_clear(0b0001)               # takes effect at the next edge only
    assert m2.irq_pending == 1


def test_checker_cancelled_at_fault_edge_v102():
    # Spec v1.0.2 §8: a fault at E_c cancels the checker's evaluation of tick c (the
    # earlier reading, SPEC-Q 4, recorded it; the RTL author read it the other way).
    # HOLD d 20 w 100; board bit 3 low only in cycle 21 -> sync(23) mismatch; the context
    # executes an ILLEGAL word (PC 5) committing at 23 -> fail recorded at u = 23.
    src = ".entry 0, s\ns: ADDT 20\nEXPECT 0x08, 0x08, HOLD, 100\nNOP\nNOP\nNOP\n.word 0\n"
    m = run(src, board=lambda t, o, oe: 0x00 if t == 21 else 0x08)
    assert at(m, 23)["outcome"] == "F" and m.dbg_read(0, 14) == ff(ILLEGAL, 0, 0, 5)
    assert m.dbg_read(0, 10) == (1 << 8)                     # max_occ 1; no fail, inactive
    assert m.dbg_read(0, 11) == 0                            # no first_fail record
