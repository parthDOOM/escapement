"""EM4100 read-only RFID tag: the 64-bit frame and a reader for the modulator signal.

Written from the EM Microelectronic EM4100 datasheet (2004), not from any firmware: the
memory array is 9 header bits (1), 10 rows of 4 data bits each with an even row parity bit,
4 even column parity bits and a stop bit 0, sent MSB of each row first and repeated while
the tag is powered. Manchester (Fig. 6): a transition in the middle of every bit; with the
modulator control low meaning ON, a 1 is ON then OFF (low, high), a 0 OFF then ON.
"""

from __future__ import annotations

from .board import Device


def frame(ident: int) -> int:
    """The 64-bit array for a 40-bit ID (10 hex digits, the first digit the first row)."""
    nib = [(ident >> (36 - 4 * r)) & 0xF for r in range(10)]
    bits = [1] * 9
    for n in nib:
        row = [(n >> (3 - k)) & 1 for k in range(4)]
        bits += row + [sum(row) & 1]
    col = 0
    for n in nib:
        col ^= n
    bits += [(col >> (3 - k)) & 1 for k in range(4)] + [0]
    assert len(bits) == 64
    return int("".join(map(str, bits)), 2)


def words(ident: int) -> tuple[int, int]:
    """The host words of em4100_tag: bits 63..32, then 31..0."""
    f = frame(ident)
    return f >> 32, f & 0xFFFFFFFF


class Em4100Reader(Device):
    """Decodes IDs from the modulator signal on `pin`: the line is split into half bits of
    `half` ticks (every run of one level must last one or two halves, within +/-10%), half
    pairs are read as Manchester bits at both alignments, and a frame is accepted only with
    its header, every row parity, the column parities and the stop bit right."""

    def __init__(self, pin: int, half: float):
        self.pin, self.half = pin, half
        self.levels: list[int] = []
        self.bad_runs: list[tuple[int, int]] = []

    def step(self, t, bus):
        self.levels.append((bus >> self.pin) & 1)
        return 0, 0

    def _halves(self):
        lv, out, t = self.levels, [], 0
        runs = []
        while t < len(lv):
            s = t
            while t < len(lv) and lv[t] == lv[s]:
                t += 1
            runs.append((s, t - s, lv[s]))
        for s, n, v in runs[1:-1]:                      # the first and last runs are cut
            k = round(n / self.half)
            if k not in (1, 2) or abs(n - k * self.half) > 0.1 * self.half:
                self.bad_runs.append((s, n))
                out.append(None)
                continue
            out += [v] * k
        return out

    def ids(self) -> list[int]:
        h = self._halves()
        found = []
        for align in (0, 1):
            bits = []
            for i in range(align, len(h) - 1, 2):
                a, b = h[i], h[i + 1]
                bits.append(1 if (a, b) == (0, 1) else 0 if (a, b) == (1, 0) else None)
            i = 0
            while i + 64 <= len(bits):
                f = bits[i:i + 64]
                if None in f or f[:9] != [1] * 9:
                    i += 1
                    continue
                rows = [f[9 + 5 * r:14 + 5 * r] for r in range(10)]
                cols = f[59:63]
                ok = all(sum(r[:4]) % 2 == r[4] for r in rows) and f[63] == 0
                ok = ok and all(sum(r[k] for r in rows) % 2 == cols[k] for k in range(4))
                if ok:
                    v = 0
                    for r in rows:
                        for k in range(4):
                            v = v << 1 | r[k]
                    found.append((i * 2 + align, v))
                    i += 64
                else:
                    i += 1
        return [v for _, v in sorted(found)]
