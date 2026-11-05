"""v1.7 live reload (S3) on the ISS: CTX_STOP, program writes during the run, CTX_START.
Context 1 is stopped, its program rewritten and restarted while context 0 runs on; context 0
must not notice, and the restarted context must behave as a run of the new program started at
the frame it restarted in. The RTL is checked against the ISS in test/cosim."""

from __future__ import annotations

import pytest

from escasm import assemble
from esciss import Machine

PROG = """
.entry 0, a
.entry 1, b
.own 0, 0
.own 1, 1
a:  ADDT 40
    EMIT 1, 1, 1
    ADDT 40
    EMIT 1, 0, 1
    JMP a
b:  ADDT {up}
    EMIT 2, 2, 2
    ADDT {down}
    EMIT 2, 0, 2
    JMP b
"""
OLD, NEW = assemble(PROG.format(up=24, down=24)), assemble(PROG.format(up=56, down=12))
TICKS = 2000


def pins(m, bit):
    """The pin as the board sees it: its level while driven, None while released (a stop
    releases a pin as a fault does, leaving its output register as it was)."""
    return [(p["uio_out"] >> bit & 1) if p["uio_oe"] >> bit & 1 else None for p in m.pins]


def reload(at, pair=False):
    """Run OLD; at tick `at` stop context 1, write NEW's differing words, restart it."""
    m = Machine(OLD)
    m.run(at, stop_when_idle=False)
    m.host_ctx_stop(0b0010)
    m.run(1, stop_when_idle=False)
    diff = [a for a in range(256) if OLD.words[a] != NEW.words[a]]
    for a in diff:
        m.imem_write(a, NEW.words[a])
    m.host_ctx_start(0b0010)
    m.run(TICKS - at - 1, stop_when_idle=False)
    return m, diff


@pytest.mark.parametrize("at", [301, 302, 303, 304, 517])
def test_context_0_does_not_notice(at):
    ref = Machine(OLD)
    ref.run(TICKS, stop_when_idle=False)
    m, diff = reload(at)
    b = NEW.entry_pcs[1]
    assert diff and all(a >= b for a in diff)          # only context 1's words change
    assert [c for c in m.commits if c["ctx"] == 0] == [c for c in ref.commits if c["ctx"] == 0]
    assert pins(m, 0) == pins(ref, 0)


@pytest.mark.parametrize("at", [301, 302, 303, 304, 517])
def test_restart_is_a_run_start_at_its_frame(at):
    m, _ = reload(at)
    stop_edge = at                                     # the CTX_STOP edge
    ones = [c for c in m.commits if c["ctx"] == 1 and c["t"] > stop_edge]
    first = ones[0]
    # restarted at the edge ending the cycle before slot 1: t_r + 1 = 1 (mod 4), after the
    # start was asked for (during cycle at + 1)
    t_r = next(t for t in range(at + 1, at + 6) if (t + 1) % 4 == 1)
    base = (t_r + 1) & ~3
    fresh = Machine(NEW)
    fresh.run(TICKS, stop_when_idle=False)
    f_ones = [c for c in fresh.commits if c["ctx"] == 1]
    # the same commits, PCs and post-state (tmr shifted by the frame base), shifted in time
    for got, want in zip(ones, f_ones):
        assert got["t"] - base == want["t"] and got["pc"] == want["pc"] and got["insn"] == want["insn"]
        assert got["post"]["tmr"] - base == want["post"]["tmr"]
    assert first["pc"] == NEW.entry_pcs[1]
    # and context 1's pin, from the restart on, is the fresh run's shifted by the frame base
    got, want = pins(m, 1)[base:], pins(fresh, 1)[:TICKS - base]
    assert got == want


def test_stop_releases_the_pin_and_discards_the_commit():
    m = Machine(OLD)
    m.run(300, stop_when_idle=False)
    assert m.oe >> 1 & 1                               # context 1 drives pin 1 by then
    m.host_ctx_stop(0b0010)
    m.run(1, stop_when_idle=False)
    assert not m.oe >> 1 & 1 and m.ctx[1].state == 2   # released; HALTED
    assert not [c for c in m.commits if c["ctx"] == 1 and c["t"] == 300]
    assert m.ctx[0].state == 1 and not m.ff.valid      # context 0 runs on; no fault record


PAIRED = """
.pair 3
.entry 0, a
.entry 3, b
.own 0, 0
.own 1, 3
a:  ADDT 40
    EMIT 1, 1, 1
    ADDT 40
    EMIT 1, 0, 1
    JMP a
b:  ADDT {up}
    EMIT 2, 2, 2
    ADDT {down}
    EMIT 2, 0, 2
    JMP b
"""


@pytest.mark.parametrize("at", [301, 302, 303, 304])
def test_paired_context_restarts_before_its_first_slot(at):
    """Context 3 paired (context 1 off) owns slots 1 and 3: it restarts before slot 1."""
    old, new = assemble(PAIRED.format(up=24, down=24)), assemble(PAIRED.format(up=56, down=12))
    ref = Machine(old)
    ref.run(TICKS, stop_when_idle=False)
    m = Machine(old)
    m.run(at, stop_when_idle=False)
    m.host_ctx_stop(0b1000)
    m.run(1, stop_when_idle=False)
    for a in range(256):
        if old.words[a] != new.words[a]:
            m.imem_write(a, new.words[a])
    m.host_ctx_start(0b1000)
    m.run(TICKS - at - 1, stop_when_idle=False)
    assert [c for c in m.commits if c["ctx"] == 0] == [c for c in ref.commits if c["ctx"] == 0]
    assert pins(m, 0) == pins(ref, 0)
    t_r = next(t for t in range(at + 1, at + 6) if (t + 1) % 4 == 1)
    base = (t_r + 1) & ~3
    fresh = Machine(new)
    fresh.run(TICKS, stop_when_idle=False)
    got = [(c["t"] - base, c["pc"], c["insn"]) for c in m.commits if c["ctx"] == 3 and c["t"] > at]
    want = [(c["t"], c["pc"], c["insn"]) for c in fresh.commits if c["ctx"] == 3]
    assert got and got == want[:len(got)]
    assert pins(m, 1)[base:] == pins(fresh, 1)[:TICKS - base]
