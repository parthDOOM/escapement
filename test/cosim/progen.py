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
    CAPT with sensible or garbage configurations (a quarter of the sensible ones in
    stream mode, v1.3), GETC, SAMPLE -> GETI (GETI waits)."""
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
            if (base * 7 + size) % 4 == 0:
                v |= 1 << 26                       # stream (v1.3); no host acks: holds
            if rng.random() < 0.15:
                v &= 0x7FFFFFFF                    # disable
        return [f"    MOVI {reg}, {v & 0xFFFF}", f"    MOVHI {reg}, {v >> 16}", f"    CAPT {reg}"]
    if r < 0.80:
        return [f"    GETC {_reg(rng)}"]
    return [f"    SAMPLE {rng.randrange(8)}, {rng.choice(['LSB', 'MSB'])}",
            f"    GETI {_reg(rng)}"]


LU_CFG = 0xF4      # v1.4: line unit configuration blocks at tape words 0xF4..0xFF


def lu_blocks(rng: random.Random, owned: dict[int, list[int]], owner: int) -> list[str]:
    """Four configuration blocks (W0, W1, W2) at LU_CFG: mostly valid, with short bit
    periods so packets fit a run. Block 0 is valid on the owner's pins (no tx_pins if it
    owns fewer than two); the others may be invalid or on pins it does not own (CONFIG
    faults OWNER or CONFIG)."""
    pins = sorted({p for ps in owned.values() for p in ps}) or [0, 1]
    mine = owned.get(owner) or []
    words = []
    for b in range(4):
        d, n, r = rng.choice(pins), rng.choice(pins), rng.choice(pins)
        if b == 0 and len(mine) >= 2:
            d, n = rng.sample(mine, 2)
        elif b == 0:
            d = n = r = rng.choice(mine or [0])
        w0 = (d | n << 3 | r << 6 | rng.randrange(2) << 9 | rng.randrange(2) << 10
              | rng.randrange(2) << 11 | rng.choice([0, 0, 1, 3, 5, 6]) << 12 | rng.randrange(2) << 15
              | rng.randrange(4) << 16 | rng.randrange(2) << 18 | rng.randrange(2) << 19
              | int(rng.random() < 0.8) << 20 | rng.choice([0, 0x80, rng.randrange(256)]) << 24)
        p = rng.choice([8, 9, 12, 16])
        w1 = p | rng.choice([0, 0, 64, 255]) << 12 | rng.randrange(4, p) << 20
        if rng.random() < 0.15:
            w1 = rng.choice([7 | 5 << 20, 10 | 10 << 20, 10 | 3 << 20])     # invalid
        w2 = (rng.choice([0, 0x8005, 0x2800, 0x8B32, 0x3100]) | rng.randrange(1 << 9) << 16
              | rng.choice([0, 0, 1, 2]) << 21)
        if b == 0:
            w1 = p | rng.choice([0, 64]) << 12 | rng.randrange(4, p) << 20
            if len(mine) < 2:
                w0 &= ~(1 << 20)                                            # receive only
        elif rng.random() < 0.1:
            w0 = rng.randrange(1 << 32)                                     # anything
        words += [w0, w1, w2]
    return [f".tape {LU_CFG}, " + ", ".join(f"0x{w:08x}" for w in words)]


def _line_op(rng: random.Random, nasty: bool) -> list[str]:
    """v1.4 line unit traffic: CONFIG, RECV, CRC, SEND, GETL waits and OFF, from any
    context (only the owner may, so the others fault OWNER), with real waits on jobs and
    on the unit's tape cycles."""
    reg = _reg(rng)
    r = rng.random()

    def val(v: int) -> list[str]:
        return [f"    MOVI {reg}, {v & 0xFFFF}", f"    MOVHI {reg}, {(v >> 16) & 0xFFFF}"]

    if r < 0.14:
        return val(3 << 28 | (LU_CFG + 3 * rng.randrange(4))) + [f"    LINE {reg}"]
    if r < 0.30:
        lim = rng.choice([0, 1, 2, 8, 31, rng.randrange(32)])
        return val(1 << 28 | lim << 8 | rng.randrange(0x80, 0xF0)) + [f"    LINE {reg}"]
    if r < 0.40:
        nb = rng.choice([0, 1, 5, 16, 32, 33, 64, rng.randrange(256)])
        return val(2 << 28 | rng.randrange(2) << 16 | nb << 8 | rng.randrange(256)) + [f"    LINE {reg}"]
    if r < 0.62:
        nb = rng.choice([0, 0, 1, 8, 16, 32, 33, rng.randrange(128)])
        v = rng.randrange(256) << 16 | rng.randrange(2) << 15 | nb << 8 | rng.randrange(256)
        lead = rng.choice([1, 2, 6, 20, 40, rng.randrange(1, 300)])
        return [f"    RDTIME {reg}", f"    ADDI {reg}, {lead}", f"    SETT {reg}"] + val(v) + [f"    SEND {reg}"]
    if r < 0.86:
        return [f"    ADDT {rng.choice([1, 8, 50, 300, 2000])}", f"    GETL {reg}"]
    if r < 0.94:
        return val(rng.randrange(1 << 28)) + [f"    LINE {reg}"]          # OFF
    v = rng.randrange(4, 16) << 28 if nasty else 0
    return val(v) + [f"    LINE {reg}"]                                    # ILLEGAL (nasty)


def gen_context(rng: random.Random, k: int, owned: list[int], length: int,
                nasty: bool = True, capt_bank: float = 0.0,
                lu: random.Random | None = None, lu_rate: float = 0.0) -> list[str]:
    lines = [f"ctx{k}_start:"]
    if lu is not None and lu_rate > 0.1:          # the line unit's intended owner
        lines += [f"    MOVI r3, {LU_CFG}", "    MOVHI r3, 0x3000", "    LINE r3"]
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
        if lu is not None and lu.random() < lu_rate:   # v1.4 line unit (own generator)
            lines += _line_op(lu, nasty)
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
    # v1.4: a third of the seeds use the line unit, from a generator of their own, so
    # every other seed keeps its earlier program
    rngl = random.Random(seed * 104729 + 0x1E)
    use_lu = rngl.random() < 0.35
    if use_lu:
        out += lu_blocks(rngl, owned, ctxs[0])
    body: list[str] = []
    for k in ctxs:
        length = rng.randrange(8, max(9, budget // (n_ctx * 2)))
        body += gen_context(rng, k, owned[k], length, nasty, capt_bank,
                            rngl if use_lu else None, 0.25 if k == ctxs[0] else 0.02)
    # Keep inside 256 words: trim (by context) if the assembler would overflow.
    return "\n".join(out + body) + "\n"


def gen_lu_program(seed: int) -> str:
    """v1.4: a firmware-shaped line unit program. The owner configures the unit (block 0
    valid on its pins), then loops over receptions, packets sent at a fresh deadline or
    right after the last job's end tick, CRC jobs, GETL with and without a timeout, OFF
    and reconfiguration, and LDT/STT bursts that meet the unit's tape cycles. Another
    context keeps emitting on its own pins and using the tape. Board: lu_board."""
    rng = random.Random(seed * 7 + 0x14)
    owner, other = rng.sample(range(4), 2)
    mine = rng.sample(range(8), 3)
    theirs = [p for p in range(8) if p not in mine][:3]
    owned = {owner: mine, other: theirs}
    out = [f"; generated by progen gen_lu_program seed {seed}"]
    out += [f".own {p}, {owner}" for p in mine] + [f".own {p}, {other}" for p in theirs]
    out += lu_blocks(rng, owned, owner)
    out += [f".tape 0x80, " + ", ".join(f"0x{rng.randrange(1 << 32):08x}" for _ in range(16))]
    out += [f".entry {owner}, own", f".entry {other}, oth"]
    body = ["own:", f"    MOVI r3, {LU_CFG}", "    MOVHI r3, 0x3000", "    LINE r3",
            "    ADDT 40", "    GETL r0", "loop:"]
    n = 0
    for _ in range(rng.randrange(6, 14)):
        n += 1
        r = rng.random()
        if r < 0.3:                                   # receive, wait with a timeout
            lim = rng.choice([1, 2, 4, 8, 0])
            body += [f"    MOVI r1, {lim << 8 | 0xC0}", "    MOVHI r1, 0x1000", "    LINE r1",
                     f"    ADDT {rng.choice([200, 800, 3000])}", "    GETL r0"]
        elif r < 0.55:                                # a packet, from the end tick or fresh
            nb = rng.choice([0, 8, 16, 33, 64])
            v = rng.randrange(256) << 16 | rng.randrange(2) << 15 | nb << 8 | 0x80
            if rng.random() < 0.5:
                body += [f"    ADDT {rng.choice([1, 4, 40, 72])}"]
            else:
                body += ["    RDTIME r2", f"    ADDI r2, {rng.choice([1, 2, 12, 30])}", "    SETT r2"]
            body += [f"    MOVI r1, {v & 0xFFFF}", f"    MOVHI r1, {v >> 16}", "    SEND r1",
                     f"    ADDT {rng.choice([100, 2000])}", "    GETL r0"]
        elif r < 0.68:                                # CRC job over tape words
            nb = rng.choice([5, 16, 33, 64, 255])
            body += [f"    MOVI r1, {nb << 8 | 0x80}", f"    MOVHI r1, {0x2000 | rng.randrange(2)}",
                     "    LINE r1", "    ADDT 400", "    GETL r0"]
        elif r < 0.85:                                # tape traffic against the unit's cycles
            body += [f"    {rng.choice(['LDT', 'STT'])} r{rng.randrange(4)}, r3, {rng.randrange(8)}"
                     for _ in range(rng.randrange(1, 5))]
        elif r < 0.93:                                # another configuration
            body += [f"    MOVI r1, {LU_CFG + 3 * rng.randrange(4)}", "    MOVHI r1, 0x3000",
                     "    LINE r1", "    ADDT 40", "    GETL r0"]
        else:                                         # OFF, and back
            body += ["    MOVI r1, 0", "    LINE r1", "    LINE r3"]
    body += ["    JMP loop"]
    body += ["oth:", "    ADDT 30", "oloop:"]
    for _ in range(rng.randrange(2, 6)):
        m = 0
        for p in range(len(theirs)):
            if rng.random() < 0.6:
                m |= 1 << p
        m = m or 1
        lm = sum(1 << theirs[i] for i in range(len(theirs)) if (m >> i) & 1)
        body += [f"    ADDT {rng.choice([3, 17, 60])}", f"    EMIT 0x{lm:02x}, 0x{rng.randrange(256) & lm:02x}, 0x{lm:02x}"]
        if rng.random() < 0.4:
            body += [f"    LDT r1, r0, {rng.randrange(0x80, 0x90)}"]
    body += ["    WAITT", "    JMP oloop"]
    return "\n".join(out + body) + "\n"


def lu_board(seed: int):
    """The line unit's world: an idle-high bus that loops the chip's drive back (so a
    receive can see the unit's own packets), with seeded noise bursts and SE0-like drops
    on every pin, and occasional long quiet stretches."""
    rng = random.Random(seed ^ 0x11E)
    bursts = []
    t = rng.randrange(50, 400)
    while t < 200000:
        bursts.append((t, t + rng.choice([3, 20, 120, 600]), rng.randrange(256), rng.choice([2, 5, 9, 16])))
        t += rng.choice([300, 1500, 5000])

    def board(t: int, uio_out: int, uio_oe: int) -> int:
        if t < 0:
            return 0
        ext = 0xFF
        for s, e, mask, per in bursts:
            if s <= t < e:
                if ((t - s) // per) & 1:
                    ext &= ~mask
                break
            if s > t:
                break
        return ((uio_oe & uio_out) | (~uio_oe & ext)) & 0xFF

    return board


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
