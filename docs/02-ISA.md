# ISA reference

The programmer's view of the Escapement instruction set, version 1.1. Encodings come
from [`isa/isa.yaml`](../isa/isa.yaml), the single source for the assembler, the
simulator and the RTL decoder. The exact, cycle-level semantics are in
[11-SEMANTICS](11-SEMANTICS.md) §7; where this page is shorter, that page is the
definition.

## Machine model

- Four contexts, each with registers `r0`–`r3` (32 bit), shift register `sh` (32),
  loop counter `lc` (16), deadline register `tmr` (32), PC and link register (8), flags
  Z and C, and input shift register `ish` (32).
- A context commits one instruction every **4 ticks** (one tick is one clock cycle,
  40 ns at 25 MHz). An instruction fetched in cycle t commits at tick t + 3.
- Pins change only through **events** queued with an absolute deadline from `tmr`.
- Program store and tape: 256 words of 32 bits each.

## Encoding

Every instruction is one 32-bit word: `[31:24]` opcode, `[23:0]` payload. Payload bits
that the format does not name are reserved and must be zero. A nonzero reserved bit or
an unlisted opcode is ILLEGAL, and so are `0x00000000` and `0xFFFFFFFF`, so erased or
uninitialised memory traps instead of executing.

| Format | Payload fields |
| --- | --- |
| N | none |
| I8 | `[7:0] code` |
| R | `[23:22] rd`, `[21:20] rs` |
| RD / RS | `[23:22] rd` / `[21:20] rs` |
| I16 | `[23:22] rd`, `[15:0] imm` |
| SH | `[23:22] rd`, `[4:0] n` |
| B | `[7:0] target` (absolute word address) |
| L16 | `[15:0] imm` |
| T24 | `[23:0] imm` (unsigned ticks) |
| P | `[23:16] mask`, `[15:8] value`, `[7:0] oe` |
| PR | `[23:16] mask`, `[15:14] rs`, `[7:0] oe` |
| PX | `[23:16] mask`, `[15] dir`, `[7:0] oe` |
| PE | `[23:16] mask`, `[15:8] level`, `[7] mode`, `[6:0] window` |
| PS | `[18:16] pin`, `[15] dir` |
| PW | `[23:16] mask`, `[15:8] value` |
| TM | `[23:22] ra`, `[21:20] rb`, `[7:0] imm` |

`dir`: 0 = LSB first, 1 = MSB first. `mode`: 0 = HOLD, 1 = ARRIVE.

## Instructions

**W** marks wait-class instructions: they keep their PC and are re-evaluated at the
context's next slot until they complete.

### Data

| Op | Instruction | Format | Effect | Flags |
| --- | --- | --- | --- | --- |
| 08 | `MOV rd, rs` | R | rd = rs | Z |
| 09 | `MOVI rd, imm16` | I16 | rd = zero-extended imm | Z |
| 0A | `MOVHI rd, imm16` | I16 | rd[31:16] = imm, low half kept | Z |
| 0B | `ADD rd, rs` | R | rd = rd + rs | Z, C carry |
| 14 | `ADDI rd, imm16` | I16 | rd = rd + imm | Z, C carry |
| 0C | `SUB rd, rs` | R | rd = rd − rs | Z, C borrow |
| 10 | `CMP rd, rs` | R | rd − rs, result discarded | Z, C borrow |
| 15 | `CMPI rd, imm16` | I16 | rd − imm, result discarded | Z, C borrow |
| 0D / 0E / 0F | `AND` / `OR` / `XOR rd, rs` | R | bitwise | Z |
| 11 | `TSTI rd, imm16` | I16 | Z = (rd & imm) == 0 | Z |
| 12 | `SHL rd, n` | SH | logical shift left, C = last bit out | Z, C |
| 13 | `SHR rd, n` | SH | logical shift right, C = last bit out | Z, C |
| 16 | `LDSH rs` | RS | sh = rs | |
| 17 | `STSH rd` | RD | rd = sh | |

### Control

| Op | Instruction | Format | Effect |
| --- | --- | --- | --- |
| 01 | `NOP` | N | nothing |
| 18 | `JMP t` | B | PC = t |
| 19–1C | `JZ` / `JNZ` / `JC` / `JNC t` | B | branch if the flag condition holds |
| 1E | `LDLC imm16` | L16 | lc = imm |
| 1D | `DJNZ t` | B | lc = lc − 1; branch if lc ≠ 0 |
| 1F | `CALL t` | B | link = PC + 1, PC = t |
| 04 | `RET` | N | PC = link |
| 03 | `IRQ code` | I8 | raise a sticky host interrupt with an 8-bit code |
| 02 | `HALT` **W** | N | halt once the context's FIFO is empty and its checker idle; pins keep their state |

### Time and input

| Op | Instruction | Format | Effect |
| --- | --- | --- | --- |
| 20 | `RDTIME rd` | RD | rd = commit tick |
| 21 | `SETT rs` | RS | tmr = rs |
| 22 | `ADDT imm24` | T24 | tmr = tmr + imm |
| 24 | `ADDTR rs` | RS | tmr = tmr + rs |
| 23 | `WAITT` **W** | N | complete at the first slot at or after tmr |
| 30 | `GETP rd` | RD | rd = synchronised input vector (Z) |
| 31 | `WAITP mask, value` **W** | PW | complete when `(in & mask) == value` (Z = 1) or at tmr (Z = 0); a match wins a tie |
| 32 | `GETI rd` **W** | RD | wait until no SAMPLE is in flight, then rd = ish and clear ish (Z) |
| 33 | `GETX rd` | RD | rd = checker status: fail count, pass count, active, failed since start (Z) |

### Events

All event instructions wait while the context's FIFO is full. The deadline is `tmr`.

| Op | Instruction | Format | Queued entry |
| --- | --- | --- | --- |
| 28 | `EMIT mask, value, oe` **W** | P | DRIVE: set value and output enable on the masked pins |
| 29 | `EMITR mask, rs, oe` **W** | PR | DRIVE with value = rs[7:0] |
| 2A | `EMITX mask, dir, oe` **W** | PX | DRIVE with every masked pin = the bit shifted out of sh; C = that bit |
| 2B | `EXPECT mask, level, mode, window` **W** | PE | EXPECT: the masked inputs HOLD `level` for, or ARRIVE at it within, `window` ticks |
| 2C | `SAMPLE pin, dir` **W** | PS | SAMPLE: shift that input bit into ish |

### Tape and capture

| Op | Instruction | Format | Effect |
| --- | --- | --- | --- |
| 34 | `LDT ra, rb, imm8` | TM | ra = tape[rb[7:0] + imm] |
| 35 | `STT ra, rb, imm8` | TM | tape[rb[7:0] + imm] = ra |
| 36 | `CAPT rs` | RS | configure capture from rs: [31] enable, [24] ring, [23:16] watched pins, [15:8] limit, [7:0] base |
| 37 | `GETC rd` | RD | rd = capture status: [31] enabled, [30] overflow, [29] full, [28] wrapped, [9:8] staged, [7:0] pointer (Z) |

### Host mailboxes

| Op | Instruction | Format | Effect |
| --- | --- | --- | --- |
| 38 | `POP rd` **W** | RD | take one word from the host's RX mailbox |
| 39 | `PUSH rs` **W** | RS | put one word in the TX mailbox |
| 3A | `RXREADY` | N | Z = RX mailbox empty |
| 3B | `TXREADY` | N | Z = TX mailbox full |

## Admission and faults

An event is admitted only if its mask is nonempty, a DRIVE selects only pins the context
owns, and its deadline is strictly after both the commit tick and the previous entry's
deadline (within half the 32-bit range). EXPECT windows of one context may not overlap.
A failed check never applies the event late: the context faults.

| Cause | Trigger |
| --- | --- |
| ILLEGAL (1) | Unlisted opcode or nonzero reserved bit |
| PC_RANGE (2) | Sequential execution or CALL past word 255 |
| ADMIT (3) | Subcodes MASK (1), OWNER (2), PAST (3), ORDER (4), OVERLAP (5) |
| LATE_HEAD (4) | A queue head past due; unreachable, and proved so |

A fault flushes the context's FIFO, releases its pins and halts it. The first fault in
a run is recorded with cause, subcode, context, PC and tick. Other contexts continue.

## Timing rules

- An event must be committed at least one tick before its deadline. "Wait until `tmr`,
  then emit at `tmr`" is always refused: emit early and let the deadline do the timing.
- Wait-class completion is quantised to the context's 4-tick slot grid (0–3 ticks after
  the condition). Exact-tick output always uses events.
- A pin driven by a DRIVE with deadline d shows its new value from cycle d + 1. Inputs
  are seen two ticks after they reach the pad.

## Assembly

The assembler (`sw/escasm`) supports labels, expressions, `.equ`, and module directives:
`.entry ctx, label` sets a context's entry point, `.own pin, ctx` assigns a pin, `.od pin`
makes it open-drain, and `.include` with `.default` parameters links protocol modules
into one image with a separate namespace each.

UART transmit at 115,200 baud (217 ticks per bit at 25 MHz), from
[`firmware/uart_tx.asm`](../firmware/uart_tx.asm):

```asm
next_byte:
    LDSH  r0
    EMIT  TX, 0, TX           ; start bit
    ADDT  BIT
    LDLC  8
bit:
    EMITX TX, LSB, TX         ; data bit (waits while the FIFO is full)
    ADDT  BIT
    DJNZ  bit
    EMIT  TX, TX, TX          ; stop bit
    ADDT  BIT
```

The bit loop is three instructions (12 ticks) per 217-tick bit. The FIFO fills four
events ahead and then paces the loop, so every edge lands on its exact tick.
