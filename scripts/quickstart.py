#!/usr/bin/env python3
"""Escapement in one command: one program from its source to the pins, and the one thing
the chip refuses to do.

    python scripts/quickstart.py          (or `make quickstart`, which then runs the RTL)

Needs Python 3.10 or later and nothing else; sigrok-cli is used when it is installed. The RTL
step needs Icarus Verilog and test/requirements.txt.
Each step prints what it did and what to read next. The steps:

  1. assemble firmware/uart_tx.asm, which sends "UK!" and a newline at 115,200 baud;
  2. certify it: escert proves how early every edge is queued, for every input;
  3. run it on the instruction-set simulator, with a UART receiver on its pin;
  4. let sigrok's UART decoder, written by other people, read the same pin;
  5. ask for the impossible, a bit 5 ticks long, and watch it be refused, not made late.

`make quickstart` then runs the same image on the chip's RTL in Icarus Verilog through
cocotb: loaded over the SPI host port, the same bytes on the pin, and the chip's 32-bit
execution digest equal to the simulator's.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "sw"))

from escasm import assemble  # noqa: E402
from escert import certify  # noqa: E402
from escmodels import Board, UartRx  # noqa: E402
from esciss import Machine  # noqa: E402
from esciss.decode import ADMIT_SUB  # noqa: E402

FW = REPO / "firmware"
CLK_HZ, BAUD = 25_000_000, 115_200
SUBCODE = {v: k for k, v in ADMIT_SUB.items()}


def step(n: int, title: str) -> None:
    print(f"\n{n}. {title}")


def show(text: str) -> None:
    for line in text.splitlines():
        print(f"   {line}")


class Levels:
    """Wraps a board and keeps the pin levels it returns, one entry per tick."""

    def __init__(self, inner):
        self.inner, self.levels = inner, []

    def __call__(self, t, *a):
        v = self.inner(t, *a)
        if t >= 0:
            self.levels.append(v)
        return v


def vcd(levels: list[int], tick_ns: float) -> str:
    """Pin 0's levels as a value change dump, the format sigrok reads."""
    out = ["$timescale 1 ns $end", "$scope module chip $end", "$var wire 1 ! tx $end",
           "$upscope $end", "$enddefinitions $end"]
    prev = None
    for t, v in enumerate(levels):
        if v & 1 != prev:
            out += [f"#{round(t * tick_ns)}", f"{v & 1}!"]
            prev = v & 1
    out.append(f"#{round((len(levels) + 2000) * tick_ns)}")
    return "\n".join(out) + "\n"


def main() -> int:
    src = (FW / "uart_tx.asm").read_text()

    step(1, "Assemble firmware/uart_tx.asm")
    image = assemble(src, base_dir=FW, name="uart_tx.asm")
    used = image.used_words
    show(f"{used} instruction words for context 0. Every bit edge in the source is an EMIT:\n"
         "a pin change at an absolute tick, held in the context's event queue until then.")

    step(2, "Certify it with escert")
    (cert,) = certify(image)
    show(str(cert).split("\n")[0])
    show(f"For every input, each edge is queued at least {int(cert.min_lead)} ticks before its\n"
         "deadline, so the chip can never refuse it. (docs/04-VERIFICATION.md, certificates)")

    step(3, "Run it on the instruction-set simulator, a UART receiver on pin 0")
    rx = UartRx(0, CLK_HZ / BAUD)
    board = Levels(Board(rx))
    m = Machine(image, board=board, record=False)
    m.run(4 * 10 * 218 + 2000, stop_when_idle=False)     # four bytes, then idle line
    text = bytes(rx.bytes).decode("ascii", "replace")
    show(f"received {[hex(b) for b in rx.bytes]} = {text!r}")
    show(f"lead measured while it ran: {m.ctx[0].min_lead} ticks (the certificate said at least "
         f"{int(cert.min_lead)})")
    show(f"execution digest 0x{m.dg:08x}: the RTL step must read the same value from the chip")
    ok = rx.bytes == [0x55, 0x4B, 0x21, 0x0A] and m.ctx[0].min_lead >= cert.min_lead

    step(4, "An outside decoder reads the same pin")
    sigrok = shutil.which("sigrok-cli")
    if sigrok is None:
        show("skipped: sigrok-cli is not installed (apt install sigrok-cli). `make judges` runs\n"
             "decoders by other people over 73 of the 80 programs, each with a control that fails.")
    else:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "uart_tx.vcd"
            p.write_text(vcd(board.levels, 1e9 / CLK_HZ), newline="\n")
            r = subprocess.run([sigrok, "-I", "vcd", "-i", str(p), "-P",
                                f"uart:rx=tx:baudrate={BAUD}", "-A", "uart=rx-data"],
                               capture_output=True, text=True, timeout=120)
        got = [w.split(":")[-1].strip() for w in r.stdout.split("\n") if w.strip()]
        show(f"sigrok's uart decoder: {' '.join(got)}")
        ok = ok and [int(g, 16) for g in got if g] == rx.bytes

    step(5, "Ask for the impossible: a bit 5 ticks long")
    bad = assemble(f".equ uart_tx.BAUD, {CLK_HZ // 5}\n.include \"uart_tx.asm\"\n",
                   base_dir=FW, name="uart_tx.asm")
    (c5,) = certify(bad, witness_trials=4)
    f = c5.findings[0]
    show(f"escert: {c5.status} at pc {f.pc}: {f.kind}, {f.note}, with inputs that reproduce it")
    m5 = Machine(bad, record=False)
    m5.run(5_000)
    ff = m5.ff
    show(f"simulator: context {ff.ctx} stopped at tick {ff.tick}, pc {ff.pc}, with an ADMIT "
         f"fault ({SUBCODE.get(ff.subcode, ff.subcode)}).\n"
         "The program needs more than 5 ticks between two edges, so the start bit's deadline\n"
         "has passed when it is queued. The chip does not send a late edge: it refuses the\n"
         "event, stops the context, releases its pins and says why. That a late edge is\n"
         "impossible is proved for every program (docs/00-WRITEUP.md, section 2).")
    ok = ok and c5.status == "REFUTED" and ff.valid

    print("\nAll steps agree." if ok else "\nSomething disagreed: see above.")
    print("Next: `make quickstart` runs this image on the chip's RTL; docs/14-FIRMWARE-GUIDE.md\n"
          "shows how to write a program of your own.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
