"""Protocols as data (sw/escgen, protocols/*.yaml): each table generates firmware that
escert CHECKs, that matches the file in firmware/ (no drift), and that drives its bus
model correctly on the ISS: UART and MIDI bytes, DMX512 packets with E1.11 break and
mark-after-break times, WS2812 frames with datasheet pulse widths.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import escgen
from escasm import assemble
from escmodels import Board, DmxRx, HostScript, UartRx, Ws2812Decoder
from esciss import Machine

ROOT = Path(__file__).resolve().parents[2]


def table(name):
    return escgen.load(ROOT / "protocols" / f"{name}.yaml")


def test_generated_firmware_is_up_to_date():
    from escgen.__main__ import main
    assert main(["--check-only"]) == 0


def test_a_late_table_is_refused():
    t = table("uart_tx_gen")
    t["constants"] = {"BIT": "3"}                 # 3 ticks per bit: no program keeps up
    with pytest.raises(escgen.TableError, match="not CHECKED"):
        escgen.build(t)


def test_bad_tables_are_rejected():
    t = table("uart_tx_gen")
    t["symbols"]["bit"] = [[{"TX": "data", "XX": 1}, "BIT"]]
    with pytest.raises(escgen.TableError):
        escgen.generate(t)


@pytest.mark.parametrize("name, baud", [("uart_tx_gen", 115200), ("midi_tx", 31250)])
def test_serial_bytes(name, baud):
    _, image = escgen.build(table(name))
    msg = [0x90, 0x3C, 0x7F, 0x80, 0x3C, 0x00, 0x55, 0xAA]
    frame = int(11 * 25e6 / baud)                 # the mailbox holds two words: one per frame
    host = HostScript([(100 + frame * i, 0, b) for i, b in enumerate(msg)])
    rx = UartRx(0, 25e6 / baud)
    m = Machine(image, board=Board(rx, host))
    host.machine = m
    m.run(frame * (len(msg) + 2), stop_when_idle=False)
    assert rx.bytes == msg and not rx.framing_errors
    assert not m.ff.valid


def test_dmx512_packets():
    """At CLK_KHZ 5000 (a bit is 20 ticks) so two whole packets fit a short run."""
    t = table("dmx512_tx")
    _, image = escgen.build(t, CLK_KHZ=5000)
    slots = [0] + [(7 * i + 1) & 0xFF for i in range(1, 512)]
    for w in range(128):
        image.tape[w] = sum(slots[4 * w + j] << (8 * j) for j in range(4))
    rx = DmxRx(0, clock_hz=5e6)
    m = Machine(image, board=Board(rx))
    m.run(250_000, stop_when_idle=False)
    assert len(rx.packets) >= 1 and not rx.violations, rx.violations
    p = rx.packets[0]
    assert p["slots"] == slots                    # start code 0, then 511 channels
    assert p["break_us"] >= 92 and p["mab_us"] >= 12
    assert not m.ff.valid


def test_ws2812_frames():
    _, image = escgen.build(table("ws2812_gen"))
    colours = [0x00FF00, 0xFF0000, 0x0000FF, 0x123456, 0xABCDEF]
    image.tape[0] = len(colours)
    for i, c in enumerate(colours):
        image.tape[1 + i] = c
    dec = Ws2812Decoder(0, reset_us=280)
    m = Machine(image, board=Board(dec))
    m.run(40_000, stop_when_idle=False)
    assert dec.frames[:2] == [colours, colours] and not dec.violations, dec.violations
    assert not m.ff.valid


def test_nec_frames_from_a_table():
    """Carrier bursts and pulse-distance bits from a table, at a 2.5 MHz timebase (the
    receiver model's tick is 400 ns): two frames decoded, 342 + 33 x 22 carrier pulses
    each, every burst at its nominal distance, 1/3 duty, one acknowledgement each."""
    from escmodels import NecReceiver
    _, image = escgen.build(table("nec_tx_gen"), CLK_KHZ=2500)
    frames = [(0x5A, 0xC3), (0x00, 0xFF)]
    raw = [a | ((a ^ 0xFF) << 8) | (c << 16) | ((c ^ 0xFF) << 24) for a, c in frames]
    host = HostScript([(100, 0, raw[0]), (200, 0, raw[1])], drain=[0], poll=1000)
    rx = NecReceiver(0, tick_ns=400.0)
    board = Board(rx, host)
    m = Machine(image, board=board)
    host.machine = m
    m.run(2 * (33_750 + 32 * 5_625 + 1_500 + 100_000) + 10_000, stop_when_idle=False)  # + 40 ms gaps
    assert rx.frames() == frames, rx.events
    assert not board.errors(), board.errors()
    assert rx.pulses == ([342] + [22] * 33) * 2
    starts = [s for s, _ in rx.marks]
    assert starts[1] - starts[0] == 2500 * 27 // 2              # leader to first bit, exact
    for i in range(32):
        bit = raw[0] >> i & 1
        assert starts[i + 2] - starts[i + 1] == (2500 * 9 // 4 if bit else 2500 * 9 // 8)
    assert host.received[0] == [1, 1] and not m.ff.valid
