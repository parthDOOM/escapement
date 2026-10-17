#!/usr/bin/env python3
"""Mutation testing of the RTL against the ISS co-simulation (docs/04-VERIFICATION.md).

Each mutant is one textual change to a copy of src/. The cosim (test/cosim) runs against
the mutated copy; a mutant is KILLED if any comparison fails. If the cosim misses it, the
directed core suite (test/rtl_unit) runs too: KILLED-UNIT means only a directed test
caught it, which the report keeps visible (the random cosim has a blind spot there).
SURVIVED means both missed it: either an equivalent mutant (document why) or a hole.

    python3 test/mutation/run_mutants.py [--seeds N] [--only NAME ...]

Run under WSL/Linux with the cosim environment (scripts/setup-wsl.sh) active.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# name -> (file, original text, mutated text, what it breaks)
MUTANTS = {
    # ---- v1.1 execution digest (sec. 10.1)
    "dg_no_poly": ("esc_core.v", "(dg[31] ? DG_POLY : 32'd0)", "32'd0",
                   "digest feedback polynomial dropped"),
    "dg_no_ldt": ("esc_core.v", " ^ (ld_v ? tape_rdata : 32'd0)", "",
                  "LDT loaded value not folded into the digest"),
    "dg_no_pins": ("esc_core.v", "{uio_oe, uio_out, 16'd0} ^ dg_cw", "dg_cw",
                   "pin state not folded into the digest"),
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
    "cap_ring_no_wrap": ("esc_cap.v", "ptr <= base; wrapped <= 1'b1;", "ptr <= ptr_inc; wrapped <= 1'b1;",
                         "ring mode does not wrap to base"),
    "cap_no_snapshot": ("esc_cap.v", "en <= 1'b1; snap <= 1'b1;", "en <= 1'b1; snap <= 1'b0;",
                        "arming does not take a snapshot"),
    "cap_mask_ignored": ("esc_cap.v", "((sync ^ prev) & mask) != 8'd0", "(sync ^ prev) != 8'd0",
                         "unwatched pins trigger records"),
    "cap_capt_no_override": ("esc_cap.v", "& ~set_full & ~capt_we &", "& ~set_full &",
                             "a record is generated at the CAPT edge"),
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


def run_one(name: str, seeds: int, workdir: Path) -> str:
    fname, old, new, _ = MUTANTS[name]
    src = workdir / name / "src"
    shutil.copytree(REPO / "src", src, dirs_exist_ok=True)
    f = src / fname
    text = f.read_text()
    if text.count(old) != 1:
        return f"BADPATCH ({text.count(old)} matches)"
    f.write_text(text.replace(old, new))
    build = workdir / name / "sim_build"
    cmd = ["make", "-s", f"SRC_DIR={src}", f"SIM_BUILD={build}"]
    env = dict(os.environ, COSIM_SEEDS=str(seeds), COSIM_OUT=str(workdir / name / "failures"))
    r = subprocess.run(cmd, cwd=REPO / "test" / "cosim", capture_output=True, text=True, env=env)
    out = r.stdout + r.stderr
    if "Error" in out and "TESTS=" not in out:
        return "COMPILE-ERROR"
    if "FAIL=0" not in out:
        return "KILLED"
    unit = workdir / name / "unit_build"
    cmd = ["make", "-s", f"SRC_DIR={src}", f"SIM_BUILD={unit}"]
    r = subprocess.run(cmd, cwd=REPO / "test" / "rtl_unit", capture_output=True, text=True)
    out = r.stdout + r.stderr
    if "Error" in out and "TESTS=" not in out:
        return "COMPILE-ERROR"
    return "KILLED-UNIT" if ("FAIL=0" not in out) else "SURVIVED"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=60)
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    names = args.only or list(MUTANTS)
    killed = 0
    with tempfile.TemporaryDirectory(prefix="escmut_") as td:
        for name in names:
            res = run_one(name, args.seeds, Path(td))
            killed += res.startswith("KILLED")
            print(f"{res:13s} {name:24s} {MUTANTS[name][3]}", flush=True)
    print(f"\n{killed}/{len(names)} mutants killed")
    return 0 if killed == len(names) else 1


if __name__ == "__main__":
    sys.exit(main())
