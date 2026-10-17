# Hold-uncertainty sweep on a post-CTS database (D-28 evidence).
#
#   openroad -exit hold_sweep.tcl     with env ODB, SDC, PDK_LIB, MACRO_DIR, OUT
#
# For each corner and each hold uncertainty u, counts register endpoints whose hold slack
# is below 0 and below the resizer's hold margin (0.1 ns post-CTS): the population that
# repair_timing -hold would buffer. Setup uncertainty is left at the SDC's 0.25 ns.

if { [info exists ::env(ENV_TCL)] } { source $::env(ENV_TCL) }
set pdk $::env(PDK_LIB)
set mac $::env(MACRO_DIR)/RM_IHPSG13_1P_256x32_c2_bm_bist

define_corners nom_fast_1p32V_m40C nom_typ_1p20V_25C nom_slow_1p08V_125C
read_liberty -corner nom_fast_1p32V_m40C $pdk/sg13cmos5l_stdcell_fast_1p32V_m40C.lib
read_liberty -corner nom_typ_1p20V_25C $pdk/sg13cmos5l_stdcell_typ_1p20V_25C.lib
read_liberty -corner nom_slow_1p08V_125C $pdk/sg13cmos5l_stdcell_slow_1p08V_125C.lib
read_liberty -corner nom_fast_1p32V_m40C $mac/RM_IHPSG13_1P_256x32_c2_bm_bist_fast_1p32V_m55C.lib
read_liberty -corner nom_typ_1p20V_25C $mac/RM_IHPSG13_1P_256x32_c2_bm_bist_typ_1p20V_25C.lib
read_liberty -corner nom_slow_1p08V_125C $mac/RM_IHPSG13_1P_256x32_c2_bm_bist_slow_1p08V_125C.lib

read_db $::env(ODB)
read_sdc $::env(SDC)
set_propagated_clock [all_clocks]
if { [info exists ::env(SET_RC)] } {
    source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
    set ::db [ord::get_db]
    set ::tech [$::db getTech]
    set ::block [[$::db getChip] getBlock]
    source $::env(SET_RC)
}
estimate_parasitics -placement

set out [open $::env(OUT) w]
puts $out "corner,hold_uncertainty_ns,endpoints_slack_lt_0,endpoints_slack_lt_margin,worst_hold_ns"
foreach u {0.25 0.15 0.10 0.05 0.0} {
    set_clock_uncertainty -hold $u [all_clocks]
    foreach c {nom_fast_1p32V_m40C nom_typ_1p20V_25C nom_slow_1p08V_125C} {
        set neg 0
        set mar 0
        set worst 1e9
        set paths [find_timing_paths -path_delay min -corner $c -group_path_count 100000 \
                       -endpoint_path_count 1 -slack_max 0.1]
        foreach p $paths {
            set s [get_property $p slack]
            if { $s < 0.1 } { incr mar }
            if { $s < 0 } { incr neg }
            if { $s < $worst } { set worst $s }
        }
        puts $out "$c,$u,$neg,$mar,$worst"
        flush $out
    }
}
close $out
