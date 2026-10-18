# Physical design

Escapement is implemented with the Tiny Tapeout flow for IHP SG13CMOS5L (LibreLane
3.1.0.dev3, OpenROAD, IHP-Open-PDK `2bbec755`; [toolchain](05-TOOLCHAIN.md)) on a
**6×4-tile** allocation. Flow configuration: [`src/config.json`](../src/config.json),
with the reasoning for each setting in its comments and in
[design decisions](06-DECISIONS.md).

## Sign-off results

Version 1.2 (pin bank B), local harden `v12b`:

| | |
| --- | --- |
| Die | 1289.28 × 710.64 µm (916,214 µm²) |
| Standard cells | 25,601 (424,370 µm²), of which 3,912 sequential |
| Macros | 2 × `RM_IHPSG13_1P_256x32_c2_bm_bist` (98,977 µm²) |
| Utilisation | 58.0% |
| Clock | 25 MHz (40 ns) |
| Setup | No violations at any corner. Worst slack: typical +2.45 ns, fast +8.64 ns; at the slow corner the worst path ends on a clock-gate latch and uses time borrowing (see below) |
| Hold | No violations. Worst slack: fast +0.097 ns, typical +0.220 ns, slow +0.449 ns |
| Hold buffers | 1,528 (resizer hold margin 0.2 ns; see below) |
| Routing | 2.13 m of wire, 229,833 vias; detailed-route DRC 0 |
| LVS | Clean (0 errors, 0 net or device differences) |
| DRC | Clean (KLayout SG13CMOS5L deck in the Tiny Tapeout precheck) |
| Antenna | 0 violations (57 diodes) |
| IR drop | 0.12 mV worst |
| Power (static estimate) | 2.63 mW; workload measurements in [power](15-POWER.md) |
| Precheck | Tiny Tapeout precheck (tt-support-tools `d66cf17`) passes: all nine checks, including KLayout SG13CMOS5L DRC |
| Gate level | The chip-level and pin test suites pass on the final netlist, including the execution digest against the ISS and both pin banks at once; the netlist is proved equivalent to the RTL |

Version 1.1 (`rc14`: 23,978 cells, 55.2%, fast-corner hold +0.025 ns, 395 hold buffers) was
reproduced exactly, metric for metric, by the GitHub Actions harden from a clean checkout
with the pinned toolchain (258 minutes on a standard public runner). Version 1.2 has
1,623 more cells (6.8%): 1,133 are hold buffers from the larger hold margin, and 490 are
the pin bank B logic and its repair buffers.

Corners: `nom_slow_1p08V_125C`, `nom_typ_1p20V_25C`, `nom_fast_1p32V_m40C`. The
remaining design-rule warnings are 157 max-slew and 39 max-capacitance nets at the slow
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
design has no decap capacitance, which is acceptable at 25 MHz with a 0.12 mV IR drop.

Other routing settings:

- **No clock non-default rule.** With about 1,000 clock nets (one subtree per clock gate), the
  double-spacing rule cost more routing resource than it is worth at 25 MHz.
- **Wires longer than 500 µm are buffered** during design repair, which cut slow-corner
  slew warnings from 314 to 124 and improved fast-corner hold slack.

## Timing constraints

[`src/escapement.sdc`](../src/escapement.sdc) is LibreLane's base SDC with one change:
the clock uncertainty is 0.25 ns for setup and **0.05 ns for hold**. A hold check
launches and captures on the same clock edge, so jitter cancels; skew is propagated with
CRPR, and on-chip variation stays covered by the 5% timing derate. Applying the full
setup margin to hold had put about 4,000 unnecessary delay cells on register self-loops.

**Hold margin after routing.** Hold is repaired once, after clock-tree synthesis
(`PL_RESIZER_HOLD_SLACK_MARGIN`), and detailed routing then takes up to 0.17 ns of hold
slack. With a 0.1 ns margin the version 1.2 layout ended with 33 fast-corner paths at up
to −0.036 ns, all from a context's register bank into its clock-gated event FIFO, where
the capture clock passes the gate and a deeper tree. The margin is 0.2 ns, which leaves
+0.097 ns after routing.

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
