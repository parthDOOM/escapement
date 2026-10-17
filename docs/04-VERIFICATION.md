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
| Firmware programs with their bus models | 11 | — | 0 |
| Regression run (`make cosim`, 200 random programs plus all firmware) | 4 tests | — | 0 |

Random programs come from a constrained generator (`test/cosim/progen.py`) that mixes
every instruction class, deliberate faults, host mailbox traffic, and tape loads and
stores that collide with capture drains.

## Formal proofs

SymbiYosys, with properties embedded in the RTL. The instruction memory is a free input,
and the tick counter can start anywhere, so the 32-bit wrap is reachable.

| Property | Statement | Method |
| --- | --- | --- |
| P1 | An open-drain pin never drives high | Unbounded (PDR) |
| **P2** | **Never late: no FIFO head is ever past its deadline** | Unbounded, assume-guarantee (below) |
| P3 | An unowned pin is never driven | Bounded (BMC from reset) |
| P4 | FIFO occupancy stays within bounds | Unbounded (PDR) |
| P5 | A faulted context drives none of its pins | Bounded (BMC from reset) |
| P6 | An admitted event is strictly ahead of its commit tick and of the FIFO tail | Unbounded (PDR) |
| P7 | Fixed slot timing: a commit belongs to the context that owns that slot | Unbounded (PDR) |
| P8 | Commits come only from running contexts; every push meets the per-context assumptions | Unbounded (PDR) for the push conditions, bounded for the rest |
| H1–H4 | Per context: queued deadlines are ahead of now and strictly increasing, only a running context has queued events, a queued DRIVE touches only owned pins | Unbounded (k-induction) |
| C1 | Capture never writes outside its region | Bounded (BMC from reset) |
| C2–C3 | Capture never stages more than 2 records, and generates only during a run | Unbounded (PDR) |
| G1–G3 | A clock-gated bank equals an enable flip-flop at every instant; the gated clock is never high while the clock is low, and rises only on a clock edge | Unbounded (k-induction, unconstrained clock) |

**Never late, for all time.** P2 is proved compositionally. The per-context proof
(`formal/esc_ctx.sby`) shows by k-induction that a context fed only admitted pushes
never has a FIFO head past its deadline; the core proof shows that every push the core
makes satisfies those assumptions (P6, P8). Together they cover every reachable state,
including counter wrap.

**Vacuity guards.** A cover task reaches the precondition of every property, including
capture overflow, region full, ring wrap, and a drain and a push with two records
staged. The clock-gate proof has teeth: a naive `clk & en` gate fails G3 at the first
step.

## Mutation testing

`test/mutation/run_mutants.py` applies one-line faults to a copy of `src/`: inverted
comparisons, off-by-one deadlines and pointers, a dropped polynomial term in the digest,
a missing GETI wait, broken tape forwarding, capture rule changes and clock-gate enable
errors. Each mutant runs against the co-simulation first; if that misses it, the
directed unit suite runs too, and the report says which one caught it.

**Result: 20 of 20 mutants killed.** The four clock-gate mutants are caught by the
directed suite rather than by the random co-simulation, and the report keeps that
visible.

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
| `ws2812` | CHECKED | 5 |
| `onewire` | CHECKED | 4 |
| `can_tx` | CHECKED | 3 |
| `ps2_device` | CHECKED | 4 |
| `jtag_idcode` | CHECKED | 3 |
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
| UART and I2C concurrently | `combo_uart_i2c` | Both models at once, on separate contexts |
| Logic analyser and replay | `logic_analyzer`, `replay` | Tick-exact replay of a captured UART trace; a too-dense trace is refused |

## Test suites

| Suite | Tests | Result |
| --- | ---: | --- |
| Software (`make sw-test`): assembler, simulator, certificates, models, tape tools | 170 | Pass |
| Chip level on the RTL (`make test`) | 5 | Pass |
| Chip level on the final gate-level netlist (`make gl-test`) | 5 | Pass |
| Co-simulation (`make cosim`) | 4 | Pass |
| Host interface (`make host-test`) | 9 | Pass |
| Core unit tests (`make unit-test`) | 22 | Pass |
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
make sw-test cert test cosim host-test unit-test lint   # simulation and certificates
make formal                                             # proofs (SymbiYosys)
make mutation                                           # mutation campaign
```
