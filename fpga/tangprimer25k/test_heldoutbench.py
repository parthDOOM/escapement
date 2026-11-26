"""heldoutbench.py and heldoutjudge.py without the hardware.

The Pico W's side runs through the bare Pico's wiring on the simulated chip (demoboard_mock,
as in test_swdbench.py). The judge is given captures made from the ISS's pins as the
analyser would record them: resampled at its rate, starting part of a sample late, with its
crystal 80 ppm off the chip's. Each capture must pass and each control must fail. The S/PDIF,
Microwire and M-Bus judges also run sigrok, so those tests are skipped where neither this
machine nor WSL has sigrok-cli.

    PYTHONPATH=../../sw python -m pytest -q test_heldoutbench.py
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parents[1] / "sw", HERE.parents[1] / "sw" / "tests",
          HERE.parents[1] / "sw" / "rp2350", HERE.parents[1] / "test" / "cosim"):
    sys.path.insert(0, str(p))

import barepico  # noqa: E402
import heldoutbench  # noqa: E402
import heldoutjudge as J  # noqa: E402
from demoboard_mock import MockBoard, SimChip  # noqa: E402
from escasm import assemble, assemble_file  # noqa: E402
from test_barepico import GpioMachine  # noqa: E402


def _have_sigrok() -> bool:
    if shutil.which("sigrok-cli"):
        return True
    try:
        r = subprocess.run(["wsl.exe", "-e", "sigrok-cli", "--version"], capture_output=True,
                           timeout=60)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


needs_sigrok = pytest.mark.skipif(not _have_sigrok(), reason="no sigrok-cli here or in WSL")


def resample(levels: list[int], tick_ns: float, t0: float, dt: float, n: int) -> list[int]:
    """`n` samples of per-tick `levels`, the first at `t0` ns and then every `dt` ns."""
    return [levels[min(int((t0 + k * dt) // tick_ns), len(levels) - 1)] for k in range(n)]


def fake(ref: dict, plan: dict, pre: int, post: int, frac=0.37, ppm=80.0) -> dict:
    """The ISS's pins as the analyser records them for `plan`: `pre` samples before the
    trigger edge and `post` after, its clock `ppm` fast, its first sample `frac` of a sample
    late."""
    tch, falling = plan["trig"]
    ri = J.trigger_index(ref[plan["pins"][tch]], falling, J.T0)
    dt = 1e9 / plan["rate"] / (1 + ppm * 1e-6)
    t0 = ri * J.TICK_NS - (pre - frac) * dt
    return {n: resample(ref[p], J.TICK_NS, t0, dt, pre + post)
            for n, p in zip(plan["names"], plan["pins"])}


def pico(tmp_path, bundle: dict):
    """A HeldoutBench on the bare Pico's wiring to the simulated chip, `bundle` its only one."""
    (tmp_path / f"{bundle['name'].removesuffix('.asm')}.json").write_text(json.dumps(bundle))
    board = MockBoard(SimChip(), op_ns=1000)
    io = barepico.open_io(machine=GpioMachine(board), time_mod=board.time_mod,
                          sys_hz=heldoutbench.SYS_HZ)
    lines = []
    return heldoutbench.HeldoutBench(io, bundles=str(tmp_path), out=lines.append), lines


def make(name: str, overrides: dict | None = None) -> dict:
    from make_bundle import bundle
    return bundle(J.FW / f"{name}.asm", name, overrides)


# ------------------------------------------------------------------ the Pico W's side

@pytest.mark.parametrize("name,words,want", [("snes_pad", [1], [0]),
                                             ("biss_c", [1], [2, 0])])
def test_requests_through_the_bare_pico(tmp_path, name, words, want):
    """The replies the judge expects with nothing on the bus: for SNES, nothing pressed and
    not connected; for BiSS-C, no ACK and no data. Then STOP with no fault and no tripwire."""
    b, lines = pico(tmp_path, make(name))
    b.setup(name)
    assert b.request(words, len(want)) == want, lines
    assert b.finish() == (0, 0)


def test_mbus_master_through_the_bare_pico(tmp_path):
    """The Pico W as the M-Bus master against the slave on the simulated chip: the address
    and the response given, then SND_NKE and REQ_UD2 to address 5. The slave's notes to its
    host name both. It runs at 9600 baud on a 4 MHz chip, the fastest pair the slave allows
    (400 ticks a bit at least), to keep the simulation short."""
    b, lines = pico(tmp_path, make("mbus_slave", {"CLK_KHZ": 4000, "BAUD": 9600}))
    b.mbus_prepare(J.MB_HOST, f_chip=4_000_000)
    notes = b.mbus_master(J.MB_TELEGRAMS[:2], baud=9600, wait_ms=40)
    b.mbus_release()
    assert notes == J.MB_NOTES, ([hex(n) for n in notes], lines)
    assert b.finish() == (0, 0)


# ------------------------------------------------------------------ the judges

@pytest.mark.parametrize("name", ["snes_pad", "biss_c", "ps2_host", "dht22"])
def test_judge_passes_the_iss_capture_and_refuses_its_controls(name):
    """The ISS's own run passes; one pulse inverted, and a chip clock off by enough to move
    the last edge 4 samples, are refused."""
    p = J.PLANS[name]
    rate, pre, post = p["rate"], p["pre"], p["post"]
    ref, replies = J.iss(name, p["words"], post * 1e9 / rate + 5e5, p.get("tape"))
    cap = fake(ref, p, pre, post)
    assert J.judge_capture(name, cap, p, rate, pre, ref, replies, replies) == []
    assert J.judge_capture(name, J.control(cap), p, rate, pre, ref, replies, replies)
    ri = J.trigger_index(ref[p["pins"][p["trig"][0]]], p["trig"][1], J.T0)
    last = max(t for pin in p["pins"] for t, _ in J.edges(ref[pin], J.TICK_NS, ri))
    ppm = 4 * 1e9 / rate / (last - ri * J.TICK_NS) * 1e6
    slow = fake(ref, p, pre, post, ppm=ppm)
    assert J.judge_capture(name, slow, p, rate, pre, ref, replies, replies), ppm


def test_judge_refuses_wrong_replies():
    p = J.PLANS["biss_c"]
    ref, replies = J.iss("biss_c", p["words"], p["post"] * 1e9 / p["rate"] + 5e5)
    cap = fake(ref, p, p["pre"], p["post"])
    bad = J.judge_capture("biss_c", cap, p, p["rate"], p["pre"], ref, [1, 0], replies)
    assert any("replies" in b for b in bad), bad


@needs_sigrok
def test_spdif_judge(tmp_path):
    """As on the bench: the stream runs long before the capture, which starts mid-stream; one
    word is pushed and the marker (the trigger) follows 150 us later; the capture keeps
    640 us before the trigger and 7.2 ms after, at 12.5 MS/s. The word's two samples decode
    as valid, before the trigger, and every other sub-frame as a zero not valid. The control
    (a pulse of the valid sub-frame inverted) and a different word are refused."""
    from escmodels import Board, HostScript
    from esciss import Machine
    p = J.PLANS["spdif_tx"]
    w = p["words"][0]
    push = 120_000                                   # the word, mid-stream
    mark = push + 150_000 // J.TICK_NS               # the marker, after the push returns
    host = HostScript([(push, 0, w)])
    board = Board(host)
    m = Machine(assemble_file(J.FW / "spdif_tx.asm"), board=board)
    host.machine = m
    m.run(mark + 7_300_000 // J.TICK_NS, stop_when_idle=False)
    dt = 1e9 / p["rate"] / (1 + 80e-6)
    t0 = mark * J.TICK_NS - (p["pre"] - 0.37) * dt
    line = resample(board.level(0), J.TICK_NS, t0, dt, p["pre"] + p["post"])
    bad, notes, at = J.spdif(line, p["rate"], [w], tmp_path)
    assert bad == [], (bad, notes)
    assert notes.startswith("2 valid samples"), notes
    assert p["pre"] - 150_000 / dt <= at <= p["pre"], at
    assert J.spdif(J.pulse_flipped(line, at), p["rate"], [w], tmp_path)[0]
    assert J.spdif(line, p["rate"], [p["words"][1]], tmp_path)[0]


@needs_sigrok
def test_microwire_merge_and_decode(tmp_path):
    """Each command's two captures, (CS, SK) and (DI, SK), at different offsets and clock
    errors as two bench runs would be, each judged against the ISS, then merged on SK and
    decoded: EWEN, WRITE 0x05 0x1234, READ 0x05 (0xFFFF from the pull-up). A DI pulse of
    the write inverted is refused."""
    p = J.PLANS["microwire_93c46"]
    pb = dict(p, **J.MW_B)
    rate, pre, post = p["rate"], p["pre"], p["post"]
    merged = []
    for _, w in p["cmds"]:
        ref, replies = J.iss("microwire_93c46", [w], post * 1e9 / rate + 1e6)
        a = fake(ref, p, pre, post, frac=0.2)
        b = fake(ref, pb, pre, post, frac=0.7, ppm=-40.0)
        assert J.judge_capture("microwire_93c46", a, p, rate, pre, ref, replies, replies) == []
        assert J.judge_capture("microwire_93c46", b, pb, rate, pre, ref, replies, replies) == []
        m, why = J.merge_microwire(a, b)
        assert why is None, why
        merged.append(m)
    assert J.microwire(merged, rate, tmp_path) == []
    ctl = list(merged)
    ctl[1] = dict(ctl[1], si=J.flipped(ctl[1]["si"], 0.7))
    assert J.microwire(ctl, rate, tmp_path)


@needs_sigrok
def test_mbus_judge(tmp_path):
    """The judge on the slave's ISS run against the M-Bus master model (escmodels.mbus, which
    knows nothing of the judge) sending the bench's four telegrams, at 9600 baud on a 4 MHz
    chip, passes. Refused: a reply pulse inverted, a missing note, and an E5h where none is
    due (after the fourth telegram, the one with a bad checksum)."""
    from escmodels import Board, HostScript, MbusMaster
    from esciss import Machine
    baud, khz = 9600, 4000
    tick = 1e6 / khz
    # the host words 2,000 ticks apart: the slave looks at its mailbox every 4 bit times
    host = HostScript([(200 + 2000 * k, 0, w) for k, w in enumerate(J.MB_HOST)], drain=[0],
                      poll=200)
    master = MbusMaster(0, 1, tick_ns=tick, baud=baud, slave_address=J.MB_ADDR,
                        start_ms=15.0, telegrams=[bytes(t) for t in J.MB_TELEGRAMS])
    board = Board(master, host)
    src = f".equ CLK_KHZ, {khz}\n.equ BAUD, {baud}\n" + (J.FW / "mbus_slave.asm").read_text()
    m = Machine(assemble(src, base_dir=J.FW, name="mbus_slave.asm"), board=board)
    host.machine = m
    m.run(300 * khz, stop_when_idle=False)
    notes = host.received[0]
    assert notes == J.MB_NOTES, [hex(n) for n in notes]
    rate = baud * 21
    lv0, lv1 = board.level(0), board.level(1)
    dt = 1e9 / rate / (1 + 80e-6)
    t0 = J.trigger_index(lv0, True, 1) * tick - (100 - 0.37) * dt
    n = int((len(lv0) * tick - t0) / dt) - 1
    cap = {"m2s": resample(lv0, tick, t0, dt, n), "s2m": resample(lv1, tick, t0, dt, n)}
    bad, info = J.mbus(cap, rate, baud, notes, tmp_path)
    assert bad == [], (bad, info)
    assert J.mbus(dict(cap, s2m=J.flipped(cap["s2m"])), rate, baud, notes, tmp_path)[0]
    assert J.mbus(cap, rate, baud, notes[:1], tmp_path)[0]
    s2m = list(cap["s2m"])
    first = next(i for i in range(1, n) if s2m[i] != s2m[i - 1])      # the E5h's start bit
    e5 = s2m[first - 2:first + 11 * 21]
    last = max(i for i in range(1, n) if cap["m2s"][i] != cap["m2s"][i - 1])
    at = last + 60 * 21                                               # 60 bit times later
    assert at + len(e5) < n
    s2m[at:at + len(e5)] = e5
    assert J.mbus(dict(cap, s2m=s2m), rate, baud, notes, tmp_path)[0]
