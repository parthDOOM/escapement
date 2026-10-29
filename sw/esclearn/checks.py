"""Check bytes: a byte of a frame that, in every recorded frame, is a function of earlier
bytes. Rules: the complement of one byte (NEC's inverted address and command), the XOR,
the 8-bit sum, or the negated sum (sum to zero) of all the bytes before it. A rule is
believed only if the bytes it reads vary between frames (MIN_FRAMES distinct inputs):
a constant byte proves nothing. With the rules found, fill() completes a new frame."""

from __future__ import annotations

from functools import reduce

MIN_FRAMES = 3


def _value(rule, before):
    kind, arg = rule
    if kind == "not":
        return ~before[arg] & 0xFF
    if kind == "xor":
        return reduce(lambda a, b: a ^ b, before, 0)
    if kind == "sum":
        return sum(before) & 0xFF
    return -sum(before) & 0xFF            # "neg"


def find(frames: list[list[int]]) -> list[tuple[int, tuple]]:
    """[(byte index, rule)] that hold in every frame (frames of one length, bytes)."""
    if len(frames) < MIN_FRAMES or len({len(f) for f in frames}) != 1:
        return []
    n = len(frames[0])
    out = []
    for j in range(1, n):
        rules = [("not", i) for i in range(j)] + [("xor", None), ("sum", None), ("neg", None)]
        for rule in rules:
            if rule[0] != "not" and j < 2:
                continue                    # one byte before: XOR and sum are copies
            inputs = {f[rule[1]] if rule[0] == "not" else tuple(f[:j]) for f in frames}
            if len(inputs) >= MIN_FRAMES and all(f[j] == _value(rule, f[:j]) for f in frames):
                out.append((j, rule))
                break
    return out


def describe(j, rule) -> str:
    kind, arg = rule
    what = {"not": f"the complement of byte {arg}", "xor": f"the XOR of bytes 0 to {j - 1}",
            "sum": f"the 8-bit sum of bytes 0 to {j - 1}",
            "neg": f"minus the 8-bit sum of bytes 0 to {j - 1} (the frame sums to 0)"}[kind]
    return f"byte {j} is {what}"


def fill(data: list[int], rules) -> list[int]:
    """`data` with every check byte recomputed, in order."""
    out = list(data)
    for j, rule in rules:
        out[j] = _value(rule, out[:j])
    return out
