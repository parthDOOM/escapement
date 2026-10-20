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


# ----------------------------------------------------------------------------- line unit (v1.4)


def test_usb_hid_replies_are_certified_exact_and_on_time():
    """Every reply's deadline is the end of the host's packet + 72, on time on every path
    where the host keeps to USB timing; every GETL waiting on a reception starts waiting
    well before the shortest packet the firmware answers (an IN token, 34 bit times at
    the slowest legal host, about 536 ticks) could end."""
    import test_usb_hid as T
    image = assemble_file(FW / "usb_hid.asm")
    c = certify(image)[0]
    assert c.status == "CHECKED" and c.replies_on_time, str(c)
    assert len(c.sends) == 3 and all(x.turn == (72, 72) for x in c.sends.values()), str(c)
    assert min(x.lead for x in c.sends.values()) >= 4
    assert c.getls and max(c.getls.values()) < 536, str(c)
    # falsifiable: on the ISS, every SEND commits at least its certified lead early
    r = T.Run(image, lambda h, rr: T.enumerate_device(h, rr.out)).check()
    seen = {}
    for cm in r.m.commits:
        if cm["ctx"] == 0 and cm["pc"] in c.sends and cm["outcome"] == "C":
            assert cm["post"]["z"] == 1                       # nothing was dropped
            lead = (cm["post"]["tmr"] - cm["t"]) & 0xFFFFFFFF
            seen[cm["pc"]] = min(seen.get(cm["pc"], 1 << 32), lead)
    assert seen and all(seen[pc] >= c.sends[pc].lead for pc in seen), (seen, str(c))
    assert r.m.ctx[0].min_lead >= c.min_lead


def test_a_short_turnaround_is_reported_as_a_possible_drop():
    src = (FW / "usb_hid.asm").read_text().replace(".equ TURN, 4 * P + 8", ".equ TURN, 3 * P")
    c = certify(assemble(src, base_dir=FW))[0]
    assert c.status == "CHECKED" and not c.replies_on_time, str(c)


def test_redispatching_an_old_packet_is_reported():
    """Back to GETL with the receiver not re-armed: GETL returns the old packet's end and a
    reply from it would be late. (usb_hid did this after a corrupt SETUP stage.)"""
    src = (FW / "usb_hid.asm").read_text().replace(
        "    JNZ   arm                     ; corrupt", "    JNZ   next                    ; corrupt")
    c = certify(assemble(src, base_dir=FW))[0]
    assert not c.replies_on_time, str(c)


LU_PRE = """
.entry 0, s
.own 0, 0
.own 1, 0
.own 2, 0
.tape 0xF0, {w0:#x}, {w1:#x}, {w2:#x}
.tape 0xE0, 0x300000F0, 0x200010D0, {send:#x}, 0x100001C0
s: LDT r0, r3, 0xE0
   LINE r0
"""
from test_line import NRZ  # noqa: E402
NRZ_W0, NRZ_W1 = NRZ[0], NRZ[1]              # pin 0 out and in, NRZ, 16 ticks per bit
CRC16_W2 = 0x8005 | (1 << 16)                # CRC-16 polynomial, all-ones start


def lu_src(body, send=0x0800):
    """CONFIG commits at 7; words: E0 CONFIG, E1 a 16-bit CRC job on D0, E2 a packet,
    E3 a 1-byte reception into C0."""
    return LU_PRE.format(w0=NRZ_W0, w1=NRZ_W1, w2=CRC16_W2, send=send) + body


@pytest.mark.parametrize("body,kind", [
    (" ADDT 100\n EMIT 4, 4, 4\n ADDT 0xFFFFFFF0\n LDT r0, r3, 0xE2\n SEND r0\n HALT\n", "ORDER"),
    (" ADDT 100\n EMIT 1, 1, 1\n HALT\n", "OWNER"),                 # pin 0 is claimed
])
def test_line_unit_admission_faults_are_found(body, kind):
    src = lu_src(body).replace("ADDT 0xFFFFFFF0", "MOVI r2, 0xFFF0\n MOVHI r2, 0xFFFF\n ADDTR r2")
    c = certify(assemble(src))[0]
    assert c.status == "POSSIBLE" and any(f.kind == kind for f in c.findings), str(c)


def test_two_contexts_on_the_line_unit_are_flagged():
    src = lu_src(" HALT\n") + ".entry 1, t\nt: ADDT 0xFFFFFF\n GETL r1\n HALT\n"
    c = certify(assemble(src))
    assert any(f.kind == "OWNER" for x in c for f in x.findings), [str(x) for x in c]


def test_waiting_for_a_crc_job_is_timed_exactly():
    """A LINE waits for the CRC job before it; an EMIT timed from before the job lands
    after that wait. The program has no inputs, so the ISS is exact: escert must prove
    the first deadline the ISS meets and refuse the one before it."""
    body = (" ADDT 0xFFFFFF\n GETL r1\n ADDT {k}\n LDT r0, r3, 0xE1\n LINE r0\n"
            " LDT r0, r3, 0xE3\n LINE r0\n EMIT 4, 4, 4\n HALT\n")

    def iss_faults(k):
        m = Machine(assemble(lu_src(body.format(k=k))))
        m.run(300)
        return bool(m.ff.valid)

    k = next(k for k in range(1, 80) if not iss_faults(k))
    assert iss_faults(k - 1)
    assert certify(assemble(lu_src(body.format(k=k))))[0].status == "CHECKED"
    c = certify(assemble(lu_src(body.format(k=k - 1))))[0]
    assert c.status == "POSSIBLE" and c.findings[0].kind == "PAST", str(c)


def test_the_path_after_a_dropped_send_is_analysed():
    """A late SEND is dropped with Z = 0; what the program does then must be checked too."""
    src = lu_src(" ADDT 0xFFFFFF\n GETL r1\n LDT r0, r3, 0xE2\n SEND r0\n JNZ late\n HALT\n"
                 "late:\n EMIT 4, 4, 4\n HALT\n")
    c = certify(assemble(src))[0]
    assert c.status == "POSSIBLE" and c.findings[0].kind == "PAST", str(c)
    m = Machine(assemble(src))
    m.run(300)
    assert m.ff.valid and m.ff.subcode == 3


def test_config_and_crc_jobs_are_timed_exactly():
    """A CRC job of nb bits ends at c + nb; GETL completes in the first slot after, 1 to 4
    ticks later (here 4: c + 16 is a slot), and the EMIT commits 8 ticks after GETL. So
    e + 13 is the earliest deadline escert can prove, and on the ISS it is exactly met."""
    body = (" ADDT 0xFFFFFF\n GETL r1\n LDT r0, r3, 0xE1\n LINE r0\n ADDT 0xFFFFFF\n GETL r1\n"
            " ADDT {k}\n EMIT 4, 4, 4\n HALT\n")
    ok = certify(assemble(lu_src(body.format(k=13))))[0]
    assert ok.status == "CHECKED" and ok.min_lead == 1, str(ok)
    bad = certify(assemble(lu_src(body.format(k=12))))[0]
    assert bad.status == "POSSIBLE" and bad.findings[0].kind == "PAST", str(bad)
    for k, faults in ((13, False), (12, True)):
        m = Machine(assemble(lu_src(body.format(k=k))))
        m.run(400)
        assert bool(m.ff.valid) == faults
    assert m.ff.subcode == 3                                    # PAST: the bound is tight


def lu_program(seed):
    """A random program on the line unit: CONFIG, receptions, CRC jobs and packets, GETL
    with random timeouts, branches on its flag, and EMITs timed from what GETL returns
    (an EMIT faults when late, so the GETL model is checked exactly)."""
    rng = __import__("random").Random(seed)
    p = rng.choice((8, 12, 16, 25, 40))
    w1 = p | ((p // 2) << 20)
    recv = (1 << 28) | (rng.randrange(1, 4) << 8) | 0xC0
    crc = (2 << 28) | (rng.randrange(1, 70) << 8) | 0xD0
    send = (rng.randrange(0, 60) << 8) | 0xD0
    lines = [".entry 0, s", ".own 0, 0", ".own 2, 0", ".own 3, 0",
             f".tape 0xF0, {NRZ_W0:#x}, {w1:#x}, {CRC16_W2:#x}",
             f".tape 0xE0, 0x300000F0, {recv:#x}, {crc:#x}, {send:#x}",
             ".tape 0xD0, 0x12345678, 0x9ABCDEF0, 0x0F0F0F0F",
             "s: LDT r0, r3, 0xE0", " LINE r0"]
    n = rng.randrange(6, 18)
    for i in range(n):
        lines.append(f"L{i}:")
        k = rng.randrange(10)
        if k == 0:
            lines += [" LDT r0, r3, 0xE0", " LINE r0"]
        elif k in (1, 2):
            lines += [" LDT r0, r3, 0xE1", " LINE r0"]
        elif k == 3:
            lines += [" LDT r0, r3, 0xE2", " LINE r0"]
        elif k in (4, 5):
            lines += [f" ADDT {rng.choice((0, 3, 50, 400, 0xFFFFFF))}", " GETL r1",
                      f" {rng.choice(('JZ', 'JNZ'))} L{rng.randrange(i, n)}"]
        elif k == 6:
            lines += [f" ADDT {rng.randrange(1, 80)}", " EMIT 4, 4, 4"]
        elif k == 7:
            lines += [f" ADDT {rng.randrange(0, 120)}", " LDT r0, r3, 0xE3", " SEND r0"]
        elif k == 8:
            lines += [" STT r1, r3, 0xB0", " LDT r2, r3, 0xB0", " NOP"]
        else:
            lines += [" RDTIME r2", f" ADDI r2, {rng.randrange(1, 40)}", " SETT r2",
                      " EMIT 8, 8, 8"]
    lines.append(f"L{n}:")
    lines.append(" JMP s" if rng.random() < 0.6 else " HALT")
    return "\n".join(lines) + "\n"


def lu_board(seed):
    rng = __import__("random").Random(seed ^ 0x5A5A)
    period = rng.choice((3, 9, 16, 30, 70, 400))
    phase = rng.randrange(1000)

    def board(t, out, oe):
        line = ((t + phase) // period) & 1 if (t // 997) % 3 else 1     # bursts, then idle
        if oe & 1:
            line = out & 1
        return 0xFE | line
    return board


LU_SEEDS = int(os.environ.get("ESCERT_LU_SEEDS", "300"))


def test_random_line_unit_programs_checked_implies_no_admit_fault():
    proved = timed = 0
    for seed in range(1, LU_SEEDS + 1):
        image = assemble(lu_program(seed))
        c = certify(image)[0]
        if c.status != "CHECKED" or not c.events:
            continue
        proved += 1
        m = Machine(image, board=lu_board(seed))
        m.run(8000, stop_when_idle=False)
        f = m.ff
        assert not (f.valid and f.ctx == 0 and f.cause == ADMIT), (seed, str(c), f.subcode, f.pc)
        if m.ctx[0].min_lead != 0xFFFF:
            timed += 1
            assert m.ctx[0].min_lead >= c.min_lead, (seed, str(c), m.ctx[0].min_lead)
    assert proved > 30 and timed > 20, (proved, timed)
