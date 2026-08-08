# this is the bottomup flow where multiple occurrences are not aligned properly on signal tracks
# compare with mix2_fast_on_aligned_sql.buda running on aligned bdb from mix2_align_and_save.buda
source mix_tracks.buda
open_bdb mix2.bdb.sql

# Bottom-up: plan/NUTS each cell template once, copy to all instances.
# Must come after open_bdb (flags persist in the BDB) and before run_planner hier.
set_bottom_up dnuts1
set_bottom_up dnuts2
set_bottom_up dogleg1
set_bottom_up dogleg2

derive_busterms 2
add_blocks_from_bdb 0
add_blocks_from_bdb 1 skip
add_blocks_from_bdb 2 skip
run_hier_bundler depth 2
# dump_hbundles
generate_hier_topologies
run_planner hier 5 signal_tracks
dump_topologies
run_nuts

# run_detailed_nuts
# Error: run_detailed_nuts: 16 bottom-up instance(s) see different signal tracks than their template reference (dnuts1/chip/i_dnuts1_1; dnuts1/chip/i_dnuts1_2; dnuts1/chip/i_dnuts1_3; dnuts1/chip/i_dnuts1_4; dnuts1/chip/i_dnuts1_5 (+11 more)). Fix the placement (offset instances by a multiple of the layer track pitch / align grid overrides), or accept per-instance solving with 'check_template_tracks on_mismatch independent'.

# Healing stage a: feed the measured NUTS overlaps back as band demand
# (negotiate), then the rnr hill-climb finishes the residual — the locked
# bottom-up copies are respected (never re-routed).
negotiate_congestion
#ripup_reroute
check_design nuts
check_template_tracks on_mismatch independent
run_detailed_nuts
# Healing stage b: negotiate each DNUTS-open segment's window, then hill-climb.
negotiate_congestion
#ripup_reroute
check_design
visualize
exit
