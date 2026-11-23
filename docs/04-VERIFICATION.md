# Verification

Escapement makes three promises, and this chapter shows how each is checked:

- **The chip keeps time exactly.** Every event it admits is applied on its tick, and an
  event that cannot be on time is refused with a fault, never emitted late.
- **Certified firmware is never refused.** A program that the certificate checker accepts
  cannot meet that fault, for any input.
- **The chip that is fabricated is the chip that was proved.** The routed netlist behaves
  exactly like the RTL that the proofs and tests are about.

No single method can show all of this. A test sees only the inputs it tries, a proof
covers only the properties someone wrote down, and both can be fooled by a model that
shares a mistake with the design. So the evidence comes from several independent kinds of
check, each able to catch what the others miss:

| Layer | What it establishes | Where |
| --- | --- | --- |
| Normative specification | One cycle-exact definition of the machine | [11-SEMANTICS](11-SEMANTICS.md) |
| Instruction-set simulator | A second implementation of that definition, in Python | `sw/esciss` |
| Lockstep co-simulation | RTL and simulator agree on every commit and every pin, every tick | `test/cosim` |
| Formal proofs | Safety properties hold in every reachable state, not just in tests | `formal/`, `ifdef FORMAL` blocks in `src/` |
| Mutation testing | The tests notice a broken design | `test/mutation` |
| Timing certificates | Firmware can never trigger an admission fault, for any input | `sw/escert` |
| On-chip telemetry, digest and tripwires | The silicon checks the certificates, the model and the proofs | [architecture](01-ARCHITECTURE.md) |
| Gate-level simulation | The hardened netlist behaves like the RTL | `test/` with `GATES=yes` |
| Equivalence checking | The hardened netlist equals the RTL for every input sequence | `formal/equiv.sh` |
| Continuous integration | Every check above reruns on each change it depends on | `.github/workflows`, [results page](https://parthdoom.github.io/escapement/#results) |

The numbers in this chapter come from the final continuous-integration runs on version 3.1
unless a section says otherwise. The final runs at a glance:

| Check | Result |
| --- | --- |
| Formal proofs | 110 of 110 tasks pass; 15 planted bugs each make their proof fail |
| Netlist against RTL | Sequentially equivalent, 336 output bits compared |
| Lockstep co-simulation | 191 random programs and 76 firmware cases, on two builds of the RTL: no difference |
| Timing certificates | 82 of 82 programs certified; the measured lead equals the bound in 59 of 76 contexts and is never below it |
| Mutation testing | 1,017 planted RTL faults: 902 caught, 57 proved harmless, 58 argued harmless, none surviving |
| Outside judges | decoders written by other people agree with 74 of the 82 programs; every control fails |
| Planted firmware bugs | 729 timing bugs in 78 programs: 178 refuted with a reproducer, 18 not proven, 533 certified, none certified that can fault |
| Pads and board | every firmware case still passes with its pins up to three ticks slower each way |
| Test suites | 1,632 software tests (5 skipped where a tool is absent); 12 chip tests on the RTL and on the netlist; 9 firmware tests through the pins |

## How the checks grew

The checks were added as the design grew, and several of them because of something an
earlier check missed. The simulator and the lockstep comparison came first: by 11 October,
before this repository's history begins at version 1.1, the two implementations agreed on
every firmware program and on 1,000 random programs. The version 1.1 release netlist was
already proved equivalent to its RTL. Mutation testing had found its first holes by version
1.3 and moved into CI with version 1.4, where its first runs showed that a simulator could
make every test fail for the wrong reason. Version 1.5 computed the deadline tests a cycle
early for speed, which needed a new kind of proof; version 1.6 registered the line unit's
outputs and split the equivalence proof in two; version 1.7 added live reload with the proof
that contexts cannot disturb each other; version 2 built proof tripwires into the silicon;
and version 3 added the bus monitor with its own proofs. Each of these is described in its
section below, with what it found.

## Two implementations of one specification

The simulator (`sw/esciss`) and the RTL (`src/`) are separate implementations of the
specification. They share only the encoding tables in `isa/isa.yaml`. Execution semantics,
timing rules and the certificate checker are never generated from shared code, because a
shared decoder can make two models agree on the same bug. Questions that came up while
implementing each model are recorded with their resolution in
`sw/esciss/SPEC_QUESTIONS.md` and `src/ESC_CORE_SPEC_QUESTIONS.md`.

The co-simulation harness runs both models side by side and compares them on every tick.
It compares each commit (the context, its PC, the instruction, the outcome, and the
registers and flags after the commit) and the pin values and output enables. At the end of
the run it also compares all debug state, the execution digest, the capture state and all
256 tape words. A single differing bit anywhere fails the run.

Three kinds of program are run this way:

- **Random programs** come from a constrained generator (`test/cosim/progen.py`). It mixes
  the data, branch, timer, event, tape, capture and line unit instructions, subroutines and
  mailboxes, and deliberate faults (every ADMIT subcode and illegal words). Programs put
  contexts on either pin bank, point the capture unit at either bank, and collide tape
  loads and stores with capture drains. Each later feature has a generator of its own that
  adds it to a share of the seeds: a host that pushes mailbox words at random ticks and
  drains the transmit mailboxes at a random rate; one context that configures the line unit
  and issues receives, packets, CRC jobs, waits and OFF, while the other contexts try the
  same and fault; and MON and GETM from every context. A program longer than the 256-word
  program page is skipped and counted.
- **Firmware cases** run the shipped programs, each against independent bus models written
  from the protocol's specification, with host traffic where the program uses it.
- **Directed cases** aim at edges a random mix rarely reaches, such as a SEND into a full
  event queue or a mailbox push and pop at the edges the host uses.

| Final run | Programs | Compared | Mismatches |
| --- | ---: | ---: | ---: |
| Random programs (`make cosim`, 200 seeds), behavioural and standard-cell builds | 191 (9 too long for a page) | 19,850 commits on each build | 0 |
| Firmware cases with their bus models, behavioural and standard-cell builds | 74 | every commit and pin | 0 |
| Line unit alone against its model, every output every cycle (`make line-test`) | 19 runs (8 seeds, 11 profiles) | 3.8 million edges | 0 |
| Bus monitor alone against its model, every output every cycle (`make mon-test`) | random rules and pin traffic | 200,000 cycles | 0 |

The standard-cell build simulates the RTL with the PDK's own clock-gate and latch cells
(`CELLS=yes`), as it is hardened, so a fault in how a latch bank is written shows up in
simulation and not only in silicon.

Larger campaigns were run at earlier versions and still apply to the logic they exercised:

| Campaign | Programs | Commits | Mismatches |
| --- | ---: | ---: | ---: |
| Random programs, version 1.0 | 1,000 | 67,370 | 0 |
| Random programs with tape traffic, version 1.1 | 300 | 21,055 | 0 |
| Random programs with pin bank B, version 1.2 | 1,000 | 68,666 | 0 |
| Random programs with the host reading the tape during the run, version 1.3 | 60 | 7,598 words read | 0 |
| Firmware-shaped line unit programs, version 1.4 | 40 | 61,738 | 0 |
| Random programs with subroutines and mailboxes, version 1.6 | 964 | 111,491 | 0 |
| Line unit alone, version 1.6, clock-gated and plain flip-flop builds | 40 seeds | 8 million edges on each build | 0 |

Each new feature got its own generator, so that every other seed kept the program it had
before. A program with no bank B context, for example, is the same program as in version
1.1, so the earlier campaigns still cover what they covered.

The line unit has two more checks of its own. `make line-test` replays random stimulus
that follows the core's rules into `src/esc_line.v` alone and compares every output with
the simulator's model on every cycle. The stimulus covers configurations from USB-like,
CAN-like and random profiles, packets at offset clocks with jitter, glitches and stuffing
errors, noise, the unit's own pins read back, owner faults, stops, starts and tape writes
from elsewhere. Every job type and every end cause occurs. In the final run that included
555 cell-decoder receives, 489 external-clock receives and 77 with both-edge sampling. The
firmware-shaped programs (`progen.gen_lu_program`) run on the whole chip: receives of the
unit's own packets and of noise, packets at fresh and end-tick deadlines, owner tape loads
and stores that meet the unit's own tape cycles, and another context's traffic beside it.

## Formal proofs

The properties are written in the RTL, inside `ifdef FORMAL` blocks, and proved with
SymbiYosys. Two choices make the proofs cover every program rather than some. The
instruction memory is a free input, so the proofs hold whatever the firmware is. The tick
counter can start at any value, so the 32-bit wrap of the counter is reachable and covered.

Most properties are proved **unbounded**: they hold in every reachable state, after any
number of steps. Three methods are used. PDR (property-directed reachability) searches for
an invariant that implies the property; k-induction shows that k good steps always lead to a
good next step; and assume-guarantee splits a proof into parts that each rely only on what
another part proves.

| Property | Statement | Method |
| --- | --- | --- |
| **P2** | **Never late: no event queue head is ever past its deadline** | Unbounded, assume-guarantee (below) |
| P6 | An admitted event is strictly ahead of its commit tick and of the event already at the queue's tail | PDR |
| P7 | Fixed slots: a commit always belongs to the context that owns that slot | PDR |
| P8 | Every push meets the per-context assumptions; HALT completes only on an empty queue; faults happen only during a run; a commit comes only from a running context, never on a start or stop edge or one where it faults | PDR, with P2 for the lateness part (below) |
| B1–B9, D1–D3 | Values computed a cycle early equal their definitions on the cycle that uses them: the deadline tests, the decoded instruction and its operands, and each queue's next head | k-induction (below) |
| P1 | An open-drain pin is never driven high | PDR |
| P3 | A pin no context owns is never driven, in either bank | PDR, in two parts: a pin away from its released level has its output enable set, and an unowned pin never has one set |
| P5 | A faulted context drives none of its pins, in either bank | PDR, in the same two parts |
| P4 | Queue occupancy stays within bounds | PDR |
| H1–H4 | Per context: queued deadlines are ahead of now and strictly increasing, only a running context has queued events, and a queued DRIVE touches only owned pins | k-induction; H3 and H4 also by PDR, as lemmas for P5 |
| C1–C3 | The capture unit never writes outside its region, never stages more than two records, and records only during a run | PDR |
| C4 | While the host streams a capture, a drain never writes over a record the host has not acknowledged | PDR |
| L1 | The line unit claims only pins its owner owns; it is idle and unconfigured while stopped and once its owner has faulted; a CONFIG never completes at one of its owner's commit edges | PDR, as lemmas for P3, P5 and P8 |
| L2 | The line unit's pin writes and transmit bit, registered in the cycle before a boundary, equal their definitions at the boundary | k-induction, assume-guarantee with the core (below) |
| G1–G3 | A clock-gated register bank equals a flip-flop with an enable at every instant; the gated clock is never high while the clock is low, and rises only on a clock edge | k-induction, with an unconstrained clock |
| G4–G5 | A latch bank equals a flip-flop with an enable at every instant, because its input is always the value registered at the write edge | k-induction and PDR (below) |
| S1 | The configuration the core runs with (enabled contexts, entry points, pin ownership, open-drain pins, pin banks) never changes during a run | PDR, on the host interface |
| C6 | A program word written during a run (live reload) reaches the store intact and never meets a running context's fetch, and a free slot comes within four cycles | induction and PDR (below) |
| NI | Two chips that differ in every command and program word aimed at other contexts give context j the same commits, state and pins | k-induction with an invariant, and bounded from reset (below) |
| M1–M7 | The bus monitor's counts never wrap, a rule write that keeps the rules changes none, its record holds until a start or a clear, a violation with no record yet is recorded, and its latch banks hold what was written | k-induction (`formal/esc_mon.sby`) |
| TW | The twelve properties watched by the on-chip tripwires imply that the tripwires stay zero | induction (below) |

Every assertion of the core is also checked **bounded from reset**: from a real reset,
with every input free, no assertion fails in the first 20 steps. This catches a property
that holds in theory but is stated about states the chip never starts from, and it gives a
short counterexample when an edit breaks something.

**Assumptions.** The core proofs assume two things about their environment: a reset at the
first step, and S1. S1 is itself proved on the host interface (`formal/esc_host.sby`),
with every SPI pin and core-side input free and the core's run flag modelled exactly as the
core drives it. Removing the host's "only while stopped" check makes that proof fail. Every
other input (instructions, pins, host strobes, mailboxes) is unconstrained.

### Never late, for all time

P2 is the property the whole design rests on, and it is proved in two parts. The
per-context proof (`formal/esc_ctx.sby`) shows by k-induction that a context fed only
admitted pushes never has a queue head past its deadline. The core proof shows that every
push the core makes satisfies those assumptions (P6, P8). Together they cover every
reachable state, including the counter's wrap.

The split is drawn so that neither side assumes what the other proves. A late queue head is
itself a fault, so the statement "no fault on a commit" contains P2. The core therefore
proves that no *other* fault coincides with a commit, the per-context proof assumes only
that, and P2 completes the clause. The exception cannot hide a late head, because lateness
depends on state alone, never on the inputs of the same cycle. Stated as one clause, P8 had
not converged after seven hours of PDR; split this way, it proves in under a minute.

### Values decided a cycle early

To meet timing, the chip computes several values in the cycle before they are used and
keeps them in registers: the retire and late flags of each queue's head (D1, D2), each
queue's next head entry (D3), so that a retiring DRIVE reaches the pins straight from
flip-flops, and, for the context that commits next, the admission and timer tests (B1–B5),
the decoded instruction and its legality (B6, B7) and its two operands (B8, B9). Each is
stated as an equality with its definition on the state of the cycle that uses it, so every
other property reads exactly as it would without the early registers.

B1–B9 are proved by k-induction in a task of their own (`sby -f esc_core.sby ahead`). The
argument is short: a commit follows at least three edges of an uninterrupted run, because
the pipeline's valid bits clear on start and stop, and over those edges the registers are a
delayed copy of their definitions. Every other task assumes B1–B9 instead of proving them
again. Bit-level PDR could not use them otherwise: its invariant is a set of clauses over
the flip-flops, and "this register equals a 32-bit comparison of those registers" has no
compact clause form, even when it holds one edge after any state. Proved as PDR tasks they
had not converged after an hour. The assumption is sound because the `ahead` task proves
them under the same environment.

The task has teeth. Measuring the lead from the current tick instead of the next fails it
with a trace, and computing the tests for the context committing now instead of next fails
the induction step.

Span (version 3.1) changed what B7 decides. A branch target with bit 8 set, which names the
partner's page, is legal only for a context that spans, so the registered legality now
depends on the committing context's configuration as well as on the instruction. A tenth
lemma, B7s, states that the registered span flag equals that context's configuration bit,
and B7 is stated with it.

### The line unit

The line unit does a packet's bit-level work on its own, so it needs its own guarantees.
L1 says it stays in its lane: it touches only pins its owner owns, and it is idle once its
owner stops or faults. A CONFIG that turns out invalid faults its owner when it completes,
at an edge where another context may commit, so P8's commit clause is stated per context,
and L1's phase lemma shows that a CONFIG never completes at one of its owner's own commit
edges.

L2 covers the registered outputs. To fit in the clock period, the unit decides a
boundary's pin writes and transmit bit in the cycle before it, and L2 says those
registered values equal what the unit would compute at the boundary itself. Inside the
core proof, PDR did not converge on L2 (one task ran out of memory after an hour), so it is
proved in two parts, like P2. `formal/esc_line.sby` proves L2 for the unit alone by
k-induction, in seconds, assuming only the rules the core follows when it drives the unit's
inputs: one operation at an edge, from a running context in its own commit cycle, from the
owner (or a CONFIG taking a free unit), never while the unit is busy, SEND only with claimed
pins, and no fault that kills the unit at an operation's edge. The core proves those rules
(LI, two PDR tasks) with no line unit assertion assumed, so the argument is not circular.
The induction needs a few lemmas (a configuration in use is valid, the cell timer stays in
range, a packet's state before its first boundary, when a stream word arrives); without
them it starts from states the unit never reaches, such as a cell timer far beyond its
period. The unit is also checked bounded from reset to depth 40.

### Latch banks

Register banks that are rarely written are built from latches instead of flip-flops, which
saves area ([architecture](01-ARCHITECTURE.md)). The logic proofs see every
bank as a flip-flop with an enable, and two proofs tie the latch build to that model.
`formal/esc_lreg.sby` proves, with an unconstrained clock, that a latch bank equals the
flip-flop at every instant provided its input is the written value registered at the write
edge (G4). The core proof then shows that every bank's input is such a value (G5): one of
the shared write-data registers, or the host's mailbox word, which the host holds for the
cycle after a push (M1 on the host interface). G4 has teeth: a latch fed straight from the
write data, the design rejected in version 1.1, fails at the first step.

### Live reload and non-interference

The host can replace one context's program while the others keep running ([architecture](01-ARCHITECTURE.md)).
Two things must then be true. The new program words must arrive intact without ever
disturbing a running fetch, and the running contexts must not be able to tell that it
happened.

C6 covers the first. With every submodule cut free and the core's proved "a free slot
reads nothing" as the one assumption, `formal/esc_top.sby` proves by induction that the
grant never meets a fetch the core uses and writes the host's word at the host's address.
The host interface keeps a waiting word, its page and its address unchanged until the
grant (PDR), and the core proves that while any context is not running, a free slot comes
within four cycles.

NI covers the second. `formal/esc_ni_env.v` builds two complete cores that receive the same
inputs on everything a context j can see, but differ freely in every command aimed at the
other contexts and in the program words only those contexts fetch. The proof shows that j's
commits, state and pins are identical in both. It is proved by k-induction with an
invariant (`esc_ni_ind.sby kind`) and checked bounded from reset (`base`). The claim has a
stated scope: j itself uses none of the shared units (the capture unit, the line unit and
the bus monitor), since another context may own or configure those, and the global digest
and first-fault record are not compared. This proof shaped version 3 directly: with a bus
monitor that any context could read, the induction failed, and the monitor was given an
owner ([bus monitor](22-BUS-MONITOR.md)). Span widened what j fetches: a spanning j reads its
partner's page whenever its program counter's bit 8 is set. The invariant that ties each of
j's fetches to j's own words therefore covers the whole store address, page and word, in
both cores, and a spanning context's live reload counts both of its pages. The partner is
off by definition, so the words j fetches there are still words no other running context
uses.

### Proof tripwires

Twelve of the proved invariants are also watched in silicon. A 12-bit register `tw`
(`esc_core.v`, debug select 27) has one bit per invariant. Bit i sets one clock edge after
the first edge at which its invariant is seen broken, and holds until reset. The proofs say
every bit stays 0, so a set bit on a real chip names a broken invariant. The monitors are
registered first (`tw_q`), because they read the ends of the core's longest paths, and the
bank is a clock-gated `esc_greg` enabled only when a registered monitor fires, so in a
correct chip it is never clocked. Nothing but select 27 reads `tw`, so behaviour,
non-interference and the digest are untouched. The switch `TRIPWIRES = 0` in `project.v`
removes it.

| Bit | Fires when | Property |
| --- | --- | --- |
| 0 | a queue head is past its deadline | P2 never late |
| 1 | an open-drain pin drives high | P1 |
| 2 | a queue holds more than 4 entries | P4 |
| 3 | an unowned pin is driven | P3 |
| 4 | a faulted context drives one of its pins | P5 |
| 5 | a pin is driven while stopped | the P3/P5 strengthening |
| 6 | the line unit claims a pin its owner does not own | L1 |
| 7 | an unowned line unit claims, drives or releases a pin | `lu_bank` group |
| 8 | a commit on a restart edge | S3 (live reload) |
| 9 | a fetch in a cycle given to a host program write | S3 (live reload) |
| 10 | with no context paired, the fetch differs from the unpaired fetch | pair mode |
| 11 | the slot counter disagrees with the tick | B0 (`ahead`) |

Each monitor is its property's negation, written on the same nets. Three checks tie each
bit to its proof:

- The `tw` task of `esc_core.sby` proves, by induction, that the twelve properties as
  written imply `tw == 0`. A monitor that could fire while its property holds fails it.
- A unit test (`test_tripwires_teeth`) forces each monitor's source nets into the
  forbidden state. Its bit, and no other, must set, hold for 1,000 cycles and clear on
  reset. This is what kills a mutant of a monitor.
- The co-simulation reads select 27 at the end of every case against the simulator's 0, so
  a monitor that fires in normal operation fails every case.

`sw/rp2350/bringup.py` reads the tripwires after every bring-up stage and names any bit that
is set.

### Proofs that can fail

A proof that passes whatever the design does proves nothing. Two kinds of check guard
against that.

**Covers.** A cover task reaches the precondition of every property: capture overflow, a
full region, a ring wrap, a drain and a push with two records staged, the line unit driving
its pins and receiving, a CONFIG faulting its owner, a late violation recorded by the bus
monitor and a saturated count. A property whose precondition can never be reached would
hold vacuously, and its cover would fail.

**Planted bugs.** `formal/teeth.sh` (`make formal-teeth`) plants known bugs, one at a time,
in a copy of `src/`, and runs the proof that must catch each. A planted bug counts only if
its edit applied exactly once and the proof fails. In the final run all fifteen were caught:

| Planted bug | Proof | Caught |
| --- | --- | --- |
| L2: the boundary flag registered a cycle late | `esc_line.sby` | step 14 |
| L2: the pin value from the current cell instead of the next | `esc_line.sby` | step 23 |
| L2: the first bit from the wrong end of the prefix | `esc_line.sby` | step 13 |
| The line unit's one-hot owner register stuck at context 0 | `esc_line.sby` | step 3 |
| The CONFIG fault terms sampled at step 0 only | `esc_line.sby` | step 5 |
| NI: any context's stop holds every context's commit | `esc_ni_ind.sby` | step 6 |
| Live reload: two restart bugs found during version 1.7 put back | bounded core check | steps 4 and 5 |
| C6: a live write takes the store port at once | `esc_top.sby` | step 0 |
| C6: a page change moves the page under a waiting word | `esc_host.sby` | step 179 |
| C6: a free slot only ever in slot 0 (the four-cycle wait) | bounded core check | step 7 |
| M3: the monitor's keep bit ignored, so a clear rewrites a rule | `esc_mon.sby` | step 3 |
| M1: the interval counter starting at 0 (an off-by-one) | `esc_mon.sby` | step 3 |
| M7: the monitor's shared write word dropping a bit | `esc_mon.sby` | step 3 |
| The monitor's results readable by any context | `esc_ni_ind.sby` | induction fails |
| A MON from a context other than the monitor's owner taking effect | bounded core check | step 6 |

The clock-gate proof has its own: a naive `clk & en` gate fails G3 at the first step.

### Run time

`make formal` proves the core in parallel. `formal/split_core.py` makes one task per
assertion source line from the elaborated design and runs `JOBS` of them at once. A task
proves its assertions without the others as lemmas, so a property that needs a lemma can
take far longer split than together. Assertions tagged with the same `lemma-group` are
therefore proved in one task. The output-enable parts of P3 and P5 both rest on H4, for
example, and together with H3 and H4 they prove in two minutes, while alone P5's part did
not finish in two hours and P3's ran past the three-hour limit on a CI runner.

In the final CI run, on 4-vCPU runners, the 44 core tasks and the ten other proofs and
covers pass in one job of 91 minutes. The bounded check from reset, split into 52 tasks
covering 72 assertion lines to depth 20 (`formal/split_bmc.py`, the boolector solver, three
at a time), passes in 87 minutes in a second job; as one task it had not finished after
16.8 hours. A third job runs the tripwire, line unit and non-interference proofs (four
tasks) and then the planted bugs. The core proof has grown with the design, from 27 tasks
in version 1.4 to 39 in version 3 and 44 in version 3.1; as one task (`sby -f esc_core.sby pdr`) it took 34
minutes on one core for version 1.3.

## RTL to netlist equivalence

The proofs above are about the RTL, but the chip is built from the netlist that synthesis,
placement and routing produce. `make equiv` proves that the two are the same machine: the
signed-off netlist of version 3.1 (SHA-256 `1342fef1bc2f9f99…`, the same from the CI harden
and the local one) is sequentially equivalent to the RTL in `src/` for every input sequence
after reset.

Both designs are flattened and placed side by side in a **miter**, a circuit that feeds
them the same pins, the same SRAM read data and the same clock, and compares every chip
output and every input of the SRAM macros (address, data and enables, 336 bits in all) on
every step. ABC's `dprove` searches 19 steps for a difference, then matches the RTL's
registers with the netlist's by signal correspondence and closes the proof by induction.
Nothing is matched by name, so the netlist's renamed registers need no mapping, nor do the
registers that reset to one in the RTL and that synthesis builds as reset-to-zero
flip-flops with inverters. The RTL is read with the standard-cell define, as it is
hardened, so the latch banks are the same standard-cell latches in both designs and the
proof covers them as logic.

**Two steps.** Synthesis recodes three small state machines of the line unit (`sk`, `noted`
and `es0_q` in `esc_line.v`) as one-hot, so three of the netlist's registers have no single
partner in the RTL. Signal correspondence closed that gap on its own until version 1.6
added registers to the line unit; after that, the one-step proof of the netlist ended
undecided after 86 minutes, with no difference found. Applying only synthesis's FSM pass to
the RTL reproduced this, and the same flow without the FSM pass proved in 198 s, so the
recoding was the whole cause. The proof therefore takes two steps:

1. `esc_line` alone is sequentially equivalent to `esc_line` after synthesis's FSM pass
   (the same miter at module level: 22 outputs, 156 bits; 10 s);
2. the netlist is sequentially equivalent to the RTL with that `esc_line` in place
   (336 bits; 349 s).

A module that is equivalent at every output from reset, for every input sequence, changes
nothing around it (every flip-flop and latch bank resets asynchronously, so nothing it
outputs during reset is kept), so together the netlist is equivalent to the RTL.
`formal/equiv.sh` runs both steps, about six minutes in all. With a second RTL tree instead
of a netlist, as the mutation campaign uses it, it runs the one direct proof. Telling
synthesis not to recode would have kept one step, but it changes the netlist, and the
layout it gave was cut short by an OpenROAD clock-tree bug that disconnects a clock gate
(D-45).

**The check has teeth.** When the two-step proof was built, faults injected into the
version 1.6 netlist were found by the proof itself: the output stage of `uio_oe[0]` with two
of its gate's inputs swapped failed at the first compared step, and the MISO shift
register's bit 6 wired to bit 5's input failed at step 17. Step 1 has its own: the CONFIG
step counter made to skip a step on the recoded side fails at step 9.

Faults deep in the line unit need more than a search from reset can reach: the host must
configure a context and start it, the program must configure the line unit, and the unit
must send. In the whole-chip miter a bounded search did not reach the CONFIG step counter's
bit 1 wired to bit 0's input, or the bit position's bit 3 wired to bit 2's, within 75 steps.
So the same miter was also run as a simulation of one concrete session. Both SRAM macros were
modelled as memories holding a small program that configures the line unit from the tape and
sends 64 bits in a loop, and the host reset the chip, wrote the configuration and started
it. The CONFIG fault made the two copies differ a few cycles after START, on the tape
address; the bit-position fault did so eight bits into the first SEND. The unfaulted
netlist showed no difference over the same 1,000 cycles, while its pins changed 166 times.
A difference in the miter is a counterexample, so the proof cannot pass with either fault.

**Scope.** The SRAM macros are hard IP and are not compared, only everything around them.
Cell behaviour comes from the functions in the PDK's liberty file, so this is a statement
about logic; timing is the job of the sign-off analysis
([physical design](03-PHYSICAL-DESIGN.md)). The clock is ideal, one edge per step, so the
clock tree is checked as logic, not as delays. The clock-gate latches have no reset and
start at zero in both designs. Reset is held for the first steps and free afterwards, as is
every other input.

## Mutation testing

Proofs and tests are only as good as their power to notice a mistake. Mutation testing
measures that power directly: it plants one fault at a time in a copy of the RTL (a
**mutant**) and checks that some test fails. A mutant that no test kills is either harmless
or a hole in the tests.

`test/mutation/run_mutants.py` runs, for each mutant, the test suites that can see the
mutated file, cheapest first, until one fails: the line unit lockstep (for `esc_line.v`),
the lockstep co-simulation, the core unit tests, the host interface tests, the bus monitor
tests, the chip tests and the firmware-through-the-pins tests. The co-simulation runs the
firmware cases with bus models from the campaign's fixed list; running every firmware case
for every mutant would triple the campaign's time, past what a CI job allows. The suites
simulate the standard-cell build (`CELLS=yes`), so a mutant of a latch bank's input is
visible.

A mutant that every suite misses is then checked formally against the original RTL with
`formal/equiv.sh`. If no input sequence ever makes any chip output differ, it is
**equivalent**, and no test could kill it. If the proof finds a difference, the mutant is a
hole in the tests and gets a new test. If the proof does not decide within 30 minutes, the
mutant is read against the RTL: it is either a hole, or **excluded** with a written
argument in `run_mutants.py` for why it cannot change behaviour.

**The harness checks itself.** A mutation campaign can report success for the wrong
reasons, so the harness guards against each way that could happen:

- *A suite that cannot run.* Before any mutant is scored, every suite must pass on the
  unmutated RTL. A suite that fails for a tool or model problem would otherwise fail for
  every mutant and read as a kill.
- *A suite or proof that can only say yes.* Each suite must fail on a **canary**, a fault
  it is known to see, and the equivalence check must prove a rewrite that changes nothing
  and refute one that changes an output.
- *A kill for the wrong reason.* Every kill stores its **evidence**: the failing tests and
  the first assertion or mismatch. A **kill audit** reruns a sample of kills with the
  killing suite alone and reads that evidence.
- *A hang.* A suite still running after three times its own time on the unmutated RTL,
  measured in the same run on the same machine, counts as a kill, so a slow runner cannot
  turn a hang into a pass or a busy runner into a kill.
- *A lost mutant.* A final **reconcile** checks that every selected mutant has exactly one
  result, that every kill names a suite and that every exclusion has its argument. In CI
  the summary job runs it over the merged shards and fails on any miss.
- *A stale mutant.* A run first checks that every selected mutant still applies (a
  hand-written one's text found exactly once, a generated one's line unchanged), and stops
  before any suite if one does not.

The line unit lockstep also counts its own replayed edges and the stray tape reads it makes,
and fails a run that replays fewer edges than it generated.

**Where the mutants come from.** The campaign has 1,017 mutants across the whole RTL,
including the line unit, the bus monitor and the program store. 947 are generated
(`test/mutation/generate.py`). Every synthesisable line outside the verification-only
blocks is scanned for classic fault sites: a comparison or logic operator swapped, an
inversion dropped, an arithmetic operator swapped, a constant changed, an `if` condition
negated, or a register that keeps its value instead of taking an assignment. A site is
chosen when a hash of its file, operator and line text falls under a fixed rate (30%), so
editing one line changes only that line's mutants. A generate condition is not mutated,
because it chooses at elaboration what is built, so its mutant only swaps one equivalent
build for another or trips a build guard. `--rate 1` takes every site, 2,507 mutants in
all; that run is too long for CI and is made locally, on the lines that change.

The other 70 are written by hand, each aimed at one feature:

- *Eighteen at the values decided a cycle early.* Six at the queue head's retire flag:
  measured against the current tick instead of the next, the entry behind a retiring head
  never tested, an entry pushed into an empty queue with lead 1 never retiring, a test that
  ignores a retirement emptying the queue, an empty queue tested against a lead nobody
  pushed, and a stop or fault that leaves the flag set. Twelve at the core's registered
  tests: computed for the context committing now instead of next, the lead measured from
  the current tick, the queue tail or the window end taken from the wrong context, a timer
  exactly half the range ahead counted as reached, a deadline 2^31 − 1 ticks ahead refused,
  unsigned comparisons in place of the half-range ones (wrong only across the counter's
  wrap), the lead saturating at 17 bits or one below 0xFFFF, and a deadline equal to the
  queue tail or to the open window's end admitted.
- *Eight at pin bank B:* a DRIVE landing one pin off, a released output going low, a
  context reading the other bank, ownership checked against the wrong bank, a fault that
  keeps its bank B pins, inputs that never reach the synchronisers, a digest blind to bank
  B, and capture ignoring the bank bit.
- *Seven at streaming capture:* a drain writing over unacknowledged records, the hold point
  one record late, TAPE_ACK ignored, arming that keeps the old read pointer, a stream that
  stops at the limit instead of wrapping, a drain that loses the port to the host, and
  debug select 20 not showing the read pointer.
- *Eighteen at the latch banks, mailboxes and debug port:* ten at the latch banks (a bank
  latching another port's data, stale data at START, an entry one behind), three at the
  two-slot mailboxes (a ring head that never moves, a push into the slot the host is
  popping), and five at the shared debug read and its slot counter (a stale field, a wrong
  context, swapped fields, slots one context off).
- *Nineteen original ones:* inverted comparisons, off-by-one deadlines and pointers, a
  dropped polynomial term in the digest, a missing GETI wait, broken tape forwarding,
  capture rule changes and clock-gate enable errors.

**Result: 1,017 mutants, none surviving.**

| Outcome | Mutants |
| --- | ---: |
| Killed by the lockstep co-simulation | 434 |
| Killed by the line unit lockstep | 278 |
| Killed by the host interface tests | 96 |
| Killed by the core unit tests | 58 |
| Killed by the bus monitor tests | 24 |
| Killed by the chip tests | 11 |
| Killed by the firmware tests through the pins | 1 |
| Proved equivalent (`formal/equiv.sh`): no chip output can differ | 57 |
| Excluded, each with its written argument in `run_mutants.py` | 58 |

Most exclusions change a reset or start value that nothing reads before it is loaded again,
such as the core's operand registers, line unit fields read only in a mode whose entry sets
them, and the host engine's data and address registers. The rest change logic that acts only
when a queue head is past its deadline, which P2 proves never happens, tape reads whose data
nothing takes, and tie-offs of the SRAM macros' configuration pins that have no function in
any model. The CI campaign runs in 40 shards, dealt by each mutant's time in earlier runs
(`test/mutation/costs.json`) so that no shard reaches the job's time limit.

### What earlier campaigns found

Each campaign that left survivors showed a hole, and the holes and their fixes shaped the
harness and the tests as they are today.

- **Version 1.3: nothing read the chip between reset and its first START.** With the tests
  as they were, 57 mutants survived. A host can read every debug select over SPI at any
  time, so a wrong reset value is a visible bug; `test_reset_state` now reads all of them
  after reset. Other holes were edge cases random programs rarely reach (the Z flag of OR
  and AND when the operands' sum wraps to zero, a taken branch at PC 255, the digest after
  a STOP that releases driven pins), a power claim with no test (a one-context program
  reads the program store one cycle in four, and a stopped chip never), and the attention
  pin. Nine new tests killed 29 of the survivors. Writing the reset test also showed that
  the unit testbench never gave reset a falling edge, so a clock-gated register's model
  could stay unknown after reset in simulation.
- **Version 1.4: a simulator can fail every test for the wrong reason.** The first run on
  the standard-cell models scored 710 of 711 killed, which was false: the CI simulator,
  Icarus Verilog 12, gives X on the PDK's cell models, so every co-simulation failed within
  seconds. The baseline check now catches that, and CI builds Icarus 13. The real campaign
  then found two holes, closed with core unit tests: a line unit tape access at a stop
  edge, and a debug read within four cycles of a commit after a stop, which returned the
  value from before that commit.
- **Version 1.5: a timeout decided by the runner, not the test.** Compared mutant by
  mutant, 20 mutants changed category between two runs, every one through the pins suite
  running out of time: the fixed limit was only about 1.3 times what the suite took
  unmutated on a busy runner. Since then each suite's limit is three times its own baseline
  time.
- **Version 1.6: random stimulus too thin for rare configurations.** Seven mutants survived
  without being equivalent, each in a line unit case the lockstep reached too rarely. The
  harness had run that suite with 4 seeds where `make line-test` runs 8, and three cases
  need a configuration the random mix almost never builds. The harness now runs the suite
  exactly as `make line-test` does, and the suite ends with profiled runs in which every
  configuration comes from one profile, such as 10BASE-T's end-of-packet symbol or CAN's
  abort monitor sampling just before a boundary. The earlier seeds drive exactly the
  stimulus they drove before, so no earlier kill changed, and all seven were killed.
- **Version 3: two start values the proof could not decide.** The first version 3 run left
  two survivors whose equivalence proofs ran out of time: `s1` at reset and `cq` at a
  start. Both are reloaded long before anything reads them, and both are argued in
  `run_mutants.py`.
- **Version 3.1: a power bug that no output shows.** A mutant that enabled the tape
  macro on every cycle after reset passed every suite. It changes no output, only the
  macro's read energy, and the chip test for the memories' enables (D-29) watched the
  program store alone; in the 8 × 4 build the tape is the only macro of its kind, so
  nothing watched it. The test now checks both macros: never enabled on an idle cycle,
  enabled for the host's own reads, and neither enabled for the other's. It fails on that
  mutant in every one of 1,999 idle cycles.
- **Version 3.1: a debug select retargeted.** Span widened debug select 7 to carry the
  ninth bit of PC and link, and a curated mutant written for the old layout no longer
  applied, which the harness reports as an error rather than a kill. It was rewritten for
  the new layout. Span's longer program-counter line also gave the generator a new mutant,
  a first-fault record whose tick keeps its old value, which no test killed. The unit test
  that forces a late head to check the tripwires now also reads the first-fault record it
  leaves: the cause, the context and the tick of the edge that saw it.

## Timing certificates

The chip refuses any event it cannot apply on time. The certificate checker, `sw/escert`,
shows ahead of time that a program will never be refused. It treats the firmware as a
timed automaton and runs a zone analysis (with difference-bound matrices) over every path,
covering all five admission subcodes. For each context it returns one of three answers:

- **CHECKED**, with a lower bound on the scheduling **lead**, the number of ticks by which
  every event is queued ahead of its deadline;
- **REFUTED**, with a witness: inputs that the simulator replays into the fault;
- **NOT_PROVEN**, when neither could be shown. Such firmware carries no certificate.

All 82 shipped programs are CHECKED, for every input and without assumptions about bus
timing. The certified minimum leads, in ticks, per context:

| Lead | Contexts | Programs |
| ---: | ---: | --- |
| 1 | 7 | `can_tx_lu`, `eth_tx`, `spi_target`, `spi_tx`, `udp_tx`, `usb_hid` (context 0), `ws2812_lu` |
| 2 | 5 | `combo_uart_i2c` (context 0), `i2c_burst`, `i2c_ctrl`, `i2c_eeprom`, `oled_disp` |
| 3 | 3 | `can_tx`, `jtag_idcode`, `swd_idcode` |
| 4 | 6 | `cec`, `combo_two_banks` (context 1), `onewire`, `onewire_dev`, `ps2_device`, `sent_tx` (context 0) |
| 5 | 5 | `combo_two_banks` (context 0), `ws2812`, `ws2812_gen`, `xy2_100` (context 1), `xy2_100` (context 2) |
| 6 | 1 | `combo_two_banks` (context 3) |
| 7 | 4 | `combo_two_banks` (context 2), `combo_uart_i2c` (context 2), `pwm_audio`, `spi_ctrl` |
| 8 | 2 | `dshot_tx` (context 0), `vga_pong` (context 1) |
| 9 | 29 | `dcc_tx`, `dcf77_tx`, `dmx512_tx`, `em4100_tag`, `ev1527_tx`, `flexray_tx`, `j1850_vpw`, `jvc_tx`, `lin_tx`, `midi_tx`, `modbus_full`, `modbus_rtu`, `nec_tx`, `nec_tx_gen`, `oregon_tx`, `panasonic_tx`, `rc5_tx`, `rc6_tx`, `samsung_tx`, `seven_seg`, `sharp_tx`, `sirc_tx`, `swim_host`, `uart_tx`, `uart_tx_gen`, `usbpd_tx`, `wiegand_tx`, `ws2801_tx`, `xy2_100` (context 0) |
| 13 | 3 | `dali`, `lin_resp`, `qi_rx` |
| 21 | 2 | `avr_isp`, `sd_spi` |
| 28 | 3 | `max7219`, `nrf24_tx`, `spi_flash` |
| 40 | 1 | `stepper` |
| 57 | 2 | `servo`, `westminster` |
| 206 | 2 | `combo_uart_i2c` (context 1), `uart_rx` |
| 347 | 1 | `sbus_rx` |
| 692 | 1 | `morse` |
| 1,597 | 1 | `vga_pong` (context 0) |
| no queued events | 16 | `can_rx`, `dshot_tx` (context 1), `eth_rx`, `freq_meter`, `i2c_timing`, `logic_analyzer`, `nec_rx`, `oled_menu`, `oled_pong`, `oled_text`, `quadrature`, `sent_tx` (context 1), `spi_sniff`, `usb_hid` (context 1), `wiegand_rx`, `xy2_100` (context 3) |

A lead of 1 is enough: the event is admitted and applied on its tick. The line unit's
programs show 1 for a specific reason. A `SEND` that is late is dropped rather than faulted,
and where escert cannot rule out such a drop, the lead it proves for an admitted `SEND` is
only 1 tick. For line unit firmware escert also reports when replies leave: every `usb_hid`
reply has its deadline exactly 72 ticks after the end of the packet it answers, with a lead
of at least 4 ticks on every path where the host keeps to USB timing ([line unit](19-LINE-UNIT.md)).
`replay.asm` computes its deadlines from captured data, so instead of a certificate it is
checked by the replay tests and by the chip's admission rules.

**Certificates hold at other clocks.** Most programs take their timing from the clock
frequency `CLK_KHZ` and rebuild for any clock. Of the 80, 66 rebuild and certify at 33,
33.333, 40 and 50 MHz; four are tied to one clock by their protocol (10BASE-T transmit at
40 MHz, USB low speed at 24 MHz, VGA at its 25 MHz pixel clock) and refuse any other with an
`.assert`; three need a whole number of ticks per bit and certify at every clock that gives
one: `eth_rx` (a 100 ns bit, so 40 or 50 MHz), XY2-100 (40 to 50 MHz in 2 MHz steps) and
FlexRay at 2.5 Mbit/s (25 to 50 MHz in 2.5 MHz steps); and seven count in ticks or follow
another device's clock. Converting programs to `CLK_KHZ` found two
clocks rounded the fast way: JTAG's TCK and SWD's SWCLK half periods were rounded down, so at
33 MHz they ran 2% and 0.6% above their stated rates; they now round up. It also found
programs that held at 25 MHz by tick count alone. `i2c_ctrl` now waits for each scheduled
SCL fall before re-anchoring its bit timeline, so every edge it schedules follows the
previous one at any clock; `nec_tx` drains its queue before its repeat code's stop burst and
before a 1's long space, gaps of more than 2^16 ticks that escert could not otherwise bound,
and now certifies from 2.5 to 100 MHz; and `westminster`'s 0.65 s rest is added in two
halves to fit ADDT's 24 bits. Both NEC programs rebuilt for 33 and 50 MHz also run a frame
through the chip's pins, transmitter through an IR receiver model into the receiver, on the
RTL and on the gate-level netlist.

**The checker found real bugs.** It refuted the I2C controller's stuck-bus timeout, which
released the bus with a lead of −4, and a PS/2 frame that started under the previous
frame's queued events when scancodes arrived back to back. No test had reached either. In
both cases the chip would have refused the edge with an ADMIT fault rather than emitting it
late. Both are fixed, and the old versions are regression tests. Run at other clocks, it
found a third, in `i2c_eeprom` below 20 MHz ([real devices](#real-devices)).

**Register intervals.** escert tracks what it knows about each register as an interval. A
`MOVHI` on a register whose value is unknown gives an interval with its high half known,
and a `LINE` whose operand is known down to bits 31:16 to be a CRC job is a job of 0 to 255
ticks that writes no tape word. `can_rx` needs this: its CRC job covers exactly the frame's
length, which the frame's own DLC gives.

**Shared units.** A context that uses the line unit or the bus monitor must be the unit's
only user, because the first context to configure it owns it and the others fault. escert
flags a combined image in which two contexts use the line unit, or two use the monitor, as
OWNER. A context that reads the monitor without ever configuring it gets a note instead,
since its reads always return 0.

**The checker is tested, not trusted.** On random co-simulation programs, every CHECKED
context runs on the simulator without an admission fault and with telemetry at or above the
bound. That test found two checker bugs before the first release, and the chip's telemetry
caught a third on its first run. The planted timing bugs (below) found a fourth: escert let
a `SEND` wait for a running CONFIG, but the chip checks ownership first
([semantics](11-SEMANTICS.md), §6.2 and §7.7), so a `SEND`, RECV or CRC before the context's
first CONFIG completes faults instead. escert now tracks whether the unit is configured.

### Certificates against measurements

`test/cosim/proof_table.py` (`make proof-table`) sets every certified bound beside the leads
the simulator and the RTL actually measure, so each certificate is tested on every run
instead of trusted. A measured lead below its bound would be a bug in escert or in whatever
measured it, and fails the table. The RTL's leads come from the co-simulation itself: with
`COSIM_PROOF_OUT=file`, `test_cosim.py` records the RTL's telemetry and digest for each
firmware case.

In the final run, the 76 co-simulation cases with their bus models and host traffic have 94
contexts:

| | Contexts |
| --- | ---: |
| Queued events, so a lead was measured | 76 |
| Measured lead equal to the certified bound, to the tick | 59 |
| Measured lead above the bound (`uart_rx` 210 against 206, alone and as `combo_uart_i2c`'s second context; `ws2812` 8 against 5, `ws2812_gen` 6 against 5, `xy2_100`'s two axes 8 and 7 against 5, `morse` 908 against 692; `pwm_audio` 12 against 7; `i2c_burst` 6 against 2 in both its cases, and `oled_disp` 6 against 2 in both OLED Pong cases; on the line unit, `usb_hid` 12, `can_tx_lu` 17, `eth_tx` 13, `udp_tx` 13 and `ws2812_lu` 20, each against 1) | 17 |
| Measured lead below the bound | 0 |
| RTL lead or digest different from the simulator's | 0 |

Most bounds are exact, which makes the check sharp: a chip that queued one event a tick
closer to its deadline than escert's worst case would fail it.

The table also runs every shipped program on the simulator in the demo board's environment,
with nothing on the Pmods. That is the environment in which bring-up stage 5 checks the same
bounds on the chip and writes `proof.json` ([bring-up](16-BRING-UP.md#stage-5-firmware-digests)).
There, of 94 contexts in 82 programs, 72 queue events; 49 measure exactly their bound and
none is below it. The other 23 are looser (`i2c_ctrl` 33 against 2, for example), because
the inputs that produce the worst case, such as a device on the bus, are absent.

**Certificates compose.** Each context issues in barrel slots fixed by the configuration,
so another context can reach it only through data: pins, tape and mailboxes, which escert
already treats as unknown inputs. Firmware certified one context at a time therefore stays
certified when the contexts run together, as long as they own disjoint pins, none writes tape
words that another reads as constants, and each shared unit has one user. The two combined
images in the table certify and measure context by context. Building the table found one
imprecision here: a RET was sent to the return address of every CALL in the image, so in an
image with `nec_tx` and `nec_rx` together the receiver's RET reached the transmitter's
routine. A RET now returns only to the CALLs its own context reaches.

### Planted timing bugs

`test/mutation/fw_mutants.py` (`make fw-mutants`) plants the timing bugs firmware authors
make, one per mutant, in the source of each shipped program: a WAITT deleted, an ADDT
deleted, an ADDT's delay cut to a quarter, or a conditional branch inverted. Mutants are
chosen by hashing each program's source lines and the searches are seeded, so the same
firmware always gives the same table, and editing a program changes only its own mutants.
The whole campaign takes over a day on a laptop, so CI runs it in 24 shards
(`.github/workflows/fw-mutants.yaml`) on each change to the firmware or escert.

Each mutant is certified, and then attacked on the simulator, in a way that depends on the
answer:

- **A certified mutant** gets escert's randomised input search, 12 runs of random pins and
  host words, and then the program's own co-simulation benches: its bus models and host
  script, which drive the protocol much further than random inputs do. A few more benches
  are aimed at paths the co-simulation leaves alone: an AVR that never echoes, with the
  next command already waiting; a SWIM byte refused for its parity, and a SWIM line held
  low by a glitch while the host sends; a CEC frame for another device; a DALI query
  answered; a Modbus server whose receiver picks up noise while it replies. A fault in any
  of these would mean escert certified a program that can fault, and fails the run.
- **A mutant escert could not prove** (POSSIBLE) gets the same random search. If that finds
  nothing, three more searches follow: the benches; a host that never pauses, over a run
  five times as long on fresh seeds (a deleted WAITT may fault only once frames follow
  each other back to back); an aimed bench too long to give every mutant (a DCF77 word
  that comes more than two seconds late, 54 million ticks); and finally the benches that set
  the program's clock or tape,
  each building the mutant its own way. A fault in that last search is reported beside
  NOT_PROVEN, never as REFUTED, because a different clock makes a different program. If
  escert certifies that build and it faults anyway, the run fails.
- **A crash** of the checker or the simulator on a mutant is reported as ERROR and fails
  the run, without losing the other results.

On the final tree the campaign planted 729 mutants in 78 programs: every program but the
logic analyser, which has no mutation sites, the two combined images, whose programs are
mutated on their own, and `pwm_audio`, written for the [DOOM console](25-DOOM-CONSOLE.md)
after the campaign. It has had a shorter run of its own, three mutants per operator: of 12,
escert refuted 1 and certified 11, none of which faults, and the bus models see 10 of the 11
break the sound. The four OLED Pong programs are mutated inside
their four-page image, beside the unmutated other three, so the search attacks the whole
system.

| escert's answer | Mutants |
| --- | ---: |
| REFUTED: a fault, reproduced on the simulator with concrete inputs | 178 |
| NOT_PROVEN: a fault not excluded, and none found by the searches | 18 |
| CHECKED: no admission fault for any input | 533 |
| CHECKED, yet a search or a bench made it fault | **0** |

Of the 178 refuted, 148 fell to the random search, 14 to the long search whose host never
pauses and 16 to a bench, 11 of those to the aimed benches.

**NOT_PROVEN is never counted as safe.** Of the 18, 9 are located exactly: escert holds a
state at the flagged instruction in which every deadline breaks a rule (escert reports the
first such state, not every state there). Seven of these fault on their bench built at its
own clock or tape, another program than the one judged. The eighth is escert being
cautious: `usbpd_tx` with the first half of a 1 bit cut to a quarter still queues its
edges faster than the line sends them, so the queue never drains as in escert's state,
and on the bench the flagged instruction always has a lead of 98 ticks or more. No search
or bench made the ninth, in `modbus_full`, fault. Five more fault on their bench built at that
bench's clock. The other four, one of them also in `modbus_full`, have a bound that straddles a
rule and no fault found anywhere; they are escert being cautious or faults on a path no search
drove, and the campaign does not tell which.

The aimed benches came from this list. In the earlier run, a mutant escert located exactly
but no search reached marked a path no bench drove: an error, or an answer. A bench aimed
at each refuted five of them at the default build. Running the same mutants through the
unit tests found a test that passed with the firmware faulted: a CEC follower that stops
leaves a frame unacknowledged just as a correct one does when the frame is not its own.
The test now checks the fault register as well.

**Timing is not function.** A CHECKED mutant is a change that keeps every deadline
admissible, which escert is right to accept: it proves timing, not protocol correctness.
Most inverted branches are of this kind, because escert already follows both directions of
a branch whose flag depends on inputs, so inverting one leaves the same timed paths. For a
certified mutant with a bench, the campaign therefore also records the bench's own
protocol check. Of the 256 certified mutants with a bench, the bus models see the
protocol fail in 121: a frame with a bad CRC, a USB device that never enumerates, a
frame never sent. Every unmutated program passes its benches, so each failure is the
mutant's doing. escert and the bus models catch different bugs, and the campaign shows both.

The first run, on version 1.6, found the one CHECKED mutant that faulted: `udp_tx` without
the ADDT before its first `GETL`, the bug in escert described above. With escert fixed,
that mutant is REFUTED. The campaign has also measured escert's precision. A frame loop
that counted its frames against a tape word made escert forget the bit loop's count inside
it, so a 20-bit frame could look like a 1-bit one and the queue seemed to drain; escert now
keeps loop counts longer than register constants, and that program's bound is exact (8,
as the simulator measures). In programs of many counted loops that keeps too many states,
which three mutants of `eth_tx` and `udp_tx` showed by running out of them; escert then
analyses again with the earlier keys, and those mutants certify. A random test of such frame
loops, at the usual key limits and at tiny ones, checks that every certificate holds on the
simulator.

A later run found a second bug in escert, and this one could certify a program that faults.
GETI writes Z from the bits its SAMPLEs read, but escert carried past it the Z it knew
before, so where Z was known just ahead of a GETI (after a MOVI, say, or on the match path
of a WAITP, which escert now tells from the timeout), the branch after the GETI was
followed one way only. The guard against incomplete analysis caught it: with `dali`'s test
of the start bit's first half inverted, escert never reached the receiver's bit loop and
said NOT_PROVEN, while on the simulator a short glitch on the bus runs straight into it.
With GETI fixed to leave Z unknown, every shipped program certifies with the same bounds as
before, and that mutant is CHECKED again. The guard notices a lost path only when the path
holds an event site no other path reaches, so the test kept for this bug has none: seven
instructions whose two paths meet at one EMIT, which the old escert certified and the
simulator faults at once.

That bug sent escert's instruction cases back through a check against the specification,
looking for its kind: state written after it was handed on to the next instruction, an
instruction that writes Z or a register with no case to say so, an instruction with no case
at all. It found one more. GETM, which reads the bus monitor or the line unit, had no case,
so the register it writes kept the value escert knew before; five instructions that load a
delay, overwrite it with GETM and wait by it were certified, and the simulator faults them
at once. GETM now leaves its register unknown. Every other instruction checked out, and
every shipped program certifies with the same bounds after both fixes. Each bug has its
test.

The 12 mutants left without an answer in the run on version 3.0 were each followed, to a
path that faults or to escert's caution. That, and the ping bench's program `eth_trx`
([FPGA bench](../fpga/tangprimer25k/README.md)), changed escert's precision in four places:

- **Widening.** At a loop head escert joins the states that come back to it, and after a
  few passes widens the join: a bound that keeps moving is pushed out to the next of a fixed
  list of thresholds instead of being followed pass by pass. The list stopped at 2^16. In
  `servo` with its channel loop's exit inverted, only a full queue holds the loop back, so
  its deadlines run at most two 22 ms frames ahead (about 1.1 million ticks); the next
  threshold was 2^31 - 1, past the half-range horizon, so the lead looked like a deadline
  in the past.
  The list now has landmarks at 2^20, 2^24 and 2^28 as well. Adding every power of two
  instead tripled the states of `xy2_100`.
- **Counted loops in shared routines.** A loop whose counter escert knows has a key per
  pass, so a state comes back to the same key only from outside the loop, from a routine
  called at several sites. Widening such a head after four visits, as for other heads,
  lost `qi_rx`'s bit timing with one ADDT removed; it now waits for eight.
- **Constants at the key limit.** When an instruction has collected too many keys, escert
  forgets register constants so that a frame counter cannot multiply them. It now keeps a
  constant that every key there already holds, which cannot multiply anything: in
  `eth_trx`, the operand that a `MOVI` loads just before a `LINE` stays known.
- **Z at the coarse key.** Past a second limit escert drops the loop counter from the key
  too, and it used to drop Z with it. In `eth_trx`, a state in which a `GETL` saw a
  reception end, with the timer at that end, then joined the states that timed out and took
  their branch as well, to a link pulse scheduled from the stale end. Z has three values at
  most, so it is now kept.

With these, every shipped program certifies with the same bounds as before except `morse`,
whose bound rose from 148 to 692 ticks (the simulator measures 908), and the largest
analysis (`udp_tx`) takes 42,995 states. Four of the 12 now certify: `servo` and `qi_rx`
above, and `vga_pong` and `dali`, each with a WAITT deleted. The campaign's searches and
benches attack each like any other certified mutant, and none of them faults. Five more
fault on benches aimed at them: `avr_isp` with a WAITT deleted, when the next command
arrives while the last RESET pulse is still queued; `swim_host` and `modbus_rtu`, each
with a WAITT deleted, when the line glitches; and `dcf77_tx` with either of two branches
inverted, when a word comes more than two seconds late. The last three, `ps2_device`,
`onewire_dev` and `can_tx`, each with a WAITT deleted, stay NOT_PROVEN: no search or bench
has made them fault.

## Protocol coverage

Each program runs against an independent bus model in `sw/escmodels` on the simulator
(`sw/tests/test_firmware.py`). All but two also run in lockstep on the RTL (`test/cosim`),
in the 76 firmware cases: `vga_pong` runs through the chip's pins on the RTL and the final
netlist instead, and the 14-second `westminster` tune only on the simulator.

| Protocol | Firmware | Checked against |
| --- | --- | --- |
| UART 8N1 at 115,200 baud (the rate is one constant) | `uart_tx`, `uart_rx` | Receiver and transmitter models; all 256 byte values, back-to-back frames, ±2% peer baud error; `uart_rx` on a real STM32's output at 1,200 to 921,600 baud (`test_real_devices.py`) |
| SPI controller, modes 0 to 3 | `spi_ctrl` | SPI target model (all four modes on the simulator, mode 3 on the RTL) |
| I2C controller, Fast mode, clock stretching, address NACK | `i2c_ctrl` | I2C target model that flags Fast-mode timing violations |
| I2C bulk writer, Fast mode, clock stretching | `i2c_burst` | SSD1306 display model written from the datasheet, with the UM10204 Fast-mode timing checks |
| I2C bulk writer's stream mode: a frame taken from the mailbox while the chip runs | `i2c_burst` | The same model; a host slower than the bus (SCL held low, never a short phase), a stream of no words, a NACK mid-stream with the next command still served |
| PWM sound: 8-bit samples at 11,025 a second, eight periods of an 88 kHz carrier each | `pwm_audio` | A receiver written from the format that decodes every sample and checks every rise against its schedule; the extremes 0 and 255; a ring that runs dry plays silence and counts it |
| I2C 24C02 EEPROM target, Standard and Fast mode | `i2c_eeprom` | Scripted controller that checks the target's timing; page roll-over |
| I2C bus timing checker on the bus monitor ([bus monitor](22-BUS-MONITOR.md)) | `i2c_timing` | A bus model that plants timing faults at chosen clock pulses: every report must be a violation by the waveform's own edges, with its rule, kind and interval, and pulses exactly at each limit pass |
| WS2812 | `ws2812` | LED chain model |
| 1-Wire, open-drain with timed sampling | `onewire` | 1-Wire device model (ROM read) |
| CAN 2.0A transmitter, 250 kbit/s | `can_tx` | Receiver model that destuffs, checks CRC-15 and acknowledges |
| PS/2 device | `ps2_device` | PS/2 host model, back-to-back scancodes, and a host that inhibits after every frame as a real PC does (50 µs of CLK high before each frame) |
| JTAG IDCODE read | `jtag_idcode` | JTAG TAP model, back-to-back requests |
| SPI target: a 256-byte 25xx-style memory (READ, WRITE) in the tape, modes 0 and 3, up to 416 kHz | `spi_target` | SPI controller model that checks MISO setup and hold at every sampling edge; every edge alignment at the rated period, CS_n rising after every bit, back-to-back frames |
| NEC infrared transmitter with 38 kHz carrier (repeat codes, extended address) and receiver | `nec_tx`, `nec_rx` | Receiver model that demodulates the carrier and checks frequency, duty and the Linux decoder's timing windows; remote-control model with ±10% clock error; transmitter → IR receiver module model → receiver loopback |
| ARM Serial Wire Debug host: JTAG-to-SWD switch, wake from dormant and TARGETSEL (SWD protocol version 2, multi-drop), DPIDR read, DP and AP reads and writes | `swd_idcode` | SW-DP target model from ADIv5.2 (IHI 0031G): WAIT retries, FAULT and ABORT, read parity errors, no-response back-off, turnaround contention; dormant state and multi-drop selection, with RP2040's three DPs on one bus (each selected in turn, and no answer without the wake or with a TARGETSEL that names none) |
| UART and I2C concurrently | `combo_uart_i2c` | Both models at once, on separate contexts |
| PS/2 and JTAG on bank A, SPI and UART on bank B, concurrently | `combo_two_banks` | All four models at once; the bank B ones see only the split pins |
| Westminster Quarters, the hour chime of Big Ben, on a buzzer on bank B | `westminster` | Note decoder at a hundredth of real time: all 17 notes in order, each at its pitch (within 0.2%) and length, and every silence to the tick |
| Logic analyser and replay | `logic_analyzer`, `replay` | Tick-exact replay of a captured UART trace; a too-dense trace is refused; streaming capture past the tape's size on the simulator, the RTL (with a host reading during the run) and through the chip's pins |
| UART and MIDI OUT from timing tables | `uart_tx_gen`, `midi_tx` | UART receiver model at 115,200 and 31,250 baud |
| DMX512 from a timing table | `dmx512_tx` | DMX receiver model with the ANSI E1.11 receiver limits: start code and 511 slots, break and mark-after-break times |
| WS2812 from a timing table, colours in the tape | `ws2812_gen` | LED chain model with datasheet timing windows |
| NEC infrared from a timing table | `nec_tx_gen` | NEC receiver model: carrier, pulse counts, every burst position |
| Seven-segment animation on the demo board's display | `seven_seg` | Every frame on bank B, in order, to the tick |
| VGA Pong, 640x480 at 60 Hz, on bank B ([VGA Pong](21-VGA-PONG.md)) | `vga_pong` | A monitor's view of the pins: VESA sync timing, every pixel of two frames against a model of the game, black blanking, 14 game steps against the model and stated outcomes; the first frame through the chip's pins on the RTL and the final netlist, with the telemetry equal to the certificate |
| Pong on a 128x64 SSD1306 OLED over I2C, four contexts with one program page each: display engine, game, text and menu | `oled_disp`, `oled_pong`, `oled_text`, `oled_menu` | SSD1306 model with the Fast-mode timing checks; every picture, the whole display RAM, against a model that draws the screens from the firmware's rules and plays the game |
| CAN 2.0A and 2.0B receiver on the line unit: standard, extended and remote frames, CRC-15 checked by the line unit's CRC engine | `can_rx` | Bus model playing frames back to back from nodes 0.5% slow or fast; bad CRCs, frames cut by an error flag, DLC over 8, 500 kbit/s |
| DShot600 for four ESCs from one context, 600 kbit/s exactly (42, 42, 41 ticks per bit), and bidirectional DShot | `dshot_tx` | ESC models that check every pulse width, bit period and CRC; frame period to the tick through host updates; at 24 MHz |
| SENT (SAE J2716) sensor, with or without a pause pulse | `sent_tx` | Receiver model that calibrates from the sync pulse and checks the recommended CRC-4 (and the CRC against polynomial division); frames back to back to the tick, values changing mid-run never mixing frames; the community `sae_j2716_sent` decoder, with a pause pulse |
| HDMI-CEC adapter: follower and initiator | `cec` | CEC device model with the Linux cec-pin timing windows: acknowledgement of its own address only, broadcast never driven, sending with arbitration (lost to a simultaneous start, then retried), retries after a refusal, a host-set logical address |
| DALI control gear (IEC 62386-102 commands and queries) | `dali` | Controller model at nominal and ±10% bit timing: levels, OFF, RECALL, queries answered 7.5 ms after the forward frame on the half-bit grid; other addresses, groups, broadcast |
| USB 1.1 low-speed HID keyboard on the line unit | `usb_hid` | Reactive USB host model from the USB 2.0 text that checks every device packet and its turnaround: enumeration, lost and corrupted handshakes, corrupt tokens and data, bus resets at every stage, host clock ±5%, hostile traffic (74 tests); and the Linux kernel itself, which enumerates the simulated chip over USB/IP and receives typed text |
| CAN 2.0A at 1 Mbit/s on the line unit | `can_tx_lu` | CAN bus model (destuffs, checks the CRC-15, acknowledges) at 40 MHz |
| 10BASE-T Ethernet transmitter: link pulses, preamble, SFD, the CRC-32 frame check sequence | `eth_tx` | The pins decoded as 802.3 Manchester, the frame check sequence against zlib, link pulses 16 ms apart while idle |
| 10BASE-T Ethernet receiver on the line unit's cell decoder, with both-edge sampling | `eth_rx` | 802.3 frames from a source model whose clock is 100 ppm off either way, with every edge moved by up to 10 ns; each frame received byte for byte, its frame check sequence judged as zlib judges it; at 50 MHz |
| UDP over IPv4 over 10BASE-T | `udp_tx` | What a receiving stack checks: the frame check sequence (zlib), the IPv4 header and its checksum, the UDP header, the payload |
| LIN commander frames at 19,200 baud | `lin_tx` | Responder-side model: break and delimiter lengths, sync, identifier parity, enhanced and classic checksums, every bit |
| Modbus RTU server: functions 03, 06 and 16, exceptions 01 to 03 | `modbus_rtu` | Client bus model: reads and writes, broadcast, the longest frames, bad frames dropped, gaps between characters, parity modes, a slow baud rate and clock error |
| S.BUS receiver: 16 channels, two digital channels, frame-lost and failsafe, decoded into the tape | `sbus_rx` | S.BUS sender model: the flags and the S.BUS2 footer, bad frames dropped, not inverted, back to back |
| Eight RC servo pulses per 22 ms frame, widths from the host in microseconds | `servo` | Every width and the frame period, at 8 and 25 MHz |
| Write-only SPI at line rate on the line unit, every mode, 10 Mbit/s at 40 MHz | `spi_tx` | SPI target model: SCK's idle level at CS_n edges, MOSI set up before each sampling edge, no SCK edge outside a frame |
| SPI sniffer on the line unit's external-clock receiver, the chip only listening | `spi_sniff` | SPI controller model writing to a target, in every mode: each frame bit for bit from MOSI, frames that open with a 1, a clock of 2 ticks a bit, pauses inside a frame, a long quiet |
| WS2812 from one line-unit SEND | `ws2812_lu` | LED chain model: every pulse against the datasheet |
| Frequency, period and duty by reciprocal counting on the capture unit | `freq_meter` | Exact to one tick per gate; resolution below one tick, a gate across an epoch boundary, a signal too fast for it flagged |
| Quadrature encoder, x4, from capture records | `quadrature` | Forward and back, bursts and the sustained rate, an illegal step and an overrun flagged |
| Sony SIRC, RC5, JVC and EV1527 remotes, NMRA DCC and Wiegand 26 output, from timing tables ([Protocols as data](18-PROTOCOLS-AS-DATA.md)) | `sirc_tx`, `rc5_tx`, `jvc_tx`, `ev1527_tx`, `dcc_tx`, `wiegand_tx` | A receiver model for each; lockstep |
| Philips RC-6 mode 0 infrared from a timing table | `rc6_tx` | RC-6 receiver model and sigrok's `ir_rc6` decoder; lockstep at 8 MHz |
| LIN 2.x responder: break and sync, identifier parity, published IDs answered with the enhanced or classic checksum computed on the chip | `lin_resp` | Commander and frame receiver models (LIN 2.x worked values, a 10-bit break and a bad parity not answered, a commander 2% fast or slow, the response space) and sigrok's `lin` decoder |
| EM4100 RFID tag: Manchester at RF/64, header, row and column parity | `em4100_tag` | Reader model and a frame worked by hand from the datasheet; every edge on the half-bit grid; sigrok's `em4100` decoder |
| Wiegand panel receiver, up to 64 bits | `wiegand_rx` | Reader model at the interface's limits (20 and 100 µs pulses, 200 µs and 20 ms intervals), a stuck line flagged, and the chip's own `wiegand_tx` on another context |
| USB Power Delivery transmitter: BMC, 4b5b, the CRC-32 computed on the chip | `usbpd_tx` | CC receiver model (CRC-32 checked with zlib), bit rate and the end of the line; sigrok's `usb_power_delivery` decoder |
| DCF77 time code: minute frames with the parities computed on the chip | `dcf77_tx` | Receiver model; every second a whole second apart; a missing frame repeated with an IRQ; sigrok's `dcf77` decoder |
| SD card host in SPI mode: CMD0, CMD8, ACMD41, CMD58, CMD17, CRC7 and CRC-16 on the chip | `sd_spi` | Card model (start-up, OCR, blocks, a CRC-16 error, a card that stays idle) and sigrok's `sdcard_spi` decoder |
| 1-Wire device in the DS18B20 role: presence, Read, Match, Skip and Search ROM, scratchpad, both CRC-8s on the chip | `onewire_dev` | Bus master model at AN126 timing and at the device's limits, with AN187's search; sigrok's `onewire_link` and `onewire_network` decoders |
| AVR in-system programmer (ATmega328P serial downloading) | `avr_isp` | ATmega328P model written from the datasheet (Programming Enable echo, a target out of step, page load, write, BUSY poll) and sigrok's `avr_isp` decoder |
| Qi power receiver communications (Baseline Protocol): differential bi-phase, SIG, ID, CFG, CE, RP8, EPT | `qi_rx` | Power Transmitter model written from the specification: every bit, parity and checksum, tWake, tStart, CE interval, the spacing after a CE, loss of the Power Signal; sigrok's `qi` decoder |
| SPI NOR flash programmer: JEDEC ID, Read Data, Page Program, Sector Erase, Write Enable checked, BUSY polled | `spi_flash` | W25Q80DV model written from its datasheet (WEL, page wrap, /CS on a byte boundary, nothing while BUSY), checked against a real flash under flashrom, and sigrok's `spiflash` decoder |
| MAX7219 LED display driver, chains of up to 8, numbers divided into decimal on the chip | `max7219` | MAX7219 chain model written from the datasheet (packet alignment, tCH, tCL, tCP, tDS, tCSW, tLDCK, code B) and sigrok's `max7219` decoder |
| Stepper motion: STEP and DIR with a linear speed ramp (AVR446) computed on the chip | `stepper` | Step/direction model with the A4988's logic timing; the ramp checked against physics (the acceleration from v² = 2As, to 2%); sigrok's `stepper_motor` decoder |
| nRF24L01+ transmitter (Enhanced ShockBurst: auto-acknowledge, retransmits, MAX_RT) | `nrf24_tx` | Radio model written from the Product Specification (SPI commands, registers, Thce, Tpd2stby, Tpece2csn, time on air, ARD and ARC) and sigrok's `nrf24l01` decoder |
| Morse code keyer (ITU-R M.1677-1) | `morse` | Listener written from the Recommendation: every key-down and gap in dots, to 1%; sigrok's `morse` decoder |
| Samsung32, Sharp and Panasonic (Kaseikyo) infrared from timing tables; Sharp's frame and its inverted partner 40 ms later; Panasonic's vendor code and its parity nibble written in the table, the host word holding the rest | `samsung_tx`, `sharp_tx`, `panasonic_tx` | A receiver model for each, written from the datasheet or the IRMP and IRremote figures: carrier, every burst and space, the data byte's complement, Sharp's pair, both Kaseikyo parities; a planted wrong vendor bit seen; IRMP; lockstep at 2 MHz |
| WS2801 LED chain, clock and data, from a timing table | `ws2801_tx` | Chain model: every bit sampled on CKI's rising edge, 24 bits an LED, the 500 µs latch; at 25, 33 and 50 MHz; sigrok's `spi` and `rgb_led_spi` |
| XY2-100 galvo scanner: clock and sync, the X and Y streams and a frame builder in four contexts, parity on the chip | `xy2_100` | Scanner model from the protocol's timing: every 20-bit frame of both axes and its parity; a host figure with every parity; planted parity and late-data bugs caught; sigrok's `xy2-100`; at every clock from 40 to 50 MHz in 2 MHz steps |
| SAE J1850 VPW (the GM OBD-II bus): pulse widths, start and end of frame, the CRC-8 computed on the chip from a nibble table | `j1850_vpw` | Receiver model from SAE J1850: every pulse's width and polarity, SOF, EOD, the 11-byte limit, the full interframe space before a waiting frame, the CRC against the catalogue's CRC-8/SAE-J1850; planted CRC, pulse and spacing bugs caught; sigrok's `sae_j1850_vpw`; lockstep at 4 MHz |
| Oregon Scientific v2.1 sensor (THGR122NX): Manchester with doubled bits, the reading in BCD, the checksum computed on the chip, the message sent twice | `oregon_tx` | Weather-station model: preamble, sync, every doubled bit, the checksum, negative temperatures and low battery; planted checksum and doubling bugs caught; rtl_433 and sigrok's `ook_oregon`; lockstep at 200 kHz |
| FlexRay static frame at 2.5 Mbit/s: TSS, FSS, a BSS before every byte, header CRC-11 and frame CRC-24 computed on the chip | `flexray_tx` | Receiver model with the specification's bit strobing: both CRCs (checked against the specification's examples), the sync and startup indicators, payload lengths; planted CRC, FSS and BSS bugs caught; sigrok's `flexray`; at every clock from 25 to 50 MHz in 2.5 MHz steps |
| STM8 SWIM debug host: entry sequence, synchronisation, SWIM_CSR written, memory read on request | `swim_host` | STM8 target model from ST's UM0470: the entry sequence, every host bit's lengths, headers, parity and acknowledges, reads up to 8 bytes, no target; planted parity, entry and bit-length bugs caught; sigrok's `swim`; at 33 and 50 MHz |

## Real devices

The bus models and the firmware are written from specifications, and so is everything
that checks them. A shared misreading of a specification would pass every test above.
`sw/tests/test_real_devices.py` therefore meets them with devices we did not design:
logic-analyser recordings from [sigrok-dumps](https://github.com/sigrokproject/sigrok-dumps)
(each file and its source are listed in `sw/tests/data/sigrok/README.md`), played onto the
pins at the chip's tick. Recordings start mid-session and carry real lines: noise, edges
moved by demodulators and by sampling, devices outside their specifications. The models that
judge our firmware's own output are strict; each has an opt-in mode for a real line (`hunt`
for Qi, `real_line` for USB PD, EM4100 and DCF77, `inhibits` for a PS/2 host's own CLK
holds), and the strict defaults are unchanged.

| Device | Chip side | Compared |
| --- | --- | --- |
| STM32F103 (Olimex STM32-H103), "Hello World!" at 1,200 to 921,600 baud | `uart_rx`, 1 to 50 MHz | Every byte, no framing error |
| MTK3339 GPS module, NMEA at 9,600 baud | `uart_rx` | Every sentence's checksum; the same bytes as the receiver model |
| PC (minimalmodbus) polling a Brainchild IO-16DO, Modbus RTU 19,200 8E1 | `modbus_rtu` in the module's place | Replies to functions 3, 6 and 16 byte for byte the module's; exception 01 to the other five |
| Cypress FX2 booting from a 24LC02B (Hantek 6022BE) | `i2c_eeprom` in the EEPROM's place | Every bit on the bus but one (below); SCL never stretched past the FX2's |
| Tektronix TDS 744A internal bus, two X24C02 EEPROMs | `i2c_eeprom` as 0x50, beside the real 0x51 | All 14 transactions bit for bit, at 500 kHz |
| An STM32F103 1-Wire master with two DS18B20s | `onewire_dev` as one sensor, beside the real other | Its presence at every reset, its half of four Search ROMs, both its scratchpad reads with the CRC-8 the chip computes; silent while the other is read (a run without the chip fails the same checks) |
| Microchip MCP2515DM-BM, CAN at 125 kbit/s, 25% and 100% bus load | `can_rx` | Every frame, standard and extended, each passing its CRC-15 |
| NEC remotes (Joy-IT, a ceiling light's 16-bit address, Hama as Matsui) | `nec_rx` | The frames and repeat codes esclearn reads from the same recording |
| 13.56 MHz Wiegand reader | `wiegand_rx` | Both 34-bit frames and their parities |
| Sony TV with Denon and Yamaha amplifiers, HDMI-CEC | `cec` | The 13 frames as the CEC model reads them |
| Rako RSR-DALI controller and a ballast | `dali` | Its 9 forward frames |
| Nexus 5 and a no-name receiver on Qi transmitters | Qi transmitter model | 24 packets, every timing rule (Nexus 5); every packet refused for the no-name one, which keys its bits at 545 µs, outside 2 kHz ±4% |
| MacBook, Pixel, ThinkPad and a ZY12PDS sink with chargers and adapters, USB PD | CC receiver model | 492 messages, every CRC-32 and EOP; sigrok's decoder reads the same, but for one GoodCRC whose edges are skewed |
| An xmore 512 MB SD card and another card read over SPI | SD card model | Its CRC7 on CMD0 and on the CSD, and the CRC-16 of the CSD and of four 512-byte blocks |
| flashrom reading, writing and erasing a Macronix MX25L1605D (FT2232H) | W25Q80DV flash model | All 168 pages read, byte for byte; every status read during 84 page programs and 4 sector erases; the programmed pages and the erased sectors |
| A PS/2 keyboard typing into a PC, passive and inhibiting after every frame | `ps2_device` before a host inhibiting as that PC does; PS/2 host model | The 18 make and break codes, every timing rule (the model); the firmware's frames, each 50 µs after CLK is let go |
| An Arduino UNO driving four cascaded MAX7219s with a published test sketch | MAX7219 chain model | Every packet latched into the chip it is for, and exactly the sketch's three intentional errors flagged |
| OpenOCD through an ST-Link and an FTDI adapter initialising a Nordic nRF51822, and writing 20 words into its RAM | SW-DP target model (which judges `swd_idcode`) | The JTAG-to-SWD select, the line reset and every one of 416 requests: each ACK and the DPIDR as the real part's, every real read parity good; no contention but in reads whose data the model, having no memory, cannot know |
| Eleven EM4100 tags, ten on an RDM630 reader and one on a reader coil | Reader model | Every tag's number and parities; for the ten, the number the RDM630 reports on its own UART |
| Pollin DCF1 receiver off the air, 2 and 30 minutes | DCF77 receiver model | Every minute's time and parities |
| Smoothieware and GRBL step/direction, a tams EasyControl DCC command station, a SENT position sensor | Step, DCC and SENT models | 32,000 steps on each of Smoothieware's axes and back to the start, GRBL's 56,903, each within the A4988's timing; accessory and loco packets with their check bytes; 11 frames with their CRCs |

Three firmware bugs and one model bug came out of this, each now covered by a test that
would fail on the old code:

- **`uart_rx` lost bytes sent back to back below about 100 ticks per bit.** The STM32
  starts each byte straight after a stop bit; the firmware pushed a byte before watching
  the line again and missed the next start edge at 460,800 baud on 25 MHz. It now reads
  the data at bit 7's centre, samples the stop bit separately, and pushes a byte while
  the next one's samples are queued. It reads every gap between bytes from 54 ticks per
  bit (460,800 baud at 25 MHz, 921,600 at 50 MHz), and every gap but 2 to 2.8 idle bits
  from its floor of 28 (`test_uart_rx_every_gap_from_54_ticks_per_bit`).
- **`i2c_eeprom` faulted below 20 MHz.** Its 320 ns SDA setup, converted to ticks, fell
  under the 7 ticks its two EMITs need, and escert, run at those clocks, said so. The setup
  now has a 7-tick floor and certifies at every clock from 500 kHz; the 25 MHz image is
  unchanged.
- **`ps2_device` began a frame 2 µs after a PC stopped inhibiting.** The PC in the
  capture holds CLK low after every frame, for 250 µs after a break prefix (F0h) and
  525 µs after other bytes, and a device must see CLK high for 50 µs before it begins
  (Chapweske's PS/2 notes). The firmware waited for CLK high only, so a byte queued
  behind another, as every break code is, started at once. It now waits for CLK to stay
  high 50 µs; the host model checks that rule, and can inhibit as that PC does.
- **The flash model cleared WEL when a program or erase began.** The W25Q80DV data
  sheet clears it when the program or erase finishes, and the Macronix part reads
  status 03h while busy. `spi_flash` polls BUSY alone, so its results do not change;
  the model now reads as the data sheet and the real part do.

One more bug, in three receivers, came from measuring the sampling point on the simulator
rather than from a device. **`uart_rx`, `sbus_rx` and `modbus_rtu` sampled 3.5 ticks late on
average, 5 at worst.**

- Each takes the start edge's time from an RDTIME after the WAITP that sees the edge.
  `uart_rx` assumed that RDTIME commits one slot later, but a JNZ sits between the two,
  so it is two slots later. `sbus_rx` and `modbus_rtu` copied its constant.
- With the lag counted, the error is only the slot's quantisation: −1.5 to +1.5 ticks for
  an odd number of ticks per bit, −2 to +1 for an even one.
- In `uart_rx`, the earlier deadline left the back-to-back path too slow at the 28-tick
  floor: the floor's sweep had passed only because the samples came late. That path now
  queues data bit 0's sample straight after setting the time, 12 ticks after the RDTIME
  instead of 32, and the floor's sweep passes.
- `test_receivers_sample_at_the_bit_centre` measures the error for every slot phase, for
  the first byte and one back to back, at each program's default rate and its floor. Its
  negative control puts the old constant back and must see +5.
- The other receivers were checked too. `lin_resp` counts its three instructions
  correctly (−0.5 on average). The rest sample in windows of microseconds or more, or
  relative to edges they drive themselves.

Where a real device and the chip legitimately differ, the test says so: the FX2's first
read is at the EEPROM's current address, which the 24LC02B data sheet leaves undefined
at power-up (the real one sent 0x00, the firmware starts at word 0); the GPS capture
starts inside a burst, so the firmware, like the receiver model, finds the framing a
sentence later; the Wiegand reader's notes say 36 bits, and it sends 34.

On the FPGA bench ([`fpga/tangprimer25k`](../fpga/tangprimer25k/README.md)), the chip's RTL
meets live equipment instead of recordings. With a laptop's Ethernet port, `eth_tx` sent 20
of 20 frames that the laptop counted without a receive error, `eth_rx` received the laptop's
own traffic with good checksums, and `eth_trx` answered its ARP requests and pings. With an
RP2040's debug port, `swd_idcode` read both cores' identification registers, and an outside
analyser decoded the same values off the wire. With a laptop's USB port, `usb_hid`
enumerated as a keyboard and typed exactly the line it was given, six times out of six.

## Outside judges

The bus models that check the firmware were written here, from the same specifications as
the firmware, so a misreading shared by both would pass every test above. The recordings in
[Real devices](#real-devices) guard against that for the devices someone recorded. The
outside judges guard against it for nearly every program: programs written by other people,
mostly libsigrokdecode's protocol decoders (sigrok-cli 0.7.2, the version CI installs), read
the pins and must decode what the test asked for.

`test/cosim/judges.py` (`make judges`) runs each co-simulation case on the simulator,
records every pin's level in every tick as the bus models saw it, writes the levels as a
VCD and runs a decoder stack on it: `uart` (with `midi`, `lin` and `modbus` on top), `i2c`
(with `eeprom24xx`), `spi`, `can`, `ps2`, `jtag`, `swd`, `ir_nec`, `ir_rc5`,
`rgb_led_ws281x`, `wiegand`, `cec`, `dali`, `onewire_link` with `onewire_network`, and
`usb_signalling` with `usb_packet`. The decoded payload must equal what the case's host
or bus model asked for, written into the judge; the judge never reads a bus model's own
decoding. For the OLED Pong cases the decoded I2C writes are replayed into the SSD1306's
addressing, and the picture they leave must be the title, or the court the game model draws.
For a receiver, such as `uart_rx`, `can_rx` or `spi_sniff`, the decoder reads the line the
firmware read, and the case checks that the firmware reported the same frames.
`sw/tests/test_sigrok_judge.py` holds 14 more judges written earlier, for programs whose
tests live in `sw/tests` (USB Power Delivery, LIN responder, RC-6, EM4100, DCF77, SD card,
1-Wire device, AVR programmer, Qi, SPI flash, MAX7219, stepper, nRF24L01+ and Morse).

**Beyond sigrok 0.7.2.** Many protocols have no decoder in that release, so the judges also
use readers that are not sigrok-cli's own, each fetched at a pinned commit by
`test/cosim/outside_decoders.sh` or installed from Ubuntu by CI:

- **libsigrokdecode's current decoders** (commit 71f4514), run on sigrok-cli 0.7.2's runtime:
  `ir_irmp` (the IRMP library, which knows 57 remote-control protocols) for SIRC,
  JVC, Samsung, Sharp and Panasonic; `xy2-100`; `sae_j1850_vpw`; `flexray`; `rgb_led_spi`
  for WS2801; `swim`; `sbus_futaba`; `ook` with `ook_oregon`; `timing` for servo pulses;
  `graycode` for the quadrature encoder; and `pwm` for the frequency meter's test signal and
  for `pwm_audio`'s samples.
- **Two community decoders**, for NMRA DCC (FrightRisk/sigrok-DCC-Protocoll) and for SENT
  (enp6s0/sent-decoder).
- **Wireshark's dissectors** (`tshark`) for 10BASE-T and UDP: `ook` undoes the Manchester
  code, and Wireshark checks the frame's FCS and the IPv4 header checksum the chip built.
- **rtl_433**, the receiver used for real 433 MHz sensors, for Oregon Scientific.
- **rc-switch's receiver**, the Arduino library's own interrupt handler compiled off its
  microcontroller (`test/cosim/rcswitch_rx.cpp` replays the edge times into it), for EV1527.

Two programs are read twice, by readers that share no code: SIRC by `ir_sirc` and IRMP,
and Oregon Scientific by rtl_433 and `ook_oregon`. The second pair caught a weakness in the
test rather than the firmware: the case's rolling code was 0xBB, which reads the same with
its two nibbles swapped, so no reader could tell whether the firmware sent them in the right
order. The case now sends 0x3A. rtl_433 forms the sensor's ID as a weather station does and
reports 58, which is 0x3A; `ook_oregon` prints the nibbles in the order they are sent, low
first, as `a3`.

**Every judge has a negative control.** The same recording with one pulse of a line
inverted, in the middle of the traffic, must fail the judge's comparison: a judge that passes
whatever the pins do proves nothing. The controls caught two judges that compared too
little, and both now compare more: the 1-Wire judge read only the network layer's command,
so it now also compares every bit the master read, and the MAX7219 judge skipped the seven
blanked digits of the set-up.

In the final run all 63 judges in `judges.py` and all 14 in `test_sigrok_judge.py` agree,
and all 77 controls fail. Together they judge 74 of the 82 programs. The other eight:

- DShot: the only decoder we found carries no licence, so we do not run it.
- `modbus_full` has no co-simulation case of its own: its tests run every test of
  `modbus_rtu`, which the Modbus judge does read, and then its extra functions against the
  Modbus client model (`test_modbus.py`).
- VGA Pong and the chime (`westminster`) have no co-simulation case to record, and the
  seven-segment display's multiplexed digits are not a protocol any decoder reads.
- `i2c_timing` and `logic_analyzer` only measure or record a line the test itself drives.
- `eth_rx` receives the 10BASE-T line the test drives, which its case makes hard on purpose
  (a transmitter 100 ppm fast, each edge moved by up to 10 ns, to exercise the both-edge
  sampler); `ook`, which undoes the Manchester code for the transmitters' judges, cannot
  follow that line.

**On hardware too.** On the FPGA bench, `swd_idcode` debugged a real RP2040 while a logic
analyser recorded the two wires (`fpga/tangprimer25k/swdjudge.py`): the target Pico itself,
running gusmanb's LogicAnalyzer firmware beside its debug port, so neither the recorder nor
the decoder is ours. sigrok's `swd` decoder read every access after the wake-up, 15 in all,
off the real wire exactly as the chip reported it to its host, each SWCLK phase measured the
640 ns the firmware is built for, and the control (one SWDIO pulse inverted) fails
(fpga/tangprimer25k/README.md, SWD).

Some of the readers have limits that the judges work around, each written in the judge:

- `ps2` emits a frame only at the next frame's first falling clock edge, and loses that
  edge, so frames sent back to back never all decode. The judge decodes each frame on its
  own, followed by one more clock pulse.
- `dmx512` misreads slots sent back to back, so the DMX judge reads the same line with the
  `uart` decoder as 8N2 frames at 250 kbit/s.
- `sae_j2716_sent` ends a frame only at a pulse longer than 100 ticks, so frames sent back
  to back never end; the judge builds `sent_tx` with a pause pulse (`FRAME_UT` = 400, every
  pause at least 128 ticks). The decoder also works out that 100-tick limit as if its
  samples were 1 µs apart, so the judge reads the recording at 1 MS/s. Every SENT edge falls
  on the 3 µs tick grid, so the coarser rate moves no nibble.
- `can` shows a remote frame's CRC bits as data bytes, so after a remote frame's length the
  judge compares nothing more. The CAN cases send identifier 0x7FF, which ISO 11898-1
  forbids for standard frames (the seven high bits all recessive); the decoder warns about
  it, rightly, and the judge allows that one warning.
- IRMP reports some frames only once the pause after them has lasted long enough (the pause
  is how it tells JVC from NEC), longer than these cases run, so the JVC, Samsung, Sharp and
  Panasonic recordings are extended with idle line.
- rtl_433 accepts EV1527 pulses within ±200 µs of its own figures, which cannot tell a
  correct transmitter from a slightly wrong one at that code's 350 µs unit, so EV1527 is
  judged by rc-switch, whose tolerance is relative to the measured sync pulse.
- `swim` reads the sample rate when it starts, which a VCD gives only later, so its judge
  decodes from a sigrok session file made from the VCD.
- The Oregon Scientific message is sent twice with a gap between, so its controls invert a
  pulse a quarter of the way through the edges, inside the first copy, not in the gap.

## Pads and board

The simulations give every pin zero delay: an output reaches the bus model in the tick it is
driven, and a model's answer reaches the input synchroniser in the tick it changes. A real
chip adds its pads and the board on the way out and on the way back, about 10 ns each way.
A protocol that drives a pin and then samples the answer it causes, such as SPI's MISO after
an SCK edge, I2C's acknowledge, a 1-Wire read slot or a USB turnaround, loses that round trip
from its margin. One with no margin left would pass in simulation and fail on a board.

`test/cosim/pad_audit.py` (`make pad-audit`) runs every co-simulation case on the simulator
with each pin delayed by k ticks in each direction, for k from 0 to 3, so a round trip costs
2k ticks more than in simulation. A tick is 20 ns at the rated 50 MHz and longer at every
lower clock, so one tick each way already covers 10 ns at any clock the chip runs at. A case
passes at k when its protocol check passes, its board reports no error and no context
faults. A check that measures an exact time allows for the delay: `usb_hid`'s replies must
start exactly 75 ticks after the host's end of packet, plus the round trip.

All 76 cases pass with three ticks each way, so no program needs a slower setting for real
pads. The closest is `spi_target` in SPI mode 3 at its rated 416 kHz (a 60-tick SCK period
at 25 MHz). Each MISO bit reaches the pad 36 to 51 ticks after the rising edge before it, so it
is set up at least 9 ticks ahead of the rising edge that samples it: room for the 2 ticks of
setup the controller model needs and three ticks of pad delay each way. An earlier version
prepared the first data bit of a READ after the last address bit's edge, which put that bit on
the pad up to 55 ticks after the edge and failed at two ticks each way; it now has both
candidate bytes loaded and shifted into place before that edge. The audit also
reports each case's fastest pin: the 10BASE-T transmitters change a pin every 50 ns, the line
unit's SPI every 80 ns, and most programs every microsecond or slower. `vga_pong`, which has
no co-simulation case, changes its colour pins at most once per 40 ns pixel, and the only
inputs it samples are its buttons.

## Test suites

| Suite | Tests | Result |
| --- | ---: | --- |
| Software (`make sw-test`): assembler, simulator, certificates, models, firmware against bus models, line unit and USB, tape tools, capture export and SUMP bridge, bring-up script, protocols learned from random, specified and real recordings and receivers generated from their tables, firmware and models against real devices, sigrok's decoders as judges, VGA and OLED Pong, the DOOM console's load model, the site's make-it-late audit, the clock-parameterised firmware at 33 to 50 MHz | 1,632 | Pass, 5 skipped where an optional tool is absent (the sigrok tests need `sigrok-cli`, which CI installs) |
| Chip level on the RTL (`make test`) | 12 | Pass |
| Chip level on the final gate-level netlist (`make gl-test`) | 12 | Pass; with SDF timing at 20 ns at the three corners, 12 each |
| Co-simulation and line unit lockstep on the RTL with the standard-cell clock gates and latch banks (`CELLS=yes make cosim-parallel line-test`) | all | Pass |
| Co-simulation (`make cosim`): 200 random programs (191 fit a page), 76 firmware cases with bus models, directed tests | all | Pass |
| Host interface (`make host-test`) | 25 | Pass |
| Core unit tests (`make unit-test`) | 40 | Pass |
| Bus monitor (`make mon-test`) | 1 | Pass |
| Line unit lockstep (`make line-test`) | 19 runs, 3.8 million edges | Pass |
| Newer firmware (SWD, SPI target, NEC, NEC rebuilt for 33 and for 50 MHz, both pin banks at once, VGA Pong) and a protocol learned from the chip's own capture, through the chip pins (`make pin-test`, `make gl-pin-test`) | 9 | Pass on the RTL and on the final netlist |
| Bring-up script on the chip RTL (`make bringup-test`) | 2 | Pass |
| Outside judges: decoders written by others on the co-simulation cases (`make judges`), each with its control; 14 more are in the software tests | 61 | Pass; every control fails |
| Pads and board: every co-simulation case with its pins delayed 0 to 3 ticks each way (`make pad-audit`) | 74 × 4 | Pass |
| Lint (`make lint`, Verilator `-Wall`) | — | Clean |

The chip-level tests drive the real pins: they load firmware over SPI, run it, decode the
protocol on the pins, and compare the execution digest with the simulator's prediction. On
the gate-level netlist, an equal digest means the gates executed exactly the commits,
register values and pin changes of the model. Gate-level simulation without SDF is
zero-delay: it shows that the gates compute what the RTL computes, not how fast. Timing,
at the rated 20 ns and at the default 40 ns, comes from static analysis of the routed
netlist with extracted parasitics at three corners ([physical design](03-PHYSICAL-DESIGN.md)),
and the SDF-annotated runs replay the chip tests with those delays.

## Limits

- **Pad timing.** CMOS5L pads are not characterised, so timing claims are made at the
  internal tick, not at the package pins. The pad audit above shows the margin each
  protocol has for them, in ticks, not their delay itself.
- **Metastability** is not modelled probabilistically: inputs pass through a two-flop
  synchroniser, and simulation models it as a two-tick delay.
- **The EEPROM** stretches the clock before each acknowledge, which a real 24C02 does
  not. Against two real controllers (a Cypress FX2 at Standard mode, a TDS 744A's slow
  bus) the stretch never outlasted the controller's own low time; its Fast-mode margin is
  measured against our controller model only.
- **Replay** covers one 2^24-tick epoch (0.67 s at 25 MHz).
- **FPGA, not silicon.** No chip exists yet. The design runs on an FPGA bench (80 of the
  82 programs matching the model and their certificates; the 14-second `westminster` chime
  has no bench bundle, and `pwm_audio` has run there as the DOOM console's sound; [the bench](../fpga/tangprimer25k/README.md)), with
  plain flip-flops for the clock gates and latch banks and block RAM for the SRAM macros;
  those stay covered by the gate-level suites and the equivalence proof.

## In CI

Every check above runs on GitHub Actions whenever something it depends on changes, except
four that are run locally: lint, the bring-up script on the chip RTL (`make bringup-test`),
the firmware through the pins on the gate-level netlist (`make gl-pin-test`) and the
SDF-annotated chip tests. The host, core unit and bus monitor suites run in CI as part of
the mutation workflow's baseline. The
[results page](https://parthdoom.github.io/escapement/#results) shows the latest run of each
workflow on master, with its commit and log, failures included.

| Workflow | Runs on | Checks |
| --- | --- | --- |
| `test` | every push | chip tests on the RTL; software tests, timing certificates, lockstep co-simulation, the certificates against the RTL's measured leads (`proof_table.py`), the outside judges and the pad audit, firmware through the pins; the co-simulation and the line unit lockstep again on the standard-cell models (Icarus Verilog 13) |
| `formal` | a change to `src/` or `formal/` | three jobs: the properties and covers with the unbounded core proof as one PDR task per assertion group; the bounded check from reset, one task per assertion group (`make formal-bmc`); the tripwire, line unit and non-interference proofs, then the planted bugs that must make each fail (`make formal-v2 formal-teeth`) |
| `mutation` | a change to `src/`, `test/` or the simulator | the whole mutation campaign in 40 shards on the standard-cell models (Icarus Verilog 13), after a baseline of every suite, with the equivalence check of survivors; the shards are dealt by each mutant's time in earlier runs (`test/mutation/costs.json`), the canaries run once beside them, and the summary requires the canaries and exactly one result per mutant |
| `fw-mutants` | a push to any branch that changes the firmware, `escert`, the simulator, the assembler or the bus models | the firmware mutation campaign (`make fw-mutants`) in 24 shards: every planted timing bug against `escert`, and each certified one attacked on the simulator |
| `gds` | a change to the RTL or flow | LibreLane layout, Tiny Tapeout precheck, chip tests on the gate-level netlist |
| `equivalence` | each successful `gds` run on master, and by hand on a branch before its merge | that run's netlist against the RTL of its commit (`formal/equiv.sh`) |

Each job writes a summary (`scripts/ci_summary.py`); the site's build collects the
summaries of the latest master runs (`site/tools/ci_results.py`) and rebuilds after each of
these workflows. The formal and equivalence jobs use the OSS CAD Suite release pinned in
`scripts/versions.txt`, and the equivalence job the standard-cell liberty of the pinned PDK.

## Rerunning

```sh
make sw-test cert test cosim-parallel host-test unit-test lint   # simulation and certificates
make proof-table fw-mutants                                      # certificates against measurements; planted timing bugs
make judges pad-audit                                            # sigrok's decoders as judges (needs sigrok-cli); pins delayed
make pin-test bringup-test                                       # newer firmware through the pins; bring-up script
make gl-test gl-pin-test                                         # the same chip-level checks on the gate-level netlist
CELLS=yes make test cosim-parallel                               # the RTL with the real clock-gate and latch cells
make formal-quick                                                # bounded core check, covers, ahead, never-late, clock gate, latch bank, host, monitor
make formal JOBS=8                                               # adds the unbounded core proof (72 min on CI's 4-vCPU runners)
make formal-v2 formal-teeth                                      # tripwires, line unit from reset, non-interference; planted bugs
make formal SKIP=1                                               # while iterating: skip proofs whose inputs match their last PASS (never for a release)
make equiv                                                       # final netlist equals the RTL (~6 min)
CELLS=yes make mutation JOBS=8                                   # mutation campaign, 8 mutants at a time (hours; CI: 40 shards dealt by cost)
```

The suites run in parallel where they can: `sw-test` uses every core when pytest-xdist is
installed, `cosim-parallel` splits the random programs and the firmware runs across `JOBS`
simulators from one build, `formal` proves the core's assertions as parallel tasks, and the
mutation campaign runs `JOBS` mutants at once.

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
