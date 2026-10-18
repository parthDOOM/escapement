"""Capture files, VCD and sigrok session export and import, and replay from a trace file
(sw/esctape/export.py, python -m esctape). A UART peer is captured on the ISS; the trace
must survive every round trip edge for edge, decode to the same bytes, and replay from
the exported file tick for tick.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile

import pytest

from escasm import Image
from escmodels import Board, UartRx, UartTx
from esciss import Machine
from esctape import decode_capture, decode_records, edges
from esctape.__main__ import main as cli
from esctape.export import (load_capture, read_sr, read_trace, read_vcd, save_capture,
                            write_sr, write_vcd)

from test_tape_replay import MSG, TPB, capture


def uart_bytes(pin_edges, tpb):
    """8N1 bytes from (tick, level) edges of an idle-high line."""
    def level(t):
        return [lv for u, lv in pin_edges if u <= t][-1]
    out, i = [], 0
    while i < len(pin_edges):
        t0, lv = pin_edges[i]
        if lv != 0 or i == 0:
            i += 1
            continue
        out.append(sum(level(t0 + (k + 1.5) * tpb) << k for k in range(8)))
        i += 1
        while i < len(pin_edges) and pin_edges[i][0] <= t0 + 9.5 * tpb:
            i += 1
    return bytes(out)


@pytest.fixture(scope="module")
def uart_capture():
    peer = UartTx(1, TPB, MSG, start=300, gap_bits=0.0)
    m = capture(Board(peer), int(300 + len(MSG) * 10 * TPB + 400))
    st, cfg = m.dbg_read(0, 17), m.dbg_read(0, 18)
    from esctape import capture_order
    words = capture_order(m.tape, st, cfg)
    recs = decode_capture(m.tape, st, cfg)
    assert uart_bytes(edges(recs, 1), TPB) == MSG
    return words, recs


def test_capture_file_round_trip(tmp_path, uart_capture):
    words, recs = uart_capture
    names = ["d0", "uart_tx"] + [f"p{i}" for i in range(2, 8)]
    save_capture(tmp_path / "c.json", words, names=names)
    d = load_capture(tmp_path / "c.json")
    assert d["words"] == words and d["names"] == names and d["clock_hz"] == 25_000_000
    assert decode_records(d["words"]) == recs


def test_vcd_round_trip_is_edge_exact(tmp_path, uart_capture):
    _, recs = uart_capture
    write_vcd(recs, tmp_path / "c.vcd")
    text = (tmp_path / "c.vcd").read_text()
    assert "$timescale 1 ns $end" in text and "$var wire 1 \" pin1 $end" in text
    back, names = read_vcd(tmp_path / "c.vcd")
    assert names == [f"pin{i}" for i in range(8)]
    assert edges(back, 1) == edges(recs, 1)
    assert uart_bytes(edges(back, 1), TPB) == MSG


def test_sr_round_trip(tmp_path, uart_capture):
    _, recs = uart_capture
    n = write_sr(recs, tmp_path / "c.sr")
    with zipfile.ZipFile(tmp_path / "c.sr") as z:
        assert z.read("version") == b"2"
        meta = z.read("metadata").decode()
        assert "capturefile=logic-1" in meta and "samplerate=25 MHz" in meta
        assert "unitsize=1" in meta and len(z.read("logic-1")) == n
    back, _ = read_sr(tmp_path / "c.sr")
    t0 = recs[0][0]                                   # samples start at the first record
    assert [(t + t0, lv) for t, lv in edges(back, 1)] == edges(recs, 1)


def test_sr_decimated_keeps_every_edge_within_div(tmp_path, uart_capture):
    _, recs = uart_capture
    write_sr(recs, tmp_path / "c4.sr", div=4)
    meta = zipfile.ZipFile(tmp_path / "c4.sr").read("metadata").decode()
    assert "samplerate=6250 kHz" in meta
    back, _ = read_sr(tmp_path / "c4.sr")
    t0, want = recs[0][0], edges(recs, 1)
    got = [(t + t0, lv) for t, lv in edges(back, 1)]
    assert [lv for _, lv in got] == [lv for _, lv in want]
    assert all(0 <= g[0] - w[0] < 4 for g, w in zip(got, want))
    assert uart_bytes(got, TPB) == MSG


def test_foreign_vcd(tmp_path):
    """A VCD as other tools write it: 10 ns units, a vector, x at time 0, named signals."""
    (tmp_path / "f.vcd").write_text("""$date today $end
$timescale 10ns $end
$scope module top $end
$var wire 8 % bus [7:0] $end
$var wire 1 a clk $end
$var wire 1 b data $end
$upscope $end
$enddefinitions $end
#0
$dumpvars
bxxxxxxxx %
xa
1b
$end
#4
1a
b00000001 %
#8
0a
0b
#12
1a
""")
    recs, names = read_vcd(tmp_path / "f.vcd", signals=["data", "clk"])
    assert names == ["data", "clk"]
    # 10 ns = 0.25 tick at 25 MHz: #4 -> tick 1, #8 -> 2, #12 -> 3
    assert recs == [(0, 0b01), (1, 0b11), (2, 0b00), (3, 0b10)]


def test_replay_from_an_exported_vcd(tmp_path, uart_capture):
    """Capture -> VCD file -> replay image (CLI) -> the chip re-sends the same bytes with
    the captured edge spacing."""
    _, recs = uart_capture
    write_vcd(recs, tmp_path / "c.vcd")
    assert cli(["replay", str(tmp_path / "c.vcd"), "--pins", "1", "--lead", "300",
                "-o", str(tmp_path / "r.json")]) == 0
    image = Image.from_json((tmp_path / "r.json").read_text())
    rx = UartRx(1, TPB)
    board = Board(rx)
    r = Machine(image, board=board)
    r.run(int(len(MSG) * 10 * TPB + 2000), stop_when_idle=False)
    assert not r.ff.valid and bytes(rx.bytes) == MSG
    lv = board.level(1)
    out = [t for t in range(1, len(lv)) if lv[t] != lv[t - 1]]
    cap = [t for t, _ in edges(recs, 1)[1:]]
    assert [b - a for a, b in zip(out, out[1:])] == [b - a for a, b in zip(cap, cap[1:])]


def test_export_cli_and_refusal(tmp_path, uart_capture, capsys):
    words, recs = uart_capture
    save_capture(tmp_path / "c.json", words)
    assert cli(["export", str(tmp_path / "c.json"), "-o", str(tmp_path / "c.sr")]) == 0
    assert cli(["export", str(tmp_path / "c.json"), "-o", str(tmp_path / "c.vcd")]) == 0
    assert edges(read_trace(tmp_path / "c.sr")[0], 1)[1:] == \
        [(t - recs[0][0], lv) for t, lv in edges(recs, 1)[1:]]
    # 300 edges cannot fit the 256-word tape: refused, with the reason
    dense = [(1000 + 400 * k, 0x02 * (k & 1)) for k in range(300)]
    write_vcd(dense, tmp_path / "d.vcd")
    assert cli(["replay", str(tmp_path / "d.vcd"), "--pins", "1", "-o", str(tmp_path / "x.json")]) == 1
    assert "the tape holds 1..256" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("sigrok-cli") is None, reason="sigrok-cli not installed")
def test_sigrok_cli_decodes_the_export(tmp_path, uart_capture):
    _, recs = uart_capture
    write_sr(recs, tmp_path / "c.sr")
    out = subprocess.run(["sigrok-cli", "-i", str(tmp_path / "c.sr"), "-P",
                          f"uart:rx=pin1:baudrate={round(25e6 / TPB)}", "-A", "uart=rx-data"],
                         capture_output=True, text=True, check=True).stdout
    got = bytes(int(line.split()[-1], 16) for line in out.splitlines() if line.strip())
    assert got == MSG
