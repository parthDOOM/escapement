# RM_IHPSG13_1P_1024x32_c2_bm_bist

IHP foundry 1024-word x 32-bit single-port SRAM (bit mask, BIST): an alternative build of
the v2 program store's four pages (src/escapement_pstore.v, MACROS). Vendored as the 256x32 is.

## Source

- The ttihp27a flow's PDK as installed by ciel: ihp-sg13 version
  `89d42ee6d2fd9b50b86a1aada6fee6673c3e6008`, `ihp-sg13cmos5l/libs.ref/sg13cmos5l_sram/`.
  Its 256x32 views are byte-identical to the vendored 256x32 (from `2bbec755`).
- Fetched 2026-11-12. No file was modified. The `.v` wrapper uses the 256x32 directory's
  `RM_IHPSG13_1P_core_behavioral_bm_bist.v` (compile with `-DFUNCTIONAL`).

## Files (SHA-256)

    58561ee0e35e4b77191318aa5ac7924e64550d9ae797c9b6d3f35ff25b540302 *RM_IHPSG13_1P_1024x32_c2_bm_bist.cdl
    4d1178811e3b83a8f6fc85f017fdd8d9b43629f785929ad60dbbaa2a763b96b1 *RM_IHPSG13_1P_1024x32_c2_bm_bist.gds
    b345b6c44defa630a7266fb1ee878b153e4b22b88461aa8216d3883592c18db2 *RM_IHPSG13_1P_1024x32_c2_bm_bist.lef
    c3074bf684370a9196b6a38060b592df1d703bb2d1af99ef0e1d2a89c0ca0a49 *RM_IHPSG13_1P_1024x32_c2_bm_bist.txt
    0775d362aafb4c375f37ebfc8820404c363aa60e5ab5cf67c28c4fd330ce095d *RM_IHPSG13_1P_1024x32_c2_bm_bist.v
    d9102b5ca8be02e7d4837fde022f1a229f30d899d51a0e67ef09354c95a09adb *RM_IHPSG13_1P_1024x32_c2_bm_bist_fast_1p32V_m55C.lib
    34497364ed23734a929454b451ee9d209b16923e03b80cba258379069af1908c *RM_IHPSG13_1P_1024x32_c2_bm_bist_slow_1p08V_125C.lib
    4eba4f904ad61e1c0dbb78a6b636e486335be19803370048276438e41a0c26d3 *RM_IHPSG13_1P_1024x32_c2_bm_bist_typ_1p20V_25C.lib
