# Physical design

Escapement is implemented with the Tiny Tapeout flow for IHP SG13CMOS5L (LibreLane
3.1.0.dev3, OpenROAD, IHP-Open-PDK `2bbec755`; [toolchain](05-TOOLCHAIN.md)) on a
**6×4-tile** allocation. Flow configuration: [`src/config.json`](../src/config.json),
with the reasoning for each setting in its comments and in
[design decisions](06-DECISIONS.md).

## Sign-off results

Version 1.6 (decode, operands and pin writes a stage earlier, D-44), harden `v16s` (local,
`scripts/local-harden.sh`):

| | |
| --- | --- |
| Die | 1289.28 × 710.64 µm (916,214 µm²) |
| Standard cells | 32,239 (491,350 µm²); storage: 2,391 flip-flops and 2,365 latches, plus 82 clock-gate latches |
| Macros | 2 × `RM_IHPSG13_1P_256x32_c2_bm_bist` (98,977 µm²) |
| Utilisation | 65.9% |
| Clock | Placed, routed and signed off at 20 ns: rated up to 50 MHz; 25 MHz by default (D-5) |
| Setup | No violations at any corner at 20 ns. The latch banks' inputs borrow time (D-40) and report 0 slack; the worst path into a flip-flop has +0.31 ns (slow), +7.62 ns (typical), +11.74 ns (fast) |
| Hold | No violations. Worst slack: fast +0.126 ns, typical +0.293 ns, slow +0.529 ns |
| Hold buffers | 2,148 after clock-tree synthesis (margin 0.2 ns), 1 after global routing |
| Routing | 2.33 m of wire, 272,825 vias; global-route overflow 666; detailed-route DRC 0 |
| LVS | Clean (0 errors, 0 net or device differences) |
| DRC | Clean (KLayout SG13CMOS5L deck in the Tiny Tapeout precheck) |
| Antenna | 0 violations (33 diodes) |
| IR drop | 0.57 mV worst (ground; supply 0.31 mV), at 20 ns |
| Power (static estimate) | 3.09 mW at 25 MHz, 6.17 mW at 50 MHz (OpenSTA's default activity, typical corner, after clock-tree synthesis, as in [power](15-POWER.md); the routed run's own report at 20 ns: 6.63 mW); workload measurements in [power](15-POWER.md) |
| Precheck | Tiny Tapeout precheck (tt-support-tools `d66cf17`) passes, including KLayout SG13CMOS5L DRC |
| Gate level | The chip-level (7) and pin (9, among them the NEC loopback rebuilt for 33 and for 50 MHz) test suites pass on the final netlist (zero-delay simulation: function, not timing); the netlist is proved equivalent to the RTL ([two steps](04-VERIFICATION.md#rtl-to-netlist-equivalence)) |
| Reproduction | A second harden of the committed `src/config.json`, with no overrides, gives the same netlist (sha256 `f5bcfa00…`), the same value for every flow metric and the same GDS geometry (KLayout's layout diff: 0 differences; `scripts/compare-runs.sh`); GitHub Actions' harden runs on push |

Version 1.6 adds 354 flip-flops (the registered decode and operands, each FIFO's registered
next head, the line unit's registered pin writes, its next-word buffer and 32-bit CRC) and
3,278 cells; the hold repair after clock-tree synthesis inserts 2,148 buffers (v1.5: 1,523),
and the one after global routing 1 (v1.5: 72). Getting it
to close at 20 ns took nine trial layouts (D-44): the post-CTS hold repair judges setup slack
from placement estimates, and with setup margins of 1 and 2 ns it put delay cells at the
start of paths that became critical after routing; with 2.5 ns (placement) and 3 ns (global
routing) it leaves them alone.

Version 1.5 (tests decided a cycle early, D-43), harden `v15c`, reproduced with the same
netlist (sha256 `2b8e5f5b…`): 28,961 cells (433,985 µm²), 59.5%, signed off at 30 ns (rated
33 MHz); at 40 ns the worst path into a flip-flop had +14.13 ns (slow), +23.73 ns (typical),
+29.41 ns (fast); fast-corner hold +0.157 ns, 1,523 hold buffers, global-route overflow 83,
2.21 m of wire, static power 2.68 mW. It added 28 flip-flops and 989 cells; 754 of those were
the second design repair after global routing (`RUN_POST_GRT_DESIGN_REPAIR`, below).

Version 1.4 (line unit, latch banks), harden `v14i`, reproduced by the GitHub Actions harden
(run 39623762996) with the same netlist (sha256 `661e5608…`): 27,972 cells (422,057 µm²),
58.2%, signed off at 40 ns; at 40 ns the worst path into a flip-flop had +5.16 ns (slow)
and +18.10 ns (typical); fast-corner hold +0.138 ns, 1,453 hold buffers, global-route
overflow 1,683, 2.22 m of wire, static power 2.64 mW.

The line unit made the design 11% larger, and the first v1.4 layouts failed with routing
shorts in the tape's pin channel. Three changes fit it into the same 6×4 tiles with no
feature removed (D-40, D-41): the rarely written registers became latches (the design
went from 379,900 to 331,300 µm² before placement, below v1.3), debug reads of a
context's registers share the commit stage's multiplexers (a thousand fewer long wires),
and the macros' pin-side halo doubled to 60 µm. Placement and routing time each latch from
its gate (`src/escapement_pnr.sdc`) and keep a 1 ns and a 2 ns setup margin, which the
detours of detailed routing used up in an earlier trial; sign-off timing uses the full
latch model. Every trial and the reason it was rejected is in
[`asic/reports/area.csv`](../asic/reports/area.csv).

Version 1.3 (streaming capture, `v13b`): 25,931 cells (435,322 µm²), 59.7%, fast-corner
hold +0.175 ns, 1,727 hold buffers, global-route overflow 4, 2.13 m of wire.

Version 1.1 (`rc14`: 23,978 cells, 55.2%, fast-corner hold +0.025 ns, 395 hold buffers) was
reproduced exactly, metric for metric, by the GitHub Actions harden from a clean checkout
with the pinned toolchain (258 minutes on a standard public runner). Version 1.2 (`v12h`:
25,864 cells, 58.9%, fast-corner hold +0.068 ns) had 1,886 more cells than version 1.1:
1,154 hold buffers from the larger hold margin, and 732 for pin bank B and its repair
buffers. Version 1.3 adds the streaming logic (D-32) for 67 more cells, and needs 79 more
hold buffers after global routing (see below). The GitHub Actions harden of version 1.3
(run 39246310949, commit `d49044a`) reproduced `v13b` metric for metric, with the same
netlist (sha256 `54236b4f…`).

Corners: `nom_slow_1p08V_125C`, `nom_typ_1p20V_25C`, `nom_fast_1p32V_m40C`. The
remaining design-rule warnings in v1.6 are 25 max-slew pins at the slow corner and 22
max-capacitance pins (v1.5: 18 and 26; v1.4: 163 nets and 41). Slew, capacitance and long
wires are repaired a second time after global routing, on its wire estimates, with 30%
margins for the detours detailed routing adds (`RUN_POST_GRT_DESIGN_REPAIR`). Of the slew
pins, nine are SRAM address and data inputs above the macro's 0.595 ns input slew limit
(worst 0.97 ns), the edge of its timing table, as in v1.5. The other 16 are four logic nets
that detailed routing took 2.2 to 3.4 times their bounding box (one of 273 µm became 932 µm,
170 µm of it on Metal1), so the repair's estimate fell short: worst 3.35 ns against the
2.5 ns limit (a minimum-size NOR3 driving two loads). LibreLane's flow has no repair step
after detailed routing. Of the capacitance warnings, 21 are SRAM data outputs above 0.064 pF
(worst 0.21 pF), and one is a buffer at 0.305 pF against 0.300 pF. None of them costs timing
or function: at 20 ns every path through the four logic nets has at least 8.25 ns of setup
slack, the SRAM inputs keep at least 3.94 ns, and the buffer feeds the input of a latch that
borrows time. The 146 max-fanout warnings (v1.5: 108) are against the template's limit of
10 loads, and the nets behind them meet their slew limits.

The flow logs carry other warnings, none of them a defect. Synthesis has none. The two
repair steps after global routing each print `EST` "missing route to pin" until OpenROAD's
limit of 1,000 messages: the parasitic estimate has no global route into the SRAM macros'
pins or to the design's output ports at the block edge (`uo_out`, `uio_out`, `uio_oe`).
Detailed routing prints 93 `DRT` warnings: 86 "term has no pins on routing grid", all SRAM
macro pins, which the router reaches off the grid, and 7 that it skips the PDK's
`LEF58_ENCLOSURE` rules without a cut class, rules the KLayout sign-off DRC in the precheck
does check. LVS (0 differences) and DRC (0) show every pin connected and every rule met.
Global routing reports congestion (the overflow of 666 above), which detailed routing
resolves with no violations, and the resizer reports setup it could not repair on placement
and global-route estimates; sign-off timing on the extracted parasitics has no violation at
any corner.

**Frequency headroom.** Static timing analysis of the final v16s netlist with its extracted
parasitics (OpenSTA, the sign-off constraints with the latches modelled, the period
shortened and the 20% I/O delays scaled with it) finds the shortest period at which setup
still holds, latch inputs included (a latch input that borrows time reports 0 slack; past its
limit, negative):

| Corner | Shortest passing period | Clock | Fails at | v1.5 (v15c) | v1.4 (v14i) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Slow, 1.08 V, 125 °C | 19.7 ns | 50.8 MHz | 19.6 ns | 38.6 MHz | 28.6 MHz |
| Typical, 1.20 V, 25 °C | 12.4 ns | 80.6 MHz | 12.3 ns | 61.3 MHz | 45 MHz |
| Fast, 1.32 V, −40 °C | 8.3 ns | 120.5 MHz | 8.2 ns | 94.3 MHz | 70 MHz |

The rating keeps 1.5% at the slow corner (v1.5, rated 33 MHz: 16%), and the 25 MHz default
half the period. At 20 ns the worst path into a flip-flop at the slow corner runs from the
host interface's registered START to the line unit's tape write data (`start` to
`lu_pwdata`, 33 cells), and it starts with a 0.98 ns delay cell that the hold repair placed.
v1.4's limit was a single-cycle path from a context's tick counter through the deadline
comparison to a pin's output register (34.4 ns); version 1.5 decided those tests a cycle
early (D-43), and its limit became the decode of the instruction (`insn` to `pin_val`, 51
cells, 25.5 ns); version 1.6 decodes in the stage before, reads the operands a cycle ahead
and registers the pin writes (D-44). Hold slack does not depend on the period. Firmware takes
the tick length as a constant (`CLK_KHZ` in the timing tables, and the tick counts in
hand-written programs), so a faster board clock needs only rebuilt firmware, not a new chip.
These figures leave out the Tiny Tapeout multiplexer and pads, whose delays the flow does not
model (the Tiny Tapeout clock specification gives 66 MHz). They limit the clock a board can
use, not whether the chip can be made.

Version 1.6 is placed, routed and signed off at 20 ns, so it is rated up to 50 MHz. The
shipped firmware still runs at 25 MHz: VGA Pong uses the core clock as its pixel clock
(VGA's is 25.175 MHz), and the hand-written programs count 40 ns ticks (D-5).

At 20 ns the checks other than setup hold too, at all three corners: minimum pulse width
(at least 9.40 ns to spare, the SRAM clock pins included), the clock gates, and setup and
hold at the SRAM inputs (3.94 ns setup slack at the slow corner, hold at least +0.171 ns).
A clock that is high for 40% or 60% of the period instead of half passes setup, hold and
minimum pulse width at all three corners (`DUTY=40 scripts/frequency-headroom.sh runs/v16s
slow:20 typ:20 fast:20`, and `DUTY=60`). The Tiny Tapeout demo board should be set to
exactly 50 MHz, which its RP2040 makes without jitter (100 MHz divided by 2), or 33 MHz
(132 MHz divided by 4); a clock it can only make with a fractional divider jitters, and a
short cycle could fall below the 20 ns the layout is signed off at (D-5).

`scripts/frequency-headroom.sh RUN_DIR` reruns the sweep behind the table on a harden run
of `scripts/local-harden.sh` ([toolchain](05-TOOLCHAIN.md#local-hardening)).

## Floorplan

```text
  y=710 ┌───────────────────────────────────────────────────────────────┐
        │ TT pins (x 30-191)                     ┌────────────────────┐ │
        │                                        │ tape SRAM (N)      │ │
        │                                        │ pins face down     │ │
        │            standard cells              └────────────────────┘ │
        │ ┌────────────────────┐                                        │
        │ │ program SRAM (FS)  │                                        │
        │ │ pins face up       │                                        │
    y=0 └─┴────────────────────┴────────────────────────────────────────┘
        x=0                                                       x=1289
```

- **Macros split, flush to the core edges.** The program store sits bottom-left at
  (12, 3.78) with orientation FS, and the tape top-right at (821.28, 588.06) with
  orientation N. Each macro's signal pins face the logic. Tiny Tapeout's I/O pins are
  at the top-left, so the top-right corner is free for the second macro.
- **No stranded cells.** A placeable strip behind a macro traps cells whose only exits
  cross the macro. Placing the macros flush to the core edge, with a 50 µm horizontal
  and 60 µm vertical row-cut halo (30 µm before v1.4), removes those strips and leaves a
  cell-free escape channel on each macro's pin side.
- **Power grid aligned to the macros.** The Metal4 stripe pitch (67.44 µm) and offset
  (48.84 µm) are chosen so every stripe crossing a macro lands in one of its same-net
  power columns ([`src/pdn_cfg.tcl`](../src/pdn_cfg.tcl), adapted from the Loom project).
  Both macro supplies (`VDD!` and `VDDARRAY!`) connect to the single VPWR rail.
- **Placement density 56%**, just above the utilisation, so cells spread evenly.

## Routing

SG13CMOS5L gives a Tiny Tapeout block four signal layers below TopMetal1. Metal2 to
Metal4 alone leave one horizontal layer (Metal3), and at 6×4 that layer carried almost
all of the congestion. Escapement therefore also routes signals on **Metal1**, which is
horizontal at the Metal3 pitch and accepted by the precheck. With it, global-route
overflow fell from 5,276 to 76 and detailed routing converges.

Metal1 routing is safe only because **no decap cells are inserted**. LibreLane adds fill
after detailed routing, and decap cells carry Metal1 power plates that the router never
saw; they shorted signal wires to power. Filler cells carry Metal1 only on their rails,
and every logic cell's Metal1 is fully described in its LEF (checked against the GDS for
all 84 cells used, [`scripts/m1_fill_overlap.py`](../scripts/m1_fill_overlap.py)). The
design has no decap capacitance, which is acceptable with an IR drop of at most 0.57 mV at
20 ns (v1.6).

Other routing settings:

- **No clock non-default rule.** With about 1,000 clock nets (one subtree per clock gate), the
  double-spacing rule cost more routing resource than it is worth at 25 MHz.
- **Wires longer than 500 µm are buffered** during design repair, which cut slow-corner
  slew warnings from 314 to 124 and improved fast-corner hold slack.

**Headroom.** Each SRAM blocks Metal1 to Metal3 over its whole footprint, so congestion
gathers around the tape SRAM. Version 1.3 ended global routing with an overflow of 4
(version 1.2: 68); a version 1.2 variant with about 1,000 more cells reached 1,900 to
3,200, and detailed routing then left shorts over the tape SRAM in four of five runs. The
first version 1.4 layout, with the line unit in flip-flops, reached about 10,000 and
failed the same way. Version 1.4 as built (latch banks, shared debug reads, the 60 µm
halo) ends at 1,683 with no detailed-routing errors. Global-route overflow is the number
to watch when adding logic.

## Timing constraints

[`src/escapement.sdc`](../src/escapement.sdc) is LibreLane's base SDC with one change:
the clock uncertainty is 0.25 ns for setup and **0.05 ns for hold**. A hold check
launches and captures on the same clock edge, so jitter cancels; skew is propagated with
CRPR, and on-chip variation stays covered by the 5% timing derate. Applying the full
setup margin to hold had put about 4,000 unnecessary delay cells on register self-loops.

**Hold margin after routing.** Routing takes hold slack from the paths that run from a
context's register bank into its clock-gated event FIFO, where the capture clock passes
the gate and a deeper tree. How much depends on how the clock feed of that gate is
routed: 0.17 ns in one layout, 0.31 ns in another. A margin applied after clock-tree
synthesis (`PL_RESIZER_HOLD_SLACK_MARGIN`) cannot see this. With 0.1 ns, version 1.2
ended with 33 fast-corner paths at up to −0.036 ns. At 0.2 ns one layout passed and
another ended at −0.106 ns, and 0.35 ns, with 1,100 more buffers, still ended at
−0.101 ns. So hold is repaired twice: after clock-tree synthesis with a 0.2 ns margin,
and after global routing (`RUN_POST_GRT_RESIZER_TIMING`), where the routed clock feed
is known. In version 1.2 a 0.1 ns margin there was enough: 34 buffers, +0.068 ns. In
version 1.3 it left two fast-corner paths at −0.030 ns after detailed routing, so the
margin is 0.2 ns: 113 buffers, +0.175 ns.

**Reading the setup report with clock gates.** The worst setup path at the slow corner
ends at a clock-gate latch, and OpenSTA reports slack to the latch's opening edge
(the falling clock edge). The true limit is the closing edge. Borrowing is harmless for
a clock gate: while the clock is low, the AND output is low whatever the latch passes.

## SRAM integration

The macro views are vendored unmodified from IHP-Open-PDK `2bbec755`
([macro/](../macro/RM_IHPSG13_1P_256x32_c2_bm_bist/README.md)). The integration follows
the published CMOS5L recipe of the Loom project, adapted from its 512×16 macro to
256×32:

1. `MACROS` keyed by the flattened instance path, with liberty files per process corner
   (`*_typ_*`, `*_fast_*`, `*_slow_*`), because the CMOS5L STA corners are all named
   `nom_*`.
2. `PDN_MACRO_CONNECTIONS` tie `VDD!` and `VDDARRAY!` to VPWR and `VSS!` to VGND.
3. A custom PDN script aligns the Metal4 stripes to the macro's power columns; Tiny
   Tapeout's pin check requires every Metal4 power port to reach both the top and bottom
   edges.
4. `MAGIC_EXT_ABSTRACT_CELLS` abstracts the macro during LVS extraction. Magic DRC is
   disabled because its CMOS5L technology file lacks the SRAM rule exceptions; sign-off
   DRC is the KLayout deck in the precheck.
5. BIST and bit-mask pins are tied off. Simulation uses the vendored behavioural model.

## Measurements

Every harden is recorded in [`asic/reports/area.csv`](../asic/reports/area.csv). The
floorplan and routing settings above were chosen from measured runs; the experiments
and their outcomes are summarised in [design decisions](06-DECISIONS.md) (D-28 to D-30).
