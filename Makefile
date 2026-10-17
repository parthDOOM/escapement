# Escapement: common entry points. RTL simulation needs Icarus Verilog and cocotb
# (test/requirements.txt); formal needs SymbiYosys; hardening needs Docker (docs/05-TOOLCHAIN.md).
PYTHON ?= python3
# replay.asm is a template: sw/esctape fills in its pins and trace, and its deadlines come
# from data, so it is checked by the replay tests instead of by escert.
FIRMWARE := $(filter-out firmware/replay.asm,$(wildcard firmware/*.asm))

.PHONY: help env sw-test cert test gl-test cosim host-test unit-test lint formal mutation harden precheck clean

help:
	@echo "sw-test    assembler, ISS, certificate checker and bus-model tests (pytest)"
	@echo "cert       static timing certificate for every firmware (escert)"
	@echo "test       chip-level tests on the RTL (test/, as in CI)"
	@echo "gl-test    chip-level tests on a gate-level netlist (test/gate_level_netlist.v)"
	@echo "cosim      ISS vs RTL lockstep co-simulation (COSIM_SEEDS random programs, default 200)"
	@echo "host-test  host SPI interface tests"
	@echo "unit-test  directed core tests"
	@echo "lint       Verilator lint of the core"
	@echo "formal     SymbiYosys proofs (core, per-context never-late, clock gate)"
	@echo "mutation   RTL mutation campaign against the co-simulation"
	@echo "harden     local LibreLane harden with the pinned CI toolchain (TAG=name)"
	@echo "precheck   Tiny Tapeout precheck on a local harden (RUN=runs/name)"
	@echo "env        report which tools are installed"

env:
	@bash scripts/check-env.sh

sw-test:
	cd sw && $(PYTHON) -m pytest -q tests

cert:
	@cd sw && for f in $(FIRMWARE); do echo "== $$f"; $(PYTHON) -m escert ../$$f || exit 1; done

test:
	cd test && $(MAKE) -B

gl-test:
	cd test && $(MAKE) -B GATES=yes

cosim:
	cd test/cosim && $(MAKE) -B

host-test:
	cd test/host && $(MAKE) -B

unit-test:
	cd test/rtl_unit && $(MAKE) -B

lint:
	cd test/rtl_unit && $(MAKE) lint

formal:
	cd formal && sby -f esc_core.sby pdr && sby -f esc_core.sby cover \
	  && sby -f esc_ctx.sby kind && sby -f esc_greg.sby

mutation:
	$(PYTHON) test/mutation/run_mutants.py

TAG ?= wokwi
harden:
	bash scripts/local-harden.sh --tag $(TAG)

RUN ?= runs/wokwi
precheck:
	bash scripts/local-precheck.sh $(RUN)

clean:
	rm -rf test/sim_build test/cosim/sim_build test/host/sim_build test/rtl_unit/sim_build \
	  test/results.xml test/*/results.xml \
	  formal/esc_core_cover formal/esc_core_bmc formal/esc_core_pdr formal/esc_core_kind \
	  formal/esc_ctx_kind formal/esc_ctx_pdr formal/esc_ctx_bmc formal/esc_greg_prove formal/esc_greg_cover
