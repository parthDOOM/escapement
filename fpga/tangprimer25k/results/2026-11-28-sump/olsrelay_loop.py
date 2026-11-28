"""Relay bytes between the bench Pico's USB serial port and socat in WSL (which makes the
pseudo-terminal sigrok-cli opens; WSL cannot open a Windows COM port). Bytes pass unchanged
both ways. socat ends when sigrok-cli closes its pty and the WSL script starts a new one per
attempt, so this reconnects each time, keeping the COM port open, until OUT/done appears.

    python olsrelay_loop.py COM8 9555 [OUT]"""
import os
import socket
import sys
import threading
import time

import serial

port, tcp = sys.argv[1], int(sys.argv[2])
out = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
done = os.path.join(out, "done")
ser = serial.Serial(port, 115200, timeout=0.001)
deadline = time.time() + 900
n = 0
while not os.path.exists(done) and time.time() < deadline:
    try:
        sock = socket.create_connection(("127.0.0.1", tcp), timeout=0.5)
    except OSError:
        time.sleep(0.2)
        continue
    sock.settimeout(None)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    n += 1
    stop = threading.Event()
    count = {"to_pico": 0, "from_pico": 0}

    def up():
        while not stop.is_set():
            try:
                d = sock.recv(4096)
            except OSError:
                break
            if not d:
                break
            ser.write(d)
            count["to_pico"] += len(d)
        stop.set()

    t = threading.Thread(target=up, daemon=True)
    t.start()
    while not stop.is_set():
        d = ser.read(ser.in_waiting or 1)
        if d:
            try:
                sock.sendall(d)
            except OSError:
                break
            count["from_pico"] += len(d)
    stop.set()
    sock.close()
    print("connection %d: %s" % (n, count), flush=True)
print("relay done")
