"""Host model for mailbox traffic: the same schedule drives the ISS (through its API)
and the RTL co-simulation (the harness reads `pushes`, `drain` and `poll`)."""

from __future__ import annotations

from .board import Device


class HostScript(Device):
    """Host model: pushes mailbox words at given ticks; optionally polls (drains) TX
    mailboxes every `poll` ticks, as an SPI host would (one MBX_POP at 1 MHz SCK
    takes ~1400 ticks, so poll >= 1400 is the realistic rate)."""

    def __init__(self, pushes, drain=(), poll=1400):
        self.pushes = sorted(pushes)   # (t, ctx, word)
        self.drain, self.poll = tuple(drain), poll
        self.received = {k: [] for k in self.drain}
        self.machine = None

    def step(self, t, bus):
        if self.machine is None:
            return 0, 0
        while self.pushes and self.pushes[0][0] <= t:
            _, ctx, word = self.pushes.pop(0)
            self.machine.host_rx_push(ctx, word)
        if self.drain and t % self.poll == 0:
            for k in self.drain:
                v = self.machine.host_tx_pop(k)
                if v is not None:
                    self.received[k].append(v)
        return 0, 0
