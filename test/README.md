# Tests

All RTL tests use [cocotb](https://docs.cocotb.org/) with Icarus Verilog
(`pip install -r requirements.txt`). Each directory has its own Makefile; the top-level
`Makefile` has a target for each.

| Directory | Make target | What it checks |
| --- | --- | --- |
| `test/` | `make test`, `make gl-test` | The whole chip through its pins (`tb.v`, `test.py`): identity over SPI, loading and running UART firmware, the execution digest against the ISS, the logic analyser, and a slow bit-banged host like the demo board's. Runs on the RTL, or on the gate-level netlist with `GATES=yes` |
| `cosim/` | `make cosim`, `make cosim-parallel` | Lockstep co-simulation of the core against the Python ISS: every commit, every pin on every tick, debug state and all 256 tape words, for every firmware with its bus models and for random programs (`COSIM_SEEDS`, default 200). `cosim-parallel` runs the same comparisons split across `JOBS` cores |
| `host/` | `make host-test` | The host SPI interface: status, memory round trips at the SCK limit and with jitter, configuration, start and stop, debug reads, mailboxes, malformed frames and tape access |
| `bringup/` | `make bringup-test` | The silicon bring-up script `sw/rp2350/bringup.py`, stages 0–6, against the RTL of the whole chip on a simulated demo board (`tb_bringup.v`), and a stale bundle failing stage 5 ([bring-up](../docs/16-BRING-UP.md)) |
| `rtl_unit/` | `make unit-test`, `make lint` | Directed tests of the core for each instruction class, admission rule and lifecycle case; Verilator lint |
| `mutation/` | `make mutation` | Mutation campaign: each mutant is a one-line change to a copy of `src/`, run against the co-simulation and the directed suite; `JOBS` mutants at a time |
| `power.py` | via `scripts/power-workload.sh` | Gate-level workloads that produce switching activity for power analysis |

## Gate-level simulation

Harden the design (`make harden`), copy the final netlist
(`runs/<tag>/final/nl/tt_um_escapement.nl.v`) to `test/gate_level_netlist.v`, and run
`make gl-test` with `PDK_ROOT` pointing at the pinned IHP-Open-PDK.

## Waveforms

The chip-level tests write `tb.fst`; open it with GTKWave (`gtkwave tb.fst tb.gtkw`) or
Surfer.
