"""firmware/eth_tx.asm, the 10BASE-T transmitter, on the ISS at 40 MHz: link pulses 16 ms
apart while idle, and a frame from the host as preamble, SFD, frame and the CRC-32 frame
check sequence, decoded here as 802.3 Manchester and checked against zlib."""

from __future__ import annotations

import zlib
from pathlib import Path

from escasm import assemble
from esciss import Machine

from test_line_ext import TD_N, TD_P, levels, to_bytes

FW = Path(__file__).resolve().parents[2] / "firmware"
MS = 40_000                                    # ticks per millisecond at 40 MHz


def pulses(m):
    """(first tick, length) of each run of TD+ driven high with TD- low."""
    p, n = levels(m, TD_P), levels(m, TD_N)
    out, t = [], 0
    while t < len(p):
        if p[t] == (1, 1) and n[t] == (1, 0):
            s = t
            while t < len(p) and p[t] == (1, 1) and n[t] == (1, 0):
                t += 1
            out.append((s, t - s))
        else:
            t += 1
    return out


def frame_bits(m, t0, period=4):
    """802.3 Manchester from tick t0: each bit's second half is its value."""
    p, n = levels(m, TD_P), levels(m, TD_N)
    bits, t = [], t0
    while t + period <= len(p):
        h1, h2 = p[t][1], p[t + period // 2][1]
        if not (p[t][0] and h1 != h2 and p[t + 1][1] == h1 and p[t + 3][1] == h2):
            break
        assert all(n[t + k][1] == 1 - p[t + k][1] for k in range(period))
        bits.append(h2)
        t += period
    return bits, t


def test_eth_tx_link_pulses_and_a_frame():
    m = Machine(assemble((FW / "eth_tx.asm").read_text(), base_dir=FW))
    frame = (bytes.fromhex("ffffffffffff 020000000001 0800")
             + bytes([0x45, 0x00]) + b"hello from escapement".ljust(44, b"\0"))
    assert len(frame) == 60
    words = [int.from_bytes(frame[i:i + 4], "little") for i in range(0, 60, 4)]
    m.run(MS // 10, stop_when_idle=False)                        # the first link pulse
    first = pulses(m)
    assert len(first) == 1 and first[0][1] == 4                     # 100 ns
    queue = [len(frame)] + words
    status = []
    while m.now < 20 * MS:
        while queue and len(m.ctx[0].rx) < 2:
            m.host_rx_push(0, queue.pop(0))
            m.run(1, stop_when_idle=False)
        m.run(4000, stop_when_idle=False)
        v = m.host_tx_pop(0)
        if v is not None:
            status.append(v)
    ps = pulses(m)
    p = levels(m, TD_P)
    # the frame: the first driven tick after the first pulse that is not a pulse
    t0 = next(t for t in range(first[0][0] + 4, len(p)) if p[t][0])
    bits, t_end = frame_bits(m, t0)
    data = to_bytes(bits)
    assert data[:8] == b"\x55" * 7 + b"\xd5"
    assert data[8:68] == frame and int.from_bytes(data[68:72], "little") == zlib.crc32(frame)
    assert len(bits) == 8 * 72
    tp = [s for s in ps if s[0] >= t_end - 4 and s[0] <= t_end + 8]
    assert tp and 10 <= tp[0][1] + (t_end - tp[0][0]) <= 16          # TP_IDL ~300 ns
    assert status and (status[0] >> 25) & 7 == 0 and (status[0] >> 28) & 7 == 4   # SEND OK
    # pulses resume 16 ms after the frame, then 16 ms apart
    after = [s for s in ps if s[0] > t_end + 20]
    assert after and abs(after[0][0] - t_end - 16 * MS) < 200
    assert all(x[1] == 4 for x in after)
    assert not m.ff.valid
