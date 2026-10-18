"""Capture files, and conversion to and from the formats logic-analyser tools read.

A capture file (`.json`) keeps the record words exactly as the chip wrote them, in time
order, with the clock they were taken at:

    {"format": "escapement-capture", "version": 1, "clock_hz": 25000000,
     "first_epoch": 0, "names": ["pin0", ...], "words": ["0x0c000000", ...]}

From records ((absolute tick, levels) pairs, see decode_records) this module writes

- VCD (IEEE 1364 value change dump): one change per record, no resampling; PulseView,
  GTKWave and sigrok-cli read it.
- sigrok session files (`.sr`, srzip version 2): the layout libsigrok's srzip output
  writes and its session loader reads (a `version` entry "2", a GLib key-file
  `metadata`, raw samples in `logic-1`, one byte per sample, channel n in bit n). The
  records are expanded into samples at clock_hz / div.

and reads both back, so any trace a tool can save can be replayed (build_replay).
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

FORMAT = "escapement-capture"
CLOCK_HZ = 25_000_000
MAX_SAMPLES = 1 << 28                 # 256 MiB of samples: refuse rather than exhaust memory


def default_names(n: int = 8) -> list[str]:
    return [f"pin{i}" for i in range(n)]


# --------------------------------------------------------------------------- capture files

def save_capture(path, words, clock_hz=CLOCK_HZ, first_epoch=0, names=None) -> None:
    Path(path).write_text(json.dumps({
        "format": FORMAT, "version": 1, "clock_hz": clock_hz, "first_epoch": first_epoch,
        "names": list(names or default_names()),
        "words": [f"0x{w & 0xFFFFFFFF:08x}" for w in words]}, indent=1) + "\n")


def load_capture(path) -> dict:
    d = json.loads(Path(path).read_text())
    if d.get("format") != FORMAT:
        raise ValueError(f"{path}: not an Escapement capture file")
    d["words"] = [int(w, 16) for w in d["words"]]
    d.setdefault("names", default_names())
    return d


# --------------------------------------------------------------------------- VCD

_IDS = "!\"#$%&'("


def _timescale(clock_hz: float) -> tuple[str, int]:
    """A VCD timescale and the clock period in its units (an integer)."""
    for unit, per_s in (("ns", 1e9), ("ps", 1e12), ("fs", 1e15)):
        p = per_s / clock_hz
        if abs(p - round(p)) < 1e-9:
            return f"1 {unit}", int(round(p))
    raise ValueError(f"clock {clock_hz} Hz has no whole-femtosecond period")


def write_vcd(records, out, clock_hz=CLOCK_HZ, names=None, pins=0xFF) -> None:
    """One VCD change per record that changes a pin in `pins`, at tick x clock period."""
    names = list(names or default_names())
    sel = [i for i in range(8) if (pins >> i) & 1]
    scale, period = _timescale(clock_hz)
    lines = ["$version Escapement esctape $end", f"$timescale {scale} $end",
             "$scope module escapement $end"]
    lines += [f"$var wire 1 {_IDS[i]} {re.sub(r'[^A-Za-z0-9_.]', '_', names[i])} $end" for i in sel]
    lines += ["$upscope $end", "$enddefinitions $end"]
    prev = None
    for t, lv in records:
        if prev is None:
            lines += [f"#{t * period}", "$dumpvars"]
            lines += [f"{(lv >> i) & 1}{_IDS[i]}" for i in sel] + ["$end"]
        elif (lv ^ prev) & pins:
            lines.append(f"#{t * period}")
            lines += [f"{(lv >> i) & 1}{_IDS[i]}" for i in sel if ((lv ^ prev) >> i) & 1]
        else:
            continue
        prev = lv
    Path(out).write_text("\n".join(lines) + "\n")


_UNITS = {"s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9, "ps": 1e-12, "fs": 1e-15}


def read_vcd(path, clock_hz=CLOCK_HZ, signals=None) -> tuple[list[tuple[int, int]], list[str]]:
    """Records from a VCD: bit i of the levels is the i-th 1-bit signal, in the order of
    `signals` (names) or of their declaration (at most 8). Simultaneous changes form one
    record; x and z read as 0. Returns (records, names)."""
    text = Path(path).read_text()
    head, _, body = text.partition("$enddefinitions")
    m = re.search(r"\$timescale\s+(\d+)\s*([a-z]+)\s+\$end", head)
    tick_s = (int(m.group(1)) * _UNITS[m.group(2)]) if m else 1e-9
    decl = re.findall(r"\$var\s+\S+\s+(\d+)\s+(\S+)\s+(\S+)(?:\s+\[[^\]]*\])?\s+\$end", head)
    ones = [(code, name) for width, code, name in decl if width == "1"]
    if signals:
        by_name = {name: code for code, name in ones}
        missing = [s for s in signals if s not in by_name]
        if missing:
            raise ValueError(f"{path}: no 1-bit signal {missing}")
        chosen = [(by_name[s], s) for s in signals]
    else:
        chosen = ones[:8]
    if not chosen or len(chosen) > 8:
        raise ValueError(f"{path}: need 1 to 8 one-bit signals, found {len(chosen)}")
    bit = {code: i for i, (code, _) in enumerate(chosen)}
    records, lv, t, pending = [], 0, None, False
    for tok in body.split()[1:]:            # [0] is the "$end" closing $enddefinitions
        if tok[0] == "#":
            if pending:
                records.append((t, lv))
            t, pending = round(int(tok[1:]) * tick_s * clock_hz), False
        elif tok[0] in "01xXzZ" and tok[1:] in bit:
            b = bit[tok[1:]]
            lv = (lv & ~(1 << b)) | ((tok[0] == "1") << b)
            pending = True
    if pending:
        records.append((t, lv))
    out, prev = [], None                    # keep only records that change something
    for t, v in records:
        if v != prev:
            out.append((t, v))
            prev = v
    return out, [name for _, name in chosen]


# --------------------------------------------------------------------------- sigrok .sr

def _rate_string(hz: int) -> str:
    """As libsigrok's sr_samplerate_string; read back by sr_parse_sizestring."""
    for div, unit in ((1_000_000_000, "GHz"), (1_000_000, "MHz"), (1_000, "kHz")):
        if hz % div == 0:
            return f"{hz // div} {unit}"
    return f"{hz} Hz"


def _parse_rate(s: str) -> float:
    m = re.fullmatch(r"\s*([\d.]+)\s*([kMG]?)(?:Hz)?\s*", s)
    if not m:
        raise ValueError(f"samplerate {s!r}")
    return float(m.group(1)) * {"": 1, "k": 1e3, "M": 1e6, "G": 1e9}[m.group(2)]


def write_sr(records, out, clock_hz=CLOCK_HZ, div=1, names=None, end=None) -> int:
    """A sigrok session file sampled every `div` ticks from the first record to `end`
    (default: one sample past the last record, so it shows). Returns the sample count."""
    if not records:
        raise ValueError("no records")
    names = list(names or default_names())
    t0 = records[0][0]
    end = records[-1][0] + div if end is None else end
    n = (end - t0 + div - 1) // div
    if n > MAX_SAMPLES:
        raise ValueError(f"{n} samples: raise div (each sample covers div ticks)")
    buf = bytearray()
    for (t, lv), nxt in zip(records, records[1:] + [(end, 0)]):
        k0, k1 = (t - t0 + div - 1) // div, min(n, (nxt[0] - t0 + div - 1) // div)
        if k1 > k0:
            buf += bytes((lv & 0xFF,)) * (k1 - k0)
    meta = ["[global]", "sigrok version=0.5.2", "", "[device 1]", "capturefile=logic-1",
            "total probes=8", f"samplerate={_rate_string(clock_hz // div)}", "total analog=0"]
    meta += [f"probe{i + 1}={names[i]}" for i in range(8)] + ["unitsize=1", ""]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("version", "2")
        z.writestr("metadata", "\n".join(meta))
        z.writestr("logic-1", bytes(buf))
    return n


def read_sr(path, clock_hz=CLOCK_HZ) -> tuple[list[tuple[int, int]], list[str]]:
    """Records from a sigrok session file (logic channels 1-8 of device 1): one record per
    sample that changes them, at tick = sample x clock_hz / samplerate. (records, names)"""
    with zipfile.ZipFile(path) as z:
        if z.read("version").strip() not in (b"1", b"2"):
            raise ValueError(f"{path}: unsupported session version")
        meta, section = {}, None
        for line in io.StringIO(z.read("metadata").decode()):
            line = line.strip()
            if line.startswith("["):
                section = line.strip("[]")
            elif "=" in line and section == "device 1":
                k, v = line.split("=", 1)
                meta[k.strip()] = v.strip()
        base, unit = meta["capturefile"], int(meta.get("unitsize", "1"))
        rate = _parse_rate(meta["samplerate"])
        names_present = set(z.namelist())
        if base in names_present:
            data = z.read(base)
        else:
            data, k = b"", 1
            while f"{base}-{k}" in names_present:
                data += z.read(f"{base}-{k}")
                k += 1
    names = [meta.get(f"probe{i + 1}", f"D{i}") for i in range(8)]
    records, prev = [], None
    for k in range(0, len(data) - unit + 1, unit):
        v = data[k]
        if v != prev:
            records.append((round(k // unit * clock_hz / rate), v))
            prev = v
    return records, names


def read_trace(path, clock_hz=CLOCK_HZ, signals=None):
    """Records and names from a .vcd, .sr or Escapement capture .json file."""
    from . import decode_records
    p = Path(path)
    if p.suffix == ".vcd":
        return read_vcd(p, clock_hz, signals)
    if p.suffix == ".sr":
        return read_sr(p, clock_hz)
    d = load_capture(p)
    return decode_records(d["words"], d.get("first_epoch", 0)), d["names"]
