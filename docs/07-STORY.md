# How Escapement was built

The other chapters describe the chip as it is. This one tells how it got that way: which
idea came first, what was tried and dropped, and why each version added what it added.
The decisions themselves, with the alternatives that lost, are recorded one by one in
[06-DECISIONS](06-DECISIONS.md).

I came to this from the firmware side. More than once I have watched a protocol driven from
software miss its timing, with nothing to say so until the device at the other end
misbehaved, and when the Jane Street competition offered a way to put a chip into
fabrication, that failure was the one I wanted to design out.

## The question

The [Jane Street protocol emulator competition](https://blog.janestreet.com/protocol-emulator-asic-competition/)
asks for a chip, built through Tiny Tapeout on IHP's 130 nm process, that can speak
digital protocols: be a UART, an I2C target, an SPI device, whatever a test bench needs.
The obvious design is a small processor that bit-bangs its pins in software. Its weakness
is equally obvious to anyone who has debugged one: a processor that bit-bangs is right
only as long as it is fast enough, and when it is not, an edge comes out late and nothing
notices.

The first design worked on a different question. Instead of "how fast can the chip
toggle a pin?", it asked "how can the chip know, at the moment a program asks for an
edge, whether that edge will be on time?". The answer was to separate deciding from
doing. A program decides early and hands over an event with an absolute deadline; a
timing unit does the edge on exactly that tick, or refuses the event at once if the
deadline cannot be met. Everything since has been built on that idea.

## Cutting to the core

An early proposal, in September 2026, wrapped that idea in much more: weighted scheduling
between programs, arbitrary capture and replay, and timing claims that rested on cell
counts nobody had measured. A review found that its pieces did not fit together. The
scheduler assumed a pipeline that the instruction set could not keep fed, and the replay
feature had no storage budget. Timestamped I/O survived as the thesis, and nearly
everything else was cut.

The rebaseline of 4 October set the shape that the chip still has:

- **A program store in SRAM.** The process kit includes single-port SRAM macros, and a
  256 × 32 macro takes about 50,000 µm², against roughly 700,000 µm² for the same storage
  in flip-flops. That one fact made a real instruction memory affordable.
- **A fixed four-slot barrel.** Four contexts take strict turns, so no program can slow
  another down, and the timing of a program can be worked out from the program alone.
- **EMITX**, which sends the next bit of a shift register to a pin, so a serial bit loop
  is three instructions instead of a branch per bit.
- **EXPECT and telemetry.** Input checks went into the same deadline queue as outputs,
  and the chip measures its own scheduling margin, so a static timing certificate can be
  checked against the hardware. A survey of the other public entries found neither.
- **Open-drain level semantics.** On an open-drain pin the event's value is the bus level
  wanted, so I2C data needs no inversion and an open-drain pin can never be driven high.
- **A host link sampled with the chip's own clock**, so the design has one clock domain.
- **The name.** A clock's escapement releases exactly one tick at a time, which is what
  admission does.

## Two implementations of one specification

Before any silicon, the machine was written down as a cycle-exact specification,
[11-SEMANTICS](11-SEMANTICS.md). Two implementations were then written from it
independently: the Python instruction-set simulator and the RTL, by separate authors who
could not read each other's code. By 11 October their tick-by-tick traces agreed on every
firmware program with its bus models and on a thousand random programs. The two
implementers' written questions about the specification differed on one point, whether a
context's checker evaluates at the edge where the context faults, and the specification
was settled in favour of cancelling it.

That arrangement became the backbone of the verification: the simulator is the reference
model for the RTL, the two run in lockstep in co-simulation, and a disagreement is always
a bug in one of them or in the specification. The admission rule also earned its place
before any chip existed, by refusing four firmware bugs that a conventional processor
would have run with a late edge: late first-bit deadlines in I2C and SPI, a stale deadline
after a 1-Wire reset, and an off-by-one in a re-anchor.

## Growing one capability at a time

The first public release, version 1.1, came on 17 October. It added the **tape**, a
second SRAM shared by firmware loads and stores and a capture unit that records pin
changes with their ticks, and the **execution digest**, a 32-bit signature of a whole run
that the simulator predicts exactly. With those, the chip could record and replay a
waveform, and silicon could be checked against the model with one read. The layout from
the competition's own CI matched the local one in every reported metric, which made
reproducibility a standing rule.

Each later version added one capability and kept every older program's behaviour
unchanged, digest included:

- **Version 1.2** (18 October) noticed that the host link left five dedicated inputs and
  outputs unused, and turned them into **pin bank B**. Each context chooses its bank, so
  the same firmware runs on either, and the chip went from 8 protocol signals to 18.
- **Version 1.3** (20 October) let the host read the tape **during** a run, so a capture
  could stream for as long as the host kept up, and made sure no record is ever
  overwritten before the host has read it.
- **Version 1.4** (27 October) added the **line unit**. Low-speed USB at 24 MHz leaves four
  instructions per bit, too few for NRZI, bit stuffing and a CRC, so a small configurable
  block took over the bit work while firmware kept the packets. A USB keyboard written for
  it enumerated on a real Linux kernel through USB/IP, and the certificate checker proved
  every reply leaves exactly 72 ticks after the host's packet. The extra logic made the
  design 11% larger, and moving rarely written registers from flip-flops into latches
  brought it back below the previous version's size.

## Learning to be fast

Version 1.4 met timing only up to 28.6 MHz at the slow corner, and the longest paths all
had the same shape: a 32-bit comparison of a deadline with the tick counter, followed by
a pin update in the same cycle. Versions 1.5 (1 November) and 1.6 (5 November) removed
that shape. Each comparison is now made a cycle early and kept in a register, which is
safe because the values compared change only at a context's own commit, four cycles
before they are used; the proofs check in every cycle that the early answer equals the
late one. Version 1.5 reached 38.6 MHz. Version 1.6 decoded instructions and read operands
a stage earlier too, and was signed off at 20 ns: the chip has been rated for 50 MHz since.

## On hardware

On 8 November version 1.6 went onto an FPGA: the chip's own RTL,
built for a Sipeed Tang Primer 25K, driven by a Raspberry Pi Pico W playing the host. A
staged bring-up script, rehearsed earlier against a simulated board with planted faults,
ran every firmware and compared each one's telemetry with its certificate. It passed.

Around the same time the bus models met real devices. Recordings of real hardware, from a
USB Power Delivery charger to a DS18B20 temperature sensor, an SD card and a DCC model
railway command station, became test oracles: the chip's firmware and models were judged
against what real devices actually did. The recordings found real bugs: the UART receiver
lost a byte sent straight after a stop bit by an STM32 at 460,800 baud, and the I2C
EEPROM firmware, standing in for a real 24LC02B, faulted at clocks below 20 MHz. Both were
fixed, and the second showed the admission rule doing its job: the firmware was refused
with a fault, not run with a late edge.

## Two programs, then four, side by side

Version 1.7 asked how much a single context could do and how freely programs could be
changed. **Pair mode** lets a context take an idle partner's slot and run twice as fast,
with a fetch prediction that is checked at commit, so a wrong guess costs a slot and never
a wrong result. **Live reload** lets the host stop one context, rewrite its program and
restart it while the others run. That raised a question the earlier proofs could not
answer: could the reload disturb a neighbour? A **non-interference proof**, comparing two
copies of the chip that differ only in what is aimed at other contexts, showed that it
cannot, within a stated scope.

On 9 November the competition's rules page confirmed that 8 × 4 tiles were allowed.
**Version 2** used the larger die for **program pages**, a 1,024-word store in four pages
so four full-size programs can run with no linking, and for **proof tripwires**, twelve
proved invariants built into the silicon as sticky flags that should never set. Moving to
8 × 4 also brought the project's most useful physical finding. The bigger die did not make
routing easier, because the chip is limited by wiring on its four metal layers, not by
cell area, and only one of those layers runs horizontally for signals. The first 8 × 4
layouts overflowed it. A measured analysis led to a new floorplan and to timing-driven
placement, and timing closed with margin at 20 ns.

## Version 3: listening as well as speaking

The last round of hardware work turned the chip's exactness outward. The line unit, which
could already send 10BASE-T Ethernet, learned to **receive** with three strobe sources: a
recovered clock, a **cell decoder** for Manchester codes and an **external clock** for
sniffing clocked buses. A margin calculation showed that 10BASE-T at 50 MHz needs
edges placed to half a tick, so the chip gained a **half-tick sampler** on the falling
clock edge, the only place it looks at a pin between ticks. And a **bus timing monitor**
began checking other devices' setup, hold, width and period against their datasheets.

Two checks shaped version 3 directly. The non-interference proof failed once the monitor
was added, because any context could read state that every other context could shape; the
fix was an **owner** for the monitor, the same rule the line unit already had. And a test
that judged I2C timing from the waveform itself, rather than from the model, found that
the RTL and the model both measured intervals one tick short, a bug the lockstep
comparison could never have seen because the two agreed.

The full monitor did not fit the version 2 floorplan: with the tape at the far left, the
logic spread across the die and its horizontal traffic overflowed again. Version 3 moved
both memories into one column on the east side, so the logic is a single rectangle with no
macro in its horizontal paths. A decorative layout-art macro, which stranded rows in that
floorplan, was removed.

Version 3.1 is a small step in silicon. A context whose partner is switched off can now
**span** both of their pages, enough for a Modbus device with its whole data model, and the
reset is released through a two-flip-flop synchroniser after a duty-cycle check found the
raw reset reaching some latches too late. The larger change was in the layout: version 3.0
had been signed off with slew, capacitance and fanout violations left standing, and a
series of measured trial hardens traced them to the repair step's picture of the wires and
of the slow corner. Version 3.1 meets every electrical rule at every corner. On the FPGA
bench the same RTL answered a laptop's ping over 10BASE-T and enumerated on its USB port as a
keyboard.

Then the chip was asked to serve a real program rather than a protocol test. The bench
Pico ran DOOM and handed every frame and sound to the chip, which drove an OLED and a laptop's
headset jack through four contexts at once. It needed two pieces of firmware and no hardware:
a PWM sound player and a stream mode for the I2C writer, so a frame could go out of the
mailbox while the Pico drew the next ([25-DOOM-CONSOLE](25-DOOM-CONSOLE.md)).

The last bench runs closed two gaps between what the simulator had shown and what the pins
had. The refusal, the idea the chip started from, had only ever been seen in simulation;
on 24 November two programs that ask for a late edge and an unowned pin were refused on the
FPGA build, and on 27 November on version 3.2's, where the certificate checker had said they would be. And learning, until then
checked against recordings in software, ran on the chip's own capture: it recorded a 433 MHz
remote's frames, and the transmitter and receiver generated from what it learned ran on the
same chip.

The last test was of the claim itself. Every program until then was for a protocol someone
had chosen knowing what the chip could do. On 4 November, with version 1.6, a pool of 24
protocols had been fixed and eight drawn from it at random, the drawn list sealed by a hash
committed before any was written. The seal was opened on 24 November, once version 3.1 was
tagged, and the eight were written on its chip, assembler and simulator unchanged: a MIL-STD-1553B terminal, an S/PDIF
transmitter, a Microwire EEPROM master, a DHT22 host, a BiSS C master, a PS/2 host, a SNES
pad reader and an M-Bus slave. All eight fit. The chip had no bug to show; the certificate
checker had one, a `CONFIG` it never checked the way the chip does, and it was fixed
([heldout/RESULTS](../heldout/RESULTS.md)).

The last version was for power alone. Version 3.1 still clocked 1,738 flip-flops at every
edge, whether or not anything wrote them, and a waiting context read its `WAIT` from the
program store again in every slot. Version 3.2 put those registers behind clock gates that
open only when the register is written, and parked a waiting context on the word it already
held. Its first layouts lost timing margin to a latch on the TX pin claim and to a chain of
97 buffers that a repair step of the layout tool built; a flip-flop and one flow setting won
it back. A stopped chip went from 3.36 to 1.27 mW, and nothing a program sees changed
(D-73 to D-77).

## What stayed the same

Through all of this the central promise did not change, and neither did the working
rule: every claim gets a second, mechanical check. Each feature arrived with its proof or
its test, and each proof with a planted fault that shows it can fail. The bugs those
checks found are recorded in [04-VERIFICATION](04-VERIFICATION.md).
