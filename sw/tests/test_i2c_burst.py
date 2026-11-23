"""I2C frame burst (firmware/i2c_burst.asm) against an SSD1306 model written from the datasheet
(escmodels/ssd1306.py), on the ISS at 25 MHz, Fast-mode with the model's UM10204 timing checks.

Command words (host -> context 0 RX mailbox), one I2C write transaction each:
  [31] = 0: command bytes: [25:24] n = 1..3, then [23:16], [15:8], [7:0] sent in that order;
  [31] = 1, [30] = 0: tape burst: [23:16] a prefix byte (the SSD1306's 0x40 control byte), then
            tape words [7:0] first .. [15:8] last, each as 4 bytes, most significant first;
  [31] = 1, [30] = 1: stream: [23:16] a prefix byte, then [15:0] words taken from the mailbox
            as the host sends them, 4 bytes each (a frame rendered while the chip runs).
Result word (TX mailbox): [31] = 1 if a byte was NACKed (the transaction stops there),
[15:0] the number of bytes after the address the target ACKed."""

from __future__ import annotations

from pathlib import Path

from escasm import assemble
from escmodels import Board, HostFeed, HostScript
from escmodels.ssd1306 import Ssd1306
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
KHZ = 25000
MS = KHZ


def cmd(*b):
    assert 1 <= len(b) <= 3
    w = len(b) << 24
    for i, x in enumerate(b):
        w |= x << (16 - 8 * i)
    return w


def burst(first, last, prefix=0x40):
    return 1 << 31 | prefix << 16 | last << 8 | first


def stream(n, prefix=0x40):
    return 3 << 30 | prefix << 16 | n


def image(tape=None, **equ):
    src = "".join(f".equ i2c_burst.{k}, {v}\n" for k, v in {"CLK_KHZ": KHZ, **equ}.items())
    im = assemble(src + '.include "i2c_burst.asm"\n', base_dir=FW)
    for a, w in enumerate(tape or []):
        im.tape[a] = w
    return im


def run(pushes, ms, tape=None, addr=0x3C, **kw):
    oled = Ssd1306(2, 3, addr, stretch_ticks=kw.pop("stretch", 0), **kw)
    host = HostScript(pushes, drain=[0], poll=1000)
    board = Board(oled, host)
    m = Machine(image(tape), board=board)
    host.machine = m
    m.run(int(ms * MS), stop_when_idle=False)
    return m, oled, host, board


def run_feed(words, ms, gap=1, stretch=0, **equ):
    """The words through a host that pushes only into room, `gap` ticks per push at most."""
    oled = Ssd1306(2, 3, 0x3C, stretch_ticks=stretch)
    host = HostFeed({0: words}, gap=gap, drain=[0], poll=1000, start=100)
    board = Board(oled, host)
    m = Machine(image(**equ), board=board)
    host.machine = m
    m.run(int(ms * MS), stop_when_idle=False)
    return m, oled, host, board


def pattern(n=256):
    """A frame no simple bug survives: every byte different from its neighbours."""
    return [((a * 0x9E3779B1) ^ (a << 7)) & 0xFFFFFFFF for a in range(n)]


def as_bytes(words):
    return [(w >> s) & 0xFF for w in words for s in (24, 16, 8, 0)]


HORIZONTAL = [cmd(0x00, 0x20, 0x00), cmd(0x00, 0x21, 0), cmd(0x00, 127), cmd(0x00, 0x22, 0),
              cmd(0x00, 7)]


# ------------------------------------------------------------------ the model, from the datasheet
def _feed(oled, *transactions):
    for tr in transactions:
        oled.transactions.append(["S", ("addr", oled.addr7 << 1, True)] + [("w", b) for b in tr] + ["P"])
    return oled.state()


def test_model_horizontal_mode_wraps_column_then_page():
    o = Ssd1306(2, 3)
    ram, cmds = _feed(o, [0x00, 0x20, 0x00, 0x21, 126, 127, 0x22, 6, 7], [0x40, 1, 2, 3, 4, 5])
    assert cmds == [(0x20, 0), (0x21, 126, 127), (0x22, 6, 7)]
    # a 2x2 window: (6,126) (6,127) (7,126) (7,127), then the 5th byte wraps to the start
    assert (ram[6][126], ram[6][127], ram[7][126], ram[7][127], ram[6][125]) == (5, 2, 3, 4, 0)


def test_model_page_mode_is_the_reset_mode():
    """Reset: page mode, page 0 column 0. 0xB3, 0x05, 0x12 point at page 3, column 0x25."""
    ram, _ = _feed(Ssd1306(2, 3), [0x40, 0xAA], [0x00, 0xB3, 0x05, 0x12], [0x40, 0x55, 0x66])
    assert (ram[0][0], ram[3][0x25], ram[3][0x26]) == (0xAA, 0x55, 0x66)


def test_model_co_bit_takes_one_byte_then_a_control_byte():
    """Co = 1: [0x80 cmd] [0xC0 data] [0x80 cmd] pairs in one transaction."""
    ram, cmds = _feed(Ssd1306(2, 3), [0x80, 0xAF, 0xC0, 0x7E, 0x80, 0xA6])
    assert cmds == [(0xAF,), (0xA6,)] and ram[0][0] == 0x7E


# ------------------------------------------------------------------ the firmware
def test_full_frame_rebuilds_the_tape_picture():
    """Horizontal mode over the whole panel, then one burst of all 256 tape words: display RAM
    equals the tape, byte for byte (word MSB first), 1025 bytes ACKed, Fast-mode timing met."""
    tape = pattern()
    pushes = [(100 + 3000 * i, 0, w) for i, w in enumerate(HORIZONTAL)] + [(16000, 0, burst(0, 255))]
    m, o, host, board = run(pushes, 40, tape)
    assert host.received[0] == [3, 3, 2, 3, 2, 1025], host.received[0]
    assert o.frame_bytes() == as_bytes(tape)
    assert not o.violations and not board.errors() and not m.ff.valid, (o.violations, board.errors())


def test_partial_burst_and_window():
    """Words 10..12 into a 12-column window on page 2: exactly those 12 bytes, nothing else."""
    tape = pattern()
    pushes = [(100, 0, cmd(0x00, 0x20, 0x00)), (3100, 0, cmd(0x00, 0x21, 40)), (6100, 0, cmd(0x00, 51)),
              (9100, 0, cmd(0x00, 0x22, 2)), (12100, 0, cmd(0x00, 2)), (15100, 0, burst(10, 12))]
    m, o, host, board = run(pushes, 3, tape)
    assert host.received[0] == [3, 3, 2, 3, 2, 13], host.received[0]
    ram, _ = o.state()
    assert ram[2][40:52] == as_bytes(tape[10:13])
    assert sum(1 for p in ram for b in p if b) == sum(1 for b in as_bytes(tape[10:13]) if b)
    assert not o.violations


def test_one_word_burst_and_prefix_byte():
    """first == last sends one word; the prefix byte is whatever the host asks for."""
    m, o, host, board = run([(100, 0, burst(7, 7, prefix=0x40))], 1, [0] * 7 + [0x11223344])
    assert host.received[0] == [5]
    assert o.state()[0][0][:4] == [0x11, 0x22, 0x33, 0x44]
    wr = [x[1] for x in o.transactions[0] if isinstance(x, tuple) and x[0] == "w"]
    assert wr == [0x40, 0x11, 0x22, 0x33, 0x44] and o.transactions[0][-1] == "P"


def test_command_bytes_one_two_three():
    m, o, host, board = run([(100, 0, cmd(0x80, 0xAF)), (3000, 0, cmd(0x00, 0x81, 0x7F)),
                             (6000, 0, cmd(0x00))], 2)
    assert host.received[0] == [2, 3, 1]
    assert o.state()[1] == [(0xAF,), (0x81, 0x7F)]
    assert [[x for x in tr if isinstance(x, str)] for tr in o.transactions] == [["S", "P"]] * 3


def test_address_nack_stops_at_once():
    """Nobody at 0x3C (the panel is at 0x3D): NACK on the address, STOP, result 0x80000000;
    the next command is served (the bus is free again)."""
    m, o, host, board = run([(100, 0, burst(0, 255)), (2000, 0, cmd(0x00, 0xAF))], 3, pattern(), addr=0x3D)
    assert host.received[0] == [0x80000000, 0x80000000]
    assert [[x if isinstance(x, str) else x[0] for x in tr] for tr in o.transactions] == [["S", "addr", "P"]] * 2
    assert not o.violations


def test_data_nack_mid_frame_stops_there():
    """The panel NACKs its 6th data byte: the burst ends with a STOP right after it, result
    [31] set and 5 bytes ACKed; nothing after that byte reaches the bus."""
    m, o, host, board = run([(100, 0, burst(0, 255))], 2, pattern(), nack_after=5)
    assert host.received[0] == [0x80000005]
    w = [x for x in o.transactions[0] if isinstance(x, tuple) and x[0] == "w"]
    assert len(w) == 6 and o.transactions[0][-1] == "P"
    assert not o.violations


def test_clock_stretching_is_honoured():
    m, o, host, board = run([(100, 0, burst(0, 3))], 2, pattern(), stretch=60)
    assert host.received[0] == [17] and o.frame_bytes()[:16] == as_bytes(pattern()[:4])
    assert not o.violations


def test_stream_frame_from_the_mailbox():
    """Horizontal mode, then one stream of 256 words the host sends while the chip runs, through
    the 2-word mailbox: display RAM equals the words, byte for byte, 1025 bytes ACKed, Fast-mode
    timing met, and the tape is not the source (it holds nothing of the frame)."""
    frame = pattern()
    m, o, host, board = run_feed(HORIZONTAL + [stream(256)] + frame, 40)
    assert host.received[0] == [3, 3, 2, 3, 2, 1025], host.received[0]
    assert o.frame_bytes() == as_bytes(frame)
    assert not o.violations and not board.errors() and not m.ff.valid, (o.violations, board.errors())


def test_stream_waits_for_a_slow_host_with_scl_low():
    """Words 40 us apart, slower than the bus takes them (a word is 36 bit times, 90 us at 400
    kHz, so a 400 us gap is a real stall): SCL stays low while the next word is missing, every
    byte still arrives, and no Fast-mode limit is broken (a long low phase is allowed)."""
    frame = pattern(8)
    m, o, host, board = run_feed([stream(8)] + frame, 8, gap=10_000)
    assert host.received[0] == [33], host.received[0]
    wr = [x[1] for x in o.transactions[0] if isinstance(x, tuple) and x[0] == "w"]
    assert wr == [0x40] + as_bytes(frame) and o.transactions[0][-1] == "P"
    assert not o.violations and not board.errors() and not m.ff.valid, (o.violations, board.errors())


def test_stream_of_no_words_sends_the_prefix_alone():
    m, o, host, board = run_feed([stream(0, prefix=0x5A)], 1)
    assert host.received[0] == [1]
    wr = [x[1] for x in o.transactions[0] if isinstance(x, tuple) and x[0] == "w"]
    assert wr == [0x5A] and o.transactions[0][-1] == "P" and not o.violations


def test_stream_stops_at_a_nack_and_the_next_command_is_served():
    """The panel NACKs a streamed byte: STOP right after it, result [31] set and 5 bytes ACKed;
    the stream still takes the words promised for it, so the command after them is served as a
    command, not read out of a stream's leftovers."""
    oled = Ssd1306(2, 3, 0x3C, nack_after=5)
    host = HostFeed({0: [stream(4)] + pattern(4) + [cmd(0x00, 0xAF)]}, drain=[0], poll=1000, start=100)
    board = Board(oled, host)
    m = Machine(image(), board=board)
    host.machine = m
    m.run(3 * MS, stop_when_idle=False)
    assert host.received[0][0] == 0x80000005 and len(host.received[0]) == 2, host.received[0]
    w = [x for x in oled.transactions[0] if isinstance(x, tuple) and x[0] == "w"]
    assert len(w) == 6 and oled.transactions[0][-1] == "P" and not oled.violations
    second = [x for x in oled.transactions[1] if isinstance(x, tuple) and x[0] == "w"]
    assert len(oled.transactions) == 2 and second[:1] == [("w", 0x00)], oled.transactions[1]


def test_certificate():
    from escert import certify
    assert [c.status for c in certify(image())] == ["CHECKED"]
