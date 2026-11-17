"""ethbench.py under CPython: firmware/eth_tx.asm at 40 MHz on the simulated chip of
sw/tests/demoboard_mock.py, behind barepico's wiring (test_barepico.GpioMachine), each frame
pushed by ethbench's precomputed burst. TD+ and TD- are decoded as 802.3 Manchester
(test_eth.frame_bits) and every frame is checked byte for byte with its CRC-32 against zlib.
A 10BASE-T receiver drops the link after 50 to 150 ms with no pulse or frame, so the
longest quiet gap on the wire must stay under 50 ms with slow register writes.

The receive bench (eth_rx_bench.asm: firmware/eth_rx.asm in context 0, eth_link.asm's link
pulses in context 1) runs on the ISS at 40 MHz against escmodels.EthSource: each frame must
arrive byte for byte with its FCS judged good, and the link pulses must be 100 ns long and
16 ms apart, with both-edge sampling and with one sample a tick.

The receive line check (eth_edges.asm, ethbench.edges): the capture unit records a frame's
edges on RXP and line_check() judges the high and low times; the cases include the three
the bench went through before it received (README.md, Ethernet).

    PYTHONPATH=<repo>/sw python -m pytest test_ethbench.py   (REPO env: the repo root)
"""
import json
import os
import random
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(os.environ.get("REPO", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(REPO / "sw" / "rp2350"))
sys.path.insert(0, str(REPO / "sw" / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import barepico  # noqa: E402
import ethbench  # noqa: E402
from demoboard_mock import MockBoard, SimChip  # noqa: E402
from escasm import assemble, assemble_file  # noqa: E402
from escmodels import Board, EthSource, HostScript, wire_bits  # noqa: E402
from esciss import Machine  # noqa: E402
from test_barepico import GpioMachine  # noqa: E402
from test_eth import frame_bits  # noqa: E402
from test_line_ext import to_bytes  # noqa: E402

TICKS_MS = 40_000                        # ticks per millisecond at 40 MHz
LINK_LOSS_MS = 50                        # the shortest link_loss_timer 802.3 allows


class WireChip(SimChip):
    """Keeps every change of TD+ and TD- (uio[1:0]) as (tick, out, oe), with the chip's
    per-tick pin record off: a run of 100 ms is 4 M ticks."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.changes, self._last = [], None

    def _start(self):
        super()._start()
        self.m.record = False

    def _board(self, t, out, oe):
        k = (out & 3, oe & 3)
        if k != self._last:
            self.changes.append((t, out & 3, oe & 3))
            self._last = k
        return super()._board(t, out, oe)


def driven(changes):
    """(first tick, tick after the last) of each run of TD+ driven."""
    out, start = [], None
    for t, _, oe in changes:
        if oe & 1 and start is None:
            start = t
        elif not oe & 1 and start is not None:
            out.append((start, t))
            start = None
    return out


def window(changes, t0, t1):
    """The ISS pin-record form (uio_out, uio_oe each tick) for ticks t0 to t1-1."""
    pins, k, cur = [], 0, (0, 0)
    for t in range(t0, t1):
        while k < len(changes) and changes[k][0] <= t:
            cur = changes[k][1:]
            k += 1
        pins.append({"uio_out": cur[0], "uio_oe": cur[1]})
    return SimpleNamespace(pins=pins)


@pytest.fixture(scope="module")
def bundles(tmp_path_factory):
    """eth_tx's bundle as make_bundle writes it, without the model prediction ethbench
    does not read."""
    image = assemble_file(REPO / "firmware" / "eth_tx.asm")
    used = [i for i, w in enumerate(image.words) if w]
    b = {"name": "eth_tx.asm", "words": image.words[: max(used) + 1],
         "tape": {str(a): image.tape[a] for a in image.tape_used},
         "cfg": [image.ctx_enable_mask, image.entry_pcs_packed, image.pin_owned_mask,
                 image.pin_owner_packed, image.pin_od_mask, image.ctx_bank_mask,
                 image.ctx_pair_mask]}
    d = tmp_path_factory.mktemp("bundles")
    (d / "eth_tx.json").write_text(json.dumps(b))
    return d


def run(bundles, op_ns, sys_hz=ethbench.SYS_HZ, count=2):
    chip = WireChip()
    board = MockBoard(chip, op_ns=op_ns)
    gm = GpioMachine(board)
    io = barepico.open_io(machine=gm, time_mod=board.time_mod, sys_hz=sys_hz)
    lines = []
    n = ethbench.run(count=count, interval_ms=5, bundles=str(bundles), io=io, out=lines.append)
    return n, chip, "\n".join(lines)


@pytest.mark.parametrize("op_ns", [10_000, 300])
def test_frames_on_the_wire(bundles, op_ns):
    """10 us a register write (slower than the bench is likely to be) keeps the line's
    longest quiet gap under 50 ms; 300 ns (faster than MicroPython can write) keeps every
    SCK phase at 4 core clocks or more."""
    n, chip, log = run(bundles, op_ns)
    assert n == 2, log
    assert chip.timing_violations == 0
    runs = driven(chip.changes)
    pulses = [r for r in runs if r[1] - r[0] == 4]          # link pulses: 100 ns
    frames = [r for r in runs if r[1] - r[0] > 64]
    assert len(frames) == 2 and len(pulses) >= 2, runs
    for k, (s, e) in enumerate(frames, start=1):
        bits, end = frame_bits(window(chip.changes, s, e + 1), 0)
        data = to_bytes(bits)
        want = ethbench.test_frame(k)
        assert data[:8] == b"\x55" * 7 + b"\xd5"
        assert data[8:68] == want
        assert int.from_bytes(data[68:72], "little") == zlib.crc32(want)
        assert len(bits) == 8 * 72
    gaps = [b[0] - a[1] for a, b in zip(runs, runs[1:])]
    print("op_ns %d: longest quiet gap %.1f ms" % (op_ns, max(gaps) / TICKS_MS))
    assert max(gaps) < LINK_LOSS_MS * TICKS_MS, (max(gaps) / TICKS_MS, log)
    assert "frame 2: 60 bytes on the wire" in log and "tripwires clear" in log


def test_frame_layout():
    f = ethbench.test_frame(7)
    assert len(f) == 60 and f[:6] == b"\xff" * 6 and f[12:14] == b"\x88\xb5"
    assert f[14:34] == b"escapement eth_tx 7\0"
    w = ethbench.frame_words(f)
    assert w[0] == 60 and len(w) == 16 and w[1] == 0xFFFFFFFF
    t = ethbench.burst_table(w, 0, barepico.UI_SHIFT)
    assert len(t) == 16 * (2 + 48 * 3)


def test_wrong_clock_is_refused(bundles):
    """At the RP's default 125 MHz the PWM cannot make 40 MHz: ethbench stops."""
    n, chip, log = run(bundles, 1000, sys_hz=125_000_000, count=1)
    assert n == -1 and "not 40 MHz" in log, log


# ------------------------------------------------------------------ the receive bench

HERE = Path(__file__).resolve().parent
RX_BIT_HT = 8                            # 100 ns at 40 MHz, in half ticks


def rx_image(both):
    return assemble(f".equ eth_rx_bench.BOTH, {both}\n" + '.include "eth_rx_bench.asm"\n',
                    base_dir=HERE, name="eth_rx_bench.asm")


class PinLog(Board):
    """A Board that keeps every change of uio[1:0] as (tick, out, oe), and no history."""

    def __init__(self, *devices):
        super().__init__(*devices, record=False)
        self.changes, self._last = [], None

    def __call__(self, t, uio_out, uio_oe):
        k = (uio_out & 3, uio_oe & 3)
        if k != self._last:
            self.changes.append((t,) + k)
            self._last = k
        return super().__call__(t, uio_out, uio_oe)


@pytest.mark.parametrize("both", [1, 0])
def test_rx_bench_receives_frames_and_keeps_the_link(both):
    rng = random.Random(both)
    sent = [bytes.fromhex("ffffffffffff 0a0b0c0d0e0f 0806")
            + bytes(rng.randrange(256) for _ in range(n - 14)) for n in (60, 342)]
    sched, t = [], 4000
    for f in sent:
        bits = wire_bits(f)
        sched.append((t, bits))
        t += len(bits) * RX_BIT_HT // 2 + 4000
    src = EthSource(2, sched, RX_BIT_HT, ppm=100.0)
    host = HostScript([], drain=[0], poll=4)
    board = PinLog(src, host)
    m = Machine(rx_image(both), board=board, half=src.half, record=False)
    host.machine = m
    m.run(17 * TICKS_MS, stop_when_idle=False)       # past the second link pulse
    words, got = list(host.received[0]), []
    while words:
        h = words.pop(0)
        n = ((h & 0x3FFF) + 31) // 32
        data = b"".join(w.to_bytes(4, "little") for w in words[:n])
        del words[:n]
        got.append((h, data[:(h & 0x3FFF) // 8]))
    assert [d for _, d in got] == [f + zlib.crc32(f).to_bytes(4, "little") for f in sent]
    assert all(h >> 31 == 1 and (h >> 28) & 7 == 0 for h, _ in got)
    pulses = driven([(c[0], c[1], c[2]) for c in board.changes])
    assert [b - a for a, b in pulses] == [4, 4], pulses
    assert pulses[1][0] - pulses[0][0] == 16 * TICKS_MS
    assert all(c[1] == 1 for c in board.changes if c[2] == 3)    # TD+ high, TD- low
    assert not m.ff.valid


class RxChip(SimChip):
    """uio[2] carries a link partner's frames (an EthSource, idle high as the bench's bias
    network holds it), sampled on both edges; the chip's per-tick pin record off."""

    def __init__(self, src, **kw):
        super().__init__(**kw)
        self.src = src

    def _start(self):
        super()._start()
        self.m.record = False
        self.m.half = self.src.half

    def _board(self, t, out, oe):
        lvl = super()._board(t, out, oe)
        return (lvl & ~4) | (self.src.level(2 * self.m.now) << 2)


def test_listen_receives_through_the_bare_pico(tmp_path):
    """ethbench.listen(): eth_rx_bench at 40 MHz, two frames from the partner after setup,
    each popped from the mailbox and returned with its FCS judged good."""
    im = rx_image(1)
    used = [i for i, w in enumerate(im.words) if w]
    (tmp_path / "eth_rx_bench.json").write_text(json.dumps(
        {"words": im.words[: max(used) + 1], "tape": {str(a): im.tape[a] for a in im.tape_used},
         "cfg": [im.ctx_enable_mask, im.entry_pcs_packed, im.pin_owned_mask,
                 im.pin_owner_packed, im.pin_od_mask, im.ctx_bank_mask, im.ctx_pair_mask]}))
    arp = bytes.fromhex("ffffffffffff 0a0b0c0d0e0f 0806 0001080006040001 0a0b0c0d0e0f a9fe0102"
                        " 000000000000 a9fe7b2d") + bytes(18)
    udp = bytes.fromhex("ffffffffffff 0a0b0c0d0e0f 0800") + bytes(range(86))
    src = EthSource(2, [(30 * TICKS_MS, wire_bits(arp)), (60 * TICKS_MS, wire_bits(udp))],
                    RX_BIT_HT, ppm=-100.0)
    chip = RxChip(src)
    board = MockBoard(chip, op_ns=1000)
    io = barepico.open_io(machine=GpioMachine(board), time_mod=board.time_mod,
                          sys_hz=ethbench.SYS_HZ)
    lines = []
    got = ethbench.listen(seconds=1, count=2, bundles=str(tmp_path), io=io, out=lines.append)
    log = "\n".join(lines)
    assert got is not None, log
    assert [d for _, d in got] == [f + zlib.crc32(f).to_bytes(4, "little") for f in (arp, udp)], log
    assert "ARP who-has 169.254.123.45, from 169.254.1.2" in log
    assert "2 frame(s) received, 2 with a good FCS" in log


# ------------------------------------------------------------------ the receive line check

def records(runs, t=1000):
    """Capture records for a sequence of (level, ticks) runs on RXP."""
    out = []
    for lvl, d in runs:
        out.append((lvl << (24 + ethbench.RXP)) | t)
        t += d
    out.append(t)                        # the level after the last run
    return out


@pytest.mark.parametrize("runs, verdict", [
    # the bench's captures, in order: edges only (a loose capacitor), the bias above the
    # switching point, clean, then the laptop's lower Energy-Efficient Ethernet amplitude,
    # which eth_rx still decoded (README.md, Ethernet)
    ([(1, 3), (0, 1)] * 40 + [(1, 7), (0, 1)] * 8, "edges missed, lows short"),
    ([(1, 2), (0, 2)] * 30 + [(1, 3), (0, 1), (1, 7), (0, 1)] * 6, "edges missed, lows short"),
    ([(1, 2), (0, 2)] * 40 + [(1, 4), (0, 4), (1, 2), (0, 4), (1, 4), (0, 2)] * 6, "clean"),
    ([(1, 3), (0, 1)] * 40 + [(1, 5), (0, 3)] * 20, "decodable, no missed edge, but the lows"),
    ([(1, 1), (0, 3)] * 30 + [(1, 1), (0, 7)] * 5, "edges missed, highs short"),
    ([], "no frame seen"),
])
def test_line_check(runs, verdict):
    r = ethbench.line_check(records(runs) + [0] * 8)
    assert r["verdict"].startswith(verdict), r


def test_edges_through_the_bare_pico(tmp_path):
    """ethbench.edges(): eth_edges.asm at 40 MHz, a frame from the partner after setup;
    the capture fills during it, and a clean line (only 2- and 4-tick runs) is judged
    clean."""
    im = assemble_file(HERE / "eth_edges.asm")
    used = [i for i, w in enumerate(im.words) if w]
    (tmp_path / "eth_edges.json").write_text(json.dumps(
        {"words": im.words[: max(used) + 1], "tape": {str(a): im.tape[a] for a in im.tape_used},
         "cfg": [im.ctx_enable_mask, im.entry_pcs_packed, im.pin_owned_mask,
                 im.pin_owner_packed, im.pin_od_mask, im.ctx_bank_mask, im.ctx_pair_mask]}))
    f = bytes.fromhex("ffffffffffff 0a0b0c0d0e0f 0800") + bytes(range(86))
    src = EthSource(2, [(30 * TICKS_MS, wire_bits(f))], RX_BIT_HT)
    chip = RxChip(src)
    board = MockBoard(chip, op_ns=1000)
    io = barepico.open_io(machine=GpioMachine(board), time_mod=board.time_mod,
                          sys_hz=ethbench.SYS_HZ)
    lines = []
    r = ethbench.edges(seconds=1, bundles=str(tmp_path), io=io, out=lines.append)
    log = "\n".join(lines)
    assert r is not None, log
    assert set(r["high"]) <= {2, 4} and set(r["low"]) <= {2, 4}, log
    assert sum(r["high"].values()) + sum(r["low"].values()) > 200, log
    assert r["verdict"].startswith("clean") and "0 run(s) over 5 ticks" in log, log


# ------------------------------------------------------------------ the ping bench

LAPTOP = bytes.fromhex("0a0b0c0d0e0f")
CHIP_IP, LAPTOP_IP = bytes((169, 254, 0, 42)), bytes((169, 254, 1, 2))


def arp_request(target=CHIP_IP):
    return (b"\xff" * 6 + LAPTOP + b"\x08\x06" + bytes.fromhex("0001080006040001") + LAPTOP
            + LAPTOP_IP + bytes(6) + target + bytes(18))


def echo_request(seq=1, data=b"abcdefghijklmnopqrstuvwabcdefghi"):
    icmp = bytearray(bytes((8, 0, 0, 0, 0, 1, 0, seq)) + data)
    c = ethbench._csum(icmp)
    icmp[2:4] = bytes((c >> 8, c & 0xFF))
    ip = bytearray(bytes.fromhex("4500") + (20 + len(icmp)).to_bytes(2, "big")
                   + bytes.fromhex("1234 0000 80 01 0000") + LAPTOP_IP + CHIP_IP)
    c = ethbench._csum(ip)
    ip[10:12] = bytes((c >> 8, c & 0xFF))
    return ethbench.SRC + LAPTOP + b"\x08\x00" + bytes(ip) + bytes(icmp)


def test_reply_to():
    r = ethbench.reply_to(arp_request(), ethbench.SRC, CHIP_IP)
    assert len(r) == 60 and r[:6] == LAPTOP and r[6:12] == ethbench.SRC
    assert r[20:22] == b"\x00\x02" and r[22:28] == ethbench.SRC and r[28:32] == CHIP_IP
    assert r[32:38] == LAPTOP and r[38:42] == LAPTOP_IP
    assert ethbench.reply_to(arp_request(bytes((169, 254, 0, 43))), ethbench.SRC, CHIP_IP) is None
    q = echo_request(7)
    r = ethbench.reply_to(q, ethbench.SRC, CHIP_IP)
    assert r[:6] == LAPTOP and r[12:14] == b"\x08\x00"
    assert r[26:30] == CHIP_IP and r[30:34] == LAPTOP_IP
    assert ethbench._csum(r[14:34]) == 0 and ethbench._csum(r[34:len(q)]) == 0
    assert r[34] == 0 and r[38:42] == q[38:42] and r[42:len(q)] == q[42:]
    assert ethbench.reply_to(q[:-2], ethbench.SRC, bytes(4)) is None


def trx_image():
    return assemble_file(HERE / "eth_trx.asm")


def test_trx_receives_answers_and_keeps_the_link():
    """eth_trx.asm on the ISS: a frame arrives and waits in the tape; the host releases it and
    pushes a reply, which leaves on TD+/TD- byte for byte with its CRC-32; the link pulses
    stay 16 ms apart through it all (only a pulse restarts their count)."""
    q = arp_request()
    src = EthSource(2, [(40_000, wire_bits(q))], RX_BIT_HT, ppm=100.0)
    reply = ethbench.reply_to(q, ethbench.SRC, CHIP_IP)
    words = ethbench.frame_words(reply)
    words[0] |= ethbench.CMD_SEND
    pushes = [(200_000, 0, ethbench.CMD_RELEASE)] + [(210_000 + 1800 * i, 0, w)
                                                     for i, w in enumerate(words)]
    host = HostScript(pushes, drain=[0], poll=400)
    board = PinLog(src, host)
    m = Machine(trx_image(), board=board, half=src.half, record=False)
    host.machine = m
    m.run(34 * TICKS_MS, stop_when_idle=False)
    assert not m.ff.valid
    got, sent = host.received[0]
    assert (got >> 24) & 3 == 1 and got >> 31 == 1 and got & 0x3FFF == 8 * (len(q) + 4)
    data = b"".join(w.to_bytes(4, "little") for w in m.tape[:16])
    assert data == q + zlib.crc32(q).to_bytes(4, "little")
    assert (sent >> 24) & 3 == 2 and (sent >> 3) & 7 == ethbench.JOB_SEND and sent & 7 == 0
    runs = driven(board.changes)
    frames = [r for r in runs if r[1] - r[0] > 64]
    assert len(frames) == 1
    bits, _ = frame_bits(window(board.changes, frames[0][0], frames[0][1] + 1), 0)
    data = to_bytes(bits)
    assert data[8:8 + len(reply)] == reply
    assert int.from_bytes(data[8 + len(reply):12 + len(reply)], "little") == zlib.crc32(reply)
    pulses = [a for a, b in runs if b - a == 4]
    gaps = [b - a for a, b in zip(pulses, pulses[1:])]
    assert len(pulses) >= 2 and all(16 * TICKS_MS <= g < 17 * TICKS_MS for g in gaps), pulses
    assert pulses[0] < 17 * TICKS_MS


class TrxChip(RxChip):
    """RxChip that also keeps every change of TD+ and TD-."""

    def __init__(self, src, **kw):
        super().__init__(src, **kw)
        self.changes, self._last = [], None

    def _board(self, t, out, oe):
        k = (out & 3, oe & 3)
        if k != self._last:
            self.changes.append((t,) + k)
            self._last = k
        return super()._board(t, out, oe)


def test_ping_through_the_bare_pico(tmp_path):
    """ethbench.ping(): the laptop's ARP request and then its echo request arrive; the Pico
    reads each from the tape and the chip sends the ARP reply and the echo reply."""
    im = trx_image()
    used = [i for i, w in enumerate(im.words) if w]
    (tmp_path / "eth_trx.json").write_text(json.dumps(
        {"words": im.words[: max(used) + 1], "tape": {str(a): im.tape[a] for a in im.tape_used},
         "cfg": [im.ctx_enable_mask, im.entry_pcs_packed, im.pin_owned_mask,
                 im.pin_owner_packed, im.pin_od_mask, im.ctx_bank_mask, im.ctx_pair_mask]}))
    arp, echo = arp_request(), echo_request(3)
    src = EthSource(2, [(30 * TICKS_MS, wire_bits(arp)), (45 * TICKS_MS, wire_bits(echo))],
                    RX_BIT_HT, ppm=-100.0)
    chip = TrxChip(src)
    board = MockBoard(chip, op_ns=1000)
    io = barepico.open_io(machine=GpioMachine(board), time_mod=board.time_mod,
                          sys_hz=ethbench.SYS_HZ)
    lines = []
    n = ethbench.ping(seconds=1, ip="169.254.0.42", count=2, bundles=str(tmp_path), io=io,
                      out=lines.append)
    log = "\n".join(lines)
    assert n is not None and n["ARP replies"] == 1 and n["echo replies"] == 1, log
    assert chip.timing_violations == 0
    frames = [r for r in driven(chip.changes) if r[1] - r[0] > 64]
    sent = []
    for s, e in frames:
        bits, _ = frame_bits(window(chip.changes, s, e + 1), 0)
        sent.append(to_bytes(bits)[8:])
    want = [ethbench.reply_to(f, ethbench.SRC, CHIP_IP) for f in (arp, echo)]
    assert [d[:-4] for d in sent] == want, log
    assert all(int.from_bytes(d[-4:], "little") == zlib.crc32(d[:-4]) for d in sent)
    assert "-> ARP reply" in log and "-> echo reply" in log
