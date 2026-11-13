# Makefile
# See https://docs.cocotb.org/en/stable/quickstart.html for more info

# defaults
SIM ?= icarus
FST ?= -fst # Use more efficient FST format
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
PROJECT_SOURCES  = project.v
PROJECT_SOURCES += escapement_host.v
PROJECT_SOURCES += escapement_sram.v
PROJECT_SOURCES += escapement_pstore.v
PROJECT_SOURCES += $(notdir $(sort $(wildcard $(SRC_DIR)/esc_*.v)))

# SRAM macro simulation models (never synthesised; src/ holds a port-only stub).
# Compile with -DFUNCTIONAL so the model takes its behavioural path.
MACRO_DIR     = $(PWD)/../macro/RM_IHPSG13_1P_256x32_c2_bm_bist
MACRO_MODELS  = $(MACRO_DIR)/RM_IHPSG13_1P_core_behavioral_bm_bist.v
MACRO_MODELS += $(MACRO_DIR)/RM_IHPSG13_1P_256x32_c2_bm_bist.v
MACRO_MODELS += $(SRC_DIR)/escapement_art.v

ifneq ($(GATES),yes)

# RTL simulation:
SIM_BUILD				= sim_build/rtl
COMPILE_ARGS    += -DFUNCTIONAL
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))
VERILOG_SOURCES += $(MACRO_MODELS)
ifeq ($(CELLS),yes)
SIM_BUILD				= sim_build/cells
endif

else

# Gate level simulation:
SIM_BUILD				= sim_build/gl
COMPILE_ARGS    += -DGL_TEST
COMPILE_ARGS    += -DSIM
ifeq ($(SDF),)
COMPILE_ARGS    += -DFUNCTIONAL
else
# With delays (campaign row 18): SDF=<file from a harden's final/sdf/<corner>/>. The cells'
# and the SRAM model's specify blocks take the SDF's delays (-gspecify); the SRAM model's
# delays are in its non-FUNCTIONAL branch, so FUNCTIONAL is not set. Icarus applies delays
# but does not report timing-check violations: a setup failure shows as a wrong value the
# tests see. CLK_NS (test.py) sets the clock, for the overclocked control.
SIM_BUILD				= sim_build/gl_sdf
# sdf_prep.py: the flat netlist's escaped instance names (clock-gate latches, gated-clock
# buffers, the SRAM macros) renamed in copies of both files, which Icarus's SDF reader needs;
# -ginterconnect: the SDF's wire delays (Icarus drops them without it).
SDF_DIR         := $(PWD)/sim_build/gl_sdf_prep
SDF_PREP        := $(shell mkdir -p sim_build && python3 $(PWD)/sdf_prep.py $(PWD)/gate_level_netlist.v $(abspath $(SDF)) $(SDF_DIR) >&2 && echo ok)
ifneq ($(SDF_PREP),ok)
$(error sdf_prep.py failed on $(SDF))
endif
GL_NETLIST      = $(SDF_DIR)/gate_level_netlist.v
COMPILE_ARGS    += -gspecify -ginterconnect -DSDF_FILE=\"$(SDF_DIR)/$(notdir $(SDF))\"
endif
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_io/verilog/sg13cmos5l_io.v
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v
# Flop models instantiate UDPs (ihp_dff_r, ...) defined here; the template omits it
# because its example design has no flops.
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_udp.v
VERILOG_SOURCES += $(MACRO_MODELS)

# this gets copied in by the GDS action workflow
GL_NETLIST      ?= $(PWD)/gate_level_netlist.v
VERILOG_SOURCES += $(GL_NETLIST)

endif

# Allow sharing configuration between design and testbench via `include`:
COMPILE_ARGS 		+= -I$(SRC_DIR)

# Include the testbench sources:
VERILOG_SOURCES += $(PWD)/tb.v
TOPLEVEL = tb

# Assembler, host protocol, bus models and the SPI master model:
export PYTHONPATH := $(PWD)/../sw:$(PWD)/common:$(PYTHONPATH)

# List test modules to run, separated by commas and without the .py suffix:
COCOTB_TEST_MODULES = test

# include cocotb's make rules to take care of the simulator setup
include $(PWD)/sim.mk
include $(shell cocotb-config --makefiles)/Makefile.sim
