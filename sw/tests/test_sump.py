"""The SUMP bridge (sw/rp2350/sump.py) on the simulated demo board, driven the way
libsigrok's openbench-logic-sniffer driver drives a device: resets, ID, metadata, trigger
stage 0, divider, read and delay counts, flags, arm; then the samples, newest first, one
byte per enabled channel group. The waveform PulseView would show must be the signal on
the pin, to the sample.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sw" / "rp2350"))

import bringup  # noqa: E402
import make_bundle  # noqa: E402
import sump  # noqa: E402
from demoboard_mock import MockBoard, SimChip  # noqa: E402

HALF = 6000                     # the test signal on uio[1]: 6000 ticks low, 6000 high


def le32(v):
    return bytes((v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF))


def parse_metadata(b):
    """As libsigrok's ols_get_metadata: key >> 5 selects string, 32-bit BE, or byte."""
    out, i = {}, 0
    while b[i]:
        key, i = b[i], i + 1
        if key >> 5 == 0:
            j = b.index(0, i)
            out[key], i = b[i:j].decode(), j + 1
        elif key >> 5 == 1:
            out[key], i = int.from_bytes(b[i:i + 4], "big"), i + 4
        else:
            out[key], i = b[i], i + 1
    return out, i + 1


def acquisition(rate, n, ratio, mask, value, groups=1):
    """The command bytes of ols_prepare_acquisition + arm, for a device with more than
    256 Ki samples (read and delay counts as 0x84 / 0x83)."""
    read = (n + 3) // 4
    delay = int(read * (1 - ratio / 100.0))
    flags = 0
    for g in range(4):
        if g >= groups:
            flags |= 1 << (2 + g)             # 1 disables a channel group
    return (b"\xC0" + le32(mask) + b"\xC1" + le32(value) + b"\xC2" + le32(1 << 3)
            + b"\x80" + le32(sump.CLOCK_RATE // rate - 1)
            + b"\x84" + le32(read - 1) + b"\x83" + le32(delay - 1)
            + b"\x82" + le32(flags) + b"\x01")


def run_bridge(commands):
    fw = ROOT / "firmware" / "logic_analyzer.asm"
    name, (_, overrides) = next(iter(make_bundle.VARIANTS.items()))
    bundle = json.loads(json.dumps(make_bundle.bundle(fw, name, overrides)))
    chip = SimChip()
    chip.sources.append(lambda t: (0x02, 0x02 * ((t // HALF) & 1)))
    board = MockBoard(chip)
    b = bringup.Bringup(board.io(bringup))
    b.stage0()
    b.connect()
    inp, out = bytearray(commands), bytearray()

    def rd(n):
        x = bytes(inp[:n])
        del inp[:n]
        return x

    br = sump.Bridge(b, bundle, rd, out.extend)
    while br.step():
        pass
    return bytes(out), br


def test_identify_and_metadata():
    out, _ = run_bridge(b"\x00" * 5 + b"\x02" + b"\x04")
    assert out[:4] == b"1ALS"
    meta, used = parse_metadata(out[4:])
    assert used == len(out) - 4
    assert meta[0x01] == "Escapement" and meta[0x20] == 8
    assert meta[0x21] == sump.MEMORY and meta[0x23] == 25_000_000


@pytest.mark.parametrize("groups", [1, 4])
def test_capture_matches_the_pin(groups):
    rate, n = 1_000_000, 2048                         # 25 ticks per sample, 2048 samples
    out, br = run_bridge(b"\x00" * 5 + acquisition(rate, n, 50, mask=0x02, value=0x02,
                                                     groups=groups))
    assert len(out) == n * groups, br.log
    smp = [out[k] for k in range(0, len(out), groups)][::-1]    # oldest first
    assert all(out[k + j] == 0 for k in range(0, len(out), groups) for j in range(1, groups))
    pin = [(s >> 1) & 1 for s in smp]
    trig = n // 2                                     # 50% ratio: the trigger is centred
    assert pin[trig] == 1 and pin[trig - 1] == 0, br.log
    # after the trigger the pin toggles every HALF ticks = 240 samples, exactly
    edges = [k for k in range(trig, n) if pin[k] != pin[k - 1]]
    assert edges[0] == trig and all(b - a == HALF // 25 for a, b in zip(edges, edges[1:]))
    assert "OVERFLOW" not in br.log[0]
