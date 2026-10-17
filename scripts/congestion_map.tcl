# Where is global routing congested? One FastRoute pass, no overflow iterations, on a
# step's database, with the flow's own layer setup; writes OpenROAD's congestion report.
#
#   openroad -exit congestion_map.tcl   with env SCRIPTS_DIR, _TCL_ENV_IN (a GRT step's
#                                       _env.tcl), ODB, OUT
source $::env(_TCL_ENV_IN)
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
read_db $::env(ODB)
set ::db [ord::get_db]
set ::tech [$::db getTech]
set ::block [[$::db getChip] getBlock]
source $::env(SCRIPTS_DIR)/openroad/common/set_routing_layers.tcl
source $::env(SCRIPTS_DIR)/openroad/common/set_layer_adjustments.tcl
global_route -congestion_iterations 0 -allow_congestion \
    -congestion_report_file $::env(OUT)
