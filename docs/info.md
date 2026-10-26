<!---
Tiny Tapeout datasheet. The full design documentation is in the other files in this
directory; start with ../README.md.
-->

## How it works

Escapement is a programmable protocol engine. Four hardware contexts run firmware from a
256 × 32 program SRAM and schedule changes on its protocol pins at absolute clock ticks.
Every scheduled event is checked when it is queued: if its deadline can still be met it
happens on that exact tick, and if not, the context stops with a visible fault instead
of producing a late edge.

There are two pin banks. Bank A is the eight bidirectional `uio` pins, push-pull or
open-drain. Bank B is five split pins, each an output on `uo[7:3]` and an input on
`ui[7:3]`. Each context works on one bank, so for example I2C and PS/2 can run on bank A
while SPI and UART run on bank B, at the same time.

The same event queues can check inputs (EXPECT: a level must hold, or arrive, within a
window) and sample them at exact ticks (SAMPLE). A second 256 × 32 SRAM, the tape, holds
firmware data and timestamped captures of the pins, so the chip can also act as a logic
analyser and replay what it recorded. UART, SPI (controller and target), I2C, CAN, PS/2,
WS2812, 1-Wire, JTAG, ARM SWD, NEC infrared, HDMI-CEC, DALI, SENT, DShot600 and an I2C
EEPROM are all firmware.

A line unit does the per-bit work of packet protocols (NRZI, bit stuffing, CRCs, clock
recovery) for one context: firmware on it acts as a low-speed USB keyboard (run the chip
at 24 MHz for USB) and receives every frame of a CAN bus.

A host loads programs and reads results over an SPI port on `ui[2:0]` / `uo[0]`. After a
run, one register holds a 32-bit digest of everything the run did, which the reference
model predicts exactly.

## How to test

1. Connect a host (for example the RP2350 on the Tiny Tapeout demo board) to the SPI
   pins: CS_n `ui[0]`, SCK `ui[1]`, MOSI `ui[2]`, MISO `uo[0]`. Use SPI mode 0 with
   SCK at most f_clk / 8 (1 MHz is typical at the 25 MHz design clock).
2. Assemble a firmware file with the assembler in `sw/escasm`, for example
   `firmware/uart_tx.asm`.
3. Send STOP, WRITE_IMEM (the image), READ_IMEM (to verify), WRITE_CFG (enabled contexts,
   entry points, pin ownership and open-drain pins), then START. The command set is in
   `docs/12-HOST-PROTOCOL.md`; `sw/rp2350/escapement_demo.py` does this from
   MicroPython, and `sw/rp2350/bringup.py` runs the staged first-silicon checklist in
   `docs/16-BRING-UP.md`.
4. Observe the protocol on `uio[7:0]` (bank A) and `uo[7:3]` / `ui[7:3]` (bank B, for
   contexts configured to use it). `uo[2]` is high while running and `uo[1]` signals
   an interrupt or a fault. After the run, READ_DBG returns telemetry, fault records and
   the execution digest; READ_TAPE returns captured edges.

The design loses all state, including both SRAMs, when it is deselected. Reload after
every reselect.

## External hardware

- An SPI host: the demo board's RP2350, or any microcontroller.
- Protocol peers on `uio[7:0]` through a Pmod, for example a UART adapter, I2C devices
  (with pull-up resistors, since I2C pins are open-drain), WS2812 LEDs, a 1-Wire
  sensor, an SPI controller, an ARM SWD target, an IR LED driver and IR receiver
  module, a CAN transceiver, or a low-speed USB connection (D+ and D−, with the device's
  1.5 kΩ pull-up on D−); HDMI-CEC and DALI through a suitable level interface.
- More push-pull peers on bank B, through the output and input Pmods: each line is an
  output (`uo[3+i]`) or an input (`ui[3+i]`). A released bank B output is high.
