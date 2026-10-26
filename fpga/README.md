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

Yosys synthesis of the whole chip (`fpga/estimate.sh`):

| Family | Logic | Flip-flops | Block RAM | Boards that fit |
| --- | ---: | ---: | --- | --- |
| Xilinx 7-series | about 8,400 LUTs | about 4,400 | 2 RAMB18 | Artix-7 35T and larger (Digilent Basys 3, Arty A7, Nexys A7, Cmod A7-35T); Spartan-7 25 |
| Lattice ECP5 | about 11,800 LUT4 (CCU2C counted as two) | 4,216 | 2 DP16KD | LFE5U-25F and larger (ULX3S, OrangeCrab 25F) |
| Lattice iCE40 | about 10,500 LUT4 | about 4,200 | 4 RAM40 | none: the UP5K (5,280) and HX8K (7,680) are too small |

The chip runs at 25 MHz; an FPGA reaches that easily, and 24 MHz for the USB firmware.

## Wiring

The chip's 24 Tiny Tapeout pins go to the board's headers: `uio[7:0]` (bank A, the
protocol pins, bidirectional), `uo_out[7:3]` and `ui_in[7:3]` (bank B), and the SPI host
interface on `ui_in[2:0]` / `uo_out[2:0]`. The host is an RP2040 or RP2350 board running
the bring-up script and host library (`sw/rp2350`), as on the Tiny Tapeout demo board.
The board-specific top level and constraints come when the board is known.
