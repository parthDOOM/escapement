"""Hand-derived tests for semantics v1.3 streaming capture (docs/11-SEMANTICS.md §6.5).

The capture region is 0x20..0x23 (lim 0x24), so the ring holds at most 3 unread records.
Context 0 arms the capture unit at c11, so the snapshot is generated at E12 and written
at E13. Pin 0 toggles at 20, 30, 40, 50 and 60; sync during cycle t is uio_in during
t - 2, so the changes are recorded at E22, E32, E42, E52 and E62, each written at the
next edge at which the port is free and the drain is not held.
"""

from __future__ import annotations

from escasm import assemble
from esciss import Machine

CHANGES = (20, 30, 40, 50, 60)
BASE, LIM = 0x20, 0x24


def board(t, o, oe):
    return sum(t >= c for c in CHANGES) & 1


def rec(t):
    """The record generated at E_t: pin 0's level during t - 2, and the tick."""
    return (board(t - 2, 0, 0) << 24) | t


def cfg(base=BASE, lim=LIM, stream=1, ring=0):
    return (1 << 31) | (stream << 26) | (ring << 24) | (0xFF << 16) | (lim << 8) | base


def machine(v=None):
    v = cfg() if v is None else v
    src = f".entry 0, s\ns: MOVI r0, {v & 0xFFFF}\n MOVHI r0, {v >> 16}\n CAPT r0\n HALT\n"
    return Machine(assemble(src), board=board)


def status(en=1, ovf=0, wrapped=0, staged=0, ptr=0):
    return (en << 31) | (ovf << 30) | (wrapped << 28) | (staged << 8) | ptr


def test_stream_holds_instead_of_overwriting():
    m = machine()
    m.run(80, stop_when_idle=False)
    # E13, E23, E33 fill 0x20-0x22. At E43 the successor of 0x23 is base = rptr: held.
    # rec(42) and rec(52) stay staged; rec(62) finds the staging full: ovf at 62.
    assert m.tape[0x20:0x23] == [rec(12), rec(22), rec(32)]
    assert m.tape[0x23] == 0
    assert m.cap.status() == status(en=0, ovf=1, staged=2, ptr=0x23)
    assert m.cap.ovf_tick == 62
    assert m.dbg_read(0, 18) == cfg() & 0x07FFFFFF        # [26] stream, no enable bit
    assert m.dbg_read(0, 20) == BASE                 # rptr = base after CAPT


def test_ack_releases_the_drain_at_the_next_edge():
    m = machine()
    m.run(45, stop_when_idle=False)                  # cycles 0-44: rec(42) is held
    assert m.cap.status() == status(staged=1, ptr=0x23)
    m.host_tape_ack(0x23)                            # the host read 0x20-0x22
    m.run(1, stop_when_idle=False)                   # E45 drains with rptr during 45
    assert m.cap.status() == status(staged=1, ptr=0x23) and m.cap.rptr == 0x23
    m.run(1, stop_when_idle=False)                   # E46: T[0x23] = rec(42), wraps
    assert m.tape[0x23] == rec(42)
    assert m.cap.status() == status(wrapped=1, ptr=BASE)
    m.run(40, stop_when_idle=False)
    # E53 writes rec(52) at 0x20, E63 rec(62) at 0x21; the next would be held at 0x22.
    assert m.tape[0x20:0x24] == [rec(52), rec(62), rec(32), rec(42)]
    assert m.cap.status() == status(wrapped=1, ptr=0x22)
    assert not m.cap.ovf


def test_ack_while_stopped_drains_the_held_records():
    m = machine()
    m.run(55, stop_when_idle=False)                  # rec(42), rec(52) held
    m.stop()
    m.run(1)
    assert m.cap.status() == status(en=0, staged=2, ptr=0x23)
    m.host_tape_ack(0x23)
    assert m.tape[0x23] == rec(42) and m.tape[0x20] == rec(52)
    assert m.cap.status() == status(en=0, wrapped=1, ptr=0x21)


def test_rptr_outside_the_region_never_holds():
    m = machine()
    m.host_tape_ack(0x80)                            # applies at E0, before the CAPT
    m.run(80, stop_when_idle=False)
    assert m.cap.rptr == BASE                        # CAPT at c11 set rptr = base
    m = machine()
    m.run(12, stop_when_idle=False)
    m.host_tape_ack(0x80)
    m.run(68, stop_when_idle=False)
    # As a plain ring: E43 wraps to 0x20, E53 and E63 overwrite 0x20 and 0x21.
    assert m.tape[0x20:0x24] == [rec(52), rec(62), rec(32), rec(42)]
    assert m.cap.status() == status(wrapped=1, ptr=0x22)


def test_capt_overrides_an_ack_at_the_same_edge():
    m = machine()
    m.run(11, stop_when_idle=False)                  # the CAPT commits at E11
    m.host_tape_ack(0x22)
    m.run(1, stop_when_idle=False)
    assert m.cap.rptr == BASE


def test_ring_without_stream_is_unchanged():
    m = machine(cfg(stream=0, ring=1))
    m.run(80, stop_when_idle=False)
    assert m.tape[0x20:0x24] == [rec(52), rec(62), rec(32), rec(42)]
    assert m.cap.status() == status(wrapped=1, ptr=0x22)
    assert m.dbg_read(0, 18) >> 26 & 1 == 0


def test_tape_at_replays_the_write_history():
    m = machine()
    m.run(80, stop_when_idle=False)
    assert m.tape_at(13, 0x20) == 0                  # written at E13: visible from 14
    assert m.tape_at(14, 0x20) == rec(12)
    assert m.tape_at(33, 0x22) == 0 and m.tape_at(34, 0x22) == rec(32)
    assert [m.tape_at(1000, a) for a in range(256)] == m.tape


def test_host_stream_chunks():
    from eschost.protocol import stream_chunks
    assert stream_chunks(0x80, 0x90, 0x80, 0x85) == [(0x80, 5)]
    assert stream_chunks(0x80, 0x90, 0x8E, 0x82) == [(0x8E, 2), (0x80, 2)]   # wraps
    assert stream_chunks(0x80, 0x90, 0x84, 0x84) == []                       # nothing new
    assert sum(n for _, n in stream_chunks(0, 0, 0xFE, 2)) == 4               # whole tape
    assert len(stream_chunks(0x80, 0x90, 0x80, 0x10)) <= 256                  # ptr outside


def test_host_stream_poll_against_the_iss():
    """Host.stream_poll's frames, answered by a model of the chip built on the ISS: every
    record arrives once, in order, across many polls of a 4-word ring."""
    from eschost import Host
    from eschost.protocol import READ_DBG, READ_TAPE, TAPE_ACK, CLEAR_ERR

    m = machine()

    def xfer(frame):
        op = frame[0]
        if op == READ_DBG:
            v = m.dbg_read(0, 17)
            return bytes(3) + v.to_bytes(4, "big")
        if op == READ_TAPE:
            a, n = frame[1], (len(frame) - 3) // 4
            return bytes(3) + b"".join(m.tape[(a + i) & 0xFF].to_bytes(4, "big") for i in range(n))
        if op == TAPE_ACK:
            m.host_tape_ack(frame[1])
            return bytes(2)
        if op == CLEAR_ERR:
            return bytes(1)
        raise AssertionError(hex(op))

    h, rptr, got = Host(xfer), BASE, []
    for _ in range(16):
        m.run(5, stop_when_idle=False)
        words, rptr, _ = h.stream_poll(BASE, LIM, rptr)
        got += words
    assert got == [rec(t) for t in (12, 22, 32, 42, 52, 62)]
    assert not m.cap.ovf
