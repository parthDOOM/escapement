"""The westminster bench bundle: make_bundle.py's own bundle(), with its prediction replaced by
the halt branch alone, run on the ISS with record=False. make_bundle's predict() keeps a digest
history for its steady-pattern search and the ISS keeps every commit and pin change; over the
chime's 391 M ticks both run out of memory. westminster only halts, so its halt prediction is
all it needs: the same dict predict() returns when every context has stopped (the final
digest a barrel round after the halt, and the snapshot).

    python make_west_bundle.py <out folder>      # about 2 h 15 min on one laptop core
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "sw" / "rp2350"))
import make_bundle as mb  # noqa: E402
from esciss import Machine  # noqa: E402

LIMIT = 400_000_000


def predict_halt(image):
    m = Machine(image, record=False)
    while m.now < LIMIT:
        m.run(mb.CHUNK, stop_when_idle=False)
        if mb._halted(m):
            break
    else:
        raise RuntimeError("no halt within %d ticks" % LIMIT)
    # predict() checks for the halt every tick; a chunk can overrun it, which changes nothing
    # once every context has stopped (a halt keeps its pins and folds nothing more)
    m.run(mb.SETTLE, stop_when_idle=False)
    return dict(kind="halt", ticks=m.now, digest=m.dg, **mb.snapshot(m))


mb.predict = predict_halt
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
t = time.time()
fw = mb.ROOT / "firmware" / "westminster.asm"
b = mb.bundle(fw)
mb.write_bundle(out, fw.stem, b)
e = b["expect"]
print(e["kind"], {k: e[k] for k in ("ticks", "digest") if k in e}, "in %.0f s" % (time.time() - t), flush=True)
