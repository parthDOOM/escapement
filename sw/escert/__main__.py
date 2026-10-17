"""python -m escert FILE.asm [--witness N]: timing certificate per context.

Exit status 0 if every enabled context is CHECKED, 1 otherwise. A CHECKED line states a
lower bound on the chip's `min_lead` telemetry; reading that telemetry back after a run
(READ_DBG sel 9) falsifies the certificate if it is ever lower.
"""

from __future__ import annotations

import argparse
import sys

from escasm import assemble_file

from . import certify


def main() -> int:
    ap = argparse.ArgumentParser(prog="escert")
    ap.add_argument("source")
    ap.add_argument("--witness", type=int, default=30,
                    help="ISS trials searching for a concrete fault when not CHECKED")
    args = ap.parse_args()
    certs = certify(assemble_file(args.source), witness_trials=args.witness)
    for c in certs:
        print(c)
    return 0 if all(c.status == "CHECKED" for c in certs) else 1


if __name__ == "__main__":
    sys.exit(main())
