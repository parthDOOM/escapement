# Toolchain and reproduction

Every tool that touches the design is pinned to an exact revision
([`scripts/versions.txt`](../scripts/versions.txt)), so a harden can be reproduced bit
for bit, in CI or locally.

## Pinned flow

| Component | Revision |
| --- | --- |
| Tiny Tapeout template | `ttihp-verilog-template` branch `cmos5l` @ `b86a2a7` |
| GDS action | `TinyTapeout/tt-gds-action` @ `3412659` (branch `ihp-cmos5l`) |
| Support tools and precheck | `TinyTapeout/tt-support-tools` @ `d66cf17` (branch `ihp-sg13cmos5l`) |
| PDK | `IHP-GmbH/IHP-Open-PDK` @ `2bbec755` (SG13CMOS5L) |
| LibreLane | 3.1.0.dev3, container `ghcr.io/librelane/librelane:3.1.0.dev3` (digest in `versions.txt`) |
| Python | 3.11 |
| Simulation and formal | OSS CAD Suite release 2026-10-04 (Yosys, Icarus Verilog, Verilator, SymbiYosys); cocotb 2.0.1 |

The workflows reference the action and the tools by commit SHA, not by branch, so an
upstream change cannot silently alter a harden.

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
| `test.yaml` | The chip-level cocotb tests on the RTL (Icarus Verilog) |
| `gds.yaml` | The Tiny Tapeout harden (LibreLane), then the precheck and the gate-level test on the hardened netlist |
| `docs.yaml` | The Tiny Tapeout datasheet build from `info.yaml` and `docs/info.md` |

## Local environment

On Linux or WSL2 (x86-64):

```sh
bash scripts/setup-wsl.sh        # OSS CAD Suite + Python 3.11 venv with cocotb, into ~/eda
source ~/eda/env.sh
make help                        # all test, formal and hardening entry points
```

## Local hardening

[`scripts/local-harden.sh`](../scripts/local-harden.sh) runs the same LibreLane flow as
the CI job, with the same tools revision, PDK and container, but on every local core:

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
| `scripts/power-workload.sh` | Workload power: gate-level simulation of three workloads, activity into OpenSTA |
| `scripts/sram_energy.py` | SRAM energy counted edge by edge from a VCD, using the macro liberty's per-state energies |
| `scripts/power_vcd.tcl` | OpenSTA power report from a VCD |
| `scripts/hold_sweep.tcl` | Hold endpoints against clock-uncertainty settings on a post-CTS database |
| `scripts/congestion_map.tcl` | One global-route pass and a congestion report on a placed database |
| `scripts/drc_map.py` | Maps detailed-route violations to regions of the die |
| `scripts/m1_fill_overlap.py` | Checks fill and decap cell Metal1 against signal wires |
| `scripts/check-env.sh` | Reports which tools are installed |

## Host hardware

The reference host is the RP2350 on the Tiny Tapeout demo board, running MicroPython
(`sw/rp2350/bringup.py` and `escapement_demo.py`; `make_bundle.py` packs firmware images
and the model's predictions for them; [bring-up](16-BRING-UP.md)). Any microcontroller
that can drive SPI mode 0 works.
