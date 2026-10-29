"""The site's "record it, speak it": sw/esclearn on a recording, then the firmware it
generates, certified and run on the ISS.

As with escplay, the same module runs in the browser (Pyodide, on a recording the visitor
drops or a sample) and at build time (site/tools/runs.py, the samples' results the page
shows before Python has loaded). It only wires esclearn, escgen, escert and the ISS
together and turns the results into plain lists.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import escgen
import esclearn
from escert import certify
from esclearn import LearnError
from esclearn.__main__ import resolution
from esctape.export import read_trace

DATA = Path(__file__).resolve().parents[2] / "sw" / "tests" / "data" / "sigrok"
UPLOAD = Path("/tmp/esclearn")
MAX_BYTES = 4 << 20

# Public-domain recordings from sigrok-dumps (sw/tests/data/sigrok/README.md)
SAMPLES = [
    {"id": "nec", "file": "nec_all.sr", "pin": 0, "send": "42ff00",
     "title": "NEC remote",
     "blurb": "A Joy-IT remote, all 21 buttons, from an IR receiver at 1 MHz. Send command "
              "0x42, which no button has: the check byte is filled in."},
    {"id": "rc5", "file": "rc5_button1.sr", "pin": 0, "send": "0ca3",
     "title": "RC-5, raw infrared",
     "blurb": "A Philips VCR remote, button 1 held, straight from the 37 kHz carrier. Send "
              "standby (address 5, command 12), which this recording never pressed."},
    {"id": "sirc", "file": "sirc1.sr", "pin": 0, "send": "08815",
     "title": "Sony SIRC",
     "blurb": "One 20-bit button, four times. Send command 21 to the same device."},
    {"id": "pt2262", "file": "pt2262_abcd.sr", "pin": 0, "send": "022a888",
     "title": "433 MHz socket remote",
     "blurb": "A PT2262 remote through its receiver, buttons A to D, with ragged packet ends. "
              "Send button C's code."},
]


def set_data_dir(path: str) -> None:
    global DATA
    DATA = Path(path)


def samples() -> str:
    return json.dumps(SAMPLES)


def _hexwords(s: str) -> list[int]:
    out = []
    for tok in s.replace(",", " ").split():
        try:
            out.append(int(tok, 16))
        except ValueError:
            raise LearnError(f"'{tok}' is not a hex word") from None
    return out


def _frame(fr):
    """A frame's (mark, space) pairs as plain numbers (an open space is null)."""
    return [[m, None if s is None else s] for m, s in fr]


def learn(source: str, pin: int, send: str = "", data_b64: str = "", unit: int = 0,
          msb: bool = False) -> str:
    """`source` is a sample id, or the visitor's file name with its bytes in data_b64;
    pin -1 picks the channel with the most edges. A failure still names the channels."""
    seen = {}
    try:
        return json.dumps(_learn(source, int(pin), send, data_b64, int(unit), bool(msb), seen))
    except (LearnError, escgen.TableError, ValueError, OSError) as e:
        return json.dumps({"ok": False, "error": str(e), **seen})


def _learn(source, pin, send, data_b64, unit, msb, seen):
    sample = next((s for s in SAMPLES if s["id"] == source), None)
    if sample:
        path = DATA / sample["file"]
        packed = path.with_name(path.name + ".b64")       # in the browser's bundle
        if not path.exists() and packed.exists():
            UPLOAD.mkdir(parents=True, exist_ok=True)
            path = UPLOAD / sample["file"]
            path.write_bytes(base64.b64decode(packed.read_text()))
    else:
        data = base64.b64decode(data_b64)
        if len(data) > MAX_BYTES:
            raise LearnError(f"{len(data) >> 20} MB: at most {MAX_BYTES >> 20} MB here")
        if Path(source).suffix not in (".sr", ".vcd", ".json"):
            raise LearnError("a sigrok session (.sr), a VCD or a chip capture (.json)")
        UPLOAD.mkdir(parents=True, exist_ok=True)
        path = UPLOAD / ("capture" + Path(source).suffix)
        path.write_bytes(data)
    records, names = read_trace(path)
    if pin < 0:
        changes = [0] * len(names)
        for (_, a), (_, b) in zip(records, records[1:]):
            for i in range(len(names)):
                changes[i] += (a ^ b) >> i & 1
        pin = max(range(len(names)), key=changes.__getitem__)
    seen.update(channels=names, pin=pin)
    if not 0 <= pin < len(names):
        raise LearnError(f"no channel {pin}: the recording has {len(names)}")
    name = "learned_" + "".join(c if c.isalnum() else "_" for c in Path(source).stem)[:24]
    model = esclearn.learn(records, pin=pin, name=name, source=Path(sample["file"] if sample
                                                                     else source).name,
                           quant=resolution(path))
    if unit or msb:
        model.regroup(unit or None, msb)
    given = _hexwords(send) if send.strip() else None
    words = model.complete(given) if given else model.frames[0]
    rec = next(fr for fr in model.frame_list(records, pin) if model.words(fr) is not None)

    table, tape = model.replay(words)
    asm, image = escgen.build(table)                      # CHECKED, or TableError
    cert = next(c for c in certify(image) if c.ctx == table["context"])
    durs = [d for m, s in rec for d in (m, s) if d is not None]
    clk = 1000 if min(durs) >= 5000 else 25000            # slow codes at 1 MHz, rescaled
    gen = esclearn.regenerate(model, words, sum(durs), clk, gap=False)
    gfr = model.frame_list(gen, 0)
    out = {"ok": True, "summary": model.summary()[:40], "channels": names, "pin": pin,
           "coding": model.coding, "frames": len(model.frames), "other": model.other,
           "words": [f"{w:0{(model.unit + 3) // 4}x}" for w in words],
           "filled": given is not None and words != given, "carrier": bool(model.carrier),
           "table": model.table_yaml("tape" if tape else "constant", words, pin=0),
           "asm": asm, "image_words": image.used_words,
           "cert": {"status": cert.status, "min_lead": None if cert.min_lead == float("inf")
                    else int(cert.min_lead)},
           "rec": _frame(rec), "gen": _frame(gfr[0]) if gfr else None,
           "decoded": [f"{w:0{(model.unit + 3) // 4}x}" for w in (model.decode(gen, 0)[:1] or [[]])[0]],
           "clk_khz": clk}
    out["sent_ok"] = bool(gfr) and model.decode(gen, 0)[:1] == [words]
    if gfr and words == model.words(rec):                # the replay: compare with it
        slack = 2 * max(25000 / clk, model.quant)
        fails, rel, worst = model.compare(gfr[0], rec, slack)
        out["check"] = {"ok": not fails, "worst_rel": rel, "fails": fails[:4]}
    return out
