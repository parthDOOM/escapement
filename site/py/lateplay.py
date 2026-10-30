"""The site's "make it late" challenge: a visitor's program, assembled, certified and run
on the ISS like any playground program, then audited edge by edge.

The audit does not trust the simulator's queue. From the events the program queued (each
DRIVE's deadline, pins and levels) and the faults it took, it rebuilds what the pins must
show on every tick, by the rules of docs/11-SEMANTICS.md section 5: a DRIVE with deadline
d shows from tick d + 1, a fault withdraws its context's queued events and releases its
pins. Then it compares that with the pins the run produced, tick by tick. An edge that
came even one tick late, or early, would show as a difference.
"""
from __future__ import annotations

import json

import escplay
from esciss.machine import BANK_B, N_PHYS

DRIVE = 1
TICKS = 20_000

ATTEMPTS = [
    {"id": "wait", "expect": "ADMIT PAST", "title": "Wait, then emit",
     "blurb": "Wait until the deadline has come, then ask for an edge at it.",
     "source": """; Wait for tick 400, then ask for an edge at tick 400.
; The edge can no longer happen on time, so what does the chip do?
.entry 0, start
.own   0, 0

start:
    ADDT  400
    WAITT                 ; now >= 400
    EMIT  1, 1, 1         ; pin 0 high at tick 400: already in the past
    HALT
"""},
    {"id": "fast", "expect": "ADMIT PAST", "title": "Too fast to keep up",
     "blurb": "A square wave whose edges come quicker than the loop can queue them.",
     "source": """; A square wave on pin 0, one edge every DELAY ticks. The loop costs 20 ticks per
; period (five instructions, one every 4 ticks), so at DELAY 9 the program falls
; behind its own deadlines. Change DELAY to 10 and it keeps up.
.equ DELAY, 9
.entry 0, start
.own   0, 0

start:
    ADDT  20
    LDLC  40
loop:
    EMIT  1, 1, 1
    ADDT  DELAY
    EMIT  1, 0, 1
    ADDT  DELAY
    DJNZ  loop
    HALT
"""},
    {"id": "order", "expect": "ADMIT ORDER", "title": "Back in time",
     "blurb": "Queue an edge, then one earlier than it.",
     "source": """; Queue an edge at tick 1000, then try to slip one in at tick 500.
.entry 0, start
.own   0, 0

start:
    ADDT  1000
    EMIT  1, 1, 1         ; pin 0 high at tick 1000
    MOVI  r0, 500
    SETT  r0
    EMIT  1, 0, 1         ; pin 0 low at tick 500, before the queued edge
    HALT
"""},
    {"id": "crowd", "expect": "on time", "title": "Crowd it out",
     "blurb": "Four programs at once: three busy loops beside a square wave.",
     "source": """; Context 0 makes a square wave on pin 0. Contexts 1 to 3 do nothing but spin,
; branch and hammer the tape. Can they push context 0's edges back?
.entry 0, wave
.entry 1, spin
.entry 2, spin
.entry 3, spin
.own   0, 0

wave:
    ADDT  20
    LDLC  200
w:
    EMIT  1, 1, 1
    ADDT  13
    EMIT  1, 0, 1
    ADDT  10
    DJNZ  w
    HALT

spin:
    ADDI  r0, 7
    STT   r0, r1, 0
    LDT   r2, r1, 0
    ADDI  r1, 1
    TSTI  r0, 4
    JZ    spin
    JMP   spin
"""},
    {"id": "flood", "expect": "on time", "title": "Flood the queue",
     "blurb": "Queue edges as fast as possible, far ahead of time.",
     "source": """; Every instruction queues an edge 1,000 ticks apart, much faster than they fall
; due. The queue holds four; what happens to the fifth?
.entry 0, start
.own   0, 0

start:
    ADDT  1000
    LDLC  12
loop:
    EMIT  1, 1, 1
    ADDT  1000
    EMIT  1, 0, 1
    ADDT  1000
    DJNZ  loop
    HALT
"""},
    {"id": "far", "expect": "ADMIT PAST", "title": "Too far ahead",
     "blurb": "A deadline so far away that it wraps around the 32-bit clock.",
     "source": """; The tick counter is 32 bits and wraps after 171.8 s. Ask for an edge 2^31 + 1000
; ticks from now: is that in the future, or 1000 ticks in the past?
.entry 0, start
.own   0, 0

start:
    MOVI  r0, 1000
    MOVHI r0, 0x8000
    ADDTR r0
    EMIT  1, 1, 1
    HALT
"""},
    {"id": "steal", "expect": "ADMIT OWNER", "title": "Someone else's pin",
     "blurb": "Drive a pin that belongs to another program.",
     "source": """; Context 0 owns pin 0 and context 1 owns pin 1. Context 1 tries to drive pin 0.
.entry 0, a
.entry 1, b
.own   0, 0
.own   1, 1

a:
    ADDT  100
    EMIT  1, 1, 1
    HALT
b:
    ADDT  200
    EMIT  1, 0, 1         ; pin 0, not ours
    HALT
"""},
    {"id": "mine", "expect": "on time", "title": "Your own", "blurb": "Write anything.",
     "source": """; Make any pin edge happen later than its deadline. Four contexts, 13 pins,
; 53 instructions: docs/02-ISA.md. The run is 20,000 ticks (0.8 ms).
.entry 0, start
.own   0, 0

start:
    ADDT  20
    EMIT  1, 1, 1
    HALT
"""},
]
BY_ID = {a["id"]: a for a in ATTEMPTS}


def attempts() -> str:
    return json.dumps([{k: a[k] for k in ("id", "title", "blurb", "expect")} for a in ATTEMPTS])


def verdict(r: dict) -> str:
    """How a run ended: refused (the fault's cause), or every edge on time."""
    if r["fault"]:
        return (r["fault"]["cause"] + " " + r["fault"]["subcode"]).strip()
    return "LATE" if r["audit"]["late"] or r["audit"]["mismatches"] else "on time"


def source_of(aid: str) -> str:
    return BY_ID[aid]["source"]


def _phys(bank: int, v: int) -> int:
    return ((v & 0x1F) << BANK_B) if bank else (v & 0xFF)


def audit(m) -> dict:
    """What the pins should have shown, tick by tick, against what they showed."""
    image = m.image
    lu_pins = 0
    for p in range(N_PHYS):                      # a line unit owner's pins move on their own
        k = image.pin_owner[p]
        if image.pin_owned[p] and any(c["ctx"] == k and (c["insn"] >> 24) in (0x2D, 0x3C)
                                      for c in m.commits):
            lu_pins |= 1 << p
    faults = {}
    for c in m.commits:
        if c["outcome"] == "F" and c["ctx"] not in faults:
            faults[c["ctx"]] = c["t"]
    changes = []                                 # (tick it shows from, set_mask, val, oe)
    drives = []
    for c in m.commits:
        ev = c.get("ev")
        if not ev or ev[0] != DRIVE:
            continue
        k, d, mask, value, oe = c["ctx"], ev[1], ev[2], ev[3], ev[4]
        bank = image.ctx_bank[k]
        pm = _phys(bank, mask)
        rec = {"ctx": k, "deadline": d, "pins": pm, "lead": ev[7]}
        if k in faults and faults[k] <= d:
            rec["fate"] = "withdrawn"            # flushed by its context's fault
        elif d + 1 >= len(m.pins):
            rec["fate"] = "queued"               # still waiting when the run ended
        else:
            rec["fate"] = "due"
            changes.append((d + 1, pm, _phys(bank, value) & pm, _phys(bank, oe) & pm))
        drives.append(rec)
    for k, f in faults.items():                  # a fault releases its context's pins
        own = sum(1 << p for p in range(N_PHYS) if image.pin_owned[p] and image.pin_owner[p] == k)
        changes.append((f + 1, own, 0, 0, "release"))
    changes.sort(key=lambda x: x[0])

    val = oe = 0
    i = 0
    diffs = []
    od = image.pin_od
    keep = ~lu_pins
    for t, rec in enumerate(m.pins):
        while i < len(changes) and changes[i][0] <= t:
            ch = changes[i]
            if len(ch) == 5:                     # release: oe only
                oe &= ~ch[1]
            else:
                val = (val & ~ch[1]) | ch[2]
                oe = (oe & ~ch[1]) | ch[3]
            i += 1
        out = oe_a = 0
        for p in range(8):
            v, e = (val >> p) & 1, (oe >> p) & 1
            if od[p]:
                oe_a |= (e & (v ^ 1)) << p
            else:
                out |= v << p
                oe_a |= e << p
        ob = ((val >> BANK_B) | ~(oe >> BANK_B)) & 0x1F
        want = (out & oe_a, oe_a, ob)
        got = (rec["uio_out"] & rec["uio_oe"], rec["uio_oe"], rec["ob"])
        bad_a = ((want[0] ^ got[0]) | (want[1] ^ got[1])) & keep & 0xFF
        bad_b = (want[2] ^ got[2]) & (keep >> BANK_B) & 0x1F
        if bad_a or bad_b:
            diffs.append({"t": t, "pins": bad_a | (bad_b << BANK_B)})
            if len(diffs) >= 20:
                break
    due = [d for d in drives if d["fate"] == "due"]
    late = []
    for d in due:                                # an edge is late if its pins were off at d + 1
        t = d["deadline"] + 1
        if any(x["t"] == t and x["pins"] & d["pins"] for x in diffs):
            late.append(d)
    return {"ticks": len(m.pins), "edges": len(due), "late": len(late),
            "withdrawn": sum(d["fate"] == "withdrawn" for d in drives),
            "queued": sum(d["fate"] == "queued" for d in drives),
            "mismatches": diffs[:5], "unaudited_pins": lu_pins,
            "min_lead": min((d["lead"] for d in due), default=None)}


def attempt(source: str) -> str:
    out = json.loads(escplay.run(source, "blank", json.dumps({"ticks": TICKS})))
    if not out.get("ok"):
        return json.dumps(out)
    out["audit"] = audit(escplay.LAST["m"])
    return json.dumps(out, separators=(",", ":"))
