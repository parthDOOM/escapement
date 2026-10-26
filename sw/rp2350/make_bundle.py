"""Build bring-up bundles for the Tiny Tapeout demo board (run on a PC).

    python sw/rp2350/make_bundle.py [OUT_DIR] [--trace] [FIRMWARE.asm ...]

One bundle per firmware, by default every firmware/*.asm except the replay template.
Each firmware is assembled and run on the ISS in the demo board's environment: nothing
plugged into the bidirectional Pmod and the RP2xxx pull-ups on uio[7:0], so a pin the
chip does not drive reads 1. That is the ISS default board.

The bundle (JSON) carries the image, the configuration, the tape contents and what the
model predicts the chip will show (`expect`):

  halt    every context halts or faults: the final digest (READ_DBG sel 16), context
          states, registers, first fault and pin levels.
  steady  the program settles into a repeating pattern, typically waiting for a host
          mailbox word or an input edge. Waiting commits keep folding into the digest,
          so the bundle carries the digest at a reference tick t0 and the values folded
          in one period; bringup.py stops the chip and finds its digest on that orbit.
  none    neither within the tick budget: only loading, running and the fault state are
          checked.

Variants (VARIANTS) are a firmware assembled with `.equ` overrides under their own
name; `logic_analyzer_stream` is the streaming capture escapement_demo.stream() runs.

sw/rp2350/bringup.py (stage 5) and escapement_demo.py read these files. With --trace,
OUT_DIR/<name>.pins.csv lists every change of uio_out/uio_oe in the model run (tick,
out, oe), to compare with a logic analyser capture of the chip.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sw"))

from escasm import assemble, assemble_file  # noqa: E402
from escmodels import Board, UartRx  # noqa: E402
from esciss import Machine  # noqa: E402
from esciss.machine import DG_POLY, M32, RUNNING, default_board  # noqa: E402

SKIP = {"replay.asm",       # a template: sw/esctape fills in its pins and trace
        "westminster.asm"}  # a 14 s tune, far beyond the model budget below
MAX_TICKS = 2_000_000       # model budget per firmware (80 ms of chip time)
MAX_PERIOD = 8192           # longest repeating pattern searched for, in ticks
CHUNK = 4096
# Firmware with no periodic prediction. The seven-segment animation waits 160 ms between
# frames, so a short repeating wait looks steady until the next frame; the tape-driven
# generated transmitters re-anchor on RDTIME every packet, so the digest never repeats;
# DShot (frames every 3125 ticks beside a context polling its mailbox: 12,500) and SENT
# (an all-zero frame is 11,775 ticks) repeat over more than MAX_PERIOD; CEC and DALI look
# at the host every millisecond while the bus is idle (25,000 ticks).
# Bundled with kind "none": loaded, run and checked for faults only.
NO_PREDICT = {"seven_seg.asm", "ws2812_gen.asm", "dmx512_tx.asm", "dshot_tx.asm", "sent_tx.asm",
              "cec.asm", "dali.asm"}
# Variants: name -> (firmware, .equ overrides). v1.3: the logic analyser streaming a
# 64-word ring to the host during the run (escapement_demo.stream).
VARIANTS = {"logic_analyzer_stream": ("logic_analyzer.asm",
                                      {"STREAM": 1, "BASE": 0x80, "LIM": 0xC0})}
# Optional demo-board smoke tests (bringup.py stage 6): firmware -> UART it transmits.
SMOKE_UART = {"uart_tx.asm": (0, 115200)}


def fold(d: int, x: int) -> int:
    """One active digest edge (docs/11-SEMANTICS.md §10.1)."""
    return (((d << 1) & M32) ^ (DG_POLY if d >> 31 else 0) ^ x) & M32


class Recorder(Machine):
    """The ISS, recording the value x folded at every active digest edge."""

    def __init__(self, image, board=None, board_b=None):
        self.xs: list[tuple[int, int]] = []        # (tick, x)
        super().__init__(image, board, board_b)

    def _digest_edge(self, out, oe, ob, commit):
        active = (commit is not None or self._ld_dg is not None
                  or (out, oe, (ob ^ 0x1F) & 0x1F) != self._dg_prev)
        d0 = self.dg
        super()._digest_edge(out, oe, ob, commit)
        if active:
            self.xs.append((self.now, self.dg ^ fold(d0, 0)))


def snapshot(m: Machine) -> dict:
    """What the host can read back: per context [state, pc, r0..r3], first fault, pins."""
    out, oe = m.outputs()
    return {
        "ctx": [[x.state, x.pc] + list(x.r) for x in m.ctx],
        "first_fault": m.dbg_read(0, 14),
        "fault_tick": m.dbg_read(0, 15),
        "uio": default_board(m.now, out, oe),
        "uio_out": out, "uio_oe": oe,
    }


def _arch(m: Machine) -> tuple:
    """Architectural state that must repeat in a steady pattern (time registers excluded)."""
    return (tuple((x.state, x.pc, tuple(x.r), x.sh, x.lc, x.z, x.c, len(x.fifo),
                   x.chk.active, x.irq_pending) for x in m.ctx), m.outputs(), m.ff.valid)


def _halted(m: Machine) -> bool:
    return (all(x.state != RUNNING and not x.fifo and not x.chk.active for x in m.ctx)
            and m._ld_dg is None)


def _period(m: Recorder, history: dict) -> int | None:
    """Smallest L (multiple of 4) such that the folds of the last 3L ticks repeat with
    period L and the architectural state now equals the state L ticks ago."""
    now = m.now
    window = {}
    for t, x in reversed(m.xs):
        if t < now - 3 * MAX_PERIOD:
            break
        window[t] = x
    seq = [window.get(t) for t in range(now - 3 * MAX_PERIOD, now)]
    arch = _arch(m)
    for L in range(4, MAX_PERIOD + 1, 4):
        w = max(3 * L, CHUNK)                   # a short burst of waits is not a pattern
        if now - w < 0:
            break
        if history.get(now - L) == arch and seq[-w + L:] == seq[-w:-L]:
            return L
    return None


def find_on_orbit(dg0: int, folds: list[int], target: int, m_max: int) -> int | None:
    """Number of folds m <= m_max after which the orbit from dg0 reaches target."""
    d, n = dg0, len(folds)
    for m in range(m_max + 1):
        if d == target:
            return m
        d = fold(d, folds[m % n])
    return None


def predict(image) -> dict:
    m = Recorder(image)
    history: dict[int, tuple] = {}
    while m.now < MAX_TICKS:
        for _ in range(CHUNK):
            m.run(1, stop_when_idle=False)
            if m.now % 4 == 0:
                history[m.now] = _arch(m)
            if _halted(m):
                return dict(kind="halt", ticks=m.now, digest=m.dg, **snapshot(m))
        history = {t: s for t, s in history.items() if t >= m.now - MAX_PERIOD - 8}
        m.xs = [(t, x) for t, x in m.xs if t >= m.now - 3 * MAX_PERIOD]
        L = _period(m, history)
        if L is not None:
            t0, dg0 = m.now, m.dg
            folds = [x for t, x in m.xs if t >= t0 - L]       # one period, in order
            snap = snapshot(m)
            # Self-check: run on and find the new digest on the predicted orbit. A pattern
            # that breaks (e.g. a FIFO-full wait inside a slow protocol) is not steady.
            more = max(3 * L, 4 * CHUNK) + 1
            m.run(more, stop_when_idle=False)
            if find_on_orbit(dg0, folds, m.dg, (more // L + 1) * len(folds)) is not None:
                return dict(kind="steady", t0=t0, dg0=dg0, period=L, folds=folds, **snap)
            history = {}
    return dict(kind="none", ticks=m.now, **snapshot(m))


def no_prediction(image) -> dict:
    m = Machine(image)
    m.run(CHUNK, stop_when_idle=False)
    return dict(kind="none", ticks=CHUNK, **snapshot(m))


def uart_bytes(image, pin: int, baud: int, ticks: int) -> list[int]:
    rx = UartRx(pin, 25e6 / baud)
    m = Machine(image, Board(rx))
    m.run(ticks + 4000, stop_when_idle=False)
    return rx.bytes


def pin_trace(image, ticks: int) -> list[tuple[int, int, int]]:
    m = Machine(image)
    m.run(ticks, stop_when_idle=False)
    out, prev = [], None
    for p in m.pins:
        v = (p["uio_out"], p["uio_oe"])
        if v != prev:
            out.append((p["t"], *v))
            prev = v
    return out


def bundle(fw: Path, name: str | None = None, overrides: dict | None = None) -> dict:
    if overrides:
        src = "".join(f".equ {fw.stem}.{k}, {v}\n" for k, v in overrides.items())
        image = assemble(src + f'.include "{fw.name}"\n', base_dir=fw.parent, name=fw.name)
    else:
        image = assemble_file(fw)
    used = [i for i, w in enumerate(image.words) if w]
    out = {
        "name": name or fw.name,
        "words": image.words[: (max(used) + 1) if used else 0],
        "tape": {str(a): image.tape[a] for a in image.tape_used},
        "cfg": [image.ctx_enable_mask, image.entry_pcs_packed, image.pin_owned_mask,
                image.pin_owner_packed, image.pin_od_mask, image.ctx_bank_mask],
        "expect": predict(image) if fw.name not in NO_PREDICT else no_prediction(image),
    }
    e = out["expect"]
    if e["kind"] == "halt":                     # read by escapement_demo.py
        out["expect_digest"] = e["digest"]
        out["expect_ticks"] = e["ticks"]
    if overrides and overrides.get("STREAM"):  # the region escapement_demo.stream reads
        out["stream"] = {"base": overrides["BASE"], "lim": overrides["LIM"]}
    if fw.name in SMOKE_UART and not overrides:
        pin, baud = SMOKE_UART[fw.name]
        ticks = e.get("ticks", e.get("t0", 0))
        out["uart"] = {"pin": pin, "baud": baud, "bytes": uart_bytes(image, pin, baud, ticks)}
    return out


def main(argv: list[str]) -> None:
    trace = "--trace" in argv
    args = [a for a in argv if a != "--trace"]
    dst = Path(args[0] if args and not args[0].endswith(".asm") else "bundles")
    files = [Path(a) for a in args if a.endswith(".asm")]
    if not files:
        files = sorted(p for p in (ROOT / "firmware").glob("*.asm") if p.name not in SKIP)
    dst.mkdir(parents=True, exist_ok=True)
    jobs = [(fw, fw.stem, None) for fw in files]
    if not any(a.endswith(".asm") for a in args):          # the default set: variants too
        jobs += [(ROOT / "firmware" / f, n, o) for n, (f, o) in VARIANTS.items()]
    for fw, stem, overrides in jobs:
        b = bundle(fw, stem + ".json" if overrides else None, overrides)
        (dst / (stem + ".json")).write_text(json.dumps(b))
        e = b["expect"]
        detail = {"halt": lambda: f"digest 0x{e['digest']:08x} at tick {e['ticks']}",
                  "steady": lambda: f"digest 0x{e['dg0']:08x} at tick {e['t0']}, "
                                    f"period {e['period']} ticks, {len(e['folds'])} folds",
                  "none": lambda: f"no prediction within {e['ticks']} ticks"}[e["kind"]]()
        print(f"{b['name']:24s} {e['kind']:7s} {detail}")
        if trace and not overrides:
            ticks = e.get("ticks", e.get("t0", 0)) + 1000
            rows = "".join(f"{t},{o:#04x},{oe:#04x}\n" for t, o, oe in pin_trace(assemble_file(fw), ticks))
            (dst / (fw.stem + ".pins.csv")).write_text("tick,uio_out,uio_oe\n" + rows)


if __name__ == "__main__":
    main(sys.argv[1:])
