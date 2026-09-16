# Copyright 2026 Ben Bulent Basaran
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# ============================================================
# flow/tcl/converge.tcl — the E1 loop driver (convergence ladder item 5).
#
#   btcl flow/tcl/converge.tcl soc 2 4 8            # healerless, every arm
#   btcl flow/tcl/converge.tcl soc 16 -heal         # the vehicle's own healing
#   btcl flow/tcl/converge.tcl soc 4 -step 2        # the blind policy's step
#   btcl flow/tcl/converge.tcl tpu 8 16 -arms blind,bu
#   btcl flow/tcl/converge.tcl soc 2 -nofloor        # the pure complement
#
# Three ways a block gets its layer budget, each run as a LOOP on the same
# vehicle at the same size, until the endpoint is clean or the arm runs out
# of moves (docs/internal/convergence_ladder.md, E1):
#
#   blind   round 1: the block routes freely, is frozen (`-bottomup`), the
#           top routes against it.  Round k: the policy takes the block's
#           highest layers away — `-reserve step*(k-1)` — and every instance
#           is re-spun.  The conventional information flow, rung 0 then 3.
#   td      the design as written: plan the whole design TOP-DOWN once,
#           read the per-instance per-layer demand, hand every cell the
#           complement (`-derive` → `-shares`), solve the template once
#           under it, copy, route the top.  Round r+1 re-derives from
#           round r.  Rung 4 with the top-down plan as its source.
#   bu      the diagnosed loop: the blind round 1 IS the measurement — it
#           routes the top against the frozen blocks, so its demand rows
#           are the top's demand on THIS geometry and its own seats are in
#           the cell frame — and round 2 is the informed one.  Rung 0 then
#           4.  Its round 1 is the blind arm's round 1 (one run, shared).
#
# Every round is one vehicle session (`soc.tcl`/`tpu.tcl` through the
# converge_lib.tcl hooks), its log and report kept in the out dir.  What
# is counted: rounds to a clean endpoint (or the endpoint after the last
# round), template classes solved (Σ marks), the first-audit and final
# verdicts, detailed WL, and the reservation efficiency of the last round
# (tracks RESERVED over instances against tracks the top USED there —
# converge_lib.tcl `efficiency`).
#
# Options:
#   -heal          run the vehicle's heal_if_dirty in every round (default:
#                  healerless — the plain pipeline's verdict)
#   -step N        the blind policy's layers per round (default 1)
#   -maxreserve N  the blind policy's ceiling (default 4: a band must keep
#                  an H and a V layer, and the six-layer stack has M2/M3
#                  under M4..M7)
#   -informed R    informed rounds per derived arm (default 2)
#   -arms a,b,c    subset of blind,td,bu (default all)
#   -nofloor       derive the PURE complement (no own-need floor): the
#                  derivation's own strawman defence
#   -out DIR       where logs/reports/tables go (default e1_out beside the
#                  current directory)
#   -tag T         a tag in the table file's name
#   -j N           BUDA_THREADS_REQUEST for every session
# ============================================================

set repo [file dirname [file dirname [file dirname [file normalize [info script]]]]]
source [file join $repo flow tcl converge_lib.tcl]

# ── the command line ──────────────────────────────────────────────────────
if {$argc < 2} {
    puts stderr "usage: converge.tcl soc|tpu <size> ... \[-heal\] \[-step N\]\
                 \[-maxreserve N\] \[-informed R\] \[-arms a,b\] \[-nofloor\]\
                 \[-out DIR\] \[-tag T\] \[-j N\]"
    exit 2
}
set vehicle [lindex $argv 0]
if {$vehicle ni {soc tpu}} { error "converge.tcl: vehicle must be soc|tpu, got '$vehicle'" }
set sizes {}
set heal 0; set step 1; set maxreserve 4; set informed 2; set nofloor 0
set arms {blind td bu}; set out e1_out; set tag ""; set threads ""
set i 1
while {$i < $argc} {
    set a [lindex $argv $i]
    if {[string is integer -strict $a]} { lappend sizes $a; incr i; continue }
    set v [lindex $argv [expr {$i + 1}]]
    switch -- $a {
        -heal       { set heal 1; incr i }
        -nofloor    { set nofloor 1; incr i }
        -step       { set step $v; incr i 2 }
        -maxreserve { set maxreserve $v; incr i 2 }
        -informed   { set informed $v; incr i 2 }
        -arms       { set arms [split $v ,]; incr i 2 }
        -out        { set out $v; incr i 2 }
        -tag        { set tag $v; incr i 2 }
        -j          { set threads $v; incr i 2 }
        default     { error "converge.tcl: unknown option '$a'" }
    }
}
if {![llength $sizes]} { error "converge.tcl: give at least one size" }
# The blind loop advances by `step` until it passes `maxreserve`: a zero
# step would re-run the same round forever on a dirty design (Codex P2 on
# #935), so the three loop bounds are checked before any session starts.
if {![string is integer -strict $step] || $step < 1} {
    error "converge.tcl: -step takes a positive integer, got '$step'"
}
if {![string is integer -strict $maxreserve] || $maxreserve < 0} {
    error "converge.tcl: -maxreserve takes a non-negative integer, got '$maxreserve'"
}
if {![string is integer -strict $informed] || $informed < 0} {
    error "converge.tcl: -informed takes a non-negative integer, got '$informed'"
}
foreach a $arms { if {$a ni {blind td bu}} { error "converge.tcl: unknown arm '$a'" } }
file mkdir $out
set script [file join $repo flow tcl $vehicle.tcl]
if {$threads ne ""} { set ::env(BUDA_THREADS_REQUEST) $threads }

# ── one session ───────────────────────────────────────────────────────────
# Runs the vehicle with the given words, keeps its log, reads its report.
# A dirty verdict is exit 1 (the vehicle's own rule) and NOT a failure here:
# the report is the result.  No report = the session crashed: stop, and
# name the log.
proc session {name size args} {
    global script out heal
    set log [file join $out $name.log]
    set rep [file join $out $name.rep]
    file delete -force $rep
    set words [list $size {*}$args -report $rep]
    if {!$heal} { lappend words -noheal }
    set t0 [clock milliseconds]
    catch {exec [info nameofexecutable] $script {*}$words > $log 2>@1}
    set secs [expr {([clock milliseconds] - $t0) / 1000.0}]
    if {![file exists $rep]} {
        error "converge.tcl: session '$name' left no report — see $log"
    }
    set r [converge::read_report $rep]
    dict set r name $name
    dict set r secs $secs
    dict set r words $words
    return $r
}

proc clean {r} {
    lassign [dict get $r verdict] ov un vi
    return [expr {$ov == 0 && $un == 0 && $vi == 0}]
}

proc cells_of {shares_file} {
    set cells [dict create]
    set f [open $shares_file]
    foreach ln [split [read $f] \n] {
        if {[regexp {^set_cell_layer_share (\S+) } $ln -> c]} { dict set cells $c 1 }
    }
    close $f
    return [join [dict keys $cells] ,]
}

# The informed rounds shared by td and bu: round r sources F_{r-1}, derives
# F_r for the next, scope pinned to F_0's cells.  Returns the rounds' reports.
proc informed_rounds {prefix size f0} {
    global informed nofloor
    set cells [cells_of $f0]
    set rounds {}
    set prev $f0
    for {set r 1} {$r <= $informed} {incr r} {
        set fr [file join [file dirname $f0] ${prefix}_shares_r$r.buda]
        set words [list -bottomup -shares $prev -derive $fr]
        if {$cells ne ""} { lappend words -derive_cells $cells }
        if {$nofloor} { lappend words -derive_opts nofloor }
        set rep [session ${prefix}_r$r $size {*}$words]
        dict set rep policy $prev
        lappend rounds $rep
        if {[clean $rep]} { break }
        set prev $fr
    }
    return $rounds
}

# ── the table ─────────────────────────────────────────────────────────────
set rows {}
proc row {size arm round policy rep policy_rep} {
    global rows vehicle heal
    lassign [dict get $rep verdict_first] fo fu fv
    lassign [dict get $rep verdict] o u v
    set eff "—"
    set reserved "—"; set used "—"
    if {$policy_rep ne ""} {
        lassign [converge::efficiency $rep $policy_rep] reserved used pairs
        if {$used > 0} {
            set eff [format %.2f [expr {double($reserved) / $used}]]
        } elseif {$reserved > 0} {
            set eff "inf"
        }
    }
    set wl [lindex [dict get $rep wl_detailed] 0]
    set wl_s [expr {($u > 0) ? "($wl)" : $wl}]
    lappend rows [list $vehicle $size [expr {$heal ? "on" : "off"}] $arm $round $policy \
                      "$fo/$fu/$fv" "$o/$u/$v" $wl_s [lindex [dict get $rep marks] 0] \
                      $reserved $used $eff [format %.1f [dict get $rep secs]]]
}

set summary {}
foreach size $sizes {
    set p ${vehicle}${size}
    set blind1 ""
    # ── blind ── (also bu's round 1, so it always runs when bu does)
    if {"blind" in $arms || "bu" in $arms} {
        set k 1
        set solved 0
        set blind_rounds {}
        while {1} {
            set reserve [expr {$step * ($k - 1)}]
            if {$reserve > $maxreserve} { break }
            set words [list -bottomup]
            if {$reserve > 0} { lappend words -reserve $reserve }
            if {$k == 1} { lappend words -derive [file join $out ${p}_bu_shares_r0.buda] }
            if {$k == 1 && $nofloor} { lappend words -derive_opts nofloor }
            set rep [session ${p}_blind_r$k $size {*}$words]
            if {$k == 1} { set blind1 $rep }
            # A blind round's reservation: the report's own `reserve`, no
            # shares — the policy rep is the round itself.
            row $size blind $k "reserve $reserve" $rep [expr {$reserve > 0 ? $rep : ""}]
            lappend blind_rounds $rep
            incr solved [lindex [dict get $rep marks] 0]
            if {[clean $rep]} { break }
            incr k
        }
        if {"blind" in $arms} {
            set last [lindex $blind_rounds end]
            lappend summary [list $size blind [llength $blind_rounds] $solved \
                                 [expr {[clean $last] ? "clean" : "dirty"}] \
                                 [join [dict get $last verdict] /]]
        }
    }
    # ── td ──
    if {"td" in $arms} {
        set f0 [file join $out ${p}_td_shares_r0.buda]
        set words [list -derive $f0]
        if {$nofloor} { lappend words -derive_opts nofloor }
        set td0 [session ${p}_td_r0 $size {*}$words]
        row $size td 0 "top-down" $td0 ""
        set rounds [informed_rounds ${p}_td $size $f0]
        set prev_rep $td0
        set r 1
        set solved 0
        foreach rep $rounds {
            row $size td $r "shares r[expr {$r - 1}]" $rep $prev_rep
            set prev_rep $rep
            incr solved [lindex [dict get $rep marks] 0]
            incr r
        }
        set last [lindex $rounds end]
        lappend summary [list $size td [expr {1 + [llength $rounds]}] $solved \
                             [expr {[clean $last] ? "clean" : "dirty"}] \
                             [join [dict get $last verdict] /]]
    }
    # ── bu ──
    if {"bu" in $arms} {
        set f0 [file join $out ${p}_bu_shares_r0.buda]
        set rounds [informed_rounds ${p}_bu $size $f0]
        set prev_rep $blind1
        set r 1
        set solved [lindex [dict get $blind1 marks] 0]
        foreach rep $rounds {
            row $size bu $r "shares r[expr {$r - 1}]" $rep $prev_rep
            set prev_rep $rep
            incr solved [lindex [dict get $rep marks] 0]
            incr r
        }
        set last [lindex $rounds end]
        lappend summary [list $size bu [expr {1 + [llength $rounds]}] $solved \
                             [expr {[clean $last] ? "clean" : "dirty"}] \
                             [join [dict get $last verdict] /]]
    }
}

# ── print ─────────────────────────────────────────────────────────────────
set hdr "| vehicle | size | heal | arm | round | policy | first ovl/unpl/viol | final ovl/unpl/viol | detailed WL | classes | reserved | used | reserved÷used | s |"
set sep "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
set lines [list $hdr $sep]
foreach r $rows { lappend lines "| [join $r { | }] |" }
lappend lines "" "| size | arm | rounds | classes solved | endpoint | final ovl/unpl/viol |" "|---|---|---|---|---|---|"
foreach s $summary { lappend lines "| [join $s { | }] |" }
set text [join $lines \n]
puts $text
set name e1_${vehicle}[expr {$heal ? "_healed" : "_healerless"}]_step$step[expr {$nofloor ? "_nofloor" : ""}]
if {$tag ne ""} { append name _$tag }
set f [open [file join $out $name.md] w]
puts $f "<!-- converge.tcl $argv -->"
puts $f $text
close $f
puts "converge.tcl: table written to [file join $out $name.md]"
