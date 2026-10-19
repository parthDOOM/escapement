"""The SUMP bridge on the simulated demo board behind a pseudo-terminal, so the real
libsigrok `ols` driver (sigrok-cli, PulseView) can capture from it (Linux/WSL only):

    cc -shared -fPIC -o /tmp/pty_serial_shim.so sw/tests/pty_serial_shim.c -ldl
    python3 sw/tests/sump_pty.py &        # prints the port, e.g. /dev/pts/5
    LD_PRELOAD=/tmp/pty_serial_shim.so sigrok-cli -d ols:conn=/dev/pts/5 \\
        --config samplerate=1m --samples 2048 -t 1=1 -O vcd -o capture.vcd

(libserialport cannot open a pty without the shim; a real board's /dev/ttyACM0 needs none.)

A square wave on uio[1] (HALF ticks low, HALF high) is the signal. The bridge serves
captures one after another until it is stopped (Ctrl-C). test_sump.py::test_sigrok_cli_captures_through_the_bridge
runs this when sigrok-cli is installed.
"""

from __future__ import annotations

import json
import os
import pty
import sys
import termios
import tty
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sw" / "rp2350"))
sys.path.insert(0, str(ROOT / "sw"))
sys.path.insert(0, str(ROOT / "sw" / "tests"))

import bringup  # noqa: E402
import make_bundle  # noqa: E402
import sump  # noqa: E402
from demoboard_mock import MockBoard, SimChip  # noqa: E402

HALF = 6000


def main():
    fw = ROOT / "firmware" / "logic_analyzer.asm"
    name, (_, overrides) = next(iter(make_bundle.VARIANTS.items()))
    bundle = json.loads(json.dumps(make_bundle.bundle(fw, name, overrides)))
    chip = SimChip()
    chip.sources.append(lambda t: (0x02, 0x02 * ((t // HALF) & 1)))
    board = MockBoard(chip)
    b = bringup.Bringup(board.io(bringup), out=lambda *a: None)
    b.stage0()
    b.connect()
    master, slave = pty.openpty()
    tty.setraw(slave)
    attrs = termios.tcgetattr(slave)
    attrs[3] &= ~termios.ECHO
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    print(os.ttyname(slave), flush=True)
    # The client opens the port by name. Keeping this end open makes a read wait for
    # bytes instead of failing until a client arrives: libsigrok's scan sends the ID
    # command right after opening and allows the reply about 10 ms.

    def rd(n):
        out = b""
        while len(out) < n:
            out += os.read(master, n - len(out))
        return out

    def wr(data):
        os.write(master, data)

    br = sump.Bridge(b, bundle, rd, wr)
    while br.step():
        while br.log:
            print(br.log.pop(0), file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
