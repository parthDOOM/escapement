# RM_IHPSG13_1P_512x32_c2_bm_bist

IHP foundry 512-word x 32-bit single-port SRAM (bit mask, BIST): an alternative build of
the v2 program store's four pages (src/escapement_pstore.v, MACROS). Vendored as the 256x32 is.

## Source

- The ttihp27a flow's PDK as installed by ciel: ihp-sg13 version
  `89d42ee6d2fd9b50b86a1aada6fee6673c3e6008`, `ihp-sg13cmos5l/libs.ref/sg13cmos5l_sram/`.
  Its 256x32 views are byte-identical to the vendored 256x32 (from `2bbec755`).
- Fetched 2026-11-12. No file was modified. The `.v` wrapper uses the 256x32 directory's
  `RM_IHPSG13_1P_core_behavioral_bm_bist.v` (compile with `-DFUNCTIONAL`).

## Files (SHA-256)

    8c12371fc9839e797753ac9c228fca30ae03d3c0c7252b33dd4bc882f341f204 *RM_IHPSG13_1P_512x32_c2_bm_bist.cdl
    c0da50c3e63190f49776e0f5127ae241500605f2f6c9cf6000b625a0772560d1 *RM_IHPSG13_1P_512x32_c2_bm_bist.gds
    6e483caff960deaeb97e69a205bbb811b94b4aba15d27d48658e9d5248733cfd *RM_IHPSG13_1P_512x32_c2_bm_bist.lef
    7864a139a0fbc1fe04d2aac973b3cef46baa0af553d95ca7cd199c02596515dc *RM_IHPSG13_1P_512x32_c2_bm_bist.txt
    2bec4b86c665f485e795da0321da2d8153947090e93d11555b19f222cbee7ed8 *RM_IHPSG13_1P_512x32_c2_bm_bist.v
    1e88cf828c81de2512d5294d0a3922854cebc8913de9af485f3e7c9fb08c3a42 *RM_IHPSG13_1P_512x32_c2_bm_bist_fast_1p32V_m55C.lib
    f23f9f8c41eff35014189b22a223295b91fd1db9d11655dba68f4591a2ed43e6 *RM_IHPSG13_1P_512x32_c2_bm_bist_slow_1p08V_125C.lib
    1a7409f75c7107385d01f8b2e9cde5d22ee54e1e914643b70e19833e03f3e9b0 *RM_IHPSG13_1P_512x32_c2_bm_bist_typ_1p20V_25C.lib
