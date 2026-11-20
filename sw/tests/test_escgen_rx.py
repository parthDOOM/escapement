"""escgen's receive side (sw/escgen/rx.py): a receiver from the same timing table.

Every shipped table either gives a receiver that escert certifies or is refused with the
reason. A receiver, run beside the transmitter generated from the same table, reads back
every word the transmitter sends: through a demodulator model for the infrared tables,
straight from the pin for the others. And a table esclearn learned from a recording of a
real remote gives a receiver that reads that recording's frames as esclearn reads them,
and a word the remote never sent."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import escgen
from escasm import assemble
from escert import certify
from escgen import TableError, build_rx, load
from escgen.rx import generate_rx
from escmodels import Board, HostScript, Tsop
from esciss import Machine

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

RECEIVES = {"ev1527_tx", "jvc_tx", "nec_tx_gen", "samsung_tx", "sirc_tx"}
REFUSED = {                                   # table: why (a part of the reason given)
    "dcc_tx": "several fields must all be LSB first",
    "dmx512_tx": "one frame per word",
    "midi_tx": "not pulse codes",
    "panasonic_tx": "does not fit a 256-word page",
    "rc5_tx": "not pulse codes",
    "rc6_tx": "several fields",
    "sharp_tx": "one frame per word",
    "uart_tx_gen": "not pulse codes",
    "wiegand_tx": "one pin",
    "ws2801_tx": "one pin",
    "ws2812_gen": "too short to time",
}


@pytest.mark.parametrize("path", sorted(escgen.TABLES.glob("*.yaml")), ids=lambda p: p.stem)
def test_every_table_gives_a_certified_receiver_or_a_reason(path):
    t = load(path)
    if t["name"] in RECEIVES:
        _, image = build_rx(t)
        assert [c.status for c in certify(image)] == ["CHECKED"]
    else:
        with pytest.raises(TableError, match=REFUSED[t["name"]]):
            build_rx(t)


def _loopback(name, words, clk_khz, carrier):
    """The table's transmitter on context 0, pin 0, and its receiver on context 1: on pin 1
    behind an IR receiver module (active low) if `carrier`, else on pin 0 itself."""
    t = load(escgen.TABLES / f"{name}.yaml")
    tx, rx = escgen.generate(t), generate_rx(t)
    with_rx = (f".equ {name}.CLK_KHZ, {clk_khz}\n.equ {name}_rx.CLK_KHZ, {clk_khz}\n"
               f".equ {name}_rx.CTX, 1\n")
    if carrier:
        with_rx += f".equ {name}_rx.PIN, 1\n.equ {name}_rx.IDLE, 1\n"
    image = _two(name, tx, rx, with_rx)
    tick_ns = 1e6 / clk_khz
    board = Board(Tsop(0, 1, tick_ns=tick_ns)) if carrier else Board(pullups=0)
    m = Machine(image, board=board, record=False)
    queue, got = list(words), []
    frame = _value_ticks(t, clk_khz)
    while m.now < frame * (len(words) + 2):
        while queue and len(m.ctx[0].rx) < 2:
            m.host_rx_push(0, queue.pop(0))
            m.run(1, stop_when_idle=False)
        m.run(5_000, stop_when_idle=False)
        m.host_tx_pop(0)                                  # the transmitter's acks
        v = m.host_tx_pop(1)
        if v is not None:
            got.append(v)
    assert not m.ff.valid, m.ff
    return got


def _two(name, tx, rx, pre, tmp=[]):
    """One image of the two generated programs (each a module of its own)."""
    import tempfile
    if not tmp:
        tmp.append(Path(tempfile.mkdtemp(prefix="escgen_rx_")))
    d = tmp[0]
    (d / f"{name}.asm").write_text(tx, newline="\n")
    (d / f"{name}_rx.asm").write_text(rx, newline="\n")
    return assemble(pre + f'.include "{name}.asm"\n.include "{name}_rx.asm"\n', base_dir=d)


def _value_ticks(t, clk_khz):
    """A packet's length, the gap after it included, in ticks at clk_khz (an upper bound:
    every phase of the longer symbol of each bit)."""
    from escgen import _phases, _value
    pk = t.get("packet", {}) or {}

    def sym(n):
        return sum(_value(t, d, clk_khz) for _, d, _ in _phases(t, n))
    total = sum(sym(s) for s in pk.get("before", []) + pk.get("after", []))
    for it in t["frame"]:
        if isinstance(it, str):
            total += sym(it)
        else:
            total += it["bits"] * max(sym(s) for s in it["choose"])
    return total


IR = [("nec_tx_gen", [0x00FF00FF, 0xEF10FF00, 0x12345678], 32),
      ("samsung_tx", [0xF00F0707, 0x00000000], 32),
      ("jvc_tx", [0xA5C3, 0xFFFF, 0x0001], 16),
      ("sirc_tx", [0x095, 0xFFF, 0x001], 12)]


@pytest.mark.parametrize("name,words,bits", IR, ids=[x[0] for x in IR])
def test_infrared_receiver_reads_its_transmitter_through_a_demodulator(name, words, bits):
    """The transmitter's carrier on pin 0, a receiver module (active low, the detection
    delay of two carrier periods) onto pin 1, the generated receiver on pin 1 at IDLE 1."""
    assert _loopback(name, words, 2500, True) == [w & ((1 << bits) - 1) for w in words]


def test_pulse_width_receiver_reads_its_transmitter_on_the_same_pin():
    """EV1527 (24 bits MSB first, no carrier): the receiver reads the transmitter's pin."""
    words = [0xABCDE1, 0x000001, 0xFFFFFE]
    assert _loopback("ev1527_tx", words, 2000, False) == words


def _learned(name="nec_all.sr"):
    import test_esclearn
    return test_esclearn.dump(name, 0)[2]


def test_a_receiver_learned_from_a_real_remote_reads_its_recording():
    """esclearn learns the NEC remote's 21 buttons from the logic analyser's recording
    (sw/tests/data/sigrok/nec_all.sr, a receiver module's output, idle high); the receiver
    generated from that table, fed the recording, reports the same 21 frames, and none for
    the 30 repeat codes of the held keys (another shape: dropped)."""
    from recording import Recording, compress_idle
    model = _learned()
    khz = 1000
    _, image = build_rx(model.table("mailbox", pin=1), CTX=1, CLK_KHZ=khz)
    rec = Recording("nec_all.sr", 1e6 / khz, {0: 1})
    rec.records = compress_idle(rec.records, 120 * khz)       # idle cut to 120 ms
    rec.end = rec.records[-1][0]
    host = HostScript([], drain=[1], poll=khz)
    m = Machine(image, board=Board(rec, host), record=False)
    host.machine = m
    m.run(rec.end + 200 * khz, stop_when_idle=False)
    assert not m.ff.valid
    assert host.received[1] == [w for (w,) in model.frames]
    assert len(host.received[1]) == 21 and model.other == 30


def test_a_learned_receiver_reads_a_button_the_remote_never_sent():
    """A command the recording does not hold (0x10, address 0x00), sent by the transmitter
    from the learned table and read by the receiver from the same table."""
    model = _learned()
    assert all((w >> 16) & 0xFF != 0x10 for (w,) in model.frames)
    t = model.table("mailbox", pin=0)
    t["name"] = "learned_nec"
    khz = 1000
    tx, rx = escgen.generate(t), generate_rx(t)
    image = _two("learned_nec", tx, rx, f".equ learned_nec.CLK_KHZ, {khz}\n"
                 f".equ learned_nec_rx.CLK_KHZ, {khz}\n.equ learned_nec_rx.CTX, 1\n")
    word = 0xEF10FF00
    m = Machine(image, board=Board(pullups=0xFF), record=False)
    m.host_rx_push(0, word)
    got = []
    while m.now < 200 * khz and not got:
        m.run(5_000, stop_when_idle=False)
        m.host_tx_pop(0)
        v = m.host_tx_pop(1)
        if v is not None:
            got.append(v)
    assert not m.ff.valid and got == [word]
