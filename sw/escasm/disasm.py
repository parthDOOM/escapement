"""Disassembler (encoding only)."""

from __future__ import annotations

from .asm import SYNTAX, REG_FIELDS
from .isa import ISA, load_isa


def disassemble(word: int, isa: ISA | None = None) -> str:
    isa = isa or load_isa()
    opc = isa.by_op.get((word >> 24) & 0xFF)
    if opc is None or word & isa.reserved_mask(opc):
        return f".word 0x{word:08x}  ; ILLEGAL"
    parts = []
    for name in SYNTAX[opc.format]:
        msb, lsb = opc.fields[name]
        v = (word >> lsb) & ((1 << (msb - lsb + 1)) - 1)
        if name in REG_FIELDS:
            parts.append(f"r{v}")
        elif name == "dir":
            parts.append("MSB" if v else "LSB")
        elif name == "mode":
            parts.append("ARRIVE" if v else "HOLD")
        elif name in ("mask", "value", "oe", "level"):
            parts.append(f"0x{v:02x}")
        else:
            parts.append(str(v))
    return f"{opc.name} {', '.join(parts)}".strip()
