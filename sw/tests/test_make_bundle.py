"""make_bundle.bundle() on programs whose certificate has no finite lead bound.

escert reports min_lead = +inf when a context queues no event and -inf when a late event is
possible (status POSSIBLE). The bundle stores the bound as None in both cases; bringup.py
reports any status other than CHECKED by itself. A probe for the Ethernet receive bench
(fpga/tangprimer25k) that sampled a pin on ADDT after a PUSH, which waits on the host, used
to stop make_bundle with an OverflowError from int(-inf)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sw" / "rp2350"))
import make_bundle  # noqa: E402

POSSIBLE = """
.entry 0, main
.own   0, 0
main:
    ADDT  64
loop:
    EMIT  1, 1, 1
    PUSH  r0                 ; waits on the host for as long as it likes
    ADDT  40                 ; so this deadline may already have passed
    JMP   loop
"""

NO_EVENT = """
.entry 0, main
main:
    ADDI  r0, 1
    JMP   main
"""


def cert_of(tmp_path, src):
    fw = tmp_path / "probe.asm"
    fw.write_text(src)
    b = json.loads(json.dumps(make_bundle.bundle(fw)))    # it must also serialise
    return b["cert"]


def test_possible_late_has_no_bound(tmp_path):
    assert cert_of(tmp_path, POSSIBLE) == [[0, "POSSIBLE", None]]


def test_no_event_has_no_bound(tmp_path):
    [[ctx, status, lead]] = cert_of(tmp_path, NO_EVENT)
    assert (ctx, lead) == (0, None)
