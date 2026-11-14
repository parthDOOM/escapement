"""The OLED Pong pictures as firmware/oled_pong.asm, oled_menu.asm and oled_text.asm describe
them in their headers, for test_oled_pong.py: a 128x64 SSD1306's display RAM (8 pages of 128
bytes, bit 0 of a byte the top row of its page), drawn from the screens' rules, the glyph
drawings below and pong_model.Pong for the game. Nothing here comes from the firmware."""

from __future__ import annotations

from pong_model import Pong

# 4 x 7 glyphs, the top row first
DRAWINGS = {
    "0": [".##.", "#..#", "#..#", "#..#", "#..#", "#..#", ".##."],
    "1": ["..#.", ".##.", "..#.", "..#.", "..#.", "..#.", ".###"],
    "2": [".##.", "#..#", "...#", "..#.", ".#..", "#...", "####"],
    "3": ["###.", "...#", "...#", ".##.", "...#", "...#", "###."],
    "4": ["#..#", "#..#", "#..#", "####", "...#", "...#", "...#"],
    "5": ["####", "#...", "###.", "...#", "...#", "#..#", ".##."],
    "6": [".##.", "#...", "#...", "###.", "#..#", "#..#", ".##."],
    "7": ["####", "...#", "..#.", "..#.", ".#..", ".#..", ".#.."],
    "8": [".##.", "#..#", "#..#", ".##.", "#..#", "#..#", ".##."],
    "9": [".##.", "#..#", "#..#", ".###", "...#", "...#", ".##."],
    " ": ["....", "....", "....", "....", "....", "....", "...."],
    ":": ["....", ".##.", ".##.", "....", ".##.", ".##.", "...."],
    "A": [".##.", "#..#", "#..#", "####", "#..#", "#..#", "#..#"],
    "E": ["####", "#...", "#...", "###.", "#...", "#...", "####"],
    "G": [".##.", "#..#", "#...", "#.##", "#..#", "#..#", ".###"],
    "K": ["#..#", "#..#", "#.#.", "##..", "#.#.", "#..#", "#..#"],
    "L": ["#...", "#...", "#...", "#...", "#...", "#...", "####"],
    "N": ["#..#", "##.#", "##.#", "#.##", "#.##", "#..#", "#..#"],
    "P": ["###.", "#..#", "#..#", "###.", "#...", "#...", "#..."],
    "R": ["###.", "#..#", "#..#", "###.", "#.#.", "#..#", "#..#"],
    "S": [".###", "#...", "#...", ".##.", "...#", "...#", "###."],
    "Y": ["#..#", "#..#", ".##.", ".##.", ".##.", ".##.", ".##."],
    "W": ["#..#", "#..#", "#..#", "#..#", "####", "####", "#..#"],
    "I": [".###", "..#.", "..#.", "..#.", "..#.", "..#.", ".###"],
}
CODES = "0123456789 :AEGKLNPRSY"          # a character's code is its place here; O is 0
EXTRA = {"W": 149, "I": 151}            # later glyphs: the code is the tape word (oled_text.asm)


def code(ch: str) -> int:
    return EXTRA[ch] if ch in EXTRA else CODES.index("0" if ch == "O" else ch)


def glyph_column(ch: str, c: int) -> int:
    """Column c (0..3) of a glyph as a byte, bit r = row r."""
    rows = DRAWINGS["0" if ch == "O" else ch]
    return sum(1 << r for r in range(7) if rows[r][c] == "#")


class Display:
    def __init__(self):
        self.ram = [[0] * 128 for _ in range(8)]

    def fill(self, p0: int, p1: int, v: int) -> None:
        for p in range(p0, p1 + 1):
            self.ram[p] = [v] * 128

    def pixel(self, x: int, y: int) -> None:
        self.ram[y >> 3][x] |= 1 << (y & 7)

    def text(self, x: int, p: int, s: int, string: str) -> None:
        """Each character a cell of 5s x 8s pixels from (x, 8p), every pixel of it written:
        its four glyph columns and a blank one, each pixel an s x s block."""
        for ch in string:
            for gc in range(5):
                g = glyph_column(ch, gc) if gc < 4 else 0
                for dx in range(s):
                    col = x + s * gc + dx
                    for j in range(s):
                        self.ram[p + j][col] = 0
                    for r in range(8):
                        if g >> r & 1:
                            for dy in range(s):
                                self.pixel(col, 8 * p + s * r + dy)
            x += 5 * s

    def strip(self, p0: int, p1: int, x0: int, x1: int, data: int) -> None:
        """Columns x0..x1, pages p0..p1: the same bytes, data's low byte first."""
        for x in range(x0, x1 + 1):
            for i, p in enumerate(range(p0, p1 + 1)):
                self.ram[p][x] = (data >> (8 * i)) & 0xFF

    def frame(self) -> list[int]:
        return [b for page in self.ram for b in page]


def title() -> Display:
    d = Display()
    d.text(34, 1, 3, "PONG")
    d.text(32, 5, 1, "PRESS ANY KEY")
    d.text(24, 7, 1, "KEY 2: 2 PLAYERS")
    return d


def count(word: str) -> Display:
    """A countdown screen: "3", "2", "1" or "GO", centred, scale 4, pages 2..5."""
    d = Display()
    d.text(64 - 10 * len(word) + 2, 2, 4, word)
    return d


def winner(side: int, scores: list[int]) -> Display:
    """The end of a game, side 0 (left) or 1 at ten points: the final score where the game
    showed it, and "PLAYER 1" or "PLAYER 2", "WINS" below, scale 2, centred."""
    d = Display()
    d.text(40, 0, 2, str(scores[0]))
    d.text(80, 0, 2, str(scores[1]))
    d.text(25, 3, 2, "PLAYER")
    d.text(95, 3, 2, str(side + 1))
    d.text(45, 5, 2, "WINS")
    return d


def court(game: Pong) -> Display:
    """The game: the score above (scale 2, columns 40 and 80, pages 0-1), and vga_pong's
    64 x 48 court of cells as 2 x 1 pixels in rows 16..63: both borders, the net in column
    32 (rows 2, 3 of every 4), the paddles in columns 2 and 61 (six rows), the ball."""
    d = Display()
    d.text(40, 0, 2, str(game.s[0]))
    d.text(80, 0, 2, str(game.s[1]))
    for k in range(64):
        for r in range(48):
            on = r in (0, 47) or (k == 32 and r & 2)
            on |= k == 2 and game.p[0] // 2 <= r < game.p[0] // 2 + 6
            on |= k == 61 and game.p[1] // 2 <= r < game.p[1] // 2 + 6
            on |= k == game.bx and r == game.by // 2
            if on:
                d.pixel(2 * k, 16 + r)
                d.pixel(2 * k + 1, 16 + r)
    return d
