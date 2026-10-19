# Physical design

Escapement is implemented with the Tiny Tapeout flow for IHP SG13CMOS5L (LibreLane
3.1.0.dev3, OpenROAD, IHP-Open-PDK `2bbec755`; [toolchain](05-TOOLCHAIN.md)) on a
**6×4-tile** allocation. Flow configuration: [`src/config.json`](../src/config.json),
with the reasoning for each setting in its comments and in
[design decisions](06-DECISIONS.md).

## Sign-off results

Version 1.3 (streaming capture), local harden `v13b`:

| | |
| --- | --- |
| Die | 1289.28 × 710.64 µm (916,214 µm²) |
| Standard cells | 25,931 (435,322 µm²), of which 3,964 sequential |
| Macros | 2 × `RM_IHPSG13_1P_256x32_c2_bm_bist` (98,977 µm²) |
| Utilisation | 59.7% |
| Clock | 25 MHz (40 ns) |
| Setup | No violations at any corner. Worst slack: typical +3.62 ns, fast +9.34 ns; at the slow corner the worst path ends on a clock-gate latch and uses time borrowing (see below) |
| Hold | No violations. Worst slack: fast +0.175 ns, typical +0.331 ns, slow +0.576 ns |
| Hold buffers | 1,727: 1,614 after clock-tree synthesis (margin 0.2 ns), 113 after global routing (margin 0.2 ns, see below) |
| Routing | 2.13 m of wire, 216,809 vias; global-route overflow 4; detailed-route DRC 0 |
| LVS | Clean (0 errors, 0 net or device differences) |
| DRC | Clean (KLayout SG13CMOS5L deck in the Tiny Tapeout precheck) |
| Antenna | 0 violations (102 diodes) |
| IR drop | 0.14 mV worst |
| Power (static estimate) | 2.73 mW; workload measurements in [power](15-POWER.md) |
| Precheck | Tiny Tapeout precheck (tt-support-tools `d66cf17`) passes: all nine checks, including KLayout SG13CMOS5L DRC |
| Gate level | The chip-level and pin test suites pass on the final netlist, including the execution digest against the ISS and both pin banks at once; the netlist is proved equivalent to the RTL |

Version 1.1 (`rc14`: 23,978 cells, 55.2%, fast-corner hold +0.025 ns, 395 hold buffers) was
reproduced exactly, metric for metric, by the GitHub Actions harden from a clean checkout
with the pinned toolchain (258 minutes on a standard public runner). Version 1.2 (`v12h`:
25,864 cells, 58.9%, fast-corner hold +0.068 ns) had 1,886 more cells than version 1.1:
1,154 hold buffers from the larger hold margin, and 732 for pin bank B and its repair
buffers. Version 1.3 adds the streaming logic (D-32) for 67 more cells, and needs 79 more
hold buffers after global routing (see below).

Corners: `nom_slow_1p08V_125C`, `nom_typ_1p20V_25C`, `nom_fast_1p32V_m40C`. The
remaining design-rule warnings are 123 max-slew and 29 max-capacitance nets at the slow
corner. They cause no timing violations. The worst are nets that the detailed router
detours around the congested horizontal layer: one 375 µm connection is routed as 1.8 mm.

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
