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
