"""pinid.v simulated (Icarus) and decoded by pinid.py: the right wiring passes, and each
wiring fault (swapped, open, shorted to GND, bridged, bitstream not loaded) is reported.

    python -m pytest test_pinid.py      (iverilog and vvp on PATH)
"""
import random
import subprocess
from pathlib import Path

import pytest

import pinid

HERE = Path(__file__).resolve().parent
DIV = 10                                   # clocks per bit in the simulation
TB = r"""
`timescale 1ns/1ps
module tb;
  reg clk50 = 0;
  always #10 clk50 = ~clk50;
  wire clk, rst_n; wire [7:0] ui_in, uo_out, uio;
  pullup (clk); pullup (rst_n);
  genvar i;
  generate for (i = 0; i < 8; i = i + 1) begin : pu
    pullup (ui_in[i]); pullup (uo_out[i]); pullup (uio[i]);
  end endgenerate
  pinid #(.BIT_DIV(%d)) dut (.clk50(clk50), .clk(clk), .rst_n(rst_n), .ui_in(ui_in),
                             .uo_out(uo_out), .uio(uio));
  integer n;
  initial begin
    for (n = 0; n < %d; n = n + 1) begin
      @(negedge clk50);
      $display("%%b", {uio, uo_out, ui_in, rst_n, clk});
    end
    $finish;
  end
endmodule
"""


@pytest.fixture(scope="module")
def levels(tmp_path_factory):
    """Per clock: the 26 pin levels (bit k = signal index k), 4 repeats long."""
    d = tmp_path_factory.mktemp("pinid")
    (d / "tb.v").write_text(TB % (DIV, 24 * DIV * 4))
    subprocess.run(["iverilog", "-g2005", "-o", str(d / "sim"), str(d / "tb.v"),
                    str(HERE / "pinid.v")], check=True)
    out = subprocess.run(["vvp", "-n", str(d / "sim")], check=True, capture_output=True,
                         text=True).stdout
    rows = [int(l, 2) for l in out.split() if set(l) <= {"0", "1"} and len(l) == 26]
    assert len(rows) == 24 * DIV * 4
    return rows


def to_words(levels, wire=None, step=1.0, jitter=0.0, seed=1):
    """Pico GPIO_IN words sampled every `step` clocks (+- jitter), through a wiring:
    wire(gpio_levels_dict) may change what each GPIO sees (faults)."""
    rnd = random.Random(seed)
    stamps, words = [], []
    t = 0.0
    while int(t) < len(levels):
        lv = levels[int(t)]
        g = {pinid.EXPECT[k]: (lv >> k) & 1 for k in range(26)}
        if wire:
            wire(g)
        w = 0
        for gp, v in g.items():
            w |= v << gp
        stamps.append(int(t))
        words.append(w)
        t += step * (1 + rnd.uniform(-jitter, jitter))
    return stamps, words


def problems(levels, **kw):
    stamps, words = to_words(levels, **kw)
    return pinid.decode(stamps, words, bit=DIV)


def test_right_wiring_passes(levels):
    res, probs = problems(levels)
    assert probs == []
    assert all(res[pinid.EXPECT[k]] == ("signal", k) for k in range(26))


def test_coarse_jittery_sampling_passes(levels):
    # the Pico takes about 10 samples per bit; 3 per bit with 25 % jitter still decodes
    res, probs = problems(levels, step=DIV / 3.3, jitter=0.25)
    assert probs == []


def test_swapped_wires(levels):
    def swap(g):
        g[13], g[14] = g[14], g[13]       # uo_out[3] and uo_out[4] crossed
    _, probs = problems(levels, wire=swap)
    assert any("uo_out[3] arrives on GP14" in p for p in probs)
    assert any("uo_out[4] arrives on GP13" in p for p in probs)
    assert len(probs) == 2


def test_open_wire(levels):
    def open_(g):
        g[5] = 1                          # ui_in[4] not connected: Pico pull-up only
    _, probs = problems(levels, wire=open_)
    assert probs == ["ui_in[4] (expected on GP5) not found; GP5 stays high: wire open "
                     "(no signal reaches it)"]


def test_short_to_ground(levels):
    def gnd(g):
        g[20] = 0
    _, probs = problems(levels, wire=gnd)
    assert len(probs) == 1 and "uio[3]" in probs[0] and "stays low" in probs[0]


def test_bridged_pair(levels):
    def bridge(g):
        g[3] = g[4] = g[3] & g[4]         # ui_in[2] and ui_in[3] soldered together
    _, probs = problems(levels, wire=bridge)
    assert len(probs) == 2 and all("bridged" in p for p in probs)


def test_clock_and_reset_wires(levels):
    def swap(g):
        g[0], g[28] = g[28], g[0]         # clk and rst_n crossed
    _, probs = problems(levels, wire=swap)
    assert sorted(probs) == ["clk arrives on GP28, expected GP0",
                             "rst_n arrives on GP0, expected GP28"]


def test_no_bitstream(levels):
    stamps = list(range(1000))
    words = [sum(1 << g for g in pinid.HEADER_GPIOS)] * 1000
    _, probs = pinid.decode(stamps, words, bit=DIV)
    assert len(probs) == 1 and "pinid.fs loaded" in probs[0]
