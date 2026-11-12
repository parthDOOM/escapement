#!/usr/bin/env python3
"""The unbounded core proof (esc_core.sby `pdr`), split into one SymbiYosys task per
assertion and run in parallel.

    python3 split_core.py [--jobs N] [--timeout SECONDS] [--json FILE]
                          [--history FILE [--slow F] [--floor S]] [TASK ...]   (from formal/)

One ABC PDR run over every assertion proves them one at a time on one core. Here each
task keeps the assertions written on one source line (a `for` loop gives several) and
removes the rest, so independent properties proceed side by side. The groups are read
from the elaborated design (Yosys names every assertion cell after its source line), so
the split follows the RTL as it changes. The script, files and engine are those of the
`pdr` task in esc_core.sby. Each group is proved without the other assertions as
lemmas, which never makes a result unsound.

Assertions whose source line carries `lemma-group: NAME` are proved together in one task
named NAME, so each serves as a lemma for the others. Proving more assertions in one
task never makes a result unsound either.

Named TASKs run only those groups (the names it prints, such as `faulted` or
`esc_core_889`).

Each group runs as its own sby process, --jobs at a time, so its --timeout counts its own
run, not time spent waiting for a job slot (fcache.run_tasks). The --json FILE records each
group's solver time. Given such a file from an earlier run as --history, a group that
passed there is limited to SLOW (10) times that time, at least --floor (600 s): a proof that
used to take seconds and stops converging then ends as a TIMEOUT within minutes, a failure
to investigate. v2 (11-12): `commit` took 13 s on v1.6 and ran 2.5 h on a4a3836 before its
cause was found (55d53e7 made lu_sel a register; sel_def is now in that group). Groups are
matched across commits by lemma-group name, or by assertion text (line numbers move); the
longest by history start first.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from fcache import Cache, group_id, history, history_limit, run_tasks, solve_secs, task_key

HERE = Path(__file__).resolve().parent
SBY = HERE / "esc_core.sby"
OUT = HERE / "esc_core_split.sby"


def sections(text):
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"\[(\w+)\]", line.strip())
        if m:
            cur = m.group(1)
            out[cur] = []
        elif cur:
            out[cur].append(line)
    return out


def task_names(sec):
    """The tasks esc_core.sby declares (its [tasks] section)."""
    return {s.strip() for s in sec["tasks"] if s.strip() and not s.strip().startswith("#")}


def task_lines(lines, task, tasks):
    """The lines of a section that apply to `task`, prefixes removed. A prefix is any task
    the file declares (`t:` and `~t:`), so a task added to esc_core.sby later is handled
    without changing this script (v2's `tw` once leaked through a fixed list)."""
    res = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"(~?)(\w+):\s*(.*)", s)
        if m and m.group(2) in tasks:
            if (m.group(1) == "") == (m.group(2) == task):
                res.append(m.group(3))
        elif m:
            sys.exit(f"esc_core.sby: '{s}' is prefixed by a task the file does not declare")
        else:
            res.append(s)
    return res


def assertion_groups(script, files, listing_name="esc_core_split.list"):
    """(file, line) of every assertion cell in the design the pdr task proves."""
    srcdir = HERE.parent / "src"
    cmds = []
    for s in script:
        cmds.append(s)
        if s.startswith("prep"):
            break
    listing = HERE / listing_name
    cmds.append(f"tee -q -o {listing} select -list n:$assert$*")
    subprocess.run(["yosys", "-q", "-p", "; ".join(cmds)], cwd=srcdir, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    groups = sorted({(m.group(1), int(m.group(2))) for m in
                     re.finditer(r"\$assert\$([\w.]+):(\d+)\$", listing.read_text())})
    listing.unlink()
    return groups


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--timeout", type=int, default=3600, help="seconds per task")
    ap.add_argument("--json", help="also write each task's result to this file")
    ap.add_argument("--skip", action="store_true",
                    help="skip a group whose inputs match its last PASS (fcache.py; never in a release campaign)")
    ap.add_argument("--history", metavar="JSON",
                    help="an earlier --json FILE: a group that passed there gets SLOW x its solver "
                         "time as its limit (at least --floor, at most --timeout)")
    ap.add_argument("--slow", type=float, default=10, help="the --history factor (default 10)")
    ap.add_argument("--floor", type=int, default=600, help="the least --history limit (default 600 s)")
    ap.add_argument("only", nargs="*", metavar="TASK", help="run only these tasks")
    a = ap.parse_args()

    sec = sections(SBY.read_text())
    script = task_lines(sec["script"], "pdr", task_names(sec))
    files = [f.strip() for f in sec["files"] if f.strip()]
    groups = assertion_groups(script, files)
    src = {f: (HERE.parent / "src" / f).read_text().splitlines() for f, _ in groups}

    tasks = {}                        # task name -> [(file, line), ...]
    for f, ln in groups:
        m = re.search(r"lemma-group:\s*(\w+)", src[f][ln - 1])
        tasks.setdefault(m.group(1) if m else f"{Path(f).stem}_{ln}", []).append((f, ln))
    unknown = set(a.only) - set(tasks)
    if unknown:
        sys.exit(f"no such task: {' '.join(sorted(unknown))} (tasks: {' '.join(tasks)})")
    names = a.only or list(tasks)

    def text_of(n):
        (f, ln), more = tasks[n][0], len(tasks[n]) - 1
        return src[f][ln - 1].strip() if not more else f"group {n}: {f}:{ln} and {more} more"
    past = history(a.history) if a.history else {}
    prev = {n: past.get(group_id(n, text_of(n))) for n in names}
    limit = {n: history_limit(prev[n], a.slow, a.timeout, a.floor) for n in names}
    body = ["# Generated by split_core.py from esc_core.sby; do not edit.", "[tasks]", *names,
            "", "[options]", "mode prove", *[f"{n}: timeout {limit[n]}" for n in names], "",
            "[engines]", "abc pdr", "", "[script]"]
    for s in script:
        body.append(s)
        if s.startswith("prep"):
            for n in names:
                keep = " ".join(f"n:$assert${f}:{ln}$*" for f, ln in tasks[n])
                body.append(f"{n}: chformal -assert -remove n:$assert$* {keep}"
                            f"{' %u' * (len(tasks[n]) - 1)} %d")
    body += ["", "[files]", *files, ""]
    OUT.write_text("\n".join(body))

    print(f"{len(names)} assertion groups, {a.jobs} at a time, {a.timeout} s each")

    # fcache.py's key per group: esc_core.sby, the files it lists and the tools, plus this
    # group's engine, mode and generated script (the timeout is left out: a PASS is a PASS)
    cache, keys, skipped = Cache(), {}, {}
    for n in names:
        keys[n] = task_key(SBY.name, "pdr-split:" + n,
                           "\n".join(["abc pdr", "mode prove", *[l for l in body if l.startswith(n + ":")
                                                                 and not l.startswith(n + ": timeout ")]]))
        when = cache.passed("pdr-split:" + n, keys[n]) if a.skip else None
        if when:
            skipped[n] = when
    run = [n for n in names if n not in skipped]
    t0 = time.time()
    if run:
        # the longest first by --history (groups it does not know first of all), so a long
        # one never starts last
        run.sort(key=lambda n: -(prev[n] if prev[n] is not None else float("inf")))
        run_tasks(OUT.name, run, a.jobs)
    wall = time.time() - t0

    worst, record = 0, []
    for n in names:
        (f, ln), more = tasks[n][0], len(tasks[n]) - 1
        if n in skipped:
            print(f"SKIP     {f}:{ln:<4d} unchanged since its PASS of {skipped[n]}")
            record.append({"task": n, "where": [f"{g}:{k}" for g, k in tasks[n]], "status": "SKIP",
                           "passed": skipped[n]})
            continue
        d = HERE / f"esc_core_split_{n}"
        status = (d / "status").read_text().split()[0] if (d / "status").exists() else "ERROR"
        log = (d / "logfile.txt").read_text() if (d / "logfile.txt").exists() else ""
        m = re.search(r"Elapsed clock time \[H:MM:SS \(secs\)\]: \S+ \((\d+)\)", log)
        secs = m.group(1) if m else "?"
        solve = solve_secs(log)
        text = text_of(n)
        print(f"{status:8s} {secs:>6s} s  {f}:{ln:<4d} {text[:70]}")
        if status == "TIMEOUT" and prev[n] is not None and limit[n] < a.timeout:
            print(f"         stalled: limit {limit[n]} s = {a.slow:g} x its {prev[n]} s in {a.history}")
        worst = max(worst, {"PASS": 0, "UNKNOWN": 1, "TIMEOUT": 1}.get(status, 2))
        if status == "PASS":
            cache.record("pdr-split:" + n, keys[n])
        record.append({"task": n, "where": [f"{g}:{k}" for g, k in tasks[n]], "status": status,
                       "secs": int(secs) if secs.isdigit() else None, "solve_secs": solve,
                       "limit": limit[n], "prev_secs": prev[n], "text": text})
    print(f"wall time {wall:.0f} s")
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps({"engine": "abc pdr", "jobs": a.jobs, "wall_secs": round(wall),
                                            "tasks": record}, indent=1) + "\n")
    return worst


if __name__ == "__main__":
    sys.exit(main())
