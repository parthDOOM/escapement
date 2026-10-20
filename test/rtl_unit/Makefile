# Unit testbench for esc_core (docs/11-SEMANTICS.md), cocotb + Icarus.
# Uses the real program-store wrapper with the vendored SRAM model (-DFUNCTIONAL).
SIM ?= icarus
TOPLEVEL_LANG ?= verilog
SRC_DIR   = $(PWD)/../../src
MACRO_DIR = $(PWD)/../../macro/RM_IHPSG13_1P_256x32_c2_bm_bist

SIM_BUILD        = sim_build
COMPILE_ARGS    += -DFUNCTIONAL
VERILOG_SOURCES += $(SRC_DIR)/esc_core.v $(SRC_DIR)/esc_ctx.v $(SRC_DIR)/esc_cap.v $(SRC_DIR)/esc_line.v $(SRC_DIR)/esc_greg.v $(SRC_DIR)/escapement_sram.v
VERILOG_SOURCES += $(MACRO_DIR)/RM_IHPSG13_1P_core_behavioral_bm_bist.v
VERILOG_SOURCES += $(MACRO_DIR)/RM_IHPSG13_1P_256x32_c2_bm_bist.v
VERILOG_SOURCES += $(PWD)/tb_core.v
TOPLEVEL = tb_core
COCOTB_TEST_MODULES = test_core

include $(PWD)/../sim.mk
include $(shell cocotb-config --makefiles)/Makefile.sim

lint:
	verilator --lint-only -Wall --top-module esc_core $(SRC_DIR)/esc_core.v $(SRC_DIR)/esc_ctx.v $(SRC_DIR)/esc_cap.v $(SRC_DIR)/esc_line.v $(SRC_DIR)/esc_greg.v

synth:
	yosys -p "read_verilog $(SRC_DIR)/esc_core.v $(SRC_DIR)/esc_ctx.v $(SRC_DIR)/esc_cap.v $(SRC_DIR)/esc_line.v $(SRC_DIR)/esc_greg.v; synth -flatten -top esc_core; stat"
