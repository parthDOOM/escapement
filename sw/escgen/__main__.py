"""python -m escgen [TABLE.yaml ...] [--out DIR] [--check-only]

Generates firmware/<name>.asm from each timing table (default: every protocols/*.yaml)
and refuses any program escert does not CHECK. --check-only compares with the files
already in DIR instead of writing them (the test suite's drift check).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import ROOT, TABLES, TableError, build, load


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="escgen")
    ap.add_argument("tables", nargs="*")
    ap.add_argument("--out", default=str(ROOT / "firmware"))
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args(argv)
    paths = [Path(p) for p in args.tables] or sorted(TABLES.glob("*.yaml"))
    status = 0
    for p in paths:
        try:
            t = load(p)
            text, image = build(t)
        except TableError as e:
            print(f"{p.name}: REFUSED: {e}")
            status = 1
            continue
        out = Path(args.out) / f"{t['name']}.asm"
        n = sum(1 for w in image.words if w)
        if args.check_only:
            same = out.exists() and out.read_text() == text
            print(f"{p.name}: {'up to date' if same else 'DIFFERS from ' + str(out)}")
            status |= 0 if same else 1
        else:
            out.write_text(text, newline="\n")
            print(f"{p.name}: {out} ({n} words, CHECKED)")
    return status


if __name__ == "__main__":
    sys.exit(main())
