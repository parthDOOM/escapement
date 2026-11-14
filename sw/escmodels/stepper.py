"""A step/direction stepper driver's logic interface (Allegro A4988 datasheet, Figure 1 and
"Step Input"), for testing a motion program.

Written from the datasheet, not from any firmware: a low-to-high transition on STEP advances
the motor one increment, in the direction DIR selects (here DIR high counts +1). STEP high at
least tA = 1 us, low at least tB = 1 us; DIR set up tC = 200 ns before a rising STEP and held
tD = 200 ns after it. The model keeps the position and the time of every step.
"""

from __future__ import annotations

from .board import Device


class StepDirDriver(Device):
    def __init__(self, step, dirp, tick_ns):
        self.step_pin, self.dir_pin, self.tick = step, dirp, tick_ns
        self.position = 0
        self.steps: list[tuple[float, int]] = []      # (us, +1 or -1)
        self.violations: list[str] = []
        self._first = True
        self._s = self._d = 0
        self._t_s = self._t_d = None
        self._t_rise = None

    def _ns(self, a, b):
        return (b - a) * self.tick

    def step(self, t, bus):
        s, d = (bus >> self.step_pin) & 1, (bus >> self.dir_pin) & 1
        if self._first:
            self._first = False
            self._s, self._d = s, d
            return 0, 0
        if d != self._d:
            if self._t_rise is not None and self._ns(self._t_rise, t) < 200:
                self.violations.append(f"t={t}: DIR changed {self._ns(self._t_rise, t):.0f} ns after STEP rose (tD 200)")
            self._t_d = t
        if s != self._s:
            if self._t_s is not None:
                w = self._ns(self._t_s, t)
                if w < 1000:
                    self.violations.append(f"t={t}: STEP {'low' if s else 'high'} {w:.0f} ns (tA, tB 1 us)")
            if s:
                if self._t_d is not None and self._ns(self._t_d, t) < 200:
                    self.violations.append(f"t={t}: DIR set up {self._ns(self._t_d, t):.0f} ns (tC 200)")
                k = 1 if d else -1
                self.position += k
                self.steps.append((t * self.tick / 1000.0, k))
                self._t_rise = t
            self._t_s = t
        self._s, self._d = s, d
        return 0, 0
