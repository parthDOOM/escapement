"""Assembler tests: encodings checked against hand-computed words (not via the ISA tables)."""

import pytest

from escasm import AsmError, assemble, disassemble


def word(src: str) -> int:
    return assemble(src).words[0]


@pytest.mark.parametrize("src,expected", [
    ("NOP", 0x01000000),
    ("HALT", 0x02000000),
    ("IRQ 0x5A", 0x0300005A),
    ("MOV r2, r1", 0x08000000 | (2 << 22) | (1 << 20)),
    ("MOVI r3, 0xBEEF", 0x09000000 | (3 << 22) | 0xBEEF),
    ("SHR r1, 31", 0x13000000 | (1 << 22) | 31),
    ("JMP 200", 0x180000C8),
    ("ADDT 0xFFFFFF", 0x22FFFFFF),
    ("EMIT 0x81, 0x01, 0x80", 0x28810180),
    ("EMITR 0x0F, r3, 0x0F", 0x290FC00F),
    ("EMITX 0x02, MSB, 0x02", 0x2A028002),
    ("EXPECT 0x04, 0x04, ARRIVE, 100", 0x2B0404E4),
    ("SAMPLE 5, MSB", 0x2C058000),
    ("WAITP 0x02, 0x00", 0x31020000),
    ("RDTIME r1", 0x20400000),
    ("SETT r2", 0x21200000),
    ("POP r3", 0x38C00000),
])
def test_encodings(src, expected):
    assert word(src) == expected


def test_labels_forward_and_backward():
    img = assemble("""
        .entry 1, top
        JMP end
    top: NOP
    end: JMP top
    """)
    assert img.words[0] == 0x18000002 and img.words[2] == 0x18000001
    assert img.entry_pcs[1] == 1 and img.ctx_enable == [False, True, False, False]


def test_equ_expressions_and_config():
    img = assemble("""
        .equ BIT, 217
        .equ PIN, 3
        .own PIN, 2
        .od  PIN
        ADDT BIT*2 + 1
        EMIT 1<<PIN, 0, 1<<PIN
    """)
    assert img.words[0] == 0x22000000 | 435
    assert img.words[1] == 0x28080008
    assert img.pin_owned_mask == 0x08 and img.pin_owner_packed == 2 << 6
    assert img.pin_od_mask == 0x08


def test_unused_memory_is_illegal_zero():
    img = assemble("NOP")
    assert img.words[1:] == [0] * 255 and img.used_words == 1


@pytest.mark.parametrize("src", [
    "MOVI r4, 1", "EMIT 0x100, 0, 0", "SHR r0, 32", "FOO r1", "JMP nowhere",
    "MOV r1", ".own 8, 0",
])
def test_errors(src):
    with pytest.raises(AsmError):
        assemble(src)


def test_disassemble_round_trip():
    for src in ["EMITX 0x02, MSB, 0x02", "EXPECT 0x04, 0x04, ARRIVE, 100", "MOV r2, r1"]:
        assert disassemble(word(src)) == src.replace("0x02", "0x02")
    assert "ILLEGAL" in disassemble(0x00000000)
    assert "ILLEGAL" in disassemble(0x01000001)  # NOP with a reserved bit set


def test_uart_firmware_assembles():
    from pathlib import Path
    img = assemble((Path(__file__).resolve().parents[2] / "firmware" / "uart_tx.asm").read_text())
    assert img.used_words < 40 and img.entry_pcs[0] == 0


def test_include_namespaces_and_defaults(tmp_path):
    (tmp_path / "mod.asm").write_text(
        ".default CTX, 1\n.default PIN, 4\n.equ M, 1 << PIN\n.entry CTX, top\n"
        "top: EMIT M, M, M\n     JMP top\n")
    (tmp_path / "other.asm").write_text(".entry 3, top\ntop: JMP top\n")   # same label name
    (tmp_path / "main.asm").write_text(
        '.equ mod.PIN, 6\n.include "mod.asm"\n.include "other.asm"\n')
    from escasm import assemble_file
    img = assemble_file(tmp_path / "main.asm")
    assert img.words[0] == 0x28404040          # EMIT 1<<6 with the overridden PIN
    assert img.words[1] == 0x18000000          # mod's own `top` (address 0)
    assert img.words[2] == 0x18000002          # other's `top` (address 2), no clash
    assert img.entry_pcs[1] == 0 and img.entry_pcs[3] == 2


def test_include_rejects_context_and_pin_conflicts(tmp_path):
    (tmp_path / "a.asm").write_text(".entry 0, s\n.own 2, 0\ns: HALT\n")
    (tmp_path / "b.asm").write_text(".entry 1, s\n.own 2, 1\ns: HALT\n")
    (tmp_path / "main.asm").write_text('.include "a.asm"\n.include "b.asm"\n')
    from escasm import assemble_file
    with pytest.raises(AsmError, match="already owned"):
        assemble_file(tmp_path / "main.asm")
