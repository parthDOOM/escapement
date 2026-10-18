# Design decisions

The choices that shaped Escapement, with the reasoning and evidence for each. The
numbers are stable identifiers cited throughout the code.

## Architecture

**D-1 Languages.** RTL in synthesisable Verilog/SystemVerilog. The assembler,
instruction-set simulator, certificate checker and tests in Python. One encoding file,
`isa/isa.yaml`, shared by all of them.

**D-2 Program memory: one 1P 256×32 SRAM macro.** A 256-word store in flip-flops would
take about 698,500 µm², 76% of the die; the macro takes 49,488 µm² (5.4%), about 14×
denser per bit. The macro passed LVS, the precheck and a gate-level test of all 256
words before the engine was built around it.

**D-3 Scheduler: a fixed 4-slot barrel.** Each context fetches every fourth cycle and
commits three cycles later, so there are no hazards, no forwarding and no interaction
between contexts. The cost is one instruction per context every 4 ticks, which is
enough: a serial bit loop is three instructions.

**D-4 No protocol hardware.** No serialisers, CRC engines or oscillators. The only
acceleration is `EMITX` (drive a pin from the bit shifted out of `sh`) and `EMITR`
(drive pins from a register), which make data-dependent waveforms branch-free. CRC-15
and bit stuffing for CAN run in firmware at 250 kbit/s.

**D-5 Clock: 25 MHz at the slow corner.** The rated frequency is quoted at the slow
corner (1.08 V, 125 °C), not the typical one.

**D-9 Event model.** Per-context FIFOs of DRIVE, EXPECT and SAMPLE entries with absolute
deadlines, strict ordering within half the counter range, and visible rejection. An
event is never applied late.

**D-10 Host link.** Eight `uio` pins for protocols (five more from v1.2, D-31); the host
uses SPI mode 0,
oversampled in the core clock domain (SCK ≤ f_clk / 8), so the design has one clock
domain. Two-entry mailboxes per context and direction. Memories are written only while
the core is stopped.

**D-11 Timing certificates.** A separate checker (`sw/escert`) analyses firmware as a
timed automaton and returns CHECKED, REFUTED with a witness, or NOT_PROVEN, with bounds
that the chip's own telemetry can contradict.

**D-13 Allocation.** 6×4 Tiny Tapeout tiles, 1289.28 × 710.64 µm.

**D-14 Open-drain level semantics.** On an open-drain pin the value bit is the bus level:
0 drives low, 1 releases. No encodable state drives an open-drain pin high, and
data-dependent SDA needs no inversion code.

**D-15 Telemetry.** Per context: minimum enqueue lead, maximum occupancy and the count
of waits on a full FIFO. These cross-check the certificates on RTL, gates and silicon.

**D-16 Protocol coverage.** UART (115,200 baud tested; the rate is one constant), SPI
modes 0 to 3, I2C controller (Fast mode) and target (Standard and Fast mode), plus protocols the
hardware was never designed for: WS2812, 1-Wire, CAN 2.0A, PS/2 and JTAG, all in
firmware.

**D-18 Reference host.** The RP2350 on the Tiny Tapeout demo board, running MicroPython.
External parts such as I2C pull-ups go on a Pmod.

## Version 1.1: the tape, the digest and GETI

**D-24 The tape.** A second 1P 256×32 macro shared by firmware (`LDT`/`STT`), a
timestamped capture unit and the host. One memory replaces three separate structures
(device storage, a capture buffer and a replay source) and holds 256 records, where a
flip-flop capture buffer of similar cost would hold 16. Rationale:
[13-TAPE-AND-DIGEST](13-TAPE-AND-DIGEST.md).

**D-25 Execution digest.** A 32-bit MISR over every commit, register write and output
change, folded only on edges where something happens, so it is final once the program
stops. One read checks silicon against the simulator. Cost: 48 flops.

**D-20 / D-26 GETI waits for in-flight SAMPLEs.** Previously, a `GETI` committing on the
edge where the last `SAMPLE` retired returned the word without that bit. GETI is now
wait-class while any SAMPLE is queued: a silent failure became impossible for one OR gate.

## Version 1.2: pin bank B

**D-31 The idle pins become a second pin bank.** The host link needs three dedicated
inputs and three outputs, so `ui_in[7:3]` and `uo_out[7:3]` did nothing: 10 of the
chip's 24 pins. In a design judged on flexibility, pins are the scarcest resource; with
eight, SPI plus UART plus I2C already filled the chip. Version 1.2 turns them into five
split pins, B0–B4, each an output on `uo_out[3+i]` and an input on `ui_in[3+i]`.

- *Per-context banks, not wider masks.* Every pin-carrying instruction format uses
  8-bit masks, so 13-bit masks would mean new encodings. Instead each context is
  configured to one bank and sees it as its eight logical pins. The mapping sits at the
  core's edge (input view, ownership masks, DRIVE and fault updates, outputs), so
  decode, admission and the event queues are unchanged, and a firmware module runs on
  either bank without edits: `combo_two_banks.asm` links four existing modules.
- *Released means high.* A split pin has no output enable at the package. Making a
  released output high, the level bank A floats to on its pull-up, keeps active-low
  selects and idle-high lines (SPI CS, UART TX) idle from reset and after a fault. The
  first version released low, and the two-bank test caught an SPI target selected at
  power-up.
- *Nothing old changes.* The digest folds bank B as `~ob`, which is 0 when released, so
  every existing program has its v1.1 digest (checked for all 16 on both simulators).
  WRITE_CFG gains four optional bytes that a v1.1 chip ignores. The capture unit
  chooses its bank with a CAPT bit that v1.1 ignored.

## Power and timing margin

**D-29 Measure power, then cut what dominates.** On the first v1.1 netlist, with
activity from gate-level simulation, a stopped chip used 93% of the power of a busy
one: flip-flop clock pins, the clock tree and two SRAMs reading every cycle. Logic was
under 7%. Two changes ([power](15-POWER.md)):

- **D-29a Clock-gated register banks.** 68 latch-and-AND gates built from allowed
  standard cells (the PDK's integrated clock gates are excluded from its LibreLane flow)
  over about 2,600 rarely written flops. Each enable is exactly the condition under
  which the bank was assigned before. A formal proof shows, for all time and with an
  unconstrained clock, that each gated bank equals an enable flop and that the gated
  clock cannot glitch.
- **D-29b SRAMs read only on demand.** The program store only for running contexts,
  the tape only for `LDT`, either one for the host only during a read command.

Result on workloads: −66% sending UART, −69% capturing, −72% stopped; 27% fewer cells.

**D-28 Hold uncertainty 0.05 ns, setup 0.25 ns.** The template applied its 0.25 ns clock
uncertainty to hold as well as setup. A hold check launches and captures on the same
edge, so jitter cancels; skew is propagated with CRPR and on-chip variation stays in the
5% derate. A sweep on the post-CTS database showed 3,554 hold endpoints at 0.25 ns and
82 at 0.05 ns; the 40 that fail at every setting are real SRAM-input paths and are still
repaired. Hold buffers fell from 4,514 to under 400, which freed area and routing.

## Physical implementation

**D-19 Magic DRC off.** Magic's CMOS5L technology file lacks the SRAM rule exceptions,
so its DRC inside the macro is always dirty and was most of each flow's run time.
Sign-off DRC is the KLayout SG13CMOS5L deck in the Tiny Tapeout precheck.

**D-23 Explicit OpenROAD thread count.** LibreLane 3.1.0.dev3 passes `-threads None` to
OpenROAD when `OPENROAD_THREADS` is unset, which runs detailed routing single-threaded
(librelane issue #1039). The configuration sets the thread count explicitly.

**D-30 Floorplan and routing.** Chosen run by run, with one global-route pass per
candidate (`scripts/congestion_map.tcl`) and residual violations mapped with
`scripts/drc_map.py` ([physical design](03-PHYSICAL-DESIGN.md)):

| Change | Total overflow | What it showed |
| --- | ---: | --- |
| Both macros side by side at the bottom, clock NDR | 34,119 | Global routing spent over 40 min relaxing the clock rule net by net |
| Tape moved to the bottom right, no clock NDR | 32,464 | Detailed routing stuck on long horizontal nets detouring through the tape |
| Tape at the top right, orientation N | 24,426 | One macro per half removes half of each band of the single horizontal layer |
| Placement density 56% | 21,635 | Remaining shorts on cells stranded below the program store |
| Both macros flush to the core edges | 19,093 | Program store clean |
| 50 µm horizontal and 30 µm vertical halos | 18,244 | No stranded strips; pin-side escape channels |
| Signal routing on Metal1, decap-free fill | 76 | Detailed routing converges; LVS, DRC and antenna clean |

Placement variance between otherwise identical runs is about ±5%, so the trend above
is real. Long wires are buffered at 500 µm, which cut slow-corner slew warnings from
314 to 124.

## Measured and rejected

- **Latch-based register banks.** A latch is 29.0 µm² in this library against 49.0 µm²
  for the flip-flop it would replace, so converting the ~2,600 gated-bank flip-flops
  saves at most about 52,000 µm², 6% of the die: not a tile at 6×4. The banks are
  already clock-gated, so the power gain is small, and latches move read-during-write
  timing by half a cycle in every bank.
- **Metal1 routing with the default fill.** Detailed routing was clean, but LVS merged
  about 4,000 nets: decap cells inserted after routing put Metal1 power plates under
  16,701 signal-wire crossings. Detailed-route DRC could not see it; only LVS did.
  Decap-free fill fixed it (D-30).
- **Answering operand and debug reads inside each context.** Congestion rose to 34,477;
  reverted.
- **Removing the clock gates to ease routing.** Congestion rose from 18,244 to 21,261:
  the enable multiplexers and hold buffers the gates remove are worth more than the
  extra clock wiring.
- **One clock gate per byte instead of per bank.** Clock wire fell from 118 mm to only
  93 mm while cells rose to 24,791; reverted.
- **Gating the remaining free-running flops** (time base, pipeline, host link,
  telemetry). They must run anyway, and each further gate saves less.
- **Global-route layer derates.** Loosening Metal2 (0.25) made the first detailed-route
  pass take 48% longer. Tightening it (0.45) pushed wiring onto Metal3, where overflow
  rose from 264 to 711 and detailed routing stopped converging. Every layer keeps the
  default derate.
- **Repair after global routing, with a larger hold margin.** Sign-off stayed clean, but
  532 more hold buffers bought only 9 ps of fast-corner hold slack, and slow-corner slew
  warnings rose from 124 to 166. The worst slew nets are detours taken by the detailed
  router (a 375 µm connection routed as 1.8 mm), which repair before detailed routing
  cannot see.
- **More detailed-routing optimisation iterations.** Values above 64 crash this OpenROAD
  build in `detailed_route`.
