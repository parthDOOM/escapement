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
    rec = iss(t, 25_000, tape=[3] + [m.stored(w) for w in new])
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


# --------------------------------------------------------------------------- Manchester

TE = 25e6 / 2400                                       # DALI half-bit, ticks
RC5_P = 25e6 / 36000                                   # RC-5 carrier period, ticks


def mark_spans(halves, half, t0, idle):
    """Mark intervals of half-bit levels (pin levels) from t0, adjacent marks merged."""
    out, t = [], t0
    for h in halves:
        if h != idle:
            if out and abs(out[-1][1] - t) < 1e-6:
                out[-1] = (out[-1][0], t + half)
            else:
                out.append((t, t + half))
        t += half
    return out


def dali_records(frames, slot=30_000 * 25):
    from escmodels.dali import manchester
    marks = []
    for k, f in enumerate(frames):
        marks += mark_spans(manchester([(f >> (15 - i)) & 1 for i in range(16)]), TE,
                            1000 + k * slot, idle=1)
    return from_marks(marks, active_low=True)


def dali_word(f):
    """The word esclearn reports: the start bit (1), then the frame MSB first."""
    return 1 | sum(((f >> (15 - i)) & 1) << (i + 1) for i in range(16))


def rc5_bits(addr, cmd, toggle):
    return [1, 1, toggle] + [(addr >> (4 - i)) & 1 for i in range(5)] + \
           [(cmd >> (5 - i)) & 1 for i in range(6)]


def rc5_marks(frames, slot=113_778 * 25):
    """IR-on intervals of RC-5 frames (a 1 is off then on, half-bit 32 carrier periods)."""
    marks = []
    for k, bits in enumerate(frames):
        halves = [h for b in bits for h in (1 - b, b)]
        marks += mark_spans(halves, 32 * RC5_P, 1000 + k * slot, idle=0)
    return marks


DALI = [0xFE80, 0x0105, 0xFF90, 0x3A21]
RC5 = [rc5_bits(0x05, 0x35, 0), rc5_bits(0x05, 0x35, 1), rc5_bits(0x1A, 0x0C, 0)]


def test_dali_frames_from_the_specification():
    rec = dali_records(DALI)
    m = learn(rec)
    assert isinstance(m, esclearn.Manchester) and m.idle == 1 and not m.carrier
    assert m.nbits == 17 and abs(m.half - TE) < 2
    assert m.frames == [[dali_word(f)] for f in DALI]
    c = check(m, rec, clk_khz=2500)
    assert c.ok and c.decoded, c.failures


def test_rc5_on_a_carrier():
    rec = from_marks(rc5_marks(RC5), carrier=(RC5_P, RC5_P / 4))
    m = learn(rec)
    assert isinstance(m, esclearn.Manchester) and m.carrier
    assert abs(m.carrier.khz - 36.0) < 0.3 and m.nbits == 14
    assert m.frames == [[sum(b << i for i, b in enumerate(bits))] for bits in RC5]
    escgen.build(m.table("mailbox"))                   # CHECKED, or TableError
    c = check(m, rec, clk_khz=2500, max_frames=2)
    assert c.ok and c.decoded and c.carrier_ok, c.failures


def test_rc5_demodulated():
    """From a TSOP (idle high, low while the carrier is on): the pin falls mid-bit for an
    RC-5 1, so the words are the complements."""
    m = learn(from_marks(rc5_marks(RC5), active_low=True))
    assert isinstance(m, esclearn.Manchester) and m.idle == 1 and m.nbits == 14
    assert m.frames == [[sum((1 - b) << i for i, b in enumerate(bits))] for bits in RC5]


def test_rc5_carrier_on_an_idle_high_pin():
    """A raw IR recording that idles high (an inverting receiver or LED driver): the
    carrier pulses go low, and escgen now sends that."""
    rec = [(t, v ^ 1) for t, v in from_marks(rc5_marks(RC5), carrier=(RC5_P, RC5_P / 4))]
    m = learn(rec)
    assert m.carrier and m.idle == 1 and m.nbits == 14
    assert m.frames == [[sum(b << i for i, b in enumerate(bits))] for bits in RC5]
    escgen.build(m.table("mailbox"))
    assert check(m, rec, clk_khz=2500, max_frames=1).ok


# --------------------------------------------------------------------------- serial

def uart_records(data, baud, nd=8, par="N", stops=1, idle=1, gap_bits=0.0, start=1000):
    """Records of characters LSB first with optional parity, `gap_bits` idle between."""
    bit = 25e6 / baud
    rec, t = [(0, idle)], float(start)
    for v in data:
        bits = [(v >> i) & 1 for i in range(nd)]
        if par != "N":
            bits.append((sum(bits) & 1) ^ (par == "O"))
        for b in [0] + bits + [1] * stops:
            lv = b if idle else 1 - b
            if rec[-1][1] != lv:
                rec.append((round(t), lv))
            t += bit
        t += gap_bits * bit
    if rec[-1][1] != idle:
        rec.append((round(t), idle))
    return rec


TEXT = b"Hello, Escapement! 0123456789 The quick brown fox."
BINARY = bytes((37 * i + 11) & 0xFF for i in range(48))


@pytest.mark.parametrize("nd, par, stops, idle, gap, data, found", [
    (8, "N", 1, 1, 0, BINARY, 1),
    (8, "E", 1, 1, 0, BINARY, 1),
    (8, "O", 1, 1, 0.5, BINARY, 1),
    (7, "E", 1, 1, 0, TEXT, 1),
    (7, "O", 1, 1, 0, TEXT, 1),
    (7, "N", 1, 1, 0, TEXT, 1),
    (7, "N", 1, 1, 1, TEXT, 2),     # one idle bit after every character: 7N2, same waveform
    (8, "N", 2, 1, 0, BINARY, 2),
    (8, "N", 1, 0, 0, BINARY, 1),   # idle low (inverted)
])
def test_serial_formats(nd, par, stops, idle, gap, data, found):
    data = bytes(v & ((1 << nd) - 1) for v in data)
    rec = uart_records(data, 9600, nd, par, stops, idle, gap)
    m = learn(rec)
    assert isinstance(m, esclearn.Serial), m.describe()
    assert (m.nd, m.parity, m.stops, m.baud, m.idle) == (nd, par, found, 9600, idle)
    assert m.frames == [list(data)]
    escgen.build(m.table("mailbox"))
    c = check(m, rec, clk_khz=2500)
    assert c.ok and c.decoded, c.failures


def test_uart_from_firmware():
    msg = list(b"Escapement")
    frame = int(11 * 25e6 / 115200)
    host = [(100 + frame * i, 0, b) for i, b in enumerate(msg)]
    rec = iss(escgen.load(escgen.TABLES / "uart_tx_gen.yaml"), frame * (len(msg) + 3),
              pushes=host)
    m = learn(rec)
    # the host pushes a byte every 11 bit times: one idle bit after each 8N1 character,
    # which reads as 8N2 (the same waveform)
    assert isinstance(m, esclearn.Serial) and m.fmt()[:2] == "8N" and m.baud == 115200
    assert m.frames == [msg]
    assert check(m, rec).ok


def test_midi_from_firmware():
    msg = [0x90, 0x3C, 0x7F, 0x80, 0x3C, 0x00, 0xB0, 0x07, 0x64]
    frame = int(11 * 25e6 / 31250)
    rec = iss(escgen.load(escgen.TABLES / "midi_tx.yaml"), frame * (len(msg) + 3),
              pushes=[(100 + frame * i, 0, b) for i, b in enumerate(msg)])
    m = learn(rec)
    assert isinstance(m, esclearn.Serial) and m.baud == 31250 and m.frames == [msg]


def test_dmx512_from_firmware():
    """DMX512 at CLK_KHZ 5000 (a bit is 4 us either way): breaks split the packets, 8N2
    at 250 kbaud, 513 slots; the learned tape table is CHECKED and sends the packet."""
    slots = [0] + [(7 * i + 1) & 0xFF for i in range(1, 512)]
    tape = [sum(slots[4 * w + j] << (8 * j) for j in range(4)) for w in range(128)]
    rec = iss(escgen.load(escgen.TABLES / "dmx512_tx.yaml"), 250_000, 5000, tape=tape)
    m = learn(rec)
    assert isinstance(m, esclearn.Serial) and m.brk and m.baud == 250000
    assert m.fmt() == "8N2" and m.frames and m.frames[0] == slots
    assert 90 * 25 <= m.brk <= 110 * 25 and 10 * 25 <= m.mab <= 14 * 25
    escgen.build(m.table("tape"))
    c = check(m, rec, clk_khz=5000, max_frames=1)
    assert c.ok and c.decoded, c.failures


def test_a_pulse_code_is_not_serial(ws_iss):
    """WS2812 also reads as 7N1 at 2.42 Mbaud (three of its bits make one character);
    the pulse-width model is the plausible one."""
    assert isinstance(learn(ws_iss), esclearn.PulseCode)


# --------------------------------------------------------------------------- check bytes

from esclearn import checks                            # noqa: E402

BUTTONS = [nec_raw(a, c) for a, c in ((0x04, 0x08), (0x10, 0xA5), (0x22, 0x15), (0x04, 0x40))]


def test_nec_check_bytes_found_and_filled():
    m = learn(tsop([(1000 + SLOT * i, w) for i, w in enumerate(BUTTONS)]))
    assert m.checks == [(1, ("not", 0)), (3, ("not", 2))]
    assert "byte 3 is the complement of byte 2" in m.summary()
    assert m.complete([0x0034_0012]) == [nec_raw(0x12, 0x34)]   # address, ~, command, ~


def test_a_constant_byte_proves_nothing():
    """One address in every frame: byte 1 = ~byte 0 holds but is not believed."""
    m = learn(tsop([(1000 + SLOT * i, nec_raw(0x04, c)) for i, c in enumerate((8, 9, 0x40))]))
    assert m.checks == [(3, ("not", 2))]


def test_serial_xor_checksum():
    msgs = [[0x02, a, b, 0x02 ^ a ^ b] for a, b in ((0x10, 0x20), (0x31, 0x07), (0x55, 0xAA),
                                                    (0x01, 0x80))]
    rec, t = [(0, 1)], 1000
    for msg in msgs:
        r = uart_records(msg, 9600, start=t)
        rec += r[1:]
        t = r[-1][0] + 200_000
    m = learn(rec)
    assert m.frames == msgs and m.checks == [(3, ("xor", None))]
    assert m.complete([0x02, 0x12, 0x34, 0x00]) == [0x02, 0x12, 0x34, 0x02 ^ 0x12 ^ 0x34]


@pytest.mark.parametrize("rule, f", [(("sum", None), lambda b: sum(b) & 0xFF),
                                     (("neg", None), lambda b: -sum(b) & 0xFF)])
def test_sum_rules(rule, f):
    frames = [[a, b, f([a, b])] for a, b in ((1, 2), (200, 100), (7, 0), (90, 91))]
    assert checks.find(frames) == [(2, rule)]


def test_cli_send(tmp_path, capsys):
    rec = tsop([(1000 + SLOT * i, w) for i, w in enumerate(BUTTONS)])
    vcd, out = tmp_path / "remote.vcd", tmp_path / "send.yaml"
    write_vcd(rec, vcd, pins=1)
    assert cli([str(vcd), "--send", "340012", "-o", str(out)]) == 0
    assert f"send 0x{nec_raw(0x12, 0x34):x} (check bytes recomputed)" in capsys.readouterr().out
    t = escgen.load(out)
    assert t["source"] == {"kind": "constant", "words": [nec_raw(0x12, 0x34)]}
    escgen.build(t)
