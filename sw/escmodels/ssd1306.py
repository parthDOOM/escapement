"""SSD1306 OLED controller (128x64) on I2C, written from the Solomon Systech SSD1306 datasheet
(rev. 1.1): section 8.1.5 (I2C write: control byte with Co and D/C#), 10.1 (command table) and
10.1.3 (memory addressing modes). An I2cTarget for the bus (open drain, UM10204 timing checks);
the controller state is rebuilt from the bytes the bus decoder records, so the I2C side is the
one every other I2C test uses.

Control byte: bit 7 Co, bit 6 D/C#. Co = 0: every following byte of the transaction is data
(D/C# = 1, into display RAM) or a command byte (D/C# = 0). Co = 1: the next byte only, then
another control byte. Display RAM: 8 pages of 128 bytes, bit 0 of a byte the top row of its
page. Addressing modes (command 0x20): 0 horizontal, 1 vertical, 2 page (the reset mode).
"""

from __future__ import annotations

from .i2c import I2cTarget

# argument bytes after each command byte (datasheet table 9-1); the rest take none
ARGS = {0x81: 1, 0x8D: 1, 0x20: 1, 0x21: 2, 0x22: 2, 0xA8: 1, 0xD3: 1, 0xD5: 1, 0xD9: 1,
        0xDA: 1, 0xDB: 1, 0x26: 6, 0x27: 6, 0x29: 5, 0x2A: 5, 0xA3: 2}


class Ssd1306(I2cTarget):
    def __init__(self, scl: int, sda: int, addr7: int = 0x3C, nack_after: int | None = None, **kw):
        super().__init__(scl, sda, addr7, mode="fast", **kw)
        self.nack_after = nack_after      # NACK the data byte after this many (a bus fault)
        self._wcount = 0

    # a forced NACK: after the bus decoder has taken a data byte, do not pull SDA for its ACK
    def _on_scl_fall(self, t):
        before = sum(1 for x in self.transactions[-1] if isinstance(x, tuple) and x[0] == "w") \
            if self.transactions else 0
        super()._on_scl_fall(t)
        after = sum(1 for x in self.transactions[-1] if isinstance(x, tuple) and x[0] == "w") \
            if self.transactions else 0
        if after > before:
            self._wcount += 1
            if self.nack_after is not None and self._wcount > self.nack_after:
                self._sda_drive_low = False
                self._state = "wait_stop"

    # ------------------------------------------------------------ the controller
    def state(self):
        """Replay every transaction addressed to this device into the controller: returns
        (ram, commands), ram = 8 pages x 128 bytes, commands = the command bytes with their
        arguments, in order, as tuples."""
        ram = [[0] * 128 for _ in range(8)]
        cmds: list[tuple] = []
        mode, cs, ce, ps, pe, col, page = 2, 0, 127, 0, 7, 0, 0
        pend: list[int] = []

        def data(b):
            nonlocal col, page
            ram[page][col] = b
            if mode == 0:                     # horizontal: column, then page
                if col == ce:
                    col = cs
                    page = ps if page == pe else page + 1
                else:
                    col += 1
            elif mode == 1:                   # vertical: page, then column
                if page == pe:
                    page = ps
                    col = cs if col == ce else col + 1
                else:
                    page += 1
            else:                             # page mode: the column wraps, the page stays
                col = cs if col == ce else (col + 1) & 0x7F

        def command(c):
            nonlocal mode, cs, ce, ps, pe, col, page
            cmds.append(tuple(c))
            op = c[0]
            if op == 0x20:
                mode = c[1] & 3
            elif op == 0x21:
                cs, ce = c[1] & 0x7F, c[2] & 0x7F
                col = cs
            elif op == 0x22:
                ps, pe = c[1] & 7, c[2] & 7
                page = ps
            elif mode == 2 and op <= 0x0F:
                col = (col & 0x70) | op
            elif mode == 2 and 0x10 <= op <= 0x1F:
                col = (col & 0x0F) | ((op & 7) << 4)
            elif mode == 2 and 0xB0 <= op <= 0xB7:
                page = op & 7

        for tr in self.transactions:
            ok = any(isinstance(x, tuple) and x[0] == "addr" and x[2] and not (x[1] & 1) for x in tr)
            if not ok:
                continue
            bytes_ = [x[1] for x in tr if isinstance(x, tuple) and x[0] == "w"]
            need_ctrl, single, dc = True, False, False
            for b in bytes_:
                if need_ctrl:                 # a control byte: Co (bit 7), D/C# (bit 6)
                    single, dc, need_ctrl = bool(b & 0x80), bool(b & 0x40), False
                    continue
                if dc:
                    data(b)
                else:
                    pend.append(b)
                    if len(pend) == 1 + ARGS.get(pend[0], 0):
                        command(pend)
                        pend = []
                need_ctrl = single            # Co = 1: one byte, then another control byte
            # a command's argument bytes may come in later transactions: drivers such as
            # Adafruit_SSD1306 send every command byte, arguments too, as its own [0x00, b]
            # write, so the command decoder keeps its place across a STOP
        return ram, cmds

    def frame_bytes(self):
        """Display RAM as 1024 bytes, page 0 column 0 first (horizontal order)."""
        ram, _ = self.state()
        return [b for p in ram for b in p]
