# Wiring check, run on the Pico with pinid.fs loaded on the Tang Primer 25K Dock.
#
#     import pinid; pinid.run()
#
# pinid.v sends each FPGA pin's index on that pin (open drain, two 8N1 bytes: 0x80|index
# then its complement, 1 kbit/s). This file pulls up all 26 header GPIOs, samples them
# together for 100 ms and decodes every GPIO: which FPGA signal arrives there, against the
# wiring table of barepico.py. No GPIO is ever driven, so it is safe with either bitstream.
#
# Plain MicroPython; decode() also runs under CPython (test_pinid.py feeds it the
# simulated pinid.v).

SIGNALS = (["clk", "rst_n"] + ["ui_in[%d]" % k for k in range(8)]
           + ["uo_out[%d]" % k for k in range(8)] + ["uio[%d]" % k for k in range(8)])
# Pico GPIO for each signal index (barepico.py's wiring table)
EXPECT = [0, 28] + [1 + k for k in range(8)] + [10 + k for k in range(8)] + [9, 18, 19, 20, 21, 22, 26, 27]
HEADER_GPIOS = sorted(EXPECT)
BIT_US = 1000
SIO_GPIO_IN = 0xD0000004


def capture(n=1000, step_us=100):
    import machine, time
    from array import array
    for g in HEADER_GPIOS:
        machine.Pin(g, machine.Pin.IN, machine.Pin.PULL_UP)
    time.sleep_ms(5)
    words = array("I", bytearray(4 * n))
    stamps = array("I", bytearray(4 * n))
    mem32 = machine.mem32
    t0 = time.ticks_us()
    nxt = t0
    for i in range(n):
        while time.ticks_diff(time.ticks_us(), nxt) < 0:
            pass
        stamps[i] = time.ticks_diff(time.ticks_us(), t0)
        words[i] = mem32[SIO_GPIO_IN]
        nxt = time.ticks_add(nxt, step_us)
    return stamps, words


def _index_at(stamps, t):
    lo, hi = 0, len(stamps) - 1
    if t < stamps[0] or t > stamps[hi]:
        return -1
    while lo < hi:                              # last sample at or before t
        mid = (lo + hi + 1) // 2
        if stamps[mid] <= t:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _byte_at(level, t0, bit):
    """An 8N1 byte whose start bit begins at t0, or None (start not 0, stop not 1)."""
    if level(t0 + bit // 2) != 0 or level(t0 + 9 * bit + bit // 2) != 1:
        return None
    b = 0
    for j in range(8):
        v = level(t0 + (j + 1) * bit + bit // 2)
        if v is None:
            return None
        b |= v << j
    return b


def decode_gpio(stamps, words, g, bit=BIT_US):
    """('signal', index) | ('high',) | ('low',) | ('garbled',) for one GPIO."""
    vals = [(w >> g) & 1 for w in words]
    if all(vals):
        return ("high",)
    if not any(vals):
        return ("low",)

    def level(t):
        i = _index_at(stamps, t)
        return None if i < 0 else vals[i]

    found = {}
    for i in range(1, len(vals)):
        if vals[i - 1] == 1 and vals[i] == 0:   # a falling edge: try a frame start here
            t0 = stamps[i]
            b1 = _byte_at(level, t0, bit)
            b2 = _byte_at(level, t0 + 10 * bit, bit)
            if b1 is None or b2 is None or b2 != (~b1 & 0xFF) or (b1 & 0xE0) != 0x80:
                continue
            k = b1 & 0x1F
            if k < len(SIGNALS):
                found[k] = found.get(k, 0) + 1
    if len(found) == 1:
        return ("signal", list(found)[0])
    return ("garbled",)


def decode(stamps, words, bit=BIT_US):
    """{gpio: result} for every header GPIO, and a list of problems (empty if wired right)."""
    res = {g: decode_gpio(stamps, words, g, bit) for g in HEADER_GPIOS}
    problems = []
    where = {}
    for g, r in res.items():
        if r[0] == "signal":
            where.setdefault(r[1], []).append(g)
    if all(r[0] == "high" for r in res.values()):
        return res, ["no GPIO toggles: is pinid.fs loaded, the Dock powered, and GND connected?"]
    for k, name in enumerate(SIGNALS):
        e = EXPECT[k]
        got = where.get(k, [])
        if got == [e]:
            continue
        if not got:
            r = res[e]
            why = {"high": "GP%d stays high: wire open (no signal reaches it)" % e,
                   "low": "GP%d stays low: shorted to GND, or a Pico pin driving low" % e,
                   "garbled": "GP%d toggles but decodes to nothing: bridged to another wire" % e}
            problems.append("%s (expected on GP%d) not found; %s" % (
                name, e, why.get(r[0], "GP%d carries %s instead" % (e, SIGNALS[r[1]]) if r[0] == "signal" else "")))
        else:
            problems.append("%s arrives on GP%s, expected GP%d" % (
                name, "/GP".join(str(x) for x in got), e))
    return res, problems


def run(n=1000, step_us=100):
    stamps, words = capture(n, step_us)
    span = stamps[-1] - stamps[0]
    if span > n * step_us * 1.2:
        print("warning: sampling ran slow (%d us for %d samples)" % (span, n))
    res, problems = decode(stamps, words)
    print("GPIO  signal")
    for g in HEADER_GPIOS:
        r = res[g]
        print("GP%-3d %s" % (g, SIGNALS[r[1]] if r[0] == "signal" else "(" + r[0] + ")"))
    if problems:
        print("WIRING: %d PROBLEM(S)" % len(problems))
        for p in problems:
            print("  -", p)
    else:
        print("WIRING: PASS, all 26 signals on the expected GPIOs")
    return not problems
