"""escusbip: the USB/IP bridge, with this test playing the kernel's vhci_hcd side.

The URBs are the ones Linux sends to a low-speed HID keyboard (vhci_hcd answers SET_ADDRESS
itself), in its order; every answer comes from the firmware on the ISS. The real-kernel
run is `python -m escusbip` inside Linux as root (docs/19-LINE-UNIT.md).
"""

from __future__ import annotations

import socket
import struct
import threading
from pathlib import Path

import pytest

from escasm import assemble_file
from escmodels.usb import keyboard_reports
from escusbip import (CMD_SUBMIT, CMD_UNLINK, ECONNRESET, EPIPE, HEADER, RET_SUBMIT,
                      RET_UNLINK, Bridge)

FW = Path(__file__).resolve().parents[2] / "firmware" / "usb_hid.asm"


class Kernel:
    """The vhci_hcd end of the socket."""

    def __init__(self):
        self.sock, far = socket.socketpair()
        self.sock.settimeout(60)
        self.bridge = Bridge(assemble_file(str(FW)), far)
        self.thread = threading.Thread(target=self.bridge.run, daemon=True)
        self.thread.start()
        assert self.bridge.ready.wait(60)
        self.seq = 0
        self.dirs = {}                                       # seqnum -> direction

    def recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            assert chunk, "bridge closed the socket"
            buf += chunk
        return buf

    def submit(self, ep, direction, length, setup=bytes(8), data=b"", wait=True):
        self.seq += 1
        self.dirs[self.seq] = direction
        hdr = struct.pack(">5I", CMD_SUBMIT, self.seq, (1 << 16) | 2, direction, ep)
        hdr += struct.pack(">5I", 0, length, 0, 0, 0) + setup
        self.sock.sendall(hdr + data)
        return self.result() if wait else self.seq

    def result(self):
        h = self.recv(HEADER)
        command, seq = struct.unpack(">2I", h[:8])
        status, actual = struct.unpack(">ii", h[20:28])
        if command == RET_UNLINK:
            return command, seq, status, b""
        assert command == RET_SUBMIT
        if not self.dirs[seq]:                               # OUT: a count and no data,
            return command, seq, status, actual              # as vhci_hcd reads it
        return command, seq, status, self.recv(actual) if actual else b""

    def control(self, bm, req, value, index, length, data=b""):
        setup = struct.pack("<BBHHH", bm, req, value, index, length)
        _, _, status, out = self.submit(0, 1 if bm & 0x80 else 0, length, setup, data)
        return status, out

    def unlink(self, victim):
        self.seq += 1
        hdr = struct.pack(">5I", CMD_UNLINK, self.seq, (1 << 16) | 2, 0, 0)
        self.sock.sendall(hdr + struct.pack(">I", victim) + bytes(24))
        return self.seq

    def close(self):
        self.bridge.stop.set()
        self.sock.close()
        self.thread.join(10)


@pytest.fixture(scope="module")
def kernel():
    k = Kernel()
    yield k
    k.close()
    assert k.bridge.errors == [], k.bridge.errors


def test_linux_enumeration_sequence(kernel):
    k = kernel
    st, dev8 = k.control(0x80, 6, 0x0100, 0, 64)              # hub_port_init: 64 at first
    assert st == 0 and len(dev8) == 18 and dev8[7] == 8
    st, dev = k.control(0x80, 6, 0x0100, 0, 18)
    assert st == 0 and dev == dev8 and dev[8:12] == bytes((0x09, 0x12, 0x01, 0x00))
    st, cfg9 = k.control(0x80, 6, 0x0200, 0, 9)
    total = cfg9[2] | (cfg9[3] << 8)
    st, cfg = k.control(0x80, 6, 0x0200, 0, total)
    assert st == 0 and len(cfg) == total == 34
    st, s0 = k.control(0x80, 6, 0x0300, 0, 255)
    assert s0 == bytes((4, 3, 9, 4))
    st, s2 = k.control(0x80, 6, 0x0302, 0x0409, 255)
    assert s2[2:].decode("utf-16-le") == "Escapement"
    st, _ = k.control(0x80, 6, 0x0600, 0, 10)                 # device qualifier: not HS
    assert st == -EPIPE
    assert k.control(0x00, 9, 1, 0, 0) == (0, 0)              # SET_CONFIGURATION 1
    assert k.control(0x21, 0x0A, 0, 0, 0) == (0, 0)           # SET_IDLE
    st, rd = k.control(0x81, 6, 0x2200, 0, 63 + 64)           # report descriptor
    assert st == 0 and len(rd) == 63 and rd[:4] == bytes((5, 1, 9, 6))
    assert k.control(0x21, 9, 0x0200, 0, 1, b"\x01") == (0, 1)    # LEDs: 1 byte taken
    st, cfg = k.control(0x80, 8, 0, 0, 1)                     # the stream is still in step
    assert (st, cfg) == (0, b"\x01")


def test_typed_text_arrives_as_interrupt_urbs(kernel):
    k = kernel
    seqs = [k.submit(1, 1, 8, wait=False) for _ in range(2)]  # usbhid keeps URBs queued
    text = "Hi!\n"
    k.bridge.type_text(text)
    want = keyboard_reports(text)
    got = []
    while len(got) < len(want):
        _, seq, status, data = k.result()
        assert status == 0
        got.append(data)
        seqs.remove(seq)
        seqs.append(k.submit(1, 1, 8, wait=False))
    assert got == want
    for s in seqs:                                            # closing: unlink them
        k.unlink(s)
    res = [k.result() for _ in seqs]
    assert sorted(r[0] for r in res) == [RET_UNLINK] * len(seqs)
    assert all(r[2] == -ECONNRESET for r in res)


def test_keys_typed_after_the_bridge_went_idle(kernel):
    """As with a real kernel: usbhid queues its URB, the bridge stops time, and the keys
    come later."""
    import time
    k = kernel
    seq = k.submit(1, 1, 8, wait=False)
    end = time.time() + 30
    while not k.bridge._idle and time.time() < end:
        time.sleep(0.01)
    time.sleep(0.3)
    assert k.bridge._idle
    k.bridge.type_text("a")
    _, got_seq, status, data = k.result()
    assert (got_seq, status, data) == (seq, 0, keyboard_reports("a")[0])
    seq = k.submit(1, 1, 8, wait=False)
    assert k.result()[1:] == (seq, 0, bytes(8))               # the release


def test_unlink_of_a_finished_urb_reports_zero(kernel):
    k = kernel
    _, seq, status, _ = k.submit(0, 1, 2, struct.pack("<BBHHH", 0x80, 0, 0, 0, 2))
    assert status == 0
    k.unlink(seq)
    command, _, status, _ = k.result()
    assert (command, status) == (RET_UNLINK, 0)
