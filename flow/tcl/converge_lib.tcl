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
# flow/tcl/converge_lib.tcl — the E1 hooks a vehicle exposes, and the
# helpers the loop driver (`converge.tcl`) reads them back with.
#
# Convergence-ladder experiment E1 (docs/internal/convergence_ladder.md)
# compares two ways of handing a block its layer budget:
#
#   blind    the block routes freely, is frozen, the top routes against it;
#            on a dirty endpoint the POLICY takes the block's highest layer
#            away (`reserve_top_layers N`, N growing by a step) and every
#            instance is re-spun — a round per step, until clean or the
#            stack is exhausted;
#   derived  plan the whole design top-down once, READ the per-instance
#            per-layer demand, hand each cell the COMPLEMENT as a share
#            (`derive_cell_layer_shares file`), solve the template once
#            under it, copy, route the top — one informed round (measured,
#            not assumed: a second informed round re-derives from the first).
#
# A vehicle (`soc.tcl`, `tpu.tcl`) takes the SAME five options through
# `converge::opt`, so the driver runs either without knowing which:
#
#   -reserve N      reserve_top_layers N before bundling (0 = none)
#   -shares FILE    `source FILE` before bundling — the derived budget
#   -derive FILE    derive_cell_layer_shares file FILE at the end (after
#                   the flow's own healing, so it reads the FINAL plan)
#   -derive_cells A,B  the derivation's `cells` scope (default: the
#                   command's own — marks, else bundle-owning cells); a
#                   second informed round passes the first round's cells
#                   so the scope cannot drift between rounds
#   -derive_opts T  extra derive tokens (`nofloor` = the pure complement,
#                   the derivation's own strawman defence)
#   -noheal         skip the vehicle's heal_if_dirty (the healerless table)
#   -report FILE    write the machine-readable report the driver reads
#
# The report is one fact per line, Tcl-list shaped:
#
#   verdict_first OVL UNPL VIOL   the first DNUTS audit (before any healing)
#   verdict OVL UNPL VIOL         the endpoint the vehicle's verdict reads
#   bundles N
#   marks N                       eligible cells marked bottom-up (0 top-down)
#   healed 0|1
#   wl_detailed N                 -1 when the run did not report one
#   reserve N                     the -reserve in force
#   share CELL LAYER PCT KEPT NSIG COLLIDE   one per derived line (-derive)
#   demand INST CELL LAYER BITS USED SUPPLY PCT   one per demand row
#
# `demand` rows are `buda::query demand`'s — the numbers a reservation
# efficiency is computed from (what was RESERVED over an instance against
# what the top actually USED there).
# ============================================================

namespace eval converge {
    variable reserve 0
    variable shares ""
    variable derive ""
    variable derive_cells ""
    variable derive_opts ""
    variable noheal 0
    variable report ""
    variable marks 0
    variable first {-1 -1 -1}
    variable derived {}
}

# One option from a vehicle's `switch` default branch.  Returns the number
# of argv words consumed (0 = not ours, so the vehicle's own handling runs).
proc converge::opt {argv argi} {
    set opt [lindex $argv $argi]
    set val [lindex $argv [expr {$argi + 1}]]
    set have_val [expr {$argi + 1 < [llength $argv]}]
    switch -- $opt {
        -reserve {
            if {!$have_val || ![string is integer -strict $val] || $val < 0} {
                error "$opt takes a non-negative integer"
            }
            set converge::reserve $val
            return 2
        }
        -shares  { if {!$have_val} { error "$opt needs a file" }
                   set converge::shares [file normalize $val]; return 2 }
        -derive  { if {!$have_val} { error "$opt needs a file" }
                   set converge::derive [file normalize $val]; return 2 }
        -derive_cells { if {!$have_val} { error "$opt needs a cell list" }
                   set converge::derive_cells $val; return 2 }
        -derive_opts { if {!$have_val} { error "$opt needs tokens" }
                   set converge::derive_opts $val; return 2 }
        -report  { if {!$have_val} { error "$opt needs a file" }
                   set converge::report [file normalize $val]; return 2 }
        -noheal  { set converge::noheal 1; return 1 }
    }
    return 0
}

# `buda::set_bottom_up ...` with the mark count kept for the report: the
# `*` form says how many eligible cells it marked, the named form names one.
proc converge::mark {args} {
    set out [buda::set_bottom_up {*}$args]
    if {[regexp {marked (\d+) eligible cell} $out -> n]} {
        incr converge::marks $n
    } elseif {[regexp {bottom_up = on} $out]} {
        incr converge::marks
    }
    return $out
}

# The budget, declared where the vehicle declares its bands: AFTER the
# marks (a share on a marked cell thins the template's pattern) and BEFORE
# bundling (`_apply_layer_policies` resolves at `run_planner hier`).
proc converge::policy {} {
    if {$converge::reserve > 0} { buda::reserve_top_layers $converge::reserve }
    if {$converge::shares ne ""} {
        if {![file exists $converge::shares]} {
            error "converge: -shares file not found: $converge::shares"
        }
        buda::source $converge::shares
    }
}

# Snapshot the FIRST audit's verdict (right after `check_design dnuts`,
# before the vehicle heals) — the healerless endpoint of the same run.
proc converge::first_audit {} {
    set converge::first [list [buda::query overlaps] [buda::query unplaced] \
                              [buda::query violations]]
}

proc converge::heal_wanted {} { return [expr {!$converge::noheal}] }

# The tail: derive (when asked) off the final plan, then the report.  Runs
# BEFORE the vehicle's verdict, which stops the engine.
proc converge::finish {healed} {
    variable derived
    set derived {}
    if {$converge::derive ne ""} {
        set cmd [list file $converge::derive]
        if {$converge::derive_cells ne ""} {
            lappend cmd cells $converge::derive_cells
        }
        lappend cmd {*}$converge::derive_opts
        set out [buda::derive_cell_layer_shares {*}$cmd]
        # The table rows: `cell  layer  share%  kept/nsig  insts  worst
        # instance  worst%  collide (inst)` — the kept/nsig pair is what a
        # driver needs to price the reservation, and the derivation is the
        # one place that knows it.
        foreach ln [split $out \n] {
            if {[regexp {^\s*(\S+)\s+(\S+)\s+(\d+)%\s+(\d+)/(\d+)\s+\d+\s+\S+\s+[\d.]+%\s+(\d+)} \
                        $ln -> cell layer pct kept nsig coll]} {
                lappend derived [list $cell $layer $pct $kept $nsig $coll]
            }
        }
    }
    if {$converge::report eq ""} { return }
    set f [open $converge::report w]
    puts $f "verdict_first $converge::first"
    puts $f "verdict [buda::query overlaps] [buda::query unplaced] [buda::query violations]"
    puts $f "bundles [buda::query bundles]"
    puts $f "marks $converge::marks"
    puts $f "healed $healed"
    set wl -1
    catch {
        set rw [buda::report_wirelength]
        regexp {total detailed WL = (\d+)} $rw -> wl
    }
    puts $f "wl_detailed $wl"
    puts $f "reserve $converge::reserve"
    foreach d $derived { puts $f "share $d" }
    set rows [buda::query demand]
    if {$rows ne "-1"} {
        foreach r $rows { puts $f "demand $r" }
    }
    close $f
}

# ── driver side ───────────────────────────────────────────────────────────

# A report file as a dict: scalar keys, plus `shares` and `demand` lists.
proc converge::read_report {path} {
    if {![file exists $path]} { error "converge: no report at $path" }
    set f [open $path]; set text [read $f]; close $f
    set d [dict create shares {} demand {}]
    foreach ln [split $text \n] {
        if {[string trim $ln] eq ""} { continue }
        set key [lindex $ln 0]
        set rest [lrange $ln 1 end]
        switch -- $key {
            share  { dict lappend d shares $rest }
            demand { dict lappend d demand $rest }
            default { dict set d $key $rest }
        }
    }
    return $d
}

# Reservation efficiency off a report: RESERVED signal tracks over every
# instance against the tracks the top USED there, per (instance, layer)
# pair that carries a reservation at all.
#
#   blind   (`reserve N`): the top N layers of the stack, every track of
#           them, over every instance;
#   derived (`share` lines): per cell and layer, the fraction the thinning
#           removes — `1 - kept/nsig` — of the instance's supply.
#
# `rep` is the round whose DEMAND is read; `policy` the report whose
# `share` lines (derived the round before) and `reserve` were in force —
# the same report for a blind round, the previous round's for an informed
# one.  Returns {reserved used pairs}: reserved ÷ used is the efficiency
# (>1 is padding, <1 means the top used tracks the reservation did not
# cover — `used` counts EVERY top track over the instance on that layer,
# inside or outside the reserved slots, which is what the honest ratio
# wants).
proc converge::efficiency {rep policy} {
    set reserve [lindex [dict get $rep reserve] 0]
    set frac_by_cell_layer [dict create]
    foreach s [dict get $policy shares] {
        lassign $s cell layer pct kept nsig coll
        dict set frac_by_cell_layer [list $cell $layer] \
            [expr {1.0 - double($kept) / double($nsig)}]
    }
    # The layers the design has, ranked by their trailing number.
    set layers [dict create]
    foreach r [dict get $rep demand] { dict set layers [lindex $r 2] 1 }
    set ranked [lsort -command converge::_layer_cmp [dict keys $layers]]
    set top [lrange $ranked end-[expr {$reserve - 1}] end]
    if {$reserve <= 0} { set top {} }
    set reserved 0.0; set used 0; set pairs 0
    foreach r [dict get $rep demand] {
        lassign $r inst cell layer bits u supply pct
        set frac 0.0
        if {$layer in $top} { set frac 1.0 }
        if {[dict exists $frac_by_cell_layer [list $cell $layer]]} {
            set frac [dict get $frac_by_cell_layer [list $cell $layer]]
        }
        if {$frac <= 0.0} { continue }
        set reserved [expr {$reserved + $frac * $supply}]
        incr used $u
        incr pairs
    }
    return [list [expr {int(round($reserved))}] $used $pairs]
}

proc converge::_layer_cmp {a b} {
    regexp {(\d+)$} $a -> na; regexp {(\d+)$} $b -> nb
    return [expr {$na - $nb}]
}
