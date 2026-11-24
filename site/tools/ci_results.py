"""The results page's data: the latest master run of each verification workflow, with the
summaries its jobs wrote (scripts/ci_summary.py), into public/data/ci.json.

    python tools/ci_results.py [OUT]        (needs the GitHub CLI, authenticated)

Run by the site workflow before the build. Without the GitHub CLI or network the file is
not written and the page says that the results are not part of this build.
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = os.environ.get("GITHUB_REPOSITORY", "parthDOOM/escapement")
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "public/data/ci.json")

# workflow, what it shows, the job names of a gds run that count as checks
WORKFLOWS = [
    ("formal", "Every SymbiYosys proof, on each change to the RTL or the proofs"),
    ("equivalence", "The netlist of each layout build against the RTL of its commit"),
    ("mutation", "Each RTL fault against the tests, on each change to the RTL or the tests"),
    ("fw-mutants", "Timing bugs planted in every firmware against escert, on each change to the firmware or escert"),
    ("test", "Software, certificates, chip tests and lockstep co-simulation, on every push"),
    ("gds", "LibreLane layout, precheck and gate-level tests, on each change to the RTL"),
]
# Since D-69 the precheck and the GL test are steps of the gds job, and the netlist's
# equivalence is proved there too; earlier gds runs had them as jobs, and the equivalence in
# a workflow of its own, which is read while no gds run has its summary.
GDS_CHECKS = {"gds": "Layout build (LibreLane)", "Build GDS": "Layout build (LibreLane)",
              "precheck": "Tiny Tapeout precheck", "Precheck": "Tiny Tapeout precheck",
              "gl_test": "Chip tests on the gate-level netlist", "GL test": "Chip tests on the gate-level netlist"}


def gh(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    return r.stdout


def latest(wf):
    runs = json.loads(gh("run", "list", "-R", REPO, "-w", wf, "-b", "master", "-L", "10",
                         "--json", "databaseId,status,conclusion,headSha,updatedAt,url"))
    done = [r for r in runs if r["status"] == "completed" and r["conclusion"] not in ("cancelled", "skipped")]
    return done[0] if done else None, (runs[0]["status"] != "completed") if runs else False


def summaries(run_id, pattern="ci-summary-*"):
    checks = []
    with tempfile.TemporaryDirectory() as td:
        try:
            gh("run", "download", str(run_id), "-R", REPO, "-p", pattern, "-D", td)
        except RuntimeError:
            return checks
        for f in sorted(Path(td).rglob("summary.json")):
            checks += json.loads(f.read_text(encoding="utf-8"))["checks"]
    return checks


def gds_checks(run_id):
    jobs = json.loads(gh("run", "view", str(run_id), "-R", REPO, "--json", "jobs"))["jobs"]
    if len(jobs) == 1:      # D-69: one job, whose steps are the checks
        parts = [(st["name"], st["conclusion"] or st["status"]) for st in jobs[0].get("steps", [])]
    else:
        parts = [(j["name"], j["conclusion"] or j["status"]) for j in jobs]
    return [{"name": GDS_CHECKS[n], "status": "pass" if c == "success" else "fail", "detail": c}
            for n, c in parts if n in GDS_CHECKS]


def equivalence():
    """The netlist proof of the latest master gds run that has one, else of the equivalence workflow."""
    run, running = latest("gds")
    checks = summaries(run["databaseId"], "ci-summary-equivalence") if run else []
    if not checks:
        run, running = latest("equivalence")
        checks = summaries(run["databaseId"]) if run else []
    return run, running, checks


def main():
    if not shutil.which("gh"):
        print("ci_results: no GitHub CLI, results page data not written")
        return
    out = []
    for wf, what in WORKFLOWS:
        checks = None
        try:
            if wf == "equivalence":
                run, running, checks = equivalence()
            else:
                run, running = latest(wf)
        except RuntimeError as e:
            print(f"ci_results: {wf}: {e}")
            run, running = None, False
        entry = {"workflow": wf, "what": what, "running": running}
        if run:
            if checks is None:
                checks = gds_checks(run["databaseId"]) if wf == "gds" else summaries(run["databaseId"])
            entry |= {"conclusion": run["conclusion"], "sha": run["headSha"], "date": run["updatedAt"],
                      "url": run["url"], "checks": checks}
        out.append(entry)
        print(f"ci_results: {wf}: {entry.get('conclusion', 'no run yet')}, {len(entry.get('checks', []))} checks")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"repo": REPO, "generated": datetime.datetime.now(datetime.timezone.utc)
                               .strftime("%Y-%m-%dT%H:%M:%SZ"), "workflows": out}, indent=1) + "\n",
                   encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
