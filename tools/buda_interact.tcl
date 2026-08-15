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
# tools/buda_interact.tcl — interactive iteration on ANY .buda flow.
#
#   bin/btcl -i <flow>.buda        (or: tclsh tools/buda_interact.tcl <flow>.buda)
#
# design.tcl and hdesign.tcl carry their own design and their own route
# recipe; this driver carries NEITHER — it runs an arbitrary flow verbatim
# (`buda::source`, the engine's own command, so the flow means exactly what
# `bin/buda` makes it mean) and then drops into the same pin/edit prompt
# (flow/tcl/prompt.tcl — the shared loop the vehicles use).
#
# The flow itself supplies the `replan` recipe, through the RECORDER: with
# BUDA_RECORD armed, every command the flow executes is written down at
# `do_command` — loops unrolled, `source` trees flattened — so after the
# run this driver knows, without parsing a line of the flow's text:
#
#   * whether the flow is HIER or FLAT (a recorded `run_planner hier`),
#   * the flow's own ROUTING TAIL — everything from the first `run_planner`
#     on, minus what a replay must not repeat (viz/exports/dumps, pins and
#     edits, generation and bundling — an iteration re-plans, it does not
#     rebuild) — which becomes the prompt's `replan`, healers, checks and
#     reports included;
#   * whether it routed at all (no `run_nuts` recorded = nothing to verdict),
#   * and where its checkpoint lives (the last file-backed `open_bdb`), so
#     the prompt can say whether a pin will outlive the session.
#
# A flow with no file-backed BDB still iterates IN-session (pin/replan);
# `save <path>` at the prompt snapshots the state so the next session can
# `open_bdb` + `load_pipeline` it.  A flow that ends in `exit` has ended
# the engine session — reported, nothing to iterate.
# ============================================================

set here [file dirname [file normalize [info script]]]
set repo [file dirname $here]
source [file join $here buda.tcl]
source [file join $repo flow tcl prompt.tcl]

if {[llength $argv] != 1} {
    puts stderr "usage: btcl -i <flow>.buda   (a .buda flow takes no arguments)"
    exit 2
}
set flow [file normalize [lindex $argv 0]]
if {![file exists $flow]} {
    puts stderr "buda_interact: no such flow: $flow"
    exit 2
}
set tag [file tail $flow]

# ── arm the recorder, run the flow verbatim ───────────────────────────────
close [file tempfile recpath buda_record]
set ::env(BUDA_RECORD) $recpath
set ::env(BUDA_RECORD_NOTE) "btcl -i $tag"

buda::start
if {[catch {buda::source $flow} err]} {
    puts stderr "$tag: the flow failed: $err"
    catch {buda::stop}
    exit 1
}
# A flow ending in `exit` has ended the session — nothing left to drive.
if {[catch {buda::query bundles} nb]} {
    puts "$tag: the flow ended its session (an `exit` in the script) --\
          nothing to iterate on"
    exit 0
}

# ── what the recorder learned ─────────────────────────────────────────────
set lines {}
set f [open $recpath]
foreach ln [split [read $f] \n] {
    set ln [string trim $ln]
    if {$ln eq "" || [string index $ln 0] eq "#"} continue
    lappend lines $ln
}
close $f
file delete $recpath

# A replay must not repeat: outputs and windows, session control, the
# checkpoint plumbing, pins/edits (already session state — and a prompt
# unpin must not be fought by a replayed pin), or generation/bundling (an
# iteration re-plans, it does not rebuild; regenerating would renumber the
# candidate pools under the user's pins).
set skip_prefixes {
    visualize save_bdb exit open_bdb load_pipeline dump_ edit_ emit_
    export_ select_topolog unpin_topology generate_ run_bundler
    run_hier_bundler import_
}
proc _skipped {verb} {
    foreach p $::skip_prefixes {
        if {[string match ${p}* $verb]} { return 1 }
    }
    return 0
}

set is_hier 0
set routed 0
set ckpt ""
set first_bus ""
set tail {}
set planning 0
foreach ln $lines {
    set verb [lindex [split $ln] 0]
    if {$verb eq "run_planner" && [lindex [split $ln] 1] eq "hier"} {
        set is_hier 1
    }
    if {$verb eq "run_nuts"} { set routed 1 }
    if {$verb eq "open_bdb"} {
        set p [lindex [split $ln] 1]
        if {$p ne ":memory:"} { set ckpt $p }
    }
    if {$first_bus eq "" && $verb eq "add_bus"} {
        # `add_bus d0[8] ...` -> hint `d0`, for the prompt banner's examples.
        regexp {^(\S+?)\[} [lindex [split $ln] 1] -> first_bus
    }
    if {$verb eq "run_planner"} { set planning 1 }
    if {$planning && ![_skipped $verb]} { lappend tail $ln }
}
if {$first_bus eq ""} { set first_bus 1 }   ;# bundle id 1: always a valid selector

puts "\n$tag: [llength $lines] command(s) ran -- [expr {$is_hier ? "HIER" : "FLAT"}] flow"
if {[llength $tail]} {
    puts "$tag: `replan` replays the flow's own routing tail\
          ([llength $tail] command(s), starting `[lindex $tail 0]`)"
} else {
    puts "$tag: the flow never ran the planner -- `replan` is unavailable"
}
if {$ckpt ne ""} {
    puts "$tag: checkpoint $ckpt -- pins persist; resume with open_bdb +\
          load_pipeline (or a checkpointing flow)"
    set snap_base $ckpt
} else {
    puts "$tag: no file-backed BDB -- pins and edits die with this session;\
          `save` (or a raw `save_bdb <path>`) writes a snapshot"
    set snap_base [file rootname $tag].bdb
}

proc replay_tail {} {
    if {![llength $::tail]} {
        puts "[set ::tag]: the flow never planned -- no replan recipe to replay"
        return
    }
    foreach ln $::tail {
        puts "replay> $ln"
        if {[catch {buda::do $ln} err]} {
            puts "[set ::tag]: replay stopped at `$ln`: $err"
            break
        }
    }
}

set pins_dirty [prompt::run $tag "[file rootname $tag]>" replay_tail \
                    $snap_base $first_bus]
if {$pins_dirty} {
    puts "$tag: pins changed since the last route -- re-planning so the\
          checkpoint stays coherent"
    replay_tail
}

# The exit code is the design's cleanliness — but only when the flow
# actually routed: a deliberately partial flow (stopped before run_nuts)
# has nothing to verdict, and -1 ("never computed") must not read as dirty.
if {!$routed} {
    buda::stop
    puts "$tag: flow did not route (no run_nuts) -- no verdict"
    exit 0
}
set ov [buda::query overlaps]
set un [buda::query unplaced]
set vi [buda::query violations]
buda::stop
# The vehicles own their flows and demand all three legs; an arbitrary flow
# may deliberately stop before detailed NUTS or never audit, and -1 ("never
# computed") must not read as dirty HERE — but it must not read as clean
# either, so the never-computed legs are named.
set dirty 0
set legs {}
foreach {v what} [list $ov "overlaps" $un "unplaced" $vi "audit violations"] {
    if {$v > 0} { set dirty 1 }
    lappend legs [expr {$v < 0 ? "$what never computed" : "$v $what"}]
}
if {$dirty} {
    puts stderr "$tag: FAILED -- [join $legs {, }]"
    exit 1
}
puts "$tag: done -- [join $legs {, }]"
