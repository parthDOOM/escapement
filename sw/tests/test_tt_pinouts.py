"""Tiny Tapeout's recommended pinouts (tinytapeout.com/specs/pinouts), reached by
assembler parameters alone: each program here runs on the pins TT suggests for its
protocol, unchanged but for its .equ and .bank lines, against the same models as its own
tests. docs/info.md lists the settings.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "test" / "cosim"))

from escasm import assemble  # noqa: E402
from escert import certify  # noqa: E402
from escmodels import Board, HostScript  # noqa: E402
from escmodels.spiflash import SpiFlashW25  # noqa: E402
from esciss import Machine  # noqa: E402

FW = REPO / "firmware"
TPB = 217                                     # 115,200 baud at 25 MHz


def build(src: str):
    image = assemble(src, base_dir=FW, name="pinout.asm")
    assert all(c.status == "CHECKED" for c in certify(image)), [str(c) for c in certify(image)]
    return image


def test_uart_on_the_demo_boards_usb_serial_pins():
    """The demo board's RP2040 bridges USB serial to ui_in[3] (the chip's RX) and
    uo_out[4] (its TX): bank B pins 0 and 1. uart_tx sends its four bytes on uo_out[4];
    uart_rx receives three bytes from ui_in[3] and pushes them to the host."""
    tx = build('.bank 0\n.equ uart_tx.PIN, 1\n.include "uart_tx.asm"\n')
    levels = []

    def watch(t, ob):
        if t >= 0:
            levels.append((ob >> 1) & 1)
        return 0x1F
    m = Machine(tx, board_b=watch, record=False)
    m.run(4 * 10 * 218 + 2000, stop_when_idle=False)
    got, i = [], 1
    while i < len(levels):
        if levels[i] == 0 and levels[i - 1] == 1:            # a start bit
            got.append(sum(levels[int(i + (k + 1.5) * TPB)] << k for k in range(8)))
            i += int(9.5 * TPB)
        else:
            i += 1
    assert got == [0x55, 0x4B, 0x21, 0x0A] and not m.ff.valid

    rx = build('.bank 1\n.equ uart_rx.RX_PIN, 0\n.include "uart_rx.asm"\n')
    wave = [1] * 500
    for b in b"Hi!":
        wave += [0] * TPB + sum(([(b >> k) & 1] * TPB for k in range(8)), []) + [1] * 2 * TPB
    m = Machine(rx, board_b=lambda t, ob: 0x1E | (wave[t] if 0 <= t < len(wave) else 1),
                record=False)
    pushed = []
    while m.now < len(wave) + 2000:
        m.run(100, stop_when_idle=False)
        v = m.host_tx_pop(1)
        m.run(1, stop_when_idle=False)
        if v is not None:
            pushed.append(v & 0xFF)
    assert bytes(pushed) == b"Hi!" and not m.ff.valid


def test_spi_flash_on_the_top_spi_row():
    """TT's SPI pinout, top row (uio[0] CS, uio[1] MOSI, uio[2] MISO, uio[3] SCK), which
    is also the SPI RAM and QSPI Pmods' pinout: the co-simulation bench's session (JEDEC ID,
    Sector Erase, Page Program, Read Data) against the W25Q80DV model on those pins."""
    image = build(".equ spi_flash.CS_PIN, 0\n.equ spi_flash.MOSI_PIN, 1\n"
                  '.equ spi_flash.MISO_PIN, 2\n.equ spi_flash.SCK_PIN, 3\n.include "spi_flash.asm"\n')
    fl = SpiFlashW25(3, 1, 2, 0, 40.0, t_pp_ms=0.3, t_se_ms=1.0)
    fl.mem.update({a: 0x5A for a in range(0x1000, 0x1010)})
    host = HostScript([(100, 0, 1 << 28), (2000, 0, 4 << 28 | 0x001000),
                       (40_000, 0, 3 << 28 | 0x001000), (40_010, 0, 1), (42_000, 0, 0x01020304),
                       (70_000, 0, 2 << 28 | 0x001000), (70_010, 0, 2)], drain=[0], poll=2500)
    board = Board(fl, host)
    m = Machine(image, board=board, record=False)
    host.machine = m
    m.run(85_000, stop_when_idle=False)
    assert host.received[0] == [0xEF4014, 0, 0, 0x01020304, 0xFFFFFFFF], host.received
    assert not fl.violations and not board.errors() and not m.ff.valid


def test_audio_on_the_tt_audio_pmod_pin():
    """The TT Audio Pmod takes uo_out[7], bank B pin 4: the Westminster tune (here at
    250 kHz, a hundredth of its time) plays its first note there and nowhere else."""
    image = build('.equ westminster.CLK_KHZ, 250\n.equ westminster.PIN, 4\n.include "westminster.asm"\n')
    seen = set()
    m = Machine(image, board_b=lambda t, ob: seen.add(ob & 0x1F) or 0, record=False)
    m.run(60_000, stop_when_idle=False)
    assert {v & 0x10 for v in seen} == {0, 0x10}        # uo_out[7] toggles
    assert len({v & 0x0F for v in seen}) == 1             # the other four never move
    assert not m.ff.valid
