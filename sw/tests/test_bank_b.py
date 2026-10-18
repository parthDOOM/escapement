"""Pin bank B (docs/11-SEMANTICS.md v1.2.0 §5): expected values derived by hand from the
spec, as in test_esciss.py. Context 0's i-th instruction commits at tick 3 + 4i,
context 1's at 4 + 4i. A DRIVE with deadline d shows during cycle d+1; a context's sync
during cycle t is its bank's inputs during cycle t-2. A released bank B output is high.
"""

import json

import pytest

from escasm import AsmError, Image, assemble
from eschost.protocol import plan_write_cfg
from esciss import Machine

ADMIT, OWNER, ILLEGAL = 3, 2, 1


def run(src, ticks=500, board=None, board_b=None, idle=True):
    m = Machine(assemble(src), board, board_b)
    m.run(ticks, stop_when_idle=idle)
    return m


def ob(m, t):
    rec = m.pins[t]
    assert rec["t"] == t
    return rec["ob"]


def test_drive_lands_on_bank_b_and_released_is_high():
    m = run(""".bank 0
.own 0, 0
.entry 0, s
s:  ADDT 10                 ; c3: tmr = 10
    EMIT 0x01, 0x00, 0x01   ; c7: d = 10, retires at E_10: B0 low during 11
    ADDT 10                 ; c11: tmr = 20
    EMIT 0x01, 0x01, 0x01   ; c15: d = 20: B0 high (driven) during 21
    HALT
""")
    assert not m.ff.valid
    assert all(ob(m, t) == 0x1F for t in range(0, 11))            # released: high
    assert all(ob(m, t) == 0x1E for t in range(11, 21))
    assert all(ob(m, t) == 0x1F for t in range(21, len(m.pins)))
    assert all(p["uio_out"] == 0 and p["uio_oe"] == 0 for p in m.pins)   # bank A untouched
    assert m.oe == 1 << 8 and m.val == 1 << 8                       # physical pin 8


def test_each_context_sees_its_own_bank():
    m = run(""".bank 0
.entry 0, a
.entry 1, b
a:  GETP r0                 ; c3: bank B view = {3'b0, ib during 1}
    HALT
b:  GETP r0                 ; c4: bank A view = uio_in during 2 (pull-ups: 0xFF)
    HALT
""", board_b=lambda t, o: 0b10101)
    assert m.ctx[0].r[0] == 0x15                     # logical pins 5..7 read 0
    assert m.ctx[1].r[0] == 0xFF


def test_bank_b_owner_rules():
    # logical pin 5 has no physical pin in bank B: OWNER (§6.2 step 3)
    m = run(""".bank 0
.own 0, 0
.entry 0, s
s:  ADDT 10
    EMIT 0x20, 0x20, 0x20   ; c7
""")
    assert (m.ff.valid, m.ff.cause, m.ff.subcode, m.ff.pc) == (True, ADMIT, OWNER, 1)
    # B1 exists but is not owned
    m = run(""".bank 0
.own 0, 0
.entry 0, s
s:  ADDT 10
    EMIT 0x02, 0x02, 0x02
""")
    assert (m.ff.cause, m.ff.subcode) == (ADMIT, OWNER)


def test_banks_are_independent():
    m = run(""".bank 1
.own 0, 0                   ; uio0, ctx 0 (bank A)
.own 0, 1                   ; B0, ctx 1 (bank B): the same logical pin, another wire
.entry 0, a
.entry 1, b
a:  ADDT 20                 ; c3
    EMIT 0x01, 0x01, 0x01   ; c7: d = 20
    HALT
b:  ADDT 20                 ; c4
    EMIT 0x01, 0x00, 0x01   ; c8: d = 20
    HALT
""")
    assert (m.pins[20]["uio_out"], m.pins[20]["uio_oe"], ob(m, 20)) == (0, 0, 0x1F)
    assert (m.pins[21]["uio_out"], m.pins[21]["uio_oe"], ob(m, 21)) == (1, 1, 0x1E)
    assert not m.ff.valid


def test_fault_and_stop_release_bank_b():
    m = run(""".bank 0
.own 0, 0
.entry 0, s
s:  ADDT 10                 ; c3
    EMIT 0x01, 0x00, 0x01   ; c7: B0 low during 11
    ADDT 10                 ; c11: tmr = 20
    WAITT                   ; c15 W, c19 W, c23 C
    .word 0                 ; c27: ILLEGAL; owned pins released at E_27
""")
    assert ob(m, 27) == 0x1E and ob(m, 28) == 0x1F
    assert (m.ff.cause, m.ff.pc) == (ILLEGAL, 4)
    m = Machine(assemble(""".bank 0
.own 0, 0
.entry 0, s
s:  ADDT 10
    EMIT 0x01, 0x00, 0x01
    HALT
"""))
    m.run(30, stop_when_idle=False)
    assert m.outputs_b() == 0x1E                     # HALT keeps pins (§7.2)
    m.stop()
    m.run(1, stop_when_idle=False)
    assert m.outputs_b() == 0x1F                     # stop releases (§9)


def test_expect_and_sample_on_bank_b():
    # ib bit 2 rises at t = 30; sync sees it during 32
    m = run(""".bank 0
.entry 0, s
s:  ADDT 25                 ; c3
    EXPECT 0x04, 0x04, ARRIVE, 20   ; c7: window ticks 25..45: arrives at 32, pass
    ADDT 15                 ; c11: tmr = 40
    SAMPLE 2, LSB           ; c15: d = 40: shifts sync[2] during 40 = 1 into ish[31]
    GETI r1                 ; c19 W ... completes once the SAMPLE retired
    GETX r2
    HALT
""", board_b=lambda t, o: 0b00100 if t >= 30 else 0)
    assert m.ctx[0].r[1] == 1 << 31
    assert m.ctx[0].r[2] >> 16 & 0xFF == 1                 # pass_count 1


def test_capture_watches_bank_b():
    # v = enable | bank B | mask 0x1F | lim 16 | base 0 = 0x821F1000
    m = run(""".entry 0, s
s:  MOVI r1, 0x1000         ; c3
    MOVHI r1, 0x821F        ; c7
    CAPT r1                 ; c11: snapshot generated at E_12
    HALT
""", ticks=100, board_b=lambda t, o: 0b00100 if t >= 40 else 0, idle=False)
    m.stop()
    m.run(1, stop_when_idle=False)
    assert m.tape[0] == (0 << 24) | 12               # snapshot: csync during 12 = 0
    assert m.tape[1] == (0x04 << 24) | 42            # ib changed at 40: csync during 42
    assert m.dbg_read(0, 18) == 0x021F1000           # sel 18 shows bank (bit 25)


def test_digest_sees_bank_b_and_ignores_it_when_released():
    src = """.bank 0
.own 0, 0
.entry 0, s
s:  ADDT 10
    EMIT 0x01, {v}, 0x01
    HALT
"""
    low, high = run(src.format(v="0x00")), run(src.format(v="0x01"))
    assert low.dg != high.dg
    # Driving high is the released level, so at the package nothing changed; a NOP at the
    # same PC folds the same commit record (§10.1), so the digests are equal.
    none = run(src.replace("EMIT 0x01, {v}, 0x01", "NOP"))
    assert [p["ob"] for p in high.pins] == [0x1F] * len(high.pins)
    assert high.dg == none.dg and not high.ff.valid and not low.ff.valid


def test_assembler_bank_rules():
    with pytest.raises(AsmError, match="uses bank A"):
        assemble(".own B0, 0\n.entry 0, s\ns: HALT\n")
    with pytest.raises(AsmError, match="after a .own"):
        assemble(".own 0, 0\n.bank 0\n.entry 0, s\ns: HALT\n")
    with pytest.raises(AsmError, match="pins 0..4"):
        assemble(".bank 0\n.own 5, 0\n.entry 0, s\ns: HALT\n")
    with pytest.raises(AsmError):
        assemble(".od B0\n.entry 0, s\ns: HALT\n")
    img = assemble(".bank 2\n.own B3, 2\n.own 1, 2\n.own 7, 0\n.entry 0, s\ns: HALT\n")
    assert img.ctx_bank == [False, False, True, False]
    assert [p for p in range(13) if img.pin_owned[p]] == [7, 9, 11]
    assert img.pin_owned_mask == 0b0101010000000 and img.ctx_bank_mask == 0b0100
    assert img.pin_owner_packed == (2 << 22) | (2 << 18) | (0 << 14)


def test_image_json_and_v11_images():
    img = assemble(".bank 1\n.own 4, 1\n.entry 1, s\ns: HALT\n")
    back = Image.from_json(img.to_json())
    assert back.ctx_bank == img.ctx_bank and back.pin_owned == img.pin_owned
    d = json.loads(img.to_json())
    del d["ctx_bank"]
    d["pin_owned"], d["pin_owner"] = d["pin_owned"][:8], d["pin_owner"][:8]
    old = Image.from_json(json.dumps(d))             # a v1.1 image: 8 pins, no banks
    assert len(old.pin_owned) == 13 and not old.uses_bank_b


def test_write_cfg_frame_layout():
    frame, _ = plan_write_cfg(0x5, 0x04030201, 0x15F0, 0x2A5BEEF, 0x81, 0b0110)
    assert frame == bytes((0x12, 0x5, 1, 2, 3, 4, 0xF0, 0xBE, 0xEF, 0x81,
                           0b0110, 0x15, 0x2, 0xA5))
