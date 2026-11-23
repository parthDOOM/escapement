"""PWM audio (firmware/pwm_audio.asm) against a receiver written from the format
(escmodels/pwm_audio.py), on the ISS: 8-bit samples from the host, four to a mailbox word, played
as PWM at an exact sample period. The receiver times every edge, so each test checks the
carrier's schedule to the tick as well as the samples."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import Board, HostFeed
from escmodels.pwm_audio import PwmAudioReceiver, schedule
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
SILENCE = 128


def image(**equ):
    src = "".join(f".equ pwm_audio.{k}, {v}\n" for k, v in equ.items())
    return assemble(src + '.include "pwm_audio.asm"\n', base_dir=FW)


def words(samples):
    s = list(samples) + [SILENCE] * (-len(samples) % 4)
    return [s[i] << 24 | s[i + 1] << 16 | s[i + 2] << 8 | s[i + 3] for i in range(0, len(s), 4)]


def run(queue, n_samples, khz=25000, gap=1, start=5000, **equ):
    im = image(CLK_KHZ=khz, **equ)
    rx = PwmAudioReceiver(6, clk_khz=khz)
    host = HostFeed({1: queue}, gap=gap, start=start)
    board = Board(rx, host, record=False)
    m = Machine(im, board=board, record=False)
    host.machine = m
    m.run(rx.sp * n_samples + 1000, stop_when_idle=False)
    return m, rx, host


def played(rx):
    """The samples between the leading silence and the trailing silence."""
    s = rx.samples
    a = next(i for i, v in enumerate(s) if v != SILENCE)
    b = len(s) - next(i for i, v in enumerate(reversed(s)) if v != SILENCE)
    return s[a:b], a


def test_schedule_at_25_mhz():
    """11,025 samples a second at 25 MHz: 2,268 ticks a sample, 283 + 284 a pair of periods."""
    assert schedule(25000, 11025) == (2268, 283, 284, 0)
    im = image()
    assert (im.symbols["pwm_audio.SP"], im.symbols["pwm_audio.PA"], im.symbols["pwm_audio.PB"]) == (2268, 283, 284)


def test_samples_come_back_exactly_on_schedule():
    """A ramp through every value and back: the receiver decodes each sample from its eight
    periods, every period starts on its scheduled tick, and the chip never faults."""
    samples = [(i * 37 + 11) & 0xFF for i in range(160)]
    samples[0] = 0x5A                               # not silence, so the start is found
    m, rx, host = run(words(samples), 200)
    got, lead_in = played(rx)
    assert got == samples
    assert not rx.violations, rx.violations[:3]
    assert m.ctx[1].state != 3 and not m.ff.valid
    assert m.tape[0x80] == 0                        # COUNT: the ring drained


def test_extremes_and_the_shortest_pulse():
    """0 and 255 alternate: the shortest high time (OFFS = 8 ticks) and the longest (263), in
    the same pair of periods."""
    samples = [0, 255] * 40
    samples[0] = 1
    m, rx, host = run(words(samples), 110)
    assert played(rx)[0] == samples and not rx.violations


def test_a_stalled_host_is_heard_as_silence_never_late():
    """The host stops for 30 ms mid-stream: the ring runs dry, the chip plays silence and counts
    it (UNDER), nothing faults, and the samples after the stall come back exactly."""
    first = [(3 * i + 1) & 0xFF or 1 for i in range(64)]
    second = [(5 * i + 7) & 0xFF or 1 for i in range(64)]
    im = image()
    rx = PwmAudioReceiver(6)
    host = HostFeed({1: words(first)}, start=5000)
    board = Board(rx, host, record=False)
    m = Machine(im, board=board, record=False)
    host.machine = m
    m.run(2268 * 64 + 30 * 25000, stop_when_idle=False)      # the first part, then a stall
    under = m.tape[0x82]
    host.queues[1] = words(second)
    m.run(2268 * 80, stop_when_idle=False)
    s = rx.samples
    a = s.index(first[0])
    assert s[a:a + 64] == first
    b = s.index(second[0], a + 64)
    # the stall, less the host's start (5,000 ticks) and the samples before the first word landed
    assert all(v == SILENCE for v in s[a + 64:b]) and b - (a + 64) >= (30 * 25000 - 5000) // 2268 - a - 1
    assert s[b:b + 64] == second
    assert m.tape[0x82] > under > 0 and not rx.violations and m.ctx[1].state != 3


def test_a_fast_host_is_held_back_by_the_mailbox():
    """The host pushes as fast as the mailbox takes words, 300 words against a 128-word ring (one
    kept free): the program leaves a word in the mailbox while the ring is full, no word is lost,
    and every sample plays in order."""
    samples = [(7 * i + 3) & 0xFF or 1 for i in range(1200)]
    m, rx, host = run(words(samples), 1260)
    assert played(rx)[0] == samples and not rx.violations
    assert len(host.pushed) == 300
    spans = [t for t, _ in host.pushed]
    assert spans[-1] - spans[0] > 2268 * 4 * 150     # held back: the pushes spread over the play


@pytest.mark.parametrize("khz", [24000, 50000])
def test_other_clocks(khz):
    """At 24 and 50 MHz the schedule follows the clock (50 MHz: 4,535 ticks a sample, pairs of
    566 + 567 and 3 ticks more at a sample's end) and the samples still come back exactly."""
    samples = [(11 * i + 5) & 0xFF or 1 for i in range(48)]
    m, rx, host = run(words(samples), 60, khz=khz)
    assert played(rx)[0] == samples and not rx.violations


def test_certificate():
    from escert import certify
    for khz in (24000, 25000, 50000):
        assert [c.status for c in certify(image(CLK_KHZ=khz))] == ["CHECKED"], khz
