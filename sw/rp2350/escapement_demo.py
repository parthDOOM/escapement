# Escapement demo for the Tiny Tapeout demo board (RP2040/RP2350, tt-micropython-firmware):
# run one firmware bundle and compare the chip with the model, or stream a logic-analyser
# capture to a file. Copy this file, bringup.py, sw/eschost/protocol.py (as protocol.py)
# and bundles/ to the board, then:
#     import escapement_demo; escapement_demo.run("bundles/uart_tx.json")
#     escapement_demo.stream(seconds=5)       # uio[7:0] -> capture.json
# On a PC, `python -m esctape export capture.json -o capture.vcd` (or .sr) opens the
# capture in PulseView or GTKWave.
#
# The board access, the bit-banged host link and the bundle check are bringup.py's (see
# its header for the board API and the date it was checked); docs/16-BRING-UP.md is the
# full bring-up procedure.

import bringup


def run(bundle_file):
    b = bringup.Bringup(bringup.DemoBoardIO.open())
    print(b.stage0())
    print(b.stage1())
    b.connect()
    st = b.host.status()
    print("status", st)
    if st["version"] != bringup.VERSION:
        raise RuntimeError("not an Escapement v1.3 chip (protocol version %d)" % st["version"])
    ok, line, diag = b.run_bundle(bringup.load_bundle(bundle_file))
    print(line)
    for k in range(4):
        tele = b.host.read_dbg(k, 9)
        print("ctx", k, "min_lead", tele >> 16, "wait_full", tele & 0xFFFF)
    ff = b.host.read_dbg(0, 14)
    print("first fault", hex(ff) if ff >> 31 else "none")
    if diag:
        print(diag)
    print("SILICON MATCHES THE MODEL" if ok else "MISMATCH")
    return ok


def stream(seconds=5, path="capture.json", bundle_file="bundles/logic_analyzer_stream.json",
           rows=24):
    """v1.3 streaming capture: watch uio[7:0] for `seconds`, reading and acknowledging
    records during the run, so the capture is not limited to the tape's 256 words. Writes
    an Escapement capture file (sw/esctape/export.py) and prints the first `rows` records
    as a timing table. Returns the record words."""
    import json
    b = bringup.Bringup(bringup.DemoBoardIO.open())
    print(b.stage0())
    b.connect()
    st = b.host.status()
    if st["version"] != bringup.VERSION:
        raise RuntimeError("not an Escapement v1.3 chip (protocol version %d)" % st["version"])
    bd = bringup.load_bundle(bundle_file)
    base, lim = bd["stream"]["base"], bd["stream"]["lim"]
    b.load(list(bd["words"]) + [0] * (256 - len(bd["words"])), bd["cfg"])
    h, io = b.host, b.io
    io.uio_drive(0, 0)                    # the RP only listens: inputs with pull-ups, as
    io.uio_pullups()                      # on the demo board's bidirectional Pmod
    h.start()
    t0, rptr, words, polls, ovf = io.ticks_us(), base, [], 0, False
    while io.ticks_diff(io.ticks_us(), t0) < seconds * 1000000:
        w, rptr, st = h.stream_poll(base, lim, rptr)
        words += w
        polls += 1
        if (st >> 30) & 1:
            ovf = True                    # the host fell behind: capture stopped at ovf_tick
            break
    h.stop()
    w, rptr, st = h.stream_poll(base, lim, rptr)        # records staged at STOP
    words += w
    with open(path, "w") as f:
        json.dump({"format": "escapement-capture", "version": 1, "clock_hz": b.f_clk,
                   "first_epoch": 0, "names": ["uio%d" % i for i in range(8)],
                   "words": ["0x%08x" % x for x in words]}, f)
    print("%d records in %d polls -> %s%s" % (len(words), polls, path,
          ", OVERFLOW at tick %d" % h.read_dbg(0, 19) if ovf else ""))
    print("      tick  uio 7..0")
    epoch = 0
    for i, x in enumerate(words[:rows]):
        if i and not x & 0xFFFFFF:
            epoch += 1
        lv = x >> 24
        print("%10d  %s" % ((epoch << 24) | (x & 0xFFFFFF),
                            "".join("#" if (lv >> p) & 1 else "_" for p in range(7, -1, -1))))
    return words


def show(bundle_file):
    """Load a bundle and leave it running (no model check): e.g. the display animation.
    Stop it with escapement_demo.stop()."""
    b = bringup.Bringup(bringup.DemoBoardIO.open())
    print(b.stage0())
    b.connect()
    bd = bringup.load_bundle(bundle_file)
    b.load(list(bd["words"]) + [0] * (256 - len(bd["words"])), bd["cfg"])
    b.host.start()
    print("%s running; escapement_demo.stop() stops it" % bd["name"])
    return b


def stop():
    b = bringup.Bringup(bringup.DemoBoardIO.open())
    b.connect()
    b.host.stop()


# What the demo board shows on its own, with nothing plugged into the Pmods.
MENU = (
    ("UART hello: uio0 sends \"UK!\", the RP's UART checks every byte", "run", "uart_tx.json"),
    ("Seven-segment animation on bank B (keeps running)", "show", "seven_seg.json"),
    ("Two pin banks at once: PS/2, JTAG, SPI and UART, checked against the model", "run",
     "combo_two_banks.json"),
    ("Every bundle against the model (bring-up stage 5)", "stage5", None),
    ("Logic analyser: stream uio[7:0] for 5 s into capture.json", "stream", None),
    ("SUMP bridge: capture from PulseView or sigrok-cli over USB", "sump", None),
)


def menu(choice=None, bundles="bundles"):
    for i, (label, _, _) in enumerate(MENU):
        print("%d  %s" % (i + 1, label))
    if choice is None:
        choice = int(input("choice: "))
    label, kind, arg = MENU[choice - 1]
    print("--", label)
    if kind == "run":
        return run(bundles + "/" + arg)
    if kind == "show":
        return show(bundles + "/" + arg)
    if kind == "stage5":
        return bringup.run(bundles=bundles, stages=[5])
    if kind == "stream":
        return stream(bundle_file=bundles + "/logic_analyzer_stream.json")
    import sump
    return sump.serve(bundles + "/logic_analyzer_stream.json")
