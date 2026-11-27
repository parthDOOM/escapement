# Physical design

This chapter describes how the RTL becomes a layout: the floorplan, placement, the clock
tree, routing, the timing constraints and the sign-off results. Escapement is built with
the Tiny Tapeout flow for IHP's SG13CMOS5L 130 nm process, pinned to the release of the
`ttihp27a` shuttle (LibreLane 3.1.0.dev4, OpenROAD, IHP-Open-PDK `89d42ee`; every pin is
listed in [05-TOOLCHAIN](05-TOOLCHAIN.md)). It occupies **8 × 4 tiles**, a die of
1724.16 × 710.64 µm. The flow configuration is [`src/config.json`](../src/config.json),
whose comments give the reason for each setting; the decisions behind them, with the
alternatives that were measured and rejected, are in [06-DECISIONS](06-DECISIONS.md).

## What limits this layout

The die is larger than the design needs: the standard cells and the two memories fill
64.4% of the core. The limit is wiring, not area. A Tiny Tapeout block in
this process has four signal layers below the top metal, and only one of them, Metal3,
runs horizontally at a useful pitch for signals. On a die more than twice as wide as it
is tall, nearly all routing congestion is horizontal, so every layout decision in this
chapter is, in the end, about keeping horizontal wires short. Two measurements show it.
Moving from 6 × 4 to 8 × 4 tiles did not make routing easier: the first 8 × 4 layouts
overflowed Metal3, because the placer spread the logic across the extra width. And in
version 3, the full bus timing monitor at first made the design unroutable at every
density tried, until the floorplan below put all the logic in one compact block (D-61).

## Floorplan

```text
  y=710 ┌──────────────────────────────────────────────────────────┬──────────────┐
        │ TT pins                                                   │ tape SRAM    │
        │ (top left)                                                │ 256 × 32 (N) │
        │                                                           │ pins down    │
        │                    standard cells                         ├──────────────┤
        │          (core, line unit, monitor, host link)            │              │
        │                                                           │ program SRAM │
        │                                                           │ 1024 × 32    │
        │                                                           │ (FS) pins up │
    y=0 └───────────────────────────────────────────────────────────┴──────────────┘
        x=0                                                     x≈1293        x=1724
```

- **Both memories in one column on the east side.** The program store, a single
  1024 × 32 macro holding the four pages, sits at the bottom right with its pins facing
  up; the tape, a 256 × 32 macro, sits above it at the top right with its pins facing
  down, so their pin edges face each other across a channel that opens onto the logic to
  the west. The logic is therefore one rectangle with no macro inside its horizontal
  paths, and every path from the core to either memory is short. An earlier floorplan with the tape at the far left anchored
  the line unit well in version 2 (D-51) but stretched the version 3 logic across the die
  (D-61).
- **One macro for the store, not four.** The store's contract is fixed (1,024 words, read
  in one cycle, data the next), and `escapement_pstore` can build it from four 256 × 32,
  two 512 × 32 or one 1024 × 32 macro. Only the single macro routes at 8 × 4: five macros'
  halos leave no room between them, and two 512 × 32 congest. The single macro is slower
  from clock to data, which costs nothing at 50 MHz, and is the smallest (D-50).
- **No stranded cells.** A strip of rows behind a macro traps cells whose only exits cross
  the macro. Each macro is flush to the core edge, with a 50 µm horizontal and 60 µm
  vertical halo that keeps a cell-free channel on its pin side.
- **The power grid follows the macros.** The Metal4 stripe pitch (67.44 µm) and offset
  (48.84 µm) are chosen so that every stripe crossing a macro lands on one of its own
  power columns ([`src/pdn_cfg.tcl`](../src/pdn_cfg.tcl)). Both macros are placed on that
  stripe phase. Their two supplies, `VDD!` and `VDDARRAY!`, connect to the single VPWR
  rail.

The die carries no decorative layout art. Every placement of it in this floorplan either
stranded rows that the power grid then fed with a stub stripe, which the shuttle's
precheck rejects, or crowded the logic next to the store and cost setup and hold margin,
so it was removed (D-61).

## Placement and the clock tree

- **Density 61.7%, timing-driven.** Global placement weights the nets on critical paths, so
  the core's commit logic packs together instead of spreading over hundreds of
  micrometres; on the first 8 × 4 floorplans this alone was worth 2.16 ns of setup (D-52).
  The density was chosen among nearby values: 60% and 61% each produced a placement that
  triggers the clock-tree defect in the next item, and 61.3% missed setup at the slow
  corner (D-65).
- **Clock-gate sinks clustered within 50 µm.** The design has 128 clock gates, one
  per gated register bank, and the clock-tree tool treats each gate's input as a
  macro-like sink. With its default clustering diameter of 833 µm it grouped gates far
  apart and moved each one to the centre of the bank it clocks. Clustering within 50 µm
  keeps the gated trees local. The same move exposes a known OpenROAD defect (issue
  #9816, fixed upstream but not in the flow's release): when two gates' banks have the
  same centre, clock-tree synthesis moves both gates there and loses one gate's clock
  input. The flow's disconnected-pin check, LVS and the netlist proofs all catch it, and
  the 61.7% density is a placement on which it does not occur (D-45, D-65).
- **Clock buffers** are the library's 8×, 4×, 2× and 1× buffers, with an 8× root and no
  non-default routing rule for clock nets: with one subtree per clock gate, double
  spacing would cost more routing than it saves. Each leaf buffer is sized for the load it
  actually drives, which makes most leaves 1× buffers where the tool's default picked 8×.
  That cut the clock tree's power by 6.6% and leaves 9 fanout flags, all at the roots of
  gated clocks (Sign-off results, below).

## Routing

Metal2 to Metal4 alone give one horizontal signal layer. Escapement also routes signals
on **Metal1**, which is horizontal at the same pitch as Metal3 and is accepted by the
precheck. On the 6 × 4 layouts this cut global-route overflow from 5,276 to 76, and every
layout since has relied on it.

Routing on Metal1 is safe only because the flow inserts **no decap cells**. LibreLane adds
fill after detailed routing, and a decap cell carries Metal1 power plates that the router
never saw; in an early layout they shorted signal wires to power in about 4,000 places,
which only LVS detected. Filler cells carry Metal1 only on their rails, and every logic
cell's Metal1 is fully described in its LEF, which was checked against the GDS for every
cell the design uses ([`scripts/m1_fill_overlap.py`](../scripts/m1_fill_overlap.py)). The
design therefore has no decap capacitance; the worst IR drop at 20 ns is 0.25 mV.

**Design repair** keeps every pin within the library's limits on slew, capacitance and
fanout, by buffering and resizing, once after placement and again after global routing.
Nothing repairs after detailed routing, which lengthens some nets beyond the global
route's estimate, so the second repair works with margins, and four settings make its
picture of each wire accurate:

- **Wire capacitance from extraction.** The resizer's wire model uses twice the
  capacitance in the technology LEF, because extraction of the version 3.0 layout measured a
  median of 0.185 fF/µm on long nets against the LEF's 0.092. Every step that sets wire
  parasitics loads all three corners.
- **Margins for the detours.** After global routing, repair keeps 40% margins on slew and
  capacitance and buffers any wire longer than 250 µm, and global routing derates each
  layer's capacity by 20%.
- **A slew limit for repair only.** OpenROAD's repair judges slew at the first corner the
  flow defines, which LibreLane makes the fast one, while slow-corner slew is 2.24 to 2.46
  times fast-corner slew on every pin measured. Against the library's 2.51 ns limit, repair
  would act only on pins already past the limit at the slow corner. The placement and
  routing constraints therefore set a maximum transition of 1.0 ns, which makes repair
  buffer a net while it is still about 1.4 ns at the slow corner. Sign-off keeps the
  library's limits.
- **Buffers before the memories.** The memories' input pins allow only 0.595 ns at the slow
  corner; how they are driven is under SRAM integration, below.

These came out of the work that met the electrical rules for version 3.1 (D-65): version
3.0 was signed off with 15 slow-corner slew violations, one capacitance violation and 155
fanout flags.

## Timing constraints

Placement and routing use [`src/escapement_pnr.sdc`](../src/escapement_pnr.sdc), which
times each latch from its gate, and sign-off uses
[`src/escapement.sdc`](../src/escapement.sdc) with the full latch model. The sign-off
constraints are LibreLane's base set with one change: the clock uncertainty is 0.25 ns for
setup and 0.05 ns for hold. A hold check launches and captures on the same clock edge, so
jitter cancels, skew is propagated with clock reconvergence pessimism removal, and
on-chip variation stays covered by the 5% timing derate. Applying the full setup
uncertainty to hold had put about 4,000 unnecessary delay cells on register self-loops.
The placement and routing constraints add only the 1.0 ns slew limit for design repair
described under Routing.

The reset input is timed like the other inputs, arriving 4 ns (20% of the period) after a
rising edge, but it no longer reaches the registers directly. Two flip-flops take it and
release the chip's internal reset on the second rising edge after `rst_n` rises (D-63), so
the release spreads through the reset tree as an ordinary timed path from one flip-flop.

**Two hold repairs.** Hold is repaired after clock-tree synthesis with a 0.1 ns margin,
and again after global routing with a 0.3 ns margin, where the routed clock feeds are
known. The second matters because routing takes hold slack from paths into the clock-gated
banks, by an amount that depends on how each gate's clock feed is routed; a margin applied
before routing cannot see it. A larger margin after clock-tree synthesis was measured and
rejected: at 0.2 ns it inserted 2,233 hold buffers instead of 289 (D-53).

**Setup margins** of 2.5 ns during placement and 3 ns after global routing keep the hold
repair from adding delay to paths that become critical after routing. With smaller
margins, it put delay cells at the start of exactly those paths (D-44).

**Reading the setup report.** The worst setup paths end at latches. A latch input that
borrows time reports zero slack, which is not a violation: a clock-gate latch, for
example, may pass its input late, because while the clock is low the gate's output is low
whatever the latch holds. The figures below therefore give the worst path into a
flip-flop separately.

## Sign-off results

The figures are from the CI harden of commit `1d644e8` and the local harden of the same
source with [`scripts/local-harden.sh`](../scripts/local-harden.sh), which produced an
identical netlist.

| Aspect | Result |
| --- | --- |
| Die | 1724.16 × 710.64 µm, 8 × 4 tiles; core area 1,208,170 µm² |
| Standard cells | 39,588 (588,882 µm²), of which 5,619 flip-flops, latches and clock-gate latches (2,835, 2,656 and 128), and 88 buffers before the memories' inputs |
| Macros | `RM_IHPSG13_1P_1024x32_c2_bm_bist` (program store) and `RM_IHPSG13_1P_256x32_c2_bm_bist` (tape), 189,671 µm² together |
| Utilisation | 64.4% |
| Clock | Placed, routed and signed off at 20 ns: rated up to 50 MHz, 25 MHz by default (D-5) |
| Setup | No violations at any corner. Worst path into a flip-flop at 20 ns: +3.015 ns at the slow corner, from the tape memory's output. At the typical and fast corners it is +6.583 and +6.327 ns, on another path: from a pin, whose input delay is 20% of the period, to the half-tick sampler's falling-edge flip-flop at half the period. That path keeps about 30% of the period as slack, so it loses only 0.3 ns for each nanosecond taken off the period, and it is not the one that limits the clock: the headroom sweep below, which shortens the period and the I/O delays together, latch inputs included, finds the memory path failing first at every corner, at 16.9, 10.6 and 7.0 ns |
| Hold | No violations. Worst slack: +0.216 ns at the fast corner (+0.348 ns typical, +0.532 ns slow) |
| Hold buffers | 3,002 delay cells: 383 inserted after clock-tree synthesis and 2,619 after global routing, which repairs hold to a 0.3 ns margin (D-53); version 3.1's netlist has 3,063 and version 3.0's 3,931 (counted in the netlists: each step's for version 3.2, the released ones for 3.1 and 3.0). LibreLane's `hold_buffer` metric reads 71, as does the repair's own log line for the step after global routing, although that step adds 2,619 delay cells; the metric read 12 for version 3.1 and 259 for 3.0, so it is not a count of the hold buffers in the chip |
| Electrical rules | Met. Max slew and max capacitance: no violation at any corner. Max fanout: 9 flags at every corner, all of them roots of gated clocks, 8× buffers driving 16 loads (the four FIFO entries of contexts 0 and 3, and the core's queued FIFO entry, each 58 bits). The limit they exceed, 8, is not measured for any cell: it is the library's one default (`default_max_fanout`), the same for its smallest buffer and its largest. Each of the nine passes slew and capacitance, the checks that keep the timing within the library's characterised tables, and they are accepted (D-65) |
| Routing | 2.38 m of wire, 299,493 vias; global-route overflow 0; detailed-route DRC 0 |
| LVS | Clean (0 errors, 0 net or device differences) |
| DRC | Clean (KLayout SG13CMOS5L deck in the Tiny Tapeout precheck) |
| Antenna | 0 violations (30 diodes) |
| IR drop | 0.25 mV worst, at 20 ns |
| Power | 2.99 mW (the flow's estimate at 20 ns, typical corner; version 3.1 7.36 mW); workload measurements in [15-POWER](15-POWER.md) |
| Precheck | The `ttihp27a` shuttle's precheck passes, every row |
| Gate level | The 9 firmware tests through the pins pass on this netlist, and the 12 chip tests pass with SDF timing at 20 ns at all three corners; Icarus leaves out five interconnect delays of 6 to 14 ps ([24-TOOL-BUGS](24-TOOL-BUGS.md#sdf-annotation-in-icarus-verilog-13)). As a control, the digest test on the slow-corner SDF at 8 ns fails (the I/O pins go unknown before the program halts), so the delays are live; at 12 ns it still passes, as on version 2, because the simulation exercises only this program's paths and lets the latches borrow time freely, where timing analysis is worst-case. The netlist is proved equivalent to the RTL ([04-VERIFICATION](04-VERIFICATION.md#rtl-to-netlist-equivalence)) |
| Reproduction | The CI harden's netlist and the local one have the same SHA-256 (`90aa97b297b9515a…`), from both CI hardens of this source (`50d1b52` and `1d644e8`) |

The corners are `nom_slow_1p08V_125C`, `nom_typ_1p20V_25C` and `nom_fast_1p32V_m40C`.

## Frequency headroom

[`scripts/frequency-headroom.sh`](../scripts/frequency-headroom.sh) runs static timing
analysis on a finished layout with its extracted parasitics, shortening the clock period
(and the I/O delays with it) until setup fails, latch inputs included. It gives the
fastest clock each corner supports:

| Corner | Shortest passing period | Clock | Fails at | Version 3.1 | Version 3.0 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Slow, 1.08 V, 125 °C | 17.0 ns | 58.8 MHz | 16.9 ns | 60.2 MHz | 53.8 MHz |
| Typical, 1.20 V, 25 °C | 10.7 ns | 93.5 MHz | 10.6 ns | 97.1 MHz | 86.2 MHz |
| Fast, 1.32 V, −40 °C | 7.1 ns | 140.8 MHz | 7.0 ns | 147.1 MHz | 129.9 MHz |

The sweep reads the signed-off netlist (SHA-256 `90aa97b297b9515a…`). Hold slack stays at
its sign-off value at every passing point: 0.532, 0.348 and 0.216 ns. At the slow corner the
20 ns rating keeps 3.0 ns, 15% of the period, in hand. Version 3.0 gained on its predecessors
mostly from the repair settings under Routing: long nets that were left slow at the slow
corner were buffered from then on, and a buffered net is also a faster one. Version 3.2 is
0.4 ns slower than 3.1 at the slow corner, with 221 more storage cells and 38 more clock
gates (D-73 to D-76) in the same die. Its first layout was 1.3 ns slower still, from a
single latch on the line unit's transmit-pin path and a chain of buffers the resizer built,
and both were removed (D-77).

The chip is rated at 50 MHz because it is placed, routed and signed off at 20 ns. A clock
that is high for 40% or 60% of the period, instead of half, also passes setup, hold and
minimum pulse width at all three corners (`DUTY=40` and `DUTY=60`). Firmware takes the
tick length as a constant (`CLK_KHZ` in the timing tables, and the tick counts in
hand-written programs), so a different board clock needs rebuilt firmware, not a new chip.
The Tiny Tapeout demo board should run the chip at exactly 50 MHz, which its microcontroller
(an RP2350 on the current ttDBv3, an RP2040 on earlier boards) makes without jitter
(100 MHz divided by 2), or 25 MHz; a clock made with a fractional divider
jitters, and a short cycle could fall below the 20 ns the layout is signed off at.

These figures cover the design inside its tiles. The Tiny Tapeout multiplexer and the pads
add delays the flow does not model. They limit what a board can use, not whether the chip
can be made, and they are not yet characterised for this shuttle
([16-BRING-UP](16-BRING-UP.md)).

## SRAM integration

The macro views are vendored unmodified from IHP-Open-PDK
([`macro/`](../macro/RM_IHPSG13_1P_256x32_c2_bm_bist/README.md)). The integration follows
the published SG13CMOS5L recipe of the Loom project, adapted to these macros:

1. `MACROS` in the flow configuration is keyed by each instance's flattened path, with a
   liberty file per process corner (`*_typ_*`, `*_fast_*`, `*_slow_*`), because the
   CMOS5L timing corners are all named `nom_*`.
2. `PDN_MACRO_CONNECTIONS` tie `VDD!` and `VDDARRAY!` to VPWR and `VSS!` to VGND.
3. A custom power-grid script aligns the Metal4 stripes with the macros' power columns.
   Tiny Tapeout's pin check requires every Metal4 power port to reach both the top and
   bottom edges of the block.
4. `MAGIC_EXT_ABSTRACT_CELLS` abstracts the macros during LVS extraction. Magic's DRC is
   disabled, because its CMOS5L technology file lacks the SRAM rule exceptions; the
   sign-off DRC is the KLayout deck in the precheck.
5. The macros' BIST and bit-mask pins are tied off. Simulation uses the vendored
   behavioural models.
6. Every switching input of both macros is driven by its own 4× buffer
   (`escapement_mbuf` in [`src/escapement_sram.v`](../src/escapement_sram.v)), and the net
   from the buffer to the pin is marked don't-touch for the resizer. The macro pins allow
   0.595 ns of slew at the slow corner, far less than a standard cell's 2.51 ns, and repair
   judges slew at the fast corner, where these pins pass, so a pin fed from across the core
   could miss at the slow corner unseen. The longest of these nets carry 80 to 137 fF; a 2×
   buffer still missed by 3 ps there. The buffers exist only in synthesis
   (`ESC_MACRO_BUFFERS`): simulation, the formal proofs and the equivalence check see a wire.

## How the layout got here

Versions 1.1 to 1.6 were laid out on 6 × 4 tiles with the two memories split, the store at
the bottom left and the tape at the top right. That layout taught the habits the current
one keeps: Metal1 routing without decap cells, the macro halos, the stripe-aligned power
grid, and two hold repairs. Each version's timing improved through the RTL rather than the
flow, from 28.6 MHz in version 1.4 to the 50 MHz rating of version 1.6 (D-43, D-44).
Version 2 moved to 8 × 4 tiles; a measured analysis of nine hardens showed that the extra
width spread the line unit far from the core, and the tape at the bottom left together
with timing-driven placement closed timing (D-51, D-52). Version 3's monitor reopened the
same problem from the other side, and the east-column floorplan above closed it (D-61).
Version 3.1 kept that floorplan and changed how the flow repairs: version 3.0 had been
signed off with electrical-rule violations, and a series of trial hardens traced them to
the resizer's wire model, the detours of detailed routing, repair that only looked at the
fast corner, and the memories' strict input pins. With all four addressed, the 3.1 layout
meets every electrical rule at every corner and closes 2 ns faster at the slow corner
(D-65). Every 6 × 4 harden, adopted or rejected, is recorded with its reason in
[`asic/reports/area.csv`](../asic/reports/area.csv), and so are version 2's frozen layout and
each adopted layout since.
