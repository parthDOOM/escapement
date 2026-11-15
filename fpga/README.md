# FPGA build

The same RTL that is hardened for the chip, built for an FPGA so that firmware can be run
against real devices before there is silicon. Two substitutions turn the chip's design into
an FPGA design, and both keep the chip's behaviour:

- `ESC_NO_CG` builds plain flip-flops in place of the clock gates and latch banks (D-29a,
  D-40). It is the same build the RTL simulations and the formal proofs use, so the FPGA runs
  the logic that is proved.
- `escapement_sram_fpga.v` infers block RAM in place of the IHP SRAM macros (the 1,024-word
  program store and the 256-word tape), with the macro wrapper's timing contract: a read
  gives its word from the next cycle and holds it, a write cycle reads nothing, and a write
  wins over a read.

In simulation the FPGA build passes 11 of the 12 chip tests and all 9 firmware-through-the-pins
tests, each against the reference model's execution digest. The twelfth chip test,
`test_store_reads_only_for_the_host_while_stopped`, watches the enable pin of the IHP macro
inside the program store, which the block-RAM model does not have. To rerun: copy `src/`,
replace `escapement_sram.v` with `fpga/escapement_sram_fpga.v`, empty
`escapement_sram_big.v`, and in `test/` run `make` and
`make COCOTB_TEST_MODULES=test_firmware_pins` with `SRC_DIR` set to the copy and
`COMPILE_ARGS=-DESC_NO_CG` in the environment (the Makefile adds its own flags to it).

## Size

Yosys synthesis of the whole chip for three FPGA families (`fpga/estimate.sh`, OSS CAD
Suite 2026-10-04 as pinned in `scripts/versions.txt`):

| Family | Logic | Flip-flops | Block RAM | Boards that fit |
| --- | ---: | ---: | --- | --- |
| Xilinx 7-series | 9,948 LUTs | 5,342 | 1 RAMB36 and 1 RAMB18 | Artix-7 35T and larger (Digilent Basys 3, Arty A7, Nexys A7, Cmod A7-35T); Spartan-7 25 (Arty S7-25) |
| Lattice ECP5 | 14,541 LUT4 (516 CCU2C counted as two each) | 5,091 | 3 DP16KD | LFE5U-25F and larger (ULX3S, OrangeCrab 25F) |
| Lattice iCE40 | 13,354 LUT4 | 5,093 | 10 RAM40 | none: the UP5K (5,280) and HX8K (7,680) are too small |

`synth_xilinx` keeps the module hierarchy unless told to flatten, so about 250 flip-flops
that the other two families merge or remove across module boundaries survive there (the
hierarchy-kept Gowin synthesis of the bench shows the same, 5,298). In all three the 13
falling-edge flip-flops of the half-tick pin sampler map to the family's own falling-edge
cell (`FDCE_1`, `SB_DFFNR`, an ECP5 slice with its clock inverted), so the clock stays on
the global network.

## A ULX3S bitstream

`fpga/build.sh` synthesises, places and routes the whole chip for a Radiona ULX3S with the
open-source flow (Yosys and nextpnr-ecp5), with a 25 MHz constraint on the clock:

```sh
fpga/build.sh ulx3s 25k           # fpga/build/ulx3s/escapement.bit (12k, 25k, 45k or 85k)
openFPGALoader -b ulx3s fpga/build/ulx3s/escapement.bit
```

| Board | Logic | Flip-flops | Block RAM | Clock after routing |
| --- | ---: | ---: | ---: | ---: |
| Radiona ULX3S 25F (LFE5U-25F) | 14,469 of 24,288 LUT4 (59%) | 5,091 (20%) | 3 of 56 | 41.6 MHz |
| Radiona ULX3S 85F (LFE5U-85F) | 14,469 of 83,640 (17%) | 5,091 (6%) | 3 of 208 | 39.3 MHz |

Both meet 25 MHz, and so 24 MHz for the USB firmware, with no place-and-route warnings
(OSS CAD Suite 2026-10-04: Yosys 0.69+190, nextpnr 0.11.1-47); each build takes under a
minute and a half. The design does not fit the 12F's rated 12k LUTs. nextpnr still builds
for it, because the 12F is a 25F die, but use a 25F or larger.

The ULX3S bitstream is built and timed but has not been run on a board; the bench below is
where the design runs on hardware.

`escapement_board.v` puts the chip's 24 Tiny Tapeout pins, its clock and its reset on the
board's headers: `uio[7:0]` (bank A, three-state as on the chip), `uo_out[7:3]` and
`ui_in[7:3]` (bank B), and the SPI host interface on `ui_in[2:0]` and `uo_out[2:0]`. The
host is an RP2040 or RP2350 board running the bring-up script and host library
(`sw/rp2350`), as on the Tiny Tapeout demo board: it drives `clk`, `rst_n`, CS_n, SCK and
MOSI, and reads MISO, IRQ and RUN. Every pin is 3.3 V; connect the grounds.

| Signal | ULX3S header pin |
| --- | --- |
| `clk` | gp[0] (a clock-capable pin) |
| `rst_n` | gn[0] |
| `ui_in[0]` CS_n, `[1]` SCK, `[2]` MOSI | gn[1], gn[2], gn[3] |
| `uo_out[0]` MISO, `[1]` IRQ, `[2]` RUN | gp[1], gp[2], gp[3] |
| `ui_in[3]` to `[7]` (bank B in) | gn[4] to gn[8] |
| `uo_out[3]` to `[7]` (bank B out) | gp[4] to gp[8] |
| `uio[0]` to `[7]` (bank A) | gp[22] to gp[25], gn[22] to gn[25] |

`ulx3s.lpf` keeps off the ESP32's pins (gp/gn 11 to 13) and the ADC's (14 to 17).

## On hardware

[`tangprimer25k/`](tangprimer25k/README.md) is a working bench: a Raspberry Pi Pico W runs
the repository's bring-up script against the chip on a Sipeed Tang Primer 25K Dock (Gowin
GW5A-25). On 2026-11-15 the version 3 RTL, commit `fa0491a`, whose Verilog is the one
hardened for silicon, passed all six bring-up stages and 71 of 71 firmware runs against the
model. That bitstream failed two of the build's own clock checks: the half-tick sampler's
falling-edge register is clocked through an inverter that leaves the FPGA's global clock
network, so it was packed by hand for the run. The scripts, the wiring, the results and
that limit are in the bench's README, and the [bench page](https://parthdoom.github.io/escapement/fpga/)
draws every wire.
