# Escapement machine semantics v1.1.0 (normative)

This document is the single normative definition of the machine. The Python
instruction-set simulator (`sw/esciss`) and the RTL (`src/`) are separate
implementations of it and share only the encoding tables in `isa/isa.yaml`. Where
[02-ISA](02-ISA.md) or [01-ARCHITECTURE](01-ARCHITECTURE.md) differ, this document wins.

Version history: 1.0 is the base machine. 1.0.1 adds the IRQ status and clear and the
`fault_valid` core port. 1.0.2 defines the checker at a fault edge (it is cancelled,
§8). 1.1.0 adds the tape, a second 256 × 32 memory shared by firmware loads and stores
and a timestamped capture unit (§6.5, §7.6), the execution digest (§10.1), and makes
GETI wait for in-flight SAMPLEs (§7.3). Rationale:
[13-TAPE-AND-DIGEST](13-TAPE-AND-DIGEST.md).

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
| Protocol pins | 8 (`uio[7:0]`) |
| Host mailboxes | 2 entries × 32 bits per context per direction |
| Tape (v1.1) | 256 words × 32 bits, single port, shared by all contexts and the capture unit |
| Capture staging (v1.1) | 2 records |

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

Global: `now`, pin registers `val[7:0]` and `oe[7:0]`, input synchronisers, the
first-fault register, run flag, the tape `T[0..255]` (not reset, not cleared by start;
written only by STT, the capture unit and the host while stopped), the capture unit
(§6.5), the execution digest `dg` (§10.1), and configuration (static during a run):
`ctx_enable[3:0]`, `entry_pc[k]`, `pin_owned[7:0]`, `pin_owner[i]` (2 b each),
`pin_od[7:0]`.

## 5. Pins

**Outputs during cycle t** come from the pin registers during cycle t:

- push-pull pin i (`pin_od[i]=0`): `uio_out[i] = val[i]`, `uio_oe[i] = oe[i]`
- open-drain pin i (`pin_od[i]=1`): `uio_out[i] = 0`, `uio_oe[i] = oe[i] & ~val[i]`

So on an open-drain pin, `val=1` (or `oe=0`) releases the line and `val=0, oe=1` drives
low. No register state can drive an open-drain pin high.

**Inputs.** Two flip-flop synchroniser: at every edge `s1 <= uio_in`, `s2 <= s1`.
`sync` during cycle t is `s2`, which equals `uio_in` during cycle t-2. The
synchronisers run continuously (they are not cleared by `start`) and reset to 0.

Pin registers change only through DRIVE retirement (§6.3), fault release (§8), `stop`
and reset (§9).

## 6. Event FIFO and event engine

### 6.1 Entries

| Kind | Fields |
| --- | --- |
| DRIVE (1) | deadline, mask, value, oe |
| EXPECT (2) | deadline, mask, level, mode (HOLD 0 / ARRIVE 1), window (0..127) |
| SAMPLE (3) | deadline, pin (0..7), dir |

Every queued entry takes its deadline from `tmr` during the commit cycle.

### 6.2 Admission (at the commit edge E_c of an EMIT, EMITR, EMITX, EXPECT or SAMPLE)

Let Q be the context's FIFO **after** the retirement that happens at E_c (§6.3), and
d the new entry's deadline. Check in this order:

1. If |Q| == 4, the outcome is **WAIT** (§7.4 counts it).
2. DRIVE or EXPECT with `mask == 0` → FAULT ADMIT/MASK.
3. DRIVE whose mask selects any pin i without `pin_owned[i] && pin_owner[i] == k` →
   FAULT ADMIT/OWNER.
4. Not `ahead(d, c)` → FAULT ADMIT/PAST. (Deadline must be ≥ c+1 and < c + 2^31.)
5. Q nonempty and not `ahead(d, tail(Q).deadline)` → FAULT ADMIT/ORDER.
6. EXPECT only: if an EXPECT has been admitted since `start`, and not
   `ahead(d, last_expect_end)` → FAULT ADMIT/OVERLAP. On admission set
   `last_expect_end = d + window`.

Otherwise the entry is appended at E_c, and the outcome is COMPLETE.

### 6.3 Retirement (every edge E_t, every context)

If the FIFO head h during cycle t has `h.deadline == t`, it retires at E_t:

- **DRIVE**: for every pin i with `mask[i]=1`: `val[i] <= value[i]`, `oe[i] <= oe[i]`
  (from the entry). Pins change in the cycle after the deadline: outputs show the new
  value **during cycle d+1**.
- **SAMPLE**: `ish <= shift_in(ish', sync_t[pin], dir)`, where `ish'` is `ish` after any
  GETI clear at the same edge (§7.3), `sync_t` is `sync` during cycle t, and
  `shift_in(x, b, 0) = (x >> 1) | (b << 31)` (LSB-first), `shift_in(x, b, 1) = (x << 1) | b`
  (MSB-first).
- **EXPECT**: starts the checker (§6.4), which evaluates tick t at this same edge.

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

State: `en` (generating), `snap`, `mask[7:0]`, `base[7:0]`, `lim[7:0]`, `ring`, `ptr[7:0]`,
`prev[7:0]`, flags `ovf`, `full`, `wrapped`, `ovf_tick[31:0]`, and a staging FIFO of at
most 2 records. A **record** is the word `{levels[7:0], tick[23:0]}`: bits 31:24 are
`sync` during the record's tick, bits 23:0 are that tick mod 2^24.

The **tape port is busy in cycle t** if a commit observed at E_t (`commit_valid`, §10)
has opcode LDT or STT, whatever its outcome. Otherwise it is free.

At every edge E_t of a run, unless start or stop is at E_t, in this order:

1. **Drain.** If the staging FIFO during cycle t is nonempty and the port is free in
   cycle t, its head is written, `T[ptr] <= head`, and removed. Then `ptr <= ptr + 1`;
   if the new value equals `lim` (8-bit compare): with `ring`, `ptr <= base` and
   `wrapped <= 1`; without, `full <= 1`, `en <= 0`, and the rest of the staging FIFO
   is discarded. (So the region is `[base, lim)` modulo 256, and `base == lim` means
   all 256 words.)
2. **Generate.** If `en` during cycle t and step 1 did not set `full`, a record
   `{sync_t, t mod 2^24}` is generated when any of these holds: `snap`;
   `((sync_t ^ prev) & mask) != 0`; `t mod 2^24 == 0` (epoch marker). Then
   `prev <= sync_t` and `snap <= 0`. If the staging FIFO after step 1 already holds 2
   records, the record is dropped and `ovf <= 1`, `ovf_tick <= t`, `en <= 0` (records
   already staged are still written). Otherwise it is appended.
3. A **CAPT** completing at E_t suppresses step 2. With `v[31] = 1` it then sets every
   capture register as in §7.6, overriding step 1's `ptr`/flag updates (step 1's tape
   write still happens). With `v[31] = 0` it sets `en = snap = 0` after step 1.

After stop, records still staged are drained in the following cycles (the port is free
while stopped). Host tape access within 4 cycles of a stop is not defined; the host
protocol cannot issue one. Start and reset: `en = snap = ovf = full = wrapped = 0`,
`ptr = 0`, `mask = base = lim = prev = 0`, `ring = 0`, `ovf_tick = 0`, staging empty.

A decoder recovers absolute ticks from bits 23:0 alone: a record whose tick field is 0
marks the start of a new 2^24-tick epoch, and one is generated at every epoch boundary
while `en`.

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
| HALT (wait) | if the FIFO after retirement at E_c is empty and the checker is not active after E_c: state = HALTED at E_c, PC unchanged, pins retained; else WAIT |

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

Outcome per §6.2. On a WAIT caused by a full FIFO, `wait_full` (16 b, saturating)
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
| CAPT rs | configure the capture unit from `v = R[rs]` (below) | — |
| GETC rd | R[rd] = capture status during cycle c: [31] en, [30] ovf, [29] full, [28] wrapped, [9:8] staged count, [7:0] ptr; other bits 0 | Z |

LDT and STT never WAIT. **Implementation note (LDT):** the memory is read at E_c and
R[ra] is physically written at E_(c+1). No instruction can observe the difference (the
context's next commit is at c+4 or later); a host debug read during cycle c+1 may see the
old value. The observation record of the LDT commit shows the loaded value.

**CAPT** at E_c with `v = R[rs]`: if `v[31]` (enable): `en = 1`, `snap = 1`,
`mask = v[23:16]`, `lim = v[15:8]`, `base = v[7:0]`, `ring = v[24]`, `ptr = base`,
`ovf = full = wrapped = 0`, `ovf_tick = 0`, staging emptied (so the first record,
generated at E_(c+1), is a snapshot). If `v[31] = 0`: `en = 0` and `snap = 0` (no record
is generated at E_c); the other capture registers keep their values after §6.5 step 1,
and staged records still drain.

## 8. Faults

A FAULT outcome at E_c (ILLEGAL, PC_RANGE, ADMIT) or a LATE_HEAD at E_t makes the
context `FAULTED` at that edge, with these effects:

- The context's FIFO is flushed. No entry of this context retires at that edge, even
  one due then.
- Its checker is deactivated without evaluating tick t at that edge (no
  pass/fail count or first_fail update; v1.0.2).
- For each pin i owned by the context: `oe[i] <= 0` (released). `val` is unchanged.
- If the first-fault register is empty, it records {cause, subcode (ADMIT only, else
  0), ctx, PC of the faulting instruction (for LATE_HEAD: PC during cycle t), tick}.

Other contexts are unaffected.

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

**Stop** at E_t (one-cycle `stop` pulse during cycle t):
- All RUNNING contexts become HALTED and all FIFOs are flushed; nothing due at E_t
  retires.
- All checkers are deactivated, and `oe = 0` on every pin.
- run = 0 and `now` stops.

Instructions in flight are discarded. A pending LDT register write (§7.6) still happens.
`dg` stops changing; capture generation stops (`en = 0`) and staged records drain.

## 10. Observation interface (verification only)

Both models produce the same records. A commit at tick c produces:

```
{t: c, ctx, pc, insn, outcome: C|W|F,
 post: {r0, r1, r2, r3, sh, lc, tmr, z, c, pc}}   # state of ctx after E_c
```

Every run cycle t produces `{t, uio_out, uio_oe}`. The end of a run produces final
per-context state (state, registers, ish, telemetry, checker status, IRQ) and the
first-fault register.

**RTL ports** (core module `esc_core`): `dbg_commit_valid`, `dbg_commit_ctx[1:0]`,
`dbg_commit_pc[7:0]`, `dbg_commit_insn[31:0]` and `dbg_commit_outcome[1:0]` (0 C, 1 W,
2 F) describe a commit at E_c and are valid **during cycle c+1**. During that same cycle,
`dbg_post_*` show the committed context's registers (post-commit): `dbg_post_r0..r3`,
`dbg_post_sh`, `dbg_post_lc[15:0]`, `dbg_post_tmr`, `dbg_post_z`, `dbg_post_c`,
`dbg_post_pc[7:0]`. A combinational read port `dbg_sel_ctx[1:0]`, `dbg_sel[3:0]` →
`dbg_rdata[31:0]` exposes current state. The ISS writes the same fields as JSON lines.

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
| 18 | capture config: [24] ring, [23:16] mask, [15:8] lim, [7:0] base |
| 19 | capture `ovf_tick` |

`dbg_sel` is 5 bits wide in v1.1 (16–19 ignore `dbg_sel_ctx`; 20–31 read 0).

### 10.1 Execution digest (v1.1)

A 32-bit signature of the run, so a host can check silicon, gates, RTL and ISS against
each other with one read. At an edge E_t of a run (not a start or stop edge) the digest
is updated **if the edge is active**: a commit is observed at E_t, an LDT completed at
E_(t-1), or the outputs during t differ from the outputs during t−1 (outputs during
cycle 0 are compared with 0). Otherwise `dg` is unchanged. So once every context has
stopped (HALT/FAULT) with its FIFO empty, `dg` is final: it does not depend on when the
host's STOP arrives.

```
dg  <= ((dg << 1) mod 2^32) XOR (dg[31] ? 0x04C11DB7 : 0) XOR x_t
x_t  = ((uio_oe_t << 24) | (uio_out_t << 16)) XOR cw_t XOR wv_t   (outputs during t, §5)
cw_t = a commit observed at E_t ? (outcome << 14) | (ctx << 12) | pc : 0
       (outcome 0 COMPLETE, 1 WAIT, 2 FAULT, as dbg_commit_*)
wv_t = XOR of every register value written at E_t: the rd result of a COMPLETE
       MOV, MOVI, MOVHI, ADD, ADDI, SUB, AND, OR, XOR, SHL, SHR (n = 0: the unchanged
       value), STSH, RDTIME, GETP, GETI, GETX, POP or GETC committing at E_t, and the
       loaded value of an LDT that completed at E_(t-1)
```

`dg = 0xFFFFFFFF` at start; it does not change while stopped.

**Core ports** (besides the debug ports; the v1.1 tape port is the last item):
- Clock and control: `clk`, `rst_n`, `start`, `stop`.
- Configuration: `ctx_enable[3:0]`, `entry_pcs[31:0]` (ctx k at [8k+7:8k]),
  `pin_owned[7:0]`, `pin_owner[15:0]` (pin i at [2i+1:2i]), `pin_od[7:0]`.
- Instruction memory: `imem_addr[7:0]` out, `imem_rdata[31:0]` in.
- Pins: `uio_in[7:0]`, `uio_out[7:0]`, `uio_oe[7:0]`.
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

- Tape (v1.1): `tape_addr[7:0]`, `tape_we`, `tape_wdata[31:0]` out, `tape_rdata[31:0]`
  in (same timing contract as `imem_rdata`), `tape_busy` out (high in every cycle the
  core may use the port: during a run and while staged records drain). The top gives
  the host the port only when `tape_busy` is low. `dbg_sel` is 5 bits.

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
