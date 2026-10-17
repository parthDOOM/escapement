"""Instruction decode (encoding only, from isa/isa.yaml via escasm.load_isa).

Only field positions, opcode values and code tables come from isa.yaml. All behaviour is
written by hand in machine.py from docs/11-SEMANTICS.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from escasm import load_isa

ISA = load_isa()

FAULT_CAUSE = dict(ISA.fault_causes)        # ILLEGAL 1, PC_RANGE 2, ADMIT 3, LATE_HEAD 4
ADMIT_SUB = dict(ISA.admit_subcodes)        # MASK 1, OWNER 2, PAST 3, ORDER 4, OVERLAP 5
EVENT_KIND = dict(ISA.event_kinds)          # DRIVE 1, EXPECT 2, SAMPLE 3


def _reserved_mask(fields: dict[str, tuple[int, int]]) -> int:
    """Payload bits [23:0] not named by the format (isa.yaml header: must be zero)."""
    used = 0
    for msb, lsb in fields.values():
        used |= ((1 << (msb - lsb + 1)) - 1) << lsb
    return 0x00FFFFFF & ~used


_RESERVED = {opc.op: _reserved_mask(opc.fields) for opc in ISA.by_op.values()}


@dataclass(frozen=True)
class Decoded:
    name: str
    f: dict[str, int]


def decode(word: int) -> Decoded | None:
    """Return the decoded instruction, or None if ILLEGAL (§7: unlisted opcode or a
    nonzero reserved bit)."""
    word &= 0xFFFFFFFF
    op = (word >> 24) & 0xFF
    opc = ISA.by_op.get(op)
    if opc is None or (word & _RESERVED[op]):
        return None
    fields = {name: (word >> lsb) & ((1 << (msb - lsb + 1)) - 1)
              for name, (msb, lsb) in opc.fields.items()}
    return Decoded(opc.name, fields)
