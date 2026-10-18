# sw/rp2350/bringup.py against the RTL of the whole chip on a simulated demo board.
SIM ?= icarus
TOPLEVEL_LANG ?= verilog
REPO      = $(abspath $(PWD)/../..)
SRC_DIR   = $(REPO)/src
MACRO_DIR = $(REPO)/macro/RM_IHPSG13_1P_256x32_c2_bm_bist
COMPILE_ARGS    += -DFUNCTIONAL -I$(SRC_DIR)
VERILOG_SOURCES += $(SRC_DIR)/project.v $(SRC_DIR)/escapement_host.v $(SRC_DIR)/escapement_sram.v
VERILOG_SOURCES += $(sort $(wildcard $(SRC_DIR)/esc_*.v))
VERILOG_SOURCES += $(MACRO_DIR)/RM_IHPSG13_1P_core_behavioral_bm_bist.v
VERILOG_SOURCES += $(MACRO_DIR)/RM_IHPSG13_1P_256x32_c2_bm_bist.v
VERILOG_SOURCES += $(PWD)/tb_bringup.v
TOPLEVEL = tb_bringup
COCOTB_TEST_MODULES = test_bringup_rtl
export PYTHONPATH := $(REPO)/sw:$(REPO)/sw/rp2350:$(REPO)/sw/tests:$(PYTHONPATH)
include $(PWD)/../sim.mk
include $(shell cocotb-config --makefiles)/Makefile.sim
