#!/usr/bin/env python3
"""Generated RTL mutants for the mutation campaign (run_mutants.py).

Every synthesisable line of the RTL, outside comments, `ifdef FORMAL` blocks, port lists
and parameter definitions, is scanned for mutation sites. Each site is one classic fault:

    rel    == and != swapped; < becomes <=; > becomes >=; >= becomes >
    logic  && and || swapped
    bit    binary & and | swapped; ^ becomes |
    not    a ~ or ! removed
    arith  binary + and - swapped
    const  1'b0 and 1'b1 swapped; a sized decimal constant moved by one
    cond   an if condition negated
    stuck  a register keeps its value instead of taking the assignment

A site is chosen when a hash of its file, operator and line text falls under the rate, so
the choice of one mutant never depends on any other: editing one line of the RTL changes
only that line's mutants. `python3 generate.py` lists them.
"""
from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
FILES = ["esc_core.v", "esc_ctx.v", "esc_cap.v", "esc_line.v", "esc_greg.v", "escapement_host.v",
         "escapement_sram.v", "project.v"]
RATE = 0.30            # fraction of all sites taken; about 400 mutants on v1.3
SALT = "escapement-mutation-1"

SKIP_LINE = re.compile(r"^\s*(module|endmodule|input|output|inout|parameter|localparam|`|function|endfunction|"
                       r"task|endtask|genvar|integer|initial|always|\$|\);|\)\s*;)|\bfor\s*\(|_unused|"
                       r"\$display|\$finish")


@dataclass
class Mutant:
    id: str
    file: str
    line: int          # 1-based
    old: str           # the whole original line
    new: str           # the whole mutated line
    op: str
    desc: str


def code_part(line: str) -> str:
    """The line with its // comment blanked (same length, so columns stay valid)."""
    i = line.find("//")
    return line if i < 0 else line[:i] + " " * (len(line) - i)


def bracket_mask(code: str) -> list[bool]:
    """True for columns inside [...] (bit selects and widths are left alone)."""
    depth, mask = 0, []
    for ch in code:
        if ch == "[":
            depth += 1
        mask.append(depth > 0)
        if ch == "]":
            depth = max(0, depth - 1)
    return mask


def usable_lines(text: str):
    """(lineno, line) of synthesisable code: no block comments, no FORMAL sections."""
    in_block, stack = False, []
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if in_block:
            if "*/" in s:
                in_block = False
            continue
        if s.startswith("/*"):
            in_block = "*/" not in s
            continue
        m = re.match(r"`(ifdef|ifndef)\s+(\w+)", s)
        if m:
            stack.append(m.group(2) == "FORMAL" if m.group(1) == "ifdef" else False)
            continue
        if s.startswith("`else"):
            if stack:
                stack[-1] = False   # the else branch of an `ifdef FORMAL is synthesised
            continue
        if s.startswith("`endif"):
            if stack:
                stack.pop()
            continue
        if any(stack) or not s or s.startswith("//") or SKIP_LINE.search(line):
            continue
        yield n, line


def is_binary(code: str, i: int) -> bool:
    """An operator at column i is binary if the previous non-space token ends an operand."""
    j = i - 1
    while j >= 0 and code[j] == " ":
        j -= 1
    return j >= 0 and (code[j].isalnum() or code[j] in ")]}_'")


def sites(line: str):
    """(op, start, end, replacement, description) for every mutation site of a line."""
    code = code_part(line)
    inb = bracket_mask(code)
    out = []

    def add(op, a, b, rep, desc):
        if not inb[a]:
            out.append((op, a, b, rep, desc))

    for m in re.finditer(r"==|!=", code):
        add("rel", m.start(), m.end(), "!=" if m.group() == "==" else "==", f"{m.group()} becomes the opposite")
    for m in re.finditer(r"(?<![<>=!-])>(?![>=])", code):
        if is_binary(code, m.start()):
            add("rel", m.start(), m.end(), ">=", "> becomes >=")
    for m in re.finditer(r">=", code):
        add("rel", m.start(), m.end(), ">", ">= becomes >")
    for m in re.finditer(r"(?<![<])<(?![<=])", code):
        if is_binary(code, m.start()):
            add("rel", m.start(), m.end(), "<=", "< becomes <=")
    for m in re.finditer(r"&&|\|\|", code):
        add("logic", m.start(), m.end(), "||" if m.group() == "&&" else "&&", f"{m.group()} swapped")
    for m in re.finditer(r"(?<![&|~^])([&|^])(?![&|~^=])", code):
        if is_binary(code, m.start()):
            rep = {"&": "|", "|": "&", "^": "|"}[m.group(1)]
            add("bit", m.start(), m.end(), rep, f"binary {m.group(1)} becomes {rep}")
    for m in re.finditer(r"~(?=[\w(\s{])|!(?=[\w(])", code):
        if m.group() == "!" and code[m.start():m.start() + 2] == "!=":
            continue
        add("not", m.start(), m.end(), "", f"{m.group()} removed")
    for m in re.finditer(r"(?<![+\-])([+\-])(?![+\-=:])", code):
        if is_binary(code, m.start()):
            add("arith", m.start(), m.end(), "-" if m.group(1) == "+" else "+", f"binary {m.group(1)} swapped")
    for m in re.finditer(r"\b1'b([01])\b", code):
        add("const", m.start(), m.end(), "1'b" + ("1" if m.group(1) == "0" else "0"), f"1'b{m.group(1)} flipped")
    for m in re.finditer(r"\b(\d+)'d(\d+)\b", code):
        w, v = int(m.group(1)), int(m.group(2))
        if w > 1:
            nv = v + 1 if v + 1 < (1 << w) else v - 1
            add("const", m.start(), m.end(), f"{w}'d{nv}", f"{w}'d{v} becomes {w}'d{nv}")
    for m in re.finditer(r"\bif\s*\(", code):
        depth, j = 0, m.end() - 1
        for j in range(m.end() - 1, len(code)):
            depth += code[j] == "("
            depth -= code[j] == ")"
            if depth == 0:
                break
        if depth == 0 and "rst_n" not in code[m.end():j]:      # the reset branch is not a condition
            cond = code[m.end():j]
            add("cond", m.start(), j + 1, f"if (!({cond}))", "if condition negated")
    # a register keeps its value: `x <= expr;` becomes `x <= x;` (one statement at a time).
    # Not for a constant right-hand side (a reset or START value): holding there is a missing
    # reset, which the equivalence check cannot judge (it starts every register at 0); the
    # const mutants cover those values instead.
    for m in re.finditer(r"(?<![\w\]])(\w+(?:\[[^\]]*\])?)\s*<=\s*([^;]+);", code):
        lhs, rhs = m.group(1), m.group(2).strip()
        if re.fullmatch(r"\d+'[bdh][0-9a-fA-F_]+|\{?\s*\d+\s*\{\s*1'b[01]\s*\}\s*\}?", rhs):
            continue
        # (a `<=` in an assign or wire declaration is a comparison, not a register)
        if rhs != lhs and not re.match(r"\s*(assign|wire)\s", code) and lhs not in ("if", "wire"):
            out.append(("stuck", m.start(2), m.start(2) + len(m.group(2)), lhs, f"{lhs} keeps its value"))
    return out


def chosen(key: str, rate: float) -> bool:
    return int(hashlib.sha1((SALT + key).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < rate


def generate(rate: float = RATE, files=FILES) -> list[Mutant]:
    muts = []
    for f in files:
        text = (SRC / f).read_text()
        seen: dict[str, int] = {}
        for n, line in usable_lines(text):
            k = line.strip()
            seen[k] = seen.get(k, 0) + 1          # identical lines (reset and START branches) differ
            for idx, (op, a, b, rep, desc) in enumerate(sites(line)):
                key = f"{f}|{op}|{k}|{seen[k]}|{idx}"
                if not chosen(key, rate):
                    continue
                new = line[:a] + rep + line[b:]
                if new == line:
                    continue
                mid = f"{Path(f).stem}_{op}_{hashlib.sha1(key.encode()).hexdigest()[:8]}"
                muts.append(Mutant(mid, f, n, line, new, op, f"{f}:{n} {desc}"))
    return muts


if __name__ == "__main__":
    rate = float(sys.argv[1]) if len(sys.argv) > 1 else RATE
    ms = generate(rate)
    from collections import Counter
    for m in ms:
        print(f"{m.id:32s} {m.desc}\n    - {m.old.strip()}\n    + {m.new.strip()}")
    print(len(ms), "mutants", dict(Counter(m.op for m in ms)), dict(Counter(m.file for m in ms)))
