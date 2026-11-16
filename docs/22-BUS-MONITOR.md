# The bus timing monitor

Escapement's central promise is about its own edges: an edge it has accepted happens on
its tick. The bus timing monitor turns the same exactness outward. It watches traffic
between other devices, which the chip does not drive, and checks every edge against the
timing those devices promise in their datasheets: setup and hold times, pulse widths,
clock periods, timeouts. A device that breaks its datasheet is reported with the rule it
broke, the tick it happened and the interval measured.

The normative definition is [11-SEMANTICS §6.7](11-SEMANTICS.md#67-bus-timing-monitor-v3-one-global),
the instructions are in [02-ISA](02-ISA.md#bus-timing-monitor), and the RTL is
`src/esc_mon.v`, with `sw/esciss/monitor.py` as its reference model.

## Why a separate unit

The chip already has a way to check inputs: the EXPECT event, which asks that the inputs
hold a level, or arrive at it, within a window after one of the program's own deadlines.
EXPECT answers the question "did the other side respond to my edge in time?". It cannot
answer a question about two edges that both come from outside, such as an I2C target's
data setup time against a clock that the controller generates, and it only watches while
a program has a window open.

The monitor answers those questions. It is passive, so it drives nothing and needs no
pins of its own; continuous, so it watches every tick of a run without the program doing
anything; and anchored to edges, so every interval it measures starts at an edge on the
bus rather than at a deadline the program chose. Between them, EXPECT checks the answers
to the chip's own edges and the monitor checks traffic the chip only watches.

## One rule shape

The monitor holds four **rules**. Each has the same shape:

> the next edge of pin B comes at least MIN and at most MAX ticks after an edge of pin A

where each edge can be rising, falling or either, and MIN or MAX can be switched off by
setting it to 0. This one shape covers the timing figures that datasheets list:

| Datasheet figure | From-edge | To-edge | Limits |
| --- | --- | --- | --- |
| Setup time | data, either edge | clock, sampling edge | MIN |
| Hold time | clock, sampling edge | data, either edge | MIN |
| Pulse width (high) | pin rises | same pin falls | MIN, and MAX if bounded |
| Clock period | clock rises | clock rises | MIN, and MAX if bounded |
| Timeout (a stuck clock, a missing reply) | the edge that starts the wait | the edge that ends it | MAX |

A rule starts timing at its from-edge and stops at the next to-edge. If the to-edge comes
before MIN ticks, the rule records an **early** violation. If MAX ticks pass with no
to-edge, it records a **late** violation at that moment, without waiting for the edge
that may never come. Either way the rule then waits for its next from-edge. A new
from-edge restarts the timing, which is what a period or a repeated pulse needs.

Intervals are counted in ticks, up to 65,535: 1.31 ms at 50 MHz and 2.62 ms at 25 MHz.
That covers the bit and clock timings of I2C, SPI, CAN and UARTs down to about 1,200 baud
at 50 MHz.

## What it reports

Each rule counts its violations, up to 255. The monitor also keeps one **record**: the
first violation since the run started or since the record was last cleared, with the
rule, whether it was early or late, the tick, and the interval measured. When several
rules break on the same tick, the lowest-numbered one is recorded and all of them are
counted.

A program reads the counts, the record and its tick with GETM, and clears them with MON.
A typical program configures its rules at the start and then polls the record, reporting
each violation and clearing it so the next one can be caught. Firmware can also turn a
violation into a host interrupt or into a pulse on a spare pin, where an oscilloscope can
trigger on it.

## How exact it is

The monitor sees the pins through the same two-flop synchroniser as every other input,
so it sees each edge two ticks after it reaches the pin. Every pin has the same delay, so
the delay cancels: the interval between two edges is measured exactly to the tick. What
remains is the resolution of one tick. An edge is placed at the first tick that sees it,
so a measured interval can differ from the true one by up to one tick either way, and an
I2C rule written with its limit rounded down to whole ticks never reports a bus that keeps
to its limits. The reverse is also true and should be stated plainly: a violation smaller
than one tick, 20 ns at 50 MHz, can go unseen, and a pulse shorter than a tick may not be
seen at all.

## An example: checking an I2C bus

[`firmware/i2c_timing.asm`](../firmware/i2c_timing.asm) watches SCL and SDA between a
controller and a target, drives nothing, and reports every clock pulse or data change
that breaks standard-mode I2C timing (100 kHz, with the limits from the I2C specification
for a 50 MHz clock):

| Rule | Measures | From-edge | To-edge | Limits |
| --- | --- | --- | --- | --- |
| 0 | SCL high time | SCL rises | SCL falls | at least tHIGH, 4.0 µs |
| 1 | SCL low time | SCL falls | SCL rises | at least tLOW, 4.7 µs; at most 1 ms (a stuck bus) |
| 2 | Data setup | SDA either edge | SCL rises | at least tSU;DAT, 250 ns |
| 3 | Clock period | SCL rises | SCL rises | at least 10 µs |

The firmware writes the four rules, then loops: it reads the record, and when a violation
is recorded it pushes three words to the host (the record, its tick, and the four counts)
and clears the record and the counts. The whole program is 22 instructions; the monitor
does all the measuring. START and STOP conditions move SDA while SCL is high, which rule 2
measures to the next rising clock edge; on a working bus that interval is never short, so
they cause no false reports.

Every limit is a parameter, so the same program checks fast mode or another clock rate by
changing `.default` values, and an `.assert` refuses limits that do not fit in 16 bits.

## Ownership

There is one monitor, so the chip has to decide which context controls it. It uses the
same rule as the line unit: the first context whose MON commits in a run owns the monitor
until the next start. Another context's MON faults with ADMIT/OWNER, and its GETM reads
zeros.

This rule came out of the non-interference proof. With a monitor that any context could
configure and read, a context's GETM would return state that every other context's MON
had shaped, so one program could observe another through the monitor. With an owner, a
program loaded live next to the owner can neither change its rules nor read what they
caught. The non-interference proof covers a context that does not use the monitor, and
checks that such a context's GETM always reads zero whatever the owner does
([04-VERIFICATION](04-VERIFICATION.md)).

The rules keep running after their owner halts or faults, so a monitor set up by a short
configuration program keeps watching for the rest of the run. That same program must also
be the one that reads the results.

`escert` checks the rule before an image runs. When more than one context can reach a MON,
each of them gets an OWNER finding, because only the first MON to commit takes the monitor.
A context that reads the monitor with GETM 0 to 2 but has no MON of its own gets a note:
it never owns the monitor, so those reads always return 0.

## The hardware

Each rule's pins, edge kinds, MIN and MAX are written rarely and read on every tick, so
they sit in latch banks behind clock gates, written through one shared word, in the same
style as the chip's other register banks. The record is a gated register bank too. The
running state of each rule, its armed flag and 16-bit counter, are ordinary flip-flops.
In synthesis the whole unit takes about 26,000 µm² of standard cells; a first version
with the rule settings in ordinary flip-flops took 31,200.

## How it is checked

- **Against its model, every cycle.** `make mon-test` runs `esc_mon.v` beside the
  reference model for 200,000 cycles of random rules and random pin traffic, and compares
  every output in every cycle. The bench fails if any of early violations, late
  violations, records, clears or saturated counts never happens, so a quiet run cannot
  pass by accident. Three planted bugs (the MIN boundary, MAX off by one, a clear
  ignored) each make it fail.
- **Formally.** `formal/esc_mon.sby` proves seven properties for all time: an armed rule
  has counted at least one tick; a count never wraps; a keep write changes no rule; the
  counter counts; a record holds until a start or a clear; every violation with no record
  is recorded, lowest rule first; and the latch banks hold what was written. Planted
  faults in `formal/teeth.sh` check that the proofs can fail.
- **Against the waveform.** [`sw/tests/test_i2c_timing.py`](../sw/tests/test_i2c_timing.py)
  runs `i2c_timing.asm` on I2C traffic from a model that plants timing faults at chosen
  clock pulses: a short high phase, a short low phase, a data change too close to the
  clock, a clock held low past the stuck-bus limit, and pulses exactly at each limit,
  which are legal. Every report must be one that the waveform's own edges call a
  violation, with its rule, kind and interval. This test found a real bug: the RTL and
  the model both measured intervals one tick short, so every legal 10 µs I2C period read
  as 499 ticks against a limit of 500. Because the model and the RTL agreed, the lockstep
  comparison could not see it; a test judged from the waveform could.
- **Through the whole chip.** The co-simulation runs `i2c_timing.asm` on the RTL and the
  simulator together, tick for tick, and random programs from the co-simulation
  generator use MON and GETM from every context.
- **Ownership.** `test_monitor_belongs_to_its_first_user` checks the owner rule on the
  RTL. Two planted faults check that the proofs catch a broken one: with a non-owner's
  GETM reading the monitor, the non-interference induction fails, and with a non-owner's
  MON taking effect, the bounded check of the core fails.

## Limits

- Four rules. Checking I2C fully (setup, hold, both clock phases, the period and a
  timeout) needs more than four, so a program chooses the four that matter for the bus
  at hand, or rotates through sets of rules.
- One detailed record. After the first violation, later ones are counted per rule but
  not recorded until the program clears the record.
- One tick of resolution, as described above, and intervals up to 65,535 ticks.
- The monitor watches the pins of both banks, but only through the synchronised view:
  it does not use the half-tick sample that the line unit's decoder can use.
