"""Tests for the v1.4 line unit (docs/11-SEMANTICS.md §6.6, §7.7) on the ISS.

Expected values come from the semantics document by hand (ticks), from the published CRC
catalogue check values (reveng), and from `escmodels.usb`, a USB model written separately
from the line unit. Context 0 runs the programs; it fetches in cycles 0 mod 4, so its
commits are at ticks 3 mod 4. The configuration block sits in tape words 0xF0..0xF2.
"""

from __future__ import annotations

import random

import pytest

from escasm import assemble
from esciss import Machine
from escmodels import usb
from escmodels.usb import J, K, SE0

CFG_AT = 0xF0
DATA_AT = 0x10


# ----------------------------------------------------------------------------- helpers


def w0(d=1, n=0, r=0, diff=1, nrzi=1, lsb=1, N=6, any_=0, eop=2, idle_drive=0,
       sync_skip=1, tx_pins=1, prefix=0x80):
    return (d | (n << 3) | (r << 6) | (diff << 9) | (nrzi << 10) | (lsb << 11) | (N << 12)
            | (any_ << 15) | (eop << 16) | (idle_drive << 18) | (sync_skip << 19)
            | (tx_pins << 20) | (prefix << 24))


def w1(P=16, frac=0, S=8):
    return P | (frac << 12) | (S << 20)


def w2(poly=0x8005, init_ones=1, inv=1, hdr=1, fall=0, abort=0, idle_min=1):
    return (poly | (init_ones << 16) | (inv << 17) | (hdr << 18) | (fall << 19)
            | (abort << 20) | (idle_min << 21))


USB = (w0(), w1(), w2())                                    # D- = pin 1, D+ = pin 0
NRZ = (w0(d=0, diff=0, nrzi=0, N=0, eop=0, idle_drive=1, sync_skip=0, prefix=0),
       w1(), w2(poly=0, hdr=0))                             # plain NRZ on pin 0, no coding


def li(r: int, v: int) -> str:
    return f" MOVI r{r}, {v & 0xFFFF}\n MOVHI r{r}, {(v >> 16) & 0xFFFF}\n"


def program(code: str, cfg=USB, tape=None, own=(0, 1), configure=True, pre="") -> str:
    t = {} if cfg is None else {CFG_AT: cfg[0], CFG_AT + 1: cfg[1], CFG_AT + 2: cfg[2]}
    t.update(tape or {})
    out = [".entry 0, main"] + [f".own {p}, 0" for p in own]
    out += [f".tape {a}, {v:#x}" for a, v in sorted(t.items())]
    out += [pre, "main:"]
    if configure:
        # LINE CONFIG commits at 11; reads at 11, 15, 19; complete at E_20 (e = 20). GETL
        # waits at 19 and completes at 23 with tmr = 20.
        out += [li(0, (3 << 28) | CFG_AT), " LINE r0", " ADDT 0xFFFFFF", " GETL r1"]
    out.append(code)
    return "\n".join(out)


def run(src: str, ticks=4000, board=None, **kw) -> Machine:
    m = Machine(assemble(src), board=board)
    m.run(ticks, **kw)
    return m


def commit_at(m: Machine, t: int) -> dict:
    return next(c for c in m.commits if c["t"] == t)


def outcomes(m: Machine, pc: int) -> list[tuple[int, str]]:
    return [(c["t"], c["outcome"]) for c in m.commits if c["pc"] == pc and c["ctx"] == 0]


def ff(m: Machine) -> dict:
    return m.final_state()["first_fault"]


def bytes_to_words(data: bytes, lsb=True) -> dict[int, int]:
    """Tape words holding `data` as a stream: little-endian bytes (lsb) or big-endian."""
    out = {}
    for i in range(0, len(data), 4):
        chunk = data[i:i + 4].ljust(4, b"\0")
        out[DATA_AT + i // 4] = int.from_bytes(chunk, "little" if lsb else "big")
    return out


def reflect(v: int, n: int) -> int:
    return int(f"{v:0{n}b}"[::-1], 2)


def status(job=0, cause=0, count=0, crc=0, busy=0):
    return (busy << 31) | (job << 28) | (cause << 25) | (count << 16) | crc


def expect_states(m: Machine, first: int, states: list[str], period=16):
    """The chip alone drives `states`, state i during ticks first + i*period .. +period-1,
    J (released) right before and right after."""
    got = usb.chip_states(m.pins)
    want = [s for s in states for _ in range(period)]
    assert got[first - 1] == J
    assert got[first:first + len(want)] == want
    assert got[first + len(want)] == J


# ----------------------------------------------------------------------------- CONFIG


def test_config_timing_and_status():
    m = run(program(" HALT"))
    assert outcomes(m, 4) == [(19, "W"), (23, "C")]          # GETL waits for E_20
    g = commit_at(m, 23)["post"]
    assert g["tmr"] == 20 and g["z"] == 1
    assert g["r1"] == status(job=3, crc=0xFFFF)              # CONFIG, OK, crc = start
    assert m.lu.owner == 0 and m.lu.configured and m.lu.claimed == 0b11
    assert m.pins[21]["uio_oe"] == 0                         # USB idle state: released
    assert ff(m)["valid"] == 0


def test_config_idle_drive_drives_at_completion():
    m = run(program(" HALT", cfg=NRZ, own=(0,)))
    assert m.pins[20]["uio_oe"] & 1 == 0
    assert m.pins[21]["uio_oe"] & 1 == 1 and m.pins[21]["uio_out"] & 1 == 1   # L = 1, driven


def test_config_reads_make_owner_ldt_wait():
    # LDT right after LINE commits at 15 = c + 4 and at 19 = c + 8, both scheduled reads
    src = "\n".join([".entry 0, main", ".own 0, 0", ".own 1, 0",
                     f".tape {CFG_AT}, {USB[0]:#x}, {USB[1]:#x}, {USB[2]:#x}", "main:",
                     li(0, (3 << 28) | CFG_AT), " LINE r0", " LDT r2, r3, 5", " HALT"])
    m = run(src)
    assert outcomes(m, 3) == [(15, "W"), (19, "W"), (23, "C")]


@pytest.mark.parametrize("cfg,sub", [
    ((USB[0], w1(P=7, S=4), USB[2]), 6),                      # P_int < 8: CONFIG
    ((USB[0], w1(P=16, S=3), USB[2]), 6),                     # S < 4
    ((USB[0], w1(P=16, S=16), USB[2]), 6),                    # S >= P_int
    ((w0(d=2, n=0), USB[1], USB[2]), 2),                      # d not owned: OWNER
    ((w0(d=1, n=1), USB[1], USB[2]), 2),                      # diff with d == n: OWNER
    ((w0(d=1, n=3), USB[1], USB[2]), 2),                      # n not owned
])
def test_config_faults_at_completion(cfg, sub):
    m = run(program(" HALT", cfg=cfg))
    f = ff(m)
    # the owner is waiting in GETL (PC 4) during cycle 20
    assert (f["valid"], f["cause"], f["subcode"], f["ctx"], f["pc"], f["tick"]) == (1, 3, sub, 0, 4, 20)
    assert not m.lu.configured and m.lu.claimed == 0


def test_config_without_tx_pins_needs_no_ownership():
    cfg = (w0(d=5, n=6, tx_pins=0), USB[1], USB[2])
    m = run(program(" HALT", cfg=cfg, own=()))
    assert ff(m)["valid"] == 0 and m.lu.configured and m.lu.claimed == 0


def test_second_context_cannot_configure():
    src = "\n".join([".entry 0, main", ".entry 1, other", ".own 0, 0", ".own 1, 0",
                     f".tape {CFG_AT}, {USB[0]:#x}, {USB[1]:#x}, {USB[2]:#x}",
                     "main:", li(0, (3 << 28) | CFG_AT), " LINE r0", " HALT",
                     "other:", " ADDT 100", " WAITT", li(0, (3 << 28) | CFG_AT), " LINE r0",
                     " HALT"])
    m = run(src)
    f = ff(m)
    assert (f["cause"], f["subcode"], f["ctx"]) == (3, 2, 1)


def test_line_reserved_op_is_illegal():
    m = run(program(li(0, 4 << 28) + " LINE r0\n HALT"))
    assert ff(m)["cause"] == 1


def test_line_and_getl_need_the_owner():
    m = run(program(li(0, 1 << 28) + " LINE r0\n HALT", cfg=None, configure=False))
    assert (ff(m)["cause"], ff(m)["subcode"]) == (3, 2)        # RECV with no owner
    m = run(program(" GETL r1\n HALT", cfg=None, configure=False))
    assert (ff(m)["cause"], ff(m)["subcode"]) == (3, 2)


# ----------------------------------------------------------------------------- CRC jobs

CHECK = b"123456789"


@pytest.mark.parametrize("name,poly,ones,lsb,result", [
    # (catalogue name, left-aligned poly, init ones, bit order, check from the raw register)
    ("CRC-5/USB", 0x05 << 11, 1, 1, lambda r: reflect(r >> 11, 5) ^ 0x1F == 0x19),
    ("CRC-16/USB", 0x8005, 1, 1, lambda r: reflect(r, 16) ^ 0xFFFF == 0xB4C8),
    ("CRC-15/CAN", 0x4599 << 1, 0, 0, lambda r: r >> 1 == 0x059E),
    ("CRC-8/SMBUS", 0x07 << 8, 0, 0, lambda r: r >> 8 == 0xF4),
    ("CRC-8/MAXIM-DOW", 0x31 << 8, 0, 1, lambda r: reflect(r >> 8, 8) == 0xA1),
    ("CRC-16/IBM-SDLC", 0x1021, 1, 1, lambda r: reflect(r, 16) ^ 0xFFFF == 0x906E),
    ("CRC-16/XMODEM", 0x1021, 0, 0, lambda r: r == 0x31C3),
    ("CRC-16/IBM-3740", 0x1021, 1, 0, lambda r: r == 0x29B1),
])
def test_crc_job_catalogue(name, poly, ones, lsb, result):
    cfg = (w0(tx_pins=0, lsb=lsb), w1(), w2(poly=poly, init_ones=ones))
    cmd = (2 << 28) | (72 << 8) | DATA_AT
    m = run(program(li(0, cmd) + " LINE r0\n ADDT 0xFFFFFF\n GETL r1\n HALT", cfg=cfg,
                    tape=bytes_to_words(CHECK, lsb=bool(lsb))))
    # LINE at 35: bits at E_36 .. E_107, words read at 35, 67, 99; GETL completes at 111
    assert outcomes(m, 9) == [(t, "W") for t in range(43, 108, 4)] + [(111, "C")]
    g = commit_at(m, 111)["post"]
    reg = g["r1"] & 0xFFFF
    assert result(reg), f"{name}: register {reg:#06x}"
    assert g["r1"] >> 16 == (2 << 12) | 72 and g["tmr"] == 107


def test_crc_job_word_reads_make_owner_ldt_wait():
    cfg = (w0(tx_pins=0), w1(), w2())
    cmd = (2 << 28) | (72 << 8) | DATA_AT
    # LINE at 35; NOPs to 63; LDT at 67 = c + 32 (word 1) waits; 71 completes
    code = li(0, cmd) + " LINE r0\n" + " NOP\n" * 7 + " LDT r2, r3, 0\n HALT"
    m = run(program(code, cfg=cfg, tape=bytes_to_words(CHECK)))
    assert outcomes(m, 15) == [(67, "W"), (71, "C")]


def test_crc_job_zero_bits_ends_at_once():
    cfg = (w0(tx_pins=0), w1(), w2())
    m = run(program(li(0, 2 << 28) + " LINE r0\n ADDT 0xFFFFFF\n GETL r1\n HALT", cfg=cfg))
    g = commit_at(m, 43)["post"]
    assert g["tmr"] == 35 and g["r1"] == status(job=2, crc=0xFFFF)


# ----------------------------------------------------------------------------- transmit

SEND_TICK = 39            # after the configuring prologue: ADDT at 27, MOVI 31, MOVHI 35


def send_program(cmd: int, cfg=USB, tape=None, lead=40, after=" HALT", own=(0, 1)) -> str:
    """tmr = 20 + lead at 27; SEND commits at 39 (d = 20 + lead)."""
    return program(f" ADDT {lead}\n" + li(0, cmd) + " SEND r0\n" + after, cfg=cfg,
                   tape=tape, own=own)


def test_ack_handshake_waveform():
    m = run(send_program(0xD2 << 16))
    # d = 60: the first K shows during 61; 19 states (SYNC, PID, SE0 x2, J); idle at 364
    states = usb.line(usb.handshake("ACK"))
    assert states == [K, J, K, J, K, J, K, K, J, J, K, J, J, K, K, K, SE0, SE0, J]
    expect_states(m, 61, states)
    assert m.pins[364]["uio_oe"] == 3 and m.pins[365]["uio_oe"] == 0
    assert m.lu.e == 364 and m.lu.status() == status(job=4, crc=0xFFFF)
    assert m.ctx[0].min_lead == 21                         # lead = 60 - 39


@pytest.mark.parametrize("n", range(9))
def test_data0_packets_match_the_usb_model(n):
    rng = random.Random(n)
    data = bytes(rng.randrange(256) for _ in range(n))
    cmd = (0xC3 << 16) | (1 << 15) | ((8 * n) << 8) | DATA_AT
    m = run(send_program(cmd, tape=bytes_to_words(data)))
    expect_states(m, 61, usb.line(usb.data_packet("DATA0", data)))
    pk = usb.decode(usb.chip_states(m.pins), 16)
    assert len(pk) == 1 and pk[0].name == "DATA0" and pk[0].body[:-2] == data
    assert pk[0].crc16_ok() and pk[0].error == ""


def test_zero_length_data1_carries_crc_0000():
    m = run(send_program((0x4B << 16) | (1 << 15)))
    pk = usb.decode(usb.chip_states(m.pins), 16)
    assert pk[0].name == "DATA1" and pk[0].body == b"\0\0"


def test_stuffing_runs_across_every_field():
    data = b"\xff" * 8                                    # long runs of 1s, stuffed in data
    m = run(send_program((0x4B << 16) | (1 << 15) | (64 << 8) | DATA_AT,
                         tape=bytes_to_words(data)))
    expect_states(m, 61, usb.line(usb.data_packet("DATA1", data)))


def test_stuff_bit_after_the_last_crc_bit():
    rng = random.Random(7)
    while True:                                           # data whose packet ends in six 1s
        data = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 9)))
        bits = usb.data_packet("DATA0", data)
        sb = usb.stuff(bits)
        if len(sb) > len(bits) and sb[-1] == 0 and bits[-6:] == [1] * 6:
            break
    m = run(send_program((0xC3 << 16) | (1 << 15) | ((8 * len(data)) << 8) | DATA_AT,
                         tape=bytes_to_words(data)))
    expect_states(m, 61, usb.line(bits))


def test_stream_word_reads_cross_words_and_wrap():
    # 64 bits starting at word 0xFF: the second word is T[0x00] (mod 256)
    data = bytes(range(1, 9))
    tape = {0xFF: int.from_bytes(data[:4], "little"), 0x00: int.from_bytes(data[4:], "little")}
    m = run(send_program((0xC3 << 16) | (1 << 15) | (64 << 8) | 0xFF, tape=tape))
    pk = usb.decode(usb.chip_states(m.pins), 16)
    assert pk[0].body[:-2] == data and pk[0].crc16_ok()


@pytest.mark.parametrize("eop", [1, 2, 3])
def test_eop_length(eop):
    cfg = (w0(eop=eop), USB[1], USB[2])
    m = run(send_program(0xD2 << 16, cfg=cfg))
    expect_states(m, 61, usb.nrzi(usb.handshake("ACK")) + [SE0] * eop + [J])


def test_frac_period_places_every_boundary():
    # NRZ, no coding: 127 bits of 1,0,1,0,... at P = 16 + 171/256 (USB at 25 MHz)
    cfg = (NRZ[0], w1(P=16, frac=171, S=8), NRZ[2])
    words = {DATA_AT + i: 0x55555555 for i in range(4)}
    m = run(send_program((127 << 8) | DATA_AT, cfg=cfg, tape=words, own=(0,)), ticks=3000)
    d = 60
    B = [d + 16 * k + (171 * k) // 256 for k in range(128)]
    lvl = [m.pins[t]["uio_out"] & 1 for t in range(len(m.pins))]
    for k in range(127):
        want = 1 - (k & 1)                                 # 0x55.. LSB first: 1, 0, 1, ...
        assert all(lvl[u] == want for u in range(B[k] + 1, B[k + 1] + 1)), k
    assert m.lu.e == B[127]


def test_nrz_bit_counts_and_order():
    rng = random.Random(3)
    for nb in (1, 2, 31, 32, 33, 64, 127):
        words = {DATA_AT + i: rng.getrandbits(32) for i in range(4)}
        for lsb in (0, 1):
            cfg = (w0(d=0, diff=0, nrzi=0, N=0, eop=0, idle_drive=1, sync_skip=0, prefix=0,
                      lsb=lsb), w1(), w2(poly=0, hdr=0))
            m = run(send_program((nb << 8) | DATA_AT, cfg=cfg, tape=words, own=(0,)))
            lvl = [m.pins[t]["uio_out"] & 1 for t in range(len(m.pins))]
            for j in range(nb):
                w = words[DATA_AT + j // 32]
                bit = (w >> (j % 32)) & 1 if lsb else (w >> (31 - j % 32)) & 1
                assert all(lvl[u] == bit for u in range(61 + 16 * j, 61 + 16 * j + 16)), (nb, lsb, j)
            assert m.lu.e == 60 + 16 * nb


def test_send_with_lead_one_uses_the_word_read_in_its_commit_cycle():
    words = {DATA_AT: 0x0000FFFF}                         # LSB first: sixteen 1s, then 0s
    m = run(send_program((32 << 8) | DATA_AT, cfg=NRZ, tape=words, lead=20, own=(0,)))
    lvl = [m.pins[t]["uio_out"] & 1 for t in range(len(m.pins))]
    assert ff(m)["valid"] == 0 and m.ctx[0].min_lead == 1
    assert lvl[41 + 16 * 16 - 1] == 1 and lvl[41 + 16 * 16] == 0   # bit 16 is the first 0


# ----------------------------------------------------------------------------- SEND admission


LATE = " JNZ late\n HALT\nlate:\n MOVI r3, 7\n HALT"


@pytest.mark.parametrize("lead,late", [(28, False), (27, True), (0, True), (-1000, True),
                                       ((1 << 31) + 26, False), ((1 << 31) + 27, True)])
def test_a_late_send_is_dropped_with_z_clear(lead, late):
    # tmr = 20 + lead at 35; SEND commits at 47. Not ahead (d <= c, or d >= c + 2^31): the
    # reply would be late, so it is not sent, and it is not a fault.
    code = li(2, lead & 0xFFFFFFFF) + " ADDTR r2\n" + li(0, 0xD2 << 16) + " SEND r0\n" + LATE
    m = run(program(code), ticks=1000)
    send = [c for c in m.commits if c["insn"] >> 24 == 0x2D][0]
    assert send["t"] == 47 and send["outcome"] == "C" and ff(m)["valid"] == 0
    assert send["post"]["z"] == int(not late) and m.ctx[0].r[3] == (7 if late else 0)
    pk = usb.decode(usb.chip_states(m.pins), 16)
    if late:
        assert pk == [] and m.lu.last_job == 3 and m.lu.mode == 0
        assert m.ctx[0].min_lead == 0xFFFF and m.ctx[0].max_occupancy == 0
    elif lead == 28:
        assert [p.name for p in pk] == ["ACK"] and m.ctx[0].min_lead == 1


def test_a_dropped_send_leaves_an_armed_receiver_armed():
    code = (li(0, (1 << 28) | DATA_AT) + " LINE r0\n" + li(2, 0xFFFFFFFF) + " ADDTR r2\n"
            + li(0, 0xD2 << 16) + " SEND r0\n ADDT 0xFFFFFF\n GETL r1\n HALT")
    bus = usb.UsbBus()
    bus.send(200, usb.line(usb.token("IN", 3, 0)))
    m = run(program(code), board=bus, ticks=2000)
    assert ff(m)["valid"] == 0 and m.lu.last_job == 1 and (m.lu.status() >> 16) & 0x1FF == 24
    assert m.tape[DATA_AT] & 0xFFFFFF == usb.PID["IN"] | (usb.token_field(3, 0) << 8)


def test_a_late_send_is_dropped_before_the_order_check():
    code = (" ADDT 100\n EMIT 0x04, 0x04, 0x04\n" + li(2, (-110) & 0xFFFFFFFF) + " ADDTR r2\n"
            + li(0, 0xD2 << 16) + " SEND r0\n" + LATE)
    m = run(program(code, own=(0, 1, 2)), ticks=1000)
    assert ff(m)["valid"] == 0 and m.ctx[0].r[3] == 7
    assert m.pins[121]["uio_out"] & 4 and m.pins[121]["uio_oe"] & 4    # the EMIT still drives


def test_send_order_against_a_queued_event():
    code = (" ADDT 100\n EMIT 0x04, 0x04, 0x04\n" + li(2, (-10) & 0xFFFFFFFF) + " ADDTR r2\n"
            + li(0, 0xD2 << 16) + " SEND r0\n HALT")
    m = run(program(code, own=(0, 1, 2)))
    assert (ff(m)["cause"], ff(m)["subcode"]) == (3, 4)


def test_send_waits_on_a_full_fifo_and_counts_it():
    code = (" ADDT 200\n EMIT 0x04, 0x04, 0x04\n ADDT 10\n EMIT 0x04, 0, 0x04\n"
            " ADDT 10\n EMIT 0x04, 0x04, 0x04\n ADDT 10\n EMIT 0x04, 0, 0x04\n"
            " ADDT 100\n" + li(0, 0xD2 << 16) + " SEND r0\n HALT")
    m = run(program(code, own=(0, 1, 2)))
    assert ff(m)["valid"] == 0
    assert m.ctx[0].wait_full > 0
    o = outcomes(m, 16)
    assert any(x == "W" for _, x in o) and o[-1][1] == "C"


def test_second_send_waits_while_the_unit_is_engaged():
    code = (" ADDT 40\n" + li(0, 0xD2 << 16) + " SEND r0\n ADDT 400\n SEND r0\n HALT")
    m = run(program(code))
    o = outcomes(m, 10)
    assert all(x == "W" for _, x in o[:-1]) and o[-1] == (367, "C")   # first ends at 364
    assert m.ctx[0].wait_full == 0                         # an engaged unit is not a full FIFO
    pk = usb.decode(usb.chip_states(m.pins), 16)
    assert [p.name for p in pk] == ["ACK", "ACK"] and pk[1].start == 461


@pytest.mark.parametrize("cfg,code", [
    (None, li(0, 0xD2 << 16) + " SEND r0\n HALT"),                          # no owner
    ((w0(tx_pins=0), w1(), w2()), li(0, 0xD2 << 16) + " ADDT 100\n SEND r0\n HALT"),
])
def test_send_owner_rules(cfg, code):
    m = run(program(code, cfg=cfg, configure=cfg is not None))
    assert (ff(m)["cause"], ff(m)["subcode"]) == (3, 2)


def test_send_by_another_context_faults():
    src = program(" HALT", pre=".entry 1, other\nother:\n ADDT 200\n WAITT\n"
                  + li(0, 0xD2 << 16) + " ADDT 100\n SEND r0\n HALT")
    m = run(src)
    assert (ff(m)["cause"], ff(m)["subcode"], ff(m)["ctx"]) == (3, 2, 1)


def test_drive_on_a_claimed_pin_faults_owner():
    m = run(program(" ADDT 40\n EMIT 0x01, 0x01, 0x01\n HALT"))
    assert (ff(m)["cause"], ff(m)["subcode"]) == (3, 2)


def test_drive_queued_before_the_claim_leaves_the_claimed_pin_alone():
    # EMIT on pins 0 and 2 for tick 40, queued at 7; CONFIG claims pin 0 at E_24
    src = "\n".join([".entry 0, main", ".own 0, 0", ".own 1, 0", ".own 2, 0",
                     f".tape {CFG_AT}, {USB[0]:#x}, {USB[1]:#x}, {USB[2]:#x}", "main:",
                     " ADDT 40", " EMIT 0x05, 0x05, 0x05", li(0, (3 << 28) | CFG_AT),
                     " LINE r0", " ADDT 0xFFFFFF", " GETL r1", " HALT"])
    m = run(src)
    assert ff(m)["valid"] == 0
    assert m.pins[41]["uio_oe"] & 0b101 == 0b100 and m.pins[41]["uio_out"] & 4


# ----------------------------------------------------------------------------- receive


def recv_program(nbytes=16, after=" ADDT 0xFFFFFF\n GETL r1\n HALT", cfg=USB) -> str:
    """LINE RECV commits at 35 (after the prologue: MOVI 27, MOVHI 31)."""
    return program(li(0, (1 << 28) | ((nbytes & 31) << 8) | DATA_AT) + " LINE r0\n" + after,
                   cfg=cfg, tape={DATA_AT + i: 0xA5A5A5A5 for i in range(8)})


def rx(states, t0=100, nbytes=16, period=16, jitter=None, after=None, ticks=5000, cfg=USB):
    bus = usb.UsbBus()
    end = bus.send(t0, states, period=period, jitter=jitter) if states else t0
    kw = {} if after is None else {"after": after}
    m = run(recv_program(nbytes, cfg=cfg, **kw), board=bus, ticks=ticks)
    return m, bus, end


def words(m, n):
    return [m.tape[DATA_AT + i] for i in range(n)]


def test_receive_token():
    bits = usb.token("IN", 0x15, 1)
    m, bus, end = rx(usb.line(bits))
    g = [c for c in m.commits if c["pc"] == 9 and c["outcome"] == "C"][0]["post"]
    st = g["r1"]
    assert st >> 16 == (1 << 12) | 24                       # RECV, OK, 24 bits after SYNC
    assert m.tape[DATA_AT] & 0xFFFFFF == usb.PID["IN"] | (usb.token_field(0x15, 1) << 8)
    assert m.tape[DATA_AT] >> 24 == 0                       # unstored bits are 0
    assert g["tmr"] == end - 16 + 2                         # SE0 -> J, seen 2 ticks later
    assert m.tape[DATA_AT + 1] == 0xA5A5A5A5


@pytest.mark.parametrize("data", [b"", b"\x00", b"\xff" * 8, bytes(range(8)), b"\x7e\x3f\xfc"])
def test_receive_data_packets(data):
    bits = usb.data_packet("DATA1", data)
    m, bus, end = rx(usb.line(bits))
    st = m.lu.status()
    n = 8 * (1 + len(data) + 2)
    assert (st >> 25) & 7 == 0 and (st >> 16) & 0x1FF == n
    assert st & 0xFFFF == 0x800D                             # CRC16 residual (8.3.5.2)
    raw = b"".join(w.to_bytes(4, "little") for w in words(m, (n + 31) // 32))
    pk = raw[:n // 8]
    assert pk[0] == usb.PID["DATA1"] and pk[1:1 + len(data)] == data
    assert usb.crc16_usb(pk[1:-2]) == pk[-2] | (pk[-1] << 8)
    assert raw[n // 8:] == bytes(len(raw) - n // 8)          # the rest of the last word is 0
    assert m.lu.e == end - 16 + 2


@pytest.mark.parametrize("period,jit", [(16 * 0.95, 0), (16 * 1.05, 0), (16 * 0.985, 3),
                                        (16 * 1.015, 3)])
def test_receive_tolerates_clock_offset_and_jitter(period, jit):
    rng = random.Random(int(period * 100) + jit)
    data = b"\xff\x00\xff\xfe\x01\x80\x7f\xff"
    states = usb.line(usb.data_packet("DATA0", data))
    jitter = [rng.randint(-jit, jit) for _ in range(len(states) + 1)]
    jitter[0] = 0
    m, bus, end = rx(states, period=period, jitter=jitter)
    st = m.lu.status()
    assert (st >> 25) & 7 == 0 and st & 0xFFFF == 0x800D, hex(st)


def test_receive_truncated_sync():
    bits = [0, 0, 0, 1] + usb.handshake("ACK")[8:]
    m, bus, end = rx(usb.line(bits))
    assert (m.lu.status() >> 16) & 0x1FF == 8 and m.tape[DATA_AT] & 0xFF == usb.PID["ACK"]


def test_receive_dribble_bit_is_stored_as_a_partial_byte():
    states = usb.line(usb.handshake("NAK"))
    states = states[:-3] + [states[-4]] + states[-3:]       # one more bit time, no transition
    m, bus, end = rx(states)
    st = m.lu.status()
    assert (st >> 25) & 7 == 0 and (st >> 16) & 0x1FF == 9
    assert m.tape[DATA_AT] == usb.PID["NAK"] | (1 << 8)


def test_receive_seven_ones_is_a_stuff_error_reported_at_eop():
    bits = usb.SYNC_BITS + usb.lsb_bits(usb.PID["DATA1"], 8) + [1] * 7 + [0, 1, 0]
    states = usb.nrzi(bits) + [SE0, SE0, J]                 # no stuffing applied
    m, bus, end = rx(states)
    st = m.lu.status()
    assert (st >> 25) & 7 == 3 and (st >> 16) & 0x1FF == 14
    assert m.lu.e == end - 16 + 2


def test_receive_keepalive_se0():
    bus = usb.UsbBus()
    bus.drive_se0(100, 32)
    m = run(recv_program(), board=bus)
    st = m.lu.status()
    assert (st >> 25) & 7 == 2 and (st >> 16) & 0x1FF == 32 and m.lu.e == 134


def test_receive_reset_se0_ends_after_255_ticks():
    bus = usb.UsbBus()
    bus.drive_se0(100, 10000)
    m = run(recv_program(), board=bus, ticks=2000)
    st = m.lu.status()
    assert (st >> 25) & 7 == 2 and (st >> 16) & 0x1FF == 255 and m.lu.e == 102 + 8 + 255


def test_receive_ignores_short_se0_at_transitions():
    bits = usb.data_packet("DATA0", b"\x12\x34\x56")
    bus = usb.UsbBus()
    end = bus.send(100, usb.line(bits))
    prev = J
    for t in range(100, end):                               # one tick of SE0 at each J/K edge
        s = bus.host[t]
        if s in (J, K) and prev in (J, K) and s != prev:
            bus.host[t] = SE0
        prev = s
    m = run(recv_program(), board=bus)
    st = m.lu.status()
    assert (st >> 25) & 7 == 0 and st & 0xFFFF == 0x800D


def test_receive_full_in_a_differential_packet_waits_for_eop():
    m, bus, end = rx(usb.line(usb.data_packet("DATA0", bytes(range(8)))), nbytes=2)
    st = m.lu.status()
    assert (st >> 25) & 7 == 4 and (st >> 16) & 0x1FF == 16 and m.lu.e == end - 16 + 2
    assert m.tape[DATA_AT] == usb.PID["DATA0"] and m.tape[DATA_AT + 1] == 0xA5A5A5A5


def test_receive_nosync():
    m, bus, end = rx(usb.nrzi([0] * 12) + [SE0, SE0, J])
    st = m.lu.status()
    assert (st >> 25) & 7 == 5 and (st >> 16) & 0x1FF == 0
    assert m.tape[DATA_AT] == 0xA5A5A5A5                   # nothing written


def test_receive_writes_full_words_in_the_next_owner_cycle():
    data = bytes(8)                                         # all 0s: a transition every bit
    m, bus, end = rx(usb.line(usb.data_packet("DATA0", data)))
    t0 = 102
    s = t0 + 39 * 16 + 8                                    # sample of stored bit 31
    u = next(x for x in range(s + 1, s + 5) if x % 4 == 3)
    assert (u, DATA_AT, usb.PID["DATA0"]) in m.tape_log


def test_idle_min_ignores_a_packet_already_under_way():
    cfg = (USB[0], USB[1], w2(idle_min=3))                  # 48 idle ticks before a start
    bus = usb.UsbBus()
    bus.send(60, usb.line(usb.handshake("ACK")))            # starts 25 ticks after the arm
    t2 = bus.send(800, usb.line(usb.handshake("NAK")))
    m = run(recv_program(cfg=cfg), board=bus)
    assert m.tape[DATA_AT] & 0xFF == usb.PID["NAK"] and m.lu.e == t2 - 16 + 2


def test_getl_timeout_while_armed():
    m, bus, end = rx([], after=" ADDT 100\n GETL r1\n HALT")
    g = [c for c in m.commits if c["pc"] == 9 and c["outcome"] == "C"][0]
    assert g["post"]["z"] == 0 and g["post"]["r1"] >> 31 == 1 and g["t"] == 123
    assert m.lu.mode == 0                                   # HALT cancelled the armed receiver


def test_send_cancels_an_armed_receiver():
    code = (li(0, (1 << 28) | DATA_AT) + " LINE r0\n ADDT 60\n" + li(0, 0xD2 << 16)
            + " SEND r0\n ADDT 0xFFFFFF\n GETL r1\n HALT")
    m = run(program(code), board=usb.UsbBus())
    pk = usb.decode(usb.chip_states(m.pins), 16)
    assert [p.name for p in pk] == ["ACK"] and m.lu.last_job == 4


def test_off_releases_and_unconfigures():
    code = (" ADDT 40\n" + li(0, 0xD2 << 16) + " SEND r0\n" + " NOP\n" * 16
            + li(1, 0) + " LINE r1\n HALT")
    m = run(program(code))
    t_off = [c["t"] for c in m.commits if c["insn"] >> 24 == 0x3C][-1]
    assert m.pins[t_off]["uio_oe"] == 3 and m.pins[t_off + 1]["uio_oe"] == 0
    assert not m.lu.configured and m.lu.mode == 0 and m.lu.owner == 0


# ----------------------------------------------------------------------------- monitor


def test_monitor_mismatch_stops_at_the_next_boundary():
    cfg = (w0(d=0, r=1, diff=0, nrzi=0, N=0, eop=0, idle_drive=1, sync_skip=0, prefix=0),
           w1(), w2(poly=0, hdr=0, abort=1))

    def board(t, o, oe):
        tx = (o & 1) if oe & 1 else 1
        rxv = 0 if 140 <= t < 150 else tx                   # a dominant bit forced during bit 5
        return 0xFC | tx | (rxv << 1)

    m = run(send_program((32 << 8) | DATA_AT, cfg=cfg, tape={DATA_AT: 0xFFFFFFFF},
                         own=(0,)), board=board)
    st = m.lu.status()
    assert (st >> 25) & 7 == 6 and (st >> 16) & 0x1FF == 5 and m.lu.e == 60 + 6 * 16


# ----------------------------------------------------------------------------- faults, stop, HALT


def test_owner_fault_while_pending_cancels_the_packet():
    m = run(send_program(0xD2 << 16, after=" .word 0\n"))
    assert ff(m)["cause"] == 1 and not m.lu.configured and m.lu.mode == 0
    assert all(s == J for s in usb.chip_states(m.pins))


def test_owner_fault_mid_packet_releases_the_pins():
    m = run(send_program(0xD2 << 16, after=" NOP\n" * 15 + " .word 0\n"))
    t = ff(m)["tick"]
    assert 61 < t < 364 and m.pins[t]["uio_oe"] == 3 and m.pins[t + 1]["uio_oe"] == 0
    assert not m.lu.configured and m.lu.claimed == 0


def test_stop_mid_packet():
    m = Machine(assemble(send_program(0xD2 << 16)))
    m.run(150)
    m.stop()
    m.run(5)
    assert m.lu.mode == 0 and not m.lu.configured and m.oe == 0


def test_halt_waits_for_the_packet():
    m = run(send_program(0xD2 << 16))
    assert outcomes(m, 9)[-1] == (367, "C") and all(o == "W" for _, o in outcomes(m, 9)[:-1])


def test_getl_by_another_context_faults():
    m = run(program(" HALT", pre=".entry 1, other\nother:\n ADDT 100\n WAITT\n GETL r1\n HALT"))
    assert (ff(m)["cause"], ff(m)["subcode"], ff(m)["ctx"]) == (3, 2, 1)


# ----------------------------------------------------------------------------- idle threshold


@pytest.mark.parametrize("t0,started", [(50, True), (49, False)])
def test_idle_min_threshold_is_exact(t0, started):
    # armed at 35: cycles 36 .. t0+1 idle; SE0 seen from t0 + 2. One bit (16) is needed.
    bus = usb.UsbBus()
    bus.drive_se0(t0, 32)
    m = run(recv_program(after=" ADDT 2000\n GETL r1\n HALT"), board=bus)
    st = m.lu.status()
    if started:
        assert (st >> 25) & 7 == 2 and (st >> 16) & 0x1FF == 32
    else:
        assert st >> 28 != 1                               # never started: no RECV job yet


# ----------------------------------------------------------------------------- CAN

CAN_P, CAN_S = 20, 14
CAN = (w0(d=0, r=1, diff=0, nrzi=0, lsb=0, N=5, any_=1, eop=0, idle_drive=1, sync_skip=0,
          prefix=0),
       w1(P=CAN_P, S=CAN_S),
       w2(poly=0x4599 << 1, init_ones=0, inv=0, hdr=0, fall=1, abort=1, idle_min=11))


def bits_to_words(bits: list[int]) -> dict[int, int]:
    """Left-aligned (MSB first) stream words from DATA_AT."""
    out = {}
    for i in range(0, len(bits), 32):
        chunk = bits[i:i + 32] + [0] * (32 - len(bits[i:i + 32]))
        out[DATA_AT + i // 32] = int("".join(map(str, chunk)), 2)
    return out


@pytest.mark.parametrize("ident,data", [(0x123, b""), (0x7FF, b"\xff" * 8), (0x000, bytes(8)),
                                        (0x555, b"\x01\x02\x03"), (0x18F, b"\xaa\x55\x0f\xf0\x00")])
def test_can_transmit_is_decoded_and_acked_by_the_can_model(ident, data):
    from escmodels import Board, CanBus, frame_bits
    bits = frame_bits(ident, data)[:-15]                    # SOF .. data; the LU adds the CRC
    bus = CanBus(0, 1, CAN_P, sample=CAN_S / CAN_P)
    cmd = (1 << 15) | (len(bits) << 8) | DATA_AT
    m = run(send_program(cmd, cfg=CAN, tape=bits_to_words(bits), lead=400, own=(0,)),
            board=Board(bus), ticks=8000, stop_when_idle=False)    # the model needs EOF
    assert bus.errors() == [] and len(bus.frames) == 1, bus.errors()
    f = bus.frames[0]
    assert (f["id"], bytes(f["data"]), f["crc_ok"], f["ack_seen"]) == (ident, data, True, True)
    assert (m.lu.status() >> 25) & 7 == 0                    # no monitor mismatch


def can_board(levels: list[int], t0: int, period=CAN_P, rise_delay=0):
    """RXD (pin 1) follows `levels` from t0, one per bit; TXD (pin 0) is the chip's."""
    def board(t, o, oe):
        lvl = 1
        k = (t - t0) // period
        if t >= t0 and k < len(levels):
            lvl = levels[k]
            # a slow recessive edge: the rise reaches the input rise_delay ticks late
            if lvl == 1 and k > 0 and levels[k - 1] == 0 and (t - t0) % period < rise_delay:
                lvl = 0
        return 0xFC | 1 | (lvl << 1)
    return board


def can_destuffed_count(levels: list[int]) -> int:
    """Stored bits before the first stuffing violation, counting from SOF."""
    n, last, run, need = 0, None, 0, False
    for b in levels:
        if need:
            if b == last:
                return n
            last, run, need = b, 1, False
            continue
        n += 1
        run = run + 1 if b == last else 1
        last = b
        need = run == 5
    return n


@pytest.mark.parametrize("ident,data,ack", [(0x123, b"", True), (0x7FF, b"\xff" * 8, False),
                                            (0x000, bytes(8), True), (0x555, b"\x01\x02\x03", False),
                                            (0x2A5, b"\x80\x7f\x01\xfe", True)])
@pytest.mark.parametrize("rise_delay", [0, 7])
def test_can_receive_and_check_the_crc(ident, data, ack, rise_delay):
    from escmodels import encode_frame
    levels = encode_frame(ident, data, ack=ack)
    nframe = 19 + 8 * len(data) + 15
    # RECV (limit 32 bytes) at 35; frame from 400; then a CRC job over the frame bits
    code = (li(0, (1 << 28) | DATA_AT) + " LINE r0\n ADDT 0xFFFFFF\n GETL r1\n"
            + li(2, (2 << 28) | (nframe << 8) | DATA_AT) + " LINE r2\n ADDT 0xFFFFFF\n GETL r3\n HALT")
    m = run(program(code, cfg=CAN, own=(0,)), board=can_board(levels, 400, rise_delay=rise_delay),
            ticks=12000)
    post = [c for c in m.commits if c["outcome"] == "C" and c["insn"] >> 24 == 0x3D]
    st_rx, st_crc = post[1]["post"]["r1"], post[2]["post"]["r3"]
    assert (st_rx >> 25) & 7 == 1                           # ended by the EOF violation
    assert (st_rx >> 16) & 0x1FF == can_destuffed_count(levels)
    want = {a: v for a, v in bits_to_words(frame_bits_after_stuff(levels, nframe)).items()}
    for a, v in want.items():
        n_in = min(32, nframe - 32 * (a - DATA_AT))
        mask = ((1 << n_in) - 1) << (32 - n_in)
        assert m.tape[a] & mask == v & mask
    assert st_crc >> 28 == 2 and (st_crc >> 16) & 0x1FF == nframe
    assert st_crc & 0xFFFF == 0                             # data + CRC-15 leaves 0


def frame_bits_after_stuff(levels: list[int], n: int) -> list[int]:
    """The first n destuffed bits."""
    out, last, run, need = [], None, 0, False
    for b in levels:
        if need:
            last, run, need = b, 1, False
            continue
        out.append(b)
        run = run + 1 if b == last else 1
        last = b
        need = run == 5
        if len(out) == n:
            break
    return out


def test_can_receive_full_without_diff_ends_at_once():
    from escmodels import encode_frame
    levels = encode_frame(0x123, b"\x11\x22\x33\x44")
    code = li(0, (1 << 28) | (2 << 8) | DATA_AT) + " LINE r0\n ADDT 0xFFFFFF\n GETL r1\n HALT"
    m = run(program(code, cfg=CAN, own=(0,)), board=can_board(levels, 400), ticks=12000)
    st = m.lu.status()
    assert (st >> 25) & 7 == 4 and (st >> 16) & 0x1FF == 16
    # the 16th stored bit (index 15, no stuff bits before it here) is sampled at 400+2+15*20+14
    assert m.lu.e == 402 + 15 * CAN_P + CAN_S


# ----------------------------------------------------------------------------- wire-order CRC


@pytest.mark.parametrize("addr,endp", [(0, 0), (1, 0), (0x15, 0xE), (0x3A, 0xA), (0x70, 4),
                                       (0x7F, 0xF), (5, 1)])
def test_crc_job_wire_order_gives_the_token_crc5_field(addr, endp):
    cfg = (w0(tx_pins=0), w1(), w2(poly=0x05 << 11))        # CRC-5/USB: ones, inverted
    value = addr | (endp << 7)
    cmd = (2 << 28) | (1 << 16) | (11 << 8) | DATA_AT
    m = run(program(li(0, cmd) + " LINE r0\n ADDT 0xFFFFFF\n GETL r1\n HALT", cfg=cfg,
                    tape={DATA_AT: value}))
    assert m.lu.crc == usb.crc5_usb(value) and m.lu.e == 35 + 11
    assert usb.token_field(addr, endp) == value | (m.lu.crc << 11)


@pytest.mark.parametrize("n", [0, 1, 2, 8])
def test_crc_job_wire_order_gives_the_data_crc16_field(n):
    data = bytes(range(0x30, 0x30 + n))
    cfg = (w0(tx_pins=0), w1(), w2())
    cmd = (2 << 28) | (1 << 16) | ((8 * n) << 8) | DATA_AT
    m = run(program(li(0, cmd) + " LINE r0\n ADDT 0xFFFFFF\n GETL r1\n HALT", cfg=cfg,
                    tape=bytes_to_words(data)))
    assert m.lu.crc == usb.crc16_usb(data)
