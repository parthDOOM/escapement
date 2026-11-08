"""LIN responder (firmware/lin_resp.asm) against a commander model on the open-drain bus
(escmodels.lin.LinCommander drives the headers) and the LinReceiver model, which decodes
whole frames from the bus levels and checks break, sync, identifier parity and checksum.
Most runs use a 4 MHz clock (the timing is in CLK_KHZ; escert proves the program at every
clock from 4 to 50 MHz)."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import Board, HostScript, LinCommander, LinReceiver, lin_checksum, lin_pid
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"


def image(khz):
    return assemble(f'.equ lin_resp.CLK_KHZ, {khz}\n.include "lin_resp.asm"\n', base_dir=FW)


def publish(slot, ident, data):
    """The host words that publish `data` for `ident` from `slot`."""
    words = [1 << 30 | slot << 12 | len(data) << 8 | ident]
    words += [2 << 30 | slot << 12 | i << 8 | b for i, b in enumerate(data)]
    return words + [3 << 30 | slot << 12]


def session(khz, config, headers, scale=1.0, baud=19200):
    """Configure through the mailbox (one word every 1.25 ms: the program looks at the host
    once a millisecond while the bus is idle), then let the commander send `headers`."""
    tick = 1e6 / khz
    pushes = [(2000 + k * khz * 5 // 4, 0, w) for k, w in enumerate(config)]
    start = 2000 + (len(config) + 2) * khz * 5 // 4
    cmd = LinCommander(0, tick, headers, baud=baud, start=start, scale=scale)
    rx = LinReceiver(0, tick, baud=baud)
    host = HostScript(pushes, drain=[0], poll=khz // 2)
    board = Board(cmd, rx, host)
    m = Machine(image(khz), board=board)
    host.machine = m
    bit = khz * 1000 / baud
    m.run(start + int((len(headers) * 200 + 40) * bit * scale), stop_when_idle=False)
    rx.finish()
    return m, rx, host, cmd, board


def answered(rx):
    return [(f["id"], f["data"], f["classic"]) for f in rx.frames]


def test_reference_values():
    """The models' identifier parity and checksum agree with LIN 2.x's worked values:
    PID of 0x10 is 0x50, of 0x3C is 0x3C; enhanced checksum of PID 0x50 with 0x12 0x34."""
    assert lin_pid(0x10) == 0x50 and lin_pid(0x3C) == 0x3C and lin_pid(0x00) == 0x80
    assert lin_checksum([0x12, 0x34], 0x50) == (~(0x50 + 0x12 + 0x34) & 0xFF)


@pytest.mark.parametrize("khz", [4000, 25000])
def test_answers_published_ids_only(khz):
    """Two published IDs (enhanced checksum), one unpublished: the responder answers exactly
    the published ones, with the data and checksum the receiver model checks, and reports
    every header to the host with [8] set for those it answered."""
    cfg = publish(0, 0x10, [0x12, 0x34]) + publish(2, 0x21, [0xA5, 0x00, 0xFF, 0x5A, 1, 2, 3, 4])
    heads = [lin_pid(0x10), lin_pid(0x11), lin_pid(0x21)]
    if khz == 25000:
        cfg, heads = publish(0, 0x10, [0x12, 0x34]), [lin_pid(0x10)]
    m, rx, host, _, board = session(khz, cfg, heads)
    want = [(0x10, [0x12, 0x34], False)] + ([(0x21, [0xA5, 0x00, 0xFF, 0x5A, 1, 2, 3, 4], False)]
                                           if khz == 4000 else [])
    assert answered(rx) == want, (answered(rx), rx.violations)
    # the unpublished header is a header with no response: the only receiver complaint
    assert all("frame [85, " in v for v in rx.violations), rx.violations
    assert len(rx.violations) == len(heads) - len(want)
    reports = host.received[0]
    assert [r & 0xFF for r in reports] == heads
    assert [r >> 8 for r in reports] == [1 if (h & 0x3F) in (0x10, 0x21) else 0 for h in heads]
    assert not board.errors() and not m.ff.valid


def test_diagnostic_id_uses_the_classic_checksum():
    """ID 0x3C (master request) and 0x3D: the checksum covers the data only."""
    m, rx, host, _, board = session(4000, publish(1, 0x3D, [0x7F, 0x06, 0xB2, 0, 0, 0, 0, 0]),
                                    [lin_pid(0x3D)])
    assert answered(rx) == [(0x3D, [0x7F, 0x06, 0xB2, 0, 0, 0, 0, 0], True)], rx.violations
    assert not rx.violations and not m.ff.valid


def test_parity_error_and_short_break_are_not_answered():
    """A header whose identifier has a wrong parity bit, and one whose break is only 10 bit
    times, get no response; the next good header is answered."""
    bad = lin_pid(0x10) ^ 0x80
    heads = [bad, {"pid": lin_pid(0x10), "brk": 10}, lin_pid(0x10)]
    m, rx, host, _, board = session(4000, publish(0, 0x10, [0xC3]), heads)
    assert answered(rx) == [(0x10, [0xC3], False)]
    # the parity error is a header the program read and reported unanswered; the short break
    # is not a header at all (the receiver model ignores it too, it needs 11 bits)
    assert [r for r in host.received[0]] == [bad, lin_pid(0x10) | 0x100]
    assert not m.ff.valid


@pytest.mark.parametrize("scale", [0.98, 1.02])
def test_commander_clock_off_by_two_percent(scale):
    """LIN 2.x allows a commander and a responder without synchronisation to differ by up
    to 2% in bit rate: headers from a commander 2% fast or slow are read and answered."""
    m, rx, host, _, board = session(4000, publish(0, 0x10, [0x12, 0x34]), [lin_pid(0x10)] * 2,
                                    scale=scale)
    assert answered(rx) == [(0x10, [0x12, 0x34], False)] * 2, rx.violations
    assert not m.ff.valid


def test_response_starts_one_bit_after_the_identifier():
    """The response's start bit falls one nominal bit after the identifier's stop bit ends
    (a response space of one bit), to within the receive edge's 4-tick slot."""
    khz = 4000
    m, rx, host, cmd, board = session(khz, publish(0, 0x10, [0x00]), [lin_pid(0x10)])
    bit = khz * 1000 / 19200
    t0 = cmd.starts[0]
    stop_end = t0 + round(14 * bit) + round(20 * bit)          # break, delimiter, two bytes
    lv = board.level(0)
    fall = next(t for t in range(stop_end, len(lv)) if lv[t - 1] == 1 and lv[t] == 0)
    assert abs((fall - stop_end) - bit) <= 20, (fall - stop_end, bit)


def test_certificate_matches_telemetry():
    from escert import certify
    certs = certify(image(4000))
    assert [(c.status, c.min_lead) for c in certs] == [("CHECKED", 13)]
    m, *_ = session(4000, publish(0, 0x10, [0x12, 0x34]), [lin_pid(0x10)])
    assert m.ctx[0].min_lead >= certs[0].min_lead
