#!/usr/bin/env python3
"""Timing bugs planted in the shipped firmware: does escert catch them?

    python test/mutation/fw_mutants.py [--per N] [--trials T] [--jobs J] [--shard K/N]
                                       [--json FILE] [FIRMWARE ...]
    python test/mutation/fw_mutants.py --merge FILE ...      (the table of shards' results)

The RTL campaign (run_mutants.py) plants faults in the chip; this one plants the timing
bugs firmware authors make, one per mutant, in the source of each shipped program:

    waitt   a WAITT deleted: the program no longer waits for its queued events
    addt    an ADDT deleted: two events share a deadline, or one lands in the past
    short   an ADDT's delay cut to a quarter (at least 1)
    branch  a conditional branch inverted (JZ/JNZ, JC/JNC): the wrong path is taken

Each mutant is assembled and certified, alone or, for programs that run as one paged
image (OLED Pong's four), inside that image. escert answers CHECKED (no admission fault for
any input), REFUTED (a fault, reproduced on the ISS with concrete inputs) or NOT_PROVEN
(a fault could not be excluded and the search found none). A CHECKED mutant is then
attacked with the same randomised input search on the ISS: a fault there would mean
escert certified a program that can fault (UNSOUND), and fails the run. So does a crash in
the checker or the ISS on a mutant (ERROR), reported beside the other results.

Random pins and host words rarely drive a protocol far: a mutant in the reply to a
command is reached only by that command. So both searches then also run the program's
own co-simulation benches (test/cosim/fw_cases.py: its bus models and host script) on the
ISS with the mutant in place, those whose image is the very one certified (a bench that
sets .equ parameters or prepares the tape runs a different program, and is left out),
and a few benches aimed at the paths those leave alone (AIMED: a target that never
answers and a command right after that, a byte NACKed for its parity, a frame for another
device, a query answered, a keyer kept busy with its longest spaces, a line held low by
another device, a receiver that glitches while the reply goes out).
A NOT_PROVEN mutant gets more searches. A host that never pauses, over a run five times
as long (a deleted WAITT may fault only once frames follow each other back to back), can
still refute it, and so can the aimed benches too long to run on every mutant (LONG_AIMED:
a host word that comes two seconds late). And the benches that set parameters (most set
the clock) build it their way and run it: a fault there is reported beside NOT_PROVEN,
never as REFUTED, because a different clock makes a different program; if escert
certifies that build, the run fails.

escert proves timing, not that the protocol still works: for a certified mutant the
bench's own check (its bus models' verdict) is recorded too, and the report counts the
certified mutants whose protocol the models see fail. Every unmutated program passes
its benches, so that count is the mutants' doing.

A mutant may also be harmless to timing (a shorter delay that still leaves the next
deadline in the future, a branch whose two paths have the same timing): escert then
certifies it, correctly. Mutants are picked by a hash of the firmware, operator and line
number, and the input search is seeded, so a run on the same sources gives the same table;
editing a firmware changes its set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sw"))

from escasm import AsmError, assemble, paged  # noqa: E402
from escert import ISA, certify, witness  # noqa: E402
from esciss import Machine  # noqa: E402
from esciss.machine import no_board_b  # noqa: E402

sys.path.insert(0, str(REPO / "test" / "cosim"))
from escmodels import Board, CecDevice, DaliController, HostScript  # noqa: E402
from escmodels.board import Device  # noqa: E402
from escmodels.swim import SwimTarget  # noqa: E402
from fw_cases import FIRMWARE_CASES, FW_EQU, SWIM_MEM, SWIM_UID, TAPE_PREP, fw_image  # noqa: E402

FW = REPO / "firmware"
SKIP = {"replay.asm",                       # a template
        "combo_two_banks.asm", "combo_uart_i2c.asm"}   # includes of the programs below
# programs that run together as one paged image, one program page each: a mutant of one of
# them is certified and attacked inside that image, beside the unmutated others
PAGED = [{0: "oled_disp.asm", 1: "oled_pong.asm", 2: "oled_text.asm", 3: "oled_menu.asm"}]


def build(fw: str, src: str):
    """The image a mutant of `fw` runs in: alone, or in its paged image."""
    for pages in PAGED:
        if fw in pages.values():
            return paged({p: assemble(src if f == fw else (FW / f).read_text(), base_dir=FW, name=f)
                          for p, f in pages.items()})
    return assemble(src, base_dir=FW, name=fw)
SALT = "escapement-fw-mutation-1"
BRANCH = {"JZ": "JNZ", "JNZ": "JZ", "JC": "JNC", "JNC": "JC"}


def mutants(src: str):
    """(op, line number, new source) for every mutation site of a firmware source."""
    lines = src.split("\n")
    for i, line in enumerate(lines):
        code = line.split(";", 1)[0]
        m = re.match(r"^(\s*(?:\w+:\s*)?)([A-Za-z]\w*)(\s*)(.*?)\s*$", code)
        if not m:
            continue
        lead, op, sp, arg = m.groups()
        op_u = op.upper()
        new = None
        if op_u == "WAITT" and not arg:
            yield "waitt", i + 1, lead + "; WAITT (deleted)"
        elif op_u == "ADDT":
            yield "addt", i + 1, lead + "; ADDT (deleted)"
            new = f"{lead}{op}{sp}((({arg}) + 3) // 4)"
            yield "short", i + 1, new
        elif op_u in BRANCH:
            yield "branch", i + 1, f"{lead}{BRANCH[op_u]}{sp}{arg}"


def chosen(key: str, per: int, n_sites: int) -> bool:
    """Up to about `per` sites of each operator per firmware, by hash."""
    h = int(hashlib.sha1((SALT + key).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return h < per / max(n_sites, per)


MS25 = 25_000                                 # ticks per ms at the default 25 MHz


class _SwimBadParity(SwimTarget):
    """A SWIM target whose first data packet has its parity bit inverted."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.corrupted = 0

    def _packet(self, header, value, n):
        p = super()._packet(header, value, n)
        if header == 1 and not self.corrupted:
            self.corrupted = 1
            p[-1] ^= 1
        return p


def _aimed_avr_no_target():
    """Programming Enable with no AVR on the bus: nothing echoes 0x53, so the programmer
    pulses RESET and tries again, eight times in all, then reports 1 (no echo). A raw
    instruction is already waiting, so the next command starts as soon as that report is
    pushed, while the last RESET pulse is still queued; MISO reads all ones (pulled up)."""
    host = _FeedHost(0, [1 << 28, 2 << 28, 0xAC530000], drain=[0], poll=2500)
    return Board(host), host, lambda b, h: ([] if h.received[0] == [1, 0xFFFFFFFF]
                                            else [f"avr_isp {h.received}"])


def _aimed_swim_nack():
    """The co-simulation bench's 4-byte ROTF, with the first byte's parity wrong: the host
    NACKs it, the target sends it again, and the host still reads the four bytes."""
    tgt = _SwimBadParity(0, tick_ns=20, mem=SWIM_MEM)
    host = HostScript([(100, 0, 3 << 24 | SWIM_UID)], drain=[0], poll=300)

    def check(board, h):
        ok = h.received[0] == [4, 0x00, 0x5A, 0x00, 0x3C] and not tgt.violations
        return ([] if ok else [f"swim {h.received} {tgt.violations[:2]}"]) + board.errors()
    return Board(tgt, host, pullups=0x01), host, check


class _LowAfterEdges(Device):
    """Another device on an open-drain line: from the line's `nth` falling edge it holds the
    line low for `hold` ticks, once (a target that answers too early, or a glitch)."""
    def __init__(self, mask, nth, hold):
        self.mask, self.nth, self.hold = mask, nth, hold
        self.prev, self.n, self.until = mask, 0, -1

    def step(self, t, bus):
        lvl = bus & self.mask
        if self.prev and not lvl:
            self.n += 1
            if self.n == self.nth:
                self.until = t + self.hold
        self.prev = lvl
        return (self.mask if t < self.until else 0), 0


def _aimed_swim_glitch():
    """The co-simulation bench's read, with SWIM held low for 80 ticks (1.6 us) from its 13th
    falling edge, the third bit of the host's first command: the host may take that low for
    the target's acknowledge. It reports the read as failed and tries again."""
    tgt = SwimTarget(0, tick_ns=20, mem=SWIM_MEM)
    host = HostScript([(100, 0, 3 << 24 | SWIM_UID)], drain=[0], poll=300)
    return (Board(_LowAfterEdges(1, 13, 80), tgt, host, pullups=0x01), host,
            lambda b, h: [] if h.received[0][:1] in ([0xFFFFFFFF], [4]) else [f"swim {h.received}"])


class _EchoNoise(Device):
    """A transceiver whose receiver output RO glitches low for 4 ticks in every 64 while
    the driver is enabled (DE high): a noisy bus, or a client talking over the reply."""
    def __init__(self, ro, de):
        self.ro, self.de = 1 << ro, 1 << de

    def step(self, t, bus):
        return (self.ro if bus & self.de and t % 64 < 4 else 0), 0


def _aimed_modbus_noise():
    """Read Holding Registers at the default 25 MHz build, with RO noisy while the server
    drives the bus: the server receives nothing while it replies, and the reply still
    comes back whole."""
    from escmodels.modbus import ModbusClient, adu
    cl = ModbusClient(ro=1, di=0, de=2, ticks_per_bit=MS25 * 1000 / 19200,
                      requests=[(adu(1, bytes([3, 0, 0, 0, 2])), True)], start=60_000, turn=5000)
    host = HostScript([], drain=[0], poll=500)

    def check(board, h):
        out = [bytes(r["bytes"]) for r in cl.replies]
        if out and out[0][:3] == bytes([1, 3, 4]):
            return []
        return [f"modbus {[o.hex() for o in out]}"]
    return Board(_EchoNoise(1, 2), cl, host), host, check


def _aimed_dcf77_late_word():
    """A frame at once, with bit 1 set so that second 1 is a 1 (second 0 is always a 0), then
    the next frame's first word 2.1 s later, after second 2 has begun: the transmitter takes
    a word only when RXREADY saw one, so a word that comes late never holds up a second,
    after a 0's mark or a 1's. 54 million ticks at the default 25 MHz: run only for a
    NOT_PROVEN mutant."""
    host = HostScript([(100, 0, 1 << 1), (101, 0, 1 << 20), (52_500_000, 0, 0)], drain=[0],
                      poll=5000)
    return Board(host), host, lambda b, h: []


def _aimed_cec_other():
    """A TV sends a frame to logical address 5, which nobody holds: the chip (4) leaves its
    header unacknowledged and reports the block."""
    tv = CecDevice(0, 0, tick_ns=40.0, send=[(2 * MS25, [0x05, 0x8F])], retries=0)
    host = HostScript([], drain=[0], poll=5000)

    def check(board, h):
        ok = [r for r, _ in tv.results] == ["nack"] and h.received[0] == [0x400 | 0x05 << 2 | 1]
        return ([] if ok else [f"cec {tv.results} {h.received}"]) + board.errors()
    return Board(tv, host), host, check


def _aimed_dali_query():
    """QUERY CONTROL GEAR PRESENT at the default clock: the gear answers with a backward
    frame."""
    ctl = DaliController(0, [(2 * MS25, 0x0191)], tick_ns=40.0)
    host = HostScript([], drain=[0], poll=5000)

    def check(board, h):
        ok = [a and a[0] for a in ctl.answers] == [0xFF] and h.received[0] == [0x30191]
        return ([] if ok else [f"dali {ctl.answers} {h.received}"]) + board.errors()
    return Board(ctl, host), host, check


def _aimed_westminster():
    """The tune, every note of it, with nothing to answer: its notes are judged in
    sw/tests."""
    return Board(), None, lambda b, h: [], lambda t, ob: 0


class _FeedHost(HostScript):
    """A host that keeps one context's RX mailbox full: each word goes in as soon as there
    is room, so the program never finds the mailbox empty and never pauses. With `drain`,
    it also reads those contexts' TX mailboxes every `poll` ticks, as HostScript does."""
    def __init__(self, ctx, words, drain=(), poll=1400):
        super().__init__([], drain=drain, poll=poll)
        self.ctx, self.words = ctx, list(words)

    def step(self, t, bus):
        if self.machine is not None and self.words and len(self.machine.ctx[self.ctx].rx) < 2:
            self.machine.host_rx_push(self.ctx, self.words.pop(0))
        return super().step(t, bus)


def _aimed_morse_spaces():
    """The longest dot (1,023 ms), then 24 word spaces with no pause between host words, then
    letters: a word space keys nothing, so only the program's waits keep its deadlines from
    running ahead of the clock. Attack only: the unmutated keyer takes minutes to send it."""
    host = _FeedHost(0, [1 << 28 | 1023] + [2 << 28 | 0x202020] * 8 + [2 << 28 | 0x454545] * 2)
    return Board(host), host, lambda b, h: []


# Benches aimed at paths the co-simulation benches leave alone, for this campaign only: an
# error, an answer or an input held at its extreme, each found by a mutant that escert flags
# and no search reached. name: (firmware, ticks, factory, parameters). Without parameters a bench runs
# the default build, as the co-simulation benches above do; with them (a slower clock: the
# 14-second tune is 350 million ticks at 25 MHz) it is another program, searched as
# config_benches searches the parameter benches.
AIMED = {
    "avr_isp_no_target": ("avr_isp.asm", 4_300_000, _aimed_avr_no_target, None),
    "swim_host_nack": ("swim_host.asm", 400_000, _aimed_swim_nack, None),
    "cec_other_address": ("cec.asm", 40 * MS25, _aimed_cec_other, None),
    "dali_query": ("dali.asm", 40 * MS25, _aimed_dali_query, None),
    "westminster_250k": ("westminster.asm", 1_000_000, _aimed_westminster, {"CLK_KHZ": 250}),
    "morse_long_spaces": ("morse.asm", 40_000, _aimed_morse_spaces, None),
    "swim_host_glitch": ("swim_host.asm", 400_000, _aimed_swim_glitch, None),
    "modbus_rtu_noise": ("modbus_rtu.asm", 500_000, _aimed_modbus_noise, None),
}
# Aimed benches too long to attack every mutant with (a second of the default clock is 25
# million ticks, minutes on the ISS): run for a NOT_PROVEN mutant only, after the searches
# above found nothing. name: (firmware, ticks, factory).
LONG_AIMED = {
    "dcf77_late_word": ("dcf77_tx.asm", 54_000_000, _aimed_dcf77_late_word),
}


def benches(fw: str) -> list:
    """The benches that run `fw`'s own image, from the co-simulation and aimed: (name,
    ticks, factory)."""
    return ([(n, t, fac) for n, (f, t, fac) in FIRMWARE_CASES.items()
             if f == fw and n not in FW_EQU and n not in TAPE_PREP]
            + [(n, t, fac) for n, (f, t, fac, equ) in AIMED.items() if f == fw and not equ])


def _from_tools(e: BaseException) -> bool:
    """Whether an exception was raised inside the ISS, the checker or the assembler (a crash:
    ERROR), not inside a bus model (the protocol broke: the bench ends there)."""
    tb, last = e.__traceback__, None
    while tb is not None:
        last, tb = tb, tb.tb_next
    where = last.tb_frame.f_code.co_filename.replace("\\", "/") if last else ""
    return any(f"/{d}/" in where for d in ("esciss", "escert", "escasm"))


def bench_witness_one(image, name: str, ticks: int, factory):
    """Run one bench on the ISS with `image`: (reproducer, None) for an ADMIT fault in any
    context (the caller decides what it means), else (None, problems), the bench's own check
    (its bus models' verdict)."""
    board, host, check, *bb = factory()
    m = Machine(image, board=board, board_b=bb[0] if bb else no_board_b,
                half=getattr(board, "half", None), record=False)
    if host is not None:
        host.machine = m
    # A mutant that breaks the protocol makes a bus model give up (escmodels.usb raises
    # UsbError when a SETUP goes unacknowledged; a host script left unread raises
    # RuntimeError): the bench ends there, and only the fault register is judged. A crash
    # in the ISS itself is not the mutant's doing: it propagates, and run_one reports ERROR.
    problems = []
    try:
        m.run(ticks, stop_when_idle=False)
    except Exception as e:                    # noqa: BLE001
        if _from_tools(e):
            raise
        problems.append(f"{type(e).__name__}: {e}"[:120])
    ff = m.ff
    if ff.valid and ff.cause == ISA.fault_causes["ADMIT"]:
        return {"bench": name, "ctx": ff.ctx, "tick": ff.tick, "pc": ff.pc,
                "subcode": ff.subcode, "strategy": "bench"}, None
    if ff.valid:                              # another fault: the program stopped
        problems.append(f"fault cause {ff.cause} in ctx {ff.ctx} at pc {ff.pc}")
    if not problems:
        try:
            problems = [str(x)[:120] for x in check(board, host)]
        except Exception as e:                # noqa: BLE001
            if _from_tools(e):
                raise
            problems = [f"check: {type(e).__name__}: {e}"[:120]]
    return None, problems


def bench_witness(image, fw: str):
    """Run each bench of `fw` on the ISS with `image` (the mutant). Returns (an ADMIT fault
    in any context, or None; protocol): protocol is None without a bench, else
    [[bench, problems]] from each bench's own check (escert proves timing, not that the
    protocol still works)."""
    protocol = []
    for name, ticks, factory in benches(fw):
        w, problems = bench_witness_one(image, name, ticks, factory)
        if w:
            return w, None
        protocol.append([name, problems])
    return None, (protocol or None)


# the second search, for NOT_PROVEN only: a host that never pauses, over a run five times
# as long, on seeds the first search did not use
LONG_TRIALS, LONG_TICKS, LONG_SEED = 3, 100_000, 1000


def long_witness(image, ctx: int):
    w = witness(image, ctx, LONG_TRIALS, ticks=LONG_TICKS, first_seed=LONG_SEED, burst_only=True)
    if w:
        w["ctx"], w["strategy"] = ctx, "burst, long"
    return w


def config_benches(fw: str, src: str):
    """A NOT_PROVEN mutant built as each bench that sets parameters builds it (its clock,
    its tape) and run on that bench, then searched at that build with the long search: a
    fault there is reported beside NOT_PROVEN (it is another program, so the default build
    stays unrefuted). If escert certifies that build and it faults, escert is wrong:
    ("UNSOUND", detail). Else ("at", bench) or None. (As fw_cases.fw_image builds a case,
    with the mutant's source; the paged OLED images are left out: their mutants are
    certified and attacked inside the paged image already.)"""
    builds = [(name, ticks, factory, lambda name=name: fw_image(name, fw, FW, src=src))
              for name, (f, ticks, factory) in FIRMWARE_CASES.items()
              if f == fw and (name in FW_EQU or name in TAPE_PREP)]
    builds += [(name, ticks, factory, lambda equ=equ: assemble(
                   "".join(f".equ {k}, {v}\n" for k, v in equ.items()) + src, base_dir=FW, name=fw))
               for name, (f, ticks, factory, equ) in AIMED.items() if f == fw and equ]
    for name, ticks, factory, make in builds:
        try:
            image = make()
        except AsmError:
            continue
        certs = {c.ctx: c.status for c in certify(image)}
        w, _ = bench_witness_one(image, name, ticks, factory)
        for k in [k for k, st in certs.items() if st != "CHECKED"] if not w else []:
            w = long_witness(image, k)        # and the host that never pauses, at that build
            if w:
                break
        if w:
            if certs.get(w["ctx"]) == "CHECKED":
                how = "its bench" if w["strategy"] == "bench" else f"the {w['strategy']} search"
                return "UNSOUND", f"certified at {name}'s parameters, yet {how} faults it: {json.dumps(w)}"
            return "at", name
    return None


def run_one(job):
    """One mutant's result; a crash in the checker or the ISS is ERROR (it fails the run),
    so one mutant cannot take its shard's other results with it."""
    try:
        return _run_one(job)
    except Exception as e:                    # noqa: BLE001
        fw, op, line, _, _ = job
        return {"fw": fw, "op": op, "line": line, "result": "ERROR",
                "detail": f"{type(e).__name__}: {e}"[:160]}


def _run_one(job):
    fw, op, line, new_line, trials = job
    src = (FW / fw).read_text().split("\n")
    src[line - 1] = new_line
    try:
        image = build(fw, "\n".join(src))
    except AsmError as e:
        return {"fw": fw, "op": op, "line": line, "result": "INVALID", "detail": str(e)[:120]}
    certs = certify(image, witness_trials=trials)
    bad = [c for c in certs if c.status != "CHECKED"]
    if bad:
        c = next((b for b in bad if b.status == "REFUTED"), bad[0])   # a refuted context wins
        status, detail = c.status, str(c).split("\n")[0][:160]
        r = {"fw": fw, "op": op, "line": line, "ctx": c.ctx}
        if status == "NOT_PROVEN":            # the program's own benches may reach it
            w, _ = bench_witness(image, fw)
            if w and w["ctx"] not in {b.ctx for b in bad}:      # a certified context faulted
                return {**r, "result": "UNSOUND", "ctx": w["ctx"], "detail": json.dumps(w)}
            for b in bad if not w else []:    # a host that never pauses, five times as long
                w = long_witness(image, b.ctx)
                if w:
                    break
            for name, (f, ticks, factory) in LONG_AIMED.items() if not w else []:
                if f == fw:                   # the long aimed benches, last
                    w, _ = bench_witness_one(image, name, ticks, factory)
                    if w and w["ctx"] not in {b.ctx for b in bad}:
                        return {**r, "result": "UNSOUND", "ctx": w["ctx"], "detail": json.dumps(w)}
                    if w:
                        break
            if w:
                r["ctx"] = w["ctx"]
                status, detail = "REFUTED", detail + " " + json.dumps(w)
            else:
                at = config_benches(fw, "\n".join(src))
                if at and at[0] == "UNSOUND":
                    return {**r, "result": "UNSOUND", "detail": at[1]}
                if at:
                    r["faults_at"] = at[1]
        return {**r, "result": status, "detail": detail}
    for c in certs:                       # certified: attack it on the ISS
        w = witness(image, c.ctx, trials)
        if w:
            return {"fw": fw, "op": op, "line": line, "result": "UNSOUND", "ctx": c.ctx,
                    "detail": json.dumps(w)}
    w, protocol = bench_witness(image, fw)
    if w:
        return {"fw": fw, "op": op, "line": line, "result": "UNSOUND", "ctx": w["ctx"],
                "detail": json.dumps(w)}
    r = {"fw": fw, "op": op, "line": line, "result": "CHECKED"}
    if protocol is not None:                  # timing certified; does the protocol still work?
        r["protocol"] = protocol
    return r


def main() -> int:
    ap = argparse.ArgumentParser(prog="fw_mutants")
    ap.add_argument("firmware", nargs="*", help="firmware file names (default: all shipped)")
    ap.add_argument("--per", type=int, default=3, help="sites per operator per firmware")
    ap.add_argument("--trials", type=int, default=12, help="ISS input searches per mutant")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--json", help="write every result to this file")
    ap.add_argument("--shard", help="K/N: run only every Nth mutant from the Kth (CI)")
    ap.add_argument("--merge", nargs="+", metavar="FILE",
                    help="run nothing: print the table of these --json files (the shards)")
    args = ap.parse_args()
    names = args.firmware or sorted(p.name for p in FW.glob("*.asm") if p.name not in SKIP)
    if args.merge:
        results = [r for f in args.merge for r in json.loads(Path(f).read_text())]
        if args.json:
            Path(args.json).write_text(json.dumps(results, indent=1))
        return report(names, results)
    jobs = []
    for fw in names:
        sites = list(mutants((FW / fw).read_text()))
        for op in ("waitt", "addt", "short", "branch"):
            mine = [s for s in sites if s[0] == op]
            for _, line, new in mine:
                if chosen(f"{fw}|{op}|{line}", args.per, len(mine)):
                    jobs.append((fw, op, line, new, args.trials))
    if args.shard:
        k, n = (int(x) for x in args.shard.split("/"))
        jobs = jobs[k::n]
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run_one, jobs))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1))
    return report(names, results)


def report(names: list, results: list) -> int:
    """The table (per firmware, per operator), the certified mutants whose bench saw the
    protocol fail, then every UNSOUND, ERROR and NOT_PROVEN mutant; 1 if any is UNSOUND
    or ERROR."""
    kinds = ["REFUTED", "NOT_PROVEN", "CHECKED", "UNSOUND", "INVALID", "ERROR"]
    print("| Firmware | Mutants | " + " | ".join(kinds) + " |")
    print("| --- | --- |" + " --- |" * len(kinds))
    for fw in names:
        rs = [r for r in results if r["fw"] == fw]
        if rs:
            print(f"| `{fw[:-4]}` | {len(rs)} | "
                  + " | ".join(str(sum(r['result'] == k for r in rs)) for k in kinds) + " |")
    tot = {k: sum(r["result"] == k for r in results) for k in kinds}
    print(f"| all | {len(results)} | " + " | ".join(str(tot[k]) for k in kinds) + " |")
    by_op = {op: [r for r in results if r["op"] == op] for op in ("waitt", "addt", "short", "branch")}
    print("\nBy operator: " + "; ".join(
        f"{op} {len(rs)} ({sum(r['result'] == 'REFUTED' for r in rs)} refuted, "
        f"{sum(r['result'] == 'NOT_PROVEN' for r in rs)} not proven, "
        f"{sum(r['result'] == 'CHECKED' for r in rs)} certified)" for op, rs in by_op.items()))
    # escert proves timing only: of the certified mutants that have a bench, those whose
    # bench's bus models then saw the protocol fail
    benched = [r for r in results if r["result"] == "CHECKED" and "protocol" in r]
    broken = [r for r in benched if any(p for _, p in r["protocol"])]
    print(f"\nCertified mutants with a bench: {len(benched)}; the protocol still works in "
          f"{len(benched) - len(broken)}, the bench's bus models see it fail in {len(broken)}.")
    notp = [r for r in results if r["result"] == "NOT_PROVEN"]
    at = [r for r in notp if r.get("faults_at")]
    print(f"Not proven either way: {len(notp)}; of these, {len(at)} fault on their bench built "
          f"with its own parameters (its clock), another program than the one judged.")
    for r in results:
        if r["result"] in ("UNSOUND", "ERROR", "NOT_PROVEN"):
            at_ = f" [faults at {r['faults_at']}'s parameters]" if r.get("faults_at") else ""
            print(f"- {r['result']} {r['fw']}:{r['line']} {r['op']}: {r.get('detail', '')}{at_}")
    for r in broken:
        why = "; ".join(f"{b}: {p[0]}" for b, p in r["protocol"] if p)
        print(f"- CHECKED, protocol broken {r['fw']}:{r['line']} {r['op']}: {why}")
    return 1 if tot["UNSOUND"] or tot["ERROR"] else 0


if __name__ == "__main__":
    sys.exit(main())
