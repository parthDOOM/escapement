# FPGA build

The same RTL that is hardened for the chip, built for an FPGA so that firmware can be run
against real devices before silicon. Two substitutions, both with the chip's behaviour:

- `ESC_NO_CG`: plain flip-flops instead of the clock gates and latch banks (D-29a, D-40),
  the build the RTL simulations and the formal proofs use;
- `escapement_sram_fpga.v`: an inferred 256 × 32 block RAM in place of the IHP SRAM macro,
  with the macro wrapper's timing contract (a read gives its word from the next cycle and
  holds it; a write cycle reads nothing; a write wins over a read).

The FPGA build passes the chip tests (7) and the firmware-through-the-pins tests (5)
in simulation, each against the reference model's execution digest: with `src/` copied,
`escapement_sram.v` replaced by this model and `ESC_NO_CG` defined, run `make` and
`make COCOTB_TEST_MODULES=test_firmware_pins` in `test/` with `SRC_DIR` set to the copy.

## Size

Yosys synthesis of the whole chip (`fpga/estimate.sh`, OSS CAD Suite 2026-10-04 as in `scripts/versions.txt`):

| Family | Logic | Flip-flops | Block RAM | Boards that fit |
| --- | ---: | ---: | --- | --- |
| Xilinx 7-series | about 7,900 LUTs | about 4,400 | 2 RAMB18 | Artix-7 35T and larger (Digilent Basys 3, Arty A7, Nexys A7, Cmod A7-35T); Spartan-7 25 |
| Lattice ECP5 | about 11,300 LUT4 (CCU2C counted as two) | 4,216 | 2 DP16KD | LFE5U-25F and larger (ULX3S, OrangeCrab 25F) |
| Lattice iCE40 | about 9,700 LUT4 | about 4,200 | 4 RAM40 | none: the UP5K (5,280) and HX8K (7,680) are too small |

## Bitstreams

`fpga/build.sh` synthesises, places and routes the whole chip for two boards with the
open-source flow, with a 25 MHz constraint on the clock:

```
fpga/build.sh tangnano20k         # fpga/build/tangnano20k/escapement.fs
fpga/build.sh ulx3s 25k           # fpga/build/ulx3s/escapement.bit (12k, 25k, 45k or 85k)
openFPGALoader -b tangnano20k fpga/build/tangnano20k/escapement.fs
```

| Board | Logic | Flip-flops | Block RAM | Clock after routing |
| --- | ---: | ---: | ---: | ---: |
| Sipeed Tang Nano 20K (GW2AR-18) | 11,839 of 20,736 LUT4 (57%), 1,048 ALU | 4,216 (27%) | 2 of 46 | 47.1 MHz |
| Radiona ULX3S 25F (LFE5U-25F) | 11,425 of 24,288 (47%) | 4,216 (17%) | 2 of 56 | 30.5 MHz |
| Radiona ULX3S 85F (LFE5U-85F) | 11,425 of 83,640 (13%) | 4,216 (5%) | 2 of 208 | 34.0 MHz |

All three meet 25 MHz, and so 24 MHz for the USB firmware, with no place-and-route
warnings (OSS CAD Suite 2026-10-04: Yosys 0.69+190, nextpnr 0.11.1-47). The 12F is a
25F die: nextpnr builds it, at 95% of its rated 12k LUTs. The Tang Nano 20K takes about
11 minutes, the ULX3S a few.

The bitstreams are built and timed but not yet run on a board.

## Wiring

`escapement_board.v` puts the chip's 24 Tiny Tapeout pins, its clock and its reset on the
board's headers: `uio[7:0]` (bank A, the protocol pins, three-state as on the chip),
`uo_out[7:3]` and `ui_in[7:3]` (bank B), and the SPI host interface on `ui_in[2:0]` /
`uo_out[2:0]`. The host is an RP2040 or RP2350 board running the bring-up script and host
library (`sw/rp2350`), as on the Tiny Tapeout demo board: it drives `clk`, `rst_n`,
CS_n, SCK and MOSI and reads MISO, IRQ and RUN. Connect the grounds; every pin is 3.3 V.

| Signal | Tang Nano 20K pin | ULX3S pin |
| --- | --- | --- |
| `clk` | 77 | gp[0] |
| `rst_n` | 76 | gn[0] |
| `ui_in[0]` CS_n, `[1]` SCK, `[2]` MOSI | 42, 41, 48 | gn[1], gn[2], gn[3] |
| `uo_out[0]` MISO, `[1]` IRQ, `[2]` RUN | 49, 86, 72 | gp[1], gp[2], gp[3] |
| `ui_in[3]` to `[7]` (bank B in) | 30, 31, 71, 85, 20 | gn[4] to gn[8] |
| `uo_out[3]` to `[7]` (bank B out) | 15 to 19 (LED0 to LED4, lit when low) | gp[4] to gp[8] |
| `uio[0]` to `[7]` (bank A) | 73, 74, 75, 27, 28, 25, 26, 29 | gp[22] to gp[25], gn[22] to gn[25] |

Pin numbers are the ones printed on the boards. `tangnano20k.cst` keeps off the audio
amplifier, WS2812 and HDMI pins; pin 85 is also on the TF slot, so leave it empty.
`ulx3s.lpf` keeps off the ESP32 and ADC pins.
