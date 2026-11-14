"""An outside judge for firmware waveforms: sigrok's protocol decoders (libsigrokdecode,
through sigrok-cli), which were written by other people from the protocols' specifications.

A run's pin levels (one sample per chip clock) are written as a VCD and decoded by
sigrok-cli; the tests compare its annotations with what the host asked for. Without
sigrok-cli on the PATH the tests skip (CI installs it: apt package sigrok-cli 0.7.2).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

SIGROK = shutil.which("sigrok-cli")


def vcd(channels: dict[str, list[int]], tick_ns: float, tail_ticks: int = 0) -> str:
    """A VCD of per-tick levels. Times in 10 ns units (tick_ns must be a multiple of 10).
    `tail_ticks` of idle and a final marker edge pad the end, so a decoder that closes a
    packet on idle sees the last one end."""
    step = round(tick_ns / 10)
    assert abs(step * 10 - tick_ns) < 1e-6, tick_ns
    ids = {name: chr(33 + k) for k, name in enumerate(channels)}
    out = ["$timescale 10 ns $end", "$scope module chip $end"]
    out += [f"$var wire 1 {ids[n]} {n} $end" for n in channels]
    out += ["$upscope $end", "$enddefinitions $end"]
    n = max(len(v) for v in channels.values())
    prev = {}
    for t in range(n):
        ch = [(ids[k], v[t]) for k, v in channels.items() if t < len(v) and prev.get(k) != v[t]]
        if ch:
            out.append(f"#{t * step}")
            for i, v in ch:
                out.append(f"{v}{i}")
            prev.update({k: v[t] for k, v in channels.items() if t < len(v)})
    end = n + tail_ticks
    if tail_ticks:
        first = next(iter(channels))
        last = prev[first]
        out += [f"#{end * step}", f"{1 - last}{ids[first]}", f"#{(end + 10) * step}", f"{last}{ids[first]}"]
        end += 20
    out.append(f"#{end * step}")
    return "\n".join(out) + "\n"


def decode(text: str, decoder: str, extra: list[str] | None = None) -> list[str]:
    """sigrok-cli's annotation lines for `decoder` (e.g. "usb_power_delivery:cc1=cc")."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "run.vcd"
        p.write_text(text, newline="\n")
        r = subprocess.run([SIGROK, "-I", "vcd", "-i", str(p), "-P", decoder] + (extra or []),
                           capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr
    return [line.split(": ", 1)[1] for line in r.stdout.splitlines() if ": " in line]


def envelope(levels: list[int], gap: int) -> list[int]:
    """An IR receiver module's output: low while carrier pulses arrive (a burst ends after
    `gap` ticks without a rising edge), high otherwise."""
    out, last_rise, prev = [], None, 0
    for t, v in enumerate(levels):
        if v and not prev:
            last_rise = t
        prev = v
        out.append(0 if last_rise is not None and t - last_rise <= gap else 1)
    return out
