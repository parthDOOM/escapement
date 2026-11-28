# Power: measure first, then cut what dominates

A protocol engine spends most of its time waiting, so its power while waiting matters
more than its power while busy. This chapter shows how the chip's power was measured,
what the measurements found, and the changes that cut it: clock-gated register
banks, memories read only on demand, and a hold margin that stopped thousands of
needless delay cells (D-28, D-29 and D-40), and in version 3.2 the same clock gating
carried to nearly every remaining register, with a waiting context that no longer reads
the program store (D-73 to D-76). The decisions are in [06-DECISIONS](06-DECISIONS.md). Every number comes from OpenROAD and OpenSTA on the
project's own layout databases, at the typical corner (1.20 V, 25 °C) and 25 MHz; the
scripts are in `scripts/` and the runs can be repeated. Version 3.2's own figures are from
its signed-off layout, with the parasitics extracted from its routed wires. Every
comparison between versions is from the layout after clock-tree synthesis, with the
parasitics estimated from placement, because that is how every version was measured.
Throughout, "measured" means this analysis: the switching activity of a gate-level
simulation, fed to OpenSTA's power report. None of these figures is from a board.

The measurements in sections 1 to 3 were taken on versions 1.1 to 1.6, on the 6 × 4
layouts, as each change was made. The current design, version 3.2 at 8 × 4, was measured
on its signed-off layout (`scripts/power-workload.sh RUN final`: the final netlist and
database, and the typical-corner parasitics extracted from the routed wires), with the same
three workloads and the same 8,000-cycle windows:

| Version 3.2, signed off | uart_tx (sending) | logic analyser (capturing) | idle (stopped) | static estimate |
| --- | ---: | ---: | ---: | ---: |
| flip-flops and latches | 0.893 mW | 0.746 mW | 0.705 mW | |
| clock tree | 0.799 mW | 0.707 mW | 0.697 mW | |
| logic | 0.450 mW | 0.322 mW | 0.005 mW | |
| SRAM macros (counted edge by edge) | 0.0175 mW | 0.0015 mW | 0 | |
| **total** | **2.160 mW** | **1.777 mW** | **1.407 mW** | 1.50 mW |

The routed wires weigh more than placement predicts. The flip-flops and latches cost the
same as in the estimate below, and nearly all of the difference, 11 to 17%, is switching
power in the clock tree and the logic.

To compare with earlier versions, version 3.2 was also measured the way they were, after
clock-tree synthesis with the parasitics estimated from placement, beside version 3.1:

| Version 3.2 (3.1), estimated wiring | uart_tx (sending) | logic analyser (capturing) | idle (stopped) | static estimate |
| --- | ---: | ---: | ---: | ---: |
| flip-flops and latches | 0.897 mW (2.49) | 0.750 mW (2.45) | 0.707 mW (2.40) | |
| clock tree | 0.646 mW (0.97) | 0.568 mW (0.97) | 0.559 mW (0.96) | |
| logic | 0.288 mW (0.66) | 0.216 mW (0.54) | 0.004 mW (0.00) | |
| SRAM macros (counted edge by edge) | 0.0175 mW (0.30) | 0.0015 mW (0.0015) | 0 (0) | |
| **total** | **1.848 mW (4.42)** | **1.535 mW (3.95)** | **1.270 mW (3.36)** | 1.32 mW (3.43) |

Version 3.2 spends 58 to 62% less than version 3.1 in these three workloads, and less than
any earlier version, including v1.1 with its first clock gates (2.92, 2.58 and 2.23 mW),
although it has far more storage and logic. Section 4 shows where the saving came from and four more
workloads that isolate waiting.

Version 3.1, in turn, measured within 0.02 mW of version 3.0 (4.42, 3.97 and 3.38 mW): span,
the reset synchroniser and the layout that meets every electrical rule cost almost nothing.
Against version 1.6 (3.60, 3.29 and 2.93 mW) it was 0.43 to 0.82 mW more, 15 to 23%. Most
of that was the clocked storage the later versions added: pair mode, live reload, the four
pages, the proof tripwires, the bus timing monitor and the half-tick sampler brought the
sequential cells to 5,398, against 4,838 in version 1.6 (2,391 flip-flops, 2,365 latches and
82 clock-gate latches). The program store added the rest of the busy workload's difference.
It is one 1024 × 32 macro, whose read costs 48.3 pJ against 26.0 pJ for a 256 × 32, so the
same 2,000 fetches in the `uart_tx` window cost 302 µW instead of 162 µW. That is the state
version 3.2 started from: a stopped chip read no memory and switched no logic, and what it
spent was the clocked storage that kept running and the clock tree.

## 1. Where the power went

A protocol emulator spends most of its life waiting: a 115,200 baud UART changes a pin
every 217 clocks. A good engine should cost little while it waits. The v1.1 chip as
first written did the opposite. We ran three workloads on its post-CTS gate-level
netlist and fed the switching activity from simulation to OpenSTA's `report_power`
(`scripts/power-workload.sh`):

| v1.1 before D-29 | uart_tx (sending) | logic analyser (capturing) | idle (stopped) |
| --- | ---: | ---: | ---: |
| flip-flops (internal) | 5.07 mW | 5.04 mW | 5.02 mW |
| clock tree | 1.67 mW | 1.67 mW | 1.67 mW |
| SRAM macros | 1.30 mW | 1.30 mW | 1.30 mW |
| logic | 0.57 mW | 0.44 mW | 0.00 mW |
| **total** | **8.61 mW** | **8.45 mW** | **7.99 mW** |

A stopped chip burned 93% of the power of a busy one. Logic, the part that does the
work, was under 7%. Three causes, none of them about the work:

1. **Every flip-flop sees every clock edge.** 3,799 flops at about 1.3 µW each,
   whether or not they change. Most of them are written rarely. A context's FIFO entry
   is written at most once per 4 ticks, a mailbox word once per host transfer, and an
   architectural register only by its own context's commits. The synthesised "hold"
   path is a multiplexer that writes the old value back every cycle.
2. **Both SRAMs read every cycle.** The macro's liberty charges 26.0 pJ per read edge,
   which is 0.65 mW per macro at 25 MHz, and 0 for a cycle with `A_MEN`, `A_REN` and
   `A_WEN` all low. The program store read on all four barrel slots, even for contexts
   that were idle or halted. The tape read every cycle, even with no LDT in flight.
3. **Hold buffers.** 4,514 delay cells (106k µm²) protected register self-loops against
   a jitter margin that does not apply to hold checks (D-28 below). They are mostly an
   area and congestion problem, not a power problem.

## 2. What changed

### D-29a: clock-gated register banks (`src/esc_greg.v`)

`esc_greg` is a register bank with an enable. In the layout the enable stops the bank's
clock, through the textbook latch-based gate: a latch transparent while `clk` is low
holds `en` through the high phase, so `gclk = clk & en_latched` cannot glitch. The
PDK's own clock-gate cells are on its LibreLane exclusion lists, so the gate is built
from two allowed cells, instantiated by name (`sg13cmos5l_dllrq_1`,
`sg13cmos5l_and2_1`) so that synthesis cannot restructure the clock path.

68 gates covered about 2,600 flops in v1.1, 17 per context: r0–r3, sh, tmr, lee, ish, the
first-failure record, the four FIFO entries and the four mailbox words. In v1.4, 80 gates
cover about 3,150 of the 4,374 storage cells, and 2,365 of those are latches (below); v1.5's
28 added flip-flops are ungated, so its final netlist has 3,151 of 4,402 behind a gate, and
v1.6's has 3,292 of 4,756 (927 of its 2,391 flip-flops, and every latch;
`scripts/clock_gating.py` counts them in a netlist). Each enable is
exactly the condition under which the old always block assigned the bank, with the same
priority (reset, then start, then that edge's effects). So the change is invisible
architecturally:

- **Proved equivalent and glitch-free.** `formal/esc_greg.sby` proves for all time, by
  k-induction with an unconstrained clock (`multiclock on`), that the gated bank equals
  an enable flip-flop at every instant (G1). It also proves that the gated clock is
  never high while `clk` is low (G2) and rises only at a rising edge of `clk` (G3). A
  naive `clk & en` gate fails G3 at step 1, so the proof has teeth.
- **Every other proof and model is unchanged.** Under `FORMAL` the banks elaborate as
  plain enable flops (the single-clock proofs P2, H1–H4 run on the same logic), and
  `ESC_NO_CG` turns every bank back into enable flops for the whole chip.
- **Simulation.** The ISS-vs-RTL lockstep co-simulation (10 firmwares plus random
  programs), the RTL unit, host and chip-level suites, and the gate-level workload
  simulations all pass with the gates in.

OpenROAD's CTS recognised all 68 gated nets and built a subtree for each. The
free-running register tree dropped from about 3,800 sinks to 1,179.

### D-40: latch banks (`esc_lreg` in `src/esc_greg.v`)

In 2,365 bits of those banks the storage is a latch (`sg13cmos5l_dlhrq_1`, 27.2 µm²)
instead of a flip-flop (49.0 µm²). It is transparent while its gated clock is high and
copies the value written at the last edge from one flip-flop register per write port,
shared by the four contexts. The gate already stopped these banks' clocks, so the change
buys area, 13% of the design, not power. `formal/esc_lreg.sby` proves the latch bank equal
to an enable flip-flop under its input contract, and the core proof shows every bank
meets it (docs/04-VERIFICATION.md).

### D-29b: SRAMs read only on demand

- **Program store.** It is read only for a slot whose context is RUNNING (`imem_re =
  f_valid` in `esc_core.v`). A slot that is not running is never committed, so its
  instruction is never needed. A one-context program leaves the macro idle three
  cycles in four, and a halted chip leaves it idle.
- **Tape.** It is read only for an LDT commit (writes are unchanged), and from v1.4 for the line unit's own accesses.
- **Host.** Either macro reads for the host only during READ_IMEM / READ_TAPE (`mem_re`
  in `escapement_host.v`; the first word is sampled a full SPI byte after its address
  is set).

The wrapper (`escapement_sram.v`) deasserts `A_MEN` on a cycle with no access. Read data
holds, as the timing contract always said it does between reads.

### D-28: hold margin, not power, but it pays for the gates

LibreLane's base SDC applies the 0.25 ns clock uncertainty to setup and hold. On the
v1.0 post-CTS database (`scripts/hold_sweep.tcl`), 3,554 endpoints were within the
resizer's 0.1 ns margin at the fast corner, and that population became the 4,044 hold
buffers. Almost all of them sit in a narrow band of slack. With 0.05 ns of hold
uncertainty, 82 remain. `src/escapement.sdc` splits the two (0.25 ns setup, 0.05 ns
hold) and documents why: a hold check launches and captures on the same edge, so jitter
cancels, skew is propagated with CRPR, and on-chip variation is the 5% derate, which
stays.

## 3. Results

Post-CTS, typical corner, 25 MHz. The workload columns use switching activity from
gate-level simulation of the same netlist. "Static" is OpenSTA's default activity
estimate, which LibreLane prints in its power report.

| design | cells after CTS | utilisation after CTS | hold buffers | static | uart_tx | logic analyser | idle |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| v1.1 before | 32,299 | 66.1% | 4,514 | 7.55 mW | 8.61 mW | 8.45 mW | 7.99 mW |
| + D-28 only | 27,025 | 57.5% | 197 | 7.21 mW | n/m | n/m | n/m |
| + D-29a, tape gating | 23,326 | 54.7% | 486 | 2.77 mW | 3.56 mW | 3.42 mW | 2.89 mW |
| + D-29b fetch/host gating (v1.1) | 23,484 | 54.7% | 383 | 2.44 mW | 2.92 mW | 2.58 mW | 2.23 mW |
| v1.2: pin bank B (D-31), hold margin 0.2 ns | 25,399 | 58.3% | 1,515 | 2.48 mW | 3.01 mW | 2.67 mW | 2.30 mW |
| v1.3: streaming capture (D-32) | 25,383 | 58.9% | 1,614 | 2.57 mW | 3.11 mW | 2.77 mW | 2.39 mW |
| v1.4: line unit (D-33), latch banks (D-40, D-41) | 27,461 | 57.4% | 1,453 | 2.64 mW | 3.13 mW | 2.82 mW | 2.48 mW |
| v1.5: tests a cycle early (D-43) | 27,726 | 57.6% | 1,523 | 2.68 mW | 3.18 mW | 2.87 mW | 2.53 mW |
| **v1.6: decode and operands a stage earlier, 50 MHz layout** | **30,752** | **63.3%** | **2,148** | 3.09 mW | **3.60 mW** | **3.29 mW** | **2.93 mW** |
| change, before → v1.6 | −5% | | −52% | −59% | **−58%** | **−61%** | **−63%** |
| version 3 (8 × 4) | 32,676 | 56.2% | 257 | 3.58 mW | 4.42 mW | 3.97 mW | 3.38 mW |
| version 3.1: span, reset synchroniser, electrical rules | 34,511 | 57.4% | 246 | 3.43 mW | 4.42 mW | 3.95 mW | 3.36 mW |
| **version 3.2: registers clocked only when written, waiting contexts parked (D-73 to D-77)** | **35,631** | **59.4%** | **383** | **1.32 mW** | **1.85 mW** | **1.54 mW** | **1.27 mW** |

n/m = not measured. The workload columns use the exact SRAM count below. Version 1.6 costs
0.40–0.42 mW (13–16%) in every workload, nearly all of it in the flip-flop and clock rows.
It has 2,391 flip-flops against v1.5's 2,037, and 213 of the 354 new ones see every clock
edge (1,464 free-running against 1,251), mostly the registered decode and first operand (84)
and the event FIFOs' registered heads (104: drive mask, value and enable, and the event
kind); the other 141 sit behind clock gates. That is the price of the shorter cycle: at
50 MHz the same work takes half the time. The layout is placed and routed for a 20 ns clock
(v1.5: 30 ns), and OpenSTA takes the clock pins' activity from the constraints' clock, not
from the simulation, so both are measured with that clock set back to 40 ns. At 20 ns
OpenSTA's static estimate is 6.17 mW. Version 1.5 adds 28 flip-flops and costs 0.04–0.05 mW
(2%) in every workload.
Version 1.4
adds the line unit, 2,600 cells, and costs 1–4% over v1.3: the unit sits behind a clock
gate that closes while it is idle, and the latch banks (D-40) replace flip-flops whose
clock was already gated, so they save area rather than power. Stopped, the 11 new clock
gates still see the clock; a first v1.4 layout also reloaded the debug read register in
every stopped cycle (2.51 mW), which now happens only when the host selects another field.
Between two layouts of the same RTL the busy workloads move by about 2%. Pin bank B
and the larger hold margin cost 0.07–0.09 mW (3%) in every workload: 1,132 more hold
buffers and the bank B logic, all on flops that are clocked anyway. Streaming capture
adds another 0.09–0.10 mW (3%). The hold buffers of the second repair, after global
routing (113 in v1.3), come later and are not in these columns. The D-29a
row was measured before the counter and before the logic-analyser window was moved
after the load. Neither changes it by more than 2 µW: its program store reads every
cycle, which OpenSTA prices correctly, and the missed peer is 14 tape writes.

SRAM reads in the 8,000-cycle windows, both macros together, counted edge by edge
(`scripts/sram_energy.py`):

| | uart_tx | logic analyser | idle |
| --- | ---: | ---: | ---: |
| before | 16,000 reads (1.30 mW) | 15,986 reads + 14 writes (1.30 mW) | 16,000 reads (1.30 mW) |
| final | 2,000 reads (0.16 mW) | 14 writes (1.5 µW) | none (0) |

The logic analyser shows the point best. Its firmware is four instructions: arm the
capture unit, then `HALT`. From then on the hardware records every edge into the tape
with no instruction fetch at all. In the old design both memories still read 16,000
words to do nothing. Synthesis alone went from 22,790 to 18,224 cells (−20%) when the
enable multiplexers disappeared. Gating adds about 290 hold buffers back, because gated
and free sinks reach the clock at different latencies, and 600 clock buffers for the
68 subtrees. Both are small next to what D-28 removes.

What is left while waiting is mostly the 1,179 free-running flops (the time base,
pipeline, host link, and per-context control and telemetry) and the main clock tree.
That is the honest floor of a design whose host link and time base must keep running.

## 4. Version 3.2: clock only what is written

By version 3.1 the chip had grown to 5,398 storage cells, and 1,738 of its flip-flops still saw
every clock edge. In a stopped chip they and their clock leaves cost 2.74 mW of the 3.36, and
the clock trunk another 0.41. Grouped by what they hold, the free-running registers were the
contexts' state (1.08 mW), the host interface (0.30), the pipeline (0.29), the bus monitor
(0.25), the capture unit (0.24), the pins and their synchronisers (0.23) and smaller groups.
Each flip-flop cost about 1.3 µW plus 0.26 µW of leaf clock, whether it changed or not. A
waiting context added 0.30 mW more, because it fetched its `WAIT` from the program store again
in every one of its slots.

Four workloads beyond the standard three show the cost of waiting directly. In `run_halted` the
chip runs with its one context halted, so nothing is fetched. In `wait1` one context sits in a
long `WAIT`; in `wait4` four contexts do; in `fifo4` four contexts are blocked on full FIFOs:

| mW, 25 MHz, estimated wiring | idle | run_halted | wait1 | wait4 | fifo4 | uart_tx | logic analyser |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| version 3.1 | 3.36 | 3.92 | 4.30 | 5.23 | 5.36 | 4.42 | 3.96 |
| after D-73 and D-74 | 2.66 | 2.92 | 2.99 | 3.35 | 3.51 | 3.06 | 2.94 |
| version 3.2 (with D-75 to D-77) | 1.27 | 1.52 | 1.71 | 2.42 | 2.90 | 1.85 | 1.54 |
| change from 3.1 | −62% | −61% | −60% | −54% | −46% | −58% | −61% |

The middle row was measured on a layout of its own, with the park, the rarely written banks
and the held select in place but the contexts and the pipeline still free-running. It shows
the two halves of the work. The park removes the program-store reads of a waiting context
(wait4: 8,000 reads in the window, 1.21 mW, to none), and the rarely written banks and the
held select take another 0.6 to 1.0 mW off every workload. The per-context and pipeline banks then halve what was left.
In `uart_tx` the store is now read 116 times in the window instead of 2,000, because the
program spends most of its time parked in a `WAIT`.

What a stopped version 3.2 still spends is mostly what must run. The free-running storage
falls from 2.29 to 0.54 mW and its leaf clocks from 0.45 to 0.13 mW. The largest remaining
groups are the pins and their synchronisers (0.16 mW with leaves), the nine flip-flops per
context that stay on the clock (0.13), the host link (0.07), and the digest and tripwires
(0.05). The clock trunk (0.35 mW) and the clock gates themselves (0.16 mW for 128 gates, against 90
in version 3.1) are now two fifths of a stopped chip's power.

The gated banks cost power only when their work happens, which is why the savings shrink
as the chip gets busier. With four contexts waiting, the pipeline stages are valid in every
slot and the pipeline banks clock at almost every edge (0.32 mW with their leaves, against none
when stopped). With four FIFOs full, each context's FIFO bank clocks at every edge, because the
head's due and late tests run on each one, and the contexts' gated banks cost 0.42 mW with
their leaves, against 0.10 mW with four contexts waiting. So `fifo4`, the busiest workload, saves
the least (46%), and an idle or lightly loaded chip saves the most (about 60%).

## 5. How to read the timing report with clock gates

The worst setup path ends at a clock-gate latch, with slack measured to the latch's
opening edge (the falling edge, 20 ns) and up to 19.9 ns of borrowing available. The
true limit is the closing edge, and borrowing is harmless for a clock gate: while `clk`
is low the AND output is low whatever the latch passes. A low headline "setup slack" is
this reporting convention, not a slow path.

## 6. Limits of these numbers

- One corner (typical), placement-estimated parasitics, no IR-drop interaction.
- Zero-delay gate-level simulation counts no glitch power, which slightly flatters
  logic for every row alike. Logic is a small share in every row.
- SRAM power is whatever the vendor liberty says: state-dependent internal power on
  `A_CLK` (26.0 pJ read, 33.4 pJ write, 0 when unselected). We did not characterise
  the macro.
- **OpenSTA misprices correlated macro pins.** It evaluates `when : "A_MEN & !A_WEN &
  A_REN"` from per-pin probabilities multiplied as if independent. After D-29b, A_MEN
  and A_REN come from the same read-enable, so a 25% read duty is priced at 6.25%:
  43 µW instead of 162.5 µW for uart_tx. `scripts/sram_energy.py` counts every rising
  A_CLK of each macro from the VCD with its (MEN, WEN, REN) state and uses the liberty's
  energy for that state. It matches OpenSTA exactly (1,300.4 µW) where the pins are
  uncorrelated (the old design), and replaces OpenSTA's Macro row in the table above.
- Workload windows are 8,000 cycles (320 µs) after start. They are representative of
  steady state for these programs, not of every program.

## 7. Reproduce

```sh
scripts/local-harden.sh --tag NAME [--to OpenROAD.ResizerTimingPostCTS]
scripts/power-workload.sh runs/NAME            # static + 3 workloads -> runs/NAME/power/
openroad hold_sweep.tcl                        # env vars in its header
sby -f formal/esc_greg.sby                     # G1-G3
```
