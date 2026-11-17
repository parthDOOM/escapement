"""swdbench.py through the bare Pico on the simulated chip: swd_idcode at 25 MHz debugging an
RP2040 model (its three SW-DPs on one bus, dormant at the start; sw/escmodels/swd.py).

    PYTHONPATH=../../sw python -m pytest -q test_swdbench.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "sw"))
sys.path.insert(0, str(HERE.parents[1] / "sw" / "tests"))
sys.path.insert(0, str(HERE.parents[1] / "sw" / "rp2350"))

import barepico  # noqa: E402
import swdbench  # noqa: E402
from demoboard_mock import MockBoard, SimChip  # noqa: E402
from escasm import assemble_file  # noqa: E402
from escmodels import Board, SwdTarget  # noqa: E402
from test_barepico import GpioMachine  # noqa: E402

FW = HERE.parents[1] / "firmware"
RP2040 = {0x01002927: 0, 0x11002927: 1, 0xF1002927: 2}
SWD_PINS = (1 << 4) | (1 << 5)


class SwdChip(SimChip):
    """uio[4] (SWCLK) and uio[5] (SWDIO) wired to an RP2040 model's debug port, pulled up
    as its SWDIO is; the other uio pins as SimChip has them."""

    def __init__(self, targets, **kw):
        super().__init__(**kw)
        self.targets = targets
        self.swd = Board(*targets)

    def _board(self, t, out, oe):
        lvl = super()._board(t, out, oe)
        return (lvl & ~SWD_PINS) | (self.swd(t, out, oe) & SWD_PINS)


def rp2040():
    return [SwdTarget(4, 5, dpidr=swdbench.RP2040_DPIDR, swj=False, dormant=True,
                      targetsel=ts, ap_regs={(0, 0xFC): swdbench.AHB_AP_IDR + (k << 24),
                                             (0, 0x0C): 0x0301754D + (k << 24)})
            for ts, k in RP2040.items()]


def bundle(tmp_path):
    im = assemble_file(FW / "swd_idcode.asm")
    used = [i for i, w in enumerate(im.words) if w]
    (tmp_path / "swd_idcode.json").write_text(json.dumps(
        {"words": im.words[: max(used) + 1], "tape": {str(a): im.tape[a] for a in im.tape_used},
         "cfg": [im.ctx_enable_mask, im.entry_pcs_packed, im.pin_owned_mask,
                 im.pin_owner_packed, im.pin_od_mask, im.ctx_bank_mask, im.ctx_pair_mask]}))


def run(tmp_path, targetsel=swdbench.RP2040_CORE0):
    bundle(tmp_path)
    targets = rp2040()
    chip = SwdChip(targets)
    board = MockBoard(chip, op_ns=1000)
    io = barepico.open_io(machine=GpioMachine(board), time_mod=board.time_mod,
                          sys_hz=swdbench.SYS_HZ)
    lines = []
    r = swdbench.run(bundles=str(tmp_path), io=io, out=lines.append, targetsel=targetsel)
    return r, "\n".join(lines), targets, chip


def test_swd_reads_the_rp2040_through_the_bare_pico(tmp_path):
    """Wake, TARGETSEL core 0, DPIDR, DLPIDR, power-up, the AHB-AP's IDR and the boot ROM's
    word at 0x10: every check passes, and only core 0's DP answered (its own AP values)."""
    r, log, targets, chip = run(tmp_path)
    assert r is not None, log
    assert log.endswith("swd: PASS: the chip read the RP2040's debug port, its AHB-AP and "
                        "its boot ROM"), log
    assert r == {"dpidr": swdbench.RP2040_DPIDR, "dlpidr": 0x00000001, "ctrlstat": 0xF0000000,
                 "ap_idr": swdbench.AHB_AP_IDR, "rom10": 0x0301754D}, log
    assert [t.selected for t in targets] == [True, False, False]
    assert not chip.swd.errors(), chip.swd.errors()


def test_swd_core1_and_a_missing_target(tmp_path):
    """TARGETSEL core 1 reaches core 1's DP (its instance in DLPIDR, its AP values, so the
    checks that name core 0's values fail); a TARGETSEL that names no DP gets no answer."""
    r, log, targets, _ = run(tmp_path, targetsel=0x11002927)
    assert r is not None and r["dlpidr"] == 0x10000001 and r["ap_idr"] == 0x05770031, log
    assert [t.selected for t in targets] == [False, True, False]
    r, log, _, _ = run(tmp_path, targetsel=0x21002927)
    assert r is None and "DP DPIDR: ACK 7, want 1" in log, log
