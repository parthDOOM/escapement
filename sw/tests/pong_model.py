"""Pong as firmware/vga_pong.asm plays it, written from the rules in that file's header,
for test_vga_pong.py: one step per vertical blank, and the framebuffer it draws.

Rows are counted doubled (row r is tape words 2r and 2r+1), as in the firmware. On odd
frames the paddles move one row: by their buttons, or else, while the ball is in that
paddle's half, towards the row the paddle picked to meet it. On even frames the ball
moves one column and one row, bounces off the borders, and at a paddle's column is hit
(the paddle's top two rows send it up, the bottom two down) or goes on to score. A
16-bit Galois LFSR steps once per ball move and picks serve rows and paddle rows.
"""
M = 0xFFFFFFFF
FONT = [0xF6DE0000, 0x4D2E0000, 0xF39E0000, 0xF3CE0000, 0xB7C80000,
        0xE7CE0000, 0xE7DE0000, 0xF2480000, 0xF7DE0000, 0xF7CE0000]
AIMS = [100, 102, 104, 106, 108, 110, 102, 108]
L_UP, L_DN = 1 << 1, 1 << 2


class Pong:
    def __init__(self):
        self.bx, self.by, self.dx, self.dy = 32, 40, 1, 2
        self.p = [40, 40]
        self.s = [0, 0]
        self.frame = 0
        self.rng = 0xACE1
        self.aim = [104, 104]

    def step(self, buttons: int = 0) -> None:
        """One vertical blank. buttons: the bank B inputs (bits 1, 2: left up, down;
        bits 3, 4: right up, down)."""
        self.frame = (self.frame + 1) & M
        if self.frame & 1:
            for side in (0, 1):
                b = (buttons >> (2 * side)) & (L_UP | L_DN)
                p, move = self.p[side], 0
                if b:
                    move = -2 if b & L_UP else 2
                elif (self.bx ^ (0, 63)[side]) < 32:
                    d = self.by - p + 100
                    if d != self.aim[side]:
                        move = 2 if d > self.aim[side] else -2
                if move < 0 and p != 2:
                    p -= 2
                if move > 0 and p != 82:
                    p += 2
                self.p[side] = p
            return
        lsb = self.rng & 1
        self.rng >>= 1
        if lsb:
            self.rng ^= 0xB400
        ny = self.by + self.dy
        if not 2 <= ny <= 92:
            self.dy = -self.dy
            ny = self.by + self.dy
        self.by = ny
        self.bx += self.dx
        if self.bx in (3, 60):
            side = 0 if self.bx == 3 else 1
            d = self.by - self.p[side]
            if 0 <= d <= 10:
                self.dx = 1 if side == 0 else -1
                if d < 4:
                    self.dy = -2
                elif d >= 8:
                    self.dy = 2
                self.aim[1 - side] = AIMS[self.rng & 7]
        elif self.bx in (0, 63):
            side = 1 if self.bx == 0 else 0           # who scores
            self.s[side] += 1
            self.dx = 1 if side == 0 else -1          # served towards the one who missed
            if self.s[side] == 10:
                self.s = [0, 0]
            self.bx = 32
            self.by = 2 * (self.rng % 32) + 16
            self.aim[1 - side] = AIMS[self.rng & 7]

    def framebuffer(self) -> list[int]:
        """96 words: row r in words 2r (columns 0-31, bit c = column c) and 2r+1."""
        fb = [0] * 96
        for r in range(48):
            fb[2 * r + 1] = 1 if r & 2 else 0                   # net, column 32
        for w in (0, 1, 94, 95):
            fb[w] = M                                           # borders
        for k in range(6):
            fb[self.p[0] + 2 * k] |= 1 << 2                     # paddles, columns 2 and 61
            fb[self.p[1] + 2 * k + 1] |= 1 << 29
        for k in range(5):                                      # scores, rows 2-6
            fb[4 + 2 * k] |= ((FONT[self.s[0]] << 3 * k) & M) >> 29 << 26
            fb[5 + 2 * k] |= ((FONT[self.s[1]] << 3 * k) & M) >> 29 << 4
        fb[self.by + (self.bx >> 5)] |= 1 << (self.bx & 31)
        return fb

    # the tape words the firmware keeps its state in
    def tape(self) -> dict[int, int]:
        words = [self.bx, self.by, self.dx, self.dy, self.p[0], self.p[1], self.s[0],
                 self.s[1], self.frame]
        t = {140 + i: v & M for i, v in enumerate(words)}
        t.update({150: self.rng, 157: self.aim[0], 158: self.aim[1]})
        return t

    def load(self, tape: list[int]) -> None:
        for addr, v in self.tape().items():
            tape[addr] = v
