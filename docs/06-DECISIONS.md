# Design decisions

The choices that shaped Escapement, with the reasoning and evidence for each. The
numbers are stable identifiers cited throughout the code; a number that is missing belonged
to a decision that was withdrawn or folded into a later one, and none is cited.

## Architecture

**D-1 Languages.** RTL in synthesisable Verilog/SystemVerilog, the language the Tiny
Tapeout flow (Yosys, LibreLane) and every open simulator and formal tool used here read.
The assembler, instruction-set simulator, certificate checker and tests in Python, because
the chip tests are cocotb, which is Python: the models, the firmware tools and the tests
then share one language, and the simulator also runs in a browser (Pyodide) on the site.
One encoding file, `isa/isa.yaml`, so the encodings are written once: the assembler and the
simulator read it, and the RTL's decoder constants, copied from it by hand, are checked
against the simulator's decode for all 256 opcodes and by lockstep co-simulation.

**D-2 Program memory: one 1P 256×32 SRAM macro.** A 256-word store in flip-flops would
take about 698,500 µm², 76% of the die; the macro takes 49,488 µm² (5.4%), about 14×
denser per bit. The macro passed LVS, the precheck and a gate-level test of all 256
words before the engine was built around it.

**D-3 Scheduler: a fixed 4-slot barrel.** Each context fetches every fourth cycle and
commits three cycles later, so there are no hazards, no forwarding and no interaction
between contexts. The cost is one instruction per context every 4 ticks, which is
enough: a serial bit loop is three instructions. (v1.7 lets a context take an idle
partner's slot as well, D-46.)

**D-4 No protocol hardware (versions 1.0 to 1.3).** No serialisers, CRC engines or
oscillators. Hardware for one protocol serves only that protocol, and the competition asks
first for protocols the chip was not designed for; with every protocol in firmware, a new
one is a new program, and the timing of all of them rests on the one mechanism of D-9. The
only acceleration is `EMITX` (drive a pin from the bit shifted out of
`sh`) and `EMITR` (drive pins from a register), which make data-dependent waveforms
branch-free. CRC-15 and bit stuffing for CAN run in firmware at 250 kbit/s. Version 1.4
adds one exception, and keeps it protocol-neutral: a configurable line unit for the bit
work that four instructions per bit cannot do (D-33).

**D-5 Clock: up to 50 MHz, 25 MHz by default.** Frequencies are quoted at the slow corner
(1.08 V, 125 °C), not the typical one. Since version 1.6 the layout is signed off at 20 ns,
so the chip is rated for clocks up to 50 MHz; it meets timing up to 50.8 MHz at the slow
corner and 80.6 MHz at the typical one (v1.5, rated 33 MHz: 38.6 and 61.3 MHz; v1.4, rated
25 MHz: 28.6 and 45 MHz) ([physical design](03-PHYSICAL-DESIGN.md#sign-off-results)). The
margin at the slow corner is small (1.5%), and the 25 MHz default keeps half the period
spare. The shipped firmware is timed for 25 MHz, the clock `info.yaml` asks the Tiny
Tapeout board for: VGA Pong uses the core clock as its pixel clock, the USB firmware runs at
24 MHz (D-37) and the 10BASE-T firmware at 40 MHz. The programs whose timing derives from
`CLK_KHZ` (14 in version 1.5, 39 since 1.6) rebuild for another clock: at 33, 33.333, 40 and
50 MHz each certifies (`test_escert.py`), and at 33 and 50 MHz 36 of them drive their bus
models, the models timing every edge in the rated tick (`test_rated_clock.py`). The other
three are the two combined images, whose modules run there on their own, and
`westminster`, a tune with no bus model. On the Tiny Tapeout demo board use exactly 50 MHz (its
firmware runs the RP2040 at 100 MHz and divides by 2) or 33 MHz (132 MHz by 4): both give 50%
duty and no jitter. A request for a clock that no even divisor of an RP2040 clock up to its
default 133 MHz makes, such as 33.333 MHz, falls back to a fractional divider and jitters, with
some cycles shorter than the request.

**D-9 Event model.** Per-context FIFOs of DRIVE, EXPECT and SAMPLE entries with absolute
deadlines, strict ordering within half the counter range, and visible rejection. An
event is never applied late. Each part answers one need. An absolute deadline means the
same thing however long the program took to compute it, so computing time never moves an
edge. A FIFO per context keeps the contexts' timing independent. Strict ordering means an
event needs comparing only with the current tick and the queue's tail, so admission is a
fixed, small check. Half the counter range keeps the comparison of two 32-bit ticks
unambiguous across the counter's wrap. And rejection at admission, with a fault that names
the rule, turns a deadline that cannot be met into a visible event before it can become a
late edge.

**D-10 Host link.** Eight `uio` pins for protocols (five more from v1.2, D-31); the host
uses SPI mode 0,
oversampled in the core clock domain (SCK ≤ f_clk / 8), so the design has one clock
domain: each SCK phase lasts at least four clocks, enough for the two-flop synchronisers,
and no clock-domain crossing needs its own verification. Two-entry mailboxes per context
and direction, so the host can write one word ahead of the program that reads it and the
two do not have to meet word by word. Memories are written only while the core is
stopped, because each is a single-port SRAM that the running core reads; the later
exceptions, the host reading the tape during a run (D-32) and live reload (D-48), each use
only cycles the core leaves free.

**D-11 Timing certificates.** A separate checker (`sw/escert`) analyses firmware as a
timed automaton and returns CHECKED, REFUTED with a witness, or NOT_PROVEN, with bounds
that the chip's own telemetry can contradict. The hardware guarantees that an edge is never
late, but a program that cannot keep its schedule is refused, at run time; the certificate
answers in advance whether any input can make that happen. It is software, so it costs no
silicon, and its bounds are numbers the chip measures, so each can be checked on the RTL,
the netlist and the bench.

**D-13 Allocation.** 6×4 Tiny Tapeout tiles, 1289.28 × 710.64 µm, the largest size the
competition allowed until 9 November 2026. Superseded when 8×4 was confirmed: version 2
moved to 8 × 4 tiles, 1724.16 × 710.64 µm (D-49 to D-51).

**D-14 Open-drain level semantics.** On an open-drain pin the value bit is the bus level:
0 drives low, 1 releases. No encodable state drives an open-drain pin high, and
data-dependent SDA needs no inversion code.

**D-15 Telemetry.** Per context: minimum enqueue lead, maximum occupancy and the count
of waits on a full FIFO. These cross-check the certificates on RTL, gates and silicon.

**D-16 Protocol coverage.** UART (115,200 baud tested; the rate is one constant), SPI
modes 0 to 3, I2C controller (Fast mode) and target (Standard and Fast mode), plus protocols the
hardware was never designed for: WS2812, 1-Wire, CAN 2.0A, PS/2 and JTAG, all in
firmware. UART, SPI and I2C are the competition's starting set; the others were chosen
because each tests the timing model differently: WS2812 by sub-microsecond pulse widths,
1-Wire by open-drain time slots the device answers in, CAN by bit stuffing, a CRC and
arbitration, PS/2 by making the clock itself as the keyboard, the reverse of the usual
host-clocked bus, JTAG by a long synchronous shift. By version 1.4 there are 31 programs, from ARM SWD and NEC infrared to HDMI-CEC,
DALI, SENT, DShot600 and a USB keyboard ([verification](04-VERIFICATION.md#protocol-coverage)).

**D-18 Reference host.** The RP2350 on the Tiny Tapeout demo board, running MicroPython.
External parts such as I2C pull-ups go on a Pmod. The demo board is the hardware every
chip of the shuttle arrives on, and its firmware is MicroPython, so the bring-up script
runs where the chip will be with nothing else to buy, and the same script drives the FPGA
bench through a small shim.

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
chip's 24 pins. For a protocol engine, pins are the scarcest resource; with eight, SPI
plus UART plus I2C already filled the chip. Version 1.2 turns them into five
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
- *Cost.* 1,886 cells (7.9%) and 0.07–0.09 mW (3%) per workload. Most of the cells are
  hold buffers: with the old 0.1 ns hold margin, routing left 33 fast-corner paths at
  up to −0.036 ns, so hold is now repaired with a 0.2 ns margin after clock-tree
  synthesis and again after global routing ([physical design](03-PHYSICAL-DESIGN.md)).
  Utilisation rises from 55.2% to 58.9%, still at 6×4.

## Version 1.3: streaming capture

**D-32 The host reads the tape during a run.** The capture unit was a snapshot logic
analyser: arm, run, STOP, read, at most 256 records, because the core owned the tape
port for the whole run. Yet the port is busy only in cycles with an LDT or STT commit or
a capture drain, and the host engine has at least 64 core cycles between SPI bytes. So
the change is arbitration, not a new memory.

- *Core first, by construction.* `tape_busy` now marks only the cycles the core uses
  the port; the host gets every other cycle. A host fetch is requested when a READ_TAPE
  address is set and must happen before the word is sent, one SPI byte later. No
  instruction's timing moves, and the core reads `tape_rdata` only in the cycle after
  its own LDT, which no host access can change. Co-simulation runs random programs
  while a host reads every few cycles and checks every word read against the
  simulator's tape at the cycle it was fetched.
- *A failed fetch is visible, not silent.* If four contexts keep the port busy for a
  whole SPI byte, the word is not fetched in time; host error is set and the host reads
  again. The alternative, reserving cycles for the host, would change core timing.
- *Stream mode never overwrites.* A new CAPT bit makes the region a ring shared with the
  host, which acknowledges what it has read with TAPE_ACK (the read pointer `rptr`). A
  drain that would write over an unacknowledged record is held instead. A host that
  falls behind therefore ends in the existing overflow flag, with `ovf_tick` saying
  when, never in quietly lost data: the rule the rest of the chip already follows.
- *Nothing old changes.* A program that does not set the stream bit behaves exactly as
  on v1.2, digest included. READ_TAPE while stopped returns the same words, now with
  one SRAM access per word instead of one per cycle (D-29). Protocol version 8.
- *What it gives.* A capture is limited by the host's link, not by the tape: the chip
  test streams a UART message through an 8-word ring while another context hammers the
  port with LDT/STT.
- *Cost.* 67 more cells than v1.2 in the final layout and 0.09–0.10 mW (3%) in every
  workload ([power](15-POWER.md)); global-route overflow 4.

## Version 1.4: the line unit

**D-33 A configurable line unit, not a USB engine.** USB low speed at 24 MHz leaves four
instructions per bit, too few for NRZI, stuffing and a CRC, so some datapath has to do
the bit work. A fixed USB serial interface engine would do one protocol; the line unit
is three configuration words of coding rules (pins, NRZI, stuffing rule, end-of-packet,
sync byte, bit period with a fraction, sample point, any CRC up to 16 bits), so the same
block sends and receives USB and CAN frames, runs CRC jobs on tape data, and monitors
its own output for CAN arbitration. Firmware keeps everything above the bit:
packets, toggles, addresses, requests. One unit per chip, owned by one context: four
would cost four times the area for protocols a host rarely runs at once.

**D-34 A reply is on time or not at all.** Every event still faults when late, except
`SEND`, which is dropped with Z = 0. A reply's deadline comes from the line (`GETL` sets
`tmr` to the end of the packet being answered), so a late reply means the line did
something early, not that the program is wrong, and the protocol's retry recovers. A
fault would turn one noisy packet into a dead device. It also separates two questions
for `escert`: that no input can fault the firmware (proved for any input), and when the
replies leave (proved exact, for a host that keeps to the protocol's timing).

**D-35 The first CONFIG owns the unit.** Ownership could have been a configuration field
sent by the host. Instead the first context whose `LINE` CONFIG commits owns the unit
for the run: no host protocol change, and the program alone decides, deterministically.
Only the line unit drives the pins it claims; a DRIVE on one faults.

**D-36 The unit borrows its owner's tape cycles.** It reads and writes the tape only in
the cycles in which its owner commits, so the other contexts' timing is unchanged and
only the owner's `LDT`/`STT` can wait, for at most two slots. A scheduled access happens
even when the job ends at that edge, which keeps the port arbitration a function of
the cycle alone.

**D-37 USB runs at 24 MHz.** Low-speed USB allows 25 ns of jitter per data transition. At
25 MHz a bit is 16⅔ ticks and the fractional period puts edges up to one tick (40 ns)
early, so the USB firmware and tests use a 24 MHz clock, 16 ticks per bit, and the chip
keeps its 25 MHz default.

**D-38 The configuration words are not debug registers.** Debug selects 22 to 24 first
showed W0 to W2. To keep them exact, a CONFIG would have had to hold its new words aside
until it completes, and a failed one to keep the old ones: a 32-bit holding register and
three more 32-bit inputs on the debug multiplexer, in a layout where routing is already
the limit. The words are a tape block the firmware wrote, so the host can read them
there; selects 22 to 24 read 0. In the RTL a new W0 waits in the unit's stream buffer and
W1 loads as it arrives, which nothing can observe.

**D-39 One counter and one clock gate.** The unit's timing state is one 12-bit tick
counter, reused in every mode: bit timer while sending and receiving, idle counter while
armed, bit index of a CRC job, step of a CONFIG, end-of-packet wait. A bit lasts P_int or
P_int + 1 ticks, so the fractional rate is one 8-bit accumulator. The configuration,
command and end tick are clock-gated banks like the core's, and all other state shares a
gate that is closed whenever the unit is idle and nothing reaches it. A program that
never uses the unit pays for it in area only: about 2,500 cells, 11% of the design before
place and route (Yosys, same script for both).

**D-40 Registers in latches.** With the line unit the design was 11% larger than v1.3,
and the first layout at 6×4 failed with routing shorts over the tape. In this library a
flip-flop is 49.0 µm² and a latch with reset 27.2 µm², and most flip-flops sit in
clock-gated banks that are written rarely. Those banks are now latches: r0–r3, SH, the
timer, lee, the FIFO entries and the mailboxes of every context, and the line unit's W0,
2,365 bits in all. A latch is transparent while its gated clock is high, so its input
must hold still for that half cycle. Each write port therefore has one flip-flop register,
shared by the four contexts, that holds what was written at the last edge (158 bits in
all), and the latches copy it. A read sees the new value right after the edge, as with a
flip-flop, so neither the semantics nor the timing of any read changes. The design
shrinks from 379,900 to 331,300 µm² before place and route (Yosys, same script), below
v1.3, and no feature is removed. The mailboxes became two-slot rings, so each slot is
written from one source; the head of an empty mailbox, which nothing reads, changed with
them. `formal/esc_lreg.sby` proves that a latch bank equals an enable flip-flop at every
instant whenever its input is the written value registered at the write edge, and the
core proof shows that every bank's input is. The test suites also run on the RTL with
the real latch cells (`CELLS=yes`). Static timing sees each latch's data arrive just after it
opens and reports it as time borrowed, with exactly zero slack at all 2,365 inputs. The
resizer treated those as violations and never finished, so placement and routing time each
latch from its gate, like the flip-flop it replaces (`src/escapement_pnr.sdc`), while
sign-off timing keeps the full latch model.

**D-41 Debug reads share the commit stage's multiplexers.** Selects 0 to 8 read a
context's registers (r0–r3, SH, LC, the timer, PC, LR, state and flags, ish). The commit
stage already selects exactly those registers of one context in every cycle, so a second
set of 4-to-1 multiplexers, 260 bits wide, only duplicated it, with its own long wires
from every context to the middle of the core. Now, while the chip is stopped, the commit
stage selects the context the host is reading. During a run a 32-bit register takes the
selected field in that context's commit slot. A read during a run therefore shows the
registers as they were up to four cycles earlier, finer than any host can time a read,
and a read while stopped is exact. The design shrinks by about 8,500 µm² and a
thousand long wires (Yosys, same script).

**D-42 A mutation campaign scores nothing until every suite passes unmutated.** The first
v1.4 campaign on the standard-cell models reported 710 of 711 mutants killed. It was
false: the CI runner's Icarus Verilog 12 gives X on the PDK's cell models, so every
co-simulation failed within seconds and each failure read as a kill. `run_mutants.py` now
runs every suite a shard needs on the unmutated RTL first and stops if one fails, and CI
builds Icarus 13. A kill that took seconds where a pass takes minutes was the clue; the
baseline makes the check mechanical.

**D-43 Tests decided a cycle early (v1.5).** v1.4's limiting path at the slow corner ran
from a context's tick counter through the FIFO head's deadline comparison and the retire
and fault logic to a pin's output register, and the committing instruction's admission
tests (its deadline ahead of now, of its FIFO tail and of its open window's end; its timer
reached) reached the same fault logic as late, so moving either alone gained nothing
([physical design](03-PHYSICAL-DESIGN.md#sign-off-results)). Every operand of those 32-bit
tests is known a cycle before it is used. A context's timer, tail and window end change
only at its own commit, four edges back, and the tick then is now + 1. So each FIFO
decides its next head's retire and late flags, and the core decides the admission and
timer tests of the context that commits next, in the cycle before, into registers. No program can tell the difference; the subtractions leave the path from the
counter to the pins. The proofs state each register as an equality with its definition in
every cycle that uses it (D1, D2, B1–B5, [verification](04-VERIFICATION.md)), so every
other property reads as before. It costs 28 flip-flops and 2% power. The layout, signed
off at 30 ns, meets timing at the slow corner up to 38.6 MHz (v1.4: 28.6 MHz; a v1.4 layout
aimed at 32 ns reached about 30.5 MHz), and the limit is now the decode of the instruction
itself. The rating rises from 25 to 33 MHz (D-5).

**D-44 Decode, operands and pin writes a stage earlier (v1.6).** After D-43 the slow-corner
limit was the decode of the committing instruction (`insn` to a pin's output register, 51
cells), then the pin-output logic and the line unit. Version 1.6 moves each of them into
the cycle before, where its inputs already settle: the instruction is decoded in the X
stage into a one-hot register (`d_op`, legality with it), the operands are read from the
register banks a cycle ahead (`opa`, `opb`), each FIFO registers the kind and payload of the
head it will have next, and the line unit decides a transmit boundary's pin writes, and the
bit that starts it, a cycle ahead into flip-flops. As in D-43 each register is stated as an
equality with its definition in every cycle that uses it (B6–B9, the FIFO's D1–D3, the line
unit's L2, proved by k-induction on the unit with the core proving its input rules,
[verification](04-VERIFICATION.md)), so no other property changes and no program can tell. It costs 354 flip-flops and 13–16% power at 25 MHz
([power](15-POWER.md)). The layout is signed off at 20 ns and meets timing at the slow corner
up to 50.8 MHz; the rating rises from 33 to 50 MHz (D-5). Getting a 20 ns layout took nine
trial hardens: the post-CTS hold repair, judging setup slack from placement estimates, put
delay cells at the roots of paths that became critical after routing until the resizer's
setup margins were raised to 2.5 ns (placement) and 3 ns (global routing).

**D-45 The netlist proof in two steps, not a recoding switch (v1.6).** Synthesis recodes the
line unit's state registers `sk`, `noted` and `es0_q` one-hot; with v1.6's registers the
one-step equivalence proof no longer closes (it ends undecided, with no difference found).
An `fsm_encoding "none"` attribute would keep the netlist one-to-one with the RTL, but it
changes the netlist, and its layout (`v16t`) stopped at the disconnected-pin check: clock-tree
synthesis had dropped the clock input of one clock gate. That is OpenROAD issue #9816, fixed
upstream in March 2026 but not in the OpenROAD that LibreLane 3.1.0.dev3 pins: when two clock
gates' sinks have the same centre, CTS moves both gates there and keeps one. The signed-off
layout (`v16s`) does not have the collision, so the proof takes two steps instead:
`esc_line` against `esc_line` after synthesis's FSM pass, then the netlist against the RTL
with that module ([verification](04-VERIFICATION.md#rtl-to-netlist-equivalence)). Every
committed layout must also pass LibreLane's disconnected-pin check, which is what caught
the bug. The defect came back twice, at 60% density in version 3 (D-61) and at 61% in
version 3.1 (D-65), and each time the check stopped the flow and a nearby density placed
without the collision.

**D-46 Pair mode, not a faster barrel (v1.7).** A context commits one instruction every 4
ticks; single-cycle programmable I/O commits every cycle. Issuing every cycle does not fit
50 MHz here: the next instruction's X stage would be the commit cycle of the one before, so
every value v1.6 moved into X (D-44) would need forwarding from the commit result, putting
the commit logic, the slow-corner limit, in front of X's own subtracts and compares. Two
slots apart there is no such hazard: an instruction's X stage comes a cycle after the one
before has committed. So a context may take the slot of its partner (k ^ 2) when that one
is off ([semantics](11-SEMANTICS.md) §3), and commits every 2 ticks. Its next fetch is made
while the instruction before is in X, where branches resolve exactly from final flags; the
fetched PC travels with the instruction and must equal the context's PC at commit, so a
wrong guess (a wait, a fault) costs a slot and never a wrong commit. After an LDT, whose
load lands an edge after its commit, no fetch is made. Pairing is a static configuration
bit (`ctx_pair`, protocol version 9), not a run-time choice, so a context's certificate
never depends on another context's progress, and with it clear the machine is v1.6, digest
included. The WAITT guess (complete if the timer is reached) was dropped: its 32-bit compare
on `now` sat on the program store's address path. Two v1.6 line-unit paths that a trial
layout placed badly were registered: the owner's pin bank (`lu_bank`, one cycle behind,
never seen: the unit loads a CONFIG for 9 cycles after taking an owner, proved) and the
configuration's pin fields (W0[11:0] in flip-flops instead of the latch bank, D-40). On the
ISS against the bus models, the minimum clock falls by 1.92 to 2.22 times for SWD, JTAG,
WS2812, DShot, I2C fast mode, SPI controller and CAN transmit; timer-bound firmware is
unchanged. A trial layout met 20 ns at the slow corner down to 19.2 ns (v1.6: 19.7 ns),
with the power estimate 1.4% higher.

**D-48 Live reload of one context, restarted at its own slot (v1.7).** A host can stop one
context, rewrite its program words and restart it while the others run (CTX_STOP, WRITE_IMEM
during a run, CTX_START; [host protocol](12-HOST-PROTOCOL.md)). Three choices make the others
unable to notice. Program words are written only in a cycle whose fetch slot belongs to a
context that is not RUNNING (the port is idle there, D-29b), so no fetch waits. A stopped
context releases its pins as on a fault but records no fault. The restart happens at the edge
before the context's own first slot of a frame, not at the frame edge the first draft used:
the latch banks (D-40) copy their reset values from shared write registers that another
context's commit can load at a frame edge, while at the restarting context's own commit slot
nothing commits. The restarted context then sees what a run started at that frame shows it,
so its timing certificate holds unchanged. `formal/esc_ni.sby` checks non-interference: two
cores with the same inputs on every channel context j can see, one free to differ in every
command aimed at the other contexts and in the words only they fetch, give j the same
commits, state and pins (j's program uses neither the capture unit nor the line unit, one
shared instance each that another context may own or configure; the digest and the
first-fault register are global and not compared). The host software refuses a reload that changes the configuration,
touches a word another enabled context can reach, or is not certified by escert.

## Power and timing margin

**D-29 Measure power, then cut what dominates.** On the first v1.1 netlist, with
activity from gate-level simulation, a stopped chip used 93% of the power of a busy
one: flip-flop clock pins, the clock tree and two SRAMs reading every cycle. Logic was
under 7%. Two changes ([power](15-POWER.md)):

- **D-29a Clock-gated register banks.** 68 latch-and-AND gates in v1.1 (80 in v1.4)
  built from allowed standard cells (the PDK's integrated clock gates are excluded from
  its LibreLane flow) over about 2,600 rarely written flops (in v1.4 most of them latches,
  D-40). Each enable is exactly the condition under
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

## Version 2: 8×4

**D-49 Scope of the release.** Pair mode (D-46), live reload with its non-interference
proof (D-48), program pages (four pages in one 1,024-word store) and proof tripwires,
frozen on 2026-11-12 and verified twice from that commit. The larger die paid for the
pages and the tripwires. The pages give each context a full 256-word program of its own,
assembled on its own at address 0, where the four had shared one 256-word store and had to
be linked together. The tripwires build twelve of the proved properties into the silicon
as sticky flags, so a defect or a wrong assumption about the board shows on a real chip as
a named, broken property rather than as an unexplained failure. The edge-anchored wait and the
symmetric receiver come after this release is done end to end: a feature waits, a check
never does.

**D-50 The program store is one 1024×32 macro.** The store's contract is fixed (1,024 words,
read in cycle N, data from N+1, held) and `escapement_pstore` can build it from four
256×32, two 512×32 or one 1024×32 macro (`STORE_MACROS` in `project.v`). At 8×4 only the
single 1024×32 routes and meets timing in the floorplan of D-51: with five macros (four
pages and the tape) the halos leave no room to route (global-route overflow 1,063), and
two 512×32 in the same shape congested (overflow 2,125 to 6,165). The single macro has a
slower clock-to-data (7.32 ns slow corner, 5.19 ns plus the page multiplexer for four
256×32) and a higher energy per read (36.1 pJ against 22.0 pJ at the slow corner; 48.3
against 26.0 pJ at the typical one), and is the smallest
(140,183 µm² against 197,954 µm²). Its slower read costs nothing at 50 MHz: in the
adopted harden every flip-flop path, the fetch included, has at least +1.51 ns at the slow
corner (D-53).

**D-51 The 8×4 floorplan: the tape at the bottom left.** A measured failure analysis of
nine 8×4 hardens (worst path into each register family, split into wire, buffer and
logic delay; register positions from the layout) found one cause behind every timing
failure. The logic on the failing paths was the same depth as on the 6×4 layout that
passed (8.5 to 10.6 ns), but the buffering was 7.8 to 12.6 ns against 5.3 to 6.7 ns: on the
wider die the placer always put the line unit at the free east end, 400 to 600 µm from the
core and over a millimetre from the pins, and its same-cycle paths to the core crossed that
gap two or three times. Placement-only screens (each a few minutes) tried the remedies:

| Change | Line unit (register centroid x, µm) | Global-route overflow |
| --- | ---: | ---: |
| Previous 8×4 floorplans | 1,198 to 1,540 | 0 to 597 |
| The tape at the top left (pins down) | 1,382 | (placement only) |
| Timing-driven placement alone | 1,427 to 1,516 | 0 to 580 |
| **The tape at the bottom left, pins up (FS)**, store build 1 at the bottom right | **401** | **8** |

Only the tape at the bottom left, its pins facing the logic, anchored the line unit: it
sits beside the tape and the core packs into about 700 µm. The store is at x 1293.36 and
the layout art at 464.88, between two pairs of Metal4 power straps; every macro is flush
to the core edge and on the power-stripe phase (`src/config.json`).

**D-52 Timing-driven global placement.** With D-51's floorplan the remaining failure was the
core's commit logic spread over about 700 µm (a path from the host's `start` went back and
forth across it five times: −0.89 ns slow corner). That left 235 endpoints for the repair after global routing,
whose added cells congested its re-route (overflow 8 → 967) and broke hold through detoured
clock wiring. Timing-driven placement weights the critical nets during placement: setup
+2.16 ns, overflow 0 at every step. On the fuller 6×4 layout it had congested detailed
routing; 8×4 has the room.

**D-53 Hold margins 0.1 ns after clock-tree synthesis, 0.3 ns after global routing.** At 0.2
after clock-tree synthesis the same placement took 2,233 hold buffers instead of 289. After
global routing, 0.2 left six paths (the tripwires' clock gate and the FIFO level and mask
registers) that lost up to 0.3 ns in detailed routing and ended at −0.098 ns at the fast
corner; each has at least 8.7 ns of setup slack, so 0.3 costs nothing: +0.200 ns, no
violations at any corner. The setup margins stay 2.5 and 3 ns (v1.6): they also stop hold
repair from slowing paths that become critical after routing.

**D-54 The line unit decides a CONFIG's faults a cycle early.** The faults at the end of a
CONFIG (the pins not owned, an invalid configuration) depend only on values final at least
a cycle before (the first configuration word from the CONFIG's first cycle, the owner's pins,
static during a run, and words written at cycles 4 and 12), so they are registered the cycle
before they are used, and the owner is also kept one-hot. Behaviour is the same cycle for
cycle: lemmas prove the registered terms equal their definitions at every completion, and
that the one-hot owner equals the owner's decode; two planted faults (the one-hot owner stuck,
the terms sampled too early) each fail them.

## Version 3: receive and the bus timing monitor

**D-55 Scope.** Two silicon additions, both chosen for what they let the chip do rather
than for speed: the line unit receives as well as it sends (three strobe sources, D-56, and
the half-tick sampler, D-57), and a bus timing monitor checks other devices' timing
(D-58). The edge-anchored wait considered for version 2 stays out: its kill check found no
program in the library that gains 1.5× in rate or margin from it. Each addition sits
behind the same campaign as everything else, and the version 2 design remained the
verified fallback until version 3 passed it.

**D-56 One receiver, three strobes.** Receiving Manchester and clocked buses could have
meant two new receivers. Instead the existing receiver gains a choice of where its bit
strobe comes from, and everything after the strobe (decoding, destuffing, the sync hunt,
the CRC, the tape writes) stays shared. The **cell decoder** reuses the bit timer as a mask
timer: after each accepted mid-bit edge it ignores edges for about three quarters of a bit,
so the next accepted edge is the next mid-bit edge. Every bit re-anchors it, so no
phase-locked loop is needed. The **external clock** takes the strobe from an edge of the
second pin, whose synchroniser matches the data pin's, so their skew is kept to the tick.
Two further changes came with it: **long receive** (limits in units of 32 bytes, up to
1,024 bytes, a 14-bit count), because a 10BASE-T frame is at least 64 bytes and the old
limit was 32; and the **hand-over** of each completed word, because with the faster
strobes a bit can arrive before the owner's tape-write slot, and in a first version it
overwrote the finished word's bit 0. The additions cost about 4,300 µm² in synthesis
against an estimate of 4,000 to 8,000.

**D-57 A half-tick sampler for the line unit only.** At 50 MHz a 10BASE-T half-bit is 2.5
ticks, and 802.3 allows each edge about ±13.5 ns of jitter. With one sample per 20 ns tick,
a bit-boundary edge can be seen 83.5 ns into the bit, past a three-quarter mask point at
75 ns. Sampling also on the falling clock edge places edges to 10 ns: the latest boundary
edge at 73.5 ns, the earliest mid-bit edge at 76.5 ns, 1.5 ns either side of the mask.
A faster clock was the alternative, and it does not work: at 60 MHz, one sample per tick
still sees a boundary edge at 80.2 ns. The sampler is one bank of falling-edge flip-flops
on the 13 protocol pins, re-timed by two rising-edge stages so that it adds one half-cycle
path of 10 ns and no other. Only the cell decoder reads it, so every other input feature,
and every existing program, sees exactly what it saw before. The test benches change
protocol pins a quarter period after the falling edge, so a timing simulation never races
the new flip-flops, and the co-simulation case for the 10BASE-T receiver is written to fail
without the half sample.

**D-58 The bus timing monitor: one rule shape, four rules, 16 bits.** EXPECT checks the
answers to the chip's own edges; nothing checked traffic between two other devices. One
rule shape, "the next edge of B comes MIN to MAX ticks after an edge of A", expresses setup,
hold, pulse width, period and timeout, so the unit needs no notion of any protocol. Four
rules cover the figures that matter on most buses (I2C needs about three, SPI two), and
16-bit intervals reach 1.31 ms at 50 MHz, which covers I2C, SPI, CAN and UARTs down to
about 1,200 baud; 12 bits would have stopped at 82 µs. The first build kept each rule's
settings in plain flip-flops with write multiplexers (31.2k µm²); moving them into latch
banks written through one shared word, as the chip's other rarely written registers are
(D-40), brought it to 26.0k µm² with the same behaviour, and a proof that the latch banks
hold what was written.

**D-59 The monitor has an owner.** The first design made the monitor a shared unit that
any context could configure and read. The non-interference induction then failed: a
context's GETM read state that every other context's MON had shaped. Three answers were
weighed. Excluding the monitor from the non-interference claim, as a shared channel like
the capture unit, would have weakened the claim. Gating only GETM by an owner would not
have isolated anything, since any context's MON could still rewrite the rules. Giving the
monitor an owner, by the line unit's rule (the first context whose MON commits in a run,
until the next start), isolates it fully for four flip-flops: another context's MON faults
ADMIT/OWNER and its GETM reads 0, so a program loaded live beside the owner can neither
rewrite its rules nor read what they caught. The non-interference proof keeps every GETM
in the observed context's words and requires it to read 0.

**D-60 Whole counts through GETM, not a trailer word.** A long receive counts to 8,192 bits
and a CRC-32 has 32 bits, but the line unit's status word has room for 9 and 16. GETM 3 and
4 return the whole count and the whole CRC register to the line unit's owner, with no new
state. The alternative, a trailer word written to the tape after the packet, would have
needed a second write and a reserved word in every buffer.

**D-61 The compact floorplan: both memories in the east column.** The first version 3
hardens did not route at any density tried (global-route overflow 932 to 4,592). Ablation
runs, each to global routing only, removed one addition at a time: without the monitor
the overflow fell to 0. The monitor and the line unit sat at the left with the tape
(D-51), about 900 µm from the core's execute stage, and the words between them (rules
written, counts and records read) crossed the die on Metal3, the only horizontal signal
layer. Narrowing those interfaces to one 32-bit read word, with the core keeping the
recorded ticks, cut the overflow by more than half and was proved sequentially equivalent, but did not
make the design routable. The floorplan did: with the tape moved inboard the logic packed
into one block, routed with no overflow and used about 20% less wire. The adopted one puts the program store at the bottom right and the tape above it at
the top right, pins facing the logic, so the logic is one rectangle with no macro in its
horizontal paths. The decorative layout-art macro could not stay: placed at the left, its
halo stranded rows that the power grid fed with a stub stripe, which the shuttle's
precheck rejects; placed beside the store, it cost 1.3 ns of setup and broke hold at the
fast corner. It was removed. Two flow settings complete the floorplan. Clock-gate sinks
are clustered within 50 µm, because the default 833 µm let clock-tree synthesis move gates
far from their banks. The placement density is 61% rather than 60%, because at 60% one
clock gate lost its clock input in clock-tree synthesis, the OpenROAD defect of D-45
(issue #9816, fixed upstream but not in the flow's release), which the disconnected-pins
check, LVS and the netlist proofs all catch. The adopted harden meets every criterion and
the shuttle's precheck: setup +1.480 ns at the slow corner, hold +0.180 ns
at the fast corner, no DRC, LVS or antenna violation.

**D-62 The rating stays at 50 MHz.** The shuttle allows 60 MHz. In version 3.0 the slow
corner would have needed about 3 ns more, against under 2 ns of slack at 20 ns, so the gain
would have meant pipeline changes and a late closure fight. Version 3.1's layout (D-65)
passes setup at the slow corner down to 16.6 ns, so 60 MHz is now possible, and a later
version may be rated for it. Not this one: at 16.7 ns less than 0.2 ns is in hand, and the
Tiny Tapeout multiplexer and pads add delays that the flow does not model. What
a faster clock would buy is small: both-edge sampling already doubles the input resolution
where it matters (D-57), and every protocol in the library already runs at 50 MHz or below.

## Version 3.1: span, the electrical rules and the reset

**D-63 A reset synchroniser (3.1).** Until version 3.1, `rst_n` went from the pin straight to
the reset input of every register and latch bank. The board releases it at no particular
point in the clock cycle, so the release reached flip-flops on either side of a clock edge
and was not truly timed: the constraints treated it as any other input, launched 4 ns (20%
of the period) after a rising edge. That assumption only just held. The latch banks close on
the falling edge, and span's layout routed the reset to context 3 through 6.3 ns of buffers,
so a clock high for 40% of the period missed by 0.77 ns at the slow corner. H4 had passed by
0.82 ns. Two flip-flops in `project.v` now take the reset: they clear as soon as `rst_n`
falls, without a clock, and release it on the second rising edge after `rst_n` rises. The
whole chip then leaves reset from one flip-flop on a known edge, and the flow times the
spread across the reset tree as an ordinary path. The cost is two flip-flops and two cycles
before the chip answers after reset. Every test already waits five or more.

**D-64 Span: a 512-word program on a context whose partner is off (3.1).** A page of 256
words is enough for most programs, but a Modbus RTU device with all of its common functions
needs 419 words. Pair mode (D-46) already lets a context whose partner, context
k ^ 2, is not enabled take the partner's issue slots. Span lets it take the partner's page as
well. The context's program counter and link register grow to 9 bits, and bit 8 selects the
partner's page, so a spanning program has 512 words and runs straight on from word 255 to
word 256. Three choices keep the change contained. Span is opt-in per context (configuration
byte 15), so a program that does not ask for it behaves exactly as on version 3.0, its
execution digest included, and running past word 255 still faults. It is independent of pair
mode, because both need only that the partner is off: a context can take the slots, the page,
or both, and a slow protocol that needs space need not double its issue rate and its power
to get it. And it needs no new page field, since the host already sets a page for every
context, enabled or not, and a disabled partner's page is otherwise idle. The silicon cost is
one more bit in each context's program counter and link register and a multiplexer on the
fetch page. Span came out of an audit of what the chip's structure limits, which weighed two
other changes. A larger tape would let Ethernet carry full-size frames, but the tape's 8-bit
addressing runs through the instruction set, so it would have meant a new instruction
layout, a macro swap and a new floorplan closure for one protocol's frame size. A faster
rated clock had no program that needed it (D-62). `modbus_full` is the first spanning
program. On the FPGA bench its smallest lead was 9 ticks, the bound its
certificate gives.

**D-65 The electrical rules met at every corner (3.1).** Version 3.0 was signed off with
electrical-rule violations left unrepaired: 15 pins over the maximum slew at the slow corner
(the worst 3.62 ns against the library's 2.51 ns), one pin over its maximum capacitance, and
155 clock-tree buffers over the fanout limit. The timing figures were computed with those
slews, but a pin beyond its limit is outside the range over which the library was
characterised, so the margins rested on an extrapolation. A series of trial hardens, each
measured against the last, traced the violations to five causes, and each fix below answers
one of them.

- **The resizer underestimated every wire.** It took the wire capacitance from the
  technology LEF, 0.092 fF/µm; extraction of the 3.0 layout gave a median of 0.185 fF/µm over
  the 7,243 nets of 100 µm or more. The flow now uses twice the LEF value at every corner, and
  every step that sets wire parasitics loads all three corners instead of the typical one.
- **Detailed routing lengthens nets after the last repair.** Nothing repairs after detailed
  routing, and the nets that failed in 3.0 had been planned at 250 to 280 µm and routed to
  0.7 to 1.3 mm. The repair after global routing now keeps 40% margins on slew and
  capacitance instead of 30%, buffers wires longer than 250 µm instead of 300 µm, and global
  routing derates each layer's capacity by 20% instead of 30%. Metal3 had run at 67 to 70%
  of its derated capacity, and the re-routes after each repair had detoured around its busy
  edges.
- **Repair judged slew at the fast corner only.** OpenROAD's design repair checks slew at the
  first corner the flow defines, which LibreLane makes the fast one. Measured on every pin of
  three finished layouts, slow-corner slew is 2.24 to 2.46 times fast-corner slew. With a
  40% margin, repair acted only above 1.50 ns at the fast corner, about 3.5 ns at the slow
  one, already past the 2.51 ns limit, so nothing protected the slow corner, and whether a
  layout passed depended on whether a weak gate happened to get a long route. The placement
  and routing constraints (`escapement_pnr.sdc`, not the sign-off ones) now set a maximum
  transition of 1.0 ns. Repair then acts above 0.6 ns at the fast corner, about 1.4 ns at the
  slow one, which leaves room for detailed routing to lengthen a net.
- **The memories' input pins are stricter than the cells'.** They allow 0.595 ns at the slow
  corner, and fast-corner repair could not see the misses. Every switching input of both
  memories now has its own buffer, driving a net that repair may not touch. A 2× buffer
  still missed by 3 ps on the longest of those nets, which carry 80 to 137 fF, so the buffers
  are 4×. Simulation, the proofs and the equivalence check see a wire.
- **The clock tree drove its leaves with 8× buffers.** Clock-tree synthesis sized each leaf
  buffer for a load it never had. Letting it size them to their real load (the sink buffer
  capacitance derate at 100%) gave 1× leaves, cut the clock tree's power by 6.6%, and took the
  fanout flags from 176 to 17.

The 17 flags left are the roots of gated clocks, each driving 16 loads against the
library's limit of 8 while passing slew and capacitance with room. They are accepted: the
fanout limit stands in for those two checks, and splitting the roots (sink clustering at 8)
raised the clock tree's power by 32% and left new slew failures elsewhere. The placement
density moved from 61% to 61.7%. With the reset synchroniser in place, 61% hit OpenROAD's
lost clock-gate input again (D-45), 61.5% left five slew pins before the fixes for the fast
corner and the memory inputs, and 61.3% missed setup at the slow corner by 0.42 ns. The
adopted layout has no slew or capacitance violation at any corner, meets setup down to
16.6 ns and hold by 0.098 ns at the fast corner, passes the 40% and 60% duty-cycle checks at
20 ns, and is clean for DRC, LVS and antennas, with 38,514 cells, 3.4% more than version 3.0,
and a flow power estimate of 7.36 mW against 7.66 mW.

**D-68 The netlist proof cuts the reset at the synchroniser (3.1).** With the synchroniser
of D-63, every register's asynchronous reset comes from a flip-flop's output instead of a
pin, and the equivalence proof stopped closing: signal correspondence matched almost none of
the registers, and the proof of the 3.1 netlist ended undecided after 2 h 21 min with no
difference found. Two ways out were weighed. Holding `rst_n` high after the miter's first
reset would let the proof close but would leave out a reset during a run, which the chip
must survive. Cutting the net keeps it: in both designs the synchroniser's second
flip-flop drives a new output, compared like any chip output, and `rst_n_s`, which resets
everything else, becomes a shared free input, held low during the miter's reset. Nothing
feeds back from that net into the synchroniser, and the free input can do anything the real
net can, so the cut proves at least as much as the whole. The cut is made at the flip-flop,
on copies of `project.v` and of the netlist. The first version used yosys's `expose -cut` on
the `rst_n_s` wire, and it proved the netlist in 273 s, but a planted one-flop synchroniser
passed too: `rst_n_s` is an alias of the flip-flop's output in the RTL and reaches the loads
through buffers in the netlist, and the cut followed the aliases, so the "compared" output
was the free input itself on both sides. With the cut at the flip-flop the 3.1 netlist
proves in 294 s, the one-flop synchroniser fails at step 5, and a MISO shift-register bit
fed from its neighbour fails at step 17. The same episode changed the workflow: run by
hand, it now takes only a harden whose commit has the same `src/` as the one it checks,
after a run dispatched before the head's harden had finished proved the previous commit's
netlist and was taken for the proof of the new one.

## The DOOM console

**D-66 The game on the host, the chip as its input/output processor.** DOOM needs far more
memory than the chip has, so it runs on a host and the chip makes its signals
([25-DOOM-CONSOLE](25-DOOM-CONSOLE.md)). Two hosts were possible: a laptop running a
portable DOOM and sending frames through the Pico, or the Pico W itself running rp2040-doom.
The Pico was chosen, because then the bench is self-contained and the chip serves the same
small host it serves in bring-up; its 264 KB of RAM was the risk, and on the bench the game's
memory zone never had less than 23,944 bytes free. The keyboard and mouse come from a window
on the laptop over USB serial to the Pico. Reading them through the chip would have needed a
keyboard and mouse that speak to its pins and a context free to serve them, and all four
contexts already make the picture and the sound or watch them.

**D-67 Frames stream through the mailbox.** `i2c_burst` sent frames from the tape, but the
host may write the tape only while the chip is stopped, and the game draws while the chip
runs. A stream command makes the controller take the frame's words from its mailbox as they
arrive. A word that is late holds SCL low, which only lengthens a low phase (I2C sets no
maximum), so a slow host costs time and never an edge. The host feeds every context the same
way: one STATUS read, then at most one word to each context with room, because a mailbox
holds two words, STATUS shows only whether it is full, and a push into a full mailbox is
lost. A lost word would raise the host error flag; in the bench run, 523,797 words went over
the link and none was lost.

## Measured and rejected

- **The tape at the bottom left in version 3.** It anchored the line unit in version 2
  (D-51), but with the full monitor the logic spread across the die and Metal3 overflowed
  (932 to 4,592) at every density from 52% to 62%. D-61.
- **Lower density to spread the version 3 logic.** It made the overflow worse (52%: 4,592;
  60%: 3,578): spreading lengthened the horizontal nets that were the problem. D-61.
- **The layout art beside the program store (version 3).** The precheck passed, but setup
  fell to +0.60 ns and fast-corner hold to −0.26 ns. D-61.
- **A 60 MHz clock.** D-62.
- **More placement draws without a fix (3.1).** Before the fixes for the fast-corner repair
  and the memory inputs, some trial layouts had no slow-corner slew failure and others had up
  to nine, each time on a different net and always a single-size complex gate on a long
  wire. A new density or seed changes which net fails, not whether one can. D-65.
- **Clock sinks clustered eight to a buffer (3.1).** It removed the fanout flags but raised
  the clock tree's power by 32% and left two slew failures elsewhere. D-65.
- **Fixed clock-buffer spacing (3.1).** At 100 to 200 µm it also cleared the fanout flags, but
  every run then left a slow-corner slew net elsewhere, and with a tighter global-route slew
  margin and 200 µm wires it added 1,857 cells for the same result. D-65.
- **Five macros at 8×4 (four 256×32 pages and the tape).** Either the line unit lands at the
  east end (setup −2.2 ns) or, with the pages as a block on the right, global routing
  overflows (1,063). D-50.
- **Two 512×32 for the store.** −4.67 ns in the previous floorplan; overflow 2,125 to 6,165 in
  D-51's.
- **The tape at the top left.** The line unit stayed at the east end (D-51).
- **A lower setup margin after global routing.** The hold violations it was suspected of
  blocking had over 3 ns of setup slack; the margin was not the cause and stays (D-53).
- **Latch-based register banks (v1.1; adopted in v1.4, D-40).** A latch is 29.0 µm² in
  this library against 49.0 µm² for the flip-flop it would replace, so converting the
  ~2,600 gated-bank flip-flops saves at most about 52,000 µm², 6% of the die: not a tile
  at 6×4. The banks are already clock-gated, so the power gain is small, and latches
  fed straight from the write data move read-during-write timing by half a cycle in
  every bank. v1.4 needed the area to stay at 6×4; a shared input register per write
  port, and the reset latch (27.2 µm²), remove the half-cycle shift.
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
- **Repair after global routing, with a larger hold margin (v1.1).** Sign-off stayed
  clean, but 532 more hold buffers bought only 9 ps of fast-corner hold slack, and
  slow-corner slew warnings rose from 124 to 166. The worst slew nets are detours taken
  by the detailed router (a 375 µm connection routed as 1.8 mm), which repair before
  detailed routing cannot see. Version 1.2 does repair after global routing, with a
  0.1 ns margin, because its hold losses come from routed clock feeds: there it adds
  34 buffers.
- **A larger hold margin after clock-tree synthesis (v1.2).** At 0.35 ns, 1,100 more
  hold buffers still left fast-corner paths at −0.101 ns, and the extra cells raised
  routing congestion.
- **More detailed-routing optimisation iterations.** Values above 64 crash this OpenROAD
  build in `detailed_route`.
