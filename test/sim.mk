# Simulator options shared by the cocotb suites; included before cocotb's Makefile.sim.
# SIM=icarus (the default and the reference) or SIM=verilator (several times faster on
# long runs; Verilator 5.036 or later). The SRAM model and the testbenches use delays,
# so Verilator needs --timing. Lint is `make lint`, so its warnings are not repeated here.
ifeq ($(SIM),verilator)
EXTRA_ARGS += --timing -Wno-fatal -Wno-lint -Wno-style
endif

# CELLS=yes (any suite): the RTL as it is hardened, with the standard-cell clock gates and
# latch banks (D-40) in place of their behavioural models. Needs PDK_ROOT.
ifeq ($(CELLS),yes)
COMPILE_ARGS    += -DSCL_sg13cmos5l_stdcell
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_udp.v
endif

# The program store build the chip ships (src/project.v STORE_MACROS; v2: 1, one 1024x32): every
# suite simulates that build. The suites that instantiate escapement_pstore themselves (cosim,
# host) take it as `ESC_STORE_MACROS; the whole-chip ones (chip, pin, gate level, bring-up) get it
# through project.v. The wrappers and the models of all three builds are compiled, so any build
# elaborates. (11-12: before this the chip and bring-up benches did not build with build 1, and
# cosim and host simulated escapement_pstore's default, build 4.)
ESC_REPO := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
STORE_MACROS := $(shell sed -n 's/^ *localparam integer STORE_MACROS = \([0-9][0-9]*\);.*/\1/p' $(ESC_REPO)/src/project.v)
ifeq ($(STORE_MACROS),)
$(error test/sim.mk: no STORE_MACROS in src/project.v)
endif
COMPILE_ARGS    += -DESC_STORE_MACROS=$(STORE_MACROS)
VERILOG_SOURCES += $(ESC_REPO)/src/escapement_sram_big.v
VERILOG_SOURCES += $(ESC_REPO)/macro/RM_IHPSG13_1P_512x32_c2_bm_bist/RM_IHPSG13_1P_512x32_c2_bm_bist.v
VERILOG_SOURCES += $(ESC_REPO)/macro/RM_IHPSG13_1P_1024x32_c2_bm_bist/RM_IHPSG13_1P_1024x32_c2_bm_bist.v
