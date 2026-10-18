"""Unit tests for escapement_host against docs/12-HOST-PROTOCOL.md."""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

from eschost import AsyncHost
from spi_master import SpiMaster


class Pulses:
    """Counts single-cycle strobes and records the data seen with them."""

    def __init__(self, dut):
        self.dut = dut
        self.start = self.stop = 0
        self.irq_clear, self.push, self.pop = [], [], []
        cocotb.start_soon(self._watch())

    async def _watch(self):
        d = self.dut
        while True:
            await RisingEdge(d.clk)
            if int(d.start.value):
                self.start += 1
            if int(d.stop.value):
                self.stop += 1
            if int(d.irq_clear.value):
                self.irq_clear.append(int(d.irq_clear.value))
            if int(d.mbx_rx_push.value):
                m = int(d.mbx_rx_push.value)
                ctx = m.bit_length() - 1
                self.push.append((m, (int(d.mbx_rx_data.value) >> (32 * ctx)) & 0xFFFFFFFF))
            if int(d.mbx_tx_pop.value):
                self.pop.append(int(d.mbx_tx_pop.value))


async def setup(dut, half=8, jitter=0, seed=1):
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    for s in ("running", "ctx_state", "irq_pending", "fault_valid", "mbx_rx_full",
              "mbx_tx_data", "mbx_tx_empty"):
        getattr(dut, s).value = 0
    dut.mbx_tx_empty.value = 0xF
    dut.rst_n.value = 0
    spi = SpiMaster(dut, dut.clk, dut.cs_n, dut.sck, dut.mosi, dut.miso, half, jitter, seed)
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    return AsyncHost(spi.xfer), spi, Pulses(dut)


@cocotb.test()
async def test_status_and_identity(dut):
    host, _, _ = await setup(dut)
    dut.ctx_state.value = 0b10_01_11_00
    dut.irq_pending.value = 0b0101
    dut.mbx_rx_full.value = 0b0011
    dut.mbx_tx_empty.value = 0b1010
    s = await host.status()
    assert s["version"] == 7 and not s["host_err"] and not s["running"]
    assert s["irq"] and s["ctx_state"] == 0b10011100
    assert s["rx_full"] == 0b0011 and s["tx_empty"] == 0b1010 and s["irq_pending"] == 0b0101
    dut.running.value = 1
    dut.fault_valid.value = 1
    s = await host.status()
    assert s["running"] and s["fault"]


async def imem_round_trip(dut, half, jitter, seed):
    host, _, _ = await setup(dut, half, jitter, seed)
    rng = random.Random(seed)
    words = [rng.randrange(1 << 32) for _ in range(256)]
    st = await host.write_imem(0, words)
    assert not st["host_err"]
    got = await host.read_imem(0, 256)
    assert got == words, f"first diff at {next(i for i in range(256) if got[i] != words[i])}"
    # Partial read from an odd address, wrapping past 255.
    got = await host.read_imem(250, 10)
    assert got == words[250:] + words[:4]


@cocotb.test()
async def test_imem_round_trip_at_sck_limit(dut):
    """f_SCK = f_clk/8 exactly (SCK high/low = 4 clocks), all 256 words."""
    await imem_round_trip(dut, half=4, jitter=0, seed=11)


@cocotb.test()
async def test_imem_round_trip_jittered(dut):
    """SCK half-periods randomly 4..12 clocks."""
    await imem_round_trip(dut, half=8, jitter=4, seed=12)


@cocotb.test()
async def test_cfg_start_stop(dut):
    host, _, pulses = await setup(dut)
    await host.write_cfg(0b1011, 0x44332211, 0xF0, 0xBEEF, 0x81)
    assert int(dut.ctx_enable.value) == 0b1011 and int(dut.entry_pcs.value) == 0x44332211
    assert int(dut.pin_owned.value) == 0xF0 and int(dut.pin_owner.value) == 0xBEEF
    assert int(dut.pin_od.value) == 0x81 and int(dut.ctx_bank.value) == 0
    await host.start()
    await host.stop()
    assert (pulses.start, pulses.stop) == (1, 1)
    dut.running.value = 1
    st = await host.start()          # START while running: ignored, host error
    st = await host.status()
    assert pulses.start == 1 and st["host_err"]
    await host.write_cfg(0, 0, 0, 0, 0)  # WRITE_CFG while running: ignored
    assert int(dut.ctx_enable.value) == 0b1011
    await host.clear_err()
    assert not (await host.status())["host_err"]


@cocotb.test()
async def test_cfg_bank_b(dut):
    """v1.2: WRITE_CFG bytes 10-13 set pin bank B; a 9-byte (v1.1) frame leaves them."""
    host, spi, _ = await setup(dut)
    await host.write_cfg(0b0001, 0x00000000, 0x15F0, 0x2A5BEEF, 0x81, 0b0110)
    assert int(dut.pin_owned.value) == 0x15F0 and int(dut.pin_owner.value) == 0x2A5BEEF
    assert int(dut.ctx_bank.value) == 0b0110 and int(dut.pin_od.value) == 0x81
    await spi.xfer(bytes((0x12, 0b0011, 1, 2, 3, 4, 0x0F, 0x12, 0x34, 0x00)))
    assert int(dut.ctx_enable.value) == 0b0011 and int(dut.entry_pcs.value) == 0x04030201
    assert int(dut.pin_owned.value) == 0x150F and int(dut.pin_owner.value) == 0x2A51234
    assert int(dut.ctx_bank.value) == 0b0110 and int(dut.pin_od.value) == 0


@cocotb.test()
async def test_write_while_running_is_rejected(dut):
    host, _, _ = await setup(dut)
    await host.write_imem(5, [0x11111111])
    dut.running.value = 1
    await host.write_imem(5, [0x22222222])
    assert (await host.status())["host_err"]
    dut.running.value = 0
    assert await host.read_imem(5, 1) == [0x11111111]


@cocotb.test()
async def test_read_dbg(dut):
    host, _, _ = await setup(dut)
    for ctx in range(4):
        for sel in (0, 7, 14, 15, 16, 19, 31):      # v1.1: 5-bit select
            v = await host.read_dbg(ctx, sel)
            expect = ((0xD0 | ctx) << 24) | (sel << 16) | (0xBEEF ^ sel)
            assert v == expect, (ctx, sel, hex(v))


@cocotb.test()
async def test_mailboxes_and_irq_clear(dut):
    host, _, pulses = await setup(dut)
    await host.mbx_push(2, 0xCAFEF00D)
    assert pulses.push == [(0b0100, 0xCAFEF00D)]
    dut.mbx_rx_full.value = 0b0100
    st = await host.mbx_push(2, 0x12345678)
    assert len(pulses.push) == 1 and (await host.status())["host_err"]
    await host.clear_err()
    data = 0
    for k in range(4):
        data |= (0xA0000000 | k) << (32 * k)
    dut.mbx_tx_data.value = data
    dut.mbx_tx_empty.value = 0b1101          # only ctx 1 has data
    assert await host.mbx_pop(1) == 0xA0000001 and pulses.pop == [0b0010]
    assert await host.mbx_pop(3) is None and pulses.pop == [0b0010]
    await host.irq_clear(0b1001)
    assert pulses.irq_clear == [0b1001]


@cocotb.test()
async def test_unknown_command_and_aborted_frame(dut):
    host, spi, pulses = await setup(dut)
    await spi.xfer(bytes((0x99, 0, 0)))
    assert (await host.status())["host_err"]
    await host.clear_err()
    # Abort a WRITE_IMEM in the middle of a word: nothing is written, next frame is clean.
    await host.write_imem(9, [0x0BADF00D])
    await spi.xfer(bytes((0x10, 9, 0xDE, 0xAD, 0xBE, 0xEF)), abort_after_bits=8 * 4 + 3)
    assert await host.read_imem(9, 1) == [0x0BADF00D]
    s = await host.status()
    assert s["version"] == 7 and not s["host_err"] and pulses.start == 0


@cocotb.test()
async def test_tape_space(dut):
    """v1.1 WRITE_TAPE/READ_TAPE reach the tape only; the program store is untouched."""
    host, _, _ = await setup(dut)
    await host.write_imem(0x20, [0x11111111, 0x22222222])
    await host.write_tape(0x20, [0xA5A5A5A5, 0x5A5A5A5A, 0xDEADBEEF])
    assert await host.read_tape(0x20, 3) == [0xA5A5A5A5, 0x5A5A5A5A, 0xDEADBEEF]
    assert await host.read_imem(0x20, 2) == [0x11111111, 0x22222222]
    s = await host.status()
    assert not s["host_err"], s
    dut.running.value = 1                        # writes while running are refused
    await host.write_tape(0x20, [0])
    dut.running.value = 0
    assert (await host.status())["host_err"]
    await host.clear_err()
    assert await host.read_tape(0x20, 1) == [0xA5A5A5A5]
