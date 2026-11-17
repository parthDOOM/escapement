"""swim_host.asm (an STM8 SWIM host) against escmodels.swim.SwimTarget, an STM8's SWIM
written from ST's UM0470: the entry sequence, the synchronization frame, every host bit's
lengths, headers, parity and acknowledges, SWIM_CSR = 0xA0, and ROTF reads, at 33 and
50 MHz (the program's floor and default)."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import Board, HostScript
from escmodels.swim import SWIM_CSR, SwimTarget
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
UID = 0x4865                                    # STM8S unique ID: 12 bytes from here
MEM = {UID + k: v for k, v in enumerate([0x00, 0x5A, 0x00, 0x3C, 0x0B, 0x47, 0x30, 0x33,
                                          0x34, 0x35, 0x36, 0x37])}
MEM.update({0x8000 + k: v for k, v in enumerate([0x82, 0x00, 0x80, 0x83, 0x82, 0x00, 0x80, 0x87])})


def image(khz, src=None):
    if src is None:
        return assemble(f".equ swim_host.CLK_KHZ, {khz}\n" + '.include "swim_host.asm"\n', base_dir=FW)
    return assemble(f".equ CLK_KHZ, {khz}\n" + src, base_dir=FW)


def req(addr, n):
    return (n - 1) << 24 | addr


def run(reqs, khz=50000, src=None, target=True, ms=14):
    tgt = SwimTarget(0, tick_ns=1e6 / khz, mem=MEM)
    host = HostScript([(100 + int(k * 6 * khz), 0, w) for k, w in enumerate(reqs)], drain=[0], poll=300)
    devs = [tgt, host] if target else [host]
    m = Machine(image(khz, src), board=Board(*devs, pullups=0x01))
    host.machine = m
    m.run(int(ms * khz), stop_when_idle=False)
    assert not m.ff.valid
    return tgt, host.received[0]


@pytest.mark.parametrize("khz", [33000, 50000])
def test_activate_and_read(khz):
    tgt, got = run([req(UID, 6), req(0x8000, 4)], khz)
    assert tgt.violations == []
    assert tgt.mem[SWIM_CSR] == 0xA0
    assert got == [6] + [MEM[UID + k] for k in range(6)] + [4] + [MEM[0x8000 + k] for k in range(4)]
    # one WOTF to SWIM_CSR, then two ROTFs: commands and parameters as the target decoded them
    assert tgt.host_packets[:7] == [("cmd", 2), ("byte", 1), ("byte", 0x00), ("byte", 0x7F),
                                    ("byte", 0x80), ("byte", 0xA0), ("cmd", 1)]


def test_eight_bytes_and_a_one_byte_read():
    tgt, got = run([req(UID, 8), req(UID + 11, 1)])
    assert tgt.violations == [] and got == [8] + [MEM[UID + k] for k in range(8)] + [1, MEM[UID + 11]]


def test_no_target_reports_a_failure_and_tries_again():
    _, got = run([req(UID, 2), req(UID, 2)], target=False, ms=24)   # entry and a 2 ms wait, twice
    assert got == [0xFFFFFFFF, 0xFFFFFFFF]


def _planted(old, new):
    src = (FW / "swim_host.asm").read_text()
    assert old in src
    return src.replace(old, new)


def test_planted_wrong_command_parity_is_nacked():
    bad = _planted("    MOVHI r0, 0x1800                    ; ROTF 001, parity 1: 0 001 1 << 27",
                   "    MOVHI r0, 0x1000")
    tgt, got = run([req(UID, 2)], src=bad)
    assert any("parity" in v for v in tgt.violations) and got == [0xFFFFFFFF]


def test_planted_skipped_entry_is_not_activated():
    bad = _planted("    LDLC  4\ne1:", "    LDLC  2\ne1:")          # two 1 kHz pulses, not four
    tgt, got = run([req(UID, 2)], src=bad)
    assert tgt.state == "off" and got == [0xFFFFFFFF]


def test_planted_long_host_bit_is_flagged():
    bad = _planted(".equ SHORT, (CLK_KHZ * 250 + 999999) // 1000000", ".equ SHORT, CLK_KHZ * 600 // 1000000")
    tgt, _ = run([req(UID, 2)], src=bad)
    assert any("a host bit low for" in v for v in tgt.violations)
