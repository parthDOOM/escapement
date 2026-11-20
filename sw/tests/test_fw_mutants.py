"""The planted timing bugs' aimed benches (test/mutation/fw_mutants.py, AIMED).

Each aimed bench drives a path the co-simulation benches leave alone, and was added for
mutants that escert flags while no search reached their fault. Here the
unmutated program must pass the bench (so a protocol failure the campaign records for a
certified mutant is the mutant's doing), and each of those mutants must fault on it at an
instruction escert flags, for the reason escert gives. (LONG_AIMED, minutes on the ISS, is
exercised by the campaign itself.)
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

# bench: the mutants it was made for, each (operator, line, the line's source before it)
MADE_FOR = {
    "avr_isp_no_target": [("addt", 83, "ADDT  CLK_KHZ // 10"), ("waitt", 219, "WAITT")],
    "swim_host_nack": [("addt", 229, "ADDT  LONG")],
    "cec_other_address": [("addt", 195, "ADDT  T_SA")],
    "dali_query": [("addt", 204, "ADDT  TE")],
    "westminster_250k": [("addt", 138, "ADDT  HB3")],
    "morse_long_spaces": [("waitt", 179, "WAITT")],
    "swim_host_glitch": [("waitt", 282, "WAITT")],
    "modbus_rtu_noise": [("waitt", 319, "WAITT")],
}
SUBCODE = {"PAST": 3, "ORDER": 4}


def test_every_aimed_bench_names_its_mutant():
    assert set(MADE_FOR) == set(M.AIMED)


def _image(fw: str, src: str, equ):
    pre = "".join(f".equ {k}, {v}\n" for k, v in (equ or {}).items())
    return assemble(pre + src, base_dir=M.FW, name=fw)


@pytest.mark.parametrize("name", sorted(M.AIMED))
def test_aimed_bench_passes_unmutated(name):
    fw, ticks, factory, equ = M.AIMED[name]
    w, problems = M.bench_witness_one(_image(fw, (M.FW / fw).read_text(), equ), name, ticks,
                                      factory)
    assert w is None and problems == [], (w, problems)


@pytest.mark.parametrize("name,mutant", [(n, m) for n in sorted(M.AIMED) for m in MADE_FOR[n]])
def test_aimed_bench_faults_its_mutant(name, mutant):
    fw, ticks, factory, equ = M.AIMED[name]
    op, line, was = mutant
    src = (M.FW / fw).read_text()
    assert src.split("\n")[line - 1].split(";")[0].strip() == was, "the firmware moved"

    lines = src.split("\n")
    lines[line - 1] = next(n for o, ln, n in M.mutants(src) if o == op and ln == line)
    image = _image(fw, "\n".join(lines), equ)
    flagged = [(c.ctx, f.pc, f.kind) for c in certify(image) if c.status != "CHECKED"
               for f in c.findings]
    w, _ = M.bench_witness_one(image, name, ticks, factory)
    assert w is not None, "the mutant runs the bench without a fault"
    assert (w["ctx"], w["pc"], next(k for k, v in SUBCODE.items() if v == w["subcode"])) in flagged
