"""v1.1 record -> replay, end to end on the ISS (firmware/logic_analyzer.asm, replay.asm).

A UART peer talks; the logic analyser captures pin 1 into the tape; the host decodes the
tape and builds a replay image; the chip then re-emits the waveform with no peer present.
The replayed edges must have exactly the captured spacing (tick for tick), and a UART
decoder must read the same bytes. A trace too dense to replay must be refused, not
replayed late.
"""

from __future__ import annotations

from pathlib import Path

from escasm import assemble
from escmodels import Board, UartRx, UartTx
from esciss import Machine
from esctape import build_replay, decode_capture, edges

FW = Path(__file__).resolve().parents[2] / "firmware"
TPB = 25e6 / 115200
MSG = b"Esc!"


def capture(board, ticks, mask=0x02):
    src = f'.equ logic_analyzer.MASK, {mask}\n.include "logic_analyzer.asm"\n'
    m = Machine(assemble(src, base_dir=FW), board=board)
    m.run(ticks, stop_when_idle=False)
    m.stop()
    m.run(1)
    return m


def test_capture_decodes_uart_edges():
    peer = UartTx(1, TPB, MSG, start=300, gap_bits=0.0)
    m = capture(Board(peer), 4000)
    recs = decode_capture(m.tape, m.dbg_read(0, 17), m.dbg_read(0, 18))
    assert not (m.dbg_read(0, 17) >> 30) & 1            # no overflow
    e = edges(recs, 1)
    assert e[0] == (12, 1)                               # snapshot at arming: idle high
    assert e[1][1] == 0 and 300 <= e[1][0] <= 304        # start bit, + 2-tick sync


def test_record_then_replay_is_tick_exact():
    peer = UartTx(1, TPB, MSG, start=300, gap_bits=0.0)
    m = capture(Board(peer), int(300 + len(MSG) * 10 * TPB + 400))
    recs = decode_capture(m.tape, m.dbg_read(0, 17), m.dbg_read(0, 18))
    image = build_replay(recs, pins=0x02, lead=300)

    rx = UartRx(1, TPB)
    board = Board(rx)
    r = Machine(image, board=board)
    r.run(int(len(MSG) * 10 * TPB + 2000), stop_when_idle=False)
    assert not r.ff.valid, r.final_state()["first_fault"]
    assert bytes(rx.bytes) == MSG
    # tick-exact: the replayed pin's edge spacing equals the captured spacing
    lv = board.level(1)
    out = [(t, lv[t]) for t in range(1, len(lv)) if lv[t] != lv[t - 1]]
    cap = edges(recs, 1)[1:]                             # changes after the snapshot
    assert len(out) == len(cap)
    assert [b[0] - a[0] for a, b in zip(out, out[1:])] == \
           [b[0] - a[0] for a, b in zip(cap, cap[1:])]


def test_too_dense_trace_is_refused_not_late():
    # 10 ticks between edges: denser than the replay loop's 40 ticks/record
    recs = [(1000 + 10 * k, 0x02 * (k & 1)) for k in range(40)]
    image = build_replay(recs, pins=0x02, lead=300)
    r = Machine(image)
    r.run(5000)
    f = r.final_state()["first_fault"]
    assert f["valid"] and f["cause"] == 3 and f["subcode"] in (3, 4)   # ADMIT/PAST|ORDER
