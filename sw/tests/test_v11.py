"""Hand-derived tests for semantics v1.1 (docs/11-SEMANTICS.md §6.5, §7.3, §7.6, §10.1).

Timing reminders: context k commits at ticks c = k + 3 (mod 4); sync during cycle t is
uio_in during t - 2; a capture record generated at E_t is written at the next edge whose
cycle has a free tape port.
"""

from __future__ import annotations

from escasm import assemble
from esciss import Machine
from esciss.decode import decode

M32 = 0xFFFFFFFF


def run(src, ticks=200, board=None, idle=True, now0=None):
    m = Machine(assemble(src), board=board or (lambda t, o, oe: 0))
    if now0 is not None:                    # test hook: start the run at another tick
        m.now = now0                        # (keeps slot alignment if now0 % 4 == 0)
    m.run(ticks, stop_when_idle=idle)
    return m


def at(m, t):
    hits = [c for c in m.commits if c["t"] == t]
    assert len(hits) == 1, f"no single commit at {t}: {hits}"
    return hits[0]


def cfg(base, lim, mask=0xFF, ring=0, enable=1):
    return (enable << 31) | (ring << 24) | (mask << 16) | (lim << 8) | base


def arm(v):
    """Two instructions that put v in r0, then CAPT r0 (commits at c3, c7, c11 on ctx 0)."""
    return f"MOVI r0, {v & 0xFFFF}\n MOVHI r0, {v >> 16}\n CAPT r0\n"


# --------------------------------------------------------------------------- tape


def test_ldt_stt_addressing_and_flags():
    m = run("""
    .tape 10, 0xDEADBEEF
    .entry 0, s
    s: MOVI r1, 7          ; c3   Z = 0
       LDT r0, r1, 3       ; c7   a = 10: r0 = 0xDEADBEEF, flags unchanged
       ADDI r0, 1          ; c11  r0 = 0xDEADBEF0
       STT r0, r1, 250     ; c15  a = (7 + 250) mod 256 = 1: T[1] = 0xDEADBEF0
       LDT r2, r1, 250     ; c19  r2 = T[1]
       HALT
    """)
    assert (at(m, 7)["post"]["r0"], at(m, 7)["post"]["z"]) == (0xDEADBEEF, 0)
    assert at(m, 11)["post"]["r0"] == 0xDEADBEF0
    assert m.tape[1] == 0xDEADBEF0
    assert at(m, 19)["post"]["r2"] == 0xDEADBEF0
    assert m.tape[10] == 0xDEADBEEF


def test_tape_persists_and_is_shared_between_contexts():
    m = run("""
    .entry 0, a
    .entry 1, b
    a: MOVI r0, 0x1234     ; c3
       STT r0, r0, 0       ; c7   T[0x34] = 0x1234
       HALT
    b: NOP                 ; c4
       NOP                 ; c8
       MOVI r3, 0x34       ; c12
       LDT r1, r3, 0       ; c16  r1 = 0x1234 (written at E_7)
       HALT
    """)
    assert at(m, 16)["post"]["r1"] == 0x1234


# --------------------------------------------------------------------------- capture


def steps(t):          # uio_in: 0 before 20, 1 from 20, 3 from 30
    return 0 if t < 20 else (1 if t < 30 else 3)


def test_capture_snapshot_and_changes():
    m = run(".entry 0, s\ns: " + arm(cfg(0x10, 0x20)) + " HALT\n", ticks=60,
            board=lambda t, o, oe: steps(t), idle=False)
    # CAPT at c11 -> en from cycle 12; snapshot at E12 (sync 0), written at E13.
    # sync_22 = uio_in(20) = 1 -> record at E22; sync_32 = 3 -> record at E32.
    assert m.tape[0x10:0x13] == [0x0000000C, 0x01000016, 0x03000020]
    assert m.cap.status() == 0x80000013            # en, ptr 0x13, nothing staged
    assert m.dbg_read(0, 17) == m.cap.status()
    assert m.dbg_read(3, 18) == cfg(0x10, 0x20) & 0x01FFFFFF
    assert m.dbg_read(0, 20) == 0x10               # v1.3: rptr, set to base by CAPT
    assert m.dbg_read(0, 21) == 0


def test_capture_mask_ignores_unwatched_pins():
    # mask = pin 1 only: the pin-0 change at 20 is not recorded, the pin-1 change at 30 is,
    # and the record carries all eight levels.
    m = run(".entry 0, s\ns: " + arm(cfg(0x10, 0x20, mask=0x02)) + " HALT\n", ticks=60,
            board=lambda t, o, oe: steps(t), idle=False)
    assert m.tape[0x10:0x12] == [0x0000000C, 0x03000020]
    assert m.cap.ptr == 0x12


def test_capture_full_stops_without_ring():
    m = run(".entry 0, s\ns: " + arm(cfg(0x20, 0x22)) + " HALT\n", ticks=60,
            board=lambda t, o, oe: steps(t), idle=False)
    # E13 writes T[0x20]; E23 writes T[0x21], ptr -> 0x22 == lim: full, en = 0.
    assert m.tape[0x20:0x22] == [0x0000000C, 0x01000016]
    assert m.cap.status() == (1 << 29) | 0x22
    assert m.tape[0x22] == 0                      # the change at 32 was not recorded


def test_capture_ring_wraps():
    m = run(".entry 0, s\ns: " + arm(cfg(0x20, 0x22, ring=1)) + " HALT\n", ticks=60,
            board=lambda t, o, oe: steps(t), idle=False)
    # E23: ptr -> lim -> base, wrapped. E33 overwrites T[0x20] with the record of tick 32.
    assert m.tape[0x20:0x22] == [0x03000020, 0x01000016]
    assert m.cap.status() == (1 << 31) | (1 << 28) | 0x21


LDT8 = "\n".join(["LDT r1, r2, 0"] * 8)


def test_capture_overflow_when_port_is_busy():
    # Every context runs LDTs, so from cycle 12 every cycle's port is busy. Inputs toggle
    # every tick: records at E12 (snapshot), E13, then E14 finds 2 staged -> ovf at 14.
    src = f"""
    .entry 0, a
    .entry 1, b
    .entry 2, b
    .entry 3, b
    a: {arm(cfg(0x40, 0x80))}
       {LDT8}
       HALT
    b: {LDT8}
       {LDT8}
       HALT
    """
    m = run(src, ticks=120, board=lambda t, o, oe: t & 1, idle=False)
    assert m.cap.ovf and not m.cap.en and m.cap.ovf_tick == 14
    assert m.dbg_read(0, 19) == 14
    # The prefix is complete: both staged records drained once the port freed up.
    assert m.tape[0x40:0x42] == [0x0000000C, 0x0100000D]
    assert m.cap.status() == (1 << 30) | 0x42


def test_capt_disable_then_rearm_snapshots():
    src = ".entry 0, s\ns: " + arm(cfg(0x10, 0x20)) + """
       MOVI r1, 0          ; c15
       CAPT r1             ; c19  disable at E19
       MOVI r1, 0x2018     ; c23
       MOVHI r1, 0x80FF    ; c27
       CAPT r1             ; c31  re-arm: ptr = 0x18, snapshot at E32
       HALT
    """
    m = run(src, ticks=60, board=lambda t, o, oe: steps(t), idle=False)
    assert m.tape[0x10] == 0x0000000C               # first arming's snapshot (E12 -> E13)
    assert m.tape[0x11] == 0                        # change at 22 fell in the disabled window
    assert m.tape[0x18] == 0x03000020               # sync_32 = uio_in(30) = 3
    assert m.cap.status() == 0x80000019


def test_capture_epoch_marker():
    # Start the run 16 ticks before a 2^24 boundary (test hook). A constant input gives
    # exactly two records: the snapshot and the epoch marker at tick 2^24 (field 0).
    n0 = (1 << 24) - 16
    m = run(".entry 0, s\ns: " + arm(cfg(0x00, 0x00)) + " HALT\n", ticks=40, now0=n0,
            board=lambda t, o, oe: 0x5A, idle=False)
    assert m.tape[0:2] == [0x5A000000 | ((n0 + 12) & 0xFFFFFF), 0x5A000000]
    assert m.cap.ptr == 2


def test_getc_reads_status_during_commit():
    src = ".entry 0, s\ns: " + arm(cfg(0x10, 0x20)) + " GETC r1\n GETC r2\n HALT\n"
    m = run(src, ticks=60, board=lambda t, o, oe: 0, idle=False)
    # c15: snapshot generated E12, written E13 -> ptr 0x11, nothing staged.
    assert at(m, 15)["post"]["r1"] == 0x80000011
    assert at(m, 15)["post"]["z"] == 0


# --------------------------------------------------------------------------- digest


def digest_from_records(m: Machine) -> int:
    """Independent recomputation of §10.1 from the observation records only."""
    rd_ops = {"MOV", "MOVI", "MOVHI", "ADD", "ADDI", "SUB", "AND", "OR", "XOR", "SHL", "SHR",
              "STSH", "RDTIME", "GETP", "GETI", "GETX", "POP", "GETC"}
    by_t = {c["t"]: c for c in m.commits}
    dg, ld, prev = 0xFFFFFFFF, None, (0, 0)
    for p in m.pins:
        t = p["t"]
        c = by_t.get(t)
        cur = (p["uio_out"], p["uio_oe"])
        active = c is not None or ld is not None or cur != prev
        prev = cur
        if not active:
            continue
        x = (p["uio_oe"] << 24) | (p["uio_out"] << 16)
        nld = None
        if c is not None:
            x ^= ({"C": 0, "W": 1, "F": 2}[c["outcome"]] << 14) | (c["ctx"] << 12) | c["pc"]
            d = decode(c["insn"])
            if c["outcome"] == "C" and d is not None:
                if d.name in rd_ops:
                    x ^= c["post"][f"r{d.f['rd']}"]
                elif d.name == "LDT":
                    nld = c["post"][f"r{d.f['ra']}"]
        if ld is not None:
            x ^= ld
        ld = nld
        dg = (((dg << 1) & M32) ^ (0x04C11DB7 if dg >> 31 else 0) ^ x) & M32
    return dg


def test_digest_matches_independent_recomputation():
    src = """
    .tape 3, 0xCAFEF00D
    .entry 0, s
    .own 0, 0
    s: MOVI r1, 3
       LDT r2, r1, 0
       ADDT 30
       EMIT 1, 1, 1
       SUB r2, r1
       SHL r2, 0
       RDTIME r3
       EMIT 1, 0, 1        ; deadline 30 again: ADMIT/ORDER fault
       HALT
    """
    m = run(src, ticks=100)
    assert m.ff.valid                                  # a FAULT outcome is folded too
    assert m.dbg_read(2, 16) == m.dg == digest_from_records(m)


def test_digest_sensitive_to_data():
    base = ".tape 0, {v}\n.entry 0, s\ns: LDT r0, r0, 0\n NOP\n HALT\n"
    a = run(base.format(v=1)).dg
    b = run(base.format(v=2)).dg
    assert a != b


def test_digest_frozen_after_stop():
    m = Machine(assemble(".entry 0, s\ns: JMP s\n"))
    m.run(40, stop_when_idle=False)
    m.stop()
    m.run(1)
    d = m.dg
    assert not m.run_flag
    m.run(10)
    assert m.dg == d


def test_digest_final_after_halt_whatever_the_stop_time():
    """Once every context has halted, dg no longer changes: the value a host reads does
    not depend on when its STOP arrives (§10.1 active edges)."""
    src = """
    .entry 0, s
    .own 0, 0
    s: ADDT 30
       EMIT 1, 1, 1
       ADDT 30
       EMIT 1, 0, 1
       HALT
    """
    finals = []
    for extra in (0, 7, 500):
        m = Machine(assemble(src))
        m.run(80 + extra, stop_when_idle=False)
        finals.append(m.dg)
    assert finals[0] == finals[1] == finals[2]


def test_capt_disable_at_a_watched_change_records_nothing():
    m = run(".entry 0, s\ns: " + arm(cfg(0x10, 0x20)) + " MOVI r1, 0\n CAPT r1\n HALT\n",
            ticks=60, board=lambda t, o, oe: 1 if t >= 17 else 0, idle=False)
    # sync_19 = uio_in(17) = 1 changes at the CAPT-disable edge E19: suppressed
    assert m.tape[0x10] == 0x0000000C and m.tape[0x11] == 0
    assert m.cap.status() == 0x00000011


def test_faulting_stt_does_not_write():
    m = run("""
    .tape 5, 0x11111111
    .tape 7, 0x22222222
    .entry 0, s
    .entry 1, x
    s: MOVI r0, 0x5A5A
       MOVI r1, 5
       .word 0x35100100    ; STT r0, r1, 0 with a reserved bit: ILLEGAL
       HALT
    .org 255
    x: STT r2, r3, 7       ; at PC 255: PC_RANGE
    """, ticks=60, idle=False)
    assert m.tape[5] == 0x11111111 and m.tape[7] == 0x22222222
    assert m.ctx[0].state == 3 and m.ctx[1].state == 3
