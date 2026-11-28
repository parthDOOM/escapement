# References

## Competition and flow

| Source | Used for |
| --- | --- |
| [Jane Street: protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/) | Brief: IHP 130 nm CMOS5L via Tiny Tapeout, tile allocation, protocol targets |
| [Tiny Tapeout IHP template, `main` @ 4b1204a](https://github.com/TinyTapeout/ttihp-verilog-template/tree/4b1204ab387b5ab94db1d57067051d8d89a2f83c) (v1.2-v1.6: `cmos5l` @ b86a2a7) | Repository layout, workflows, base flow configuration |
| [tt-gds-action @ e7a50f8](https://github.com/TinyTapeout/tt-gds-action/tree/e7a50f8f46f9f8df4b2c9935bfeb1cbc6b6ae4f6), the `ttihp27a` tag when pinned (it moved to `df73a1e` on 24 November 2026) (v1.2-v1.6: `ihp-cmos5l` @ 3412659) | CI harden, precheck and gate-level test actions |
| [tt-support-tools, `main` @ 0922544](https://github.com/TinyTapeout/tt-support-tools/tree/0922544e6c7f6126047c3fba720b0437fa32a1b8) (v1.2-v1.6: `ihp-sg13cmos5l` @ d66cf17) | Tile sizes (`tech/ihp-sg13cmos5l/tile_sizes.yaml`) and the precheck |
| [LibreLane](https://github.com/librelane/librelane) and its [configuration reference](https://librelane.readthedocs.io/en/latest/reference/configuration.html) | RTL-to-GDS flow, version 3.1.0.dev4 (v1.2-v1.6: 3.1.0.dev3) |
| [LibreLane issue #1039](https://github.com/librelane/librelane/issues/1039) | `-threads None` in 3.1.0.dev3 (D-23) |
| [Tiny Tapeout local hardening guide](https://www.tinytapeout.com/guides/local-hardening/) | Local flow setup |
| [Tiny Tapeout demo board](https://tinytapeout.com/specs/pcb-etr/) | RP2350 host, Pmod headers |
| [Tiny Tapeout GPIO specification](https://www.tinytapeout.com/specs/gpio/) | Pin conventions |
| [OpenROAD issue #9816](https://github.com/The-OpenROAD-Project/OpenROAD/issues/9816) | A clock gate disconnected in clock-tree synthesis, fixed upstream but not in the flow's release (D-45, D-61, D-65) |
| [OpenROAD issue #11977](https://github.com/The-OpenROAD-Project/OpenROAD/issues/11977) | A crash of the resizer inside timing-driven global placement, on the flow's own OpenROAD commit; the same bug as this design's occasional placement crashes, with its reproducer ([24-TOOL-BUGS](24-TOOL-BUGS.md)) |
| [OpenSTA issue #398](https://github.com/The-OpenROAD-Project/OpenSTA/issues/398) | An out-of-bounds access on the same rebuffering call chain, on an IHP design with SRAM ([24-TOOL-BUGS](24-TOOL-BUGS.md)) |
| Icarus Verilog [#509](https://github.com/steveicarus/iverilog/issues/509), [#746](https://github.com/steveicarus/iverilog/issues/746), [#889](https://github.com/steveicarus/iverilog/issues/889), [#945](https://github.com/steveicarus/iverilog/issues/945) and [PR #1694](https://github.com/steveicarus/iverilog/pull/1694) | The SDF annotation gaps that `test/sdf_prep.py` works around ([24-TOOL-BUGS](24-TOOL-BUGS.md)) |

## PDK and macros

| Source | Used for |
| --- | --- |
| [IHP-Open-PDK @ 89d42ee](https://github.com/IHP-GmbH/IHP-Open-PDK/tree/89d42ee6d2fd9b50b86a1aada6fee6673c3e6008) (v1.2-v1.6: [2bbec755](https://github.com/IHP-GmbH/IHP-Open-PDK/tree/2bbec755dc67ca3db0261c3d6163e15735d66710)) | SG13CMOS5L standard cells, I/O and SRAM views (v2: the 1024x32 and 512x32 macros from ciel's install of 89d42ee) |
| [SG13G2 SRAM views](https://github.com/IHP-GmbH/IHP-Open-PDK/tree/2bbec755dc67ca3db0261c3d6163e15735d66710/ihp-sg13g2/libs.ref/sg13g2_sram) | The `RM_IHPSG13_1P_256x32_c2_bm_bist` macro (re-exported as `sg13cmos5l_sram`) |
| [IHP SRAM documentation](https://ihp-open-pdk-docs.readthedocs.io/en/latest/contents/reference_libraries/sram.html) | Macro family and interface |
| [Loom: CMOS5L SRAM integration notes](https://github.com/thomasgilbert481/tt_um_loom/blob/main/docs/tt_cmos5l_facts.md) (Apache-2.0) | The SRAM integration recipe and PDN script that ours is adapted from |
| [Ken Pettit: IHP SRAM macro on a CMOS5L tile](https://kdp1965.github.io/ihp-um-janestreet-prism/ihp_sram_macro.html) | An alternative SRAM integration, KLayout DRC sign-off |

## Protocols and prior art

| Source | Used for |
| --- | --- |
| [NXP UM10204, I2C-bus specification, rev. 7](https://www.nxp.com/docs/en/user-guide/UM10204.pdf) | I2C timing, open-drain behaviour, clock stretching |
| [USB 2.0 specification](https://www.usb.org/document-library/usb-20-specification) | Low-speed signalling, packet fields, CRC-5 and CRC-16, turnaround (`usb_hid`, the USB host model) |
| IEEE Std 802.3, clause 14 (10BASE-T) | Manchester coding, preamble and start-of-frame delimiter, the frame check sequence, TP_IDL (`eth_tx`, `eth_rx`, the Ethernet models) |
| ISO 11898-1 (CAN data link layer) | Frame format, bit stuffing, CRC-15 polynomial 0x4599 (`can_tx`, `can_rx`, the CAN models) |
| [Arm Debug Interface ADIv5.2, ARM IHI 0031G](https://support.arm.com/documentation/ihi0031/latest) | SWD packets, turnaround, ACK and parity (`swd_idcode`, the SW-DP model) |
| [Vishay, Data Formats for IR Remote Control, doc. 80071](https://www.vishay.com/docs/80071/dataform.pdf) and [SB-Projects, NEC protocol](https://www.sbprojects.net/knowledge/ir/nec.php) | NEC carrier, burst and slot timing (`nec_tx`, `nec_rx`, `nec_tx_gen`) |
| [ESTA, ANSI E1.11-2008 (DMX512-A)](https://tsp.esta.org/tsp/documents/published_docs.php) | DMX break, mark-after-break and slot timing (`dmx512_tx`, the DMX model) |
| [Linux, `drivers/media/cec/core/cec-pin.c`](https://github.com/torvalds/linux/blob/master/drivers/media/cec/core/cec-pin.c) | HDMI-CEC bit timing and receive windows (`cec`, the CEC device model) |
| [brushlesswhoop.com, DSHOT: the missing handbook](https://brushlesswhoop.com/dshot-and-bidirectional-dshot/) | DShot600 timing, frame and CRC, bidirectional DShot (`dshot_tx`, the ESC model) |
| [NXP AN4219, SENT/SPC driver for MPC5510](https://www.nxp.com/docs/en/application-note/AN4219.pdf) and [Microchip, recommended J2716 CRC](https://onlinedocs.microchip.com/oxy/GUID-04B5982F-17EC-4A6E-B7FE-72DF0A5463B9-en-US-3/GUID-23F22D39-15E3-4B81-A752-CC095EE469F1.html) | SENT pulse format, CRC-4 seed and table (`sent_tx`, the SENT receiver model) |
| [Microchip, DALI bus communication](https://onlinedocs.microchip.com/oxy/GUID-0CDBB4BA-5972-4F58-98B2-3F0408F3E10B-en-US-1/GUID-910C4B97-3A38-4F04-A78A-DF74EE1A3612.html) | DALI Manchester coding, half-bit time, forward-to-backward settling time (`dali`, the controller model) |
| Solomon Systech, SSD1306 datasheet | The OLED controller's I2C commands and display memory (OLED Pong, [23-OLED-PONG](23-OLED-PONG.md); the DOOM console's horizontal addressing, [25-DOOM-CONSOLE](25-DOOM-CONSOLE.md)) |
| [rp2040-doom](https://github.com/kilograham/rp2040-doom), Graham Sanderson's port of Chocolate Doom to the RP2040 | The game the DOOM console runs on its host, a Pico W ([25-DOOM-CONSOLE](25-DOOM-CONSOLE.md)) |
| [sigrok-dumps](https://github.com/sigrokproject/sigrok-dumps) | Logic-analyser recordings of real devices that `esclearn` learns protocols from ([20-LEARN](20-LEARN.md)) |
| [RP2040 datasheet, chapter 3 (PIO)](https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf) | Prior art in deterministic I/O processors |
| David May, *The XMOS XS1 Architecture*, XMOS Ltd., 2009 | Prior art: hardware threads with timed ports |
| S. A. Edwards and E. A. Lee, "The Case for the Precision Timed (PRET) Machine", DAC 2007; I. Liu et al., "A PRET Microarchitecture Implementation with Repeatable Timing and Competitive Performance", ICCD 2012 | Prior art: repeatable instruction timing, deadline instructions and the thread-interleaved pipeline |
| [riscv-formal](https://github.com/YosysHQ/riscv-formal) | The shape of the instruction-set proof: a checker per instruction, held against the core at every commit ([04-VERIFICATION](04-VERIFICATION.md)) |

## Outside judges

| Source | Used for |
| --- | --- |
| [sigrok](https://sigrok.org): sigrok-cli 0.7.2 with its release's decoders and libsigrokdecode's current ones (commit 71f4514), and PulseView | The protocol decoders that judge the simulated pins, and the capture viewer ([04-VERIFICATION](04-VERIFICATION.md), [17-INSTRUMENT](17-INSTRUMENT.md)) |
| [IRMP](https://www.mikrocontroller.net/articles/IRMP), Frank Meyer's infrared decoder, through libsigrokdecode's `ir_irmp` | The infrared judge |
| [Wireshark](https://www.wireshark.org) and its `tshark` dissectors | The judge for 10BASE-T and UDP |
| [rtl_433](https://github.com/merbanan/rtl_433) | A 433 MHz judge |
| [rc-switch](https://github.com/sui77/rc-switch) | A 433 MHz judge, from its receiver code |
| [gusmanb's LogicAnalyzer](https://github.com/gusmanb/logicanalyzer) | The firmware of the second Pico that records the FPGA bench as an outside logic analyser |
