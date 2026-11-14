"""A Morse listener for testing a keyer (Recommendation ITU-R M.1677-1). Written from the
Recommendation, not from any firmware: Part 1's characters (the table below), and Part 2's
spacing: a dash is three dots; one dot between the signals of a character, three between
characters, seven between words. Given the dot length, every key-down and every gap is
measured in dots and must be one of those (to TOL); the text is decoded from them."""

from __future__ import annotations

from .board import Device

ITU = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.", "H": "....",
    "I": "..", "J": ".---", "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---", "P": ".--.",
    "Q": "--.-", "R": ".-.", "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..", "1": ".----", "2": "..---", "3": "...--", "4": "....-", "5": ".....",
    "6": "-....", "7": "--...", "8": "---..", "9": "----.", "0": "-----", ".": ".-.-.-", ",": "--..--",
    ":": "---...", "?": "..--..", "'": ".----.", "-": "-....-", "/": "-..-.", "(": "-.--.",
    ")": "-.--.-", '"': ".-..-.", "=": "-...-", "+": ".-.-.", "@": ".--.-.",
}
CODE = {v: k for k, v in ITU.items()}
TOL = 0.01


class MorseListener(Device):
    def __init__(self, pin, tick_ns, dot_ms):
        self.pin, self.tick, self.dot = pin, tick_ns, dot_ms * 1000.0
        self.edges: list[tuple[float, int]] = []
        self.violations: list[str] = []
        self._first = True
        self._k = 0

    def step(self, t, bus):
        k = (bus >> self.pin) & 1
        if self._first:
            self._first, self._k = False, k
        elif k != self._k:
            self._k = k
            self.edges.append((t * self.tick / 1000.0, k))
        return 0, 0

    def _dots(self, us, allowed, what, at):
        n = us / self.dot
        best = min(allowed, key=lambda a: abs(n - a))
        if abs(n - best) > TOL * best:
            self.violations.append(f"{at / 1000:.1f} ms: {what} of {n:.3f} dots")
        return best

    def text(self):
        e = self.edges
        if e and e[0][1] == 0:
            e = e[1:]
        out, sym = [], ""
        for i in range(0, len(e) - 1, 2):
            down, up = e[i][0], e[i + 1][0]
            sym += "." if self._dots(up - down, (1, 3), "key-down", down) == 1 else "-"
            if i + 2 < len(e):
                gap = self._dots(e[i + 2][0] - up, (1, 3, 7), "gap", up)
                if gap >= 3:
                    out.append(CODE.get(sym, "<%s>" % sym))
                    sym = ""
                    if gap == 7:
                        out.append(" ")
        if sym:
            out.append(CODE.get(sym, "<%s>" % sym))
        return "".join(out)
