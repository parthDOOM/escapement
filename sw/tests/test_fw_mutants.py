"""The planted timing bugs' aimed benches (test/mutation/fw_mutants.py, AIMED).

Each aimed bench drives a path the co-simulation benches leave alone, and was added for a
mutant that escert flags exactly while no search reached its flagged instruction. Here the
unmutated program must pass the bench (so a protocol failure the campaign records for a
certified mutant is the mutant's doing), and that mutant must fault on it at the
instruction escert flags, for the reason escert gives.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "test" / "mutation"), str(REPO / "test" / "cosim")]

import fw_mutants as M  # noqa: E402
from escasm import assemble  # noqa: E402
from escert import certify  # noqa: E402

# bench: (operator, line, the line's source before the mutant), the mutant it was made for
MADE_FOR = {
    "avr_isp_no_target": ("addt", 83, "ADDT  CLK_KHZ // 10"),
    "swim_host_nack": ("addt", 229, "ADDT  LONG"),
    "cec_other_address": ("addt", 195, "ADDT  T_SA"),
    "dali_query": ("addt", 204, "ADDT  TE"),
    "westminster_250k": ("addt", 138, "ADDT  HB3"),
}
SUBCODE = {"PAST": 3, "ORDER": 4}


def test_every_aimed_bench_names_its_mutant():
    assert set(MADE_FOR) == set(M.AIMED)


def _image(fw: str, src: str, equ):
    pre = "".join(f".equ {k}, {v}\n" for k, v in (equ or {}).items())
    return assemble(pre + src, base_dir=M.FW, name=fw)


@pytest.mark.parametrize("name", sorted(M.AIMED))
def test_aimed_bench_passes_unmutated_and_faults_its_mutant(name):
    fw, ticks, factory, equ = M.AIMED[name]
    op, line, was = MADE_FOR[name]
    src = (M.FW / fw).read_text()
    assert src.split("\n")[line - 1].split(";")[0].strip() == was, "the firmware moved"
    w, problems = M.bench_witness_one(_image(fw, src, equ), name, ticks, factory)
    assert w is None and problems == [], (w, problems)

    lines = src.split("\n")
    lines[line - 1] = next(n for o, ln, n in M.mutants(src) if o == op and ln == line)
    image = _image(fw, "\n".join(lines), equ)
    flagged = [(c.ctx, f.pc, f.kind) for c in certify(image) if c.status != "CHECKED"
               for f in c.findings]
    w, _ = M.bench_witness_one(image, name, ticks, factory)
    assert w is not None, "the mutant runs the bench without a fault"
    assert (w["ctx"], w["pc"], next(k for k, v in SUBCODE.items() if v == w["subcode"])) in flagged
