"""Escapement assembler / disassembler.

Encoding only: everything here is derived from isa/isa.yaml. Execution semantics
live in docs/11-SEMANTICS.md and are implemented independently by the ISS and RTL.
"""

from .isa import ISA, load_isa
from .asm import AsmError, Image, assemble, assemble_file
from .disasm import disassemble

__all__ = ["ISA", "load_isa", "AsmError", "Image", "assemble", "assemble_file", "disassemble"]
