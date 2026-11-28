"""Two facts the instruction-set proof settled (docs/04-VERIFICATION.md, "What each instruction
does"), pinned here on the ISS and on the tools that share isa/isa.yaml.

Every expected value is derived by hand from docs/11-SEMANTICS.md (3.2); nothing here was
produced by running the ISS. Timing (§3): context 0 alone, with no waits, commits its i-th
instruction at tick 3 + 4i.
"""

import importlib.util
from pathlib import Path

from escasm import assemble
from esciss import Machine

ROOT = Path(__file__).resolve().parents[2]


def test_getm_sets_z_and_mon_leaves_it():
    """§7.8: GETM writes R[rd] and sets Z from it, like every read of a unit's state (D-78);
    MON writes no register and lists no flag, so Z is unchanged (§7: flags are unchanged
    unless listed)."""
    m = Machine(assemble("""
    .entry 0, c0
    c0: MOVI r1, 1      ; t 3: R1 = 1, Z = 0
        GETM r0, 0      ; t 7: no context owns the monitor, so sel 0 reads 0: R0 = 0, Z = 1
        MOVI r1, 1      ; t 11: Z = 0
        MON  r2, r2     ; t 15: R2 = 0, no owner: this context takes the monitor; Z stays 0
        GETM r0, 0      ; t 19: the owner's sel 0, the four counts, all 0 (no pin moves): Z = 1
        MOVI r1, 1      ; t 23: Z = 0
        GETM r0, 9      ; t 27: an unused sel reads 0: Z = 1
        HALT
    """))
    m.run(200)
    z = {c["t"]: (c["outcome"], c["post"]["r0"], c["post"]["z"]) for c in m.commits}
    assert z[3][0] == "C" and z[3][2] == 0
    assert z[7] == ("C", 0, 1)
    assert z[11][2] == 0
    assert z[15][0] == "C" and z[15][2] == 0
    assert z[19] == ("C", 0, 1)
    assert z[23][2] == 0
    assert z[27] == ("C", 0, 1)


def test_isa_checker_encodings_are_current():
    """formal/esc_isa_enc.vh, the encodings of the instruction checker (the only part of it not
    written from the specification), is exactly what scripts/gen_isa_enc.py makes from
    isa/isa.yaml: an encoding change that was not regenerated fails here, not in a proof."""
    spec = importlib.util.spec_from_file_location("gen_isa_enc", ROOT / "scripts" / "gen_isa_enc.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    have = (ROOT / "formal" / "esc_isa_enc.vh").read_bytes().decode("utf-8")
    assert have == gen.render(), "formal/esc_isa_enc.vh is stale: run python scripts/gen_isa_enc.py"
