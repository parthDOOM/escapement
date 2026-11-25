"""Microwire controller for a 93xx46 EEPROM (firmware/microwire_93c46.asm, held-out round)
against the EEPROM model escmodels.eeprom93.Eeprom93, written apart from the firmware from
Microchip DS21749H alone. The model checks the controller's timing (Table 1-2, A1-A8), the
instruction framing, instructions sent while the part is busy and DO contention, and
models DO (TPD, TCZ, TSV at their maxima) and the self-timed cycle. What the memory should
hold is decided here."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import Board, Eeprom93, HostScript
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
READ, WRITE, ERASE, ERAL, WRAL, EWEN, EWDS = range(1, 8)


def cmd(op, addr=0, data=0):
    return op << 28 | addr << 16 | data


def image(khz=25000, org16=1, sk=None, tout_ms=None, patch=()):
    src = (FW / "microwire_93c46.asm").read_text()
    for a, b in patch:
        assert src.count(a) == 1, a
        src = src.replace(a, b)
    head = f".equ CLK_KHZ, {khz}\n.equ ORG16, {org16}\n"
    head += f".equ SK_KHZ, {sk}\n" if sk else ""
    head += f".equ TOUT_MS, {tout_ms}\n" if tout_ms else ""
    return assemble(head + src, base_dir=FW, name="microwire_93c46.asm")


def session(cmds, khz=25000, org16=1, sk=None, twc_us=50, tout_ms=None, patch=(), init=None,
            grade="LC", gap_us=None, board_out=False):
    """Push the host commands one at a time, each after the reply to the one before could
    have come (programming waits up to twc_us). Returns the model, machine and replies."""
    gap = gap_us if gap_us is not None else 200 + 2 * twc_us
    pushes = [(100 + i * gap * khz // 1000, 0, c) for i, c in enumerate(cmds)]
    host = HostScript(pushes, drain=[0], poll=khz // 20)
    ee = Eeprom93(tick_ns=1e6 / khz, org16=bool(org16), grade=grade, twc_us=twc_us, init=init)
    board = Board(ee, host)
    m = Machine(image(khz, org16, sk, tout_ms, patch), board=board)
    if board_out:
        ee.board = board                 # (for a judge that reads the pin levels)
    host.machine = m
    m.run(100 + (len(cmds) + 1) * gap * khz // 1000, stop_when_idle=False)
    ee.finish()
    return ee, m, host.received[0]


def clean(ee, m):
    assert not m.ff.valid, m.ff
    assert not ee.errors(), ee.errors()[:5]


def executed(ee):
    return [(o["op"], o["addr"]) for o in ee.ops if o["executed"]]


@pytest.mark.parametrize("khz,org16,sk", [(25000, 1, None), (25000, 0, None), (50000, 1, 1562),
                                          (16000, 0, None), (33000, 1, 1000)])
def test_write_and_read_back(khz, org16, sk):
    """EWEN, three WRITEs, READ them back (and an untouched cell: erased, all ones), EWDS:
    replies 1 for every programming step, data and the dummy zero right."""
    full = 0xFFFF if org16 else 0xFF
    vals = [0x1234 & full, 0xA5C3 & full, 0x0001]
    addrs = [0, 37, (64 if org16 else 128) - 1]
    cmds = [cmd(EWEN)] + [cmd(WRITE, a, v) for a, v in zip(addrs, vals)]
    cmds += [cmd(READ, a) for a in addrs] + [cmd(READ, 5), cmd(EWDS)]
    ee, m, got = session(cmds, khz, org16, sk)
    clean(ee, m)
    assert got == [1, 1, 1, 1] + vals + [full, 1]
    for a, v in zip(addrs, vals):
        assert ee.mem[a] == v
    assert [op for op, _ in executed(ee)] == ["EWEN", "WRITE", "WRITE", "WRITE",
                                               "READ", "READ", "READ", "READ", "EWDS"]


def test_write_protected_until_ewen_and_after_ewds():
    """The part powers up write-disabled (2.3): a WRITE before EWEN and after EWDS is not
    executed; the controller still sees ready."""
    cmds = [cmd(WRITE, 3, 0x1111), cmd(READ, 3), cmd(EWEN), cmd(WRITE, 3, 0x2222), cmd(EWDS),
            cmd(WRITE, 3, 0x3333), cmd(READ, 3)]
    ee, m, got = session(cmds)
    clean(ee, m)
    assert got == [1, 0xFFFF, 1, 1, 1, 1, 0x2222]
    assert [o["executed"] for o in ee.ops if o["op"] == "WRITE"] == [False, True, False]


@pytest.mark.parametrize("org16", [1, 0])
def test_erase_eral_wral(org16):
    full = 0xFFFF if org16 else 0xFF
    n = 64 if org16 else 128
    init = [(i * 7) & full for i in range(n)]
    cmds = [cmd(EWEN), cmd(ERASE, 9), cmd(READ, 9), cmd(READ, 10), cmd(WRAL, 0, 0x5A5A & full),
            cmd(READ, 0), cmd(READ, n - 1), cmd(ERAL), cmd(READ, 20)]
    ee, m, got = session(cmds, org16=org16, init=init)
    clean(ee, m)
    assert got == [1, 1, full, init[10], 1, 0x5A5A & full, 0x5A5A & full, 1, full]
    assert ee.mem == [full] * n


@pytest.mark.parametrize("grade", ["C", "AA"])
def test_other_grades(grade):
    """A 93C part (cycle started by the rising edge before the last bit) and an AA part."""
    ee, m, got = session([cmd(EWEN), cmd(WRITE, 1, 0xBEEF), cmd(READ, 1)], grade=grade)
    clean(ee, m)
    assert got == [1, 1, 0xBEEF]


def test_busy_poll_waits_for_the_cycle():
    """A programming cycle of 3 ms: the reply comes after it, not before, and the next
    instruction is not sent while the part is busy (the model flags that)."""
    ee, m, got = session([cmd(EWEN), cmd(WRITE, 2, 0x0F0F), cmd(READ, 2)], twc_us=3000)
    clean(ee, m)
    assert got == [1, 1, 0x0F0F]


def test_timeout_reported():
    """A cycle longer than the controller waits (TOUT_MS 1, the part 2 ms): reply 0."""
    ee, m, got = session([cmd(EWEN), cmd(WRITE, 2, 0x0F0F)], twc_us=2000, tout_ms=1,
                         gap_us=2600)
    assert not m.ff.valid
    assert got == [1, 0]


# ------------------------------------------------------------ negative controls

def test_negative_control_di_setup_too_short():
    """DI changed one tick (40 ns) before the rising edge: below A7's 100 ns."""
    patch = [("    ADDT  1\n    EMITX DI, MSB, DI           ; T + 1: DI\n    ADDT  H - 1\n",
              "    ADDT  H - 1\n    EMITX DI, MSB, DI           ; T + 1: DI\n    ADDT  1\n")]
    ee, m, got = session([cmd(EWEN), cmd(WRITE, 1, 0x1234)], patch=patch)
    assert ee.errors()


def test_negative_control_clock_high_too_short():
    """CLK high for 3 ticks (120 ns) while sending: below A2's 250 ns."""
    rise = "    EMIT  SK, SK, SK            ; T + H: CLK rises: the EEPROM takes DI\n"
    patch = [(rise + "    ADDT  H\n", rise + "    ADDT  3\n")]
    ee, m, got = session([cmd(EWEN), cmd(READ, 1)], patch=patch)
    assert ee.errors()


def test_negative_control_sampling_before_tpd():
    """DO sampled 1 tick after the rising edge, before A9's 250 ns: the data read back is
    wrong."""
    patch = [("    EMIT  SK, SK, SK            ; T + H: CLK rises: the next data bit comes out\n"
              "    ADDT  H - 2\n    SAMPLE DO_PIN, MSB          ; T + 2H - 2\n    ADDT  2\n",
              "    EMIT  SK, SK, SK            ; T + H: CLK rises: the next data bit comes out\n"
              "    ADDT  1\n    SAMPLE DO_PIN, MSB          ; T + 2H - 2\n    ADDT  H - 1\n")]
    ee, m, got = session([cmd(EWEN), cmd(WRITE, 1, 0x1234), cmd(READ, 1)], patch=patch)
    assert got[:2] == [1, 1] and got[2] != 0x1234


# ------------------------------------------------------------ certificate

@pytest.mark.parametrize("khz", [16000, 25000, 50000])
def test_certificate(khz):
    from escert import certify
    certs = certify(image(khz))
    assert [c.status for c in certs] == ["CHECKED"]
    ee, m, got = session([cmd(EWEN), cmd(WRITE, 1, 0x1234), cmd(READ, 1)], khz)
    assert m.ctx[0].min_lead >= certs[0].min_lead


def test_slow_clock_refused():
    from escasm import AsmError
    with pytest.raises(AsmError):
        image(12000)
