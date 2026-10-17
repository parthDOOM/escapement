# RM_IHPSG13_1P_256x32_c2_bm_bist

IHP foundry 256-word x 32-bit single-port SRAM (bit mask, BIST), the Escapement
program store (docs/01-ARCHITECTURE.md, decision D-2). Vendored so the gds workflow
hardens against a fixed copy.

## Source

- Repository: <https://github.com/IHP-GmbH/IHP-Open-PDK>
- Commit: `2bbec755dc67ca3db0261c3d6163e15735d66710` (the revision tt-gds-action
  `ihp-cmos5l` installs)
- Path: `ihp-sg13g2/libs.ref/sg13g2_sram/{gds,lef,lib,cdl,verilog}/`
- At this commit `ihp-sg13cmos5l/libs.ref/sg13cmos5l_sram` is a symlink to that
  directory: the CMOS5L PDK re-exports the SG13G2 macros.
- Fetched 2026-10-04. No file was modified.

## Files (SHA-256)

    f8989b3a4cc392c57cfafdf82985e27b1e6626b0948b6a926ca6ace2fe01531f *RM_IHPSG13_1P_256x32_c2_bm_bist.cdl
    e77a4d177e02541fe6c58351008455f9a05a715b504f8e318599f3d41bf5f959 *RM_IHPSG13_1P_256x32_c2_bm_bist.gds
    2f1e886f5c363d743da550c5d255b9f31dd62569dda1405a64e2c90b2278127f *RM_IHPSG13_1P_256x32_c2_bm_bist.lef
    074beaf0e84f5ed4f770b2304cf1907a2252ac20d550b8a219c7df1278687bb2 *RM_IHPSG13_1P_256x32_c2_bm_bist.v
    c46525af05ae9c0b99ee9dda0c43a9dffb17b90762e654d635c432d1ad89bb27 *RM_IHPSG13_1P_256x32_c2_bm_bist_fast_1p32V_m55C.lib
    8ab1090ecf527bc71f0c4dc8db05b833e0f9960b4bdb94f3b0351533463b79a2 *RM_IHPSG13_1P_256x32_c2_bm_bist_slow_1p08V_125C.lib
    5592828b5fbce6d368f870ce725ebe9e14d980061a535604e8ad9ada68b219c6 *RM_IHPSG13_1P_256x32_c2_bm_bist_typ_1p20V_25C.lib
    d0a6258f8e9c93d1d5b1f78c0a1d66f30d28b1ad8037e5e07f53d1a4e3670b12 *RM_IHPSG13_1P_core_behavioral_bm_bist.v

The `.v` wrapper instantiates `SRAM_1P_behavioral_bm_bist` from the core model; compile
both with `-DFUNCTIONAL`. Simulation only: synthesis sees the port-only stub
`src/RM_IHPSG13_1P_256x32_c2_bm_bist.v`.

## Physical summary (from the LEF)

- `SIZE 416.64 BY 118.78` (49,488 µm²), `SYMMETRY X Y R90`, `CLASS BLOCK`; geometry on
  Metal1-Metal4 only, so it fits the cmos5l stack under TopMetal1.
- 186 signal pins, all Metal2 stubs on the bottom edge (y 0..0.26), x 3.29..413.35.
- Metal4 power columns, 2.81 µm wide: VSS! full height; VDD! y 0..38.825 and VDDARRAY!
  y 45.465..118.78 sharing columns, except four full-height VDD! in the middle band.
  Column grids and the PDN stripe alignment are derived in `src/pdn_cfg.tcl`.

## Licence

Apache-2.0 (IHP PDK Authors headers in each file; upstream LICENSE is Apache-2.0).
