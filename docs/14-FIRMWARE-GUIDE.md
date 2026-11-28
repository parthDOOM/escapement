# Writing Escapement firmware: idioms and one anti-pattern

A new protocol on Escapement is a new program, so this chapter collects what the shipped
firmware in [firmware/](../firmware) has learned about writing one: the idioms that keep
edges on time, the one pattern that reliably breaks them, and how to budget a protocol's
instructions. Every rule here is checked by the certificate checker,
`python -m escert file.asm`, which should be run before trusting any timing; the machine
itself is defined in [11-SEMANTICS](11-SEMANTICS.md).

A transmitter that is only timing (framed serial, pulse-width and burst codes) need not
be written by hand: describe it as a timing table and `python -m escgen` writes and
certifies the firmware ([protocols as data](18-PROTOCOLS-AS-DATA.md)). The idioms below
are what it generates, and what receivers and two-way buses still need by hand.

## The timing model in one paragraph

Each context commits one instruction every 4 ticks (40 ns ticks at 25 MHz), or every 2
when it is paired (idiom 15). Wait-class instructions repeat on a 4-tick grid. Pins change only through events with
absolute deadlines taken from `tmr`: a DRIVE, or the line unit started by a SEND's LINE event;
EXPECT and SAMPLE events watch pins without changing them. The chip checks each event at
enqueue: its deadline must be after the commit tick and after the previous queued
event, otherwise the context faults (ADMIT) and its pins release. An admitted event
happens exactly on its tick. Firmware therefore runs **ahead** of real time, by up to
four queued events.

The chip is rated up to 50 MHz (D-5). Programs that take their timing from `CLK_KHZ`
rebuild for another clock by defining it before the include, for example
`.equ nec_tx.CLK_KHZ, 50000` then `.include "nec_tx.asm"`. Of the 90 programs, 74 rebuild and certify at 33, 33.333, 40 and 50 MHz; four are tied to one clock by their protocol (10BASE-T transmit at 40 MHz, USB low speed at 24 MHz, VGA at its 25 MHz pixel clock); four need a whole number of ticks per bit (`eth_rx` at 40 or 50 MHz, XY2-100 and the MIL-STD-1553B terminal from 40 to 50 MHz in 2 MHz steps, FlexRay at 2.5 Mbit/s from 25 to 50 MHz in 2.5 MHz steps); and eight count in ticks or follow another device's clock, among them the S/PDIF transmitter, whose clock must be 128 × fs × its cell length. Rerun
`python -m escert` after any rebuild: a delay that fits at one clock can outgrow a
16-bit compare, ADDT's 24 bits or the 2^16-tick gap escert can bound at a faster one.

## Idioms

**1. Accumulate for periodic streams.** For a fixed bit rate, add a constant to `tmr`
per bit (`ADDT BIT`). When the FIFO fills, the loop paces itself on the hardware. Used
by `uart_tx`, `ws2812`, `ps2_device` and `jtag_idcode`.

**2. Re-anchor after anything unbounded.** After `WAITP`, `POP`, `PUSH` or a data-dependent
loop, take a fresh time base:

```text
RDTIME r1
ADDI   r1, LAT        ; LAT = 4 * (instructions from RDTIME to the EMIT) + margin
SETT   r1
EMIT   ...            ; 3 instructions after RDTIME: LAT >= 13; 14 gives lead 2
```

A short `LAT` is refused (ADMIT/PAST), never emitted late. escert reports the resulting
lead exactly.

**3. Drain before re-anchoring (the anti-pattern this prevents).** A transaction that
ends with events still queued, followed by a new transaction that re-anchors on
`RDTIME`, puts new deadlines before old ones. The chip refuses the new transaction with
ADMIT/ORDER. End each transaction with `WAITT`, so the next one starts on an empty
queue. escert found this bug three times, in code that passed every test because the
tests never sent two requests back to back:
- `i2c_ctrl`: a timeout handler with `LAT` too short;
- `ps2_device`: back-to-back scancodes;
- `jtag_idcode`: back-to-back requests.

`swd_idcode` applies the same rule inside one packet: each segment (request,
turnaround and ACK, data, parity) starts on an empty queue with a fresh time base, so
escert proves each segment on its own.

**4. Make WAITP exact.** `WAITP` completes on a match or when `tmr` is reached. Keep
`tmr` far in the future (`ADDT 0xFFFFFF` after each EMIT) and it completes on the first
4-tick slot that sees the edge. Then it is a precise wait, not an 8-tick poll loop.
`i2c_eeprom` meets Fast-mode I2C this way.

**5. Let data choose the edge.** `EMITX` drives a pin from the shifted-out bit, so a
data-dependent waveform needs no branch: WS2812 pulse widths, open-drain I2C data,
SPI MOSI, JTAG TMS paths.

**6. Sample asynchronous inputs at exact ticks.** `SAMPLE pin` at a computed deadline
(UART RX bit centres, the CAN ACK slot, JTAG TDO before the rising edge) has no
quantisation. `GETI` waits until every queued SAMPLE has landed.

**7. Compose, then play.** For frames that need computation (CRC, bit stuffing), build
the bit stream in the tape first, with no deadline, then play it with one `EMITR` per
bit. `can_tx` computes CRC-15 and stuffing this way and still runs at 250 kbit/s.

**8. Stretch, then think (clocked targets).** As an I2C target, hold SCL low as soon as
the 8th falling edge is seen (≤ 28 ticks), then do the slow work while the bus waits.

**9. Put each protocol on the bank that suits it.** Bank A pins are bidirectional and
can be open-drain: I2C, PS/2, 1-Wire, SWD's SWDIO, anything that turns a line around.
Bank B pins are push-pull and one-directional: clocks, chip selects, data out, data in.
SPI controller, UART, JTAG, WS2812 and NEC fit bank B. A module needs no change to
move: put `.bank CTX` before its `.include`, and its `.own PIN, CTX` names the bank B
pin. A released bank B output is high, so drive active-high lines to their idle level
first.

```text
.bank 2
.equ spi_ctrl.CTX, 2
.equ spi_ctrl.SCK_PIN, 0         ; B0 = uo_out[3]
.equ spi_ctrl.MISO_PIN, 2        ; B2 = ui_in[5], an input: not owned
.include "spi_ctrl.asm"
```

**10. Reply from the line.** With the [line unit](19-LINE-UNIT.md), a reply's
deadline is the end of the packet it answers plus a constant: `GETL` sets `tmr` to that
end tick, so `ADDT TURN` then `SEND` puts the reply exactly TURN ticks after the
packet, whatever ran in between. Three rules from `usb_hid` keep it on time:

- *Re-arm first.* `LINE` RECV straight after `GETL`, before deciding what the packet was,
  so a host that sends its next packet quickly is not missed. A path that goes back to
  `GETL` without re-arming gets the old packet again.
- *Keep the decision short.* The path from `GETL` to `SEND` must fit in TURN. A late
  `SEND` is dropped rather than faulted, so no test failure shows a path that is too
  long; the certificate does: escert reports the lead of every `SEND`, or "MAY DROP".
- *Answer what comes.* Waiting for an ACK, any other packet is the host's next one:
  dispatch it at once instead of waiting again.

```text
next: ADDT  FAR
      GETL  r1                ; a packet ended; tmr = its end tick
      JNZ   next              ; (a timeout)
      LDT   r2, r3, RXB
      LDT   r0, r3, K_RECV
      LINE  r0                ; armed again at once
      ...                     ; what was it? (usb_hid: 9 to 14 instructions, GETL to SEND)
      ADDT  TURN
      SEND  r0                ; exactly TURN after the host's packet
```

**11. Keep the host out of the timed context.** A `POP` waits for the host, so a timed
loop may only pop where `RXREADY` has just seen a word: only the context itself pops its
mailbox, so the word is still there, and escert certifies that `POP` as not
waiting (`servo` takes one update a frame this way, and `pwm_audio` one word of four
samples a sample, into a ring on the tape). Even so, a frame built while values
arrive can mix two of them. Give the host its own context: it
takes words from the mailbox and stores them in the tape; the timed context reads the tape
once per frame and waits on nothing but its own events. One `STT` is atomic, so a frame
that fits in a word cannot mix values (`sent_tx`). For more, build the next frame in a
second buffer, switch a pointer word, then wait one frame period before rebuilding the old
buffer, so that no frame still reads it (`dshot_tx`, four motors from one context).

**12. Time a data-dependent length by its bits.** `ADDTR` with a computed value has no
certificate. Test each bit of the value and `ADDT` its weight instead (`TSTI r1, 1`,
`JZ`, `ADDT UT`, then 2, 4, 8): every path adds a constant, and escert stays exact
(`sent_tx`'s nibble pulses and its pause pulse, `pwm_audio`'s high time of 8 + *s* ticks
for an 8-bit sample *s*).

**13. Leave on failure; do not return a flag.** A subroutine that checks a timing window
and returns Z for success merges both outcomes at its return site, and escert loses which
one left the edge register fresh. Jump to the abort path from inside it instead (`cec`'s
`edge`).

**14. Start a far-off reply close to its deadline.** A reply 7.5 ms ahead queued at once
leaves its events far ahead of the clock, and a loop of them loses escert's upper bound on
the lead. Anchor, compute the reply, `WAITT` until shortly before it, then queue it (`dali`).

**15. Pair a busy context.** `.pair CTX` gives context CTX the slot of its partner,
context CTX ^ 2 (0 with 2, 1 with 3), which must stay off: the context then commits every
2 ticks instead of 4. Its code is unchanged; escert certifies it paired, so rerun it and
take the new bound. The cost is the partner's context, so a chip has two fast contexts,
or one fast and two normal, instead of four normal. Three things do not speed up: a wait
(`WAITT`, `WAITP`, `POP`, a full FIFO) still retries every 4 ticks, the instruction after
an `LDT` commits 4 ticks after it, and nothing timer-bound changes. On the ISS against the
bus models, pairing lowers the minimum clock about 2x for instruction-bound firmware (SWD,
JTAG, WS2812, DShot, I2C fast mode, SPI controller, CAN transmit) and leaves UART, SENT,
PS/2, CEC and 1-Wire where they were. A paired image needs a chip that reports protocol
version 9; `sw/eschost` refuses to load it onto an older one.

```text
.pair 0                          ; context 2 stays off
.equ swd_idcode.CTX, 0
.include "swd_idcode.asm"
```

**16. Count the lag from an input edge.** A `WAITP` that sees an edge commits 2 to 5 ticks
after it: the 2-tick synchroniser, then up to one 4-tick slot. Each instruction after it
adds 4 more, branches included. So an `RDTIME` taken k instructions after the `WAITP`
reads the edge's tick + 3.5 + 4k on average. Subtract that, and what is left is the slot's
quantisation: -1.5 to +1.5 ticks, or -2 to +1 when the target is a whole tick.

- `uart_rx`, `sbus_rx` and `modbus_rtu` put their first sample 1.5 bits after the edge this
  way: `RDTIME` two instructions after the `WAITP`, then `ADDI` of `(3 * BIT - 23) // 2`
  (`uart_rx` writes it as `FIRST - 8`).
- `test_receivers_sample_at_the_bit_centre` measures every phase.
- The `JNZ` that handles a `WAITP` timeout counts. `uart_rx` once left it out, the other
  two copied its constant, and all three sampled 3.5 ticks late on average.

When the exact tick matters more than reaction time, use the capture unit. It stamps
every change of a watched pin with its tick, all delayed alike by the synchroniser. That
is how `freq_meter` measures to one tick.

**17. Check the other side's timing with the monitor.** When the chip only watches a bus,
EXPECT cannot help, because it measures from the program's own deadlines. Write the
datasheet's figures as monitor rules instead: a setup time is a rule from a data edge to
the clock's sampling edge with a MIN, a hold time the reverse, a pulse width a rule from a
rise to a fall of one pin, a period a rule from one rise to the next, a timeout a rule with
only a MAX. Write the rules once with MON and poll the record with GETM; clear it with a
MON whose keep bit is set, so the clear rewrites no rule mid-pulse. GETM sets Z from the
value it reads, as GETP, GETI, GETX and GETC do, so `GETM r0, 0` and a `JZ` skip the report while no rule has
counted a violation; test the record's `valid` bit (bit 31) rather than the whole word,
since a cleared record keeps its other fields. Limits are whole ticks,
so round a minimum down: a bus inside its limits then never reports
(`i2c_timing`, [22-BUS-MONITOR](22-BUS-MONITOR.md)).

**18. Choose the receive strobe by how the line is clocked.** With the line unit, a line
with frequent edges in a known rhythm (UART-like, USB, CAN) uses the bit timer; a line
whose every bit has a mid-bit edge (Manchester, 10BASE-T) uses the cell decoder, with S at
about three quarters of a bit and both-edge sampling where the half-bit is not a whole
number of ticks; a bus with its own clock (SPI, a clocked sensor) uses the external clock,
and starts at once when armed, so arm it at the frame's start, on chip select
(`eth_rx`, `spi_sniff`, [19-LINE-UNIT](19-LINE-UNIT.md#receiving-three-strobes)).

**19. Give each program its own page.** Four programs assembled separately can each start
at address 0 and use 256 words; `escasm.paged()` places them on pages 0 to 3 and refuses
two that claim the same context, pin or tape word. Programs that talk to each other do so
through the tape, so agree on the shared words' addresses in one place and check them, as
`test_oled_pong.py` does for the four files of [OLED Pong](23-OLED-PONG.md).

**20. Span two pages when one program outgrows one.** `.span ctx` lets context `ctx` use 512
words: its own page and the page of its partner, context `ctx ^ 2`, which the program must
leave disabled. The assembler then accepts addresses up to 511 and branch targets with bit 8
set, and `escasm.paged()` puts words 256 to 511 on the next page and makes it the partner's.
Nothing else changes: the program runs straight on from word 255 to 256, and `escert`
follows its branches across the boundary and certifies it like any other. Prefer one page
where the program fits, since spanning costs the partner context. `modbus_full` serves the
whole Modbus data model (functions 01 to 06, 15, 16 and 23, four tables) in 419 words on
context 0 with context 2 off; `modbus_rtu` is the same server with holding registers only,
in one page.

## Budgeting a protocol

- One instruction takes 4 ticks (2 paired): a 3-instruction bit loop (`EMITX`, `ADDT`,
  `DJNZ`) sustains up to 2 Mbit/s for a single pin at 25 MHz, twice that paired.
- A target on a bus it cannot stretch must finish every bit within one clock period.
  `spi_target` polls SCK in a 12-tick loop; its longest bit is 15 instructions, which
  rates it at 416 kHz. Where the bus allows stretching, use idiom 8 instead.
- `LDT`/`STT` take a register as base address; keep one register at 0 while working
  with fixed scratch words (`can_tx`).
- A page holds 256 words. Programs assembled into one image share a page, so `can_tx`
  (157 words) and `can_rx` (179) do not fit in one image; assembled separately and placed
  on two pages with `escasm.paged()`, they run side by side (idiom 19). One program longer
  than a page spans two, at the cost of its partner context (idiom 20).
- Four contexts run independently. A slow protocol in one context never changes another
  context's timing, so concurrent images certify per context.
