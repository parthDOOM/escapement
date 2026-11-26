"""M-Bus master model: the master, the two-wire bus and the slave's transceiver, at logic level.

Source: "The M-Bus: A Documentation", Rev. 4.8, M-Bus Usergroup ("Wired M-Bus
Specification", m-bus.com/documentation; it follows EN 1434-3 and IEC 870-5). Section
numbers below are that document's.

What is modelled
----------------
The chip under test is the microcontroller of an M-Bus SLAVE. On a real slave it sits
behind a TSS721 transceiver (4.4), which turns the bus voltage steps from the master
(Mark = 36 V, Space = 24 V, 4.1) into a logic-level UART line, and turns the
microcontroller's UART output into the slave's current modulation (Mark = <= 1.5 mA,
Space = +11-20 mA, 4.1). At logic level this leaves two lines, both with Mark = logical
1 = high and the idle (quiescent) state Mark (4.1, 5.1):
  * `to_slave`: the master's characters, driven push-pull by this model, idle high;
  * `from_slave`: the chip's output carrying the slave's characters, read by this model.
The TSS721 echoes what the microcontroller sends back onto its receive line (4.4,
"to allow the processor to recognize collisions"). `echo=True` models that: `to_slave`
then also shows the slave's Spaces. The default (False) shows only the master's
characters, as most slave firmware ignores its own echo anyway.

Character format (5.1)
----------------------
Each character is a start bit (Space), 8 data bits LSB first, an EVEN parity bit and a
stop bit (Mark): 11 bits. There must be no pauses within a telegram, "not even after a
stop bit". The bit rate is `baud` (300..38400; 6.4.1 lists 300-38400). `rate_error_ppm`
makes the master's own clock fast (positive) or slow; the master both sends and measures
everything (bit sampling, reply window, gaps) with that clock.

Telegram formats (5.2, 5.3)
---------------------------
Single character E5h; short frame 10h C A CS 16h; control frame 68h 03h 03h 68h C A CI
CS 16h; long frame 68h L L 68h C A CI data(0-252) CS 16h with L = len(data) + 3. CS is
the arithmetic sum without carry of C, A (short) or C, A, CI, data (control/long).
Helpers: `checksum(bytes)`, `short_frame(c, a)`, `long_frame(c, a, ci, data=b"")`
(a control frame when data is empty) and `parse(bytes)`.

parse(bytes) -> dict, never raises:
  valid (bool: the 5.2/5.4 format checks pass), errors (list of str), kind ("E5" |
  "short" | "control" | "long" | None), C, A, CI, L, data (user data after CI, bytes),
  app_errors (list of str, application-layer remarks only; they do not make it invalid).
  For CI 72h/76h (variable data respond, 6.3) with >= 12 data bytes, the fixed data
  header of 6.3.1: ident (int from the 8 BCD digits, None if a nibble is not BCD),
  ident_hex ("12345678"), ident_raw (4 bytes as sent), manufacturer (int),
  manufacturer_str (three letters, 6.3.1 formula), version, medium, access_no, status,
  signature (int) and records (bytes after the 12-byte header, raw). CI 76h is mode 2:
  multibyte fields most significant byte first (6.1). For CI 73h/77h (fixed data
  respond, 6.2): ident, ident_hex, ident_raw, access_no, status, medium_unit (2 bytes,
  always LSB first per 6.2), counter1_raw, counter2_raw.

The master (5.4)
----------------
`telegrams` is the master's script: a list of telegrams (bytes or lists of ints) sent in
order, the first at `start_ms`. Each next one is sent `gap_bits` (default 33) bit times
after the slave's reply has ended (the end of its last stop bit), or, when no reply comes,
after the reply timeout of 330 bit times + 50 ms counted from the end of the master's
telegram. The 33 bit times are the "idle time of at least 33 bit periods" of 5.4; the
document only demands it after failed attempts, this model uses it after every exchange
(so the slave also always has a quiet line before the next telegram). The master always
waits the full timeout for telegrams that need no reply (address 255, faulty
telegrams...), so that a wrong reply would be seen. 5.4's repeat rule ("repeats the same
telegram up to two times") is available as `retries` (default 0: the script says
exactly what is sent; tests can also list a telegram twice). With retries > 0 a telegram
that requires a reply and gets no valid one is sent again, identical (same FCB, 5.3), up
to `retries` more times; every attempt is its own exchange (key "attempt").

Faults the master puts into its own telegrams (`faults`), to test the slave
-----------------------------------------------------------------------------
A dict; each value names telegram i (index in `telegrams`) and, where it applies,
character k (0 = first character). A list of such entries applies several.
  "parity":    (i, k)        character k of telegram i is sent with ODD parity
  "stop":      (i, k)        its stop bit is a Space (for one bit time; the next character
                             follows at its normal time)
  "checksum":  i             the check sum character is sent plus 1 (mod 256)
  "stop_char": i             the last character is sent XOR 01h (16h -> 17h)
  "length":    i             the second L field is sent as L + 1 (long/control frames)
  "pause":     (i, k, bits)  `bits` bit times of Mark are inserted before character k
                             (k >= 1)
Each is a fault a receiver detects with the checks of 5.4 (bits, start/check sum/stop
characters, second start character, the L fields, the character count), or (pause) 5.1.
A faulty telegram must get no reply (5.4: "the reply or acknowledgement will not be
sent"). Byte faults show in the exchange's "sent"; bit faults (parity, stop, pause) do
not. A script telegram that itself fails parse() counts as faulty too.

What it records
---------------
`exchanges`: one dict per telegram sent:
  t                 tick at which the telegram's first start bit began on `to_slave`
  t_end             tick at which its last stop bit ended
  index, attempt    script index, attempt number (0 = first send)
  sent              bytes sent (with byte faults applied)
  fault             the faults applied to this telegram ({"parity": (i, k), ...}) or None
  function          "SND_NKE" | "SND_UD" | "REQ_UD2" | "REQ_UD1" | "other" | None
                    (None: not a valid telegram); address: its A field (or None)
  required          True if 5.4 requires an answer (see below)
  reply             bytes received from the slave (b"" if none)
  reply_kind        "E5" | "short" | "long" | "control" | None (from the first character)
  reply_valid       True when a reply came and all checks on it passed (False if none)
  reply_delay_bits  from t_end to the reply's first start bit, in bit times (None if none)
  parsed            parse(reply) for a valid reply, else None
  errors            the violations recorded during this exchange; ok: no violations
`violations`: every violation as "t=<tick>: <message> (<section>)"; `errors()` returns
it. `finish()` closes the run: a reply window still open or telegrams not yet sent are
violations too (run long enough; `done` is True once the script is complete).

Checks on the slave (each failure is a violation)
-------------------------------------------------
Characters (5.1). The master's receiver works like a UART at the master's bit rate: a
falling edge after Mark starts a character, each bit is sampled at its centre measured
from that edge, start must be Space, then 8 data bits LSB first, even parity, stop Mark.
INTERPRETATION, bit-rate tolerance: 5.1 gives no number; the signal quality paper it
refers to (WG4N86R2.DOC) is not part of the document. This model requires the level to
be steady over the middle half of every bit (from 1/4 to 3/4 of each bit time after its
nominal start, measured from the start edge): a 16x-oversampling UART samples about
7/16-9/16 into the bit and loses up to 1/16 bit finding the start edge, so 1/4 bit of
margin each side is what a careful master can count on. Since the last possible level
change inside a character is the one before the stop bit, 10 bit times after the start
edge, this accepts a slave bit rate within +/-2.5 % of the master's (less if the slave's
edges also jitter), and also rejects a stop bit shorter than 3/4 bit.
INTERPRETATION, pause: at logic level a "pause within a telegram" is Mark between the
end of one character's stop bit and the next start bit. The model measures from the
nominal end of the stop bit (start edge + 11 bit times, master's clock) to the next
start edge and accepts up to `max_gap_bits` (default 0.5) bit times: less than that is
explained by the 2.5 % rate tolerance (0.28 bit over 11 bits) plus edge quantisation,
while a whole extra Mark bit, which is what "not even after a stop bit" forbids (a
second stop bit), is a pause. A reply that stops before it is complete is given up
after `idle_bits` (default 33, the 5.4 idle time) bit times of Mark.

The reply (5.2, 5.3, 5.4): the format checks parse() makes: start character E5h/10h/68h
(5.2); for a short frame 5 characters, check sum, stop 16h; for a control/long frame the
two L fields equal, the second start character 68h, L >= 3, the number of characters
L + 6, the check sum and stop 16h (5.2, and the fault list of 5.4). In the reply
direction C bits 7 and 6 must be 0 (5.3, Fig. 14). The answer 5.4 requires: E5h to
SND_NKE and to SND_UD; RSP_UD to REQ_UD2, a long or control frame whose C field is 08h,
18h, 28h or 38h (RSP_UD with ACD/DFC, Table 1; 5.3 allows both bits), and whose A field
is the slave's own primary address, also when the master used 254 (5.3, 5.5.2 item 5).
To REQ_UD1 (8.1) E5h or RSP_UD is accepted. For other telegrams any well-formed reply
from the slave's own address is accepted. A reply to address 253 (7.1, network layer) may
carry the own address or FDh.

The reply window (5.4): the first start bit of the reply must begin no earlier than 11 bit
times and no later than 330 bit times + 50 ms after the end of the master's telegram
(t_end). Characters that start after the window has closed are late replies (violation).

Who must not reply (5.3, 5.4, 5.5.2 item 4): no reply to address 255 (broadcast), to an
address other than the slave's own (`slave_address`, default 0), 254 (test address) or 253
(selected via the network layer, 7.1), or to a faulty telegram. The slave must not send a
Space while the master is sending (half duplex, 4.1, 5.1), and no characters may come
outside a reply window (extra characters after a complete reply, unsolicited ones).

Required answers (5.4): a valid SND_NKE, SND_UD or REQ_UD2 to `slave_address` or 254 that
gets no reply within the window is a violation. Telegrams to 253 do not require an answer
(the model does not track the slave's selection state).

The model does not decide what the slave's application data should be: tests do, from
`exchanges[i]["parsed"]`.

Timing conventions: step(t, bus) sees the bus level of tick t-1, so an edge on
`from_slave` seen at step t happened at tick t-1, and that is the tick recorded. The level
this model drives at step t is on `to_slave` in tick t.
"""

from __future__ import annotations

import math

from .board import Device

E5 = 0xE5
_INF = float("inf")
_WAIT, _SEND, _AWAIT, _DONE = range(4)


# ---------------------------------------------------------------- telegram helpers (5.2, 5.3)

def checksum(data) -> int:
    """Arithmetic sum without carry (5.3) of the given bytes."""
    return sum(bytes(data)) & 0xFF


def short_frame(c: int, a: int) -> bytes:
    """Short frame 10h C A CS 16h (5.2)."""
    return bytes([0x10, c, a, (c + a) & 0xFF, 0x16])


def long_frame(c: int, a: int, ci: int, data=b"") -> bytes:
    """Long frame 68h L L 68h C A CI data CS 16h; a control frame (L = 3) when data is empty."""
    data = bytes(data)
    n = len(data) + 3
    if n > 255:
        raise ValueError(f"long frame user data {len(data)} bytes, at most 252 (5.2)")
    body = bytes([c, a, ci]) + data
    return bytes([0x68, n, n, 0x68]) + body + bytes([checksum(body), 0x16])


def control_frame(c: int, a: int, ci: int) -> bytes:
    """Control frame: a long frame without user data, L = 3 (5.2)."""
    return long_frame(c, a, ci)


def _bcd(v: int, digits: int = 8):
    out = 0
    for i in range(digits - 1, -1, -1):
        d = (v >> (4 * i)) & 0xF
        if d > 9:
            return None
        out = out * 10 + d
    return out


def _man_str(m: int) -> str:
    return "".join(chr(((m >> s) & 0x1F) + 64) for s in (10, 5, 0))


def _parse_app(p: dict) -> None:
    ci, d = p["CI"], p["data"]
    order = "big" if ci in (0x76, 0x77) else "little"      # 6.1: M bit set = mode 2

    def num(bs):
        return int.from_bytes(bs, order)

    if ci in (0x72, 0x76):
        if len(d) < 12:
            p["app_errors"].append(f"CI {ci:02X}h but only {len(d)} data bytes, the fixed "
                                   "data header has 12 (6.3.1)")
            return
        idv = num(d[0:4])
        man = num(d[4:6])
        p.update(ident=_bcd(idv), ident_hex=f"{idv:08X}", ident_raw=bytes(d[0:4]),
                 manufacturer=man, manufacturer_str=_man_str(man), version=d[6],
                 medium=d[7], access_no=d[8], status=d[9], signature=num(d[10:12]),
                 records=bytes(d[12:]))
    else:                                                   # 73h/77h fixed data (6.2)
        if len(d) != 16:
            p["app_errors"].append(f"CI {ci:02X}h with {len(d)} data bytes, the fixed data "
                                   "structure has 16 (6.2)")
            if len(d) < 16:
                return
        idv = num(d[0:4])
        p.update(ident=_bcd(idv), ident_hex=f"{idv:08X}", ident_raw=bytes(d[0:4]),
                 access_no=d[4], status=d[5], medium_unit=int.from_bytes(d[6:8], "little"),
                 counter1_raw=bytes(d[8:12]), counter2_raw=bytes(d[12:16]))


def parse(telegram) -> dict:
    """Decode and check one telegram (5.2, 5.3, 5.4); see the module docstring."""
    b = bytes(telegram)
    p = {"valid": False, "errors": [], "kind": None, "C": None, "A": None, "CI": None,
         "L": None, "data": b"", "app_errors": []}
    err = p["errors"].append
    if not b:
        err("empty telegram (5.2)")
        return p
    s = b[0]
    if s == E5:
        p["kind"] = "E5"
        if len(b) != 1:
            err(f"single character E5h followed by {len(b) - 1} more characters (5.2)")
    elif s == 0x10:
        p["kind"] = "short"
        if len(b) != 5:
            err(f"short frame of {len(b)} characters, the format has 5 (5.2)")
        if len(b) >= 3:
            p["C"], p["A"] = b[1], b[2]
        if len(b) >= 4 and b[3] != (b[1] + b[2]) & 0xFF:
            err(f"short frame check sum {b[3]:02X}h, C + A gives {(b[1] + b[2]) & 0xFF:02X}h "
                "(5.2, 5.3)")
        if len(b) >= 5 and b[4] != 0x16:
            err(f"short frame stop character {b[4]:02X}h, not 16h (5.2)")
    elif s == 0x68:
        if len(b) < 4:
            err(f"long/control frame cut short after {len(b)} characters (5.2, 5.4)")
        else:
            n = b[1]
            p["L"] = n
            p["kind"] = "control" if n == 3 else "long"
            if b[1] != b[2]:
                err(f"the two L fields differ: {b[1]:02X}h and {b[2]:02X}h (5.2, 5.4)")
            if b[3] != 0x68:
                err(f"second start character {b[3]:02X}h, not 68h (5.2, 5.4)")
            if n < 3:
                err(f"L field {n}, at least 3 for C, A and CI (5.2)")
            if len(b) != n + 6:
                err(f"{len(b)} characters, the L field {n} gives L + 6 = {n + 6} (5.4)")
            if n >= 3 and len(b) >= 7:
                p["C"], p["A"], p["CI"] = b[4], b[5], b[6]
                p["data"] = bytes(b[7:min(len(b), 4 + n)])
            if n >= 3 and len(b) >= n + 6:
                cs = checksum(b[4:4 + n])
                if b[4 + n] != cs:
                    err(f"check sum {b[4 + n]:02X}h, C..data gives {cs:02X}h (5.2, 5.3)")
                if b[5 + n] != 0x16:
                    err(f"stop character {b[5 + n]:02X}h, not 16h (5.2)")
    else:
        err(f"start character {s:02X}h is none of E5h, 10h, 68h (5.2)")
    p["valid"] = not p["errors"]
    if p["valid"] and p["CI"] in (0x72, 0x76, 0x73, 0x77):
        _parse_app(p)
    return p


def _classify(tel: bytes):
    """(parse, function name or None) of a telegram in the calling direction (Table 1)."""
    p = parse(tel)
    if not p["valid"]:
        return p, None
    c, kind = p["C"], p["kind"]
    if c is None or c & 0xC0 != 0x40:
        return p, "other"
    f = c & 0x4F                       # without FCB (bit 5) and FCV (bit 4)
    if kind == "short" and f == 0x40:
        return p, "SND_NKE"
    if kind in ("long", "control") and f == 0x43:
        return p, "SND_UD"
    if kind == "short" and f == 0x4B:
        return p, "REQ_UD2"
    if kind == "short" and f == 0x4A:
        return p, "REQ_UD1"
    return p, "other"


def _norm_faults(faults, script):
    """-> {i: {"parity": set, "stop": set, "pause": {k: bits}, "checksum"/"stop_char"/"length": bool}},
    {i: record dict}"""
    per, rec = {}, {}
    if not faults:
        return per, rec
    n = len(script)

    def entries(v, single_is_tuple):
        if single_is_tuple:
            return list(v) if isinstance(v, list) else [v]
        return list(v) if isinstance(v, (list, tuple, set)) else [v]

    for key, val in faults.items():
        if key not in ("parity", "stop", "pause", "checksum", "stop_char", "length"):
            raise ValueError(f"unknown fault {key!r}")
        for e in entries(val, key in ("parity", "stop", "pause")):
            if key in ("parity", "stop"):
                i, k = e
            elif key == "pause":
                i, k, bits = e
            else:
                i = e
            if not 0 <= i < n:
                raise ValueError(f"fault {key}: telegram {i} not in the script")
            d = per.setdefault(i, {"parity": set(), "stop": set(), "pause": {}})
            if key in ("parity", "stop", "pause"):
                if not 0 <= k < len(script[i]):
                    raise ValueError(f"fault {key}: telegram {i} has no character {k}")
            if key in ("parity", "stop"):
                d[key].add(k)
            elif key == "pause":
                if k < 1 or bits <= 0:
                    raise ValueError("fault pause: needs k >= 1 and bits > 0")
                d["pause"][k] = d["pause"].get(k, 0.0) + float(bits)
            else:
                if key == "length" and (script[i][:1] != b"\x68" or len(script[i]) < 3):
                    raise ValueError(f"fault length: telegram {i} is not a long/control frame")
                if len(script[i]) < 2:
                    raise ValueError(f"fault {key}: telegram {i} is too short")
                d[key] = True
            r = rec.setdefault(i, {})
            if key in r:
                r[key] = (r[key] if isinstance(r[key], list) else [r[key]]) + [e]
            else:
                r[key] = e
    return per, rec


# ---------------------------------------------------------------- the master

class MbusMaster(Device):
    """M-Bus master + bus + slave transceiver at logic level (see the module docstring)."""

    def __init__(self, to_slave: int = 0, from_slave: int = 1, tick_ns: float = 40.0,
                 baud: int = 2400, telegrams=(), start_ms: float = 1.0, faults=None,
                 slave_address: int = 0, rate_error_ppm: float = 0.0, gap_bits: float = 33.0,
                 max_gap_bits: float = 0.5, idle_bits: float = 33.0, retries: int = 0,
                 echo: bool = False):
        if not 300 <= baud <= 38400:
            raise ValueError(f"baud {baud}: M-Bus rates are 300..38400 (5.1, 6.4.1)")
        if not 0 <= slave_address <= 250:
            raise ValueError(f"slave_address {slave_address}: primary addresses are 0..250 (5.3)")
        self.to_slave, self.from_slave = to_slave, from_slave
        self.tick_ns, self.baud = tick_ns, baud
        self.slave_address = slave_address
        clk = 1.0 / (1.0 + rate_error_ppm * 1e-6)            # the master's own clock
        self.tpb = 1e9 / (baud * tick_ns) * clk              # ticks per bit (master's clock)
        self.ms = 1e6 / tick_ns * clk                        # ticks per millisecond
        self.timeout = 330 * self.tpb + 50 * self.ms         # reply window end (5.4)
        self.gap_bits, self.max_gap_bits, self.idle_bits = gap_bits, max_gap_bits, idle_bits
        self.retries, self.echo = retries, echo
        self.script = [bytes(x) for x in telegrams]
        self._fper, self._frec = _norm_faults(faults, self.script)
        self.sent = []                                       # bytes actually sent, per script line
        for i, tel in enumerate(self.script):
            b = bytearray(tel)
            f = self._fper.get(i, {})
            if f.get("checksum"):
                b[-2] = (b[-2] + 1) & 0xFF
            if f.get("stop_char"):
                b[-1] ^= 0x01
            if f.get("length"):
                b[2] = (b[2] + 1) & 0xFF
            self.sent.append(bytes(b))
        self.exchanges: list[dict] = []
        self.violations: list[str] = []
        self.done = not self.script
        self._finished = False
        self._t = 0
        self._mto, self._fp = 1 << to_slave, from_slave
        self._out = (0, self._mto)
        self._txl = 1
        self._prev = -1                        # the first step always looks at the line
        self._seen_idle = False
        # transmitter
        self._tx_ticks: list[int] = []
        self._tx_lvls: list[int] = []
        self._tx_i = 0
        self._t_end = 0
        self._tx_flagged = False
        # receiver
        self._rx_state = 0                     # 0 hunt, 1 in a character, 2 wait for Mark
        self._rx_start = 0
        self._rx_i = 0
        self._rx_val = 0
        self._rx_par = 0
        self._rx_flag = False
        self._rx_thr = _INF
        self._rx_prev_start = None
        # exchange state
        self._state = _DONE if self.done else _WAIT
        self._t_next = math.ceil(start_ms * self.ms)
        self._idx = 0
        self._attempt = 0
        self._cur = None
        self._deadline = _INF
        self._r_started = False
        self._r_bytes = bytearray()
        self._r_starts: list[float] = []
        self._r_bad = False
        self._wake = self._t_next if not self.done else _INF

    # -------------------------------------------------------------- per tick
    def step(self, t, bus):
        self._t = t
        lvl = (bus >> self._fp) & 1
        if lvl != self._prev or t >= self._wake:
            self._event(t, lvl)
        return self._out

    def _event(self, t, lvl):
        obs = t - 1                                    # the tick this bus level belongs to
        if lvl != self._prev:
            self._prev = lvl
            if not self._seen_idle:
                self._seen_idle = lvl == 1             # the undriven/low line before idle
            else:
                if not lvl and self._state == _SEND and not self._tx_flagged:
                    self._tx_flagged = True
                    self._viol(obs, "the slave sent a Space while the master was sending "
                               "(half duplex, 4.1, 5.1)")
                self._rx_edge(obs, lvl)
        if self._rx_state == 1 and t >= self._rx_thr:
            self._rx_sample(obs, lvl)
        st = self._state
        if st == _WAIT and t >= self._t_next:
            self._begin_send(t, lvl)
            st = self._state
        if st == _SEND:
            ticks = self._tx_ticks
            i = self._tx_i
            while i < len(ticks) and t >= ticks[i]:
                self._txl = self._tx_lvls[i]
                i += 1
            self._tx_i = i
            if i == len(ticks) and t >= self._t_end:
                self._state = st = _AWAIT
                self._deadline = self._t_end + self.timeout + 1
        if st == _AWAIT and t >= self._deadline:
            self._await_timer(t)
        # output and next wake-up
        out = self._txl & (lvl if self.echo else 1)
        self._out = (0, self._mto) if out else (self._mto, 0)
        w = self._rx_thr if self._rx_state == 1 else _INF
        st = self._state
        if st == _SEND:
            nt = self._tx_ticks[self._tx_i] if self._tx_i < len(self._tx_ticks) else self._t_end
            w = min(w, nt)
        elif st == _WAIT:
            w = min(w, self._t_next)
        elif st == _AWAIT:
            w = min(w, self._deadline)
        self._wake = w

    # -------------------------------------------------------------- helpers
    def _viol(self, t, msg, reply=False):
        s = f"t={int(t)}: {msg}"
        self.violations.append(s)
        if self._cur is not None and self._state in (_SEND, _AWAIT):
            self._cur["errors"].append(s)
        if reply:
            self._r_bad = True

    def _bits(self, n):
        return n / self.tpb

    # -------------------------------------------------------------- transmitter
    def _begin_send(self, t, lvl):
        i = self._idx
        tel = self.sent[i]
        f = self._fper.get(i, {})
        par_f, stop_f, pause = f.get("parity", ()), f.get("stop", ()), f.get("pause", {})
        trans = []
        cur_l, pos = 1, 0.0
        for k, b in enumerate(tel):
            pos += pause.get(k, 0.0)
            par = bin(b).count("1") & 1                # even parity: total of ones even
            if k in par_f:
                par ^= 1
            bits = [0] + [(b >> j) & 1 for j in range(8)] + [par, 0 if k in stop_f else 1]
            for j, v in enumerate(bits):
                if v != cur_l:
                    trans.append((pos + j, v))
                    cur_l = v
            pos += 11
        if cur_l == 0:
            trans.append((pos, 1))
        tpb = self.tpb
        self._tx_ticks = [t + math.ceil(x * tpb - 1e-9) for x, _ in trans]
        self._tx_lvls = [v for _, v in trans]
        self._tx_i = 0
        self._t_end = t + math.ceil(pos * tpb - 1e-9)
        p, func = _classify(tel)
        fault = self._frec.get(i)
        addr = p["A"]
        own = self.slave_address
        sent_ok = fault is None and func is not None
        self._cur = {
            "t": t, "t_end": self._t_end, "index": i, "attempt": self._attempt,
            "sent": tel, "fault": dict(fault) if fault else None, "function": func,
            "address": addr,
            "required": sent_ok and func in ("SND_NKE", "SND_UD", "REQ_UD2") and addr in (own, 254),
            "reply": b"", "reply_kind": None, "reply_valid": False, "reply_delay_bits": None,
            "parsed": None, "errors": [], "ok": True,
        }
        self._cur_ok = sent_ok
        self.exchanges.append(self._cur)
        self._state = _SEND
        self._tx_flagged = False
        self._r_started = False
        self._r_bytes = bytearray()
        self._r_starts = []
        self._r_bad = False
        if self._seen_idle and lvl == 0:
            self._tx_flagged = True
            self._viol(t - 1, "the slave's line is Space as the master starts sending "
                       "(half duplex, 4.1, 5.1)")

    # -------------------------------------------------------------- receiver (5.1)
    def _rx_edge(self, obs, lvl):
        st = self._rx_state
        if st == 0:
            if lvl == 0:
                ps = self._rx_prev_start
                if ps is not None and obs - ps < 10.75 * self.tpb:
                    self._viol(obs, f"stop bit of the previous character ended "
                               f"{self._bits(obs - ps) - 10:.2f} bit times after it began, "
                               "less than 3/4 bit (5.1; tolerance: level steady over the "
                               "middle half of each bit)", reply=self._in_reply())
                self._rx_state = 1
                self._rx_start = obs
                self._rx_i = 0
                self._rx_val = 0
                self._rx_flag = False
                self._rx_thr = obs + 0.5 * self.tpb + 1
        elif st == 1:
            if not self._rx_flag:
                p = (obs - self._rx_start) / self.tpb
                n = int(p)
                if n <= 10 and 0.25 < p - n < 0.75:
                    self._rx_flag = True
                    self._viol(obs, f"slave character: level change {p:.2f} bit times after "
                               f"the start edge, inside the middle half of bit {n} (5.1; "
                               "accepted: bit rate within +/-2.5 % of the master's)",
                               reply=self._in_reply())
        elif lvl == 1:
            self._rx_state = 0

    def _in_reply(self):
        return self._state == _AWAIT and self._r_started

    def _rx_sample(self, obs, lvl):
        i = self._rx_i
        if i == 0:
            if lvl:
                self._rx_state = 0
                self._viol(self._rx_start, "slave line: Space shorter than half a bit, not a "
                           "start bit (5.1)", reply=self._in_reply())
                if self._in_reply():
                    self._deadline = self._rx_start + self.idle_bits * self.tpb + 1
                return
            self._on_rx_start(self._rx_start)
        elif i <= 8:
            self._rx_val |= lvl << (i - 1)
        elif i == 9:
            self._rx_par = lvl
        else:
            start, val = self._rx_start, self._rx_val
            self._rx_prev_start = start
            ok = not self._rx_flag
            inr = self._in_reply()
            if (bin(val).count("1") + self._rx_par) & 1:
                ok = False
                self._viol(obs, f"slave character {val:02X}h has odd parity, 5.1 requires even",
                           reply=inr)
            if not lvl:
                ok = False
                self._rx_state = 2
                self._viol(obs, f"slave character {val:02X}h: stop bit is a Space (5.1)",
                           reply=inr)
            else:
                self._rx_state = 0
            self._on_rx_char(start, val, ok)
            return
        self._rx_i = i + 1
        self._rx_thr = self._rx_start + (i + 1.5) * self.tpb + 1

    # -------------------------------------------------------------- exchange logic (5.4)
    def _on_rx_start(self, start):
        if self._state != _AWAIT:
            return
        cur = self._cur
        if start < cur["t_end"]:
            return                                       # began while the master was sending
        if not self._r_started:
            d = start - cur["t_end"]
            if d > self.timeout:
                return                                   # too late: _await_timer closes it
            self._r_started = True
            self._deadline = _INF
            cur["reply_delay_bits"] = self._bits(d)
            if self._bits(d) < 11:
                self._viol(start, f"reply began {self._bits(d):.2f} bit times after the end of "
                           "the master's telegram, the slave must wait at least 11 (5.4)",
                           reply=True)
        else:
            self._deadline = _INF
            gap = self._bits(start - (self._r_starts[-1] + 11 * self.tpb))
            if gap > self.max_gap_bits:
                self._viol(start, f"pause of {gap:.2f} bit times before character "
                           f"{len(self._r_starts)} of the reply; no pauses within a telegram "
                           f"(5.1; accepted up to {self.max_gap_bits} bit)", reply=True)

    def _on_rx_char(self, start, val, ok):
        if self._state == _AWAIT and self._r_started and start >= self._cur["t_end"]:
            self._r_bytes.append(val)
            self._r_starts.append(start)
            if not ok:
                self._r_bad = True
            r = self._r_bytes
            need = None
            if r[0] == E5:
                need = 1
            elif r[0] == 0x10:
                need = 5
            elif r[0] == 0x68 and len(r) >= 2:
                need = r[1] + 6
            end = start + 11 * self.tpb
            if need is not None and len(r) >= need:
                self._finalize(end)
            else:
                self._deadline = end + self.idle_bits * self.tpb + 1
            return
        if self._state == _SEND or (self._cur is not None and self._state == _AWAIT
                                    and start < self._cur["t_end"]):
            return                                       # sent during the master's telegram
        self._viol(start, f"character {val:02X}h from the slave outside any reply window "
                   "(extra character after a reply, late reply or unsolicited) (5.4)")

    def _await_timer(self, t):
        if not self._r_started:
            if (self._rx_state == 1 and self._rx_i == 0
                    and self._rx_start - self._cur["t_end"] <= self.timeout):
                self._deadline = self._rx_thr           # decide at the start bit's centre
                return
            self._finalize(None)
        elif self._rx_state == 1:
            self._deadline = _INF                        # a character is being received
        else:
            last = self._r_starts[-1] + 11 * self.tpb if self._r_starts else t
            self._viol(t, f"reply incomplete: {len(self._r_bytes)} characters, then "
                       f"{self.idle_bits:g} bit times of Mark (5.2, 5.4)", reply=True)
            self._finalize(last + self.idle_bits * self.tpb)

    def _finalize(self, end):
        cur = self._cur
        r = bytes(self._r_bytes)
        own = self.slave_address
        addr, func = cur["address"], cur["function"]
        cur["reply"] = r
        p = None
        tq = end if end is not None else cur["t_end"] + self.timeout
        if r:
            p = parse(r)
            cur["reply_kind"] = p["kind"]
            for e in p["errors"]:
                self._viol(tq, f"reply {r.hex(' ')}: {e}", reply=True)
            if not self._cur_ok:
                why = f"fault {cur['fault']}" if cur["fault"] else "not a valid telegram"
                self._viol(tq, f"the slave replied to a faulty telegram ({why}); a faulty "
                           "telegram gets no reply (5.4)", reply=True)
            elif addr == 255:
                self._viol(tq, "the slave replied to a telegram for broadcast address 255; "
                           "with 255 none of the slaves reply (5.3, 5.5.2 item 4)", reply=True)
            elif addr not in (own, 254, 253):
                self._viol(tq, f"the slave (address {own}) replied to a telegram for address "
                           f"{addr} (5.3, 5.4)", reply=True)
            else:
                kind = p["kind"]
                if kind in ("short", "long", "control") and p["C"] is not None:
                    c, a = p["C"], p["A"]
                    if c & 0xC0:
                        self._viol(tq, f"reply C field {c:02X}h: bits 7 and 6 must be 0 in the "
                                   "reply direction (5.3, Fig. 14)", reply=True)
                    allowed = (own, 253) if addr == 253 else (own,)
                    if a not in allowed:
                        via = " (the master used the test address 254)" if addr == 254 else ""
                        self._viol(tq, f"reply A field {a} is not the slave's own primary "
                                   f"address {own}{via} (5.3, 5.5.2 item 5)", reply=True)
                rsp = kind in ("long", "control") and p["C"] is not None and p["C"] & 0xCF == 0x08
                if func in ("SND_NKE", "SND_UD") and kind != "E5":
                    self._viol(tq, f"the answer to {func} must be the single character E5h "
                               f"(5.4), got {kind} {r.hex(' ')}", reply=True)
                elif func == "REQ_UD2" and not rsp:
                    self._viol(tq, "the answer to REQ_UD2 must be RSP_UD: a long or control "
                               "frame with C = 08h/18h/28h/38h (5.4, 5.3 Table 1), got "
                               f"{r.hex(' ')}", reply=True)
                elif func == "REQ_UD1" and not (kind == "E5" or rsp):
                    self._viol(tq, "the answer to REQ_UD1 must be E5h or RSP_UD (8.1, Table 1),"
                               f" got {r.hex(' ')}", reply=True)
        elif cur["required"]:
            self._viol(tq, f"no reply to a valid {func} for address {addr} within 330 bit times "
                       "+ 50 ms (5.4: the slave must answer)")
        valid = bool(r) and not self._r_bad
        cur["reply_valid"] = valid
        cur["parsed"] = p if valid else None
        cur["ok"] = not cur["errors"]
        # what next (5.4: repeat, idle time)
        self._state = _WAIT
        if cur["required"] and not valid and self._attempt < self.retries:
            self._attempt += 1
        else:
            self._attempt = 0
            self._idx += 1
        self._deadline = _INF
        self._t_next = math.ceil(tq + self.gap_bits * self.tpb)
        if self._idx >= len(self.script):
            self._state = _DONE
            self.done = True

    # -------------------------------------------------------------- results
    def finish(self, t=None):
        """End of run: flags an open reply window and telegrams not sent. Returns errors()."""
        if self._finished:
            return self.errors()
        self._finished = True
        t = self._t if t is None else t
        if self._state in (_SEND, _AWAIT):
            cur = self._cur
            what = "while it was being sent" if self._state == _SEND else (
                "during its reply" if self._r_started else "with its reply window still open")
            self._viol(t, f"run ended at telegram {cur['index']} ({cur['sent'].hex(' ')}) "
                       f"{what} (5.4)")
            cur["reply"] = bytes(self._r_bytes)
            cur["ok"] = False
        left = len(self.script) - self._idx - (1 if self._state in (_SEND, _AWAIT) else 0)
        if left > 0 and self._state != _DONE:
            self.violations.append(f"t={t}: run ended with {left} telegram(s) of the script "
                                   "not sent")
        return self.errors()

    def errors(self):
        return list(self.violations)


# ---------------------------------------------------------------- self-test

class _SimSlave(Device):
    """A bit-banged M-Bus slave behind a logic-level transceiver, written from the same text:
    receives 11-bit characters (5.1), assembles FT 1.2 telegrams (5.2), rejects faulty ones
    (5.4 checks; a pause > 1/2 bit inside a telegram, 5.1), answers SND_NKE and SND_UD with
    E5h and REQ_UD2 with RSP_UD (CI 72h, 6.3) after `delay_bits`, to its own address and
    254, never to 255 (5.3). Misbehaviours are switched on by keyword."""

    def __init__(self, rx, tx, tpb, address, records=b"", delay_bits=15.0, bad_parity=None,
                 pause=None, reply_255=False, bad_cs=False, no_nke=False, a_field=None,
                 talk_during=False, lax=False, tx_rate=1.0):
        self.rx, self.tx, self.tpb, self.address = rx, tx, tpb, address
        self.tx_tpb = tpb * tx_rate
        self.records, self.delay = bytes(records), delay_bits
        self.bad_parity, self.pause, self.reply_255 = bad_parity, pause, reply_255
        self.bad_cs, self.no_nke, self.a_field = bad_cs, no_nke, a_field
        self.talk_during, self.lax = talk_during, lax
        self.access = 0
        self.frames: list[bytes] = []          # telegrams accepted
        self._m = 1 << tx
        self._out = (0, self._m)
        self._prev = 1
        self._st = 0
        self._thr = _INF
        self._buf = bytearray()
        self._err = False
        self._last = None
        self._quiet = _INF
        self._txt: list[int] = []
        self._txv: list[int] = []
        self._txi = 0
        self._wake = _INF

    def step(self, t, bus):
        lvl = (bus >> self.rx) & 1
        if lvl != self._prev or t >= self._wake:
            self._ev(t, lvl)
        return self._out

    def _ev(self, t, lvl):
        tpb = self.tpb
        if lvl != self._prev:
            self._prev = lvl
            if self._st == 0 and lvl == 0:
                s = t - 1
                if self._buf and s - (self._last + 11 * tpb) > 0.5 * tpb:
                    self._err = True                      # pause within the telegram
                self._st, self._i, self._v, self._s = 1, 0, 0, s
                self._thr = s + 0.5 * tpb + 1
                if self.talk_during and len(self._buf) == 2 and not self._txt[self._txi:]:
                    self._send(t, bytes([0xFF]))          # a start bit inside the master's frame
            elif self._st == 2 and lvl == 1:
                self._st = 0
        if self._st == 1 and t >= self._thr:
            i = self._i
            if i == 0 and lvl:
                self._st = 0
            elif 1 <= i <= 8:
                self._v |= lvl << (i - 1)
            elif i == 9:
                self._p = lvl
            if self._st == 1 and i < 10:
                self._i = i + 1
                self._thr = self._s + (i + 1.5) * tpb + 1
            elif self._st == 1:
                ones = self._p
                for j in range(8):
                    ones += (self._v >> j) & 1
                if ones % 2 or not lvl:
                    self._err = True
                self._st = 0 if lvl else 2
                self._buf.append(self._v)
                self._last = self._s
                self._quiet = self._s + 22 * tpb + 1
                b = self._buf
                need = 5 if b[0] == 0x10 else (b[1] + 6 if b[0] == 0x68 and len(b) > 1 else None)
                if b[0] not in (0x10, 0x68):
                    self._err = True
                if need is not None and len(b) >= need:
                    if not self._err:
                        self._frame(bytes(b), self._s + 11 * tpb)
                    self._buf, self._err, self._quiet = bytearray(), False, _INF
        if self._buf and t >= self._quiet and self._st != 1:
            self._buf, self._err, self._quiet = bytearray(), False, _INF   # idle: discard
        while self._txi < len(self._txt) and t >= self._txt[self._txi]:
            self._out = (0, self._m) if self._txv[self._txi] else (self._m, 0)
            self._txi += 1
        w = self._thr if self._st == 1 else _INF
        if self._buf:
            w = min(w, self._quiet)
        if self._txi < len(self._txt):
            w = min(w, self._txt[self._txi])
        self._wake = w

    def _frame(self, f, end):
        if f[0] == 0x10:
            ok = f[3] == (f[1] + f[2]) & 0xFF or self.lax
            ok = ok and f[4] == 0x16
            c, a, long_ = f[1], f[2], False
        else:
            n = f[1]
            ok = f[2] == n and f[3] == 0x68 and n >= 3 and len(f) == n + 6 and f[-1] == 0x16
            ok = ok and (self.lax or f[-2] == sum(f[4:4 + n]) & 0xFF)
            c, a, long_ = (f[4], f[5], True) if ok else (0, 0, True)
        if not ok or c & 0xC0 != 0x40:
            return
        self.frames.append(f)
        if a == 255 and not self.reply_255:
            return
        if a not in (self.address, 254, 255):
            return
        fn = c & 0x4F
        reply = None
        if not long_ and fn == 0x40 and not self.no_nke:
            reply = bytes([E5])
        elif long_ and fn == 0x43:
            reply = bytes([E5])
        elif not long_ and fn == 0x4B:
            hdr = (bytes.fromhex("78563412")                   # ident 12345678, BCD, LSB first
                   + (0x4024).to_bytes(2, "little")            # "PAD"
                   + bytes([0x01, 0x07, self.access & 0xFF, 0x00, 0x00, 0x00]))
            self.access += 1
            aa = self.address if self.a_field is None else self.a_field
            body = bytes([0x08, aa, 0x72]) + hdr + self.records
            cs = sum(body) & 0xFF
            if self.bad_cs:
                cs = (cs + 1) & 0xFF
            reply = bytes([0x68, len(body), len(body), 0x68]) + body + bytes([cs, 0x16])
        if reply is not None:
            self._send(math.ceil(end + self.delay * self.tpb), reply)

    def _send(self, t0, data):
        ts, vs, lvl, x = [], [], 1, float(t0)
        for k, b in enumerate(data):
            if self.pause is not None and k == self.pause[0] and len(data) > 1:
                x += self.pause[1] * self.tx_tpb
            par = 0
            bits = [0]
            for j in range(8):
                bit = (b >> j) & 1
                par ^= bit
                bits.append(bit)
            if self.bad_parity is not None and k == self.bad_parity:
                par ^= 1
            bits += [par, 1]
            for j, v in enumerate(bits):
                if v != lvl:
                    ts.append(math.ceil(x + j * self.tx_tpb))
                    vs.append(v)
                    lvl = v
            x += 11 * self.tx_tpb
        self._txt, self._txv, self._txi = ts, vs, 0
        self._wake = min(self._wake, ts[0]) if ts else self._wake


def _selftest(verbose: bool = True) -> bool:
    import time
    from .board import Board

    fails: list[str] = []

    def check(cond, what):
        if not cond:
            fails.append(what)
            if verbose:
                print("FAIL:", what)

    # --- helpers against the document's own examples (5.2, 6.2, 6.3, 6.4)
    var_ex = bytes.fromhex("68 1F 1F 68 08 02 72 78 56 34 12 24 40 01 07 55 00 00 00"
                           " 03 13 15 31 00 DA 02 3B 13 01 8B 60 04 37 18 02 18 16")
    data = var_ex[7:-2]
    check(long_frame(0x08, 0x02, 0x72, data) == var_ex, "6.3 variable data example frame")
    p = parse(var_ex)
    check(p["valid"] and p["ident"] == 12345678 and p["manufacturer"] == 0x4024
          and p["manufacturer_str"] == "PAD" and p["version"] == 1 and p["medium"] == 7
          and p["access_no"] == 0x55 and p["status"] == 0 and p["signature"] == 0
          and p["records"] == var_ex[19:-2], f"6.3 example parse {p}")
    fix_ex = bytes.fromhex("68 13 13 68 08 05 73 78 56 34 12 0A 00 E9 7E"
                           " 01 00 00 00 35 01 00 00 3C 16")
    check(long_frame(0x08, 0x05, 0x73, fix_ex[7:-2]) == fix_ex, "6.2 fixed data example frame")
    p = parse(fix_ex)
    check(p["valid"] and p["ident"] == 12345678 and p["access_no"] == 10
          and p["medium_unit"] == 0x7EE9, f"6.2 example parse {p}")
    check(long_frame(0x53, 0xFE, 0xBD) == bytes.fromhex("68 03 03 68 53 FE BD 0E 16"),
          "6.4.1 baud rate switch example")
    check(long_frame(0x53, 0xFE, 0x50, b"\x10") == bytes.fromhex("6804046853FE5010B116"),
          "6.1 application reset example")
    check(short_frame(0x7B, 5) == bytes([0x10, 0x7B, 0x05, 0x80, 0x16]), "short frame")
    check(parse(b"\xe5")["kind"] == "E5" and parse(b"\xe5")["valid"], "E5 parse")
    for bad in (b"\x10\x7b\x05\x81\x16", b"\x68\x03\x04\x68\x53\x05\x51\xa9\x16",
                long_frame(0x53, 5, 0x51)[:-1], b"\x11"):
        check(not parse(bad)["valid"], f"parse rejects {bad.hex()}")

    own = 5
    records = bytes.fromhex("0313153100DA023B1301")

    def run(baud=2400, tpb=41.7, tick_ns=None, script=(), slave_kw=None, master_kw=None,
            slave_rate=1.0, address=own):
        tick_ns = tick_ns or 1e9 / (baud * tpb)
        mk = dict(master_kw or {})
        m = MbusMaster(to_slave=0, from_slave=1, tick_ns=tick_ns, baud=baud,
                       telegrams=list(script), start_ms=1.0, slave_address=address, **mk)
        s = _SimSlave(0, 1, m.tpb * (1 + mk.get("rate_error_ppm", 0) * 1e-6) * slave_rate,
                      address, records=records, **(slave_kw or {}))
        board = Board(m, s, record=False)
        limit = 10 ** 8
        t = 0
        while not m.done and t < limit:
            board(t, 0, 0)
            t += 1
        for _ in range(int(60 * m.tpb)):        # look for stray characters after the script
            board(t, 0, 0)
            t += 1
        m.finish()
        return m, s, t

    def legal_script(a):
        other = 7 if a != 7 else 8
        return [short_frame(0x40, a),
                long_frame(0x73, a, 0x51, bytes([0x01, 0x7A, 0x09])),
                short_frame(0x7B, a), short_frame(0x5B, a), short_frame(0x7B, 254),
                short_frame(0x40, 255), short_frame(0x5B, other), short_frame(0x7B, 254)]

    kinds = ["E5", "E5", "long", "long", "long", None, None, "long"]
    for baud, a in ((300, own), (2400, own), (9600, own), (2400, 0), (38400, 250)):
        m, s, _ = run(baud=baud, script=legal_script(a), address=a)
        ex = m.exchanges
        check(not m.errors(), f"legal slave {baud} Bd addr {a}: {m.errors()[:3]}")
        check([e["reply_kind"] for e in ex] == kinds, f"legal {baud}/{a} kinds "
              f"{[e['reply_kind'] for e in ex]}")
        for e in ex:
            if e["reply"]:
                check(e["reply_valid"] and 14.5 < e["reply_delay_bits"] < 16.5,
                      f"legal {baud}/{a} delay {e['reply_delay_bits']}")
            if e["reply_kind"] == "long":
                q = e["parsed"]
                check(q and q["A"] == a and q["CI"] == 0x72 and q["ident"] == 12345678
                      and q["manufacturer_str"] == "PAD" and q["records"] == records,
                      f"legal {baud}/{a} parsed {q}")
        if verbose:
            print(f"legal slave {baud:5d} Bd addr {a:3d}: {len(ex)} exchanges, "
                  f"{len(m.errors())} violations")

    # --- at the real tick (40 ns) for speed
    t0 = time.time()
    m, s, nt = run(baud=9600, tick_ns=40.0, script=[short_frame(0x40, own), short_frame(0x7B, own)])
    dt = time.time() - t0
    check(not m.errors() and [e["reply_kind"] for e in m.exchanges] == ["E5", "long"],
          f"40 ns run {m.errors()[:3]}")
    if verbose:
        print(f"40 ns tick, 9600 Bd: {nt} ticks in {dt:.2f} s ({nt / dt / 1e6:.2f} Mtick/s "
              "with the Board and the simulated slave)")

    # --- tolerance: legal timing variations accepted
    for name, kw in (("master clock +1.5 %", dict(master_kw={"rate_error_ppm": 15000})),
                     ("master clock -1.5 %", dict(master_kw={"rate_error_ppm": -15000})),
                     ("slave 2 % slow", dict(slave_rate=1.02)),
                     ("slave 2 % fast", dict(slave_rate=0.98)),
                     ("TSS721 echo", dict(master_kw={"echo": True})),
                     ("slave reply 11.5 bits", dict(slave_kw={"delay_bits": 11.5})),
                     ("slave reply near the window end",
                      dict(slave_kw={"delay_bits": 330 + 50 * 2400 / 1000 - 2}))):
        m, s, _ = run(script=[short_frame(0x40, own), short_frame(0x7B, own),
                              long_frame(0x53, own, 0x51)], **kw)
        check(not m.errors() and all(e["reply_valid"] for e in m.exchanges),
              f"{name}: {m.errors()[:3]}")
        if verbose:
            print(f"accepted: {name}")

    # --- illegal slaves
    base = [short_frame(0x40, own), short_frame(0x7B, own)]
    for name, kw, script, expect in (
            ("wrong parity", {"bad_parity": 3}, base, "odd parity"),
            ("pause between characters", {"pause": (5, 2.0)}, base, "pause of"),
            ("reply 5 bit times after the telegram", {"delay_bits": 5}, base, "at least 11"),
            ("reply to address 255", {"reply_255": True}, [short_frame(0x40, 255)], "255"),
            ("wrong check sum", {"bad_cs": True}, base, "check sum"),
            ("no answer to SND_NKE", {"no_nke": True}, base, "no reply to a valid SND_NKE"),
            ("A field 254 instead of its own", {"a_field": 254}, [short_frame(0x7B, 254)],
             "own primary address"),
            ("reply after the window", {"delay_bits": 330 + 120 + 5}, base, "outside any reply"),
            ("Space while the master sends", {"talk_during": True}, base, "while the master"),
            ("slave sends 6 % fast", {"tx_rate": 0.94}, base, "middle half"),
            ("replies to a faulty telegram", {"lax": True}, base, "faulty telegram")):
        mk = {"faults": {"checksum": 1}} if name == "replies to a faulty telegram" else None
        m, s, _ = run(script=script, slave_kw=kw, master_kw=mk)
        errs = m.errors()
        check(any(expect in e for e in errs), f"illegal slave '{name}': expected '{expect}' "
              f"in {errs[:4]}")
        if verbose:
            print(f"rejected: {name}: {errs[0] if errs else 'NO VIOLATION'}")

    # retries (5.4): a silent slave gets the telegram three times
    m, s, _ = run(script=[short_frame(0x40, own)], slave_kw={"no_nke": True},
                  master_kw={"retries": 2})
    check(len(m.exchanges) == 3 and [e["attempt"] for e in m.exchanges] == [0, 1, 2],
          f"retries: {[e['attempt'] for e in m.exchanges]}")

    # --- master faults: the legal slave must not answer, and must answer the next telegram
    snd = long_frame(0x53, own, 0x51, bytes([0x01, 0x7A, 0x09]))
    req = short_frame(0x7B, own)
    for name, tel, f in (("parity", req, {"parity": (0, 2)}), ("parity", snd, {"parity": (0, 6)}),
                         ("stop", req, {"stop": (0, 2)}), ("stop", snd, {"stop": (0, 8)}),
                         ("checksum", req, {"checksum": 0}), ("checksum", snd, {"checksum": 0}),
                         ("stop_char", req, {"stop_char": 0}), ("stop_char", snd, {"stop_char": 0}),
                         ("length", snd, {"length": 0}),
                         ("pause", req, {"pause": (0, 2, 3.0)}), ("pause", snd, {"pause": (0, 5, 1.0)}),
                         ("pause", snd, {"pause": (0, 3, 20.0)})):
        m, s, _ = run(script=[tel, req], master_kw={"faults": f})
        ex = m.exchanges
        check(not m.errors(), f"fault {f}: {m.errors()[:3]}")
        check(ex[0]["reply"] == b"" and ex[0]["fault"] == {name: f[name]},
              f"fault {f}: reply {ex[0]['reply']} fault {ex[0]['fault']}")
        check(ex[1]["reply_valid"] and ex[1]["reply_kind"] == "long", f"fault {f}: no recovery")
        if verbose:
            print(f"master fault {f} on {'SND_UD' if tel is snd else 'REQ_UD2'}: no reply, "
                  "next telegram answered")

    if verbose:
        print("M-Bus self-test:", "PASS" if not fails else f"{len(fails)} FAILURES")
    return not fails
