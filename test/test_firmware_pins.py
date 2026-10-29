# SPDX-FileCopyrightText: © 2026 Escapement contributors
# SPDX-License-Identifier: Apache-2.0
"""SWD, SPI target, NEC infrared, the two-bank image and a protocol learned from a
recording, through the chip's real pins, on RTL and on the gate-level netlist (make
pin-test / make gl-pin-test): each program is loaded over SPI and run against the bus
models of sw/escmodels; the protocol outcome and the host replies must be right, with no
fault and no contention."""

from cocotb.triggers import ClockCycles

import escgen
import esclearn
import test as chip                    # test/test.py: bring_up, FW
from escasm import assemble, assemble_file
from escmodels import (Board, JtagTap, NecReceiver, Ps2Host, SpiTarget, SplitBoard, SwdTarget,
                       Tsop, UartRx)
from escmodels.nec import NecSource, nec_raw
from escmodels.spi import SpiController
from esctape import decode_capture

FW = chip.FW
REPO = FW.parent


async def pop_n(dut, host, ctx, n, max_cycles, step=400):
    got, waited = [], 0
    while len(got) < n and waited < max_cycles:
        await ClockCycles(dut.clk, step)
        waited += step
        v = await host.mbx_pop(ctx)
        if v is not None:
            got.append(v)
    return got


async def no_fault(host):
    st = await host.status()
    assert not st["fault"] and not st["host_err"], st


@chip.cocotb.test()
async def test_swd_on_chip(dut):
    tgt = SwdTarget(4, 5, dpidr=0x0BC12477, waits=2, ap_regs={(0, 0xFC): 0x24770011})
    board = Board(tgt)
    host = await chip.bring_up(dut, board)
    await host.load(assemble_file(FW / "swd_idcode.asm"))
    await host.start()
    replies = []
    for words in [(0x80000004,), (0x04,), (0x10, 0xF0), (0x1E,), (0x1C,)]:
        for w in words:
            await host.mbx_push(2, w)
        replies.append(tuple(await pop_n(dut, host, 2, 2, 20_000)))
    dut._log.info(f"SWD replies {[tuple(map(hex, r)) for r in replies]}")
    assert replies == [(1, 0x0BC12477), (1, 0x0BC12477), (0x201, 0), (1, 0), (1, 0x24770011)]
    assert tgt.writes == [(0, 0x8, 0xF0)], tgt.writes
    assert not board.errors(), board.errors()
    await no_fault(host)


@chip.cocotb.test()
async def test_nec_loopback_on_chip(dut):
    """Both NEC programs at a 2.5 MHz timebase (as the co-simulation): carrier on pin 0,
    receiver-module model into pin 1, a frame then a repeat code one slot later."""
    rx = NecReceiver(0, tick_ns=400.0)
    board = Board(rx, Tsop(0, 1, tick_ns=400.0))
    host = await chip.bring_up(dut, board)
    image = assemble(open(REPO / "test/cosim/nec_ir_cosim.asm").read(),
                     base_dir=REPO / "test/cosim")
    await host.load(image)
    await host.start()
    await host.mbx_push(0, 0x5AA5)
    await host.mbx_push(0, 1 << 31)
    got = await pop_n(dut, host, 1, 2, 330_000, step=5_000)
    acks = await pop_n(dut, host, 0, 2, 20_000, step=2_000)
    dut._log.info(f"NEC rx {[hex(v) for v in got]} acks {acks} frames {rx.frames()}")
    assert rx.frames() == [(0xA5, 0x5A), "R"], rx.events
    assert got == [0x5A00A5, 1 << 31] and acks == [1, 1]
    assert not board.errors(), board.errors()
    await no_fault(host)


async def spi_target_run(dut, mode, frames, mem):
    ctl = SpiController(4, 5, 6, 7, frames, mode=mode, t_high=30, t_low=30, t_css=25,
                        t_idle=100, start_at=2_000)
    board = Board()                     # the controller joins once the program runs
    host = await chip.bring_up(dut, board)
    await host.load(assemble_file(FW / "spi_target.asm"))
    await host.write_tape(0, mem)
    await host.start()
    board.devices.append(ctl)           # its script starts 2,000 cycles from here
    for _ in range(40):
        await ClockCycles(dut.clk, 1_000)
        if ctl.done:
            break
    assert ctl.done and not board.errors() and not ctl.violations, (ctl.violations, board.errors())
    await no_fault(host)
    return host, ctl


@chip.cocotb.test()
async def test_spi_target_mode0_on_chip(dut):
    mem = [(a * 11 + 5) & 0xFF for a in range(256)]
    frames = [([0x02, 0x40, 0xDE, 0xAD], None),
              ([0x03, 0x3F, 0, 0, 0, 0], [None, None, mem[0x3F], 0xDE, 0xAD, mem[0x42]]),
              ([0x03, 0xFF, 0, 0], [None, None, mem[0xFF], mem[0x00]]),
              ([0x02, 0x50, 0x12, 0x34], None, 27)]
    host, ctl = await spi_target_run(dut, 0, frames, mem)
    await host.stop()                   # the core owns the tape port while it runs
    await ClockCycles(dut.clk, 100)
    assert await host.read_tape(0x40, 2) == [0xDE, 0xAD]
    assert await host.read_tape(0x50, 2) == [0x12, mem[0x51]]     # cut-short byte dropped


@chip.cocotb.test()
async def test_spi_target_mode3_on_chip(dut):
    mem = [(a * 13 + 1) & 0xFF for a in range(256)]
    frames = [([0x03, 0x10, 0, 0, 0], [None, None, mem[0x10], mem[0x11], mem[0x12]]),
              ([0x02, 0x20, 0x99], None),
              ([0x03, 0x20, 0], [None, None, 0x99])]
    await spi_target_run(dut, 3, frames, mem)


@chip.cocotb.test()
async def test_two_banks_on_chip(dut):
    """v1.2: PS/2 and JTAG on uio[7:0] while an SPI controller and a UART transmitter run
    on bank B (uo_out[7:3] / ui_in[7:3]), eleven protocol signals at once."""
    ps2 = Ps2Host(0, 1)
    tap = JtagTap(4, 5, 6, 7, idcode=0x4BA00477)
    spi = SpiTarget(sck=0, mosi=1, miso=2, cs=3, mode=3, reply=bytes((0x3C, 0xC3)))
    uart = UartRx(4, 25e6 / 115200)
    board, board_b = Board(ps2, tap), SplitBoard(spi, uart, outputs=0b11011)
    host = await chip.bring_up(dut, board, board_b)
    await host.load(assemble_file(FW / "combo_two_banks.asm"))
    await host.start()
    await host.mbx_push(2, 3)            # SPI mode 3
    await host.mbx_push(0, 0x1C)         # PS/2 scancode
    await host.mbx_push(1, 0)            # JTAG IDCODE request
    await host.mbx_push(2, 0x1A5)        # SPI: 0xA5, CS held
    await host.mbx_push(2, 0x05A)        # SPI: 0x5A, CS released
    spi_rx = await pop_n(dut, host, 2, 2, 20_000)
    idcode = await pop_n(dut, host, 1, 1, 20_000)
    ack = await pop_n(dut, host, 0, 1, 40_000, step=2_000)
    dut._log.info(f"two banks: spi {spi_rx} {spi.frames} jtag {idcode} ps2 {ps2.bytes} "
                  f"ack {ack} uart {uart.bytes}")
    assert spi.frames == [[0xA5, 0x5A]] and spi_rx == [0x3C, 0xC3]
    assert idcode == [0x4BA00477] and ps2.bytes == [0x1C] and ack == [1]
    assert uart.bytes == [0x55, 0x4B, 0x21, 0x0A], uart.bytes
    assert not board.errors() and not board_b.errors(), board.errors() + board_b.errors()
    await no_fault(host)


@chip.cocotb.test()
async def test_learned_nec_on_chip(dut):
    """Record it, speak it, on the RTL (sw/esclearn): a remote built from the NEC
    specification presses three buttons on uio[1], as an IR receiver's output (low
    during a burst). The chip records them (logic_analyzer.asm), the host reads the tape,
    esclearn learns the protocol and escgen builds firmware that escert certifies. The
    host keeps the address, changes the command to one the remote never sent (esclearn
    recomputes its complement), and the chip sends it on uio[0], where the
    specification's receiver decodes it and checks every mark and space. Timebase 20x
    (tick_ns=800) to keep the simulation short: the learned table is in chip ticks."""
    tick, slot = 800.0, int(108e6 / 800)
    cmds = (0x08, 0x10, 0x15)
    remote = NecSource(1, [(20_000 + slot * i, nec_raw(0x04, c)) for i, c in enumerate(cmds)],
                       tick_ns=tick)
    rx = NecReceiver(0, tick_ns=tick, modulated=False, active_low=True)
    board = Board(remote, rx)
    host = await chip.bring_up(dut, board)
    await host.load(assemble('.equ logic_analyzer.MASK, 0x02\n.include "logic_analyzer.asm"\n',
                             base_dir=FW))
    await host.start()
    await ClockCycles(dut.clk, 20_000 + slot * len(cmds))
    await host.stop()
    status, cfg = await host.read_dbg(0, 17), await host.read_dbg(0, 18)
    assert not (status >> 30) & 1, hex(status)                 # no overflow
    n = status & 0xFF
    tape = [0] * 256
    tape[:n] = await host.read_tape(0, n)
    model = esclearn.learn(decode_capture(tape, status, cfg), pin=1, name="learned_remote")
    dut._log.info(f"{n} records: " + "; ".join(model.summary()[:4]))
    assert model.coding == "pulse distance" and model.idle == 1
    assert model.frames == [[nec_raw(0x04, c)] for c in cmds]

    word = model.complete([model.frames[0][0] & 0x0000FFFF | 0x42 << 16])
    assert word == [nec_raw(0x04, 0x42)]
    _, image = escgen.build(model.table("mailbox", pin=0))     # CHECKED, or TableError
    await host.load(image)
    await host.start()
    await host.mbx_push(0, word[0])
    ack = await pop_n(dut, host, 0, 1, 2 * slot, step=5_000)
    dut._log.info(f"sent 0x{word[0]:08x}: receiver {rx.frames()} ack {ack}")
    assert rx.frames() == [(0x04, 0x42)] and ack == [1], rx.events
    assert not board.errors(), board.errors()
    await no_fault(host)
