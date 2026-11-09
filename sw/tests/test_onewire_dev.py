"""1-Wire device (firmware/onewire_dev.asm) against a bus master model at AN126 standard
timing (escmodels.onewire.OneWireMaster): reset and presence, Read ROM, Match ROM, Search
ROM (Maxim AN187), the scratchpad, and the CRC-8s the chip computes, checked with
crc8_maxim (escmodels.onewire), written apart from the firmware."""

from __future__ import annotations

from pathlib import Path

from escasm import assemble
from escmodels import Board, HostScript, OneWireMaster, crc8_maxim
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
KHZ = 25000


def rom_bytes(serial=(1, 2, 3, 4, 5, 6)):
    b = bytes([0x28, *serial])
    return b + bytes([crc8_maxim(b)])


def run(program, pushes=(), ms=40, **timing):
    im = assemble(f'.equ onewire_dev.CLK_KHZ, {KHZ}\n.include "onewire_dev.asm"\n', base_dir=FW)
    # the chip takes host words between transactions, once a millisecond: start after them
    master = OneWireMaster(0, 1e6 / KHZ, program, start_us=5000 if pushes else 100, **timing)
    host = HostScript(list(pushes), drain=[0], poll=5000)
    board = Board(master, host)
    m = Machine(im, board=board)
    host.machine = m
    m.run(ms * KHZ, stop_when_idle=False)
    assert master.done, "the master did not finish"
    return m, master, board


def test_read_rom():
    def prog(mm):
        mm.results.append((yield from mm.reset()))
        yield from mm.write(0x33)
        mm.results.append((yield from mm.read_n(8)))
    m, master, board = run(prog)
    present, rom = master.results
    assert present and rom == rom_bytes() and crc8_maxim(rom[:7]) == rom[7]
    assert not board.errors() and not m.ff.valid


def test_search_rom_finds_the_device():
    def prog(mm):
        mm.results.append((yield from mm.search()))
    m, master, board = run(prog, ms=60)
    want = int.from_bytes(rom_bytes(), "little")
    assert master.results == [[want]], [hex(x) for x in master.results[0]]


def test_match_rom_selects_only_this_device():
    """Match ROM with this ROM: the scratchpad comes back; with another: the bus stays 0xFF."""
    other = bytes([0x28, 9, 9, 9, 9, 9, 9]) + bytes([crc8_maxim(bytes([0x28, 9, 9, 9, 9, 9, 9]))])

    def prog(mm):
        for rom in (rom_bytes(), other):
            yield from mm.reset()
            yield from mm.write(0x55)
            for b in rom:
                yield from mm.write(b)
            yield from mm.write(0xBE)
            mm.results.append((yield from mm.read_n(9)))
    m, master, board = run(prog, ms=60)
    mine, theirs = master.results
    assert crc8_maxim(mine[:8]) == mine[8] and mine[:2] == bytes([0x50, 0x05])   # +85.0 C at power-up
    assert theirs == b"\xff" * 9


def test_scratchpad_write_and_temperature_from_the_host():
    """The host sets the temperature (+25.0625 C = 0x0191) and a new serial number; Write
    Scratchpad sets TH, TL and the configuration; Read ROM and Read Scratchpad return them,
    both CRC-8s recomputed by the chip."""
    serial = (0xA1, 0xB2, 0xC3, 0xD4, 0xE5, 0xF6)
    pushes = [(200, 0, 1 << 30 | 0xC3B2A1), (30_000, 0, 2 << 30 | 0xF6E5D4), (60_000, 0, 3 << 30 | 0x0191)]

    def prog(mm):
        yield from mm.reset()
        yield from mm.write(0xCC)
        yield from mm.write(0x4E)
        for b in (0x55, 0xAA, 0x3F):
            yield from mm.write(b)
        yield from mm.reset()
        yield from mm.write(0x33)
        mm.results.append((yield from mm.read_n(8)))
        yield from mm.write(0xBE)
        mm.results.append((yield from mm.read_n(9)))
    m, master, board = run(prog, pushes=pushes, ms=60)
    rom, sp = master.results
    assert rom == rom_bytes(serial)
    assert sp[:8] == bytes([0x91, 0x01, 0x55, 0xAA, 0x3F, 0xFF, 0x0C, 0x10]) and sp[8] == crc8_maxim(sp[:8])


def test_slots_at_the_edges_of_the_timing():
    """A master at the limits a device must meet (DS18B20 datasheet): a write-1 low for
    15 us (the device samples 15-60 us after the edge), a read sampled 14 us after the edge
    (the device's 0 is valid for 15 us), a write-0 low for 60 us: Read ROM still exact."""
    def prog(mm):
        yield from mm.reset()
        yield from mm.write(0x33)
        mm.results.append((yield from mm.read_n(8)))
    m, master, board = run(prog, A=15, B=55, RL=1, RS=14)
    assert master.results == [rom_bytes()], master.results


def test_certificate_matches_telemetry():
    from escert import certify
    im = assemble(f'.equ onewire_dev.CLK_KHZ, {KHZ}\n.include "onewire_dev.asm"\n', base_dir=FW)
    certs = certify(im)
    assert [(c.status, c.min_lead) for c in certs] == [("CHECKED", 4)]
