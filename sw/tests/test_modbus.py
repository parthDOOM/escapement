"""firmware/modbus_rtu.asm (v1.6): a Modbus RTU server against a client bus model."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from esciss import Machine
from escmodels import Board
from escmodels.modbus import ModbusClient, adu, crc16

FW = Path(__file__).resolve().parents[2] / "firmware"
CLK = 25_000_000


def read(addr, first, count):
    return adu(addr, bytes([3, first >> 8, first & 255, count >> 8, count & 255]))


def write1(addr, reg, value):
    return adu(addr, bytes([6, reg >> 8, reg & 255, value >> 8, value & 255]))


def write_n(addr, first, values):
    n = len(values)
    data = b"".join(v.to_bytes(2, "big") for v in values)
    return adu(addr, bytes([16, first >> 8, first & 255, 0, n, 2 * n]) + data)


def regs(data: bytes):
    return [int.from_bytes(data[3 + 2 * k:5 + 2 * k], "big") for k in range(data[2] // 2)]


def serve(requests, baud=115200, parity="E", equ="", tape=None, host=(), client_baud=None,
          drain=True, **kw):
    """Run the server until the client has sent every request and seen every reply.
    Returns (client, machine, host notices)."""
    par = {"E": 0, "O": 1, "N": 2}[parity]
    img = assemble(f".equ modbus_rtu.BAUD, {baud}\n.equ modbus_rtu.PARITY, {par}\n{equ}"
                   '.include "modbus_rtu.asm"\n', base_dir=FW)
    for k, v in (tape or {}).items():
        img.tape[k] = v
    kw.setdefault("start", 60_000)
    cl = ModbusClient(ro=1, di=0, de=2, ticks_per_bit=CLK / (client_baud or baud),
                      requests=requests, parity=parity, silence=round(t35(baud)) + 500, **kw)
    m = Machine(img, board=Board(cl))
    host, notes = list(host), []
    while cl.k < len(requests) or cl._cur is not None or cl.next_at is not None:
        if host and m.now >= host[0][0]:
            m.host_rx_push(0, host.pop(0)[1])
        m.run(2000, stop_when_idle=False)
        v = m.host_tx_pop(0) if drain else None
        if v is not None:
            notes.append(v)
        assert m.now < 4_000_000, f"the client never finished: request {cl.k}, {cl.errors()}"
    return cl, m, notes


def t35(baud):
    """The silence a frame ends with, in ticks: 3.5 characters, at least 1.75 ms."""
    return max(3.5 * 11 * CLK / baud, 1.75e-3 * CLK)


def test_read_write_and_exceptions():
    tape = {k: 0x1000 + 17 * k for k in range(64)}
    reqs = [(read(1, 5, 3), True), (write1(1, 9, 0xBEEF), True), (read(1, 9, 1), True),
            (adu(1, bytes([0x2B, 0, 0])), True), (read(1, 63, 2), True),
            (read(1, 0, 0), True), (read(1, 0, 126), True), (write1(1, 64, 1), True)]
    cl, m, notes = serve(reqs, tape=tape)
    out = [bytes(r["bytes"]) for r in cl.replies]
    assert cl.errors() == [] and len(out) == len(reqs)
    assert regs(out[0]) == [0x1055, 0x1066, 0x1077]
    assert out[1] == reqs[1][0]                                 # 06: the request echoed
    assert regs(out[2]) == [0xBEEF] and m.tape[9] == 0xBEEF
    assert out[3][:3] == bytes([1, 0xAB, 1])                    # illegal function
    assert out[4][:3] == bytes([1, 0x83, 2])                    # past the table
    assert out[5][:3] == bytes([1, 0x83, 3]) and out[6][:3] == bytes([1, 0x83, 3])
    assert out[7][:3] == bytes([1, 0x86, 2])
    assert notes == [9 | 1 << 16] and m.irq_pending == 0
    for r in cl.replies:
        bit = CLK / 115200
        assert r["starts"][0] - cl.sent[r["req"]][1] >= t35(115200)    # the silence before
        assert r["starts"][0] - r["de_rise"] == round(bit)           # DE a bit time early
        assert 0 <= r["de_fall"] - r["end"] <= 1                       # off at the stop bit's end
        gaps = [b - a for a, b in zip(r["starts"], r["starts"][1:])]
        assert max(gaps) <= 11 * bit + 1                               # characters back to back


def test_write_multiple_broadcast_and_other_servers():
    S = round(t35(115200)) + 500
    vals = [0x0102, 0xA0B0, 0xFFFF, 0x0000, 0x7E7E]
    reqs = [(write_n(1, 20, vals), True), (read(1, 20, 5), True),
            (write1(0, 30, 0x4242), False),                    # broadcast: done, no reply
            (write_n(0, 40, [7, 8]), False),
            (read(0, 20, 1), False),                           # a broadcast read: ignored
            (read(2, 20, 1), False, S),                        # another server's, and the
            (write1(2, 31, 0x1111), False, S),                 # next 3.5 characters later
            (read(1, 30, 1), True), (read(1, 40, 2), True), (read(1, 31, 1), True)]
    cl, m, notes = serve(reqs, tape={k: 0 for k in range(64)})
    out = [bytes(r["bytes"]) for r in cl.replies]
    assert cl.errors() == [] and len(out) == 5
    assert out[0] == reqs[0][0][:6] + crc16(reqs[0][0][:6]).to_bytes(2, "little")
    assert regs(out[1]) == vals
    assert regs(out[2]) == [0x4242] and regs(out[3]) == [7, 8] and regs(out[4]) == [0]
    assert notes == [20 | 5 << 16, 30 | 1 << 16, 40 | 2 << 16]


def test_longest_frames():
    """57 registers in one write (123 bytes, the longest frame), 64 read back (133 bytes);
    a 58-register write (125 bytes) is too long: dropped."""
    vals = [(0x9E37 * (k + 1)) & 0xFFFF for k in range(57)]
    reqs = [(write_n(1, 3, vals), True), (read(1, 0, 64), True),
            (write_n(1, 0, [1] * 58), False), (read(1, 0, 1), True)]
    cl, m, notes = serve(reqs, tape={k: 0x5A5A for k in range(64)})
    out = [bytes(r["bytes"]) for r in cl.replies]
    assert cl.errors() == [] and len(out) == 3
    assert regs(out[1]) == [0x5A5A] * 3 + vals + [0x5A5A] * 4
    assert regs(out[2]) == [0x5A5A]


def test_bad_frames_dropped():
    good = read(1, 2, 1)
    bad_crc = bytearray(good)
    bad_crc[-1] ^= 0x40
    reqs = [(bytes(bad_crc), False), (good, True),
            (good, False),                                     # a parity error (bad_bit)
            (good, True),
            (good[:3], False),                                 # too short
            (good, True),
            (adu(1, bytes([3, 0, 2, 0, 1, 0])), True),         # 9 bytes: wrong length, 03
            (adu(1, bytes([16, 0, 0, 0, 1, 3, 0, 1, 0])), True)]  # byte count 3: 03
    cl, m, notes = serve(reqs, tape={2: 0x2222}, bad_bit={2: (3, 4)})
    out = [bytes(r["bytes"]) for r in cl.replies]
    assert cl.errors() == [] and len(out) == 5
    assert [r["req"] for r in cl.replies] == [1, 3, 5, 6, 7]
    assert all(regs(o) == [0x2222] for o in out[:3])
    assert out[3][:3] == bytes([1, 0x83, 3]) and out[4][:3] == bytes([1, 0x90, 3])


def test_gaps_between_characters():
    """2 character times of silence inside a frame keep it one frame; 4 split it in two,
    both halves failing their CRC (no reply)."""
    reqs = [(read(1, 1, 1), True), (read(1, 1, 1), False), (read(1, 1, 1), True)]
    cl, m, notes = serve(reqs, baud=19200, tape={1: 0x0101},
                         gaps={0: 22.0, 1: 44.0})
    assert cl.errors() == [] and [r["req"] for r in cl.replies] == [0, 2]


@pytest.mark.parametrize("parity", ["O", "N"])
def test_parity_modes(parity):
    reqs = [(write1(1, 4, 0x8001), True), (read(1, 4, 1), True)]
    cl, m, notes = serve(reqs, parity=parity)
    out = [bytes(r["bytes"]) for r in cl.replies]
    assert cl.errors() == [] and regs(out[1]) == [0x8001]


def test_slow_baud_and_clock_error():
    """19200 baud (3.5 characters: 2.0 ms) with the client 2% fast, then 2% slow."""
    reqs = [(read(1, 7, 2), True)]
    for cb in (19200 * 1.02, 19200 * 0.98):
        cl, m, _ = serve(reqs, baud=19200, client_baud=cb, tape={7: 1, 8: 2})
        assert cl.errors() == [] and regs(bytes(cl.replies[0]["bytes"])) == [1, 2]
        assert cl.replies[0]["starts"][0] - cl.sent[0][1] >= t35(19200)


def test_host_side_writes():
    """The host writes registers between frames; k >= NREG raises IRQ 4."""
    reqs = [(read(1, 10, 2), True)]
    cl, m, _ = serve(reqs, tape={10: 0, 11: 0}, start=200_000,         # (each word taken at
                     host=[(60_000, 10 << 16 | 0x1234), (100_000, 11 << 16 | 0x5678),  # a 1 ms
                           (140_000, 64 << 16 | 1)])                  # poll while idle)
    assert cl.errors() == [] and regs(bytes(cl.replies[0]["bytes"])) == [0x1234, 0x5678]
    assert m.irq_pending & 1 and m.ctx[0].irq_code == 4


def test_notice_lost_when_mailbox_full():
    """A host that does not drain the mailbox: the first two notices wait there, the
    third is lost (IRQ 2); every write is still done and answered."""
    reqs = [(write1(1, k, 0x100 + k), True) for k in range(3)] + [(read(1, 0, 3), True)]
    cl, m, notes = serve(reqs, tape={0: 9, 1: 9, 2: 9}, drain=False)
    assert cl.errors() == [] and len(cl.replies) == 4
    assert regs(bytes(cl.replies[3]["bytes"])) == [0x100, 0x101, 0x102]
    assert list(m.ctx[0].tx) == [0 | 1 << 16, 1 | 1 << 16]
    assert m.irq_pending & 1 and m.ctx[0].irq_code == 2


def test_frames_for_others_back_to_back():
    """A 123-byte frame for another server, then one for this server 3.5 characters after
    it: the server answers (it ignores the first by its address byte, at once)."""
    S = round(t35(115200)) + 500
    reqs = [(write_n(7, 0, [0x0F0F] * 57), False, S), (read(1, 1, 1), True),
            (read(9, 0, 1), False, S), (read(1, 1, 1), True)]
    cl, m, notes = serve(reqs, tape={1: 0x0A0B})
    assert cl.errors() == [] and [r["req"] for r in cl.replies] == [1, 3]
    assert all(regs(bytes(r["bytes"])) == [0x0A0B] for r in cl.replies)
