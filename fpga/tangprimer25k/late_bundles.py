"""Bundles for the refusal bench (latebench.py, README.md "The chip refuses"), in their own
directory so the bring-up's stage 5, which runs every bundle it finds and expects each to
pass its certificate, never sees them:

    uart_tx_late.json   firmware/uart_tx.asm with a bit 5 ticks long (BAUD = 5 MHz at 25 MHz):
                        its second edge is queued after its deadline (ADMIT, PAST)
    refuse_owner.json   refuse_owner.asm: a DRIVE on a pin the context does not own (ADMIT,
                        OWNER)

Each is make_bundle.py's bundle (the image and what the model predicts the chip will show,
here a fault record, a fault tick and a digest) plus `escert`: the certificate checker's
status and findings for the same image, with witnesses, so the bench can show the refusal
was foretold before the program ever ran.

    python fpga/tangprimer25k/late_bundles.py OUT_DIR      (build.sh bundles runs it)
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "sw"))
sys.path.insert(0, str(ROOT / "sw" / "rp2350"))
import make_bundle  # noqa: E402
from escasm import assemble, assemble_file  # noqa: E402
from escert import certify  # noqa: E402

CLK_HZ = 25_000_000
BUNDLES = {"uart_tx_late": (ROOT / "firmware" / "uart_tx.asm", {"BAUD": CLK_HZ // 5}),
           "refuse_owner": (HERE / "refuse_owner.asm", None)}


def image_of(fw: Path, overrides):
    if not overrides:
        return assemble_file(fw)
    src = "".join(f".equ {fw.stem}.{k}, {v}\n" for k, v in overrides.items())
    return assemble(src + f'.include "{fw.name}"\n', base_dir=fw.parent, name=fw.name)


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for stem, (fw, overrides) in BUNDLES.items():
        b = make_bundle.bundle(fw, stem + ".json", overrides)
        e = b["expect"]
        if e["kind"] != "halt" or not e["first_fault"] >> 31:
            raise SystemExit(f"{stem}: the model does not fault ({e['kind']}): not a refusal bundle")
        b["escert"] = [{"ctx": c.ctx, "status": c.status,
                        "findings": [[f.kind, f.pc, f.note] for f in c.findings]}
                       for c in certify(image_of(fw, overrides), witness_trials=4)]
        (out / (stem + ".json")).write_text(json.dumps(b))
        ff = e["first_fault"]
        print(f"{stem + '.json':20s} fault 0x{ff:08x} at tick {e['fault_tick']}, digest "
              f"0x{e['digest']:08x}; escert {[c['status'] for c in b['escert']]}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
