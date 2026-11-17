"""SWD on a real RP2040 (README.md, SWD): the chip runs firmware/swd_idcode.asm at 25 MHz and
debugs a second Pico through its 3-pin debug header, while the bench Pico W only pushes host
commands and pops the replies.

    import swdbench; swdbench.run()            (.\\bench.ps1 swd)

What it does, in order (ADIv5.2, ARM IHI 0031; RP2040 datasheet, 2.3.4 SWD):
  1. wake from dormant, line reset, TARGETSEL core 0 (0x01002927): no target answers it
  2. DPIDR: the RP2040's debug port, 0x0BC12477
  3. DLPIDR (DP 0x4 with DPBANKSEL 3): SWD protocol version 2, instance 0
  4. ABORT (clear sticky flags), CTRL/STAT power-up request, until both power-up ACKs
  5. AP 0 IDR (bank 0xF): the AHB-AP, 0x04770031
  6. AP 0 CSW (32-bit), TAR 0x00000010, DRW: the boot ROM's magic 'M' 'u' 0x01 at 0x10
Every access is a DP or AP read or write; nothing halts or writes the target's cores.
Wiring: J6 bottom row col 6 (uio[4]) to the target's SWCLK, col 5 (uio[5]) to SWDIO, the
target's debug GND to a free bench GND (the Pico W's pin 38); the Pico W's own wires to those
two holes (GP21, GP22) unplugged. The target is powered from its own USB.
"""
import barepico
import bringup
from bringup import Fail, P

CTX = 2                                    # swd_idcode's context
SYS_HZ = 125_000_000                       # the RP's clock: 25 MHz from the PWM exactly
F_CHIP = 25_000_000
RUNNING = 1

# command words: [31] connect, [30] wake; [4:1] APnDP, RnW, A[2], A[3] (swd_idcode.asm)
WAKE = 1 << 30
DP_RD = {0x0: 0x04, 0x4: 0x0C, 0x8: 0x14, 0xC: 0x1C}
DP_WR = {0x0: 0x00, 0x4: 0x08, 0x8: 0x10, 0xC: 0x18}
AP_RD = {a: v | 0x02 for a, v in DP_RD.items()}
AP_WR = {a: v | 0x02 for a, v in DP_WR.items()}
ACKS = {0: "-", 1: "OK", 2: "WAIT", 4: "FAULT"}

RP2040_CORE0 = 0x01002927                  # TARGETSEL: TARGETID 0x1002927, instance 0
RP2040_DPIDR = 0x0BC12477
AHB_AP_IDR = 0x04770031
ROM_MAGIC = 0x01754D                       # 'M', 'u', 0x01 at 0x10 (the fourth byte: version)


class SwdBench:
    def __init__(self, io, bundles="bundles", out=print, bundle="swd_idcode"):
        self.io, self.bundles, self.say, self.bundle = io, bundles, out, bundle
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
        b = bringup.load_bundle(self.bundles + "/" + self.bundle + ".json")
        words = list(b["words"]) + [0] * (256 - len(b["words"]))
        tape = [0] * 256
        for a, w in b["tape"].items():
            tape[int(a)] = w
        self.b.load(words, b["cfg"], tape)
        h.start()
        io.sleep_ms(5)
        st = h.status()
        if (st["ctx_state"] >> (2 * CTX)) & 3 != RUNNING or st["fault"]:
            raise Fail("swd_idcode is not running after START: %r" % st)
        self.say("swd: chip at %.3f MHz, swd_idcode running in context %d" % (hz / 1e6, CTX))

    def xfer(self, cmd, data=None, timeout_ms=500):
        """One command (and its write data): returns (status, data) from swd_idcode."""
        io, h = self.io, self.host
        h.mbx_push(CTX, cmd)
        if data is not None:
            h.mbx_push(CTX, data)
        got = []
        t0 = io.ticks_us()
        while len(got) < 2:
            v = h.mbx_pop(CTX)
            if v is not None:
                got.append(v)
            elif io.ticks_diff(io.ticks_us(), t0) > timeout_ms * 1000:
                raise Fail("no reply to command 0x%08x in %d ms (status %r)"
                           % (cmd, timeout_ms, h.status()))
        if h.status()["fault"]:
            raise Fail("the chip faulted on command 0x%08x: %r" % (cmd, h.status()))
        return got[0], got[1]

    def access(self, what, cmd, data=None, want_ack=1):
        st, v = self.xfer(cmd, data)
        ack = st & 7
        self.say("swd: %-34s ACK %-5s %s" % (what, ACKS.get(ack, "none (%d)" % ack),
                                            "" if data is not None else "0x%08x" % v))
        if ack != want_ack:
            raise Fail("%s: ACK %d, want %d (status 0x%x)" % (what, ack, want_ack, st))
        return v

    def step(self, k, targetsel=RP2040_CORE0):
        """Run step k of steps(targetsel); returns (its key, the value read)."""
        what, cmd, data, ack, key = steps(targetsel)[k]
        return key, self.access(what, cmd, data, ack)

    def session(self, targetsel=RP2040_CORE0):
        """Steps 1 to 6 of the header. Returns a dict of what was read."""
        r = {}
        for k in range(len(steps(targetsel))):
            key, v = self.step(k, targetsel)
            if key:
                r[key] = v
        if r["ctrlstat"] & 0xA0000000 != 0xA0000000:      # CSYSPWRUPACK, CDBGPWRUPACK
            raise Fail("no power-up ACK in CTRL/STAT: 0x%08x" % r["ctrlstat"])
        return r


def steps(targetsel=RP2040_CORE0):
    """The session as (what, command, write data or None, expected ACK, key of the value
    read or None): one list for the bench run and for swdjudge.py, which captures each."""
    return [("wake, TARGETSEL 0x%08x" % targetsel, WAKE | DP_WR[0xC], targetsel, 0, None),
            ("DP DPIDR", DP_RD[0x0], None, 1, "dpidr"),
            ("DP SELECT DPBANKSEL 3", DP_WR[0x8], 0x3, 1, None),
            ("DP DLPIDR", DP_RD[0x4], None, 1, "dlpidr"),
            ("DP SELECT 0", DP_WR[0x8], 0x0, 1, None),
            ("DP ABORT (clear sticky flags)", DP_WR[0x0], 0x1E, 1, None),
            ("DP CTRL/STAT power-up request", DP_WR[0x4], 0x50000000, 1, None),
            ("DP CTRL/STAT", DP_RD[0x4], None, 1, "ctrlstat"),
            ("DP SELECT AP 0 bank 0xF", DP_WR[0x8], 0xF0, 1, None),
            ("AP IDR (posted)", AP_RD[0xC], None, 1, None),
            ("DP RDBUFF: AP IDR", DP_RD[0xC], None, 1, "ap_idr"),
            ("DP SELECT AP 0 bank 0", DP_WR[0x8], 0x0, 1, None),
            ("AP CSW 32-bit", AP_WR[0x0], 0xA2000002, 1, None),
            ("AP TAR 0x00000010", AP_WR[0x4], 0x10, 1, None),
            ("AP DRW (posted)", AP_RD[0xC], None, 1, None),
            ("DP RDBUFF: ROM word at 0x10", DP_RD[0xC], None, 1, "rom10")]


def run(bundles="bundles", io=None, out=print, targetsel=RP2040_CORE0):
    """O1: the chip debugs a real RP2040. Prints each access and a verdict; returns the
    values read, or None on a failure."""
    if io is None:
        io = barepico.open_io(sys_hz=SYS_HZ)
    s = SwdBench(io, bundles, out)
    try:
        s.setup()
        r = s.session(targetsel)
    except Fail as x:
        out("swd: FAIL  %s" % x)
        if s.host is not None:
            s.host.stop()
        return None
    s.host.stop()
    checks = [("DPIDR", r["dpidr"] == RP2040_DPIDR, "0x%08x" % r["dpidr"]),
              ("DLPIDR: protocol version 2, instance %d" % (targetsel >> 28),
               r["dlpidr"] & 0xF0000000 == targetsel & 0xF0000000 and r["dlpidr"] & 0xF == 1,
               "0x%08x" % r["dlpidr"]),
              ("AHB-AP IDR", r["ap_idr"] == AHB_AP_IDR, "0x%08x" % r["ap_idr"]),
              ("boot ROM 'Mu' 0x01 at 0x10", r["rom10"] & 0xFFFFFF == ROM_MAGIC,
               "0x%08x (ROM version %d)" % (r["rom10"], r["rom10"] >> 24))]
    for name, ok, got in checks:
        out("swd: %s  %-40s %s" % ("PASS" if ok else "FAIL", name, got))
    out("swd: %s" % ("PASS: the chip read the RP2040's debug port, its AHB-AP and its boot ROM"
                     if all(ok for _, ok, _ in checks) else "FAIL"))
    return r
