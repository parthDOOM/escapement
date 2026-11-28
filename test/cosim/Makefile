# ISS <-> RTL co-simulation (docs/04-VERIFICATION.md, "Independent models").
# Usage: make            (all tests)
#        make SEEDS=500  (more random programs; default in test_cosim.py)
SIM ?= icarus
FST ?= -fst
TOPLEVEL_LANG ?= verilog
REPO     = $(abspath $(PWD)/../..)
SRC_DIR  = $(REPO)/src
MACRO_DIR = $(REPO)/macro/RM_IHPSG13_1P_256x32_c2_bm_bist

COMPILE_ARGS    += -DFUNCTIONAL
ifneq ($(SIM),verilator)
COMPILE_ARGS    += -g2012
endif
VERILOG_SOURCES += $(sort $(wildcard $(SRC_DIR)/esc_*.v))
VERILOG_SOURCES += $(SRC_DIR)/escapement_sram.v
VERILOG_SOURCES += $(SRC_DIR)/escapement_pstore.v
VERILOG_SOURCES += $(MACRO_DIR)/RM_IHPSG13_1P_core_behavioral_bm_bist.v
VERILOG_SOURCES += $(MACRO_DIR)/RM_IHPSG13_1P_256x32_c2_bm_bist.v
VERILOG_SOURCES += $(PWD)/esc_tb.v
# The ISA checker (formal/esc_isa_check.v, docs/11-SEMANTICS.md section 7) on every commit,
# beside the ISS. A failure is printed by the simulator, not by cocotb, so run_parallel.py and
# the mutation campaign search the output for it (isa_fail.py). ISA=no builds without it.
ISA ?= yes
ifeq ($(ISA),yes)
COMPILE_ARGS    += -DESC_ISA_CHECK -I$(REPO)/formal
VERILOG_SOURCES += $(REPO)/formal/esc_isa_check.v
ifeq ($(SIM),verilator)
EXTRA_ARGS      += --assert
endif
endif
TOPLEVEL = esc_tb
COCOTB_TEST_MODULES = test_cosim
export PYTHONPATH := $(REPO)/sw:$(PWD):$(PYTHONPATH)

include $(PWD)/../sim.mk
include $(shell cocotb-config --makefiles)/Makefile.sim
