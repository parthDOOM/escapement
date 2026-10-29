# Escapement: common entry points. RTL simulation needs Icarus Verilog and cocotb
# (test/requirements.txt); formal needs SymbiYosys; hardening needs Docker (docs/05-TOOLCHAIN.md).
PYTHON ?= python3
# replay.asm is a template: sw/esctape fills in its pins and trace, and its deadlines come
# from data, so it is checked by the replay tests instead of by escert.
FIRMWARE := $(filter-out firmware/replay.asm,$(wildcard firmware/*.asm))

.PHONY: help env sw-test cert proof-table fw-mutants test gl-test pin-test gl-pin-test cosim cosim-parallel host-test bringup-test unit-test line-test lint formal formal-quick equiv mutation harden precheck clean

help:
	@echo "sw-test    assembler, ISS, certificate checker and bus-model tests (pytest)"
	@echo "cert       static timing certificate for every firmware (escert)"
	@echo "proof-table  every certified lead beside the lead the ISS measures (RTL=file adds the RTL's)"
	@echo "fw-mutants timing bugs planted in the firmware: escert must reject every one that can fault"
	@echo "test       chip-level tests on the RTL (test/, as in CI)"
	@echo "gl-test    chip-level tests on a gate-level netlist (test/gate_level_netlist.v)"
	@echo "pin-test   SWD, SPI target, NEC and a learned protocol through the chip pins (gl-pin-test: on the netlist)"
	@echo "cosim      ISS vs RTL lockstep co-simulation (COSIM_SEEDS random programs, default 200)"
	@echo "cosim-parallel  the same, split over JOBS cores (default 8)"
	@echo "host-test  host SPI interface tests"
	@echo "bringup-test  sw/rp2350/bringup.py against the RTL on a simulated demo board (~15 min)"
	@echo "           (add SIM=verilator to any simulation target to run it on Verilator: 3-7x faster on the long ones)"
	@echo "unit-test  directed core tests"
	@echo "line-test  line unit RTL against the ISS model, every output every cycle (LINE_SEEDS, default 8)"
	@echo "lint       Verilator lint of the core"
	@echo "formal     all SymbiYosys proofs, with the unbounded core proof split over JOBS cores (~25 min)"
	@echo "formal-quick  bounded core proof, cover, never-late, clock-gate and configuration proofs (minutes)"
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

formal: formal-quick
	cd formal && $(PYTHON) split_core.py --jobs $(JOBS) $(if $(FORMAL_TIMEOUT),--timeout $(FORMAL_TIMEOUT)) \
	  $(if $(FORMAL_JSON),--json $(abspath $(FORMAL_JSON)))

formal-quick:
	cd formal && sby -f esc_core.sby bmc && sby -f esc_core.sby cover \
	  && sby -f esc_ctx.sby kind && sby -f esc_greg.sby && sby -f esc_lreg.sby && sby -f esc_host.sby

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
	rm -rf formal/esc_core_split_* formal/esc_core_split.sby formal/esc_host_prove formal/esc_host_cover formal/equiv_build
