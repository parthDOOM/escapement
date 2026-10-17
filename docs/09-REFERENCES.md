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
| [RP2040 datasheet, chapter 3 (PIO)](https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf) | Prior art in deterministic I/O processors |
