"""PS/2 host (firmware/ps2_host.asm, held-out round) against the keyboard model
escmodels.ps2kbd.Ps2Keyboard, written apart from the firmware from IBM's PS/2 Common
Interfaces technical reference (Keyboard/Auxiliary Device Controller timings) and the
101/102-key keyboard reference alone. The model is the keyboard: it makes the clock both ways,
sends scan codes, answers commands, gives up a byte on line contention, and checks the host's
request to send, its data timing, the line-control handshake and that it receives a byte
once past the 10th clock. What the host should report is decided here."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import Board, HostScript, Ps2Keyboard
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
SENT = 0x8000_0000
PAR_ERR, START_ERR, STOP_ERR, STUCK = 0x100, 0x200, 0x400, 0x800


def image(khz=25000, patch=()):
    src = (FW / "ps2_host.asm").read_text()
    for a, b in patch:
        assert src.count(a) == 1, a
        src = src.replace(a, b)
    return assemble(f".equ CLK_KHZ, {khz}\n" + src, base_dir=FW, name="ps2_host.asm")


def session(ms, khz=25000, cmds=(), patch=(), poll_us=20, **kw):
    """`cmds`: (t_ms, byte) the host sends; the run lasts `ms` milliseconds."""
    pushes = [(int(t * khz), 0, b) for t, b in cmds]
    host = HostScript(pushes, drain=[0], poll=max(1, poll_us * khz // 1000))
    kbd = Ps2Keyboard(0, 1, tick_ns=1e6 / khz, **kw)
    m = Machine(image(khz, patch), board=Board(kbd, host))
    host.machine = m
    m.run(int(ms * khz), stop_when_idle=False)
    kbd.finish()
    return kbd, m, host.received[0]


def clean(kbd, m):
    assert not m.ff.valid, m.ff
    assert not kbd.errors(), kbd.errors()[:5]


SCAN = [0x1C, 0xF0, 0x1C, 0xE0, 0x75, 0xE0, 0xF0, 0x75, 0x00, 0xFF]


@pytest.mark.parametrize("timing,seed", [("typ", 0), ("min", 0), ("max", 0), ("random", 1),
                                         ("random", 2)])
def test_receives_scan_codes(timing, seed):
    kbd, m, got = session(14, send=[(100, b) for b in SCAN], timing=timing, seed=seed)
    clean(kbd, m)
    assert got == SCAN
    assert all(s["complete"] for s in kbd.sent)


@pytest.mark.parametrize("khz", [4000, 50000])
def test_receives_at_other_clocks(khz):
    kbd, m, got = session(6, khz, send=[(100, b) for b in SCAN[:4]], timing="random", seed=3)
    clean(kbd, m)
    assert got == SCAN[:4]


@pytest.mark.parametrize("timing", ["typ", "min", "max", "random"])
def test_echo(timing):
    kbd, m, got = session(5, cmds=[(0.2, 0xEE)], timing=timing, seed=5)
    clean(kbd, m)
    assert got == [SENT, 0xEE]
    assert [r["byte"] for r in kbd.received] == [0xEE]
    assert kbd.received[0]["parity_ok"] and kbd.received[0]["acked"]
    assert kbd.received[0]["rts_low_us"] > 60


def test_reset_and_read_id():
    kbd, m, got = session(14, cmds=[(0.2, 0xFF), (6.0, 0xF2)], bat_ms=1.0)
    clean(kbd, m)
    assert got == [SENT, 0xFA, 0xAA, SENT, 0xFA, 0xAB, 0x83]


def test_set_indicators_with_value():
    kbd, m, got = session(8, cmds=[(0.2, 0xED), (3.0, 0x07)])
    clean(kbd, m)
    assert got == [SENT, 0xFA, SENT, 0xFA]
    assert [r["byte"] for r in kbd.received] == [0xED, 0x07]


def test_inhibits_while_the_mailbox_is_full():
    """The host reads its mailbox every 2 ms; the keyboard has 8 bytes to send. The program
    holds CLK low while it cannot take a byte; nothing is lost and nothing is out of order."""
    kbd, m, got = session(30, send=[(100, b) for b in SCAN[:8]], poll_us=2000)
    clean(kbd, m)
    assert got == SCAN[:8]
    assert kbd.inhibits


def test_scan_codes_and_a_command_interleaved():
    kbd, m, got = session(12, cmds=[(1.0, 0xEE)], send=[(100, 0x1C), (2500, 0x32)])
    clean(kbd, m)
    assert sorted(got) == sorted([0x1C, SENT, 0xEE, 0x32]) and got.index(SENT) < got.index(0xEE)


@pytest.mark.parametrize("fault,word", [({"parity": 0}, PAR_ERR | 0x1C),
                                        ({"stop": 0}, STOP_ERR | 0x1C)])
def test_bad_bytes_reported(fault, word):
    kbd, m, got = session(6, send=[(100, 0x1C), (100, 0x32)], faults=fault)
    assert not m.ff.valid
    assert got == [word, 0x32]


def test_stalled_byte_reported_then_resent():
    """The keyboard stops clocking after 5 clocks; the program inhibits, reports, and the
    keyboard sends the byte again."""
    kbd, m, got = session(8, send=[(100, 0x1C)], faults={"stall": (0, 5)})
    assert not m.ff.valid
    assert got[0] & STUCK and got[1:] == [0x1C]


@pytest.mark.parametrize("fault,word", [({"no_ack": 0}, SENT | 1), ({"ignore_rts": 0}, SENT | 2)])
def test_send_faults_reported(fault, word):
    kbd, m, got = session(25, cmds=[(0.2, 0xEE)], faults=fault)
    assert not m.ff.valid
    assert got[0] == word


# ------------------------------------------------------------ negative controls

def test_negative_control_rts_too_short():
    """CLK held low 40 us for the request to send (more than 60 us needed)."""
    patch = [("    EMIT  CLKM, 0, CLKM        ; CLK low: the device stops or does not start\n"
              "    ADDT  RTS_US * US\n",
              "    EMIT  CLKM, 0, CLKM        ; CLK low: the device stops or does not start\n"
              "    ADDT  40 * US\n")]
    kbd, m, got = session(5, cmds=[(0.2, 0xEE)], patch=patch)
    assert kbd.errors()


def test_negative_control_data_changed_with_clk_high():
    """Each host bit put on DATA 40 us after CLK falls: with the keyboard's shortest low
    phase (30 us) that is after CLK has risen."""
    patch = [("    SETT  r2\n    EMITX DATM, LSB, DATM\n",
              "    ADDI  r2, 40 * US\n    SETT  r2\n    EMITX DATM, LSB, DATM\n")]
    kbd, m, got = session(5, cmds=[(0.2, 0xEE)], patch=patch, timing="min")
    assert kbd.errors()


def test_negative_control_parity_wrong():
    """Even parity on the host's byte: the keyboard asks for it again (Resend)."""
    patch = [("    XOR   r1, r2               ; [8]: odd parity\n", "")]
    kbd, m, got = session(5, cmds=[(0.2, 0xEE)], patch=patch)
    assert not kbd.received[0]["parity_ok"]
    assert got[1:2] == [0xFE]


# ------------------------------------------------------------ certificate

@pytest.mark.parametrize("khz", [4000, 25000, 50000])
def test_certificate(khz):
    from escert import certify
    assert [c.status for c in certify(image(khz))] == ["CHECKED"]


def test_rts_60us_refused():
    from escasm import AsmError
    with pytest.raises(AsmError):
        assemble(".equ RTS_US, 60\n" + (FW / "ps2_host.asm").read_text(), base_dir=FW)
