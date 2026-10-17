"""sw/rp2350/bringup.py before silicon: every stage on a simulated demo board.

The board is demoboard_mock.MockBoard (tt-micropython-firmware's API surface, with RP
time) and the chip is demoboard_mock.SimChip: the host interface modelled cycle by cycle
after src/escapement_host.v, the ISS as the core, and both SRAMs. A correct chip must
pass every stage; each injected fault must make the stage that is meant to catch it
FAIL, with the diagnosis that points at it. test/test_bringup_rtl.py runs the same
script against the RTL.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sw" / "rp2350"))

import bringup  # noqa: E402
import make_bundle  # noqa: E402
from demoboard_mock import Logic, MockBoard, SimChip  # noqa: E402
from escasm import assemble  # noqa: E402

SQUARE_SRC = """
.equ HALF, 12500
.entry 0, start
""" + "".join(f".own {i}, 0\n" for i in range(8)) + """
start:
    ADDT  64
    EMIT  0xFF, 0x55, 0xFF
loop:
    ADDT  HALF
    EMIT  0xFF, 0xAA, 0xFF
    ADDT  HALF
    EMIT  0xFF, 0x55, 0xFF
    JMP   loop
"""
GETP_SRC = """
.entry 0, start
start:
    GETP  r0
    JMP   start
"""


@pytest.fixture(scope="module")
def bundles(tmp_path_factory):
    d = tmp_path_factory.mktemp("bundles")
    for fw in sorted((ROOT / "firmware").glob("*.asm")):
        if fw.name not in make_bundle.SKIP:
            (d / (fw.stem + ".json")).write_text(json.dumps(make_bundle.bundle(fw)))
    return d


def subset(src, tmp_path, names, edit=None):
    d = tmp_path / "subset"
    d.mkdir(exist_ok=True)
    for n in names:
        b = json.loads((src / n).read_text())
        if edit:
            edit(b)
        (d / n).write_text(json.dumps(b))
    return d


def run_on(chip, bundle_dir, *, stages=None, optional=False, quick=True, keep_going=False,
           **board_kw):
    board = MockBoard(chip, **board_kw)
    lines = []
    ok = bringup.run(bundles=str(bundle_dir), stages=stages, optional=optional, quick=quick,
                     keep_going=keep_going, io=board.io(bringup), out=lines.append)
    return ok, bringup.LAST.results, "\n".join(lines)


def test_stage4_programs_are_the_assembled_sources():
    for src, words, cfg in ((SQUARE_SRC, bringup.SQUARE, bringup.SQUARE_CFG),
                            (GETP_SRC, bringup.GETP, bringup.GETP_CFG)):
        im = assemble(src)
        assert im.words[:len(words)] == words and not any(im.words[len(words):])
        assert (im.ctx_enable_mask, im.entry_pcs_packed, im.pin_owned_mask,
                im.pin_owner_packed, im.pin_od_mask) == cfg
    assert assemble(SQUARE_SRC).symbols.get("HALF", bringup.SQUARE_HALF) == bringup.SQUARE_HALF


def test_mock_logic_is_as_strict_as_microcotb():
    with pytest.raises(TypeError):
        Logic(1) & 1                                   # the bug escapement_demo.py had
    assert int(Logic(1)) == 1 and not Logic(0)


def test_every_bundle_has_a_prediction(bundles):
    kinds = {}
    for f in bundles.glob("*.json"):
        b = json.loads(f.read_text())
        kinds[b["name"]] = b["expect"]["kind"]
    assert kinds["uart_tx.asm"] == "halt" and kinds["i2c_ctrl.asm"] == "steady"
    # A firmware that neither halts nor settles gets only a state check on silicon.
    assert [n for n, k in kinds.items() if k == "none"] == [], kinds


def test_correct_chip_passes_every_stage(bundles):
    chip = SimChip()
    ok, results, log = run_on(chip, bundles, optional=True, quick=False)
    assert ok, log
    assert [n for n, passed, _ in results] == [0, 1, 2, 3, 4, 5, 6], log
    assert all(passed for _, passed, _ in results), log
    n = len(list(bundles.glob("*.json")))
    assert f"{n} of {n} firmware match the model" in log, log
    assert not chip.contention, chip.contention[:4]    # the RP never fought the chip
    assert chip.timing_violations == 0                 # SCK phases always >= 4 clocks


FAULTS = [
    # id, chip kwargs, board kwargs, stage that must fail, words in the diagnosis
    ("not in shuttle", {}, {"in_shuttle": False}, 0, "not in this board's shuttle"),
    ("DIP switch on ui[1]", {}, {"dip_high": 0b010}, 0, "DIP switch"),
    ("PWM overridden", {}, {"pwm_report": 10_000_000}, 1, "reports 10000000 Hz"),
    ("MISO stuck high", {"miso_stuck": 1}, {}, 1, "Bits 0 are high"),
    ("uio3 held low", {"uio_ext_low": 0x08}, {}, 1, "uio pins 3 read 0"),
    ("no project clock", {"clock_dead": True}, {}, 2, "MISO is always 0"),
    ("MISO stuck low", {"miso_stuck": 0}, {}, 2, "MISO is always 0"),
    ("MOSI stuck low", {"mosi_stuck": 0}, {}, 2, "unknown command (0x99) did not set host error"),
    ("MOSI stuck high", {"mosi_stuck": 1}, {}, 2, "MOSI (ui[2]) is stuck high"),
    ("v1.0 chip", {"version": 5}, {}, 2, "protocol version 5"),
    ("bit-bang too fast", {}, {"op_ns": 100}, 2, "one pin write takes 100 ns"),
    ("program SRAM address bit 3 stuck", {"mem_faults": {0: {"addr_stuck": (3, 0)}}}, {}, 3,
     "program SRAM, address in data pattern: 128 of 256 words wrong; address bit(s) 3 "
     "have no effect"),
    ("tape SRAM data bit 17 stuck at 1", {"mem_faults": {1: {"data_stuck": (17, 1)}}}, {}, 3,
     "tape SRAM: data bits 17 never read 0 (stuck at 1)"),
    ("program SRAM data bits 4 and 9 shorted", {"mem_faults": {0: {"data_short": (4, 9)}}},
     {}, 3, "program SRAM: data bits always equal (shorted): 4=9"),
    ("tape SRAM address lines 5 and 6 shorted", {"mem_faults": {1: {"addr_short": (5, 6)}}},
     {}, 3, "shorted address lines"),
    ("uio4 output driver dead", {"uio_out_dead": 0x10}, {}, 4,
     "Pins 4 never read 0"),
    ("chip clock at half rate", {"clock_scale": 0.5}, {}, 4, "the chip runs at 12.500 MHz"),
    ("uio2 input stuck at 0", {"uio_in_stuck": (2, 0)}, {}, 4, "Input 2 stuck at 0"),
]


@pytest.mark.parametrize("name,chip_kw,board_kw,stage,diag", FAULTS, ids=[f[0] for f in FAULTS])
def test_fault_fails_the_right_stage(bundles, tmp_path, name, chip_kw, board_kw, stage, diag):
    small = subset(bundles, tmp_path, ["uart_tx.json"])
    ok, results, log = run_on(SimChip(**chip_kw), small, **board_kw)
    assert not ok, log
    failed = [(n, d) for n, passed, d in results if not passed]
    assert failed[0][0] == stage, log
    assert diag in failed[0][1], log
    assert all(passed for n, passed, _ in results if n < stage), log


def test_address_fault_names_the_bit(bundles, tmp_path):
    ok, results, log = run_on(SimChip(mem_faults={0: {"addr_stuck": (3, 1)}}),
                              subset(bundles, tmp_path, ["uart_tx.json"]))
    assert "address bit(s) 3 have no effect" in results[-1][2], log


def test_execution_fault_changes_state(bundles, tmp_path):
    small = subset(bundles, tmp_path, ["uart_tx.json", "i2c_ctrl.json"])
    ok, results, log = run_on(SimChip(exec_fault="r0bit31"), small)
    n, passed, diag = results[-1]
    assert (n, passed) == (5, False), log
    assert "uart_tx.asm" in diag and "chip and model differ at the end" in log, log
    assert "ctx 0 r0 0x80" in log and ", model 0x00000000" in log, log


def test_digest_only_fault_is_caught(bundles, tmp_path):
    small = subset(bundles, tmp_path, ["uart_tx.json"])
    ok, results, log = run_on(SimChip(exec_fault="digest"), small)
    assert results[-1][:2] == (5, False), log
    assert "same end state, different history" in log, log


def test_stale_bundles_are_caught(bundles, tmp_path):
    def corrupt(b):
        if b["expect"]["kind"] == "halt":
            b["expect"]["digest"] ^= 1
        else:
            b["expect"]["dg0"] ^= 1
    small = subset(bundles, tmp_path, ["uart_tx.json", "can_tx.json"], corrupt)
    ok, results, log = run_on(SimChip(), small, stages=(5,))
    assert results[-1][:2] == (5, False), log
    assert "2 of 2 firmware differ" in results[-1][2], log
    assert "not on the model orbit" in log, log


def test_keep_going_reports_later_stages(bundles, tmp_path):
    small = subset(bundles, tmp_path, ["uart_tx.json"])
    ok, results, log = run_on(SimChip(mem_faults={1: {"data_stuck": (17, 1)}}), small,
                              keep_going=True)
    assert [(n, p) for n, p, _ in results][3:] == [(3, False), (4, True), (5, False)], log
    assert "LOAD FAILED" in log and "tape readback differs" in log, log


def test_escapement_demo_runs_one_bundle(bundles, monkeypatch, capsys):
    import escapement_demo
    board = MockBoard(SimChip())
    monkeypatch.setattr(bringup.DemoBoardIO, "open", classmethod(lambda cls: board.io(bringup)))
    assert escapement_demo.run(str(bundles / "uart_tx.json"))
    assert "SILICON MATCHES THE MODEL" in capsys.readouterr().out


def test_orbit_search_matches_the_iss(bundles):
    b = json.loads((bundles / "combo_uart_i2c.json").read_text())
    e = b["expect"]
    from esciss import Machine
    from escasm import assemble_file
    m = Machine(assemble_file(ROOT / "firmware" / "combo_uart_i2c.asm"))
    m.run(e["t0"] + 12345, stop_when_idle=False)
    hit = bringup.find_on_orbit(e["dg0"], e["folds"], m.dg, 20000)
    assert hit is not None and hit == make_bundle.find_on_orbit(e["dg0"], e["folds"], m.dg, 20000)
