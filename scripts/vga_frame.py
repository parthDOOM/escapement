#!/usr/bin/env python3
"""A frame of VGA Pong as a monitor sees it (docs/21-VGA-PONG.md), from the simulator:

    python scripts/vga_frame.py OUT.png [bx by dx dy left right score_l score_r]

firmware/vga_pong.asm runs on the instruction-set simulator from the given game state
(by default the docs' picture: the ball at column 18 heading left, 3 to 7), and the
frame after its first VSYNC pulse is cut out of the bank B pins by their HSYNC and VSYNC
edges: 640 pixels from 144 after each HSYNC fall, 480 lines from 35 lines after VSYNC.
Rows are doubled, as in the firmware (by 34 is row 17).
"""
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sw"))
from escasm import assemble_file  # noqa: E402
from esciss import Machine  # noqa: E402

LINE, FRAME, T0 = 800, 525 * 800, 45 * 800


def frame(state):
    image = assemble_file(ROOT / "firmware" / "vga_pong.asm")
    for i, v in enumerate(state):
        image.tape[140 + i] = v & 0xFFFFFFFF
    m = Machine(image)
    lv, prev, t = bytearray(), 0, 0
    end = T0 + 2 * FRAME
    while m.now < end:
        m.run(min(20_000, end - m.now), stop_when_idle=False)
        for p in m.pins:
            lv.append(p["ob"])
        m.pins.clear()
        m.commits.clear()
    assert not m.ff.valid, m.ff.__dict__
    vs = next(t for t in range(1, len(lv)) if lv[t - 1] & 1 and not lv[t] & 1)
    top = vs + 144 + 34 * LINE          # the VSYNC fall is an HSYNC fall: 144 to line 491
    return [lv[top + y * LINE: top + y * LINE + 640] for y in range(480)]


def png(path, rows):
    raw = b"".join(b"\0" + bytes(c for v in r for c in (255 * (v >> 1 & 1), 255 * (v >> 2 & 1),
                                                      255 * (v >> 3 & 1))) for r in rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n"
                           + chunk(b"IHDR", struct.pack(">IIBBBBB", 640, 480, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    args = [int(a, 0) for a in sys.argv[2:]] or [18, 34, -1, 2, 30, 56, 3, 7]
    png(sys.argv[1], frame(args + [1]))         # frame counter 1: the first step moves the ball
