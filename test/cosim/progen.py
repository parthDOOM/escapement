"""Constrained-random Escapement programs and boards for ISS <-> RTL co-simulation.

Programs are assembly text (assembled by escasm), so every generated case is a
readable, replayable artifact. Generation is deterministic in the seed.
The generator deliberately produces faults (ADMIT subcodes, ILLEGAL words, PC_RANGE)
and waits, not only "nice" programs: agreement on failure behaviour is the point.
v1.2: some contexts use pin bank B, and capture may watch it. Those choices come from a
second generator, so a seed that picks no bank B context yields its v1.1 program.
"""

from __future__ import annotations

import random

DATA_R = ["MOV", "ADD", "SUB", "AND", "OR", "XOR", "CMP"]
DATA_I = ["MOVI", "MOVHI", "ADDI", "CMPI", "TSTI"]
BRANCH = ["JZ", "JNZ", "JC", "JNC", "JMP"]


def _reg(rng: random.Random) -> str:
    return f"r{rng.randrange(4)}"


def _mask(rng: random.Random, pins: list[int], nasty: bool = True) -> int:
    if pins and rng.random() < (0.85 if nasty else 0.99):
        m = 0
        for p in pins:
            if rng.random() < 0.6:
                m |= 1 << p
        return m or (1 << rng.choice(pins))
    return rng.randrange(256)  # may hit unowned pins (OWNER) or be 0 (MASK)


def _tape_op(rng: random.Random, nasty: bool, capt_bank: float = 0.0) -> list[str]:
    """v1.1 tape/capture traffic: loads/stores (bursts contend with capture drains),
    CAPT with sensible or garbage configurations, GETC, SAMPLE -> GETI (GETI waits)."""
    r = rng.random()
    if r < 0.40:
        n = rng.choice([1, 1, 2, 3, 6]) if nasty else rng.choice([1, 2, 4, 8])
        return [f"    {rng.choice(['LDT', 'STT'])} {_reg(rng)}, {_reg(rng)}, {rng.randrange(256)}"
                for _ in range(n)]
    if r < 0.65:
        reg = _reg(rng)
        if nasty and rng.random() < 0.4:
            v = rng.randrange(1 << 32)
        else:
            base = rng.randrange(256)
            size = rng.choice([1, 2, 3, 8, 64, 256])
            v = ((1 << 31) | (rng.randrange(2) << 24) | (rng.choice([0xFF, rng.randrange(256)]) << 16)
                 | (((base + size) & 0xFF) << 8) | base)
            if capt_bank and rng.random() < capt_bank:
                v |= 1 << 25                       # watch bank B (v1.2)
            if rng.random() < 0.15:
                v &= 0x7FFFFFFF                    # disable
        return [f"    MOVI {reg}, {v & 0xFFFF}", f"    MOVHI {reg}, {v >> 16}", f"    CAPT {reg}"]
    if r < 0.80:
        return [f"    GETC {_reg(rng)}"]
    return [f"    SAMPLE {rng.randrange(8)}, {rng.choice(['LSB', 'MSB'])}",
            f"    GETI {_reg(rng)}"]


def gen_context(rng: random.Random, k: int, owned: list[int], length: int,
                nasty: bool = True, capt_bank: float = 0.0) -> list[str]:
    lines = [f"ctx{k}_start:"]
    label_n = 0
    pending_forward: list[str] = []

    def new_label() -> str:
        nonlocal label_n
        label_n += 1
        return f"c{k}_L{label_n}"

    # Start with a small tmr lead so early events are usually admissible.
    lines.append(f"    ADDT {rng.randrange(4, 40)}")
    i = 0
    while i < length:
        r = rng.random()
        if not nasty:
            # "nice" profile: keep events admissible and runs long
            if r >= 0.97:
                r = rng.random() * 0.97          # no raw words, fewer HALTs
            if i % 9 == 0:
                reg = _reg(rng)
                lines += [f"    RDTIME {reg}", f"    ADDI {reg}, {rng.randrange(16, 60)}",
                          f"    SETT {reg}"]
            if 0.57 <= r < 0.76:
                lines.append(f"    ADDT {rng.randrange(6, 40)}")
            if 0.91 <= r < 0.94:
                r = 0.92                          # more DJNZ loops
        if pending_forward and rng.random() < 0.25:
            lines.append(f"{pending_forward.pop()}:")
        if rng.random() < 0.10:                    # v1.1 tape / capture / GETI-wait
            ops = _tape_op(rng, nasty, capt_bank)
            lines += ops
            i += len(ops)
            continue
        if r < 0.22:
            op = rng.choice(DATA_R)
            lines.append(f"    {op} {_reg(rng)}, {_reg(rng)}")
        elif r < 0.36:
            op = rng.choice(DATA_I)
            imm = rng.choice([0, 1, 0xFFFF, rng.randrange(1 << 16), rng.randrange(16)])
            lines.append(f"    {op} {_reg(rng)}, {imm}")
        elif r < 0.41:
            lines.append(f"    {rng.choice(['SHL', 'SHR'])} {_reg(rng)}, {rng.choice([0, 1, 31, rng.randrange(32)])}")
        elif r < 0.44:
            lines.append(f"    {rng.choice(['LDSH', 'STSH'])} {_reg(rng)}")
        elif r < 0.52:
            lines.append(f"    ADDT {rng.choice([1, 2, 3, 4, rng.randrange(1, 120), rng.randrange(1, 600)])}")
        elif r < 0.55:
            reg = _reg(rng)
            lines += [f"    RDTIME {reg}", f"    ADDI {reg}, {rng.randrange(0, 80)}", f"    SETT {reg}"]
            i += 2
        elif r < 0.57:
            reg = _reg(rng)
            lines += [f"    MOVI {reg}, {rng.randrange(0, 200)}", f"    ADDTR {reg}"]
            i += 1
        elif r < 0.68:
            m = _mask(rng, owned, nasty)
            kind = rng.random()
            if kind < 0.45:
                lines.append(f"    EMIT 0x{m:02x}, 0x{rng.randrange(256):02x}, 0x{rng.randrange(256) & m | (m if rng.random() < 0.7 else 0):02x}")
            elif kind < 0.7:
                lines.append(f"    EMITR 0x{m:02x}, {_reg(rng)}, 0x{m:02x}")
            else:
                lines.append(f"    EMITX 0x{m:02x}, {rng.choice(['LSB', 'MSB'])}, 0x{m:02x}")
        elif r < 0.72:
            lines.append(f"    EXPECT 0x{rng.randrange(256):02x}, 0x{rng.randrange(256):02x}, "
                         f"{rng.choice(['HOLD', 'ARRIVE'])}, {rng.choice([0, 1, rng.randrange(20), 127])}")
        elif r < 0.76:
            lines.append(f"    SAMPLE {rng.randrange(8)}, {rng.choice(['LSB', 'MSB'])}")
        elif r < 0.80:
            lines.append(f"    {rng.choice(['GETP', 'GETI', 'GETX'])} {_reg(rng)}")
        elif r < 0.83:
            m = rng.randrange(256)
            lines.append(f"    WAITP 0x{m:02x}, 0x{rng.randrange(256) & m:02x}")
        elif r < 0.85:
            lines.append("    WAITT")
        elif r < 0.91:
            lbl = new_label()
            pending_forward.append(lbl)
            lines.append(f"    {rng.choice(BRANCH)} {lbl}")
        elif r < 0.94:
            lbl = new_label()
            n = rng.randrange(1, 5) if nasty else rng.randrange(2, 12)
            lines += [f"    LDLC {n}", f"{lbl}:", f"    ADDT {rng.randrange(1, 50)}"]
            if owned and rng.random() < 0.7:
                lines.append(f"    EMITX 0x{1 << rng.choice(owned):02x}, LSB, 0x{1 << rng.choice(owned):02x}")
            lines.append(f"    DJNZ {lbl}")
            i += 3
        elif r < 0.955:
            lines.append(f"    IRQ {rng.randrange(256)}")
        elif r < 0.97:
            lines.append("    NOP")
        elif r < 0.985:
            # Raw word: random valid opcode with random payload, or pure garbage.
            lines.append(f"    .word 0x{rng.randrange(1 << 32):08x}")
        else:
            lines.append("    HALT")
        i += 1
    for lbl in pending_forward:
        lines.append(f"{lbl}:")
    lines.append("    HALT")
    return lines


def gen_program(seed: int) -> str:
    """Even seeds: "nasty" (deliberate faults, raw words); odd seeds: "nice" (long runs)."""
    rng = random.Random(seed)
    nasty = seed % 2 == 0
    n_ctx = rng.choice([1, 1, 2, 3, 4])
    ctxs = rng.sample(range(4), n_ctx)
    pins = list(range(8))
    rng.shuffle(pins)
    owned: dict[int, list[int]] = {k: [] for k in ctxs}
    out = [f"; generated by progen seed {seed}"]
    own_lines: list[tuple[int, str]] = []
    for p in pins[: rng.randrange(1, 9)]:
        k = rng.choice(ctxs)
        owned[k].append(p)
        own_lines.append((k, f".own {p}, {k}"))
        if rng.random() < 0.3:
            own_lines.append((-1, f".od {p}"))
    # v1.2: contexts in bank B own split pins B0..B4 instead (their logical pins 0..4)
    rngb = random.Random(seed * 7919 + 0xB)
    in_b = [k for k in ctxs if rngb.random() < 0.3]
    for k in in_b:
        owned[k] = []
        out.append(f".bank {k}")
    out += [line for k, line in own_lines if k not in in_b]
    if in_b:
        for j in rngb.sample(range(5), rngb.randrange(1, 6)):
            k = rngb.choice(in_b)
            owned[k].append(j)
            out.append(f".own B{j}, {k}")
    capt_bank = 0.5 if in_b else 0.0
    budget = 240
    # v1.1: host-loaded tape contents, so loads see varied data
    for a in rng.sample(range(0, 256, 16), rng.randrange(0, 4)):
        out.append(f".tape {a}, " + ", ".join(f"0x{rng.randrange(1 << 32):08x}" for _ in range(16)))
    for k in ctxs:
        out.append(f".entry {k}, ctx{k}_start")
    body: list[str] = []
    for k in ctxs:
        length = rng.randrange(8, max(9, budget // (n_ctx * 2)))
        body += gen_context(rng, k, owned[k], length, nasty, capt_bank)
    # Keep inside 256 words: trim (by context) if the assembler would overflow.
    return "\n".join(out + body) + "\n"


def make_board(seed: int):
    """Deterministic external world: per pin, a pull-up, optionally an external
    driver that pulls low in a seeded pattern. uio_in = chip drive where oe=1."""
    rng = random.Random(seed ^ 0x5EED)
    period = [rng.choice([3, 7, 13, 50, 211]) for _ in range(8)]
    phase = [rng.randrange(256) for _ in range(8)]
    active = [rng.random() < 0.5 for _ in range(8)]

    def board(t: int, uio_out: int, uio_oe: int) -> int:
        if t < 0:
            return 0
        ext = 0
        for i in range(8):
            level = 1
            if active[i] and ((t + phase[i]) // period[i]) % 3 == 0:
                level = 0
            ext |= level << i
        return ((uio_oe & uio_out) | (~uio_oe & ext)) & 0xFF

    return board


def make_board_b(seed: int):
    """Bank B world: each ui_in[3+i] is either wired back from uo_out[3+i] or driven
    externally in a seeded pattern."""
    rng = random.Random(seed ^ 0xB0B)
    wired = rng.randrange(32)
    period = [rng.choice([2, 5, 11, 37, 101]) for _ in range(5)]
    phase = [rng.randrange(128) for _ in range(5)]

    def board_b(t: int, ob: int) -> int:
        if t < 0:
            return 0
        ext = sum((((t + phase[i]) // period[i]) & 1) << i for i in range(5))
        return ((ob & wired) | (ext & ~wired)) & 0x1F

    return board_b


def loopback_board(t: int, uio_out: int, uio_oe: int) -> int:
    if t < 0:
        return 0
    return ((uio_oe & uio_out) | (~uio_oe & 0xFF)) & 0xFF
