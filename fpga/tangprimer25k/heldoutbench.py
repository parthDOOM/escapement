"""The held-out round's programs on the bench, from the Pico W's side (heldoutjudge.py runs
them and judges the analyser's captures; README.md, "Held-out round: two analyser channels").

The chip runs one program at a time from its bundle (build.sh bundles), clocked at 25 MHz by
the Pico W, which is also the host: it pushes the program's host words and pops the replies.
No device is on the other end of any bus, so the uio pins rest on the Pico W's pull-ups, as
in bring-up stage 5. For M-Bus the Pico W also plays the bus master.

    import barepico, heldoutbench
    b = heldoutbench.HeldoutBench(barepico.open_io(sys_hz=heldoutbench.SYS_HZ))
    b.setup("snes_pad")          # reset, load and START
    b.request([0x1], 1)          # push the host words, wait for one reply
    b.finish()                   # STOP: (first-fault word, tripwires), (0, 0) when clean

Plain MicroPython (no f-strings), like barepico.py and bringup.py, so the same file runs on
the board and under CPython against the simulated chip (test_heldoutbench.py).
"""
import bringup
from bringup import Fail, P

CTX = 0                          # every held-out program runs in context 0
SYS_HZ = 100_000_000             # the RP's clock, which the PWM divides to 25 or 50 MHz exactly
F_CHIP = 25_000_000
RUNNING = 1                      # a context's state in STATUS


class HeldoutBench:
    """One program on the chip at a time, driven through bring-up's host link."""

    def __init__(self, io, bundles="bundles", out=print):
        self.io, self.bundles, self.say = io, bundles, out
        self.b = bringup.Bringup(io, bundles, out=out, proof=None)
        self.host = None
        self.name = None
        self.mtx = None              # the M-Bus master's line, once taken

    def setup(self, name, f_chip=F_CHIP, start=True):
        """Reset the chip, pull the uio pins up, load bundle `name` and START it (unless
        `start` is false: then go() starts it)."""
        io = self.io
        io.select()
        hz = io.set_clock(f_chip)
        if hz != f_chip:
            raise Fail("the clock PWM makes %s Hz, not %d (open_io(sys_hz=%d))"
                       % (hz, f_chip, SYS_HZ))
        io.reset(True)
        io.sleep_ms(2)
        io.reset(False)
        io.sleep_ms(2)
        io.uio_pullups()
        self.b.connect()
        self.host = h = self.b.host
        st = h.status()
        if st["version"] < P.SPAN_VERSION or st["running"]:
            raise Fail("status after reset: %r; run the bring-up first (.\\bench.ps1 bringup)"
                       % st)
        bd = bringup.load_bundle(self.bundles + "/" + name + ".json")
        tape = [0] * 256
        for a, w in bd["tape"].items():
            tape[int(a)] = w
        self.b.load(list(bd["words"]), bd["cfg"], tape)
        self.name = name
        if start:
            self.go()
            io.sleep_ms(5)
            self.check_running()
        self.say("heldout: chip at %.3f MHz, %s loaded in context %d" % (hz / 1e6, name, CTX))

    def go(self):
        self.host.start()

    def check_running(self):
        st = self.host.status()
        if (st["ctx_state"] >> (2 * CTX)) & 3 != RUNNING or st["fault"]:
            raise Fail("%s is not running after START: %r" % (self.name, st))

    def request(self, words, n_replies, timeout_ms=1000):
        """Push `words`, each as soon as the mailbox has room, then collect `n_replies`
        reply words and return them. A fault or a missing reply raises Fail."""
        io, h = self.io, self.host
        got = []
        t0 = io.ticks_us()
        for w in words:
            while (h.status()["rx_full"] >> CTX) & 1:
                if io.ticks_diff(io.ticks_us(), t0) > timeout_ms * 1000:
                    raise Fail("%s: the mailbox stayed full (status %r)"
                               % (self.name, h.status()))
                v = h.mbx_pop(CTX)
                if v is not None:
                    got.append(v)
            h.mbx_push(CTX, w)
        while len(got) < n_replies:
            v = h.mbx_pop(CTX)
            if v is not None:
                got.append(v)
            elif io.ticks_diff(io.ticks_us(), t0) > timeout_ms * 1000:
                raise Fail("%s: %d of %d replies in %d ms (status %r)"
                           % (self.name, len(got), n_replies, timeout_ms, h.status()))
        st = h.status()
        if st["fault"]:
            raise Fail("%s faulted: %r" % (self.name, st))
        return got

    def stream_marked(self, words, marker=18):
        """Push `words` with no status reads between them, then mark the moment for the
        analyser: a short low pulse on GPIO `marker`, driven both ways, then released to
        its pull-up. A push takes about 9.4 ms on the bit-banged link, far longer than an
        S/PDIF frame, so the analyser triggers on this mark rather than on the push. The
        marker is uio[1] (GP18), a pin the S/PDIF program does not own, so only the Pico
        drives it. The pin is built and the heap collected before the push, so the pulse
        follows the push at once (it lasts about 100 us at MicroPython's speed). Returns the
        words pushed."""
        import gc
        m = self.io.platform.machine
        h = self.host
        pin = m.Pin(marker, m.Pin.OUT, value=1)    # resting high, as the pull-up held it
        gc.collect()
        for w in words:
            h.mbx_push(CTX, w)
        pin.value(0)
        self.io.sleep_us(20)
        pin.value(1)
        pin.init(m.Pin.IN, m.Pin.PULL_UP)
        self.check_running()
        return list(words)

    # ---------------------------------------------------------------- M-Bus master
    # The Pico W as the M-Bus master at logic level (no TSS721 transceiver: both sides are
    # 3.3 V logic). It sends telegrams on uio[0] (GP9), the slave's RX. The slave answers on
    # uio[1] (GP18), which the Pico leaves alone: the analyser and sigrok's uart decoder
    # judge both lines.

    def mbus_prepare(self, host_words, rx_gpio=9, f_chip=F_CHIP, name="mbus_slave"):
        """Load and start the slave, give it its host words (its address and its response),
        and take the master's line, GPIO `rx_gpio`, driven Mark (1) as an idle bus is."""
        self.setup(name, f_chip)
        self.request(host_words, 0)
        m = self.io.platform.machine
        self.mtx = m.Pin(rx_gpio, m.Pin.OUT, value=1)

    def mbus_master(self, telegrams, baud=2400, wait_ms=300):
        """Send each telegram (a list of bytes) as 8E1 characters back to back (M-Bus 5.1:
        a start bit, 8 data bits LSB first, even parity, a stop bit), then wait `wait_ms`
        for the reply, popping the slave's notes to its host meanwhile. Each telegram is
        bit-banged against the microsecond timer, every bit timed from the telegram's start
        so that no error builds up, with interrupts off while it goes. Returns the notes."""
        io, h = self.io, self.host
        m = io.platform.machine
        pin = self.mtx
        notes = []
        for tel in telegrams:
            bits = []
            for c in tel:
                parity, x = 0, c
                while x:
                    parity ^= x & 1
                    x >>= 1
                bits += [0] + [(c >> i) & 1 for i in range(8)] + [parity, 1]
            irq = m.disable_irq() if hasattr(m, "disable_irq") else None
            t0 = io.ticks_us()
            for k in range(len(bits) + 1):           # the last pass waits out the stop bit
                due = k * 1000000 // baud
                while io.ticks_diff(io.ticks_us(), t0) < due:
                    pass
                if k < len(bits):
                    pin.value(bits[k])
            if irq is not None:
                m.enable_irq(irq)
            t1 = io.ticks_us()
            while io.ticks_diff(io.ticks_us(), t1) < wait_ms * 1000:
                v = h.mbx_pop(CTX)
                if v is not None:
                    notes.append(v)
                else:
                    io.sleep_ms(5)
        return notes

    def mbus_release(self):
        """Give the master's line back to its pull-up."""
        m = self.io.platform.machine
        self.mtx.init(m.Pin.IN, m.Pin.PULL_UP)

    def finish(self):
        """STOP; returns the first-fault word (0: none) and the tripwires."""
        h = self.host
        h.stop()
        return h.read_dbg(0, 14), h.tripwires()
