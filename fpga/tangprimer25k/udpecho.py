# UDP echo against the chip, from the laptop (README.md, Ethernet): while the Pico runs
# ethbench.ping() (bench.ps1 ethping), each datagram goes to the chip's echo port (7, RFC 862)
# with a number and bytes drawn for this run, and must come back byte for byte.
#
#     python udpecho.py [count] [ip]

import os
import socket
import sys
import time


def run(count=20, ip="169.254.0.42", size=32, timeout=2.0, gap=0.5, out=print):
    """Sends `count` datagrams of `size` bytes, each after the last one's echo or timeout.
    Returns (echoed intact, sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    tag = os.urandom(4).hex()
    good = 0
    try:
        for i in range(count):
            d = ("escapement %s %04d " % (tag, i)).encode()
            d += os.urandom(max(0, size - len(d)))
            t0 = time.perf_counter()
            s.sendto(d, (ip, 7))
            try:
                while True:
                    r, a = s.recvfrom(2048)
                    if a[0] == ip:
                        break
                ms = (time.perf_counter() - t0) * 1000
                ok = r == d
                good += ok
                out("udp %d: %d bytes back in %.0f ms%s" % (i + 1, len(r), ms,
                                                             "" if ok else ", NOT the bytes sent"))
            except (socket.timeout, ConnectionResetError):   # Windows: an ICMP unreachable
                out("udp %d: no echo in %.1f s" % (i + 1, timeout))
            time.sleep(gap)
    finally:
        s.close()
    out("udp: %d of %d datagrams echoed intact (run %s)" % (good, count, tag))
    return good, count


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 20,
        sys.argv[2] if len(sys.argv) > 2 else "169.254.0.42")
