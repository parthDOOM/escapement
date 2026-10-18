# Protocols as data

A transmit-side protocol is mostly a timing table: which pins, what idle levels, which
symbols last how long, and how a frame is built from them. `sw/escgen` turns such a
table into firmware in the same idioms as the hand-written programs, and refuses the
result unless the timing certificate checker proves it never schedules an edge late.
The generated program is ordinary firmware: it runs on the simulator and the chip, it
co-simulates against the RTL, and it can be edited by hand afterwards.

```
python -m escgen                       # every protocols/*.yaml -> firmware/<name>.asm
python -m escgen protocols/midi_tx.yaml
python -m escgen --check-only          # the files in firmware/ are up to date
```

## A table

MIDI OUT is UART framing at 31,250 baud ([`protocols/midi_tx.yaml`](../protocols/midi_tx.yaml)):

```yaml
name: midi_tx
title: MIDI OUT, 31.25 kbaud 8N1; one MIDI byte per host mailbox word
refs: ["MIDI 1.0 Detailed Specification 4.2, Hardware: 31.25 kbaud, ..."]
pins:
  OUT: {pin: 0, idle: 1}
constants:
  BIT: CLK_KHZ * 1000 // 31250        # 800 ticks at 25 MHz
symbols:
  start: [[{OUT: 0}, BIT]]
  bit:   [[{OUT: data}, BIT]]
  stop:  [[{OUT: 1}, BIT]]
frame: [start, {bits: 8, order: lsb, symbol: bit}, stop]
source: {kind: mailbox}
```

- **Symbols** are lists of phases, `[levels, duration]`. A phase sets its pins at the
  current deadline and lasts `duration` ticks. A pin level of `data` takes the next bit
  of the unit being sent (one `EMITX`), so a symbol whose width or level depends on the
  data needs no branch. A level of `carrier` sends whole carrier pulses for the phase
  (the table's `carrier: {period, high}`), for infrared and other burst codes.
- **The frame** is the sequence for one unit: symbols, and fields of `bits` data bits.
  In a plain field each bit is sent as a symbol with one data phase; in a `choose` field
  each bit picks one of two symbols, which is how pulse-distance codes such as NEC are
  written.
- **The source** says where units come from: the host mailbox (one word per packet),
  the tape (a fixed range, or a count in the first word followed by that many words),
  or constants (sent once, then the context halts). `units_per_word` sends several
  units from each word. **Packets** add symbols before and after each packet, and an
  optional acknowledgement to the host.
- **Durations** are assembler expressions over `CLK_KHZ`, so one table serves any clock;
  the tests run long protocols at a reduced clock.

## What it generates

Every edge is a DRIVE event at an absolute deadline: the program only keeps the event
queue ahead of the output, so the timing of the pins does not depend on instruction
timing. Packets that start after a wait (for the host, or after a pass over the tape)
re-anchor the schedule a fixed lead after the current time. The generator tracks which
pin levels are known, so a phase that changes nothing becomes a wait, not an event.
`escert` then proves a minimum lead for every event site or the table is refused, with
the instruction where the proof fails.

## The tables

| Table | Protocol | Source | Checked against |
| --- | --- | --- | --- |
| `uart_tx_gen` | UART 8N1, 115,200 baud | One byte per host word | UART receiver model; ISS and RTL in lockstep |
| `midi_tx` | MIDI OUT, 31.25 kbaud (MIDI 1.0) | One byte per host word | UART receiver model at 31,250 baud; lockstep |
| `dmx512_tx` | DMX512 (ANSI E1.11): 100 µs break, 12 µs mark after break, start code and 511 slots of 8N2 at 250 kbit/s | Tape words 0 to 127, four slots each, refreshed continuously | DMX receiver model with E1.11 receiver limits: every slot, break and mark-after-break times; lockstep at a 5 MHz timebase |
| `ws2812_gen` | WS2812B: 0.4 / 0.8 µs high, 1.24 µs bit, 300 µs latch | LED count in tape word 0, colours after it | WS2812 decoder with datasheet windows; lockstep |
| `nec_tx_gen` | NEC infrared: 38 kHz carrier at 1/3 duty, 9 ms leader, 4.5 ms space, 32 pulse-distance bits, stop burst, 40 ms quiet | One raw 32-bit frame per host word, acknowledged | NEC receiver model: carrier frequency and duty, pulse counts, every burst at its nominal distance; lockstep at a 2.5 MHz timebase |

Against the hand-written programs for the same protocols:

| | Hand-written | Generated |
| --- | --- | --- |
| UART | `uart_tx`: 20 words, certified lead ≥ 9 ticks | `uart_tx_gen`: 18 words, ≥ 9 |
| WS2812 | `ws2812`: 28 words, ≥ 5 | `ws2812_gen`: 29 words, ≥ 5 |
| NEC | `nec_tx`: 69 words, ≥ 9 | `nec_tx_gen`: 42 words, ≥ 9 |

The generated WS2812 bit loop is the hand-written one instruction for instruction
(seven instructions in a 31-tick bit). The hand-written programs do more around the
protocol: `ws2812` computes a colour gradient on chip, `nec_tx` adds repeat codes, the
108 ms slot and the address and command inverses. A table describes the line; work like
that stays in hand-written code.

## Limits

- Transmitters only. Receivers and clocked buses with a target that answers (I2C, SPI,
  SWD) stay hand-written: they need decisions on input during a frame.
- A data phase drives only its data pins; other pins change in their own phases.
- Fields up to 32 bits, units taken from bit 0 of the source word up; a `choose` field
  is sent LSB first and needs the mailbox or constant source (it counts bits in r3).
- One carrier per table.
- A symbol has to be long enough for its instructions: four ticks per instruction per
  context, plus whatever the event queue has banked. The checker decides; for example
  DMX512 needs a clock of at least about 4 MHz.
