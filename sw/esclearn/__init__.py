"""Protocols from recordings (sw/esclearn): a captured waveform in, a timing table out.

learn() takes one pin of a recording (esctape records: a chip capture, a VCD or a sigrok
session) and works out the protocol: a Manchester code (manchester.py), asynchronous
serial (serial.py) or a two-symbol pulse code (pulse.py), whichever describes the
recording in the fewest bits, with the carrier if there is one, the gaps between frames
and every symbol's timing. It decodes every recorded frame into words, and writes the
timing table in escgen's format, so the generated firmware, which carries a timing
certificate, sends them again or new ones. Nothing is guessed
silently: a recording that fits no model raises LearnError (with each model's reason),
and raw replay (esctape.build_replay) is the way to re-send it unchanged.

check() closes the loop: it builds the table with escgen, runs it on the ISS once per
recorded frame, compares the output's timing with the recording, and reads it back with
the model's own decoder.

Durations are ticks of the 25 MHz chip clock (40 ns), as in esctape's records. A mark
is a run away from the idle level, which is the level the recording starts and ends at.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .core import (CLOCK_HZ, RAW_BITS, Carrier, Cluster, LearnError, Recording, clusters,
                   edges, envelope, find_carrier, runs_of, us)
from .manchester import Manchester, learn_manchester
from .pulse import PulseCode, learn_pulse
from .serial import Serial, learn_serial

__all__ = ["CLOCK_HZ", "Carrier", "Check", "Cluster", "LearnError", "Manchester", "PulseCode",
           "Serial", "check", "clusters", "learn", "regenerate"]

LEARNERS = (("Manchester", learn_manchester), ("pulse code", learn_pulse),
            ("serial", learn_serial))


def description_bits(model, records, pin, runs) -> float:
    """How many bits it takes to write the recording down with this model (minimum
    description length). When several models, or several framings, decode a recording,
    the one that needs the fewest bits has found the most structure in it. The parts:

    - its timings (constant_bits): a time that is a whole multiple of the shortest costs
      the multiple, any other a raw length (RAW_BITS); a carrier two raw lengths;
    - the data bits of every frame it reads (frame_bits);
    - the frames it does not read, in the model's own symbols (unread_bits), each
      distinct shape once and then which shape each one is (a held key's repeat codes
      are one shape; a frame spoilt by a glitch costs little more than the glitch);
    - the gaps between frames, each distinct length once and then which one each gap is;
    - RAW_BITS for any other run outside every frame (of `runs`, the recording's runs).

    A 20-bit pulse code that also reads as serial costs 20 bits a frame as itself, and
    six characters of 8 bits, with the idle between them, as serial; a leader's long
    space taken for the gap between frames leaves every leader unexplained and doubles
    the gaps."""
    frs = model.frame_list(records, pin)
    read = [fr for fr in frs if model.words(fr) is not None]
    shapes, unread = [], [fr for fr in frs if model.words(fr) is None]
    for fr in unread:
        if not any(_same_shape(fr, s) for s in shapes):
            shapes.append(fr)
    env = envelope(runs_of(edges(records, pin)), model.idle, model.carrier)
    thr = model.gap_thr
    gaps = [d for k, (m, d, _) in enumerate(env) if not m and 0 < k < len(env) - 1
            and thr is not None and thr <= d != math.inf]
    gcl = clusters(gaps, model.quant) if gaps else []
    covered = sum(2 * len(fr) - 1 for fr in frs)
    return (constant_bits(model.timings()) + 2 * RAW_BITS * bool(model.carrier)
            + sum(model.frame_bits(fr) for fr in read)
            + sum(model.unread_bits(fr) for fr in shapes)
            + len(unread) * math.log2(1 + len(shapes))
            + RAW_BITS * len(gcl) + len(gaps) * math.log2(1 + len(gcl))
            + RAW_BITS * max(0, runs - covered - len(gaps)))


def constant_bits(times, most=64, tol=0.05) -> float:
    """The shortest time costs RAW_BITS; each other one log2(most) if it is within tol of
    a whole multiple (up to `most`) of the shortest, else RAW_BITS."""
    ts = sorted(t for t in times if t)
    if not ts:
        return 0.0
    out = RAW_BITS
    for t in ts[1:]:
        k = round(t / ts[0])
        out += math.log2(most) if 1 <= k <= most and abs(t - k * ts[0]) <= tol * t else RAW_BITS
    return out


def _same_shape(a, b, tol=1.2):
    """Two frames of the same runs, each within `tol` of the other's."""
    return len(a) == len(b) and all(
        (x is None) == (y is None) and (x is None or max(x, y) <= tol * min(x, y))
        for p, q in zip(a, b) for x, y in zip(p, q))


def learn(records, pin=0, name="learned", source="capture", quant=1):
    """The model of one pin's recording, sampled every `quant` ticks (1 for a chip
    capture; 25e6 / sample rate for a logic analyser's): of every model that decodes it,
    at every candidate frame gap, the one with the shortest description
    (description_bits). Raises LearnError, with each model's reason, if none does."""
    base = Recording.of(records, pin, quant)
    inner = [(m, d) for m, d, _ in base.env[1:-1] if d != math.inf]
    ms = [d for m, d in inner if m]
    ss = [d for m, d in inner if not m and (base.thr is None or d < base.thr)]
    if ms and len(clusters(ms, quant)) == 1 and len(clusters(ss, quant)) <= 1:
        cycle = sum(ms) / len(ms) + (sum(ss) / len(ss) if ss else 0)
        raise LearnError(f"every mark is one length and every space another ({us(cycle)} a "
                         "cycle): a periodic signal, which carries no data (if it is data, "
                         "it never changes in this recording)")
    found, why = [], []
    for i, rec in enumerate(base.splits()):
        for kind, fn in LEARNERS:
            if i and kind == "serial":                  # serial frames its own messages
                continue
            try:
                found.append(fn(rec, name, source))
            except LearnError as e:
                if not i:
                    why.append(f"{kind}: {e}")
    if not found:
        raise LearnError("; ".join(why))
    runs = max(0, len(base.env) - 2)                    # not the lead-in, nor the open end
    model = min(found, key=lambda m: description_bits(m, records, pin, runs))
    _alike(model, model.frame_list(records, pin))
    return model


def _alike(model, frs):
    """A note when the frames the model does not read are all one shape, with its timing
    (a held key's repeat code, say): run by run within 20% of each other."""
    odd = [fr for fr in frs if model.words(fr) is None]
    if len(odd) < 2 or len(odd) != model.other or not all(_same_shape(fr, odd[0]) for fr in odd):
        return
    runs = []
    for i in range(len(odd[0])):
        for j, what in enumerate(("mark", "space")):
            ds = [fr[i][j] for fr in odd]
            if None not in ds:
                runs.append(f"{what} {us(sorted(ds)[len(ds) // 2])}")
    model.notes.append(f"the {len(odd)} frames of another shape are all alike: "
                       + ", ".join(runs))


@dataclass
class Check:
    frames: int
    worst_rel: float          # largest |generated - recorded| / recorded
    worst_ticks: int
    decoded: bool             # the model reads every generated frame back as its words
    carrier_ok: bool
    failures: list[str]

    @property
    def ok(self) -> bool:
        return not self.failures


def regenerate(model, words, length, clk_khz=25000, gap=True):
    """Records [(tick, levels)], one per tick, of the model's table sending `words` once
    (model.replay: as constants or on the tape), run on the ISS for a frame of `length`
    ticks and (if gap) the gap after it; at clk_khz, rescaled to 25 MHz ticks."""
    import escgen
    from esciss import Machine
    from escmodels import Board

    scale = 25000 / clk_khz
    table, tape = model.replay(words)
    _, image = escgen.build(table, check=False, CLK_KHZ=clk_khz)
    for i, w in enumerate(tape or []):
        image.tape[i] = w
    board = Board(pullups=0xFF if model.idle else 0)
    Machine(image, board=board).run(int((length + gap * model.gap) / scale) + 2_000,
                                    stop_when_idle=False)
    return [(round(t * scale), lv) for t, lv in enumerate(board.history)]


def check(model, records, pin=0, clk_khz=25000, max_frames=3) -> Check:
    """Run the table on the ISS once per recorded frame (model.replay: that frame's words
    as constants or on the tape; at clk_khz, rescaled to 25 MHz ticks) and compare the
    first frame it sends (on pin 0, wherever the recording's pin was) with the recording.
    The firmware sends each symbol's typical length and a real transmitter varies, so a
    generated duration passes if it lies within the lengths the recording shows for that
    symbol (serial: the bit time within the recorded bit times), give or take two ticks
    of the run clock or two samples of the recording."""
    recorded = [(w, fr) for fr in model.frame_list(records, pin)
                if (w := model.words(fr)) is not None]
    scale = 25000 / clk_khz
    slack = 2 * max(scale, model.quant)
    worst_rel, worst_t, failures, decoded, car_ok = 0.0, 0, [], True, True
    for k, (words, fr) in enumerate(recorded[:max_frames]):
        gen = regenerate(model, words, sum(a + (b or 0) for a, b in fr), clk_khz)
        gen_frames = model.frame_list(gen, 0)
        if not gen_frames:
            failures.append(f"frame {k}: the program sent no complete frame")
            continue
        fails, rel, worst = model.compare(gen_frames[0], fr, slack)
        failures += [f"frame {k}: {f}" for f in fails]
        worst_rel, worst_t = max(worst_rel, rel), max(worst_t, worst)
        dec = model.decode(gen, 0)[:1]           # a tape replay repeats the frame
        if dec != [words]:
            decoded = False
            failures.append(f"frame {k}: decoded {dec}, sent {words}")
        if model.carrier:
            got = find_carrier(runs_of(edges(gen, 0)), model.idle)
            if got is None or abs(got.period - model.carrier.period) > slack:
                car_ok = False
                failures.append(f"frame {k}: carrier {got} for {model.carrier}")
    if not recorded:
        failures.append("no recorded frame fits the model")
    return Check(min(len(recorded), max_frames), worst_rel, worst_t, decoded, car_ok, failures)
