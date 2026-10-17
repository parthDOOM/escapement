"""Host tools for the v1.1 tape (docs/11-SEMANTICS.md §6.5, §7.6).

decode_capture() turns the tape words written by the capture unit into (tick, levels)
pairs with absolute ticks; build_replay() turns such pairs into an assembled image that
re-emits them (firmware/replay.asm). Together: record a real bus, then play it back
with no device present, at the original tick spacing or not at all.
"""

from __future__ import annotations

from pathlib import Path

from escasm import Image, assemble

FW = Path(__file__).resolve().parents[2] / "firmware"
EPOCH = 1 << 24


def capture_order(tape: list[int], status: int, cfg: int) -> list[int]:
    """The captured words, oldest first (handles ring mode), from GETC/sel-17 status and
    the sel-18 configuration."""
    base, lim = cfg & 0xFF, (cfg >> 8) & 0xFF
    ptr, wrapped = status & 0xFF, bool((status >> 28) & 1)
    size = (lim - base) & 0xFF or 256
    region = [(base + i) & 0xFF for i in range(size)]
    k = (ptr - base) & 0xFF
    order = region[k:] + region[:k] if wrapped else region[:k]
    return [tape[a] for a in order]


def decode_capture(tape: list[int], status: int, cfg: int, first_epoch: int = 0
                   ) -> list[tuple[int, int]]:
    """[(absolute tick, levels)] in time order. Tick fields are mod 2^24; a record whose
    field is 0 opens a new epoch (§6.5 epoch markers). `first_epoch` is the epoch of the
    first record (0 for captures armed in the first 0.67 s of a run). In ring mode the
    epoch count is only exact if no marker was overwritten."""
    out, epoch = [], first_epoch
    for i, w in enumerate(capture_order(tape, status, cfg)):
        t24 = w & 0xFFFFFF
        if t24 == 0 and i > 0:
            epoch += 1
        out.append((epoch * EPOCH + t24, (w >> 24) & 0xFF))
    return out


def edges(records: list[tuple[int, int]], pin: int) -> list[tuple[int, int]]:
    """[(tick, level)] of one pin: the first record, then every change."""
    res, prev = [], None
    for t, lv in records:
        b = (lv >> pin) & 1
        if b != prev:
            res.append((t, b))
            prev = b
    return res


def build_replay(records: list[tuple[int, int]], pins: int, ctx: int = 0, lead: int = 200,
                 od: int = 0) -> Image:
    """Assemble firmware/replay.asm with `records` in the tape (rebased to the first
    record; the span must fit one 2^24-tick epoch). Only records that change a pin in
    `pins` are kept, so the trace is as sparse as the replayed signals."""
    kept, prev = [], None
    for t, lv in records:
        if prev is None or (lv ^ prev) & pins:
            kept.append((t, lv))
            prev = lv
    if not kept or len(kept) > 256:
        raise ValueError(f"{len(kept)} records: the tape holds 1..256")
    t0 = kept[0][0]
    if kept[-1][0] - t0 >= EPOCH:
        raise ValueError("trace spans more than one 2^24-tick epoch")
    words = [((lv & 0xFF) << 24) | (t - t0) for t, lv in kept]
    lines = [f".equ replay.CTX, {ctx}", f".equ replay.PINS, {pins}",
             f".equ replay.N, {len(words)}", f".equ replay.LEAD, {lead}"]
    lines += [f".own {p}, {ctx}" for p in range(8) if (pins >> p) & 1]
    lines += [f".od {p}" for p in range(8) if (od >> p) & 1]
    for i in range(0, len(words), 16):
        lines.append(f".tape {i}, " + ", ".join(f"0x{w:08x}" for w in words[i:i + 16]))
    lines.append('.include "replay.asm"')
    return assemble("\n".join(lines) + "\n", base_dir=FW, name="<replay>")
