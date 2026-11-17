"""The SWD bench judged by an outside analyser and decoder (J2): while the chip runs
swdbench's session against an RP2040, a second Pico running gusmanb's LogicAnalyzer firmware
(v6.0) records SWCLK and SWDIO, and sigrok's `swd` decoder reads each access from the
recording. Neither the analyser nor the decoder is ours.

    python fpga/tangprimer25k/swdjudge.py --la COM12 --host COM8 [--tc TerminalCapture.exe] [--out DIR]

For every step of swdbench.steps() (one host command each): TerminalCapture (the analyser's
command-line client, v6.0.0.1) is armed on the first SWCLK edge, the Pico W runs that one
step (mpremote, keeping the interpreter's state between steps), and the capture (25 MS/s,
40 ns a sample) is written as a VCD and decoded by sigrok-cli in WSL, as test/cosim/judges.py
decodes the simulated pins. The judge passes when:
  - every read's data, as the decoder takes it off the wire, equals what the chip reported
    to its host, and every write's data what the host asked for, with the decoder's ACK OK
    and no parity warning. The first step (wake-up and TARGETSEL) is judged on its clock
    only: the decoder has no dormant state or TARGETSEL (SWD protocol version 2), and
    reads the selection alert as packets; the next step shows its effect;
  - every SWCLK high and low phase lasts at least H = 16 ticks (640 ns at 25 MHz) less one
    sample, as swd_idcode is built to make them;
  - the control fails: the DPIDR capture with one SWDIO pulse inverted (judges.flipped)
    must no longer decode to the chip's value.
Wiring: README.md, SWD (the analyser's GP2 on SWCLK, GP3 on SWDIO).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (HERE, REPO / "sw", REPO / "sw" / "rp2350", REPO / "test" / "cosim"):
    sys.path.insert(0, str(p))
import swdbench  # noqa: E402
from judges import flipped, vcd  # noqa: E402

RATE = 25_000_000
NS = 1e9 / RATE                                  # 40 ns a sample
PRE, POST = 1000, 30000                          # 1.24 ms: the longest step is about 0.6 ms
H_NS = 16 * 40                                   # swd_idcode's H at 25 MHz
ANN = "swd=read:write:ack:data:parity"


def settings(path: Path) -> None:
    """A TerminalCapture session (SharedDriver.CaptureSession as JSON): two channels, an
    edge trigger on the first falling SWCLK edge (SWCLK rests high between packets)."""
    path.write_text(json.dumps({
        "Frequency": RATE, "PreTriggerSamples": PRE, "PostTriggerSamples": POST,
        "LoopCount": 0, "MeasureBursts": False,
        "CaptureChannels": [{"ChannelNumber": 0, "ChannelName": "swclk"},
                            {"ChannelNumber": 1, "ChannelName": "swdio"}],
        "TriggerType": 0, "TriggerChannel": 0, "TriggerInverted": True,
        "TriggerBitCount": 0, "TriggerPattern": 0}))


def read_csv(path: Path) -> dict[str, list[int]]:
    lines = path.read_text().split()
    names = [n.strip() for n in lines[0].split(",")]
    cols = list(zip(*(map(int, ln.split(",")) for ln in lines[1:])))
    return {n: list(c) for n, c in zip(names, cols)}


def wslpath(p: Path) -> str:
    return subprocess.run(["wsl.exe", "wslpath", "-a", str(p).replace("\\", "/")],
                          capture_output=True, text=True).stdout.strip()


def sigrok(text: str, work: Path) -> list[str]:
    f = work / "decode.vcd"
    f.write_text(text, newline="\n")
    r = subprocess.run(["wsl.exe", "-e", "sigrok-cli", "-I", "vcd", "-i", wslpath(f),
                        "-P", "swd:swclk=swclk:swdio=swdio", "-A", ANN],
                       capture_output=True, text=True, timeout=300)
    if r.returncode:
        return ["ERROR sigrok-cli: " + r.stderr.strip()[:300]]
    return [ln.split(":", 1)[-1].strip() for ln in r.stdout.splitlines() if ln.strip()]


def phases(clk: list[int]) -> tuple[int, int]:
    """(shortest high, shortest low) SWCLK phase in samples, between two edges."""
    e = [t for t in range(1, len(clk)) if clk[t] != clk[t - 1]]
    hi = [b - a for a, b in zip(e, e[1:]) if clk[a] == 1]
    lo = [b - a for a, b in zip(e, e[1:]) if clk[a] == 0]
    return (min(hi) if hi else 0), (min(lo) if lo else 0)


class Bench:
    """The Pico W through mpremote, state kept between commands (resume)."""

    def __init__(self, port: str):
        self.mp = shutil.which("mpremote") or "mpremote"
        self.port = port

    def run(self, code: str, resume: bool = True) -> str:
        cmd = [self.mp, "connect", self.port] + (["resume"] if resume else []) + ["exec", code]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode:
            raise RuntimeError(f"mpremote: {r.stderr.strip()[:400]}")
        return r.stdout


def main() -> int:
    ap = argparse.ArgumentParser(prog="swdjudge")
    ap.add_argument("--la", required=True, help="the analyser's serial port (COMx)")
    ap.add_argument("--host", required=True, help="the bench Pico W's serial port (COMx)")
    ap.add_argument("--targetsel", type=lambda s: int(s, 0), default=swdbench.RP2040_CORE0)
    ap.add_argument("--out", help="keep each step's capture (VCD) and the log here")
    ap.add_argument("--arm", type=float, default=3.0, help="seconds for the analyser to arm")
    ap.add_argument("--tc", default=os.environ.get("TERMINALCAPTURE", "TerminalCapture"),
                    help="TerminalCapture.exe (the analyser's command-line client, from the "
                         "terminalcapture_6.0.0.1 release zip); default: $TERMINALCAPTURE, "
                         "else on the PATH")
    a = ap.parse_args()
    tc = shutil.which(a.tc) or a.tc
    out = Path(a.out) if a.out else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    log: list[str] = []

    def say(s: str) -> None:
        print(s, flush=True)
        log.append(s)

    bench = Bench(a.host)
    steps = swdbench.steps(a.targetsel)
    bad = 0
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        tcs = work / "swd.tcs"
        settings(tcs)
        bench.run("import barepico, swdbench\nio = barepico.open_io(sys_hz=swdbench.SYS_HZ)\n"
                  "s = swdbench.SwdBench(io, out=lambda t: None)\ns.setup()", resume=False)
        say(f"swdjudge: analyser {a.la} at {RATE // 1_000_000} MS/s, bench {a.host}, "
            f"TARGETSEL 0x{a.targetsel:08x}; decoder: sigrok swd ({ANN})")
        dpidr_pins = None
        for k, (what, cmd, data, ack, _) in enumerate(steps):
            csv = work / f"step{k:02d}.csv"
            cap = subprocess.Popen([tc, "capture", a.la, str(tcs), str(csv)],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            time.sleep(a.arm)
            if cap.poll() is not None:
                say(f"swdjudge: FAIL  the analyser exited before the step: {cap.stdout.read()[:300]}")
                return 1
            reply = bench.run(f"print(s.xfer({cmd}, {data}))")
            try:
                cap.wait(timeout=20)
            except subprocess.TimeoutExpired:
                cap.kill()
                say(f"swdjudge: FAIL  step {k} ({what}): the analyser never triggered")
                return 1
            if not csv.exists():
                say(f"swdjudge: FAIL  step {k}: no capture ({cap.stdout.read()[:300]})")
                return 1
            st, v = map(int, re.findall(r"\d+", reply)[-2:])
            pins = read_csv(csv)
            text = vcd(pins, NS)
            if out:
                (out / f"step{k:02d}.vcd").write_text(text, newline="\n")
            ann = sigrok(text, work)
            hi, lo = phases(pins["swclk"])
            ok_t = min(hi, lo) * NS >= H_NS - NS
            ok_p = not any("parity" in x.lower() and "error" in x.lower() for x in ann)
            if ack == 0:
                # the wake-up and TARGETSEL: sigrok's swd decoder (0.7.2) has no dormant state
                # and no TARGETSEL, so it reads the selection alert as packets. Judged on its
                # clock only; step 1 shows its effect (the RP2040 answers only once woken
                # and selected)
                ok_d, seen = True, "(not judged: the decoder has no dormant state or TARGETSEL)"
            else:
                want = f"0x{(v if data is None else data):08x}"     # a read's data or a write's
                ok_d = want in ann and "OK" in ann and ok_p
                seen = " ".join(ann)[:90]
            ok = ok_t and ok_d and (st & 7) == ack
            bad += not ok
            say(f"step {k:2d} {what:34s} chip: ACK {st & 7} 0x{v:08x} | decoder: {seen}"
                f" | SWCLK high >= {hi * NS:.0f} ns, low >= {lo * NS:.0f} ns"
                f" | {'PASS' if ok else 'FAIL'}")
            if cmd == swdbench.DP_RD[0x0]:
                dpidr_pins, dpidr_v = pins, v
        bench.run("s.host.stop()")
        if dpidr_pins is not None:
            ctl = dict(dpidr_pins, swdio=flipped(dpidr_pins["swdio"]))
            ann = sigrok(vcd(ctl, NS), work)
            caught = f"0x{dpidr_v:08x}" not in ann
            say(f"control: the DPIDR capture with one SWDIO pulse inverted decodes to "
                f"{' '.join(ann)[:80]!r}: {'fails, as it must' if caught else 'STILL PASSES'}")
            bad += not caught
    say("swdjudge: " + ("PASS: sigrok's swd decoder read every access after the wake-up off the "
                        "wire as the chip and its host had it, every SWCLK phase >= 640 ns "
                        "less a sample, and the control fails"
                        if not bad else f"FAIL ({bad})"))
    if out:
        (out / "swdjudge.log").write_text("\n".join(log) + "\n", newline="\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
