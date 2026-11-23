"""The DOOM console's load on the ISS (docs/25-DOOM-CONSOLE.md): all four contexts loaded as on the bench,
fed by a host that behaves like the real one, and every claim checked from outside the chip.

    python tools/console_load.py [--ms 300] [--fps 30] [--gap 400] [--jobs 1]

Image (one page each): 0 i2c_burst streaming frames to an SSD1306 (SCL pin 2, SDA pin 3, Fast
mode); 1 pwm_audio on pin 6; 2 i2c_timing, the bus timing monitor on SCL/SDA with Fast-mode limits;
3 logic_analyzer, the capture unit on the audio pin in ring mode. Tape: the audio ring 0x00-0x7F
and its variables 0x80-0x82, the capture window 0x88-0xDF, the monitor's rules 0xE0-0xE7, the
stream's word 0xFF.

Host: a frame rendered every 1/fps s (a new one dropped, as a game drops it, while the last is
still going out), sent as one stream of 256 words; audio queued a game tic (1/35 s) at a time,
11,025 samples a second, with one tic queued ahead (as a game mixes ahead of its output, and the
46 ms ring holds more than a tic); every push only into room (the mailboxes hold 2 words), `gap` ticks
apiece on the host link (an SPI MBX_PUSH at f_clk / 8 is about 400 ticks). Checks, each from
outside the chip: every frame that went out is on the panel byte for byte; the audio pin decodes
to exactly the samples sent, on its schedule to the tick, with no underrun once playing; the bus
monitor reports nothing; no context faults; each context's min_lead is at or above its
certificate; the capture window's edges fall on the receiver's ticks; and how much of the host
link the load takes.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SW = HERE.parent
REPO = SW.parent
sys.path.insert(0, str(SW))

from escasm import assemble, paged  # noqa: E402
from escert import certify  # noqa: E402
from esciss import Machine  # noqa: E402
from escmodels import Board, HostFeed  # noqa: E402
from escmodels.pwm_audio import PwmAudioReceiver  # noqa: E402
from escmodels.ssd1306 import Ssd1306  # noqa: E402

FW = REPO / "firmware"
KHZ = 25000
RATE = 11025
SCL, SDA, AUDIO = 2, 3, 6
CAP_BASE, CAP_LIM = 0x88, 0xE0
PAGES = {
    0: ("i2c_burst.asm", {"CTX": 0, "SCL_PIN": SCL, "SDA_PIN": SDA, "CLK_KHZ": KHZ, "STW": 0xFF}),
    1: ("pwm_audio.asm", {"CTX": 1, "PIN": AUDIO, "CLK_KHZ": KHZ, "RATE": RATE, "RING": 0x00,
                          "VARS": 0x80}),
    2: ("i2c_timing.asm", {"CTX": 2, "SCL": SCL, "SDA": SDA, "CLK_KHZ": KHZ, "THIGH_NS": 600,
                           "TLOW_NS": 1300, "TSU_NS": 100, "TPER_NS": 2500, "TOUT_US": 2600,
                           "TB": 0xE0}),
    3: ("logic_analyzer.asm", {"CTX": 3, "MASK": 1 << AUDIO, "BASE": CAP_BASE, "LIM": CAP_LIM,
                               "RING": 1}),
}
def cmd(*b):
    w = len(b) << 24
    for i, x in enumerate(b):
        w |= x << (16 - 8 * i)
    return w


SETUP = [cmd(0x00, 0x20, 0x00), cmd(0x00, 0x21, 0), cmd(0x00, 127), cmd(0x00, 0x22, 0), cmd(0x00, 7)]


def page(fw: str, equ: dict):
    mod = fw.rsplit(".", 1)[0]
    src = "".join(f".equ {mod}.{k}, {v}\n" for k, v in equ.items()) + f'.include "{fw}"\n'
    return assemble(src, base_dir=FW, name=fw)


def console_image():
    pages = {p: page(fw, equ) for p, (fw, equ) in PAGES.items()}
    return paged(pages), pages


class Game(HostFeed):
    """HostFeed driven by a game loop: frames and audio tics appear over time."""

    def __init__(self, fps: float, gap: int, seed: int = 1):
        super().__init__({0: list(SETUP), 1: []}, gap=gap, drain=[0, 2], poll=1000, start=2000)
        self.rng = random.Random(seed)
        self.frame_ticks = round(KHZ * 1000 / fps)
        self.tic_ticks = round(KHZ * 1000 / 35)
        self.next_frame, self.next_tic = 20_000, 2_000
        self.ahead = 1                              # tics of audio queued before the first plays
        self.frames: list[list[int]] = []        # every frame queued, in order
        self.dropped = 0
        self.samples: list[int] = []
        self._carry = 0.0

    def step(self, t, bus):
        if t >= self.next_frame:
            self.next_frame += self.frame_ticks
            if self.queues[0]:
                self.dropped += 1                   # the last frame is still going out
            else:
                f = [self.rng.getrandbits(32) for _ in range(256)]
                self.frames.append(f)
                self.queues[0] += [3 << 30 | 0x40 << 16 | 256] + f
        while t >= self.next_tic:
            if self.ahead:
                self.ahead -= 1                     # the next tic at once: one tic of lead
            else:
                self.next_tic += self.tic_ticks
            self._carry += RATE / 35
            n = int(self._carry) // 4 * 4
            self._carry -= n
            s = [self.rng.randrange(256) for _ in range(n)]
            if not self.samples:
                s[0] = s[0] if s[0] != 128 else 1   # the first sample marks the start
            self.samples += s
            self.queues[1] += [s[i] << 24 | s[i + 1] << 16 | s[i + 2] << 8 | s[i + 3]
                               for i in range(0, n, 4)]
        return super().step(t, bus)


def run(ms: float, fps: float, gap: int) -> dict:
    image, pages = console_image()
    certs = {c.ctx: c for p in pages.values() for c in certify(p)}
    oled = Ssd1306(SCL, SDA, 0x3C)
    rx = PwmAudioReceiver(AUDIO, clk_khz=KHZ, rate=RATE)
    host = Game(fps, gap)
    board = Board(oled, rx, host, record=False)
    m = Machine(image, board=board, record=False)
    host.machine = m
    ticks = int(ms * KHZ)
    t0 = time.time()
    m.run(ticks, stop_when_idle=False)
    wall = time.time() - t0

    # frames: every stream transaction's data bytes against the frame queued for it
    done = [tr for tr in oled.transactions if tr and tr[-1] == "P"]
    streams = [[x[1] for x in tr if isinstance(x, tuple) and x[0] == "w"][1:]
               for tr in done if len(tr) > 1000]
    sent = [[(w >> s) & 0xFF for w in f for s in (24, 16, 8, 0)] for f in host.frames]
    frames_ok = bool(streams) and all(a == b for a, b in zip(streams, sent)) and len(streams) >= len(sent) - 1
    # audio: what the pin decodes to, against what was sent
    got = rx.samples
    a = got.index(host.samples[0]) if host.samples and host.samples[0] in got else None
    played = got[a:] if a is not None else []
    n = min(len(played), len(host.samples))
    audio_ok = a is not None and played[:n] == host.samples[:n]
    gaps = [i for i, (x, y) in enumerate(zip(played[:n], host.samples[:n])) if x != y][:5]
    # the capture window: each record is {levels[7:0], tick[23:0]}; the rising edges it holds
    # must sit at the receiver's rise ticks, all at one offset (the input side's delay)
    edges = {r & 0xFFFFFF for r in rx.rises}
    rises_cap = sorted(m.tape[w] & 0xFFFFFF for w in range(CAP_BASE, CAP_LIM)
                       if m.tape[w] and m.tape[w] >> 24 >> AUDIO & 1)
    offs = {min(((c - d) & 0xFFFFFF for d in edges), key=lambda x: min(x, 0x1000000 - x))
            for c in rises_cap[:4]} if rises_cap else set()
    off = offs.pop() if len(offs) == 1 else None
    cap_ok = off is not None and all(((c - off) & 0xFFFFFF) in edges for c in rises_cap)
    # min_lead 0xFFFF: no event queued (the instruments queue none), nothing to compare
    leads = {k: (None if m.ctx[k].min_lead == 0xFFFF else m.ctx[k].min_lead, certs[k].min_lead)
             for k in certs if certs[k].status == "CHECKED"}
    link = len(host.pushed) * gap / ticks
    return {
        "ticks": ticks, "wall_s": round(wall, 1),
        "frames_queued": len(host.frames), "frames_on_panel": len(streams), "frames_dropped": host.dropped,
        "fps_out": round(len(streams) / (ms / 1000), 1), "frames_ok": frames_ok,
        "transactions_complete": len(done),
        "samples_sent": len(host.samples), "samples_played": n, "audio_ok": audio_ok,
        # UNDER counts the silences the ring's emptiness caused; the very first sample is the
        # program's own starting silence (before any fetch), not one of them
        "underrun_after_start": m.tape[0x82] - ((a or 1) - 1), "first_mismatches": gaps, "pwm_violations": rx.violations[:3],
        "monitor_reports": host.received[2][:6], "burst_results": host.received[0][-3:],
        "oled_violations": oled.violations[:3],
        "faults": [k for k in range(4) if m.ctx[k].state == 3], "ff": m.ff.valid,
        "min_lead_vs_cert": leads, "leads_ok": all(ml is None or ml >= c for ml, c in leads.values()),
        "capture_rises": len(rises_cap), "capture_offset": off, "capture_ok": cap_ok,
        "pushes": len(host.pushed), "link_busy": round(link, 3),
        "certs": {k: c.status for k, c in certs.items()},
    }


def main() -> int:
    ap = argparse.ArgumentParser(prog="console_load")
    ap.add_argument("--ms", type=float, default=300)
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--gap", type=int, default=400)
    a = ap.parse_args()
    r = run(a.ms, a.fps, a.gap)
    for k, v in r.items():
        print(f"{k:24s} {v}")
    ok = (r["frames_ok"] and r["audio_ok"] and not r["pwm_violations"] and not r["monitor_reports"]
          and not r["faults"] and not r["ff"] and r["leads_ok"] and r["capture_ok"]
          and r["underrun_after_start"] == 0 and not r["oled_violations"]
          and all(s == "CHECKED" for s in r["certs"].values()))
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
