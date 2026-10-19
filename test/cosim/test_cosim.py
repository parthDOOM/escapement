"""ISS <-> RTL lock-step comparison (docs/04-VERIFICATION.md, docs/11-SEMANTICS.md §10).

For each program: assemble once, run the Python ISS and the RTL with the same board
model, then compare every commit record, the pin outputs of every run cycle, and the
full final state through the dbg_sel read map. Any difference fails the test with the
first diverging record and its disassembly.

Environment: COSIM_SEEDS (default 200), COSIM_SEED0 (default 1), COSIM_MAX_TICKS,
COSIM_FIRMWARE (restrict the bus-model test to the named firmware).
"""

from __future__ import annotations

import os
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

from escasm import AsmError, assemble, assemble_file, disassemble
from esciss import Machine
from esciss.machine import no_board_b

from escmodels import (Board, CanBus, HostScript, I2cController, I2cTarget, JtagTap,
                       OneWireDevice, Ps2Host, SpiTarget, SplitBoard, UartRx, UartTx,
                       Ws2812Decoder, crc8_maxim)
from progen import gen_program, loopback_board, make_board, make_board_b

REPO = Path(__file__).resolve().parents[2]
MAX_TICKS = int(os.environ.get("COSIM_MAX_TICKS", "4000"))
POST_KEYS = ["r0", "r1", "r2", "r3", "sh", "lc", "tmr", "z", "c", "pc"]


async def reset_and_load(dut, image):
    dut.rst_n.value = 0
    for sig in ("start", "stop", "load_we", "load_space", "load_addr", "load_data", "uio_in",
                "mbx_rx_push", "mbx_rx_data", "mbx_tx_pop", "dbg_sel_ctx", "dbg_sel", "irq_clear",
                "ctx_enable", "entry_pcs", "pin_owned", "pin_owner", "pin_od", "ctx_bank",
                "ib_in"):
        getattr(dut, sig).value = 0
    for _ in range(4):
        await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    for space, words in ((0, image.words), (1, image.tape)):   # program, then tape (v1.1)
        for addr, word in enumerate(words):
            await FallingEdge(dut.clk)
            dut.load_we.value = 1
            dut.load_space.value = space
            dut.load_addr.value = addr
            dut.load_data.value = word
    await FallingEdge(dut.clk)
    dut.load_we.value = 0
    dut.load_space.value = 0
    dut.ctx_enable.value = image.ctx_enable_mask
    dut.entry_pcs.value = image.entry_pcs_packed
    dut.pin_owned.value = image.pin_owned_mask
    dut.pin_owner.value = image.pin_owner_packed
    dut.pin_od.value = image.pin_od_mask
    dut.ctx_bank.value = image.ctx_bank_mask
    for _ in range(4):  # uio_in = 0 so both synchroniser stages are 0 at start
        await FallingEdge(dut.clk)


async def run_rtl(dut, image, board, n_cycles, host=None, board_b=no_board_b):
    """Returns (commits, commits<n, pins, final) using the RTL. Cycle numbering per §2.
    `host` (an escmodels.HostScript, never attached to a machine) gives the mailbox
    schedule; pops are recorded into host.received like the ISS run does."""
    await reset_and_load(dut, image)
    pushes = sorted(host.pushes) if host else []
    # Falling edge of cycle -1: assert start, sampled at E_-1.
    dut.start.value = 1
    dut.uio_in.value = board(-1, 0, 0)
    dut.ib_in.value = board_b(-1, 0) & 0x1F
    commits, pins = [], []
    for t in range(n_cycles + 1):
        await FallingEdge(dut.clk)  # mid-cycle t: registered outputs are stable
        dut.start.value = 0
        # host side of the mailboxes: strobes during cycle t take effect at E_t
        push_mask, push_data, pop_mask = 0, 0, 0
        while pushes and pushes[0][0] <= t:
            _, ctx, word = pushes.pop(0)
            push_mask |= 1 << ctx
            push_data |= (word & 0xFFFFFFFF) << (32 * ctx)
        if host and host.drain and t % host.poll == 0:
            empty = int(dut.mbx_tx_empty.value)
            data = int(dut.mbx_tx_data.value)
            for k in host.drain:
                if not (empty >> k) & 1:
                    host.received[k].append((data >> (32 * k)) & 0xFFFFFFFF)
                    pop_mask |= 1 << k
        dut.mbx_rx_push.value = push_mask
        dut.mbx_rx_data.value = push_data
        dut.mbx_tx_pop.value = pop_mask
        out, oe, ob = int(dut.uio_out.value), int(dut.uio_oe.value), int(dut.ob_out.value)
        if t < n_cycles:
            pins.append({"t": t, "uio_out": out, "uio_oe": oe, "ob": ob})
        if int(dut.dbg_commit_valid.value):
            post = {k: int(getattr(dut, f"dbg_post_{k}").value) for k in POST_KEYS}
            commits.append({
                "t": t - 1, "ctx": int(dut.dbg_commit_ctx.value),
                "pc": int(dut.dbg_commit_pc.value), "insn": int(dut.dbg_commit_insn.value),
                "outcome": "CWF?"[int(dut.dbg_commit_outcome.value)], "post": post,
            })
        dut.uio_in.value = board(t, out, oe)
        dut.ib_in.value = board_b(t, ob) & 0x1F
    # Now in cycle n_cycles: state after E_(n_cycles-1).
    final = {}
    for ctx in range(4):
        for sel in range(14):
            dut.dbg_sel_ctx.value = ctx
            dut.dbg_sel.value = sel
            await Timer(1, unit="ps")
            final[(ctx, sel)] = int(dut.dbg_rdata.value)
    for sel in (14, 15, 16, 17, 18, 19):          # 16-19: digest + capture (v1.1)
        dut.dbg_sel.value = sel
        await Timer(1, unit="ps")
        final[(0, sel)] = int(dut.dbg_rdata.value)
    for ctx in range(4):                          # 20-31 read 0
        for sel in range(20, 32):
            dut.dbg_sel_ctx.value = ctx
            dut.dbg_sel.value = sel
            await Timer(1, unit="ps")
            final[(ctx, sel)] = int(dut.dbg_rdata.value)
    mem = dut.u_tape.sram.i_SRAM_1P_behavioral_bm_bist.memory
    for a in range(256):
        final[("tape", a)] = int(mem[a].value)
    return commits, [c for c in commits if c["t"] < n_cycles], pins, final


def iss_final(m: Machine) -> dict:
    f = {(ctx, sel): m.dbg_read(ctx, sel) for ctx in range(4) for sel in range(14)}
    for sel in (14, 15, 16, 17, 18, 19):
        f[(0, sel)] = m.dbg_read(0, sel)
    f.update({(ctx, sel): m.dbg_read(ctx, sel) for ctx in range(4) for sel in range(20, 32)})
    for a in range(256):
        f[("tape", a)] = m.tape[a]
    return f


def compare(name, image, m: Machine, rtl_commits, rtl_pins, rtl_final, log):
    errs = []
    n = min(len(m.commits), len(rtl_commits))
    for i in range(n):
        a, b = m.commits[i], rtl_commits[i]
        if a != b:
            errs.append(f"commit #{i} differs\n  ISS: {a}\n  RTL: {b}\n  insn: "
                        f"{disassemble(a['insn'])} | {disassemble(b['insn'])}")
            break
    if not errs and len(m.commits) != len(rtl_commits):
        extra = (m.commits if len(m.commits) > n else rtl_commits)[n]
        errs.append(f"commit count ISS {len(m.commits)} vs RTL {len(rtl_commits)}; first extra {extra}")
    for a, b in zip(m.pins, rtl_pins):
        if a != b:
            errs.append(f"pins differ at t={a['t']}: ISS {a} RTL {b}")
            break
    if len(m.pins) != len(rtl_pins):
        errs.append(f"pin record count ISS {len(m.pins)} vs RTL {len(rtl_pins)}")
    fin = iss_final(m)
    for key in sorted(fin, key=str):
        if fin[key] != rtl_final[key]:
            errs.append(f"final state ctx {key[0]} sel {key[1]}: ISS 0x{fin[key]:08x} RTL 0x{rtl_final[key]:08x}")
    if errs:
        log.error(f"[{name}] MISMATCH:\n" + "\n".join(errs[:6]))
    return errs


async def cosim_one(dut, name, image, board, max_ticks=MAX_TICKS, rtl_board=None,
                    iss_host=None, rtl_host=None, stop_when_idle=True, board_b=no_board_b,
                    rtl_board_b=None):
    """Stateless boards may be shared; stateful ones need `rtl_board` (a fresh twin)."""
    m = Machine(image, board=board, board_b=board_b)
    if iss_host is not None:
        iss_host.machine = m
    m.run(max_ticks, stop_when_idle=stop_when_idle)
    n_cycles = len(m.pins)
    all_commits, rtl_commits, rtl_pins, rtl_final = await run_rtl(
        dut, image, rtl_board or board, n_cycles, rtl_host, rtl_board_b or board_b)
    errs = compare(name, image, m, rtl_commits, rtl_pins, rtl_final, dut._log)
    if iss_host is not None and iss_host.received != rtl_host.received:
        errs.append(f"host mailbox results differ: ISS {iss_host.received} RTL {rtl_host.received}")
        dut._log.error(f"[{name}] {errs[-1]}")
    return errs, m


@cocotb.test()
async def test_directed_firmware(dut):
    """Shipped firmware and the spec's worked example, compared tick by tick."""
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    cases = {
        "uart_tx": (REPO / "firmware" / "uart_tx.asm").read_text(),
        "spec_s11": ".entry 0, s\n.own 0, 0\ns: ADDT 10\n EMIT 0x01, 0x01, 0x01\n HALT\n",
    }
    failures = []
    for name, src in cases.items():
        errs, m = await cosim_one(dut, name, assemble(src), loopback_board, max_ticks=20000)
        dut._log.info(f"[{name}] {len(m.commits)} commits, {len(m.pins)} cycles: "
                      f"{'MATCH' if not errs else 'MISMATCH'}")
        if errs:
            failures.append(name)
    assert not failures, f"mismatching programs: {failures}"


@cocotb.test()
async def test_random_programs(dut):
    """Constrained-random programs (with deliberate faults) on random boards."""
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    seeds = int(os.environ.get("COSIM_SEEDS", "200"))
    seed0 = int(os.environ.get("COSIM_SEED0", "1"))
    out_dir = Path(os.environ.get("COSIM_OUT", "cosim_failures"))
    failures, commits, cycles, skipped = [], 0, 0, 0
    for seed in range(seed0, seed0 + seeds):
        src = gen_program(seed)
        try:
            image = assemble(src)
        except AsmError:
            skipped += 1  # e.g. generated program exceeded 256 words
            continue
        errs, m = await cosim_one(dut, f"seed{seed}", image, make_board(seed),
                                  board_b=make_board_b(seed))
        commits += len(m.commits)
        cycles += len(m.pins)
        if errs:
            failures.append(seed)
            out_dir.mkdir(exist_ok=True)
            (out_dir / f"seed{seed}.asm").write_text(src)
            (out_dir / f"seed{seed}.txt").write_text("\n".join(errs))
            if len(failures) >= 5:
                break
    dut._log.info(f"random: {seeds - skipped} programs, {commits} commits, {cycles} cycles, "
                  f"{skipped} skipped, {len(failures)} mismatching {failures}")
    assert not failures, f"mismatching seeds: {failures} (see {out_dir}/)"


ROM = bytes([0x28, 0xAA, 0x01, 0x02, 0x03, 0x04, 0x05])
ROM += bytes([crc8_maxim(ROM)])
UART_TPB = 25e6 / 115200


def i2c_cmd(addr7, reg, data=0, write=False, read=False):
    return (addr7 << 24) | (reg << 16) | (data << 8) | (1 if write else 0) | (2 if read else 0)


def case_uart_rx():
    tx = UartTx(1, UART_TPB, b"Esc\x00\xff", start=300, gap_bits=0.0)
    host = HostScript([], drain=[1], poll=1400)

    def check(board, h):
        return [] if h.received[1] == list(b"Esc\x00\xff") else [f"uart_rx got {h.received[1]}"]
    return Board(tx, host), host, check


def case_i2c():
    tgt = I2cTarget(2, 3, 0x42, {5: 0}, mode="fast", stretch_ticks=37)
    host = HostScript([(50, 0, i2c_cmd(0x42, 5, 0xA5, write=True)),
                       (4000, 0, i2c_cmd(0x42, 5, read=True))], drain=[0], poll=500)

    def check(board, h):
        e = [] if h.received[0] == [0x0000, 0x00A5] else [f"i2c results {h.received[0]}"]
        return e + board.errors()
    return Board(tgt, host), host, check


def case_spi3():
    tgt = SpiTarget(4, 5, 6, 7, mode=3, reply=b"\x3C\xC3")
    host = HostScript([(20, 2, 3), (100, 2, 0x1A5), (110, 2, 0x05A)], drain=[2], poll=300)

    def check(board, h):
        ok = tgt.frames == [[0xA5, 0x5A]] and h.received[2] == [0x3C, 0xC3]
        return ([] if ok else [f"spi {tgt.frames} {h.received[2]}"]) + board.errors()
    return Board(tgt, host), host, check


def case_ws2812():
    dec = Ws2812Decoder(0)
    host = HostScript([(30, 3, 0x102030), (40, 3, 0x010203), (2000, 3, 4)], drain=[3], poll=97)

    def check(board, h):
        dec.finish()
        ok = dec.frames == [[0x102030, 0x112233, 0x122436, 0x132639]]
        return ([] if ok else [f"ws2812 {dec.frames}"]) + board.errors()
    return Board(dec, host), host, check


def case_onewire():
    dev = OneWireDevice(1, ROM)
    host = HostScript([(30, 3, 0x80000033), (40000, 3, 0xFF)], drain=[3], poll=500)

    def check(board, h):
        r = h.received[3]
        ok = dev.written[:1] == [0x33] and len(r) >= 2 and (r[0] >> 8) == 1 and (r[1] & 0xFF) == ROM[0]
        return ([] if ok else [f"1-wire {dev.written} {r}"]) + board.errors()
    return Board(dev, host), host, check


def case_combo():
    tx_dec = UartRx(0, UART_TPB)
    peer = UartTx(1, UART_TPB, b"hello", start=700, gap_bits=0.0)
    i2c = I2cTarget(2, 3, 0x42, {9: 0x77}, mode="fast", stretch_ticks=50)
    host = HostScript([(100, 0, i2c_cmd(0x42, 9, 0x33, write=True)),
                       (4500, 0, i2c_cmd(0x42, 9, read=True))], drain=[0, 1], poll=700)

    def check(board, h):
        e = []
        if tx_dec.bytes != [0x55, 0x4B, 0x21, 0x0A]:
            e.append(f"uart tx {tx_dec.bytes}")
        if h.received.get(1) != list(b"hello"):
            e.append(f"uart rx {h.received.get(1)}")
        if h.received.get(0) != [0x0000, 0x0033]:
            e.append(f"i2c {h.received.get(0)}")
        return e + board.errors()
    return Board(tx_dec, peer, i2c, host), host, check


EEPROM_SCRIPT = [("write", 0x50, 0x10, [0xDE, 0xAD, 0xBE, 0xEF]), ("read", 0x50, 0x10, 4),
                 ("probe", 0x51), ("cur", 0x50, 2), ("write", 0x50, 0x1E, [1, 2, 3]),
                 ("read", 0x50, 0x18, 1)]


def case_eeprom():
    """v1.1: 24C02 emulator, memory in the tape, Fast-mode controller with timing checks."""
    ctl = I2cController(2, 3, EEPROM_SCRIPT, mode="fast")

    def check(board, h):
        r = ctl.results
        ok = (len(r) == 6 and r[1]["data"] == [0xDE, 0xAD, 0xBE, 0xEF] and r[2]["acks"] == [False]
              and r[5]["data"] == [3])
        return ([] if ok else [f"eeprom {[(x['acks'], x['data']) for x in r]}"]) + board.errors()
    return Board(ctl), None, check


def case_can():
    """CAN 2.0A transmitter in firmware (CRC-15, stuffing) against a receiving node."""
    bus = CanBus(0, 1, 100)
    host = HostScript([(100, 0, (0x7FF << 16) | 8), (150, 0, 0x01020304), (200, 0, 0x05060708),
                       (40000, 0, (0x555 << 16) | 8), (40050, 0, 0xFFFFFFFF), (40100, 0, 0)],
                      drain=[0], poll=1000)

    def check(board, h):
        f = bus.frames
        ok = (len(f) == 2 and f[0]["id"] == 0x7FF and f[0]["data"] == [1, 2, 3, 4, 5, 6, 7, 8]
              and f[1]["id"] == 0x555 and all(x["crc_ok"] for x in f) and h.received[0] == [1, 1])
        return ([] if ok else [f"can {f} {h.received}"]) + board.errors()
    return Board(bus, host), host, check


def case_ps2():
    """PS/2 device in firmware (chip-generated clock, odd parity), scancodes back to back."""
    ps2 = Ps2Host(4, 5)
    host = HostScript([(50, 1, 0x1C), (70, 1, 0xF0)], drain=[1], poll=1000)

    def check(board, h):
        ok = ps2.bytes == [0x1C, 0xF0] and h.received[1] == [1, 1]
        return ([] if ok else [f"ps2 {ps2.bytes} {h.received}"]) + board.errors()
    return Board(ps2, host), host, check


def case_jtag():
    """JTAG controller reading an IDCODE, two requests back to back."""
    tap = JtagTap(4, 5, 6, 7, idcode=0x4BA00477)
    host = HostScript([(20, 2, 0), (40, 2, 0)], drain=[2], poll=500)

    def check(board, h):
        ok = h.received[2] == [0x4BA00477] * 2 and tap.state == "RTI"
        return ([] if ok else [f"jtag {h.received} {tap.state}"]) + board.errors()
    return Board(tap, host), host, check


def case_two_banks():
    """v1.2: PS/2 and JTAG on bank A while SPI and UART run on bank B, at once."""
    ps2 = Ps2Host(0, 1)
    tap = JtagTap(4, 5, 6, 7, idcode=0x4BA00477)
    spi = SpiTarget(sck=0, mosi=1, miso=2, cs=3, mode=3, reply=bytes((0x3C, 0xC3)))
    uart = UartRx(4, UART_TPB)
    host = HostScript([(20, 2, 3), (100, 2, 0x1A5), (110, 2, 0x05A), (30, 0, 0x1C), (40, 1, 0)],
                      drain=[0, 1, 2], poll=300)
    board_b = SplitBoard(spi, uart, outputs=0b11011)

    def check(board, h):
        ok = (ps2.bytes == [0x1C] and h.received[0] == [1] and h.received[1] == [0x4BA00477]
              and spi.frames == [[0xA5, 0x5A]] and h.received[2] == [0x3C, 0xC3]
              and uart.bytes == [0x55, 0x4B, 0x21, 0x0A])
        return (([] if ok else [f"two banks {ps2.bytes} {h.received} {spi.frames} {uart.bytes}"])
                + board.errors() + board_b.errors())
    return Board(ps2, tap, host), host, check, board_b


def case_swd():
    """SWD host: connect (JTAG-to-SWD) and DPIDR read, a SELECT write that is WAITed
    twice, a posted AP read and RDBUFF (the AP's IDR)."""
    from escmodels import SwdTarget
    tgt = SwdTarget(4, 5, dpidr=0x2BA01477, waits=2, ap_regs={(0, 0xFC): 0x24770011})
    host = HostScript([(20, 2, 0x80000004), (40, 2, 0x10), (50, 2, 0xF0),
                       (8000, 2, 0x1E), (8010, 2, 0x1C)], drain=[2], poll=300)

    def check(board, h):
        ok = (h.received[2] == [1, 0x2BA01477, 0x201, 0, 1, 0, 1, 0x24770011]
              and tgt.switches == 1 and tgt.writes == [(0, 0x8, 0xF0)])
        return ([] if ok else [f"swd {[hex(v) for v in h.received[2]]}"]) + board.errors()
    return Board(tgt, host), host, check
def case_nec_ir():
    """NEC IR: the transmitter's 38 kHz carrier through a receiver-module model into the
    receiver program, a frame and a repeat code one slot later, at a 2.5 MHz timebase
    (nec_ir_cosim.asm; the models' tick is 400 ns)."""
    from escmodels import NecReceiver, Tsop
    rx = NecReceiver(0, tick_ns=400.0)
    host = HostScript([(50, 0, 0x5AA5), (100, 0, 1 << 31)], drain=[0, 1], poll=500)

    def check(board, h):
        ok = (rx.frames() == [(0xA5, 0x5A), "R"] and h.received[0] == [1, 1]
              and h.received[1] == [0x5A00A5, 1 << 31])
        return ([] if ok else [f"nec {rx.events} {h.received}"]) + board.errors()
    return Board(rx, Tsop(0, 1, tick_ns=400.0), host), host, check


def eeprom_tape(image):
    for a in range(256):
        image.tape[a] = (a * 7 + 3) & 0xFF


def spi_target_case(mode):
    """SPI target (25xx-style memory in the tape) at its rated limit, 416 kHz SCK, against
    a controller model that checks MISO setup/hold: WRITE, READ back across a frame
    boundary, a READ and a WRITE cut mid-byte, frames back to back (CS_n high 4 us)."""
    from escmodels.spi import SpiController
    mem = [(a * 7 + 3) & 0xFF for a in range(256)]

    def factory():
        frames = [([0x02, 0x10, 0xDE, 0xAD], None),
                  ([0x03, 0x0F, 0, 0, 0, 0], [None, None, mem[0x0F], 0xDE, 0xAD, mem[0x12]]),
                  ([0x03, 0x10, 0, 0], [None, None, 0xDE, 0xAD], 21),
                  ([0x02, 0x11, 0x55, 0x66], None, 27),
                  ([0x03, 0x11, 0], [None, None, 0x55])]
        ctl = SpiController(4, 5, 6, 7, frames, mode=mode, t_high=30, t_low=30, t_css=25,
                            t_idle=100)

        def check(board, h):
            return ([] if ctl.done else ["spi_target: controller script not finished"]) + board.errors()
        return Board(ctl), None, check
    return factory


# name -> (firmware, ticks, factory() -> (board, host, check(board, host) -> list[str])
#          [, board_b] for programs on pin bank B)
FIRMWARE_CASES = {
    "uart_rx": ("uart_rx.asm", 13500, case_uart_rx),
    "i2c_ctrl": ("i2c_ctrl.asm", 9000, case_i2c),
    "spi_mode3": ("spi_ctrl.asm", 2500, case_spi3),
    "ws2812": ("ws2812.asm", 9000, case_ws2812),
    "onewire": ("onewire.asm", 60000, case_onewire),
    "combo_uart_i2c": ("combo_uart_i2c.asm", 13000, case_combo),
    "i2c_eeprom": ("i2c_eeprom.asm", 110000, case_eeprom),
    "can_tx": ("can_tx.asm", 80000, case_can),
    "ps2_device": ("ps2_device.asm", 55000, case_ps2),
    "jtag_idcode": ("jtag_idcode.asm", 9000, case_jtag),
    "swd_idcode": ("swd_idcode.asm", 17000, case_swd),
    "nec_ir": ("../test/cosim/nec_ir_cosim.asm", 304000, case_nec_ir),
    "spi_target": ("spi_target.asm", 11000, spi_target_case(0)),
    "spi_target_mode3": ("spi_target.asm", 11000, spi_target_case(3)),
    "combo_two_banks": ("combo_two_banks.asm", 30000, case_two_banks),
}
TAPE_PREP = {"i2c_eeprom": eeprom_tape}
TAPE_PREP["spi_target"] = TAPE_PREP["spi_target_mode3"] = eeprom_tape


@cocotb.test()
async def test_firmware_with_bus_models(dut):
    """Every protocol program, on both implementations, against the same bus models:
    the per-tick traces, the decoded bus traffic and the host results must all agree.
    COSIM_FIRMWARE (comma-separated names) restricts the run, for run_parallel.py."""
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    only = [n for n in os.environ.get("COSIM_FIRMWARE", "").split(",") if n]
    assert all(n in FIRMWARE_CASES for n in only), f"unknown COSIM_FIRMWARE {only}"
    failures = []
    for name, (fw, ticks, factory) in FIRMWARE_CASES.items():
        if only and name not in only:
            continue
        image = assemble_file(REPO / "firmware" / fw)
        if name in TAPE_PREP:
            TAPE_PREP[name](image)
        board_i, host_i, check_i, *bb_i = factory()
        board_r, host_r, check_r, *bb_r = factory()
        if bb_i:                       # bank B: the RTL gets its own stateful twin
            bb_i, bb_r = bb_i[0], bb_r[0]
        errs, m = await cosim_one(dut, name, image, board_i, max_ticks=ticks, rtl_board=board_r,
                                  iss_host=host_i, rtl_host=host_r, stop_when_idle=False,
                                  board_b=bb_i or no_board_b,
                                  rtl_board_b=bb_r or None)
        errs += [f"ISS: {e}" for e in check_i(board_i, host_i)]
        errs += [f"RTL: {e}" for e in check_r(board_r, host_r)]
        dut._log.info(f"[{name}] {len(m.commits)} commits, {len(m.pins)} cycles: "
                      f"{'MATCH + protocol OK' if not errs else errs[:3]}")
        if errs:
            failures.append(name)
    assert not failures, f"failing firmware: {failures}"


# --------------------------------------------------------------------------- v1.1 directed
# Corner cases the random generator rarely reaches; each one exists because a mutant of
# the RTL survived the random campaign (test/mutation/run_mutants.py).

V11_LDT8 = "\n".join(["LDT r1, r2, 0"] * 8)
V11_DIRECTED = {
    # staging overflow: every port cycle busy (4 contexts of LDTs), inputs toggle each tick
    "cap_overflow": (f"""
.entry 0, a
.entry 1, b
.entry 2, b
.entry 3, b
a: MOVI r0, 0x8040
   MOVHI r0, 0x80FF
   CAPT r0
   {V11_LDT8}
   HALT
b: {V11_LDT8}
   {V11_LDT8}
   HALT
""", lambda t, o, oe: (t & 1) if t >= 0 else 0, 120),
    # CAPT disable at the very edge a watched pin changes: no record may be generated
    "capt_disable_on_change": ("""
.entry 0, s
s: MOVI r0, 0x2010
   MOVHI r0, 0x80FF
   CAPT r0
   MOVI r1, 0
   CAPT r1
   HALT
""", lambda t, o, oe: 1 if t >= 17 else 0, 60),
    # a faulting STT (ILLEGAL reserved bit; PC_RANGE at 255) must not write the tape
    "stt_faults_do_not_write": ("""
.tape 5, 0x11111111
.tape 7, 0x22222222
.entry 0, s
.entry 1, x
s: MOVI r0, 0x5A5A
   MOVI r1, 5
   .word 0x35100100
   HALT
.org 255
x: STT r2, r3, 7
""", lambda t, o, oe: 0, 60),
}


@cocotb.test()
async def test_v11_directed(dut):
    """v1.1 corner cases, ISS against RTL (tape contents included in the comparison)."""
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    failures = []
    for name, (src, board, ticks) in V11_DIRECTED.items():
        errs, m = await cosim_one(dut, name, assemble(src), board, max_ticks=ticks,
                                  stop_when_idle=False)
        dut._log.info(f"[{name}] {len(m.commits)} commits: {'MATCH' if not errs else 'MISMATCH'}")
        if errs:
            failures.append(name)
    assert not failures, f"mismatching: {failures}"


# --------------------------------------------------------------------------- v1.2 bank B
# Each rule of docs/11-SEMANTICS.md §5 for bank B, ISS against RTL.

def _ib_rise(bit, at):
    return lambda t, ob: (1 << bit) if t >= at else 0


V12_DIRECTED = {
    "drive_and_release": (""".bank 0
.own 0, 0
.own 3, 0
.entry 0, s
s: ADDT 10
   EMIT 0x09, 0x08, 0x09
   ADDT 10
   EMIT 0x09, 0x01, 0x09
   ADDT 10
   EMIT 0x01, 0x00, 0x00
   HALT
""", lambda t, ob: ob, 60),
    "views_per_context": (""".bank 0
.entry 0, a
.entry 1, b
a: GETP r0
   WAITP 0x04, 0x04
   GETP r1
   HALT
b: GETP r0
   HALT
""", lambda t, ob: 0b10101 if t >= 9 else 0b10001, 60),
    "fault_releases": (""".bank 0
.own 1, 0
.entry 0, s
s: ADDT 10
   EMIT 0x02, 0x00, 0x02
   ADDT 10
   WAITT
   EMIT 0x20, 0x20, 0x20
""", lambda t, ob: ob, 60),
    "expect_sample_capture": (""".bank 1
.entry 0, c
.entry 1, s
c: MOVI r1, 0x1000
   MOVHI r1, 0x821F
   CAPT r1
   HALT
s: ADDT 25
   EXPECT 0x04, 0x04, ARRIVE, 20
   ADDT 15
   SAMPLE 2, LSB
   GETI r1
   GETX r2
   HALT
""", _ib_rise(2, 30), 120),
    "two_banks_one_logical_pin": (""".bank 1
.own 0, 0
.own 0, 1
.entry 0, a
.entry 1, b
a: ADDT 20
   EMIT 0x01, 0x01, 0x01
   HALT
b: ADDT 20
   EMIT 0x01, 0x00, 0x01
   HALT
""", lambda t, ob: ob, 60),
}


@cocotb.test()
async def test_v12_bank_b(dut):
    """v1.2 pin bank B corner cases, ISS against RTL (pins, capture and digest included)."""
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    failures = []
    for name, (src, board_b, ticks) in V12_DIRECTED.items():
        errs, m = await cosim_one(dut, name, assemble(src), loopback_board, max_ticks=ticks,
                                  stop_when_idle=False, board_b=board_b)
        dut._log.info(f"[{name}] {len(m.commits)} commits: {'MATCH' if not errs else 'MISMATCH'}")
        if errs:
            failures.append(name)
    assert not failures, f"mismatching: {failures}"
