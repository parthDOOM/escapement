"""usbbench.py under CPython: firmware/usb_hid.asm at 24 MHz on the simulated chip of
sw/tests/demoboard_mock.py, behind barepico's wiring (test_barepico.GpioMachine), with
escmodels.usb's host on D+ and D- (uio[1:0]). The host enumerates the keyboard as Linux does
and polls endpoint 1; every report usbbench pushes must reach it in order and type the text
back, with no host error (EOP, stuffing, CRC, turnaround) and no fault.

    PYTHONPATH=<repo>/sw python -m pytest test_usbbench.py   (REPO env: the repo root)
"""
import json
import os
import random
import sys
from pathlib import Path

import pytest

REPO = Path(os.environ.get("REPO", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(REPO / "sw" / "rp2350"))
sys.path.insert(0, str(REPO / "sw" / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import barepico  # noqa: E402
import escapement_demo  # noqa: E402
import usbbench  # noqa: E402
from demoboard_mock import MockBoard, SimChip  # noqa: E402
from escasm import assemble_file  # noqa: E402
from escmodels.usb import UsbHost  # noqa: E402
from test_barepico import GpioMachine  # noqa: E402
from test_usb_hid import check_descriptors, enumerate_device, poll_reports, typed  # noqa: E402


class UsbChip(SimChip):
    """D+ and D- (uio[1:0]) on a UsbHost, which starts its script when the chip starts;
    the chip's per-tick pin record off."""

    def __init__(self, script, **kw):
        super().__init__(**kw)
        self.script, self.usb = script, None

    def _start(self):
        self.usb = UsbHost()                     # the Machine reads the pins as it is made
        super()._start()
        self.m.record = False
        self.usb.start(self.script)

    def _board(self, t, out, oe):
        lvl = super()._board(t, out, oe)
        return (lvl & ~3) | (self.usb(t, out, oe) & 3)


@pytest.fixture(scope="module")
def bundles(tmp_path_factory):
    """usb_hid's bundle as make_bundle writes it, without the model prediction usbbench
    does not read."""
    im = assemble_file(REPO / "firmware" / "usb_hid.asm")
    used = [i for i, w in enumerate(im.words) if w]
    b = {"name": "usb_hid.asm", "words": im.words[: max(used) + 1],
         "tape": {str(a): im.tape[a] for a in im.tape_used},
         "cfg": [im.ctx_enable_mask, im.entry_pcs_packed, im.pin_owned_mask,
                 im.pin_owner_packed, im.pin_od_mask, im.ctx_bank_mask, im.ctx_pair_mask]}
    d = tmp_path_factory.mktemp("bundles")
    (d / "usb_hid.json").write_text(json.dumps(b))
    return d


def bench(bundles, script, text, sys_hz=usbbench.SYS_HZ, **kw):
    chip = UsbChip(script)
    board = MockBoard(chip, op_ns=1000)
    io = barepico.open_io(machine=GpioMachine(board), time_mod=board.time_mod, sys_hz=sys_hz)
    lines = []
    # attach=None: the mock's bus idles at J from the start (the pull-up on 3V3)
    n = usbbench.run(text, attach=None, bundles=str(bundles), io=io, out=lines.append, **kw)
    return n, chip, "\n".join(lines)


def test_types_through_the_bare_pico(bundles):
    """Enumeration with exact descriptors, then every report typed in order, repeated
    characters included ("ll")."""
    text = "Hello 42\n"
    reports = escapement_demo.key_reports(text)
    got, desc = [], {}

    def script(h):
        yield from enumerate_device(h, desc)
        yield from poll_reports(h, len(reports), random.Random(3), got)

    n, chip, log = bench(bundles, script, text, enumerate_ms=20, hold_ms=20)
    assert n == len(reports), log
    check_descriptors(desc)
    assert got == reports and typed(got) == text, log
    assert chip.usb.errors == [], chip.usb.errors[:5]
    assert chip.timing_violations == 0
    assert "typed 9 characters in 18 key reports, no fault" in log


def test_not_enumerated_is_reported(bundles):
    """No host traffic (the bus idles at J, as the D- pull-up holds it): the mailbox stays
    full after the first report is staged, and usbbench says the computer is not polling."""
    def script(h):
        yield ("idle", 1 << 40)

    n, chip, log = bench(bundles, script, "abc", enumerate_ms=5, timeout_ms=30)
    assert n == -1 and "nothing is polling the keyboard" in log, log
    assert chip.usb.errors == [], chip.usb.errors[:5]


def test_wrong_clock_is_refused(bundles):
    """At the RP's default 125 MHz the PWM cannot make 24 MHz: usbbench stops."""
    def script(h):
        yield ("idle", 1 << 40)

    n, _, log = bench(bundles, script, "a", sys_hz=125_000_000)
    assert n == -1 and "not 24 MHz" in log, log
