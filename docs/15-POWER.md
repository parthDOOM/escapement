# Power: measure first, then cut what dominates

Design decisions D-28 (hold margin) and D-29 (power) in
[design decisions](06-DECISIONS.md). Every number here comes from OpenROAD/OpenSTA on our
own harden databases, at the typical corner (1.20 V, 25 °C) and 25 MHz, with parasitics
estimated from placement. The scripts are in `scripts/` and the runs can be repeated.

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

`esc_greg` is a register bank with an enable. In silicon the enable stops the bank's
clock, through the textbook latch-based gate: a latch transparent while `clk` is low
holds `en` through the high phase, so `gclk = clk & en_latched` cannot glitch. The
PDK's own clock-gate cells are on its LibreLane exclusion lists, so the gate is built
from two allowed cells, instantiated by name (`sg13cmos5l_dllrq_1`,
`sg13cmos5l_and2_1`) so that synthesis cannot restructure the clock path.

68 gates covered about 2,600 flops in v1.1, 17 per context: r0–r3, sh, tmr, lee, ish, the
first-failure record, the four FIFO entries and the four mailbox words. In v1.4, 80 gates
cover about 3,150 of the 4,374 storage cells, and 2,365 of those are latches (below). Each enable is
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

### D-40: latch banks (`esc_lreg` in `src/esc_greg.v`, v1.4)

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

| design | cells | utilisation | hold buffers | static | uart_tx | logic analyser | idle |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| v1.1 before | 32,299 | 66.1% | 4,514 | 7.55 mW | 8.61 mW | 8.45 mW | 7.99 mW |
| + D-28 only | 27,025 | 57.5% | 197 | 7.21 mW | n/m | n/m | n/m |
| + D-29a, tape gating | 23,326 | 54.7% | 486 | 2.77 mW | 3.56 mW | 3.42 mW | 2.89 mW |
| + D-29b fetch/host gating (v1.1) | 23,484 | 54.7% | 383 | 2.44 mW | 2.92 mW | 2.58 mW | 2.23 mW |
| v1.2: pin bank B (D-31), hold margin 0.2 ns | 25,399 | 58.3% | 1,515 | 2.48 mW | 3.01 mW | 2.67 mW | 2.30 mW |
| v1.3: streaming capture (D-32) | 25,383 | 58.9% | 1,614 | 2.57 mW | 3.11 mW | 2.77 mW | 2.39 mW |
| **v1.4: line unit (D-33), latch banks (D-40, D-41)** | **27,461** | **57.4%** | **1,453** | 2.64 mW | **3.13 mW** | **2.82 mW** | **2.48 mW** |
| change, before → v1.4 | −15% | | −68% | −65% | **−64%** | **−67%** | **−69%** |

n/m = not measured. The workload columns use the exact SRAM count below. Version 1.4
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

## 4. How to read the timing report with clock gates

The worst setup path ends at a clock-gate latch, with slack measured to the latch's
opening edge (the falling edge, 20 ns) and up to 19.9 ns of borrowing available. The
true limit is the closing edge, and borrowing is harmless for a clock gate: while `clk`
is low the AND output is low whatever the latch passes. A low headline "setup slack" is
this reporting convention, not a slow path.

## 5. Limits of these numbers

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

## 6. Reproduce

```sh
scripts/local-harden.sh --tag NAME [--to OpenROAD.ResizerTimingPostCTS]
scripts/power-workload.sh runs/NAME            # static + 3 workloads -> runs/NAME/power/
openroad hold_sweep.tcl                        # env vars in its header
sby -f formal/esc_greg.sby                     # G1-G3
```
