# Toolchain and reproduction

Every tool that touches the design is pinned to an exact revision
([`scripts/versions.txt`](../scripts/versions.txt)), so a harden can be reproduced bit
for bit, in CI or locally.

## Pinned flow

| Component | Revision |
| --- | --- |
| Tiny Tapeout template | `ttihp-verilog-template` branch `main` @ `4b1204a` (the ttihp27a shuttle's) |
| GDS action | `TinyTapeout/tt-gds-action` @ `e7a50f8` (tag `ttihp27a`) |
| Support tools and precheck | `TinyTapeout/tt-support-tools` @ `9d13ff3` (`main`, the action's default) |
| PDK | `IHP-GmbH/IHP-Open-PDK` @ `89d42ee` (SG13CMOS5L), installed by ciel |
| LibreLane | 3.1.0.dev4, container `ghcr.io/librelane/librelane:3.1.0.dev4` (digest in `versions.txt`) |
| Python | 3.11 |
| Simulation and formal | OSS CAD Suite release 2026-10-04 (Yosys and ABC, Verilator, SymbiYosys); Icarus Verilog 13.0 (built from its `v13_0` tag in CI, `.github/actions/iverilog`); cocotb 2.0.1 |

The workflows reference the action and the tools by commit SHA, not by branch, so an
upstream change cannot silently alter a harden.

Version 2.0 is signed off on the March 2027 shuttle's release (`ttihp27a`). Versions 1.2 to
1.6 were signed off on the pre-release flow: action `3412659` (branch `ihp-cmos5l`), tools
`d66cf17`, PDK `2bbec755`, LibreLane 3.1.0.dev3. `FLOW=pre` in the local scripts reproduces
that flow. The cell models and liberty that ciel installs at `89d42ee` are byte-identical to
the IHP-Open-PDK tree at that commit, which the CI simulation and equivalence jobs check out.

## Implementation languages

- **RTL:** Verilog/SystemVerilog in the subset Yosys synthesises (`src/`).
- **Software:** Python 3.11 for the assembler, instruction-set simulator, certificate
  checker, host library, tape tools and cocotb tests (`sw/`, `test/`).
- **Encodings:** `isa/isa.yaml` is the only shared definition. The assembler, simulator
  and RTL decoder each read or mirror it; execution semantics are never generated from
  it.

## Continuous integration

| Workflow | Runs |
| --- | --- |
| `test.yaml` | The chip-level cocotb tests on the RTL; then, in a second job, `sw-test`, `cert`, `cosim-parallel`, `proof-table` (on the co-simulation's RTL telemetry) and `pin-test`, and the co-simulation and line unit lockstep again on the standard-cell models (Icarus Verilog 13) |
| `formal.yaml` | Three jobs side by side: the property proofs (`make formal-props`) and the unbounded core proof as parallel PDR tasks (`make formal-pdr`); the bounded core check from reset, one task per assertion group (`make formal-bmc`); the v2 proofs (tripwires, the line unit from reset, non-interference: `make formal-v2`) and the teeth (`make formal-teeth`) |
| `mutation.yaml` | The mutation campaign in sixteen shards dealt by cost, after a baseline of every suite on the unmutated RTL, with the canaries in a job of their own |
| `gds.yaml` | The Tiny Tapeout harden (LibreLane), then the precheck and the gate-level test on the hardened netlist |
| `equivalence.yaml` | After a successful harden on master: that netlist against the RTL of its commit |
| `docs.yaml` | The Tiny Tapeout datasheet build from `info.yaml` and `docs/info.md` |
| `site.yaml` | The project site and its results page, on master: when the site, the software or the firmware change, and after each of the workflows above |

The distribution's Icarus Verilog (12.0 on the Ubuntu runners) gives X on the PDK's
standard-cell models, so every job that simulates them (`CELLS=yes`) uses Icarus 13.

## Local environment

On Linux or WSL2 (x86-64):

```sh
bash scripts/setup-wsl.sh        # OSS CAD Suite + Python 3.11 venv with cocotb, into ~/eda
source ~/eda/env.sh
make help                        # all test, formal and hardening entry points
```

## Local hardening

[`scripts/local-harden.sh`](../scripts/local-harden.sh) runs the same LibreLane flow as
the CI job, with the same tools revision, PDK and container (`FLOW=ttihp27a`, the default;
`FLOW=pre` for the v1.2-v1.6 flow), but on every local core:

```sh
scripts/local-harden.sh --tag NAME                    # full flow into runs/NAME
scripts/local-harden.sh --tag NAME --to OpenROAD.ResizerTimingPostCTS   # stop after CTS
scripts/local-harden.sh --tag NAME KEY=VALUE ...      # override flow variables for an experiment
scripts/local-precheck.sh runs/NAME                   # Tiny Tapeout precheck on that run
python3 scripts/ci_budget.py runs/NAME                # predicted CI time of that run
```

Detailed routing in OpenROAD gives the same result at any thread count, so a local run
predicts the CI result; `ci_budget.py` converts its routing CPU time into an estimate of
CI wall time.

## Analysis scripts

| Script | Purpose |
| --- | --- |
| `scripts/frequency-headroom.sh` | Shortest passing clock period per corner: OpenSTA on a run's final netlist and parasitics with the sign-off constraints, with minimum pulse width; `DUTY=40` (or 60) checks a clock that is not high for exactly half the period |
| `scripts/compare-runs.sh` | Two harden runs compared: netlist and GDS sha256, every flow metric, and GDS geometry (KLayout's layout diff) |
| `scripts/power-workload.sh` | Workload power: gate-level simulation of three workloads, activity into OpenSTA |
| `scripts/clock_gating.py` | Flip-flops and latches behind a clock gate in a final netlist, each clock pin traced to the clock port or a gate |
| `scripts/sram_energy.py` | SRAM energy counted edge by edge from a VCD, using the macro liberty's per-state energies |
| `scripts/power_vcd.tcl` | OpenSTA power report from a VCD |
| `scripts/hold_sweep.tcl` | Hold endpoints against clock-uncertainty settings on a post-CTS database |
| `scripts/congestion_map.tcl` | One global-route pass and a congestion report on a placed database |
| `scripts/drc_map.py` | Maps detailed-route violations to regions of the die |
| `scripts/m1_fill_overlap.py` | Checks fill and decap cell Metal1 against signal wires |
| `scripts/check-env.sh` | Reports which tools are installed |

## FPGA build

[`fpga/`](../fpga/README.md) builds the same RTL for an FPGA, with plain flip-flops
(`ESC_NO_CG`) and a block-RAM model of the SRAM macro: about 8,800 LUTs on a Xilinx
7-series part (v1.6), so an Artix-7 35T board is enough. `fpga/estimate.sh` gives the size for
Xilinx, ECP5 and iCE40 with Yosys. `fpga/build.sh` places and routes it for the Sipeed
Tang Nano 20K and the ULX3S with yosys and nextpnr; both meet 25 MHz.

## Host hardware

The reference host is the RP2350 on the Tiny Tapeout demo board, running MicroPython
(`sw/rp2350/bringup.py` and `escapement_demo.py`; `make_bundle.py` packs firmware images
and the model's predictions for them; [bring-up](16-BRING-UP.md)). Any microcontroller
that can drive SPI mode 0 works.
