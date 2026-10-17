"""Build demo bundles for the Tiny Tapeout demo board (run on a PC).

    python sw/rp2350/make_bundle.py OUT_DIR

Each firmware is assembled and run on the ISS until every context halts; the bundle
(JSON) carries the image, the configuration bytes, the tape contents and the ISS's
predicted execution digest. On the board, escapement_demo.py loads the image, runs it,
reads the chip's digest (READ_DBG sel 16) and compares: one 32-bit word says whether the
silicon executed exactly the commits, register values and pin changes the model did.
Digest prediction needs input-independent firmware (or a board whose inputs are known);
the bundle records which.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sw"))

from escasm import assemble_file  # noqa: E402
from esciss import Machine  # noqa: E402

# firmware -> does the digest depend only on the image? (no inputs read before HALT)
DEMOS = {"uart_tx.asm": True}


def bundle(fw: str, predict: bool) -> dict:
    image = assemble_file(ROOT / "firmware" / fw)
    out = {
        "name": fw,
        "words": [w for w in image.words[: max(i + 1 for i, w in enumerate(image.words) if w)]],
        "tape": {str(a): image.tape[a] for a in image.tape_used},
        "cfg": [image.ctx_enable_mask, image.entry_pcs_packed, image.pin_owned_mask,
                image.pin_owner_packed, image.pin_od_mask],
    }
    if predict:
        m = Machine(image)
        m.run(2_000_000)                       # until every context has halted
        out["expect_digest"] = m.dg
        out["expect_ticks"] = m.now
    return out


def main() -> None:
    dst = Path(sys.argv[1] if len(sys.argv) > 1 else "bundles")
    dst.mkdir(parents=True, exist_ok=True)
    for fw, predict in DEMOS.items():
        b = bundle(fw, predict)
        (dst / (Path(fw).stem + ".json")).write_text(json.dumps(b))
        print(fw, "digest" if predict else "", hex(b.get("expect_digest", 0)))


if __name__ == "__main__":
    main()
