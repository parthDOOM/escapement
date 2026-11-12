# Escapement: common entry points. RTL simulation needs Icarus Verilog and cocotb
# (test/requirements.txt); formal needs SymbiYosys; hardening needs Docker (docs/05-TOOLCHAIN.md).
PYTHON ?= python3
# replay.asm is a template: sw/esctape fills in its pins and trace, and its deadlines come
# from data, so it is checked by the replay tests instead of by escert.
FIRMWARE := $(filter-out firmware/replay.asm,$(wildcard firmware/*.asm))

.PHONY: help env sw-test cert proof-table fw-mutants test gl-test pin-test gl-pin-test cosim cosim-parallel host-test bringup-test unit-test line-test lint formal formal-quick formal-bmc formal-props formal-pdr formal-v2 formal-teeth equiv mutation harden precheck clean

help:
	@echo "sw-test    assembler, ISS, certificate checker and bus-model tests (pytest)"
	@echo "cert       static timing certificate for every firmware (escert)"
	@echo "proof-table  every certified lead beside the lead the ISS measures (RTL=file adds the RTL's)"
	@echo "fw-mutants timing bugs planted in the firmware: escert must reject every one that can fault"
	@echo "test       chip-level tests on the RTL (test/, as in CI)"
	@echo "gl-test    chip-level tests on a gate-level netlist (test/gate_level_netlist.v)"
	@echo "pin-test   SWD, SPI target, NEC (25, 33, 50 MHz), two banks, a learned protocol and VGA Pong through the chip pins (gl-pin-test: on the netlist)"
	@echo "cosim      ISS vs RTL lockstep co-simulation (COSIM_SEEDS random programs, default 200)"
	@echo "cosim-parallel  the same, split over JOBS cores (default 8)"
	@echo "host-test  host SPI interface tests"
	@echo "bringup-test  sw/rp2350/bringup.py against the RTL on a simulated demo board (~15 min)"
	@echo "           (add SIM=verilator to any simulation target to run it on Verilator: 3-7x faster on the long ones)"
	@echo "unit-test  directed core tests"
	@echo "line-test  line unit RTL against the ISS model, every output every cycle (LINE_SEEDS, default 8)"
	@echo "lint       Verilator lint of the core"
	@echo "formal     formal-quick, then the unbounded core proof split over JOBS cores (formal-pdr)"
	@echo "formal-quick  formal-bmc and formal-props"
	@echo "formal-bmc    the bounded core check from reset, one task per assertion group (BMC_JOBS side by side)"
	@echo "formal-props  cover, ahead, never-late, clock-gate, latch, configuration, live-write grant and line unit proofs"
	@echo "formal-v2  the v2 proofs: tripwires (esc_core tw), the line unit bounded from reset, non-interference (esc_ni_ind kind + base)"
	@echo "formal-teeth  known bugs planted one at a time (formal/teeth.sh): each proof they stand for must fail"
	@echo "equiv      the gate-level netlist is sequentially equivalent to the RTL (yosys, ABC; ~4 min)"
	@echo "mutation   RTL mutation campaign against the co-simulation (JOBS in parallel)"
	@echo "harden     local LibreLane harden with the pinned CI toolchain (TAG=name)"
	@echo "precheck   Tiny Tapeout precheck on a local harden (RUN=runs/name)"
	@echo "env        report which tools are installed"

env:
	@bash scripts/check-env.sh

# Uses every core when pytest-xdist is installed.
sw-test:
	cd sw && $(PYTHON) -m pytest -q $$($(PYTHON) -c "import xdist" 2>/dev/null && echo "-n auto") $(PYTEST_ARGS) tests

cert:
	@cd sw && for f in $(FIRMWARE); do echo "== $$f"; $(PYTHON) -m escert ../$$f || exit 1; done

# RTL=file: the telemetry test/cosim/test_cosim.py records with COSIM_PROOF_OUT=file
proof-table:
	$(PYTHON) test/cosim/proof_table.py $(if $(RTL),--rtl $(RTL))

fw-mutants:
	$(PYTHON) test/mutation/fw_mutants.py --jobs $(JOBS)

test:
	cd test && $(MAKE) -B

pin-test:
	cd test && $(MAKE) -B COCOTB_TEST_MODULES=test_firmware_pins COCOTB_RESULTS_FILE=results_pins.xml

gl-pin-test:
	cd test && $(MAKE) -B GATES=yes COCOTB_TEST_MODULES=test_firmware_pins COCOTB_RESULTS_FILE=results_pins.xml

gl-test:
	cd test && $(MAKE) -B GATES=yes

cosim:
	cd test/cosim && $(MAKE) -B

JOBS ?= 8
cosim-parallel:
	$(PYTHON) test/cosim/run_parallel.py --jobs $(JOBS)

host-test:
	cd test/host && $(MAKE) -B

bringup-test:
	cd test/bringup && $(MAKE) -B

unit-test:
	cd test/rtl_unit && $(MAKE) -B

line-test:
	$(PYTHON) test/line/run.py --jobs $(JOBS)

lint:
	cd test/rtl_unit && $(MAKE) lint

formal: formal-quick formal-pdr

# SKIP=1: skip each proof whose inputs (its .sby, the files it lists, the tools) match its
# last PASS (formal/fcache.py); every skip is printed. For iterating, never for a release.
SKIP_FLAG = $(if $(SKIP),--skip)

# the unbounded core proof, one PDR task per assertion group, JOBS side by side.
# FORMAL_HISTORY=FILE (an earlier FORMAL_JSON): a group that passed there is limited to 10x
# its solver time (at least 600 s), so a proof that stops converging fails in minutes.
formal-pdr:
	cd formal && $(PYTHON) split_core.py $(SKIP_FLAG) --jobs $(JOBS) $(if $(FORMAL_TIMEOUT),--timeout $(FORMAL_TIMEOUT)) \
	  $(if $(FORMAL_JSON),--json $(abspath $(FORMAL_JSON))) $(if $(FORMAL_HISTORY),--history $(abspath $(FORMAL_HISTORY)))

formal-quick: formal-bmc formal-props

# the bounded core check from reset (esc_core.sby bmc's depth), one task per assertion group
# (formal/split_bmc.py, boolector), BMC_JOBS side by side: each solver can need about 2.5 GB
BMC_JOBS ?= 1
formal-bmc:
	cd formal && $(PYTHON) split_bmc.py $(SKIP_FLAG) --jobs $(BMC_JOBS) $(if $(BMC_JSON),--json $(abspath $(BMC_JSON))) \
	  $(if $(BMC_HISTORY),--history $(abspath $(BMC_HISTORY)))

# each proof runs (fcache.py runs them all and fails if any fails)
formal-props:
	cd formal && $(PYTHON) fcache.py $(SKIP_FLAG) esc_core.sby:cover esc_core.sby:ahead esc_ctx.sby:kind \
	  esc_greg.sby esc_lreg.sby esc_host.sby esc_top.sby esc_line.sby:kind

# v2: the tripwire proof, the line unit bounded from reset (depth 40), and non-interference
# for live reload, by induction (kind) and bounded from reset (base)
formal-v2:
	cd formal && $(PYTHON) fcache.py $(SKIP_FLAG) esc_core.sby:tw esc_line.sby:bmc esc_ni_ind.sby:kind esc_ni_ind.sby:base

formal-teeth:
	bash formal/teeth.sh

equiv:
	bash formal/equiv.sh

mutation:
	$(PYTHON) test/mutation/run_mutants.py --jobs $(JOBS)

TAG ?= wokwi
harden:
	bash scripts/local-harden.sh --tag $(TAG)

RUN ?= runs/wokwi
precheck:
	bash scripts/local-precheck.sh $(RUN)

clean:
	rm -rf test/sim_build test/cosim/sim_build test/host/sim_build test/rtl_unit/sim_build test/bringup/sim_build \
	  test/results.xml test/results_*.xml test/*/results.xml test/cosim/results_*.xml test/cosim/cosim_failures \
	  formal/esc_core_cover formal/esc_core_bmc formal/esc_core_pdr formal/esc_core_kind \
	  formal/esc_ctx_kind formal/esc_ctx_pdr formal/esc_ctx_bmc formal/esc_greg_prove formal/esc_greg_cover
	rm -rf formal/esc_core_split_* formal/esc_core_split.sby formal/esc_host_prove formal/esc_host_cover formal/esc_top_prove formal/esc_top_cover formal/equiv_build
