"""BiSS C master (firmware/biss_c.asm, held-out round) against the slave model
escmodels.biss.BissSlave, written apart from the firmware from the BiSS C Protocol
Description Rev D2 and AN3 alone. The model plays one sensor and the line: it answers the
master's clock with ACK, start bit, CDS, the data channel and its inverted 6-bit CRC, the
stop bit and the timeout, with a line delay, a processing time and a static or adaptive
timeout; it checks the clock (Table 10 Nos. 1, 3, 4), complete frames, the timeout handshake
and the 40 us idle after power-up. What the master should report is decided here."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import BissSlave, Board, HostScript
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
CRC_OK, NO_ACK, NO_START, NO_TO, CDS, NO_STOP = 1, 2, 4, 8, 16, 32


def crc6(value, nbits):
    """AN3: x^6 + x + 1 (0x43), start value 0, over the data bits MSB first; sent inverted."""
    crc = 0
    for i in range(nbits - 1, -1, -1):
        fb = ((value >> i) & 1) ^ (crc >> 5)
        crc = ((crc << 1) & 0x3F) ^ (0x03 if fb else 0)
    return crc ^ 0x3F


def image(khz=25000, ma=250, dlen=26, patch=()):
    src = (FW / "biss_c.asm").read_text()
    for a, b in patch:
        assert src.count(a) == 1, a
        src = src.replace(a, b)
    head = f".equ CLK_KHZ, {khz}\n.equ MA_KHZ, {ma}\n.equ DLEN, {dlen}\n"
    return assemble(head + src, base_dir=FW, name="biss_c.asm")


def session(n=3, khz=25000, ma=250, dlen=26, patch=(), every_us=400, **kw):
    pushes = [(khz // 10 + i * every_us * khz // 1000, 0, 1) for i in range(n)]
    host = HostScript(pushes, drain=[0], poll=khz // 100)
    slave = BissSlave(0, 1, tick_ns=1e6 / khz, dlen=dlen, **kw)
    m = Machine(image(khz, ma, dlen, patch), board=Board(slave, host))
    host.machine = m
    m.run(khz // 10 + (n + 1) * every_us * khz // 1000, stop_when_idle=False)
    slave.finish()
    return slave, m, host.received[0]


def replies(got):
    return list(zip(got[0::2], got[1::2]))


def clean(slave, m):
    assert not m.ff.valid, m.ff
    assert not slave.errors(), slave.errors()[:5]


def test_crc6_reference():
    """The test's own CRC against a value worked by hand from AN3's definition."""
    assert crc6(0, 26) == 0x3F and crc6(1, 1) == 0x3F ^ 0x03


@pytest.mark.parametrize("khz,ma", [(25000, 250), (50000, 500), (16000, 181)])
def test_reads_position(khz, ma):
    data = [0x2ABCDE7, 0x0000003, 0x3FFFFFF]
    slave, m, got = session(3, khz, ma, data=data)
    clean(slave, m)
    r = replies(got)
    assert [d for _, d in r] == data
    assert all(s & 0xFF == CRC_OK for s, _ in r), [hex(s) for s, _ in r]
    assert [f["data"] for f in slave.frames] == data and all(f["complete"] for f in slave.frames)


@pytest.mark.parametrize("delay_ns,clocks", [(0, 1), (1300, 1), (9000, 3)])
def test_line_delay_compensated(delay_ns, clocks):
    """Line delay (No. 7): the master clocks until ACK; whole clocks are counted."""
    slave, m, got = session(2, data=[0x1234567, 0x3654321], line_delay_ns=delay_ns)
    clean(slave, m)
    r = replies(got)
    assert [d for _, d in r] == [0x1234567, 0x3654321]
    assert all(s & 0xFF == CRC_OK and (s >> 8) == clocks for s, _ in r), [hex(s) for s, _ in r]


@pytest.mark.parametrize("busy_ns,busy_clocks", [(10_000, 0), (0, 3), (39_000, 8)])
def test_processing_time(busy_ns, busy_clocks):
    """A start bit delayed by the slave's processing time (No. 10a, 10b)."""
    slave, m, got = session(2, data=0x15A5A5A, busy_ns=busy_ns, busy_clocks=busy_clocks)
    clean(slave, m)
    assert replies(got) == [(CRC_OK | 1 << 8, 0x15A5A5A)] * 2


@pytest.mark.parametrize("timeout,tto", [("static", 20.0), ("static", 40.0), ("adaptive", 20.0)])
def test_timeouts(timeout, tto):
    slave, m, got = session(3, data=[1, 2, 3], timeout=timeout, tto_us=tto)
    clean(slave, m)
    assert [d for _, d in replies(got)] == [1, 2, 3]


@pytest.mark.parametrize("dlen", [1, 13, 31])
def test_data_lengths(dlen):
    data = [(1 << dlen) - 1, 0x55555555 & ((1 << dlen) - 1)]
    slave, m, got = session(2, dlen=dlen, data=data, cds=1)
    clean(slave, m)
    assert replies(got) == [(CRC_OK | CDS | 1 << 8, d) for d in data]


def test_faults_reported():
    slave, m, got = session(4, data=[10, 11, 12, 13], faults={"crc": 0, "silent": 1, "no_start": 2})
    assert not m.ff.valid
    r = replies(got)
    assert r[0][0] & CRC_OK == 0 and r[0][1] == 10
    assert r[1] == (NO_ACK, 0)
    assert r[2][0] & 0xFF == NO_START and r[2][1] == 0
    assert r[3] == (CRC_OK | 1 << 8, 13)


def back_to_back(fault, patch=()):
    """Two requests at once at 50 MHz, 500 kHz, with the shortest static timeout (12.5 us):
    the next frame starts as soon as the program allows."""
    host = HostScript([(5000, 0, 1), (5001, 0, 1)], drain=[0], poll=250)
    slave = BissSlave(0, 1, tick_ns=20.0, data=[10, 11], faults=fault, tto_us=12.5)
    m = Machine(image(50000, 500, patch=patch), board=Board(slave, host))
    host.machine = m
    m.run(120_000, stop_when_idle=False)
    slave.finish()
    return slave, m, host.received[0]


@pytest.mark.parametrize("fault", [{"crc": 0}, {"silent": 0}, {"no_start": 0}])
def test_break_after_an_error(fault):
    """After an error frame the master keeps MA high 40 us (Bus Reset/Initialization,
    Application Hints step 5) before the next frame."""
    slave, m, got = back_to_back(fault)
    clean(slave, m)
    assert replies(got)[1] == (CRC_OK | 1 << 8, 11)


# ------------------------------------------------------------ negative controls

def test_negative_control_frame_cut_short():
    """One CRC clock fewer: the master stops before the frame is complete."""
    slave, m, got = session(2, data=5, patch=[(".equ NB, 6 ", ".equ NB, 5 ")])
    assert slave.errors()


def test_negative_control_clock_high_too_short():
    """MA high for 2 ticks (80 ns) in the data clocks at 250 kHz: off No. 3/4's duty."""
    patch = [("da:\n    ADDT  HT\n    EMIT  MA, 0, MA\n    ADDT  HT - 2\n",
              "da:\n    ADDT  2\n    EMIT  MA, 0, MA\n    ADDT  2 * HT - 4\n")]
    slave, m, got = session(1, data=5, patch=patch)
    assert slave.errors()


def test_negative_control_no_timeout_wait():
    """The next frame started without waiting for SL to rise after the timeout."""
    wait = "    WAITP 1 << SL_PIN, 1 << SL_PIN   ; the slave's timeout ends: SL rises\n"
    patch = [("    ADDT  TO_WAIT\n" + wait, "    ADDT  4\n" + wait)]
    slave, m, got = session(3, data=5, patch=patch, every_us=20)
    assert slave.errors()


def test_negative_control_no_break_after_crc_error():
    """The 40 us break after a CRC error left out."""
    slave, m, got = back_to_back({"crc": 0}, patch=[("    ADDI  r1, BRK ", "    ADDI  r1, 4 ")])
    assert any("40 us" in e for e in slave.errors()), slave.errors()


# ------------------------------------------------------------ certificate

@pytest.mark.parametrize("khz,ma", [(25000, 250), (50000, 500), (16000, 181), (25000, 347)])
def test_certificate(khz, ma):
    from escert import certify
    assert [c.status for c in certify(image(khz, ma))] == ["CHECKED"]


def test_clock_too_fast_refused():
    from escasm import AsmError
    with pytest.raises(AsmError):
        image(25000, 400)
