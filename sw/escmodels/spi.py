"""SPI target (peripheral) model, modes 0-3, MSB first, with a waveform checker."""

from __future__ import annotations

from .board import Device


class SpiTarget(Device):
    """Samples MOSI and shifts MISO according to CPOL/CPHA while CS_n is low.

    Responds with `reply` bytes (cycled). Records received bytes per CS frame.
    Checks: SCK idle level == CPOL whenever CS_n changes; MOSI stable at each
    sampling edge for `setup` ticks; no SCK edges while CS_n is high.
    """

    def __init__(self, sck: int, mosi: int, miso: int, cs: int, mode: int,
                 reply: bytes = b"\xA5", setup: int = 2):
        self.sck, self.mosi, self.miso, self.cs = sck, mosi, miso, cs
        self.cpol, self.cpha = (mode >> 1) & 1, mode & 1
        self.reply, self.setup = reply, setup
        self.frames: list[list[int]] = []
        self.violations: list[str] = []
        self._p = None
        self._bits = 0
        self._rx = 0
        self._tx = 0
        self._ridx = 0
        self._miso = 1
        self._t_mosi = -10**9
        self._load()              # first reply byte; then one more per completed byte

    def _load(self):
        self._tx = self.reply[self._ridx % len(self.reply)]
        self._ridx += 1

    def step(self, t, bus):
        sck = (bus >> self.sck) & 1
        cs = (bus >> self.cs) & 1
        mosi = (bus >> self.mosi) & 1
        if self._p is None:
            self._p = (sck, cs, mosi)
        psck, pcs, pmosi = self._p
        if mosi != pmosi:
            self._t_mosi = t
        if pcs == 1 and cs == 0:                       # frame start
            if sck != self.cpol:
                self.violations.append(f"t={t}: SCK={sck} at CS fall, CPOL={self.cpol}")
            self.frames.append([])
            self._bits, self._rx = 0, 0
            if self.cpha == 0:
                self._miso = (self._tx >> 7) & 1
        elif pcs == 0 and cs == 1:
            if sck != self.cpol:
                self.violations.append(f"t={t}: SCK={sck} at CS rise, CPOL={self.cpol}")
        elif cs == 0 and sck != psck:
            leading = sck != self.cpol
            sample_edge = leading if self.cpha == 0 else not leading
            if sample_edge:
                if t - self._t_mosi < self.setup:
                    self.violations.append(f"t={t}: MOSI changed {t - self._t_mosi} ticks before sample")
                self._rx = ((self._rx << 1) | pmosi) & 0xFF
                self._bits += 1
                if self._bits == 8:
                    self.frames[-1].append(self._rx)
                    self._bits, self._rx = 0, 0
                    self._load()
                    if self.cpha == 0:
                        self._miso = (self._tx >> 7) & 1
            else:
                # shift edge: present the next bit
                if self.cpha == 0:
                    self._miso = (self._tx >> (7 - self._bits)) & 1 if self._bits else self._miso
                else:
                    self._miso = (self._tx >> (7 - self._bits)) & 1
        self._p = (sck, cs, mosi)
        m = 1 << self.miso
        if cs == 0:
            return (0, m) if self._miso else (m, 0)
        return 0, 0

    def errors(self):
        return [f"SPI: {v}" for v in self.violations[:10]]


class SpiController(Device):
    """Scripted SPI controller (bus master): drives SCK, MOSI and CS_n push-pull, samples
    MISO, and checks the TARGET's MISO timing. Written from the mode definitions alone:

      CPOL    SCK idle level.
      CPHA=0  data is sampled on the leading edge (idle -> active) and changes on the
              trailing edge; the first MOSI bit is set up when CS_n falls.
      CPHA=1  data changes on the leading edge and is sampled on the trailing edge.
      MSB first, 8-bit bytes, full duplex.

    `frames` is a list of (mosi_bytes, expect) or (mosi_bytes, expect, abort_bits), one
    CS_n-low frame each. `expect` gives the MISO byte expected for each MOSI byte (None =
    don't care), or is None. With abort_bits, CS_n rises after that many bits.

    Timing in ticks: t_high / t_low (SCK phases: an int, a per-bit list, or a callable of
    the bit index), t_css (CS_n fall to the first edge), t_csh (last edge to CS_n rise),
    t_idle (CS_n high between frames), byte_gap (extra idle-SCK time between bytes).
    Checks: MISO holds one level from t_su ticks before each sampling edge to t_ho ticks
    after it, and every received byte matches `expect`.

    Results: `rx` (MISO bytes per frame, a cut-short last byte holds its bits so far),
    `samples` (tick of every sampling edge), `cs_fall` / `cs_rise` (ticks of the CS_n
    edges, one per frame).
    """

    def __init__(self, sck: int, mosi: int, miso: int, cs: int, frames, mode: int = 0,
                 t_high=32, t_low=32, t_css: int = 32, t_csh: int = 32, t_idle: int = 64,
                 byte_gap: int = 0, t_su: int = 2, t_ho: int = 2, start_at: int = 100):
        self.sck, self.mosi, self.miso, self.cs = sck, mosi, miso, cs
        self.cpol, self.cpha = (mode >> 1) & 1, mode & 1
        self.frames = [tuple(f) for f in frames]
        self.t_high, self.t_low = t_high, t_low
        self.t_css, self.t_csh, self.t_idle = t_css, t_csh, t_idle
        self.byte_gap, self.t_su, self.t_ho = byte_gap, t_su, t_ho
        self.start_at = start_at
        self.rx: list[list[int]] = []
        self.samples: list[int] = []
        self.cs_fall: list[int] = []
        self.cs_rise: list[int] = []
        self.violations: list[str] = []
        self.done = False
        self.t = -1
        self._sck, self._mosi, self._cs = self.cpol, 1, 1
        self._miso: dict[int, int] = {}                  # MISO level per cycle
        self._checks: list[tuple[int, int, int]] = []    # (sampling cycle, frame, byte)
        self._left: dict[tuple[int, int], int] = {}      # bits still to sample per byte
        self._nbits: dict[tuple[int, int], int] = {}     # bits clocked per byte
        self._gen = self._run()

    # ------------------------------------------------------------ Device interface
    def step(self, t, bus):
        self.t = t
        if t > 0:
            self._miso[t - 1] = (bus >> self.miso) & 1   # step t sees the bus of cycle t-1
            self._settle(t - 1)
        if not self.done:
            try:
                next(self._gen)
            except StopIteration:
                self.done = True
        pins = (1 << self.sck) | (1 << self.mosi) | (1 << self.cs)
        hi = (self._sck << self.sck) | (self._mosi << self.mosi) | (self._cs << self.cs)
        return pins & ~hi, hi

    def errors(self):
        errs = [f"SPI controller: {v}" for v in self.violations[:8]]
        if not self.done:
            errs.append(f"SPI controller: script not finished ({len(self.rx)} frames started)")
        return errs

    # ------------------------------------------------------------ MISO checks
    def _settle(self, seen: int):
        """Evaluate every sampling edge whose hold window has now been observed."""
        while self._checks and self._checks[0][0] + self.t_ho <= seen:
            ts, f, b = self._checks.pop(0)
            win = [self._miso.get(u) for u in range(ts - self.t_su, ts + self.t_ho + 1)]
            if None in win or len(set(win)) != 1:
                self.violations.append(f"t={ts}: MISO not held from {self.t_su} ticks before "
                                       f"to {self.t_ho} after the sampling edge: {win}")
            self.rx[f][b] = ((self.rx[f][b] << 1) | self._miso[ts]) & 0xFF
            self._left[(f, b)] -= 1
            exp = self.frames[f][1]
            if self._left[(f, b)] == 0 and exp is not None and b < len(exp) and exp[b] is not None:
                n = self._nbits[(f, b)]                  # 8, or fewer for a cut-short byte
                if self.rx[f][b] != exp[b] >> (8 - n):
                    self.violations.append(f"frame {f} byte {b}: MISO 0x{self.rx[f][b]:02x} "
                                           f"({n} bits), expected 0x{exp[b]:02x}")

    # ------------------------------------------------------------ waveform
    def _phase(self, level: int, i: int) -> int:
        p = self.t_high if level else self.t_low
        if callable(p):
            return p(i)
        if isinstance(p, (list, tuple)):
            return p[i % len(p)]
        return p

    def _hold(self, n):
        for _ in range(int(n)):
            yield

    def _sample(self, f: int, b: int):
        self.samples.append(self.t)
        self._checks.append((self.t, f, b))

    def _run(self):
        yield from self._hold(self.start_at)
        idle, active = self.cpol, 1 - self.cpol
        k_all = 0                                       # bit index over the whole script
        for f, fr in enumerate(self.frames):
            data, abort = fr[0], (fr[2] if len(fr) > 2 else None)
            nbits = 8 * len(data) if abort is None else abort
            bits = [(data[k // 8] >> (7 - k % 8)) & 1 for k in range(nbits)]
            self.rx.append([0] * ((nbits + 7) // 8))
            for k in range(nbits):
                self._left[(f, k // 8)] = self._left.get((f, k // 8), 0) + 1
                self._nbits[(f, k // 8)] = self._left[(f, k // 8)]
            self._sck, self._cs = idle, 0
            self.cs_fall.append(self.t)
            if self.cpha == 0 and bits:
                self._mosi = bits[0]
            yield from self._hold(self.t_css)
            for k, bit in enumerate(bits):
                self._sck = active                      # leading edge
                if self.cpha == 0:
                    self._sample(f, k // 8)
                else:
                    self._mosi = bit
                yield from self._hold(self._phase(active, k_all))
                self._sck = idle                        # trailing edge
                if self.cpha == 1:
                    self._sample(f, k // 8)
                elif k + 1 < nbits:
                    self._mosi = bits[k + 1]
                if k + 1 == nbits:
                    yield from self._hold(self.t_csh)
                else:
                    yield from self._hold(self._phase(idle, k_all))
                    if (k + 1) % 8 == 0:
                        yield from self._hold(self.byte_gap)
                k_all += 1
            self._cs = 1
            self.cs_rise.append(self.t)
            yield from self._hold(self.t_idle)
        while self._checks:                             # see out the last hold windows
            yield
