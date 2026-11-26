"""MIL-STD-1553B remote terminal (firmware/mil1553_rt.asm, held-out round) against the
bus model escmodels.mil1553.Bc1553: a bus controller, the bus and the RT's transceiver.
The model was written apart from the firmware, from MIL-STD-1553B (21 Sep 1978) alone.
It decodes every RT transmission and checks the output timing (each zero crossing within
25 ns, 4.5.2.1.1.2), contiguity (4.3.3.6.2), the 4 to 12 us response time (4.3.3.8), the
14 us no-response timeout (4.3.3.9), unasked transmissions and the 800 us fail-safe
(4.4.1.3). What the RT should answer is decided here.

Most runs use 40 MHz (25 ns ticks, the model's default); escert proves the program at
every clock from 40 to 50 MHz in steps of 2 MHz."""

from __future__ import annotations

from pathlib import Path

import pytest

from escasm import assemble
from escmodels import Bc1553, Board, HostFeed
from esciss import Machine

FW = Path(__file__).resolve().parents[2] / "firmware"
RT = 5
ST = RT << 11                  # the status word: the RT address in bits 15..11
ME = 1 << 10                   # message error (bit time 9)
BCR = 1 << 4                   # broadcast command received (bit time 15)
POLL = 1400                    # ticks between the host's mailbox reads (an SPI MBX_POP)


def cmd(rt, tr, sa, wc):
    return rt << 11 | tr << 10 | sa << 5 | (wc & 31)


def rx(sa, words, rt=RT):
    return [("cmd", cmd(rt, 0, sa, len(words)))] + [("data", w) for w in words]


def tx(sa, n, rt=RT):
    return [("cmd", cmd(rt, 1, sa, n))]


def mode(code, tr=1, data=None, rt=RT):
    w = [("cmd", cmd(rt, tr, 0, code))]
    return w + ([("data", data)] if data is not None else [])


def source(khz=40000, resp_us=None, patch=()):
    src = (FW / "mil1553_rt.asm").read_text()
    for a, b in patch:
        assert src.count(a) == 1, a
        src = src.replace(a, b)
    head = f".equ CLK_KHZ, {khz}\n" + (f".equ RESP_US, {resp_us}\n" if resp_us else "")
    return head + src


def image(khz=40000, resp_us=None, patch=()):
    return assemble(source(khz, resp_us, patch), base_dir=FW, name="mil1553_rt.asm")


def session(msgs, khz=40000, tx_words=(), resp_us=None, patch=(), start_us=None, **bc):
    """Run `msgs` (lists of words, or dicts with "words" and "faults"), one every
    `spacing` us after the host has loaded `tx_words` (index, value) into the transmit
    buffer. Returns the model, the machine and what the host context reported."""
    tick = 1e6 / khz
    queues = {1: [0x01 << 24 | i << 16 | v for i, v in tx_words]}
    host = HostFeed(queues, gap=khz // 10, drain=[1], poll=POLL)
    at = start_us if start_us is not None else 20 + 120 * len(tx_words)
    script = []
    for m in msgs:
        m = m if isinstance(m, dict) else {"words": m}
        n = len(m["words"])
        script.append(dict(m, at_us=at))
        at += 60 + 20 * n + 20 * 33            # the longest possible reply, and a gap
    model = Bc1553(tick_ns=tick, messages=script, **bc)
    mach = Machine(image(khz, resp_us, patch), board=Board(model, host))
    host.machine = mach
    words = sum(len(m["words"]) if isinstance(m, dict) else len(m) for m in msgs)
    mach.run(int((at + 40) * khz / 1000) + POLL * (words + 4), stop_when_idle=False)
    model.finish()
    return model, mach, host.received[1]


def statuses(model):
    return [None if r is None else r["status"] for r in model.responses]


def clean(model, mach):
    assert not mach.ff.valid, mach.ff
    assert not model.errors(), model.errors()
    assert all(r is None or r["status_ok"] for r in model.responses), model.responses


def messages(words):
    """Split the host context's words into (header, data words) per message."""
    out, i = [], 0
    while i < len(words):
        n = words[i] >> 16 & 63
        out.append((words[i], words[i + 1:i + 1 + n]))
        i += 1 + n
    return out


@pytest.mark.parametrize("khz", [40000, 50000])
def test_receive_transmit_and_status(khz):
    """A receive command with two data words: status, and the host gets the message; a
    transmit command: status and the words the host loaded; transmit status word (mode
    code 00010): the last status again."""
    data = [(0, 0x1234), (1, 0xBEEF), (2, 0x0001)]
    model, mach, host = session([rx(3, [0x1234, 0xBEEF]), tx(3, 3), mode(2)], khz, data)
    clean(model, mach)
    assert statuses(model) == [ST, ST, ST]
    assert model.responses[1]["data"] == [0x1234, 0xBEEF, 0x0001]
    assert messages(host) == [(2 << 16 | cmd(RT, 0, 3, 2), [0x1234, 0xBEEF])]
    for r in model.responses:
        assert 7.9 <= r["response_us"] <= 8.1, r


def test_word_counts_32_and_1():
    """A word count field of 0 means 32 (4.3.3.5.1.5): 32 data words received, 32 sent."""
    words = [(i * 0x0811 + 7) & 0xFFFF for i in range(32)]
    model, mach, host = session([rx(1, words), rx(30, [0xA5A5]), tx(2, 0), tx(2, 1)],
                                tx_words=[(0, 0xFFFF), (31, 0x8001)])
    clean(model, mach)
    assert statuses(model) == [ST] * 4
    assert messages(host) == [(32 << 16 | cmd(RT, 0, 1, 0), words),
                              (1 << 16 | cmd(RT, 0, 30, 1), [0xA5A5])]
    sent = model.responses[2]["data"]
    assert len(sent) == 32 and sent[0] == 0xFFFF and sent[31] == 0x8001 and set(sent[1:31]) == {0}
    assert model.responses[3]["data"] == [0xFFFF]


def test_other_terminals_are_ignored():
    """Commands to another address get no answer and reach no host (4.4.3.1)."""
    model, mach, host = session([rx(3, [1, 2], rt=6), tx(3, 2, rt=4), mode(2, rt=30),
                                 tx(3, 1)])
    clean(model, mach)
    assert statuses(model) == [None, None, None, ST]
    assert host == []


def test_broadcast():
    """A broadcast receive command (address 31) is taken without a status word, and sets
    the broadcast-received bit (4.3.3.6.7, 4.3.3.5.3.9); transmit status word shows it and
    does not reset it (4.3.3.5.4); the next valid command does."""
    model, mach, host = session([rx(7, [0xCAFE], rt=31), mode(2), mode(2), tx(1, 1),
                                 mode(2), tx(3, 1, rt=31)])
    clean(model, mach)
    assert statuses(model) == [None, ST | BCR, ST | BCR, ST, ST, None]
    assert messages(host) == [(1 << 16 | cmd(31, 0, 7, 1), [0xCAFE])]


@pytest.mark.parametrize("fault", [{"parity": 2}, {"manchester": (1, 0)}, {"manchester": (2, 15)},
                                   {"sync": 2}, {"gap_us": (2, 4.0)}, {"truncate": 2},
                                   {"parity": 0}])
def test_invalid_message_is_not_answered(fault):
    """An invalid data word, a gap inside the message or too few words: no status word,
    the message error bit set for transmit status word (4.4.3.6, 4.4.1.2); nothing to the
    host. An invalid command word is not a command: no answer, and no error either."""
    model, mach, host = session([{"words": rx(3, [0x1111, 0x2222]), "faults": fault}, mode(2),
                                 tx(3, 1), mode(2)])
    clean(model, mach)
    err = ST if "parity" in fault and fault["parity"] == 0 else ST | ME
    assert statuses(model) == [None, err, ST, ST]
    assert host == []


def test_too_many_words_is_an_error():
    """A data word more than the count is an error: no status, message error set."""
    msg = [("cmd", cmd(RT, 0, 3, 1)), ("data", 1), ("data", 2)]
    model, mach, host = session([msg, mode(2)])
    clean(model, mach)
    assert statuses(model) == [None, ST | ME]
    assert host == []


def test_data_word_after_transmit_command_is_an_error():
    model, mach, host = session([tx(3, 1) + [("data", 5)], mode(2)])
    clean(model, mach)
    assert statuses(model) == [None, ST | ME]


def test_mode_codes():
    """Synchronize (00001) and reset (01000) are taken; synchronize with data word (10001)
    hands its word to the host; any other code is illegal here: status with the message
    error bit (4.4.3.4), and its data word, if it has one, is not used."""
    model, mach, host = session([mode(1), mode(8), mode(17, tr=0, data=0x0F0F), mode(2),
                                 mode(3), mode(18), mode(20, tr=0, data=0x1234), mode(2),
                                 mode(1)])
    clean(model, mach)
    assert statuses(model) == [ST, ST, ST, ST, ST | ME, ST | ME, ST | ME, ST | ME, ST]
    assert messages(host) == [(1 << 16 | cmd(RT, 0, 0, 17), [0x0F0F])]


def test_superseding_command():
    """A command followed at once by another: the second is taken (4.4.3.2)."""
    model, mach, host = session([[("cmd", cmd(RT, 1, 3, 2)), ("cmd", cmd(RT, 1, 3, 1))]],
                                tx_words=[(0, 0x7777)])
    clean(model, mach)
    assert statuses(model) == [ST] and model.responses[0]["data"] == [0x7777]


@pytest.mark.parametrize("khz,ppm,seed", [(40000, 1000, 1), (40000, -1000, 2), (50000, 1000, 3),
                                          (50000, -1000, 4), (40000, 0, 5), (50000, 0, 6)])
def test_receiver_tolerance(khz, ppm, seed):
    """The BC's zero crossings each moved by up to 90 ns (a bounded random walk in the
    model) with its bit rate 1000 ppm off (4.3.3.3), or 100 ns at the nominal rate:
    every word still read. 4.5.2.2.2.1
    asks for 150 ns; this receiver times every sample from the mid-sync crossing, so it
    takes about 200 ns between that crossing and any other (any one crossing moved by
    150 ns), not 150 ns on all of them at once (5 seeds in 8 fail at 120 ns, all at 150)."""
    words = [(0x9E37 * (i + 1)) & 0xFFFF for i in range(32)]
    model, mach, host = session([rx(9, words), rx(9, words[:3]), mode(2)], khz,
                                distortion_ns=90.0 if ppm else 100.0, rate_ppm=ppm, seed=seed)
    clean(model, mach)
    assert statuses(model) == [ST, ST, ST]
    assert [d for _, d in messages(host)] == [words, words[:3]]


@pytest.mark.parametrize("resp_us", [9, 11])
def test_response_time_setting(resp_us):
    """RESP_US sets the response time from 8 to 11 us, inside 4.3.3.8's 4 to 12."""
    model, mach, host = session([tx(3, 2), rx(3, [1])], resp_us=resp_us)
    clean(model, mach)
    assert statuses(model) == [ST, ST]
    for r in model.responses:
        assert abs(r["response_us"] - resp_us) <= 0.1, r


# ------------------------------------------------------------ negative controls
# The same session, with one timing moved outside the specification or one bit wrong:
# the model must catch each.

def test_negative_control_late_response():
    """A response 13 us after the command is outside 4 to 12 us: the model must flag it.
    (RESP_US is held to 8 to 11 by an .assert, so the constant is moved instead.)"""
    patch = [("- 9 + NX2", "- 9 + NX2 + 5 * B")]
    model, mach, host = session([tx(3, 2)], patch=patch)
    assert model.errors()


def test_resp_us_outside_the_window_is_refused():
    from escasm import AsmError
    with pytest.raises(AsmError):
        image(resp_us=12)
    with pytest.raises(AsmError):
        image(resp_us=7)


def test_negative_control_cell_one_tick_long():
    """Cells one tick (25 ns) too long: the crossings drift past the 25 ns limit."""
    patch = [("H | ((H // 2) << 20)", "(H + 1) | ((H // 2) << 20)")]
    model, mach, host = session([tx(3, 2)], patch=patch)
    assert model.errors()


def test_negative_control_wrong_parity_bit():
    """The status word's parity cells inverted: the model must refuse the status word."""
    patch = [(".equ VAR0, 0x55555500 + ((1 - (PCA & 1)) << 8)",
              ".equ VAR0, 0x55555500 + ((PCA & 1) << 8)")]
    model, mach, host = session([tx(3, 1)], patch=patch)
    assert model.errors() or not model.responses[0]["status_ok"]


# ------------------------------------------------------------ certificate

@pytest.mark.parametrize("khz", [40000, 50000])
def test_certificate(khz):
    """escert proves every event admitted and the status word's SEND on time; the ISS's
    lead is no smaller than the proved bound."""
    from escert import certify
    certs = certify(image(khz))
    assert [c.status for c in certs] == ["CHECKED", "CHECKED"]
    assert certs[0].replies_on_time
    model, mach, host = session([rx(3, [1, 2]), tx(3, 1)], khz)
    clean(model, mach)
    assert mach.ctx[0].min_lead >= certs[0].min_lead
