# SUMP (Openbench Logic Sniffer) bridge: PulseView and sigrok-cli capture from Escapement
# through the demo board's RP2040/RP2350, with libsigrok's existing `ols` driver.
#
#     import sump; sump.serve()        # then, on the PC: PulseView, driver "Openbench
#                                      # Logic Sniffer & SUMP compatibles", the board's
#                                      # USB serial port
#
# SUMP is a buffer protocol: the analyser is told a sample rate and a sample count, arms,
# captures, and then sends every sample, newest first. Here the chip does the capturing:
# the logic analyser firmware streams change records over SPI during the run (v1.3
# streaming capture), the bridge keeps the records (not samples), and on sending it
# expands them into samples at the requested rate. So the window can be long, and the
# chip's timestamps are exact to one 40 ns tick whatever rate PulseView asks for.
#
# Protocol as libsigrok's openbench-logic-sniffer driver speaks it (protocol.h/.c and
# api.c, checked 2026-10-19): five 0x00 resets, ID 0x02 answered "1ALS", metadata 0x04
# (tokens: 0x01 name and 0x02 version as NUL-terminated strings, 0x20 probes, 0x21 sample
# memory bytes, 0x23 max rate, 0x24 protocol version as 32-bit big-endian, 0 ends);
# long commands are a byte >= 0x80 and 4 argument bytes: 0x80 divider (rate = 100 MHz /
# (divider + 1)), 0x81 read and delay counts (16-bit LE each, in 4-sample units, minus
# 1), 0x83/0x84 delay and read counts (32-bit LE, same units, for large memories), 0x82
# flags (bits 2-5 disable channel groups; a sample has one byte per enabled group),
# 0xC0/0xC1/0xC2 trigger stage 0 mask, value and configuration; 0x01 arms. The driver
# ignores timeouts until the first data byte, then ends at a 100 ms gap, so a capture
# may take as long as its window but the samples go out without pauses.
#
# The trigger is stage 0's mask and value (parallel mode): the capture centres on the
# first record whose watched pins match. Pins are uio[7:0], channels 0-7.

import bringup

CLOCK_RATE = 100000000                 # the divider's reference in the SUMP protocol
MEMORY = 1 << 22                       # samples offered (4 Mi at one byte each)
MAX_RECORDS = 16384                    # records kept in RP memory per capture
ID = b"1ALS"


def metadata(f_chip):
    m = bytearray(b"\x01Escapement\x00\x02v1.3\x00")
    for key, v in ((0x20, 8), (0x21, MEMORY), (0x23, int(f_chip)), (0x24, 2)):
        m += bytes((key, (v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF))
    return bytes(m + b"\x00")


class Capture:
    """One SUMP acquisition, as configured by the long commands."""

    def __init__(self):
        self.divider = 0
        self.read_count = 4
        self.delay_count = 4
        self.flags = 0
        self.mask = 0
        self.value = 0

    @property
    def rate(self):
        return CLOCK_RATE / (self.divider + 1)

    @property
    def groups(self):
        return sum(1 for g in range(4) if not (self.flags >> (2 + g)) & 1) or 1


def samples(records, start, step, n):
    """Levels at start, start + step, ... (n samples, ticks may be fractional) from
    (tick, levels) records in time order, generated newest first as SUMP sends them.
    Before the first record the first record's levels hold."""
    i = len(records) - 1
    for k in range(n - 1, -1, -1):
        t = start + k * step
        while i > 0 and records[i][0] > t:
            i -= 1
        yield records[i][1]


class Bridge:
    """The SUMP device. `rd(n)` and `wr(bytes)` are the host serial link; `b` is a
    connected bringup.Bringup; `bundle` the logic_analyzer_stream bundle."""

    def __init__(self, b, bundle, rd, wr):
        self.b, self.bundle, self.rd, self.wr = b, bundle, rd, wr
        self.cap = Capture()
        self.log = []                  # what happened, for the REPL afterwards

    def _long(self, cmd, a):
        c = self.cap
        v = a[0] | (a[1] << 8) | (a[2] << 16) | (a[3] << 24)
        if cmd == 0x80:
            c.divider = v & 0xFFFFFF
        elif cmd == 0x81:
            c.read_count = ((v & 0xFFFF) + 1) * 4
            c.delay_count = ((v >> 16) + 1) * 4
        elif cmd == 0x84:
            c.read_count = (v + 1) * 4
        elif cmd == 0x83:
            c.delay_count = (v + 1) * 4
        elif cmd == 0x82:
            c.flags = v
        elif cmd == 0xC0:
            c.mask = v & 0xFF
        elif cmd == 0xC1:
            c.value = v & 0xFF

    def step(self):
        """Handle one command. Returns False when the link closed."""
        x = self.rd(1)
        if not x:
            return False
        cmd = x[0]
        if cmd >= 0x80:
            self._long(cmd, self.rd(4))
        elif cmd == 0x02:
            self.wr(ID)
        elif cmd == 0x04:
            self.wr(metadata(self.b.f_clk))
        elif cmd == 0x01:
            self.acquire()
        elif cmd == 0x00:
            pass                       # reset: nothing is armed between commands
        return True

    def acquire(self):
        """Capture the configured window around the trigger and send it."""
        c, b, h, io = self.cap, self.b, self.b.host, self.b.io
        n = min(c.read_count, MEMORY)
        step = b.f_clk / c.rate                     # ticks per sample
        post = min(c.delay_count, n) * step         # window after the trigger, ticks
        pre = n * step - post
        base, lim = self.bundle["stream"]["base"], self.bundle["stream"]["lim"]
        words = list(self.bundle["words"])
        b.load(words + [0] * (256 - len(words)), self.bundle["cfg"])
        io.uio_drive(0, 0)
        io.uio_pullups()
        h.start()
        recs, rptr, trig, epoch, ovf, full = [], base, None, 0, False, False
        t_us, post_us = 0, int(post / b.f_clk * 1e6) + 200000
        while True:
            w, rptr, st = h.stream_poll(base, lim, rptr)
            for x in w:
                t24 = x & 0xFFFFFF
                if t24 == 0 and recs:
                    epoch += 1
                rec = ((epoch << 24) | t24, x >> 24)
                recs.append(rec)
                if trig is None and (rec[1] & c.mask) == (c.value & c.mask):
                    trig, t_us = rec[0], io.ticks_us()
            if trig is None and len(recs) > MAX_RECORDS // 2:
                recs = recs[-(MAX_RECORDS // 2):]   # waiting: keep a pre-trigger history
            if (st >> 30) & 1:
                ovf = True
                break
            if trig is not None and recs[-1][0] >= trig + post:
                break
            if trig is not None and io.ticks_diff(io.ticks_us(), t_us) > post_us:
                break                               # quiet line: the window has passed
            if len(recs) >= MAX_RECORDS:
                full = True                         # the rest of the window is not kept
                break
        h.stop()
        w, rptr, st = h.stream_poll(base, lim, rptr)
        for x in w:
            t24 = x & 0xFFFFFF
            if t24 == 0 and recs:
                epoch += 1
            recs.append(((epoch << 24) | t24, x >> 24))
        if trig is None:
            trig = recs[0][0]
        start = trig - pre
        out, chunk, g = self.wr, bytearray(), c.groups
        for lv in samples(recs, start, step, n):
            chunk.append(lv)
            if g > 1:
                chunk.extend(bytes(g - 1))
            if len(chunk) >= 1024:
                out(bytes(chunk))
                chunk = bytearray()
        if chunk:
            out(bytes(chunk))
        self.log.append("%d samples at %d Hz, %d records, trigger at tick %d%s%s"
                        % (n, c.rate, len(recs), trig, ", OVERFLOW" if ovf else "",
                           ", record memory full" if full else ""))


def serve(bundle_file="bundles/logic_analyzer_stream.json"):
    """Run the bridge on the board's USB serial port until it is closed. While it runs,
    the REPL is off the port (Ctrl-C included); press the board's reset to get it back."""
    import sys
    import micropython
    b = bringup.Bringup(bringup.DemoBoardIO.open())
    b.stage0()
    b.connect()
    if b.host.status()["version"] != bringup.VERSION:
        raise RuntimeError("not an Escapement v1.3 chip")
    br = Bridge(b, bringup.load_bundle(bundle_file),
                sys.stdin.buffer.read, sys.stdout.buffer.write)
    micropython.kbd_intr(-1)           # 0x03 is data now, not Ctrl-C
    try:
        while br.step():
            pass
    finally:
        micropython.kbd_intr(3)
