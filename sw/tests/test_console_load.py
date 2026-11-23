"""The DOOM console's load (sw/tools/console_load.py), short: all four contexts as on
the bench for 40 ms, long enough for one whole frame on the panel (about 23 ms of Fast-mode I2C)
and a tic and a half of audio. The tool itself runs 300 ms by default."""

from __future__ import annotations

import importlib.util
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "console_load.py"


def load():
    spec = importlib.util.spec_from_file_location("console_load", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_console_load_short():
    r = load().run(40, 30, 400)
    assert all(s == "CHECKED" for s in r["certs"].values()), r["certs"]
    assert r["frames_on_panel"] >= 1 and r["frames_ok"]
    assert r["audio_ok"] and r["samples_played"] > 300 and not r["pwm_violations"]
    assert r["underrun_after_start"] == 0
    assert not r["monitor_reports"] and not r["oled_violations"]
    assert not r["faults"] and not r["ff"]
    assert r["leads_ok"], r["min_lead_vs_cert"]
    assert r["capture_ok"] and r["capture_rises"] > 0
