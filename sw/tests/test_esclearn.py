"""Protocols from recordings (sw/esclearn): a recording in, a timing table out, firmware
regenerated from it on the ISS and compared mark by mark with the recording.

Recordings come from our own firmware on the ISS (escgen's NEC and WS2812 tables) and
from a remote built from the NEC specification (escmodels.nec.NecSource: TSOP-style
demodulated output with repeat codes, or the raw 38 kHz carrier), with oscillator
error, jitter, coarse sampling, glitches and cut frames. Noise must be refused, never
modelled.
"""

from __future__ import annotations

import random

import pytest
import yaml

import escgen
import esclearn
from esciss import Machine
from esclearn import LearnError, check, learn
from esclearn.__main__ import main as cli
from escmodels import Board, HostScript
from escmodels.nec import NecSource, nec_raw
from esctape.export import write_vcd

NEC = [nec_raw(0x04, 0x08), nec_raw(0x10, 0xA5)]
SLOT = 108_000 * 25                        # 108 ms in ticks
COLOURS = [0x00FF00, 0x123456, 0xABCDEF]


def rev(v, n):
    """The word esclearn reports for n bits sent MSB first (it numbers bits as sent)."""
    return int(f"{v:0{n}b}"[::-1], 2)


def from_marks(marks, active_low=False, carrier=None):
    """Records of one pin from (start, end) marks in ticks; with carrier = (period,
    high), each mark is a burst of pulses (active high)."""
    idle = 1 if active_low else 0
    rec = [(0, idle)]
    for a, b in marks:
        if carrier:
            p, h = carrier
            t = a
            while t < b:
                rec += [(round(t), 1), (round(min(t + h, b)), 0)]
                t += p
        else:
            rec += [(round(a), 1 - idle), (round(b), idle)]
    return rec


def remote(items, **kw):
    """Marks of a NEC remote from the specification."""
    return NecSource(0, items, **kw).marks


def tsop(items, **kw):
    return from_marks(remote(items, **kw), active_low=True)


def jitter(records, frac, seed=1):
    """Every run stretched or shrunk by up to frac."""
    rng = random.Random(seed)
    out, t = [records[0]], records[0][0]
    for (t0, _), (t1, v) in zip(records, records[1:]):
        t += max(1, round((t1 - t0) * rng.uniform(1 - frac, 1 + frac)))
        out.append((t, v))
    return out


def quantise(records, step):
    """As sampled every `step` ticks: each edge moves to the next sample."""
    out = []
    for t, v in records:
        t = -(-t // step) * step
        if out and out[-1][0] == t:
            out[-1] = (t, v)
        elif not out or out[-1][1] != v:
            out.append((t, v))
    return out


def iss(table, ticks, clk_khz=25000, pushes=(), tape=None):
    """Records of bank A from table's firmware on the ISS, in 25 MHz ticks."""
    _, image = escgen.build(table, CLK_KHZ=clk_khz)
    for i, w in enumerate(tape or []):
        image.tape[i] = w
    host = HostScript(list(pushes))
    pull = sum(1 << int(q["pin"]) for q in table["pins"].values() if int(q.get("idle", 0)))
    board = Board(host, pullups=pull)                  # the pins start at their idle levels
    m = Machine(image, board=board)
    host.machine = m
    m.run(ticks, stop_when_idle=False)
    scale = 25000 / clk_khz
    return [(round(i * scale), v) for i, v in enumerate(board.history)]


@pytest.fixture(scope="module")
def nec_iss():
    """escgen's NEC table on the ISS at 2.5 MHz, two frames from the mailbox."""
    slot = SLOT // 10
    return iss(escgen.load(escgen.TABLES / "nec_tx_gen.yaml"), 2 * slot + 1000, 2500,
               pushes=[(100 + slot * i, 0, w) for i, w in enumerate(NEC)])


@pytest.fixture(scope="module")
def ws_iss():
    return iss(escgen.load(escgen.TABLES / "ws2812_gen.yaml"), 25_000,
               tape=[len(COLOURS)] + COLOURS)


def test_nec_firmware_round_trip(nec_iss):
    m = learn(nec_iss)
    assert m.coding == "pulse distance" and m.idle == 0
    assert m.frames == [[w] for w in NEC] and m.other == 0
    assert m.carrier and 37.0 < m.carrier.khz < 39.0
    assert len(m.leader) == 1 and len(m.trailer) == 1
    c = check(m, nec_iss, clk_khz=2500, max_frames=1)
    assert c.ok and c.decoded and c.carrier_ok, c.failures


def test_ws2812_round_trip(ws_iss):
    m = learn(ws_iss)
    assert m.coding == "pulse width" and not m.carrier and m.last_bit_open
    assert m.nbits == 72 and m.unit == 24
    assert m.frames and all(f == [rev(c, 24) for c in COLOURS] for f in m.frames)
    c = check(m, ws_iss)
    assert c.ok and c.decoded, c.failures


def test_ws2812_tape_table_sends_new_colours(ws_iss):
    """The learned table with the tape source is CHECKED, and sends other colours."""
    m = learn(ws_iss)
    t = m.table("tape")
    escgen.build(t)                                    # refuses unless CHECKED
    new = [rev(c, 24) for c in (0xFF0000, 0x0000FF, 0x5A5A5A)]
    rec = iss(t, 25_000, tape=[3] + [m._stored(w) for w in new])
    assert new in m.decode(rec)


def test_spec_remote_with_repeat_codes():
    """A TSOP-demodulated remote (idle high): two frames and two repeat codes. The
    repeat codes have another shape and are counted, not modelled."""
    rec = tsop([(1000, NEC[0]), (1000 + SLOT, None), (1000 + 2 * SLOT, None),
                (1000 + 3 * SLOT, NEC[1])])
    m = learn(rec)
    assert m.idle == 1 and not m.carrier and m.coding == "pulse distance"
    assert m.frames == [[w] for w in NEC] and m.other == 2
    c = check(m, rec, clk_khz=2500, max_frames=2)
    assert c.ok and c.decoded, c.failures


def test_modulated_spec_remote():
    period = 25e6 / 38000
    rec = from_marks(remote([(1000, NEC[0]), (1000 + SLOT, NEC[1])]),
                     carrier=(period, period / 3))
    m = learn(rec)
    assert m.carrier and abs(m.carrier.khz - 38.0) < 0.3
    assert abs(m.carrier.high / m.carrier.period - 1 / 3) < 0.02
    assert m.frames == [[w] for w in NEC]


@pytest.mark.parametrize("kw, frac", [({"scale": 1.03}, 0.10), ({"scale": 0.97}, 0.05),
                                      ({"mark_bias_us": 60}, 0.05)])
def test_oscillator_error_jitter_and_distortion(kw, frac):
    rec = jitter(tsop([(1000, NEC[0]), (1000 + SLOT, NEC[1])], **kw), frac)
    assert learn(rec).frames == [[w] for w in NEC]


@pytest.mark.parametrize("step", [25, 250])            # sampled at 1 MHz and 100 kHz
def test_coarse_sampling(step):
    rec = quantise(tsop([(1000, NEC[0]), (1000 + SLOT, NEC[1])]), step)
    m = learn(rec)
    assert m.frames == [[w] for w in NEC]
    assert check(m, rec, clk_khz=2500, max_frames=1).ok


def test_a_glitch_spoils_only_its_frame():
    marks = remote([(1000, NEC[0]), (1000 + SLOT, NEC[1]), (1000 + 2 * SLOT, NEC[0])])
    a, _ = marks[10]                                   # a 2 us spike in frame 0's 10th space
    marks.insert(11, (a + 25 * 900, a + 25 * 902))
    m = learn(from_marks(marks, active_low=True))
    assert m.frames == [[NEC[1]], [NEC[0]]] and m.other == 1


def test_frames_among_receiver_noise():
    """A 433 MHz receiver outputs noise while nothing is sent: frames in between still
    give the model; the noise bursts are counted, not modelled."""
    rng = random.Random(5)
    rec = tsop([(1000 + SLOT * i, NEC[i % 2]) for i in range(4)])
    noisy, t_end = [], rec[-1][0]
    for burst in range(5):                             # 20 random edges in each quiet stretch
        t = 1000 + SLOT * burst - 30 * 25_000
        for k in range(20):
            t += round(25 * 10 ** rng.uniform(1, 2.5))
            noisy.append(t)
    toggles = [(t, None) for t in noisy if 0 < t < t_end + 50_000]
    lv, out = 1, [rec[0]]
    for t, v in sorted(rec[1:] + toggles, key=lambda e: e[0]):   # noise edges toggle
        lv = (lv ^ 1) if v is None else v
        if out[-1][1] != lv:
            out.append((t, lv))
    if out[-1][1] != 1:
        out.append((out[-1][0] + 1000, 1))
    m = learn(out)
    assert m.frames == [[NEC[i % 2]] for i in range(4)] and m.other >= 1


def test_cut_frames_are_dropped():
    rec = tsop([(1000 + SLOT * i, NEC[i % 2]) for i in range(4)])
    a, b = 1000 + 20 * 25_000, 1000 + 3 * SLOT + 30 * 25_000   # inside frames 0 and 3
    cut = [(a, rec[0][1])] + [(t, v) for t, v in rec if a < t < b]
    lv = [v for t, v in rec if t <= b][-1]
    m = learn(cut + [(b, lv)] if cut[-1][1] != lv else cut)
    assert m.frames == [[NEC[1]], [NEC[0]]]


NOISE = {"log-uniform": lambda r: 25 * 10 ** r.uniform(0, 3),     # 1 us to 1 ms
         "uniform": lambda r: r.uniform(25, 25_000),
         "exponential": lambda r: 25 + r.expovariate(1 / 2500)}


@pytest.mark.parametrize("kind", NOISE)
def test_noise_is_refused(kind):
    """300 edges at random times, 30 seeds per distribution: never a model."""
    for seed in range(30):
        rng = random.Random(seed)
        t, rec = 0, [(0, 0)]
        for k in range(300):
            t += max(1, round(NOISE[kind](rng)))
            rec.append((t, (k + 1) & 1))
        with pytest.raises(LearnError):
            learn(rec)


def test_too_little_is_refused():
    with pytest.raises(LearnError):
        learn([(0, 0), (100, 1), (200, 0)])


@pytest.mark.parametrize("source", ["mailbox", "constant"])
def test_yaml_table_is_the_table(nec_iss, source):
    """The written file loads as the table, and escgen certifies it at 25 MHz."""
    m = learn(nec_iss, name="nec_learned")
    t = m.table(source)
    loaded = yaml.safe_load(m.table_yaml(source))
    assert loaded == {k: v for k, v in t.items() if k != "source_file"}
    escgen.build(t)                                    # CHECKED or TableError


def test_mailbox_table_sends_new_words():
    """Learned from a TSOP recording, the mailbox table sends a frame the remote never sent."""
    m = learn(tsop([(1000, NEC[0]), (1000 + SLOT, NEC[1])]))
    new = nec_raw(0x5A, 0x3C)
    slot = SLOT // 10
    rec = iss(m.table("mailbox"), slot + 1000, 2500, pushes=[(100, 0, new)])
    assert m.decode(rec) == [[new]]


def test_cli(tmp_path, capsys):
    rec = tsop([(1000, NEC[0]), (1000 + SLOT, NEC[1])])
    vcd, out = tmp_path / "remote.vcd", tmp_path / "remote.yaml"
    write_vcd(rec, vcd, names=["ir"] + [f"d{i}" for i in range(1, 8)], pins=1)
    assert cli([str(vcd), "--signal", "ir", "-o", str(out)]) == 0
    text = capsys.readouterr().out
    assert "pulse distance" in text and f"0x{NEC[0]:08x}" in text
    escgen.build(escgen.load(out))
    assert cli([str(vcd), "--pin", "0", "--check", "--clk-khz", "2500"]) == 0


def test_cli_refuses_noise(tmp_path, capsys):
    rng = random.Random(3)
    t, rec = 0, [(0, 0)]
    for k in range(300):
        t += round(25 * 10 ** rng.uniform(0, 3))
        rec.append((t, (k + 1) & 1))
    vcd = tmp_path / "noise.vcd"
    write_vcd(rec, vcd, pins=1)
    assert cli([str(vcd)]) == 1
    assert "no model" in capsys.readouterr().out


def test_clusters_split_at_the_ratio():
    cl = esclearn.clusters([100, 104, 98, 300, 310, 1000])
    assert [(c.lo, c.hi, c.n) for c in cl] == [(98, 104, 3), (300, 310, 2), (1000, 1000, 1)]
