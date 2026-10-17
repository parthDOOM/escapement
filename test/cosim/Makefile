# ISS <-> RTL co-simulation (docs/04-VERIFICATION.md, "Independent models").
# Usage: make            (all tests)
#        make SEEDS=500  (more random programs; default in test_cosim.py)
SIM ?= icarus
FST ?= -fst
TOPLEVEL_LANG ?= verilog
REPO     = $(abspath $(PWD)/../..)
SRC_DIR  = $(REPO)/src
MACRO_DIR = $(REPO)/macro/RM_IHPSG13_1P_256x32_c2_bm_bist

COMPILE_ARGS    += -DFUNCTIONAL -g2012
VERILOG_SOURCES += $(sort $(wildcard $(SRC_DIR)/esc_*.v))
VERILOG_SOURCES += $(SRC_DIR)/escapement_sram.v
VERILOG_SOURCES += $(MACRO_DIR)/RM_IHPSG13_1P_core_behavioral_bm_bist.v
VERILOG_SOURCES += $(MACRO_DIR)/RM_IHPSG13_1P_256x32_c2_bm_bist.v
VERILOG_SOURCES += $(PWD)/esc_tb.v
TOPLEVEL = esc_tb
COCOTB_TEST_MODULES = test_cosim
export PYTHONPATH := $(REPO)/sw:$(PWD):$(PYTHONPATH)

include $(shell cocotb-config --makefiles)/Makefile.sim
