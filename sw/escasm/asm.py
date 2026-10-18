"""Two-pass assembler for Escapement.

Syntax (one statement per line; `;` or `#` starts a comment):

    label:                      define a label at the current word address
    MNEMONIC operands           instruction (case-insensitive), operands comma-separated
    .equ NAME, expr             constant
    .org expr                   set the current word address
    .word expr                  emit a raw 32-bit word
    .entry ctx, label           context `ctx` starts at `label` and is enabled
    .own pin, ctx               context `ctx` owns its pin `pin`: 0..7 in bank A; in bank B
                                0..4, which are B0..B4 (also written B0..B4)
    .od pin                     bank A pin `pin` is open-drain
    .bank ctx                   context `ctx` uses bank B (v1.2); put it before that
                                context's .own lines

Operands: registers r0..r3; integers (123, 0x7B, 0b1111011); labels and .equ names;
`LSB`/`MSB` for dir; `HOLD`/`ARRIVE` for mode; `+ - * << >> | & ~ ( )` expressions.
Unused memory is filled with 0x00000000, which is ILLEGAL, so stray fetches trap.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .isa import ISA, Opcode, load_isa


class AsmError(Exception):
    pass


KEYWORDS = {"LSB": 0, "MSB": 1, "HOLD": 0, "ARRIVE": 1}
REGS = {"r0": 0, "r1": 1, "r2": 2, "r3": 3}

# Operand order per format (assembly syntax), mapped to encoding fields.
SYNTAX = {
    "N": [],
    "I8": ["code"],
    "R": ["rd", "rs"],
    "RD": ["rd"],
    "RS": ["rs"],
    "I16": ["rd", "imm"],
    "SH": ["rd", "n"],
    "B": ["target"],
    "L16": ["imm"],
    "T24": ["imm"],
    "P": ["mask", "value", "oe"],
    "PR": ["mask", "rs", "oe"],
    "PX": ["mask", "dir", "oe"],
    "PE": ["mask", "level", "mode", "window"],
    "PS": ["pin", "dir"],
    "PW": ["mask", "value"],
    "TM": ["ra", "rb", "imm"],
}
REG_FIELDS = {"rd", "rs", "ra", "rb"}


@dataclass
class Image:
    words: list[int]
    entry_pcs: list[int]
    ctx_enable: list[bool]
    pin_owned: list[bool]       # physical pins 0..12 (8..12: bank B, v1.2)
    pin_owner: list[int]
    pin_od: list[bool]          # bank A only
    symbols: dict[str, int] = field(default_factory=dict)
    listing: list[str] = field(default_factory=list)
    used_words: int = 0
    # v1.1 tape initial contents (host-loaded with WRITE_TAPE; the SRAM powers up undefined)
    tape: list[int] = field(default_factory=lambda: [0] * 256)
    tape_used: list[int] = field(default_factory=list)   # addresses set by .tape
    # v1.2: contexts whose logical pins are bank B
    ctx_bank: list[bool] = field(default_factory=lambda: [False] * 4)

    def __post_init__(self) -> None:
        # images from v1.1 tools describe 8 pins; bank B pins are then unowned
        self.pin_owned = list(self.pin_owned) + [False] * (13 - len(self.pin_owned))
        self.pin_owner = list(self.pin_owner) + [0] * (13 - len(self.pin_owner))

    # Packed forms matching the esc_core configuration ports (docs/11-SEMANTICS.md §10).
    @property
    def entry_pcs_packed(self) -> int:
        return sum((pc & 0xFF) << (8 * k) for k, pc in enumerate(self.entry_pcs))

    @property
    def ctx_enable_mask(self) -> int:
        return sum(1 << k for k, e in enumerate(self.ctx_enable) if e)

    @property
    def pin_owned_mask(self) -> int:
        return sum(1 << i for i, o in enumerate(self.pin_owned) if o)

    @property
    def pin_owner_packed(self) -> int:
        return sum((o & 3) << (2 * i) for i, o in enumerate(self.pin_owner))

    @property
    def pin_od_mask(self) -> int:
        return sum(1 << i for i, o in enumerate(self.pin_od) if o)

    @property
    def ctx_bank_mask(self) -> int:
        return sum(1 << k for k, b in enumerate(self.ctx_bank) if b)

    @property
    def uses_bank_b(self) -> bool:
        return any(self.ctx_bank) or any(self.pin_owned[8:])

    def to_json(self) -> str:
        return json.dumps({
            "words": [f"{w:08x}" for w in self.words],
            "entry_pcs": self.entry_pcs, "ctx_enable": self.ctx_enable,
            "pin_owned": self.pin_owned, "pin_owner": self.pin_owner, "pin_od": self.pin_od,
            "ctx_bank": self.ctx_bank, "symbols": self.symbols, "used_words": self.used_words,
            "tape": {f"{a}": f"{self.tape[a]:08x}" for a in self.tape_used},
        }, indent=1)

    @classmethod
    def from_json(cls, text: str) -> "Image":
        d = json.loads(text)
        tape = [0] * 256
        for a, w in d.get("tape", {}).items():
            tape[int(a)] = int(w, 16)
        return cls([int(w, 16) for w in d["words"]], d["entry_pcs"], d["ctx_enable"],
                   d["pin_owned"], d["pin_owner"], d["pin_od"], d.get("symbols", {}),
                   [], d.get("used_words", 0), tape, sorted(int(a) for a in d.get("tape", {})),
                   d.get("ctx_bank", [False] * 4))


_ALLOWED_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name,
                  ast.Add, ast.Sub, ast.Mult, ast.LShift, ast.RShift, ast.BitOr,
                  ast.BitAnd, ast.BitXor, ast.Invert, ast.USub, ast.UAdd, ast.Load,
                  ast.FloorDiv, ast.Mod)


def _eval(expr: str, symbols: dict[str, int], line: int, allow_undefined: bool) -> int:
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise AsmError(f"{line}: bad expression '{expr}'") from e
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise AsmError(f"{line}: unsupported syntax in '{expr}'")
    env: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            name = node.id
            if name.upper() in KEYWORDS:
                env[name] = KEYWORDS[name.upper()]
            elif name in symbols:
                env[name] = symbols[name]
            elif allow_undefined:
                env[name] = 0
            else:
                raise AsmError(f"{line}: undefined symbol '{name}'")
    return int(eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, env))


def _split_operands(text: str) -> list[str]:
    return [p.strip() for p in text.split(",")] if text.strip() else []


def _put(word: int, fld: tuple[int, int], value: int, name: str, line: int) -> int:
    msb, lsb = fld
    width = msb - lsb + 1
    if not 0 <= value < (1 << width):
        raise AsmError(f"{line}: {name}={value} does not fit {width} bits")
    return word | (value << lsb)


def _encode(opc: Opcode, operands: list[str], symbols: dict[str, int], line: int,
            final: bool) -> int:
    names = SYNTAX[opc.format]
    if len(operands) != len(names):
        raise AsmError(f"{line}: {opc.name} takes {len(names)} operand(s), got {len(operands)}")
    word = opc.op << 24
    for name, text in zip(names, operands):
        if name in REG_FIELDS:
            reg = REGS.get(text.lower())
            if reg is None:
                raise AsmError(f"{line}: expected register r0..r3, got '{text}'")
            value = reg
        else:
            value = _eval(text, symbols, line, allow_undefined=not final)
            if not final:
                value = 0
        word = _put(word, opc.fields[name], value, name, line)
    return word


_LABEL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):")
_INCLUDE = re.compile(r'^\.include\s+"([^"]+)"\s*$', re.IGNORECASE)


def _expand(source: str, name: str, base: Path | None, ns: str, depth: int = 0):
    """Yield (ns, where, text) for every source line, expanding .include "file".
    Each included file gets its own label/constant namespace (its file stem)."""
    if depth > 8:
        raise AsmError(f"{name}: .include nested too deeply")
    for n, raw in enumerate(source.splitlines(), 1):
        text = re.split(r"[;#]", raw, maxsplit=1)[0].strip()
        m = _INCLUDE.match(text)
        if m:
            if base is None:
                raise AsmError(f"{name}:{n}: .include needs a file context (use assemble_file)")
            path = (base / m.group(1)).resolve()
            if not path.exists():
                raise AsmError(f"{name}:{n}: cannot include '{m.group(1)}'")
            yield from _expand(path.read_text(), path.name, path.parent, path.stem, depth + 1)
        else:
            yield ns, f"{name}:{n}", raw, text


def assemble(source: str, isa: ISA | None = None, base_dir: str | Path | None = None,
             name: str = "<source>") -> Image:
    """Assemble `source`. `.include "f.asm"` (relative to base_dir) pulls in a module whose
    labels and .equ/.default constants are private to it (namespace = file stem); names
    resolve in the module first, then globally. `.equ mod.NAME, v` in the top file
    pre-sets a module's `.default NAME, ...` (e.g. which context and pins it uses)."""
    isa = isa or load_isa()
    lines = list(_expand(source, name, Path(base_dir) if base_dir else None, ""))

    def statements():
        for ns, where, raw, text in lines:
            while True:
                m = _LABEL.match(text)
                if not m:
                    break
                yield ns, where, raw, "label", m.group(1), ""
                text = text[m.end():].strip()
            if text:
                head, _, rest = text.partition(" ")
                yield ns, where, raw, "stmt", head, rest.strip()

    def qual(ns: str, nm: str) -> str:
        return nm if (not ns or "." in nm) else f"{ns}.{nm}"

    def scope(ns: str) -> dict[str, int]:
        view = {k: v for k, v in symbols.items() if "." not in k}
        if ns:
            pre = ns + "."
            view.update({k[len(pre):]: v for k, v in symbols.items() if k.startswith(pre)})
        return view

    symbols: dict[str, int] = {}
    for final in (False, True):
        pc = 0
        words: dict[int, int] = {}
        entry = [0] * isa.contexts
        enable = [False] * isa.contexts
        owned = [False] * 13
        owner = [0] * 13
        od = [False] * 8
        bank = [False] * isa.contexts
        owns_seen: set[int] = set()
        tape: dict[int, int] = {}
        listing: list[str] = []
        labels_seen: set[str] = set()
        for ns, where, raw, kind, head, rest in statements():
            if kind == "label":
                q = qual(ns, head)
                if q in labels_seen:
                    raise AsmError(f"{where}: duplicate label '{head}'")
                labels_seen.add(q)
                if not final:
                    symbols[q] = pc
                continue
            ops = _split_operands(rest)
            directive = head.lower()
            sc = scope(ns)
            ev = lambda t: _eval(t, sc, where, allow_undefined=not final)  # noqa: E731
            if directive in (".equ", ".default"):
                if len(ops) != 2:
                    raise AsmError(f"{where}: {directive} NAME, value")
                q = qual(ns, ops[0])
                if directive == ".equ" or q not in symbols:   # .default never overrides
                    symbols[q] = ev(ops[1])
            elif directive == ".tape":
                # .tape ADDR, v0, v1, ... : initial tape words at ADDR, ADDR+1, ...
                if len(ops) < 2:
                    raise AsmError(f"{where}: .tape ADDR, value[, value...]")
                base_addr = ev(ops[0])
                for i, op in enumerate(ops[1:]):
                    ta = base_addr + i
                    if not 0 <= ta < 256:
                        raise AsmError(f"{where}: tape address {ta} out of range")
                    if final:
                        if ta in tape:
                            raise AsmError(f"{where}: tape word {ta} written twice")
                        tape[ta] = ev(op) & 0xFFFFFFFF
            elif directive == ".org":
                pc = ev(ops[0])
            elif directive == ".word":
                if final:
                    words[pc] = ev(ops[0]) & 0xFFFFFFFF
                pc += 1
            elif directive == ".entry":
                ctx, target = ev(ops[0]), ev(ops[1])
                if not 0 <= ctx < isa.contexts:
                    raise AsmError(f"{where}: context {ctx} out of range")
                if final and enable[ctx]:
                    raise AsmError(f"{where}: context {ctx} already has an entry point")
                entry[ctx], enable[ctx] = target, True
            elif directive == ".own":
                if len(ops) != 2:
                    raise AsmError(f"{where}: .own pin, ctx")
                mb = re.fullmatch(r"[Bb]([0-4])", ops[0].strip())
                pin = 8 + int(mb.group(1)) if mb else ev(ops[0])
                ctx = ev(ops[1])
                if not (0 <= pin < 8 or mb) or not 0 <= ctx < isa.contexts:
                    raise AsmError(f"{where}: .own pin 0..7 or B0..B4, ctx 0..3")
                if bank[ctx] and not mb:          # a bank B context names its logical pin
                    if pin >= 5:
                        raise AsmError(f"{where}: context {ctx} uses bank B: pins 0..4")
                    pin += 8
                owns_seen.add(ctx)
                if final and owned[pin] and owner[pin] != ctx:
                    raise AsmError(f"{where}: pin {pin} already owned by context {owner[pin]}")
                owned[pin], owner[pin] = True, ctx
            elif directive == ".od":
                pin = ev(ops[0])
                if not 0 <= pin < 8:
                    raise AsmError(f"{where}: .od pin 0..7 (bank B pins cannot be open-drain)")
                od[pin] = True
            elif directive == ".bank":
                ctx = ev(ops[0])
                if len(ops) != 1 or not 0 <= ctx < isa.contexts:
                    raise AsmError(f"{where}: .bank ctx (0..3)")
                if ctx in owns_seen and not bank[ctx]:
                    raise AsmError(f"{where}: .bank {ctx} after a .own for context {ctx}")
                bank[ctx] = True
            elif directive.startswith("."):
                raise AsmError(f"{where}: unknown directive '{head}'")
            else:
                opc = isa.by_name.get(head.upper())
                if opc is None:
                    raise AsmError(f"{where}: unknown mnemonic '{head}'")
                word = _encode(opc, ops, sc, where, final)
                if final:
                    if pc in words:
                        raise AsmError(f"{where}: address {pc} written twice")
                    words[pc] = word
                    listing.append(f"{pc:3d}  {word:08x}  {raw.strip()}")
                pc += 1
            if pc > isa.imem_words:
                raise AsmError(f"{where}: program exceeds {isa.imem_words} words")
    for p in range(13):                 # a context only ever uses pins of its own bank
        if owned[p] and bank[owner[p]] != (p >= 8):
            name = f"B{p - 8}" if p >= 8 else str(p)
            raise AsmError(f"pin {name} is owned by context {owner[p]}, which uses bank "
                           f"{'B' if bank[owner[p]] else 'A'}")
    image_words = [words.get(a, 0) for a in range(isa.imem_words)]
    return Image(image_words, entry, enable, owned, owner, od,
                 {k: v for k, v in symbols.items()}, listing, len(words),
                 [tape.get(a, 0) for a in range(256)], sorted(tape), bank)


def assemble_file(path: str | Path, isa: ISA | None = None) -> Image:
    path = Path(path)
    return assemble(path.read_text(), isa, base_dir=path.parent, name=path.name)
