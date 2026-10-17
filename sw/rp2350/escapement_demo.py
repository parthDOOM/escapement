# Escapement demo for the Tiny Tapeout demo board (RP2040/RP2350, tt-micropython-firmware).
# Copy this file, sw/eschost/protocol.py (as protocol.py) and bundles/*.json to the
# board, then:  import escapement_demo; escapement_demo.run("uart_tx.json")
#
# The host link is the chip's oversampled SPI mode 0 (docs/12-HOST-PROTOCOL.md), bit-
# banged on ui_in[0] CS_n, ui_in[1] SCK, ui_in[2] MOSI and uo_out[0] MISO. MicroPython
# is far slower than the chip's limit (SCK high/low >= 4 core clocks), so no delays are
# needed. Untested on silicon: the board API follows tt-micropython-firmware's README
# (tt.ui_in[bit], tt.uo_out[bit], tt.shuttle.<name>.enable(), tt.reset_project(),
# tt.clock_project_PWM()), checked 2026-10-13.

import json
import time

import protocol as P

PROJECT = "tt_um_escapement"
CLOCK_HZ = 25_000_000


class TtSpi:
    """One CS_n-low frame per xfer(), bit-banged through the demo board's ui_in/uo_out."""

    def __init__(self, tt):
        self.tt = tt
        tt.ui_in[0] = 1     # CS_n high
        tt.ui_in[1] = 0     # SCK low (mode 0)

    def xfer(self, data):
        tt = self.tt
        out = bytearray()
        tt.ui_in[0] = 0
        for byte in data:
            r = 0
            for i in range(7, -1, -1):
                tt.ui_in[2] = (byte >> i) & 1
                # Sample MISO BEFORE the rising edge: the chip advances MISO ~3 core clocks
                # after it sees SCK rise, far sooner than MicroPython can read a pin, so a
                # read after the edge would return the next bit.
                r = (r << 1) | (tt.uo_out[0] & 1)
                tt.ui_in[1] = 1
                tt.ui_in[1] = 0
            out.append(r)
        tt.ui_in[0] = 1
        return bytes(out)


def board():
    from ttboard.demoboard import DemoBoard
    tt = DemoBoard.get()
    getattr(tt.shuttle, PROJECT).enable()
    tt.clock_project_PWM(CLOCK_HZ)
    tt.reset_project(True)
    time.sleep_ms(1)
    tt.reset_project(False)
    return tt


def run(bundle_file, wait_ms=50):
    b = json.load(open(bundle_file))
    tt = board()
    host = P.Host(TtSpi(tt).xfer)
    st = host.status()
    print("status", st)
    if st["version"] != 6:
        raise RuntimeError("not an Escapement v1.1 chip (protocol version %d)" % st["version"])
    host.stop()
    host.write_imem(0, b["words"])                       # reload after every reselect
    if host.read_imem(0, len(b["words"])) != b["words"]:
        raise RuntimeError("program readback mismatch")
    for a, w in b["tape"].items():
        host.write_tape(int(a), [w])
    host.write_cfg(*b["cfg"])
    host.start()
    time.sleep_ms(wait_ms)
    digest = host.read_dbg(0, 16)
    for k in range(4):
        tele = host.read_dbg(k, 9)
        print("ctx", k, "min_lead", tele >> 16, "wait_full", tele & 0xFFFF)
    ff = host.read_dbg(0, 14)
    print("first fault", hex(ff) if ff >> 31 else "none")
    if "expect_digest" in b:
        ok = digest == b["expect_digest"]
        print("digest chip 0x%08x  model 0x%08x  %s" % (digest, b["expect_digest"],
              "SILICON MATCHES THE MODEL" if ok else "MISMATCH"))
        return ok
    print("digest 0x%08x" % digest)
    return None
