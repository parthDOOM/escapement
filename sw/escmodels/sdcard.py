"""SD memory card in SPI mode, for testing a host (SD Association, Physical Layer Simplified
Specification, chapter 7: SPI mode).

Written from the specification, not from any firmware. Mode 0: MOSI is sampled on SCK's
rising edge, MISO changes after the falling edge; MISO is released (high) while CS_n is high.
A command is 6 bytes: 01 and the 6-bit index, the 32-bit argument, CRC7 << 1 | 1; this model
checks the CRC7 of every command (stricter than a card, which in SPI mode checks it only for
CMD0 and CMD8). Responses come Ncr bytes after the command (1..8). Implemented: CMD0 (idle,
R1 0x01), CMD8 (R7: R1 and the echoed voltage and check pattern), CMD55 + ACMD41 (the card
stays idle for `busy` attempts, then R1 0x00), CMD58 (R3: R1 and the OCR: power-up done,
CCS, 2.7-3.6 V), CMD17 (R1 0x00, then Nac bytes of 0xFF, the start token 0xFE, 512 bytes and
their CRC-16 most significant byte first; binascii.crc_hqx is the reference CRC-16).
"""

from __future__ import annotations

import binascii

from .board import Device

OCR_READY = 0x80000000 | 0x40000000 | 0x00FF8000      # powered up, CCS (SDHC), 2.7-3.6 V


def crc7(data: bytes) -> int:
    crc = 0
    for byte in data:
        for k in range(7, -1, -1):
            fb = ((crc >> 6) & 1) ^ ((byte >> k) & 1)
            crc = (crc << 1) & 0x7F
            if fb:
                crc ^= 0x09
    return crc


def block_bytes(addr: int) -> bytes:
    """The card's contents: block `addr`, a pattern no two blocks share."""
    return bytes(((addr * 131 + i * 7 + (i >> 8) * 3) ^ (addr >> 3)) & 0xFF for i in range(512))


class SdCardSpi(Device):
    def __init__(self, sck: int, mosi: int, miso: int, cs: int, busy: int = 3, ncr: int = 2,
                 nac: int = 5, bad_crc: bool = False):
        self.sck, self.mosi, self.miso, self.cs = sck, mosi, miso, cs
        self.busy, self.ncr, self.nac, self.bad_crc = busy, ncr, nac, bad_crc
        self.spi_mode = False
        self.idle = True
        self.app = False
        self.acmd41s = 0
        self.commands: list[tuple[int, int]] = []          # (index, argument) as received
        self.violations: list[str] = []
        self._p = None
        self._cmd: list[int] = []
        self._out: list[int] = []                          # bytes queued for MISO
        self._rx = 0
        self._nbit = 0
        self._tx = 0xFF
        self._txbits = 0
        self._miso = 1
        self._t_mosi = -10 ** 9
        self._prev_mosi = 1

    # -- bytes
    def _byte(self, b: int):
        if self._cmd or (not self._out and b & 0xC0 == 0x40):
            self._cmd.append(b)
            if len(self._cmd) == 6:
                self._command(bytes(self._cmd))
                self._cmd = []

    def _queue(self, *bs, wait=None):
        self._out += [0xFF] * (self.ncr if wait is None else wait) + list(bs)

    def _command(self, c: bytes):
        idx, arg = c[0] & 0x3F, int.from_bytes(c[1:5], "big")
        self.commands.append((idx, arg))
        if c[5] != (crc7(c[:5]) << 1 | 1):
            self.violations.append(f"CMD{idx} CRC byte {c[5]:#04x}, want {crc7(c[:5]) << 1 | 1:#04x}")
            self._queue(0x08 | (0x01 if self.idle else 0))          # R1: COM CRC error
            return
        app, self.app = self.app, False
        r1 = 0x01 if self.idle else 0x00
        if idx == 0:
            self.spi_mode, self.idle = True, True
            self._queue(0x01)
        elif not self.spi_mode:
            self.violations.append(f"CMD{idx} before CMD0")
        elif idx == 8:
            self._queue(r1, 0, 0, (arg >> 8) & 0x0F, arg & 0xFF)
        elif idx == 55:
            self.app = True
            self._queue(r1)
        elif idx == 41 and app:
            self.acmd41s += 1
            if self.acmd41s > self.busy:
                self.idle = False
            self._queue(0x01 if self.idle else 0x00)
        elif idx == 58:
            ocr = OCR_READY if not self.idle else 0x00FF8000
            self._queue(r1, *ocr.to_bytes(4, "big"))
        elif idx == 17 and not self.idle:
            data = block_bytes(arg)
            crc = binascii.crc_hqx(data, 0) ^ (0x0101 if self.bad_crc else 0)
            self._queue(0x00)
            self._queue(0xFE, *data, crc >> 8, crc & 0xFF, wait=self.nac)
        else:
            self._queue(r1 | 0x04)                                  # illegal command

    # -- bits (mode 0)
    def step(self, t, bus):
        sck, mosi, cs = (bus >> self.sck) & 1, (bus >> self.mosi) & 1, (bus >> self.cs) & 1
        if mosi != self._prev_mosi:
            self._t_mosi = t
        self._prev_mosi = mosi
        if cs:
            self._nbit, self._rx = 0, 0
            self._txbits = 0
            self._miso = 1
            self._p = sck
            return 0, 0
        if self._txbits == 0:                 # load the next byte to send at a byte start
            self._tx = self._out.pop(0) if self._out else 0xFF
            self._txbits = 8
            self._miso = (self._tx >> 7) & 1
        if self._p == 0 and sck == 1:         # rising: sample MOSI
            if t - self._t_mosi < 1:
                self.violations.append(f"t={t}: MOSI changed at the sampling edge")
            self._rx = (self._rx << 1) | mosi
            self._nbit += 1
            if self._nbit == 8:
                self._byte(self._rx)
                self._nbit, self._rx = 0, 0
        elif self._p == 1 and sck == 0:       # falling: next MISO bit
            self._txbits -= 1
            if self._txbits:
                self._miso = (self._tx >> (self._txbits - 1)) & 1
        self._p = sck
        return (0 if self._miso else 1 << self.miso), 0
