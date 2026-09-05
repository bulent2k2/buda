# Measurement A, step 2 of 2 -- does `read_guides` ADD guides for nets that
# `set_nets_to_route` left unrouted, and does `detailed_route` then seat those
# nets INSIDE them?  This is the corridor handoff the plan commits to
# (docs/internal/librelane_hier_flow.md §6, mechanism A).
#   env: ODB (the SAME post-CTS .odb as guide_ref.tcl), OUT (dir holding
#        bus.guide from extract_bus_guides.py), BUS_PREFIX (default "mid[")
read_db $::env(ODB)
set bus_prefix [expr {[info exists ::env(BUS_PREFIX)] ? $::env(BUS_PREFIX) : "mid\["}]
set_routing_layers -signal $::env(RT_MIN_LAYER)-$::env(RT_MAX_LAYER) \
                   -clock  $::env(RT_MIN_LAYER)-$::env(RT_MAX_LAYER)
set_global_routing_layer_adjustment * 0.3
set_macro_extension 0

# 1. Route everything EXCEPT the bus.
set others {}
set bus {}
foreach n [[ord::get_db_block] getNets] {
    set nm [$n getName]
    if {[$n getSigType] eq "POWER" || [$n getSigType] eq "GROUND"} continue
    if {[string first $bus_prefix $nm] == 0} { lappend bus $nm } else { lappend others $nm }
}
puts "A: [llength $bus] bus net(s), [llength $others] other net(s)"
set_nets_to_route $others
global_route -congestion_iterations 50 -verbose
write_guides $::env(OUT)/nobus.guide        ;# EXPECT: no bus entries

# 2. Add the bus guides and see whether the others survive.
read_guides $::env(OUT)/bus.guide
write_guides $::env(OUT)/merged.guide       ;# EXPECT: bus present, others unchanged

# 3. Detailed-route under the merged guides (LibreLane's drt.tcl arguments).
detailed_route -droute_end_iter 64 -or_seed 42 -verbose 1 \
               -output_drc $::env(OUT)/guided.drc
write_def $::env(OUT)/guided.def
write_db  $::env(OUT)/guided.odb
puts "A: wrote $::env(OUT)/guided.def -- now: check_inside.py"
