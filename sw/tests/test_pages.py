"""v2 (S-A) program pages: a 1,024-word program store of four 256-word pages, each context
fetching from its own (x84notes/pages-DESIGN.md). The assembler's paged() and page_view(),
the ISS's paged fetch, escert per page, live reload per page, and the host's frames."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import AsmError, assemble, page_view, paged
from escert import certify
from eschost.live import plan_reload
from eschost.protocol import (IMEM_PAGE, WRITE_CFG, WRITE_IMEM, Host, check_pages,
                              page_loads, reload_writes)
from esciss import Machine
from escmodels import Board, CecDevice, UartRx
from escmodels.modbus import ModbusClient, adu

FW = Path(__file__).resolve().parents[2] / "firmware"
KHZ = 2500                                   # 400 ns ticks: CEC's frames are tens of ms
MB = f'.equ modbus_rtu.CLK_KHZ, {KHZ}\n.equ modbus_rtu.BAUD, 9600\n.include "modbus_rtu.asm"\n'
CE = f'.equ cec.CLK_KHZ, {KHZ}\n.equ cec.CTX, 1\n.equ cec.PIN, 3\n.include "cec.asm"\n'
TX_KHZ = 25000
TPB = TX_KHZ * 1000 / 115200


def uart_tx(ctx, pin, baud=115200):
    """A uart_tx image (its message, then HALT) on context ctx and pin `pin`, at 0."""
    src = (f".equ uart_tx.CTX, {ctx}\n.equ uart_tx.PIN, {pin}\n.equ uart_tx.CLK_KHZ, {TX_KHZ}\n"
           f'.equ uart_tx.BAUD, {baud}\n.include "uart_tx.asm"\n')
    return assemble(src, base_dir=FW)


# --------------------------------------------------------------------------- assembler

def test_paged_places_each_image_in_its_page_and_refuses_conflicts():
    a, b = assemble(MB, base_dir=FW), assemble(CE, base_dir=FW)
    img = paged({0: a, 2: b})
    assert len(img.words) == 1024 and img.ctx_page == [0, 2, 0, 0]
    assert img.words[:256] == a.words and img.words[512:768] == b.words
    assert img.words[256:512] == [0] * 256 and img.ctx_enable == [True, True, False, False]
    assert img.ctx_page_packed == 2 << 2
    assert page_view(img, 1).words == b.words and page_view(img, 0).words == a.words
    assert page_view(a, 0) is a                                    # a one-page image is its own
    with pytest.raises(ValueError, match="context 0 is enabled by two images"):
        paged({0: a, 1: a})
    clash = assemble(CE.replace("cec.CTX, 1", "cec.CTX, 2").replace("cec.PIN, 3", "cec.PIN, 0"),
                     base_dir=FW)
    with pytest.raises(ValueError, match="pin 0 is owned by two images"):
        paged({0: a, 1: clash})
    with pytest.raises(ValueError, match="out of range"):
        paged({4: a})


def test_the_showcase_does_not_fit_one_store():
    """modbus_rtu (256 words) and cec (233): impossible together without pages."""
    with pytest.raises(AsmError, match="exceeds 256 words"):
        assemble(MB + CE, base_dir=FW)


# --------------------------------------------------------------------------- escert

def test_escert_certifies_each_page_as_each_image_alone():
    a, b = assemble(MB, base_dir=FW), assemble(CE, base_dir=FW)
    alone = [str(c) for c in certify(a) + certify(b)]
    assert [str(c) for c in certify(paged({0: a, 1: b}))] == alone
    assert [str(c) for c in certify(paged({3: a, 1: b}))] == alone    # any pages
    assert all(" CHECKED" in c for c in alone)


# --------------------------------------------------------------------------- ISS

def _run(img, ticks):
    rx = UartRx(0, TPB)
    m = Machine(img, board=Board(rx))
    m.run(ticks, stop_when_idle=False)
    return m.commits, m.pins, rx.bytes


def test_a_one_page_image_runs_as_on_one_page():
    """paged({0: image}) is T0: the same commits, the same pins."""
    a = uart_tx(0, 0)
    one, four = _run(a, 20_000), _run(paged({0: a}), 20_000)
    assert one == four and one[0] and one[2]


def test_each_context_fetches_from_its_own_page():
    """Two programs at the same addresses, on pages 1 and 3: each context runs its own.
    The negative control puts both contexts on page 1: context 1 then runs a program for
    a pin it does not own, and its UART stays silent."""
    a, b = uart_tx(0, 0), uart_tx(1, 4)
    assert a.words != b.words                                     # same addresses, other code
    rx0, rx4 = UartRx(0, TPB), UartRx(4, TPB)
    m = Machine(paged({1: a, 3: b}), board=Board(rx0, rx4))
    m.run(20_000, stop_when_idle=False)
    assert rx0.bytes and rx0.bytes == rx4.bytes and not m.ff.valid
    img = paged({1: a, 3: b})
    img.ctx_page = [1, 1, 0, 0]
    rx0, rx4 = UartRx(0, TPB), UartRx(4, TPB)
    m = Machine(img, board=Board(rx0, rx4))
    m.run(20_000, stop_when_idle=False)
    assert rx0.bytes and not rx4.bytes


def test_modbus_and_cec_run_together_on_two_pages():
    """The showcase: a Modbus RTU server (context 0, page 0) and an HDMI-CEC adapter
    (context 1, page 1, pin 3) on one chip, each against its bus model."""
    img = paged({0: assemble(MB, base_dir=FW), 1: assemble(CE, base_dir=FW)})
    for k in range(64):
        img.tape[k] = 0x1000 + 17 * k
    ms = KHZ
    bit = KHZ * 1000 / 9600
    silence = round(max(3.5 * 11 * bit, 1.75 * ms)) + 500
    reqs = [(adu(1, bytes([3, 0, 5, 0, 3])), True), (adu(1, bytes([6, 0, 9, 0xBE, 0xEF])), True),
            (adu(1, bytes([3, 0, 9, 0, 1])), True)]
    cl = ModbusClient(ro=1, di=0, de=2, ticks_per_bit=bit, requests=reqs, parity="E",
                      silence=silence, start=60 * ms)
    tv = CecDevice(3, 0, tick_ns=400.0, retries=1,
                   send=[(20 * ms, [0x04, 0x83]), (120 * ms, [0x0F, 0x36]), (220 * ms, [0x05, 0x8F])])
    board = Board(cl, tv)
    m = Machine(img, board=board)
    cec_words = []
    while m.now < 400 * ms or cl.k < len(reqs) or cl._cur is not None:
        m.run(200, stop_when_idle=False)
        m.host_tx_pop(0)
        v = m.host_tx_pop(1)
        if v is not None:
            cec_words.append(v)
        m.pins.clear()
        m.commits.clear()
        board.history.clear()
        assert m.now < 2_000 * ms, f"stuck: modbus request {cl.k}, {cl.errors()}"
    out = [bytes(r["bytes"]) for r in cl.replies]
    assert cl.errors() == [] and len(out) == 3
    assert [int.from_bytes(out[0][3 + 2 * k:5 + 2 * k], "big") for k in range(3)] == \
        [0x1055, 0x1066, 0x1077]
    assert out[1] == reqs[1][0] and m.tape[9] == 0xBEEF
    assert [r for r, _ in tv.results] == ["ack", "ack", "nack"]
    assert not board.errors(), board.errors()
    assert not m.ff.valid and cec_words


# --------------------------------------------------------------------------- live reload

def test_reload_inside_a_page_ignores_other_pages_code():
    """Context 1's program (page 1) is replaced by the same one at another baud: the changed
    words sit at addresses where context 0 has code in page 0, and the reload is allowed.
    The negative control puts both contexts on page 1: the same reload is refused."""
    a, b, b2 = uart_tx(0, 0), uart_tx(1, 4), uart_tx(1, 4, baud=57600)
    running, new = paged({0: a, 1: b}), paged({0: a, 1: b2})
    runs = plan_reload(running, new, 1)
    assert runs and all(256 <= addr < 512 for addr, _ in runs)
    changed = {x % 256 for addr, words in runs for x in range(addr, addr + len(words))}
    assert changed & {pc for pc in range(256) if a.words[pc]}
    for addr, words in runs:
        assert addr // 256 == (addr + len(words) - 1) // 256      # a run stays in one page
    running.ctx_page = [1, 1, 0, 0]
    new.ctx_page = [1, 1, 0, 0]
    with pytest.raises(ValueError, match="context 0 can reach"):
        plan_reload(running, new, 1)


# --------------------------------------------------------------------------- host frames

class FakeChip:
    """Records frames; every reply's first byte is a status with this protocol version."""

    def __init__(self, version):
        self.version, self.frames = version, []

    def xfer(self, frame):
        self.frames.append(bytes(frame))
        r = bytearray(len(frame))
        r[0] = self.version & 0x0F
        return bytes(r)


def test_host_refuses_a_paged_image_on_a_chip_without_pages():
    img = paged({0: assemble(MB, base_dir=FW), 1: assemble(CE, base_dir=FW)})
    with pytest.raises(IOError, match="program pages"):
        check_pages(img, {"version": 9})
    check_pages(img, {"version": 10})
    check_pages(assemble(MB, base_dir=FW), {"version": 9})


def test_host_frames_for_a_paged_image():
    img = paged({0: assemble(MB, base_dir=FW), 2: assemble(CE, base_dir=FW)})
    assert [(p, base) for p, base, _ in page_loads(img)] == [(0, 0), (2, 512)]
    chip = FakeChip(10)
    Host(chip.xfer).load(img, verify=False)
    cmds = [f[0] for f in chip.frames]
    assert [f for f in chip.frames if f[0] == IMEM_PAGE] == \
        [bytes((IMEM_PAGE, 0)), bytes((IMEM_PAGE, 2)), bytes((IMEM_PAGE, 0))]
    assert cmds[cmds.index(IMEM_PAGE) + 1] == WRITE_IMEM
    cfg = [f for f in chip.frames if f[0] == WRITE_CFG]
    assert len(cfg) == 1 and len(cfg[0]) == 15 and cfg[0][14] == img.ctx_page_packed == 2 << 2


def test_host_frames_for_an_older_chip_are_unchanged():
    """A 256-word image on a version-9 chip: no IMEM_PAGE, the 14-byte WRITE_CFG."""
    chip = FakeChip(9)
    Host(chip.xfer).load(assemble(MB, base_dir=FW), verify=False)
    assert IMEM_PAGE not in [f[0] for f in chip.frames]
    assert [len(f) for f in chip.frames if f[0] == WRITE_CFG] == [14]
    assert reload_writes(False, [(5, [1, 2])]) == [(None, 5, [1, 2])]
    assert reload_writes(True, [(261, [1, 2])]) == [(1, 5, [1, 2])]
