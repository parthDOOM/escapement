# Physical design

Escapement is implemented with the Tiny Tapeout flow for IHP SG13CMOS5L (LibreLane
3.1.0.dev3, OpenROAD, IHP-Open-PDK `2bbec755`; [toolchain](05-TOOLCHAIN.md)) on a
**6×4-tile** allocation. Flow configuration: [`src/config.json`](../src/config.json),
with the reasoning for each setting in its comments and in
[design decisions](06-DECISIONS.md).

## Sign-off results

Version 1.4 (line unit, latch banks), local harden `v14i`:

| | |
| --- | --- |
| Die | 1289.28 × 710.64 µm (916,214 µm²) |
| Standard cells | 27,972 (422,057 µm²); storage: 2,009 flip-flops and 2,365 latches, plus 80 clock-gate latches |
| Macros | 2 × `RM_IHPSG13_1P_256x32_c2_bm_bist` (98,977 µm²) |
| Utilisation | 58.2% |
| Clock | 25 MHz (40 ns) |
| Setup | No violations at any corner. The latch banks' inputs borrow time (D-40) and report 0 slack; every other path has at least +9.11 ns (slow), +10.10 ns (typical), +10.73 ns (fast) |
| Hold | No violations. Worst slack: fast +0.138 ns, typical +0.266 ns, slow +0.502 ns |
| Hold buffers | 1,453 after clock-tree synthesis (margin 0.2 ns); none needed after global routing |
| Routing | 2.22 m of wire, 243,088 vias; global-route overflow 1,683; detailed-route DRC 0 |
| LVS | Clean (0 errors, 0 net or device differences) |
| DRC | Clean (KLayout SG13CMOS5L deck in the Tiny Tapeout precheck) |
| Antenna | 0 violations (54 diodes) |
| IR drop | 0.12 mV worst |
| Power (static estimate) | 2.64 mW; workload measurements in [power](15-POWER.md) |
| Precheck | Tiny Tapeout precheck (tt-support-tools `d66cf17`) passes, including KLayout SG13CMOS5L DRC |
| Gate level | The chip-level and pin test suites pass on the final netlist; the netlist is proved equivalent to the RTL |

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
remaining design-rule warnings in v1.4 are 163 max-slew and 41 max-capacitance nets at the
slow corner (v1.3: 123 and 29). They cause no timing violations. The worst are nets that the detailed router
detours around the congested horizontal layer: one 375 µm connection is routed as 1.8 mm.

**Frequency headroom.** 25 MHz is the rating at the slow corner with margin to spare (D-5),
not the limit of the layout. Static timing analysis of the final v14i (v1.4) netlist with
its extracted parasitics (OpenSTA, the sign-off constraints with the latches modelled, the
period shortened and the 20% I/O delays scaled with it) finds the shortest period at which
setup still holds, latch inputs included (a latch input that borrows time reports 0 slack;
past its limit, negative):

| Corner | Shortest passing period | Clock | Fails at | v1.3 (v13b) |
| --- | ---: | ---: | ---: | ---: |
| Slow, 1.08 V, 125 °C | 35 ns | 28.6 MHz | 34.5 ns | 35 MHz |
| Typical, 1.20 V, 25 °C | 22 ns | 45 MHz | 21.5 ns | 55 MHz |
| Fast, 1.32 V, −40 °C | 14.3 ns | 70 MHz | 14 ns | 83 MHz |

The rating keeps 14% at the slow corner. v1.3's limit was the enable of a clock-gated
register bank; v1.4's is a single-cycle path from a context's tick counter through the
deadline comparison and the retire and fault logic to a pin's output register (`now` to
`pin_val`, about fifty gates, 34.4 ns at the slow corner), in which two minimum-size cells
on long wires take 3.8 and 1.8 ns. Hold slack does not depend on the period.
Firmware takes the tick length as a constant (`CLK_KHZ` in the timing tables, and the tick
counts in hand-written programs), so a faster board clock needs only rebuilt firmware, not a
new chip. These figures leave out the Tiny Tapeout multiplexer and pads, whose delays the
flow does not model (the Tiny Tapeout clock specification gives 66 MHz).

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
  and 30 µm vertical row-cut halo, removes those strips and leaves a cell-free escape
  channel on each macro's pin side.
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
design has no decap capacitance, which is acceptable at 25 MHz with a 0.13 mV IR drop.

Other routing settings:

- **No clock non-default rule.** With about 1,000 clock nets (one subtree per clock gate), the
  double-spacing rule cost more routing resource than it is worth at 25 MHz.
- **Wires longer than 500 µm are buffered** during design repair, which cut slow-corner
  slew warnings from 314 to 124 and improved fast-corner hold slack.

**Headroom.** Each SRAM blocks Metal1 to Metal3 over its whole footprint, so congestion
gathers around the tape SRAM. Version 1.3 ends global routing with an overflow of 4
(version 1.2: 68). A variant with about 1,000 more cells reached 1,900 to 3,200, and
detailed routing then left shorts over the tape SRAM in four of five runs. Global-route
overflow is the number to watch when adding logic.

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
