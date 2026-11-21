# Instruction set

This is the programmer's reference for the Escapement instruction set: what each
instruction does, how it is encoded, and the few timing rules a program has to respect.
Every encoding comes from [`isa/isa.yaml`](../isa/isa.yaml), the one file that the
assembler, the instruction-set simulator and the RTL decoder all read, so the three cannot
disagree about an opcode or a field. The cycle-exact behaviour of each instruction is
defined in [11-SEMANTICS](11-SEMANTICS.md) §7, and the machine it runs on is described in
[01-ARCHITECTURE](01-ARCHITECTURE.md).

## The programmer's model

A program runs in one of four **contexts**. Each context has:

- four 32-bit general registers, `r0` to `r3`;
- a 32-bit shift register `sh`, from which EMITX sends bits and into which data can be
  loaded with LDSH;
- a 16-bit loop counter `lc`, used by DJNZ;
- the 32-bit deadline register `tmr`, which supplies the tick of every event the context
  queues;
- an 8-bit program counter and an 8-bit link register for CALL and RET, 9-bit when the
  context spans (below);
- the flags Z (zero) and C (carry or borrow);
- a 32-bit input shift register `ish`, filled by SAMPLE events and read by GETI.

A context commits one instruction every **4 ticks**, or every 2 in pair mode, where a
tick is one clock cycle (40 ns at 25 MHz, 20 ns at 50 MHz). Because a program's
instruction timing does not depend on the other contexts, the time between any two of its
instructions can be counted from its own code, which is what the certificate checker
`escert` does.

Pins never change as a direct result of an instruction. An instruction queues an
**event** with an absolute deadline taken from `tmr`, and the event changes the pins on
exactly that tick. Every instruction names pins as **eight logical pins**. A context on
bank A sees the bidirectional pins `uio[7:0]` as its pins 0 to 7; a context on bank B sees
the five split pins B0 to B4 (outputs `uo_out[7:3]`, inputs `ui_in[7:3]`) as its pins 0
to 4. The bank is part of the configuration, not of the code, so one protocol module runs
on either bank without change.

A context fetches from its own **page** of the program store: 256 words, addressed by the
8-bit program counter. Branch targets are word addresses within the page. A context whose
partner (the context numbered k XOR 2) is not enabled can **span** (3.1): it also fetches
from the partner's page, which would otherwise sit idle, and its program counter and link
register grow to 9 bits, so its program has 512 words, addresses 256 to 511 being the
partner's page. Code runs straight on from word 255 to word 256. Span is asked for per
context, so a program that does not ask behaves exactly as before. It is independent of
pair mode, which also needs the partner off: a context can pair, span, both or neither.
The **tape** is a separate memory of 256 words that every context can load from and store
to.

## Encoding

Every instruction is one 32-bit word, with the opcode in bits [31:24] and a payload in
[23:0]. Payload bits that the instruction's format does not use are **reserved** and must
be zero: a word with a nonzero reserved bit, or with an opcode that does not exist, faults
as ILLEGAL. So do the words `0x00000000` and `0xFFFFFFFF`, which means that a jump into
erased or uninitialised memory stops the context with a fault instead of executing
garbage.

| Format | Payload fields |
| --- | --- |
| N | none |
| I8 | `[7:0] code` |
| R | `[23:22] rd`, `[21:20] rs` |
| RD / RS | `[23:22] rd` / `[21:20] rs` |
| I16 | `[23:22] rd`, `[15:0] imm` |
| SH | `[23:22] rd`, `[4:0] n` |
| B | `[8:0] target` (a word address in the context's page; bit 8, the partner's page, only when the context spans, and ILLEGAL otherwise) |
| L16 | `[15:0] imm` |
| T24 | `[23:0] imm` (unsigned ticks) |
| P | `[23:16] mask`, `[15:8] value`, `[7:0] oe` |
| PR | `[23:16] mask`, `[15:14] rs`, `[7:0] oe` |
| PX | `[23:16] mask`, `[15] dir`, `[7:0] oe` |
| PE | `[23:16] mask`, `[15:8] level`, `[7] mode`, `[6:0] window` |
| PS | `[18:16] pin`, `[15] dir` |
| PW | `[23:16] mask`, `[15:8] value` |
| TM | `[23:22] ra`, `[21:20] rb`, `[7:0] imm` |

`dir` is 0 for least significant bit first and 1 for most significant bit first; the
assembler accepts the names `LSB` and `MSB`. `mode` is 0 for HOLD and 1 for ARRIVE.

## Instructions

Instructions marked **W** are **wait-class**: when they cannot complete yet, they keep
their program counter and are tried again at the context's next slot. A wait therefore
ends within one slot period (four ticks, or two when paired) of its condition, which is
why waits are used to pace a program and events are used for anything that must be exact.

### Data

| Op | Instruction | Format | Effect | Flags |
| --- | --- | --- | --- | --- |
| 08 | `MOV rd, rs` | R | rd = rs | Z |
| 09 | `MOVI rd, imm16` | I16 | rd = imm, zero-extended | Z |
| 0A | `MOVHI rd, imm16` | I16 | rd[31:16] = imm, the low half kept | Z |
| 0B | `ADD rd, rs` | R | rd = rd + rs | Z, C = carry |
| 14 | `ADDI rd, imm16` | I16 | rd = rd + imm | Z, C = carry |
| 0C | `SUB rd, rs` | R | rd = rd − rs | Z, C = borrow |
| 10 | `CMP rd, rs` | R | rd − rs, result discarded | Z, C = borrow |
| 15 | `CMPI rd, imm16` | I16 | rd − imm, result discarded | Z, C = borrow |
| 0D / 0E / 0F | `AND` / `OR` / `XOR rd, rs` | R | bitwise | Z |
| 11 | `TSTI rd, imm16` | I16 | Z = 1 if rd AND imm is 0 | Z |
| 12 | `SHL rd, n` | SH | logical shift left by n; C = the last bit shifted out | Z, C |
| 13 | `SHR rd, n` | SH | logical shift right by n; C = the last bit shifted out | Z, C |
| 16 | `LDSH rs` | RS | sh = rs | |
| 17 | `STSH rd` | RD | rd = sh | |

A 32-bit constant takes two instructions, MOVI for the low half and then MOVHI for the
high half.

### Control

| Op | Instruction | Format | Effect |
| --- | --- | --- | --- |
| 01 | `NOP` | N | nothing |
| 18 | `JMP t` | B | PC = t |
| 19 to 1C | `JZ` / `JNZ` / `JC` / `JNC t` | B | branch if the flag condition holds |
| 1E | `LDLC imm16` | L16 | lc = imm |
| 1D | `DJNZ t` | B | lc = lc − 1, then branch if lc is not 0 |
| 1F | `CALL t` | B | link = PC + 1, PC = t |
| 04 | `RET` | N | PC = link |
| 03 | `IRQ code` | I8 | raise a host interrupt with an 8-bit code; it stays raised until the host clears it |
| 02 | `HALT` **W** | N | stop once the context's event queue is empty, its checker idle and any line unit job it owns finished; the pins keep their last state |

### Time and input

| Op | Instruction | Format | Effect |
| --- | --- | --- | --- |
| 20 | `RDTIME rd` | RD | rd = the tick on which this instruction commits |
| 21 | `SETT rs` | RS | tmr = rs |
| 22 | `ADDT imm24` | T24 | tmr = tmr + imm |
| 24 | `ADDTR rs` | RS | tmr = tmr + rs |
| 23 | `WAITT` **W** | N | complete at the first slot at or after tick `tmr` |
| 30 | `GETP rd` | RD | rd = the context's eight synchronised input pins (Z) |
| 31 | `WAITP mask, value` **W** | PW | complete when the masked inputs equal `value` (Z = 1), or at tick `tmr` (Z = 0); a match wins a tie |
| 32 | `GETI rd` **W** | RD | wait until no SAMPLE is still queued, then rd = ish and ish = 0 (Z) |
| 33 | `GETX rd` | RD | rd = the checker's status: fail count, pass count, active, failed since start (Z) |

WAITP with a deadline is the usual way to wait for an input with a timeout: Z tells the
program which of the two happened.

### Events

Every event instruction queues one entry with deadline `tmr`, and waits while the
context's queue (four entries) is full.

| Op | Instruction | Format | Queued event |
| --- | --- | --- | --- |
| 28 | `EMIT mask, value, oe` **W** | P | DRIVE: set the value and output enable of the masked pins |
| 29 | `EMITR mask, rs, oe` **W** | PR | DRIVE with the value taken from rs[7:0] |
| 2A | `EMITX mask, dir, oe` **W** | PX | DRIVE every masked pin with the next bit shifted out of `sh`; C = that bit |
| 2B | `EXPECT mask, level, mode, window` **W** | PE | EXPECT: the masked inputs must HOLD `level` for `window` ticks, or ARRIVE at it within `window` ticks |
| 2C | `SAMPLE pin, dir` **W** | PS | SAMPLE: shift that input bit into `ish` |
| 2D | `SEND rs` **W** | RS | LINE: the line unit starts a packet, described by rs[23:0]; Z = 1, or Z = 0 if it was dropped as late (below) |

### Tape and capture

| Op | Instruction | Format | Effect |
| --- | --- | --- | --- |
| 34 | `LDT ra, rb, imm8` | TM | ra = tape[(rb[7:0] + imm) mod 256] |
| 35 | `STT ra, rb, imm8` | TM | tape[(rb[7:0] + imm) mod 256] = ra |
| 36 | `CAPT rs` | RS | configure the capture unit from rs: [31] enable, [26] stream, [25] watch bank B, [24] ring, [23:16] watched pins, [15:8] limit, [7:0] base |
| 37 | `GETC rd` | RD | rd = capture status: [31] enabled, [30] overflow, [29] full, [28] wrapped, [9:8] records staged, [7:0] write pointer (Z) |

The tape address is a register plus a constant, so a loop can walk a buffer with one
register while the constant picks a field.

### Host mailboxes

| Op | Instruction | Format | Effect |
| --- | --- | --- | --- |
| 38 | `POP rd` **W** | RD | take one word from the context's receive mailbox |
| 39 | `PUSH rs` **W** | RS | put one word in the context's transmit mailbox |
| 3A | `RXREADY` | N | Z = 1 if the receive mailbox is empty |
| 3B | `TXREADY` | N | Z = 1 if the transmit mailbox is full |

### Line unit

The line unit does the per-bit work of packet protocols between the tape and the pins:
NRZI, bit stuffing, symbol coding, CRCs of up to 32 bits, and on receive a choice of
strobe (a recovered clock, a cell decoder for Manchester codes, or an external clock).
[19-LINE-UNIT](19-LINE-UNIT.md) explains it. There is one line unit, and the first context
whose CONFIG commits in a run owns it; another context that uses it faults ADMIT/OWNER.

| Op | Instruction | Format | Effect |
| --- | --- | --- | --- |
| 3C | `LINE rs` **W** | RS | rs[31:28] selects the job: 0 OFF; 1 RECV into tape word rs[7:0], at most rs[12:8] bytes (units of 32 bytes in long mode); 2 CRC over rs[15:8] bits from word rs[7:0]; 3 CONFIG from three tape words at rs[7:0], or five when W2 bit 25 is set. Waits while a job runs, except OFF |
| 3D | `GETL rd` **W** | RD | when the job has ended: rd = status, `tmr` = the tick it ended, Z = 1. If tick `tmr` comes first: rd = status, Z = 0 |

### Bus timing monitor

The monitor checks the timing of edges between any two pins, whoever drives them, against
four rules of the form "the next edge of pin B comes MIN to MAX ticks after an edge of pin
A". [22-BUS-MONITOR](22-BUS-MONITOR.md) explains it. The first context whose MON commits in
a run owns the monitor.

| Op | Instruction | Format | Effect |
| --- | --- | --- | --- |
| 3E | `MON rd, rs` | R | write rule rd[30:29] from rd and rs (below), or clear the counts and the record |
| 3F | `GETM rd, sel` | I16 | rd = monitor or line unit state, chosen by `sel` (below) |

The rule written by MON:

| Register | Bits | Field |
| --- | --- | --- |
| rd | [31] | enable the rule |
| rd | [30:29] | rule number, 0 to 3 |
| rd | [28:25], [24:23] | from-pin (physical pin 0 to 12) and its edge: 1 rise, 2 fall, 3 either, 0 none |
| rd | [22:19], [18:17] | to-pin and its edge |
| rd | [16] | keep: write no rule, only clear |
| rd | [15:0] | MIN ticks (0: no minimum) |
| rs | [31] | clear every count and the record |
| rs | [15:0] | MAX ticks (0: no maximum) |

GETM `sel`: 0 reads the four violation counts, rule k in bits [8k+7:8k]; 1 the record of
the first violation, `{[31] valid, [30] late, [17:16] rule, [15:0] interval}`; 2 that
violation's tick; 3 the line unit's whole received bit count; 4 the line unit's whole CRC
register. Selects 0 to 2 return the monitor's state only to its owner, and 3 and 4 the line
unit's only to that unit's owner; any other context, and any other `sel`, reads 0. A MON
from a context that is not the owner faults ADMIT/OWNER.

## Admission and faults

An event is **admitted**, that is, accepted into the queue, only if it can happen on time
and is allowed to happen at all:

- its mask is not empty, and a DRIVE masks only pins the context owns and the line unit
  has not claimed;
- its deadline is strictly after the tick on which it commits, and strictly after the
  deadline of the event before it in the queue, both within half the 32-bit tick range;
- an EXPECT's window does not overlap the context's previous EXPECT window.

An event that fails a check is never applied late. Instead the context faults, and the
fault's subcode says which check failed. The one exception is a SEND whose deadline has
already passed, which is dropped with Z = 0 instead. A SEND's deadline usually comes from
the line, because GETL sets `tmr` to the end of the packet being answered; if the reply
cannot leave on time, the protocol's own retry is the right response, so the reply is
either on time or not sent at all.

| Cause | Trigger |
| --- | --- |
| ILLEGAL (1) | an opcode that does not exist, or a nonzero reserved bit |
| PC_RANGE (2) | sequential execution, or a CALL's return address, past word 255 of the page (past word 511 for a spanning context) |
| ADMIT (3) | an event or unit request refused; subcodes MASK (1), OWNER (2), PAST (3), ORDER (4), OVERLAP (5), CONFIG (6, an invalid line unit configuration) |
| LATE_HEAD (4) | an event at the head of the queue found past its deadline; unreachable, and proved so |

A fault empties the context's queue, releases its pins and stops it. The first fault in a
run is recorded with its cause, subcode, context, program counter and tick. The other
contexts carry on.

## Timing rules

Three rules cover almost every timing question a program raises.

- **Queue early, not on time.** An event must commit at least one tick before its
  deadline. A program that waits until tick `tmr` and then emits an event at `tmr` is
  always refused, because the deadline has arrived. The right pattern is the reverse:
  emit the event early and let its deadline do the timing.
- **Waits are coarse; events are exact.** A wait-class instruction completes up to one
  slot period after its condition holds (zero to three ticks late, unpaired). Anything
  that must happen on an exact tick goes through an event.
- **Pins and inputs have fixed delays.** A pin driven by a DRIVE with deadline d shows its
  new value from cycle d + 1. An input is seen by the program two ticks after it reaches
  the pin, through the synchroniser. Both delays are constant, so they cancel whenever a
  program measures one input edge against another.

## Assembly

The assembler (`sw/escasm`) is a two-pass assembler with labels, constants and
expressions. Its directives:

| Directive | Meaning |
| --- | --- |
| `.equ NAME, expr` | a constant |
| `.default NAME, expr` | a constant that an including file may set first: a module's parameter |
| `.include "file"` | assemble another file here, with its own namespace for labels and constants |
| `.org expr`, `.word expr` | set the address; place a raw word |
| `.entry ctx, label` | context `ctx` is enabled and starts at `label` |
| `.own pin, ctx` | context `ctx` owns its logical pin `pin` (`B0` to `B4` name bank B pins) |
| `.od pin` | bank A pin `pin` is open-drain |
| `.bank ctx` | context `ctx` uses bank B (before its `.own` lines) |
| `.pair ctx` | context `ctx` also takes the slot of context ctx XOR 2, which must not be enabled |
| `.span ctx` | context `ctx` may use 512 words, its own page and that of context ctx XOR 2, which must not be enabled; one per source |
| `.tape addr, words...` | initial tape words, loaded by the host before the run |
| `.tapeb addr, bytes...` | initial tape bytes from `addr`, packed little-endian (USB descriptors, for example) |
| `.assert expr, message` | stop the assembly unless `expr` is nonzero |

Expressions use `+ - * << >> | & ~` and parentheses, and comparisons (`<`, `<=`, `==`,
`!=`, `>=`, `>`) that give 1 or 0 and chain, as in `25000 <= CLK_KHZ <= 25175`. With
`.assert`, a module can state what it assumes about its parameters: the 10BASE-T
transmitter, for example, refuses to assemble unless `CLK_KHZ` is 40000. The names `LSB`,
`MSB`, `HOLD` and `ARRIVE` are keywords in every expression, so the assembler refuses them
as label or constant names, which would otherwise be silently read as 0 or 1.

A bank B pin is push-pull and one-directional. A context drives it (an output on
`uo_out`), or leaves it unowned and reads it (an input on `ui_in`); released, it is high.
[`firmware/combo_two_banks.asm`](../firmware/combo_two_banks.asm) runs four unchanged
modules on both banks at once.

An image assembled from one source fills one page, or two with `.span`. To give each
context its own page, the host tools combine up to four separately assembled images with
`escasm.paged()`, which places image p in page p and refuses two images that enable the same
context, own the same pin or set the same tape word differently. A spanning image takes
pages p and p + 1: its words 256 to 511 go to page p + 1, which becomes the page of the
spanning context's partner, and `paged()` refuses another image there or one that enables
the partner. [OLED Pong](23-OLED-PONG.md) runs this way, on
four pages.

## An example: UART transmit

This is the inner loop of [`firmware/uart_tx.asm`](../firmware/uart_tx.asm), which sends a
byte at 115,200 baud; at 25 MHz one bit is 217 ticks.

```asm
next_byte:
    LDSH  r0
    EMIT  TX, 0, TX           ; start bit
    ADDT  BIT
    LDLC  8
bit:
    EMITX TX, LSB, TX         ; data bit (waits while the queue is full)
    ADDT  BIT
    DJNZ  bit
    EMIT  TX, TX, TX          ; stop bit
    ADDT  BIT
```

Each data bit takes three instructions, 12 ticks, against a bit time of 217 ticks. The
program therefore runs far ahead of the line until the queue holds four events, after
which each EMITX waits for space and the queue paces the loop. The program's own timing
never reaches the pin: every edge lands on the tick that `tmr` named for it.
