"""python -m escusbip [TEXT]: plug the simulated keyboard into this Linux kernel (as root).

Attaches firmware/usb_hid.asm, running on the ISS, to vhci_hcd as a low-speed device,
waits for the kernel to enumerate it and bind usbhid, opens the input device the kernel
made, types TEXT through the chip, and checks the key events the kernel reports.
"""

from __future__ import annotations

import os
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

from escasm import assemble_file

from . import Bridge

VHCI = Path("/sys/devices/platform/vhci_hcd.0")
FW = Path(__file__).resolve().parents[2] / "firmware" / "usb_hid.asm"
EV_KEY, KEY_LEFTSHIFT = 1, 42
EVENT = struct.Struct("llHHi")             # struct input_event (64-bit time)

# Linux key codes (include/uapi/linux/input-event-codes.h) to characters
_ROWS = {2: "1234567890-=", 16: "qwertyuiop", 30: "asdfghjkl;", 44: "zxcvbnm,./"}
KEYMAP = {code + i: ch for code, row in _ROWS.items() for i, ch in enumerate(row)}
KEYMAP.update({28: "\n", 57: " "})
SHIFTED = {"1": "!", "/": "?", ";": ":"}


def free_port() -> int:
    for line in (VHCI / "status").read_text().splitlines()[1:]:
        f = line.split()
        if f[0] == "hs" and int(f[2]) == 4:            # VDEV_ST_NULL: free
            return int(f[1])
    raise SystemExit("no free vhci_hcd port")


def wait_for(fn, timeout: float, what: str):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    raise SystemExit(f"timed out waiting for {what}")


def usb_device():
    for d in Path("/sys/bus/usb/devices").iterdir():
        try:
            if ((d / "idVendor").read_text().strip(), (d / "idProduct").read_text().strip()) \
                    == ("1209", "0001"):
                return d
        except OSError:
            continue
    return None


def input_node():
    for e in Path("/sys/class/input").glob("event*"):
        try:
            if "Escapement" in (e / "device" / "name").read_text():
                return Path("/dev/input") / e.name
        except OSError:
            continue
    return None


def decode(events) -> str:
    out, shift = "", False
    for typ, code, value in events:
        if typ != EV_KEY:
            continue
        if code == KEY_LEFTSHIFT:
            shift = value != 0
        elif value == 1 and code in KEYMAP:
            ch = KEYMAP[code]
            out += (SHIFTED.get(ch, ch.upper()) if shift else ch)
    return out


def main(argv: list[str]) -> int:
    text = argv[0] if argv else "Hello from Escapement!\n"
    if os.geteuid() != 0:
        raise SystemExit("run as root (vhci_hcd's attach file needs it)")
    for mod in ("vhci-hcd", "usbhid", "hid-generic", "evdev"):
        subprocess.run(["modprobe", mod], check=False)
    kern, ours = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    bridge = Bridge(assemble_file(str(FW)), ours)
    threading.Thread(target=bridge.run, daemon=True).start()
    if not bridge.ready.wait(60):
        raise SystemExit("the device did not come up on the simulated bus")
    port = free_port()
    t0 = time.time()
    (VHCI / "attach").write_text(f"{port} {kern.fileno()} {(1 << 16) | 2} 1")   # 1: low speed
    print(f"attached to vhci_hcd port {port} as a low-speed device")
    try:
        dev = wait_for(usb_device, 60, "the kernel to enumerate the device")
        node = wait_for(input_node, 60, "usbhid to make an input device")
        print(f"enumerated in {time.time() - t0:.1f} s as {dev.name}; input device {node}")
        print(subprocess.run(["lsusb", "-d", "1209:0001"], capture_output=True, text=True).stdout.strip())
        fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)  # opening it starts the polling
        time.sleep(0.5)
        bridge.type_text(text)
        events, end = [], time.time() + 120
        while time.time() < end and decode(events) != text:
            try:
                buf = os.read(fd, EVENT.size * 64)
            except BlockingIOError:
                time.sleep(0.05)
                continue
            for i in range(0, len(buf), EVENT.size):
                _, _, typ, code, value = EVENT.unpack_from(buf, i)
                events.append((typ, code, value))
        os.close(fd)
        got = decode(events)
        print(f"typed {text!r}; the kernel reported {got!r}")
        dmesg = subprocess.run(["dmesg"], capture_output=True, text=True).stdout.splitlines()
        print("\n".join(line for line in dmesg[-40:] if "usb" in line or "input" in line
                        or "hid" in line))
        ok = got == text and not bridge.errors
    finally:
        (VHCI / "detach").write_text(str(port))
        bridge.stop.set()
    print("errors:", bridge.errors or "none")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
