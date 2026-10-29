# Record it, speak it

Point the chip at a transmitter it has never seen: an IR remote, a 433 MHz socket
remote, an LED strip controller, a serial device. It records the waveform, `sw/esclearn`
works out the protocol's timing table from the recording, `escgen` turns the table into
firmware that escert certifies, and the chip then sends **new** messages in that
protocol, such as a button the remote never sent. A recording that fits no model is
refused, with the reason, and can still be replayed unchanged (`esctape.build_replay`).

```text
python -m esclearn capture.sr                          # what it found, frame by frame
python -m esclearn capture.sr -o remote.yaml           # the timing table (escgen's format)
python -m esclearn capture.sr --send 42ff00 -o remote.yaml --check
python -m escgen remote.yaml --out firmware/           # firmware, refused unless CHECKED
```

The input is one pin of a chip capture ([instrument](17-INSTRUMENT.md)), a VCD or a
sigrok session (`.sr`, from PulseView or sigrok-cli); `--pin` or `--signal` picks the
channel. Everything is pure Python with only PyYAML, so the same code can run in the
browser.

## An example

Twenty-one buttons of a NEC remote, recorded by a logic analyser at 1 MHz
([`sw/tests/data/sigrok/nec_all.sr`](../sw/tests/data/sigrok/README.md)):

```text
$ python -m esclearn tests/data/sigrok/nec_all.sr
pulse distance, 32 bits per frame, idle high
leader mark 9091.0 us space 4482.0 us
zero: mark 597.0 us space 586.0 us; one: mark 597.0 us space 1640.5 us
trailer mark 597.0 us
gap 96111.0 us
byte 3 is the complement of byte 2
frame 0xba45ff00
frame 0xb946ff00
...
the 30 frames of another shape are all alike: mark 9090.0 us, space 2220.0 us, mark 604.0 us
```

The check byte is found from the data: in every frame, byte 3 is the complement of
byte 2. Byte 1 is also the complement of byte 0, but the address never changes in this
recording, so that rule is not claimed (a constant byte proves nothing). The held keys'
repeat codes are not modelled, but they are reported as one shape with its timing.
To send a command the remote never sent, give the address and the new command; the
check byte is recomputed:

```text
$ python -m esclearn tests/data/sigrok/nec_all.sr --send 42ff00 -o remote.yaml --check --clk-khz 2500
send 0xbd42ff00 (check bytes recomputed)
table: remote.yaml (source constant)
check: 3 frame(s) regenerated on the ISS, worst duration error 5.40% (750 ticks), decoded back: yes
```

The table names every constant with the spread it was measured over:

```yaml
constants:
  M1: CLK_KHZ * 227275 // 25000    # mark 9091.0 us; 51 seen, 9079.0 us to 9102.0 us
  S1: CLK_KHZ * 112050 // 25000    # space 4482.0 us; 21 seen, 4477.0 us to 4489.0 us
  M2: CLK_KHZ * 14925 // 25000     # mark 597.0 us; 723 seen, 567.0 us to 607.0 us
  S2: CLK_KHZ * 14650 // 25000     # space 586.0 us; 336 seen, 556.0 us to 593.0 us
  S3: CLK_KHZ * 41012 // 25000     # space 1640.5 us; 336 seen, 1633.0 us to 1646.0 us
  GAP: CLK_KHZ * 2402775 // 25000  # 96111.0 us of quiet after a frame
symbols:
  leader: [[{OUT: 0}, M1], [{OUT: 1}, S1]]
  zero:   [[{OUT: 0}, M2], [{OUT: 1}, S2]]
  one:    [[{OUT: 0}, M2], [{OUT: 1}, S3]]
  stop:   [[{OUT: 0}, M2]]
  gap:    [[{OUT: 1}, GAP]]
frame: [{bits: 32, order: lsb, choose: [zero, one]}]
source: {kind: constant, words: [3175284480]}
packet: {before: [leader], after: [stop, gap]}
```

## How it works

1. **Edges and runs.** One pin's edges become alternating runs. The idle level is the
   level the recording starts and ends at; a mark is a run away from it. Durations are
   ticks of the 25 MHz clock, and the recording's sample period is kept as its
   resolution, so a 1 MHz recording is never asked for 40 ns agreement.
2. **Carrier.** Short, regular pulses in bursts separated by spaces several periods
   long are a carrier. Each burst becomes one mark of whole carrier periods (what
   `escgen` sends), and the period and duty are kept. A raw IR channel at 38 kHz and the
   demodulated channel of the same recording give the same frames.
3. **Frames.** Frames end at gaps: the first large jump among the sorted spaces.
4. **Models.** Three learners try the frames, and the most plausible fit wins:
   - *Manchester* (RC-5, DALI): every run is one or two half-bits and every bit has
     opposite halves. The half-bit is the one most runs fit, so a ragged receiver
     output or one bogus packet leaves that frame unread, not the recording.
   - *Asynchronous serial* (UART, MIDI, DMX512): marks are whole numbers of one bit
     time; nine formats from 8N1 to 5N1, parity, stop bits, standard baud rates, line
     breaks and mark-after-break.
   - *Pulse codes*: pulse distance (NEC, SIRC-like), pulse width (WS2812, PT2262),
     pulse length, or any two symbols; leaders and trailers are the outliers at the
     ends.

   Plausibility matters because some signals fit two models: a WS2812 stream also reads
   as 7N1 serial at 2.42 Mbaud (three of its bits are one character).
5. **Words.** Bits are numbered as sent: the first bit sent is bit 0, a frame of up to
   32 bits is one word, a longer one words of its largest divisor from 32 down to 8.
   `--unit` and `--msb` number the same bits another way: `--unit 24 --msb` gives an LED
   strip's colours as its datasheet writes them, and the table sends MSB first.
6. **Check bytes.** A byte that, in every frame, is the complement of an earlier byte,
   or the XOR, the 8-bit sum or the negated sum of all the bytes before it. A rule is
   believed only if its inputs take at least three values across the frames.
7. **Check.** `--check` builds the table, runs it on the instruction-set simulator once
   per recorded frame, and compares every generated mark and space with the recording.
   A transmitter varies and the firmware sends each symbol's typical length, so a
   generated duration passes if it lies within the lengths the recording shows for that
   symbol, give or take two samples. The output is then decoded again by the same model.
   The "worst duration error" above is the remote's own spread: its shortest mark is 5%
   below the median.

## Real recordings

Twelve public-domain recordings from [sigrok-dumps](https://github.com/sigrokproject/sigrok-dumps)
are in [`sw/tests/data/sigrok`](../sw/tests/data/sigrok/README.md). Where sigrok-cli has
a decoder that reads the file, the tests compare its decode with esclearn's, frame by
frame. That is a separate implementation reading the same samples. CI installs sigrok-cli.

| Recording | esclearn | sigrok-cli 0.7.2 |
| --- | --- | --- |
| Philips RC-5 VCR remote, buttons 1, 2 and standby | Manchester, 14 bits, 37 kHz carrier; address 5, commands 1, 2, 12 | ir_rc5: address 5, commands 1, 2, 12 |
| The same, with one bogus packet | 4 frames read, 1 left unread | 4 frames |
| Matsui TV remote (NEC), standby held | 3 frames: address 0x40, command 0x12, both complements; 13 repeat codes reported | ir_nec: address 0x40, command 0x12 (standby), 13 repeat codes |
| Joy-IT remote (NEC), 21 buttons | 21 frames, the command's complement found | misreads it: the remote is outside the decoder's fixed 5% tolerance, as the recording's notes say |
| Ceiling light remote (extended NEC) | 16-bit address 0xEA41; only the command's complement is claimed | address 0x41, then an address error: it does not know extended addresses |
| Sony SIRC, PT2262 433 MHz socket remote | pulse length, 20 bits; pulse width, 25 bits, buttons A to D | no decoder |

Four of them, NEC, SIRC, PT2262 and RC-5 on its carrier, are regenerated on the
simulator and pass the check.

## On the chip

`make pin-test` runs the whole loop on the RTL, and `make gl-pin-test` on the final
gate-level netlist (`test_learned_nec_on_chip`):

1. A remote built from the NEC specification presses three buttons on `uio[1]`.
2. The chip records them with the logic analyser firmware, and the host reads the tape.
3. esclearn learns the protocol from the capture, and escgen builds firmware that
   escert certifies.
4. The host keeps the address, changes the command to one the remote never sent (the
   complement follows), and the chip sends it on `uio[0]`.
5. The specification's receiver decodes it and checks every mark and space against the
   windows a real decoder accepts.

Every duration is scaled down 20 times (the models take a tick as 800 ns) to keep the
simulation short, as the other infrared tests on the RTL scale theirs. It takes about
90 seconds in Icarus on the RTL, and 6 minutes on the netlist.

## What it will not do

- **Guess.** Random edges, a recording that is too short, and frames with no repeating
  shape are refused: none of the 90 noise recordings in the tests (300 random edges
  each, three distributions of spacing) yields a model.
- **Tell apart what the waveform does not.** 7N1 with one idle bit between characters
  is the same waveform as 7N2, and esclearn reports 7N2; the replay is identical. An
  idle-high pin with no carrier can be a DALI line (a 1 rises mid-bit) or an IR
  receiver's output (low while the carrier is on, so an RC-5 1 falls). esclearn uses
  the first convention and prints the other reading as well.
- **Resolve what was not sampled.** A Manchester half-bit must span at least four
  samples: at 6 MHz, a WS2812 stream's runs fit some half-bit, so it stays a pulse-width
  code. A raw carrier needs a few samples per pulse.
- **Receive, or drive several wires.** It generates transmitters for single-wire,
  self-timed codes. Clocked buses (SPI, I2C) already have firmware, and checksums beyond
  the simple check bytes above (CRCs) are not inferred: such a frame can be replayed but
  not changed.

## Prior work

Radio tools analyse recordings too: rtl_433's analyser (`-A`) measures pulse timings
and suggests a flex decoder, and Universal Radio Hacker detects modulation parameters
and helps find message fields. Both decode. Here the learned protocol becomes a
transmitter: firmware with a static timing certificate, run on a chip that cannot emit
a late edge, and checked edge by edge against the recording it came from.
