# Escapement demo for the Tiny Tapeout demo board (RP2040/RP2350, tt-micropython-firmware):
# run one firmware bundle and compare the chip with the model, or stream a logic-analyser
# capture to a file. Copy this file, bringup.py, sw/eschost/protocol.py (as protocol.py)
# and bundles/ to the board, then:
#     import escapement_demo; escapement_demo.run("bundles/uart_tx.json")
#     escapement_demo.stream(seconds=5)       # uio[7:0] -> capture.json
#     escapement_demo.prove()                 # check uart_tx, then type the result over USB
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


# Boot keyboard usage IDs, US layout (HID Usage Tables 1.5, section 10: Keyboard/Keypad).
_KEYS = {" ": 0x2C, "\n": 0x28, "-": 0x2D, "=": 0x2E, ";": 0x33, ",": 0x36, ".": 0x37,
         "/": 0x38}
_SHIFTED = {":": ";", ">": ".", "<": ",", "!": "1", "?": "/", "_": "-", "+": "=", "(": "9",
            ")": "0"}


def key_reports(text):
    """The 8-byte boot keyboard reports that type `text` on a US layout: each character
    pressed, then every key released, so a repeated character registers twice."""
    out = []
    for ch in text:
        shift = 0
        if ch in _SHIFTED:
            ch, shift = _SHIFTED[ch], 0x02            # left shift
        elif "A" <= ch <= "Z":
            ch, shift = ch.lower(), 0x02
        if "a" <= ch <= "z":
            code = 0x04 + ord(ch) - ord("a")
        elif "1" <= ch <= "9":
            code = 0x1E + ord(ch) - ord("1")
        elif ch == "0":
            code = 0x27
        elif ch in _KEYS:
            code = _KEYS[ch]
        else:
            raise ValueError("no key for %r" % ch)
        out.append(bytes((shift, 0, code, 0, 0, 0, 0, 0)))
        out.append(bytes(8))
    return out


def type_text(b, text, ctx=1):
    """Type `text` through firmware/usb_hid.asm, already running: each report goes into
    context `ctx`'s mailbox as two words (bytes 0-3, 4-7, little-endian) as it has room."""
    h = b.host
    for r in key_reports(text):
        for w in (int.from_bytes(r[0:4], "little"), int.from_bytes(r[4:8], "little")):
            while (h.status()["rx_full"] >> ctx) & 1:
                b.io.sleep_ms(1)
            h.mbx_push(ctx, w)


def prove(bundle_file="bundles/uart_tx.json", usb_bundle="bundles/usb_hid.json",
          enumerate_ms=3000):
    """Run a firmware and check the chip against the model and the timing certificate,
    then turn the chip into a USB keyboard and type the result into the computer it is
    plugged into: open a text editor there first. USB wiring as in firmware/usb_hid.asm
    (D+ uio0, D- uio1, 27 ohm in series each, 1.5 kohm from D- to 3.3 V)."""
    b = bringup.Bringup(bringup.DemoBoardIO.open())
    print(b.stage0())
    print(b.stage1())
    b.connect()
    bd = bringup.load_bundle(bundle_file)
    ok, line, diag = b.run_bundle(bd)
    print(line)
    dg = b.host.read_dbg(0, 16)
    leads, cert_bad = b.check_cert(bd, b.read_sig())
    text = "Escapement %s: digest 0x%08x %s%s\n" % (
        bd["name"].split(".")[0], dg, "matches the model" if ok else "DIFFERS from the model",
        leads.replace(";", ","))
    ub = bringup.load_bundle(usb_bundle)
    b.io.set_clock(24000000)              # low-speed USB: 16 ticks per bit
    b.load(list(ub["words"]) + [0] * (256 - len(ub["words"])), ub["cfg"],
           [ub["tape"].get(str(a), 0) for a in range(256)])
    b.host.start()
    b.io.sleep_ms(enumerate_ms)           # the computer enumerates the keyboard
    type_text(b, text)
    print("typed: " + text.strip())
    return ok and not cert_bad


# What the demo board shows on its own, with nothing plugged into the Pmods.
MENU = (
    ("UART hello: uio0 sends \"UK!\", the RP's UART checks every byte", "run", "uart_tx.json"),
    ("Seven-segment animation on bank B (keeps running)", "show", "seven_seg.json"),
    ("Two pin banks at once: PS/2, JTAG, SPI and UART, checked against the model", "run",
     "combo_two_banks.json"),
    ("Every bundle against the model (bring-up stage 5)", "stage5", None),
    ("Logic analyser: stream uio[7:0] for 5 s into capture.json", "stream", None),
    ("SUMP bridge: capture from PulseView or sigrok-cli over USB", "sump", None),
    ("Proof by keyboard: check uart_tx, then type the result as a USB keyboard (needs the "
     "USB wiring of firmware/usb_hid.asm)", "prove", "uart_tx.json"),
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
    if kind == "prove":
        return prove(bundles + "/" + arg, bundles + "/usb_hid.json")
    import sump
    return sump.serve(bundles + "/logic_analyzer_stream.json")
