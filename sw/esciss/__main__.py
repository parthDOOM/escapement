"""CLI: python -m esciss image.json --ticks N [--trace out.jsonl]

image may be an escasm image JSON or (convenience) an .asm source file.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from escasm import Image, assemble_file

from .machine import Machine


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="esciss", description="Escapement ISS")
    ap.add_argument("image", help="image JSON from escasm (or an .asm file)")
    ap.add_argument("--ticks", type=int, required=True, help="maximum run cycles")
    ap.add_argument("--trace", help="write JSON-lines trace here")
    ap.add_argument("--no-stop-when-idle", action="store_true",
                    help="run all ticks even when every context is idle")
    args = ap.parse_args(argv)

    path = Path(args.image)
    image = assemble_file(path) if path.suffix == ".asm" else Image.from_json(path.read_text())
    m = Machine(image)
    t0 = time.perf_counter()
    m.run(args.ticks, stop_when_idle=not args.no_stop_when_idle)
    dt = time.perf_counter() - t0
    if args.trace:
        m.write_trace(args.trace)

    fin = m.final_state()
    last_t = m.pins[-1]["t"] if m.pins else None
    counts = {o: sum(1 for c in m.commits if c["outcome"] == o) for o in "CWF"}
    print(f"run cycles: {len(m.pins)} (last t = {last_t}), {dt:.2f} s")
    print(f"commits: {len(m.commits)} (C {counts['C']}, W {counts['W']}, F {counts['F']})")
    for k, c in enumerate(fin["contexts"]):
        ch = c["checker"]
        print(f"ctx{k}: {c['state']:8s} pc={c['pc']:3d} r0={c['r0']:#010x} r1={c['r1']:#010x} "
              f"r2={c['r2']:#010x} r3={c['r3']:#010x} tmr={c['tmr']} min_lead={c['min_lead']} "
              f"max_occ={c['max_occupancy']} wait_full={c['wait_full']} "
              f"pass={ch['pass_count']} fail={ch['fail_count']} irq={c['irq_pending']}:{c['irq_code']}")
    ff = fin["first_fault"]
    if ff["valid"]:
        print(f"first fault: cause={ff['cause']} subcode={ff['subcode']} ctx={ff['ctx']} "
              f"pc={ff['pc']} tick={ff['tick']}")
    else:
        print("first fault: none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
