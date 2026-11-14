"""OLED Pong: firmware/oled_disp.asm (ctx 0), oled_pong.asm (ctx 1), oled_text.asm (ctx 2) and
oled_menu.asm (ctx 3), one program page each, on the ISS at 25 MHz with the SSD1306 model
(escmodels/ssd1306.py, UM10204 Fast-mode checks) on uio[2] SCL, uio[3] SDA. Every picture is
compared, the whole display RAM, with oled_pong_model.py, which draws the screens from the
firmware headers' rules and plays the game with pong_model.Pong.

The firmware steps every TICK_US (60 Hz on the chip); the tests use 3 ms steps, two-step
countdown screens and a four-step winner screen, so a run is short, and the pictures are
the same. STEPS (a tape word)
counts the steps, and when it moves on, everything queued before is drawn: the tests look
at the display then."""

from __future__ import annotations

from pathlib import Path

import pytest

import oled_pong_model as om
from pong_model import Pong

from escasm import assemble, paged
from escert import certify
from escmodels import Board
from escmodels.ssd1306 import Ssd1306
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
FILES = {0: "oled_disp.asm", 1: "oled_pong.asm", 2: "oled_text.asm", 3: "oled_menu.asm"}
FAST = {"TICK_US": 3000, "COUNT": 2, "SHOW": 4}
ESC, START = 0b00001, 0b10000                 # bank B: Esc (ui_in[3]); a start press (ui_in[7])
MS = 25000


def images(pages=(0, 1, 2, 3), **equ):
    over = "".join(f".equ {k}, {v}\n" for k, v in equ.items())
    return {p: assemble(over + (FW / FILES[p]).read_text(), base_dir=FW, name=FILES[p]) for p in pages}


SYM = {k: v for im in images().values() for k, v in im.symbols.items()}


def machine(pages=(0, 1, 2, 3), tape=None, buttons=lambda m: 0, oled=None, off=(), **equ):
    """off: contexts loaded (their tape too) but not started."""
    im = paged(images(pages, **{**FAST, **equ}))
    for k in off:
        im.ctx_enable[k] = False
    for a, w in (tape or {}).items():
        im.tape[a] = w
    oled = oled or Ssd1306(2, 3, 0x3C)
    box = {}
    m = Machine(im, board=Board(oled), board_b=lambda t, ob: buttons(box["m"]) if box else 0)
    box["m"] = m
    return m, oled


def vertical(oled, picture: om.Display | None = None):
    """A display already set up (vertical addressing), holding picture: what the menu leaves
    for a test that starts at the game."""
    a = ("addr", 0x3C << 1, True)
    cmds = [0x00, 0x20, 0x01, 0x21, 0, 127, 0x22, 0, 7]
    oled.transactions.append(["S", a] + [("w", b) for b in cmds] + ["P"])
    if picture:
        data = [picture.ram[p][x] for x in range(128) for p in range(8)]
        oled.transactions.append(["S", a, ("w", 0x40)] + [("w", b) for b in data] + ["P"])


def ram(oled):
    return oled.state()[0]


def show(r):
    return "\n" + "\n".join("".join("#" if r[y >> 3][x] >> (y & 7) & 1 else "." for x in range(128))
                            for y in range(64))


class Watched(list):
    """The ISS's tape, calling back on every write of one word."""
    def __init__(self, words, addr, fn):
        super().__init__(words)
        self.addr, self.fn = addr, fn

    def __setitem__(self, a, w):
        super().__setitem__(a, w)
        if a == self.addr:
            self.fn()


def forget(m):
    """The ISS keeps every cycle's pins and every commit (about 20 MB per simulated ms):
    nothing here reads them, and a menu test runs hundreds of ms."""
    m.pins.clear()
    m.commits.clear()
    m.tape_log.clear()


def run_steps(m, oled, until, limit_ms, chunk=5000, stop=lambda m: False, at_step=None):
    """Run, and at every write of STEPS (the moment a step begins, everything queued before
    it drawn) take the display and the state words; until(snap) is called for each, in
    order, and the run returns when it returns True, or when stop(m) does. until runs
    after the chunk the step began in, so buttons it sets are read a step late;
    at_step(snap) runs at the write itself, before the step reads its buttons."""
    snaps = []
    if not isinstance(m.tape, Watched):
        m.tape = Watched(m.tape, SYM["STEPS"], None)

    def take():
        snap = dict(frame=m.tape[SYM["FRAME"]], phase=m.tape[SYM["PHASE"]], ram=ram(oled))
        snaps.append(snap)
        if at_step:
            at_step(snap)
    m.tape.fn = take
    for _ in range(int(limit_ms * MS / chunk)):
        m.run(chunk, stop_when_idle=False)
        forget(m)
        assert not m.ff.valid, f"fault: {m.ff}"
        while snaps:
            if until(snaps.pop(0)):
                return
        if stop(m):
            return
    raise AssertionError("time limit")


# ------------------------------------------------------------------ static checks
def test_font_is_the_drawings():
    font = images((2,))[2].tape
    for code, ch in enumerate(om.CODES):
        assert font[SYM["FONT"] + code] == sum(om.glyph_column(ch, c) << 8 * c for c in range(4)), ch
    for ch, code in om.EXTRA.items():
        assert SYM["G_" + ch] == code and code != 0xFF
        assert font[code] == sum(om.glyph_column(ch, c) << 8 * c for c in range(4)), ch


def test_files_agree_on_every_shared_name():
    ims = images()
    for name in sorted({k for im in ims.values() for k in im.symbols if k.isupper() or k[0].isupper()}):
        vals = {p: im.symbols[name] for p, im in ims.items() if name in im.symbols}
        assert len(set(vals.values())) == 1, (name, vals)


def test_tape_words_set_once():
    """paged() refuses a tape word two files set differently; none sets another's at all."""
    seen = {}
    for p, im in images().items():
        for a in im.tape_used:
            assert a not in seen, (a, FILES[seen[a]], FILES[p])
            seen[a] = p
    paged(images())


def test_certificate():
    """Every context: the display engine's edges are never late; the others emit none."""
    assert [c.status for c in certify(paged(images()))] == ["CHECKED"] * 4


# ------------------------------------------------------------------ the engine and the text layer
def test_engine_and_text_at_every_scale():
    """Commands put straight into TQ (no game, no menu): fills, a strip, and text at scales
    1 to 4, drawn by ctx 2 and ctx 0 as the model draws them; every byte ACKed in time."""
    S = SYM
    txt = lambda p, s, x, a: 3 << 28 | p << 25 | s << 22 | x << 15 | a       # noqa: E731
    cmds = [1 << 28 | 0 << 25 | 7 << 22 | 0x00,              # clear
            1 << 28 | 6 << 25 | 7 << 22 | 0xA5,              # pages 6-7: 0xA5
            txt(0, 1, 0, 200), txt(1, 2, 3, 202), txt(3, 3, 60, 204), txt(4, 4, 0, 206),
            2 << 28 | 0 << 25 | 1 << 22 | 120 << 15 | 127 << 8 | 210]
    tape = {S["TQ"] + i: c for i, c in enumerate(cmds)}
    tape[S["TQT"]] = len(cmds)
    strings = {200: "PLAYERS", 202: "KEY:", 204: "S", 206: "GO"}
    for a, s in strings.items():
        b = [om.code(c) for c in s] + [0xFF]
        b += [0] * (-len(b) % 4)
        for i in range(0, len(b), 4):
            tape[a + i // 4] = b[i] | b[i + 1] << 8 | b[i + 2] << 16 | b[i + 3] << 24
    tape[210], tape[211] = 0x12345678, 0
    oled = Ssd1306(2, 3, 0x3C)
    vertical(oled)
    m, oled = machine((0, 2), tape=tape, oled=oled)
    for _ in range(40):                                       # up to 200 ms; about 2,300 bytes
        m.run(5 * MS, stop_when_idle=False)
        forget(m)
        if m.tape[S["TQH"]] == m.tape[S["TQT"]] and m.tape[S["DQH"]] == m.tape[S["DQT"]]:
            break
    else:
        raise AssertionError("not drawn")
    d = om.Display()
    d.fill(6, 7, 0xA5)
    d.text(0, 0, 1, "PLAYERS")
    d.text(3, 1, 2, "KEY:")
    d.text(60, 3, 3, "S")
    d.text(0, 4, 4, "GO")
    d.strip(0, 1, 120, 127, 0x12345678)
    assert ram(oled) == d.ram, show(ram(oled)) + show(d.ram)
    assert not oled.violations and m.tape[S["NACKS"]] == 0 and not m.ff.valid


def test_a_display_that_nacks_costs_a_command_not_the_engine():
    """Nobody at 0x3C: every command ends at its address NACK, is counted, and the queue goes
    on (the producer is never stuck behind a missing display)."""
    S = SYM
    cmds = [1 << 28 | 7 << 22, 3 << 28 | 1 << 22 | 200, 4 << 28]
    tape = {S["TQ"] + i: c for i, c in enumerate(cmds)}
    tape.update({S["TQT"]: 3, 200: 0xFFFF0201})
    m, oled = machine((0, 2), tape=tape, oled=Ssd1306(2, 3, 0x3D))
    m.run(2 * MS, stop_when_idle=False)
    assert m.tape[S["TQH"]] == 3 and m.tape[S["DQH"]] == m.tape[S["DQT"]]
    assert m.tape[S["NACKS"]] == 1 + 2 * 5 + 1                # FILL, 2 characters x 5 strips, INIT
    assert not oled.violations and not m.ff.valid


# ------------------------------------------------------------------ the game
def game_run(start: Pong, buttons, steps, scores_until_over=False):
    """The game from `start` (ctx 0-2, PHASE = 1, the court on the display), `steps` steps;
    buttons(k) = the bank B inputs for step k. Each step is compared with the model."""
    S = SYM
    tape = {S["PHASE"]: 1, S["S_SL"]: 0xFF00 | start.s[0], S["S_SR"]: 0xFF00 | start.s[1]}
    t = [0] * 256
    start.load(t)
    tape.update({a: t[a] for a in start.tape()})
    oled = Ssd1306(2, 3, 0x3C)
    vertical(oled, om.court(start))
    model = Pong()
    model.__dict__.update({k: (list(v) if isinstance(v, list) else v) for k, v in start.__dict__.items()})
    m, oled = machine((0, 1, 2), tape=tape, oled=oled,
                      buttons=lambda m: buttons(m.tape[S["FRAME"]]))
    seen = []

    def check(snap):
        k = snap["frame"]
        while model.frame < k:
            model.step(buttons(model.frame + 1))
        assert snap["ram"] == om.court(model).ram, f"step {k}" + show(snap["ram"]) + show(om.court(model).ram)
        seen.append(k)
        return k >= start.frame + steps
    run_steps(m, oled, check, limit_ms=4 * steps + 20, stop=lambda m: m.tape[S["PHASE"]] == 0)
    while model.frame < m.tape[S["FRAME"]]:
        model.step(buttons(model.frame + 1))
    assert not oled.violations and m.tape[S["NACKS"]] == 0
    return m, model, seen


def test_game_steps_match_the_model():
    """A dozen steps from a set position: the right side misses (a point to the left, the
    score redrawn, the serve), the left paddle on its buttons, the right one the chip's."""
    g = Pong()
    g.bx, g.by, g.dx, g.dy, g.p, g.s, g.frame = 61, 40, 1, 2, [40, 2], [3, 5], 1
    press = lambda k: 0b00010 if k < 7 else 0b00100          # noqa: E731  left up, then down
    m, model, seen = game_run(g, press, 14)
    assert seen == list(range(1, 16)), seen
    assert model.s == [4, 5] and model.bx != 61               # the point was scored and served


def test_esc_ends_the_game_at_its_next_paddle_step():
    """Esc (ui_in[3]) from step 5 on: the game is over there, before the paddles move, with
    no point given (the menu then shows no winner), and it waits for the menu."""
    g = Pong()
    g.bx, g.by, g.dx, g.dy, g.p, g.s, g.frame = 20, 40, 1, 2, [40, 40], [3, 5], 1
    m, model, seen = game_run(g, lambda k: ESC if k >= 5 else 0, 20)
    S = SYM
    assert m.tape[S["PHASE"]] == 0 and m.tape[S["FRAME"]] == 5
    assert [m.tape[S["SL"]], m.tape[S["SR"]]] == [3, 5]
    m.run(5 * MS, stop_when_idle=False)
    assert m.tape[S["FRAME"]] == 5


def test_ten_points_end_the_game():
    """The left side's tenth point: PHASE goes back to the menu and the game stops."""
    g = Pong()
    g.bx, g.by, g.dx, g.p, g.s, g.frame = 62, 40, 1, [40, 2], [9, 4], 1
    m, model, seen = game_run(g, lambda k: 0, 4)
    S = SYM
    assert m.tape[S["PHASE"]] == 0 and m.tape[S["SL"]] == 10 and m.tape[S["FRAME"]] == 2
    f = m.tape[S["FRAME"]]
    m.run(5 * MS, stop_when_idle=False)
    assert m.tape[S["FRAME"]] == f                            # the game waits for the menu


# ------------------------------------------------------------------ the menu after a game
def menu_machine(**equ):
    """ctx 0, 2, 3: the menu with no game (its page and tape loaded, its context off); the
    test plays the game's part (PHASE)."""
    b = {"start": False, "esc": False}
    m, oled = machine(buttons=lambda m: (START if b["start"] else 0) | (ESC if b["esc"] else 0),
                      off=(1,), **equ)
    return m, oled, b


def screens(m, oled, done, limit_ms=300, on_snap=lambda seq, counts: None):
    """The screens from now on, each once, and how many steps each was up; on_snap(seq,
    counts) at every step (to set the buttons for it), done(seq, counts) ends the run."""
    seq, counts = [], []

    def f(snap):                                      # at the step, before its buttons
        if not seq or seq[-1] != snap["ram"]:
            seq.append(snap["ram"])
            counts.append(0)
        counts[-1] += 1
        on_snap(seq, counts)
    run_steps(m, oled, lambda snap: done(seq, counts), limit_ms, chunk=1000, at_step=f)
    return seq, counts


def to_the_game(m, oled, b):
    """From reset: a press on the title, the countdown, the court, up to PHASE = 1, with
    the display answering at another address meanwhile, so every command ends at its
    address byte (test_title_countdown_and_game draws these screens); then the display is
    back, set up as the menu leaves it (vertical addressing), blank, and no NACK counted."""
    n = [0]

    def f(snap):
        n[0] += 1
        b["start"] = n[0] >= 2                                # after the first, all-released step
        return False
    oled.addr7 = 0x3D
    run_steps(m, oled, f, 100, chunk=1000, stop=lambda m: m.tape[SYM["PHASE"]] == 1)
    b["start"] = False
    oled.addr7 = 0x3C
    oled.transactions.clear()
    vertical(oled)
    m.tape[SYM["NACKS"]] = 0


def over(m, sl, sr):
    """The game's part: over (PHASE = 0) at sl : sr."""
    S = SYM
    m.tape[S["SL"]], m.tape[S["SR"]], m.tape[S["PHASE"]] = sl, sr, 0


@pytest.mark.parametrize("sl,sr,side", [(10, 7, 0), (3, 10, 1), (10, 0, 0)])
def test_ten_points_show_the_winner_then_the_title(sl, sr, side):
    """A side at ten: the final score, PLAYER 1 or 2, WINS, up for SHOW steps; then the
    title, waiting for a press as at the start."""
    m, oled, b = menu_machine()
    to_the_game(m, oled, b)
    over(m, sl, sr)
    seq, counts = screens(m, oled, lambda seq, c: len(seq) == 2)
    want = [om.winner(side, [sl, sr]), om.title()]
    for i, (got, w) in enumerate(zip(seq, want)):
        assert got == w.ram, f"screen {i}" + show(got) + show(w.ram)
    assert counts[0] == FAST["SHOW"]
    assert not oled.violations and m.tape[SYM["NACKS"]] == 0


def test_over_with_no_winner_is_the_title():
    """The game over below ten (Esc in the game): straight to the title."""
    m, oled, b = menu_machine()
    to_the_game(m, oled, b)
    over(m, 4, 6)
    seq, counts = screens(m, oled, lambda seq, c: True)
    assert seq == [om.title().ram], show(seq[0])


def test_esc_on_the_winner_screen_and_in_the_countdown():
    """Esc cuts a long winner screen short at its first step, and stops a countdown at "3":
    the title both times; the title itself ignores it (a press still starts)."""
    m, oled, b = menu_machine(SHOW=1000)
    to_the_game(m, oled, b)
    over(m, 10, 2)

    def esc_on_first(seq, counts):
        b["esc"] = len(seq) == 1
    seq, counts = screens(m, oled, lambda seq, c: len(seq) == 2, on_snap=esc_on_first)
    assert seq == [om.winner(0, [10, 2]).ram, om.title().ram] and counts[0] == 1

    def esc_on_3(seq, counts):
        title_steps = counts[0] if len(seq) == 1 else 0
        b["esc"] = len(seq) == 2 or (len(seq) == 1 and title_steps == 2)   # on the title too
        b["start"] = len(seq) == 1 and title_steps >= 3
    seq, counts = screens(m, oled, lambda seq, c: len(seq) == 3, on_snap=esc_on_3)
    assert seq == [om.title().ram, om.count("3").ram, om.title().ram], "".join(map(show, seq))
    assert counts[1] == 1
    assert not oled.violations and m.tape[SYM["NACKS"]] == 0


# ------------------------------------------------------------------ the whole thing
def test_title_countdown_and_game():
    """From reset: the title; a press (after all released) starts 3, 2, 1, GO, each drawn
    on its own; then the court of a new game, and its first steps against the model."""
    S = SYM
    press = {"on": False, "esc": False}
    m, oled = machine(buttons=lambda m: (START if press["on"] else 0) | (ESC if press["esc"] else 0))
    seq = []

    n = {"steps": 0}

    def screens(snap):
        n["steps"] += 1
        if not seq or seq[-1] != snap["ram"]:
            seq.append(snap["ram"])
        # the menu looks for "all released" after its first step, then for a press: press
        # from the second step on, while the title is up, and let go when "3" is
        press["on"] = len(seq) == 1 and n["steps"] >= 2
        return snap["phase"] == 1
    run_steps(m, oled, screens, limit_ms=400, chunk=1000)
    want = [om.title(), om.count("3"), om.count("2"), om.count("1"), om.count("GO"), om.court(Pong())]
    for i, (got, w) in enumerate(zip(seq, want)):
        assert got == w.ram, f"screen {i}" + show(got) + show(w.ram)
    assert len(seq) == len(want)
    model = Pong()

    def steps(snap):
        k = snap["frame"]
        while model.frame < k:
            model.step(0)
        assert snap["ram"] == om.court(model).ram, f"step {k}" + show(snap["ram"]) + show(om.court(model).ram)
        return k >= 4
    run_steps(m, oled, steps, limit_ms=60)
    press["esc"] = True                                       # Esc: from the game to the title
    run_steps(m, oled, lambda snap: snap["ram"] == om.title().ram, limit_ms=300, chunk=1000)
    assert m.tape[S["PHASE"]] == 0 and [m.tape[S["SL"]], m.tape[S["SR"]]] == [0, 0]
    assert not oled.violations and m.tape[S["NACKS"]] == 0
