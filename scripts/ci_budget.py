#!/usr/bin/env python3
"""Predict the CI gds job time of a local harden from its detailed-routing CPU time.

    python3 scripts/ci_budget.py runs/TAG

Detailed routing is deterministic across thread counts (rc14 locally on 24 threads and
on a 2-vCPU CI runner found the same 51 antenna nets and inserted the same 54 diodes),
so its CPU time transfers from a local run to CI. Wall time on CI is that CPU time
divided by an effective speed-up, plus the rest of the flow:

  * 2.13 local cpu-min per CI minute on a 2-vCPU private-repo runner (rc14's first
    pass: 552 cpu-min locally, 04:19:18 on CI) and 3.7 on a 4-vCPU public-repo runner
    (rc14's whole gds job: 843 cpu-min of routing, 258 min on CI, results identical).
  * 30 min for every other step (CI reached detailed routing after 19 min; the v1.0 CI
    harden spent 32 min outside routing).

GitHub caps a hosted job at 360 min; the workflow's own limit is in gds.yaml.
"""
import glob
import re
import sys

OTHER_MIN = 30
LIMIT_MIN = 360


def hms(s):
    h, m, sec = (int(x) for x in s.split(":"))
    return h * 60 + m + sec / 60


def main(run):
    log = glob.glob(f"{run}/*-openroad-detailedrouting/openroad-detailedrouting.log")[0]
    text = open(log).read()
    # The DRT-0267 line right after "Complete detail routing" is the cumulative cost of a pass.
    passes = [hms(m) for m in re.findall(
        r"Complete detail routing\.\n(?:.*\n)*?\[INFO DRT-0267\] cpu time = ([\d:]+)", text)]
    first = re.findall(r"Number of violations = (\d+)", text)
    ants = re.findall(r"ANT-0002\] Found (\d+) net violations", text)
    total = sum(passes)
    print(f"{run}: {len(passes)} routing pass(es), cpu min "
          + " + ".join(f"{p:.0f}" for p in passes) + f" = {total:.0f}")
    print(f"  first-iteration violations {first[0] if first else '?'}; "
          f"post-route antenna nets per pass {ants}")
    for name, par in (("private (2 vCPU)", 2.13), ("public (4 vCPU)", 3.7)):
        t = OTHER_MIN + total / par
        print(f"  CI {name}: ~{t:.0f} min ({'fits' if t < LIMIT_MIN else 'OVER'} the {LIMIT_MIN} min cap)")


if __name__ == "__main__":
    main(sys.argv[1].rstrip("/"))
