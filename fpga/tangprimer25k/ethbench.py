# 10BASE-T on the bench, both directions, with the FPGA build of the chip clocked at 40 MHz
# by the Pico and a laptop's Ethernet port as the link partner (README.md, Ethernet, has the
# line circuits and the bench results).
#
#     import ethbench
#     ethbench.run()       # transmit: firmware/eth_tx.asm sends numbered test frames, which
#                          # Wireshark shows with the filter `eth.type == 0x88b5`
#     ethbench.listen()    # receive: firmware/eth_rx.asm (eth_rx_bench.asm) prints every
#                          # frame the laptop sends, with its FCS verdict
#     ethbench.edges()     # the receive line check: high and low times on RXP (eth_edges.asm)
#
# The clock: a Manchester cell is exactly 2 ticks at 40 MHz. The PWM divides the RP's
# system clock by a whole number, so the system clock goes to 160 MHz first (the RP2040's
# and RP2350's PLL make it exactly from the 12 MHz crystal); the PWM then divides by 4, with
# a 50% duty. 10BASE-T allows the bit rate 100 ppm off; the crystal is far closer.
#
# The upload: POP waits for each word, so eth_tx sends no link pulse while a frame comes
# in, and a 10BASE-T receiver drops the link after 50 to 150 ms with no pulse. The
# bring-up link (bringup.Link) takes about 68 us per SCK half period, about 105 ms for the
# 16 pushes of a 60-byte frame. So the pushes are bit-banged from a precomputed table of
# GPIO_OUT words, one register write each, by a @micropython.viper loop that writes through
# a pointer: 0.9 ms a frame on the bench (11.3 ms as bytecode, 9.4 ms as native code). The
# chip needs each SCK phase to last 4 core clocks (100 ns at 40 MHz): setup() calibrates a
# delay to CAL_WRITE_NS a write, and send() refuses a burst faster than MIN_WRITE_NS. MISO is
# not read during the burst; the status after it shows whether a push found the mailbox full.
#
# Receiving needs no burst: eth_rx arms again once the Pico has popped a frame's words, so
# a frame that arrives while the Pico drains the previous one is missed, never corrupted.
#
# Plain MicroPython (no typing, dataclasses or f-strings), so the same file runs on the
# board and under CPython with a simulated `machine` (test_ethbench.py).

from array import array

try:
    import micropython
    MPY = True
except ImportError:                      # CPython (the tests)
    MPY = False

import barepico
import bringup
from barepico import SIO_GPIO_OUT, UI_MASK, UI_SHIFT
from bringup import CS, MOSI, SCK, Fail, P

F_ETH = 40000000
SYS_HZ = 160000000
CTX = 0                                  # eth_tx's context (its .default CTX)
ETHERTYPE = 0x88B5                       # IEEE 802 local experimental EtherType 1
DST = b"\xff\xff\xff\xff\xff\xff"        # broadcast: every port passes it up
SRC = b"\x02\x00\x00\x00\x00\x01"        # a locally administered address
JOB_SEND = 4
# After a frame eth_tx waits for its next link pulse, 16 ms later, before it polls the
# mailbox again: a frame pushed sooner fills the two-word mailbox and loses a word.
MIN_GAP_MS = 20
MIN_WRITE_NS = 200                       # 8 core clocks a write: twice the chip's minimum
CAL_WRITE_NS = 300                       # what setup() calibrates the burst's delay to
RUNNING, HALTED = 1, 2                   # a context's state in the status word
RXP = 2                                  # the receive pin, uio[2] (eth_rx's .default RXP)
CAP_FULL = 1 << 29                       # capture status (debug select 17): the region full

if MPY:
    @micropython.viper
    def _blast(addr: uint, buf, n: int, d: int):
        p = ptr32(addr)
        t = ptr32(buf)
        i = 0
        while i < n:
            p[0] = t[i]
            j = d
            while j > 0:
                j -= 1
            i += 1


def blast(mem, addr, table, delay):
    """One register write per table entry. On the Pico a viper loop writes through a
    pointer, with `delay` empty iterations after each write (setup() calibrates it); under
    CPython, `mem` is the simulated register file."""
    if MPY:
        _blast(addr, table, len(table), delay)
    else:
        for v in table:
            mem[addr] = v


def test_frame(n):
    """Frame n: broadcast, EtherType 0x88B5, the text 'escapement eth_tx <n>', padded to
    60 bytes (the 802.3 minimum without the FCS, which the chip appends)."""
    f = DST + SRC + bytes((ETHERTYPE >> 8, ETHERTYPE & 0xFF)) + ("escapement eth_tx %d" % n).encode()
    return f + bytes(60 - len(f)) if len(f) < 60 else f


def frame_words(frame):
    """eth_tx's host input: the length in bytes, then the bytes as little-endian words."""
    out = [len(frame)]
    for i in range(0, len(frame), 4):
        c = frame[i:i + 4]
        out.append(int.from_bytes(c + bytes(4 - len(c)), "little"))
    return out


def burst_table(words, base, ui_shift):
    """GPIO_OUT words for one MBX_PUSH frame per word: CS_n low, 48 bits (MOSI set while SCK
    is low, then SCK high, then low: mode 0, as bringup.Link), CS_n high. `base` holds the
    other GPIOs' bits (reset, the uio pins the RP drives)."""
    t = array("I")
    for w in words:
        t.append(base)                           # CS_n low, SCK low
        for byte in P.plan_mbx_push(CTX, w)[0]:
            for i in range(7, -1, -1):
                m = base | (((MOSI if (byte >> i) & 1 else 0)) << ui_shift)
                t.append(m)
                t.append(m | (SCK << ui_shift))
                t.append(m)
        t.append(base | (CS << ui_shift))        # CS_n high: the frame ends
    return t


class EthBench:
    def __init__(self, io, bundles="bundles", out=print, bundle="eth_tx"):
        self.io, self.bundles, self.say, self.bundle = io, bundles, out, bundle
        self.b = bringup.Bringup(io, bundles, out=out, proof=None)
        self.host = None
        self.delay = 0

    def calibrate(self):
        """The viper burst's delay: the smallest that makes a write take CAL_WRITE_NS or
        more, measured with CS_n held high (the chip ignores SCK and MOSI then)."""
        if not MPY:
            return 0
        io, mem = self.io, self.io.platform.mem32
        idle = (mem[SIO_GPIO_OUT] & ~UI_MASK & 0x3FFFFFFF) | (CS << UI_SHIFT)
        t = array("I", [idle] * 2000)
        for d in range(0, 200):
            t0 = io.ticks_us()
            _blast(SIO_GPIO_OUT, t, len(t), d)
            us = io.ticks_diff(io.ticks_us(), t0)
            if us * 1000 >= CAL_WRITE_NS * len(t):
                return d
        raise Fail("could not slow the burst to %d ns a write" % CAL_WRITE_NS)

    def setup(self, halts=0):
        """The chip at 40 MHz with the bundle loaded and started; every enabled context
        must be running, except those in `halts`, which must have halted."""
        io = self.io
        io.select()                              # the project, a reset pulse, its clock
        hz = io.set_clock(F_ETH)
        if hz != F_ETH:
            raise Fail("the clock PWM makes %s Hz, not 40 MHz, from a %d Hz system clock: "
                       "eth_tx needs exactly 40 MHz (open_io(sys_hz=%d))"
                       % (hz, io.rp_clock(), SYS_HZ))
        io.reset(True)
        io.sleep_ms(2)
        io.reset(False)
        io.sleep_ms(2)
        self.b.connect()                         # CS_n high: the link is idle
        self.delay = self.calibrate()
        self.host = h = self.b.host
        st = h.status()
        if st["version"] < P.PAGES_VERSION or st["running"]:
            raise Fail("status after reset: %r (want version %d, stopped); run the bring-up "
                       "first (.\\bench.ps1 bringup)" % (st, P.PAGES_VERSION))
        b = bringup.load_bundle(self.bundles + "/" + self.bundle + ".json")
        words = list(b["words"]) + [0] * (256 - len(b["words"]))
        tape = [0] * 256
        for a, w in b["tape"].items():
            tape[int(a)] = w
        self.b.load(words, b["cfg"], tape)
        h.start()
        io.sleep_ms(20)                          # past the first link pulse
        st = h.status()
        for k in range(4):
            want = HALTED if (halts >> k) & 1 else RUNNING
            if (b["cfg"][0] >> k) & 1 and ((st["ctx_state"] >> (2 * k)) & 3 != want or st["fault"]):
                raise Fail("%s context %d is not %s after START: %r"
                           % (self.bundle, k, "halted" if want == HALTED else "running", st))
        self.say("eth: chip at %.3f MHz (RP system clock %d MHz); %s running, link "
                 "pulses every 16 ms; burst delay %d" % (hz / 1e6, io.rp_clock() // 1000000,
                                                         self.bundle, self.delay))

    def send(self, frame, timeout_ms=500):
        """Push one frame, wait for eth_tx's status word, check it. Returns (status word,
        upload microseconds)."""
        io, h = self.io, self.host
        plat = io.platform
        mem = plat.mem32
        base = mem[SIO_GPIO_OUT] & ~UI_MASK & 0x3FFFFFFF
        table = burst_table(frame_words(frame), base, UI_SHIFT)
        t0 = io.ticks_us()
        blast(mem, SIO_GPIO_OUT, table, self.delay)
        t_up = io.ticks_diff(io.ticks_us(), t0)
        if t_up * 1000 < MIN_WRITE_NS * len(table):
            raise Fail("the burst took %d us for %d writes, under %d ns a write: SCK phases "
                       "may be shorter than the chip's 4 core clocks"
                       % (t_up, len(table), MIN_WRITE_NS))
        st = h.status()
        if st["host_err"]:
            h.clear_err()
            raise Fail("a push found eth_tx's mailbox full (host error): a word was dropped")
        t0 = io.ticks_us()
        while True:
            v = h.mbx_pop(CTX)
            if v is not None:
                break
            if io.ticks_diff(io.ticks_us(), t0) > timeout_ms * 1000:
                raise Fail("no status from eth_tx %d ms after the frame (status %r)"
                           % (timeout_ms, h.status()))
        job, cause = (v >> 28) & 7, (v >> 25) & 7
        if job != JOB_SEND or cause:
            raise Fail("eth_tx's line unit status 0x%08x: job %d, cause %d (want SEND, 0)"
                       % (v, job, cause))
        if h.status()["fault"]:
            raise Fail("the chip reports a fault after frame: %r" % h.status())
        return v, t_up

    def run(self, count=10, interval_ms=1000):
        n = 0
        try:
            while not count or n < count:
                n += 1
                f = test_frame(n)
                v, t_up = self.send(f)
                self.say("frame %d: %d bytes on the wire, status 0x%08x (SEND, OK), upload "
                         "%.1f ms" % (n, len(f), v, t_up / 1000))
                self.io.sleep_ms(max(interval_ms, MIN_GAP_MS))
        except KeyboardInterrupt:
            n -= 1
        tw = self.host.tripwires()
        if tw:
            raise Fail("tripwires set (0x%03x): %s" % (tw, "; ".join(P.tripwire_names(tw))))
        self.say("eth: %d frame(s) sent, tripwires clear. Wireshark: eth.type == 0x88b5. "
                 "The chip keeps sending link pulses until the Pico resets it." % n)
        return n

    # -- receive (eth_rx_bench.asm: eth_rx in context RX_CTX, link pulses in context 1)
    def pop(self, timeout_ms):
        """One word from eth_rx's mailbox, or None after timeout_ms."""
        io, h = self.io, self.host
        t0 = io.ticks_us()
        while True:
            v = h.mbx_pop(RX_CTX)
            if v is not None:
                return v
            if io.ticks_diff(io.ticks_us(), t0) > timeout_ms * 1000:
                return None

    def listen(self, seconds=30, count=0):
        """eth_rx's receptions for `seconds`, or until `count` frames: each printed with its
        FCS verdict and a summary. eth_rx arms again only once a frame's words are popped,
        so a frame that arrives while the Pico drains the last one is missed (never
        corrupted). Returns [(header, bytes with the FCS)]."""
        io, got = self.io, []
        t0 = io.ticks_us()
        try:
            while (not count or len(got) < count) and                     io.ticks_diff(io.ticks_us(), t0) < seconds * 1000000:
                hdr = self.pop(100)
                if hdr is None:
                    continue
                bits = hdr & 0x3FFF
                ws = []
                for _ in range((bits + 31) // 32):
                    w = self.pop(500)
                    if w is None:
                        raise Fail("eth_rx's header 0x%08x promises %d bits; only %d words came"
                                   % (hdr, bits, len(ws)))
                    ws.append(w)
                data = b"".join(w.to_bytes(4, "little") for w in ws)[:bits // 8]
                got.append((hdr, data))
                cause = (hdr >> 28) & 7
                self.say("rx %d: %d bytes + FCS, FCS %s%s  %s"
                         % (len(got), max(len(data) - 4, 0), "good" if hdr >> 31 else "BAD",
                            "" if not cause else " (cause %d: the buffer was full)" % cause,
                            summary(data[:-4])))
        except KeyboardInterrupt:
            pass
        tw = self.host.tripwires()
        if tw:
            raise Fail("tripwires set (0x%03x): %s" % (tw, "; ".join(P.tripwire_names(tw))))
        return got

    # -- the receive line check (eth_edges.asm: the capture unit on RXP, link pulses)
    def edges(self, seconds=15):
        """Wait for the capture to fill the tape (the first frame does), or `seconds`;
        then stop the chip and read the records back. Returns line_check()'s result."""
        io, h = self.io, self.host
        t0 = io.ticks_us()
        while not h.read_dbg(0, 17) & CAP_FULL:
            if io.ticks_diff(io.ticks_us(), t0) > seconds * 1000000:
                break
            io.sleep_ms(100)
        h.stop()
        recs = []
        for a in range(0, 256, 16):
            recs += h.read_tape(a, 16)
        return line_check(recs)

    # -- the ping bench (eth_trx.asm: receive and transmit on one line unit)
    def push_words(self, words):
        """Mailbox pushes to context 0 by the burst, then a check that none found it full."""
        io, h = self.io, self.host
        mem = io.platform.mem32
        base = mem[SIO_GPIO_OUT] & ~UI_MASK & 0x3FFFFFFF
        blast(mem, SIO_GPIO_OUT, burst_table(words, base, UI_SHIFT), self.delay)
        if h.status()["host_err"]:
            h.clear_err()
            raise Fail("a push found eth_trx's mailbox full: a word was dropped")

    def respond(self, ip, mac=SRC, seconds=60, count=0):
        """Answer ARP requests for `ip`, and ICMP echo requests and UDP echo datagrams to it,
        for `seconds` or until `count` replies. Each received frame is read from the tape,
        its buffer released at once, and its reply (if any) pushed for sending. Returns the
        counts."""
        io, h = self.io, self.host
        n = {"received": 0, "bad FCS": 0, "ARP replies": 0, "echo replies": 0,
             "UDP echoes": 0, "sent": 0}
        t0 = io.ticks_us()
        try:
            while (not count or n["ARP replies"] + n["echo replies"] + n["UDP echoes"]
                   < count) and \
                    io.ticks_diff(io.ticks_us(), t0) < seconds * 1000000:
                v = h.mbx_pop(CTX)
                if v is None:
                    continue
                tag = (v >> 24) & 3
                if tag == TAG_SENT:
                    if (v >> 3) & 7 != JOB_SEND or v & 7:
                        raise Fail("eth_trx's line unit status 0x%08x after a send" % v)
                    n["sent"] += 1
                    continue
                if tag != TAG_RECEIVED:
                    raise Fail("unexpected word 0x%08x from eth_trx" % v)
                bits = v & 0x3FFF
                ws, a, nw = [], 0, (bits + 31) // 32
                while a < nw:
                    k = min(16, nw - a)
                    ws += h.read_tape(TRX_RB + a, k)
                    a += k
                self.push_words([CMD_RELEASE])           # the receiver arms again
                data = b"".join(w.to_bytes(4, "little") for w in ws)[:bits // 8]
                n["received"] += 1
                if not v >> 31:
                    n["bad FCS"] += 1
                    self.say("rx %d: %d bytes, FCS BAD" % (n["received"], len(data)))
                    continue
                f = data[:-4]
                r = reply_to(f, mac, ip)
                kind = None if r is None else (0 if r[12:14] == b"\x08\x06" else
                                               1 if r[23] == 1 else 2)
                self.say("rx %d: %s%s" % (n["received"], summary(f), "" if kind is None else
                                          ("  -> ARP reply", "  -> echo reply",
                                           "  -> UDP echo")[kind]))
                if r is not None:
                    w = frame_words(r)
                    w[0] |= CMD_SEND
                    self.push_words(w)
                    n[("ARP replies", "echo replies", "UDP echoes")[kind]] += 1
        except KeyboardInterrupt:
            pass
        tw = h.tripwires()
        if tw:
            raise Fail("tripwires set (0x%03x): %s" % (tw, "; ".join(P.tripwire_names(tw))))
        return n


RX_CTX = 0                               # eth_rx's context in eth_rx_bench.asm
TRX_RB = 0x00                            # eth_trx.asm: the receive buffer in the tape
TAG_RECEIVED, TAG_SENT = 1, 2            # eth_trx.asm: [25:24] of each word it pushes
CMD_RELEASE, CMD_SEND = 1 << 28, 2 << 28  # eth_trx.asm: the host's commands
TRX_MAX = 448                            # eth_trx.asm: the transmit buffer, in bytes


def _ip(s):
    return bytes(int(x) for x in s.split("."))


def _csum(b):
    """The internet checksum (RFC 1071) of b."""
    s = 0
    for i in range(0, len(b) - 1, 2):
        s += (b[i] << 8) | b[i + 1]
    if len(b) & 1:
        s += b[-1] << 8
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return ~s & 0xFFFF


def _pad(f):
    return f + bytes(60 - len(f)) if len(f) < 60 else f


def reply_to(f, mac, ip):
    """The frame the chip answers frame f (without its FCS) with, or None: an ARP reply to a
    request for `ip`, an ICMP echo reply to an echo request to `ip`, or a UDP datagram to
    `ip`'s echo port (7, RFC 862) sent back to its source port (IPv4 options and the data
    kept). mac, ip: bytes."""
    if len(f) < 42:
        return None
    et = (f[12] << 8) | f[13]
    if et == 0x0806 and f[14:22] == b"\x00\x01\x08\x00\x06\x04\x00\x01" and f[38:42] == ip:
        return _pad(f[22:28] + mac + b"\x08\x06" + b"\x00\x01\x08\x00\x06\x04\x00\x02"
                    + mac + ip + f[22:28] + f[28:32])
    if et != 0x0800 or f[14] >> 4 != 4 or f[23] not in (1, 17) or f[30:34] != ip:
        return None
    ihl, tot = (f[14] & 15) * 4, (f[16] << 8) | f[17]
    ic = bytearray(f[14 + ihl:14 + tot])
    if len(ic) < 8 or 14 + tot > TRX_MAX:
        return None
    if f[23] == 1:
        if ic[0] != 8:
            return None
        ic[0], ic[2], ic[3] = 0, 0, 0            # echo reply, checksum recomputed
        c = _csum(ic)
        ic[2], ic[3] = c >> 8, c & 0xFF
    else:
        if ic[2:4] != b"\x00\x07" or (ic[4] << 8) | ic[5] != len(ic):
            return None
        ic[0:4] = ic[2:4] + ic[0:2]              # from the echo port, to the source port
        ic[6], ic[7] = 0, 0                      # checksum over the pseudo-header, recomputed
        c = _csum(f[30:34] + f[26:30] + b"\x00\x11" + bytes(ic[4:6]) + bytes(ic)) or 0xFFFF
        ic[6], ic[7] = c >> 8, c & 0xFF
    h = bytearray(f[14:14 + ihl])
    h[8], h[10], h[11] = 64, 0, 0                # TTL, checksum recomputed
    h[12:16], h[16:20] = f[30:34], f[26:30]      # from us, to the sender
    c = _csum(h)
    h[10], h[11] = c >> 8, c & 0xFF
    return _pad(f[6:12] + mac + b"\x08\x00" + bytes(h) + bytes(ic))
TICK_NS = 25                             # at 40 MHz
LONG_RUN = 5                             # ticks: no Manchester run lasts longer (100 ns + a tick)
GAP = 40                                 # ticks: a longer run is idle between frames


def line_check(recs):
    """High and low times on RXP from capture records ({levels[7:0], tick[23:0]}, one per
    change). Runs longer than GAP ticks are idle and skipped. A clean 10BASE-T line at
    40 MHz has only 2- and 4-tick runs (50 and 100 ns), the lows as long as the highs; a run
    over LONG_RUN ticks means an edge was missed. The runs are whole ticks, and over one
    frame's start the partner's edges keep nearly one phase against the chip's clock, so a
    skew of a few ns can read as a tick at each edge. Without a missed edge the skew gives the
    direction to move the bias, and eth_rx's FCS count says whether to. Returns a dict: high
    and low (width: count), the mean of each, long (runs over LONG_RUN), and verdict."""
    hi, lo, long_ = {}, {}, 0
    for a, b in zip(recs, recs[1:]):
        if not a or not b:
            continue
        la, lb = (a >> (24 + RXP)) & 1, (b >> (24 + RXP)) & 1
        d = ((b & 0xFFFFFF) - (a & 0xFFFFFF)) & 0xFFFFFF
        if la == lb or not 1 <= d <= GAP:
            continue
        c = hi if la else lo
        c[d] = c.get(d, 0) + 1
        if d > LONG_RUN:
            long_ += 1

    def mean(c):
        n = sum(c.values())
        return sum(k * v for k, v in c.items()) / n if n else 0.0
    r = {"high": hi, "low": lo, "mean_high": mean(hi), "mean_low": mean(lo), "long": long_}
    skew = r["mean_high"] - r["mean_low"]
    side = "lows" if skew > 0 else "highs"
    fix = ("the bias is above the pin's switching point: lower it" if skew > 0 else
           "the bias is below the pin's switching point: raise it")
    if not hi or not lo:
        r["verdict"] = ("no frame seen: RXP never changed (nothing reaches the pin, or the "
                        "swing never crosses its switching point)")
    elif long_:
        r["verdict"] = ("edges missed, %s short: eth_rx cannot decode this; %s (or a "
                        "coupling capacitor is loose: only the edges get through)" % (side, fix))
    elif abs(skew) <= 0.5:
        r["verdict"] = "clean: highs and lows alike, no missed edge"
    else:
        r["verdict"] = ("decodable, no missed edge; the %s read %.0f ns short, within a tick "
                        "at each edge, finer than this check resolves: if listen() finds bad "
                        "FCSs, %s" % (side, abs(skew) * TICK_NS, fix))
    return r


def _mac(b):
    return ":".join("%02x" % x for x in b)


def _ip4(b):
    return ".".join(str(x) for x in b)


def summary(f):
    """One line about an Ethernet frame (without its FCS): addresses, EtherType, and for
    ARP, IPv4 (UDP ports) and IPv6 the first fields."""
    if len(f) < 14:
        return "runt (%d bytes)" % len(f)
    et = (f[12] << 8) | f[13]
    s = "%s -> %s  type 0x%04x" % (_mac(f[6:12]), _mac(f[0:6]), et)
    p = f[14:]
    if et == 0x0806 and len(p) >= 28:
        op = (p[6] << 8) | p[7]
        s += "  ARP %s %s, from %s" % ("who-has" if op == 1 else "reply", _ip4(p[24:28]),
                                       _ip4(p[14:18]))
    elif et == 0x0800 and len(p) >= 20:
        ihl = (p[0] & 15) * 4
        s += "  IPv4 %s -> %s proto %d" % (_ip4(p[12:16]), _ip4(p[16:20]), p[9])
        if p[9] == 17 and len(p) >= ihl + 4:
            s += " UDP %d -> %d" % ((p[ihl] << 8) | p[ihl + 1], (p[ihl + 2] << 8) | p[ihl + 3])
    elif et == 0x86DD and len(p) >= 40:
        s += "  IPv6 next header %d" % p[6]
    return s


def listen(seconds=30, count=0, bundles="bundles", io=None, out=print, bundle="eth_rx_bench"):
    """Set up the chip at 40 MHz with the receive bench (eth_rx_bench.asm; bundle
    eth_rx_bench_one: one sample a tick), then print every frame the link partner sends for
    `seconds` (or until `count` frames). Returns [(header, bytes)], or None on a failure."""
    if io is None:
        io = barepico.open_io(sys_hz=SYS_HZ)
    e = EthBench(io, bundles, out, bundle)
    try:
        e.setup()
        got = e.listen(seconds, count)
    except Fail as x:
        out("eth: FAIL  %s" % x)
        if e.host is not None:
            e.host.stop()
        return None
    good = sum(1 for h, _ in got if h >> 31)
    out("eth: %d frame(s) received, %d with a good FCS, tripwires clear" % (len(got), good))
    return got


def edges(seconds=60, bundles="bundles", io=None, out=print, bundle="eth_edges"):
    """The receive line check: set up the chip at 40 MHz with eth_edges.asm (link pulses,
    the capture unit on RXP), capture the edges of the link partner's first frame, and
    print the high and low times with a verdict. Returns line_check()'s dict, or None."""
    if io is None:
        io = barepico.open_io(sys_hz=SYS_HZ)
    e = EthBench(io, bundles, out, bundle)
    try:
        e.setup(halts=1 << RX_CTX)
        r = e.edges(seconds)
    except Fail as x:
        out("eth: FAIL  %s" % x)
        if e.host is not None:
            e.host.stop()
        return None

    def fmt(c):
        return " ".join("%d:%d" % (k, c[k]) for k in sorted(c)) or "-"
    out("eth: RXP high runs (ticks:count) %s, mean %.0f ns" % (fmt(r["high"]), r["mean_high"] * TICK_NS))
    out("eth: RXP low runs  (ticks:count) %s, mean %.0f ns" % (fmt(r["low"]), r["mean_low"] * TICK_NS))
    out("eth: %d run(s) over %d ticks; %s" % (r["long"], LONG_RUN, r["verdict"]))
    return r


def ping(seconds=60, ip="169.254.0.42", count=0, bundles="bundles", io=None, out=print,
         bundle="eth_trx"):
    """The ping bench: set up the chip at 40 MHz with eth_trx.asm, then answer ARP, ICMP echo
    and UDP echo for `ip` (default a link-local address, beside the laptop's own) for
    `seconds`, or until `count` replies. From the laptop: ping 169.254.0.42, or udpecho.py.
    Returns the counts, or None."""
    if io is None:
        io = barepico.open_io(sys_hz=SYS_HZ)
    e = EthBench(io, bundles, out, bundle)
    try:
        e.setup()
        out("eth: answering ARP, ping and UDP echo for %s as %s" % (ip, _mac(SRC)))
        n = e.respond(_ip(ip), SRC, seconds, count)
    except Fail as x:
        out("eth: FAIL  %s" % x)
        if e.host is not None:
            e.host.stop()
        return None
    out("eth: %s; tripwires clear" % ", ".join("%s %d" % (k, n[k]) for k in
                                               ("received", "bad FCS", "ARP replies",
                                                "echo replies", "UDP echoes", "sent")))
    return n


def run(count=10, interval_ms=1000, bundles="bundles", io=None, out=print, bundle="eth_tx",
        settle_ms=0):
    """Set up the chip at 40 MHz with eth_tx, then send `count` test frames (0: until
    Ctrl-C), `interval_ms` apart (at least MIN_GAP_MS). io: a bringup.DemoBoardIO (default:
    barepico's, with the system clock at 160 MHz). bundle: eth_tx's bundle name.
    settle_ms: a wait after setup, with link pulses only, so the link partner has the link
    up before the first frame (a laptop takes several seconds). Returns the number of frames
    sent."""
    if io is None:
        io = barepico.open_io(sys_hz=SYS_HZ)
    e = EthBench(io, bundles, out, bundle)
    try:
        e.setup()
        if settle_ms:
            out("eth: link pulses only for %d ms, for the link partner" % settle_ms)
            io.sleep_ms(settle_ms)
        return e.run(count, interval_ms)
    except Fail as x:
        out("eth: FAIL  %s" % x)
        if e.host is not None:
            e.host.stop()
        return -1
