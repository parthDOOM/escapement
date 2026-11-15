# The tape and the execution digest

This chapter explains two design choices that shape how the chip stores data and how it
is checked: one shared, timestamped memory, the **tape**, instead of separate buffers for
each job; and the **execution digest**, a 32-bit signature of a whole run. It also
explains a smaller rule that came with them, that GETI waits for samples still in flight.
The normative text is [11-SEMANTICS](11-SEMANTICS.md) §6.5, §7.3, §7.6 and §10.1, and the
decisions are D-24 to D-26 in [06-DECISIONS](06-DECISIONS.md).

## The problem

A chip that only drives, samples and checks pins at exact ticks has nowhere to put
anything. An emulated I2C EEPROM needs 256 bytes of memory; a logic analyser needs a
buffer of timestamped edges; replaying a recorded waveform needs that buffer again as a
source. Built separately in flip-flops, each 16-entry buffer would cost about 1,000
flip-flops, add routing congestion, and still be too small to be useful.

## One timestamped memory

The tape is a second single-port SRAM macro of 256 words of 32 bits, 49,488 µm², about 4%
of the core. Firmware, the capture unit and the host share it:

| User | Access | When |
| --- | --- | --- |
| Firmware | `LDT ra, rb, imm8` and `STT ra, rb, imm8`, at address `R[rb] + imm8` | In its own commit cycle |
| Capture unit | One record `{levels[7:0], tick[23:0]}` per change of a watched pin | Any cycle the port is free |
| Line unit | Packet data, read for transmission and written on receive | Its owner's commit cycles |
| Host | `WRITE_TAPE`, `READ_TAPE` and `TAPE_ACK` ([12-HOST-PROTOCOL](12-HOST-PROTOCOL.md)) | Writes while stopped; reads in any cycle the core leaves free |

One memory then serves four jobs:

- **Device emulation.** [`i2c_eeprom.asm`](../firmware/i2c_eeprom.asm) is a 24C02 whose
  256 bytes live in the tape, at Standard and Fast mode.
  [`spi_target.asm`](../firmware/spi_target.asm) serves the same kind of memory as a
  25xx-style SPI part.
- **Logic analyser.** [`logic_analyzer.asm`](../firmware/logic_analyzer.asm) is four
  instructions long, because the capture unit does the work. Capture keeps running after
  every context halts, until its region fills, or for ever in ring mode, which keeps the
  history before a trigger. In stream mode the host reads and acknowledges records during
  the run, so the length of a capture is limited by the host link's rate, not by the
  tape's 256 words.
- **Record and replay.** A capture record has the same shape as a DRIVE event, a tick and
  levels, so `sw/esctape.build_replay()` turns a capture directly into
  [`replay.asm`](../firmware/replay.asm) and its tape contents. The replay is exact to the
  tick, or it is refused if the trace is too dense to replay on time.
- **Exact input timestamps.** Firmware can read capture records too, so it can learn the
  exact tick of an input edge without the four-tick uncertainty of a wait.

## Why sharing costs no determinism

Four users share one port, and none of them ever makes another wait in a way that changes
a program's timing:

- **Firmware uses the port only in its own commit cycle**, and one context commits per
  cycle, so loads and stores never conflict. STT writes at the commit edge. LDT reads at
  the commit edge and writes its register one edge later, which no program can observe,
  because the same context's next commit is four ticks away; in pair mode too, because a
  paired context makes no fetch right after an LDT.
- **The line unit uses its owner's commit cycles.** Only the owner's own loads and stores
  can therefore wait for it, and only for one of the unit's accesses; the timing
  certificate accounts for that wait, and no other context is affected.
- **Capture fills the gaps.** A two-record staging buffer drains into any cycle without a
  load or store. A burst that would need a third slot does not lose a record silently:
  capture stops, records the tick at which it overflowed, and still writes the records
  already staged, so the recording is complete up to a known point and the failure is
  visible. This is the chip's refuse-rather-than-be-late rule applied to observation.
- **The host takes what is left.** It writes the tape only while the chip is stopped. It
  can read during a run, each word in a cycle the core leaves free, so a host read never
  delays the core; a word it could not fetch in time is flagged, not returned wrong.

Whether a capture overflows is therefore a function of the programs and the inputs alone,
and both the simulator and the RTL predict it. The random co-simulation provokes it on
purpose, with bursts of loads and stores colliding with capture drains, and compares all
256 tape words after every run.

### The record format

- **Ticks modulo 2^24, with epoch records.** A record is one word, so the tape holds 256
  edges. While capture runs, a record is also written at every 2^24-tick boundary, so
  absolute time can be rebuilt from the words alone. An epoch is 0.67 s at 25 MHz and
  0.34 s at 50 MHz.
- **All eight levels, triggered by a mask.** Each record is a full snapshot of its bank's
  eight pins, so it replays without context, but only the watched pins create records.
- **A snapshot when capture is armed**, so a trace always starts from known levels.

## The execution digest: a 32-bit signature of a run

On every edge of a run where something happens (an instruction commits, a loaded
register is written, or an output changes), the chip folds the pin state, the commit's
outcome, context and program counter, and any register write data into a 32-bit
multiple-input signature register using the CRC-32 polynomial. The exact rule is in
[§10.1](11-SEMANTICS.md).

The digest is useful in three ways:

- **Silicon against the model in one read.** Both the simulator and the RTL are exact to
  the tick, so after a run the host reads one word (`READ_DBG` select 16), and the
  simulator predicts the same word from the same image and inputs. Equal words mean the
  chip executed the same commits, outcomes, register values and pin states as the
  reference, with a chance of about 2^−32 that a difference goes unnoticed.
- **Inputs come from the tape.** For a program whose behaviour depends on its inputs, the
  capture records those inputs. Capture, the simulator and the digest together therefore
  make a silicon run fully observable without a debug port: replay the captured inputs in
  the simulator, and the digest confirms the replay matches what the chip did.
- **It is cheap**: 48 flip-flops, 32 for the signature and 16 for the previous outputs.

The digest changes only on edges where something happens, so it stops changing once
every context has stopped. A host STOP that arrives at an unpredictable moment of an SPI
transfer therefore cannot change it.

## GETI waits for samples in flight

GETI returns the bits that SAMPLE events have shifted into `ish`. In the first version of
the machine, a GETI that committed on the same edge as the last SAMPLE's deadline returned
the word without that bit, and the bit went into the next word. Firmware had to avoid this
with an explicit wait, and an SPI routine once lost a bit by missing one. That is a silent
failure in a design whose rule is that nothing fails silently, so GETI is wait-class: it
waits while any SAMPLE is still in the queue, including one due in that cycle. The cost is
one OR over the queue's four entry kinds.

## Cost

| Item | Flip-flops | Area |
| --- | ---: | --- |
| Tape macro | – | 49,488 µm² (top right, [03-PHYSICAL-DESIGN](03-PHYSICAL-DESIGN.md)) |
| Capture unit (`esc_cap.v`) | about 150 | mostly the staging buffer (64) and the overflow tick (32) |
| Digest | 48 | |
| LDT write-back, GETI wait | 5 | |
| Host tape commands | 1 | |

That is about 200 flip-flops in all, against roughly 1,000 for a single 16-entry capture
buffer in flip-flops, which would have a sixteenth of the depth, no data memory and no
replay.

## Evidence

- **Simulator tests derived by hand** (`sw/tests/test_v11.py`) cover addressing, ring
  mode, a full region, overflow on a busy port, the epoch record, disabling and re-arming
  capture, the timing of GETC, and a digest recomputed independently from the observation
  records.
- **The simulator against the RTL, tick for tick.** Random programs with heavy tape
  traffic, and every protocol firmware, are compared commit by commit, pin by pin on every
  tick, and on the digest, the capture state and all 256 tape words at the end.
- **Formal.** Proved properties: capture never writes outside its region, the staging
  buffer never holds more than two records, and records are generated only during a run.
  Cover goals reach capture overflow, a full region, a ring wrap, and a drain together
  with a new record while two are staged.
- **Mutation.** Mutants of the digest, the tape port, the LDT write-back, GETI's wait and
  the capture rules are killed by the suites ([04-VERIFICATION](04-VERIFICATION.md)).
- **Record and replay** (`sw/tests/test_tape_replay.py`). A UART capture replays with
  identical edge spacing, and a trace that is too dense is refused with ADMIT, not
  replayed late.

## Limits

- **Replay** covers one 2^24-tick epoch (0.67 s at 25 MHz) and costs ten instructions, 40
  ticks, per record. A denser trace faults (ADMIT) at the first edge it cannot meet; the
  four-entry queue absorbs short bursts.
- **Capture** sees the synchronised inputs, so it has their two-tick lag, and a pulse
  shorter than a tick may not be seen.
- **The EEPROM** stretches the clock before each ACK, which a real 24C02 does not. Its
  Fast-mode timing relies on holding SCL low within 28 ticks of the eighth falling edge,
  inside the 33-tick minimum low time at 25 MHz; this is measured in simulation against
  the controller model, and the firmware has also stood in for a real 24LC02B on a real bus
  recording ([04-VERIFICATION](04-VERIFICATION.md)).
