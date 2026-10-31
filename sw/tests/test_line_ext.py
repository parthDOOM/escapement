"""The v1.6 extended line unit configuration (docs/11-SEMANTICS.md §6.6, `ext`) on the ISS.

Checked against references outside the line unit: an 802.3 Manchester decoder written
here from the standard's definition (the second half of each bit cell is the bit), the
frame check sequence from zlib's CRC-32, the WS2812 decoder model in `escmodels`, and SPI
sampled at the clock's rising edges. Context 0 runs the programs; the configuration block
sits in tape words 0xF0..0xF4.
"""

from __future__ import annotations

import zlib

from escmodels import Board, Ws2812Decoder

from test_line import DATA_AT, bytes_to_words, li, run, w0, w1, w2

CFG_AT = 0xF0
TD_P, TD_N = 0, 1                     # 10BASE-T TD+ and TD-


def w2x(**kw) -> int:
    """W2 with ext set."""
    return w2(**kw) | 1 << 25


def w3(cells=1, p0=0, p1=1, clk=0, pc=0, cidle=0) -> int:
    return (cells - 1) | p0 << 3 | p1 << 11 | clk << 19 | pc << 20 | cidle << 28


def w4(poly_hi=0, prep=0, eopl=0, ilow=0) -> int:
    return poly_hi | prep << 16 | eopl << 19 | ilow << 20


# 10BASE-T at 40 MHz: a bit is 100 ns = 4 ticks, two Manchester cells of 2 ticks; 7 x 0x55
# then the SFD 0xD5 as the header (outside the CRC); CRC-32 (0x04C11DB7) from all ones,
# inverted on the wire; bytes LSB first; after the frame 3 bit times of L = 1 (TP_IDL),
# then the pins are released.
ETH = (w0(d=TD_P, n=TD_N, diff=1, nrzi=0, lsb=1, N=0, eop=3, idle_drive=0, sync_skip=0,
          prefix=0x55),
       w1(P=2, S=0),
       w2x(poly=0x1DB7, init_ones=1, inv=1, hdr=1, idle_min=0),
       w3(cells=2, p0=0b01, p1=0b10),
       w4(poly_hi=0x04C1, prep=6, eopl=1))
# a link pulse (NLP): no prefix, header or data; one bit time of L = 1, then release
NLP = (w0(d=TD_P, n=TD_N, diff=1, nrzi=0, lsb=1, N=0, eop=1, idle_drive=0, sync_skip=0,
          prefix=0),
       w1(P=2, S=0),
       w2x(poly=0, init_ones=0, inv=0, hdr=0, idle_min=0),
       w3(cells=2, p0=0b01, p1=0b10),
       w4(eopl=1))


def program(code: str, cfg, tape=None, own=(0, 1)) -> str:
    t = {CFG_AT + i: w for i, w in enumerate(cfg)}
    t.update(tape or {})
    out = [".entry 0, main"] + [f".own {p}, 0" for p in own]
    out += [f".tape {a}, {v:#x}" for a, v in sorted(t.items())]
    out += ["main:", li(0, (3 << 28) | CFG_AT), " LINE r0", " ADDT 0xFFFFFF", " GETL r1", code]
    return "\n".join(out)


def send_op(nbits: int, hdr: int = 0, crc: int = 0, word: int = DATA_AT) -> int:
    return word | (nbits & 0x7F) << 8 | crc << 15 | hdr << 16 | (nbits >> 7) << 24


def levels(m, pin: int):
    """(driven, level) of a pin during each tick, from the ISS's pin history."""
    return [((p["uio_oe"] >> pin) & 1, (p["uio_out"] >> pin) & 1) for p in m.pins]


def manchester_frame(m, period=4):
    """Decode one 10BASE-T transmission on TD+/TD- (802.3 clause 7/14: a bit's second half
    carries its value, the first half the complement; TD- is the complement of TD+).
    Returns (bits, first tick, tick after the last bit)."""
    p, n = levels(m, TD_P), levels(m, TD_N)
    t0 = next(t for t in range(len(p)) if p[t][0])      # released until the frame
    bits, t = [], t0
    while True:
        h1, h2 = [p[t + k][1] for k in range(period // 2)], [p[t + period // 2 + k][1] for k in range(period // 2)]
        if not (len(set(h1)) == 1 and len(set(h2)) == 1 and h1[0] != h2[0]):
            break
        for k in range(period):
            assert n[t + k][1] == 1 - p[t + k][1] and n[t + k][0] and p[t + k][0]
        bits.append(h2[0])
        t += period
    return bits, t0, t


def to_bytes(bits):
    return bytes(sum(bits[8 * i + j] << j for j in range(8)) for i in range(len(bits) // 8))


# ----------------------------------------------------------------------------- Ethernet


def test_10base_t_frame_has_preamble_sfd_payload_and_the_zlib_fcs():
    dst, src = bytes.fromhex("ffffffffffff"), bytes.fromhex("020000000001")
    payload = bytes(range(46))
    frame = dst + src + b"\x08\x00" + payload                    # 60 bytes, the minimum
    nbits = 8 * len(frame)
    code = (li(2, send_op(nbits, hdr=0xD5, crc=1)) + " ADDT 64\n SEND r2\n ADDT 0xFFFFFF\n"
            " GETL r3\n HALT")
    m = run(program(code, ETH, tape=bytes_to_words(frame)), ticks=3000, stop_when_idle=False)
    bits, t0, t_end = manchester_frame(m)
    data = to_bytes(bits)
    assert data[:7] == b"\x55" * 7 and data[7] == 0xD5              # preamble, SFD
    body, fcs = data[8:8 + len(frame)], data[8 + len(frame):]
    assert body == frame and len(fcs) == 4
    assert int.from_bytes(fcs, "little") == zlib.crc32(frame)        # 802.3 FCS
    assert len(bits) == 8 * (8 + len(frame) + 4)
    # TP_IDL: 3 bit times of TD+ high (the frame's last bit may extend into it), then both
    # pins released
    p = levels(m, TD_P)
    tp = [t for t in range(t_end, t_end + 40) if p[t] == (1, 1)]
    assert tp and p[tp[-1] + 1][0] == 0 and 10 <= len(tp) <= 14
    assert all(p[t][0] == 0 for t in range(tp[-1] + 1, len(p)))


def test_link_pulse_is_one_100ns_positive_pulse():
    code = li(2, send_op(0)) + " ADDT 64\n SEND r2\n ADDT 0xFFFFFF\n GETL r3\n HALT"
    m = run(program(code, NLP), ticks=600)
    p, n = levels(m, TD_P), levels(m, TD_N)
    driven = [t for t in range(len(p)) if p[t][0]]
    pulse = [t for t in driven if p[t][1] == 1 and n[t][1] == 0]
    # the idle state after CONFIG is released (idle_drive = 0): only the pulse is driven
    assert pulse == driven and len(pulse) == 4                       # 4 ticks = 100 ns
    assert pulse == list(range(pulse[0], pulse[0] + 4))


# ----------------------------------------------------------------------------- SPI clock


def test_clock_pattern_on_n_shifts_spi_mode_0_at_10_mbit():
    """MOSI on pin 0, SCK on pin 1: two cells of 2 ticks per bit, SCK low then high, so the
    data is stable at every rising edge; MSB first."""
    cfg = (w0(d=0, n=1, diff=1, nrzi=0, lsb=0, N=0, eop=0, idle_drive=1, sync_skip=0, prefix=0),
           w1(P=2, S=0), w2x(poly=0, init_ones=0, inv=0, hdr=0, idle_min=0),
           w3(cells=2, p0=0b00, p1=0b11, clk=1, pc=0b10, cidle=0), w4())
    data = bytes([0xA5, 0x3C, 0x81, 0x7E, 0x00, 0xFF])
    code = li(2, send_op(8 * len(data))) + " ADDT 64\n SEND r2\n ADDT 0xFFFFFF\n GETL r3\n HALT"
    m = run(program(code, cfg, tape=bytes_to_words(data, lsb=False)), ticks=2000)
    mosi, sck = levels(m, 0), levels(m, 1)
    rises = [t for t in range(1, len(sck)) if sck[t] == (1, 1) and sck[t - 1][1] == 0]
    got = [mosi[t][1] for t in rises]
    want = [(b >> (7 - i)) & 1 for b in data for i in range(8)]
    assert got == want
    assert all(b - a == 4 for a, b in zip(rises, rises[1:]))           # 4 ticks per bit
    assert sck[rises[-1] + 2] == (1, 0)                                # SCK idles low after


# ----------------------------------------------------------------------------- WS2812


def test_pulse_code_drives_ws2812_from_one_send_at_33_mhz():
    """A WS2812 bit as three cells (0: high one cell, 1: high two), 13.75 ticks a cell at
    33 MHz (0.417 us); the decoder model checks every pulse against the datasheet."""
    tick = 1e3 / 33.0
    cfg = (w0(d=0, diff=0, nrzi=0, lsb=0, N=0, eop=0, idle_drive=1, sync_skip=0, prefix=0),
           w1(P=13, frac=192, S=4), w2x(poly=0, init_ones=0, inv=0, hdr=0, idle_min=0),
           w3(cells=3, p0=0b001, p1=0b011), w4(ilow=1))          # WS2812 idles low
    grb = [0x00FF00, 0x123456, 0xABCDEF]
    data = b"".join(c.to_bytes(3, "big") for c in grb)
    code = li(2, send_op(8 * len(data))) + " ADDT 64\n SEND r2\n ADDT 0xFFFFFF\n GETL r3\n HALT"
    dec = Ws2812Decoder(0, tick_ns=tick, reset_us=60)
    m = run(program(code, cfg, tape=bytes_to_words(data, lsb=False), own=(0,)), ticks=6000,
            board=Board(dec), stop_when_idle=False)
    dec.finish()                                   # the frame latches after its reset gap
    assert dec.frames and dec.frames[0] == grb and not dec.violations, (dec.frames, dec.violations)


# ----------------------------------------------------------------------------- CRC-32, RECV


def test_crc32_job_wire_order_matches_zlib():
    cfg = ETH[:2] + (ETH[2] & ~(1 << 18),) + ETH[3:]                   # (header off: no matter)
    data = b"123456789"
    cmd = (2 << 28) | (1 << 16) | ((8 * len(data)) << 8) | DATA_AT
    m = run(program(li(0, cmd) + " LINE r0\n ADDT 0xFFFFFF\n GETL r1\n HALT", cfg,
                    tape=bytes_to_words(data)))
    assert zlib.crc32(data) == 0xCBF43926
    assert m.lu.status() & 0xFFFF == 0x3926                           # its first 16 bits


def test_recv_with_a_symbol_coding_ends_at_once():
    cmd = (1 << 28) | DATA_AT
    m = run(program(li(0, cmd) + " LINE r0\n ADDT 0xFFFFFF\n GETL r1\n HALT", ETH))
    c = [x for x in m.commits if x["ctx"] == 0 and x["outcome"] == "C"]
    line = next(x for x in c if x["insn"] >> 24 == 0x3C and x["t"] > 40)
    assert m.lu.status() == (1 << 28) | (1 << 25) | 0xFFFF             # RECV, VIOL, 0; crc start
    assert m.lu.e == line["t"] and not m.lu.busy()
