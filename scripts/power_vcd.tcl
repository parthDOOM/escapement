# Power from real switching activity (D-29 evidence).
#
#   openroad -exit power_vcd.tcl   with env ODB, SDC, PDK_LIB, MACRO_DIR, SCRIPTS_DIR,
#                                  _TCL_ENV_IN (a step's _env.tcl), SET_RC, VCD, OUT,
#                                  POWER_PERIOD (ns, optional), SPEF (optional)
#
# Typical corner. Parasitics are estimated from placement, or with SPEF set (the signed-off
# layout's typical-corner extraction) read from the routed wires. With VCD unset, reports the default static activity estimate instead,
# which is what LibreLane's own power.rpt shows.

if { [info exists ::env(_TCL_ENV_IN)] } { source $::env(_TCL_ENV_IN) }
set pdk $::env(PDK_LIB)

define_corners nom_typ_1p20V_25C
read_liberty -corner nom_typ_1p20V_25C $pdk/sg13cmos5l_stdcell_typ_1p20V_25C.lib
# every SRAM macro's liberty (v2: the 1024x32 store and the 256x32 tape; an unused one is harmless)
foreach mac [lsort [glob -type d $::env(MACRO_DIR)/RM_IHPSG13_1P_*]] {
    set name [file tail $mac]
    read_liberty -corner nom_typ_1p20V_25C $mac/${name}_typ_1p20V_25C.lib
}

read_db $::env(ODB)
read_sdc $::env(SDC)
# At the 25 MHz rating whatever period the layout was signed off at (v1.5: 30 ns): OpenSTA
# takes the clock pins' activity from the constraints' clock, not from the VCD.
if { [info exists ::env(POWER_PERIOD)] && $::env(POWER_PERIOD) != "" } {
    create_clock -name clk -period $::env(POWER_PERIOD) [get_ports clk]
}
set_propagated_clock [all_clocks]
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
set ::db [ord::get_db]
set ::tech [$::db getTech]
set ::block [[$::db getChip] getBlock]
if { [info exists ::env(SPEF)] && $::env(SPEF) != "" } {
    # the routed layout: the parasitics extracted from its wires
    read_spef $::env(SPEF)
} else {
    source $::env(SET_RC)
    estimate_parasitics -placement
}

if { [info exists ::env(VCD)] && $::env(VCD) != "" } {
    read_vcd -scope tb/user_project $::env(VCD)
}
report_power > $::env(OUT)
report_power -highest_power_instances 15 >> $::env(OUT)
