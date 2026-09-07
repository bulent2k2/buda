# Arm H+B's top-side handoff: put BUDA's corridors into the ODB that
# LibreLane's OWN detailed router will then read
# (docs/internal/librelane_hier_flow.md §8 step 7f, mechanism A).
#
#   env: ODB   the ODB LibreLane left after the last step before
#              DetailedRouting (which the run was told to skip)
#        GUIDE the guide file `emit_guides` wrote
#        OUT   a directory for the merged guide file and the new ODB
#
# WHY THIS IS THE WHOLE OF THE OPENROAD SIDE.  The phase-0 measurement
# (§8 step 5b) ended in `detailed_route` here, because it was measuring
# whether the router FOLLOWS a guide.  An arm must not: every routing
# metric, the DRC count and the signoff that follow are LibreLane's, so
# the detailed route has to be LibreLane's own step.  Guides live in the
# ODB (`dbGuide`), so this writes them there and hands the file back --
# `librelane --last-run --from OpenROAD.DetailedRouting -e odb=...` picks
# it up and the rest of the flow is untouched.
#
# Two things phase 0 measured are load-bearing here and are not obvious:
#  * `read_guides` REPLACES the guide set rather than adding to it, so the
#    merge happens in the FILE -- the other nets' guides from this script's
#    own global route, concatenated with BUDA's -- and is read back as one
#    set.  (Tcl channels, not `exec cat`: a path may contain spaces.)
#  * Net names in the database are DEF-escaped (`fa_0\[0\]`), and
#    `set_nets_to_route` matches either spelling but NOT the doubly-escaped
#    form a Tcl LIST gives a backslashed string -- and a call matching
#    NOTHING silently routes everything.  So names are compared plain.
#
# WHICH NETS ARE WITHHELD is read off the guide file itself, not from a
# prefix: the nets BUDA supplied a corridor for are exactly the nets the
# global router must not route.  A systolic array has one bus prefix per
# link (fa_, fp_, tp_, p_, w_, ...), and a prefix list would be one more
# thing to keep in step with the emitter.
proc plain {s} { return [string map {\\ {}} $s] }

read_db $::env(ODB)
set guide_file $::env(GUIDE)
set out $::env(OUT)

# The nets BUDA wrote a corridor for.  The format alternates: a NET NAME,
# `(`, one `x1 y1 x2 y2 layer` box per line, `)` -- so the name is read
# off the STRUCTURE (the first non-blank line, and the one after each
# `)`), never guessed from the line's shape: a net may be named anything,
# `0` and `met3` included.
set buda {}
set expect_name 1
set fh [open $guide_file r]
while {[gets $fh line] >= 0} {
    set line [string trim $line]
    if {$line eq ""} continue
    if {$line eq ")"} { set expect_name 1; continue }
    if {$line eq "("} { set expect_name 0; continue }
    if {$expect_name} { dict set buda [plain $line] 1 }
}
close $fh
if {[dict size $buda] == 0} { error "H+B: $guide_file names no net" }

set_routing_layers -signal $::env(RT_MIN_LAYER)-$::env(RT_MAX_LAYER) \
                   -clock  $::env(RT_MIN_LAYER)-$::env(RT_MAX_LAYER)
set_macro_extension 0

set others {}
set found 0
foreach n [[ord::get_db_block] getNets] {
    set nm [plain [$n getName]]
    if {[$n getSigType] eq "POWER" || [$n getSigType] eq "GROUND"} continue
    if {[dict exists $buda $nm]} { incr found } else { lappend others $nm }
}
puts "H+B: [dict size $buda] guided net(s), $found of them in the design, [llength $others] routed here"
if {$found == 0} {
    error "H+B: none of the guide file's nets is in the design -- the guides\
           were written against a different netlist (names are compared plain:\
           [lindex [dict keys $buda] 0])"
}
if {$found != [dict size $buda]} {
    puts "H+B: WARNING: [expr {[dict size $buda] - $found}] guided net(s) are\
          not in the design; their corridors are read anyway and OpenROAD\
          reports them"
}

# 1. Route everything EXCEPT the guided nets.  -allow_congestion for the
#    reason step 5b records: a gcell over by one is the detailed router's
#    to resolve, and refusing here would end the arm before it starts.
set_nets_to_route $others
global_route -congestion_iterations 50 -allow_congestion -verbose
write_guides $out/others.guide

# 2. Merge in the file, read back as one set.  BUDA's entries REPLACE the
#    router's for those nets, so the others' file is filtered as it is
#    copied rather than concatenated whole: `set_nets_to_route` says what
#    `global_route` ROUTES, and the ODB handed to this script already
#    carries LibreLane's OWN guides from step 39 for every net -- so
#    `write_guides` emits the guided nets too and a straight concatenation
#    named all 256 of them TWICE (measured at N=2), leaving which corridor
#    the router obeys up to `read_guides`.  Phase 0's recipe could not see
#    it: its ODB was the post-CTS one, where no net had a guide yet.
set merged [open [file join $out merged.guide] w]
set dropped 0
set fh [open $out/others.guide r]
set skip 0
set copy 1
while {[gets $fh line] >= 0} {
    set s [string trim $line]
    if {$s eq "("} { if {$copy} { puts $merged $line }; continue }
    if {$s eq ")"} { if {$copy} { puts $merged $line }; set copy 1; continue }
    if {$s ne "" && ![string match "* *" $s]} {
        # a NET NAME line (a box line has coordinates and a layer on it)
        set copy [expr {![dict exists $buda [plain $s]]}]
        if {!$copy} { incr dropped; continue }
    }
    if {$copy} { puts $merged $line }
}
close $fh
close $merged
if {$dropped != [dict size $buda]} {
    puts "H+B: NOTE: dropped $dropped of [dict size $buda] router corridors for\
          the guided nets (the rest had none)"
}
set merged [open [file join $out merged.guide] a]
set in [open $guide_file r]
fcopy $in $merged
close $in
close $merged
read_guides [file join $out merged.guide]

# 3. Hand the ODB back to LibreLane, guides and all.
write_db $out/guided.odb
puts "H+B: wrote $out/guided.odb -- resume with librelane --last-run --from\
      OpenROAD.DetailedRouting -e odb=$out/guided.odb"
