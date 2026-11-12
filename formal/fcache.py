#!/usr/bin/env python3
"""Run SymbiYosys tasks, optionally skipping the ones whose inputs have not changed since
they last passed.

    python3 fcache.py [--skip] FILE.sby[:TASK] ...                              (from formal/)

Each task is keyed on a hash of everything it reads: its .sby file, every file in the
file's [files] section (the RTL and the formal harness), and the Yosys build. A task that
passes has its key recorded in formal/.fcache.json. With --skip, a task whose key matches
its last PASS is not run and prints SKIP. A firmware or docs change then costs no formal
time, and an edit to the line unit leaves a proof that never reads it alone. A task that
failed, timed out or never ran always runs.

Without --skip every task runs, and the passes are still recorded, so a full run leaves the
cache warm for the iterations after it. Release campaigns never use --skip.

FILE.sby alone runs every task the file declares, one at a time; FILE.sby:TASK runs one.
split_core.py and split_bmc.py use the same key per assertion group (their --skip).
"""
import argparse
import functools
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".fcache.json"


def sby_sections(text):
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"\[(\w+)\]", line.strip())
        if m:
            cur = m.group(1)
            out[cur] = []
        elif cur:
            out[cur].append(line)
    return out


def sby_tasks(sby):
    """The tasks a .sby file declares, or [None] when it has no [tasks] section."""
    sec = sby_sections((HERE / sby).read_text())
    names = [s.split(":")[0].split()[0] for s in sec.get("tasks", [])
             if s.strip() and not s.strip().startswith("#")]
    return names or [None]


@functools.lru_cache(maxsize=None)
def tool_id():
    """The Yosys build (every engine ships in the same suite) and the sby script itself."""
    v = subprocess.run(["yosys", "-V"], capture_output=True, text=True).stdout.strip()
    sby = shutil.which("sby")
    h = hashlib.sha256(Path(sby).read_bytes()).hexdigest()[:16] if sby else "no-sby"
    return f"{v} sby:{h}"


def task_key(sby, task, extra=""):
    """sha256 of the .sby text, every file it lists (each with its name), the tools, the
    task name and `extra` (the generated task text for the split tools)."""
    h = hashlib.sha256()
    text = (HERE / sby).read_text()
    h.update(f"{sby}\0{task}\0{tool_id()}\0{extra}\0".encode())
    h.update(text.encode())
    for f in sby_sections(text).get("files", []):
        f = f.strip()
        if not f or f.startswith("#"):
            continue
        path = (HERE / f.split()[-1]).resolve()   # "[files]" lines may be "name path"
        h.update(f"\0{f}\0".encode())
        h.update(path.read_bytes())
    return h.hexdigest()


class Cache:
    def __init__(self, path=CACHE):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def passed(self, name, key):
        e = self.data.get(name)
        return e["when"] if e and e.get("key") == key else None

    def record(self, name, key):
        self.data[name] = {"key": key, "when": time.strftime("%Y-%m-%d %H:%M")}
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--skip", action="store_true",
                    help="skip a task whose inputs match its last PASS (never in a release campaign)")
    ap.add_argument("targets", nargs="+", metavar="FILE.sby[:TASK]")
    a = ap.parse_args()

    runs = []
    for t in a.targets:
        sby, _, task = t.partition(":")
        runs += [(sby, task)] if task else [(sby, n) for n in sby_tasks(sby)]

    cache, worst = Cache(), 0
    for sby, task in runs:
        name = f"{sby}:{task}" if task else sby
        key = task_key(sby, task)
        when = cache.passed(name, key) if a.skip else None
        if when:
            print(f"SKIP     {name:24s} unchanged since its PASS of {when}", flush=True)
            continue
        t0 = time.time()
        r = subprocess.run(["sby", "-f", sby] + ([task] if task else []), cwd=HERE,
                           capture_output=True, text=True)
        done = re.findall(r"DONE \((\w+)", r.stdout)
        status = done[-1] if done else "ERROR"
        if r.returncode == 0 and status == "PASS":
            cache.record(name, key)
        else:
            worst = 1
            print(r.stdout[-3000:], end="")
        print(f"{status:8s} {name:24s} {time.time() - t0:6.0f} s", flush=True)
    return worst


if __name__ == "__main__":
    sys.exit(main())
