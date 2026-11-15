"""Exact SRAM macro energy from a gate-level VCD (docs/15-POWER.md).

    python3 scripts/sram_energy.py power.vcd [CLOCK_NS]

OpenSTA prices the macro's state-dependent clock power (`when : "A_MEN & !A_WEN & A_REN"`)
from per-pin static probabilities multiplied as if independent. Since D-29b, A_MEN and
A_REN are driven by the same read-enable, so that product is wrong by a factor of the read
duty. Here we count instead: at every rising A_CLK of each macro instance, the state of
(A_MEN, A_WEN, A_REN) just before the edge selects the energy from that macro's liberty
(RM_IHPSG13_1P_<size>_c2_bm_bist_typ_1p20V_25C.lib, pin A_CLK, rise_power, pJ). The size
comes from the instance path (escapement_pstore.v: g_whole is the 1024x32 store, g_half a
512x32; every other instance, the tape among them, is a 256x32). Pin toggle energies
(<= 0.12 pJ) are ignored.
"""

from __future__ import annotations

import sys
from collections import Counter

# (MEN, WEN, REN) -> pJ per rising A_CLK, typical corner, per macro size
EDGE_PJ = {
    "256x32": {
        (0, 0, 0): 0.0, (0, 1, 0): 0.5036, (1, 1, 0): 33.4496, (1, 1, 1): 37.7280,
        (1, 0, 1): 26.0060, (0, 0, 1): 0.3024, (0, 1, 1): 1.1356, (1, 0, 0): 0.0,
    },
    "512x32": {
        (0, 0, 0): 0.0, (0, 1, 0): 0.5389, (1, 1, 0): 42.3165, (1, 1, 1): 47.6101,
        (1, 0, 1): 36.0099, (0, 0, 1): 0.3230, (0, 1, 1): 1.2182, (1, 0, 0): 0.0,
    },
    "1024x32": {
        (0, 0, 0): 0.0, (0, 1, 0): 0.5298, (1, 1, 0): 58.9183, (1, 1, 1): 61.8765,
        (1, 0, 1): 48.2666, (0, 0, 1): 0.3622, (0, 1, 1): 1.2134, (1, 0, 0): 0.0,
    },
}


def macro_size(inst: str) -> str:
    """The macro behind an instance path (see the module docstring)."""
    if ".g_whole." in f".{inst}.":
        return "1024x32"
    if ".g_half." in f".{inst}.":
        return "512x32"
    return "256x32"
PINS = ("A_CLK", "A_MEN", "A_WEN", "A_REN")


def parse(path: str):
    scopes: list[str] = []
    # vcd id -> every (instance, pin) it names: the store's and the tape's A_CLK are one net
    # in a netlist, so the VCD gives them one id
    ids: dict[str, list[tuple[str, str]]] = {}
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
                # the whole path: a gate-level netlist names each macro by its flattened path
                # (\u_sram.g_whole.u0.sram, \u_tape.sram), an RTL one by a scope "sram"
                inst = ".".join(scopes)
                if name in PINS and scopes and scopes[-1].strip().endswith("sram"):
                    ids.setdefault(tok[3], []).append((inst, name))
            elif tok[0] == "$enddefinitions":
                break
        cur: dict[tuple[str, str], int] = {}
        edges: dict[str, Counter] = {}
        t0 = t_last = None
        pending: dict[str, int] = {}

        def flush():
            before = dict(cur)
            for vid, v in pending.items():
                for key in ids[vid]:
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
        e_pj = sum(EDGE_PJ[macro_size(inst)][s] * n for s, n in cnt.items())
        uw = e_pj / span_ps * 1e6 if span_ps else 0.0       # pJ/ps = W; x1e6 -> uW
        total_uw += uw
        n = sum(cnt.values())
        rd = cnt[(1, 0, 1)]
        wr = cnt[(1, 1, 0)] + cnt[(1, 1, 1)]
        print(f"{inst.split('user_project.')[-1]:>24s} {macro_size(inst):>7s}  edges {n:7d}  "
              f"reads {rd:7d} ({100*rd/max(n,1):5.1f}%)  writes {wr:6d}  {uw:8.1f} uW")
    cycles = span_ps / (clock_ns * 1e3)
    print(f"{'all macros':>32s}  window {cycles:.0f} cycles  {total_uw:8.1f} uW")


if __name__ == "__main__":
    main()
