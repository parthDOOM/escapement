"""M-Bus slave (firmware/mbus_slave.asm, held-out round) against the master model
escmodels.mbus.MbusMaster, written apart from the firmware from the M-Bus Usergroup's
documentation (Rev. 4.8) alone. The model is the master, the bus and the transceiver at
logic level: it sends a script of telegrams, optionally corrupted, and checks the slave's
characters, its reply format and window, and that it answers what it must and nothing else.
What the slave should report to its host is decided here.

Most runs use a 4 MHz clock (a bit at 2400 baud is 1,667 ticks); escert proves the program
from 4 to 50 MHz and 300 to 38400 baud."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import Board, HostFeed, MbusMaster
from escmodels.mbus import long_frame, short_frame
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
ADDR = 5
SND_NKE, REQ_UD2, SND_UD = 0x40, 0x7B, 0x53
HEADER = [0x78, 0x56, 0x34, 0x12, 0x2D, 0x2C, 0x01, 0x07, 0x00, 0x00, 0x00, 0x00]   # 6.3.1
RECORDS = [0x04, 0x13, 0x39, 0x30, 0x00, 0x00]          # DIF 04 (32 bit), VIF 13 (volume l)
RSP = [0x72] + HEADER + RECORDS


def image(khz=4000, baud=2400, addr=ADDR, patch=()):
    src = (FW / "mbus_slave.asm").read_text()
    for a, b in patch:
        assert src.count(a) == 1, a
        src = src.replace(a, b)
    head = f".equ CLK_KHZ, {khz}\n.equ BAUD, {baud}\n.equ ADDR, {addr}\n"
    return assemble(head + src, base_dir=FW, name="mbus_slave.asm")


def rsp_words(data=RSP):
    return [2 << 24 | len(data)] + list(data)


def session(telegrams, khz=4000, baud=2400, addr=ADDR, host=(), patch=(), ms=None, **kw):
    """The host's words go first (flow controlled); the master starts after start_ms."""
    feed = HostFeed({0: list(host)}, drain=[0], poll=max(1, khz // 10))
    kw.setdefault("slave_address", addr)
    kw.setdefault("start_ms", 2.0)
    master = MbusMaster(0, 1, tick_ns=1e6 / khz, baud=baud, telegrams=telegrams, **kw)
    m = Machine(image(khz, baud, addr, patch), board=Board(master, feed))
    feed.machine = m
    if ms is None:
        bits = sum(11 * (len(t) + 3 + len(RSP) + 10) + 400 for t in telegrams)
        ms = kw["start_ms"] + bits * 1000 / baud + 60 * len(telegrams)
    m.run(int(ms * khz), stop_when_idle=False)
    master.finish()
    return master, m, feed.received[0]


def clean(master, m):
    assert not m.ff.valid, m.ff
    assert not master.errors(), master.errors()[:5]


def note(kind, a, c, n=0):
    return kind << 24 | a << 16 | c << 8 | n


def test_snd_nke_answered():
    master, m, got = session([short_frame(SND_NKE, ADDR)])
    clean(master, m)
    x = master.exchanges[0]
    assert x["reply"] == b"\xE5" and x["reply_valid"]
    assert 11 <= x["reply_delay_bits"] <= 24
    assert got == [note(1, ADDR, SND_NKE)]


def test_req_ud2_answered_with_the_hosts_data():
    master, m, got = session([short_frame(SND_NKE, ADDR), short_frame(REQ_UD2, ADDR),
                              short_frame(0x5B, ADDR)], host=rsp_words())
    clean(master, m)
    for x in master.exchanges[1:]:
        assert x["reply_kind"] == "long" and x["reply_valid"]
        p = x["parsed"]
        assert p["C"] == 0x08 and p["A"] == ADDR and p["CI"] == 0x72
        assert list(x["reply"][7:-2]) == HEADER + RECORDS
    assert got == [note(1, ADDR, SND_NKE), note(2, ADDR, REQ_UD2), note(2, ADDR, 0x5B)]


def test_default_response_before_the_host_gives_one():
    """A CI 72h telegram with a 12-byte header of zeros."""
    master, m, got = session([short_frame(REQ_UD2, ADDR)])
    clean(master, m)
    assert list(master.exchanges[0]["reply"][6:-2]) == [0x72] + [0] * 12


def test_snd_ud_acknowledged_and_passed_to_the_host():
    data = [0x01, 0x7A, 0x09]                       # e.g. DIF 01, VIF 7A: an address
    master, m, got = session([long_frame(SND_UD, ADDR, 0x51, bytes(data)),
                              long_frame(0x73, ADDR, 0x50)])
    clean(master, m)
    assert [x["reply"] for x in master.exchanges] == [b"\xE5", b"\xE5"]
    assert got == [note(3, ADDR, SND_UD, 4), 0x51] + data + [note(3, ADDR, 0x73, 1), 0x50]


def test_test_address_254_answered_with_own_address():
    master, m, got = session([short_frame(SND_NKE, 254), short_frame(REQ_UD2, 254)],
                             host=rsp_words())
    clean(master, m)
    assert master.exchanges[0]["reply"] == b"\xE5"
    assert master.exchanges[1]["parsed"]["A"] == ADDR
    assert got == [note(1, 254, SND_NKE), note(2, 254, REQ_UD2)]


def test_broadcast_255_acted_on_not_answered():
    master, m, got = session([short_frame(SND_NKE, 255), long_frame(SND_UD, 255, 0x50),
                              short_frame(REQ_UD2, 255), short_frame(SND_NKE, ADDR)])
    clean(master, m)
    assert [x["reply"] for x in master.exchanges] == [b"", b"", b"", b"\xE5"]
    assert got == [note(1, 255, SND_NKE), note(3, 255, SND_UD, 1), 0x50, note(1, ADDR, SND_NKE)]


def test_other_address_ignored():
    master, m, got = session([short_frame(SND_NKE, ADDR + 1), short_frame(REQ_UD2, 0),
                              short_frame(SND_NKE, ADDR)])
    clean(master, m)
    assert [x["reply"] for x in master.exchanges] == [b"", b"", b"\xE5"]
    assert got == [note(1, ADDR, SND_NKE)]


def test_primary_address_set_by_the_host():
    master, m, got = session([short_frame(SND_NKE, 7), short_frame(SND_NKE, ADDR)],
                             host=[1 << 24 | 7], slave_address=7)
    clean(master, m)
    assert [x["reply"] for x in master.exchanges] == [b"\xE5", b""]


@pytest.mark.parametrize("fault", [{"parity": (0, 2)}, {"stop": (0, 1)}, {"checksum": 0},
                                   {"stop_char": 0}, {"length": 0}, {"pause": (0, 3, 30)}])
def test_faulty_telegrams_not_answered(fault):
    """5.4: a telegram failing a check gets no reply; the next good one is answered."""
    first = long_frame(SND_UD, ADDR, 0x51, b"\x01\x7A\x09") if "length" in fault \
        else short_frame(SND_NKE, ADDR)
    master, m, got = session([first, short_frame(SND_NKE, ADDR)], faults=fault)
    clean(master, m)
    assert [x["reply"] for x in master.exchanges] == [b"", b"\xE5"]
    assert got == [note(1, ADDR, SND_NKE)]


@pytest.mark.parametrize("khz,baud", [(4000, 300), (25000, 9600), (50000, 38400),
                                      (25000, 2400)])
def test_rates(khz, baud):
    master, m, got = session([short_frame(SND_NKE, ADDR), short_frame(REQ_UD2, ADDR)], khz,
                             baud, host=rsp_words())
    clean(master, m)
    assert master.exchanges[0]["reply"] == b"\xE5"
    assert master.exchanges[1]["reply_valid"] and master.exchanges[1]["parsed"]["CI"] == 0x72


# ------------------------------------------------------------ negative controls

def test_negative_control_reply_too_early():
    """The reply 5 bit times after the telegram (11 at least)."""
    patch = [(".equ REPLY_T, REPLY_BITS * B\n", ".equ REPLY_T, 5 * B - B + HB\n")]
    master, m, got = session([short_frame(SND_NKE, ADDR)], patch=patch)
    assert master.errors()


def test_negative_control_parity_odd():
    """The reply's characters with the parity bit inverted (odd parity)."""
    patch = [("    MOVI  r1, 0x400            ; [10] stop (Mark)\n    OR    r0, r1\n",
              "    MOVI  r1, 0x600            ; [10] stop (Mark)\n    XOR   r0, r1\n")]
    master, m, got = session([short_frame(SND_NKE, ADDR)], patch=patch)
    assert master.errors()


def test_negative_control_answers_broadcast():
    """E5h sent to a SND_NKE for address 255 (the broadcast test never matches)."""
    patch = [("    CMPI  r1, 0xFF\n    JZ    after                ; broadcast: no answer\n",
              "    CMPI  r1, 0x1FF\n    JZ    after                ; broadcast: no answer\n")]
    master, m, got = session([short_frame(SND_NKE, 255)], patch=patch)
    assert master.errors()


# ------------------------------------------------------------ certificate

@pytest.mark.parametrize("khz,baud", [(25000, 2400), (25000, 300), (4000, 2400),
                                      (50000, 38400), (25000, 9600)])
def test_certificate(khz, baud):
    from escert import certify
    assert [c.status for c in certify(image(khz, baud))] == ["CHECKED"]


@pytest.mark.parametrize("over", [".equ BAUD, 57600\n", ".equ ADDR, 251\n",
                                  ".equ REPLY_BITS, 10\n"])
def test_out_of_range_refused(over):
    from escasm import AsmError
    with pytest.raises(AsmError):
        assemble(over + (FW / "mbus_slave.asm").read_text(), base_dir=FW)
