# Verification

Escapement's correctness rests on several independent kinds of evidence, each of which
can catch a mistake the others miss:

| Layer | What it establishes | Where |
| --- | --- | --- |
| Normative specification | One cycle-exact definition of the machine | [11-SEMANTICS](11-SEMANTICS.md) |
| Instruction-set simulator | A second implementation of that definition, in Python | `sw/esciss` |
| Lockstep co-simulation | RTL and simulator agree on every commit and every pin, every tick | `test/cosim` |
| Formal proofs | Safety properties hold in every reachable state, not just in tests | `formal/`, `ifdef FORMAL` blocks in `src/` |
| Mutation testing | The tests notice a broken design | `test/mutation` |
| Timing certificates | Firmware can never trigger an admission fault, for any input | `sw/escert` |
| On-chip telemetry and digest | The silicon checks the certificates and the model | [architecture](01-ARCHITECTURE.md) |
| Gate-level simulation | The hardened netlist behaves like the RTL | `test/` with `GATES=yes` |
| Equivalence checking | The hardened netlist equals the RTL for every input sequence | `formal/equiv.sh` |

## Two implementations of one specification

The simulator and the RTL are separate implementations of the specification. They share
only the encoding tables in `isa/isa.yaml`; execution semantics, timing rules and the
certificate checker are never generated from shared code, because a shared decoder can
make two models agree on the same bug. Questions found while implementing each model are
recorded with their resolution in `sw/esciss/SPEC_QUESTIONS.md` and
`src/ESC_CORE_SPEC_QUESTIONS.md`.

The co-simulation harness runs both models side by side and compares, every tick: each
commit (context, PC, instruction, outcome, post-commit registers and flags), the pin
values and output enables, and at the end of the run all debug state, the execution
digest, the capture state and all 256 tape words.

| Campaign | Programs | Commits | Mismatches |
| --- | ---: | ---: | ---: |
| Random programs, version 1.0 machine | 1,000 | 67,370 | 0 |
| Random programs with tape traffic, version 1.1 | 300 | 21,055 | 0 |
| Random programs with pin bank B, version 1.2 | 1,000 | 66,991 | 0 |
| Firmware programs with their bus models | 15 | — | 0 |
| Regression run (`make cosim`, 200 random programs plus all firmware) | 4 tests | — | 0 |

Random programs come from a constrained generator (`test/cosim/progen.py`) that mixes
every instruction class, deliberate faults, host mailbox traffic, and tape loads and
stores that collide with capture drains. Since version 1.2 it also puts contexts on pin
bank B, owns and drives bank B pins, and points the capture unit at either bank, each on
a random board for both banks: in the version 1.2 campaign, 520 programs configure a
bank B context and 187 change a bank B output. A program with no bank B context is the
same program as in version 1.1, so the earlier campaigns still apply.

## Formal proofs

SymbiYosys, with properties embedded in the RTL. The instruction memory is a free input,
and the tick counter can start anywhere, so the 32-bit wrap is reachable.

| Property | Statement | Method |
| --- | --- | --- |
| P1 | An open-drain pin never drives high | Unbounded (PDR) |
| **P2** | **Never late: no FIFO head is ever past its deadline** | Unbounded, assume-guarantee (below) |
| P3 | An unowned pin is never driven, in either bank (a bank B pin stays at its released level) | Unbounded (PDR), in two parts: a pin away from its released level has its output enable set, and an unowned pin never has one set |
| P4 | FIFO occupancy stays within bounds | Unbounded (PDR) |
| P5 | A faulted context drives none of its pins, in either bank | Unbounded (PDR), in two parts: a pin away from its released level has its output enable set, and a faulted context never has one set |
| P6 | An admitted event is strictly ahead of its commit tick and of the FIFO tail | Unbounded (PDR) |
| P7 | Fixed slot timing: a commit belongs to the context that owns that slot | Unbounded (PDR) |
| P8 | Every push meets the per-context assumptions; HALT completes only on an empty FIFO; faults happen only during a run; a commit comes only from a running context, never on a start, stop or fault edge | Unbounded (PDR, with P2 for the lateness part of the last clause, below) |
| H1–H4 | Per context: queued deadlines are ahead of now and strictly increasing, only a running context has queued events, a queued DRIVE touches only owned pins | Unbounded (k-induction); H3 and H4 also by PDR in the core proof, as lemmas for P5 |
| C1–C3 | Capture never writes outside its region, never stages more than 2 records, and generates only during a run | Unbounded (PDR) |
| G1–G3 | A clock-gated bank equals an enable flip-flop at every instant; the gated clock is never high while the clock is low, and rises only on a clock edge | Unbounded (k-induction, unconstrained clock) |
| S1 | The configuration the core runs with (enabled contexts, entry points, pin ownership, open-drain pins, pin banks) never changes during a run | Unbounded (PDR), on the host interface |

**Assumptions.** The core proofs assume two things about their environment: reset at
the first step, and S1. S1 is proved on the host interface (`formal/esc_host.sby`),
with every SPI pin and core-side input free and the core's run flag modelled exactly as
the core drives it; removing the host's "only while stopped" check makes it fail. Every
other input (instructions, pins, host strobes, mailboxes) is unconstrained.

**Never late, for all time.** P2 is proved compositionally. The per-context proof
(`formal/esc_ctx.sby`) shows by k-induction that a context fed only admitted pushes
never has a FIFO head past its deadline; the core proof shows that every push the core
makes satisfies those assumptions (P6, P8). Together they cover every reachable state,
including counter wrap.

The split is drawn so that neither side assumes what the other proves. A late FIFO head
is itself a fault, so "no fault on a commit" contains P2. The core therefore proves that
no other fault coincides with a commit, the per-context proof assumes only that, and P2
completes the clause. The exception cannot hide a late head, because lateness depends on
state alone, never on the inputs of the same cycle. Stated as one clause, P8 had not
converged after seven hours of PDR; split this way, it proves in under a minute.

**Run time.** `make formal` proves the core in parallel: `formal/split_core.py` makes
one task per assertion source line from the elaborated design and runs `JOBS` of them at
once. A task proves its assertions without the others as lemmas, so a property that
needs a lemma can take far longer split than together. Assertions tagged with the same
`lemma-group` are therefore proved in one task: "a faulted context's output enables stay
clear" proves in 140 seconds together with H3 and H4, and did not finish in two hours on
its own. With 6 jobs all 21 tasks pass in 17 minutes of wall time; the slowest is a
capture-region bound. The single task (`sby -f esc_core.sby pdr`, all assertions
together) passes too, in 34 minutes on one core.

**Vacuity guards.** A cover task reaches the precondition of every property, including
capture overflow, region full, ring wrap, and a drain and a push with two records
staged. The clock-gate proof has teeth: a naive `clk & en` gate fails G3 at the first
step.

## RTL to netlist equivalence

The proofs above are about the RTL; the chip is built from the netlist that synthesis,
placement and routing produce. `make equiv` proves that the two are the same machine:
the final netlist of the version 1.2 harden (sha256 `65db67ec…`) is
sequentially equivalent to the RTL in `src/`, for every input sequence after reset.

Both designs are flattened and placed side by side in a miter that feeds them the same
pins, the same SRAM read data and the same clock, and compares all 24 chip outputs and
every SRAM input (address, data, enables, 332 bits in all) on every step. ABC's `dprove`
searches 19 steps for a difference, then matches the RTL's registers with the netlist's by
signal correspondence and closes the proof by induction. Nothing is matched by name, so
the netlist's renamed registers need no mapping, nor do the registers that reset to one
in the RTL and that synthesis builds as reset-to-zero flip-flops with inverters. The proof
takes about 3 minutes. The version 1.1 release netlist (sha256 `1c9004ee…`) passes the
same proof.

**The check has teeth.** Two faults injected into the netlist are both found: a NAND
in the `uio_oe[0]` path turned into a NOR fails at the first compared step, and the MISO
shift register's bit 6 wired to bit 5's input fails at step 21, about ten clock cycles
after reset.

**Scope.** The SRAM macros are hard IP and are not compared, only everything around
them. Cell behaviour comes from the functions in the PDK's liberty file, so this is a
statement about logic; timing is the signoff's job. The clock is ideal, one edge per
step, so the clock tree is checked as logic, not as delays. The clock-gate latches have
no reset and start at zero in both designs. Reset is held for the first steps and free
afterwards, as is every other input.

## Mutation testing

`test/mutation/run_mutants.py` applies one-line faults to a copy of `src/`: inverted
comparisons, off-by-one deadlines and pointers, a dropped polynomial term in the digest,
a missing GETI wait, broken tape forwarding, capture rule changes and clock-gate enable
errors. Each mutant runs against the co-simulation first; if that misses it, the
directed unit suite runs too, and the report says which one caught it.

Eight mutants target pin bank B: a DRIVE landing one pin off, a released output going
low, a context reading the other bank, ownership checked against the wrong bank, a fault
that keeps its bank B pins, inputs that never reach the synchronisers, a digest blind to
bank B, and capture ignoring the bank bit.

**Result: 28 of 28 mutants killed.** Twenty-seven are caught by the co-simulation. One
clock-gate mutant (START not clearing `tmr`) is caught only by the directed suite, and
the report keeps that visible.

## Timing certificates

`sw/escert` treats firmware as a timed automaton and runs a zone (difference-bound
matrix) analysis over every path, covering all five admission subcodes. It returns
CHECKED with a lower bound on scheduling lead, REFUTED with a witness that the simulator
replays into the fault, or NOT_PROVEN.

| Firmware | Result | Certified minimum lead (ticks) |
| --- | --- | ---: |
| `uart_tx` | CHECKED | 9 |
| `uart_rx` | CHECKED | 301 |
| `spi_ctrl` | CHECKED | 7 |
| `i2c_ctrl` | CHECKED | 2 |
| `i2c_eeprom` | CHECKED | 2 |
| `combo_uart_i2c` (three contexts) | CHECKED | 2 / 301 / 7 |
| `combo_two_banks` (four contexts, both banks) | CHECKED | 5 / 4 / 7 / 6 |
| `ws2812` | CHECKED | 5 |
| `onewire` | CHECKED | 4 |
| `can_tx` | CHECKED | 3 |
| `ps2_device` | CHECKED | 4 |
| `jtag_idcode` | CHECKED | 3 |
| `spi_target` | CHECKED | 1 |
| `nec_tx` | CHECKED | 9 |
| `nec_rx` | CHECKED | no events |
| `swd_idcode` | CHECKED | 3 |
| `logic_analyzer` | CHECKED | no events |

Every certificate is CHECKED for all inputs, without assumptions about bus timing:
re-anchoring the time base after every wait makes the input-dependent I2C paths provable
unconditionally. The certified bounds are at or below the leads measured by the chip's
telemetry in simulation, and most are equal to the tick. `replay.asm` computes its
deadlines from captured data, so it is checked by the replay tests and by the hardware's
admission rules instead.

**The checker found real bugs.** It refuted the I2C controller's stuck-bus timeout,
which released the bus with a lead of −4, and a PS/2 frame that started under the
previous frame's queued events when scancodes arrived back to back. No test had reached
either. In both cases the chip would have refused the edge with an ADMIT fault rather
than emitting it late. Both are fixed, and the old versions are regression tests.

**The checker is tested, not trusted.** On random co-simulation programs, every CHECKED
context runs on the simulator without an admission fault and with telemetry at or above
the bound. That test found two checker bugs before release, and the chip's telemetry
caught a third on the first run.

## Protocol coverage

Each firmware runs against an independent bus model in `sw/escmodels` on the simulator
(`sw/tests/test_firmware.py`), and in lockstep on the RTL (`test/cosim`):

| Protocol | Firmware | Checked against |
| --- | --- | --- |
| UART 8N1 at 115,200 baud (the rate is one constant) | `uart_tx`, `uart_rx` | Receiver and transmitter models; all 256 byte values, back-to-back frames, ±2% peer baud error |
| SPI controller, modes 0 to 3 | `spi_ctrl` | SPI target model (all four modes on the simulator, mode 3 on the RTL) |
| I2C controller, Fast mode, clock stretching, address NACK | `i2c_ctrl` | I2C target model that flags Fast-mode timing violations |
| I2C 24C02 EEPROM target, Standard and Fast mode | `i2c_eeprom` | Scripted controller that checks the target's timing; page roll-over |
| WS2812 | `ws2812` | LED chain model |
| 1-Wire, open-drain with timed sampling | `onewire` | 1-Wire device model (ROM read) |
| CAN 2.0A transmitter, 250 kbit/s | `can_tx` | Receiver model that destuffs, checks CRC-15 and acknowledges |
| PS/2 device | `ps2_device` | PS/2 host model, back-to-back scancodes |
| JTAG IDCODE read | `jtag_idcode` | JTAG TAP model, back-to-back requests |
| SPI target: a 256-byte 25xx-style memory (READ, WRITE) in the tape, modes 0 and 3, up to 416 kHz | `spi_target` | SPI controller model that checks MISO setup and hold at every sampling edge; every edge alignment at the rated period, CS_n rising after every bit, back-to-back frames |
| NEC infrared transmitter with 38 kHz carrier (repeat codes, extended address) and receiver | `nec_tx`, `nec_rx` | Receiver model that demodulates the carrier and checks frequency, duty and the Linux decoder's timing windows; remote-control model with ±10% clock error; transmitter → IR receiver module model → receiver loopback |
| ARM Serial Wire Debug host: JTAG-to-SWD switch, DPIDR read, DP and AP reads and writes | `swd_idcode` | SW-DP target model from ADIv5.2 (IHI 0031G): WAIT retries, FAULT and ABORT, read parity errors, no-response back-off, turnaround contention |
| UART and I2C concurrently | `combo_uart_i2c` | Both models at once, on separate contexts |
| PS/2 and JTAG on bank A, SPI and UART on bank B, concurrently | `combo_two_banks` | All four models at once; the bank B ones see only the split pins |
| Logic analyser and replay | `logic_analyzer`, `replay` | Tick-exact replay of a captured UART trace; a too-dense trace is refused |

## Test suites

| Suite | Tests | Result |
| --- | ---: | --- |
| Software (`make sw-test`): assembler, simulator, certificates, models, tape tools, bring-up script | 260 | Pass |
| Chip level on the RTL (`make test`) | 5 | Pass |
| Chip level on the final gate-level netlist (`make gl-test`) | 5 | Pass |
| Co-simulation (`make cosim`) | 5 | Pass |
| Host interface (`make host-test`) | 10 | Pass |
| Core unit tests (`make unit-test`) | 22 | Pass |
| Newer firmware (SWD, SPI target, NEC, both pin banks at once) through the chip pins (`make pin-test`, `make gl-pin-test`) | 5 | Pass on the RTL and on the final netlist |
| Bring-up script on the chip RTL (`make bringup-test`) | 2 | Pass |
| Lint (`make lint`, Verilator `-Wall`) | — | Clean |

The chip-level tests drive the real pins: they load firmware over SPI, run it, decode
the protocol on the pins, and compare the execution digest with the simulator's
prediction. On the gate-level netlist, an equal digest means the gates executed exactly
the commits, register values and pin changes of the model.

## Limits

- **Pad timing.** CMOS5L pads are not characterised, so timing claims are made at the
  internal tick, not at the package pins.
- **Metastability** is not modelled probabilistically: inputs pass through a two-flop
  synchroniser, and simulation models it as a two-tick delay.
- **The EEPROM** stretches the clock before each acknowledge, which a real 24C02 does
  not; its Fast-mode margin is measured against our controller model only.
- **Replay** covers one 2^24-tick epoch (0.67 s at 25 MHz).

## Rerunning

```sh
make sw-test cert test cosim-parallel host-test unit-test lint   # simulation and certificates
make pin-test bringup-test                                       # newer firmware through the pins; bring-up script
make gl-test gl-pin-test                                         # the same chip-level checks on the gate-level netlist
make formal-quick                                                # bounded core proof, cover, never-late, clock gate
make formal JOBS=8                                               # adds the unbounded core proof (~20 min)
make equiv                                                       # final netlist equals the RTL (~4 min)
make mutation JOBS=8                                             # mutation campaign, 8 mutants at a time
```

The suites run in parallel where they can: `sw-test` uses every core when pytest-xdist is
installed, `cosim-parallel` splits the random programs and the firmware runs across
`JOBS` simulators from one build, `formal` proves the core's assertions as parallel
tasks, and the mutation campaign runs `JOBS` mutants at once.

Every simulation target also runs on Verilator with `SIM=verilator` (5.036 or later).
Icarus stays the default and the reference; CI uses it. Measured on one machine, both
simulators passing every suite:

| Suite | Icarus | Verilator |
| --- | ---: | ---: |
| `bringup-test` | 985 s | 140 s |
| `cosim` (one process) | 193 s | 57 s |
| `cosim-parallel JOBS=8` | 63 s | 30 s |
| `pin-test` | 149 s | 40 s |
| `test`, `host-test`, `unit-test` | 3–24 s | 12–19 s (mostly compilation) |

GitHub Actions runs the chip-level tests, `sw-test`, `cert`, `cosim-parallel` and
`pin-test` on every push, and the harden with the gate-level tests whenever the design
changes.
