"""escert: timing certificates, checked against the ISS (the reference model).

Soundness is not argued, it is tested: every CHECKED certificate is falsifiable by the
chip's own `min_lead` telemetry, so (1) shipped firmware must certify with a bound no
greater than what the ISS measures, and (2) on random programs (the co-simulation
generator: loops, branches, raw words, deliberate faults), whenever escert says CHECKED
the ISS run must not fault with ADMIT in that context and must measure min_lead >= bound.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from escasm import AsmError, assemble, assemble_file
from escert import certify
from escmodels import (Board, CanBus, I2cController, I2cTarget, JtagTap, OneWireDevice,
                       Ps2Host, SpiTarget, SwdTarget, UartRx, UartTx)
from escmodels.spi import SpiController
from esciss import Machine

REPO = Path(__file__).resolve().parents[2]
FW = REPO / "firmware"
sys.path.insert(0, str(REPO / "test" / "cosim"))
from progen import gen_program, make_board  # noqa: E402

ADMIT = 3
TPB = 25e6 / 115200


def i2c_cmd(addr7, reg, data=0, write=False, read=False):
    return (addr7 << 24) | (reg << 16) | (data << 8) | (1 if write else 0) | (2 if read else 0)


class Pusher:
    """Pushes host words into a context's RX mailbox at given ticks."""

    def __init__(self, pushes):
        self.pushes = sorted(pushes)

    def drive(self, m, ticks):
        t = 0
        for at, ctx, w in self.pushes + [(ticks, None, None)]:
            if at > t:
                m.run(at - t, stop_when_idle=False)
                t = at
            if ctx is not None:
                m.host_rx_push(ctx, w)


# firmware -> (board factory, host pushes, ticks, tape prep)
SHIPPED = {
    "uart_tx.asm": (lambda: Board(UartRx(0, TPB)), [], 20_000, None),
    "uart_rx.asm": (lambda: Board(UartTx(1, TPB, b"Esc", start=300)), [], 12_000, None),
    "i2c_ctrl.asm": (lambda: Board(I2cTarget(2, 3, 0x42, {5: 0}, mode="fast", stretch_ticks=37)),
                     [(50, 0, i2c_cmd(0x42, 5, 0xA5, write=True)),
                      (4000, 0, i2c_cmd(0x42, 5, read=True))], 9_000, None),
    "spi_ctrl.asm": (lambda: Board(SpiTarget(4, 5, 6, 7, mode=3, reply=b"\x3C\xC3")),
                     [(20, 2, 3), (100, 2, 0x1A5), (110, 2, 0x05A)], 2_500, None),
    "ws2812.asm": (lambda: Board(), [(30, 3, 0x102030), (40, 3, 0x010203), (2000, 3, 4)],
                   9_000, None),
    "onewire.asm": (lambda: Board(OneWireDevice(1, bytes(8))), [(30, 3, 0x80000033)], 30_000, None),
    "i2c_eeprom.asm": (lambda: Board(I2cController(2, 3, [("write", 0x50, 0x10, [1, 2]),
                                                          ("read", 0x50, 0x10, 2)], mode="fast")),
                       [], 40_000, None),
    "ps2_device.asm": (lambda: Board(Ps2Host(4, 5)), [(50, 1, 0x1C), (70, 1, 0xF0)], 60_000, None),
    "jtag_idcode.asm": (lambda: Board(JtagTap(4, 5, 6, 7)), [(20, 2, 0), (40, 2, 0)], 9_000, None),
    "can_tx.asm": (lambda: Board(CanBus(0, 1, 100)),
                   [(100, 0, (0x555 << 16) | 8), (150, 0, 0xFFFFFFFF), (200, 0, 0)], 40_000, None),
    "nec_tx.asm": (lambda: Board(), [(20, 0, 0x5AA5)], 30_000, None),
    "swd_idcode.asm": (lambda: Board(SwdTarget(4, 5, waits=2)), [(20, 2, 0x80000004), (9000, 2, 0x1C)],
                       30_000, None),
    "spi_target.asm": (lambda: Board(SpiController(4, 5, 6, 7, [([0x02, 0x10, 0xDE, 0xAD], None),
                                                               ([0x03, 0x10, 0, 0], None)],
                                                   t_high=30, t_low=30, t_css=25, t_idle=100)),
                       [], 6_000, None),
}


@pytest.mark.parametrize("fw", sorted(SHIPPED))
def test_shipped_firmware_is_certified_and_telemetry_agrees(fw):
    image = assemble_file(FW / fw)
    certs = certify(image)
    board_f, pushes, ticks, _ = SHIPPED[fw]
    m = Machine(image, board=board_f())
    Pusher(pushes).drive(m, ticks)
    for c in certs:
        assert c.status == "CHECKED", str(c)
        assert c.min_lead >= 1
        assert m.ctx[c.ctx].min_lead >= c.min_lead, (str(c), m.ctx[c.ctx].min_lead)


def test_concurrent_image_certifies_every_context():
    certs = certify(assemble_file(FW / "combo_uart_i2c.asm"))
    assert [c.status for c in certs] == ["CHECKED"] * 3


def test_historic_i2c_timeout_bug_is_refuted_with_a_witness():
    """The stuck-bus handler once used ADDI r1, 8 before its release EMIT (lead -4):
    escert proved the violation and the ISS reproduced it as ADMIT/PAST."""
    src = (FW / "i2c_ctrl.asm").read_text().replace("ADDI  r1, 14", "ADDI  r1, 8")
    certs = certify(assemble(src, base_dir=FW), witness_trials=20)
    assert certs[0].status == "REFUTED"
    assert certs[0].findings[0].kind == "PAST"
    w = certs[0].witness
    assert w["subcode"] == 3 and w["pc"] == certs[0].findings[0].pc


def test_historic_ps2_back_to_back_bug_is_refuted():
    """ps2_device.asm once started a new frame under the previous frame's queued events
    (no wait after the stop bit): escert flags ORDER at the frame start and the burst
    witness search reproduces the ADMIT fault on the ISS."""
    src = (FW / "ps2_device.asm").read_text()
    src = src.replace("    ADDT  1250", "    ; ADDT  1250").replace("    WAITT     ", "    ; WAITT   ")
    c = certify(assemble(src, base_dir=FW), witness_trials=10)[0]
    assert c.status == "REFUTED" and c.witness["pc"] == c.findings[0].pc, str(c)


def test_historic_jtag_reanchor_bug_is_refuted():
    """jtag_idcode.asm once re-anchored the next request on RDTIME while its last TCK
    events were still queued: ORDER, reproduced by the burst witness search."""
    src = (FW / "jtag_idcode.asm").read_text().replace("    WAITT     ", "    ; WAITT   ")
    c = certify(assemble(src, base_dir=FW), witness_trials=10)[0]
    assert c.status == "REFUTED" and c.findings[0].kind == "ORDER", str(c)


@pytest.mark.parametrize("src,kind", [
    (".entry 0, s\n.own 0, 0\ns: ADDT 5\n EMIT 1, 1, 1\n HALT\n", "PAST"),          # lead 2? no: 5-7
    (".entry 0, s\n.own 0, 0\ns: ADDT 40\n EMIT 1, 1, 1\n EMIT 1, 0, 1\n HALT\n", "ORDER"),
    (".entry 0, s\ns: ADDT 40\n EXPECT 1, 1, HOLD, 20\n ADDT 10\n EXPECT 1, 1, HOLD, 5\n HALT\n",
     "OVERLAP"),
])
def test_obvious_violations_are_found(src, kind):
    c = certify(assemble(src))[0]
    assert c.status == "POSSIBLE" and c.findings[0].kind == kind, str(c)


def test_input_dependent_wait_then_reanchor_is_checked():
    # after an unbounded wait the deadline is re-anchored on RDTIME: provable
    src = """
    .entry 0, s
    .own 0, 0
    s: POP r0
       RDTIME r1
       ADDI r1, 14
       SETT r1
       EMIT 1, 1, 1
       JMP s
    """
    c = certify(assemble(src))[0]
    assert c.status == "CHECKED" and c.min_lead == 2, str(c)


def test_accumulating_deadlines_after_unbounded_wait_are_refuted():
    # ADDT-only timing across a POP: the queue's deadline can fall behind real time
    src = """
    .entry 0, s
    .own 0, 0
    s: ADDT 100
    l: POP r0
       EMIT 1, 1, 1
       ADDT 100
       JMP l
    """
    c = certify(assemble(src), witness_trials=10)[0]
    assert c.status in ("REFUTED", "NOT_PROVEN") and c.findings[0].kind == "PAST", str(c)


SEEDS = int(os.environ.get("ESCERT_SEEDS", "300"))


def test_random_programs_checked_implies_no_admit_fault():
    checked = proved_contexts = 0
    for seed in range(1, SEEDS + 1):
        try:
            image = assemble(gen_program(seed))
        except AsmError:
            continue
        certs = [c for c in certify(image) if c.status == "CHECKED" and c.events]
        if not certs:
            continue
        checked += 1
        m = Machine(image, board=make_board(seed))
        m.run(4000)
        for c in certs:
            proved_contexts += 1
            f = m.ff
            assert not (f.valid and f.ctx == c.ctx and f.cause == ADMIT), (seed, str(c))
            # telemetry is only comparable while the context really admitted events
            if m.ctx[c.ctx].min_lead != 0xFFFF:
                assert m.ctx[c.ctx].min_lead >= c.min_lead, (seed, str(c), m.ctx[c.ctx].min_lead)
    assert proved_contexts > 20, (checked, proved_contexts)      # the test is not vacuous
