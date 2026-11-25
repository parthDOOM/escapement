"""S/PDIF / AES3 transmitter (firmware/spdif_tx.asm, held-out round) against the receiver
model escmodels.spdif.SpdifRx, written apart from the firmware from EBU Tech 3250-E (2004)
alone. The model decodes the line and checks biphase mark (2.3), the preambles and their
order with Z every 192 frames (2.2.2, 2.4), even parity (2.2.1), every transition on an
ideal clock within 0.025 UI (6.2.5.1, without its filter, so stricter), the rate, and that
the stream never stops once it has started. What the frames should carry is decided here.

The chip clock is 128 x FS x CELL: 49.152 MHz for 48 kHz with CELL 8 (most runs)."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import Board, HostFeed, SpdifRx
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"


def source(cell=8, fs=48000, patch=()):
    src = (FW / "spdif_tx.asm").read_text()
    for a, b in patch:
        assert src.count(a) == 1, a
        src = src.replace(a, b)
    return f".equ CELL, {cell}\n.equ FS, {fs}\n" + src


def image(cell=8, fs=48000, patch=()):
    return assemble(source(cell, fs, patch), base_dir=FW, name="spdif_tx.asm")


def run(frames, samples=(), cell=8, fs=48000, patch=(), tape=None):
    """Run for `frames` frame times with the host feeding `samples` (host words) as fast
    as the mailbox takes them. Returns the model, the machine and the samples sent."""
    tick = 1e9 / (128 * fs * cell)
    im = image(cell, fs, patch)
    if tape:
        for a, w in tape.items():
            im.tape[a] = w
    host = HostFeed({0: list(samples)}, gap=64)
    rx = SpdifRx(0, tick_ns=tick, fs_hz=fs)
    m = Machine(im, board=Board(rx, host))
    host.machine = m
    m.run(int((frames + 3) * 128 * cell), stop_when_idle=False)
    rx.finish()
    return rx, m


def word(left, right):
    return (left & 0xFFFF) << 16 | (right & 0xFFFF)


def sample_words(n):
    return [word((i * 977) - 30000, 0x8000 ^ (i * 4099)) for i in range(n)]


def clean(rx, m):
    assert not m.ff.valid, m.ff
    assert not rx.errors(), rx.errors()[:5]
    assert rx.locks == 1


def valid(rx):
    return [f for f in rx.frames if f["v"] == (0, 0)]


def in_order(got, words):
    """The decoded valid frames are the host's words in order, from wherever the receiver
    locked (it cannot lock on the first preamble: the line idles high before it, so the
    first sub-frame shows no edge at its start)."""
    want = [((w >> 16) << 8, (w & 0xFFFF) << 8) for w in words]
    pairs = [(f["a"], f["b"]) for f in got]
    k = want.index(pairs[0])
    return k <= 2 and pairs == want[k:k + len(pairs)]


def test_audio_and_channel_status_at_48_khz():
    """Host samples in order, MSB-justified in slots 12 to 27 with slots 4 to 11 zero;
    validity 0, user 0; frames before the host's first word carry validity 1 and zero; one
    whole block with Tech 3250's professional channel status: byte 0 = professional, 48 kHz
    (bits 6-7 = 01), byte 23 = CRCC; UI = 162.76 ns."""
    words = sample_words(420)
    rx, m = run(400, words)
    clean(rx, m)
    got = valid(rx)
    assert len(got) >= 390 and in_order(got, words)
    first = rx.frames.index(got[0])
    assert all(f["v"] == (1, 1) and f["a"] == f["b"] == 0 for f in rx.frames[:first])
    assert all(f["v"] == (0, 0) for f in rx.frames[first:])      # no underrun while fed
    assert all(f["u"] == (0, 0) for f in rx.frames)
    assert rx.blocks, "no whole block"
    want = bytes([0x81] + [0] * 22 + [0x9B])
    for blk in rx.blocks:
        assert blk["a"] == want and blk["b"] == want and blk["crc_ok_a"] and blk["crc_ok_b"]
    assert abs(rx.ui_ns - 1e9 / (128 * 48000)) < 0.01


@pytest.mark.parametrize("cell,fs,b0,crc", [(10, 48000, 0x81, 0x9B), (8, 44100, 0x41, 0xDE),
                                            (8, 32000, 0xC1, 0x77), (8, 24414, 0x01, 0x32)])
def test_other_clocks_and_rates(cell, fs, b0, crc):
    """CELL 8 and 10, 48, 44.1 and 32 kHz, and 24.4 kHz (the chip's 25 MHz with CELL 8,
    a rate channel status cannot name: byte 0 bits 6-7 = 00): the block's byte 0 and
    CRCC follow, the stream is clean."""
    words = sample_words(420)
    rx, m = run(400, words, cell=cell, fs=fs)
    clean(rx, m)
    assert rx.blocks
    blk = rx.blocks[0]
    assert blk["a"][0] == b0 and blk["a"][23] == crc and blk["crc_ok_a"] and blk["crc_ok_b"]
    assert in_order(valid(rx), words)


def test_two_blocks_z_every_192_frames():
    rx, m = run(600, sample_words(620))
    clean(rx, m)
    starts = [i for i, f in enumerate(rx.frames) if f["block_start"]]
    assert len(starts) >= 2 and starts[1] - starts[0] == 192
    assert len(rx.blocks) >= 1


def test_no_host_sends_invalid_silence():
    """With no samples the stream still runs: zero samples, validity 1 (2.5)."""
    rx, m = run(220)
    clean(rx, m)
    assert len(rx.frames) >= 200
    assert all(f["v"] == (1, 1) and f["a"] == 0 and f["b"] == 0 for f in rx.frames)


def test_host_underrun_mid_stream():
    """The host stops after 60 samples: the stream goes on, the next frames invalid."""
    words = sample_words(60)
    rx, m = run(150, words)
    clean(rx, m)
    v = [f["v"] for f in rx.frames]
    n = v.count((0, 0))
    assert 58 <= n <= 60 and v[:n] == [(0, 0)] * n and set(v[n:]) == {(1, 1)}
    assert in_order(valid(rx), words)


def test_host_written_channel_status():
    """Any 192-bit block per channel from the tape (here a consumer-style block on A and a
    different one on B), sent as written."""
    a = bytes([0x04, 0x01, 0x00, 0x02] + [0] * 20)
    b = bytes([0x04, 0x01, 0x20, 0x02] + [0] * 19 + [0x5A])
    tape = {0x40 + k: int.from_bytes(a[4 * k:4 * k + 4], "little") for k in range(6)}
    tape.update({0x46 + k: int.from_bytes(b[4 * k:4 * k + 4], "little") for k in range(6)})
    rx, m = run(400, sample_words(420), tape=tape)
    clean(rx, m)
    assert rx.blocks and rx.blocks[0]["a"] == a and rx.blocks[0]["b"] == b


# ------------------------------------------------------------ negative controls
# One bit or one timing wrong: the model must catch each.

def test_invalid_cell_is_refused():
    from escasm import AsmError
    with pytest.raises(AsmError):
        image(cell=7)


def test_negative_control_wrong_preamble():
    """Sub-frame 2 sent with preamble X instead of Y."""
    rx, m = run(30, sample_words(40), patch=[("    MOVI  r2, PY\n", "    MOVI  r2, PX\n")])
    assert rx.errors()


def test_negative_control_odd_parity():
    """The parity bit inverted in every sub-frame (odd parity)."""
    patch = [("    SHL   r1, 31\n    SHR   r1, 4\n    OR    r0, r1               ; slot 31\n",
              "    SHL   r1, 31\n    SHR   r1, 4\n    OR    r0, r1               ; slot 31\n"
              "    MOVI  r1, 1\n    SHL   r1, 27\n    XOR   r0, r1\n")]
    patch = [(a, b) for a, b in patch]
    src = (FW / "spdif_tx.asm").read_text()
    assert src.count(patch[0][0]) == 2              # both sub-frames
    rx, m = run(30, sample_words(40), patch=[(src, src.replace(patch[0][0], patch[0][1]))])
    assert rx.errors()


def test_negative_control_junction_one_tick_late():
    """Each packet one tick late: the cell at the junction is one tick (0.125 UI) long."""
    rx, m = run(30, sample_words(40), patch=[("    ADDT  3 * CELL ", "    ADDT  3 * CELL + 1 ")])
    assert rx.errors()


# ------------------------------------------------------------ certificate

def test_certificate():
    """escert proves every event admitted at CELL 8 and 10; it cannot bound the chained
    SENDs (it bounds a packet's end only from below), so it reports them MAY DROP: the
    runs above show none dropped (one lock, no gap)."""
    from escert import certify
    for cell in (8, 10):
        certs = certify(image(cell))
        assert [c.status for c in certs] == ["CHECKED"]
        assert not certs[0].replies_on_time
