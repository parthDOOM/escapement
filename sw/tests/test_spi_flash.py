"""SPI NOR flash programmer (firmware/spi_flash.asm) against a W25Q80DV model written from
its datasheet (escmodels/spiflash.py), at 25 MHz with SCK at 500 kHz. The model's tPP and
tSE are shortened (0.3 ms, 3 ms) to keep the runs short; the firmware polls BUSY either way."""

from __future__ import annotations

from pathlib import Path

from escasm import assemble
from escmodels import Board, HostScript
from escmodels.spiflash import SpiFlashW25
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
KHZ = 25000
MS = KHZ


def run(pushes, ms, equ="", flash_cls=SpiFlashW25, mem=None, **flash):
    im = assemble(f'.equ spi_flash.CLK_KHZ, {KHZ}\n{equ}.include "spi_flash.asm"\n', base_dir=FW)
    fl = flash_cls(4, 5, 6, 7, 1e6 / KHZ, **{"t_pp_ms": 0.3, "t_se_ms": 3.0, **flash})
    fl.mem.update(mem or {})
    host = HostScript(pushes, drain=[0], poll=2500)
    board = Board(fl, host)
    m = Machine(im, board=board)
    host.machine = m
    m.run(int(ms * MS), stop_when_idle=False)
    return m, fl, host, board


def test_jedec_id_and_status():
    m, fl, host, board = run([(100, 0, 1 << 28), (2000, 0, 5 << 28)], 1)
    assert host.received[0] == [0xEF4014, 0]
    assert not fl.violations and not board.errors() and not m.ff.valid


DATA = [(0x01020304 * (k + 1)) & 0xFFFFFFFF for k in range(16)]


def test_erase_program_and_read_back():
    """Sector Erase over old data (0x5A from 0x1000 to 0x1FFF, and 0x2000 beyond the
    sector), Page Program of 64 bytes, Read Data of them: the flash holds them in address
    order, they come back, and only the sector was erased."""
    pushes = [(100, 0, 4 << 28 | 0x001000)]
    t = 5 * MS
    pushes += [(t, 0, 3 << 28 | 0x001010), (t + 10, 0, 16)]
    pushes += [(t + 1000 + 5000 * k, 0, w) for k, w in enumerate(DATA)]      # 200 us apart
    t = 10 * MS
    pushes += [(t, 0, 2 << 28 | 0x001010), (t + 10, 0, 16)]
    old = {a: 0x5A for a in range(0x1000, 0x2001)}
    m, fl, host, board = run(pushes, 13, mem=old)
    assert host.received[0][:2] == [0, 0], host.received[0][:2]
    got = bytes(fl.read(0x1010 + k) for k in range(64))
    assert got == b"".join(w.to_bytes(4, "big") for w in DATA)
    assert all(fl.read(a) == 0xFF for a in range(0x1000, 0x1010))          # erased, not programmed
    assert all(fl.read(a) == 0xFF for a in range(0x1050, 0x2000)) and fl.read(0x2000) == 0x5A
    assert host.received[0][2:] == DATA
    assert [c for c, _ in fl.instructions if c != 0x05] == [0x06, 0x20, 0x06, 0x02, 0x03]
    assert (0x20, 32) in fl.instructions and (0x02, 32 + 64 * 8) in fl.instructions
    assert not fl.violations, fl.violations
    assert not board.errors() and not m.ff.valid


class _Protected(SpiFlashW25):
    """A flash whose Write Enable does nothing (as with /WP and SRP set, say)."""

    def _end(self, t):
        if self._code == 0x06:
            return
        super()._end(t)


def test_write_enable_refused():
    """WEL stays 0: status 1, no Page Program sent, the data words taken; the next command
    runs."""
    pushes = [(100, 0, 3 << 28 | 0x000100), (110, 0, 2)]
    pushes += [(3000, 0, 0x11111111), (8000, 0, 0x22222222), (13000, 0, 1 << 28)]
    m, fl, host, board = run(pushes, 1, flash_cls=_Protected)
    assert host.received[0] == [1, 0xEF4014]
    assert 0x02 not in [c for c, _ in fl.instructions]


def test_busy_too_long_is_reported():
    pushes = [(100, 0, 3 << 28 | 0x000200), (110, 0, 1), (3000, 0, 0xCAFEF00D)]
    m, fl, host, board = run(pushes, 3, equ=".equ spi_flash.POLLS, 20\n", t_pp_ms=50)
    assert host.received[0] == [2]


def test_certificate_matches_telemetry():
    from escert import certify
    im = assemble(f'.equ spi_flash.CLK_KHZ, {KHZ}\n.include "spi_flash.asm"\n', base_dir=FW)
    assert [(c.status, c.min_lead) for c in certify(im)] == [("CHECKED", 28)]
