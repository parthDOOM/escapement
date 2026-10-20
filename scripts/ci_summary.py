#!/usr/bin/env python3
"""Add one check's result to a CI summary file (JSON), for the results page on the site.

    ci_summary.py SUMMARY formal FORMAL_DIR [SPLIT_JSON]
    ci_summary.py SUMMARY junit  NAME FILE.xml...
    ci_summary.py SUMMARY cert   LOG
    ci_summary.py SUMMARY equiv  LOG
    ci_summary.py SUMMARY mutation RESULTS_JSON

Each call appends a check {name, status, detail, items} to SUMMARY, creating it with the
commit and run from the GitHub Actions environment. The site's build collects the
summaries of the latest master runs (site/tools/ci_results.py). Exit status is 0 even for
a failed check: the step that ran the check decides whether the job fails.
"""
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

# formal-quick's SymbiYosys tasks, by output directory
QUICK = [
    ("esc_core_bmc", "Core properties, bounded to 20 steps"),
    ("esc_core_cover", "Core cover: each property's precondition is reachable"),
    ("esc_ctx_kind", "P2 never late, per context (k-induction)"),
    ("esc_greg_prove", "G1-G3 clock-gated banks (k-induction)"),
    ("esc_greg_cover", "Clock-gate cover"),
    ("esc_host_prove", "S1 configuration frozen during a run (PDR)"),
    ("esc_host_cover", "Host interface cover"),
]


def secs_of(d: Path):
    log = d / "logfile.txt"
    if not log.exists():
        return None
    m = re.search(r"Elapsed clock time \[H:MM:SS \(secs\)\]: \S+ \((\d+)\)", log.read_text(errors="replace"))
    return int(m.group(1)) if m else None


def status_of(d: Path):
    s = d / "status"
    return s.read_text().split()[0] if s.exists() else "NOT RUN"


def formal(args):
    root = Path(args[0])
    items = [{"name": label, "status": status_of(root / d), "secs": secs_of(root / d)} for d, label in QUICK]
    split = Path(args[1]) if len(args) > 1 else None
    n_core = 0
    if split and split.exists():
        for t in json.loads(split.read_text())["tasks"]:
            where = t["where"][0] + (f" and {len(t['where']) - 1} more" if len(t["where"]) > 1 else "")
            items.append({"name": f"Core, unbounded (PDR): {where}", "status": t["status"], "secs": t["secs"],
                          "text": t["text"]})
            n_core += 1
    else:
        items.append({"name": "Core, unbounded (PDR)", "status": "NOT RUN", "secs": None})
    ok = sum(i["status"] == "PASS" for i in items)
    detail = f"{ok} of {len(items)} tasks pass; {n_core} unbounded core proof tasks"
    return {"name": "Formal proofs", "status": "pass" if ok == len(items) else "fail", "detail": detail,
            "items": items}


def junit(args):
    name, files = args[0], args[1:]
    tests = fails = skipped = 0
    for f in files:
        if not Path(f).exists():
            return {"name": name, "status": "fail", "detail": f"no results file {f}"}
        root = ET.parse(f).getroot()
        for case in root.iter("testcase"):
            tests += 1
            if case.find("failure") is not None or case.find("error") is not None:
                fails += 1
            elif case.find("skipped") is not None:
                skipped += 1
    detail = f"{tests - fails - skipped} of {tests} pass" + (f", {skipped} skipped" if skipped else "")
    return {"name": name, "status": "pass" if fails == 0 and tests else "fail", "detail": detail,
            "tests": tests, "failures": fails}


def cert(args):
    # make cert prints "== firmware" and then one "ctx N: VERDICT ..." line per context
    blocks = Path(args[0]).read_text(errors="replace").split("== ")[1:]
    total = len(blocks)
    checked = sum(1 for b in blocks if re.findall(r"ctx \d+: (\w+)", b)
                  and all(v == "CHECKED" for v in re.findall(r"ctx \d+: (\w+)", b)))
    return {"name": "Timing certificates", "status": "pass" if total and checked == total else "fail",
            "detail": f"{checked} of {total} firmware CHECKED for all inputs"}


def equiv(args):
    text = Path(args[0]).read_text(errors="replace")
    m = re.search(r"miter: (\d+) outputs \((\d+) bits\)", text)
    ok = "Networks are equivalent" in text and "PASS:" in text
    detail = (f"sequentially equivalent; {m.group(2)} output bits compared" if m else "") if ok else "not proved"
    return {"name": "Netlist equals RTL", "status": "pass" if ok else "fail", "detail": detail}


def mutation(args):
    res = json.loads(Path(args[0]).read_text())
    c = res["counts"]
    killed, eq, survived = c.get("KILLED", 0), c.get("EQUIVALENT", 0), c.get("SURVIVED", 0)
    invalid = c.get("BADPATCH", 0) + c.get("COMPILE-ERROR", 0)
    detail = f"{killed} of {killed + survived} killed"
    if eq:
        detail += f"; {eq} more proved to change no output"
    if c.get("EXCLUDED"):
        detail += f"; {c['EXCLUDED']} excluded with a written argument (dead under P2, or SRAM ties)"
    if invalid:
        detail += f"; {invalid} did not apply or build"
    by_suite = Counter((r.get("killed_by") or "").split(" ")[0] for r in res.get("results", [])
                       if r["result"] == "KILLED")
    return {"name": "Planted RTL faults", "status": "pass" if survived == 0 and invalid == 0 else "fail",
            "detail": detail, "counts": c, "killed_by": dict(by_suite)}


MODES = {"formal": formal, "junit": junit, "cert": cert, "equiv": equiv, "mutation": mutation}


def main():
    summary, mode, rest = Path(sys.argv[1]), sys.argv[2], sys.argv[3:]
    data = json.loads(summary.read_text()) if summary.exists() else {
        "sha": os.environ.get("GITHUB_SHA", ""),
        "run": (f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/"
                f"{os.environ['GITHUB_RUN_ID']}") if "GITHUB_RUN_ID" in os.environ else "",
        "checks": []}
    try:
        check = MODES[mode](rest)
    except Exception as e:  # a missing or unreadable result is itself a failed check
        check = {"name": mode, "status": "fail", "detail": f"no result ({e.__class__.__name__})"}
    data["checks"].append(check)
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(json.dumps(data, indent=1) + "\n")
    print(f"{check['name']}: {check['status']} ({check.get('detail', '')})")


if __name__ == "__main__":
    main()
