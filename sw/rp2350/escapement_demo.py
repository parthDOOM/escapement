# Escapement demo for the Tiny Tapeout demo board (RP2040/RP2350, tt-micropython-firmware):
# run one firmware bundle and compare the chip with the model. Copy this file,
# bringup.py, sw/eschost/protocol.py (as protocol.py) and bundles/ to the board, then:
#     import escapement_demo; escapement_demo.run("bundles/uart_tx.json")
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
        raise RuntimeError("not an Escapement v1.2 chip (protocol version %d)" % st["version"])
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
