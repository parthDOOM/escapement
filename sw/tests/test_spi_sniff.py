"""firmware/spi_sniff.asm, the SPI sniffer (v3: the line unit's external-clock receiver), on
the ISS: a controller (escmodels.SpiController) writes frames to a target the chip only
listens to, in every SPI mode; each frame is logged bit for bit from MOSI. Frames that open
with a 1 (an external-clock receive starts at once, without waiting for a start bit), a
fast clock (2 ticks a bit), pauses inside a frame, and a long QUIET whose 1.5 x QUIET
passes the bit timer's saturation (the packet still ends)."""

from __future__ import annotations

from pathlib import Path

import pytest
from escasm import assemble
from escmodels import Board, HostScript
from escmodels.spi import SpiController
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
SCK, MOSI, MISO, CS = 0, 1, 2, 3
FRAMES = [bytes([0xC5, 0x01, 0xFF, 0x80]), bytes([0x3A]), bytes(range(0x10, 0x30))]


def sniff(mode, quiet=2000, t_high=8, t_low=8, byte_gap=0, frames=FRAMES):
    img = assemble(f".equ spi_sniff.MODE, {mode}\n.equ spi_sniff.QUIET, {quiet}\n"
                   '.include "spi_sniff.asm"\n', base_dir=FW, name="spi_sniff.asm")
    ctl = SpiController(SCK, MOSI, MISO, CS, [(f, None) for f in frames], mode=mode,
                        t_high=t_high, t_low=t_low, t_css=24, t_idle=3 * quiet + 400,
                        byte_gap=byte_gap, start_at=400)
    host = HostScript([], drain=[0], poll=4)
    m = Machine(img, board=Board(ctl, host))
    host.machine = m
    while not ctl.done and m.now < 400_000:
        m.run(5000, stop_when_idle=False)
    m.run(3 * quiet + 4000, stop_when_idle=False)
    w, out = list(host.received[0]), []
    while w:
        n = w.pop(0)
        k = (n + 31) // 32
        data = b"".join(x.to_bytes(4, "big") for x in w[:k])
        del w[:k]
        out.append((n, data[:n // 8]))
    return ctl, out


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_every_frame_is_logged_in_every_mode(mode):
    ctl, got = sniff(mode)
    assert ctl.errors() == []
    assert got == [(8 * len(f), f) for f in FRAMES]


def test_two_ticks_a_bit_and_pauses_inside_a_frame():
    ctl, got = sniff(0, t_high=1, t_low=1, byte_gap=900)        # 25 MHz SCK at 50 MHz
    assert got == [(8 * len(f), f) for f in FRAMES]


def test_a_quiet_past_the_bit_timer_still_ends_the_frame():
    ctl, got = sniff(3, quiet=4000, frames=FRAMES[:2])          # 1.5 x 4000 > 4093 ticks
    assert got == [(8 * len(f), f) for f in FRAMES[:2]]
