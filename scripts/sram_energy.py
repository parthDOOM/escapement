"""Exact SRAM macro energy from a gate-level VCD (docs/15-POWER.md).

    python3 scripts/sram_energy.py power.vcd [CLOCK_NS]

OpenSTA prices the macro's state-dependent clock power (`when : "A_MEN & !A_WEN & A_REN"`)
from per-pin static probabilities multiplied as if independent. Since D-29b, A_MEN and
A_REN are driven by the same read-enable, so that product is wrong by a factor of the read
duty. Here we count instead: at every rising A_CLK of each macro instance, the state of
(A_MEN, A_WEN, A_REN) just before the edge selects the energy from the macro's liberty
(RM_IHPSG13_1P_256x32_c2_bm_bist_typ_1p20V_25C.lib, pin A_CLK, rise_power, pJ). Pin
toggle energies (<= 0.12 pJ) are ignored.
"""

from __future__ import annotations

import sys
from collections import Counter

# (MEN, WEN, REN) -> pJ per rising A_CLK, typical corner
EDGE_PJ = {
    (0, 0, 0): 0.0, (0, 1, 0): 0.5036, (1, 1, 0): 33.4496, (1, 1, 1): 37.7280,
    (1, 0, 1): 26.0060, (0, 0, 1): 0.3024, (0, 1, 1): 1.1356, (1, 0, 0): 0.0,
}
PINS = ("A_CLK", "A_MEN", "A_WEN", "A_REN")


def parse(path: str):
    scopes: list[str] = []
    ids: dict[str, tuple[str, str]] = {}     # vcd id -> (instance, pin)
    timescale_ps = 1.0
    with open(path) as f:
        it = iter(f)
        for line in it:
            tok = line.split()
            if not tok:
                continue
            if tok[0] == "$timescale":
                spec = " ".join(tok[1:]) if len(tok) > 1 else next(it).strip()
                spec = spec.replace("$end", "").strip()
                num = "".join(c for c in spec if c.isdigit()) or "1"
                unit = spec[len(num):].strip()
                timescale_ps = int(num) * {"s": 1e12, "ms": 1e9, "us": 1e6, "ns": 1e3,
                                           "ps": 1.0, "fs": 1e-3}[unit]
            elif tok[0] == "$scope":
                scopes.append(tok[2])
            elif tok[0] == "$upscope":
                scopes.pop()
            elif tok[0] == "$var":
                name = tok[4]
                inst = scopes[-1] if scopes else ""
                if name in PINS and ("sram" in inst):
                    ids[tok[3]] = (inst, name)
            elif tok[0] == "$enddefinitions":
                break
        cur: dict[tuple[str, str], int] = {}
        edges: dict[str, Counter] = {}
        t0 = t_last = None
        pending: dict[str, int] = {}

        def flush():
            before = dict(cur)
            for vid, v in pending.items():
                key = ids[vid]
                old = before.get(key, 0)
                cur[key] = v
                if key[1] == "A_CLK" and old == 0 and v == 1:
                    inst = key[0]
                    st = (before.get((inst, "A_MEN"), 0), before.get((inst, "A_WEN"), 0),
                          before.get((inst, "A_REN"), 0))
                    edges.setdefault(inst, Counter())[st] += 1
            pending.clear()

        for line in it:
            if not line:
                continue
            c = line[0]
            if c == "#":
                flush()
                t = int(line[1:])
                if t0 is None:
                    t0 = t
                t_last = t
            elif c in "01xzXZ":
                vid = line[1:].strip()
                if vid in ids:
                    pending[vid] = 1 if c == "1" else 0
            elif c in "bB":
                val, vid = line[1:].split()
                if vid in ids:
                    pending[vid] = 1 if val.strip("0") == "1" else 0
        flush()
    return edges, (t_last - t0) * timescale_ps if t0 is not None else 0.0


def main() -> None:
    edges, span_ps = parse(sys.argv[1])
    clock_ns = float(sys.argv[2]) if len(sys.argv) > 2 else 40.0
    total_uw = 0.0
    for inst, cnt in sorted(edges.items()):
        e_pj = sum(EDGE_PJ[s] * n for s, n in cnt.items())
        uw = e_pj / span_ps * 1e6 if span_ps else 0.0       # pJ/ps = W; x1e6 -> uW
        total_uw += uw
        n = sum(cnt.values())
        rd = cnt[(1, 0, 1)]
        wr = cnt[(1, 1, 0)] + cnt[(1, 1, 1)]
        print(f"{inst:>16s}  edges {n:7d}  reads {rd:7d} ({100*rd/max(n,1):5.1f}%)  "
              f"writes {wr:6d}  {uw:8.1f} uW")
    cycles = span_ps / (clock_ns * 1e3)
    print(f"{'all macros':>16s}  window {cycles:.0f} cycles  {total_uw:8.1f} uW")


if __name__ == "__main__":
    main()
