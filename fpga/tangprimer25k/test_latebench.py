"""latebench.py through the bare Pico on the simulated chip: the two refused programs of
late_bundles.py, and a chip or bundle that must fail each check.

    PYTHONPATH=../../sw python -m pytest -q test_latebench.py
"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "sw"))
sys.path.insert(0, str(HERE.parents[1] / "sw" / "tests"))
sys.path.insert(0, str(HERE.parents[1] / "sw" / "rp2350"))

import barepico  # noqa: E402
import late_bundles  # noqa: E402
import latebench  # noqa: E402
from demoboard_mock import MockBoard, SimChip  # noqa: E402
from test_barepico import GpioMachine  # noqa: E402


@pytest.fixture(scope="module")
def refused(tmp_path_factory):
    d = tmp_path_factory.mktemp("refused")
    late_bundles.main(d)
    return d


def run(d, **chip_kw):
    chip = SimChip(**chip_kw)
    board = MockBoard(chip, op_ns=1000)
    io = barepico.open_io(machine=GpioMachine(board), time_mod=board.time_mod,
                          sys_hz=latebench.SYS_HZ)
    lines = []
    fails = latebench.run(bundles=str(d), io=io, out=lines.append)
    return fails, "\n".join(lines)


def test_both_programs_are_refused_as_foretold(refused):
    """uart_tx with a 5-tick bit: escert REFUTED (PAST at pc 8), the chip faults at pc 8, tick
    35, ADMIT PAST, digest as the model's; refuse_owner: OWNER at pc 3, tick 15. Pins released,
    tripwires clear."""
    fails, log = run(refused)
    assert fails == [], log
    assert "late:   escert, before the run: context 0 REFUTED, PAST at pc 8" in log, log
    assert "late:   the chip: ADMIT PAST, context 0, pc 8, tick 35, the context FAULTED" in log, log
    assert "late:   the chip: ADMIT OWNER, context 0, pc 3, tick 15, the context FAULTED" in log, log
    assert log.count("none driven") == 2 and log.count("proof tripwires 0x000:") == 2, log
    assert log.endswith("no late or unowned edge reached a pin"), log


def test_a_tripwire_fails_the_bench(refused):
    """A chip whose never-late monitor fired (select 27 bit 0) is reported by name."""
    fails, log = run(refused, tripwire=1)
    assert len(fails) == 2 and all("P2 never late" in f for f in fails), log


def test_a_bundle_whose_model_disagrees_fails(refused, tmp_path):
    """A stale prediction (the fault one tick later) is a difference, not a pass."""
    for f in refused.glob("*.json"):
        b = json.loads(f.read_text())
        if f.name == "uart_tx_late.json":
            b["expect"]["fault_tick"] += 1
        (tmp_path / f.name).write_text(json.dumps(b))
    fails, log = run(tmp_path)
    assert len(fails) == 1 and fails[0].startswith("uart_tx_late.json: chip and model differ"), log


def test_a_certified_program_is_not_a_refusal(refused, tmp_path):
    """The bench insists that escert foretold the refusal."""
    for f in refused.glob("*.json"):
        b = json.loads(f.read_text())
        b["escert"][0]["status"] = "CHECKED"
        (tmp_path / f.name).write_text(json.dumps(b))
    fails, log = run(tmp_path)
    assert len(fails) == 2 and all("not REFUTED" in f for f in fails), log
