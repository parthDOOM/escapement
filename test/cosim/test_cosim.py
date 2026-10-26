"""ISS <-> RTL lock-step comparison (docs/04-VERIFICATION.md, docs/11-SEMANTICS.md §10).

For each program: assemble once, run the Python ISS and the RTL with the same board
model, then compare every commit record, the pin outputs of every run cycle, and the
full final state through the dbg_sel read map. Any difference fails the test with the
first diverging record and its disassembly.

Environment: COSIM_SEEDS (default 200), COSIM_SEED0 (default 1), COSIM_MAX_TICKS,
COSIM_FIRMWARE (restrict the bus-model test to the named firmware), COSIM_HOST_SEEDS
(default 60: random programs with host tape reads and acknowledgements during the run).
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

from escasm import AsmError, assemble, assemble_file, disassemble
from esciss import Machine
from esciss.machine import no_board_b

from escmodels import Board, HostScript
from fw_cases import FIRMWARE_CASES, TAPE_PREP
from progen import gen_lu_program, gen_program, loopback_board, lu_board, make_board, make_board_b

REPO = Path(__file__).resolve().parents[2]
MAX_TICKS = int(os.environ.get("COSIM_MAX_TICKS", "4000"))
POST_KEYS = ["r0", "r1", "r2", "r3", "sh", "lc", "tmr", "z", "c", "pc"]


async def reset_and_load(dut, image):
    dut.rst_n.value = 0
    for sig in ("start", "stop", "load_we", "load_space", "load_addr", "load_data", "uio_in",
                "mbx_rx_push", "mbx_rx_data", "mbx_tx_pop", "dbg_sel_ctx", "dbg_sel", "irq_clear",
                "ctx_enable", "entry_pcs", "pin_owned", "pin_owner", "pin_od", "ctx_bank",
                "ib_in", "hrd_req", "hrd_addr", "tape_ack", "tape_ack_v"):
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


class TapeHost:
    """v1.3 host side of the tape during a run (docs/12-HOST-PROTOCOL.md). Makes the same
    decisions on the ISS and on the RTL (one instance each):

    - `reads` (t, addr): RTL-only reads, each fetched in the first free cycle at or after
      t; every word is checked against the ISS's tape at the grant cycle (tape_at).
    - `acks` (t, rptr): TAPE_ACK during cycle t, i.e. at edge E_t, on both models.
    - `stream` (base, lim, poll, delay): a streaming host. Every `poll` cycles it reads
      the capture status (sel 17), reads the words from its rptr up to ptr and
      acknowledges them `delay` cycles later. `received` collects the words in order.
    """

    def __init__(self, reads=(), acks=(), stream=None):
        self.queue = [(t, a, False) for t, a in sorted(reads)]
        self.acks = dict(acks)
        self.stream = stream
        self.rptr = stream[0] if stream else 0
        self.received: list[int] = []
        self.reads: list[tuple[int, int, int]] = []      # RTL: (grant cycle, addr, word)
        self.late: list[int] = []                        # RTL: acks sent before reads done
        self._cur = None
        self._due = None

    def _poll(self, t, status):
        if not self.stream or t <= 0 or t % self.stream[2]:
            return []
        base, lim, _, delay = self.stream
        ptr, a, addrs = status & 0xFF, self.rptr, []
        while a != ptr and len(addrs) < 256:     # bounded: a broken ptr must not hang
            addrs.append(a)
            a = (a + 1) & 0xFF
            if a == lim:
                a = base
        if addrs:
            self.acks[t + delay] = ptr
            self.rptr = ptr
        return addrs

    def iss_cycle(self, m: Machine, t: int) -> None:
        for a in self._poll(t, m.dbg_read(0, 17)):
            self.received.append(m.tape[a])          # unread words cannot change
        if t in self.acks:
            m.host_tape_ack(self.acks.pop(t))

    async def rtl_cycle(self, dut, t: int) -> None:
        if self._due is not None:                    # granted last cycle: data now
            tg, a, mine = self._due
            w = int(dut.tape_rdata.value)
            self.reads.append((tg, a, w))
            if mine:
                self.received.append(w)
            self._due = None
        if self.stream:
            dut.dbg_sel_ctx.value = 0
            dut.dbg_sel.value = 17
            await Timer(1, unit="ps")
            self.queue += [(t, a, True) for a in self._poll(t, int(dut.dbg_rdata.value))]
        ack = self.acks.pop(t, None)
        if ack is not None and (any(q[2] for q in self.queue) or
                                (self._cur and self._cur[1]) or (self._due and self._due[2])):
            self.late.append(t)
        dut.tape_ack.value = int(ack is not None)
        dut.tape_ack_v.value = ack or 0
        if self._cur is None and self.queue and self.queue[0][0] <= t:
            _, a, mine = self.queue.pop(0)
            self._cur = (a, mine)
        dut.hrd_req.value = int(self._cur is not None)
        dut.hrd_addr.value = self._cur[0] if self._cur else 0
        await Timer(1, unit="ps")
        if self._cur is not None and int(dut.hrd_gnt.value):
            self._due = (t, *self._cur)
            self._cur = None

    def check_reads(self, m: Machine) -> list[str]:
        bad = [(tg, a, w, m.tape_at(tg, a)) for tg, a, w in self.reads if w != m.tape_at(tg, a)]
        errs = [f"host read at t={tg} addr {a}: RTL 0x{w:08x} ISS 0x{x:08x}" for tg, a, w, x in bad]
        if self.late:
            errs.append(f"TAPE_ACK before its reads finished at {self.late[:5]}")
        return errs


async def run_rtl(dut, image, board, n_cycles, host=None, board_b=no_board_b, tape_host=None):
    """Returns (commits, commits<n, pins, final) using the RTL. Cycle numbering per §2.
    `host` (an escmodels.HostScript, never attached to a machine) gives the mailbox
    schedule; pops are recorded into host.received like the ISS run does. `tape_host`
    (a TapeHost) reads the tape and acknowledges records during the run (v1.3)."""
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
        if tape_host is not None:
            if t < n_cycles:
                await tape_host.rtl_cycle(dut, t)
            else:
                dut.hrd_req.value = 0
                dut.tape_ack.value = 0
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
    # Selects 0-8 come from the contexts themselves: the debug port shows them as of each
    # context's last commit slot (D-41), which the core unit tests check.
    final = {}
    for ctx in range(4):
        c = dut.u_core.g_ctx[ctx].u_ctx
        regs = {k: int(getattr(c, k).value) for k in
                ("o_r0", "o_r1", "o_r2", "o_r3", "o_sh", "o_lc", "o_tmr", "o_lr", "o_pc",
                 "o_state", "o_z", "o_c", "o_ish")}
        for sel in range(4):
            final[(ctx, sel)] = regs[f"o_r{sel}"]
        final[(ctx, 4)], final[(ctx, 5)], final[(ctx, 6)] = regs["o_sh"], regs["o_lc"], regs["o_tmr"]
        final[(ctx, 7)] = ((regs["o_lr"] << 16) | (regs["o_pc"] << 8) | (regs["o_state"] << 2)
                           | (regs["o_z"] << 1) | regs["o_c"])
        final[(ctx, 8)] = regs["o_ish"]
        for sel in range(9, 14):
            dut.dbg_sel_ctx.value = ctx
            dut.dbg_sel.value = sel
            await Timer(1, unit="ps")
            final[(ctx, sel)] = int(dut.dbg_rdata.value)
    dut.dbg_sel_ctx.value = 0
    for sel in (14, 15, 16, 17, 18, 19):          # 16-19: digest + capture (v1.1)
        dut.dbg_sel.value = sel
        await Timer(1, unit="ps")
        final[(0, sel)] = int(dut.dbg_rdata.value)
    for ctx in range(4):                          # 20 rptr (v1.3); 21-31 read 0
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
                    rtl_board_b=None, iss_tape=None, rtl_tape=None, rtl_out=None):
    """Stateless boards may be shared; stateful ones need `rtl_board` (a fresh twin).
    `iss_tape` / `rtl_tape`: twin TapeHost instances (v1.3). `rtl_out` (a dict) receives
    the RTL's final read map, keyed (ctx, sel)."""
    def iss_board(t, o, oe):
        if t >= 0:
            iss_tape.iss_cycle(m, t)
        return board(t, o, oe)

    m = Machine(image, board=iss_board if iss_tape else board, board_b=board_b)
    if iss_host is not None:
        iss_host.machine = m
    m.run(max_ticks, stop_when_idle=stop_when_idle)
    n_cycles = len(m.pins)
    all_commits, rtl_commits, rtl_pins, rtl_final = await run_rtl(
        dut, image, rtl_board or board, n_cycles, rtl_host, rtl_board_b or board_b, rtl_tape)
    errs = compare(name, image, m, rtl_commits, rtl_pins, rtl_final, dut._log)
    if rtl_out is not None:
        rtl_out.update(rtl_final)
    if rtl_tape is not None:
        terrs = rtl_tape.check_reads(m)
        if iss_tape.received != rtl_tape.received:
            terrs.append(f"streamed words differ: ISS {len(iss_tape.received)} "
                         f"RTL {len(rtl_tape.received)} words")
        for e in terrs[:6]:
            dut._log.error(f"[{name}] {e}")
        errs += terrs
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


@cocotb.test()
async def test_firmware_with_bus_models(dut):
    """Every protocol program, on both implementations, against the same bus models:
    the per-tick traces, the decoded bus traffic and the host results must all agree.
    COSIM_FIRMWARE (comma-separated names) restricts the run, for run_parallel.py.
    COSIM_PROOF_OUT (a file) appends one JSON line per firmware with the RTL's telemetry
    and digest, for proof_table.py --rtl."""
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    only = [n for n in os.environ.get("COSIM_FIRMWARE", "").split(",") if n]
    assert all(n in FIRMWARE_CASES for n in only), f"unknown COSIM_FIRMWARE {only}"
    proof_out = os.environ.get("COSIM_PROOF_OUT")
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
        rtl = {}
        errs, m = await cosim_one(dut, name, image, board_i, max_ticks=ticks, rtl_board=board_r,
                                  iss_host=host_i, rtl_host=host_r, stop_when_idle=False,
                                  board_b=bb_i or no_board_b,
                                  rtl_board_b=bb_r or None, rtl_out=rtl)
        errs += [f"ISS: {e}" for e in check_i(board_i, host_i)]
        errs += [f"RTL: {e}" for e in check_r(board_r, host_r)]
        if proof_out:
            rec = {"name": name, "ok": not errs, "digest": rtl[(0, 16)],
                   "tele": [rtl[(k, 9)] for k in range(4)]}
            with open(proof_out, "a") as f:
                f.write(json.dumps(rec) + "\n")
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


# --------------------------------------------------------------------------- v1.3


def host_schedule(seed: int, n: int = MAX_TICKS):
    """Random host tape reads (every 1-12 cycles) and acknowledgements with random rptr."""
    rng = random.Random(seed * 31337 + 13)
    reads, acks, t = [], [], 0
    while t < n:
        t += rng.randrange(1, 13)
        reads.append((t, rng.randrange(256)))
    for _ in range(rng.randrange(0, 12)):
        acks.append((rng.randrange(n), rng.randrange(256)))
    return reads, acks


@cocotb.test()
async def test_v13_host_reads_during_runs(dut):
    """Random programs, including LDT/STT storms, stream-mode captures and garbage CAPT
    configurations, while a host reads the tape every few cycles and acknowledges at
    random. Every commit, pin and final state must match the ISS, which knows nothing of
    the reads (a host read never disturbs the core), and every word read must be the
    ISS's tape at the cycle it was fetched."""
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    seeds = int(os.environ.get("COSIM_HOST_SEEDS", "60"))
    seed0 = int(os.environ.get("COSIM_SEED0", "1"))
    failures, reads, waited = [], 0, 0
    for seed in range(seed0, seed0 + seeds):
        try:
            image = assemble(gen_program(seed))
        except AsmError:
            continue
        r, a = host_schedule(seed)
        iss_tape, rtl_tape = TapeHost(acks=a), TapeHost(reads=r, acks=a)
        errs, m = await cosim_one(dut, f"host{seed}", image, make_board(seed),
                                  board_b=make_board_b(seed), iss_tape=iss_tape,
                                  rtl_tape=rtl_tape)
        reads += len(rtl_tape.reads)
        req = dict(r)
        waited += sum(1 for tg, addr, _ in rtl_tape.reads if tg not in req)
        if errs:
            failures.append(seed)
            if len(failures) >= 5:
                break
    dut._log.info(f"host reads: {seeds} programs, {reads} words read during runs, "
                  f"{waited} granted after their request cycle, {len(failures)} mismatching {failures}")
    assert not failures, f"mismatching seeds: {failures}"


def streaming_case(poll: int, toggle: int = 37):
    """logic_analyzer in stream mode over a 16-word region while two contexts hammer the
    tape port with LDT/STT outside it; pin 0 toggles every `toggle` cycles."""
    src = f"""
.equ logic_analyzer.STREAM, 1
.equ logic_analyzer.MASK, 0x01
.equ logic_analyzer.BASE, 0x80
.equ logic_analyzer.LIM, 0x90
.include "logic_analyzer.asm"
.entry 1, storm
.entry 2, storm
storm:
    MOVI  r2, 0x10
    LDLC  250
spin:
    STT   r1, r2, 0
    LDT   r3, r2, 1
    STT   r3, r2, 2
    DJNZ  spin
    HALT
"""
    image = assemble(src, base_dir=REPO / "firmware")
    board = lambda t, o, oe: ((t // toggle) & 1) if t >= 0 else 0
    return image, board, (0x80, 0x90, poll, 64)


@cocotb.test()
async def test_v13_streaming_capture(dut):
    """A streaming host keeps up (poll 200 cycles): every record arrives, in order, with no
    overflow, far beyond the 16-word region. A slow host (poll 1500) ends in ovf at the
    same tick on both models, with the records before it intact."""
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    failures = []
    for poll in (200, 1500):
        image, board, stream = streaming_case(poll)
        iss_tape, rtl_tape = TapeHost(stream=stream), TapeHost(stream=stream)
        errs, m = await cosim_one(dut, f"stream{poll}", image, board, max_ticks=6000,
                                  stop_when_idle=False, iss_tape=iss_tape, rtl_tape=rtl_tape)
        got = iss_tape.received
        ticks = [w & 0xFFFFFF for w in got]
        levels = [w >> 24 for w in got]
        dut._log.info(f"[stream{poll}] {len(got)} records streamed, ovf {int(m.cap.ovf)} "
                      f"at {m.cap.ovf_tick}: {'MATCH' if not errs else 'MISMATCH'}")
        if poll == 200:
            # snapshot, then one record per toggle (sync lags uio_in by 2 cycles)
            if m.cap.ovf or len(got) < 6000 // 37 - 10:
                errs.append(f"fast host lost records: {len(got)}, ovf {m.cap.ovf}")
            if any(b - a != 37 for a, b in zip(ticks[1:], ticks[2:])):
                errs.append("fast host: a record is missing between two toggles")
            if any(x == y for x, y in zip(levels[1:], levels[2:])):
                errs.append("fast host: levels do not alternate")
        elif not m.cap.ovf:
            errs.append("slow host: expected an overflow")
        if errs:
            failures.append(poll)
    assert not failures, f"streaming cases failing: {failures}"


# --------------------------------------------------------------------------- v1.4 line unit
# --------------------------------------------------------------------------- v1.4 directed
# Corner cases the random programs rarely reach, ISS against RTL. LU_W: one valid line
# unit configuration (transmit on pin 0, 8 ticks a bit, sample at 4, an 8-bit header).
LU_W = ".tape 0xF4, 0x00100800, 0x00400008, 0x00040000"
LU_CONFIG = """   MOVI r3, 0xF4
   MOVHI r3, 0x3000
   LINE r3"""

V14_DIRECTED = {
    # SEND into a full FIFO waits (and counts in wait_full) until an entry retires
    "send_fifo_full": (f"""{LU_W}
.own 0, 0
.own 1, 0
.entry 0, s
s:
{LU_CONFIG}
   ADDT 200
   EMIT 0x02, 0x02, 0x02
   ADDT 4
   EMIT 0x02, 0x00, 0x02
   ADDT 4
   EMIT 0x02, 0x02, 0x02
   ADDT 4
   EMIT 0x02, 0x00, 0x02
   ADDT 4
   MOVI r1, 0
   MOVHI r1, 0x00A5
   SEND r1
   GETL r2
   HALT
""", None, 400),
    # SEND at a deadline before the FIFO tail faults ADMIT/ORDER
    "send_order": (f"""{LU_W}
.own 0, 0
.own 1, 0
.entry 0, s
s:
{LU_CONFIG}
   ADDT 60
   EMIT 0x02, 0x02, 0x02
   MOVI r2, 50
   SETT r2
   MOVI r1, 0
   MOVHI r1, 0x00A5
   SEND r1
   HALT
""", None, 200),
    # mailbox rings: a POP at the edge the host pushes a second word, and a PUSH at the
    # edge the host pops the first (context 1 commits at 4, 8, 12, ...; the host polls
    # every 4 ticks)
    "mailbox_same_edge": ("""
.entry 1, m
m: NOP
   NOP
   POP r0
   POP r1
   PUSH r0
   PUSH r1
   HALT
""", lambda: HostScript([(2, 1, 0x11111111), (12, 1, 0x22222222)], drain=[1], poll=4), 80),
}


async def v14_directed(dut) -> list[str]:
    failures = []
    for name, (src, host, ticks) in V14_DIRECTED.items():
        board, kw = (lambda t, o, oe: 0), {}
        if host:                      # the ISS steps its host as a device on the board
            iss_host = host()
            board, kw = Board(iss_host), {"iss_host": iss_host, "rtl_host": host(),
                                          "rtl_board": lambda t, o, oe: 0}
        errs, m = await cosim_one(dut, name, assemble(src), board,
                                  max_ticks=ticks, stop_when_idle=False, **kw)
        dut._log.info(f"[{name}] {len(m.commits)} commits: {'MATCH' if not errs else 'MISMATCH'}")
        if errs:
            failures.append(name)
    return failures


@cocotb.test()
async def test_v14_line_unit(dut):
    """Firmware-shaped line unit programs (progen.gen_lu_program): configuration, receives
    of the unit's own packets and of noise, packets at fresh or end-tick deadlines, CRC
    jobs, GETL waits and timeouts, OFF, and owner LDT/STT that meet the unit's tape
    cycles, beside another context's traffic. COSIM_LU_SEEDS programs (default 40)."""
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    directed = await v14_directed(dut)
    assert not directed, f"mismatching v1.4 directed programs: {directed}"
    seeds = int(os.environ.get("COSIM_LU_SEEDS", "40"))
    seed0 = int(os.environ.get("COSIM_SEED0", "1"))
    out_dir = Path(os.environ.get("COSIM_OUT", "cosim_failures"))
    failures = []
    for seed in range(seed0, seed0 + seeds):
        src = gen_lu_program(seed)
        errs, m = await cosim_one(dut, f"lu{seed}", assemble(src), lu_board(seed),
                                  max_ticks=6000, stop_when_idle=False)
        if errs:
            failures.append(seed)
            out_dir.mkdir(exist_ok=True)
            (out_dir / f"lu{seed}.asm").write_text(src)
            (out_dir / f"lu{seed}.txt").write_text("\n".join(errs))
            if len(failures) >= 3:
                break
    dut._log.info(f"line unit: {seeds} programs, {len(failures)} mismatching {failures}")
    assert not failures, f"mismatching line unit seeds: {failures} (see {out_dir}/)"
