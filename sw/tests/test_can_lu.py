"""firmware/can_tx_lu.asm: CAN 2.0A at 1 Mbit/s on the line unit (v1.6), against the CAN
bus model of escmodels (it destuffs, checks the CRC-15 and acknowledges), at 40 MHz."""

from __future__ import annotations

from pathlib import Path

from escasm import assemble
from escmodels import Board, CanBus
from escmodels.board import Device
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
KHZ, BIT = 40000, 40                          # 1 Mbit/s: 40 ticks a bit
FRAMES = [(0x123, 2, 0xDEAD0000, 0), (0x7FF, 8, 0x01234567, 0x89ABCDEF), (0x000, 0, 0, 0),
          (0x555, 5, 0xCAFEF00D, 0x5A000000)]


class Competitor(Device):
    """Another node: pulls the bus dominant during [t0, t1) (it wins arbitration there)."""

    def __init__(self, rx: int, t0: int, t1: int):
        self.rx, self.t0, self.t1 = rx, t0, t1

    def step(self, t, bus):
        return ((1 << self.rx) if self.t0 <= t < self.t1 else 0), 0


def run(frames, ack=True, extra=()):
    bus = CanBus(0, 1, BIT, ack=ack)
    m = Machine(assemble(f'.equ can_tx_lu.CLK_KHZ, {KHZ}\n.include "can_tx_lu.asm"\n',
                         base_dir=FW), board=Board(bus, *extra))
    out = []
    m.run(15 * BIT, stop_when_idle=False)       # the bus idle (11 recessive bits) first
    for ident, dlc, d03, d47 in frames:
        for w in ((ident << 16) | dlc, d03, d47):
            while len(m.ctx[0].rx) >= 2:
                m.run(50, stop_when_idle=False)
            m.host_rx_push(0, w)
            m.run(1, stop_when_idle=False)
        while not m.ctx[0].tx:
            m.run(100, stop_when_idle=False)
        out.append(m.host_tx_pop(0))
        m.run(20 * BIT, stop_when_idle=False)
    return m, bus, out


def want(ident, dlc, d03, d47):
    data = (d03.to_bytes(4, "big") + d47.to_bytes(4, "big"))[:min(dlc, 8)]
    return ident, dlc, list(data)


def test_frames_at_1_mbit_are_received_and_acknowledged():
    m, bus, acks = run(FRAMES)
    got = [(f["id"], f["dlc"], f["data"]) for f in bus.frames]
    assert got == [want(*f) for f in FRAMES] and not bus.violations, bus.violations
    assert all(f["crc_ok"] for f in bus.frames)
    assert acks == [1] * len(FRAMES) and not m.ff.valid


def test_no_acknowledgement_is_reported():
    m, bus, acks = run(FRAMES[:2], ack=False)
    assert acks == [0, 0] and len(bus.frames) == 2


def test_lost_arbitration_sends_the_frame_again_after_11_recessive_bits():
    """A node holding the bus dominant during one bit of the identifier wins arbitration:
    the line unit's monitor stops the frame there, and the firmware sends it again once
    the bus has been recessive for 11 bit times."""
    m0, _, _ = run(FRAMES[:1])
    # where the first frame starts with no competitor: the first TXD edge after CONFIG
    lv = [p["uio_out"] & 1 for p in m0.pins]
    t_sof = next(t for t in range(1, len(lv)) if lv[t] == 0 and lv[t - 1] == 1)
    t_bit = t_sof + 3 * BIT + BIT // 2                              # inside identifier bit 2
    comp = Competitor(1, t_bit - BIT // 2, t_bit + BIT // 2)
    m, bus, acks = run(FRAMES[:1], extra=(comp,))
    lv = [p["uio_out"] & 1 for p in m.pins]
    sofs = [t for t in range(1, len(lv)) if lv[t] == 0 and lv[t - 1] == 1]
    assert len(sofs) >= 2 and sofs[1] - t_sof >= 11 * BIT             # sent again, after idle
    assert [(f["id"], f["dlc"], f["data"]) for f in bus.frames if f["crc_ok"]][-1] == want(*FRAMES[0])
    assert acks == [1] and not m.ff.valid
