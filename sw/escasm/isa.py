"""Load the encoding tables from isa/isa.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

ISA_YAML = Path(__file__).resolve().parents[2] / "isa" / "isa.yaml"


@dataclass(frozen=True)
class Opcode:
    name: str
    op: int
    format: str
    fields: dict[str, tuple[int, int]]  # field -> (msb, lsb)
    wait_class: bool


@dataclass(frozen=True)
class ISA:
    version: str
    imem_words: int
    contexts: int
    by_name: dict[str, Opcode]
    by_op: dict[int, Opcode]
    fault_causes: dict[str, int]
    admit_subcodes: dict[str, int]
    event_kinds: dict[str, int]

    def reserved_mask(self, opc: Opcode) -> int:
        """Payload bits that must be zero for this opcode."""
        used = 0
        for msb, lsb in opc.fields.values():
            used |= ((1 << (msb - lsb + 1)) - 1) << lsb
        return 0x00FFFFFF & ~used


@lru_cache(maxsize=None)
def load_isa(path: str | None = None) -> ISA:
    data = yaml.safe_load(Path(path or ISA_YAML).read_text())
    formats = {name: {f: tuple(r) for f, r in (fields or {}).items()}
               for name, fields in data["formats"].items()}
    by_name: dict[str, Opcode] = {}
    for name, spec in data["opcodes"].items():
        opc = Opcode(name, int(spec["op"]), spec["format"], formats[spec["format"]],
                     bool(spec.get("wait_class", False)))
        by_name[name] = opc
    by_op = {o.op: o for o in by_name.values()}
    if len(by_op) != len(by_name):
        raise ValueError("duplicate opcode values in isa.yaml")
    return ISA(str(data["version"]), int(data["imem_words"]), int(data["contexts"]),
               by_name, by_op, dict(data["fault_causes"]), dict(data["admit_subcodes"]),
               dict(data["event_kinds"]))
