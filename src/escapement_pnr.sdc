# escapement_pnr.sdc - timing constraints for placement and routing (PNR_SDC_FILE).
#
# escapement.sdc, plus two settings for the latch banks (D-40). Each bank's data arrives just
# after its gated clock opens, so STA times it as time borrowed through the latch, D to Q.
# With that arc on 2,365 latches the resizer re-propagates arrivals through every latch
# after each buffer it inserts: post-CTS hold repair ran past 5 GB and never finished (v1.3:
# 19 s). Here a latch is timed from its gate only, like the flip-flop it replaces, and the
# repair takes seconds; with the D inputs' setup checks left out (below) the resizer also
# keeps a real setup margin. Sign-off (SIGNOFF_SDC_FILE = escapement.sdc) keeps the D-to-Q arc,
# so the reported setup and hold timing include the borrowed time.

source [file join [file dirname $::env(PNR_SDC_FILE)] escapement.sdc]

set esc_latches [get_cells -hierarchical -filter "ref_name == sg13cmos5l_dlhrq_1"]
set_disable_timing -from D -to Q $esc_latches
# Their D inputs would still report exactly 0 slack (time borrowed), which hides every real
# path and forces a zero setup margin. The paths into D are one flip-flop and a 2-to-1
# multiplexer long, and sign-off checks them; here they are left out.
set esc_latch_d {}
foreach c $esc_latches {
    foreach p [get_pins -of_objects $c] {
        if {[get_property $p lib_pin_name] == "D"} { lappend esc_latch_d $p }
    }
}
set_false_path -setup -to $esc_latch_d

# Slew, for repair only (3.1, 11-22). The library allows 2.507 ns at every corner, and
# repair_design judges slew at the first corner the flow defines, which LibreLane always
# makes fast (its corners are defined in Tcl array order; config.json, escapement_mbuf, has
# the same finding for the SRAM pins). Slow-corner slew is 2.24 to 2.46 times the fast
# value on every pin measured, so repair acted only on edges that would be 3.4 to 3.7 ns at
# the slow corner, and any minimum-size gate left on a long route could fail sign-off there.
# Limited to 1.0 ns here, repair acts at 0.6 ns fast after global routing (40% margin) and
# 0.8 ns before it: about 1.4 and 1.9 ns at the slow corner. Sign-off (escapement.sdc) keeps
# the library limit.
set_max_transition 1.0 [current_design]
