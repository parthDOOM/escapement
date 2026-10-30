"""VGA Pong on bank B (firmware/vga_pong.asm): what a monitor would see, the proof that
no edge of the picture is late, and the game against pong_model.py.

The pins are read the way a monitor reads them: HSYNC and VSYNC give the line and frame
timing, and the visible area is the 640 pixels from 144 after each HSYNC fall, on the
480 lines from 35 after each VSYNC fall's line (VESA 640x480: sync 96 + back porch 48;
2 + 33 lines).
"""
from pathlib import Path

import pytest

from escasm import assemble_file
from escert import certify
from esciss import Machine
from pong_model import Pong

FW = Path(__file__).resolve().parents[2] / "firmware" / "vga_pong.asm"
VS, HS, RGB = 1 << 0, 1 << 4, 0b01110
LINE, FRAME, T0 = 800, 525 * 800, 45 * 800


def run(image, ticks, board_b=None):
    """Run, keeping only the bank B output changes (a frame is 420,000 ticks)."""
    m = Machine(image, board_b=board_b)
    edges, prev = [], None
    while m.now < ticks:
        m.run(min(20_000, ticks - m.now), stop_when_idle=False)
        for p in m.pins:
            if p["ob"] != prev:
                edges.append((p["t"], p["ob"]))
                prev = p["ob"]
        m.pins.clear()
        m.commits.clear()
    return m, edges


def levels(edges, end):
    lv = bytearray(end)
    for (t, v), (nt, _) in zip(edges, edges[1:] + [(end, 0)]):
        lv[t:nt] = bytes([v]) * (nt - t)
    return lv


def falls(lv, bit):
    return [t for t in range(1, len(lv)) if lv[t - 1] & bit and not lv[t] & bit]


def rises(lv, bit):
    return [t for t in range(1, len(lv)) if not lv[t - 1] & bit and lv[t] & bit]


@pytest.fixture(scope="module")
def two_frames():
    image = assemble_file(FW)
    m, edges = run(image, T0 + 2 * FRAME + 1000)
    return image, m, levels(edges, m.now)


def test_both_contexts_certified_and_the_bound_is_met_exactly(two_frames):
    image, m, _ = two_frames
    certs = certify(image)
    assert [c.status for c in certs] == ["CHECKED", "CHECKED"]
    assert not m.ff.valid
    for c in certs:
        assert m.ctx[c.ctx].min_lead == c.min_lead      # the certificate is tight
    assert [c.min_lead for c in certs] == [1597, 8]


def test_sync_is_vesa_640x480(two_frames):
    _, _, lv = two_frames
    hf, hr = falls(lv, HS), rises(lv, HS)
    vf, vr = falls(lv, VS), rises(lv, VS)
    assert len(vf) == 2
    assert {b - a for a, b in zip(hf, hf[1:])} == {LINE}
    assert {r - f for f, r in zip(hf, hr)} == {96}
    assert vf[1] - vf[0] == FRAME
    assert {r - f for f, r in zip(vf, vr)} == {2 * LINE}
    assert set(vf) <= set(hf)                            # VSYNC moves with HSYNC
    assert hf[0] == T0 + 656 + 1                         # a DRIVE shows the cycle after its deadline


def test_picture_is_the_framebuffer_and_blanking_is_black(two_frames):
    _, _, lv = two_frames
    md = Pong()
    vf = falls(lv, VS)
    for k in range(2):
        md.step()
        top = T0 + k * FRAME + 1                         # frame k's first pixel
        if k:
            assert top == vf[k - 1] + 144 + 34 * LINE    # where a monitor puts it
        fb = md.framebuffer()
        for y in range(480):
            row = fb[2 * (y // 10)] | fb[2 * (y // 10) + 1] << 32
            want = bytes(RGB if row >> (x // 10) & 1 else 0 for x in range(640))
            line = lv[top + y * LINE: top + y * LINE + 640]
            assert bytes(v & RGB for v in line) == want, (k, y)
            assert not any(v & RGB for v in lv[top + y * LINE + 640: top + (y + 1) * LINE])
        blank = lv[top + 480 * LINE: top + FRAME]
        assert not any(v & RGB for v in blank)


def first_step(md, buttons=0):
    """The firmware's first game step runs before frame 0: compare at its first pixel."""
    image = assemble_file(FW)
    md.load(image.tape)
    m = Machine(image, board_b=lambda t, ob: buttons)
    m.run(T0 + 10, stop_when_idle=False)
    assert not m.ff.valid
    md.step(buttons)
    for addr, v in md.tape().items():
        assert m.tape[addr] == v, addr
    assert m.tape[:96] == md.framebuffer()
    return md


def pong(**kw):
    md = Pong()
    for k, v in kw.items():
        setattr(md, k, v)
    return md


# (start, buttons, what the step must give). frame=1 makes the first step a ball step,
# frame=0 a paddle step; paddle top rows and ball rows are doubled.
CASES = {
    "top rows send it up": (dict(frame=1, bx=4, dx=-1, by=38, dy=2), 0, dict(bx=3, dx=1, dy=-2)),
    "middle keeps the angle": (dict(frame=1, bx=4, dx=-1, by=42, dy=2), 0, dict(dx=1, dy=2)),
    "bottom rows send it down": (dict(frame=1, bx=4, dx=-1, by=50, dy=-2), 0, dict(dx=1, dy=2)),
    "right paddle hit": (dict(frame=1, bx=59, dx=1, by=38, dy=2), 0, dict(bx=60, dx=-1, dy=-2)),
    "missed at the paddle": (dict(frame=1, bx=4, dx=-1, by=70, dy=2), 0, dict(bx=3, dx=-1)),
    "right scores": (dict(frame=1, bx=1, dx=-1, s=[3, 4]), 0, dict(s=[3, 5], bx=32, dx=-1)),
    "tenth point: new game": (dict(frame=1, bx=62, dx=1, s=[9, 2]), 0, dict(s=[0, 0], bx=32, dx=1)),
    "top border": (dict(frame=1, by=2, dy=-2), 0, dict(by=4, dy=2)),
    "bottom border": (dict(frame=1, by=92, dy=2), 0, dict(by=90, dy=-2)),
    "chip watches its half": (dict(frame=0, bx=10, by=70, p=[40, 10]), 0, dict(p=[42, 10])),
    "chip steers its row": (dict(frame=0, bx=50, by=40, p=[40, 36], aim=[104, 100]), 0,
                            dict(p=[40, 38])),
    "left up, right down": (dict(frame=0), 0b10010, dict(p=[38, 42])),
    "both buttons: up": (dict(frame=0), 0b11110, dict(p=[38, 38])),
    "buttons stop at borders": (dict(frame=0, p=[2, 82]), 0b10010, dict(p=[2, 82])),
}


@pytest.mark.parametrize("name", CASES)
def test_game_step(name):
    start, buttons, want = CASES[name]
    md = first_step(pong(**{k: (list(v) if isinstance(v, list) else v)
                            for k, v in start.items()}), buttons)
    for k, v in want.items():
        assert getattr(md, k) == v, k
