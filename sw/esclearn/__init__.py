"""Protocols from recordings (sw/esclearn): a captured waveform in, a timing table out.

learn() takes one pin of a recording (esctape records: a chip capture, a VCD or a sigrok
session) and works out the protocol: a Manchester code (manchester.py), asynchronous
serial (serial.py) or a two-symbol pulse code (pulse.py), with the carrier if there is
one, the gaps between frames and every symbol's timing. It decodes every recorded frame
into words, and writes the timing table in escgen's format, so the generated firmware,
which carries a timing certificate, sends them again or new ones. Nothing is guessed
silently: a recording that fits no model raises LearnError (with each model's reason),
and raw replay (esctape.build_replay) is the way to re-send it unchanged.

check() closes the loop: it builds the table with escgen, runs it on the ISS once per
recorded frame, compares the output's timing with the recording, and reads it back with
the model's own decoder.

Durations are ticks of the 25 MHz chip clock (40 ns), as in esctape's records. A mark
is a run away from the idle level, which is the level the recording starts and ends at.
"""

from __future__ import annotations

from dataclasses import dataclass

from .core import (CLOCK_HZ, Carrier, Cluster, LearnError, Recording, clusters, edges,
                   find_carrier, runs_of, us)
from .manchester import Manchester, learn_manchester
from .pulse import PulseCode, learn_pulse
from .serial import Serial, learn_serial

__all__ = ["CLOCK_HZ", "Carrier", "Check", "Cluster", "LearnError", "Manchester", "PulseCode",
           "Serial", "check", "clusters", "learn", "regenerate"]

LEARNERS = (("Manchester", learn_manchester), ("pulse code", learn_pulse),
            ("serial", learn_serial))


def plausibility(m) -> float:
    """How believable a model is, to choose when several decode the recording: Manchester
    first (every bit has opposite halves: it rarely fits anything else), then serial at
    a standard baud rate, a named pulse code, any pulse code, serial at an odd rate; the
    share of frames the model decodes breaks ties. (A WS2812 stream reads as 7N1 serial
    at 2.42 Mbaud: three of its bits are one character, so every character fits.)"""
    kind = (4 if isinstance(m, Manchester) else
            2.5 if isinstance(m, Serial) and m.baud else
            2 if isinstance(m, PulseCode) and m.coding != "two symbols" else
            1 if isinstance(m, PulseCode) else 0)
    return kind + len(m.frames) / (len(m.frames) + m.other)


def learn(records, pin=0, name="learned", source="capture", quant=1):
    """The model of one pin's recording, sampled every `quant` ticks (1 for a chip
    capture; 25e6 / sample rate for a logic analyser's): the most plausible of those
    that decode it. Raises LearnError, with each model's reason, if none does."""
    rec = Recording.of(records, pin, quant)
    found, why = [], []
    for kind, fn in LEARNERS:
        try:
            found.append(fn(rec, name, source))
        except LearnError as e:
            why.append(f"{kind}: {e}")
    if not found:
        raise LearnError("; ".join(why))
    model = max(found, key=plausibility)
    _alike(model, model.frame_list(records, pin))
    return model


def _alike(model, frs):
    """A note when the frames the model does not read are all one shape, with its timing
    (a held key's repeat code, say): run by run within 20% of each other."""
    odd = [fr for fr in frs if model.words(fr) is None]
    if len(odd) < 2 or len(odd) != model.other or len({len(fr) for fr in odd}) != 1:
        return
    runs = []
    for i in range(len(odd[0])):
        for j, what in enumerate(("mark", "space")):
            ds = [fr[i][j] for fr in odd]
            if None in ds:
                continue
            if max(ds) > 1.2 * min(ds):
                return
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
