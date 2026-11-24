"""The learning bench end to end on the simulated chip: a real PT2262 remote's recording
(sigrok-dumps) played onto uio[2], the chip's capture of it learned on the "laptop", the
generated receiver hearing the playback and the generated transmitter sending a word the
remote never sent, judged by the learned model.

    PYTHONPATH=../../sw python -m pytest -q test_learnbench.py
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
import learnbench  # noqa: E402
import learnhost  # noqa: E402
import make_bundle  # noqa: E402
from demoboard_mock import MockBoard, SimChip  # noqa: E402
from test_barepico import GpioMachine  # noqa: E402

REC = HERE.parents[1] / "sw" / "tests" / "data" / "sigrok" / "pt2262_abcd.sr"
NEW_WORD = 0x0A8888            # an address the recording never has


class FakePlayer:
    """What the PIO does, on the simulated chip: the pin follows the segments from the chip
    tick the play starts at (an external source pulls it low; the RP's pull-up makes 1)."""

    def __init__(self, chip):
        self.chip = chip
        chip.sources = [lambda t: (1 << learnbench.SRC, 0)]      # PIO's OUT_LOW: idle low

    def play(self, segs, io):
        io.sleep_us(1)                                           # sync the chip to now
        t0, edges, t = self.chip.m.now, [], 0
        for lv, us in segs:
            edges.append((t, lv))
            t += int(us) * 25
        end = t0 + t

        def level(tick):
            if tick >= end:
                return 1 << learnbench.SRC, 0
            lv = 0
            for e, v in edges:
                if tick - t0 >= e:
                    lv = v
                else:
                    break
            return 1 << learnbench.SRC, lv << learnbench.SRC
        self.chip.sources = [level]
        io.sleep_us(t // 25 + 100)


@pytest.fixture(scope="module")
def bench(tmp_path_factory):
    d = tmp_path_factory.mktemp("learn")
    bundles, learn = d / "bundles", d / "learn"
    bundles.mkdir()
    learn.mkdir()
    b = make_bundle.bundle(make_bundle.ROOT / "firmware" / "logic_analyzer.asm")
    (bundles / "logic_analyzer.json").write_text(json.dumps(b))
    learnhost.source(REC, learn / "source.json", nframes=4)
    chip = SimChip()
    board = MockBoard(chip, op_ns=1000)
    io = barepico.open_io(machine=GpioMachine(board), time_mod=board.time_mod,
                          sys_hz=learnbench.SYS_HZ)
    return {"io": io, "chip": chip, "bundles": str(bundles), "learn": learn}


def test_capture_learn_receive_transmit(bench):
    io, chip, learn = bench["io"], bench["chip"], bench["learn"]
    kw = {"io": io, "player": FakePlayer(chip), "bundles": bench["bundles"], "learn": str(learn),
          "out": lambda *a: None}
    words = learnbench.capture(**kw)
    # the line's level at START (uio[2] already low, PIO's OUT_LOW), then 4 frames of 50 edges
    assert len(words) == 201, len(words)
    assert not (words[0] >> (24 + learnbench.SRC)) & 1
    # the laptop: learned from the chip's capture, both programs certified
    model, res = learnhost.learn_step(learn / "capture.json", learn, source_doc=learn / "source.json")
    assert set(res) == {"tx", "rx"}
    assert all(c[1] == "CHECKED" for k in res for c in res[k]["cert"])
    lines = []
    kw["out"] = lines.append
    assert learnbench.receive(rounds=1, **kw), "\n".join(lines)
    marks = learnbench.transmit(NEW_WORD, **kw)
    # two frames: the first starts before the pushes return, so its head may go untimed (as
    # on the bench); the second is whole, its 25 marks after the gap between frames
    gaps = [i for i in range(1, len(marks)) if marks[i][0] - marks[i - 1][1] > 5000]
    assert len(gaps) == 1 and len(marks) - gaps[0] == 25, marks
    assert learnhost.judge(learn / "capture.json", learn / "sent.json", NEW_WORD)
