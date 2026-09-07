# PSM's connectivity check ALONE, on a post-pdngen ODB -- the cheap way to
# judge a PDN plan (docs/internal/librelane_hier_flow.md §11 item 10).
#
#   ../../../phase0/measure/run_or.sh <run_dir> check_grid.tcl ODB=<odb> [NETS="VGND VPWR"]
#
# WHY IT EXISTS.  The PDN verdict is `PSM-0040`/`PSM-0069`, and LibreLane
# reaches it at `OpenROAD.IRDropReport` -- step 56 of 72, after routing and
# RCX, because `analyze_power_grid` needs the SPEF.  So testing whether a
# PDN plan connects has been costing a full top run (77 minutes at N = 8).
# `check_power_grid` is the same connectivity check without the IR solve and
# needs only the ODB, so a plan can be judged straight after
# `OpenROAD.GeneratePDN` -- minutes instead of an hour.  That is what makes
# a PDN offset claim (`pdn_phase.py`'s "shifting every macro by dy=+1.600
# leaves nothing predicted to fail") falsifiable at a sane price.
#
# It is NOT a substitute for the signoff run: it answers connectivity only,
# says nothing about IR drop, and sees the PDN as pdngen left it rather than
# as routing and fill leave it.  Validated against a known-failing plan
# before being trusted -- see the doc.
read_db $::env(ODB)
set nets [expr {[info exists ::env(NETS)] ? $::env(NETS) : "VGND VPWR"}]
set bad 0
foreach n $nets {
    puts "=== check_power_grid -net $n ==="
    if {[catch {check_power_grid -net $n} err]} {
        puts "RESULT $n: FAIL -- $err"
        incr bad
    } else {
        puts "RESULT $n: PASS"
    }
}
puts "check_grid: [llength $nets] net(s), $bad failing"
