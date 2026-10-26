# References

## Competition and flow

| Source | Used for |
| --- | --- |
| [Jane Street: protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/) | Brief: IHP 130 nm CMOS5L via Tiny Tapeout, tile allocation, protocol targets |
| [Tiny Tapeout IHP template, `cmos5l` @ b86a2a7](https://github.com/TinyTapeout/ttihp-verilog-template/tree/b86a2a781484bcab7ba522dc5de540086695a430) | Repository layout, workflows, base flow configuration |
| [tt-gds-action, `ihp-cmos5l` @ 3412659](https://github.com/TinyTapeout/tt-gds-action/tree/ihp-cmos5l) | CI harden, precheck and gate-level test actions |
| [tt-support-tools, `ihp-sg13cmos5l` @ d66cf17](https://github.com/TinyTapeout/tt-support-tools/tree/ihp-sg13cmos5l) | Tile sizes (`tech/ihp-sg13cmos5l/tile_sizes.yaml`) and the precheck |
| [LibreLane](https://github.com/librelane/librelane) and its [configuration reference](https://librelane.readthedocs.io/en/latest/reference/configuration.html) | RTL-to-GDS flow, version 3.1.0.dev3 |
| [LibreLane issue #1039](https://github.com/librelane/librelane/issues/1039) | `-threads None` in 3.1.0.dev3 (D-23) |
| [Tiny Tapeout local hardening guide](https://www.tinytapeout.com/guides/local-hardening/) | Local flow setup |
| [Tiny Tapeout demo board](https://tinytapeout.com/specs/pcb-etr/) | RP2350 host, Pmod headers |
| [Tiny Tapeout GPIO specification](https://www.tinytapeout.com/specs/gpio/) | Pin conventions |

## PDK and macros

| Source | Used for |
| --- | --- |
| [IHP-Open-PDK @ 2bbec755](https://github.com/IHP-GmbH/IHP-Open-PDK/tree/2bbec755dc67ca3db0261c3d6163e15735d66710) | SG13CMOS5L standard cells, I/O and SRAM views |
| [SG13G2 SRAM views](https://github.com/IHP-GmbH/IHP-Open-PDK/tree/2bbec755dc67ca3db0261c3d6163e15735d66710/ihp-sg13g2/libs.ref/sg13g2_sram) | The `RM_IHPSG13_1P_256x32_c2_bm_bist` macro (re-exported as `sg13cmos5l_sram`) |
| [IHP SRAM documentation](https://ihp-open-pdk-docs.readthedocs.io/en/latest/contents/reference_libraries/sram.html) | Macro family and interface |
| [Loom: CMOS5L SRAM integration notes](https://github.com/thomasgilbert481/tt_um_loom/blob/main/docs/tt_cmos5l_facts.md) (Apache-2.0) | The SRAM integration recipe and PDN script that ours is adapted from |
| [Ken Pettit: IHP SRAM macro on a CMOS5L tile](https://kdp1965.github.io/ihp-um-janestreet-prism/ihp_sram_macro.html) | An alternative SRAM integration, KLayout DRC sign-off |

## Protocols and prior art

| Source | Used for |
| --- | --- |
| [NXP UM10204, I2C-bus specification, rev. 7](https://www.nxp.com/docs/en/user-guide/UM10204.pdf) | I2C timing, open-drain behaviour, clock stretching |
| [USB 2.0 specification](https://www.usb.org/document-library/usb-20-specification) | Low-speed signalling, packet fields, CRC-5 and CRC-16, turnaround (`usb_hid`, the USB host model) |
| ISO 11898-1 (CAN data link layer) | Frame format, bit stuffing, CRC-15 polynomial 0x4599 (`can_tx`, `can_rx`, the CAN models) |
| [Arm Debug Interface ADIv5.2, ARM IHI 0031G](https://developer.arm.com/documentation/ihi0031/latest) | SWD packets, turnaround, ACK and parity (`swd_idcode`, the SW-DP model) |
| [Vishay, Data Formats for IR Remote Control, doc. 80071](https://www.vishay.com/docs/80071/dataform.pdf) and [SB-Projects, NEC protocol](https://www.sbprojects.net/knowledge/ir/nec.php) | NEC carrier, burst and slot timing (`nec_tx`, `nec_rx`, `nec_tx_gen`) |
| [ESTA, ANSI E1.11-2008 (DMX512-A)](https://tsp.esta.org/tsp/documents/published_docs.php) | DMX break, mark-after-break and slot timing (`dmx512_tx`, the DMX model) |
| [Linux, `drivers/media/cec/core/cec-pin.c`](https://github.com/torvalds/linux/blob/master/drivers/media/cec/core/cec-pin.c) | HDMI-CEC bit timing and receive windows (`cec`, the CEC device model) |
| [brushlesswhoop.com, DSHOT: the missing handbook](https://brushlesswhoop.com/dshot-and-bidirectional-dshot/) | DShot600 timing, frame and CRC, bidirectional DShot (`dshot_tx`, the ESC model) |
| [NXP AN4219, SENT/SPC driver for MPC5510](https://www.nxp.com/docs/en/application-note/AN4219.pdf) and [Microchip, recommended J2716 CRC](https://onlinedocs.microchip.com/oxy/GUID-04B5982F-17EC-4A6E-B7FE-72DF0A5463B9-en-US-3/GUID-23F22D39-15E3-4B81-A752-CC095EE469F1.html) | SENT pulse format, CRC-4 seed and table (`sent_tx`, the SENT receiver model) |
| [Microchip, DALI bus communication](https://onlinedocs.microchip.com/oxy/GUID-0CDBB4BA-5972-4F58-98B2-3F0408F3E10B-en-US-1/GUID-910C4B97-3A38-4F04-A78A-DF74EE1A3612.html) | DALI Manchester coding, half-bit time, forward-to-backward settling time (`dali`, the controller model) |
| [RP2040 datasheet, chapter 3 (PIO)](https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf) | Prior art in deterministic I/O processors |
