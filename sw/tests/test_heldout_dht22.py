"""DHT22 (AM2302) host (firmware/dht22.asm, held-out round) against the sensor model
escmodels.dht22.Dht22, written apart from the firmware from the Aosong AM2302 Product
Manual alone. The model answers a start signal with Table 6's timings (typical, minimum,
maximum or random within them) and checks the host's start low (Tbe), that it keeps off the
line while the sensor sends, and the 2 s read interval (shortened here so runs stay short).
What the host should report is decided here.

Most runs use a 4 MHz clock (the program's timing is in CLK_KHZ; escert proves it at 4, 25
and 50 MHz)."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import Board, Dht22, HostScript
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
MIN_MS = 6


def image(khz=4000, min_ms=MIN_MS, patch=()):
    src = (FW / "dht22.asm").read_text()
    for a, b in patch:
        assert src.count(a) == 1, a
        src = src.replace(a, b)
    return assemble(f".equ CLK_KHZ, {khz}\n.equ MIN_MS, {min_ms}\n" + src, base_dir=FW,
                    name="dht22.asm")


def session(n=1, khz=4000, min_ms=MIN_MS, patch=(), every_ms=None, **kw):
    """n host requests, `every_ms` apart (default: as fast as the program allows). The
    sensor's start-up and read interval are MIN_MS - 1 ms."""
    kw.setdefault("startup_s", (MIN_MS - 1) / 1000)
    kw.setdefault("min_interval_s", (MIN_MS - 1) / 1000)
    gap = (every_ms if every_ms is not None else 1) * khz
    pushes = [(100 + i * gap, 0, 1) for i in range(n)]
    host = HostScript(pushes, drain=[0], poll=khz // 10)
    sensor = Dht22(0, tick_ns=1e6 / khz, **kw)
    m = Machine(image(khz, min_ms, patch), board=Board(sensor, host))
    host.machine = m
    m.run((n + 1) * (min_ms + 7) * khz + n * gap, stop_when_idle=False)
    sensor.finish()
    return sensor, m, host.received[0]


def clean(sensor, m):
    assert not m.ff.valid, m.ff
    assert not sensor.errors(), sensor.errors()[:5]


@pytest.mark.parametrize("khz", [4000, 25000])
def test_reads_the_worked_example(khz):
    """7.2's example: 65.8 %RH and 26.9 C arrive as 0x0292 and 0x010D."""
    sensor, m, got = session(1, khz, humidity=65.8, temperature=26.9)
    clean(sensor, m)
    assert got == [0x0292_010D]
    assert sensor.reads[0]["answered"] and 800 <= sensor.reads[0]["start_us"] <= 1100


def test_negative_temperature_and_extremes():
    """-10.1 C (bit 15 set, 7.2's special instructions), 0 %RH at -40 C, 99.9 %RH at 80 C."""
    raws = [(50.0, -10.1), (0.0, -40.0), (99.9, 80.0)]
    sensor, m, got = session(3, humidity=[h for h, _ in raws], temperature=[t for _, t in raws])
    clean(sensor, m)
    assert got == [500 << 16 | 0x8065, 0 << 16 | 0x8190, 999 << 16 | 800]


@pytest.mark.parametrize("timing,seed", [("min", 1), ("max", 1), ("random", 3), ("random", 4)])
def test_sensor_timing_extremes(timing, seed):
    sensor, m, got = session(3, timing=timing, seed=seed, humidity=[12.3, 45.6, 78.9],
                             temperature=[-0.1, 0.0, 33.3])
    clean(sensor, m)
    assert got == [123 << 16 | 0x8001, 456 << 16 | 0, 789 << 16 | 333]


def test_reads_are_spaced_by_min_ms():
    """The host asks three times 1 ms apart (the mailbox holds two words while the first
    read waits); the program keeps MIN_MS between start signals, so the sensor sees no read
    too soon (7.1 item 3)."""
    sensor, m, got = session(3, every_ms=1, humidity=55.5, temperature=22.2)
    clean(sensor, m)
    assert got == [555 << 16 | 222] * 3
    ts = [r["t"] for r in sensor.reads]
    assert all(b - a >= MIN_MS * 4000 for a, b in zip(ts, ts[1:]))


@pytest.mark.parametrize("fault,code", [({"checksum": 0}, 4), ({"no_response": 0}, 1),
                                        ({"truncate": (0, 20)}, 3), ({"stretch": (0, 50)}, 3)])
def test_bad_reads_are_reported(fault, code):
    """A wrong parity byte, no response, a reply cut after 20 bits, the first bit high for
    50 us (neither a 0 nor a 1): each reported with its code, and the next read is good."""
    sensor, m, got = session(2, faults=fault, humidity=65.8, temperature=26.9)
    assert not m.ff.valid
    assert got == [0xFFFF0000 | code, 0x0292_010D]


# ------------------------------------------------------------ negative controls

def test_negative_control_start_too_short():
    """A start low of 0.7 ms, below Tbe's 0.8 ms: the model must flag it."""
    sensor, m, got = session(1, patch=[("    ADDT  START_US * US\n", "    ADDT  700 * US\n")])
    assert sensor.errors()


def test_negative_control_reads_too_close():
    """MIN_MS shorter than the sensor's read interval: the model must flag it."""
    sensor, m, got = session(3, min_ms=MIN_MS, min_interval_s=(MIN_MS + 4) / 1000,
                             every_ms=1)
    assert sensor.errors()


def test_negative_control_bit_threshold_wrong():
    """A 0/1 threshold above TH1 (every bit read as 0): the reading is wrong."""
    patch = [("    CMPI  r3, 36 * US + 1\n    JC    b_ok                 ; a 0\n",
              "    CMPI  r3, 80 * US + 1\n    JC    b_ok                 ; a 0\n")]
    sensor, m, got = session(1, patch=patch, humidity=65.8, temperature=26.9)
    assert got != [0x0292_010D]


# ------------------------------------------------------------ certificate

@pytest.mark.parametrize("khz", [4000, 25000, 50000])
def test_certificate(khz):
    from escert import certify
    certs = certify(image(khz))
    assert [c.status for c in certs] == ["CHECKED"]


def test_start_outside_tbe_refused():
    from escasm import AsmError
    with pytest.raises(AsmError):
        assemble(".equ START_US, 700\n" + (FW / "dht22.asm").read_text(), base_dir=FW)
