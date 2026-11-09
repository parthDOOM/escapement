"""nRF24L01+ transmitter driver (firmware/nrf24_tx.asm) against a radio model written from the
nRF24L01+ Product Specification (escmodels/nrf24.py), at 25 MHz with SCK at 500 kHz."""

from __future__ import annotations

from pathlib import Path

from escasm import assemble
from escmodels import Board, HostScript
from escmodels.nrf24 import Nrf24Ptx
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
KHZ = 25000
MS = KHZ
ADDR = bytes([0x11, 0x22, 0x33, 0x44, 0x55])          # LSByte first


def run(pushes, ms, acks=None):
    im = assemble(f'.equ nrf24_tx.CLK_KHZ, {KHZ}\n.include "nrf24_tx.asm"\n', base_dir=FW)
    radio = Nrf24Ptx(4, 5, 6, 7, 3, 2, 1e6 / KHZ, acks=acks)
    host = HostScript(pushes, drain=[0], poll=2500)
    board = Board(radio, host)
    m = Machine(im, board=board)
    host.machine = m
    m.run(int(ms * MS), stop_when_idle=False)
    return m, radio, host, board


def setup(retr=0x13, ch=76, rf=0x06):
    """ARD 500 us, ARC 3; channel 76; 1 Mbps, 0 dBm."""
    return [(100, 0, 1 << 28 | rf << 16 | retr << 8 | ch),
            (3 * MS, 0, 2 << 28 | ADDR[4]), (3 * MS + 10, 0, int.from_bytes(ADDR[:4], "big"))]


def payload_words(data):
    data = data + bytes(-len(data) % 4)
    return [int.from_bytes(data[i:i + 4], "big") for i in range(0, len(data), 4)]


def send(t, data):
    return [(t, 0, 3 << 28 | len(data))] + [(t + 2000 * (k + 1), 0, w) for k, w in enumerate(payload_words(data))]


PAYLOAD = bytes(range(0x40, 0x60))


def test_setup_address_and_a_packet():
    m, radio, host, board = run(setup() + send(5 * MS, PAYLOAD), 9)
    assert host.received[0] == [0x0E, 0x20], host.received
    r = radio.reg
    assert (r[0x00], r[0x01], r[0x02], r[0x04], r[0x05], r[0x06]) == (0x0E, 0x01, 0x01, 0x13, 76, 0x06)
    assert r[0x10] == ADDR and r[0x0A] == ADDR
    assert [(s["payload"], s["ch"], s["addr"]) for s in radio.sent] == [(PAYLOAD, 76, ADDR)]
    assert r[0x07] & 0x70 == 0 and not radio.fifo
    assert not radio.violations and not board.errors() and not m.ff.valid, radio.violations


def test_retransmits_until_acknowledged():
    """Two attempts lost, the third acknowledged: three on air, ARD 500 us apart."""
    data = b"hello"
    m, radio, host, board = run(setup() + send(5 * MS, data), 10, acks=[False, False, True])
    assert host.received[0] == [0x0E, 0x20]
    assert [s["payload"] for s in radio.sent] == [data] * 3
    t = [s["start"] for s in radio.sent]
    toa = (8 * (1 + 5 + 5 + 2) + 9)                     # us at 1 Mbps
    assert all(abs(b - a - (toa + 500)) < 1 for a, b in zip(t, t[1:]))
    assert not radio.violations, radio.violations


def test_no_ack_then_the_next_packet_goes():
    """No ACK in 1 + ARC attempts: MAX_RT, FLUSH_TX, STATUS cleared; the next packet goes."""
    m, radio, host, board = run(setup() + send(5 * MS, b"lost") + send(12 * MS, b"next"), 16,
                                acks=[False] * 4 + [True])
    assert host.received[0] == [0x0E, 0x10, 0x20], host.received
    assert [s["payload"] for s in radio.sent] == [b"lost"] * 4 + [b"next"]
    assert (0xE1, b"") in radio.commands[-6:]
    assert not radio.violations, radio.violations


def test_send_right_after_setup():
    """A packet queued behind the set-up: CE only Tpd2stby (1.5 ms) after PWR_UP."""
    m, radio, host, board = run([(100, 0, 1 << 28 | 0x06 << 16 | 0x13 << 8 | 2)] + send(110, b"now"), 4)
    assert host.received[0] == [0x0E, 0x20] and [s["payload"] for s in radio.sent] == [b"now"]
    assert radio.sent[0]["start"] - 130 - radio._t_pu >= 1500
    assert not radio.violations, radio.violations


def test_certificate_matches_telemetry():
    from escert import certify
    im = assemble(f'.equ nrf24_tx.CLK_KHZ, {KHZ}\n.include "nrf24_tx.asm"\n', base_dir=FW)
    assert [(c.status, c.min_lead) for c in certify(im)] == [("CHECKED", 28)]
