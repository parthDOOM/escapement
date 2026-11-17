"""Bundles for the Ethernet receive bench (README.md, Ethernet), next to make_bundle.py's:

    eth_rx_bench.json      eth_rx_bench.asm: eth_rx with both-edge sampling, link pulses
    eth_rx_bench_one.json  the same with one sample a tick (BOTH = 0)
    eth_edges.json         eth_edges.asm: the receive line check (the capture unit on RXP)
    eth_trx.json           eth_trx.asm: receive and transmit on one line unit (the ping bench)

    python fpga/tangprimer25k/eth_bundles.py OUT_DIR      (build.sh bundles runs it)
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "sw"))
sys.path.insert(0, str(HERE.parents[1] / "sw" / "rp2350"))
import make_bundle  # noqa: E402

BUNDLES = {"eth_rx_bench": ("eth_rx_bench.asm", {"BOTH": 1}),
           "eth_rx_bench_one": ("eth_rx_bench.asm", {"BOTH": 0}),
           "eth_edges": ("eth_edges.asm", None),
           "eth_trx": ("eth_trx.asm", None)}


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for stem, (asm, overrides) in BUNDLES.items():
        b = make_bundle.bundle(HERE / asm, stem + ".json", overrides)
        (out / (stem + ".json")).write_text(json.dumps(b))
        print(f"{stem + '.json':24s} {b['expect']['kind']:7s} cert {b['cert']}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
