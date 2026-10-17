"""CLI: python -m escasm program.asm [-o image.json] [-l]"""

import argparse
import sys
from pathlib import Path

from .asm import AsmError, assemble_file


def main() -> int:
    ap = argparse.ArgumentParser(prog="escasm", description="Escapement assembler")
    ap.add_argument("source")
    ap.add_argument("-o", "--output", help="image JSON (default: <source>.json)")
    ap.add_argument("-l", "--listing", action="store_true", help="print listing")
    args = ap.parse_args()
    try:
        image = assemble_file(args.source)
    except AsmError as e:
        print(f"{args.source}: {e}", file=sys.stderr)
        return 1
    out = Path(args.output or Path(args.source).with_suffix(".json"))
    out.write_text(image.to_json())
    if args.listing:
        print("\n".join(image.listing))
    print(f"{args.source}: {image.used_words} words -> {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
