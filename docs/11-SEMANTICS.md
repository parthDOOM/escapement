# Escapement machine semantics v1.4.0 (normative)

This document is the single normative definition of the machine. The Python
instruction-set simulator (`sw/esciss`) and the RTL (`src/`) are separate
implementations of it and share only the encoding tables in `isa/isa.yaml`. Where
[02-ISA](02-ISA.md) or [01-ARCHITECTURE](01-ARCHITECTURE.md) differ, this document wins.

Version history: 1.0 is the base machine. 1.0.1 adds the IRQ status and clear and the
`fault_valid` core port. 1.0.2 defines the checker at a fault edge (it is cancelled,
§8). 1.1.0 adds the tape, a second 256 × 32 memory shared by firmware loads and stores
and a timestamped capture unit (§6.5, §7.6), the execution digest (§10.1), and makes
GETI wait for in-flight SAMPLEs (§7.3). Rationale:
[13-TAPE-AND-DIGEST](13-TAPE-AND-DIGEST.md). 1.2.0 adds pin bank B, five split pins on
the chip's otherwise unused dedicated inputs and outputs, chosen per context (§5); the
instruction set is unchanged, and a program that does not use bank B behaves exactly as
on 1.1.0, digest included. 1.3.0 adds streaming capture: a capture mode in which the
host reads records while the run goes on and acknowledges them, and the capture unit
never writes over a record the host has not acknowledged (§6.5). The tape port gains a
host side during a run (§10). A program that does not set the new CAPT bit behaves
exactly as on 1.2.0, digest included. 1.4.0 adds the line unit (§6.6): packets with NRZI,
bit stuffing and a CRC sent from and received into the tape, with clock recovery on
receive, a packet start admitted as an event (`SEND`), and the instructions `LINE` and
`GETL` (§7.7). A late `SEND` is dropped rather than faulted, so a reply is on time or
not sent. A program that does not use them behaves exactly as on 1.3.0, digest
included.

Notation: all arithmetic on 32-bit values is modulo 2^32 unless stated. `zext` is
zero extension. `ahead(a, b)` means `1 <= (a - b) mod 2^32 < 2^31` ("a is strictly
after b within the half-range horizon"). `reached(t, d)` means `(t - d) mod 2^32 < 2^31`
("tick t is at or after d").

---

## 1. Parameters

| Name | Value |
| --- | --- |
| Contexts | 4 (k = 0..3) |
| Instruction memory | 256 words × 32 bits, single port, shared |
| Event FIFO depth | 4 entries per context |
| Protocol pins | 13: bank A, 8 bidirectional (`uio[7:0]`); bank B (v1.2), 5 split pins (out `uo_out[7:3]`, in `ui_in[7:3]`) |
| Pins per context | 8 logical pins, mapped onto the context's bank (§5) |
| Host mailboxes | 2 entries × 32 bits per context per direction |
| Tape (v1.1) | 256 words × 32 bits, single port, shared by all contexts and the capture unit |
| Capture staging (v1.1) | 2 records |
| Line unit (v1.4) | 1, half duplex, owned by one context per run |

## 2. Time

The machine is synchronous to `clk`. A **cycle** is the interval between two rising
edges. **Edge E_t** is the rising edge that ends cycle t. Registers change only at
edges; "X during cycle t" is the value of X after edge E_(t-1).

A **run** starts with a one-cycle `start` pulse. If `start` is high during cycle s,
then at edge E_s the machine initialises the run (§9). The cycle after E_s is run
cycle **0**. The **tick counter** `now` equals t during run cycle t and wraps modulo
2^32. All ticks in this document are run cycles.

## 3. Context scheduling (fixed 4-slot barrel)

A context is in one of four states: `IDLE`, `RUNNING`, `HALTED`, `FAULTED`.

A `RUNNING` context k **fetches** in every run cycle t with `t mod 4 == k`. The
instruction fetched in cycle t **commits at edge E_(t+3)**; t+3 is its **commit tick**.
So context k commits only at ticks c with `c mod 4 == (k + 3) mod 4`, and its next
fetch is at t+4. A context that is not `RUNNING` during its fetch cycle does not
fetch, and its slot is empty.

**Instruction memory interface.** In fetch cycle t the machine presents
`imem_addr = PC_k` (PC during cycle t). The memory returns `imem_rdata = IMEM[PC_k]`
during cycle t+1 (synchronous read registered at E_t). IMEM is never written during a
run.

**What a commit sees.** Every decision and value used by the instruction committing
at tick c is taken from state **during cycle c**: registers, flags, `tmr`, `lc`,
`sh`, `ish`, checker status, FIFO contents, mailbox contents and the synchronised
input vector `sync`. Only the instruction word is fetched earlier. (Context state can
change between fetch and commit only through the event engine and the host.)

Each commit has one **outcome**:

- **COMPLETE**: effects are applied at E_c, and PC becomes the next PC.
- **WAIT** (wait-class only): no architectural effect except the telemetry counter
  in §7.4; PC is unchanged, so the same instruction is fetched again in the next slot.
- **FAULT**: no architectural effect of the instruction; fault handling (§8) at E_c.

## 4. Architectural state

Per context k: `r0..r3` (32 b), `sh` (32 b), `lc` (16 b), `tmr` (32 b), `PC` (8 b),
`lr` (8 b), flags `Z`, `C`, state, `ish` (32 b), event FIFO (4 entries), expectation
checker, telemetry, IRQ pending + code, mailboxes.

Global: `now`, pin registers `val[12:0]` and `oe[12:0]`, input synchronisers, the
first-fault register, run flag, the tape `T[0..255]` (not reset, not cleared by start;
written only by STT, the capture unit and the host while stopped), the capture unit
(§6.5), the line unit (§6.6), the execution digest `dg` (§10.1), and configuration
(static during a run):
`ctx_enable[3:0]`, `entry_pc[k]`, `pin_owned[12:0]`, `pin_owner[p]` (2 b each),
`pin_od[7:0]`, `ctx_bank[3:0]` (v1.2).

## 5. Pins

**Physical pins** p = 0..12:

| p | Bank | Output | Input |
| --- | --- | --- | --- |
| 0..7 | A | `uio_out[p]`, `uio_oe[p]` | `uio_in[p]` |
| 8..12 (v1.2) | B | `uo_out[p − 5]` | `ui_in[p − 5]` |

A bank B pin is split: its output and its input are different package pins, so it has
no output enable at the package and cannot be open-drain.

**Outputs during cycle t** come from the pin registers during cycle t:

- push-pull pin p < 8 (`pin_od[p]=0`): `uio_out[p] = val[p]`, `uio_oe[p] = oe[p]`
- open-drain pin p < 8 (`pin_od[p]=1`): `uio_out[p] = 0`, `uio_oe[p] = oe[p] & ~val[p]`
- bank B pin p: `uo_out[p − 5] = oe[p] ? val[p] : 1`; written `ob[i]` for p = 8 + i

So on an open-drain pin, `val=1` (or `oe=0`) releases the line and `val=0, oe=1` drives
low. No register state can drive an open-drain pin high. A released bank B output
(`oe=0`) is high, the level a bank A pin floats to on its pull-up, so active-low selects
and idle-high lines (SPI CS, UART TX) are idle from reset and after a fault or stop.

**Inputs.** Two flip-flop synchroniser: at every edge `s1 <= {ui_in[7:3], uio_in}`,
`s2 <= s1`. `psync` during cycle t is `s2` (13 bits), which equals the inputs during
cycle t-2. The synchronisers run continuously (they are not cleared by `start`) and
reset to 0.

**Logical pins (v1.2).** Every instruction names pins by an 8-bit logical vector: masks,
values and output enables of DRIVE, EXPECT and WAITP, the SAMPLE pin index and GETP.
Context k's logical pin j is physical pin `phys(k, j)`:

- `ctx_bank[k] = 0`: `phys(k, j) = j`.
- `ctx_bank[k] = 1`: `phys(k, j) = 8 + j` for j < 5; logical pins 5..7 have no
  physical pin.

Context k's view of the inputs, `sync` in the rest of this document when context k
evaluates it, is `sync[j] = psync[phys(k, j)]`, and 0 for a logical pin with no physical
pin. Context k **owns** logical pin j if `phys(k, j)` exists, `pin_owned[phys(k, j)]` and
`pin_owner[phys(k, j)] == k`. A DRIVE of context k changes, for each masked logical pin
j, the registers of physical pin `phys(k, j)`. A context sees only its own bank; contexts
in either bank may run at the same time.

Pin registers change only through DRIVE retirement (§6.3), the line unit (§6.6), fault
release (§8), `stop` and reset (§9). A pin claimed by the line unit (v1.4) is written
only by the line unit.

## 6. Event FIFO and event engine

### 6.1 Entries

| Kind | Fields |
| --- | --- |
| DRIVE (1) | deadline, mask, value, oe |
| EXPECT (2) | deadline, mask, level, mode (HOLD 0 / ARRIVE 1), window (0..127) |
| SAMPLE (3) | deadline, pin (0..7), dir |
| LINE (0, v1.4) | deadline, command (24 bits) |

Every queued entry takes its deadline from `tmr` during the commit cycle.

### 6.2 Admission (at the commit edge E_c of an EMIT, EMITR, EMITX, EXPECT, SAMPLE or SEND)

Let Q be the context's FIFO **after** the retirement that happens at E_c (§6.3), and
d the new entry's deadline. Check in this order:

1. If |Q| == 4, the outcome is **WAIT** (§7.4 counts it).
2. DRIVE or EXPECT with `mask == 0` → FAULT ADMIT/MASK.
3. DRIVE whose mask selects any logical pin j that context k does not own (§5), or a pin
   claimed by the line unit (§6.6) → FAULT ADMIT/OWNER. SEND (v1.4) by a context that is
   not the line unit's owner, or while the line unit is unconfigured or has no
   `tx_pins` → FAULT ADMIT/OWNER; SEND while the line unit is engaged (§6.6) → **WAIT**.
4. Not `ahead(d, c)` → FAULT ADMIT/PAST. (Deadline must be ≥ c+1 and < c + 2^31.)
   SEND (v1.4) instead **drops** the packet: COMPLETE with no entry, no other effect and
   Z = 0. (A SEND's deadline usually comes from the line, through GETL's `tmr = e`, so
   a late one means the line did something early, not that the program is wrong: the
   reply is sent on time or not at all, and the protocol's own retry recovers.)
5. Q nonempty and not `ahead(d, tail(Q).deadline)` → FAULT ADMIT/ORDER.
6. EXPECT only: if an EXPECT has been admitted since `start`, and not
   `ahead(d, last_expect_end)` → FAULT ADMIT/OVERLAP. On admission set
   `last_expect_end = d + window`.

Otherwise the entry is appended at E_c, and the outcome is COMPLETE.

### 6.3 Retirement (every edge E_t, every context)

If the FIFO head h during cycle t has `h.deadline == t`, it retires at E_t:

- **DRIVE**: for every logical pin j with `mask[j]=1`, with p = `phys(k, j)`:
  `val[p] <= value[j]`, `oe[p] <= oe[j]` (from the entry). Pins change in the cycle
  after the deadline: outputs show the new value **during cycle d+1**.
- **SAMPLE**: `ish <= shift_in(ish', sync_t[pin], dir)`, where `ish'` is `ish` after any
  GETI clear at the same edge (§7.3), `sync_t` is `sync` during cycle t, and
  `shift_in(x, b, 0) = (x >> 1) | (b << 31)` (LSB-first), `shift_in(x, b, 1) = (x << 1) | b`
  (MSB-first).
- **EXPECT**: starts the checker (§6.4), which evaluates tick t at this same edge.
- **LINE** (v1.4): starts the line unit's transmission (§6.6) if it is in PEND.

If the head during cycle t has `reached(t, h.deadline)` and `h.deadline != t`, the
context faults with LATE_HEAD at E_t. Admission rules make this unreachable; it exists
as a checked invariant. At most one entry per context retires per edge. Retirement is
suppressed if the context faults at E_t (§8). Different contexts retire independently
at the same edge; DRIVE pins are disjoint by ownership.

### 6.4 Expectation checker (one per context)

An EXPECT with deadline d, window w checks ticks u = d, d+1, …, d+w. At tick u it
computes `m = ((sync_u & mask) == level)` and acts at E_u:

- HOLD: if `!m`, then **fail** at u and stop. If `m` and u == d+w, then **pass** and stop.
- ARRIVE: if `m`, then **pass** and stop. If `!m` and u == d+w, then **fail** at u and stop.

Status per context: `active` (the checker is between d and its stop tick), `pass_count`
and `fail_count` (8 b, saturating), and a `first_fail` record {tick u, observed `sync_u`,
mask, level}, written on the first fail since `start`. A fail never faults the context;
firmware reads the status with GETX. Admission (§6.2 step 6) guarantees windows of
one context never overlap.

### 6.5 Capture unit (v1.1, one, global)

State: `en` (generating), `snap`, `bank` (v1.2), `mask[7:0]`, `base[7:0]`, `lim[7:0]`,
`ring`, `stream` (v1.3), `ptr[7:0]`, `rptr[7:0]` (v1.3), `prev[7:0]`, flags `ovf`,
`full`, `wrapped`, `ovf_tick[31:0]`, and a staging FIFO of at most 2 records. The capture unit watches one bank: `csync` is
`psync[7:0]` when `bank = 0` and `{3'b0, psync[12:8]}` when `bank = 1`, and `sync_t` in
this section means `csync` during cycle t. A **record** is the word
`{levels[7:0], tick[23:0]}`: bits 31:24 are `csync` during the record's tick, bits 23:0
are that tick mod 2^24.

The **tape port is busy in cycle t** if a commit observed at E_t (`commit_valid`, §10)
has opcode LDT or STT, whatever its outcome, or (v1.3) the host writes the tape in
cycle t (`tape_host_we`, §10; the host writes only while stopped), or (v1.4) the line
unit makes it busy in cycle t (§6.6). Otherwise it is free.

The **successor** of `ptr` is `base` if `ptr + 1 == lim` (8-bit compare) and `ring` or
`stream` is set, and `ptr + 1` otherwise. **Stream mode** (v1.3): with `stream`, the
region is a ring shared with the host. `rptr` is the first word the host has not
acknowledged, and the words from `rptr` up to but not including `ptr`, around the
region, are records the host has not yet read. The drain is **held** in cycle t if
`stream` and the successor of `ptr` equals `rptr`: a held record stays staged, so a
record is never written over an unread one, and the region holds at most size − 1
unread records. If the host falls behind, the staging FIFO fills and the next record
sets `ovf` in step 2, so a loss is never silent and `ovf_tick` says when it began.
The host acknowledges records with `tape_ack` (§10): at every edge E_t with `tape_ack`
during cycle t, except a start edge, `rptr <= tape_ack_v`, while stopped too; a CAPT that
enables at E_t overrides it (step 3). The drain in cycle t uses `rptr` during cycle t. A
`rptr` outside the region never holds the drain.

At every edge E_t of a run, unless start or stop is at E_t, in this order:

1. **Drain.** If the staging FIFO during cycle t is nonempty, the port is free in
   cycle t and the drain is not held, its head is written, `T[ptr] <= head`, and
   removed. Then `ptr <= ptr + 1`; if the new value equals `lim` (8-bit compare): with
   `ring` or `stream`, `ptr <= base` and `wrapped <= 1`; with neither, `full <= 1`,
   `en <= 0`, and the rest of the staging FIFO is discarded. (So the region is
   `[base, lim)` modulo 256, and `base == lim` means all 256 words.)
2. **Generate.** If `en` during cycle t and step 1 did not set `full`, a record
   `{sync_t, t mod 2^24}` is generated when any of these holds: `snap`;
   `((sync_t ^ prev) & mask) != 0`; `t mod 2^24 == 0` (epoch marker). Then
   `prev <= sync_t` and `snap <= 0`. If the staging FIFO after step 1 already holds 2
   records, the record is dropped and `ovf <= 1`, `ovf_tick <= t`, `en <= 0` (records
   already staged are still written). Otherwise it is appended.
3. A **CAPT** completing at E_t suppresses step 2. With `v[31] = 1` it then sets every
   capture register as in §7.6, overriding step 1's `ptr`/flag updates (step 1's tape
   write still happens). With `v[31] = 0` it sets `en = snap = 0` after step 1.

After stop, records still staged are drained (step 1) at the following edges at which
the port is free and the drain is not held. Start and reset:
`en = snap = ovf = full = wrapped = 0`, `ptr = rptr = 0`, `mask = base = lim = prev = 0`,
`ring = stream = bank = 0`, `ovf_tick = 0`, staging empty.

A decoder recovers absolute ticks from bits 23:0 alone: a record whose tick field is 0
marks the start of a new 2^24-tick epoch, and one is generated at every epoch boundary
while `en`.

### 6.6 Line unit (v1.4, one, global)

The line unit (LU) moves a packet between the tape and two or three pins and does the
line coding on the way: NRZI, bit stuffing, a CRC, and clock recovery on receive. It is
half duplex and runs one **job** at a time. Rationale and profiles:
[19-LINE-UNIT](19-LINE-UNIT.md).

**Owner.** At start the LU has no owner. The context whose `LINE` CONFIG first commits
(§7.7) becomes its owner for the rest of the run.

**Configuration** is three words W0, W1, W2, loaded from the tape by CONFIG:

| Field | Bits | Meaning |
| --- | --- | --- |
| `d` | W0[2:0] | data pin (a logical pin of the owner) |
| `n` | W0[5:3] | complement pin (with `diff`) |
| `r` | W0[8:6] | receive pin (without `diff`) |
| `diff` | W0[9] | differential pair: `d` carries the line level L, `n` carries ¬L, and both low is SE0 |
| `nrzi` | W0[10] | NRZI: a 0 bit toggles L, a 1 keeps it. Otherwise L is the bit |
| `lsb` | W0[11] | bit order of the stream and of byte fields (below) |
| `N` | W0[14:12] | stuffing run length; 0 = no stuffing |
| `any` | W0[15] | 0: after N consecutive 1s a 0 is inserted. 1: after N consecutive equal bits their complement is inserted |
| `eop` | W0[17:16] | with `diff`: SE0 bit times sent after a packet, followed by one bit of L = 1 |
| `idle_drive` | W0[18] | idle state of the claimed pins: L = 1 driven (`oe = 1`), or released (`oe = 0`) |
| `sync_skip` | W0[19] | receive: the decoded bits up to and including the first 1 are a sync pattern, not stored |
| `tx_pins` | W0[20] | claim `d`, and `n` with `diff`, for transmitting. 0: no pins are claimed (receive and CRC jobs only) |
| `prefix` | W0[31:24] | 8 bits sent before the header; 0 = none |
| `P_int` | W1[11:0] | bit period in whole ticks |
| `P_frac` | W1[19:12] | bit period fraction, in 1/256 tick |
| `S` | W1[31:20] | receive sample point, ticks after a bit boundary |
| `poly` | W2[15:0] | CRC polynomial, left-aligned (below) |
| `init_ones` | W2[16] | CRC start value: all ones over its width, else 0 |
| `inv` | W2[17] | a transmitted CRC is inverted |
| `hdr` | W2[18] | packets carry a header byte after the prefix |
| `fall` | W2[19] | receive resyncs only on transitions from L = 1 to L = 0; else on every transition |
| `abort` | W2[20] | transmit stops at the first monitor mismatch (below) |
| `idle_min` | W2[24:21] | receive: bit times of idle line required before a start |

Bits not listed are ignored. A configuration is **invalid** if `P_int < 8`, `S < 4` or
`S ≥ P_int`.

**CRC.** The register `crc` has 16 bits. A polynomial G of width w ≤ 16 is written
left-aligned, `poly = (G mod x^w) << (16 − w)`, so its lowest set bit is bit 16 − w
(CRC-5/USB 0x2800, CRC-16/USB 0x8005, CRC-15/CAN 0x8B32). For `poly ≠ 0` the width is
`w = 16 − ctz(poly)` and the width mask is `M = (−(poly & −poly)) mod 2^16`; the start
value is `M` if `init_ones`, else 0. For `poly = 0` the start value is 0 and w = 0. A CRC
bit b updates `crc <= ((crc << 1) mod 2^16) XOR ((crc[15] XOR b) ? poly : 0)`. A
transmitted CRC is w bits; bit i (i = 0 first) is bit 15 − i of `inv ? ~crc : crc`.

**The stream.** A job's tape data is a bit stream from word a. Stream bit j is bit
`j mod 32` of `T[(a + ⌊j/32⌋) mod 256]` with `lsb`, and bit `31 − (j mod 32)` of it
without. A byte field (prefix, header) goes bit 0 first with `lsb`, bit 7 first without.

**Claimed pins.** A configuration with `tx_pins` claims the owner's logical pins `d`, and
`n` with `diff`. Only the LU writes a claimed pin: a DRIVE retiring at E_t leaves the pins
claimed during cycle t unchanged (its other pins change as usual), the LU's own pin
writes at E_t come after DRIVE retirement, and a DRIVE that masks a claimed pin faults
ADMIT/OWNER (§6.2).
**Driving L** sets `val[d] = L`, with `diff` also `val[n] = ¬L`, and `oe = 1` on the
claimed pins. **SE0** sets `val[d] = val[n] = 0`, `oe = 1`. The **idle state** is L = 1 with
`oe = idle_drive`.

**Bit timing.** Each bit boundary B carries an accumulator α. The bit starting at B lasts
`P_int + carry` ticks, where `carry = 1` if `α + P_frac ≥ 256`, else 0; the next timed
boundary is `B + P_int + carry`, with accumulator `(α + P_frac) mod 256`. A job's first
boundary has α = 0, so transmitted bit k starts at `B_k = d + k·P_int + ⌊k·P_frac/256⌋`.

**Tape access.** The LU uses the tape port only in its **owner's commit cycles**, the
cycles u with `u mod 4 == (owner + 3) mod 4`, whether or not the owner commits in u. The
port is busy (§6.5) in the commit cycle of every SEND and LINE, whatever its outcome,
and in every cycle with a **scheduled access** (one requested at an earlier edge, listed
below). A scheduled access in cycle u happens even if the job ends at E_u, and an LDT or
STT of the owner committing in u has outcome WAIT. A read in cycle u returns T during
cycle u, and the LU can use it from cycle u+1. A write in cycle u sets `T[a] <= v` at E_u.

**Modes.** IDLE; CFG (loading a configuration); PEND (a SEND is queued); TX; ARM (a
receiver waits for a start); RX (receiving, up to the last tape write); CRC. The LU is
**engaged** in CFG, PEND, TX, RX and CRC, and **busy** when engaged or in ARM. When a job
ends it records its **end tick** `e` (32 bits).

**Status** (GETL, dbg sel 21): [31] busy; [30:28] last job (1 RECV, 2 CRC, 3 CONFIG,
4 SEND); [27:25] cause; [24:16] count; [15:0] `crc`. Causes: 0 OK; 1 VIOL, a receive
ended by a stuffing violation; 2 SE0, a receive that saw SE0 and no packet; 3 STUFF, a
stuffing violation inside a differential packet; 4 FULL, the receive limit was reached;
5 NOSYNC, a packet ended before its sync pattern did; 6 MISMATCH, a transmit stopped by
`abort`. A job sets the last-job field and clears cause and count when it starts, and
sets cause and count when it ends. `crc` is the live register. All fields are 0 at
start.

#### CONFIG (from LINE, word a, committing at E_c)

Mode CFG, last job CONFIG. The LU reads `T[a]` in cycle c, `T[a+1]` in cycle c+4 and
`T[a+2]` in cycle c+8 (addresses mod 256), and completes at E_(c+9):

1. If the new W0 has `tx_pins` and `d`, or with `diff` `n`, is not a logical pin the owner
   owns (§5), or `diff` and `d == n`, the owner faults ADMIT/OWNER at E_(c+9). Otherwise,
   if the configuration is invalid, it faults ADMIT/CONFIG there. A fault leaves the LU
   unconfigured (§8).
2. Otherwise the new configuration takes effect. Pins claimed before and not claimed now
   are released (`oe <= 0`); the claimed pins are set to the idle state; `crc` = the new
   start value; cause OK, count 0; `e = c + 9`; mode IDLE.

#### SEND and transmission

On admission at E_c (§6.2, with d = `tmr` and `v = R[rs]`) the mode becomes PEND, a LINE
entry {d, v[23:0]} is appended, and if `v[14:8] > 0` the LU reads stream word 0,
`T[v[7:0]]`, in cycle c. At E_d the entry retires (§6.3) and the job starts: mode TX,
last job SEND, `crc` = start value. The packet is this bit sequence:

- the 8 prefix bits, if `prefix ≠ 0`; the 8 bits of the header `v[23:16]`, if `hdr`;
- `v[14:8]` stream bits from word `v[7:0]`, each also a CRC bit;
- the CRC (w bits), if `v[15]` and `poly ≠ 0`.

**Stuffing** covers the whole sequence. With N > 0 the LU tracks the run of the last
line bits: when, after a bit, the run has reached N, the next line bit is a **stuff bit**
(`any = 0`: a 0, which ends the run; `any = 1`: the complement of the run's bit, which
starts a run of 1) and the next packet bit waits. A stuff bit is inserted after the last
packet bit too when the run requires it. **NRZI**, if set, then turns each line bit into
L, starting from L = 1.

Line bit k (stuff bits counted) is driven at E_(B_k), so it shows during cycle B_k + 1.
With `diff` and `eop > 0`, `eop` bits of SE0 and one bit of L = 1 follow, each one bit
long. At the boundary after the last of these the idle state is applied, the job ends
(cause OK, count 0, `e` = that boundary) and the mode is IDLE. A packet with no bits and
no EOP applies the idle state at E_d.

If stream bits from 32(m+1) remain, stream word m+1 is read in the first owner commit
cycle u > B, where E_B drives stream bit 32m + 31. (It is usable from u + 1 ≤ B + 5,
before the next boundary.)

**Monitor.** With `abort` and without `diff`, at tick `B_k + S` of each line bit k of
the packet (not the EOP bits), the LU compares `sync[r]` with the L it drives for bit k.
At a mismatch the idle state is applied at `B_(k+1)` instead of the next bit, and the job
ends there with cause MISMATCH, count = min(k, 511) and `e = B_(k+1)`.

#### RECV and reception

RECV at E_c (`v = R[rs]`): mode ARM, buffer word a = `v[7:0]`, limit `L_max = 8·v[12:8]`
stored bits (`v[12:8] = 0` means 256). During cycle t the **line level** `x_t` is
`sync[d]` with `diff` and `sync[r]` without, and `se0_t` holds if `diff` and
`sync[d] = sync[n] = 0`. The line is **idle** at t if `x_t = 1` and not `se0_t`.

**Start.** In ARM, the receiver starts at the first tick t0 > c with `x_t0 = 0` for which
the line was idle in each of the K cycles before t0, all of them after c, where
K = max(1, `idle_min`·`P_int`). It takes effect at E_t0, unless a LINE or SEND of the owner
completes at E_t0 (the commit wins and the receiver is cancelled). Then: mode RX, last
job RECV, t0 is a boundary with α = 0, the NRZI reference is L = 1, the stuffing run is
empty, count = 0, `crc` = start value, and the receiver is in **hunt** if `sync_skip`,
else in **data**.

**Boundaries.** A tick t > t0 is a boundary if `x_t ≠ x_(t−1)` and (not `fall` or
`x_t = 0`) (a resync, α = 0), or else if it is the next timed boundary after the previous
boundary. The receiver **samples** at tick B + S after each boundary B, unless another
boundary comes first.

**A sample** at tick t does, in order (once storing has stopped, only step 1):

1. With `se0_t`: end of packet (below).
2. Decode b: with `nrzi`, b = 1 if `x_t` equals the reference and 0 otherwise, and the
   reference becomes `x_t`; without, b = `x_t`.
3. Destuff: if the run has reached N, b must be the stuff bit (0, or the complement of
   the run's bit). If it is, it is dropped and the run continues as in transmission. If
   not, there is a **stuffing violation**: with `diff`, cause STUFF is noted and storing
   stops; without `diff`, the job ends with cause VIOL and `e = t`. Any other bit extends
   or restarts the run.
4. In hunt: a 1 ends the sync pattern and the receiver enters data. The bit is not stored.
5. In data: b becomes stream bit `count` of the LU's buffer word; if `hdr` is off or
   count ≥ 8, b is also a CRC bit; count = count + 1. When count reaches a multiple of 32,
   the buffer word is written to `T[a + count/32 − 1]` in the first owner commit cycle
   after t, and the buffer is cleared at that edge. When count reaches `L_max`: with
   `diff`, cause FULL is noted and storing stops; without `diff`, the job ends with cause
   FULL and `e = t`.

**End of packet** (`diff` only). After the SE0 sample at tick s, the receiver waits for
the first tick t1 > s at which `se0_t1` does not hold, or for tick s + 255, whichever
comes first, and `e` is that tick. The cause is SE0 if the receiver was in hunt and had
decoded no bit (count = min(e − t0, 255)); NOSYNC if it was in hunt after decoding bits
(count 0); otherwise the cause noted in step 3 or 5, or OK, with count = stored bits.

**Finish.** If a full buffer word is still waiting, or count is not a multiple of 32 and
the cause is not SE0 or NOSYNC, the word (bits not stored are 0) is written to
`T[a + ⌊(count − 1)/32⌋]` in the first owner commit cycle u > e, and the mode becomes
IDLE at E_u. Otherwise it becomes IDLE at E_e.

#### CRC (from LINE, word a, nb bits, committing at E_c)

Mode CRC, last job CRC, `crc` = start value. The LU reads stream word m in cycle c + 32m,
and stream bit j is a CRC bit at E_(c+1+j), for j < nb. The job ends at E_(c+nb): cause
OK, count = nb, `e = c + nb`, mode IDLE. (With nb = 0 it ends at E_c and reads nothing.)
With the **wire** flag (`v[16]`), at that same edge `crc` becomes the CRC in transmission
order: bit i, for i < w, is bit 15 − i of `inv ? ~crc : crc`, the i-th CRC bit a SEND would
put on the line, and bits w..15 are 0. (An LSB-first protocol can then place the CRC in a
field directly, as a USB token's CRC-5.)

#### OFF, faults, stop and HALT

LINE OFF at E_c ends any job and any armed receiver at once (an access scheduled in cycle
c happens; none after it), releases the claimed pins (`oe <= 0`) and unclaims them, and
leaves the LU unconfigured in mode IDLE; the owner stays. A LINE entry that retires while the LU is
not in PEND has no effect. When the owner faults (§8) the LU does the same at that edge.
Start and stop reset the LU (§9). The owner's HALT waits while the LU is engaged and
cancels an armed receiver when it completes (§7.2).

## 7. Instructions

Formats and opcodes are in `isa/isa.yaml`. Unlisted opcodes and nonzero reserved bits
→ FAULT ILLEGAL. On COMPLETE the next PC is PC+1 unless stated. **PC_RANGE**: an
instruction at PC = 255 whose outcome would be COMPLETE with a sequential next PC (or
a CALL at 255) faults PC_RANGE instead, with no effects.

`R[x]` is register x. Flags are unchanged unless listed.

### 7.1 Data

| Instr | Effect | Flags |
| --- | --- | --- |
| MOV rd, rs | R[rd] = R[rs] | Z |
| MOVI rd, imm16 | R[rd] = zext(imm) | Z |
| MOVHI rd, imm16 | R[rd] = imm<<16 \| R[rd][15:0] | Z |
| ADD rd, rs | R[rd] = R[rd] + R[rs] | Z, C = carry out of bit 31 |
| ADDI rd, imm16 | R[rd] = R[rd] + zext(imm) | Z, C = carry |
| SUB rd, rs | R[rd] = R[rd] − R[rs] | Z, C = borrow (R[rd] < R[rs], unsigned) |
| CMP rd, rs | (R[rd] − R[rs]) discarded | Z, C = borrow |
| CMPI rd, imm16 | (R[rd] − zext(imm)) discarded | Z, C = borrow |
| AND / OR / XOR rd, rs | bitwise | Z |
| TSTI rd, imm16 | Z = (R[rd] & zext(imm)) == 0 | Z |
| SHL rd, n | n=0: no change, C=0; else C = R[rd][32−n], R[rd] <<= n | Z, C |
| SHR rd, n | n=0: no change, C=0; else C = R[rd][n−1], R[rd] >>= n (logical) | Z, C |
| LDSH rs | sh = R[rs] | — |
| STSH rd | R[rd] = sh | — |

Z is set from the written or compared result, `Z = (result == 0)`. For TSTI it is as
listed.

### 7.2 Control

| Instr | Effect |
| --- | --- |
| NOP | none |
| JMP t | PC = t |
| JZ / JNZ / JC / JNC t | PC = t if the condition holds, else PC+1 |
| LDLC imm16 | lc = imm |
| DJNZ t | lc = lc − 1 (16-bit wrap); PC = t if the new lc ≠ 0, else PC+1 |
| CALL t | lr = PC+1, PC = t (PC_RANGE at PC 255) |
| RET | PC = lr |
| IRQ code | irq_pending = 1, irq_code = code (sticky; cleared by the host) |
| HALT (wait) | if the FIFO after retirement at E_c is empty, the checker is not active after E_c and (v1.4) the context does not own an engaged line unit during cycle c: state = HALTED at E_c, PC unchanged, pins retained, and an armed receiver of its line unit is cancelled; else WAIT |

### 7.3 Time and input

| Instr | Effect | Flags |
| --- | --- | --- |
| RDTIME rd | R[rd] = c (the commit tick) | — |
| SETT rs | tmr = R[rs] | — |
| ADDT imm24 | tmr = tmr + zext(imm) | — |
| ADDTR rs | tmr = tmr + R[rs] | — |
| WAITT (wait) | COMPLETE if `reached(c, tmr)`, else WAIT | — |
| GETP rd | R[rd] = zext(sync_c) | Z |
| WAITP mask, value (wait) | if `(sync_c & mask) == value`: COMPLETE, Z = 1; else if `reached(c, tmr)`: COMPLETE, Z = 0; else WAIT | Z |
| GETI rd (wait, v1.1) | if the FIFO **during cycle c** holds any SAMPLE entry (including one retiring at E_c): WAIT. Else R[rd] = ish (during cycle c) and ish is cleared at E_c | Z |
| GETX rd | R[rd] = {fail_count[7:0], pass_count[7:0], 14'b0, active, failed_since_start} | Z |

A wait-class instruction is re-evaluated at each of its slots, so its completion is
quantised to the 4-tick slot grid. Exact-tick I/O uses events.

### 7.4 Events

| Instr | Entry |
| --- | --- |
| EMIT mask, value, oe | DRIVE {tmr, mask, value, oe} |
| EMITR mask, rs, oe | DRIVE {tmr, mask, R[rs][7:0], oe} |
| EMITX mask, dir, oe | b = dir ? sh[31] : sh[0]; DRIVE {tmr, mask, b ? mask : 0, oe}; on COMPLETE also sh = dir ? sh<<1 : sh>>1 and C = b |
| EXPECT mask, level, mode, window | EXPECT {tmr, mask, level, mode, window} |
| SAMPLE pin, dir | SAMPLE {tmr, pin, dir} |
| SEND rs (v1.4) | LINE {tmr, R[rs][23:0]}: a packet (§6.6); on COMPLETE, Z = 1 if admitted, 0 if dropped (§6.2 step 4) |

Outcome per §6.2. A dropped SEND changes no telemetry. On a WAIT caused by a full FIFO, `wait_full` (16 b, saturating)
increments at E_c; that is the only effect of a WAIT. On COMPLETE, telemetry updates:
`lead = (d − c) mod 2^32`, `min_lead = min(min_lead, min(lead, 0xFFFF))`,
`max_occupancy = max(max_occupancy, |Q| + 1)`.

### 7.5 Host mailboxes

| Instr | Effect | Flags |
| --- | --- | --- |
| POP rd (wait) | if RX mailbox nonempty during cycle c: R[rd] = head, removed at E_c; else WAIT | — |
| PUSH rs (wait) | if TX mailbox not full during cycle c: append R[rs] at E_c; else WAIT | — |
| RXREADY | Z = (RX mailbox empty) | Z |
| TXREADY | Z = (TX mailbox full) | Z |

The host side of the mailboxes is defined in [12-HOST-PROTOCOL](12-HOST-PROTOCOL.md).
Same-edge host access never changes what the commit at that edge observed.

### 7.6 Tape and capture (v1.1)

`a = (R[rb][7:0] + imm8) mod 256`. T is the tape during cycle c.

| Instr | Effect | Flags |
| --- | --- | --- |
| LDT ra, rb, imm8 | R[ra] = T[a] | — |
| STT ra, rb, imm8 | T[a] <= R[ra] at E_c | — |
| CAPT rs | configure the capture unit from `v = R[rs]` (below): [31] enable, [26] stream (v1.3), [25] bank (v1.2), [24] ring, [23:16] watched pins, [15:8] limit, [7:0] base | — |
| GETC rd | R[rd] = capture status during cycle c: [31] en, [30] ovf, [29] full, [28] wrapped, [9:8] staged count, [7:0] ptr; other bits 0 | Z |

LDT and STT WAIT only in a line unit access cycle of their own context (v1.4, §6.6).
**Implementation note (LDT):** the memory is read at E_c and
R[ra] is physically written at E_(c+1). No instruction can observe the difference (the
context's next commit is at c+4 or later); a host debug read during cycle c+1 may see the
old value. The observation record of the LDT commit shows the loaded value.

**CAPT** at E_c with `v = R[rs]`: if `v[31]` (enable): `en = 1`, `snap = 1`,
`mask = v[23:16]`, `lim = v[15:8]`, `base = v[7:0]`, `ring = v[24]`, `bank = v[25]`
(v1.2; the capture unit watches a bank directly, whatever bank the configuring context
uses, and needs no pin ownership), `stream = v[26]` and `rptr = base` (v1.3),
`ptr = base`, `ovf = full = wrapped = 0`, `ovf_tick = 0`, staging emptied (so the first record,
generated at E_(c+1), is a snapshot). If `v[31] = 0`: `en = 0` and `snap = 0` (no record
is generated at E_c); the other capture registers keep their values after §6.5 step 1,
and staged records still drain.

### 7.7 Line unit (v1.4)

`v = R[rs]`. SEND is in §7.4 and §6.2. "Owner" is the line unit's owner (§6.6).

| Instr | Effect | Flags |
| --- | --- | --- |
| LINE rs (wait) | op = `v[31:28]`: 0 OFF, 1 RECV (word `v[7:0]`, limit `v[12:8]` bytes), 2 CRC (word `v[7:0]`, `v[15:8]` bits, `v[16]` wire), 3 CONFIG (block at word `v[7:0]`); other bits ignored; §6.6 | — |
| GETL rd (wait) | if the line unit is not busy during cycle c: R[rd] = status, Z = 1, `tmr = e`. Else if `reached(c, tmr)`: R[rd] = status, Z = 0. Else WAIT | Z |

LINE checks, in order: op 4..15 → FAULT ILLEGAL. CONFIG when another context is the
owner, any other op by a context that is not the owner, and RECV or CRC while the line
unit is unconfigured → FAULT ADMIT/OWNER. Then, except for OFF, if the line unit is
engaged during cycle c: WAIT. Otherwise COMPLETE: an armed receiver is cancelled and the
operation starts at E_c (a CONFIG with no owner makes this context the owner). GETL by a
context that is not the owner → FAULT ADMIT/OWNER. The status GETL returns is the status
during cycle c.

## 8. Faults

A FAULT outcome at E_c (ILLEGAL, PC_RANGE, ADMIT) or a LATE_HEAD at E_t makes the
context `FAULTED` at that edge, with these effects:

- The context's FIFO is flushed. No entry of this context retires at that edge, even
  one due then.
- Its checker is deactivated without evaluating tick t at that edge (no
  pass/fail count or first_fail update; v1.0.2).
- For each physical pin p owned by the context (`pin_owned[p] && pin_owner[p] == k`):
  `oe[p] <= 0` (released). `val` is unchanged.
- If the first-fault register is empty, it records {cause, subcode (ADMIT only, else
  0), ctx, PC of the faulting instruction (for LATE_HEAD and for a CONFIG completing at
  E_t: PC during cycle t), tick}.
- (v1.4) If the context owns the line unit, the line unit ends its job and any armed
  receiver, unclaims its pins (released above, as the context's own) and becomes
  unconfigured (§6.6).

Other contexts are unaffected. ADMIT subcodes: MASK 1, OWNER 2, PAST 3, ORDER 4,
OVERLAP 5, CONFIG 6 (v1.4, an invalid line unit configuration, §6.6).

## 9. Reset, start and stop

**Reset** (`rst_n` low, asynchronous assertion): run flag 0, all contexts IDLE, all
FIFOs empty, checkers idle, `val = oe = 0`, synchronisers 0, first-fault empty, IRQs
clear, mailboxes empty, `now = 0`.

**Start** at E_s:
- run = 1 and `now` = 0 in the next cycle.
- Each context k: state = RUNNING if `ctx_enable[k]` else IDLE.
- r0–r3 = sh = tmr = 0, lc = 0, lr = 0, Z = C = 0, PC = entry_pc[k], ish = 0.
- FIFO empty, checker idle with counts 0 and no fail record, EXPECT history cleared.
- min_lead = 0xFFFF, max_occupancy = 0, wait_full = 0, IRQ clear.
- `val = oe = 0`, first-fault cleared, mailboxes emptied.
- Capture as in §6.5; no pending LDT write; `dg = 0xFFFFFFFF` (§10.1). The tape keeps
  its contents.
- Line unit (v1.4): no owner, unconfigured (W0 = W1 = W2 = 0), no claimed pins, mode
  IDLE, buffer 0, `crc = 0`, status 0, `e = 0`.

**Stop** at E_t (one-cycle `stop` pulse during cycle t):
- All RUNNING contexts become HALTED and all FIFOs are flushed; nothing due at E_t
  retires.
- All checkers are deactivated, and `oe = 0` on every pin.
- run = 0 and `now` stops.

Instructions in flight are discarded. A pending LDT register write (§7.6) still happens.
`dg` stops changing; capture generation stops (`en = 0`) and staged records drain. The
line unit (v1.4) ends its job as on OFF (§6.6); no access is made at E_t.

## 10. Observation interface (verification only)

Both models produce the same records. A commit at tick c produces:

```
{t: c, ctx, pc, insn, outcome: C|W|F,
 post: {r0, r1, r2, r3, sh, lc, tmr, z, c, pc}}   # state of ctx after E_c
```

Every run cycle t produces `{t, uio_out, uio_oe, ob}` (`ob` = `uo_out[7:3]`, v1.2). The
end of a run produces final
per-context state (state, registers, ish, telemetry, checker status, IRQ) and the
first-fault register.

**RTL ports** (core module `esc_core`): `dbg_commit_valid`, `dbg_commit_ctx[1:0]`,
`dbg_commit_pc[7:0]`, `dbg_commit_insn[31:0]` and `dbg_commit_outcome[1:0]` (0 C, 1 W,
2 F) describe a commit at E_c and are valid **during cycle c+1**. During that same cycle,
`dbg_post_*` show the committed context's registers (post-commit): `dbg_post_r0..r3`,
`dbg_post_sh`, `dbg_post_lc[15:0]`, `dbg_post_tmr`, `dbg_post_z`, `dbg_post_c`,
`dbg_post_pc[7:0]`. A combinational read port `dbg_sel_ctx[1:0]`, `dbg_sel[3:0]` →
`dbg_rdata[31:0]` exposes the state (selects 0–8 as described below the map). The ISS writes the same fields as JSON lines.

`dbg_sel` map (fields not listed read 0; 14 and 15 ignore `dbg_sel_ctx`):

| sel | Content |
| --- | --- |
| 0–3 | r0–r3 |
| 4 | sh |
| 5 | lc in [15:0] |
| 6 | tmr |
| 7 | [23:16] lr, [15:8] PC, [3:2] state (0 IDLE, 1 RUNNING, 2 HALTED, 3 FAULTED), [1] Z, [0] C |
| 8 | ish |
| 9 | [31:16] min_lead, [15:0] wait_full |
| 10 | [31:24] fail_count, [23:16] pass_count, [10:8] max_occupancy, [6:4] FIFO count, [1] checker active, [0] failed since start |
| 11 | first_fail tick |
| 12 | [23:16] first_fail mask, [15:8] first_fail level, [7:0] first_fail observed |
| 13 | [8] irq_pending, [7:0] irq_code |
| 14 | first-fault: [31] valid, [27:24] cause, [23:20] subcode, [17:16] ctx, [7:0] PC |
| 15 | first-fault tick |
| 16 | execution digest `dg` (v1.1) |
| 17 | capture status, as GETC |
| 18 | capture config: [26] stream (v1.3), [25] bank, [24] ring, [23:16] mask, [15:8] lim, [7:0] base |
| 19 | capture `ovf_tick` |
| 20 | capture `rptr` in [7:0] (v1.3) |
| 21 | line unit status, as GETL (v1.4) |
| 22–24 | 0 (the configuration words are tape words the firmware wrote) |
| 25 | line unit end tick `e` (v1.4) |
| 26 | line unit: [31] owned, [29:28] owner, [26:24] mode (0 IDLE, 1 CFG, 2 PEND, 3 TX, 4 ARM, 5 RX, 6 CRC), [20] configured (v1.4) |
| 27–31 | 0 |

`dbg_sel` is 5 bits wide since v1.1 (16–26 ignore `dbg_sel_ctx`; 22–24 and 27–31 read 0).

Selects 0–8 (v1.4) show the context's registers as they were in its most recent commit
slot (`now mod 4 = ctx - 1`) during a run, at most four cycles before the read, and as
they were one cycle before the read while stopped. All other selects show the current
state.

### 10.1 Execution digest (v1.1)

A 32-bit signature of the run, so a host can check silicon, gates, RTL and ISS against
each other with one read. At an edge E_t of a run (not a start or stop edge) the digest
is updated **if the edge is active**: a commit is observed at E_t, an LDT completed at
E_(t-1), or the outputs (`uio_out`, `uio_oe`, `ob`) during t differ from the outputs
during t−1 (outputs during cycle 0 are compared with 0). Otherwise `dg` is unchanged. So
once every context has stopped (HALT/FAULT) with its FIFO empty, `dg` is final: it does
not depend on when the host's STOP arrives.

```
dg  <= ((dg << 1) mod 2^32) XOR (dg[31] ? 0x04C11DB7 : 0) XOR x_t
x_t  = ((uio_oe_t << 24) | (uio_out_t << 16) | ((ob_t XOR 0x1F) << 8)) XOR cw_t XOR wv_t
       (outputs during t, §5; a released bank B folds as 0)
cw_t = a commit observed at E_t ? (outcome << 14) | (ctx << 12) | pc : 0
       (outcome 0 COMPLETE, 1 WAIT, 2 FAULT, as dbg_commit_*)
wv_t = XOR of every register value written at E_t: the rd result of a COMPLETE
       MOV, MOVI, MOVHI, ADD, ADDI, SUB, AND, OR, XOR, SHL, SHR (n = 0: the unchanged
       value), STSH, RDTIME, GETP, GETI, GETX, POP, GETC or GETL (v1.4) committing at
       E_t, and the loaded value of an LDT that completed at E_(t-1)
```

`dg = 0xFFFFFFFF` at start; it does not change while stopped. A run that never drives
bank B (`ob` stays 0x1F) produces the same digest as on 1.1.0.

**Core ports** (besides the debug ports; the v1.1 tape port is the last item):
- Clock and control: `clk`, `rst_n`, `start`, `stop`.
- Configuration: `ctx_enable[3:0]`, `entry_pcs[31:0]` (ctx k at [8k+7:8k]),
  `pin_owned[12:0]`, `pin_owner[25:0]` (pin p at [2p+1:2p]), `pin_od[7:0]`,
  `ctx_bank[3:0]` (v1.2).
- Instruction memory: `imem_addr[7:0]` out, `imem_rdata[31:0]` in.
- Pins: `uio_in[7:0]`, `uio_out[7:0]`, `uio_oe[7:0]`; bank B (v1.2) `ib_in[4:0]`
  (`ui_in[7:3]`) and `ob_out[4:0]` (`uo_out[7:3]`).
- Status: `running`, `ctx_state[7:0]` (2 bits per context, encoded as in sel 7),
  `irq_pending[3:0]` (per-context IRQ pending), `fault_valid` (first-fault register
  valid).
- IRQ clear (v1.0.1): `irq_clear[3:0]` input. At an edge where `irq_clear[k]` is high,
  context k's `irq_pending` is cleared, unless an IRQ instruction of context k commits
  at that same edge; the set wins, and that instruction's code is recorded.
- Mailboxes (host side), per context k (packed buses, ctx k at its slice):
  `mbx_rx_push[3:0]`, `mbx_rx_data[127:0]`, `mbx_rx_full[3:0]`, `mbx_tx_pop[3:0]`,
  `mbx_tx_data[127:0]` (TX head), `mbx_tx_empty[3:0]`. A push or pop on a host port
  takes effect at the edge where it is asserted, is ignored when full or empty, and is
  never visible to a commit at that same edge.

- Tape (v1.1): `tape_addr[7:0]`, `tape_we`, `tape_re`, `tape_wdata[31:0]` out,
  `tape_rdata[31:0]` in (same timing contract as `imem_rdata`), `tape_busy` out. v1.3:
  `tape_busy` is high exactly in the cycles in which the core uses the port, that is,
  a commit observed at that cycle's edge is an LDT or STT, the capture unit drains
  (§6.5), or (v1.4) the line unit accesses the tape (§6.6). The top gives the host the port in every other cycle, during a run too. A
  host access in cycle t changes `tape_rdata` only from cycle t+1 on, and the core
  reads `tape_rdata` only in the cycle right after its own LDT, so a host access never
  changes a value the core sees. `tape_host_we` in: the host
  writes the tape in this cycle (only while stopped); a drain waits for it.
  `tape_ack`, `tape_ack_v[7:0]` in (v1.3): the host acknowledges capture records up to
  `tape_ack_v` (§6.5 step 1). `dbg_sel` is 5 bits.

All configuration inputs must be stable from the `start` edge until the run ends.

## 11. Worked timing example

Context 0 runs `ADDT 10` at PC 0 and `EMIT 0x01,0x01,0x01` at PC 1, with pin 0 owned
by ctx 0, push-pull, from start:

- Fetch PC 0 in cycle 0 → commit at E_3: tmr = 10.
- Fetch PC 1 in cycle 4 → commit at E_7: d = 10, ahead(10, 7) holds, so it is admitted.
- At E_10 the DRIVE retires: `val[0] = oe[0] = 1`.
- `uio_out[0] = uio_oe[0] = 1` during cycle 11 onward.
- `min_lead = 3`.
- An external loopback (`uio_in[0]` = output) makes `sync[0] = 1` during cycle 13.
