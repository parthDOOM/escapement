# Writing Escapement firmware: idioms and one anti-pattern

For anyone adding a protocol after fabrication. Every rule here comes from shipped
firmware in [firmware/](../firmware) and is checked by `python -m escert file.asm`.
Run escert before trusting any timing; the semantics are in [11-SEMANTICS](11-SEMANTICS.md).

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

## Budgeting a protocol

- One instruction takes 4 ticks: a 3-instruction bit loop (`EMITX`, `ADDT`, `DJNZ`)
  sustains up to 2 Mbit/s for a single pin.
- `LDT`/`STT` take a register as base address; keep one register at 0 while working
  with fixed scratch words (`can_tx`).
- Four contexts run independently. A slow protocol in one context never changes another
  context's timing, so concurrent images certify per context.
