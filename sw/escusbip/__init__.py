"""escusbip: a simulated Escapement chip on a real Linux USB stack, over USB/IP.

The Linux kernel's `vhci_hcd` driver is a USB host controller whose devices are reached
through a socket speaking the USB/IP protocol (Documentation/usb/usbip_protocol.rst):
the kernel sends USBIP_CMD_SUBMIT for every URB and waits for USBIP_RET_SUBMIT. This
module answers those URBs by running real USB transactions, packet by packet, on the
pins of the ISS running the firmware, with the low-speed host model of escmodels.usb.
Nothing about the device is stubbed: every descriptor and report the kernel sees was
sent by the firmware through the line unit and decoded from the line states.

vhci_hcd handles a bus reset and SET_ADDRESS itself, so the bridge does both on the
simulated bus when it starts, and sends every URB to that address. Virtual time stands
still while the kernel has nothing outstanding and no key is waiting to go out (the
firmware then just waits in GETL), so an idle device costs nothing.
"""

from __future__ import annotations

import os
import queue
import socket
import sys
import struct
import threading
from dataclasses import dataclass, field
from typing import Optional

from esciss import Machine
from escmodels.usb import (UsbError, UsbHost, bus_reset, control, in_txn, keyboard_reports,
                           setup_bytes)

CMD_SUBMIT, CMD_UNLINK, RET_SUBMIT, RET_UNLINK = 1, 2, 3, 4
EPIPE, ECONNRESET, EPROTO = 32, 104, 71
HEADER = 48
ADDR = 1                                     # the address the bridge gives the device
DEBUG = bool(os.environ.get("ESCUSBIP_DEBUG"))


@dataclass
class Urb:
    seqnum: int
    devid: int
    direction: int                           # 0 OUT, 1 IN
    ep: int
    length: int
    setup: bytes
    data: bytes = b""


def parse_header(h: bytes) -> tuple:
    command, seqnum, devid, direction, ep = struct.unpack(">5I", h[:20])
    return command, seqnum, devid, direction, ep


def ret_submit(seqnum: int, status: int, data: bytes = b"", actual: Optional[int] = None) -> bytes:
    """`data` only for IN transfers; an OUT transfer reports `actual` bytes and sends none."""
    n = len(data) if actual is None else actual
    return (struct.pack(">5I", RET_SUBMIT, seqnum, 0, 0, 0)
            + struct.pack(">iiiii", -status if status else 0, n, 0, 0, 0)
            + bytes(8) + data)


def ret_unlink(seqnum: int, status: int) -> bytes:
    return struct.pack(">5I", RET_UNLINK, seqnum, 0, 0, 0) + struct.pack(">i", -status) + bytes(24)


@dataclass
class Bridge:
    """Serves the USB/IP side of `sock` with the firmware `image` on the ISS."""
    image: object
    sock: socket.socket
    log: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def __post_init__(self):
        self.host = UsbHost()
        self.m = Machine(self.image, board=self.host)
        self.urbs: "queue.Queue[Urb]" = queue.Queue()
        self.unlinked: set = set()
        self.words: list[int] = []           # report words for the firmware's mailbox
        self.outstanding = 0                 # reports pushed but not yet delivered
        self.toggle = "DATA0"                # endpoint 1, as the host tracks it
        self.ready = threading.Event()       # the device is reset and addressed
        self.stop = threading.Event()
        self.work = threading.Event()       # a URB or a key arrived
        self._lock = threading.RLock()        # (the script runs inside m.run, on the same thread)
        self._send_lock = threading.Lock()
        self._idle = False

    # ------------------------------------------------------------ input

    def type_text(self, text: str) -> None:
        with self._lock:
            for r in keyboard_reports(text):
                self.words += [int.from_bytes(r[:4], "little"), int.from_bytes(r[4:], "little")]
                self.outstanding += 1
        self.work.set()

    # ------------------------------------------------------------ socket side

    def _reader(self) -> None:
        try:
            while not self.stop.is_set():
                h = self._recv(HEADER)
                if h is None:
                    break
                command, seqnum, devid, direction, ep = parse_header(h)
                if command == CMD_SUBMIT:
                    flags, length, _sf, _np, _iv = struct.unpack(">5I", h[20:40])
                    setup = h[40:48]
                    data = self._recv(length) if direction == 0 and length else b""
                    if DEBUG:
                        print(f"urb {seqnum} ep{ep} {'IN' if direction else 'OUT'} len {length} setup {setup.hex()}", file=sys.stderr)
                    self.urbs.put(Urb(seqnum, devid, direction, ep, length, setup, data or b""))
                    self.work.set()
                elif command == CMD_UNLINK:
                    victim = struct.unpack(">I", h[20:24])[0]
                    if DEBUG:
                        print(f"unlink {victim}", file=sys.stderr)
                    with self._lock:
                        self.unlinked.add(victim)
                    self.urbs.put(Urb(seqnum, devid, 0, -1, victim, b""))
                    self.work.set()
                else:
                    self.errors.append(f"unknown USB/IP command {command}")
                    break
        except OSError:
            pass
        self.stop.set()
        self.work.set()

    def _recv(self, n: int) -> Optional[bytes]:
        buf = b""
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
            except OSError:
                return None
            if not chunk:
                return None
            buf += chunk
        return buf

    def _reply(self, b: bytes) -> None:
        with self._send_lock:
            self.sock.sendall(b)

    # ------------------------------------------------------------ the host script

    def _script(self, h):
        yield from bus_reset(h, 1000)
        r = yield from control(h, 0, setup_bytes(0, 5, ADDR, 0, 0))
        assert r == b"", r
        yield ("idle", 3000)
        self.ready.set()
        pending: list[Urb] = []              # interrupt URBs waiting for a report
        while not self.stop.is_set():
            try:
                u = self.urbs.get_nowait()
            except queue.Empty:
                u = None
            if u is not None and u.ep == -1:                     # UNLINK
                hit = [p for p in pending if p.seqnum == u.length]
                for p in hit:
                    pending.remove(p)
                self._reply(ret_unlink(u.seqnum, ECONNRESET if hit else 0))
                continue
            if u is not None and u.ep == 0:
                yield from self._control(h, u)
                continue
            if u is not None:
                pending.append(u)
            with self._lock:
                feeding = self.outstanding > 0
            if pending and feeding:
                yield from self._interrupt(h, pending)
                continue
            self._idle = True                                    # nothing to do: time stops
            yield ("idle", 64)
            self._idle = False

    def _control(self, h, u: Urb):
        setup = u.setup
        wlength = setup[6] | (setup[7] << 8)
        try:
            out = u.data[:wlength] if not setup[0] & 0x80 and wlength else None
            r = yield from control(h, ADDR, setup, out=out)
        except UsbError as e:
            self.errors.append(f"seq {u.seqnum}: {e}")
            self._reply(ret_submit(u.seqnum, EPROTO))
            return
        if setup[1] == 9 or (setup[0] == 2 and setup[1] == 1):   # the toggle restarts
            self.toggle = "DATA0"
        self.log.append((u.seqnum, setup.hex(), r if r == "STALL" else len(r)))
        if r == "STALL":
            self._reply(ret_submit(u.seqnum, EPIPE))
        elif setup[0] & 0x80:
            self._reply(ret_submit(u.seqnum, 0, r[:u.length]))
        else:
            self._reply(ret_submit(u.seqnum, 0, actual=0 if out is None else len(out)))

    def _interrupt(self, h, pending: list):
        p = yield from in_txn(h, ADDR, pending[0].ep)
        if p is None or p.packet.name == "NAK":
            yield ("idle", 200)
            return
        if p.packet.name not in ("DATA0", "DATA1"):
            self.errors.append(f"endpoint {pending[0].ep}: {p.packet.name}")
            return
        if p.packet.name != self.toggle:
            return                                               # a repeat: discarded
        self.toggle = "DATA1" if self.toggle == "DATA0" else "DATA0"
        with self._lock:
            self.outstanding -= 1
        u = pending.pop(0)
        self._reply(ret_submit(u.seqnum, 0, p.packet.body[:-2][:u.length]))

    # ------------------------------------------------------------ the simulation loop

    def run(self) -> None:
        """Serve until the socket closes or `stop` is set (call from one thread)."""
        threading.Thread(target=self._reader, daemon=True).start()
        try:
            self._serve()
        except Exception as e:                                   # noqa: BLE001
            self.errors.append(f"bridge stopped: {e!r}")
            self.stop.set()
        if self.m.ff.valid:
            self.errors.append(f"chip fault: {self.m.final_state()['first_fault']}")
        self.errors += self.host.errors

    def _serve(self) -> None:
        self.host.start(self._script)
        while not self.stop.is_set() and not self.m.ff.valid:
            with self._lock:
                while self.words and len(self.m.ctx[1].rx) < 2:
                    self.m.host_rx_push(1, self.words.pop(0))
                    self.m.run(1, stop_when_idle=False)
            if self._idle and self.urbs.empty() and not self.work.is_set():
                self.work.wait(0.05)                             # time stands still
                continue
            self.work.clear()                                    # (the script sees it next)
            self.m.run(256, stop_when_idle=False)
