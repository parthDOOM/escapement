#!/usr/bin/env python3
"""Mutation testing of the RTL (docs/04-VERIFICATION.md).

Each mutant is one change to a copy of src/: the curated mutants below, each aimed at a
specific feature, and the generated ones (generate.py), one classic fault at a sampled
site of every synthesisable line. The test suites that can see the mutated file run
against the copy, cheapest first, and the first that fails kills it:

    esc_*.v            cosim (ISS lockstep), rtl_unit, chip tests, pin tests
    escapement_host.v  host, chip tests, pin tests
    escapement_sram.v  host, cosim, chip tests
    project.v          chip tests, pin tests

A suite that hangs past its time limit counts as a kill. A mutant that no suite kills is
then checked formally against the original RTL (formal/equiv.sh with the mutated tree):
EQUIVALENT means no input sequence ever makes any chip output differ, so no test could
kill it; SURVIVED means a test hole, or a proof that did not finish.

    python3 test/mutation/run_mutants.py [--jobs J] [--seeds N] [--only ID ...]
        [--curated | --generated] [--shard I/N] [--no-equiv] [--json FILE] [--list]

Run under WSL/Linux with the simulation environment (scripts/setup-wsl.sh) active; the
equivalence check also needs PDK_ROOT (or LIB) as for make equiv.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate  # noqa: E402

# Curated mutants: name -> (file, original text, mutated text, what it breaks)
MUTANTS = {
    # ---- v1.1 execution digest (sec. 10.1)
    "dg_no_poly": ("esc_core.v", "(dg[31] ? DG_POLY : 32'd0)", "32'd0",
                   "digest feedback polynomial dropped"),
    "dg_no_ldt": ("esc_core.v", " ^ (ld_v ? tape_rdata : 32'd0)", "",
                  "LDT loaded value not folded into the digest"),
    "dg_no_pins": ("esc_core.v", "{uio_oe, uio_out, 3'd0, ob_low, 8'd0} ^ dg_cw", "dg_cw",
                   "pin state not folded into the digest"),
    # ---- v1.2 pin bank B (sec. 5)
    "b_drive_shifted": ("esc_core.v", "to_phys = bank ? {v[4:0], 8'd0} : {5'd0, v};",
                        "to_phys = bank ? {v[3:0], 9'd0} : {5'd0, v};",
                        "a bank B DRIVE lands one pin off"),
    "b_released_low": ("esc_core.v", "assign ob_out  = pin_val[12:8] | ~pin_oe[12:8];",
                       "assign ob_out  = pin_val[12:8] & pin_oe[12:8];",
                       "a released bank B output is low (selects an SPI device at reset)"),
    "b_view_bank_a": ("esc_core.v", "assign vsync[8*go +: 8]    = ctx_bank[go] ? {3'd0, s2[12:8]} : s2[7:0];",
                      "assign vsync[8*go +: 8]    = s2[7:0];",
                      "a bank B context reads bank A's inputs"),
    "b_owner_bank_a": ("esc_core.v",
                       "assign owned_by[8*go +: 8] = ctx_bank[go] ? {3'd0, pown[13*go + 8 +: 5]} : pown[13*go +: 8];",
                       "assign owned_by[8*go +: 8] = pown[13*go +: 8];",
                       "bank B ownership checked against bank A pins"),
    "b_fault_keeps_pins": ("esc_core.v", "if (fault_k[pk]) oe_n = oe_n & ~pown[13*pk +: 13];",
                           "if (fault_k[pk]) oe_n = oe_n & ~{5'd0, pown[13*pk +: 8]};",
                           "a fault does not release the context's bank B pins"),
    "b_sync_missing": ("esc_core.v", "s1 <= {ib_in, uio_in};", "s1 <= {5'd0, uio_in};",
                       "bank B inputs never reach the synchronisers"),
    "b_digest_blind": ("esc_core.v", "{ob_low, uio_oe, uio_out} != dg_prev", "{5'd0, uio_oe, uio_out} != dg_prev",
                       "a bank B output change alone is not an active digest edge"),
    "b_capture_bank_a": ("esc_cap.v", "wire [7:0] sync = bank ? {3'd0, psync[12:8]} : psync[7:0];",
                         "wire [7:0] sync = psync[7:0];",
                         "capture ignores the CAPT bank bit"),
    # ---- v1.1 tape port and LDT/STT (sec. 7.6)
    "tape_no_imm": ("esc_core.v", "(opb[7:0] + f_imm16[7:0])", "opb[7:0]",
                    "tape address ignores imm8"),
    "stt_ignores_outcome": ("esc_core.v", "(cm_ok & (op == OP_STT))", "(commit_valid & (op == OP_STT))",
                            "STT writes even when it faults (ILLEGAL/PC_RANGE)"),
    "ldt_no_forward": ("esc_core.v", "(ld_v && ld_rd == 2'd0) ? tape_rdata : ", "",
                       "observation record of LDT r0 shows the stale value"),
    "ldt_wrong_ctx": ("esc_core.v", ".ld_we       (ld_v & (ld_ctx == gk[1:0]) & ~start)",
                      ".ld_we       (ld_v & (cctx == gk[1:0]) & ~start)",
                      "deferred LDT write lands in the wrong context"),
    # ---- v1.1 GETI wait (sec. 7.3, D-20)
    "geti_no_wait": ("esc_core.v", "if (cs_hsmp) outcome = OC_W;", "if (1'b0) outcome = OC_W;",
                     "GETI no longer waits for in-flight SAMPLEs"),
    # ---- v1.1 capture unit (sec. 6.5)
    "cap_ignores_busy": ("esc_cap.v", "(n != 2'd0) & ~busy & ~start & ~stop",
                         "(n != 2'd0) & ~start & ~stop", "capture drains into a busy port"),
    "cap_no_overflow": ("esc_cap.v", "gen & (n_after == 2'd2)", "1'b0",
                        "staging overflow not detected"),
    "cap_ring_no_wrap": ("esc_cap.v", "(hit_lim & wrap) ? base : ptr_inc", "ptr_inc",
                         "ring mode does not wrap to base"),
    "cap_no_snapshot": ("esc_cap.v", "en <= 1'b1; snap <= 1'b1;", "en <= 1'b1; snap <= 1'b0;",
                        "arming does not take a snapshot"),
    "cap_mask_ignored": ("esc_cap.v", "((sync ^ prev) & mask) != 8'd0", "(sync ^ prev) != 8'd0",
                         "unwatched pins trigger records"),
    "cap_capt_no_override": ("esc_cap.v", "& ~set_full & ~capt_we &", "& ~set_full &",
                             "a record is generated at the CAPT edge"),
    # ---- v1.3 streaming capture (sec. 6.5, sec. 10)
    "st_no_hold": ("esc_cap.v", "wire       held     = stream & (ptr_nxt == rptr);",
                   "wire       held     = 1'b0;", "stream mode writes over unacknowledged records"),
    "st_hold_off_by_one": ("esc_cap.v", "wire       held     = stream & (ptr_nxt == rptr);",
                           "wire       held     = stream & (ptr == rptr);",
                           "stream mode holds one record late (overwrites the oldest)"),
    "st_no_ack": ("esc_cap.v", "if (ack) rptr <= ack_v;", "if (1'b0) rptr <= ack_v;",
                  "TAPE_ACK ignored"),
    "st_capt_keeps_rptr": ("esc_cap.v", "ptr <= capt_v[7:0]; rptr <= capt_v[7:0];",
                           "ptr <= capt_v[7:0];", "arming does not reset rptr to base"),
    "st_no_wrap": ("esc_cap.v", "wire       wrap     = ring | stream;", "wire       wrap     = ring;",
                   "stream mode stops at lim instead of wrapping"),
    "st_host_beats_drain": ("esc_core.v", "assign tape_busy  = tp_busy | cap_drain;",
                            "assign tape_busy  = tp_busy;",
                            "a capture drain does not claim the port from the host"),
    "st_rptr_hidden": ("esc_core.v", "5'd20: dbg_rdata = {24'd0, cap_rptr};",
                       "5'd20: dbg_rdata = 32'd0;", "sel 20 does not show rptr"),
    # ---- D-29 clock-gated banks: an enable that misses one condition holds a stale value
    "cg_r0_no_ldt": ("esc_ctx.v", ".en(st | (live & ((rdw & (w_rd_idx == 2'd0)) | ldk0))),",
                     ".en(st | (live & ((rdw & (w_rd_idx == 2'd0))))),",
                     "r0 bank not clocked by a deferred LDT write"),
    "cg_tmr_no_start": ("esc_ctx.v", ".en(st | (live & cm_ok & w_tmr_we)),",
                        ".en(live & cm_ok & w_tmr_we),", "START does not clear tmr"),
    "cg_ish_no_geti": ("esc_ctx.v", ".en(st | (live & (sample_ret | (cm_ok & w_geti)))),",
                       ".en(st | (live & sample_ret)),", "GETI does not clear ish"),
    "cg_rx_no_pop_shift": ("esc_ctx.v", ".en(live & (rx_popped | rx_w0)),", ".en(live & rx_w0),",
                           "RX mailbox does not shift on pop"),
    # ---- v1.0 core (regression of the original campaign)
    "admit_past_off_by_one": ("esc_core.v", "wire        ahd_now = (d_now != 32'd0) & ~d_now[31];",
                              "wire        ahd_now = ~d_now[31];", "deadline == commit tick admitted"),
    "retire_not_suppressed": ("esc_ctx.v", "wire       ret_ok     = due & ~kill;",
                              "wire       ret_ok     = due;", "retirement at a fault edge"),
}


# Mutants no test can kill and no equivalence proof here decides, each with the argument
# that it changes no behaviour. They are reported as EXCLUDED, never counted as killed.
P2 = ("acts only when a FIFO head is past its deadline (LATE_HEAD), which P2 proves never "
      "happens in any reachable state (docs/04-VERIFICATION.md), so the mutated logic is dead")
EXCLUDED = {
    "esc_core_rel_b808cdef": P2,       # esc_core.v:491 cs_late suppression
    "esc_core_cond_6ee4183d": P2,      # esc_core.v:666 late_ctx priority
    "esc_core_const_f8680ed1": P2,     # esc_core.v:776 first-fault record of LATE_HEAD
    "escapement_sram_const_6c8bc178": "A_DLY is the SRAM macro's delay-select tie (must be 1); it has no "
                                      "function in any model, and the macro model flags any other value",
    "escapement_sram_const_8907efe9": "A_BIST_REN is read only in the macro's BIST mode, which A_BIST_EN = 0 "
                                      "never enables",
    # reset and idle values of state that is always loaded before anything reads it
    "esc_core_const_ed962f97": "s2 reloads from s1 at every edge, so its reset value lives one cycle; nothing "
                               "reads the synchronised pins before a START, which takes a whole SPI frame",
    "esc_ctx_const_90bb8e65": "the checker's active flag is recomputed at every edge from eval_en, which is 0 "
                              "while not running: it clears at the first edge after reset",
    "esc_cap_const_9f9b6767": "snap acts only while en is set, and every CAPT that sets en also sets snap",
    "esc_cap_const_27c100a8": "snap acts only while en is set, and every CAPT that sets en also sets snap",
    "esc_cap_const_bda4e745": "snap acts only while en is set, and every CAPT that sets en also sets snap",
    "esc_cap_const_e24728af": "prev is read only to detect a change while en is set, and arming takes a "
                              "snapshot first, which loads prev",
    "esc_cap_const_f94d1ea0": "st1 is read only when two records are staged, and a record is written to it "
                              "before n counts it",
    "escapement_host_const_6c0e07d1": "mem_wdata reaches a memory only with mem_we, and every write loads it "
                                      "first; with mem_we low the SRAM ignores its data pins",
    "escapement_host_const_4f254f37": "READ_DBG sets dbg_ctx in its first payload byte, before the word is "
                                      "read in its second",
    "escapement_host_const_ba0e3c3f": "an extra tape read after reset whose data no command uses: READ_TAPE "
                                      "requests a fetch of its own for every word",
    "escapement_host_stuck_5c77ffc7": "cmd is C_IGNORE at every command byte (deselecting sets it), so "
                                      "holding it equals setting it",
    "escapement_host_const_28d0a987": "MBX_PUSH pushes at byte 5; a byte 6 would only shift word again, and "
                                      "every command that sends word loads it first",
    "escapement_host_stuck_44a212d9": "mctx is read only by MBX_PUSH, which sets it in its own first payload "
                                      "byte",
}


@dataclass
class Mutant:
    id: str
    file: str
    old: str
    new: str
    desc: str
    origin: str               # curated or generated
    op: str = "curated"
    line: int | None = None   # generated mutants replace one whole line


def all_mutants() -> list[Mutant]:
    ms = [Mutant(n, f, o, w, d, "curated") for n, (f, o, w, d) in MUTANTS.items()]
    ms += [Mutant(g.id, g.file, g.old, g.new, g.desc, "generated", g.op, g.line) for g in generate.generate()]
    return ms


# suite -> (directory, extra make arguments, time limit in seconds)
SUITES = {
    "cosim": ("test/cosim", [], 1800),
    "unit": ("test/rtl_unit", [], 900),
    "host": ("test/host", [], 900),
    "chip": ("test", [], 2400),
    "pins": ("test", ["COCOTB_TEST_MODULES=test_firmware_pins"], 2400),
}
PIPELINE = {
    "escapement_host.v": ["host", "chip", "pins"],
    "escapement_sram.v": ["host", "cosim", "chip"],
    "project.v": ["chip", "pins"],
}
CORE_PIPELINE = ["cosim", "unit", "chip", "pins"]


def apply(m: Mutant, src: Path) -> str | None:
    f = src / m.file
    text = f.read_text()
    if m.line is not None:
        lines = text.split("\n")
        if lines[m.line - 1] != m.old:
            return "line moved"
        lines[m.line - 1] = m.new
        f.write_text("\n".join(lines))
        return None
    if text.count(m.old) != 1:
        return f"{text.count(m.old)} matches"
    f.write_text(text.replace(m.old, m.new))
    return None


def run_suite(suite: str, src: Path, work: Path, seeds: int) -> str:
    """'pass', 'fail', 'timeout' or 'compile-error' for one suite on the mutated tree."""
    d, extra, limit = SUITES[suite]
    cwd = REPO / d
    env = dict(os.environ, PWD=str(cwd), COCOTB_PLUSARGS="+nodump",
               COCOTB_RESULTS_FILE=str(work / f"results_{suite}.xml"),
               COSIM_SEEDS=str(seeds), COSIM_OUT=str(work / "failures"))
    cmd = ["make", "-s", "-B", f"SRC_DIR={src}", f"SIM_BUILD={work / ('build_' + suite)}", *extra]
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env, timeout=limit)
    except subprocess.TimeoutExpired:
        subprocess.run(["pkill", "-f", str(work)], capture_output=True)
        return "timeout"
    out = r.stdout + r.stderr
    if "TESTS=" not in out:
        # no summary: either the mutant did not build (a broken mutant, not a kill) or the
        # simulation crashed or aborted after it started (a kill)
        started = "Running on" in out or "running " in out.lower() and "test_" in out
        return "fail" if started else "compile-error"
    return "pass" if "FAIL=0" in out else "fail"


EQUIV_LIMIT = int(os.environ.get("EQUIV_LIMIT", "1800"))   # seconds per equivalence proof


def _lift_memory_cap():
    """The proof needs more memory than one simulation: raise a soft address-space cap to
    the hard limit (run the campaign under `ulimit -S -v`, not `ulimit -v`)."""
    try:
        import resource
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        resource.setrlimit(resource.RLIMIT_AS, (hard, hard))
    except (ImportError, ValueError, OSError):
        pass


def equivalent(src: Path, work: Path) -> tuple[bool, str]:
    """(proved, note): the mutant changes no chip output, or why that was not shown."""
    try:
        r = subprocess.run(["bash", str(REPO / "formal/equiv.sh"), str(src), str(work / "equiv")],
                           capture_output=True, text=True, timeout=EQUIV_LIMIT, preexec_fn=_lift_memory_cap)
    except subprocess.TimeoutExpired:
        subprocess.run(["pkill", "-f", str(work / "equiv")], capture_output=True)
        return False, f"equivalence: undecided in {EQUIV_LIMIT // 60} min"
    if r.returncode == 0 and "PASS:" in r.stdout:
        return True, "equivalence: proved"
    log = work / "equiv" / "dprove.log"
    text = log.read_text(errors="replace") if log.exists() else ""
    if "NOT EQUIVALENT" in text.upper() or "not equivalent" in text:
        return False, "equivalence: outputs differ (a test hole)"
    if text:
        return False, "equivalence: undecided"
    return False, f"equivalence: check did not run (exit {r.returncode})"


def run_one(m: Mutant, seeds: int, root: Path, do_equiv: bool) -> dict:
    t0 = time.time()
    work = root / m.id
    src = work / "src"
    shutil.copytree(REPO / "src", src)
    res = {"id": m.id, "file": m.file, "line": m.line, "op": m.op, "origin": m.origin, "desc": m.desc}
    bad = apply(m, src)
    if bad:
        return res | {"result": "BADPATCH", "note": bad, "secs": 0}
    killed_by, result = None, "SURVIVED"
    for suite in PIPELINE.get(m.file, CORE_PIPELINE):
        r = run_suite(suite, src, work, seeds)
        if r == "compile-error":
            result = "COMPILE-ERROR"
            killed_by = suite
            break
        if r in ("fail", "timeout"):
            result, killed_by = "KILLED", suite + (" (timeout)" if r == "timeout" else "")
            break
    note = None
    if result == "SURVIVED" and m.id in EXCLUDED:
        shutil.rmtree(work, ignore_errors=True)
        return res | {"result": "EXCLUDED", "killed_by": None, "note": EXCLUDED[m.id],
                      "secs": round(time.time() - t0)}
    if result == "SURVIVED" and do_equiv:
        proved, note = equivalent(src, work)
        if proved:
            result = "EQUIVALENT"
    shutil.rmtree(work, ignore_errors=True)
    return res | {"result": result, "killed_by": killed_by, "note": note, "secs": round(time.time() - t0)}


def recheck_one(m: Mutant, root: Path) -> dict:
    """The equivalence check alone, for a mutant the tests already let through."""
    work = root / m.id
    src = work / "src"
    shutil.copytree(REPO / "src", src)
    bad = apply(m, src)
    if bad:
        return {"result": "BADPATCH", "note": bad}
    proved, note = equivalent(src, work)
    shutil.rmtree(work, ignore_errors=True)
    return {"result": "EQUIVALENT" if proved else "SURVIVED", "note": note}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=60)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--only", nargs="*")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--curated", action="store_true")
    g.add_argument("--generated", action="store_true")
    ap.add_argument("--shard", help="I/N: run every N-th mutant starting at I (0-based)")
    ap.add_argument("--no-equiv", action="store_true", help="skip the equivalence check of survivors")
    ap.add_argument("--json", help="write every result to this file")
    ap.add_argument("--progress", help="append each result to this JSON-lines file as it finishes, and skip "
                                       "mutants it already holds (resume an interrupted run)")
    ap.add_argument("--recheck", action="store_true",
                    help="with --progress: rerun only the equivalence check of the survivors it holds")
    ap.add_argument("--list", action="store_true", help="list the mutants and exit")
    args = ap.parse_args()

    if args.recheck:
        prog = Path(args.progress)
        rows = [json.loads(line) for line in prog.read_text().splitlines() if line.strip()]
        byid = {m.id: m for m in all_mutants()}
        todo = [r for r in rows if r["result"] == "SURVIVED" and r["id"] in byid and not r.get("rechecked")]
        print(f"rechecking {len(todo)} survivors, {args.jobs} at a time", flush=True)
        with tempfile.TemporaryDirectory(prefix="escmut_") as td, ThreadPoolExecutor(max(1, args.jobs)) as pool:
            futs = {pool.submit(recheck_one, byid[r["id"]], Path(td)): r for r in todo}
            for f in as_completed(futs):
                r = futs[f]
                r.update(f.result())
                r["rechecked"] = True
                print(f"{r['result']:13s} {r['id']:34s} {r['note']}  {r['desc']}", flush=True)
                prog.write_text("".join(json.dumps(x) + "\n" for x in rows))   # keep each result
        return 0

    ms = all_mutants()
    if args.curated:
        ms = [m for m in ms if m.origin == "curated"]
    if args.generated:
        ms = [m for m in ms if m.origin == "generated"]
    if args.only:
        ms = [m for m in ms if m.id in args.only]
    if args.shard:
        i, n = map(int, args.shard.split("/"))
        ms = ms[i::n]
    if args.list:
        for m in ms:
            print(f"{m.id:34s} {m.desc}")
        print(len(ms), "mutants")
        return 0

    results = []
    if args.progress and Path(args.progress).exists():
        done = {}
        for line in Path(args.progress).read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["id"]] = r
        results = [done[m.id] for m in ms if m.id in done]
        ms = [m for m in ms if m.id not in done]
        print(f"{len(results)} mutants already done ({args.progress})", flush=True)
    jobs = max(1, min(args.jobs, len(ms) or 1))
    print(f"{len(ms)} mutants, {jobs} at a time", flush=True)
    with tempfile.TemporaryDirectory(prefix="escmut_") as td, ThreadPoolExecutor(jobs) as pool:
        futs = [pool.submit(run_one, m, args.seeds, Path(td), not args.no_equiv) for m in ms]
        for f in as_completed(futs):
            r = f.result()
            results.append(r)
            if args.progress:
                with open(args.progress, "a") as fh:
                    fh.write(json.dumps(r) + "\n")
            print(f"{r['result']:13s} {r.get('killed_by') or '':16s} {r['secs']:5d} s  {r['id']:34s} {r['desc']}",
                  flush=True)
    counts = Counter(r["result"] for r in results)
    killed, eq, ex = counts["KILLED"], counts["EQUIVALENT"], counts["EXCLUDED"]
    invalid = counts["BADPATCH"] + counts["COMPILE-ERROR"]
    scored = len(results) - invalid - eq - ex
    print(f"\n{killed} of {scored} killed; {eq} equivalent (proved); {ex} excluded (argued, see EXCLUDED); "
          f"{invalid} invalid; {counts['SURVIVED']} survived")
    if args.json:
        Path(args.json).write_text(json.dumps({"counts": dict(counts), "seeds": args.seeds,
                                               "results": results}, indent=1) + "\n")
    return 0 if counts["SURVIVED"] == 0 and invalid == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
