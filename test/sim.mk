# Simulator options shared by the cocotb suites; included before cocotb's Makefile.sim.
# SIM=icarus (the default and the reference) or SIM=verilator (several times faster on
# long runs; Verilator 5.036 or later). The SRAM model and the testbenches use delays,
# so Verilator needs --timing. Lint is `make lint`, so its warnings are not repeated here.
ifeq ($(SIM),verilator)
EXTRA_ARGS += --timing -Wno-fatal -Wno-lint -Wno-style
endif
