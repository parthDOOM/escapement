# Tool bugs

Six defects in the tools this design is built and checked with have shown up on it. This
page records each one: what it does, how it showed, why it cannot pass unnoticed, what the
design does about it, and where it stands upstream. A defect counts here if it is in the
version the project uses now; one already fixed in that version is listed under
[Fixed in the versions used](#fixed-in-the-versions-used). Each entry should be checked
again whenever a tool version changes.

The versions are those of the shuttle's flow and of CI: LibreLane 3.1.0.dev4 with OpenROAD
`dcf36133` (2026-02-17) for the `ttihp27a` shuttle, Icarus Verilog 13.0, and Yosys
0.69+190 with SBY 0.69 and Yices 2.7.0 from the OSS CAD Suite of 2026-10-04
([`scripts/versions.txt`](../scripts/versions.txt)).

| Tool | Defect | How it shows | Upstream (2026-11-22) |
| --- | --- | --- | --- |
| OpenROAD, global placement | The resizer inside timing-driven placement follows timing-path records its own buffering has freed | A segmentation fault, or RSZ-1008, a delay too large for its fixed-point type; the flow stops | [#11977](https://github.com/The-OpenROAD-Project/OpenROAD/issues/11977), open |
| OpenROAD, clock-tree synthesis | When two clock gates' banks share a centre, one gate loses its clock input | The disconnected-pin check stops the flow | [#9816](https://github.com/The-OpenROAD-Project/OpenROAD/issues/9816), fixed after the flow's commit |
| OpenROAD, design repair | Slew and capacitance are repaired at the first corner defined, which LibreLane makes the fast one | Violations at the slow corner that the repair leaves in place | not reported |
| OpenROAD, setup repair after clock-tree synthesis | Rebuffering can turn a net's fanout tree into a long chain of buffers in series | Setup violations after routing, on paths that met timing before | not reported |
| Icarus Verilog 13, `$sdf_annotate` | Five gaps in SDF annotation, one of which stops reading the file without failing the run | SDF errors in the simulation log | [#509](https://github.com/steveicarus/iverilog/issues/509), [#945](https://github.com/steveicarus/iverilog/issues/945), [#746](https://github.com/steveicarus/iverilog/issues/746) open; [PR #1694](https://github.com/steveicarus/iverilog/pull/1694) merged, not released |
| yosys-smtbmc 0.69 | The witness writer wraps one-bit values in a bit-vector extract, which fails when `--unroll` has made them Boolean | A failed induction ends in ERROR, not UNKNOWN, and loses its witness file | no issue found; not reported |

## Timing-driven global placement crashes (OpenROAD #11977)

**What happens.** Inside global placement, OpenROAD runs timing-driven iterations that
repair the design and rebuffer nets before the placer continues. In the first of them, the
resizer can follow a timing-path record that its own buffer insertion has just freed. If the
memory has been reused, the run ends with a segmentation fault in the timing engine
(`sta::Path::vertex`, called from `rsz::Rebuffer::drvrPinTiming`, `fullyRebuffer` and
`Resizer::findResizeSlacks`). If it has not, the stale record yields a delay of about
10³⁵ and the run ends with `[ERROR RSZ-1008] FixedDelay conversion out of range`. Both
stop the flow, so neither can pass unnoticed.

**Two symptoms, one bug.** The two looked unrelated until one placement input produced
either of them at the same point. [Issue #11977](https://github.com/The-OpenROAD-Project/OpenROAD/issues/11977),
opened on 2026-10-24 against the same OpenROAD commit, describes the same call chain in the
same first timing-driven pass, right after repair has inserted buffers, and its reporter
reads it as this use of freed path records. The issue's trigger is a `set_max_delay
-ignore_clock_latency` constraint. This design has none, and the crash also happens with
the sign-off constraints in place of the placement constraints, so that constraint is not
needed for it.

**The reproducer.** Global placement alone (`--from OpenROAD.GlobalPlacement --to
OpenROAD.GlobalPlacement`), replayed from the final layout's own input state (the step
before it in the signed-off harden), took one to two minutes a run:

| Variant | Crashes |
| --- | --- |
| The committed settings: the memories' input nets kept untouched (`RSZ_DONT_TOUCH_RX`, `macro_in_`) | 0 of 8 |
| The same, with the sign-off constraints instead of the placement constraints | 0 of 8 |
| The same, with one OpenROAD thread | 0 of 8 |
| Repair free to rebuffer every net (`RSZ_DONT_TOUCH_RX` set to `$^`, which matches nothing) | 8 of 8 (6 faults, 2 RSZ-1008) |
| That, with the sign-off constraints | 3 of 4 |
| That, with one thread | 4 of 4 |

What it needs, then, is for the resizer to be free to rebuffer the nets that drive the
memories' inputs: 88 four-drive buffers, each loaded with 80 to 137 fF. With one thread it
crashes every time and always as a fault, so it is not a race between threads; the
single-threaded allocation order reuses the freed block every time. The smallest reproducer
is that input state with `RSZ_DONT_TOUCH_RX` set to `$^` and `OPENROAD_THREADS` set to 1:
four crashes in four, about 75 seconds each.

**What the design does.** The committed settings keep the memories' input nets out of
repair, which [D-65](06-DECISIONS.md) needed for other reasons (a buffer before every
memory input, which repair must not undo). From the final layout's input, those settings
never crashed in 24 runs. A full harden in CI still crashes now and then, and not only
from a different input: on 25 November two runs crashed in a row on a tree identical to
one that had passed the day before, all three entering placement in the same state and
agreeing to the last printed digit up to the first repair. Whether the freed record has
been reused then decides it. The gds job therefore builds again when the placement log
shows the crash, up to three times ([D-72](06-DECISIONS.md)). The signed-off netlist is not
affected either way: a run that crashes produces no layout, and a run that passes has gone
through every sign-off check.

**Related upstream.** [OpenSTA #398](https://github.com/The-OpenROAD-Project/OpenSTA/issues/398)
(open) is an out-of-bounds access on the same `fullyRebuffer` call chain, on an IHP design
with SRAM. [OpenROAD #8351](https://github.com/The-OpenROAD-Project/OpenROAD/issues/8351),
an intermittent segmentation fault in global placement, was closed as stale without a fix.

## A clock gate disconnected in clock-tree synthesis (OpenROAD #9816)

Clock-tree synthesis moves each clock gate to the centre of the bank of registers it
clocks and then rewires its clock input. When two gates' banks have exactly the same
centre, as two registers of one context interleaved by the placer can, both gates are
moved there and only one keeps its clock input. On version 3 about one full harden in six
drew such a placement.

LibreLane's disconnected-pin check stops the flow at the gate whose input is unconnected;
LVS, the gate-level tests and the netlist equivalence proof would each catch it too. None of
OpenROAD's clock-tree options avoids it, and setting the gates don't-touch makes clock-tree
synthesis abort. The adopted layouts are placements without a collision (D-45, and the
placement density of D-61), and every committed layout passes the check. The issue was fixed
upstream in March 2026, after the commit the shuttle's flow pins.

## Design repair at one corner only

OpenROAD's design repair, the step that fixes slew and capacitance, judges them at the first
corner the flow defines. LibreLane defines its corners in an order that puts the fast one
first. Slow-corner slew is 2.2 to 2.5 times fast-corner slew on this design, and this is
one of the reasons version 3.0 was signed off with 15 pins over the slew limit at the slow
corner: the memories' inputs passed at the fast corner and failed at the other two.
Changing the command corner does not change it. Version 3.1 meets every
electrical rule at every corner by working around it: a buffer before every switching
memory input, a tighter slew limit during repair, and the other changes of
[D-65](06-DECISIONS.md).

## Setup repair builds a buffer chain

After clock-tree synthesis, OpenROAD's setup repair (`repair_timing`) sizes cells, swaps
pins, clones gates and rebuffers nets until every path has the margin the flow asks for,
2.5 ns here. On one layout of version 3.2 it inserted 154 buffers, against 31 on a layout of
nearly the same design, and 97 of them sat in series on a single net: a value from the tape
memory that spreads across the core. They were placed within a patch of about 50 × 70 µm,
each driving the next buffer and two or three of the net's loads, so the net's fanout tree had
become a daisy chain about 11.7 ns long. The step's own report ended with no path short of
its margin, so it did not see the chain as a fault. After routing, setup failed at the slow
corner by 1.13 ns at 20 ns, on 28 paths, all starting at the tape memory's output.

Setup is checked at every corner on the routed layout, so a chain like this cannot pass
unnoticed: it shows as failing paths, or as a lower frequency limit. The flow turns off
rebuffering in that one step (`PL_RESIZER_SETUP_BUFFERING` false in `src/config.json`).
Sizing, pin swaps and cloning stay, and the repair after global routing still rebuffers.
The same source then met 20 ns with no chain (D-77). The case has not been reduced to a
small reproducer, and it is not reported upstream.

## SDF annotation in Icarus Verilog 13

The SDF-annotated gate-level runs found five places where the harden's SDF (written by
OpenSTA) and Icarus 13's `$sdf_annotate` disagree. The first four are changed in copies of
the netlist and SDF by [`test/sdf_prep.py`](../test/sdf_prep.py), which has a self-test that
fails if any one step is left out; the fifth leaves out five delays of a few picoseconds.

- **Escaped dotted names abort the run.** The flattened netlist keeps the RTL path of each
  hand-placed cell (clock-gate latches, gated-clock buffers, the memories) as one escaped
  name. Icarus splits an SDF path at every dot, escaped or not, finds no such submodule and
  aborts. Fixed on master by [PR #1694](https://github.com/steveicarus/iverilog/pull/1694),
  merged on 2026-11-05 and in no release yet; v13_0 (2026-03-02) is still the latest.
- **Bit-selected ports stop the parser.** OpenSTA writes one IOPATH per bit of a memory's
  data bus; Icarus accepts only the whole bus, and after "Too many errors" it stops reading
  the file. Every later cell is left without delays, 5,856 of 67,586 on version 2, and the
  run goes on. This is the dangerous one, and the reason every SDF error a run prints is
  accounted for. Known and open ([#509](https://github.com/steveicarus/iverilog/issues/509); the error
  handling is [#889](https://github.com/steveicarus/iverilog/issues/889)).
- **Timing checks are not applied** ([#945](https://github.com/steveicarus/iverilog/issues/945),
  open). They are dropped; setup and hold are signed off by static timing analysis.
- **Interconnect delays need `-ginterconnect`** ([#746](https://github.com/steveicarus/iverilog/issues/746),
  open); the test Makefile passes it.
- **An interconnect to a port behind an `assign` is not found.** The version 3.2 netlist
  connects five outputs, `uo_out[3]` to `uo_out[7]`, to their buffers through `assign`
  statements, and Icarus reports "Could not find intermodpath" for the interconnect from
  each buffer to the port. Those five delays, 6 to 14 ps each, are left out; the other
  19 interconnects to ports, which connect directly, and the rest of the file's 86,702 are
  applied. Which outputs go through an `assign` depends on the layout: version 3.1 had four,
  without `uo_out[4]`. Each SDF-annotated run of version 3.2 prints exactly these five errors
  and no other, which the runs check, since an error of any other kind may mean part of the
  file went unread.

An empty typical voltage or temperature in the SDF header also fails, but Icarus is asking
for a value the file leaves empty, so it is not counted as a bug; `sdf_prep.py` fills it.

## The witness file of a failed induction (yosys-smtbmc)

With `--unroll`, which SBY passes for `smtbmc yices` in prove mode, one-bit values become
Boolean terms, but the witness writer (`write_yw_trace`) still wraps every bit in a
bit-vector extract. When an induction fails, Yices rejects the term, the pipe breaks, and
the task ends in ERROR instead of UNKNOWN, with its witness file empty; the VCD trace
survives. It cannot make a proof pass: a passing induction writes no witness, and a failing
one is still not a pass. It showed on a planted fault in the non-interference proof, whose
check was then moved to the base case, where it bites. The writer is unchanged on Yosys
main, and no issue was found upstream.

## Fixed in the versions used

- **LibreLane [#1039](https://github.com/librelane/librelane/issues/1039).** In 3.1.0.dev3 an
  unset `OPENROAD_THREADS` became `-threads None`, and OpenROAD ran single-threaded (D-23).
  It cost time only, never a result, and is fixed in 3.1.0.dev4.
- **Icarus Verilog 12 and the PDK's cell models.** Icarus 12 gave X on the standard-cell
  models, so on version 1.4 every co-simulation on those models failed within seconds, and
  the mutation campaign counted the failures as kills. CI builds Icarus 13, and the campaign's baseline check now catches it
  ([04-VERIFICATION](04-VERIFICATION.md)).

None of these has been reported upstream from this project. Where an issue already exists,
this page is the design's record of it.
