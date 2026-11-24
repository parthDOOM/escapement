"""Learned on the bench (README.md): the chip records a transmitter it was never programmed
for with its own capture unit, and runs the transmitter and receiver learned from that
capture. The Pico W's half; learnhost.py is the laptop's, between the runs.

    import learnbench; learnbench.capture()      (.\\bench.ps1 learn runs all three)
    import learnbench; learnbench.receive()
    import learnbench; learnbench.transmit(0x...)

  capture   logic_analyzer (bundles/) watches uio[7:0] into the whole tape; the Pico plays
            the frames of learn/source.json (a real remote's recording, learnhost.py
            source) onto uio[2] back to back with PIO, one microsecond a cycle, then reads
            the records; writes learn/capture.json, the chip's own recording
  receive   learn/learned_rx.json (escgen's receiver for what was learned, on uio[2]): the
            frames played again, and every word the chip PUSHes is compared with the
            frame's own word
  transmit  learn/learned_tx.json (escgen's transmitter, on uio[3]): a word the recording
            never had, pushed twice, as a remote repeats its frames; the Pico times every
            mark on uio[3] (machine.time_pulse_us) and writes learn/sent.json for
            learnhost.py judge. The first frame starts 64 ticks after the chip takes the
            word, before MicroPython is back from the push to time it, so the judge reads
            the second, timed from the gap before it
Wiring: the bring-up's (uio[2] is GP19, uio[3] GP20); nothing else on those two pins.
"""
import gc
import json

import barepico
import bringup
from bringup import Fail, P

SYS_HZ = 125_000_000
F_CHIP = 25_000_000
SRC, TX = 2, 3                             # uio pins: the playback, the chip's transmitter
RUNNING = 1


class Player:
    """[(level, us), ...] onto one GPIO from a PIO state machine at 1 MHz: each segment is
    one 32-bit word, level in bit 0 and the microseconds less the loop's 3 cycles above it,
    so the timing is the PIO's, not MicroPython's."""

    def __init__(self, gpio, sm_id=0):
        import rp2
        from machine import Pin
        self.Pin, self.gpio = Pin, gpio

        @rp2.asm_pio(out_init=rp2.PIO.OUT_LOW, out_shiftdir=rp2.PIO.SHIFT_RIGHT,
                     autopull=True, pull_thresh=32)
        def seg():
            out(pins, 1)                   # noqa: F821 (PIO assembler names)
            out(x, 31)                     # noqa: F821
            label("w")                     # noqa: F821
            jmp(x_dec, "w")                # noqa: F821

        self.rp2, self.prog = rp2, seg
        self.sm = rp2.StateMachine(sm_id, seg, freq=1_000_000, out_base=Pin(gpio))
        self.sm.active(1)

    def play(self, segs, io):
        for lv, us in segs:
            self.sm.put(((max(int(us), 3) - 3) << 1) | (lv & 1))
        while self.sm.tx_fifo():
            io.sleep_us(100)
        io.sleep_us(int(segs[-1][1]) + 100)    # the last segment, now in the loop

    def close(self):
        self.sm.active(0)
        self.rp2.PIO(0).remove_program(self.prog)   # its 4 instructions, for the next run
        self.Pin(self.gpio, self.Pin.IN, self.Pin.PULL_UP)


class LearnBench:
    def __init__(self, io, bundles="bundles", learn="learn", out=print, player=None):
        self.io, self.bundles, self.dir, self.say = io, bundles, learn, out
        self.b = bringup.Bringup(io, bundles, out=out, proof=None)
        self.host = None
        self.player = player

    def setup(self):
        io = self.io
        io.select()
        hz = io.set_clock(F_CHIP)
        if hz != F_CHIP:
            raise Fail("the clock PWM makes %s Hz, not 25 MHz (open_io(sys_hz=%d))" % (hz, SYS_HZ))
        io.reset(True)
        io.sleep_ms(2)
        io.reset(False)
        io.sleep_ms(2)
        self.b.connect()
        self.host = h = self.b.host
        st = h.status()
        if st["version"] < P.PAGES_VERSION or st["running"]:
            raise Fail("status after reset: %r; run the bring-up first (.\\bench.ps1 bringup)" % st)
        io.uio_drive(0, 0)
        io.uio_pullups()
        if self.player is None:
            self.player = Player(barepico.UIO_GPIO[SRC])
        return hz

    def load(self, path):
        gc.collect()
        bd = bringup.load_bundle(path)
        words = list(bd["words"]) + [0] * (256 - len(bd["words"]))
        tape = [0] * 256
        for a, w in bd["tape"].items():
            tape[int(a)] = w
        self.b.load(words, bd["cfg"], tape)
        return bd

    def frames(self):
        with open(self.dir + "/source.json") as f:
            return json.load(f)["frames"]

    def capture(self):
        """The chip records the playback: returns the record words, written to capture.json.
        The frames go out back to back, gaps as recorded, into the whole tape (256 records,
        logic_analyzer.json), read once after STOP: reading between frames would stretch
        the gaps, and the chip would learn the stretched ones."""
        h, io = self.host, self.io
        self.load(self.bundles + "/logic_analyzer.json")
        frames = self.frames()
        if sum(len(fr["segs"]) for fr in frames) > 250:
            raise Fail("the frames have more edges than the tape holds (256 records)")
        h.start()
        io.sleep_ms(1)
        for fr in frames:
            self.player.play(fr["segs"], io)
        h.stop()
        st = h.read_dbg(0, 17)
        n = 256 if (st >> 29) & 1 else st & 0xFF
        if (st >> 30) & 1:
            raise Fail("capture overflow (status 0x%08x)" % st)
        words = h.read_tape(0, n) if n else []
        with open(self.dir + "/capture.json", "w") as f:
            json.dump({"format": "escapement-capture", "version": 1, "clock_hz": self.b.f_clk,
                       "first_epoch": 0, "names": ["uio%d" % i for i in range(8)],
                       "words": ["0x%08x" % x for x in words]}, f)
        self.say("learn: the chip recorded %d frames on uio[%d]: %d records -> %s/capture.json"
                 % (len(frames), SRC, len(words), self.dir))
        return words

    def receive(self, rounds=2):
        """The learned receiver hears the playback: returns [(frame's words, words PUSHed)]."""
        h, io = self.host, self.io
        self.load(self.dir + "/learned_rx.json")
        h.start()
        io.sleep_ms(2)
        res = []
        for _ in range(rounds):
            for fr in self.frames():
                self.player.play(fr["segs"], io)
                got = []
                while True:
                    v = h.mbx_pop(0)
                    if v is None:
                        break
                    got.append(v)
                res.append((fr["words"], got))
                self.say("learn: played %s, the chip's receiver PUSHed %s" % (
                    " ".join("0x%x" % w for w in fr["words"]),
                    " ".join("0x%x" % w for w in got) or "nothing"))
        if h.status()["fault"]:
            raise Fail("the receiver faulted: %r" % h.status())
        h.stop()
        return res

    def transmit(self, word, frames=2, timeout_ms=200):
        """The learned transmitter sends `word` `frames` times; returns the marks timed on
        uio[TX]."""
        h, io = self.host, self.io
        self.load(self.dir + "/learned_tx.json")
        h.start()
        io.sleep_ms(2)
        for _ in range(frames):
            h.mbx_push(0, word)
        marks, wait = [], timeout_ms * 1000
        while True:
            d = io.pulse_us(TX, 1, wait)           # waits for a mark, then times it
            t = io.ticks_us()
            if d < 0:
                break
            marks.append((t - d, t))
            wait = 30000                           # a frame's spaces are far shorter
        acks = []
        while True:
            v = h.mbx_pop(0)
            if v is None:
                break
            acks.append(v)
        fault = h.status()["fault"]
        h.stop()
        if fault:
            raise Fail("the transmitter faulted")
        with open(self.dir + "/sent.json", "w") as f:
            json.dump({"word": word, "frames": frames, "marks": marks, "acks": acks}, f)
        self.say("learn: the chip sent 0x%x %d times on uio[%d]: %d marks timed -> %s/sent.json"
                 % (word, frames, TX, len(marks), self.dir))
        return marks


def _bench(io, out, player, bundles, learn):
    if io is None:
        io = barepico.open_io(sys_hz=SYS_HZ)
    s = LearnBench(io, bundles, learn, out=out, player=player)
    s.setup()
    return s


def _close(s):
    if isinstance(s.player, Player):
        s.player.close()
    s.io.uio_pullups()


def capture(io=None, out=print, player=None, bundles="bundles", learn="learn"):
    s = _bench(io, out, player, bundles, learn)
    try:
        return s.capture()
    finally:
        _close(s)


def receive(io=None, out=print, player=None, rounds=2, bundles="bundles", learn="learn"):
    """Prints a verdict; returns True when every frame came back as its own word, once."""
    s = _bench(io, out, player, bundles, learn)
    try:
        res = s.receive(rounds)
    finally:
        _close(s)
    ok = all(got == want for want, got in res)
    out("learn: %s" % ("PASS: the learned receiver read every frame as the recording's word"
                       if ok else "FAIL: %d of %d frames read wrong" % (
                           sum(got != want for want, got in res), len(res))))
    return ok


def transmit(word, io=None, out=print, player=None, frames=2, bundles="bundles", learn="learn"):
    s = _bench(io, out, player, bundles, learn)
    try:
        return s.transmit(word, frames)
    finally:
        _close(s)
