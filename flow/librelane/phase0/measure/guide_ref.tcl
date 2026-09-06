# Measurement A, step 1 of 2 -- the REFERENCE: global-route everything and keep
# the guides, so the bus nets' guides can be extracted (extract_bus_guides.py)
# and fed back in step 2 through `read_guides`.  Mirrors LibreLane's
# scripts/openroad/common/grt.tcl (layers, adjustment, macro extension).
#   env: ODB (a post-CTS two_reg32 .odb), OUT (directory)
read_db $::env(ODB)
set_routing_layers -signal $::env(RT_MIN_LAYER)-$::env(RT_MAX_LAYER) \
                   -clock  $::env(RT_MIN_LAYER)-$::env(RT_MAX_LAYER)
set_global_routing_layer_adjustment * 0.3
set_macro_extension 0
# -allow_congestion: a gcell over by one is DRT's to resolve, and refusing
# here would end the measurement before it starts -- the two_reg32 top
# with BUDA's pin template ends global routing with 4 overflow units at
# the die's east margin and detailed-routes DRC-clean (2026-09-06).
global_route -congestion_iterations 50 -allow_congestion -verbose
write_guides $::env(OUT)/all.guide
puts "REF: wrote $::env(OUT)/all.guide"
