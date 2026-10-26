# Escapement silicon bring-up on the Tiny Tapeout demo board (RP2040 or RP2350 running
# tt-micropython-firmware). The checklist that goes with it is docs/16-BRING-UP.md.
#
# Copy to the board: this file, sw/eschost/protocol.py (as protocol.py) and the bundle
# directory written by `python sw/rp2350/make_bundle.py bundles`. Then, at the REPL:
#
#     import bringup
#     bringup.run()                       # stages 0-5, stop at the first failure
#     bringup.run(keep_going=True)        # run every stage and report all failures
#     bringup.run(optional=True)          # also stage 6 (UART smoke test, drives ui[7])
#     bringup.run(stages=(0, 1, 2))       # only some stages
#
# Stages: 0 board and project, 1 clock and reset, 2 host link, 3 memories, 4 pins and
# clock rate, 5 firmware digests and timing certificates against the model, 6 (optional)
# UART smoke test. Each
# stage prints PASS or FAIL with a diagnosis. The same code runs under CPython against a
# simulated demo board (sw/tests/test_bringup.py and test/test_bringup_rtl.py).
#
# Board API, checked on 2026-10-17 against tt-micropython-firmware main at d485c7a
# (github.com/TinyTapeout/tt-micropython-firmware: README.md, src/ttboard/demoboard.py,
# src/ttboard/util/platform/rp2040.py and rp2350.py, src/ttboard/pins/standard.py) and
# microcotb 81f2498 (src/microcotb/types/logic.py):
#   DemoBoard.get(), tt.mode = RPMode.ASIC_RP_CONTROL, tt.shuttle.has(name),
#   tt.shuttle.<name>.enable(), tt.clock_project_PWM(hz), tt.auto_clocking_freq,
#   tt.reset_project(bool), tt.ui_in[bit] = v, tt.uo_out[bit] (a Logic: use int(), it
#   has no & with an int), tt.uio_in.value, tt.uio_oe_pico.value, tt.pins.uioN.pull,
#   tt.pins.<pin>.raw_pin / .gpio_num, and the "Fast I/O" byte functions in
#   ttboard.util.platform (write_ui_in_byte, read_ui_in_byte, read_uo_out_byte,
#   read_uio_byte). machine.time_pulse_us and machine.UART are MicroPython's
#   (docs.micropython.org/en/latest/library/machine.html, ports/rp2/machine_uart.c).

import json
import time as _time

try:
    import protocol as P                  # on the board
except ImportError:                       # on a PC (tests): sw/eschost
    from eschost import protocol as P

PROJECT = "tt_um_escapement"
F_CLK = 25000000
VERSION = 8
M32 = 0xFFFFFFFF
DG_POLY = 0x04C11DB7
CS, SCK, MOSI = 1, 2, 4                   # ui_in bits (docs/12-HOST-PROTOCOL.md)
MISO, IRQ, RUN = 1, 2, 4                  # uo_out bits
UO_RESET = 0xF8                           # in and after reset: bank B (uo[7:3]) released high
STATE = ("IDLE", "RUNNING", "HALTED", "FAULTED")
CAUSE = {1: "ILLEGAL", 2: "PC_RANGE", 3: "ADMIT", 4: "LATE_HEAD"}
RESET_STATUS = b"\x08\x00\x0f\x00"        # version 8, all IDLE, TX mailboxes empty
MIN_HALF_NS = 8 * 1000000000 // F_CLK     # bit-bang margin: 2x the 4-clock SCK minimum
# Every data bit at 0 and 1, and every pair of bits different in at least one word.
DATA_LINE_PATTERNS = (0, M32, 0x55555555, 0xAAAAAAAA, 0x33333333, 0xCCCCCCCC, 0x0F0F0F0F,
                      0xF0F0F0F0, 0x00FF00FF, 0xFF00FF00, 0x0000FFFF, 0xFFFF0000)

# Stage 4 test programs, assembled with sw/escasm (sw/tests/test_bringup.py checks the
# words against the assembler):
#   SQUARE: .equ HALF, 12500 / .entry 0, start / .own 0..7, 0
#           start: ADDT 64 / EMIT 0xFF, 0x55, 0xFF
#           loop:  ADDT HALF / EMIT 0xFF, 0xAA, 0xFF / ADDT HALF / EMIT 0xFF, 0x55, 0xFF
#                  JMP loop
#   GETP:   .entry 0, start / start: GETP r0 / JMP start          (owns no pin)
#   BSQUARE: SQUARE on pin bank B: .bank 0 / .own 0..4, 0 / EMIT 0x1F, 0x15 (0x0A), 0x1F
# GETP with context 0 on bank B (GETP_B_CFG) reads {000, ui[7:3]}.
SQUARE = [0x22000040, 0x28FF55FF, 0x220030D4, 0x28FFAAFF, 0x220030D4, 0x28FF55FF,
          0x18000002]
SQUARE_CFG = (1, 0, 0xFF, 0, 0)
SQUARE_HALF = 12500                       # ticks per half period (500 us at 25 MHz)
GETP = [0x30000000, 0x18000000]
GETP_CFG = (1, 0, 0, 0, 0)
BSQUARE = [0x22000040, 0x281F151F, 0x220030D4, 0x281F0A1F, 0x220030D4, 0x281F151F,
           0x18000002]
BSQUARE_CFG = (1, 0, 0x1F00, 0, 0, 1)
GETP_B_CFG = (1, 0, 0, 0, 0, 1)
# v1.3: context 0 uses the tape port in two of every three commits (STT, LDT, JMP)
STORM = [0x09800010, 0x35600000, 0x34E00001, 0x18000001]
STORM_CFG = (1, 0, 0, 0, 0)
# v1.3: logic_analyzer.asm with STREAM 1, MASK 0x01: uio[0] streamed into tape 0x80-0x87
STREAM_LA = [0x09008880, 0x0A008401, 0x36000000, 0x02000000]
STREAM_LA_CFG = (1, 0, 0, 0, 0)
STREAM_BASE, STREAM_LIM = 0x80, 0x88


class Fail(Exception):
    """A stage failed; the message is the diagnosis."""


def bits(v, n=8):
    return "".join("1" if (v >> i) & 1 else "0" for i in range(n - 1, -1, -1))


def pins_of(mask):
    return ",".join(str(i) for i in range(8) if (mask >> i) & 1)


def bpins_of(mask):
    return ",".join("B%d (uo[%d])" % (i, i + 3) for i in range(5) if (mask >> i) & 1)


def _input_diagnosis(reads, width, prefix):
    """Per input bit of (driven, read) pairs: stuck, crossed or inconsistent."""
    msg = ""
    for i in range(width):
        col = [(g >> i) & 1 for p, g in reads]
        if col == [(p >> i) & 1 for p, g in reads]:
            continue
        if all(col):
            msg += " Input %s%d stuck at 1." % (prefix, i)
        elif not any(col):
            msg += " Input %s%d stuck at 0." % (prefix, i)
        else:
            src = [j for j in range(width) if col == [(p >> j) & 1 for p, g in reads]]
            msg += (" Input %s%d follows pad %s%d (crossed or shorted)."
                    % (prefix, i, prefix, src[0])
                    if src else " Input %s%d reads inconsistently." % (prefix, i))
    return msg


def fold(d, x):
    """One active edge of the execution digest (docs/11-SEMANTICS.md 10.1)."""
    return (((d << 1) & M32) ^ (DG_POLY if d >> 31 else 0) ^ x) & M32


def find_on_orbit(dg0, folds, target, m_max):
    d, n = dg0, len(folds)
    for m in range(m_max + 1):
        if d == target:
            return m
        d = fold(d, folds[m % n])
    return None


# ------------------------------------------------------------------------ board access

class DemoBoardIO:
    """Everything bring-up needs from the demo board, on top of tt-micropython-firmware.
    The tests construct it with simulated tt/platform/machine/time objects."""

    def __init__(self, tt, platform, machine, time_mod, rpmode):
        self.tt, self.platform, self.machine, self.time = tt, platform, machine, time_mod
        self.rpmode = rpmode

    @classmethod
    def open(cls):
        import machine
        import ttboard.util.platform as platform
        from ttboard.demoboard import DemoBoard
        from ttboard.mode import RPMode
        return cls(DemoBoard.get(), platform, machine, _time, RPMode)

    # project selection, clock, reset
    def select(self):
        tt = self.tt
        tt.mode = self.rpmode.ASIC_RP_CONTROL
        if not tt.shuttle.has(PROJECT):
            raise Fail("%s is not in this board's shuttle index (shuttle %s). Update the "
                       "tt-micropython-firmware release, or check the chip on the carrier."
                       % (PROJECT, getattr(tt.shuttle, "run", "?")))
        getattr(tt.shuttle, PROJECT).enable()
        en = tt.shuttle.enabled
        if en is None or getattr(en, "name", "") != PROJECT:
            raise Fail("enable() did not select %s (enabled: %s)" % (PROJECT, en))
        chip = "RP2350" if getattr(self.platform, "IsRP2350", False) else "RP2040"
        return "%s, SDK %s, shuttle %s" % (chip, getattr(tt, "version", "?"),
                                           getattr(tt.shuttle, "run", "?"))

    def set_clock(self, hz):
        self.tt.clock_project_PWM(hz)
        return self.tt.auto_clocking_freq

    def rp_clock(self):
        return self.platform.get_RP_system_clock()

    def reset(self, hold):
        self.tt.reset_project(hold)

    # pins: fast byte access (README "Fast I/O") and the slower per-bit port API
    def write_ui(self, v):
        self.platform.write_ui_in_byte(v)

    def read_ui(self):
        return self.platform.read_ui_in_byte()

    def read_uo(self):
        return self.platform.read_uo_out_byte()

    def read_uio(self):
        return self.platform.read_uio_byte()

    def set_ui_bit(self, i, v):
        self.tt.ui_in[i] = v

    def uo_bit(self, i):
        return int(self.tt.uo_out[i])

    def _pin(self, name):
        return getattr(self.tt.pins, name)

    def uio_pullups(self):
        for i in range(8):
            self._pin("uio%d" % i).pull = self.machine.Pin.PULL_UP

    def uio_drive(self, oe, value):
        self.tt.uio_in.value = value          # applies to RP outputs only
        self.tt.uio_oe_pico.value = oe

    def pulse_us(self, pin, level, timeout_us):
        return self.machine.time_pulse_us(self._pin("uio%d" % pin).raw_pin, level, timeout_us)

    def uart_open(self, pin, baud):
        """A hardware UART receiving on uio[pin]. Its TX goes to ui[7], which the design
        ignores (MicroPython always claims a TX pin). Returns (uart, close)."""
        m = self.machine
        rx, tx = self._pin("uio%d" % pin), self._pin("ui_in7")
        uart_id = ((rx.gpio_num + 4) & 8) >> 3     # rp2 machine_uart.c IS_VALID_PERIPH
        u = m.UART(uart_id, baudrate=baud, rx=m.Pin(rx.gpio_num), tx=m.Pin(tx.gpio_num),
                   timeout=10)

        def close():
            u.deinit()
            rx.mode = m.Pin.IN
            rx.pull = m.Pin.PULL_UP
            tx.mode = m.Pin.OUT
            self.write_ui(CS)
        return u, close

    # time
    def ticks_us(self):
        return self.time.ticks_us()

    def ticks_diff(self, a, b):
        return self.time.ticks_diff(a, b)

    def sleep_us(self, n):
        self.time.sleep_us(n)

    def sleep_ms(self, n):
        self.time.sleep_ms(n)


class Link:
    """One CS_n-low SPI frame per xfer(), bit-banged on ui[2:0] / uo[0], mode 0. MISO is
    sampled BEFORE each SCK rise: the chip moves MISO about 3 core clocks after it sees
    the rise, far sooner than MicroPython reads a pin, so a read after the edge would
    return the next bit. fast=True uses the byte functions; fast=False the per-bit port
    API with slow_us of extra delay after every pin change."""

    def __init__(self, io, fast=True, slow_us=0):
        self.io, self.fast, self.slow_us = io, fast, slow_us
        self.t_begin = self.t_end = self.nbits = 0
        self.hi = 0                           # held on ui[7:3] (bank B inputs) by every write
        io.write_ui(CS)

    def xfer(self, data):
        io = self.io
        self.t_begin = io.ticks_us()
        out = self._fast(data) if self.fast else self._slow(data)
        self.t_end = io.ticks_us()
        self.nbits = 8 * len(data)
        return out

    def half_ns(self):
        """Average SCK half period of the last frame."""
        return self.io.ticks_diff(self.t_end, self.t_begin) * 1000 // max(2 * self.nbits, 1)

    def _fast(self, data):
        w, r = self.io.platform.write_ui_in_byte, self.io.platform.read_uo_out_byte
        out = bytearray(len(data))
        h = self.hi
        w(h)
        for n in range(len(data)):
            byte, v = data[n], 0
            for i in range(7, -1, -1):
                m = (MOSI if (byte >> i) & 1 else 0) | h
                w(m)
                v = (v << 1) | (r() & 1)
                w(m | SCK)
                w(m)
            out[n] = v
        w(CS | h)
        return bytes(out)

    def _slow(self, data):
        io, d = self.io, self.slow_us

        def put(bit, v):
            io.set_ui_bit(bit, v)
            if d:
                io.sleep_us(d)
        out = bytearray(len(data))
        put(0, 0)
        for n in range(len(data)):
            byte, v = data[n], 0
            for i in range(7, -1, -1):
                put(2, (byte >> i) & 1)
                v = (v << 1) | io.uo_bit(0)
                put(1, 1)
                put(1, 0)
            out[n] = v
        put(0, 1)
        return bytes(out)


# ------------------------------------------------------------------------ stages

class Bringup:
    def __init__(self, io, bundles="bundles", quick=False, out=print, proof=None):
        self.io, self.bundles, self.quick, self.say = io, bundles, quick, out
        self.proof_file = proof
        self.link = self.host = None
        self.f_clk = F_CLK                    # replaced by the stage 4 measurement
        self.results = []
        self.proof = {}                       # stage 5: bundle name -> chip telemetry

    # -- helpers
    def connect(self):
        self.link = Link(self.io, fast=True)
        self.host = P.Host(self.link.xfer)

    def load(self, words, cfg, tape=None, verify=True):
        h = self.host
        h.stop()
        h.write_imem(0, words)
        if verify:
            got = h.read_imem(0, len(words))
            if got != list(words):
                bad = [a for a in range(len(words)) if got[a] != words[a]]
                raise Fail("program readback differs at %d words (first at 0x%02x: wrote "
                           "0x%08x, read 0x%08x); stage 3 should have found this"
                           % (len(bad), bad[0], words[bad[0]], got[bad[0]]))
        if tape is not None:
            h.write_tape(0, tape)
            if verify and h.read_tape(0, len(tape)) != list(tape):
                raise Fail("tape readback differs; stage 3 should have found this")
        h.write_cfg(*cfg)
        h.clear_err()

    # -- 0
    def stage0(self):
        io = self.io
        info = io.select()
        for v in (0, 7, 5, 2, CS):
            io.write_ui(v)
            io.sleep_us(10)
            got = io.read_ui() & 7
            if got != v:
                raise Fail("ui[2:0] reads %s after driving %s: an input DIP switch is on, "
                           "or something else drives ui[%s] (the SDK will not drive a ui "
                           "pin that reads high when the mode is set). Switch all input "
                           "DIP switches off and rerun." % (bits(got, 3), bits(v, 3),
                                                            pins_of(got ^ v)))
        return info

    # -- 1
    def stage1(self):
        io = self.io
        hz = io.set_clock(F_CLK)
        if not hz or abs(hz - F_CLK) > F_CLK // 1000:
            raise Fail("clock_project_PWM(%d) reports %s Hz (RP system clock %s Hz). The "
                       "project clock is the RP's PWM; check config.ini for a "
                       "[%s] section that overrides clock_frequency or rp_clock_frequency."
                       % (F_CLK, hz, io.rp_clock(), PROJECT))
        io.write_ui(CS)
        io.reset(True)
        io.sleep_ms(1)
        uo = io.read_uo()
        if uo != UO_RESET:
            raise Fail("uo_out = %s while rst_n is low; expected %s: MISO, IRQ and RUN low, "
                       "pin bank B (uo[7:3]) released high. Bits %s differ: an output "
                       "stuck, a short, or the wrong design selected."
                       % (bits(uo), bits(UO_RESET), pins_of(uo ^ UO_RESET)))
        io.reset(False)
        io.sleep_ms(1)
        uo = io.read_uo()
        if uo != UO_RESET:
            raise Fail("uo_out = %s after reset with CS_n high; expected %s (status bit 7, "
                       "'running', is 0 after reset; bank B released). Bits %s differ."
                       % (bits(uo), bits(UO_RESET), pins_of(uo ^ UO_RESET)))
        io.uio_pullups()
        io.sleep_us(200)
        uio = io.read_uio()
        if uio != 0xFF:
            raise Fail("uio pins %s read 0 with only the RP pull-ups after reset; the chip "
                       "must not drive any uio pin after reset (uio_oe = 0). Unplug "
                       "everything from the bidirectional Pmod; if it persists, the pin "
                       "is driven low by the chip or shorted." % pins_of(~uio & 0xFF))
        return "PWM %d Hz (RP %s Hz), uo = %s in and after reset, uio released" % (
            hz, io.rp_clock(), bits(UO_RESET))

    def _status_diagnosis(self, got):
        g = int.from_bytes(got, "big")
        e = int.from_bytes(RESET_STATUS, "big")
        if g == 0:
            return ("MISO is always 0: the chip does not answer. Check that the project "
                    "clock runs (scope the clk pin: 25 MHz), rst_n is high, the design "
                    "is selected, and CS_n/SCK reach ui[0]/ui[1].")
        if g == M32:
            return "MISO is always 1: uo[0] stuck high or not driven by the design."
        if g == (e << 1) & M32 or g == ((e << 1) | 1) & M32:
            return ("the answer is one bit late (MISO sampled after the SCK rising edge, "
                    "or the host is much faster than expected).")
        if (g << 1) & M32 == e & ~1 & M32:
            return "the answer is one bit early (MISO sampled before the chip drove it)."
        v = got[0] & 0x0F
        if v == 5:
            return "protocol version 5: an Escapement v1.0 design answers, not v1.3."
        if v == 6:
            return "protocol version 6: an Escapement v1.1 design answers, not v1.3."
        if v == 7:
            return "protocol version 7: an Escapement v1.2 design answers, not v1.3."
        if v != VERSION:
            return ("protocol version %d: a different design is answering; check the "
                    "project selection (stage 0)." % v)
        if got[0] & 0x80:
            return "the running bit is set right after reset: reset did not reach the chip."
        if got[2] != 0x0F:
            return ("the chip did not decode STATUS (byte 2 should be 0x0F, empty TX "
                    "mailboxes): MOSI (ui[2]) is stuck high or floating.")
        return ("unexpected answer %s; expected %s. Capture CS_n, SCK, MOSI and MISO with "
                "a logic analyser." % (_hex(got), _hex(RESET_STATUS)))

    # -- 2
    def stage2(self):
        io = self.io
        self.connect()
        link, host = self.link, self.host
        # timing of the bit-bang itself: the SCK high phase is one raw pin write
        n, w = 500, io.platform.write_ui_in_byte
        t0 = io.ticks_us()
        for _ in range(n):
            w(CS)
        call_ns = io.ticks_diff(io.ticks_us(), t0) * 1000 // n
        frame = link.xfer(bytes(32))
        half_ns = link.half_ns()
        if call_ns < MIN_HALF_NS:
            raise Fail("one pin write takes %d ns; the SCK high phase is one pin write and "
                       "must stay above %d ns (4 core clocks, doubled for margin). Slow "
                       "the link down (Link(fast=False)) or lower the RP clock."
                       % (call_ns, MIN_HALF_NS))
        if frame[:4] != RESET_STATUS:
            raise Fail(self._status_diagnosis(frame[:4]))
        # MOSI: an unknown command must set host error, CLEAR_ERR must clear it
        link.xfer(b"\x99")
        if not host.status()["host_err"]:
            raise Fail("an unknown command (0x99) did not set host error: the chip sees "
                       "MOSI as 0, so ui[2] is stuck low or not connected.")
        host.clear_err()
        st = host.status()
        if st["host_err"]:
            raise Fail("CLEAR_ERR (0x51) did not clear host error: MOSI bits are corrupted.")
        # known 32-bit answers after reset, through the 5-bit READ_DBG select
        expect = [(0, 16, M32)] + [(k, 9, 0xFFFF0000) for k in range(4)]
        for k, sel, want in expect:
            got = host.read_dbg(k, sel)
            if got != want:
                raise Fail("READ_DBG ctx %d sel %d = 0x%08x after reset, expected 0x%08x "
                           "(%s)." % (k, sel, got, want,
                                      "digest reset value" if sel == 16 else "min_lead 0xFFFF"))
        # repeat at the fast and the slow rate to catch marginal timing
        reps = 20 if self.quick else 200
        errors = 0
        for _ in range(reps):
            if link.xfer(bytes(4)) != RESET_STATUS:
                errors += 1
            k = _ & 3
            if host.read_dbg(k, 9) != 0xFFFF0000:
                errors += 1
        slow = Link(io, fast=False, slow_us=5 if self.quick else 50)
        slow_host = P.Host(slow.xfer)
        sreps = 2 if self.quick else 10
        serr = 0
        for _ in range(sreps):
            if slow.xfer(bytes(4)) != RESET_STATUS:
                serr += 1
            if slow_host.read_dbg(0, 16) != M32:
                serr += 1
        if errors or serr:
            raise Fail("%d of %d frames wrong at the fast rate, %d of %d at the slow rate. "
                       "Errors only at the fast rate: marginal SCK timing or MISO slew; "
                       "at both: noise or a loose connection on ui[2:0]/uo[0]."
                       % (errors, 2 * reps, serr, 2 * sreps))
        return ("STATUS %s x %d; one pin write %d ns; SCK half period about %d ns (fast) "
                "and %d ns (slow)" % (_hex(RESET_STATUS), reps + 1, call_ns, half_ns,
                                      slow.half_ns()))

    # -- 3
    def stage3(self):
        """Per SRAM: (a) data lines, one word at two addresses, so an address fault
        cannot hide or fake a data fault; (b) address lines, 'address in data' over all
        256 words, where an aliased read names the address it came from; (c) cells,
        whole-array patterns. The program SRAM gets each pattern and the tape its
        inverse, which also shows that the two spaces are independent."""
        host = self.host
        host.stop()
        spaces = (("program SRAM", host.write_imem, host.read_imem, 0),
                  ("tape SRAM", host.write_tape, host.read_tape, M32))
        report = []
        stuck = {}
        for sname, wr, rd, flip in spaces:
            ones = zeros = 0
            same = [M32] * 32                 # same[i]: bits that always read like bit i
            for a in (0x00, 0xFF):
                for p in DATA_LINE_PATTERNS:
                    wr(a, [p])
                    g = rd(a, 1)[0]
                    ones |= g
                    zeros |= g ^ M32
                    for i in range(32):
                        same[i] &= ~(g ^ (M32 if (g >> i) & 1 else 0)) & M32
            s0, s1 = M32 & ~ones, M32 & ~zeros
            msg = []
            if s0:
                msg.append("data bits %s never read 1 (stuck at 0)" % _bitlist(s0))
            if s1:
                msg.append("data bits %s never read 0 (stuck at 1)" % _bitlist(s1))
            pairs = ["%d=%d" % (i, j) for i in range(32) for j in range(i + 1, 32)
                     if (same[i] >> j) & 1 and not ((s0 | s1) >> i) & 1]
            if pairs:
                msg.append("data bits always equal (shorted): %s" % ", ".join(pairs[:6]))
            stuck[sname] = (s0, s1)
            if msg:
                report.append("%s: %s" % (sname, "; ".join(msg)))
        if report:
            if stuck["program SRAM"] == stuck["tape SRAM"]:
                report.append("both memories show the same data faults: suspect the shared "
                              "host data path (escapement_host), not one macro")
            raise Fail(" | ".join(report))
        problems = []
        pats = _patterns(self.quick)
        for name, data in pats:
            for sname, wr, rd, flip in spaces:
                wr(0, [w ^ flip for w in data])
            for sname, wr, rd, flip in spaces:
                got = rd(0, 256)
                bad = [a for a in range(256) if got[a] ^ flip != data[a]]
                if not bad:
                    continue
                msg = "%s, %s pattern: %d of 256 words wrong" % (sname, name, len(bad))
                if name == "address in data":
                    xors = set()
                    for a in bad:
                        src = _addr_of(got[a] ^ flip)
                        xors.add(src ^ a if src is not None else -1)
                    single = sorted(x for x in xors if x > 0 and not x & (x - 1))
                    multi = sorted(x for x in xors if x > 0 and x & (x - 1))
                    if single:
                        msg += ("; address bit(s) %s have no effect (stuck or open: address a "
                                "reads the word written to a^2^b)"
                                % ",".join(str(_log2(x)) for x in single))
                    if multi:
                        msg += ("; addresses alias across bit groups %s (shorted address "
                                "lines)" % ",".join("0x%02x" % x for x in multi))
                    if -1 in xors:
                        msg += "; some words are not any address's word (cell faults)"
                a = bad[0]
                msg += "; e.g. @0x%02x wrote 0x%08x read 0x%08x" % (a, data[a] ^ flip, got[a])
                if rd(0, 256) != got:
                    msg += "; two reads of the same contents differ: unstable reads, " \
                           "suspect supply or read timing"
                problems.append(msg)
            if problems:
                raise Fail(" | ".join(problems))
        self._tape_during_run()
        return ("data lines at 2 addresses, %d patterns x 256 words, in both SRAMs; spaces "
                "independent; tape reads during a run while the core uses the port"
                % len(pats))

    def _tape_during_run(self):
        """v1.3: READ_TAPE while a program keeps the tape port busy in two of every three
        of its commits. Every word must be right, and fetched in time (no host error)."""
        host = self.host
        pat = [(0x9E3779B9 * (i + 1)) & M32 for i in range(32)]
        host.write_tape(0x80, pat)
        self.load(STORM, STORM_CFG)
        host.start()
        got = host.read_tape(0x80, 32)
        st = host.clear_err()                 # the status before the clear
        host.stop()
        bad = [i for i in range(32) if got[i] != pat[i]]
        if bad:
            a = bad[0]
            raise Fail("tape read during a run: %d of 32 words wrong, e.g. @0x%02x wrote "
                       "0x%08x read 0x%08x, while the program used the tape port. The same "
                       "words read correctly while stopped (above), so suspect the v1.3 "
                       "port arbitration (tape_busy, host grant, the host's word latch)."
                       % (len(bad), 0x80 + a, pat[a], got[a]))
        if st["host_err"]:
            raise Fail("a tape read during a run was not fetched before it was due (host "
                       "error), although the program leaves a third of the cycles free: "
                       "the host's fetch request is not being granted.")

    # -- 4
    def stage4(self):
        io, host = self.io, self.host
        self.load(SQUARE, SQUARE_CFG)
        host.start()
        seen = {}
        n = 100 if self.quick else 400
        for _ in range(n):
            v = io.read_uio()
            seen[v] = seen.get(v, 0) + 1
            io.sleep_us(23)
        odd = dict((v, c) for v, c in seen.items() if v not in (0x55, 0xAA))
        if len(seen) - len(odd) < 2 or sum(odd.values()) > 2:
            host.stop()
            hi = lo = 0
            for v in seen:
                hi |= v
                lo |= ~v & 0xFF
            msg = "uio levels seen while the chip drives 0x55/0xAA alternately: %s." % (
                ", ".join("%s x%d" % (bits(v), c) for v, c in sorted(seen.items())))
            if ~hi & 0xFF:
                msg += " Pins %s never read 1." % pins_of(~hi & 0xFF)
            if ~lo & 0xFF:
                msg += " Pins %s never read 0 (output driver dead, or pulled up harder " \
                       "than the chip drives)." % pins_of(~lo & 0xFF)
            for i in range(7):
                if all(((v >> i) ^ (v >> (i + 1))) & 1 == 0 for v in seen):
                    msg += " Pins %d and %d always read the same: shorted?" % (i, i + 1)
            raise Fail(msg)
        # clock rate: one SQUARE period is 2*HALF core clocks
        if io.pulse_us(0, 0, 5000) < 0:
            host.stop()
            raise Fail("uio[0] did not toggle for time_pulse_us (no edge in 5 ms).")
        periods = 2 if self.quick else 8
        t0 = io.ticks_us()
        for _ in range(periods):
            if io.pulse_us(0, 1, 5000) < 0 or io.pulse_us(0, 0, 5000) < 0:
                host.stop()
                raise Fail("uio[0] stopped toggling during the period measurement.")
        period_us = io.ticks_diff(io.ticks_us(), t0) / periods
        f = 2 * SQUARE_HALF * 1000000 / period_us
        host.stop()
        io.sleep_us(50)
        if abs(f - F_CLK) > F_CLK / 100:
            raise Fail("the chip runs at %.3f MHz (one %d-tick period took %.1f us), not "
                       "%.3f MHz. The RP set its PWM to %s Hz in stage 1; scope the clk "
                       "pin, and check that nothing else reprograms the RP clock."
                       % (f / 1e6, 2 * SQUARE_HALF, period_us, F_CLK / 1e6,
                          self.io.tt.auto_clocking_freq))
        self.f_clk = f
        uio = io.read_uio()
        if uio != 0xFF:
            raise Fail("after STOP, uio pins %s still read 0: STOP must release every pin "
                       "(oe = 0)." % pins_of(~uio & 0xFF))
        # inputs: the chip owns no pin and copies uio_in into r0 every 8 ticks
        self.load(GETP, GETP_CFG)
        host.start()
        io.sleep_us(50)
        if io.read_uio() != 0xFF:
            host.stop()
            raise Fail("a uio pin reads 0 although the running program owns no pin.")
        pats = [0x00, 0xFF, 0x55, 0xAA] + [1 << i for i in range(8)] + \
               [0xFF ^ (1 << i) for i in range(8)]
        reads = []
        try:
            for p in pats:
                io.uio_drive(0xFF, p)
                io.sleep_us(30)
                reads.append((p, host.read_dbg(0, 0) & 0xFF))
        finally:
            io.uio_drive(0, 0xFF)
            io.uio_pullups()
            host.stop()
        wrong = [(p, g) for p, g in reads if p != g]
        if wrong:
            msg = "the chip read the wrong uio levels (GETP r0): %s." % ", ".join(
                "drove %s read %s" % (bits(p), bits(g)) for p, g in wrong[:4])
            raise Fail(msg + _input_diagnosis(reads, 8, ""))
        self._bank_b(n)
        k = self._stream()
        return ("uio[7:0] toggle as driven; chip clock %.4f MHz from a %d-tick period; "
                "STOP releases pins; all 8 inputs read correctly; bank B: 5 outputs toggle "
                "and release high, 5 inputs read correctly; streaming capture: %d edges "
                "through an 8-word ring" % (f / 1e6, 2 * SQUARE_HALF, k))

    def _stream(self, toggles=16):
        """v1.3 streaming capture: the RP toggles uio[0] while the chip streams the edges
        into an 8-word ring and the host reads and acknowledges them. Every edge must
        arrive, in order, with no overflow: more than twice what the ring holds."""
        io, host = self.io, self.host
        self.load(STREAM_LA, STREAM_LA_CFG)
        io.uio_drive(0x01, 0x01)
        words, rptr, ovf = [], STREAM_BASE, False
        try:
            host.start()
            io.sleep_us(50)
            for i in range(toggles):
                io.uio_drive(0x01, i & 1)     # 0, 1, 0, ...: one edge each
                io.sleep_us(60)
                w, rptr, st = host.stream_poll(STREAM_BASE, STREAM_LIM, rptr)
                words += w
                ovf |= bool((st >> 30) & 1)
            io.sleep_us(60)
            host.stop()
            w, rptr, st = host.stream_poll(STREAM_BASE, STREAM_LIM, rptr)
            words += w
            ovf |= bool((st >> 30) & 1)
        finally:
            io.uio_drive(0, 0xFF)
            io.uio_pullups()
            host.stop()
        levels = [(x >> 24) & 1 for x in words]
        ticks = [x & 0xFFFFFF for x in words]
        want = [1] + [i & 1 for i in range(toggles)]
        # The records pass through the tape, so a tape fault from stage 3 shows up here too.
        tape = ("; the memories stage failed, and the records are stored in the tape"
                if any(n == 3 and not ok for n, ok, _ in self.results) else "")
        if ovf:
            raise Fail("streaming capture overflowed (ovf) with only %d edges: the host "
                       "read and acknowledged after every edge, so the ring should never "
                       "fill. Check TAPE_ACK (the capture's rptr, READ_DBG sel 20)%s."
                       % (toggles, tape))
        if levels != want or any(b <= a for a, b in zip(ticks, ticks[1:])):
            raise Fail("streaming capture: uio[0] levels %s (ticks %s), expected %s: %s%s"
                       % ("".join(map(str, levels)), ticks[:6], "".join(map(str, want)),
                          "records lost" if len(levels) < len(want) else "wrong records",
                          tape))
        return toggles

    def _bank_b(self, n):
        """Pin bank B: the square wave on uo[7:3], then GETP on ui[7:3]."""
        io, host, link = self.io, self.host, self.link
        self.load(BSQUARE, BSQUARE_CFG)
        host.start()
        seen = {}
        for _ in range(n):
            v = io.read_uo() >> 3
            seen[v] = seen.get(v, 0) + 1
            io.sleep_us(23)
        host.stop()
        io.sleep_us(50)
        odd = dict((v, c) for v, c in seen.items() if v not in (0x15, 0x0A))
        if len(seen) - len(odd) < 2 or sum(odd.values()) > 2:
            hi = lo = 0
            for v in seen:
                hi |= v
                lo |= ~v & 0x1F
            msg = ("bank B levels (uo[7:3]) seen while the chip drives 10101/01010 "
                   "alternately: %s." % ", ".join("%s x%d" % (bits(v, 5), c)
                                                  for v, c in sorted(seen.items())))
            if ~hi & 0x1F:
                msg += " Outputs %s never read 1." % bpins_of(~hi & 0x1F)
            if ~lo & 0x1F:
                msg += " Outputs %s never read 0 (stuck high)." % bpins_of(~lo & 0x1F)
            raise Fail(msg)
        ob = io.read_uo() >> 3
        if ob != 0x1F:
            raise Fail("after STOP, bank B outputs %s read 0: a released bank B output must "
                       "be high." % bpins_of(~ob & 0x1F))
        self.load(GETP, GETP_B_CFG)
        host.start()
        pats = [0x00, 0x1F, 0x15, 0x0A] + [1 << i for i in range(5)] + \
               [0x1F ^ (1 << i) for i in range(5)]
        reads = []
        try:
            for p in pats:
                link.hi = p << 3
                io.write_ui(CS | link.hi)
                io.sleep_us(30)
                reads.append((p, host.read_dbg(0, 0) & 0xFF))
        finally:
            link.hi = 0
            io.write_ui(CS)
            host.stop()
        wrong = [(p, g) for p, g in reads if p != g]
        if wrong:
            msg = ("the chip read the wrong bank B levels (GETP r0 on bank B, ui[7:3]): %s."
                   % ", ".join("drove %s read %s" % (bits(p, 5), bits(g))
                               for p, g in wrong[:4]))
            if any(g >> 5 for p, g in reads):
                msg += " Logical pins 5..7 read 1: the context is not on bank B."
            raise Fail(msg + _input_diagnosis(reads, 5, "B") + " ui[7:3] are also the "
                       "input DIP switches: switch them all off.")

    # -- 5
    def stage5(self):
        files = _bundle_files(self.bundles)
        if not files:
            raise Fail("no bundles in '%s': run `python sw/rp2350/make_bundle.py bundles` "
                       "on a PC and copy the directory to the board." % self.bundles)
        self.io.uio_pullups()
        bad = []
        for fn in files:
            b = load_bundle(self.bundles + "/" + fn)
            ok, line, diag = self.run_bundle(b)
            self.say("    %s" % line)
            if not ok:
                bad.append(b["name"])
                self.say("      " + diag)
        if self.proof_file:                   # for test/cosim/proof_table.py --board
            try:
                with open(self.proof_file, "w") as f:
                    json.dump(self.proof, f)
            except OSError:
                pass
        if bad:
            raise Fail("%d of %d firmware differ from the model: %s (details above)."
                       % (len(bad), len(files), ", ".join(bad)))
        return "%d of %d firmware match the model and their certificates" % (len(files), len(files))

    def run_bundle(self, b):
        io, host, link = self.io, self.host, self.link
        e = b["expect"]
        words = list(b["words"]) + [0] * (256 - len(b["words"]))
        tape = [0] * 256
        for a, w in b["tape"].items():
            tape[int(a)] = w
        name = "%-22s %-6s" % (b["name"], e["kind"])
        try:
            self.load(words, b["cfg"], tape)
        except Fail as x:
            return False, name + " LOAD FAILED", str(x)
        f_lo, f_hi = self.f_clk * 0.97, self.f_clk * 1.03
        host.start()
        t_start = link.t_begin
        note = ""
        if e["kind"] == "halt":
            limit_us = int(e["ticks"] * 2 * 1000000 / f_lo) + 100000
            while True:
                st = host.status()["ctx_state"]
                if all((st >> (2 * k)) & 3 != 1 for k in range(4)):
                    break
                if io.ticks_diff(io.ticks_us(), t_start) > limit_us:
                    note = "still running after %d ms; " % (limit_us // 1000)
                    break
                io.sleep_ms(1)
            sig = self.read_sig()
            dg = host.read_dbg(0, 16)
            host.stop()
            dg_ok = dg == e["digest"]
            result = "digest 0x%08x model 0x%08x" % (dg, e["digest"])
        elif e["kind"] == "steady":
            wait_us = int(e["t0"] * 1000000 / f_lo) + 1000
            while io.ticks_diff(io.ticks_us(), t_start) < wait_us:
                io.sleep_us(200)
            sig = self.read_sig()
            host.stop()
            run_us = io.ticks_diff(link.t_end, t_start)
            dg = host.read_dbg(0, 16)
            folds = e["folds"]
            m_max = (int(run_us * f_hi / 1000000 - e["t0"]) // e["period"] + 1) * len(folds)
            m = find_on_orbit(e["dg0"], folds, dg, max(m_max, 0))
            dg_ok = m is not None
            if dg_ok:
                result = "digest 0x%08x = model at tick %d" % (dg, e["t0"] + m * e["period"]
                                                                // len(folds))
            else:
                result = "digest 0x%08x not on the model orbit (%d folds from 0x%08x)" % (
                    dg, m_max, e["dg0"])
        else:
            io.sleep_us(int(e["ticks"] * 1000000 / f_lo))
            sig = self.read_sig()
            host.stop()
            dg_ok = True
            result = "no digest prediction; state compared"
        diffs = self.compare_sig(sig, e)
        leads, cert_bad = self.check_cert(b, sig)
        self.proof[b["name"]] = {"kind": e["kind"], "digest_ok": dg_ok, "tele": sig["tele"]}
        ok = dg_ok and not diffs and not cert_bad
        line = "%s %s  %s%s%s" % (name, "ok  " if ok else "FAIL", note, result, leads)
        if ok:
            return True, line, ""
        if cert_bad:
            diag = ("%s. escert proved this bound for every input, so the chip measured a "
                    "lead the certificate excludes: a bug in escert or in the chip's "
                    "admission path. Report it with the firmware name." % "; ".join(cert_bad))
        elif diffs:
            diag = ("chip and model differ at the end: %s. Compare a logic analyser capture "
                    "of uio[7:0] from START with `make_bundle.py --trace` (%s.pins.csv)."
                    % ("; ".join(diffs[:4]), b["name"].rsplit(".", 1)[0]))
        else:
            diag = ("same end state, different history: some commit, register value or pin "
                    "change differed during the run, or the digest path itself is faulty. "
                    "Capture uio[7:0] from START and compare with `make_bundle.py --trace`.")
        return False, line, diag

    def read_sig(self):
        h = self.host
        ctx = []
        for k in range(4):
            v7 = h.read_dbg(k, 7)
            ctx.append([(v7 >> 2) & 3, (v7 >> 8) & 0xFF] + [h.read_dbg(k, r) for r in range(4)])
        return {"ctx": ctx, "first_fault": h.read_dbg(0, 14), "fault_tick": h.read_dbg(0, 15),
                "uio": self.io.read_uio(), "tele": [h.read_dbg(k, 9) for k in range(4)]}

    def check_cert(self, b, sig):
        """The chip's min_lead (sel 9 [31:16]) against each context's certificate: a
        summary for the result line and the violations."""
        leads, bad = [], []
        for k, status, lb in b.get("cert", []):
            ml = sig["tele"][k] >> 16
            if status != "CHECKED":
                bad.append("ctx %d certificate %s" % (k, status))
            elif lb is not None and ml != 0xFFFF and ml < lb:
                bad.append("ctx %d min_lead %d below the certified %d" % (k, ml, lb))
            if lb is not None:
                leads.append("%d:%s (cert %d)" % (k, "none" if ml == 0xFFFF else ml, lb))
        return ("; min lead " + ", ".join(leads)) if leads else "", bad

    def compare_sig(self, sig, e):
        diffs = []
        if sig["first_fault"] != e["first_fault"]:
            diffs.append("first fault chip %s, model %s" % (_fault(sig["first_fault"]),
                                                            _fault(e["first_fault"])))
        elif e["kind"] == "halt" and e["first_fault"] >> 31 and sig["fault_tick"] != e["fault_tick"]:
            diffs.append("fault tick chip %d, model %d" % (sig["fault_tick"], e["fault_tick"]))
        if e["kind"] == "none":               # the state keeps changing: faults only
            return diffs
        for k in range(4):
            c, m = sig["ctx"][k], e["ctx"][k]
            if c[0] != m[0]:
                diffs.append("ctx %d %s, model %s" % (k, STATE[c[0]], STATE[m[0]]))
            if c[1] != m[1]:
                diffs.append("ctx %d PC 0x%02x, model 0x%02x" % (k, c[1], m[1]))
            for r in range(4):
                if c[2 + r] != m[2 + r]:
                    diffs.append("ctx %d r%d 0x%08x, model 0x%08x" % (k, r, c[2 + r], m[2 + r]))
        if sig["uio"] != e["uio"]:
            diffs.append("uio levels %s, model %s (pins %s)" % (
                bits(sig["uio"]), bits(e["uio"]), pins_of(sig["uio"] ^ e["uio"])))
        return diffs

    # -- 6 (optional)
    def stage6(self):
        io = self.io
        found = 0
        for fn in _bundle_files(self.bundles):
            b = load_bundle(self.bundles + "/" + fn)
            if "uart" not in b:
                continue
            found += 1
            u = b["uart"]
            words = list(b["words"]) + [0] * (256 - len(b["words"]))
            self.load(words, b["cfg"])
            try:
                uart, close = io.uart_open(u["pin"], u["baud"])
            except Exception as x:          # e.g. a pin the RP's UART cannot use
                raise Fail("could not open an RP UART on uio[%d]: %s" % (u["pin"], x))
            try:
                while uart.any():
                    uart.read()
                self.host.start()
                io.sleep_ms(20)
                got = uart.read() or b""
            finally:
                close()
                self.host.stop()
            want = bytes(u["bytes"])
            if bytes(got) != want:
                raise Fail("%s: the RP's UART received %s, the model sends %s at %d baud. "
                           "Garbled bytes: bit timing (clock) or the pad; nothing: uio[%d] "
                           "is not driven." % (b["name"], _hex(got), _hex(want), u["baud"],
                                               u["pin"]))
            self.say("    %s: RP UART received %s" % (b["name"], _hex(got)))
        if not found:
            raise Fail("no bundle with a UART smoke test (uart_tx.json) in '%s'" % self.bundles)
        return "%d UART smoke test(s) received exactly the model's bytes" % found


STAGES = ((0, "board and project", "stage0"), (1, "clock and reset", "stage1"),
          (2, "host link", "stage2"), (3, "memories", "stage3"),
          (4, "pins and clock rate", "stage4"), (5, "firmware digests", "stage5"),
          (6, "UART smoke test (optional)", "stage6"))


LAST = None                               # the last run's Bringup object (results, f_clk)


def run(bundles="bundles", stages=None, keep_going=False, optional=False, quick=False,
        io=None, out=print, proof="proof.json"):
    """Run the bring-up stages; returns True if every stage run passed. Stages 0-2 always
    run (they select, clock and connect the chip). quick=True uses fewer repetitions and
    patterns (a fast re-check, and the RTL simulation of this script). Stage 5 writes the
    chip's telemetry per firmware to `proof` (None: no file)."""
    global LAST
    b = LAST = Bringup(io or DemoBoardIO.open(), bundles, quick, out, proof)
    want = set(stages if stages is not None else (0, 1, 2, 3, 4, 5))
    if optional:
        want.add(6)
    want |= {0, 1, 2}
    ok = True
    for num, title, meth in STAGES:
        if num not in want:
            continue
        out("[%d] %s ..." % (num, title))
        try:
            detail = getattr(b, meth)()
            b.results.append((num, True, detail))
            out("[%d] PASS  %s" % (num, detail))
        except Fail as x:
            ok = False
            b.results.append((num, False, str(x)))
            out("[%d] FAIL  %s" % (num, x))
            if not keep_going or num < 3:
                break
    out("bring-up %s" % ("PASSED" if ok else "FAILED"))
    return ok


# ------------------------------------------------------------------------ helpers

def _hex(bs):
    return " ".join("%02x" % c for c in bs)


def _log2(x):
    n = 0
    while x > 1:
        x >>= 1
        n += 1
    return n


def _bitlist(mask):
    return ",".join(str(i) for i in range(32) if (mask >> i) & 1)


def _addr_word(a):
    return (a << 24) | ((a ^ 0xFF) << 16) | (a << 8) | (a ^ 0xFF)


def _addr_of(w):
    """The address whose 'address in data' word this is, or None if it is not one."""
    a = w >> 24
    return a if _addr_word(a) == w else None


def _patterns(quick):
    p = [("address in data", [_addr_word(a) for a in range(256)]),
         ("walking one", [1 << (a & 31) for a in range(256)])]
    if not quick:
        p.append(("walking zero", [M32 ^ (1 << (a & 31)) for a in range(256)]))
        p.append(("checkerboard", [0x55555555 if (a & 1) else 0xAAAAAAAA for a in range(256)]))
    return p


def _fault(w):
    if not w >> 31:
        return "none"
    cause = (w >> 24) & 0xF
    return "%s/%d ctx %d PC 0x%02x" % (CAUSE.get(cause, str(cause)), (w >> 20) & 0xF,
                                       (w >> 16) & 3, w & 0xFF)


def load_bundle(path):
    with open(path) as f:
        return json.load(f)


def _bundle_files(d):
    try:
        import os
    except ImportError:                   # older MicroPython
        import uos as os
    try:
        names = os.listdir(d)
    except OSError:
        return []
    return sorted(n for n in names if n.endswith(".json") and not n.startswith("."))
