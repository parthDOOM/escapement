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
| Continuous integration | Every check above reruns on each change it depends on | `.github/workflows`, [results page](https://parthdoom.github.io/escapement/#results) |

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
| Random programs, version 1.0 machine (before this repository's history, which starts at v1.1) | 1,000 | 67,370 | 0 |
| Random programs with tape traffic, version 1.1 (seeds 1 to 300) | 300 | 21,055 | 0 |
| Random programs with pin bank B, version 1.2 (rerun at 683bc92, seeds 1 to 1,000) | 1,000 | 68,666 | 0 |
| Random programs, version 1.6 generator, on the v1.6 RTL (seeds 1 to 1,000; 36 longer than the program memory are skipped; `run_parallel.py --seeds 1000`) | 964 | 111,491 | 0 |
| Firmware programs with their bus models: 43 cases covering 45 of the 49 programs (the logic analyser runs in the streaming-capture case), on the v1.6 RTL, behavioural and standard-cell builds | 43 | — | 0 |
| Random programs with the host reading the tape during the run, version 1.3 | 60 | 7,598 words read | 0 |
| Firmware-shaped line unit programs, version 1.4 (`test_v14_line_unit`) | 40 | 61,738 (240,000 cycles) | 0 |
| The USB keyboard (`usb_hid.asm`) against the USB host model, version 1.4 | 1 | 70,000 cycles | 0 |
| The line unit alone against the simulator's model, every output every cycle (`make line-test`), on the v1.6 RTL, clock-gated and plain flip-flop builds | 40 seeds | 8 million edges on each build | 0 |
| Regression run (`make cosim`, 200 seeds plus the directed and firmware cases), behavioural and standard-cell builds | 8 tests | 19,720 in the random test (191 programs; 9 too long, skipped) | 0 |

Random programs come from a constrained generator (`test/cosim/progen.py`) that mixes
the data, branch, timer, event, tape, capture and line unit instructions, deliberate
faults (every ADMIT subcode and ILLEGAL words), and tape loads and stores that collide with
capture drains. A program longer than the 256-word program memory is skipped (counted in
the test's summary line). Since version 1.2 it also puts contexts on pin bank B, owns and
drives bank B pins, and points the capture unit at either bank, each on a random board for
both banks: in the version 1.2 campaign (seeds 1 to 1,000), 516 programs configure a bank B
context and 183 change a bank B output. A program with no bank B context is the same
program as in version 1.1, so the earlier campaigns still apply. Since version 1.4 a third
of the seeds also use the line unit, from a generator of their own, so every other seed
keeps its earlier program: one context configures the unit and issues receives, packets,
CRC jobs, GETL waits and OFF, and the other contexts try the same and fault. Since version
1.6 a third of the seeds, chosen independently by a generator of their own, also call
subroutines and use the mailboxes (`CALL`, `RET`, `PUSH`, `POP`, `RXREADY`, `TXREADY`), with a host that pushes words
at random ticks and drains the transmit mailboxes at a random rate (337 of the 964 programs
of seeds 1 to 1,000); before that those six instructions were compared only in the firmware
and directed cases.

The line unit has two more checks of its own. `make line-test` replays random stimulus
that follows the core's rules into `src/esc_line.v` alone and compares every output with
the simulator's model on every cycle: configurations from USB-like, CAN-like and random
profiles, packets at offset clocks with jitter, glitches and stuffing errors, noise, the
unit's own pins read back (the monitor), owner faults, stop, start and tape writes from
elsewhere; every job type and every end cause occurs. The firmware-shaped programs
(`progen.gen_lu_program`) run on the whole chip: receives of the unit's own packets and of
noise, packets at fresh and end-tick deadlines, owner `LDT`/`STT` that meet the unit's tape
cycles, and another context's traffic beside it.

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
| P8 | Every push meets the per-context assumptions; HALT completes only on an empty FIFO; faults happen only during a run; a commit comes only from a running context, never on a start or stop edge or one where it faults | Unbounded (PDR, with P2 for the lateness part of the last clause, below) |
| L1 | The line unit (v1.4) claims only pins its owner owns; it is idle and unconfigured while stopped and once its owner has faulted; a CONFIG never completes at one of its owner's commit edges | Unbounded (PDR), as lemmas for P3, P5 and P8 |
| L2 | (v1.6) The line unit's pin writes and transmit bit, registered in the cycle before a boundary, equal their definitions at the boundary; with the lemmas that make this inductive (a configuration in use is valid, the cell timer's range, a packet's state before its first boundary, when a stream word arrives) | Unbounded (k-induction, `esc_line.sby`), assume-guarantee: the core proves the unit's input rules (LI, PDR) |
| H1–H4 | Per context: queued deadlines are ahead of now and strictly increasing, only a running context has queued events, a queued DRIVE touches only owned pins | Unbounded (k-induction); H3 and H4 also by PDR in the core proof, as lemmas for P5 |
| D1–D3 | Per context: the retire and late flags (v1.5), and the head entry's kind and payload (v1.6), registered in the cycle before, equal the deadline tests on the current head and the current tick, and the current head | Unbounded (k-induction), in the per-context proof |
| B1–B9 | The values registered in the cycle before a commit equal their definitions on the commit cycle's state: the tests (v1.5: deadline ahead of the tick, timer reached, ahead of the FIFO tail, ahead of the open EXPECT window's end, and the lead saturated to 16 bits), and (v1.6) the decoded instruction and its legality (B6, B7) and the two operands (B8, B9) | Unbounded (k-induction, `esc_core.sby ahead`); assumed by the PDR tasks |
| C1–C3 | Capture never writes outside its region, never stages more than 2 records, and generates only during a run | Unbounded (PDR) |
| C4 | In stream mode a capture drain never writes over a record the host has not acknowledged (v1.3) | Unbounded (PDR) |
| G1–G3 | A clock-gated bank equals an enable flip-flop at every instant; the gated clock is never high while the clock is low, and rises only on a clock edge | Unbounded (k-induction, unconstrained clock) |
| G4–G5 | (v1.4) A latch bank equals an enable flip-flop at every instant whenever its input is the written value registered at the write edge (G4); every latch bank's input is that value (G5), and the host holds a mailbox word for a cycle after its push (M1) | Unbounded: G4 by k-induction with an unconstrained clock (`formal/esc_lreg_env.v`, its L1 and L2); G5 in the core proof (PDR; the D-40 contract assertions in `src/esc_ctx.v`); M1 on the host interface (PDR) |
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
`lemma-group` are therefore proved in one task: the output-enable parts of P3 and P5
both rest on H4, and together with H3 and H4 they prove in two minutes. Alone, P5's did
not finish in two hours, and P3's ran past the three-hour limit on the CI runner. Since
version 1.4 the line unit's L1 lemmas join that group (five minutes), and P8's commit
clause is proved with the CONFIG phase lemma. For version 1.6, with 2 jobs, the 29 core
tasks (20 minutes; the slowest 20 minutes alone) and the separate proofs (bounded check,
covers, the `ahead` task, context, clock-gate and latch-bank banks, host interface, line
unit: 61 minutes, 50 of them the bounded check) all pass in 81 minutes of wall time
(v1.5 with 6 jobs: 28 tasks, 72 minutes; v1.4: 27 tasks, 58 minutes). The
single task (`sby -f esc_core.sby pdr`, all assertions together) passed in 34 minutes on
one core for version 1.3.

**Tests decided a cycle early (v1.5, D-43).** Since version 1.5 the deadline tests are
registers, computed in the cycle before they are used: the retire and late flags of each
context's FIFO head (D1, D2), and the admission and timer tests of the context that
commits next (B1–B5). Version 1.6 registers more the same way: each FIFO's next head entry
(D3), so a retiring DRIVE reaches the pins from flip-flops, and, in the stage before the
commit, the decoded instruction (B6, B7) and the operands of the context that commits next
(B8, B9). Each is stated as an equality with its definition on the state of the cycle that
uses it, so every other property reads exactly as before. B1–B9 are proved by k-induction
in their own task (`sby -f esc_core.sby ahead`, in formal-quick), which keeps only those
labelled assertions: a commit follows at least three edges of an uninterrupted run,
because the pipeline's valid bits clear on start and stop, and over those edges the
registers are a delayed copy of their definitions. The task passes in seconds, and it has
teeth: measuring the lead from the current tick instead of the next one fails with a
trace, and computing the tests for the context committing now instead of next fails the
induction step. Every other task (the PDR tasks, the bounded check and the covers) assumes
B1–B9 instead of proving them. Bit-level PDR cannot use them otherwise: its invariant is a
set of clauses over the flip-flops, and "this register equals a 32-bit comparison of those
registers" has no compact clause form, even when it holds one edge after any state. Proved
as PDR tasks, alone or grouped with helper lemmas, they had not converged after an hour.
The assumption is sound because the `ahead` task proves them under the same environment.
Asserted at every step of the bounded check, they doubled its time by depth 15; assumed,
it reaches depth 16 in 569 s locally against 351 s for version 1.4, the rest being v1.5's
added state.

**Faults from the line unit.** A CONFIG that turns out invalid faults its owner when it
completes, an edge where another context may commit. P8's commit clause is therefore
per context, which is all `formal/esc_ctx_env.v` assumes: the committing context does not
fault at its own commit edge. L1's phase lemma shows a CONFIG never completes at one of
its owner's commit edges.

**Latch banks (v1.4, D-40).** The logic proofs see every bank as an enable flip-flop.
The latch build is tied to that model in two steps: `formal/esc_lreg.sby` proves, with
an unconstrained clock, that a latch bank equals the flip-flop at every instant provided
its input is the written value registered at the write edge (G4). The core proof then
shows that every bank's input is (G5). A bank's input is one of the shared write-data
registers, or the host's mailbox word, which the host holds for the cycle after a push
(M1). G4 has teeth: a latch fed straight from the write data, the design rejected in
v1.1, fails at the first step.

**The line unit's registered values (v1.6, D-44).** To fit in 20 ns the line unit decides
a boundary's pin writes and transmit bit in the cycle before it. L2 says the registered
values equal what the unit would compute at the boundary itself. Inside the core proof,
PDR did not converge on it (one task ran out of memory after an hour), so it is proved
like P2, in two parts. `formal/esc_line.sby` proves L2 for the unit alone by k-induction,
in seconds, assuming only the rules the core follows when it drives the unit's inputs:
one operation at an edge, from a running context in its own commit cycle, from the
owner (or a CONFIG taking a free unit), never while the unit is busy, SEND only with
claimed pins, and no fault kills the unit at an operation's edge. The core proves those
rules (LI, two PDR tasks, under 20 s together), with no line unit assertion assumed, so
the argument is not circular. Induction needed the lemmas listed with L2: without them it
starts from states the unit never reaches, such as a cell timer far beyond its period.
L2 has teeth: the boundary flag registered a cycle late fails at step 16, the pin value
built from the current cell instead of the next at step 25, and the first bit taken from
the wrong end of the prefix at step 15.

**Vacuity guards.** A cover task reaches the precondition of every property, including
capture overflow, region full, ring wrap, a drain and a push with two records staged,
and (v1.4) the line unit driving its pins, receiving, and a CONFIG faulting its owner. The clock-gate proof has teeth: a naive `clk & en` gate fails G3 at the first
step.

## RTL to netlist equivalence

The proofs above are about the RTL; the chip is built from the netlist that synthesis,
placement and routing produce. `make equiv` proves that the two are the same machine:
the final netlist of the version 1.6 harden (`v16s`, sha256 `f5bcfa00…`) is sequentially
equivalent to the RTL in `src/`, for every input sequence after reset.

Both designs are flattened and placed side by side in a miter that feeds them the same
pins, the same SRAM read data and the same clock, and compares all 24 chip outputs and
every SRAM input (address, data, enables, 332 bits in all) on every step. ABC's `dprove`
searches 19 steps for a difference, then matches the RTL's registers with the netlist's by
signal correspondence and closes the proof by induction. Nothing is matched by name, so
the netlist's renamed registers need no mapping, nor do the registers that reset to one
in the RTL and that synthesis builds as reset-to-zero flip-flops with inverters. The latch
banks are the standard-cell latches in both designs (the RTL is read with the standard-cell
define, as it is hardened), so the proof covers them as logic too.

**Two steps since version 1.6.** Synthesis recodes three small state machines of the line
unit (`sk`, `noted` and `es0_q` in `esc_line.v`) one-hot, so three of the netlist's registers
have no single partner in the RTL. Up to version 1.5 signal correspondence closed that
anyway (v1.5 already had `noted` recoded); with version 1.6's extra registers it no longer
does, and the one-step proof of the v1.6 netlist ended undecided after 86 minutes, with no
difference found. Applying only synthesis's FSM pass to the RTL reproduces this (undecided
after 56 minutes), and the same flow without the FSM pass proves in 198 s, so the recoding
is the whole cause. The proof therefore takes two steps:

1. `esc_line` alone is sequentially equivalent to `esc_line` after synthesis's FSM pass
   (the same miter at module level: 20 outputs, 151 bits; 7 s);
2. the netlist is sequentially equivalent to the RTL with that `esc_line` in place
   (332 bits; 228 s).

A module equivalent at every output from reset, for every input sequence, changes nothing
around it (every flip-flop and latch bank resets asynchronously, so nothing it outputs
during reset is kept), so together the netlist is equivalent to the RTL. `formal/equiv.sh`
runs both steps (about 4 minutes); with a second RTL tree instead of a netlist (the
mutation campaign) it runs the one direct proof, as before. Telling synthesis not to
recode (`fsm_encoding "none"`) would have kept one step, but it changes the netlist, and
the layout it gave (`v16t`) was cut short by an OpenROAD clock-tree bug that disconnects a
clock gate (D-45). The version 1.4 netlist (sha256 `661e5608…`, 245 s), the version 1.3
netlist (`54236b4f…`) and the version 1.1 release netlist (`1c9004ee…`) passed the one-step
proof.

**The check has teeth.** Faults injected into the v16s netlist are found by the proof
itself: the output stage of `uio_oe[0]` with two of its gate's inputs swapped fails at the
first compared step (37 s), and the MISO shift register's bit 6 wired to bit 5's input
fails at step 17 (38 s). Step 1 has its own: `esc_line`'s CONFIG step counter made to skip a
step on the recoded side fails at step 9.

Faults deep in the line unit need more than a search from reset can reach: the host must
configure a context and start it, the program must configure the line unit, and the unit
must send. In the whole-chip miter a bounded search did not reach the CONFIG step counter's
bit 1 wired to bit 0's input, or the bit position's bit 3 wired to bit 2's, within 75 steps.
So the same miter was also run as a simulation of one concrete session. Both SRAM macros are
modelled as memories holding a small program that configures the line unit from the tape and
sends 64 bits in a loop, and the host resets the chip, writes the configuration and starts
it. The CONFIG fault makes the two copies differ a few cycles after START, on the tape
address; the bit-position fault does so eight bits into the first SEND. The unfaulted
netlist shows no difference over the same 1,000 cycles, while its pins change 166 times.
A difference in the miter is a counterexample, so the proof cannot pass with either fault.

**Scope.** The SRAM macros are hard IP and are not compared, only everything around
them. Cell behaviour comes from the functions in the PDK's liberty file, so this is a
statement about logic; timing is the signoff's job. The clock is ideal, one edge per
step, so the clock tree is checked as logic, not as delays. The clock-gate latches have
no reset and start at zero in both designs. Reset is held for the first steps and free
afterwards, as is every other input.

## Mutation testing

`test/mutation/run_mutants.py` plants one fault at a time in a copy of `src/` and runs
the test suites that can see the mutated file, cheapest first, until one fails: the
line unit lockstep (for `esc_line.v`), the lockstep co-simulation, the core unit tests, the
host interface tests, the chip tests and the firmware-through-the-pins tests. A suite that
hangs counts as a kill: one still running after three times its own time on the unmutated
RTL, measured in the same run on the same machine (or after its fixed limit, if longer).
The suites simulate the RTL with the standard-cell clock gates and
latch banks (`CELLS=yes`), as it is hardened, so that a mutant of a latch bank's input is
visible. Before any mutant is scored, every suite it needs must pass on the unmutated
RTL: a suite that cannot run (a tool or model problem) would otherwise fail for every
mutant and read as kills.

The harness checks itself as well (v1.6). After the baseline, each suite must fail on a
**canary**, a fault it is known to see, and the equivalence check must prove a rewrite that
changes nothing and refute one that changes an output, so a suite or a proof that can only
say yes is caught before any mutant is scored. Every kill stores its **evidence**, the failing
tests and the first assertion or mismatch, and a **kill audit** reruns a sample of kills with
the killing suite alone and reads that evidence. A final **reconcile** checks that every
selected mutant has exactly one result, that every kill names a suite and every exclusion
has its argument; in CI the summary job runs it over the merged shards and fails on any
miss. The line unit lockstep counts its own replayed edges and the stray tape
reads it makes, and fails a run that replays fewer edges than it generated or makes none.

The campaign has 836 mutants across the whole RTL, the line unit included (745 before
v1.6). Seventy are written by hand, each aimed at one feature (below). The other 766 are
generated
(`test/mutation/generate.py`):
every synthesisable line outside the verification-only blocks (`ifdef FORMAL` and the
other macros only `formal/*.sby` defines) is scanned for classic fault sites
(a comparison or logic operator swapped, an inversion dropped, an arithmetic operator
swapped, a constant changed, an `if` condition negated, a register that keeps its value
instead of taking an assignment), and a site is chosen when a hash of its file, operator
and line text falls under a fixed rate (30%), so editing one line changes only that line's
mutants. `--rate 1` takes every site, 2,507 mutants in all; that run is too long for CI
and is made locally, on lines that change.

A mutant that every suite misses is then checked formally against the original RTL with
`formal/equiv.sh`: if no input sequence ever makes any chip output differ, it is
**equivalent**, and no test could kill it. Otherwise it is a test hole.

The hand-written mutants:

Eighteen target v1.5's tests decided a cycle early (D-43). Six the FIFO head's retire
flag: measured against the current tick instead of the next, the entry behind a retiring
head never tested, an entry pushed into an empty FIFO with lead 1 never retiring, a test
that ignores a retirement emptying the FIFO, an empty FIFO tested against a lead nobody
pushed, and a stop or fault that leaves the flag set. Twelve the core's registered tests:
computed for the context committing now instead of next, the lead measured from the
current tick, the FIFO tail or the window end taken from the wrong context, a timer exactly
half the range ahead counted as reached, a deadline 2^31 − 1 ticks ahead refused, unsigned
comparisons in place of the half-range ones (wrong only across the counter's wrap), the
lead saturating at 17 bits or one below 0xFFFF, and a deadline equal to the FIFO tail or
to the open window's end admitted.

Eight mutants target pin bank B: a DRIVE landing one pin off, a released output going
low, a context reading the other bank, ownership checked against the wrong bank, a fault
that keeps its bank B pins, inputs that never reach the synchronisers, a digest blind to
bank B, and capture ignoring the bank bit.

Seven target streaming capture: a drain writing over unacknowledged records, the hold
point one record late, TAPE_ACK ignored, arming that keeps the old read pointer, a
stream that stops at the limit instead of wrapping, a drain that loses the port to the
host, and debug select 20 not showing the read pointer.

Eighteen target v1.4: ten the latch banks (a bank latching another port's data, stale
data at START, an entry one behind), three the two-slot mailboxes (a ring head that never
moves, a push into the slot the host is popping), and five the shared debug read and its
slot counter (a stale field, a wrong context, swapped fields, slots one context off).

and the original nineteen: inverted comparisons, off-by-one deadlines and pointers, a
dropped polynomial term in the digest, a missing GETI wait, broken tape forwarding,
capture rule changes and clock-gate enable errors.

**Result (v1.6, every site on the lines v1.6 changed): 626 mutants, none surviving.** The
campaign took every generated site on the lines v1.6 changed in `esc_core.v`, `esc_ctx.v`
and `esc_line.v`, 230 at the default rate and 479 more at `--rate 1`, less 85 in
verification-only blocks that the generator no longer mutates, and the two hand-written
mutants moved onto v1.6's code. It ran locally on the standard-cell models, as CI runs
mutants.

| Outcome | Mutants |
| --- | ---: |
| Killed by the line unit lockstep | 355 |
| Killed by the lockstep co-simulation | 160 |
| Killed by the core unit tests | 41 |
| Proved equivalent (`formal/equiv.sh`) | 22 |
| Excluded, each with its written argument in `run_mutants.py` | 48 |

No kill is a timeout. The kill audit reran 46 kills alone, 15 per suite and two chosen for
dying within five seconds: 44 failed with an assertion or a mismatch against the reference.
The two fast ones fail every co-simulation test because their LDT write strobe fires in
almost every cycle and fills the registers with the tape's undefined read data, so the
testbench stops on a non-0/1 value rather than at a comparison. That is a real difference
(silicon would load arbitrary words where the simulator keeps defined values), found by X
propagation.

*What the survivors found.* Seven mutants survived the campaign without being equivalent.
Each changed a line unit pin, port or status output in a case the line unit lockstep reached
too rarely: the mutation harness ran that suite with 4 seeds where `make line-test` runs 8,
and three cases need a configuration the random mix almost never builds. The harness now
runs the suite exactly as `make line-test` does, and the suite ends with three profiled runs
in which every configuration comes from one profile: 10BASE-T (an end-of-packet symbol of two
cells after a 0 bit), SPI-like (an empty packet in a differential configuration without
end-of-packet symbols) and CAN with the abort monitor sampling in the cycle before a
boundary (S = P − 1). Seeds 1–8 drive exactly the stimulus they drove before, so no earlier
kill changes. With that, all seven are killed: four by seeds 6 and 7 (the prefetch of a
last word of one bit, the end-of-packet level after a configuration without `ext`, the second
pin's idle level after a CONFIG, and the drive enable of an empty packet) and three only by
the profiled runs (the symbol kind of an end-of-packet cell, the first segment of an empty
packet, and the abort monitor's prediction). Every excluded line unit mutant was then run
through the strengthened suite as well; none is killed by it.

*The 48 exclusions* did not decide within the proof's 30 minutes and change no behaviour by
argument: 30 change a value that is always written again before anything reads it (reset,
START, kill, end-of-packet and CONFIG values: the core's operand registers, line unit
registers read only in the transmit or receive modes or by a later job, and the CRC's low
half, which no output shows); 6 add a
tape read whose data nothing takes; 4 change the FIFO's head or ring position before
anything reads through it; 4 change a value that gives the same result in every reachable
state (a segment past the last, the next symbol's kind, a mode that is already IDLE);
3 load the next-word buffer with a word that is overwritten before it is read; and 1 acts
only when a FIFO head is past its deadline, which P2 proves never happens.

**Result (v1.4): 653 of 653 scored mutants killed, none surviving.**

| Outcome | Mutants |
| --- | ---: |
| Killed by the lockstep co-simulation | 342 |
| Killed by the line unit lockstep | 185 |
| Killed by the host interface tests | 81 |
| Killed by the core unit tests | 42 |
| Killed by the chip tests | 3 |
| Proved equivalent (`formal/equiv.sh`): no chip output can differ | 19 |
| Excluded, each with its written argument in `run_mutants.py` | 38 |

**v1.5's new logic, every site (local, behavioural build).** The 18 hand-written v1.5
mutants, the original `admit_past_off_by_one` (moved to v1.5's registered test) and all 84
generated sites (`--rate 1`) on the lines v1.5 changed in `esc_ctx.v` and `esc_core.v`, 103
mutants: 97 killed, 5 proved equivalent, 1 excluded (`late_q` keeping its value, when it is
never set because no head is ever late: argument P2), none surviving. Five survived a first
run; the core unit tests `test_timer_range_edges`, `test_lead_saturates` and the no-fault
check in `test_stop` kill four of them. The whole campaign (745 mutants then) on the standard-cell
models runs in CI. Its first run on v1.5 killed 699, proved 16 equivalent and excluded 29;
one survived: START setting the FIFO's head to 1 instead of 0, whose equivalence proof
was undecided in 30 minutes. It is excluded with its argument: the FIFO is a ring
addressed only relative to the head, START empties it, and every read of a slot is
gated by the count. The rerun with that exclusion reported 705 killed, 15 proved
equivalent and 25 excluded, none surviving. Compared mutant by mutant, 20 mutants changed
category between the two runs, every one through the pins suite running out of time, and
7 of them were proved equivalent in one run and scored as killed by the timeout in the
other: the fixed limit (40 minutes) was only about 1.3 times what the suite took unmutated
on a CI runner with four jobs, so a busy runner, not a test, decided those. 20 of the
rerun's 705 kills were such timeouts. Since v1.6 each suite's limit is three times its
baseline time (above); counts from v1.6 on come from runs with that limit.

**v1.4's holes, now closed.** The first v1.4 campaign, on the behavioural build, left 9
survivors. Three showed holes, closed with directed co-simulation programs: a SEND into a
full FIFO, a SEND before the FIFO's tail, and a mailbox push and pop at the edges the host
uses. Four were excluded with an argument, and the rest were latch-bank mutants that only
the standard-cell models can show, so the campaign moved to them. The next run scored 710
of 711 killed, which was false: the CI simulator, Icarus Verilog 12, gives X on the PDK's
cell models, so every co-simulation failed within seconds of starting. The baseline check
above now catches that, and CI builds Icarus 13. The real campaign then left 19
survivors. Two holes were closed with core unit tests that kill three of them: a line
unit tape access at a stop edge (`test_line_unit_makes_no_access_at_a_stop_edge`, which
watches the tape port in a cycle the unit had scheduled an access), and the debug port
read after a stop within four cycles of a commit, which returned the value before that
commit (`test_debug_port_after_stop_shows_final_registers`). The other 16 are excluded
with an argument (below).

**v1.3's holes, closed then.** With the tests as they were, 57
mutants survived. Each was read against the RTL; the holes they showed were closed with
nine new tests, which kill 29 of them:

- *Nothing read the chip between reset and its first START.* A host can read every debug
  select over SPI at any time, so a wrong reset value is a visible bug. `test_reset_state`
  reads all of them after reset; the host test checks that a reset clears the
  configuration, fires no strobe and writes no memory word, and that both memories keep
  their contents.
- *Edge cases random programs rarely reach:* the Z flag of OR and AND when the operands'
  sum wraps to zero (`0x80000000 | 0x80000000`), a taken JC, JNC or DJNZ at PC 255 (not
  sequential, so no PC_RANGE fault), GETI next to a retired SAMPLE that still sits one slot
  past the FIFO tail, the stale-fetch error on the first word of a tape read, and the
  digest after a STOP that releases driven pins.
- *A power claim with no test:* a one-context program reads the program store one cycle
  in four, and a stopped chip never (D-29b).
- *The attention pin* `uo_out[1]`, IRQ or fault, for an IRQ alone and for a fault alone.

Writing the reset test also showed that the unit testbench never gave reset a falling
edge, so the behavioural model of a clock-gated register could stay unknown after reset
in simulation; silicon flip-flops reset on the level. The testbench now does.

**The 38 exclusions** are mutants that the equivalence check did not decide within its
time limit (30 minutes each) and that change no behaviour by argument: three act only when
a FIFO head is past its deadline, which P2 proves never happens; two change tie-offs of
the SRAM macro's configuration pins that have no function in any model; four add a tape
read whose data nothing takes (every consumer takes read data only in the cycle after a
read of its own, and a write wins over a read); four change when the debug port's cached
field reloads while the chip is stopped, so that it reloads the value it already holds;
one changes a first-fault priority between two events that cannot meet (a line unit
CONFIG fault is never at an owner commit edge); and 24 change the reset or idle value of
state that is always loaded before anything reads it (the capture unit's snapshot flag
and staging word, the host engine's data, address and context registers, and line unit
fields read only in the transmit or receive mode, whose entry sets them).

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
| `westminster` | CHECKED | 57 |
| `usb_hid` (v1.4 line unit) | CHECKED | 1; replies 4 (below) |
| `can_rx` (v1.4 line unit) | CHECKED | no events |
| `dshot_tx` (two contexts) | CHECKED | 8 / no events |
| `sent_tx` (two contexts; with or without the pause pulse) | CHECKED | 4 / no events |
| `cec` | CHECKED | 4 |
| `dali` | CHECKED | 13 |
| `seven_seg` | CHECKED | 9 |
| `vga_pong` (two contexts) | CHECKED | 1597 / 8 |
| Generated from timing tables: `uart_tx_gen`, `midi_tx`, `dmx512_tx`, `nec_tx_gen` | CHECKED | 9 each |
| Generated from a timing table: `ws2812_gen` | CHECKED | 5 |

Every certificate is CHECKED for all inputs, without assumptions about bus timing:
re-anchoring the time base after every wait makes the input-dependent I2C paths provable
unconditionally. For line unit firmware escert also reports when replies leave: every
`usb_hid` reply has its deadline exactly 72 ticks after the end of the packet it
answers, with a lead of at least 4 ticks on every path where the host keeps to USB
timing; a late reply is dropped, never a fault ([line unit](19-LINE-UNIT.md)). The certified bounds are at or below the leads measured by the chip's
telemetry in simulation, and most are equal to the tick. `replay.asm` computes its
deadlines from captured data, so it is checked by the replay tests and by the hardware's
admission rules instead.

**At the rated maximum clock (D-5).** In version 1.5 the 14 programs whose timing derived
from `CLK_KHZ` certified CHECKED rebuilt for 33 MHz and for 33.333 MHz, and each drove its
bus model at 33 MHz with the model timing every edge in 30.3 ns ticks
(`test_rated_clock.py`; the 14-second `westminster` tune is certified at that clock and
played at a hundredth of real time in `test_firmware.py`). Since version 1.6, 39 programs
derive their timing from `CLK_KHZ`: all certify at 33, 33.333, 40 and 50 MHz, and 36 drive
their bus models at 33 and at 50 MHz (the other three: the two combined images, whose modules
run on their own, and `westminster`). Converting them found two clocks rounded the fast
way: JTAG's TCK and SWD's SWCLK half periods were rounded down, so at 33 MHz the clocks
ran 2% and 0.6% above their stated rates; they now round up. At 25 MHz every converted
program assembles to the same image as before except two, changed on purpose: `nec_tx`
drains its queue before a 1's long space too (the same edges at 25 MHz), and `i2c_ctrl`
waits for each scheduled SCL fall before re-anchoring its bit timeline and watches for
SCL's rise only once its release is due, so every edge it schedules follows the previous
one at any clock (the 25 MHz build had held by tick count alone). The second wait
lengthens each bit: SCL runs at 347 kHz at 25 MHz instead of 368 kHz. Four programs are tied to one clock by
their protocol (10BASE-T, USB low speed, VGA) and refuse any other with an `.assert`.
In version 1.5, three needed changes, which leave their 25 MHz behaviour as it was. `nec_rx` measured in
4-tick units, and its longest bound outgrew the 16-bit compare; its unit now doubles above
25 MHz. `westminster`'s 0.65 s rest outgrew ADDT's 24 bits; it is added in two halves.
`nec_tx`'s repeat code queued its stop burst more than 2^16 ticks after the leader's last
edge, which escert could not bound (POSSIBLE): at 25 MHz the gap had been 56,214 ticks. It
now drains the queue first, as the frame path always did, and certified from 2.5 to 38 MHz;
since version 1.6 a 1's long space drains it too, and it certifies from 2.5 to 100 MHz.
Both NEC programs rebuilt for 33 MHz and for 50 MHz also run a frame through the chip's
pins, transmitter through an IR receiver model into the receiver, on the RTL and on the
v16s netlist (`test_nec_loopback_at_33_mhz_on_chip`, `test_nec_loopback_at_50_mhz_on_chip`;
47 and 71 minutes on the netlist). That simulation is functional; the 20 ns timing of the
same netlist is the static analysis in
[physical design](03-PHYSICAL-DESIGN.md#sign-off-results).

**The checker found real bugs.** It refuted the I2C controller's stuck-bus timeout,
which released the bus with a lead of −4, and a PS/2 frame that started under the
previous frame's queued events when scancodes arrived back to back. No test had reached
either. In both cases the chip would have refused the edge with an ADMIT fault rather
than emitting it late. Both are fixed, and the old versions are regression tests.

**Register intervals.** A `MOVHI` on a register whose value is unknown gives an interval
with its high half known; a `LINE` whose operand is known down to bits 31:16 to be a CRC
job is a job of 0 to 255 ticks that writes no tape word. `can_rx` needs this: its CRC job
covers exactly the frame's length, which the frame's own DLC gives.

**The checker is tested, not trusted.** On random co-simulation programs, every CHECKED
context runs on the simulator without an admission fault and with telemetry at or above
the bound. That test found two checker bugs before release, and the chip's telemetry
caught a third on the first run.

The planted timing bugs (below) found a fourth in v1.6. escert let a `SEND` wait for a
running CONFIG, but the chip checks ownership first (SEMANTICS §6.2, §7.7). A `SEND`,
RECV or CRC before the context's first CONFIG completes, or after an OFF, faults
ADMIT/OWNER instead. With the ADDT before `udp_tx`'s first `GETL` deleted, the `GETL` timed
out at once, and the `SEND` committed one tick before the extended CONFIG completed. The
random line-unit programs missed this because each loaded its operand with an `LDT`,
which waits on the CONFIG's tape reads. escert now tracks whether the unit is configured,
and an owner `LDT` or `STT` during the first CONFIG commits exactly in the slot after it.
New tests cover the boundary tick, OFF, use before any CONFIG and a configuration without
`tx_pins`, plus random line-unit programs with operands from `MOVI`/`MOVHI`.

### Certificates against measurements

`test/cosim/proof_table.py` (`make proof-table`) sets every certified bound beside the
leads the implementations measure, so each certificate is tested on every run instead of
trusted. A measured lead below its bound is a bug in escert or in whatever measured it,
and fails the table.

Result on 2026-11-02 (v1.6): the 43 co-simulation cases with their bus models and host
traffic (53 contexts). In v1.5 there were 26 cases and 36 contexts; 31 measured a lead,
28 of them exactly the bound.

| | Contexts |
| --- | ---: |
| Queued events, so a lead was measured | 46 |
| Measured lead equal to the certified bound, to the tick | 39 |
| Measured lead above the bound (`ws2812` 8 against 5, `ws2812_gen` 6 against 5, `usb_hid` 12 against 1; on the line unit, `can_tx_lu` 17, `eth_tx` 13, `udp_tx` 13 and `ws2812_lu` 20, each against 1) | 7 |
| Measured lead below the bound | 0 |
| RTL lead or digest different from the ISS's | 0 |

A `SEND` that is late is dropped rather than faulted. Where escert cannot rule out such a
drop, the lead it proves for an admitted `SEND` is only 1 tick. Hence the four line-unit
bounds of 1.

The RTL columns come from the co-simulation itself: with `COSIM_PROOF_OUT=file`,
`test_cosim.py` records the RTL's telemetry and digest for each firmware, and `--rtl file`
adds them. Most bounds are exact, which makes the check sharp: a chip that queued one
event a tick closer to its deadline than escert's worst case would fail it.

The table also runs every shipped firmware on the ISS in the demo board's environment,
with nothing on the Pmods: the environment in which bring-up stage 5 checks the same
bounds on the chip and writes `proof.json` for `--board`
([bring-up](16-BRING-UP.md#stage-5-firmware-digests)). There, of 57 contexts in 48
programs, 43 queue events (v1.6). Of those, 32 measure exactly their bound and none is
below it; v1.5 had 30 and 21. The other 11 are looser (`i2c_ctrl` 33 against 2, for
example), because the inputs that produce the worst case, such as a device on the bus,
are absent.

Certificates compose. Each context issues in barrel slots fixed by the configuration (a
paired context's two included, v1.7), so another context can
reach it only through data: pins, tape and mailboxes, which escert already treats as
unknown inputs. Firmware certified one context at a time therefore stays certified when
the contexts run together, as long as they own disjoint pins, none writes tape words
that another reads as constants, and at most one uses the line unit (there is one, and the
first context to configure it owns it; escert flags a second user in a combined image). The two combined images in the table certify and
measure context by context.

Building the table found one imprecision in escert. Its completeness check sent a RET to
the return address of every CALL in the image, so in `nec_ir_cosim.asm` (`nec_tx` and
`nec_rx` together) the receiver's RET reached the transmitter's burst routine, and the
receiver could not be certified. A RET now returns only to the CALLs its own context
reaches (`test_a_ret_reaches_only_its_own_contexts_calls`).

### Planted timing bugs

`test/mutation/fw_mutants.py` (`make fw-mutants`) plants the timing bugs firmware authors
make, one per mutant, in the source of each shipped program: a WAITT deleted, an ADDT
deleted, an ADDT's delay cut to a quarter, or a conditional branch inverted. Each mutant
is certified, and a certified mutant is then attacked with escert's randomised input
search on the ISS: a fault there would mean escert certified a program that can fault.

Mutants are chosen by hashing each program's source lines and the searches are seeded,
so `make fw-mutants` on the same firmware prints the same table exactly; editing a
program changes its mutants. The first run on v1.6's 45 programs found one CHECKED mutant
that faulted: `udp_tx` without the ADDT at its line 73, the escert bug described above.
With escert fixed, that mutant is REFUTED. Result on 2026-11-02, 394 mutants, 12 input
searches each (v1.5 on 2026-10-31: 237 mutants in 28 programs, none CHECKED and
faulting):

| escert's answer | Mutants |
| --- | ---: |
| REFUTED: a fault, reproduced on the ISS with concrete inputs | 88 |
| NOT_PROVEN: a fault not excluded, and none found by the search | 29 |
| CHECKED: no admission fault for any input | 277 |
| CHECKED, yet the search made it fault | **0** |

A CHECKED mutant is a change that keeps every deadline admissible, which escert is right
to accept: it proves timing, not protocol correctness. Of the 92 inverted branches, 91
are of this kind: escert already follows both directions of a branch whose flag depends
on inputs, so inverting one leaves the same timed paths. The remaining one, in `servo`,
is NOT_PROVEN. Of the 122 shortened delays, 102 are certified. Fifteen of the 29
NOT_PROVEN mutants violate a rule in every state escert holds at the flagged
instruction, for example a lead of exactly −24 ticks in `cec` without one of its ADDTs:
they fault if that instruction is reachable, and the random search did not reach it.
NOT_PROVEN is never counted as safe; such firmware carries no certificate.

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
| Westminster Quarters, the hour chime of Big Ben, on a buzzer on bank B | `westminster` | Note decoder at a hundredth of real time: all 17 notes in order, each at its pitch (within 0.2%) and length, and every silence to the tick |
| Logic analyser and replay | `logic_analyzer`, `replay` | Tick-exact replay of a captured UART trace; a too-dense trace is refused; streaming capture past the tape's size (v1.3) on the simulator, the RTL (with a host reading during the run) and through the chip's pins |
| UART and MIDI OUT from timing tables | `uart_tx_gen`, `midi_tx` | UART receiver model at 115,200 and 31,250 baud |
| DMX512 from a timing table | `dmx512_tx` | DMX receiver model with the ANSI E1.11 receiver limits: start code and 511 slots, break and mark-after-break times |
| WS2812 from a timing table, colours in the tape | `ws2812_gen` | LED chain model with datasheet timing windows |
| NEC infrared from a timing table | `nec_tx_gen` | NEC receiver model: carrier, pulse counts, every burst position |
| Seven-segment animation on the demo board's display | `seven_seg` | Every frame on bank B, in order, to the tick |
| VGA Pong, 640x480 at 60 Hz, on bank B ([VGA Pong](21-VGA-PONG.md)) | `vga_pong` | A monitor's view of the pins: VESA sync timing, every pixel of two frames against a model of the game, black blanking, 14 game steps against the model and stated outcomes; the first frame through the chip's pins on the RTL and the final netlist, with the telemetry equal to the certificate |
| CAN 2.0A and 2.0B receiver on the line unit (v1.4): standard, extended and remote frames, CRC-15 checked by the line unit's CRC engine | `can_rx` | Bus model playing frames back to back from nodes 0.5% slow or fast; bad CRCs, frames cut by an error flag, DLC over 8, 500 kbit/s |
| DShot600 for four ESCs from one context, 600 kbit/s exactly (42, 42, 41 ticks per bit), and bidirectional DShot | `dshot_tx` | ESC models that check every pulse width, bit period and CRC; frame period to the tick through host updates; at 24 MHz |
| SENT (SAE J2716) sensor, with or without a pause pulse | `sent_tx` | Receiver model that calibrates from the sync pulse and checks the recommended CRC-4 (and the CRC against polynomial division); frames back to back to the tick, values changing mid-run never mixing frames |
| HDMI-CEC adapter: follower and initiator | `cec` | CEC device model with the Linux cec-pin timing windows: acknowledgement of its own address only, broadcast never driven, sending with arbitration (lost to a simultaneous start, then retried), retries after a refusal, a host-set logical address |
| DALI control gear (IEC 62386-102 commands and queries) | `dali` | Controller model at nominal and ±10% bit timing: levels, OFF, RECALL, queries answered 7.5 ms after the forward frame on the half-bit grid; other addresses, groups, broadcast |
| USB 1.1 low-speed HID keyboard on the line unit (v1.4) | `usb_hid` | Reactive USB host model from the USB 2.0 text that checks every device packet and its turnaround: enumeration, lost and corrupted handshakes, corrupt tokens and data, bus resets at every stage, host clock ±5%, hostile traffic (74 tests); and the Linux kernel itself, which enumerates the simulated chip over USB/IP and receives typed text |

## Test suites

| Suite | Tests | Result |
| --- | ---: | --- |
| Software (`make sw-test`): assembler, simulator, certificates, models, firmware against bus models, line unit and USB, tape tools, capture export and SUMP bridge, bring-up script, protocols learned from random, specified and real recordings, VGA Pong, the site's make-it-late audit, the clock-parameterised firmware at 33 to 50 MHz | 980 | Pass (7 need `sigrok-cli`, which CI installs) |
| Chip level on the RTL (`make test`) | 7 | Pass |
| Chip level on the final gate-level netlist (`make gl-test`) | 7 | Pass |
| Chip level and co-simulation on the RTL with the standard-cell clock gates and latch banks (`CELLS=yes make test cosim-parallel`, v1.4) | 7 + all | Pass |
| Co-simulation (`make cosim`) | 8 | Pass |
| Host interface (`make host-test`) | 16 | Pass |
| Core unit tests (`make unit-test`) | 35 | Pass |
| Newer firmware (SWD, SPI target, NEC, NEC rebuilt for 33 and for 50 MHz, both pin banks at once, VGA Pong) and a protocol learned from the chip's own capture, through the chip pins (`make pin-test`, `make gl-pin-test`) | 9 | Pass on the RTL and on the final netlist |
| Bring-up script on the chip RTL (`make bringup-test`) | 2 | Pass |
| Lint (`make lint`, Verilator `-Wall`) | — | Clean |

The chip-level tests drive the real pins: they load firmware over SPI, run it, decode
the protocol on the pins, and compare the execution digest with the simulator's
prediction. On the gate-level netlist, an equal digest means the gates executed exactly
the commits, register values and pin changes of the model. Gate-level simulation is
zero-delay: it shows that the gates compute what the RTL computes, not how fast. Timing,
at the rated 20 ns and at the default 40 ns, comes from static analysis of the routed
netlist with extracted parasitics at three corners ([physical design](03-PHYSICAL-DESIGN.md)).

## Limits

- **Pad timing.** CMOS5L pads are not characterised, so timing claims are made at the
  internal tick, not at the package pins.
- **Metastability** is not modelled probabilistically: inputs pass through a two-flop
  synchroniser, and simulation models it as a two-tick delay.
- **The EEPROM** stretches the clock before each acknowledge, which a real 24C02 does
  not; its Fast-mode margin is measured against our controller model only.
- **Replay** covers one 2^24-tick epoch (0.67 s at 25 MHz).
- **No hardware yet.** Every result on this page comes from simulation, formal proof or
  static analysis. The FPGA build (`fpga/`) passes the chip tests and the firmware through
  the pins in simulation; a board run is planned before submission.

## In CI

Every check above runs on GitHub Actions whenever something it depends on changes, and the
[results page](https://parthdoom.github.io/escapement/#results) shows the latest run of each
on master, with its commit and log, failures included.

| Workflow | Runs on | Checks |
| --- | --- | --- |
| `test` | every push | chip tests on the RTL; software tests, timing certificates, lockstep co-simulation, the certificates against the RTL's measured leads (`proof_table.py`), firmware through the pins; the co-simulation and the line unit lockstep again on the standard-cell models (Icarus Verilog 13) |
| `formal` | a change to `src/` or `formal/` | `make formal`: formal-quick, then the unbounded core proof as one PDR task per assertion |
| `mutation` | a change to `src/`, `test/` or the simulator | the whole mutation campaign in sixteen shards on the standard-cell models (Icarus Verilog 13), after a baseline of every suite, with the equivalence check of survivors; the shards are dealt by each mutant's time in earlier runs (`test/mutation/costs.json`), the canaries run once beside them, and the summary requires the canaries and exactly one result per mutant |
| `gds` | a change to the RTL or flow | LibreLane layout, Tiny Tapeout precheck, chip tests on the gate-level netlist |
| `equivalence` | each successful `gds` run on master | that run's netlist against the RTL of its commit (`formal/equiv.sh`) |

Each job writes a summary (`scripts/ci_summary.py`); the site's build collects the
summaries of the latest master runs (`site/tools/ci_results.py`) and rebuilds after each of
these workflows. The formal and equivalence jobs use the OSS CAD Suite release pinned in
`scripts/versions.txt`, and the equivalence job the standard-cell liberty of the pinned PDK.

## Rerunning

```sh
make sw-test cert test cosim-parallel host-test unit-test lint   # simulation and certificates
make proof-table fw-mutants                                      # certificates against measurements; planted timing bugs
make pin-test bringup-test                                       # newer firmware through the pins; bring-up script
make gl-test gl-pin-test                                         # the same chip-level checks on the gate-level netlist
CELLS=yes make test cosim-parallel                               # the RTL with the real clock-gate and latch cells
make formal-quick                                                # bounded core proof, cover, ahead, never-late, clock gate, latch bank, host
make formal JOBS=8                                               # adds the unbounded core proof (81 min for v1.6 at 2 jobs)
make equiv                                                       # final netlist equals the RTL (~4 min)
CELLS=yes make mutation JOBS=8                                   # mutation campaign, 8 mutants at a time (hours; CI: 16 shards dealt by cost)
```

The suites run in parallel where they can: `sw-test` uses every core when pytest-xdist is
installed, `cosim-parallel` splits the random programs and the firmware runs across
`JOBS` simulators from one build, `formal` proves the core's assertions as parallel
tasks, and the mutation campaign runs `JOBS` mutants at once.

Every simulation target also runs on Verilator with `SIM=verilator` (5.036 or later).
Icarus stays the default and the reference; CI uses it (version 13: the distribution's
12.0 gives X on the PDK's cell models). Measured on one machine for version 1.3, both
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
