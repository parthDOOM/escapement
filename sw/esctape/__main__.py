"""CLI for captures and replays (docs/13-TAPE-AND-DIGEST.md).

    python -m esctape export CAPTURE.json -o OUT.vcd|OUT.sr [--div N] [--pins MASK]
    python -m esctape replay TRACE.vcd|TRACE.sr|CAPTURE.json --pins 0,1 [--signals a,b]
                             [--ctx K] [--lead TICKS] [--od 1] -o IMAGE.json

`export` converts a capture file (written by the host tools) for PulseView, GTKWave or
sigrok-cli. `replay` turns any trace into an image that re-drives the chosen pins at the
original tick spacing (firmware/replay.asm), or refuses one the tape cannot hold.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import build_replay, decode_records
from .export import CLOCK_HZ, load_capture, read_trace, write_sr, write_vcd


def _mask(s: str) -> int:
    return sum(1 << int(p) for p in s.split(",") if p != "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="esctape", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    ex = sub.add_parser("export", help="capture file -> .vcd or .sr")
    ex.add_argument("capture")
    ex.add_argument("-o", "--out", required=True)
    ex.add_argument("--div", type=int, default=1, help=".sr: one sample every DIV ticks")
    ex.add_argument("--pins", default="0,1,2,3,4,5,6,7", help=".vcd: pins to include")

    rp = sub.add_parser("replay", help="trace -> replay image")
    rp.add_argument("trace")
    rp.add_argument("-o", "--out", required=True)
    rp.add_argument("--pins", required=True, help="pins to re-drive, e.g. 0,1")
    rp.add_argument("--signals", help=".vcd: signal names for bits 0, 1, ...")
    rp.add_argument("--ctx", type=int, default=0)
    rp.add_argument("--lead", type=int, default=200)
    rp.add_argument("--od", default="", help="open-drain pins, e.g. 0,1")
    rp.add_argument("--clock", type=float, default=CLOCK_HZ, help="chip clock in Hz")
    args = ap.parse_args(argv)

    if args.cmd == "export":
        d = load_capture(args.capture)
        recs = decode_records(d["words"], d.get("first_epoch", 0))
        if Path(args.out).suffix == ".sr":
            n = write_sr(recs, args.out, d["clock_hz"], args.div, d["names"])
            print(f"{args.out}: {len(recs)} records, {n} samples at {d['clock_hz'] / args.div:g} Hz")
        else:
            write_vcd(recs, args.out, d["clock_hz"], d["names"], _mask(args.pins))
            print(f"{args.out}: {len(recs)} records")
        return 0

    signals = args.signals.split(",") if args.signals else None
    recs, names = read_trace(args.trace, args.clock, signals)
    try:
        image = build_replay(recs, _mask(args.pins), ctx=args.ctx, lead=args.lead,
                             od=_mask(args.od))
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    Path(args.out).write_text(image.to_json())
    print(f"{args.out}: replays {len(recs)} records on pins {args.pins} "
          f"({', '.join(names[int(p)] for p in args.pins.split(','))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
