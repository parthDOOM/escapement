"""The site's "make it late" challenge (site/py/lateplay.py): every trick on the page ends
the way the page says, and the audit that compares the pins with the queued events
catches an edge one tick late or one tick early."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "site" / "py"))
import escplay  # noqa: E402
import lateplay  # noqa: E402


@pytest.mark.parametrize("aid", [a["id"] for a in lateplay.ATTEMPTS])
def test_trick_ends_as_the_page_says(aid):
    a = lateplay.BY_ID[aid]
    r = json.loads(lateplay.attempt(a["source"]))
    assert r["ok"], r
    assert lateplay.verdict(r) == a["expect"]
    assert r["audit"]["late"] == 0 and r["audit"]["mismatches"] == []


def first_edge(m, pin=0):
    rows = m.pins
    return next(t for t in range(1, len(rows))
                if (rows[t]["uio_out"] ^ rows[t - 1]["uio_out"]) >> pin & 1)


@pytest.mark.parametrize("shift", ["late", "early"])
def test_audit_catches_an_edge_off_by_one_tick(shift):
    json.loads(escplay.run(lateplay.source_of("crowd"), "blank", '{"ticks": 2000}'))
    m = escplay.LAST["m"]
    assert lateplay.audit(m)["mismatches"] == []
    t = first_edge(m)
    if shift == "late":
        m.pins[t] = dict(m.pins[t - 1], t=t)       # the pin keeps its old level one tick longer
    else:
        m.pins[t - 1] = dict(m.pins[t], t=t - 1)   # the pin changes one tick before its deadline
    a = lateplay.audit(m)
    assert a["mismatches"] and a["mismatches"][0]["pins"] & 1
    assert a["late"] == (1 if shift == "late" else 0)
