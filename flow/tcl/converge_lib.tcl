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
#   cap CELL FLOOR CAP            one per cell layer band in force (layer
#                                 names, `-` for no floor) — which cells
#                                 the -reserve actually capped
#   demand INST CELL LAYER BITS USED SUPPLY PCT   one per demand row
#
# `demand` rows are `buda::query demand`'s and `cap` rows `buda::query
# caps`' — the numbers a reservation efficiency is computed from (what was
# RESERVED over an instance against what the top actually USED there).
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
        # instance  worst%  own%[F]  collide (inst)` — the kept/nsig pair
        # is what a driver needs to price the reservation, and the
        # derivation is the one place that knows it.  The own% column sits
        # BETWEEN worst% and collide and is matched explicitly: unanchored,
        # the pattern captured its digits as the collision count (Codex P2
        # on #935).
        foreach ln [split $out \n] {
            if {[regexp {^\s*(\S+)\s+(\S+)\s+(\d+)%\s+(\d+)/(\d+)\s+\d+\s+\S+\s+[\d.]+%\s+\d+%F?\s+(\d+)(?:\s|$)} \
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
    foreach c [buda::query caps] { puts $f "cap $c" }
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
    set d [dict create shares {} caps {} demand {}]
    foreach ln [split $text \n] {
        if {[string trim $ln] eq ""} { continue }
        set key [lindex $ln 0]
        set rest [lrange $ln 1 end]
        switch -- $key {
            share  { dict lappend d shares $rest }
            cap    { dict lappend d caps $rest }
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
#   blind   (`cap` rows — the bands `reserve_top_layers` declared): every
#           track of every layer ABOVE the cell's cap, over every instance
#           of a CAPPED cell.  Read off the bands in force rather than off
#           `reserve N`, because the command caps every cell BELOW the top
#           level and leaves the top level unrestricted — counting the top
#           N layers over EVERY instance charged the uncapped cells'
#           tracks (the SoC's `quad_cell`, the largest footprints of all)
#           as reserved (Codex P2 on #935);
#   derived (`share` lines): per cell and layer, the fraction the thinning
#           removes — `1 - kept/nsig` — of the instance's supply.
#
# `rep` is the round whose DEMAND is read; `policy` the report whose
# `share` lines (derived the round before) and `cap` rows were in force —
# the same report for a blind round, the previous round's for an informed
# one.  Returns {reserved used pairs}: reserved ÷ used is the efficiency
# (>1 is padding, <1 means the top used tracks the reservation did not
# cover — `used` counts EVERY top track over the instance on that layer,
# inside or outside the reserved slots, which is what the honest ratio
# wants).
proc converge::efficiency {rep policy} {
    set frac_by_cell_layer [dict create]
    foreach s [dict get $policy shares] {
        lassign $s cell layer pct kept nsig coll
        dict set frac_by_cell_layer [list $cell $layer] \
            [expr {1.0 - double($kept) / double($nsig)}]
    }
    set cap_by_cell [dict create]
    foreach c [dict get $policy caps] {
        lassign $c cell floor cap
        dict set cap_by_cell $cell $cap
    }
    set reserved 0.0; set used 0; set pairs 0
    foreach r [dict get $rep demand] {
        lassign $r inst cell layer bits u supply pct
        set frac 0.0
        if {[dict exists $cap_by_cell $cell]
            && [converge::_layer_cmp $layer [dict get $cap_by_cell $cell]] > 0} {
            set frac 1.0
        }
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

# The cells a derivation file was derived FOR, as the `-derive_cells` list
# a later round pins its scope to.  Read off the file's `# scope:` header,
# which names every cell in scope — a cell with no line this round (the
# top took nothing over it) is still in scope next round, and reading the
# scope off the emitted lines dropped it (Codex P2 on #935).  A file with
# no header (an older derivation) falls back to the lines.  An EXPLICITLY
# empty scope comes back as the header's own `(none)`, distinct from the
# empty string: the driver must not read it as "unspecified" and let the
# next round derive under the vehicle's default scope, which would hand
# shares to cells the first derivation never covered (Codex P2 on #935) —
# `converge::scope_empty` is the test, and an empty scope means there is
# nothing to hand down, so no informed round is worth a session.
proc converge::scope_of {shares_file} {
    set f [open $shares_file]; set text [read $f]; close $f
    if {[regexp -line {^# scope: (.*)$} $text -> s]} {
        return [string trim $s]
    }
    set cells [dict create]
    foreach ln [split $text \n] {
        if {[regexp {^set_cell_layer_share (\S+) } $ln -> c]} { dict set cells $c 1 }
    }
    return [join [dict keys $cells] ,]
}

proc converge::scope_empty {cells} { return [expr {$cells eq "(none)"}] }

# Whether the blind sweep runs ANOTHER round after this one: only while the
# design is dirty AND the blind arm itself was asked for.  The bu arm needs
# blind round 1 alone (its measurement); with `-arms bu` the reserved
# rounds are neither wanted nor cheap (Codex P2 on #935).
proc converge::blind_more {arms clean} {
    return [expr {!$clean && "blind" in $arms}]
}

proc converge::_layer_cmp {a b} {
    regexp {(\d+)$} $a -> na; regexp {(\d+)$} $b -> nb
    return [expr {$na - $nb}]
}
