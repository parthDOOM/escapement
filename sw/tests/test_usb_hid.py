"""firmware/usb_hid.asm on the ISS against an independent USB host (escmodels.usb).

The host is a reactive board model: it drives packets, checks every device packet (EOP,
stuffing, CRC, turnaround), and flags the device driving the bus when it was not asked
to. Every test runs to completion with no host error and no fault, and every reply the
device sends leaves exactly TURN ticks after the end of the host's packet.
"""

from __future__ import annotations

import os
import random
from pathlib import Path

import pytest

from escasm import assemble_file
from esciss import Machine
from escmodels import usb
from escmodels.usb import (SE0, UsbError, UsbHost, bus_reset, control, handshake, in_txn,
                           listen, out_txn, send, setup_bytes, setup_txn, token)

# USB_HID_ASM: another source to test (the planted-bug campaign uses it)
FW = Path(os.environ.get("USB_HID_ASM")
          or Path(__file__).resolve().parents[2] / "firmware" / "usb_hid.asm")
TURN = 75              # host EOP SE0->J to the device's first K: 4.69 bit times (USB: 2 to 6.5)
ADDR = 5

# HID 1.11 appendix B.1: the boot keyboard report descriptor
BOOT_KEYBOARD = bytes.fromhex(
    "05010906a101050719e029e7150025017501950881029501750881019505750105081901290591"
    "02950175039101950675081500256505071900296581" "00c0")


@pytest.fixture(scope="module")
def image():
    return assemble_file(str(FW))


class Run:
    """Run `script(host, run)` against the firmware. `reports` (8 bytes each) are pushed
    into context 1's mailbox as it has room, two words per report."""

    def __init__(self, image, script, *, rate=None, gap=2, reports=(), limit=4_000_000):
        self.host = UsbHost(rate=rate, gap=gap)
        self.m = Machine(image, board=self.host)
        self.words = [int.from_bytes(r[i:i + 4], "little") for r in reports for i in (0, 4)]
        self.out: dict = {}
        self.host.start(lambda h: script(h, self))
        while not self.host.done and self.m.now < limit and not self.m.ff.valid:
            while self.words and len(self.m.ctx[1].rx) < 2:
                self.m.host_rx_push(1, self.words.pop(0))
                self.m.run(1, stop_when_idle=False)
            self.m.run(256, stop_when_idle=False)

    def check(self):
        assert self.m.ff.valid == 0, self.m.final_state()["first_fault"]
        assert self.host.done, f"script unfinished at tick {self.m.now}"
        assert self.host.errors == [], self.host.errors[:5]
        assert self.host.replies, "the device never answered"
        tas = sorted({r.turnaround for r in self.host.replies})
        assert tas == [TURN], tas
        return self


def run(image, script, **kw) -> Run:
    return Run(image, script, **kw).check()


# ----------------------------------------------------------------------------- host scripts


def get_desc(kind: int, index: int = 0, lang: int = 0, length: int = 255) -> bytes:
    return setup_bytes(0x80, 6, (kind << 8) | index, lang, length)


def keep_alive(h: UsbHost):
    """A low-speed keep-alive: an EOP (SE0 for two bit times, then J), after the gap."""
    yield ("idle", 2 * h.p)
    for u in range(2 * h.p):
        h.drive[h._t + 1 + u] = SE0
    yield ("idle", 3 * h.p + 1)


def pulse(h: UsbHost, state: str, ticks: int, after: int):
    """`ticks` of `state`, then `after` ticks of idle bus. Callers leave the gap first."""
    for u in range(ticks):
        h.drive[h._t + 1 + u] = state
    yield ("idle", ticks + after)


def corrupt(bits: list[int], i: int) -> list[int]:
    out = list(bits)
    out[i] ^= 1
    return out


def enumerate_device(h: UsbHost, out: dict, addr: int = ADDR, reset: int = 1000,
                     recovery: int = 3000):
    """What Linux does for a low-speed HID keyboard, with short resets by default."""
    yield from bus_reset(h, reset)
    out["dev64"] = yield from control(h, 0, get_desc(1, length=64))
    yield from bus_reset(h, reset)
    out["setaddr"] = yield from control(h, 0, setup_bytes(0, 5, addr, 0, 0))
    yield ("idle", recovery)
    out["dev"] = yield from control(h, addr, get_desc(1, length=18))
    out["cfg9"] = yield from control(h, addr, get_desc(2, length=9))
    total = out["cfg9"][2] | (out["cfg9"][3] << 8)
    out["cfg"] = yield from control(h, addr, get_desc(2, length=total))
    out["str0"] = yield from control(h, addr, get_desc(3, 0))
    out["str2"] = yield from control(h, addr, get_desc(3, 2, 0x0409))
    out["qual"] = yield from control(h, addr, get_desc(6, length=10))
    out["setcfg"] = yield from control(h, addr, setup_bytes(0, 9, 1, 0, 0))
    out["setidle"] = yield from control(h, addr, setup_bytes(0x21, 0x0A, 0, 0, 0))
    out["report"] = yield from control(h, addr, setup_bytes(0x81, 6, 0x2200, 0, 63 + 64))
    out["leds"] = yield from control(h, addr, setup_bytes(0x21, 9, 0x0200, 0, 1), out=b"\x01")


def check_descriptors(out: dict) -> None:
    dev = out["dev"]
    assert out["dev64"] == dev and len(dev) == 18 and dev[:2] == bytes((18, 1))
    assert int.from_bytes(dev[2:4], "little") == 0x0110                # USB 1.1
    assert dev[4:8] == bytes((0, 0, 0, 8))                             # class per interface; EP0 8
    assert int.from_bytes(dev[8:10], "little") == 0x1209                # pid.codes test VID:PID
    assert int.from_bytes(dev[10:12], "little") == 0x0001
    assert dev[14:18] == bytes((0, 2, 0, 1))                           # product string 2, 1 config
    cfg = out["cfg"]
    assert out["cfg9"] == cfg[:9] and len(cfg) == cfg[2] | (cfg[3] << 8) == 34
    assert cfg[4:6] == bytes((1, 1))                                   # 1 interface, value 1
    descs, i = [], 0
    while i < len(cfg):
        descs.append(cfg[i:i + cfg[i]])
        i += cfg[i]
    assert i == len(cfg) and [d[1] for d in descs] == [2, 4, 0x21, 5]
    iface, hid, ep = descs[1], descs[2], descs[3]
    assert iface[4:8] == bytes((1, 3, 1, 1))                          # 1 endpoint, HID boot keyboard
    assert int.from_bytes(hid[2:4], "little") == 0x0111 and hid[6] == 0x22
    assert int.from_bytes(hid[7:9], "little") == len(out["report"])
    assert ep[2:7] == bytes((0x81, 3, 8, 0, 10))                       # IN 1, interrupt, 8 B, 10 ms
    assert out["report"] == BOOT_KEYBOARD
    assert out["str0"] == bytes((4, 3, 0x09, 0x04))
    s = out["str2"]
    assert s[0] == len(s) and s[1] == 3 and s[2:].decode("utf-16-le") == "Escapement"
    assert out["qual"] == "STALL"
    assert out["setaddr"] == out["setcfg"] == out["setidle"] == out["leds"] == b""


# ----------------------------------------------------------------------------- enumeration


def test_enumerates_with_exact_descriptors(image):
    def script(h, r):
        yield from enumerate_device(h, r.out)
    check_descriptors(run(image, script).out)


@pytest.mark.parametrize("rate", [16 * 0.985, 16 * 1.015, 16 * 0.95, 16 * 1.05])
def test_host_clock_offset(image, rate):
    """USB allows 1.5%; the receiver resynchronises on every transition, so 5% works too.
    The reply time stays exact: it counts from the end of the host's packet."""
    def script(h, r):
        yield from enumerate_device(h, r.out)
    check_descriptors(run(image, script, rate=rate).out)


@pytest.mark.parametrize("seed", range(4))
def test_random_host_gaps(image, seed):
    """Every host packet after a random gap of 2 (the minimum, USB 2.0 7.1.18.1) to 24 bit
    times, the host's ACK after the device's data included."""
    rng = random.Random(seed)

    def script(h, r):
        yield from enumerate_device(h, r.out)
    check_descriptors(run(image, script, gap=lambda: rng.choice((2, 2, 2, 3, 5, 8, 24))).out)


def test_keep_alives_between_every_transaction(image):
    def script(h, r):
        yield from keep_alive(h)
        yield from bus_reset(h, 1000)
        yield from keep_alive(h)
        yield from control(h, 0, setup_bytes(0, 5, ADDR, 0, 0))
        for _ in range(3):
            yield from keep_alive(h)
        yield ("idle", 3000)
        for kind, n in ((1, 18), (2, 34), (3, 4)):
            yield from keep_alive(h)
            r.out[kind] = yield from control(h, ADDR, get_desc(kind, length=n))
            yield from keep_alive(h)
    out = run(image, script).out
    assert len(out[1]) == 18 and len(out[2]) == 34 and out[3] == bytes((4, 3, 9, 4))


# ----------------------------------------------------------------------------- requests


def addressed(script):
    """Reset, SET_ADDRESS, then `script`."""
    def full(h, r):
        yield from bus_reset(h, 1000)
        yield from control(h, 0, setup_bytes(0, 5, ADDR, 0, 0))
        yield ("idle", 3000)
        yield from script(h, r)
    return full


@pytest.mark.parametrize("wlength", [1, 7, 8, 9, 16, 17, 18, 19, 64, 255, 0xFFFF])
def test_device_descriptor_any_wlength(image, wlength):
    def script(h, r):
        r.out["d"] = yield from control(h, ADDR, get_desc(1, length=wlength))
    d = run(image, addressed(script)).out["d"]
    assert len(d) == min(18, wlength) and d == bytes.fromhex(
        "120110010000000809120100040100020001")[:len(d)]


@pytest.mark.parametrize("wlength", [9, 33, 34, 35, 256])
def test_configuration_descriptor_any_wlength(image, wlength):
    def script(h, r):
        r.out["c"] = yield from control(h, ADDR, get_desc(2, length=wlength))
    assert len(run(image, addressed(script)).out["c"]) == min(34, wlength)


def test_no_descriptor_is_a_multiple_of_eight_bytes(image):
    """A reply shorter than wLength ends with a short packet; one of 8k bytes would need a
    zero-length packet after it, which the firmware does not send."""
    for kind, idx, lang in ((1, 0, 0), (2, 0, 0), (3, 0, 0), (3, 2, 0x0409)):
        def script(h, r, kind=kind, idx=idx, lang=lang):
            r.out["x"] = yield from control(h, ADDR, get_desc(kind, idx, lang))
        assert len(run(image, addressed(script)).out["x"]) % 8 != 0
    rd = len(BOOT_KEYBOARD)
    assert rd % 8 != 0


def test_get_with_zero_wlength_has_only_a_status_stage(image):
    def script(h, r):
        r.out["d"] = yield from control(h, ADDR, get_desc(1, length=0))
        r.out["after"] = yield from control(h, ADDR, get_desc(1, length=18))
    out = run(image, addressed(script)).out
    assert out["d"] == b"" and len(out["after"]) == 18


@pytest.mark.parametrize("setup,reply", [
    (setup_bytes(0x80, 0, 0, 0, 2), b"\0\0"),             # GET_STATUS device
    (setup_bytes(0x81, 0, 0, 0, 2), b"\0\0"),             # interface
    (setup_bytes(0x82, 0, 0, 0x81, 2), b"\0\0"),          # endpoint
    (setup_bytes(0x80, 8, 0, 0, 1), b"\x01"),             # GET_CONFIGURATION
    (setup_bytes(0x80, 6, 0x0301, 0, 255), "STALL"),      # no string 1
    (setup_bytes(0x80, 6, 0x0303, 0x0409, 255), "STALL"),
    (setup_bytes(0x80, 6, 0x0600, 0, 10), "STALL"),       # device qualifier: not high speed
    (setup_bytes(0x80, 6, 0x0700, 0, 9), "STALL"),        # other speed configuration
    (setup_bytes(0xA1, 1, 0x0100, 0, 8), "STALL"),        # GET_REPORT
    (setup_bytes(0x00, 3, 1, 0, 0), b""),                 # SET_FEATURE: accepted
    (setup_bytes(0x21, 0x0B, 0, 0, 0), b""),              # SET_PROTOCOL (boot)
])
def test_standard_and_class_requests(image, setup, reply):
    def script(h, r):
        r.out["x"] = yield from control(h, ADDR, setup)
        r.out["then"] = yield from control(h, ADDR, get_desc(3, 0))     # a STALL clears
    out = run(image, addressed(script)).out
    assert out["x"] == reply and out["then"] == bytes((4, 3, 9, 4))


# ----------------------------------------------------------------------------- errors on the wire


def test_corrupt_setup_data_gets_no_handshake(image):
    good = get_desc(1, length=18)

    def script(h, r):
        for bad in (usb.data_packet("DATA0", good)[:-1] + [1 - usb.data_packet("DATA0", good)[-1]],
                    usb.data_packet("DATA0", good[:7]), usb.data_packet("DATA0", good + b"\0"),
                    corrupt(usb.data_packet("DATA0", good), 30)):
            yield from send(h, token("SETUP", ADDR, 0), "SETUP")
            yield from send(h, bad, "DATA0*")
            r.out.setdefault("replies", []).append((yield from listen(h)))
        r.out["d"] = yield from control(h, ADDR, good)
    out = run(image, addressed(script)).out
    assert out["replies"] == [None] * 4 and len(out["d"]) == 18


def test_corrupt_out_data_gets_no_handshake(image):
    """A status stage or an LED report whose CRC is wrong is not acknowledged; the host
    repeats it. (The DATA PID is not checked: the CRC does not cover it, and neither
    stage means anything different under the other PID.)"""
    def script(h, r):
        replies = []
        yield from setup_txn(h, ADDR, get_desc(3, 0))
        p = yield from in_txn(h, ADDR, 0)
        assert p.packet.name == "DATA1"
        for bits in (corrupt(usb.data_packet("DATA1", b""), 20),
                     corrupt(usb.data_packet("DATA1", b""), 31)):
            yield from send(h, token("OUT", ADDR, 0), "OUT")
            yield from send(h, bits, "DATA1*")
            replies.append((yield from listen(h)))
        st = yield from out_txn(h, ADDR, 0, b"", "DATA1")
        replies.append(st.packet.name)
        yield from setup_txn(h, ADDR, setup_bytes(0x21, 9, 0x0200, 0, 1))
        yield from send(h, token("OUT", ADDR, 0), "OUT")
        yield from send(h, corrupt(usb.data_packet("DATA1", b"\x02"), 18), "DATA1*")
        replies.append((yield from listen(h)))
        st = yield from out_txn(h, ADDR, 0, b"\x02", "DATA1")
        replies.append(st.packet.name)
        r.out["replies"] = replies
        r.out["zlp"] = yield from in_txn(h, ADDR, 0)
    out = run(image, addressed(script)).out
    assert out["replies"] == [None, None, "ACK", None, "ACK"]
    assert out["zlp"].packet.name == "DATA1" and out["zlp"].packet.body == b"\0\0"


def test_corrupt_and_foreign_tokens_get_no_answer(image):
    def script(h, r):
        for bits in (corrupt(token("IN", ADDR, 0), 20),            # bad CRC-5
                     corrupt(token("IN", ADDR, 0), 13),            # bad PID check
                     token("IN", ADDR + 1, 0), token("IN", 0, 0),  # other devices
                     token("IN", ADDR, 2), token("OUT", ADDR, 1),  # endpoints we do not have
                     token("SETUP", ADDR, 1), token("SOF", 0x23, 0xB),
                     handshake("ACK"), handshake("NAK")):
            yield from send(h, bits, "x")
            r.out.setdefault("replies", []).append((yield from listen(h)))
        r.out["d"] = yield from control(h, ADDR, get_desc(1, length=18))
    out = run(image, addressed(script)).out
    assert out["replies"] == [None] * 10 and len(out["d"]) == 18


def test_lost_host_ack_in_a_data_stage(image):
    """The host's ACK is corrupted: the device sends the packet again with the same PID; the
    host, which has the data, discards the copy and acknowledges it (USB 2.0 8.6.4)."""
    def script(h, r):
        r_ = yield from setup_txn(h, ADDR, get_desc(1, length=18))
        assert r_.packet.name == "ACK"
        first = yield from in_txn(h, ADDR, 0, ack=False)
        yield from send(h, corrupt(handshake("ACK"), 12), "ACK*")
        again = None
        for _ in range(4):
            again = yield from in_txn(h, ADDR, 0)
            if again is not None:
                break
        assert (first.packet.name, again.packet.name) == ("DATA1", "DATA1")
        assert first.packet.body == again.packet.body
        nxt = yield from in_txn(h, ADDR, 0)
        assert nxt.packet.name == "DATA0"
        r.out["d"] = first.packet.body[:-2] + nxt.packet.body[:-2]
        last = yield from in_txn(h, ADDR, 0)
        r.out["d"] += last.packet.body[:-2]
        st = yield from out_txn(h, ADDR, 0, b"", "DATA1")
        assert st.packet.name == "ACK"
    assert len(run(image, addressed(script)).out["d"]) == 18


def test_unacknowledged_data_is_sent_again(image):
    """The host saw a bad packet and sends no ACK at all: the same packet comes again."""
    def script(h, r):
        yield from setup_txn(h, ADDR, get_desc(2, length=34))
        seen = []
        while len(seen) < 9:
            p = yield from in_txn(h, ADDR, 0, ack=len(seen) % 2 == 1)
            if p is not None and p.packet.name != "NAK":
                seen.append((p.packet.name, p.packet.body))
        assert [n for n, _ in seen] == ["DATA1", "DATA1", "DATA0", "DATA0", "DATA1", "DATA1",
                                        "DATA0", "DATA0", "DATA1"]
        assert all(seen[i] == seen[i + 1] for i in (0, 2, 4, 6))
        r.out["c"] = b"".join(b[:-2] for _, b in seen[1::2]) + seen[8][1][:-2]
        yield from out_txn(h, ADDR, 0, b"", "DATA1")
    assert len(run(image, addressed(script)).out["c"]) == 34


def test_set_address_waits_for_its_status_stage_ack(image):
    """The new address applies only once the status stage completes (9.4.6): with the
    host's ACK lost, the device stays at address 0 and answers the repeated IN."""
    def script(h, r):
        yield from bus_reset(h, 1000)
        yield from setup_txn(h, 0, setup_bytes(0, 5, ADDR, 0, 0))
        z = yield from in_txn(h, 0, 0, ack=False)
        assert z.packet.name == "DATA1" and z.packet.body == b"\0\0"
        yield ("idle", 400)
        z = yield from in_txn(h, 0, 0)
        assert z.packet.name == "DATA1"
        yield ("idle", 3000)
        old = yield from in_txn(h, 0, 0)
        r.out["old"] = old
        r.out["d"] = yield from control(h, ADDR, get_desc(1, length=18))
    out = run(image, script).out
    assert out["old"] is None and len(out["d"]) == 18


def test_a_new_setup_abandons_a_data_stage(image):
    def script(h, r):
        yield from setup_txn(h, ADDR, get_desc(2, length=34))
        p = yield from in_txn(h, ADDR, 0)
        assert p.packet.name == "DATA1"
        r.out["d"] = yield from control(h, ADDR, get_desc(1, length=18))
    assert len(run(image, addressed(script)).out["d"]) == 18


def test_status_stage_before_the_data_ends(image):
    """Windows asks for 64 bytes of the device descriptor, reads one packet, ends the
    transfer with the status stage, and resets."""
    def script(h, r):
        yield from bus_reset(h, 1000)
        yield from setup_txn(h, 0, get_desc(1, length=64))
        p = yield from in_txn(h, 0, 0)
        st = yield from out_txn(h, 0, 0, b"", "DATA1")
        r.out["first"], r.out["st"] = p.packet.body[:-2], st.packet.name
        yield from enumerate_device(h, r.out)
    out = run(image, script).out
    assert out["st"] == "ACK" and out["first"] == out["dev"][:8]
    check_descriptors(out)


# ----------------------------------------------------------------------------- bus reset


@pytest.mark.parametrize("where", ["after_setup", "mid_data", "before_status",
                                   "after_address", "configured"])
def test_bus_reset_anywhere_returns_to_address_zero(image, where):
    def script(h, r):
        yield from bus_reset(h, 1000)
        if where == "after_setup":
            yield from setup_txn(h, 0, get_desc(1, length=18))
        elif where == "mid_data":
            yield from setup_txn(h, 0, get_desc(1, length=18))
            yield from in_txn(h, 0, 0)
        elif where == "before_status":
            yield from setup_txn(h, 0, setup_bytes(0, 5, 9, 0, 0))   # SET_ADDRESS 9, no status
        else:
            yield from control(h, 0, setup_bytes(0, 5, 9, 0, 0))
            yield ("idle", 3000)
            if where == "configured":
                yield from control(h, 9, setup_bytes(0, 9, 1, 0, 0))
        yield ("idle", 200)
        yield from bus_reset(h, 1000)
        r.out["old"] = yield from in_txn(h, 9, 0)
        yield from enumerate_device(h, r.out)
    out = run(image, script).out
    assert out["old"] is None
    check_descriptors(out)


def test_full_timing_enumeration(image):
    """Real timing: 10 ms resets (USB 2.0 7.1.7.5), 2 ms SET_ADDRESS recovery (9.2.6.3),
    keep-alives every millisecond while idle (7.1.7.6), and the report polled every 10 ms."""
    ms = 24_000

    def idle_ms(h, n):
        for _ in range(n):
            yield from keep_alive(h)
            yield ("idle", ms - 4 * h.p)

    def script(h, r):
        yield from idle_ms(h, 2)
        yield from bus_reset(h, 10 * ms)
        yield from idle_ms(h, 1)
        r.out["dev64"] = yield from control(h, 0, get_desc(1, length=64))
        yield from bus_reset(h, 10 * ms)
        yield from idle_ms(h, 1)
        r.out["setaddr"] = yield from control(h, 0, setup_bytes(0, 5, ADDR, 0, 0))
        yield from idle_ms(h, 2)
        rest = {}
        yield from enumerate_tail(h, rest)
        r.out.update(rest)
        reps = []
        for _ in range(4):
            yield from idle_ms(h, 10)
            p = yield from in_txn(h, ADDR, 1)
            reps.append(p.packet.name if p else None)
        r.out["ep1"] = reps

    out = run(image, script, reports=[bytes((2, 0, 0x0B, 0, 0, 0, 0, 0)), bytes(8)],
              limit=2_000_000).out
    check_descriptors(out)
    assert out["ep1"] == ["DATA0", "DATA1", "NAK", "NAK"]


def enumerate_tail(h, out, addr=ADDR):
    out["dev"] = yield from control(h, addr, get_desc(1, length=18))
    out["cfg9"] = yield from control(h, addr, get_desc(2, length=9))
    out["cfg"] = yield from control(h, addr, get_desc(2, length=34))
    out["str0"] = yield from control(h, addr, get_desc(3, 0))
    out["str2"] = yield from control(h, addr, get_desc(3, 2, 0x0409))
    out["qual"] = yield from control(h, addr, get_desc(6, length=10))
    out["setcfg"] = yield from control(h, addr, setup_bytes(0, 9, 1, 0, 0))
    out["setidle"] = yield from control(h, addr, setup_bytes(0x21, 0x0A, 0, 0, 0))
    out["report"] = yield from control(h, addr, setup_bytes(0x81, 6, 0x2200, 0, 63 + 64))
    out["leds"] = yield from control(h, addr, setup_bytes(0x21, 9, 0x0200, 0, 1), out=b"\x01")


# ----------------------------------------------------------------------------- key reports


def poll_reports(h, n, rng, got, lose=0.0, corrupt_ack=0.0):
    """Poll endpoint 1 until n reports have arrived, as a host does: a report with the
    expected DATA PID is new, one with the other PID is a repeat (discarded, ACKed)."""
    want = "DATA0"
    while len(got) < n:
        roll = rng.random()
        p = yield from in_txn(h, ADDR, 1, ack=roll >= lose + corrupt_ack)
        if p is None:
            continue
        if p.packet.name == "NAK":
            yield ("idle", rng.randrange(50, 1500))
            continue
        assert p.packet.name in ("DATA0", "DATA1") and len(p.packet.body) == 10
        if roll < lose:
            continue                                     # the host saw an error: not taken
        if roll < lose + corrupt_ack:
            yield from send(h, corrupt(handshake("ACK"), 12), "ACK*")
        if p.packet.name == want:
            got.append(p.packet.body[:-2])
            want = "DATA1" if want == "DATA0" else "DATA0"


@pytest.mark.parametrize("seed", range(3))
def test_long_report_sequence_with_lost_handshakes(image, seed):
    rng = random.Random(100 + seed)
    reports = [bytes((rng.randrange(256), 0, *(rng.randrange(256) for _ in range(6))))
               for _ in range(40)]
    got: list[bytes] = []

    def script(h, r):
        yield from enumerate_device(h, r.out)
        yield from poll_reports(h, len(reports), rng, got, lose=0.15, corrupt_ack=0.15)
        p = yield from in_txn(h, ADDR, 1)
        if p.packet.name != "NAK":                    # the last ACK was lost: one repeat
            assert p.packet.body[:-2] == got[-1] and p.packet.name == ("DATA0", "DATA1")[
                len(got) % 2 == 0]
            p = yield from in_txn(h, ADDR, 1)
        for _ in range(3):
            assert p.packet.name == "NAK"
            p = yield from in_txn(h, ADDR, 1)
    run(image, script, reports=reports)
    assert got == reports


def test_reset_with_a_report_staged_restarts_at_data0(image):
    """Report 0 goes out as DATA0, so report 1 is staged as DATA1. A bus reset restarts
    the endpoint: report 1 must then go out as DATA0, or the host would drop it."""
    reports = [bytes((i, 0, 4 + i, 0, 0, 0, 0, 0)) for i in range(4)]
    got: list[bytes] = []
    rng = random.Random(1)

    def script(h, r):
        yield from enumerate_device(h, r.out)
        yield from poll_reports(h, 1, rng, got)            # report 0 as DATA0
        yield ("idle", 500)                                 # report 1 staged as DATA1
        yield from bus_reset(h, 1000)
        yield from enumerate_device(h, r.out)
        yield from poll_reports(h, 4, rng, got)             # DATA0 again first
    run(image, script, reports=reports)
    assert got == reports


@pytest.mark.parametrize("restart", [setup_bytes(0, 9, 1, 0, 0),          # SET_CONFIGURATION
                                     setup_bytes(0x02, 1, 0, 0x81, 0)])    # CLEAR_FEATURE(HALT)
def test_endpoint_1_restarts_at_data0(image, restart):
    """USB 2.0 9.4.5: both requests reset the endpoint's data toggle."""
    reports = [bytes((i, 0, 4 + i, 0, 0, 0, 0, 0)) for i in range(3)]
    got: list[bytes] = []
    rng = random.Random(2)

    def script(h, r):
        yield from enumerate_device(h, r.out)
        yield from poll_reports(h, 1, rng, got)
        yield ("idle", 500)
        yield from control(h, ADDR, restart)
        yield from poll_reports(h, 3, rng, got)
    run(image, script, reports=reports)
    assert got == reports


# ----------------------------------------------------------------------------- noise


@pytest.mark.parametrize("seed", range(3))
def test_glitches_between_transactions_do_no_harm(image, seed):
    """Short K and SE0 pulses on an idle bus (shorter than a reset): never a fault, never
    an answer, and the next transaction works. The host leaves 40 bit times after each."""
    rng = random.Random(seed)

    def noise(h):
        yield ("idle", 2 * h.p)
        for _ in range(rng.randrange(1, 4)):
            yield from pulse(h, rng.choice((usb.K, SE0)), rng.randrange(1, 50),
                             rng.randrange(1, 40))
        yield ("idle", 40 * h.p)

    def script(h, r):
        yield from bus_reset(h, 1000)
        yield from noise(h)
        yield from control(h, 0, setup_bytes(0, 5, ADDR, 0, 0))
        yield ("idle", 3000)
        for kind, n in ((1, 18), (2, 34), (3, 4), (2, 9)):
            yield from noise(h)
            r.out[(kind, n)] = yield from control(h, ADDR, get_desc(kind, length=n))
    out = run(image, script).out
    assert [len(v) for v in out.values()] == [18, 34, 4, 9]


def fuzz_packet(rng: random.Random) -> list[int]:
    """A packet a confused or hostile host might send: mostly ones the device answers."""
    k = rng.randrange(8)
    if k < 3:
        return token("IN", 0, rng.choice((0, 0, 1)))
    if k == 3:
        return token(rng.choice(("SETUP", "OUT")), 0, 0)
    if k == 4:
        return usb.data_packet(rng.choice(("DATA0", "DATA1")),
                               rng.choice((get_desc(1, length=18), get_desc(2, length=34),
                                           setup_bytes(0, 5, 0, 0, 0), b"\x01")))
    if k == 5:
        return handshake(rng.choice(("ACK", "NAK")))
    return corrupt(token("IN", 0, 0), rng.randrange(8, 32))


@pytest.mark.parametrize("seed", range(4))
def test_hostile_traffic_never_faults_the_device(image, seed):
    """Packets at random moments, gaps down to zero, over the device's own replies and its
    busy stretches. The device must never fault (a reply that would miss its slot is
    dropped, v1.4), and once the bus is sane again it must enumerate normally."""
    rng = random.Random(1000 + seed)
    drops = []

    def script(h, r):
        yield from bus_reset(h, 1000)
        for _ in range(60):
            yield ("idle", rng.randrange(1, 40 * h.p))
            yield ("send", usb.line(fuzz_packet(rng)))
        yield ("idle", 6000)
        h.errors.clear()
        h.replies.clear()
        yield from enumerate_device(h, r.out)
        r.out["drops"] = drops

    rr = Run(image, script, limit=3_000_000)
    drops.extend(c["t"] for c in rr.m.commits
                 if c["insn"] >> 24 == 0x2D and c["outcome"] == "C" and c["post"]["z"] == 0)
    rr.check()
    check_descriptors(rr.out)


def test_host_errors_are_reported(image):
    """The harness itself: a host that expects a reply that never comes raises."""
    def script(h, r):
        yield from control(h, 7, get_desc(1, length=18))
    with pytest.raises(UsbError):
        Run(image, addressed(script))
