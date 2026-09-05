# Measurement B -- do the routers KEEP a net's FIXED wiring?  Undocumented in
# both grt's and drt's README, so it is measured, not assumed (plan §6, B).
#
# Input is a DEF whose bus nets carry `+ FIXED` wiring (mark_fixed.py, from a
# routed DEF), read into a fresh session with the same LEFs LibreLane used.
# Then global_route on EVERY net (does grt skip/keep the fixed ones?) and
# detailed_route (does drt leave them byte-identical?  compare_bus_wires.py).
#   env: DEF (the fixed DEF), MACRO_LEF (reg32.lef), OUT
foreach lef [concat [list $::env(TECH_LEF)] [split $::env(CELL_LEFS) " "] [list $::env(MACRO_LEF)]] {
    if {$lef ne ""} { read_lef $lef }
}
read_def $::env(DEF)
set_routing_layers -signal $::env(RT_MIN_LAYER)-$::env(RT_MAX_LAYER) \
                   -clock  $::env(RT_MIN_LAYER)-$::env(RT_MAX_LAYER)
set_global_routing_layer_adjustment * 0.3
set_macro_extension 0
global_route -congestion_iterations 50 -verbose
write_guides $::env(OUT)/fixed_after_grt.guide   ;# does grt emit guides for FIXED nets?
detailed_route -droute_end_iter 64 -or_seed 42 -verbose 1 \
               -output_drc $::env(OUT)/fixed.drc
write_def $::env(OUT)/fixed_after.def
puts "B: wrote $::env(OUT)/fixed_after.def -- now: compare_bus_wires.py"
