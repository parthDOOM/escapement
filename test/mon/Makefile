# Lockstep test of the bus timing monitor (src/esc_mon.v) against its reference model
# (sw/esciss/monitor.py), cocotb + Icarus.
SIM ?= icarus
TOPLEVEL_LANG ?= verilog
SRC_DIR   = $(PWD)/../../src
SIM_BUILD        = sim_build
VERILOG_SOURCES += $(SRC_DIR)/esc_greg.v $(SRC_DIR)/esc_mon.v
TOPLEVEL = esc_mon
COCOTB_TEST_MODULES = test_mon

include $(PWD)/../sim.mk
include $(shell cocotb-config --makefiles)/Makefile.sim
