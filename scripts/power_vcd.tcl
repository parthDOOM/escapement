# Power from real switching activity (D-29 evidence).
#
#   openroad -exit power_vcd.tcl   with env ODB, SDC, PDK_LIB, MACRO_DIR, SCRIPTS_DIR,
#                                  _TCL_ENV_IN (a step's _env.tcl), SET_RC, VCD, OUT
#
# Typical corner. With VCD unset, reports the default static activity estimate instead,
# which is what LibreLane's own power.rpt shows.

if { [info exists ::env(_TCL_ENV_IN)] } { source $::env(_TCL_ENV_IN) }
set pdk $::env(PDK_LIB)
set mac $::env(MACRO_DIR)/RM_IHPSG13_1P_256x32_c2_bm_bist

define_corners nom_typ_1p20V_25C
read_liberty -corner nom_typ_1p20V_25C $pdk/sg13cmos5l_stdcell_typ_1p20V_25C.lib
read_liberty -corner nom_typ_1p20V_25C $mac/RM_IHPSG13_1P_256x32_c2_bm_bist_typ_1p20V_25C.lib

read_db $::env(ODB)
read_sdc $::env(SDC)
set_propagated_clock [all_clocks]
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
set ::db [ord::get_db]
set ::tech [$::db getTech]
set ::block [[$::db getChip] getBlock]
source $::env(SET_RC)
estimate_parasitics -placement

if { [info exists ::env(VCD)] && $::env(VCD) != "" } {
    read_vcd -scope tb/user_project $::env(VCD)
}
report_power > $::env(OUT)
report_power -highest_power_instances 15 >> $::env(OUT)
