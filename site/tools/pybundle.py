"""Package what the browser needs to run the real toolchain, and the data the page reads.

    python site/tools/pybundle.py

Writes, under site/public/ (all generated, git-ignored):
  py/bundle.json  sw/ packages, the playground modules, firmware sources, the ISA
                  table and the sample recordings (base64), as {path: text} laid out as
                  in the repository, for the worker to write into Pyodide's file system
  pyodide/        the Pyodide runtime, copied from node_modules (self-hosted)
  data/isa.json   the instruction table for the explorer: isa/isa.yaml (encodings)
                  joined with the tables in docs/02-ISA.md (what each one does)

Pyodide has no PyYAML without a download, so the bundle carries isa.yaml as JSON (JSON
is YAML) and a two-line yaml module that parses it.
"""
import base64
import json
import re
import shutil
import sys
from pathlib import Path

import yaml

SITE = Path(__file__).resolve().parents[1]
ROOT = SITE.parent
PUB = SITE / "public"
PACKAGES = ["escasm", "esciss", "escert", "escmodels", "escgen", "esclearn", "esctape"]
SKIP_FW = {"westminster.asm", "seven_seg.asm"}
RUNTIME = ["pyodide.mjs", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip",
           "pyodide-lock.json"]


def bundle():
    isa = yaml.safe_load((ROOT / "isa" / "isa.yaml").read_text())
    files = {}
    for pkg in PACKAGES:
        for f in sorted((ROOT / "sw" / pkg).glob("*.py")):
            files[f"sw/{pkg}/{f.name}"] = f.read_text(encoding="utf-8")
    for f in sorted((SITE / "py").glob("*.py")):
        files[f"site/py/{f.name}"] = f.read_text(encoding="utf-8")
    sys.path[:0] = [str(ROOT / "sw"), str(SITE / "py")]
    import learnplay
    for s in learnplay.SAMPLES:
        data = (learnplay.DATA / s["file"]).read_bytes()
        files[f"sw/tests/data/sigrok/{s['file']}.b64"] = base64.b64encode(data).decode()
    for f in sorted((ROOT / "firmware").glob("*.asm")):
        if f.name not in SKIP_FW:
            files[f"firmware/{f.name}"] = f.read_text(encoding="utf-8")
    files["isa/isa.yaml"] = json.dumps(isa)
    files["shim/yaml.py"] = "import json\n\n\ndef safe_load(s):\n    return json.loads(s)\n"
    (PUB / "py").mkdir(parents=True, exist_ok=True)
    text = json.dumps(files, separators=(",", ":"))
    (PUB / "py" / "bundle.json").write_text(text, encoding="utf-8")
    return len(text)


def runtime():
    src = SITE / "node_modules" / "pyodide"
    dst = PUB / "pyodide"
    dst.mkdir(parents=True, exist_ok=True)
    for name in RUNTIME:
        shutil.copy2(src / name, dst / name)


def isa_table():
    sys.path.insert(0, str(ROOT / "sw"))
    from escasm.asm import SYNTAX
    isa = yaml.safe_load((ROOT / "isa" / "isa.yaml").read_text())
    doc = (ROOT / "docs" / "02-ISA.md").read_text(encoding="utf-8")
    rows, group = {}, None
    for line in doc.splitlines():
        if line.startswith("### "):
            group = line[4:].strip()
            continue
        m = re.match(r"^\| ([0-9A-F]{2}(?:\s*[–/-]\s*[0-9A-F]{2})*(?:\s*/\s*[0-9A-F]{2})*) \|(.*)\|\s*$", line)
        if not m or group is None:
            continue
        cells = [c.strip() for c in m.group(2).split("|")]
        ops = m.group(1)
        if "–" in ops or "-" in ops:
            a, b = re.split(r"\s*[–-]\s*", ops)
            codes = range(int(a, 16), int(b, 16) + 1)
        else:
            codes = [int(x, 16) for x in re.split(r"\s*/\s*", ops)]
        for c in codes:
            rows[c] = {"group": group, "syntax": cells[0].replace("**W**", "").strip(),
                       "effect": cells[2] if len(cells) > 2 else "",
                       "flags": cells[3] if len(cells) > 3 else ""}
    out = []
    for name, spec in isa["opcodes"].items():
        op = int(spec["op"])
        r = rows.get(op, {})
        out.append({"name": name, "op": op, "format": spec["format"],
                    "wait": bool(spec.get("wait_class", False)),
                    "fields": isa["formats"][spec["format"]] or {},
                    "group": r.get("group", "Other"),
                    "syntax": (name + " " + ", ".join(SYNTAX[spec["format"]])).strip(),
                    "operands": SYNTAX[spec["format"]],
                    "effect": r.get("effect", ""), "flags": r.get("flags", "")})
    missing = [o["name"] for o in out if not o["effect"]]
    if missing:
        print("no description in docs/02-ISA.md for", missing, file=sys.stderr)
    (PUB / "data").mkdir(parents=True, exist_ok=True)
    (PUB / "data" / "isa.json").write_text(json.dumps(
        {"version": isa["version"], "opcodes": sorted(out, key=lambda o: o["op"]),
         "fault_causes": isa["fault_causes"], "admit_subcodes": isa["admit_subcodes"]},
        separators=(",", ":")))
    return len(out)


if __name__ == "__main__":
    n = bundle()
    runtime()
    k = isa_table()
    print(f"bundle {n // 1024} KiB, runtime copied, {k} opcodes")
