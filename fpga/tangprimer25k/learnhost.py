"""The laptop's half of the learning bench (learnbench.py runs on the Pico W; README.md,
"Learned on the bench"). Three steps, each between two Pico runs:

    python learnhost.py source REC.sr OUT/source.json [--pin P] [--frames N]
        a real transmitter's recording (a sigrok session) cut into its first frame of each
        distinct word, as (level, microseconds) segments the Pico plays onto the chip's pin
    python learnhost.py learn OUT/capture.json OUT [--src 2] [--tx 3]
        what the CHIP recorded of that playback (its own capture unit): esclearn learns the
        protocol from it, escgen writes a transmitter (on --tx) and a receiver (on --src),
        each certified by escert, and make_bundle turns them into the Pico's bundles
        OUT/learned_tx.json and OUT/learned_rx.json; the words the chip's capture decodes to
        are compared with the recording's
    python learnhost.py judge OUT/capture.json OUT/sent.json WORD
        the marks the Pico timed on --tx while the chip sent WORD (a word the recording never
        had), decoded with the learned model, and by rc-switch when it is built here

The recording's frames are played, not the device itself: the chip never sees the file,
only the pin, and learns from its own capture of it.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "sw"))
sys.path.insert(0, str(ROOT / "sw" / "rp2350"))
import make_bundle  # noqa: E402
from esclearn import LearnError, learn  # noqa: E402
from esclearn.__main__ import resolution  # noqa: E402
from escgen import TableError, build, build_rx, load  # noqa: E402
from esctape.export import read_trace  # noqa: E402

TICK_US = 1 / 25  # chip ticks are 40 ns at 25 MHz
SRC, TX = 2, 3    # uio pins: the playback into the chip, the chip's transmitter


def frame_segments(fr, gap_ticks):
    """[(level, us)] of one frame (esclearn's [(mark, space), ..., (mark, None)]), idle low,
    with the gap after it."""
    segs = []
    for m, s in fr:
        segs.append([1, round(m * TICK_US)])
        segs.append([0, round((gap_ticks if s is None else s) * TICK_US)])
    return segs


def source(rec: Path, out: Path, pin: int = 0, nframes: int = 4) -> dict:
    records, _ = read_trace(rec)
    model = learn(records, pin=pin, name="recording", source=rec.name, quant=resolution(rec))
    if model.idle:
        raise SystemExit("the recording idles high: the player and this bench assume idle low")
    gap = getattr(model, "gap", None) or 10 * 25000      # ticks of quiet after a frame
    seen, frames = set(), []
    for fr in model.frame_list(records, pin):
        w = model.words(fr)
        if w is None or tuple(w) in seen:
            continue
        seen.add(tuple(w))
        frames.append({"words": list(w), "segs": frame_segments(fr, gap)})
        if len(frames) == nframes:
            break
    doc = {"recording": rec.name, "summary": model.summary()[:4], "frames": frames}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc))
    for f in frames:
        print("frame %s: %d segments, %.1f ms" % (" ".join("0x%x" % w for w in f["words"]),
                                                  len(f["segs"]), sum(d for _, d in f["segs"]) / 1000))
    return doc


def learned_model(capture: Path, src: int):
    records, _ = read_trace(capture)
    return records, learn(records, pin=src, name="learned_on_chip", source=capture.name, quant=1)


def learn_step(capture: Path, out: Path, src: int = SRC, tx: int = TX, source_doc: Path | None = None):
    records, model = learned_model(capture, src)
    print("\n".join(model.summary()))
    got = model.decode(records, src)
    if source_doc and source_doc.exists():
        want = [f["words"] for f in json.loads(source_doc.read_text())["frames"]]
        ok = all(w in got for w in want)
        print("the chip's capture decodes to %d frame(s); the recording's %d words %s" % (
            len(got), len(want), "all among them" if ok else "NOT all among them: %r" % want))
        if not ok:
            raise SystemExit(1)
    out.mkdir(parents=True, exist_ok=True)
    table = out / "learned_on_chip.yaml"
    table.write_text(model.table_yaml("mailbox", None), newline="\n")
    t = load(table)
    asm_tx, asm_rx = out / "learned_on_chip.asm", out / "learned_on_chip_rx.asm"
    try:                       # escgen refuses either unless escert certifies it
        asm_tx.write_text(build(t)[0], newline="\n")
        asm_rx.write_text(build_rx(t)[0], newline="\n")
    except TableError as e:
        raise SystemExit("escgen refused the learned table: %s" % e)
    res = {}
    # the transmitter's pin parameter is named after the table's pin (PIN_OUT), the
    # receiver's is PIN
    pin_tx = "PIN_" + next(iter(t["pins"]))
    for asm, pin, key, par in ((asm_tx, tx, "tx", pin_tx), (asm_rx, src, "rx", "PIN")):
        b = make_bundle.bundle(asm, "learned_%s.json" % key, {par: pin})
        if not (b["cfg"][2] >> pin) & 1 and key == "tx":
            raise SystemExit("the transmitter does not own uio[%d]: %r" % (pin, b["cfg"]))
        bad = [c for c in b["cert"] if c[1] != "CHECKED"]
        print("%s: %s, %d words, pin uio[%d], escert %s" % (
            key, asm.name, len(b["words"]), pin, ", ".join("ctx %d %s%s" % (
                k, s, "" if lb is None else " (lead >= %d)" % lb) for k, s, lb in b["cert"])))
        if bad:
            raise SystemExit("%s is not certified: %r" % (key, bad))
        (out / ("learned_%s.json" % key)).write_text(json.dumps(b))
        res[key] = b
    return model, res


def judge(capture: Path, sent: Path, word: int, src: int = SRC) -> bool:
    _, model = learned_model(capture, src)
    marks = json.loads(sent.read_text())["marks"]          # [(rise_us, fall_us)], Pico time
    if not marks:
        print("judge: the Pico timed no marks on the transmit pin")
        return False
    t0 = marks[0][0] - 2000
    records = [(0, 0)]
    for r, f in marks:
        records += [(round((r - t0) / TICK_US), 1), (round((f - t0) / TICK_US), 0)]
    records.append((records[-1][0] + 50 * 25000, 0))
    got = model.decode(records, 0)
    ok = [word] in got
    print("judge: the learned model reads %s: %s" % (
        ", ".join(" ".join("0x%x" % w for w in fr) for fr in got) or "nothing",
        "the word sent, 0x%x" % word if ok else "NOT 0x%x" % word))
    rc = rcswitch_line(marks)
    if rc is not None:
        print("judge: rc-switch (outside decoder) reads: %s" % (rc or "nothing"))
    return ok


def rcswitch_line(marks):
    """rc-switch's receiver (test/cosim/rcswitch_rx.cpp, built by outside_decoders.sh) on the
    edges in microseconds, closed by one more edge a sync gap later; None if not built here."""
    exe = Path(os.environ.get("OUTSIDE_PD", ROOT / "build" / "outside-decoders")) / "bin" / "rcswitch_rx"
    if not exe.exists() and not shutil.which("rcswitch_rx"):
        return None
    t0 = marks[0][0]
    edges = [e - t0 for r, f in marks for e in (r, f)]
    edges.append(edges[-1] + (edges[2] - edges[1]))
    r = subprocess.run([str(exe if exe.exists() else "rcswitch_rx")],
                       input="".join("%d\n" % e for e in edges), capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="learnhost")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("source")
    a.add_argument("recording")
    a.add_argument("out")
    a.add_argument("--pin", type=int, default=0)
    a.add_argument("--frames", type=int, default=4)
    a = sub.add_parser("learn")
    a.add_argument("capture")
    a.add_argument("out")
    a.add_argument("--src", type=int, default=SRC)
    a.add_argument("--tx", type=int, default=TX)
    a.add_argument("--source-doc")
    a = sub.add_parser("judge")
    a.add_argument("capture")
    a.add_argument("sent")
    a.add_argument("word")
    a.add_argument("--src", type=int, default=SRC)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "source":
            source(Path(args.recording), Path(args.out), args.pin, args.frames)
        elif args.cmd == "learn":
            learn_step(Path(args.capture), Path(args.out), args.src, args.tx,
                       Path(args.source_doc) if args.source_doc else None)
        else:
            return 0 if judge(Path(args.capture), Path(args.sent), int(args.word, 16), args.src) else 1
    except LearnError as e:
        print("no model: %s" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
