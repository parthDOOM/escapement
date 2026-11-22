# Low-speed USB on the bench: the FPGA build of the chip, clocked at 24 MHz by the Pico, is a
# USB keyboard on a laptop's port (firmware/usb_hid.asm) and types a line into it (README.md,
# USB keyboard).
#
#     import usbbench
#     usbbench.run("escapement typed this\n")
#
# usb_hid needs exactly 24 MHz, so the RP's system clock goes to 144 MHz and the PWM divides
# it by 6. Context 0 answers the enumeration on its own; context 1 moves each key report from
# its mailbox to the tape for the computer's next IN. A mailbox that stays full means nothing
# is polling: the computer has not enumerated the keyboard.
#
# Plain MicroPython, so the same file runs on the board and under CPython (test_usbbench.py).

import barepico
import bringup
import escapement_demo
from bringup import Fail, P

F_USB = 24000000
SYS_HZ = 144000000
DEVICE, FEED = 0, 1                      # usb_hid's contexts (.default CTX, FEED)
RUNNING = 1                              # a context's state in the status word
BUNDLE = "usb_hid"
ATTACH = 9                               # GP9 feeds the D- pull-up: attach once usb_hid runs


def push_reports(io, host, reports, timeout_ms):
    """Each report into context FEED's mailbox as two little-endian words, each only when
    the mailbox has room. Returns the number of reports pushed; raises Fail when a word
    waits more than timeout_ms."""
    for i, r in enumerate(reports):
        for w in (int.from_bytes(r[0:4], "little"), int.from_bytes(r[4:8], "little")):
            t0 = io.ticks_us()
            while (host.status()["rx_full"] >> FEED) & 1:
                if io.ticks_diff(io.ticks_us(), t0) > timeout_ms * 1000:
                    raise Fail("report %d of %d waited %d ms for room in the mailbox: nothing "
                               "is polling the keyboard, so the computer has not enumerated "
                               "it (D+ and D- swapped or open, the 1.5 kohm on D-, the port)"
                               % (i + 1, len(reports), timeout_ms))
                io.sleep_ms(1)
            host.mbx_push(FEED, w)
    return len(reports)


class UsbBench:
    def __init__(self, io, bundles="bundles", out=print):
        self.io, self.bundles, self.say = io, bundles, out
        self.b = bringup.Bringup(io, bundles, out=out, proof=None)
        self.host = None

    def setup(self):
        """The chip at 24 MHz with usb_hid loaded and started; both contexts running."""
        io = self.io
        io.select()                              # the project, a reset pulse, its clock
        hz = io.set_clock(F_USB)
        if hz != F_USB:
            raise Fail("the clock PWM makes %s Hz, not 24 MHz, from a %d Hz system clock: "
                       "usb_hid needs exactly 24 MHz (open_io(sys_hz=%d))"
                       % (hz, io.rp_clock(), SYS_HZ))
        io.reset(True)
        io.sleep_ms(2)
        io.reset(False)
        io.sleep_ms(2)
        self.b.connect()                         # CS_n high: the link is idle
        self.host = h = self.b.host
        st = h.status()
        if st["version"] < P.PAGES_VERSION or st["running"]:
            raise Fail("status after reset: %r (want version %d, stopped); run the bring-up "
                       "first (.\\bench.ps1 bringup)" % (st, P.PAGES_VERSION))
        bd = bringup.load_bundle(self.bundles + "/" + BUNDLE + ".json")
        words = list(bd["words"]) + [0] * (256 - len(bd["words"]))
        tape = [0] * 256
        for a, w in bd["tape"].items():
            tape[int(a)] = w
        self.b.load(words, bd["cfg"], tape)
        h.start()
        io.sleep_ms(5)
        st = h.status()
        for k in (DEVICE, FEED):
            if (st["ctx_state"] >> (2 * k)) & 3 != RUNNING or st["fault"]:
                raise Fail("usb_hid context %d is not running after START: %r" % (k, st))
        self.say("usb: chip at %.3f MHz (RP system clock %d MHz); usb_hid running"
                 % (hz / 1e6, io.rp_clock() // 1000000))

    def type(self, text, timeout_ms=1000, hold_ms=200):
        reports = escapement_demo.key_reports(text)
        n = push_reports(self.io, self.host, reports, timeout_ms)
        self.io.sleep_ms(hold_ms)                # the last report (all keys up) goes out
        st = self.host.status()
        if st["fault"]:
            raise Fail("usb_hid faulted while typing: %r" % st)
        return n


def run(text="escapement typed this\n", enumerate_ms=6000, hold_ms=1000, timeout_ms=1000,
        attach=ATTACH, bundles="bundles", io=None, out=print):
    """Set up the chip, attach the keyboard (the D- pull-up on GPIO `attach`; None: wired to
    3V3), wait `enumerate_ms`, type `text` into the focused window, then detach. Returns the
    number of key reports typed, or -1 on a failure."""
    if io is None:
        io = barepico.open_io(sys_hz=SYS_HZ)
    u = UsbBench(io, bundles, out)
    pull = None
    try:
        u.setup()
        if attach is not None:
            import machine
            pull = machine.Pin(attach, machine.Pin.OUT, value=1)
            out("usb: D- pull-up on (GP%d high): the keyboard is attached" % attach)
        out("usb: waiting %d ms for the computer to enumerate the keyboard" % enumerate_ms)
        io.sleep_ms(enumerate_ms)
        n = u.type(text, timeout_ms)
        io.sleep_ms(hold_ms)
    except Fail as x:
        out("usb: FAIL  %s" % x)
        n = -1
    finally:
        if pull is not None:
            pull.value(0)                        # detached before the chip stops answering
            io.sleep_ms(50)
    if n < 0:
        if u.host is not None:
            u.host.stop()
        return -1
    out("usb: typed %d characters in %d key reports, no fault: %r; detached"
        % (len(text), n, text))
    return n
