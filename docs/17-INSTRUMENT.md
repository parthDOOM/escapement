# Escapement as an instrument

The chip that speaks protocols can also watch and replay them. The capture unit
timestamps every change of the watched pins to the tick (40 ns at 25 MHz, 20 ns at
50 MHz), the tape holds the records, and the host tools turn them into files that
logic-analyser software opens. This chapter covers the three ways to capture, the files,
replay and the limits. Two other units also listen without driving anything: the line
unit decodes whole packets from a bus between other devices, and the bus timing monitor
checks that bus's timing against a datasheet (below).

## Three ways to capture

| | How | Length | Needs |
| --- | --- | --- | --- |
| Stopped capture | Arm, run, STOP, read the tape | Up to 256 records | Any host |
| Streaming capture | The host reads and acknowledges records during the run | As long as the host keeps up | Any host |
| Live in PulseView | The board answers PulseView's Openbench Logic Sniffer driver | Up to 4 Mi samples per capture, while the host link keeps up | The demo board, or the FPGA bench's Pico |

All three run [`firmware/logic_analyzer.asm`](../firmware/logic_analyzer.asm): four
instructions that arm the capture unit and halt, so the capture costs no instruction
fetches at all. The watched pins are one bank: `uio[7:0]`, or bank B's five pins.

**Stopped capture.** The capture unit writes one record per change,
`{levels[7:0], tick[23:0]}`, into a region of the tape, linear or as a ring that keeps
the newest records. After STOP the host reads the status (`READ_DBG` sel 17) and the
region, and `esctape.decode_capture()` gives `(tick, levels)` pairs with absolute ticks.

**Streaming capture.** With the stream bit set, the region is a ring shared with the
host. The host repeatedly reads the capture status, reads the records from its read
pointer up to the capture pointer, and acknowledges them with `TAPE_ACK`. Each word is
fetched in a cycle the core leaves free, so streaming never delays an instruction. The
capture unit never writes over a record the host has not acknowledged; if the host
falls behind until the ring and the two-record staging buffer are full, capture stops,
`ovf` is set and `ovf_tick` says when, and every record before it is intact.
`eschost` does one round with `stream_poll()`; on the demo board,
`escapement_demo.stream(seconds=5)` streams `uio[7:0]` into `capture.json`.

**Live in PulseView.** `sump.serve()` on the demo board answers libsigrok's
`openbench-logic-sniffer` driver over the board's USB serial port, so PulseView and
sigrok-cli capture from the chip with no new driver. PulseView asks for a sample rate,
a sample count and a trigger. The board streams change records during the window and
then expands them into samples at the requested rate, so the edges keep the chip's
tick timing whatever rate is chosen. The trigger is the first record whose pins match
the stage 0 mask and value (the driver offers level triggers, 0 or 1 per channel); the
capture is placed around it at PulseView's pre-trigger ratio. `sw/tests/test_sump.py`
runs sigrok-cli 0.7.2 (libsigrok 0.5.2) against the bridge on the simulated board,
through a pseudo-terminal, and checks the waveform it writes, edge for edge.

On the FPGA bench (`sump.serve(io=barepico.open_io())` on its Pico W, 28 November 2026)
sigrok-cli with the same driver captured 2 ms at 1 MHz of two square waves the Pico's PIO
fed into uio[2] and uio[3], 10 kHz and 1 kHz: every interval was exactly 50 and 500
samples. A 20 ms window of the same lines stopped after 2.9 ms. The two waves make 22,000
edges a second, more than the bench's host link, bit-banged by MicroPython, reads, so the
ring filled and the chip ended the capture with its overflow tick, as it is built to. SUMP
cannot tell PulseView this: the bridge's log has it, and the samples after it repeat the
last level. A capture lasts as long as the host link keeps up with the line's edges
([the run](../fpga/tangprimer25k/results/2026-11-28-sump/README.md)).

## Files

A capture file (`.json`) keeps the record words as the chip wrote them, with the clock
they were taken at and the pin names. On the demo board that clock is the one the board
is running: 25 MHz by default, or a `clock_frequency` in the `[tt_um_escapement]`
section of the board's `config.ini`, up to the rated 50 MHz (D-5; use exactly
25000000 or 50000000, or 33000000 for rebuilt firmware). `stream()` and the SUMP bridge read it back from the board, and the PC tools
rescale a capture taken at another clock (`esclearn` learns in 25 MHz ticks). The host
tools write it, and on a PC it converts for other viewers:

```
python -m esctape export capture.json -o capture.vcd        # GTKWave, PulseView
python -m esctape export capture.json -o capture.sr         # PulseView, sigrok-cli
python -m esctape export capture.json -o capture.sr --div 25   # 1 MHz samples
```

A VCD has one change per record and loses nothing. A sigrok session file stores
samples: every `div` ticks from the first record, so a long, quiet capture at full rate
is large, and `--div` trades resolution for size. The `.sr` layout follows libsigrok's
srzip writer and session loader: a `version` entry "2", a key-file `metadata`, and the
samples in `logic-1`, one byte per sample with channel n in bit n.

## Replay

Any trace becomes firmware that re-drives it, tick for tick:

```
python -m esctape replay capture.vcd --pins 1 -o replay.json
python -m esctape replay trace.vcd --signals sda,scl --pins 0,1 --od 0,1 -o i2c.json
```

The input may be a capture file, a VCD from any tool (pick its 1-bit signals with
`--signals`) or a sigrok session file. The result is an image of
[`firmware/replay.asm`](../firmware/replay.asm) with the trace in the tape. A trace the
tape cannot hold is refused when it is built, and one too dense to replay on time is
refused by the chip at admission: it faults instead of driving an edge late.

## Listening at a higher level

A capture records edges; two other units turn a watched bus into something more useful,
and neither drives a pin.

- **Packets.** The line unit's receiver decodes a bus between two other devices into
  bytes on the tape, with its CRC checked: `spi_sniff.asm` logs every frame an SPI
  controller sends, clocked by the controller's own SCK, and `eth_rx.asm` receives
  10BASE-T frames and checks each frame check sequence
  ([19-LINE-UNIT](19-LINE-UNIT.md#receiving-three-strobes)).
- **Timing.** The bus timing monitor checks every edge of a bus against rules written
  from a datasheet (setup, hold, pulse width, period, timeout) and reports each violation
  with its tick and the interval it measured: `i2c_timing.asm` does this for I2C
  ([22-BUS-MONITOR](22-BUS-MONITOR.md)).

Either can run beside a capture, in another context, so the same bus can be recorded edge
by edge, decoded and checked for timing at once.

## Limits

- **One bank, eight channels per capture.** The record has eight level bits; bank B
  shows as channels 0 to 4.
- **Resolution and latency.** One tick (40 ns at 25 MHz, 20 ns at 50 MHz). Inputs pass a two-flop
  synchroniser, so a record's tick is two ticks after the pin changed, the same for
  every edge. Pulses shorter than a tick can be missed.
- **Bursts and the sustained rate.** The capture unit takes a change every tick. A burst
  is absorbed by the ring (up to its size minus one) and the two-record staging buffer;
  the sustained rate is what the host link can read. On the demo board the RP2040/RP2350
  bit-bangs SPI from MicroPython; the board model used by the tests (1 µs per pin-byte
  call, about 4 µs per SPI bit) reads about 7,000 records per second. That figure is
  the model's; a board's own rate depends on its MicroPython build's pin-write timing.
- **Stopped capture** holds at most 256 records, and the ring mode keeps only the newest.
- **Replay** covers one 2^24-tick epoch (0.67 s at 25 MHz, 0.34 s at 50 MHz), at most 256 records, and
  needs about 40 ticks between records.
- **PulseView.** Up to 4 Mi samples and 16,384 records per capture; trigger stage 0
  only, in parallel mode; no run-length encoding. A capture waits for its trigger as long
  as it takes. While `sump.serve()` runs, the board's REPL is off the USB port (Ctrl-C
  too); the board's reset button brings it back.
