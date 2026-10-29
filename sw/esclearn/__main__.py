"""python -m esclearn CAPTURE [--pin N | --signal NAME] [--unit BITS] [--msb]
                    [-o TABLE.yaml] [--source S] [--send W,W,...] [--check]

Learns the protocol on one pin of a recording (a chip capture .json, a VCD or a sigrok
.sr) and prints what it found: the coding, the carrier, the symbol timings and the words
of every frame. The first bit sent is bit 0, and a frame of up to 32 bits is one word,
a longer one words of its largest divisor from 32 down to 8 bits; --unit and --msb
number the same bits another way (an LED strip: --unit 24 --msb gives each LED's
colour). -o writes the timing table (escgen's format; python -m escgen TABLE turns it
into firmware with a timing certificate). --send gives the words of a new frame (hex;
serial: bytes): check bytes the recording shows (a complement, an XOR, a sum) are
recomputed, and the table sends that frame. --check builds it, runs it on the ISS for
the recorded frames and compares every mark and space with the recording; --clk-khz
runs that check at a lower clock, rescaled (long signals such as IR take a while on the
ISS at 25 MHz). Exit status 1 if nothing fits or the check fails.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

from esctape.export import _parse_rate, read_trace

from . import CLOCK_HZ, LearnError, check, learn


def resolution(path: Path) -> float:
    """Ticks per sample: from a sigrok session's sample rate; 1 for other recordings."""
    if path.suffix == ".sr":
        with zipfile.ZipFile(path) as z:
            for line in z.read("metadata").decode().splitlines():
                k, _, v = line.partition("=")
                if k.strip() == "samplerate":
                    return max(1.0, CLOCK_HZ / _parse_rate(v))
    return 1.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="esclearn")
    ap.add_argument("capture")
    ap.add_argument("--pin", type=int, default=None, help="channel (bit) to learn, default 0")
    ap.add_argument("--signal", help="channel by name (a VCD signal or a sigrok probe)")
    ap.add_argument("--unit", type=int, help="bits per word (must divide a frame's bits)")
    ap.add_argument("--msb", action="store_true", help="the first bit sent is a word's top bit")
    ap.add_argument("-o", "--out", help="write the timing table here")
    ap.add_argument("--name", help="table name (default learned_<capture>)")
    ap.add_argument("--source", default=None, choices=["mailbox", "constant", "tape"],
                    help="where the generated firmware takes its words (default: mailbox "
                         "for one word per frame, else constant: the first frame)")
    ap.add_argument("--send", help="a new frame's words, comma-separated (hex)")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--clk-khz", type=int, default=25000)
    args = ap.parse_args(argv)
    path = Path(args.capture)
    signals = [args.signal] if args.signal and path.suffix == ".vcd" else None
    records, names = read_trace(path, signals=signals)
    pin = args.pin or 0
    if args.signal and not signals:
        if args.signal not in names:
            print(f"no channel {args.signal!r}; channels: {', '.join(names)}")
            return 1
        pin = names.index(args.signal)
    name = args.name or "learned_" + "".join(c if c.isalnum() else "_" for c in path.stem)
    try:
        model = learn(records, pin=pin, name=name, source=path.name, quant=resolution(path))
    except LearnError as e:
        print(f"{path.name}: no model: {e}")
        print("raw replay re-sends a recording unchanged: esctape.build_replay")
        return 1
    if args.unit or args.msb:
        try:
            model.regroup(args.unit, args.msb)
        except LearnError as e:
            print(f"--unit/--msb: {e}")
            return 1
    print("\n".join(model.summary()))
    source = args.source or model.default_source
    words = None
    if args.send:
        given = [int(w, 16) for w in args.send.split(",")]
        words = model.complete(given)
        filled = "" if words == given else " (check bytes recomputed)"
        print("send " + " ".join(f"0x{w:x}" for w in words) + filled)
        source = "constant"
    status = 0
    if args.out:
        try:
            Path(args.out).write_text(model.table_yaml(source, words), newline="\n")
        except LearnError as e:
            print(f"no table: {e}")
            return 1
        print(f"table: {args.out} (source {source})")
    if args.check:
        c = check(model, records, pin=pin, clk_khz=args.clk_khz)
        print(f"check: {c.frames} frame(s) regenerated on the ISS, worst duration error "
              f"{100 * c.worst_rel:.2f}% ({c.worst_ticks} ticks), "
              f"decoded back: {'yes' if c.decoded else 'NO'}")
        for f in c.failures[:10]:
            print("  " + f)
        status |= 0 if c.ok else 1
    return status


if __name__ == "__main__":
    sys.exit(main())
