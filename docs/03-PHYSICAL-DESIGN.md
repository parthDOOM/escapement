# Physical design

Escapement is implemented with the Tiny Tapeout flow for IHP SG13CMOS5L (LibreLane
3.1.0.dev3, OpenROAD, IHP-Open-PDK `2bbec755`; [toolchain](05-TOOLCHAIN.md)) on a
**6×4-tile** allocation. Flow configuration: [`src/config.json`](../src/config.json),
with the reasoning for each setting in its comments and in
[design decisions](06-DECISIONS.md).

## Sign-off results

Version 1.5 (tests decided a cycle early, D-43), harden `v15c` (local, `scripts/local-harden.sh`):

| | |
| --- | --- |
| Die | 1289.28 × 710.64 µm (916,214 µm²) |
| Standard cells | 28,961 (433,985 µm²); storage: 2,037 flip-flops and 2,365 latches, plus 80 clock-gate latches |
| Macros | 2 × `RM_IHPSG13_1P_256x32_c2_bm_bist` (98,977 µm²) |
| Utilisation | 59.5% |
| Clock | Placed, routed and signed off at 30 ns: rated up to 33 MHz; 25 MHz by default (D-5) |
| Setup | No violations at any corner at 30 ns. The latch banks' inputs borrow time (D-40) and report 0 slack; at 40 ns the worst path into a flip-flop has +14.13 ns (slow), +23.73 ns (typical), +29.41 ns (fast) |
| Hold | No violations. Worst slack: fast +0.157 ns, typical +0.290 ns, slow +0.478 ns |
| Hold buffers | 1,523 after clock-tree synthesis (margin 0.2 ns), 72 after global routing |
| Routing | 2.21 m of wire, 242,715 vias; global-route overflow 83; detailed-route DRC 0 |
| LVS | Clean (0 errors, 0 net or device differences) |
| DRC | Clean (KLayout SG13CMOS5L deck in the Tiny Tapeout precheck) |
| Antenna | 0 violations (52 diodes) |
| IR drop | 0.19 mV worst |
| Power (static estimate) | 2.68 mW at 25 MHz; workload measurements in [power](15-POWER.md) |
| Precheck | Tiny Tapeout precheck (tt-support-tools `d66cf17`) passes, including KLayout SG13CMOS5L DRC |
| Gate level | The chip-level (7) and pin (8, one of them NEC rebuilt for 33 MHz) test suites pass on the final netlist (zero-delay simulation: function, not timing); the netlist is proved equivalent to the RTL |
| Reproduction | A second harden of the committed `src/config.json`, with no overrides, gives the same netlist (sha256 `2b8e5f5b…`) and the same value for every flow metric; GitHub Actions' harden runs on push |

Version 1.5 adds 28 flip-flops (each FIFO's retire and late flags, the core's four test
flags and its 16-bit lead) and 989 cells. A layout of the same RTL without the second
design repair after global routing (`v15a`) had 235 more cells than v1.4; the other 754 are
that repair's buffers and resized gates (`RUN_POST_GRT_DESIGN_REPAIR`, below).

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
remaining design-rule warnings in v1.5 are 18 max-slew pins at the slow corner and 26
max-capacitance pins (v1.4: 163 nets and 41). Version 1.5 repairs slew, capacitance and long
wires a second time after global routing, on its wire estimates, with 30% margins for the
detours detailed routing adds (`RUN_POST_GRT_DESIGN_REPAIR`); the same RTL laid out without
it (`v15a`) had 242 and 34. What is left is mostly at the SRAM macros' pins, whose limits are
the edges of the macro's timing tables: eleven address and data inputs above the 0.595 ns
input slew limit (worst 1.13 ns) and the 26 capacitance warnings, all data outputs above
0.064 pF (worst 0.18 pF). The other seven slew pins are on three logic nets, each within
0.25 ns of the 2.5 ns limit. None of them costs timing: at 40 ns every path through any of
them has at least 23.4 ns of setup slack, except one that arrives at a clock-gate latch of
the line unit 1.0 ns before the latch opens and could borrow 19.9 ns more. The 108
max-fanout warnings (v1.4: 101) are against the template's limit of 10 loads, and the nets
behind them meet their slew limits.

**Frequency headroom.** 33 MHz is the rating at the slow corner with margin to spare (D-5),
not the limit of the layout. Static timing analysis of the final v15c (v1.5) netlist with
its extracted parasitics (OpenSTA, the sign-off constraints with the latches modelled, the
period shortened and the 20% I/O delays scaled with it) finds the shortest period at which
setup still holds, latch inputs included (a latch input that borrows time reports 0 slack;
past its limit, negative):

| Corner | Shortest passing period | Clock | Fails at | v1.4 (v14i) |
| --- | ---: | ---: | ---: | ---: |
| Slow, 1.08 V, 125 °C | 25.9 ns | 38.6 MHz | 25.8 ns | 28.6 MHz |
| Typical, 1.20 V, 25 °C | 16.3 ns | 61.3 MHz | 16.2 ns | 45 MHz |
| Fast, 1.32 V, −40 °C | 10.6 ns | 94.3 MHz | 10.5 ns | 70 MHz |

The rating keeps 16% at the slow corner, and the 25 MHz default 54% (v1.4, rated 25 MHz:
14%). v1.4's limit was a single-cycle path
from a context's tick counter through the deadline comparison and the retire and fault
logic to a pin's output register (`now` to `pin_val`, 34.4 ns at the slow corner), and the
committing instruction's admission tests reached the same fault logic as late. Version 1.5
decides both a cycle early (D-43). Its limit is now the decode of the instruction itself:
from the fetched instruction register to a pin's output register (`insn` to `pin_val`, 51
cells, 25.5 ns at the slow corner), in which three minimum-size AND-OR-invert gates take
2.1, 1.7 and 1.4 ns. Hold slack does not depend on the period.
Firmware takes the tick length as a constant (`CLK_KHZ` in the timing tables, and the tick
counts in hand-written programs), so a faster board clock needs only rebuilt firmware, not a
new chip. These figures leave out the Tiny Tapeout multiplexer and pads, whose delays the
flow does not model (the Tiny Tapeout clock specification gives 66 MHz).

Layout effort alone did not move v1.4 this far: a trial harden of the v1.4 RTL with a 32 ns
target (`v14j`) ended with its limiting path at −0.42 ns at the slow corner, about 30.5 MHz.
Version 1.5 is placed, routed and signed off at 30 ns, so it is rated up to 33 MHz. The
shipped firmware still runs at 25 MHz: VGA Pong uses the core clock as its pixel clock
(VGA's is 25.175 MHz), and the hand-written programs count 40 ns ticks (D-5).

At 30 ns the checks other than setup hold too, at all three corners: minimum pulse width
(at least 14.5 ns to spare, the SRAM clock pins included), the clock gates, and setup and
hold at the SRAM inputs (12.5 ns setup slack at the slow corner, hold at least +0.33 ns).
A clock that is high for 40% or 60% of the period instead of half gives the same setup and
hold slack at the slow and fast corners. The Tiny Tapeout demo board should be set to
exactly 33 MHz, which its RP2040 makes without jitter (132 MHz divided by 4); a request for
33.333 MHz gets a fractional divider unless the RP2040 is overclocked, and its jitter could
shorten a cycle below 30 ns (D-5).

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
design has no decap capacitance, which is acceptable at 25 MHz with a 0.12 mV IR drop.

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
