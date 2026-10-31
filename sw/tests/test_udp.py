"""firmware/udp_tx.asm (v1.6): UDP datagrams over IPv4 over 10BASE-T, decoded from the
pins as 802.3 Manchester and checked as a receiving network stack would: the frame check
sequence (zlib's CRC-32), the IPv4 header and its checksum, the UDP header, the payload."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from escasm import assemble
from esciss import Machine

from test_eth import MS, frame_bits
from test_line_ext import TD_P, levels, to_bytes

FW = Path(__file__).resolve().parents[2] / "firmware"


def ones_sum(b: bytes) -> int:
    s = sum(struct.unpack(f"!{len(b) // 2}H", b))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return s


def send(payload: bytes, equ: str = ""):
    m = Machine(assemble(equ + '.include "udp_tx.asm"\n', base_dir=FW))
    m.run(MS // 10, stop_when_idle=False)                        # past the first link pulse
    padded = payload + b"\0" * (-len(payload) % 4)
    queue = [len(payload)] + [int.from_bytes(padded[i:i + 4], "little")
                              for i in range(0, len(padded), 4)]
    status = None
    while status is None:
        while queue and len(m.ctx[0].rx) < 2:
            m.host_rx_push(0, queue.pop(0))
            m.run(1, stop_when_idle=False)
        m.run(500, stop_when_idle=False)
        status = m.host_tx_pop(0)
    p = levels(m, TD_P)
    t0 = next(t for t in range(MS // 10, len(p)) if p[t][0])
    bits, _ = frame_bits(m, t0)
    data = to_bytes(bits)
    assert data[:8] == b"\x55" * 7 + b"\xd5"
    assert (status >> 25) & 7 == 0 and (status >> 28) & 7 == 4         # SEND OK
    return data[8:]


def check(frame: bytes, payload: bytes, src_ip="192.168.1.100", dst_ip="255.255.255.255",
          ports=(5000, 5000), dst_mac="ffffffffffff", src_mac="020000000001"):
    body, fcs = frame[:-4], frame[-4:]
    assert len(body) == max(60, 42 + len(payload))
    assert int.from_bytes(fcs, "little") == zlib.crc32(body)
    assert body[0:6].hex() == dst_mac and body[6:12].hex() == src_mac
    assert body[12:14] == b"\x08\x00"
    ip = body[14:34]
    ver_ihl, _, total, ident, frag, ttl, proto, _, src, dst = struct.unpack("!BBHHHBBH4s4s", ip)
    assert ver_ihl == 0x45 and total == 28 + len(payload) and frag == 0x4000
    assert ttl == 64 and proto == 17
    assert ones_sum(ip) == 0xFFFF                                        # the header checksum
    assert ".".join(map(str, src)) == src_ip and ".".join(map(str, dst)) == dst_ip
    sport, dport, ulen, ucsum = struct.unpack("!HHHH", body[34:42])
    assert (sport, dport) == ports and ulen == 8 + len(payload) and ucsum == 0
    assert body[42:42 + len(payload)] == payload
    assert body[42 + len(payload):] == b"\0" * (len(body) - 42 - len(payload))   # padding


@pytest.mark.parametrize("n", [0, 1, 5, 18, 19, 100, 916])
def test_datagram(n):
    payload = bytes((7 * k + 3) & 255 for k in range(n))
    check(send(payload), payload)


def test_addresses_and_ports_from_the_assembler():
    equ = (".equ udp_tx.DMAC_HI, 0x0011\n.equ udp_tx.DMAC_LO, 0x22334455\n"
           ".equ udp_tx.SIP, 0x0A000002\n.equ udp_tx.DIP, 0x0A0000FE\n"
           ".equ udp_tx.SPORT, 40000\n.equ udp_tx.DPORT, 53\n")
    payload = b"hello from escapement"
    check(send(payload, equ), payload, src_ip="10.0.0.2", dst_ip="10.0.0.254",
          ports=(40000, 53), dst_mac="001122334455")
