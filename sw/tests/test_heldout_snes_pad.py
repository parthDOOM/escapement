"""SNES controller reader (firmware/snes_pad.asm, held-out round) against the controller model
escmodels.snes.SnesPad, written apart from the firmware from the CD4021B data sheet, Nintendo's
patent and the SNES Development Manual's key data layout alone. The model is the pad's two
cascaded CD4021B: it loads the buttons while LATCH is high, shifts on each rising CLOCK edge
with the 5 V propagation delay, and checks the latch width, the P/S removal time and the clock
pulse widths and frequency. What the reader should report is decided here."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import Board, HostScript, SnesPad
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
CONNECTED = 1 << 16
NAMES = ["B", "Y", "Select", "Start", "Up", "Down", "Left", "Right", "A", "X", "L", "R"]
BIT = {n: 15 - i for i, n in enumerate(NAMES)}


def image(khz=25000, half_ns=None, latch_ns=None, patch=()):
    src = (FW / "snes_pad.asm").read_text()
    for a, b in patch:
        assert src.count(a) == 1, a
        src = src.replace(a, b)
    head = f".equ CLK_KHZ, {khz}\n"
    if half_ns is not None:
        head += f".equ HALF_NS, {half_ns}\n"
    if latch_ns is not None:
        head += f".equ LATCH_NS, {latch_ns}\n"
    return assemble(head + src, base_dir=FW, name="snes_pad.asm")


def session(n=1, khz=25000, half_ns=None, latch_ns=None, patch=(), **kw):
    every = 600 * khz // 1000                       # a request every 600 us
    pushes = [(khz // 10 + i * every, 0, 1) for i in range(n)]
    host = HostScript(pushes, drain=[0], poll=max(1, khz // 50))
    pad = SnesPad(0, 1, 2, tick_ns=1e6 / khz, **kw)
    m = Machine(image(khz, half_ns, latch_ns, patch), board=Board(pad, host))
    host.machine = m
    m.run(khz // 10 + (n + 1) * every, stop_when_idle=False)
    pad.finish()
    return pad, m, host.received[0]


def clean(pad, m):
    assert not m.ff.valid, m.ff
    assert not pad.errors(), pad.errors()[:5]


def mask(*names):
    return sum(1 << BIT[n] for n in names)


COMBOS = [0, mask(*NAMES), mask("B"), mask("R"), mask("Start", "Select", "L", "R"),
          mask("Up", "A"), mask("Down", "Left", "X", "Y")]


@pytest.mark.parametrize("timing", ["max", "typ", "min"])
def test_reads_buttons(timing):
    pad, m, got = session(len(COMBOS), buttons=COMBOS, timing=timing)
    clean(pad, m)
    assert got == [CONNECTED | c for c in COMBOS]
    assert all(latch["clocks"] == 17 for latch in pad.latches)


def test_each_button_alone():
    combos = [mask(n) for n in NAMES]
    pad, m, got = session(len(combos), buttons=combos)
    clean(pad, m)
    assert got == [CONNECTED | c for c in combos]
    assert got[3] == CONNECTED | 0x1000 and got[0] == CONNECTED | 0x8000


def test_reset_combination_is_3030():
    """2-24-3: Start, Select, L and R with the standard signature read as 3030H."""
    pad, m, got = session(1, buttons={"Start", "Select", "L", "R"})
    clean(pad, m)
    assert got == [CONNECTED | 0x3030]


@pytest.mark.parametrize("sig", [0b0001, 0b1111])
def test_signature(sig):
    pad, m, got = session(1, buttons=mask("A"), signature=sig)
    clean(pad, m)
    assert got == [CONNECTED | mask("A") | sig]


def test_unplugged():
    pad, m, got = session(2, connected=False)
    assert not m.ff.valid
    assert got == [0, 0]


@pytest.mark.parametrize("khz,half_ns,latch_ns", [(50000, 320, 160), (25000, 640, 640),
                                                  (4000, 6000, 12000), (16000, 1000, 500)])
def test_fast_and_slow_reads(khz, half_ns, latch_ns):
    pad, m, got = session(3, khz, half_ns, latch_ns, buttons=COMBOS[:3], timing="max")
    clean(pad, m)
    assert got == [CONNECTED | c for c in COMBOS[:3]]


# ------------------------------------------------------------ negative controls

def test_negative_control_latch_too_short():
    """The latch high for 3 ticks (120 ns, under the 160 ns P/S pulse width)."""
    patch = [("    ADDT  LT\n    EMIT  LATM, 0, LATM        ; shift mode\n",
              "    ADDT  3\n    EMIT  LATM, 0, LATM        ; shift mode\n")]
    pad, m, got = session(1, buttons=mask("B"), patch=patch)
    assert pad.errors()


def test_negative_control_clock_low_too_short():
    """At 50 MHz, each clock low for 6 ticks (120 ns, under the 180 ns clock pulse width),
    the high phase taking the rest of the period."""
    patch = [("    ADDT  HT\n    EMIT  CLKM, 0, CLKM\n    ADDT  HT - 2\n",
              "    ADDT  2 * HT - 6\n    EMIT  CLKM, 0, CLKM\n    ADDT  4\n")]
    pad, m, got = session(1, 50000, patch=patch, buttons=mask("B"))
    assert not m.ff.valid
    assert pad.errors()


def test_negative_control_sample_after_the_edge():
    """Each bit sampled 6 ticks after the rising edge (the pin as it was 4 ticks after it)
    instead of just before it: with the register's output following in one tick (timing
    "min") every bit read is the next one, so the reading is wrong."""
    sample = "    SAMPLE DATA_PIN, MSB       ; the bit, just before the rising edge\n"
    shift = "    EMIT  CLKM, CLKM, CLKM     ; shift: the next bit out\n"
    patch = [("    ADDT  HT - 2\n" + sample + "    ADDT  2\n" + shift,
              "    ADDT  HT\n" + shift + "    ADDT  6\n" + sample)]
    pad, m, got = session(1, buttons=mask("B"), timing="min", patch=patch)
    assert not m.ff.valid
    assert got != [CONNECTED | mask("B")]


# ------------------------------------------------------------ certificate

@pytest.mark.parametrize("khz,half_ns,latch_ns", [(25000, None, None), (4000, None, None),
                                                  (50000, 320, 160), (25000, 640, 640)])
def test_certificate(khz, half_ns, latch_ns):
    from escert import certify
    assert [c.status for c in certify(image(khz, half_ns, latch_ns))] == ["CHECKED"]


@pytest.mark.parametrize("half_ns,latch_ns", [(170, 12000), (6000, 150), (300, 12000)])
def test_out_of_spec_settings_refused(half_ns, latch_ns):
    from escasm import AsmError
    with pytest.raises(AsmError):
        image(50000 if half_ns == 300 else 25000, half_ns, latch_ns)
