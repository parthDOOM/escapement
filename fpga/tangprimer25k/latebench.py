"""The chip refuses (README.md, "The chip refuses"): programs that ask for the impossible, run
on the FPGA build, are refused exactly where the model and the certificate checker said they
would be, and nothing late or unowned reaches a pin.

    import latebench; latebench.run()            (.\\bench.ps1 late)

For each bundle in refused/ (late_bundles.py):
  1. escert's verdict, from the bundle: REFUTED, with the finding and its pc, before any run
  2. load, START: the context must fault, and the chip's first-fault record (READ_DBG 14: cause,
     subcode, context, pc) and fault tick (15) must be the model's, with the digest (16) and
     every context's state and registers
  3. while the run is still on (STOP releases every pin anyway), the program's pins read 0
     with the RP's pull-downs instead of its pull-ups: the chip drives none of them, so the
     faulted context let go of its pins (P5) and the unowned pin was never driven (P3)
  4. the proof tripwires (READ_DBG 27) read 0: the chip's own monitors saw no FIFO head past
     its deadline (P2, "never late"), no unowned pin driven and no faulted context driving
Wiring: the bring-up's. The program's uio pins must be free of other loads (only the Pico
W's GPIOs on them), or a pull-up there reads as a driven pin.
"""
import gc

import barepico
import bringup
from bringup import Fail, P

SYS_HZ = 125_000_000                       # the RP's clock: 25 MHz from the PWM exactly
F_CHIP = 25_000_000
CAUSES = {1: "ILLEGAL", 2: "PC_RANGE", 3: "ADMIT", 4: "LATE_HEAD"}
ADMIT = {1: "MASK", 2: "OWNER", 3: "PAST", 4: "ORDER", 5: "OVERLAP"}
STATES = ("IDLE", "RUNNING", "HALTED", "FAULTED")
# the pins each program owns or asks to drive (late_bundles.py)
WATCH = {"uart_tx_late.json": 0x01, "refuse_owner.json": 0x03}


def fault_text(ff, tick):
    cause = CAUSES.get((ff >> 24) & 15, "cause %d" % ((ff >> 24) & 15))
    sub = ADMIT.get((ff >> 20) & 15, "%d" % ((ff >> 20) & 15)) if cause == "ADMIT" else ""
    return "%s %s, context %d, pc %d, tick %d" % (cause, sub, (ff >> 16) & 3, ff & 0x1FF, tick)


class LateBench:
    def __init__(self, io, bundles="refused", out=print):
        self.io, self.dir, self.say = io, bundles, out
        self.b = bringup.Bringup(io, bundles, out=out, proof=None)
        self.host = None

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
        self.say("late: chip at %.3f MHz" % (hz / 1e6))

    def pulls(self, mask, down):
        m = self.io.machine
        p = getattr(m.Pin, "PULL_DOWN", 2) if down else m.Pin.PULL_UP
        for i in range(8):
            if (mask >> i) & 1:
                self.io._pin("uio%d" % i).pull = p

    def one(self, fn):
        """Run one refused bundle; returns the list of failed checks (empty: it passed)."""
        io, h, say = self.io, self.host, self.say
        gc.collect()
        bd = bringup.load_bundle(self.dir + "/" + fn)
        e = bd["expect"]
        watch = WATCH.get(fn, 0)
        bad = []
        say("late: %s" % bd["name"])
        # 1. the verdict before the run
        for c in bd.get("escert", []):
            f = c["findings"][0] if c["findings"] else None
            say("late:   escert, before the run: context %d %s%s" % (
                c["ctx"], c["status"], ", %s at pc %d (%s)" % (f[0], f[1], f[2]) if f else ""))
            if c["status"] != "REFUTED":
                bad.append("escert says %s, not REFUTED" % c["status"])
        # 2. the run: pull-ups as the model's board, START, wait for every context to stop
        io.uio_drive(0, 0)
        io.uio_pullups()
        words = list(bd["words"]) + [0] * (256 - len(bd["words"]))
        tape = [0] * 256
        for a, w in bd["tape"].items():
            tape[int(a)] = w
        self.b.load(words, bd["cfg"], tape)
        h.start()
        t0 = io.ticks_us()
        while any((h.status()["ctx_state"] >> (2 * k)) & 3 == 1 for k in range(4)):
            if io.ticks_diff(io.ticks_us(), t0) > 100000:
                bad.append("a context still runs 100 ms after START")
                break
            io.sleep_ms(1)
        # 3. the pins while the run is on: pull-downs, so only a driving chip reads 1
        self.pulls(watch, True)
        io.sleep_ms(2)
        held = io.read_uio() & watch
        self.pulls(watch, False)
        io.sleep_ms(2)
        sig = self.b.read_sig()
        dg, tw = h.read_dbg(0, 16), h.read_dbg(0, 27)
        h.stop()
        ff, tick = sig["first_fault"], sig["fault_tick"]
        state = STATES[sig["ctx"][(ff >> 16) & 3][0]]
        say("late:   the chip: %s, the context %s" % (fault_text(ff, tick), state))
        say("late:   the model: %s" % fault_text(e["first_fault"], e["fault_tick"]))
        if not ff >> 31:
            bad.append("no fault recorded")
        diffs = self.b.compare_sig(sig, e)
        if diffs:
            bad.append("chip and model differ: " + "; ".join(diffs))
        if dg != e["digest"]:
            bad.append("digest 0x%08x, model 0x%08x" % (dg, e["digest"]))
        else:
            say("late:   digest 0x%08x = model" % dg)
        say("late:   uio pins 0x%02x with pull-downs during the run: %s" % (
            watch, "none driven" if not held else "0x%02x driven" % held))
        if held:
            bad.append("uio 0x%02x still driven after the fault" % held)
        say("late:   proof tripwires 0x%03x%s" % (tw, "" if tw else ": never late, no unowned "
                                                  "pin driven, no faulted context driving"))
        if tw:
            bad.append("tripwires set: " + "; ".join(P.tripwire_names(tw)))
        return bad


def bundle_files(d):
    return bringup._bundle_files(d)


def run(bundles="refused", io=None, out=print):
    """Every refused bundle: prints each check and a verdict; returns the failures (an empty
    list when every refusal matched), or None if the bench could not start."""
    if io is None:
        io = barepico.open_io(sys_hz=SYS_HZ)
    s = LateBench(io, bundles, out)
    files = bundle_files(bundles)
    if not files:
        out("late: FAIL  no bundles in '%s' (.\\bench.ps1 bundles, then deploy)" % bundles)
        return None
    try:
        s.setup()
    except Fail as x:
        out("late: FAIL  %s" % x)
        return None
    fails = []
    for fn in files:
        try:
            bad = s.one(fn)
        except Fail as x:
            bad = [str(x)]
            s.host.stop()
        out("late: %s  %s%s" % ("FAIL" if bad else "PASS", fn, ": " + "; ".join(bad) if bad else ""))
        fails += [fn + ": " + x for x in bad]
    io.uio_pullups()
    out("late: %s" % ("PASS: every program was refused where escert and the model said, and "
                      "no late or unowned edge reached a pin" if not fails else
                      "FAIL (%d)" % len(fails)))
    return fails
