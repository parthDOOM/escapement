# SPDX-FileCopyrightText: © 2026 Escapement contributors
# SPDX-License-Identifier: Apache-2.0
"""Workload activity for power analysis (docs/06-DECISIONS.md D-29).

    make GATES=yes COMPILE_ARGS=-DPOWER_VCD COCOTB_TEST_MODULES=power POWER_WORKLOAD=uart_tx

Runs one workload on the gate-level netlist and records a VCD of the design for exactly
the measurement window (tb.v holds the dump on while `dump_on` is high). OpenSTA then
reads it (scripts/power_vcd.tcl) to report power from real switching activity instead
of the default static guess, which assumes 10% toggling everywhere and cannot know
that a gated clock is idle.

Workloads (POWER_WORKLOAD):
  uart_tx         firmware/uart_tx.asm sending 4 bytes at 115200 baud: a typical
                  emulation, mostly waiting on deadlines (window: the transmission)
  logic_analyzer  capture of a 115200 baud peer into the tape (tape writes, capture)
  idle            loaded, stopped chip with the host link quiet
"""

import os
from pathlib import Path

import cocotb
from cocotb.triggers import ClockCycles
from cocotb.utils import get_sim_time

from escasm import assemble, assemble_file
from escmodels import Board, UartRx, UartTx
from test import bring_up

FW = Path(__file__).resolve().parents[1] / "firmware"
WINDOW = int(os.environ.get("POWER_CYCLES", "8000"))
PEER_START = 40_000          # board ticks: after the SPI load (~17k cycles), inside the window


@cocotb.test()
async def power_workload(dut):
    w = os.environ.get("POWER_WORKLOAD", "uart_tx")
    tpb = 25e6 / 115200
    if w == "uart_tx":
        board = Board(UartRx(0, tpb))
        image = assemble_file(FW / "uart_tx.asm")
    elif w == "logic_analyzer":
        board = Board(UartTx(1, tpb, b"Hi", start=PEER_START, gap_bits=1.0))
        image = assemble('.equ logic_analyzer.MASK, 0x02\n.include "logic_analyzer.asm"\n',
                         base_dir=FW)
    elif w == "idle":
        board = Board()
        image = assemble_file(FW / "uart_tx.asm")
    else:
        raise ValueError(w)
    host = await bring_up(dut, board)
    await host.load(image)
    if w != "idle":
        await host.start()
    await ClockCycles(dut.clk, 20)
    st = await host.status()                 # before the window: SPI traffic stays out
    assert st["running"] == (w != "idle") and not st["fault"], st
    if w == "logic_analyzer":                # open the window just before the peer talks
        now = int(get_sim_time("ns")) // 40 - 15          # board tick (bring_up: +15)
        assert now < PEER_START - 200, f"load ran past the peer start ({now})"
        await ClockCycles(dut.clk, PEER_START - 200 - now)
    dut.dump_on.value = 1
    await ClockCycles(dut.clk, WINDOW)       # the simulation, and the VCD, end here
    dut._log.info(f"workload {w}: {WINDOW} cycles recorded; status before {st}")
