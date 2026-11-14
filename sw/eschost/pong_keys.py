"""Play OLED Pong (firmware/oled_*.asm) from a laptop keyboard.

    python -m eschost.pong_keys --bundle oled_pong.json   # the image, for the board
    python -m eschost.pong_keys --port COM8 [--bare]      # play on the chip
    python -m eschost.pong_keys --sim                     # play the model, no chip

On the chip: the board runs sw/rp2350/oled_pong_host.py (this starts it: --bare for a bare
Pico wired to an FPGA build, else the TT demo board), and a small window here takes the keys
(a terminal cannot see a key being let go). The chip only sees five buttons, ui_in[3..7]:
Esc, left paddle up, down, right paddle up, down; a paddle nobody presses is played by the
chip.

Keys: 1 player (the start): W / Up move the left paddle up, S / Down down; the chip plays
the right. 2 players: press 2 (on the title, it is the start press too): W / S the left
paddle, Up / Down the right; 1 goes back to one player. Esc goes back to the title (from
the countdown, a game, or the winner screen). Any other key is a start press (the left
paddle's up button, held while the key is). A game ends at ten points with the winner's
screen, then the title.

--sim plays oled_pong_model.py and pong_model.py (the tests' references, which the firmware
is checked against picture by picture) at 60 Hz in the window: the same screens and game as
the chip, but not the firmware itself (the instruction-set simulator is about 500 times
slower than the chip).
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ESC, L_UP, L_DN, R_UP, R_DN = 1, 2, 4, 8, 16         # ui_in[3..7] (bank B pins 0..4)
BTNS = L_UP | L_DN | R_UP | R_DN


class Keys:
    def __init__(self):
        self.down: set[str] = set()
        self.two = False

    def press(self, key: str) -> None:
        if key == "2":
            self.two = True
        elif key == "1":
            self.two = False
        self.down.add(key)

    def release(self, key: str) -> None:
        self.down.discard(key)

    def buttons(self) -> int:
        b = 0
        for k in self.down:
            if k == "Escape":
                b |= ESC
            elif self.two:
                b |= {"w": L_UP, "s": L_DN, "Up": R_UP, "Down": R_DN, "2": R_UP}.get(k, L_UP)
            else:
                b |= {"w": L_UP, "Up": L_UP, "s": L_DN, "Down": L_DN}.get(k, L_UP)
        if b & L_UP and b & L_DN:                    # both: the firmware takes up
            b &= ~L_DN
        if b & R_UP and b & R_DN:
            b &= ~R_DN
        return b


def bundle(path: Path) -> None:
    sys.path.insert(0, str(ROOT / "sw"))
    from escasm import assemble, paged
    fw = ROOT / "firmware"
    files = {0: "oled_disp.asm", 1: "oled_pong.asm", 2: "oled_text.asm", 3: "oled_menu.asm"}
    im = paged({p: assemble((fw / f).read_text(), base_dir=fw, name=f) for p, f in files.items()})
    d = json.loads(im.to_json())
    d.pop("symbols", None)
    path.write_text(json.dumps(d))
    print(f"wrote {path}: {sum(1 for w in im.words if w)} program words in 4 pages, "
          f"{len(im.tape_used)} tape words")


class Sim:
    """The menu and the game as the firmware headers describe them, one step per call."""
    COUNT, SHOW = 60, 240

    def __init__(self):
        sys.path.insert(0, str(ROOT / "sw" / "tests"))
        import oled_pong_model as om
        from pong_model import Pong
        self.om, self.Pong = om, Pong
        self.rng = Pong().rng
        self.title()

    def title(self):
        self.state, self.waiting_release, self.screen = "title", True, self.om.title()

    def step(self, b: int) -> None:
        if self.state == "title":
            if self.waiting_release:
                self.waiting_release = bool(b & BTNS)
            elif b & BTNS:
                self.state, self.n, self.left = "count", 0, self.COUNT
                self.screen = self.om.count("3")
        elif self.state == "won":
            self.left -= 1
            if b & ESC or not self.left:
                self.title()
        elif b & ESC and (self.state == "count" or self.game.frame & 1 == 0):
            self.title()                             # the game looks on its paddle steps
        elif self.state == "count":
            self.left -= 1
            if not self.left:
                self.n, self.left = self.n + 1, self.COUNT
                if self.n < 4:
                    self.screen = self.om.count(("3", "2", "1", "GO")[self.n])
                else:
                    self.game = self.Pong()
                    self.game.rng = self.rng
                    self.state, self.screen = "game", self.om.court(self.game)
        else:
            before = list(self.game.s)
            self.game.step(b)
            self.rng = self.game.rng
            if 9 in before and self.game.s == [0, 0]:  # ten points (pong_model starts again)
                side = 0 if self.game.dx == 1 else 1     # served towards the one who missed
                before[side] += 1
                self.state, self.left = "won", self.SHOW
                self.screen = self.om.winner(side, before)
            else:
                self.screen = self.om.court(self.game)


def window(keys: Keys, title: str, on_buttons=None, sim: Sim | None = None, status=None):
    import tkinter as tk
    root = tk.Tk()
    root.title(title)
    lit, dark, scale = "#9be7ff", "#000000", 5
    small = tk.PhotoImage(width=128, height=64)
    label = tk.Label(root, bg=dark)
    label.pack()
    text = tk.StringVar(value="1 player: W/S or arrows. 2 players: press 2, then W/S and arrows. Esc: title.")
    tk.Label(root, textvariable=text, anchor="w", justify="left").pack(fill="x")
    last = [None]

    def changed():
        b = keys.buttons()
        if b != last[0]:
            last[0] = b
            if on_buttons:
                on_buttons(b)

    root.bind("<KeyPress>", lambda e: (keys.press(e.keysym if len(e.keysym) > 1 else e.keysym.lower()), changed()))
    root.bind("<KeyRelease>", lambda e: (keys.release(e.keysym if len(e.keysym) > 1 else e.keysym.lower()), changed()))

    def draw(ram):
        rows = []
        for y in range(64):
            rows.append("{" + " ".join(lit if ram[y >> 3][x] >> (y & 7) & 1 else dark
                                       for x in range(128)) + "}")
        small.put(" ".join(rows))
        big = small.zoom(scale)
        label.configure(image=big)
        label.image = big

    def tick():
        if sim:
            sim.step(keys.buttons())
            draw(sim.screen.ram)
        mode = "2 players" if keys.two else "1 player"
        text.set(f"{mode}; buttons {keys.buttons():05b} (ui_in[7:3])" + (f"   {status()}" if status else ""))
        root.after(16, tick)

    if not sim:
        label.configure(text="keys go to the chip: watch the OLED", fg=lit, width=60, height=6)
    tick()
    root.mainloop()


def chip(port: str, bare: bool) -> None:
    import serial                                    # pyserial
    s = serial.Serial(port, 115200, timeout=0.1)
    s.write(b"\r\x03\x03")                           # stop whatever runs
    s.reset_input_buffer()
    start = ("import barepico, oled_pong_host; oled_pong_host.run(barepico.open_io())" if bare
             else "import oled_pong_host; oled_pong_host.run()")
    s.write(start.encode() + b"\r")
    lines = ["starting"]

    def reader():
        buf = b""
        while s.is_open:
            try:
                buf += s.read(64)
            except Exception:
                return
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                t = line.decode(errors="replace").strip()
                if t:
                    lines.append(t)
                    print(t)
    threading.Thread(target=reader, daemon=True).start()
    try:
        window(Keys(), "Pong keys (to the chip)", on_buttons=lambda b: s.write(bytes((0xC0 | b,))),
               status=lambda: lines[-1][:60])
    finally:
        s.write(b"\xaa")
        s.close()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--bundle", type=Path, help="write the image (JSON) for the board")
    g.add_argument("--port", help="the board's USB serial port: play on the chip")
    g.add_argument("--sim", action="store_true", help="play the model in a window")
    ap.add_argument("--bare", action="store_true", help="a bare Pico wired to an FPGA build")
    a = ap.parse_args(argv)
    if a.bundle:
        bundle(a.bundle)
    elif a.port:
        chip(a.port, a.bare)
    else:
        window(Keys(), "Pong (the model, not the chip)", sim=Sim())


if __name__ == "__main__":
    main()
