# Writing Escapement firmware: idioms and one anti-pattern

For anyone adding a protocol after fabrication. Every rule here comes from shipped
firmware in [firmware/](../firmware) and is checked by `python -m escert file.asm`.
Run escert before trusting any timing; the semantics are in [11-SEMANTICS](11-SEMANTICS.md).

A transmitter that is only timing (framed serial, pulse-width and burst codes) need not
be written by hand: describe it as a timing table and `python -m escgen` writes and
certifies the firmware ([protocols as data](18-PROTOCOLS-AS-DATA.md)). The idioms below
are what it generates, and what receivers and two-way buses still need by hand.

## The timing model in one paragraph

Each context commits one instruction every 4 ticks (40 ns ticks at 25 MHz). Wait-class
instructions repeat on that 4-tick grid. Pins change only through events (DRIVE,
EXPECT, SAMPLE) with absolute deadlines taken from `tmr`. The chip checks each event at
enqueue: its deadline must be after the commit tick and after the previous queued
event, otherwise the context faults (ADMIT) and its pins release. An admitted event
happens exactly on its tick. Firmware therefore runs **ahead** of real time, by up to
four queued events.

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

**10. Reply from the line (v1.4).** With the [line unit](19-LINE-UNIT.md), a reply's
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

**11. Keep the host out of the timed context.** escert cannot know that a `POP` after
`RXREADY` never waits, so a timed loop that reads the mailbox cannot be certified, and a
frame built while values arrive can mix two of them. Give the host its own context: it
takes words from the mailbox and stores them in the tape; the timed context reads the tape
once per frame and waits on nothing but its own events. One `STT` is atomic, so a frame
that fits in a word cannot mix values (`sent_tx`). For more, build the next frame in a
second buffer, switch a pointer word, then wait one frame period before rebuilding the old
buffer, so that no frame still reads it (`dshot_tx`, four motors from one context).

**12. Time a data-dependent length by its bits.** `ADDTR` with a computed value has no
certificate. Test each bit of the value and `ADDT` its weight instead (`TSTI r1, 1`,
`JZ`, `ADDT UT`, then 2, 4, 8): every path adds a constant, and escert stays exact
(`sent_tx`'s nibble pulses and its pause pulse).

**13. Leave on failure; do not return a flag.** A subroutine that checks a timing window
and returns Z for success merges both outcomes at its return site, and escert loses which
one left the edge register fresh. Jump to the abort path from inside it instead (`cec`'s
`edge`).

**14. Start a far-off reply close to its deadline.** A reply 7.5 ms ahead queued at once
leaves its events far ahead of the clock, and a loop of them loses escert's upper bound on
the lead. Anchor, compute the reply, `WAITT` until shortly before it, then queue it (`dali`).

## Budgeting a protocol

- One instruction takes 4 ticks: a 3-instruction bit loop (`EMITX`, `ADDT`, `DJNZ`)
  sustains up to 2 Mbit/s for a single pin.
- A target on a bus it cannot stretch must finish every bit within one clock period.
  `spi_target` polls SCK in a 12-tick loop; its longest bit is 15 instructions, which
  rates it at 416 kHz. Where the bus allows stretching, use idiom 8 instead.
- `LDT`/`STT` take a register as base address; keep one register at 0 while working
  with fixed scratch words (`can_tx`).
- The program store is 256 words for all four contexts: `can_tx` (157 words) and
  `can_rx` (179) do not fit in one image.
- Four contexts run independently. A slow protocol in one context never changes another
  context's timing, so concurrent images certify per context.
