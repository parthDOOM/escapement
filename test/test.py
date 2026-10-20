# SPDX-FileCopyrightText: © 2026 Escapement contributors
# SPDX-License-Identifier: Apache-2.0
"""Chip-level tests through the real pins, on RTL and on the gate-level netlist.

The host side talks the docs/12-HOST-PROTOCOL.md SPI protocol on ui_in[2:0]/uo_out[0];
protocol pins are connected to bus models (sw/escmodels), exactly as on the ISS.
"""

from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge
from cocotb.utils import get_sim_time

from escasm import assemble, assemble_file
from eschost import AsyncHost
from escmodels import Board, UartRx, UartTx
from esciss import Machine
from esctape import decode_capture, decode_records, edges
from spi_master import SpiMaster

FW = Path(__file__).resolve().parents[1] / "firmware"
CLK_NS = 40  # 25 MHz design target


class UiBit:
    """A single bit of dut.ui_in, usable where SpiMaster expects a signal handle.

    Keeps a shadow of the whole bus in Python: reading dut.ui_in back would return the
    old value when two bits are written in the same simulation timestep."""

    shadow = {}

    def __init__(self, dut, bit):
        self.dut, self.bit = dut, bit
        UiBit.shadow.setdefault(id(dut), 0b001)

    @property
    def value(self):
        return (UiBit.shadow[id(self.dut)] >> self.bit) & 1

    @value.setter
    def value(self, v):
        cur = UiBit.shadow[id(self.dut)]
        cur = (cur & ~(1 << self.bit)) | ((int(v) & 1) << self.bit)
        UiBit.shadow[id(self.dut)] = cur
        self.dut.ui_in.value = cur


class UoBit:
    def __init__(self, dut, bit):
        self.dut, self.bit = dut, bit

    @property
    def value(self):
        return (int(self.dut.uo_out.value) >> self.bit) & 1


async def run_board(dut, board):
    """Drive uio_in from the board model once per cycle (mid-cycle)."""
    t = 0
    while True:
        await FallingEdge(dut.clk)
        dut.uio_in.value = board(t, int(dut.uio_out.value), int(dut.uio_oe.value))
        t += 1


async def run_board_b(dut, board_b):
    """Drive ui_in[7:3], the pin bank B inputs (v1.2), from a bank B model once per cycle,
    through the same shadow as the SPI host's bits."""
    t = 0
    while True:
        await FallingEdge(dut.clk)
        ib = board_b(t, (int(dut.uo_out.value) >> 3) & 0x1F) & 0x1F
        cur = (UiBit.shadow[id(dut)] & 0x07) | (ib << 3)
        UiBit.shadow[id(dut)] = cur
        dut.ui_in.value = cur
        t += 1


async def bring_up(dut, board, board_b=None):
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    dut.ena.value = 1
    dut.ui_in.value = 0b001          # CS_n high
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    cocotb.start_soon(run_board(dut, board))
    spi = SpiMaster(dut, dut.clk, UiBit(dut, 0), UiBit(dut, 1), UiBit(dut, 2), UoBit(dut, 0),
                    half=6)
    if board_b is not None:
        cocotb.start_soon(run_board_b(dut, board_b))
    return AsyncHost(spi.xfer)


@cocotb.test()
async def test_identity_over_spi(dut):
    host = await bring_up(dut, Board())
    s = await host.status()
    assert s["version"] == 8 and not s["running"] and not s["host_err"], s


@cocotb.test()
async def test_attention_pin(dut):
    """uo_out[1] is IRQ-or-fault (docs/info.md): high for an IRQ alone, low again after
    IRQ_CLEAR, and high for a fault alone."""
    host = await bring_up(dut, Board())
    attn = lambda: (int(dut.uo_out.value) >> 1) & 1   # noqa: E731
    assert attn() == 0
    await host.load(assemble(".entry 0, s\ns: IRQ 0x5A\nHALT"))
    await host.start()
    await ClockCycles(dut.clk, 40)
    assert attn() == 1 and (await host.read_dbg(0, 14)) >> 31 == 0     # IRQ, no fault
    await host.irq_clear(0x1)
    await ClockCycles(dut.clk, 8)
    assert attn() == 0
    await host.stop()
    await host.write_imem(0, [0])            # ILLEGAL at the entry (load() skips zero words)
    await host.start()
    await ClockCycles(dut.clk, 40)
    assert attn() == 1 and (await host.read_dbg(0, 13)) & 0x100 == 0      # fault, no IRQ
    await host.stop()


@cocotb.test()
async def test_load_and_run_uart_tx(dut):
    """Load firmware over SPI (with readback verify), run it, decode UART on uio[0]."""
    rx = UartRx(0, 25e6 / 115200)
    board = Board(rx)
    host = await bring_up(dut, board)
    image = assemble_file(FW / "uart_tx.asm")
    n = await host.load(image)
    dut._log.info(f"loaded {n} words over SPI and verified")
    await host.start()
    assert (await host.status())["running"]
    await ClockCycles(dut.clk, 4 * 10 * 218 + 2000)
    assert rx.bytes == [0x55, 0x4B, 0x21, 0x0A], rx.bytes
    assert not board.errors(), board.errors()
    state = (await host.read_dbg(0, 7) >> 2) & 3
    fault = await host.read_dbg(0, 14)
    tele = await host.read_dbg(0, 9)
    dut._log.info(f"ctx0 state {state}, min_lead {tele >> 16}, wait_full {tele & 0xFFFF}")
    assert state == 2 and fault >> 31 == 0      # HALTED, no fault
    await host.stop()
    assert not (await host.status())["running"]


@cocotb.test()
async def test_digest_matches_iss(dut):
    """v1.1 execution digest (11-SEMANTICS 10.1): after the program halts, ONE read of
    sel 16 over SPI must equal the ISS's prediction for the same image. On the gate-level
    netlist this is gates against the independent Python model, tick for tick, in 32 bits."""
    board = Board(UartRx(0, 25e6 / 115200))
    host = await bring_up(dut, board)
    image = assemble_file(FW / "uart_tx.asm")
    await host.load(image)
    await host.start()
    await ClockCycles(dut.clk, 4 * 10 * 218 + 2000)
    assert (await host.read_dbg(0, 7) >> 2) & 3 == 2           # HALTED
    chip = await host.read_dbg(0, 16)
    await ClockCycles(dut.clk, 500)
    again = await host.read_dbg(0, 16)                         # frozen after HALT
    m = Machine(image)
    m.run(200_000)                                             # until idle
    dut._log.info(f"digest chip 0x{chip:08x} ISS 0x{m.dg:08x}")
    assert chip == again == m.dg


def _uart_bytes(pin_edges, tpb):
    """Decode 8N1 bytes from (tick, level) edges of an idle-high line."""
    out, i = [], 0
    while i < len(pin_edges):
        t0, lv = pin_edges[i]
        if lv != 0:
            i += 1
            continue
        level_at = lambda t: [l for (u, l) in pin_edges if u <= t][-1]  # noqa: E731
        out.append(sum(level_at(t0 + (k + 1.5) * tpb) << k for k in range(8)))
        nxt = t0 + 9.5 * tpb
        while i < len(pin_edges) and pin_edges[i][0] <= nxt:
            i += 1
    return out


@cocotb.test()
async def test_logic_analyzer_over_spi(dut):
    """v1.1 capture: arm, record a UART peer on uio[1], STOP, READ_TAPE over SPI, decode."""
    tpb = 25e6 / 115200
    msg = b"Hi"
    board = Board(UartTx(1, tpb, msg, start=6000, gap_bits=1.0))
    host = await bring_up(dut, board)
    src = '.equ logic_analyzer.MASK, 0x02\n.include "logic_analyzer.asm"\n'
    image = assemble(src, base_dir=FW)
    await host.load(image)
    await host.start()
    await ClockCycles(dut.clk, 6000 + int(len(msg) * 11 * tpb) + 3000)
    await host.stop()
    status, cfg = await host.read_dbg(0, 17), await host.read_dbg(0, 18)
    assert not (status >> 30) & 1, hex(status)                 # no overflow
    n = status & 0xFF
    tape = [0] * 256
    tape[:n] = await host.read_tape(0, n)
    recs = decode_capture(tape, status, cfg)
    got = _uart_bytes(edges(recs, 1), tpb)
    dut._log.info(f"captured {n} records over SPI: {bytes(got)!r}")
    assert bytes(got) == msg


@cocotb.test()
async def test_streaming_capture_over_spi(dut):
    """v1.3 streaming capture through the real tape port arbiter (project.v): the logic
    analyser streams a UART peer into an 8-word ring while context 1 hammers the tape
    with LDT/STT. The host reads and acknowledges records during the run; a message far
    longer than the ring arrives complete, with no overflow and no failed fetch."""
    tpb = 25e6 / 38400
    msg = b"Hi!"
    board = Board(UartTx(1, tpb, msg, start=30000, gap_bits=1.0))   # after the load
    host = await bring_up(dut, board)
    t0 = get_sim_time(unit="ns")                                    # board tick 0
    src = """
.equ logic_analyzer.STREAM, 1
.equ logic_analyzer.MASK, 0x02
.equ logic_analyzer.BASE, 0x80
.equ logic_analyzer.LIM, 0x88
.include "logic_analyzer.asm"
.entry 1, storm
storm:
    MOVI  r2, 0x10
    LDLC  4000
spin:
    STT   r1, r2, 0
    LDT   r3, r2, 1
    DJNZ  spin
    HALT
"""
    image = assemble(src, base_dir=FW)
    await host.load(image)
    await host.start()
    end = t0 + CLK_NS * (30000 + int(len(msg) * 11 * tpb) + 3000)
    words, rptr, polls = [], 0x80, 0
    while get_sim_time(unit="ns") < end:
        w, rptr, st = await host.stream_poll(0x80, 0x88, rptr)
        words += w
        polls += 1
        assert not (st >> 30) & 1, f"overflow: status 0x{st:08x}"
    await host.stop()
    w, rptr, st = await host.stream_poll(0x80, 0x88, rptr)    # records staged at STOP
    words += w
    got = _uart_bytes(edges(decode_records(words), 1), tpb)
    dut._log.info(f"streamed {len(words)} records through an 8-word ring in {polls} polls: "
                  f"{bytes(got)!r}")
    assert len(words) > 16 and bytes(got) == msg
    assert not (await host.status())["host_err"]


@cocotb.test()
async def test_slow_bitbang_host_like_the_demo_board(dut):
    """The demo board's bit-bang (sw/rp2350/bringup.py, Link): MOSI, sample MISO
    BEFORE the rising edge, SCK high, SCK low, with microsecond gaps as in MicroPython.
    Sampling after the edge would read the next bit (the chip updates MISO ~3 clocks in)."""
    host_board = Board()
    await bring_up(dut, host_board)
    gap = 60                                   # 2.4 us between pin writes at 25 MHz
    ui = UiBit.shadow

    async def setbit(bit, v):
        cur = ui[id(dut)]
        ui[id(dut)] = (cur & ~(1 << bit)) | ((v & 1) << bit)
        dut.ui_in.value = ui[id(dut)]
        await ClockCycles(dut.clk, gap)

    async def xfer(data, sample_before=True):
        out = []
        await setbit(0, 0)
        for byte in data:
            r = 0
            for i in range(7, -1, -1):
                await setbit(2, (byte >> i) & 1)
                if sample_before:
                    r = (r << 1) | (int(dut.uo_out.value) & 1)
                await setbit(1, 1)
                if not sample_before:
                    r = (r << 1) | (int(dut.uo_out.value) & 1)
                await setbit(1, 0)
            out.append(r)
        await setbit(0, 1)
        return out

    good = await xfer([0x00, 0, 0, 0])
    st = good[0]
    assert st & 0x0F == 8 and not st & 0x80, hex(st)
    bad = await xfer([0x00, 0, 0, 0], sample_before=False)
    assert bad != good                          # the wrong discipline really is wrong
