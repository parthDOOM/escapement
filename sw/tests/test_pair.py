"""v1.7 pair mode: a context also takes the slot of its partner (k ^ 2) when that one is
off. The assembler directive, the image and WRITE_CFG fields, the host's version guard, and
the instruction rate on the ISS (the RTL is checked against the ISS in test/cosim)."""

from __future__ import annotations

import pytest

from escasm import AsmError, Image, assemble
from eschost.protocol import Host, plan_write_cfg
from esciss import Machine

LOOP = """
.entry {k}, s
.own 0, {k}
s:  LDLC 100
l:  NOP
    DJNZ l
    HALT
"""


def test_pair_directive_image_and_json():
    im = assemble(".pair 1\n" + LOOP.format(k=1))
    assert im.ctx_pair == [False, True, False, False] and im.ctx_pair_mask == 0b0010
    back = Image.from_json(im.to_json())
    assert back.ctx_pair == im.ctx_pair
    old = Image.from_json(assemble(LOOP.format(k=1)).to_json().replace('"ctx_pair"', '"x"'))
    assert old.ctx_pair == [False] * 4               # images from v1.6 tools: no pair
    with pytest.raises(AsmError, match=r"\.pair ctx"):
        assemble(".pair 4\n" + LOOP.format(k=1))
    with pytest.raises(AsmError, match="context 3 is enabled"):
        assemble(".pair 1\n.entry 3, s\n" + LOOP.format(k=1))


def test_write_cfg_byte_10_carries_pair_beside_bank_b():
    frame, _ = plan_write_cfg(0x5, 0x04030201, 0x15F0, 0x2A5BEEF, 0x81, 0b0110, 0b1001)
    assert frame[10] == 0b1001_0110
    frame, _ = plan_write_cfg(0x5, 0x04030201, 0x15F0, 0x2A5BEEF, 0x81, 0b0110)
    assert frame[10] == 0b0110


class _Chip:
    """Answers every frame with a status byte of the given protocol version."""

    def __init__(self, version):
        self.version, self.frames = version, []

    def __call__(self, frame):
        self.frames.append(bytes(frame))
        return bytes([self.version]) + bytes(len(frame) - 1)


def test_host_refuses_a_paired_image_on_an_older_chip():
    paired = assemble(".pair 1\n" + LOOP.format(k=1))
    with pytest.raises(IOError, match="protocol version 8"):
        Host(_Chip(8)).load(paired, verify=False)
    chip = _Chip(8)                                  # an unpaired image still loads there
    Host(chip).load(assemble(LOOP.format(k=1)), verify=False)
    assert chip.frames[-1][0] == 0x12 and chip.frames[-1][10] == 0
    chip = _Chip(9)
    Host(chip).load(paired, verify=False)
    assert chip.frames[-1][0] == 0x12 and chip.frames[-1][10] == 0b0010_0000


@pytest.mark.parametrize("k", range(4))
def test_pair_doubles_the_instruction_rate(k):
    """100 iterations of a 2-instruction loop: 4 ticks a commit unpaired, 2 paired (the
    early fetch resolves DJNZ exactly), so the run takes half the ticks, on any context."""
    ticks = {}
    for pair in (False, True):
        m = Machine(assemble((f".pair {k}\n" if pair else "") + LOOP.format(k=k)))
        m.run(5000)
        commits = [c for c in m.commits if c["ctx"] == k]
        assert len(commits) == 1 + 200 + 1
        gaps = {b["t"] - a["t"] for a, b in zip(commits, commits[1:])}
        assert gaps == ({2} if pair else {4}), gaps
        ticks[pair] = commits[-1]["t"] - commits[0]["t"]
    assert ticks[False] == 2 * ticks[True]
