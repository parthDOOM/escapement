"""firmware/eth_rx.asm, the 10BASE-T receiver (v3: the line unit's cell decoder and the
both-edge pin sampler), on the ISS at 50 MHz: 802.3 frames from escmodels.EthSource with
the transmitter's clock off by 100 ppm either way and every edge moved by up to 10 ns,
each received byte for byte with its FCS judged as zlib's CRC-32 judges it. With one
sample a tick (BOTH = 0) the same jitter puts cell boundaries past the mask point; with
none it decodes too, so the half-tick sample is what buys the margin. (Measured on these
frames, 4 seeds x +-100 ppm: both edges decode all to +-10 ns an edge, one sample a tick
all to +-5 ns and few at +-7.5; at +-12.5 ns an edge, 25 ns between two edges, neither can,
being past the margin a 3/4-bit mask point has.)"""

from __future__ import annotations

import random
import zlib
from pathlib import Path

import pytest
from escasm import assemble
from escmodels import Board, EthSource, HostScript, wire_bits
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
RXP = 2
BIT_HT = 10                                    # 100 ns at 50 MHz, in half ticks


def image(both: int = 1):
    return assemble(f".equ eth_rx.BOTH, {both}\n" + '.include "eth_rx.asm"\n',
                    base_dir=FW, name="eth_rx.asm")


def frames(seed: int = 1) -> list[bytes]:
    rng = random.Random(seed)
    def body(n):
        return (bytes.fromhex("ffffffffffff 020000000001 0800")
                + bytes(rng.randrange(256) for _ in range(n - 14)))
    return [body(60), body(300), body(880)]


def receive(plays, both=1, ppm=0.0, jitter=0.0, seed=1, gap=4000):
    """Runs eth_rx against `plays` (a list of wire bit lists), one after another with
    `gap` ticks between; returns the host's receptions as (header, bytes)."""
    sched, t = [], 2000
    for bits in plays:
        sched.append((t, bits))
        t += len(bits) * BIT_HT // 2 + gap
    src = EthSource(RXP, sched, BIT_HT, ppm=ppm, jitter=jitter, seed=seed)
    host = HostScript([], drain=[0], poll=4)
    m = Machine(image(both), board=Board(src, host), half=src.half)
    host.machine = m
    m.run(t + 8000, stop_when_idle=False)          # (and the host drains the last frame)
    words, out = host.received[0], []
    while words:
        h = words.pop(0)
        n = ((h & 0x3FFF) + 31) // 32
        data = b"".join(w.to_bytes(4, "little") for w in words[:n])
        del words[:n]
        out.append((h, data[:(h & 0x3FFF) // 8]))
    return out


def good(h):
    return h >> 31 == 1


@pytest.mark.parametrize("ppm", [100.0, -100.0])
def test_eth_rx_frames_with_offset_and_jitter(ppm):
    fs = frames()
    bad = bytearray(fs[1])
    bad[40] ^= 0x10                              # one bit flipped after the FCS was made
    plays = [wire_bits(f) for f in fs]
    plays.insert(2, wire_bits(fs[1])[:64] + wire_bits(bytes(bad), fcs=False)[64:]
                 + wire_bits(fs[1])[-32:])       # the frame with the original FCS
    got = receive(plays, ppm=ppm, jitter=1.0)
    assert len(got) == 4
    for (h, data), f in zip([got[0], got[1], got[3]], fs):
        assert good(h) and (h >> 28) & 7 == 0, hex(h)
        assert (h & 0x3FFF) == 8 * (len(f) + 4)
        assert data == f + zlib.crc32(f).to_bytes(4, "little")
    h, data = got[2]
    assert not good(h) and (h & 0x3FFF) == 8 * 304 and data[:300] == bytes(bad)


def test_eth_rx_a_frame_over_the_buffer_is_cut_and_flagged():
    big = frames()[2] + bytes(range(100))        # 980 bytes: more than the 896-byte buffer
    (h, data), = receive([wire_bits(big)])
    assert (h >> 28) & 7 == 4 and not good(h)    # FULL
    assert (h & 0x3FFF) == 8 * 896 and data == big[:896]


def test_eth_rx_one_sample_a_tick_is_not_enough_for_the_jitter():
    plays = [wire_bits(f) for f in frames(2)]
    # a transmitter's clock is never locked to the chip's: the offset walks each edge
    # through every phase of the tick, the worst one included
    clean = receive(plays, both=0, ppm=100.0)
    assert [good(h) for h, _ in clean] == [True] * 3          # no jitter: it decodes
    jittered = receive(plays, both=0, ppm=100.0, jitter=1.0, seed=3)
    assert sum(good(h) for h, _ in jittered) < 3               # boundaries past the mask
    assert [good(h) for h, _ in receive(plays, both=1, ppm=100.0, jitter=1.0, seed=3)] == [True] * 3


def test_the_cosim_case_needs_the_falling_edge_sample():
    """test/cosim/fw_cases.py case_eth_rx's frames and seed: they decode with the half-tick
    sample and not without it (the falling-edge sample taken equal to the rising one, as a
    core that ignored pin_h would see), so the whole-chip case checks pin_h, not just the
    decoder."""
    head = bytes.fromhex("ffffffffffff 020000000001 0800")
    fa, fl = head + bytes(range(46)), head + bytes((i * 37) & 0xFF for i in range(386))
    sched, t = [], 1500
    for f in (fa, fl):
        bits = wire_bits(f)
        sched.append((t, bits))
        t += len(bits) * 5 + 2500
    verdicts = []
    for blind in (False, True):
        src = EthSource(RXP, sched, BIT_HT, ppm=100.0, jitter=1.0, seed=7)
        host = HostScript([], drain=[0], poll=4)
        m = Machine(image(1), board=Board(src, host),
                    half=(lambda t, b, c: c) if blind else src.half)
        host.machine = m
        m.run(t + 3000, stop_when_idle=False)
        w, v = list(host.received[0]), []
        while w:
            h = w.pop(0)
            del w[:((h & 0x3FFF) + 31) // 32]
            v.append(h >> 31)
        verdicts.append(v)
    assert verdicts == [[1, 1], [1, 0]]
