"""firmware/i2c_timing.asm, the I2C bus timing checker (v3: the bus timing monitor), on the
ISS: controller writes between two other devices (escmodels.I2cWave) with timing faults
planted at chosen clock pulses: a short high phase, a short low phase that also makes the
period short, a data change too close to the rise, a clock held low past the stuck-bus
limit, and pulses exactly at each limit (which are legal). Every report must be one the
waveform's own edges call a violation, at a fixed latency, with its rule, kind and
interval, and the counts must hold the coincident ones."""

from __future__ import annotations

from pathlib import Path

from escasm import assemble
from escmodels import Board, HostScript
from escmodels.i2c_wave import ANY, FALL, RISE, I2cWave
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
SCL, SDA = 0, 1
T_H, T_L, T_SU, T_P, T_OUT = 200, 235, 12, 500, 5000           # 50 MHz, TOUT 100 us
RULES = [(SCL, RISE, SCL, FALL, T_H, 0), (SCL, FALL, SCL, RISE, T_L, T_OUT),
         (SDA, ANY, SCL, RISE, T_SU, 0), (SCL, RISE, SCL, RISE, T_P, 0)]
SCRIPT = [bytes([0xA0, 0x55, 0x0F]), bytes([0xA1, 0xC3])]
PLANT = {
    5: {"high": 150},                    # rule 0: high 150 < 200
    6: {"low": 350},                     #   (the period stays 500)
    8: {"high": 200},                    # exactly tHIGH: legal ...
    9: {"low": 300},                     # ... and the period 200 + 300 = 500 exactly
    12: {"low": 235, "high": 300},       # exactly tLOW, after a 265 high: legal
    11: {"high": 265},
    14: {"low": 200},                    # rule 1: low 200 < 235, and rule 3: period 450
    22: {"hold": 245},                   # rule 2: setup 250 - 245 = 5 < 12
    30: {"low": 6000},                   # rule 1 late: held low past 5000 ticks
    36: {"low": 5000},                   # exactly the stuck limit: legal
}


def run(plant):
    wave = I2cWave(SCL, SDA, 1000, SCRIPT, plant=plant)
    host = HostScript([], drain=[0], poll=8)
    img = assemble(".equ i2c_timing.TOUT_US, 100\n" + '.include "i2c_timing.asm"\n',
                   base_dir=FW, name="i2c_timing.asm")
    m = Machine(img, board=Board(wave, host))
    host.machine = m
    m.run(wave.end + 2000, stop_when_idle=False)
    w = host.received[0]
    assert len(w) % 3 == 0
    return wave, [tuple(w[i:i + 3]) for i in range(0, len(w), 3)]


def expected(wave):
    """(end tick, rule, kind, interval), from the waveform's edges, in time order."""
    out = []
    for k, r in enumerate(RULES):
        out += [(t, k, kind, ivl) for kind, t, ivl in wave.violations(*r)]
    return sorted(out)


def test_clean_traffic_reports_nothing():
    wave, reports = run({})
    assert expected(wave) == [] and reports == []


def test_planted_faults_are_reported_with_rule_kind_and_interval():
    wave, reports = run(PLANT)
    want = expected(wave)
    # the waveform's own verdict: the planted faults, and nothing at the exact limits
    assert [(k, kind, ivl) for _, k, kind, ivl in want] == [
        (0, "early", 150), (1, "early", 200), (3, "early", 450), (2, "early", 5),
        (1, "late", T_OUT)]
    by_tick: dict[int, list] = {}
    for t, k, kind, ivl in want:
        by_tick.setdefault(t, []).append((k, kind, ivl))
    assert len(reports) == len(by_tick) == 4
    lat = set()
    for (rec, tick, counts), (t, vs) in zip(reports, sorted(by_tick.items())):
        k, kind, ivl = min(vs)                       # the lowest rule is recorded
        assert rec >> 31 == 1 and (rec >> 16) & 3 == k
        assert (rec >> 30) & 1 == (kind == "late") and rec & 0xFFFF == ivl, hex(rec)
        assert counts == sum(1 << (8 * j) for j, _, _ in vs), hex(counts)
        lat.add(tick - t)
    assert len(lat) == 1 and 0 < lat.pop() < 8      # one fixed synchroniser latency
