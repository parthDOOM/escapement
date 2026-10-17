"""sw/rp2350/bringup.py against the RTL of the whole chip (tb_bringup.v).

The board side is sw/tests/demoboard_mock.MockBoard, as in sw/tests/test_bringup.py; the
chip is the RTL, driven through its pins. bringup.py is synchronous MicroPython-style
code, so it runs in a cocotb bridge thread, and the chip backend below resumes the
simulator whenever the script reads a pin. Pin writes are queued with their RP time and
played back at those times (offset from the clock edges), so the chip sees the same
waveform as from a board whose pin write takes `op_ns`.

The bundle subset (BRINGUP_FIRMWARE, default four firmware covering halt, capture, open-
drain pins and two contexts in a steady state) keeps the run to minutes; set
BRINGUP_FIRMWARE=all for every firmware.
"""

import json
import os
import tempfile
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.simtime import get_sim_time
from cocotb.task import bridge, resume
from cocotb.triggers import First, RisingEdge, Timer

import bringup
import make_bundle
from demoboard_mock import MockBoard
from escmodels import UartRx

ROOT = Path(__file__).resolve().parents[2]
PERIOD = 40                      # ns, 25 MHz
DEFAULT_FW = ("uart_tx.asm", "logic_analyzer.asm", "i2c_ctrl.asm", "combo_uart_i2c.asm")


def _now_ns():
    """Simulation time in whole ns (the steps are ps; a float ns can be off by 1e-8)."""
    return get_sim_time("step") // 1000


def build_bundles(names, edit=None):
    d = Path(tempfile.mkdtemp(prefix="bundles_"))
    for fw in sorted((ROOT / "firmware").glob("*.asm")):
        if fw.name in make_bundle.SKIP or (names != "all" and fw.name not in names):
            continue
        b = make_bundle.bundle(fw)
        if edit:
            edit(b)
        (d / (fw.stem + ".json")).write_text(json.dumps(b))
    return d


class RtlPads:
    """The RP side of uio[7:0]; every change is queued for the simulator."""

    def __init__(self, chip):
        object.__setattr__(self, "_chip", chip)
        object.__setattr__(self, "_v", {"oe": 0, "value": 0, "pullup": 0})

    def __getattr__(self, name):
        return self.__dict__["_v"][name]

    def __setattr__(self, name, v):
        self._v[name] = v
        self._chip.events.append((self._chip.t_now, "pads", dict(self._v)))


class RtlChip:
    """demoboard_mock backend on the RTL. RP time t (ns) is simulation time t0 + t; pin
    changes are applied at the next 40k+10 ns, away from the clock edges at 0 and 20."""

    def __init__(self, dut):
        self.dut = dut
        self.events = []
        self.t_now = 0
        self.t0 = 0
        self.pads = RtlPads(self)
        self.uarts = []
        self.contention = []
        self.pwm = 0

    async def start(self):
        self.t0 = _now_ns()
        cocotb.start_soon(Clock(self.dut.clk, PERIOD, unit="ns").start())
        cocotb.start_soon(self._watch_contention())

    async def _watch_contention(self):
        while True:
            await self.dut.contention.value_change
            v = int(self.dut.contention.value)
            if v:
                self.contention.append((_now_ns(), v))

    # ---- queueing (called from the bring-up thread)
    def set_pwm(self, hz):
        self.pwm = hz                         # the testbench clock always runs at 25 MHz

    def set_reset(self, hold):
        self.events.append((self.t_now, "rst", 0 if hold else 1))

    def set_ui(self, v):
        self.events.append((self.t_now, "ui", v & 0xFF))

    def advance(self, t_ns):
        self.t_now = max(self.t_now, t_ns)
        if len(self.events) > 4096:
            self.flush()

    def flush(self):
        resume(self._play)()

    def uo(self):
        return resume(self._read)("uo_out")

    def uio(self):
        return resume(self._read)("uio_level")

    def wait_uio(self, pin, level, t_limit_ns):
        return resume(self._wait)(pin, level, t_limit_ns)

    def attach_uart(self, pin, baud):
        return resume(self._attach)(pin, baud)

    def detach_uart(self, u):
        self.uarts.remove(u)

    # ---- simulator side
    async def _until(self, t):
        g = ((t - 10 + PERIOD - 1) // PERIOD) * PERIOD + 10
        now = _now_ns()
        if self.t0 + g > now:
            await Timer(self.t0 + g - now, unit="ns")

    async def _play(self):
        d = self.dut
        for t, kind, v in self.events:
            await self._until(t)
            if kind == "ui":
                d.ui_in.value = v
            elif kind == "rst":
                d.rst_n.value = v
            else:
                d.rp_oe.value, d.rp_val.value, d.rp_pull.value = v["oe"], v["value"], v["pullup"]
        self.events.clear()
        await self._until(self.t_now)
        await Timer(1, unit="ns")             # let this step's writes settle before reading

    async def _read(self, sig):
        await self._play()
        return int(getattr(self.dut, sig).value)

    async def _wait(self, pin, level, t_limit):
        await self._play()
        sig = self.dut.uio_level
        while True:
            now = _now_ns()
            if (int(sig.value) >> pin) & 1 == level:
                self.t_now = now - self.t0
                return self.t_now
            left = self.t0 + t_limit - now
            if left <= 0:
                self.t_now = now - self.t0
                return None
            await First(sig.value_change, Timer(left, unit="ns"))

    async def _attach(self, pin, baud):
        await self._play()
        u = UartRx(pin, 25e6 / baud)
        self.uarts.append(u)
        cocotb.start_soon(self._uart(u))
        return u

    async def _uart(self, u):
        t = 0
        while u in self.uarts:
            await RisingEdge(self.dut.clk)
            u.step(t, int(self.dut.uio_level.value))
            t += 1


async def run_bringup(dut, bundle_dir, **kw):
    chip = RtlChip(dut)
    await chip.start()
    board = MockBoard(chip, op_ns=400)
    lines = []

    def out(s):
        lines.append(s)
        dut._log.info(s)

    def body():
        ok = bringup.run(bundles=str(bundle_dir), io=board.io(bringup), out=out, **kw)
        return ok, bringup.LAST.results

    ok, results = await bridge(body)()
    return ok, results, "\n".join(lines), chip


@cocotb.test()
async def test_bringup_passes_on_rtl(dut):
    names = os.environ.get("BRINGUP_FIRMWARE", ",".join(DEFAULT_FW))
    names = "all" if names == "all" else tuple(names.split(","))
    d = build_bundles(names)
    n = len(list(d.glob("*.json")))
    ok, results, log, chip = await run_bringup(dut, d, quick=True, optional=True)
    assert ok, log
    assert [r[0] for r in results] == [0, 1, 2, 3, 4, 5, 6], log
    assert f"{n} of {n} firmware match the model" in log, log
    assert not chip.contention, chip.contention[:4]


@cocotb.test()
async def test_stale_bundle_fails_on_rtl(dut):
    def corrupt(b):
        b["expect"]["digest"] ^= 0x80000000
    d = build_bundles(("uart_tx.asm",), corrupt)
    ok, results, log, chip = await run_bringup(dut, d, quick=True, stages=(5,))
    assert not ok and results[-1][:2] == (5, False), log
    assert "1 of 1 firmware differ" in results[-1][2], log
