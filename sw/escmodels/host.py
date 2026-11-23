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


class HostFeed(Device):
    """Host model with flow control, as a host that reads STATUS before it pushes: each context
    in `queues` ({ctx: [word, ...]}) gets its next word only while its RX mailbox has room, so
    no push is ever lost (a push into a full mailbox is dropped, sec. 7.5). `gap` is the ticks
    one push takes on the host link (an SPI MBX_PUSH), so pushes to all contexts together are
    at least that far apart; words can be added to `queues` while the run goes on. `pushed`
    records (tick, ctx) for every push, `drain` TX mailboxes are emptied as HostScript does."""

    def __init__(self, queues=None, gap: int = 1, drain=(), poll: int = 1400, start: int = 0):
        self.queues = {k: list(v) for k, v in (queues or {}).items()}
        self.gap, self.start, self.drain, self.poll = gap, start, tuple(drain), poll
        self.received = {k: [] for k in self.drain}
        self.pushed: list[tuple[int, int]] = []
        self.machine = None
        self._next = start
        self._turn = 0

    def step(self, t, bus):
        m = self.machine
        if m is None:
            return 0, 0
        if t >= self._next:
            ks = sorted(self.queues)
            for i in range(len(ks)):              # round robin over contexts with work and room
                k = ks[(self._turn + i) % len(ks)]
                if self.queues[k] and len(m.ctx[k].rx) < 2:
                    m.host_rx_push(k, self.queues[k].pop(0))
                    self.pushed.append((t, k))
                    self._turn = (self._turn + i + 1) % len(ks)
                    self._next = t + self.gap
                    break
        if self.drain and t % self.poll == 0:
            for k in self.drain:
                v = m.host_tx_pop(k)
                if v is not None:
                    self.received[k].append(v)
        return 0, 0
