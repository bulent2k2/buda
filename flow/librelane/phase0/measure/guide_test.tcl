# Measurement A, step 2 of 2 -- does `read_guides` deliver guides for nets that
# `set_nets_to_route` left unrouted, and does `detailed_route` then seat those
# nets INSIDE them?  This is the corridor handoff the plan commits to
# (docs/internal/librelane_hier_flow.md §6, mechanism A).
#   env: ODB (the SAME post-CTS .odb as guide_ref.tcl), OUT (dir holding
#        bus.guide from extract_bus_guides.py), BUS_PREFIX (default "mid[")
#
# Two things measured on 2026-09-05 shape this script:
#  * Net NAMES in the database are DEF-escaped (`mid\[0\]`, backslashes and
#    all), and `set_nets_to_route` matches a name in EITHER that spelling or
#    the plain `mid[0]` -- but not the doubly-escaped form a Tcl LIST gives
#    a backslashed string, and a call matching NOTHING silently routes
#    everything.  So the bus is found and passed by its PLAIN name, which
#    has no backslash to be doubled.
#  * `read_guides` REPLACES the guide set rather than adding to it: after
#    it, `write_guides` held only the bus, and `detailed_route` routed only
#    the bus.  So the merge is done in the FILE -- the other nets' guides
#    from step 1 concatenated with the bus's -- and read back as one set.
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
    set nm [string map {\\ {}} [$n getName]]     ;# plain name, see above
    if {[$n getSigType] eq "POWER" || [$n getSigType] eq "GROUND"} continue
    if {[string first $bus_prefix $nm] == 0} { lappend bus $nm } else { lappend others $nm }
}
puts "A: [llength $bus] bus net(s), [llength $others] other net(s)"
if {[llength $bus] == 0} { error "A: no net starts with '$bus_prefix' -- nothing to withhold" }
set_nets_to_route $others
global_route -congestion_iterations 50 -verbose
write_guides $::env(OUT)/nobus.guide        ;# EXPECT: no bus entries

# 2. Merge the bus guides IN THE FILE (read_guides replaces, see above), read.
exec sh -c "cat $::env(OUT)/nobus.guide $::env(OUT)/bus.guide > $::env(OUT)/merged.guide"
read_guides $::env(OUT)/merged.guide

# 3. Detailed-route under the merged guides (LibreLane's drt.tcl arguments).
detailed_route -droute_end_iter 64 -or_seed 42 -verbose 1 \
               -output_drc $::env(OUT)/guided.drc
write_def $::env(OUT)/guided.def
write_db  $::env(OUT)/guided.odb
puts "A: wrote $::env(OUT)/guided.def -- now: check_inside.py"
