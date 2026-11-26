<!---
Tiny Tapeout datasheet. The full design documentation is in the other files in this
directory; start with ../README.md.
-->

## How it works

Escapement is a programmable protocol engine. Four hardware contexts run firmware from a
1,024-word program SRAM, in four pages of 256 words (a context whose partner is switched
off can run one 512-word program across both of their pages), and schedule changes on the
protocol pins at absolute clock ticks. Every scheduled event is checked when it is queued: if its
deadline can still be met it happens on exactly that tick, and if not, the context stops
with a visible fault instead of producing a late edge.

There are two pin banks. Bank A is the eight bidirectional `uio` pins, push-pull or
open-drain. Bank B is five split pins, each an output on `uo[7:3]` and an input on
`ui[7:3]`. Each context works on one bank, so I2C and PS/2 can run on bank A while SPI and
UART run on bank B, at the same time. Two contexts can also be paired, so that one program
gets two slots of the four and runs at twice the instruction rate.

The same event queues check inputs (EXPECT: a level must hold, or arrive, within a window)
and sample them at exact ticks (SAMPLE). A second 256-word SRAM, the tape, holds firmware
data and timestamped captures of the pins, so the chip also works as a logic analyser that
can replay what it recorded. UART, SPI, I2C, CAN, PS/2, WS2812, 1-Wire, JTAG, ARM SWD,
infrared remotes, HDMI-CEC, DALI, SENT, DShot and many more are firmware: the repository
holds 90 programs, eight of them for protocols drawn at random from a sealed list
([held-out round](../heldout/RESULTS.md)).

Three units do work that firmware cannot do per bit:

- A **line unit** handles the bits of packet protocols (NRZI, bit stuffing, CRCs, clock
  recovery, Manchester cells) for one context. With it the chip is a low-speed USB
  keyboard (at 24 MHz), receives and sends CAN frames, drives WS2812 strings, sends and
  receives 10BASE-T Ethernet frames (at 40 or 50 MHz) and listens to an SPI bus.
- A **bus timing monitor** checks up to four interval rules between pin edges (for example
  an I2C setup or hold time) at every tick, counts the violations and records the first.
- A **capture unit** streams timestamped pin changes into the tape.

The design clock is 25 MHz. The layout is signed off at 20 ns, so the chip is rated for
clocks up to 50 MHz. On the demo board, use a clock its RP2350 or RP2040 makes without jitter (25, 33
and 50 MHz all are; see `docs/16-BRING-UP.md`).

A host loads programs and reads results over an SPI port on `ui[2:0]` / `uo[0]`, and can
stop, rewrite and restart one context while the others run. After a run, one register holds
a 32-bit digest of everything the run did, which the reference model predicts exactly.

## How to test

1. Connect a host (for example the RP2350 on the Tiny Tapeout demo board) to the SPI
   pins: CS_n `ui[0]`, SCK `ui[1]`, MOSI `ui[2]`, MISO `uo[0]`. Use SPI mode 0 with
   SCK at most f_clk / 8 (1 MHz is typical at the 25 MHz design clock).
2. Assemble a firmware file with the assembler in `sw/escasm`, for example
   `firmware/uart_tx.asm`.
3. Send STOP, WRITE_IMEM (the image), READ_IMEM (to verify), WRITE_CFG (enabled contexts,
   entry points, pin ownership, open-drain pins, banks, pairs and pages), then START. The
   command set is in `docs/12-HOST-PROTOCOL.md`; `sw/rp2350/escapement_demo.py` does this
   from MicroPython, and `sw/rp2350/bringup.py` runs the staged first-silicon checklist in
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
- An SSD1306 OLED and two buttons for the OLED Pong demo (`docs/23-OLED-PONG.md`).

## Tiny Tapeout's common pinouts

A program names its pins with assembler parameters, so it runs on the pins Tiny Tapeout
recommends for its protocol with no change to its code. `.bank n` puts context n on bank B.
`sw/tests/test_tt_pinouts.py` runs the first three rows against their bus models.

| Pinout | Pins | Program and settings |
| --- | --- | --- |
| UART to USB through the demo board's RP2350 or RP2040 | `ui[3]` RX, `uo[4]` TX | `uart_rx` with `.bank 1` and `.equ uart_rx.RX_PIN, 0`; `uart_tx` with `.bank 0` and `.equ uart_tx.PIN, 1` |
| SPI, top row (also the SPI RAM and QSPI Pmods) | `uio[0]` CS, `uio[1]` MOSI, `uio[2]` MISO, `uio[3]` SCK | `spi_flash` with `CS_PIN` 0, `MOSI_PIN` 1, `MISO_PIN` 2, `SCK_PIN` 3 |
| TT Audio Pmod | `uo[7]` | `westminster` with `.equ westminster.PIN, 4` |
| I2C, top row | `uio[2]` SCL, `uio[3]` SDA | `i2c_ctrl` and `i2c_eeprom` as they are |
| Tiny VGA | `uo[3]` vsync, `uo[4]` to `uo[6]` R0, G0, B0, `uo[7]` hsync | `vga_pong` as it is; the high colour bits' pins carry the host port |
