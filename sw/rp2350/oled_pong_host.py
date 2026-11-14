# OLED Pong from the board's microcontroller: load the four-page image (firmware/oled_*.asm),
# start it, then hold ui_in[3..7] (Esc and the paddles' buttons) as the laptop's keyboard says, over
# the USB serial line (sw/eschost/pong_keys.py does both ends). Copy this file, bringup.py,
# sw/eschost/protocol.py (as protocol.py) and oled_pong.json (`python -m eschost.pong_keys
# --bundle oled_pong.json`) to the board, wire the SSD1306 to uio[2] (SCL) and uio[3] (SDA)
# with its own pull-ups, then:
#     import oled_pong_host; oled_pong_host.run()                         # TT demo board
#     import barepico, oled_pong_host; oled_pong_host.run(barepico.open_io())  # bare Pico + FPGA
# Keys: one byte per change, 0xC0 | b with b bit i = ui_in[3 + i] (1 = pressed): Esc, then
# the left paddle's up, down, the right's; 0xAA stops the chip and returns. Anything else
# is ignored.
#
# Plain MicroPython (no f-strings), as bringup.py.

import json
import sys

import bringup

PAGES_VERSION = 10


class Image:
    """What protocol.Host.load needs, from escasm's Image.to_json."""

    def __init__(self, d):
        self.words = [int(w, 16) for w in d["words"]]
        self.tape = [0] * 256
        for a, w in d["tape"].items():
            self.tape[int(a)] = int(w, 16)
        self.tape_used = sorted(int(a) for a in d["tape"])
        self.entry_pcs, self.ctx_enable = d["entry_pcs"], d["ctx_enable"]
        self.pin_owned, self.pin_owner, self.pin_od = d["pin_owned"], d["pin_owner"], d["pin_od"]
        self.ctx_bank, self.ctx_pair, self.ctx_page = d["ctx_bank"], d["ctx_pair"], d["ctx_page"]

    @property
    def entry_pcs_packed(self):
        return sum((pc & 0xFF) << (8 * k) for k, pc in enumerate(self.entry_pcs))

    @property
    def ctx_enable_mask(self):
        return sum(1 << k for k, e in enumerate(self.ctx_enable) if e)

    @property
    def pin_owned_mask(self):
        return sum(1 << i for i, o in enumerate(self.pin_owned) if o)

    @property
    def pin_owner_packed(self):
        return sum((o & 3) << (2 * i) for i, o in enumerate(self.pin_owner))

    @property
    def pin_od_mask(self):
        return sum(1 << i for i, o in enumerate(self.pin_od) if o)

    @property
    def ctx_bank_mask(self):
        return sum(1 << k for k, b in enumerate(self.ctx_bank) if b)

    @property
    def ctx_pair_mask(self):
        return sum(1 << k for k, p in enumerate(self.ctx_pair) if p)

    @property
    def ctx_page_packed(self):
        return sum((p & 3) << (2 * k) for k, p in enumerate(self.ctx_page))


def run(io=None, path="oled_pong.json", out=print, keys=None):
    """keys: a function returning the next key byte (default: the USB serial line)."""
    io = io or bringup.DemoBoardIO.open()
    b = bringup.Bringup(io, out=out)
    b.stage0()
    b.stage1()
    b.connect()
    h = b.host
    st = h.status()
    if st["version"] < PAGES_VERSION:
        raise RuntimeError("the chip reports protocol version %d; four program pages need %d"
                           % (st["version"], PAGES_VERSION))
    with open(path) as f:
        image = Image(json.load(f))
    h.load(image)
    h.start()
    out("PONG RUNNING")
    read = keys or (lambda: sys.stdin.buffer.read(1)[0])
    link = b.link
    while True:
        c = read()
        if c == 0xAA:
            break
        if c & 0xE0 == 0xC0:
            link.hi = (c & 0x1F) << 3        # ui[7:3]; the link holds it on every write
            io.write_ui(bringup.CS | link.hi)
    link.hi = 0
    io.write_ui(bringup.CS)
    h.stop()
    out("PONG STOPPED")
