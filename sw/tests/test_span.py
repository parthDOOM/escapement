"""3.1 span: a context whose partner (k ^ 2) is off also fetches from the partner's page, with
a 9-bit PC and link, so its program has 512 words (docs/11-SEMANTICS.md §3). The assembler
directive and image, paged(), the ISS's fetch, ILLEGAL and PC_RANGE rules, the observation
fields, the host's WRITE_CFG byte 15 and version guard, and the live-reload reach check.
The RTL is checked against the ISS in test/cosim."""

from __future__ import annotations

import pytest

from escasm import AsmError, Image, assemble, load_isa, page_view, paged, spans
from escert import certify
from eschost.protocol import Host, plan_write_cfg
from esciss import Machine

ILLEGAL, PC_RANGE = 1, 2
HALTED, FAULTED = 2, 3

# r0 collects one bit per stop, in this order: 1 (start, page 0), 2 (far, page 1),
# 4 (back in page 0), 8 (the subroutine in page 1, called from page 0), 16 (after RET),
# 32 (just before the boundary), 64 (word 256, reached by running on from 255)
CROSS = """
.span {k}
.entry {k}, s
s:      MOVI r0, 1
        JMP far
back:   ADDI r0, 4
        CALL sub_hi
        ADDI r0, 16
        JMP edge
.org 250
edge:   ADDI r0, 32
        NOP
        NOP
        NOP
        NOP
        NOP
        ADDI r0, 64
        HALT
.org 300
far:    ADDI r0, 2
        JMP back
.org 310
sub_hi: ADDI r0, 8
        RET
"""


def run(image, ticks=2000, **kw):
    m = Machine(image, **kw)
    m.run(ticks)
    return m


# ------------------------------------------------------------------ assembler and image

def test_span_image_is_paged_with_the_partner_on_page_1():
    im = assemble(CROSS.format(k=1))
    assert len(im.words) == 1024 and im.ctx_span == [False, True, False, False]
    assert im.ctx_span_mask == 0b0010 and im.ctx_page == [0, 0, 0, 1]
    assert im.symbols["far"] == 300 and im.words[300] == assemble("ADDI r0, 2").words[0]
    assert im.words[256] == assemble("ADDI r0, 64").words[0]
    back = Image.from_json(im.to_json())
    assert back.ctx_span == im.ctx_span and back.words == im.words
    plain = assemble(".entry 0, s\ns: HALT")
    assert '"ctx_span"' not in plain.to_json()             # 3.0 images are unchanged
    assert Image.from_json(plain.to_json()).ctx_span == [False] * 4
    assert spans(im, 1) and not spans(im, 0)


def test_span_encodes_a_9_bit_target():
    im = assemble(CROSS.format(k=0))
    assert im.words[im.symbols["s"] + 1] == (0x18 << 24) | 300     # JMP far
    call = im.words[im.symbols["back"] + 1]                         # CALL sub_hi
    assert call >> 24 == load_isa().by_name["CALL"].op and call & 0xFFFFFF == 310


def test_assembler_refusals():
    with pytest.raises(AsmError, match="exceeds 256 words"):
        assemble(".entry 0, s\n.org 255\ns: NOP\nHALT")
    with pytest.raises(AsmError, match="past word 255"):
        assemble(".entry 0, s\ns: JMP 300")
    with pytest.raises(AsmError, match="a second .span"):
        assemble(".span 0\n.span 1\n.entry 0, s\ns: HALT")
    with pytest.raises(AsmError, match="context 2 is enabled"):
        assemble(".span 0\n.entry 0, s\n.entry 2, s\ns: HALT")
    with pytest.raises(AsmError, match="entry PCs are 8 bits"):
        assemble(".span 0\n.entry 0, s\n.org 300\ns: HALT")
    with pytest.raises(AsmError, match="exceeds 512 words"):
        assemble(".span 0\n.entry 0, s\ns: HALT\n.org 511\nNOP\nNOP")
    with pytest.raises(AsmError, match=r"\.span ctx"):
        assemble(".span 4\n.entry 0, s\ns: HALT")
    # .span may come after the code that needs it
    assert assemble(".entry 0, s\ns: JMP t\n.org 400\nt: HALT\n.span 0").ctx_span[0]


# ------------------------------------------------------------------ ISS

@pytest.mark.parametrize("k", range(4))
def test_a_spanning_program_crosses_both_ways(k):
    m = run(assemble(CROSS.format(k=k)))
    x = m.ctx[k]
    assert x.state == HALTED and not m.ff.valid
    assert x.r[0] == 127 and x.pc == 257                   # HALT at 257 keeps its PC
    pcs = [c["pc"] for c in m.commits if c["ctx"] == k]
    assert pcs[:4] == [0, 1, 300, 301]
    i = pcs.index(255)
    assert pcs[i:i + 3] == [255, 256, 257]                 # sequential across the boundary
    call = [c for c in m.commits if c["ctx"] == k and c["pc"] == 310][0]
    assert m.commits[m.commits.index(call) - 1]["pc"] == 3          # CALL from page 0
    assert x.lr == 4                                        # lr = CALL's PC + 1


def test_a_call_from_page_1_returns_to_page_1():
    src = """
.span 0
.entry 0, s
s:   JMP hi
sub: MOVI r1, 7
     RET
.org 400
hi:  CALL sub
     MOVI r2, 9
     HALT
"""
    m = run(assemble(src))
    x = m.ctx[0]
    assert x.state == HALTED and x.r[1] == 7 and x.r[2] == 9 and x.lr == 401
    assert m.dbg_read(0, 7) >> 25 & 1 == 1 and m.dbg_read(0, 7) >> 16 & 0xFF == 401 & 0xFF
    assert m.dbg_read(0, 7) >> 24 & 1 == 1 and m.dbg_read(0, 7) >> 8 & 0xFF == 402 & 0xFF


def test_pc_range_moves_to_511():
    m = run(assemble(".span 0\n.entry 0, s\ns: JMP e\n.org 511\ne: NOP"))
    assert m.ctx[0].state == FAULTED and m.ff.cause == PC_RANGE and m.ff.pc == 511
    assert m.dbg_read(0, 14) & 0x1FF == 511
    m = run(assemble(".span 0\n.entry 0, s\ns: JMP e\nf: HALT\n.org 511\ne: CALL f"))
    assert m.ctx[0].state == FAULTED and m.ff.cause == PC_RANGE and m.ff.pc == 511
    m = run(assemble(".span 0\n.entry 0, s\ns: JMP e\nf: HALT\n.org 511\ne: JMP f"))
    assert m.ctx[0].state == HALTED                        # a jump at 511 is fine


def test_without_span_255_still_faults_and_bit_8_is_illegal():
    m = run(assemble(".entry 0, s\ns: JMP e\n.org 255\ne: NOP"))
    assert m.ctx[0].state == FAULTED and m.ff.cause == PC_RANGE and m.ff.pc == 255
    jmp_300 = (0x18 << 24) | 300                          # target bit 8 set
    m = run(assemble(f".entry 0, s\ns: .word {jmp_300}"))
    assert m.ctx[0].state == FAULTED and m.ff.cause == ILLEGAL and m.ff.pc == 0


def test_span_needs_the_partner_off():
    """ctx_span set while the partner is enabled: no span, the 3.0 machine (the assembler
    refuses this; a hand-made configuration gets the 3.0 behaviour)."""
    im = assemble(".span 0\n.entry 0, s\ns: JMP e\n.org 255\ne: NOP")
    im.ctx_enable[2], im.entry_pcs[2], im.ctx_page[2] = True, 0, 0
    m = run(im)
    assert not spans(im, 0)
    assert m.ff.valid and m.ff.cause == PC_RANGE and m.ff.pc == 255 and m.ff.ctx in (0, 2)


def test_a_context_that_does_not_span_never_fetches_the_partner_page():
    """Context 1 shares page 0 with spanning context 0; a target of 300 is ILLEGAL for it,
    and it never reads page 1 (where a HALT waits at 300)."""
    jmp_300 = (0x18 << 24) | 300
    src = f"""
.span 0
.entry 0, a
.entry 1, b
a:  HALT
b:  .word {jmp_300}
.org 300
    HALT
"""
    m = run(assemble(src))
    assert m.ctx[0].state == HALTED
    assert m.ctx[1].state == FAULTED and m.ff.cause == ILLEGAL and m.ff.ctx == 1


@pytest.mark.parametrize("pair", [False, True])
def test_span_and_pair_together_and_apart(pair):
    src = (".pair 0\n" if pair else "") + CROSS.format(k=0)
    m = run(assemble(src))
    x = m.ctx[0]
    assert x.state == HALTED and x.r[0] == 127
    commits = [c for c in m.commits if c["ctx"] == 0]
    i = [c["pc"] for c in commits].index(255)
    gap = commits[i + 1]["t"] - commits[i]["t"]
    assert gap == (2 if pair else 4)                       # 255 -> 256 at the full rate


def test_paired_prefetch_across_the_boundary_and_back():
    """Pair mode fetches the next PC early: a taken JMP from page 1 to page 0, a RET
    across, and 255 -> 256 are all resolved without a lost slot."""
    m = run(assemble(".pair 0\n" + CROSS.format(k=0)))
    commits = [c for c in m.commits if c["ctx"] == 0]
    gaps = {b["t"] - a["t"] for a, b in zip(commits, commits[1:])}
    assert gaps == {2}, gaps


def test_restart_returns_to_the_entry_in_page_0():
    src = """
.span 0
.entry 0, s
s:   JMP hi
.org 300
hi:  ADDI r0, 1
     JMP hi
"""
    m = Machine(assemble(src))
    m.run(200, stop_when_idle=False)
    assert m.ctx[0].pc >= 300
    m.host_ctx_stop(1)
    m.run(1, stop_when_idle=False)
    m.host_ctx_start(1)
    m.run(8, stop_when_idle=False)
    starts = [c for c in m.commits if c["ctx"] == 0 and c["t"] > 200]
    assert starts[0]["pc"] == 0 and starts[0]["post"]["r0"] == 0


def test_digest_carries_the_9_bit_pc():
    """cw_t = (outcome << 14) | (ctx << 12) | pc: a commit at 256 differs from one at 0."""
    a = run(assemble(".span 0\n.entry 0, s\ns: JMP t\n.org 256\nt: HALT"))
    b = run(assemble(".entry 0, s\ns: JMP t\nt: HALT"))
    assert a.ctx[0].state == b.ctx[0].state == HALTED and a.dg != b.dg


# ------------------------------------------------------------------ paged() and views

def test_paged_places_a_spanning_image_in_two_pages():
    span = assemble(CROSS.format(k=1))
    other = assemble(".entry 0, s\n.own 0, 0\ns: HALT")
    im = paged({0: other, 2: span})
    assert im.ctx_span == [False, True, False, False] and im.ctx_page == [0, 2, 0, 3]
    assert im.words[512:768] == span.words[:256] and im.words[768:1024] == span.words[256:512]
    alone = run(span)
    both = run(im)
    assert [c for c in both.commits if c["ctx"] == 1] == [c for c in alone.commits if c["ctx"] == 1]
    v = page_view(im, 1)
    assert len(v.words) == 512 and v.words == span.words[:512]
    assert len(page_view(im, 0).words) == 256
    with pytest.raises(ValueError, match="page 4 out of range"):
        paged({3: span})
    with pytest.raises(ValueError, match="page 3 is taken"):
        paged({2: span, 3: other})
    with pytest.raises(ValueError, match="context 3 is enabled"):
        paged({0: span, 2: assemble(".entry 3, s\ns: HALT")})


# ------------------------------------------------------------------ host

def test_write_cfg_byte_15():
    frame, _ = plan_write_cfg(0x1, 0, 0, 0, 0, 0, 0, 0b11_00_00_00, 0b0001)
    assert len(frame) == 16 and frame[14] == 0b11_00_00_00 and frame[15] == 0b0001
    frame, _ = plan_write_cfg(0x1, 0, 0, 0, 0, 0, 0, 0)
    assert len(frame) == 15                                # a 3.0 chip's frame is unchanged


class _Chip:
    """Answers every frame with a status byte of the given protocol version."""

    def __init__(self, version):
        self.version, self.frames = version, []

    def __call__(self, frame):
        self.frames.append(bytes(frame))
        return bytes([self.version]) + bytes(len(frame) - 1)


def test_host_refuses_a_spanning_image_on_an_older_chip():
    span = assemble(CROSS.format(k=0))
    with pytest.raises(IOError, match="protocol version 10"):
        Host(_Chip(10)).load(span, verify=False)
    chip = _Chip(10)                                       # a 3.0 image still loads there
    Host(chip).load(assemble(".entry 0, s\ns: HALT"), verify=False)
    assert chip.frames[-1][0] == 0x12 and len(chip.frames[-1]) == 15
    chip = _Chip(11)
    Host(chip).load(span, verify=False)
    cfg = chip.frames[-1]
    assert cfg[0] == 0x12 and len(cfg) == 16 and cfg[14] == span.ctx_page_packed and cfg[15] == 1


# ------------------------------------------------------------------ escert and live reload

# a square wave on pin 0 whose loop runs across the boundary: EMITs on both pages, a
# subroutine in page 1 called from page 0, and the loop's back edge from page 1 to page 0
WAVE = """
.span {k}
.entry {k}, s
.own 0, {k}
s:      RDTIME r0
        SETT r0
        ADDT 200
loop:   EMIT 0x01, 0x01, 0x01
        ADDT 100
        CALL lo
        JMP edge
.org 254
edge:   NOP
        NOP
        ADDT 100
        JMP loop
.org 300
lo:     EMIT 0x01, 0x00, 0x01
        ADDT 100
        RET
"""


def _cert(image, ctx):
    return [c for c in certify(image) if c.ctx == ctx][0]


@pytest.mark.parametrize("k", range(4))
def test_escert_certifies_a_spanning_program(k):
    im = assemble(WAVE.format(k=k))
    c = _cert(im, k)
    assert c.status == "CHECKED" and c.min_lead >= 0, c
    m = Machine(im)
    m.run(3000, stop_when_idle=False)
    assert not m.ff.valid and m.ctx[k].min_lead >= c.min_lead
    assert any(x["pc"] >= 256 for x in m.commits if x["ctx"] == k)


def test_escert_on_a_paged_spanning_image_matches_alone():
    span = assemble(WAVE.format(k=1))
    other = assemble(".entry 0, s\n.own 1, 0\ns: HALT")
    both = paged({0: other, 2: span})
    a, b = _cert(span, 1), _cert(both, 1)
    assert (a.status, a.min_lead) == (b.status, b.min_lead) == ("CHECKED", a.min_lead)


def test_escert_refuses_a_call_at_the_last_address():
    """A CALL at the last address faults PC_RANGE (sec. 7) even when its subroutine never
    returns; escert flags it (255 without span, 511 with)."""
    plain = assemble(".entry 0, s\n.own 0, 0\ns: JMP c\nsub: JMP sub\n.org 255\nc: CALL sub")
    assert _cert(plain, 0).status != "CHECKED"
    span = assemble(".span 0\n.entry 0, s\n.own 0, 0\ns: JMP c\nsub: JMP sub\n.org 511\nc: CALL sub")
    assert _cert(span, 0).status != "CHECKED"
    ok = assemble(".span 0\n.entry 0, s\n.own 0, 0\ns: JMP c\nsub: JMP sub\n.org 255\nc: CALL sub")
    assert _cert(ok, 0).status == "CHECKED"                 # 255 is not the last when spanning


def test_escert_refuses_bit_8_on_a_context_that_does_not_span():
    jmp_300 = (0x18 << 24) | 300
    src = f".span 0\n.entry 0, a\n.entry 1, b\na: HALT\nb: .word {jmp_300}\n.org 300\nHALT"
    im = assemble(src)
    assert _cert(im, 0).status == "CHECKED"
    assert _cert(im, 1).status != "CHECKED"


def test_reload_beside_a_spanning_context_checks_its_partner_page():
    """Context 1 shares page 0 with spanning context 0, which also fetches from page 1: a
    reload of context 1 may write neither page, whether or not context 0 reaches the word
    (the non-interference proof assumes no write lands in a page the other context fetches
    from)."""
    from eschost.live import plan_reload
    base = """
.span 0
.entry 0, a
.entry 1, b
.own 0, 0
.own 1, 1
a:  JMP hi
b:  RDTIME r0
    SETT r0
bl: ADDT 1000
    EMIT 0x02, {v}, 0x02
    JMP bl
.org 300
hi: RDTIME r0
    SETT r0
hl: ADDT 1000
    EMIT 0x01, {w}, 0x01
    JMP hl
.org 400
    .word {x}
"""
    run = assemble(base.format(v="0x02", w="0x01", x="0"))
    new = assemble(base.format(v="0x00", w="0x01", x="0"))
    with pytest.raises(ValueError, match="page 0, which context 0 fetches from"):
        plan_reload(run, new, 1)                            # page 0, context 1's own word
    bad = assemble(base.format(v="0x00", w="0x00", x="0"))
    with pytest.raises(ValueError, match="context 0 fetches from"):
        plan_reload(run, bad, 1)
    far = assemble(base.format(v="0x02", w="0x01", x="0x01000000"))
    with pytest.raises(ValueError, match="page 1, which context 0 fetches from"):
        plan_reload(run, far, 1)                            # word 400: unreached, still its page


# ------------------------------------------------------------------ bring-up bundles

@pytest.mark.parametrize("k", (0, 3))
def test_a_spanning_bundle_runs_through_bringup(tmp_path, k):
    """make_bundle keeps a spanning image's page layout and adds WRITE_CFG bytes 14 and 15;
    bringup.py writes every page it carries and the full frame, and stage 5 (the simulated
    board) matches the ISS's prediction and escert's certificate. A bundle that does not
    span is as before: 7 cfg fields, at most 256 words."""
    import json
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rp2350"))
    import make_bundle
    from test_bringup import run_on
    from demoboard_mock import SimChip

    fw = tmp_path / f"wave{k}.asm"
    fw.write_text(WAVE.format(k=k))
    b = make_bundle.bundle(fw)
    im = assemble(WAVE.format(k=k))
    assert len(b["words"]) > 256 and b["cfg"][7:] == [im.ctx_page_packed, 1 << k]
    assert im.ctx_page[k ^ 2] == 1 and b["words"][300] == im.words[300]
    d = tmp_path / "bundles"
    d.mkdir()
    make_bundle.write_bundle(d, fw.stem, b)
    ok, results, log = run_on(SimChip(), d, stages=(5,))
    assert ok, log
    assert "1 of 1 firmware match" in log, log

    plain = make_bundle.bundle(Path(__file__).resolve().parents[2] / "firmware" / "uart_tx.asm")
    assert len(plain["cfg"]) == 7 and len(plain["words"]) <= 256
