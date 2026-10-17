SIM ?= icarus
FST ?= -fst
TOPLEVEL_LANG ?= verilog
REPO      = $(abspath $(PWD)/../..)
MACRO_DIR = $(REPO)/macro/RM_IHPSG13_1P_256x32_c2_bm_bist
COMPILE_ARGS    += -DFUNCTIONAL
VERILOG_SOURCES += $(REPO)/src/escapement_host.v $(REPO)/src/escapement_sram.v
VERILOG_SOURCES += $(MACRO_DIR)/RM_IHPSG13_1P_core_behavioral_bm_bist.v
VERILOG_SOURCES += $(MACRO_DIR)/RM_IHPSG13_1P_256x32_c2_bm_bist.v
VERILOG_SOURCES += $(PWD)/tb_host.v
TOPLEVEL = tb_host
COCOTB_TEST_MODULES = test_host
export PYTHONPATH := $(REPO)/sw:$(REPO)/test/common:$(PYTHONPATH)
include $(shell cocotb-config --makefiles)/Makefile.sim
