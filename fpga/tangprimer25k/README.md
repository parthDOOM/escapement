# Hardware bench: Pico W + Tang Primer 25K Dock

The chip's RTL running on an FPGA, driven through its 24 Tiny Tapeout pins, its clock and its
reset by a Raspberry Pi Pico W that runs the repository's own bring-up script
(`sw/rp2350/bringup.py`). `barepico.py` stands in for the Tiny Tapeout demo board's firmware,
so the script runs unchanged.

| Part | Board |
| --- | --- |
| Host | Raspberry Pi Pico W (RP2040, MicroPython 1.24.1), soldered headers |
| Chip | Sipeed Tang Primer 25K Dock (GW5A-LV25MG121NC1/I0), loaded over its onboard USB debugger |
| Build | `fpga/escapement_board.v` with `ESC_NO_CG` and `fpga/escapement_sram_fpga.v`, as in `fpga/README.md` |

## Result, 2026-11-08 (v1.6, commit d3b5dbc)

| Step | Result | Evidence |
| --- | --- | --- |
| Wiring check (`pinid`) | `WIRING: PASS`, all 26 signals on the expected GPIOs | `results/2026-11-08/pinid.txt` |
| 0 board and project | PASS | `results/2026-11-08/bringup.log` |
| 1 clock and reset | PASS: 25 MHz from the Pico, outputs correct in and after reset | same |
| 2 host link | PASS: STATUS over SPI, fast and slow SCK | same |
| 3 memories | PASS: 4 patterns x 256 words in both SRAMs, tape reads during a run | same |
| 4 pins and clock rate | PASS: every uio, input and bank B pin; chip clock 25.0094 MHz | same |
| 5 firmware | PASS: 48 of 48 firmware match the model and their certificates | same, and `proof.json` |

The bitstream (sha256 16e5da57...) and the firmware bundles were built from commit d3b5dbc with
clean sources: `chip.MANIFEST` and `bundles.MANIFEST` record the commit and the sha256 of every
input. `chip-pnr.log` is the place-and-route log of that bitstream.

What this does not show: the hardened netlist (a different synthesis tool and cell library), the
clock gates and latch banks (`ESC_NO_CG` makes them plain flip-flops), the IHP SRAM macro (replaced
by block RAM with the same timing contract). Those stay covered by the gate-level suites, the
RTL-to-netlist equivalence checks and timing signoff.

## Files

| File | What |
| --- | --- |
| `tangprimer25k.cst` | Dock pins: the 24 Tiny Tapeout pins on the three Pmod sockets, clk and rst_n on the 40-pin header |
| `build.sh` | `pinid`, `chip` and `bundles` builds, each with a MANIFEST |
| `clkskew.py` | nextpnr post-route check: clock delay to every sink |
| `pinid.v`, `pinid.py` | wiring check: the FPGA sends each signal's index on its pin; the Pico decodes every GPIO |
| `barepico.py` | the demo board's `tt` object on plain MicroPython GPIO, for `bringup.py` |
| `test_pinid.py`, `test_barepico.py` | software tests: pinid.v simulated through pinid.py; bring-up through barepico's wiring on the simulated chip |
| `bench.ps1` | Windows helper: build, deploy, load, run (the Dock's USB goes to WSL with usbipd-win) |
| `hierarchy.sh`, `hierarchy.py` | block-level LUT, flip-flop and bus counts of the FPGA build |
| `results/2026-11-08/` | the run above |

## Running it

Tools: OSS CAD Suite (yosys, nextpnr-himbaechel, gowin_pack, openFPGALoader) and `mpremote`.

    fpga/tangprimer25k/build.sh pinid && fpga/tangprimer25k/build.sh chip && fpga/tangprimer25k/build.sh bundles

1. MicroPython on the Pico W (RPI_PICO_W 1.24.1 UF2), then copy `sw/rp2350/bringup.py`,
   `sw/eschost/protocol.py`, `barepico.py`, `pinid.py` and `build/bundles/*.json` (into `bundles/`)
   onto it. `bench.ps1 deploy` does this and checks every file by sha256.
2. Wire it with both boards unplugged (below). Each board has its own USB to the same computer.
3. `openFPGALoader -b tangprimer25k build/pinid/pinid.fs`, then on the Pico `import pinid; pinid.run()`.
   It must print `WIRING: PASS`; otherwise it names each wire that is swapped, open, shorted or
   bridged. The Pico drives nothing in this step.
4. `openFPGALoader -b tangprimer25k build/chip/chip.fs`, then
   `import barepico, bringup; bringup.run(io=barepico.open_io(), keep_going=True)`.
   Never run bring-up with pinid loaded: both drive pins.
5. Stage 5 leaves its telemetry in `proof.json` on the Pico.

Bitstreams load into the FPGA's SRAM: a power cycle clears them.

## Wiring

Every signal is 3.3 V on both boards and no power wire is used. Lay the Dock component side up
with the 40-pin header J3 on the far edge and the three Pmod sockets facing you: J6 on the left,
J5 in the middle, J4 on the right.

The Dock prints no pin numbers on its Pmod sockets, and Sipeed's schematic numbers them 1 to 12
with pins 1 and 2 = 3V3, 3 and 4 = GND (not the standard Pmod numbering in the `.cst` comments).
So the table goes by position, looking into the openings: **column 1 (both rows) is 3V3 and stays
empty**, column 2 is GND, columns 3 to 6 are signals, bit 0 is the top row's rightmost hole.
**J3 pin 11 is +5V** and stays bare; pin 12 is J3's only GND. J3 pin 1 is at the left end of the
inner row (the board marks 1, 2, 39 and 40); odd pins are the inner row.

| # | Suggested colour | Pico pin | Signal | Dock | Hole (facing the socket) | Ball / Sipeed pin |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | black | 8 GND | GND | J4 | top row, col 2 | GND / 3 |
| 2 | brown | 2 GP1 | ui_in[0] CS_n | J4 | top row, col 6 | G11 / 11 |
| 3 | red | 4 GP2 | ui_in[1] SCK | J4 | top row, col 5 | D11 / 9 |
| 4 | orange | 5 GP3 | ui_in[2] MOSI | J4 | top row, col 4 | B11 / 7 |
| 5 | yellow | 6 GP4 | ui_in[3] | J4 | top row, col 3 | C11 / 5 |
| 6 | green | 7 GP5 | ui_in[4] | J4 | bottom row, col 6 | G10 / 12 |
| 7 | blue | 9 GP6 | ui_in[5] | J4 | bottom row, col 5 | D10 / 10 |
| 8 | purple | 10 GP7 | ui_in[6] | J4 | bottom row, col 4 | B10 / 8 |
| 9 | grey | 11 GP8 | ui_in[7] | J4 | bottom row, col 3 | C10 / 6 |
| 10 | black | 13 GND | GND | J5 | top row, col 2 | GND / 3 |
| 11 | black | 18 GND | GND | J5 | bottom row, col 2 | GND / 4 |
| 12 | brown | 14 GP10 | uo_out[0] MISO | J5 | top row, col 6 | A11 / 11 |
| 13 | red | 15 GP11 | uo_out[1] IRQ | J5 | top row, col 5 | E11 / 9 |
| 14 | orange | 16 GP12 | uo_out[2] RUN | J5 | top row, col 4 | K11 / 7 |
| 15 | yellow | 17 GP13 | uo_out[3] | J5 | top row, col 3 | L5 / 5 |
| 16 | green | 19 GP14 | uo_out[4] | J5 | bottom row, col 6 | A10 / 12 |
| 17 | blue | 20 GP15 | uo_out[5] | J5 | bottom row, col 5 | E10 / 10 |
| 18 | purple | 21 GP16 | uo_out[6] | J5 | bottom row, col 4 | L11 / 8 |
| 19 | grey | 22 GP17 | uo_out[7] | J5 | bottom row, col 3 | K5 / 6 |
| 20 | black | 23 GND | GND | J6 | top row, col 2 | GND / 3 |
| 21 | black | 28 GND | GND | J6 | bottom row, col 2 | GND / 4 |
| 22 | brown | 12 GP9 | uio[0] | J6 | top row, col 6 | F5 / 11 |
| 23 | red | 24 GP18 | uio[1] | J6 | top row, col 5 | G7 / 9 |
| 24 | orange | 25 GP19 | uio[2] | J6 | top row, col 4 | H8 / 7 |
| 25 | yellow | 26 GP20 | uio[3] | J6 | top row, col 3 | H5 / 5 |
| 26 | green | 27 GP21 | uio[4] | J6 | bottom row, col 6 | G5 / 12 |
| 27 | blue | 29 GP22 | uio[5] | J6 | bottom row, col 5 | G8 / 10 |
| 28 | purple | 31 GP26 | uio[6] | J6 | bottom row, col 4 | H7 / 8 |
| 29 | grey | 32 GP27 | uio[7] | J6 | bottom row, col 3 | J5 / 6 |
| 30 | black | 3 GND | GND (twist with clk) | J3 | pin 12 (outer row, col 6) | GND |
| 31 | white | 1 GP0 | clk 25 MHz | J3 | pin 9 (inner row, col 5) | L3 |
| 32 | grey | 34 GP28 | rst_n | J3 | pin 10 (outer row, col 5) | L4 |

Never wired: Pico pins 30 RUN, 33 AGND, 35 ADC_VREF, 36 3V3(OUT), 37 3V3_EN, 38 GND, 39 VSYS,
40 VBUS; Pmod column 1 on all three sockets; J3 pin 11 and every J3 pin other than 9, 10 and 12.
Pin positions were checked against Sipeed's Dock schematic (v1.0, 2023, and Rev v1.1,
2025-08-28), its interactive BOM (pad positions) and litex-boards' Dock connector table.

## Why the clock is on L3

The clock enters on ball L3 (J3 pin 9), a clock-capable pin. nextpnr-0.11.1-47 cannot put the
neighbouring L4 (GCLKT_4) on the GW5A-25's global clock network: a build with the clock there fell
back to general routing, with 5.5 ns of clock skew and 191 hold violations. From L3 the clock goes
through a BUFG to all 4,600 sinks with 0.000 ns measured skew (`clkskew.py`).

nextpnr still flags hold at the block RAM address pins (40 flags). A single counter feeding a block
RAM gets the same 40 flags, also with the clock on the Dock's own oscillator pin, which the router
puts entirely on global resources: it comes from the tool's block RAM timing model, not the design.
`build.sh chip` therefore allows timing failures only after its own checks pass (clock on the
global network, skew at most 0.05 ns, setup at 25 MHz, no hold flag anywhere else), and bring-up
stage 3 exercises both block RAMs on the board.
