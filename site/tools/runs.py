"""Every stock firmware, run once at build time, so the page has its waveforms before
Python has loaded in the browser.

    python site/tools/runs.py

Writes site/public/data/runs/index.json (the scenarios) and one <id>.json per scenario,
in exactly the form the in-browser playground returns, plus the firmware source; and
site/public/data/learn/ the same for "record it, speak it": each sample recording learned,
its new message built, certified and sent on the simulator.
"""
import json
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SITE.parent / "sw"), str(SITE / "py")]

import escplay  # noqa: E402
import learnplay  # noqa: E402

OUT = SITE / "public" / "data" / "runs"
LEARN = SITE / "public" / "data" / "learn"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    index = json.loads(escplay.scenarios())
    for s in index:
        src = escplay.source_of(s["id"])
        r = json.loads(escplay.run(src, s["id"], "{}"))
        if not r["ok"] or r["fault"] or r["errors"]:
            raise SystemExit(f"{s['id']}: {r.get('error') or r['fault'] or r['errors']}")
        r["source"] = src
        (OUT / f"{s['id']}.json").write_text(json.dumps(r, separators=(",", ":")), newline="\n")
        print(f"{s['id']:8s} {r['result']}")
    (OUT / "index.json").write_text(json.dumps(index, separators=(",", ":")), newline="\n")
    LEARN.mkdir(parents=True, exist_ok=True)
    for s in learnplay.SAMPLES:
        r = json.loads(learnplay.learn(s["id"], s["pin"], s["send"]))
        if not r["ok"] or not r["sent_ok"] or r["cert"]["status"] != "CHECKED":
            raise SystemExit(f"learn {s['id']}: {r.get('error') or r}")
        (LEARN / f"{s['id']}.json").write_text(json.dumps(r, separators=(",", ":")), newline="\n")
        print(f"learn {s['id']:7s} {r['coding']}, sent {' '.join(r['words'])}")
    (LEARN / "index.json").write_text(learnplay.samples(), newline="\n")


if __name__ == "__main__":
    main()
