"""PWM audio receiver: decodes 8-bit PCM samples from a pulse-width-modulated pin.

Written from the format firmware/pwm_audio.asm documents, as a measuring instrument would read
it, not from the program: a sample lasts SP ticks, split into eight PWM periods whose lengths
alternate PA, PB (with any remainder of SP / 4 added to the eighth); in each period the pin is
high for OFFS + s ticks, s the sample. The receiver times every rising and falling edge, so it
checks the carrier's schedule to the tick as well as the samples: each period must start exactly
where the schedule says, and the eight periods of a sample must carry the same s.
"""

from __future__ import annotations

from .board import Device


def schedule(clk_khz: int = 25000, rate: int = 11025) -> tuple[int, int, int, int]:
    """(SP, PA, PB, REM) for a clock and a sample rate, as the firmware computes them."""
    sp = (clk_khz * 1000 + rate // 2) // rate
    pair = sp // 4
    pa = pair // 2
    return sp, pa, pair - pa, sp - 4 * pair


class PwmAudioReceiver(Device):
    """Decodes samples on `pin`. `samples` holds one value per sample period, `rises` the tick
    of every period's rising edge; a period that starts off the schedule, a high time outside
    OFFS .. OFFS + 255 or a sample whose eight periods disagree is a violation."""

    def __init__(self, pin: int, clk_khz: int = 25000, rate: int = 11025, offs: int = 8):
        self.pin, self.offs = pin, offs
        self.sp, self.pa, self.pb, self.rem = schedule(clk_khz, rate)
        self.samples: list[int] = []
        self.rises: list[int] = []
        self.violations: list[str] = []
        self._prev = None
        self._rise = None
        self._highs: list[int] = []     # high times of the current sample's periods

    def _period(self, k: int) -> int:
        """Length of period k of a sample (0..7)."""
        return (self.pa if k % 2 == 0 else self.pb) + (self.rem if k == 7 else 0)

    def step(self, t, bus):
        lvl = (bus >> self.pin) & 1
        if self._prev is None:
            self._prev = lvl
            return 0, 0
        if lvl and not self._prev:
            if self.rises:
                k = (len(self.rises) - 1) % 8
                want = self.rises[-1] + self._period(k)
                if t != want:
                    self.violations.append(f"t={t}: period {len(self.rises)} starts {t - want:+d} "
                                           f"ticks off the schedule")
            self.rises.append(t)
            self._rise = t
        elif self._prev and not lvl and self._rise is not None:
            s = t - self._rise - self.offs
            if not 0 <= s <= 255:
                self.violations.append(f"t={t}: high for {t - self._rise} ticks")
            self._highs.append(s)
            if len(self._highs) == 8:
                if len(set(self._highs)) != 1:
                    self.violations.append(f"t={t}: one sample's periods carry {self._highs}")
                self.samples.append(self._highs[0])
                self._highs = []
        self._prev = lvl
        return 0, 0

    def errors(self) -> list[str]:
        return [f"pwm audio: {v}" for v in self.violations[:5]]
