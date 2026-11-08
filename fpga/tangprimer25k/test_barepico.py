"""barepico.py under CPython: bringup.py's stages through barepico's GPIO mapping, on a
simulated `machine` (SIO GPIO registers, Pin, PWM, time_pulse_us, UART) whose pins are
wired to the simulated chip of sw/tests/demoboard_mock.py, the model test_bringup.py
uses. Checks the wiring table end to end: a swapped or shifted bit fails stage 2 or 4.

    PYTHONPATH=<repo>/sw python -m pytest test_barepico.py   (REPO env: the repo root)
"""
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(os.environ.get("REPO", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(REPO / "sw" / "rp2350"))
sys.path.insert(0, str(REPO / "sw" / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import barepico  # noqa: E402
import bringup  # noqa: E402
import make_bundle  # noqa: E402
from demoboard_mock import MockBoard, MockMachine, MockUART, SimChip  # noqa: E402

UIO = barepico.UIO_GPIO


class GpioMachine(MockMachine):
    """MicroPython's machine module at the GPIO level: one GPIO_OUT word, per-pin mode and
    pull, PWM on GP0; every register access costs RP time like a byte function."""

    def __init__(self, board, sys_hz=125_000_000):
        super().__init__(board)
        self.out = 0
        self.mode = {}
        self.pull = {}
        self.sys_hz = sys_hz
        self.pwm_hz = 0
        self.mem32 = _Mem32(self)
        m = self

        class Pin:
            IN, OUT, PULL_UP = 0, 1, 1

            def __init__(self, gpio, mode=-1, pull=-1, value=None):
                self.gpio = gpio
                self.uio = UIO.index(gpio) if gpio in UIO else None
                self.init(mode, pull, value=value)

            def init(self, mode=-1, pull=-1, value=None):
                if value is not None:
                    m.set_bit(self.gpio, value)
                if mode != -1:
                    m.mode[self.gpio] = mode
                if pull != -1:
                    m.pull[self.gpio] = pull
                m.sync()

            def value(self, v=None):
                if v is None:
                    return (m.read_in() >> self.gpio) & 1
                m.set_bit(self.gpio, v)
                m.sync()

        class PWM:
            def __init__(self, pin):
                assert pin.gpio == barepico.CLK_GPIO
                self.hz = 0

            def freq(self, hz=None):
                if hz is None:
                    return self.hz
                self.hz = m.sys_hz / max(1, round(m.sys_hz / hz))   # a whole divider
                m.board.sync()
                m.board.chip.set_pwm(self.hz)

            def duty_u16(self, d):
                pass

            def deinit(self):
                m.board.sync()
                m.board.chip.set_pwm(0)

        self.Pin, self.PWM = Pin, PWM

    def freq(self, hz=None):
        if hz is None:
            return self.sys_hz
        self.sys_hz = hz

    def set_bit(self, gpio, v):
        self.out = (self.out | (1 << gpio)) if v else (self.out & ~(1 << gpio))

    def sync(self):
        """Pin levels after a change: ui_in, reset and the uio pads."""
        b, chip = self.board, self.board.chip
        b.sync()
        chip.set_ui((self.out >> barepico.UI_SHIFT) & 0xFF)
        b.ui_driven = (self.out >> barepico.UI_SHIFT) & 0xFF
        if self.mode.get(barepico.RST_GPIO) == self.Pin.OUT:
            chip.set_reset(not (self.out >> barepico.RST_GPIO) & 1)
        pads = chip.pads
        pads.oe = pads.value = pads.pullup = 0
        for k, g in enumerate(UIO):
            if self.mode.get(g) == self.Pin.OUT:
                pads.oe |= 1 << k
            pads.value |= ((self.out >> g) & 1) << k
            if self.pull.get(g) == self.Pin.PULL_UP:
                pads.pullup |= 1 << k

    def read_in(self):
        chip = self.board.chip
        g = ((self.out >> barepico.UI_SHIFT) & 0xFF) << barepico.UI_SHIFT
        g |= chip.uo() << barepico.UO_SHIFT
        u = chip.uio()
        for k, gp in enumerate(UIO):
            g |= ((u >> k) & 1) << gp
        g |= self.out & (1 << barepico.RST_GPIO)
        return g

    def UART(self, uart_id, baudrate, rx, tx, timeout=0):
        # RP2040 and RP2350 alike here: UART1 RX on GP9, TX on GP8
        assert (rx.gpio, tx.gpio, uart_id) == (9, 8, 1), (rx.gpio, tx.gpio, uart_id)
        u = MockUART.__new__(MockUART)
        u.board, u.taken = self.board, 0
        u.rx_dev = self.board.chip.attach_uart(UIO.index(rx.gpio), baudrate)
        return u


class _Mem32:
    def __init__(self, m):
        self.m = m

    def __getitem__(self, a):
        b = self.m.board
        b.cost(b.op_ns)
        if a == barepico.SIO_GPIO_IN:
            return self.m.read_in()
        if a == barepico.SIO_GPIO_OUT:
            return self.m.out
        raise KeyError(hex(a))

    def __setitem__(self, a, v):
        assert a == barepico.SIO_GPIO_OUT, hex(a)
        b = self.m.board
        b.cost(b.op_ns)
        self.m.out = v & 0x3FFFFFFF
        self.m.sync()


@pytest.fixture(scope="module")
def bundles(tmp_path_factory):
    d = tmp_path_factory.mktemp("bundles")
    for fw in sorted((REPO / "firmware").glob("*.asm")):
        if fw.name not in make_bundle.SKIP:
            (d / (fw.stem + ".json")).write_text(json.dumps(make_bundle.bundle(fw)))
    return d


def run(bundle_dir, chip=None, sys_hz=100_000_000, **kw):
    board = MockBoard(chip or SimChip())
    gm = GpioMachine(board)
    io = barepico.open_io(machine=gm, time_mod=board.time_mod, sys_hz=sys_hz)
    lines = []
    ok = bringup.run(bundles=str(bundle_dir), io=io, out=lines.append, quick=True, **kw)
    return ok, bringup.LAST.results, "\n".join(lines), gm


def test_every_stage_passes_through_the_bare_pico_wiring(bundles):
    ok, results, log, gm = run(bundles, optional=True)
    assert ok, log
    assert [r[0] for r in results] == [0, 1, 2, 3, 4, 5, 6], results
    assert gm.sys_hz == 100_000_000


def test_byte_functions_follow_the_wiring_table():
    board = MockBoard(SimChip())
    gm = GpioMachine(board)
    io = barepico.open_io(machine=gm, time_mod=board.time_mod)
    io.write_ui(0xA5)
    assert (gm.out >> 1) & 0xFF == 0xA5 and gm.out & 1 == 0
    assert io.read_ui() == 0xA5
    assert barepico._uio_byte(1 << 9) == 0x01
    assert [barepico._uio_byte(1 << g) for g in (18, 19, 20, 21, 22, 26, 27)] == \
        [0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80]
    assert barepico._uio_byte(~((1 << 9) | (0x1F << 18) | (3 << 26)) & 0x3FFFFFFF) == 0


@pytest.mark.parametrize("sys_hz, hz, got", [(125_000_000, 25_000_000, 25_000_000),
                                             (100_000_000, 50_000_000, 50_000_000),
                                             (125_000_000, 50_000_000, 62_500_000)])
def test_clock_reports_what_the_divider_makes(sys_hz, hz, got):
    """A whole divider of the system clock: 50 MHz needs a system clock it divides (the
    stage 1 clock check then measures what really runs)."""
    board = MockBoard(SimChip())
    gm = GpioMachine(board, sys_hz=sys_hz)
    io = barepico.open_io(machine=gm, time_mod=board.time_mod)
    assert io.set_clock(hz) == got


def test_a_swapped_wire_is_caught(bundles):
    """MISO and IRQ swapped on the board (uo_out[0] and [1]): stage 2 fails."""
    class Swapped(SimChip):
        def uo(self):
            v = super().uo()
            return (v & ~3) | ((v & 1) << 1) | ((v >> 1) & 1)
    ok, results, log, _ = run(bundles, chip=Swapped(), stages=(0, 1, 2))
    assert not ok and results[-1][0] == 2, log
