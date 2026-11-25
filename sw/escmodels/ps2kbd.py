"""PS/2 keyboard model (the DEVICE) on the keyboard port; the chip is the SYSTEM (host).

Sources (IBM, public):
  [CI] "Personal System/2 Hardware Interface Technical Reference: Common Interfaces"
       (84F9735, Oct 1990), "Keyboard/Auxiliary Device Controller": "Auxiliary Device and
       System Timings" (Figure 7 bit definitions), "System Receiving Data" steps 1-7 with
       Figure 8 (T1-T5), "System Sending Data" steps 1-8 with Figure 9 (T7-T9), "Signals"
       (Figure 10: open-collector drivers with pull-ups).
  [KB] "Keyboards (101- and 102-Key)" Hardware Interface Technical Reference (Oct 1990):
       "Clock and Data Signals", "Data Stream" (Figure 18), "Data Output", "Data Input",
       "Power-On Routine" (BAT), "Commands from the System" (Figure 1 and the command
       texts), "Commands to the System" (Figure 6 and the command texts).

Electrical model ([CI] Signals, [KB] Clock and Data Signals): both lines are open
collector with pull-ups; this model only pulls CLK or DATA low or releases them
(drive_high_mask is always 0), so a line reads low when either side pulls it. Like every
board device it sees the bus level of the previous tick (board.py). It knows its own drive
on that tick, so "the host is pulling X low" means: X reads low while this model released
X. While the keyboard itself holds a line low, a host pull on it is invisible (as on a
real wire) until the keyboard releases it.

Frame ([CI] Figure 7, [KB] Figure 18): 11 bits, start 0, 8 data bits LSB first, odd
parity (data + parity have an odd number of 1s), stop 1.

Constructor
-----------
Ps2Keyboard(clk=0, data=1, tick_ns=40.0, send=(), timing="typ", seed=0, reply_us=1000.0,
            bat_ms=1.0, faults=None, *, poll_us=100.0, idle_us=50.0, stall_us=2000.0,
            power_on=False, clear_buffer=True)

  clk, data   pin numbers of the keyboard CLK and DATA lines on the 8-bit bus.
  send        [(t_us, byte), ...] scan codes the keyboard sends on its own, kept in the
              given order; each is sent at or after t_us, when the keyboard may send
              ([KB] Data Output: CLK and DATA both high, not inhibited, no request to
              send) and is scanning, and only when no response to a command is pending
              (responses go first).
  timing      "min" | "max" | "typ" | "random": how the keyboard picks its own clock
              timing inside the Figure 8/9 limits (see Timing below).
  seed        seed of the "random" timing.
  reply_us    delay from accepting a host byte (end of its line-control bit) to the
              earliest start of the first byte of its response. Must be <= 20000:
              [KB] Commands from the System "The keyboard responds within 20
              milliseconds"; [KB] Data Input "will respond within 20 milliseconds".
              Further bytes of one response (Read ID's AB 83) follow with only the
              idle gap below ([KB] Read ID: the second byte follows the first within
              500 us unless the host inhibits).
  bat_ms      BAT duration after a Reset command. [KB] Power-On Routine gives 300-500 ms
              (completion code 300-500 ms after a Reset is acknowledged); the default
              1 ms is SHORTENED for simulation. During the BAT the keyboard releases both
              lines and ignores them ([KB] Power-On Routine, Clock and Data Signals);
              the LEDs (`leds`) are 7 during the BAT and 0 after it.
  faults      see Faults below.
  poll_us     ([KB] Data Input "checks the state of the 'clock' line at intervals of no
              more than 10 milliseconds") the keyboard looks for a request to send every
              poll_us while idle (and at once when it has something to send); must be
              <= 10000.
  idle_us     the keyboard starts a byte only after CLK and DATA have both been high for
              idle_us. Default 50 us = [CI] Figure 8 T5 (the system may inhibit up to
              50 us after clock 11 and still prevent the next transmission); the same
              wait is used after any inhibit or request ends.
  stall_us    with a "stall" fault, how long the keyboard stays silent before it sends
              the stalled byte again from the start.
  power_on    True: start with a BAT (bat_ms) and send AA, as after power-on. Default
              False: the keyboard is already powered, enabled and idle at t=0.
  clear_buffer  True (the text): commands F0, F4, F5, F6, F7-FD and FF clear the output
              buffer: scan codes already due (t_us passed) but not yet sent completely
              are dropped and listed in `dropped`. False: never drop scan codes.

Timing (the keyboard's own clock, all in whole ticks inside the limits)
------
Keyboard to system ([CI] Figure 8): T1 DATA change to CLK fall 5-25 us; T2 CLK rise to
DATA change 5 us to T4-5 us; T3 CLK low 30-50 us; T4 CLK high 30-50 us. Per bit the model
picks T3, T4 and T1 (T1 <= T4 - 5 us) and sets T2 = T4 - T1.
  "min": T3 = T4 = 30, T1 = 5 (T2 = 25)       "max": T3 = T4 = 50, T1 = 25 (T2 = 25)
  "typ": T3 = T4 = 40, T1 = 15 (T2 = 25)      "random": each uniform over the ticks in
  its limits, drawn per bit from random.Random(seed).
System to keyboard ([CI] Figure 9): T7 CLK low 30-50 us, T8 CLK high 30-50 us, T9 CLK
rise to DATA sample 5-25 us: min 30/30/5, max 50/50/25, typ 40/40/15, random per bit.
The first CLK fall of a host byte comes no sooner than T8 after the host released CLK.

Keyboard to system ([KB] Data Output; [CI] System Receiving Data)
------------------
Before a byte: CLK low -> keep the data (inhibited); CLK high and DATA low -> receive the
host's byte first; both high for idle_us -> send. DATA is set T1 before each CLK fall and
changed T2 after each rise. The keyboard watches CLK on every tick that it has released
CLK ([KB]: "at least every 60 milliseconds"; [CI] step 3: "intervals not exceeding 100
microseconds"). Host CLK low seen before the keyboard drives the 10th CLK fall (the
leading edge of the 10th clock, the parity bit) is LINE CONTENTION ([KB] Data Output;
[CI] step 3 "The system can terminate transmission anytime during the first 10 clock
cycles"): legal; the keyboard releases both lines, records it in `contentions` and in
`sent` (complete False), keeps the byte and sends it again later. From the 10th CLK fall
up to the 11th CLK fall the host must not pull CLK low ([KB] Data Input "the system must
receive the transmission"; [KB] Data Output "the keyboard completes the transmission"):
violation, and the keyboard completes the byte. From the 11th CLK fall on (stop bit
presented) the host may inhibit ([CI] Figure 8 T5 > 0 after clock 11; step 5).

System to keyboard ([KB] Data Input; [CI] System Sending Data)
------------------
A request to send (RTS) is the host holding CLK low and then releasing CLK with DATA
already low. On detecting CLK high + DATA low the keyboard clocks 11 bits: it samples
DATA T9 after each of the first 10 CLK rises (bits 0-7, parity, stop). If the stop bit is
1 it pulls DATA low (the line-control bit) right after sampling it, clocks the 11th
clock, and releases DATA T9 after the 11th rise. If the stop bit is 0 it is a framing
error: it keeps clocking until it samples DATA high, then gives the line-control bit and
answers FE (Resend); after 32 extra clocks it gives up. A parity error is answered with
FE (Resend) ([KB] Commands to the System, Resend) and is recorded, not a violation.

Commands answered ([KB] Commands from the System / Commands to the System)
-----------------
  FF Reset            FA; clears the output buffer; the keyboard is disabled until the
                      host accepts the ACK by keeping CLK and DATA high for 500 us; then
                      BAT (bat_ms), then AA; then enabled, set 2, typematic 2B, LEDs off.
                      Another command before the ACK is accepted overrides the Reset
                      (a Resend instead repeats the FA and keeps the Reset pending).
  FE Resend           the previous output again (the last byte the keyboard sent
                      completely, never its own FE: "unless the previous output was the
                      Resend command ..."), put in front of any pending bytes; it keeps
                      its kind and is marked "resend". Does not cancel a pending value
                      byte wait.
  EE Echo             EE.
  F2 Read ID          FA AB 83 (low byte first).
  ED Set indicators   FA, then the option byte (< 80) -> FA, `leds` = value & 7. Scanning
                      stops until the option byte.
  F3 Set typematic    FA, then the value byte (< 80) -> FA, `typematic` = value.
  F0 Select scan set  FA, then option 1-3 -> FA (`scan_set`), 0 -> FA and the set number,
                      other values < 80 -> FE (still waiting).
  F4 Enable           FA, clear buffer, scanning on.
  F5 Default disable  FA, clear buffer, typematic default, scanning off.
  F6 Set default      FA, clear buffer, typematic default (scanning state unchanged).
  F7-FA Set all keys  FA, clear buffer.
  FB-FD Set key type  FA, clear buffer; following bytes < ED are key ids, each -> FA.
  EF, F1 invalid      FE, no other change ([KB] Invalid Command).
  any other byte      (< ED with no value byte expected) FE: "The keyboard issues a
                      Resend command following receipt of an invalid input".
While a value byte is awaited (ED/F3/F0), a byte >= 80 is a new command: the wait ends
with no change and the new command is processed ([KB] Set/Reset Status Indicators and
Set Typematic Rate/Delay texts); it is noted in `notes`, not a violation. Any new command
(not FE, EF, F1) discards the still pending response bytes of the previous command
([KB] Acknowledge: "If the keyboard is interrupted while sending ACK, it discards ACK and
accepts and responds to the new command"), except the BAT's AA once the BAT has begun.
Mode 1 and scan code generation are not modelled.

Faults (bytes counted from 0)
------
  {"parity": i}         the keyboard's i-th transmission has wrong parity.
  {"stop": i}           the i-th transmission has stop bit 0 (DATA released T2 after the
                        11th rise).
  {"stall": (i, bit)}   in the i-th transmission the keyboard stops after `bit` complete
                        clocks (0-10), releases both lines, stays silent stall_us, then
                        sends the byte again from the start (a new transmission).
  {"no_ack": j}         the j-th byte from the host (index in `received`) is clocked in
                        but gets no line-control bit (DATA stays released during the 11th
                        clock); the keyboard drops that byte (no response).
  {"ignore_rts": j}     the keyboard does not clock in the j-th host request to send
                        (requests counted from 0 as the keyboard detects them, ignored
                        ones included); it waits until the host lowers CLK again or
                        releases DATA. Listed in `ignored`.
  "Transmission i" is the index in `sent`: every start counts, aborted ones and resends
  included. Each value may be an int or a list of ints (a list of tuples for "stall").

Records
-------
  sent       [{"t": tick of the first CLK fall (the start tick if aborted before it),
               "t_start": tick DATA went low for the start bit, "byte", "complete": False
               if aborted by line contention or stalled, "kind": "scan" | "reply"},
               plus "resend": True for a Resend's repeat, "stalled": True]
  received   [{"t": tick of the first CLK fall, "t_rts": tick the host released CLK,
               "byte", "parity_ok", "framing_ok", "acked", "rts_low_us": how long CLK was
               low before the release (None if DATA went low only after CLK was
               released)}, plus "aborted": True (byte None) if the host broke it off]
  inhibits   [(t_start, t_end)] host CLK-low periods that ended with DATA high.
  contentions [(t, clocks)] line contention aborts: tick and CLK falls already given.
  dropped    [(t, byte)] scan codes dropped by an output buffer clear.
  ignored    [t] requests ignored by the ignore_rts fault.
  notes      [(t, text)] legal but notable events.
  violations [str] "t=<tick>: <what> (<reference>)"; errors() returns them all;
             finish() adds the end-of-run checks and returns them.
  leds, typematic, scan_set, enabled: keyboard state.
Ticks of the keyboard's own edges are the ticks it drives them; ticks of host events are
the step ticks at which the model sees them (one tick after the bus level, board.py).
A CLK-low period that began while the keyboard itself held CLK low is measured from the
keyboard's own fall (the host's pull is invisible before), so rts_low_us can only err
long, never flag a legal host.

Checks on the host (each appends a violation with the tick and reference)
------
  1. RTS: CLK held low less than 60 us before the release ([KB] Data Input: "forces the
     keyboard 'clock' line to an inactive level for more than 60 microseconds").
  2. RTS: DATA not low before CLK is released ([KB] Data Input "When the system is
     ready to send the start bit ('data' line will be inactive), it allows the 'clock'
     line to go to an active (high) level"; [CI] System Sending Data step 3, Figure 9
     (1)-(3)); and DATA pulled low with CLK high without a CLK-low request first.
  3. Host DATA changing while CLK is high more than 1 us after the keyboard's CLK rise,
     during a host byte (start bit included) ([CI] System Sending Data steps 4-5: the
     system places each bit when the device sets CLK inactive; "Data must be stable
     within 1 microsecond after the rising edge of the 'clock' line"; Figure 9).
  4. Host stop bit 0 ([CI] Figure 7 / [KB] Figure 18 "Stop bit (always 1)"; [CI] step 6).
  5. Host holding DATA low after the keyboard releases its line-control bit ([CI] System
     Sending Data steps 6-7; [KB] Data Input: the line-control bit "signals the system
     that the keyboard has received its data; ... the system returns to a ready state").
     A host releasing DATA early (a 0 bit cut short) is caught by check 3.
  6. Host pulling CLK low while the keyboard clocks in a host byte, before the 11th
     clock's rise ([CI] System Sending Data steps 4-8: only after the line-control bit
     "the system can pull the 'clock' line inactive"); the keyboard drops the byte.
  7. Host pulling CLK low between the 10th and 11th CLK falls of a keyboard byte
     ([KB] Data Input "If the keyboard transmission is beyond the 10th 'clock' signal,
     the system must receive the transmission"; [CI] System Sending Data step 1).
  8. Host pulling DATA low during a keyboard byte while CLK is not held low by the host
     (seen on a released-CLK tick, or during the keyboard's CLK low followed by a CLK
     rise) ([KB] Data Output / Data Input: the system stops a transmission only by
     forcing CLK low, then sends; [KB] Clock and Data Signals).
  9. A new host byte (request to send) before any response byte to the previous host
     byte was sent completely ([KB] Data Input "Each system command or data
     transmission to the keyboard requires a response from the keyboard before the
     system can send its next output"). Bytes that get no response by design (no_ack
     fault, Resend with nothing to repeat) are exempt.
 10. ED or F3 (or F0) whose value byte never came by finish() ([KB] Set/Reset Status
     Indicators, Set Typematic Rate/Delay: the keyboard "waits for the option byte").
 11. ED option byte with reserved bits 7-3 set ([KB] Figure 4 "Reserved (must be 0's)");
     F3 value with bit 7 set ([KB] Set Typematic Rate/Delay "Bit 7 ... is always 0").
 12. Resend (FE) from the host before the keyboard ever sent a byte ([KB] Resend: "It is
     sent only after a keyboard transmission").
Line contention before the 10th clock, inhibits at any time outside the windows above,
and parity errors in host bytes are legal and recorded only.
"""

from __future__ import annotations

import math
import random

from .board import Board, Device

_KB_IN = "KB Data Input"
_KB_OUT = "KB Data Output"
_CI_SEND = "CI System Sending Data"
_CI_RECV = "CI System Receiving Data"


def _ints(v):
    if v is None:
        return set()
    return {v} if isinstance(v, int) else set(v)


class Ps2Keyboard(Device):
    def __init__(self, clk=0, data=1, tick_ns=40.0, send=(), timing="typ", seed=0,
                 reply_us=1000.0, bat_ms=1.0, faults=None, *, poll_us=100.0, idle_us=50.0,
                 stall_us=2000.0, power_on=False, clear_buffer=True):
        if timing not in ("min", "max", "typ", "random"):
            raise ValueError(f"timing {timing!r}: min, max, typ or random")
        if not 0 <= reply_us <= 20000:
            raise ValueError("reply_us must be 0-20000 (KB: the keyboard responds within 20 ms)")
        if not 0 < poll_us <= 10000:
            raise ValueError("poll_us must be 0-10000 (KB Data Input: checks within 10 ms)")
        self.clk, self.data = clk, data
        self._cm, self._dm = 1 << clk, 1 << data
        self.tick_ns = tick_ns
        u = self._u = 1000.0 / tick_ns
        self.timing = timing
        self._rng = random.Random(seed)
        self._reply_t = round(reply_us * u)
        self._bat_t = round(bat_ms * 1000.0 * u)
        self._poll_t = max(1, round(poll_us * u))
        self._idle_t = round(idle_us * u)
        self._stall_t = round(stall_us * u)
        self._acc_t = round(500.0 * u)          # Reset: ACK accepted after 500 us high
        self._rts_min = 60.0 * u                # Data Input: CLK low > 60 us
        self._us1 = 1.0 * u                     # CI step 5: stable within 1 us
        self.clear_buffer = clear_buffer

        def lim(lo, hi):
            return math.ceil(lo * u - 1e-6), math.floor(hi * u + 1e-6)
        self._L3050 = lim(30, 50)
        self._L525 = lim(5, 25)
        self._t5 = self._L525[0]
        self._T40 = round(40 * u)
        self._T15 = round(15 * u)

        f = dict(faults or {})
        bad = set(f) - {"parity", "stop", "stall", "no_ack", "ignore_rts"}
        if bad:
            raise ValueError(f"unknown faults {sorted(bad)}")
        self._f_parity = _ints(f.get("parity"))
        self._f_stop = _ints(f.get("stop"))
        st = f.get("stall")
        if st is None:
            st = []
        elif isinstance(st, tuple) and len(st) == 2 and isinstance(st[0], int):
            st = [st]
        self._f_stall = {int(i): int(b) for i, b in st}
        for b in self._f_stall.values():
            if not 0 <= b <= 10:
                raise ValueError("stall bit must be 0-10")
        self._f_noack = _ints(f.get("no_ack"))
        self._f_ignore = _ints(f.get("ignore_rts"))

        # records
        self.sent: list[dict] = []
        self.received: list[dict] = []
        self.inhibits: list[tuple[int, int]] = []
        self.contentions: list[tuple[int, int]] = []
        self.dropped: list[tuple[int, int]] = []
        self.ignored: list[int] = []
        self.notes: list[tuple[int, str]] = []
        self.violations: list[str] = []
        # keyboard state
        self.leds = 0
        self.typematic = 0x2B          # 10.9 cps, 500 ms ([KB] default values)
        self.scan_set = 2
        self.enabled = True
        self._await = None             # "ED" | "F3" | "F0" | "KEY"
        self._reset_armed = False      # FF received, ACK not yet accepted
        self._reset_wait = None        # tick the FA of a Reset completed
        self._owed = False             # a host byte still waits for its first response
        self._last_out = None          # last completely sent byte (not our own FE)
        # output queues
        self._scan = [[round(tu * u), b & 0xFF] for tu, b in send]
        self._si = 0
        self._scan_e = None
        self._replies: list[dict] = []
        self._hold_until = 0
        # line tracking
        self._cl = self._dl = False    # our drive on the tick the bus shows
        self._pclk = self._pdat = 1
        self._phc = self._phd = False
        self._clow_t = 0
        self._chigh_t = 0
        self._hi_t = 0                 # both lines high since (None: not both high)
        self._rel = None               # (t release, low ticks | None) of a request
        self._rts_block = False
        self._nreq = 0
        self._next_poll = 0
        self._ddat_until = None        # release DATA at this tick (idle)
        self._ddat_lc = False          # ... it was a line-control bit
        self._chk_t = None
        self._finished = False
        # transfer state
        self._mode = "idle"
        self._ev = None
        self._ev_t = 0
        self._fall_t = 0
        self._k = 0
        if power_on:
            self._start_bat(0)

    # ------------------------------------------------------------------ helpers
    def _viol(self, t, what, ref):
        self.violations.append(f"t={t}: {what} ({ref})")

    def _pick(self, lo, hi, typ):
        m = self.timing
        if hi < lo:
            hi = lo
        if m == "min":
            return lo
        if m == "max":
            return hi
        if m == "typ":
            return min(max(typ, lo), hi)
        return self._rng.randint(lo, hi)

    def _p3050(self):
        return self._pick(self._L3050[0], self._L3050[1], self._T40)

    def _p525(self):
        return self._pick(self._L525[0], self._L525[1], self._T15)

    def _scanning(self):
        return self.enabled and not self._reset_armed and self._await not in ("ED", "F3")

    def _head(self, t):
        r = self._replies
        if r:
            return r[0] if t >= r[0]["due"] else None
        if self._si < len(self._scan) and t >= self._scan[self._si][0] and self._scanning():
            e = self._scan_e
            if e is None:
                e = self._scan_e = {"byte": self._scan[self._si][1], "kind": "scan", "resp": False}
            return e
        return None

    def _clear_buffer(self, t):
        if not self.clear_buffer:
            return
        while self._si < len(self._scan) and self._scan[self._si][0] <= t:
            self.dropped.append((t, self._scan[self._si][1]))
            self._si += 1
        self._scan_e = None

    def _reply(self, t, data, **flags):
        for i, b in enumerate(data):
            e = {"byte": b, "kind": "reply", "resp": True,
                 "due": t + (self._reply_t if i == 0 else 0)}
            e.update(flags)
            self._replies.append(e)
        self._owed = True

    # ------------------------------------------------------------------ step
    def step(self, t, bus):
        clk = (bus >> self.clk) & 1
        dat = (bus >> self.data) & 1
        hc = not clk and not self._cl          # the host holds CLK low
        hd = not dat and not self._dl          # the host holds DATA low
        mode = self._mode
        if clk != self._pclk:
            if not clk:
                self._clow_t = t
            else:
                self._chigh_t = t
                if self._phc:
                    self._host_release(t, hd)
        if clk and dat:
            if self._hi_t is None:
                self._hi_t = t
        else:
            self._hi_t = None
        if not clk or not hd:
            self._rts_block = False
            if clk:
                self._rel = None               # DATA high with CLK high: no request
        elif (not self._phd and self._rel is None and (mode == "idle" or mode == "bat")
              and self._chk_t is None):
            self._viol(t, "host pulled DATA low with CLK high without first holding CLK low"
                       " (the request to send needs CLK low > 60 us, then DATA low, then CLK"
                       " released)", _KB_IN)
            self._rel = (t, None)
        if mode == "idle":
            self._idle(t, clk, dat, hd)
        elif mode == "tx":
            self._txstep(t, clk, hc, hd)
        elif mode == "rx":
            self._rxstep(t, clk, dat, hc, hd)
        elif t >= self._bat_end:               # bat
            self._bat_done(t)
        self._pclk, self._pdat, self._phc, self._phd = clk, dat, hc, hd
        return (self._cm if self._cl else 0) | (self._dm if self._dl else 0), 0

    def _host_release(self, t, hd):
        low = t - self._clow_t
        if hd:
            if not self._phd:
                self._viol(t, "DATA went low only as CLK was released: the start bit must be"
                           " on DATA before the system releases CLK", _KB_IN + "; " + _CI_SEND
                           + " step 3, Figure 9")
            self._rel = (t, low)
        else:
            self.inhibits.append((self._clow_t, t))
            self._rel = None

    # ------------------------------------------------------------------ idle
    def _idle(self, t, clk, dat, hd):
        if self._ddat_until is not None:
            if t < self._ddat_until:
                return
            self._dl = False
            self._ddat_until = None
            if self._ddat_lc:
                self._chk_t = t + 1
            return
        if self._chk_t is not None:
            if t < self._chk_t:
                return
            self._chk_t = None
            if hd:
                self._viol(t, "host held DATA low after the keyboard's line-control bit",
                           _CI_SEND + " steps 6-7; " + _KB_IN)
                self._rts_block = True
        if self._reset_wait is not None and self._hi_t is not None:
            if t - max(self._hi_t, self._reset_wait) >= self._acc_t:
                self._start_bat(t)
                return
        e = self._head(t)
        poll = t >= self._next_poll
        if poll:
            self._next_poll = t + self._poll_t
        if (poll or e is not None) and clk and hd and not self._rts_block:
            self._rx_start(t)
            return
        if (e is not None and clk and dat and self._hi_t is not None
                and t - self._hi_t >= self._idle_t and t >= self._hold_until):
            self._tx_start(t, e)

    # ------------------------------------------------------------------ keyboard -> host
    def _tx_start(self, t, e):
        idx = len(self.sent)
        b = e["byte"]
        bits = [0] + [(b >> i) & 1 for i in range(8)]
        par = 1 ^ (sum(bits) & 1)
        if idx in self._f_parity:
            par ^= 1
        bits += [par, 0 if idx in self._f_stop else 1]
        rec = {"t": t, "t_start": t, "byte": b, "complete": False, "kind": e["kind"]}
        if e.get("resend"):
            rec["resend"] = True
        self.sent.append(rec)
        self._tx_e, self._tx_rec, self._bits = e, rec, bits
        self._stall_at = self._f_stall.get(idx)
        self._k = 0
        self._v10 = self._vdat = self._hdlow = False
        self._dl = True                                   # start bit
        self._ev = "F"
        self._ev_t = t + self._pick(self._t5, self._L525[1], self._T15)   # T1
        self._mode = "tx"

    def _txstep(self, t, clk, hc, hd):
        if hc:
            if self._k < 10:                              # line contention: legal
                self._cl = self._dl = False
                self.contentions.append((t, self._k))
                self._mode = "idle"
                return
            if self._k < 11 and not self._v10:
                self._v10 = True
                self._viol(t, f"host pulled CLK low after the 10th clock of keyboard byte"
                           f" 0x{self._tx_rec['byte']:02x}; the system must receive the"
                           f" transmission", _KB_IN + "; " + _KB_OUT + "; " + _CI_SEND + " step 1")
        elif hd:
            if self._cl:
                self._hdlow = True
            elif clk:
                self._tx_data_viol(t)
        if self._hdlow and not self._cl and clk:
            self._tx_data_viol(t)
        if t < self._ev_t:
            return
        ev = self._ev
        if ev == "F":
            if self._stall_at is not None and self._k == self._stall_at:
                self._cl = self._dl = False
                self._tx_rec["stalled"] = True
                self._hold_until = t + self._stall_t
                self._mode = "idle"
                return
            self._cl = True
            self._k += 1
            if self._k == 1:
                self._tx_rec["t"] = t
            self._ev = "R"
            self._ev_t = t + self._p3050()                # T3
        elif ev == "R":
            self._cl = False
            if self._k == 11:
                if self._dl:                              # stop-bit fault: release after T2
                    t4 = self._p3050()
                    t1 = self._pick(self._t5, min(self._L525[1], t4 - self._t5), self._T15)
                    self._ddat_until = t + t4 - t1
                    self._ddat_lc = False
                self._tx_done(t)
                return
            t4 = self._p3050()
            t1 = self._pick(self._t5, min(self._L525[1], t4 - self._t5), self._T15)
            self._ev = "D"
            self._ev_t = t + t4 - t1                      # T2
            self._fall_t = t + t4
        else:                                             # "D": next bit
            self._dl = not self._bits[self._k]
            self._ev = "F"
            self._ev_t = self._fall_t

    def _tx_data_viol(self, t):
        self._hdlow = False
        if not self._vdat:
            self._vdat = True
            self._viol(t, f"host drove DATA low during keyboard byte 0x{self._tx_rec['byte']:02x}"
                       f" (clock {self._k}) without holding CLK low",
                       _KB_OUT + "; " + _KB_IN + "; KB Clock and Data Signals")

    def _tx_done(self, t):
        self._tx_rec["complete"] = True
        e = self._tx_e
        if self._replies and self._replies[0] is e:
            self._replies.pop(0)
        elif e is self._scan_e:
            self._si += 1
            self._scan_e = None
        if not (e["kind"] == "reply" and e["byte"] == 0xFE):
            self._last_out = e
        if e.get("resp"):
            self._owed = False
        if e.get("reset") and self._reset_armed:
            self._reset_wait = t
        self._mode = "idle"

    # ------------------------------------------------------------------ host -> keyboard
    def _rx_start(self, t):
        j = self._nreq
        self._nreq += 1
        rel, self._rel = self._rel, None
        if j in self._f_ignore:
            self.ignored.append(t)
            self._rts_block = True
            return
        if rel is None:
            self._viol(t, "request to send (CLK high, DATA low) without the system first"
                       " holding CLK low", _KB_IN)
        elif rel[1] is not None and rel[1] < self._rts_min:
            self._viol(t, f"request to send: CLK held low {rel[1] / self._u:.2f} us, not more"
                       f" than 60 us", _KB_IN)
        if self._owed:
            self._viol(t, "host started a new byte before the keyboard's response to its"
                       " previous byte was sent", _KB_IN + " (each system command requires a"
                       " response before the system can send its next output)")
        rec = {"t": None, "t_rts": rel[0] if rel else t, "byte": None, "parity_ok": None,
               "framing_ok": None, "acked": False,
               "rts_low_us": None if rel is None or rel[1] is None else round(rel[1] / self._u, 3)}
        self.received.append(rec)
        self._rx_rec = rec
        self._noack = (len(self.received) - 1) in self._f_noack
        self._rbits = []
        self._k = 0
        self._lc = False
        self._vchg = False
        self._rise_t = self._chigh_t
        self._ev = "F"
        self._ev_t = max(t, self._chigh_t + self._p3050())   # T8 before the first fall
        self._mode = "rx"

    def _rxstep(self, t, clk, dat, hc, hd):
        if hc:
            self._viol(t, f"host pulled CLK low while the keyboard was clocking in its byte"
                       f" (after {self._k} clocks); byte dropped", _CI_SEND + " steps 4-8")
            self._cl = self._dl = False
            self._rx_rec["aborted"] = True
            self._rx_rec["framing_ok"] = False
            self._mode = "idle"
            return
        if (hd != self._phd and clk and not self._cl and not self._vchg
                and t - 1 - self._rise_t > self._us1):
            self._vchg = True
            self._viol(t, f"host DATA changed while CLK high, {(t - 1 - self._rise_t) / self._u:.2f}"
                       f" us after the rising edge (bit {len(self._rbits)}); data may change"
                       f" only while CLK is low and must be stable within 1 us after the rise",
                       _CI_SEND + " steps 4-5, Figure 9")
        if t < self._ev_t:
            return
        ev = self._ev
        if ev == "F":
            self._cl = True
            self._k += 1
            if self._k == 1:
                self._rx_rec["t"] = t
            self._ev = "R"
            self._ev_t = t + self._p3050()                # T7
        elif ev == "R":
            self._cl = False
            self._rise_t = t
            if self._lc:
                self._rx_done(t)
                return
            self._ev = "S"
            self._fall_t = t + self._p3050()              # T8
            self._ev_t = t + self._p525()                 # T9
        else:                                             # "S": sample DATA
            self._rbits.append(dat)
            n = len(self._rbits)
            if n >= 10 and dat:
                self._lc = True
                if not self._noack:
                    self._dl = True                       # line-control bit
            elif n == 10:
                self._viol(t, f"host stop bit 0 (framing error); the keyboard clocks on until"
                           f" DATA is high, then sends Resend",
                           _CI_SEND + " step 6; CI Figure 7; " + _KB_IN)
            elif n > 42:
                self._viol(t, "host held DATA low for 32 clocks after the stop bit; keyboard"
                           " gave up", _KB_IN)
                self._cl = self._dl = False
                self._rx_rec.update(byte=self._rx_byte(), framing_ok=False, acked=False)
                self._rx_rec["parity_ok"] = self._rx_par()
                self._mode = "idle"
                return
            self._ev = "F"
            self._ev_t = self._fall_t

    def _rx_byte(self):
        return sum(b << i for i, b in enumerate(self._rbits[:8]))

    def _rx_par(self):
        return (sum(self._rbits[:9]) & 1) == 1

    def _rx_done(self, t):
        b, pok = self._rx_byte(), self._rx_par()
        fok = len(self._rbits) == 10
        self._rx_rec.update(byte=b, parity_ok=pok, framing_ok=fok, acked=not self._noack)
        self._mode = "idle"
        if self._dl:
            self._ddat_until = t + self._p525()           # release after the 11th rise
            self._ddat_lc = True
        else:
            self._chk_t = t + 1
        if self._noack:
            self.notes.append((t, f"no_ack fault: host byte 0x{b:02x} not acknowledged, dropped"))
            return
        if not fok or not pok:
            self._reply(t, [0xFE])
            return
        self._command(t, b)

    # ------------------------------------------------------------------ commands
    def _command(self, t, b):
        if b == 0xFE:                                     # Resend
            lo = self._last_out
            if lo is None:
                self._viol(t, "host sent Resend (FE) before any keyboard transmission",
                           "KB Commands from the System, Resend")
                return
            e = {"byte": lo["byte"], "kind": lo["kind"], "resp": True, "resend": True,
                 "due": t + self._reply_t}
            if lo.get("reset") and self._reset_armed:
                e["reset"] = True
            self._replies.insert(0, e)
            self._owed = True
            return
        if b in (0xEF, 0xF1):                             # invalid commands
            self._reply(t, [0xFE])
            return
        aw = self._await
        if aw in ("ED", "F3", "F0") and b < 0x80:
            self._await = None
            if aw == "ED":
                if b & 0xF8:
                    self._viol(t, f"ED option byte 0x{b:02x}: bits 7-3 are reserved and must"
                               f" be 0", "KB Set/Reset Status Indicators, Figure 4")
                self.leds = b & 7
                self._reply(t, [0xFA])
            elif aw == "F3":
                self.typematic = b
                self._reply(t, [0xFA])
            elif b == 0:
                self._reply(t, [0xFA, self.scan_set])
            elif b <= 3:
                self.scan_set = b
                self._reply(t, [0xFA])
            else:
                self._await = "F0"
                self._reply(t, [0xFE])
            return
        if aw == "KEY" and b < 0xED:
            self._reply(t, [0xFA])
            return
        if b < 0xED:                                      # invalid input
            if aw == "F3":
                self._viol(t, f"F3 value byte 0x{b:02x}: bit 7 must be 0",
                           "KB Set Typematic Rate/Delay")
            self._reply(t, [0xFE])
            return
        # a new command
        if aw in ("ED", "F3", "F0"):
            self.notes.append((t, f"command 0x{b:02x} in place of the {aw} value byte;"
                               f" {aw} stopped with no change"))
        self._await = None
        self._replies = [e for e in self._replies if e.get("keep")]
        self._reset_armed = False
        self._reset_wait = None
        if b == 0xFF:
            self._clear_buffer(t)
            self._reset_armed = True
            self._reply(t, [0xFA], reset=True)
        elif b == 0xEE:
            self._reply(t, [0xEE])
        elif b == 0xF2:
            self._reply(t, [0xFA, 0xAB, 0x83])
        elif b in (0xED, 0xF3):
            self._await = "ED" if b == 0xED else "F3"
            self._reply(t, [0xFA])
        elif b == 0xF0:
            self._clear_buffer(t)
            self._await = "F0"
            self._reply(t, [0xFA])
        else:
            self._clear_buffer(t)
            if b == 0xF4:
                self.enabled = True
            elif b == 0xF5:
                self.enabled = False
                self.typematic = 0x2B
            elif b == 0xF6:
                self.typematic = 0x2B
            elif b >= 0xFB:                               # FB-FD: key ids follow
                self._await = "KEY"
            self._reply(t, [0xFA])

    # ------------------------------------------------------------------ BAT
    def _start_bat(self, t):
        self._mode = "bat"
        self._bat_end = t + self._bat_t
        self.leds = 7
        self._reset_armed = False
        self._reset_wait = None
        self._await = None
        self._clear_buffer(t)
        self._replies = [e for e in self._replies if e.get("keep")]
        self._cl = self._dl = False

    def _bat_done(self, t):
        self._mode = "idle"
        self.leds = 0
        self.enabled = True
        self.typematic = 0x2B
        self.scan_set = 2
        self._replies.append({"byte": 0xAA, "kind": "reply", "resp": False, "keep": True,
                              "due": t})

    # ------------------------------------------------------------------ results
    def finish(self, t=None):
        if not self._finished:
            self._finished = True
            if self._await in ("ED", "F3", "F0"):
                tt = t if t is not None else "end"
                self.violations.append(
                    f"t={tt}: the value byte for the {self._await} command never came"
                    f" (KB Set/Reset Status Indicators / Set Typematic Rate/Delay: the"
                    f" keyboard waits for the option byte)")
        return list(self.violations)

    def errors(self):
        return list(self.violations)


# ====================================================================== self-test
class _Host(Device):
    """Synthetic bit-banged system (host) for the self-test, driven by a generator script."""

    def __init__(self, clk=0, data=1, tick_ns=40.0, hold=(15.0, 200.0)):
        self.c, self.d = clk, data
        self.u = 1000.0 / tick_ns
        self.cl = self.dl = False
        self.hold = hold
        self.inh = (-1, -1)
        self.got = []            # (t, byte, parity_ok, stop_ok)
        self.bits = []
        self.sending = False
        self.clk = self.dat = 1
        self.pclk = self.pdat = 1
        self.d1 = self.d2 = False
        self.tfall = self.trise = self.tdat = 0
        self.nf = 0
        self.lows, self.highs, self.t1, self.t2 = [], [], [], []
        self.rx_lows, self.rx_highs = [], []
        self.acks = []
        self.gen = None
        self.t = 0

    def step(self, t, bus):
        self.t = t
        clk = (bus >> self.c) & 1
        dat = (bus >> self.d) & 1
        self.clk, self.dat = clk, dat
        if dat != self.pdat:
            self.tdat = t
        if clk != self.pclk and not (self.d1 or self.d2):
            if clk == 0:
                if self.sending:
                    if self.nf:
                        self.rx_highs.append(t - self.trise)
                    self.nf += 1
                else:
                    if self.bits and t - self.tfall > 150 * self.u:
                        self.bits = []                    # a stalled byte: drop it
                    if self.bits:
                        self.highs.append(t - self.trise)
                        if self.tdat > self.trise:
                            self.t1.append(t - self.tdat)
                            self.t2.append(self.tdat - self.trise)
                    elif t - self.tdat < 100 * self.u:
                        self.t1.append(t - self.tdat)     # start bit to first fall
                    self.bits.append(dat)
                    if len(self.bits) == 11:
                        b = self.bits
                        byte = sum(x << i for i, x in enumerate(b[1:9]))
                        self.got.append((t, byte, (sum(b[1:10]) & 1) == 1, b[10] == 1))
                        self.bits = []
                        if self.hold:
                            self.inh = (t + self.hold[0] * self.u, t + self.hold[1] * self.u)
                self.tfall = t
            else:
                if self.sending:
                    if self.nf:
                        self.rx_lows.append(t - self.tfall)
                elif self.bits:
                    self.lows.append(t - self.tfall)
                self.trise = t
        self.pclk, self.pdat = clk, dat
        if self.gen is not None:
            try:
                next(self.gen)
            except StopIteration:
                self.gen = None
        cl = self.cl or self.inh[0] <= t < self.inh[1]
        self.d2, self.d1 = self.d1, cl
        return (1 << self.c if cl else 0) | (1 << self.d if self.dl else 0), 0

    # script helpers (generators)
    def wait_us(self, us):
        end = self.t + us * self.u
        while self.t < end:
            yield

    def until(self, f, timeout_us=None):
        end = None if timeout_us is None else self.t + timeout_us * self.u
        while not f():
            if end is not None and self.t >= end:
                return False
            yield
        return True

    def edge(self, lvl, timeout_us=20000.0):
        prev = self.clk
        end = self.t + timeout_us * self.u
        while True:
            yield
            if self.clk == lvl and prev != lvl:
                return True
            prev = self.clk
            if self.t >= end:
                return False

    def send(self, b, rts_us=100.0, flip=False, late=False, timeout_us=20000.0):
        self.sending, self.bits, self.nf = True, [], 0
        self.cl = True
        yield from self.wait_us(rts_us - 10)
        self.dl = True
        yield from self.wait_us(10)
        self.cl = False
        bits = [(b >> i) & 1 for i in range(8)]
        p = 1 ^ (sum(bits) & 1) ^ (1 if flip else 0)
        for bit in bits + [p, 1]:
            ok = yield from self.edge(0, timeout_us)
            if not ok:
                self.dl = False
                self.sending = False
                self.acks.append(None)
                return None
            if late:
                yield from self.edge(1)
                yield from self.wait_us(5)
            self.dl = bit == 0
        if not late:
            yield from self.edge(1)
        yield from self.edge(0)
        ack = self.dat == 0
        yield from self.edge(1)
        self.acks.append(ack)
        self.sending = False
        return ack


def _run(kb, host, script, tick_ns=40.0, max_ms=100.0, after_us=3000.0):
    board = Board(kb, host, record=False)
    host.gen = script(host)
    u = 1000.0 / tick_ns
    end = None
    limit = int(max_ms * 1000 * u)
    t = 0
    while t < limit:
        board(t, 0, 0)
        if end is None and host.gen is None:
            end = t + int(after_us * u)
        if end is not None and t >= end:
            break
        t += 1
    kb.finish(t)
    return t < limit


def _selftest(verbose=True):
    ok = True
    fails = []

    def check(name, cond, info=""):
        nonlocal ok
        if not cond:
            ok = False
            fails.append(f"{name}: {info}")

    tick = 40.0
    u = 1000.0 / tick

    # ---------------- legal host, three timings
    for timing in ("min", "max", "random"):
        kb = Ps2Keyboard(0, 1, tick, send=[(100, 0x1C), (100, 0xF0), (100, 0x1C), (8000, 0x32)],
                         timing=timing, seed=7, reply_us=300.0, bat_ms=1.0)
        h = _Host(0, 1, tick)

        def legal(h):
            yield from h.until(lambda: len(h.got) >= 3)
            yield from h.until(lambda: len(h.bits) >= 3)      # interrupt 0x32 at clock 3
            yield from h.send(0xEE)
            yield from h.until(lambda: len(h.got) >= 5)
            n = 5
            for cmd, nresp, flip in ((0xFF, 2, False), (0xEE, 1, False), (0xF2, 3, False),
                                     (0xED, 1, False), (0x02, 1, False), (0xF4, 1, True),
                                     (0xF4, 1, False)):
                yield from h.wait_us(300)
                yield from h.send(cmd, flip=flip)
                n += nresp
                yield from h.until(lambda: len(h.got) >= n)

        done = _run(kb, h, legal, tick, max_ms=80)
        tag = f"legal/{timing}"
        check(tag + " finished", done)
        check(tag + " no violations", kb.violations == [], kb.violations[:5])
        exp_host = [0x1C, 0xF0, 0x1C, 0xEE, 0x32, 0xFA, 0xAA, 0xEE, 0xFA, 0xAB, 0x83,
                    0xFA, 0xFA, 0xFE, 0xFA]
        check(tag + " host bytes", [g[1] for g in h.got] == exp_host,
              [hex(g[1]) for g in h.got])
        check(tag + " host parity/stop", all(g[2] and g[3] for g in h.got))
        exp_sent = [0x1C, 0xF0, 0x1C, 0x32] + exp_host[3:]
        check(tag + " sent bytes", [s["byte"] for s in kb.sent] == exp_sent,
              [hex(s["byte"]) for s in kb.sent])
        check(tag + " sent complete", [s["complete"] for s in kb.sent] ==
              [True, True, True, False] + [True] * 12, [s["complete"] for s in kb.sent])
        check(tag + " sent kinds", [s["kind"] for s in kb.sent] ==
              ["scan"] * 4 + ["reply", "scan"] + ["reply"] * 10, [s["kind"] for s in kb.sent])
        check(tag + " contention", len(kb.contentions) == 1 and kb.contentions[0][1] == 3,
              kb.contentions)
        check(tag + " received", [r["byte"] for r in kb.received] ==
              [0xEE, 0xFF, 0xEE, 0xF2, 0xED, 0x02, 0xF4, 0xF4], [r["byte"] for r in kb.received])
        check(tag + " parity flags", [r["parity_ok"] for r in kb.received] ==
              [True] * 6 + [False, True])
        check(tag + " framing/acked", all(r["framing_ok"] and r["acked"] for r in kb.received))
        check(tag + " host saw acks", h.acks == [True] * 8, h.acks)
        check(tag + " rts > 60 us", all(r["rts_low_us"] is not None and r["rts_low_us"] > 60
                                        for r in kb.received), [r["rts_low_us"] for r in kb.received])
        check(tag + " inhibits", len(kb.inhibits) == len(h.got), (len(kb.inhibits), len(h.got)))
        check(tag + " leds", kb.leds == 2 and kb.enabled, kb.leds)
        # timing values (ticks); 30/50 us = 750/1250 ticks, 5/25 us = 125/625
        lo30, hi50, lo5, hi25 = 30 * u, 50 * u, 5 * u, 25 * u
        allv = h.lows + h.highs + h.rx_lows + h.rx_highs
        check(tag + " clock phases measured", len(h.lows) >= 150 and len(h.rx_lows) >= 80,
              (len(h.lows), len(h.rx_lows)))
        check(tag + " clock phases 30-50 us", all(lo30 - 1 <= v <= hi50 + 1 for v in allv),
              (min(allv), max(allv)))
        check(tag + " T1 5-25 us", all(lo5 - 1 <= v <= hi25 + 1 for v in h.t1),
              (min(h.t1), max(h.t1)))
        check(tag + " T2 >= 5 us", all(v >= lo5 - 1 for v in h.t2), min(h.t2))
        check(tag + " T1 measured", len(h.t1) > 50, len(h.t1))
        if timing == "min":
            check(tag + " exact", set(allv) == {750} and set(h.t1) == {125}
                  and set(h.t2) == {625}, (set(allv), set(h.t1), set(h.t2)))
        elif timing == "max":
            check(tag + " exact", set(allv) == {1250} and set(h.t1) == {625}
                  and set(h.t2) == {625}, (set(allv), set(h.t1), set(h.t2)))
        else:
            check(tag + " spread", len(set(allv)) > 50 and len(set(h.t1)) > 20)

    # ---------------- illegal hosts (typ)
    def bad(name, kbargs, script, key, hostargs=None):
        kb = Ps2Keyboard(0, 1, tick, **kbargs)
        h = _Host(0, 1, tick, **(hostargs or {}))
        _run(kb, h, script, tick, max_ms=30)
        hit = [v for v in kb.violations if key in v]
        check("illegal " + name, bool(hit), kb.violations)
        return kb, h

    def s_short(h):
        yield from h.wait_us(200)
        yield from h.send(0xEE, rts_us=40)
        yield from h.until(lambda: len(h.got) >= 1)
    bad("RTS < 60 us", {}, s_short, "not more than 60 us")

    def s_late(h):
        yield from h.wait_us(200)
        yield from h.send(0xEE, late=True)
        yield from h.until(lambda: len(h.got) >= 1, 5000)
    bad("data change with CLK high", {}, s_late, "changed while CLK high")

    def s_clk10(h):
        yield from h.until(lambda: len(h.bits) >= 10)
        h.cl = True
        yield from h.wait_us(100)
        h.cl = False
        yield from h.wait_us(500)
    kb, _ = bad("CLK low after 10th clock", {"send": [(100, 0x1C)]}, s_clk10,
                "after the 10th clock", {"hold": None})
    check("illegal CLK10 completes", kb.sent and kb.sent[0]["complete"], kb.sent)

    def s_data(h):
        yield from h.until(lambda: len(h.bits) >= 3)
        yield from h.edge(1)
        yield from h.wait_us(2)
        h.dl = True
        yield from h.wait_us(10)
        h.dl = False
        yield from h.wait_us(1500)
    bad("DATA low during keyboard output", {"send": [(100, 0xFF)]}, s_data,
        "drove DATA low during keyboard byte")

    def s_early(h):
        yield from h.wait_us(200)
        yield from h.send(0xEE)
        yield from h.send(0xF2)
        yield from h.until(lambda: len(h.got) >= 3, 10000)
    bad("new command before response", {}, s_early, "before the keyboard's response")

    def s_noval(h):
        yield from h.wait_us(200)
        yield from h.send(0xED)
        yield from h.until(lambda: len(h.got) >= 1)
    bad("ED without value", {}, s_noval, "never came")

    # ---------------- faults (legal host reacting)
    def s_resend_on_error(h):
        yield from h.until(lambda: len(h.got) >= 1)
        yield from h.wait_us(300)
        yield from h.send(0xFE)
        yield from h.until(lambda: len(h.got) >= 2)
    for fk in ("parity", "stop"):
        kb = Ps2Keyboard(0, 1, tick, send=[(100, 0x1C)], faults={fk: 0}, reply_us=200)
        h = _Host(0, 1, tick)
        _run(kb, h, s_resend_on_error, tick, max_ms=20)
        g = h.got
        check(f"fault {fk}", len(g) == 2 and g[0][1] == 0x1C and not (g[0][2] and g[0][3])
              and g[1][1] == 0x1C and g[1][2] and g[1][3] and kb.violations == []
              and kb.sent[1].get("resend") and kb.sent[1]["kind"] == "scan", (g, kb.violations))

    kb = Ps2Keyboard(0, 1, tick, send=[(100, 0x1C)], faults={"stall": (0, 4)})
    h = _Host(0, 1, tick)
    _run(kb, h, lambda h: h.until(lambda: len(h.got) >= 1), tick, max_ms=20)
    check("fault stall", [g[1] for g in h.got] == [0x1C] and not kb.sent[0]["complete"]
          and kb.sent[0].get("stalled") and kb.sent[1]["complete"] and kb.violations == [],
          (h.got, kb.sent, kb.violations))

    def s_noack(h):
        yield from h.wait_us(200)
        yield from h.send(0xEE)
        yield from h.wait_us(2000)
        yield from h.send(0xEE)
        yield from h.until(lambda: len(h.got) >= 1)
    kb = Ps2Keyboard(0, 1, tick, faults={"no_ack": 0}, reply_us=200)
    h = _Host(0, 1, tick)
    _run(kb, h, s_noack, tick, max_ms=20)
    check("fault no_ack", h.acks == [False, True] and [g[1] for g in h.got] == [0xEE]
          and [r["acked"] for r in kb.received] == [False, True] and kb.violations == [],
          (h.acks, h.got, kb.received, kb.violations))

    def s_ignore(h):
        yield from h.wait_us(200)
        yield from h.send(0xEE, timeout_us=3000)
        yield from h.wait_us(200)
        yield from h.send(0xEE)
        yield from h.until(lambda: len(h.got) >= 1)
    kb = Ps2Keyboard(0, 1, tick, faults={"ignore_rts": 0}, reply_us=200)
    h = _Host(0, 1, tick)
    _run(kb, h, s_ignore, tick, max_ms=20)
    check("fault ignore_rts", h.acks == [None, True] and len(kb.ignored) == 1
          and [g[1] for g in h.got] == [0xEE] and kb.violations == [],
          (h.acks, kb.ignored, h.got, kb.violations))

    if verbose:
        print("ps2kbd self-test:", "PASS" if ok else "FAIL")
        for f in fails:
            print("  ", f)
    return ok
