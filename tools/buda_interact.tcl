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
#   bin/btcl -i <flow>.buda ?<ckpt.bdb>?
#   (or: tclsh tools/buda_interact.tcl <flow>.buda ?<ckpt.bdb>?)
#
# design.tcl and hdesign.tcl carry their own design and their own route
# recipe; this driver carries NEITHER — it runs an arbitrary flow verbatim
# (`buda::source`, the engine's own command, so the flow means exactly what
# `bin/buda` makes it mean) and then drops into the same pin/edit prompt
# (tools/buda_prompt.tcl — the shared loop the vehicles use).
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
# A flow with no file-backed BDB still iterates IN-session (pin/replan),
# but its pins die with the session: `save_bdb` snapshots the OPEN BDB, so
# only a flow that opened one has a snapshot to write (opening one after
# the fact would not backfill the rows the pipeline persists as it runs).
# The optional SECOND argument closes exactly that gap the honest way
# round: `btcl -i <flow>.buda ckpt.bdb` opens the BDB BEFORE the flow, so
# every stage persists as it runs and a prompt pin lands durably — the
# next session with the same pair restores it onto the rebuilt candidate
# pool by content uid (_apply_bdb_pins) and the flow's own `run_planner`
# honors it.  A flow that ends in `exit` has ended the engine session —
# reported, nothing to iterate.
# ============================================================

set here [file dirname [file normalize [info script]]]
source [file join $here buda.tcl]
source [file join $here buda_prompt.tcl]

if {[llength $argv] < 1 || [llength $argv] > 2} {
    puts stderr "usage: btcl -i <flow>.buda ?<ckpt.bdb>?   (the optional BDB\
                 is opened BEFORE the flow, so pins persist across sessions)"
    exit 2
}
set flow [file normalize [lindex $argv 0]]
if {![file exists $flow]} {
    puts stderr "buda_interact: no such flow: $flow"
    exit 2
}
set tag [file tail $flow]
set armed ""
if {[llength $argv] == 2} { set armed [file normalize [lindex $argv 1]] }

# ── arm the recorder, run the flow verbatim ───────────────────────────────
close [file tempfile recpath buda_record]
set ::env(BUDA_RECORD) $recpath
set ::env(BUDA_RECORD_NOTE) "btcl -i $tag"

buda::start
if {$armed ne ""} {
    # Arm a file-backed BDB BEFORE the flow runs, so the whole pipeline
    # persists as it goes — the flow/tcl/design.tcl pattern, without editing
    # the flow.  This is what gives a flow that never opens a BDB durable
    # pins: a `pin` at the prompt writes through, and the next
    # `btcl -i <flow> <same.bdb>` restores it onto the rebuilt pool
    # (_apply_bdb_pins, uid-keyed).  It cannot be done at SAVE time instead:
    # the pipeline persists its rows as it runs, so a BDB opened after the
    # fact holds none of them.  The open is recorded like everything else,
    # so the checkpoint detection below finds it with no special case.
    # A failure here is most often ANOTHER session holding the same BDB —
    # SQLite allows one writer, which is what keeps two concurrent sessions
    # from corrupting the file: the loser must fail LOUDLY at the door, in
    # this driver's voice, not as a raw Tcl stack trace.
    if {[catch {buda::open_bdb $armed} err]} {
        puts stderr "$tag: cannot open the armed BDB $armed: $err"
        puts stderr "$tag: (another session holding it?  one writer at a\
              time -- finish or kill it, or arm a different path)"
        catch {buda::stop}
        exit 1
    }
}
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
set ckpt_live 0
set first_bus ""
set tail {}
set planning 0
foreach ln $lines {
    # The engine lowercases the COMMAND NAME (do_command's parts[0].lower())
    # but the recorder writes the line verbatim, so a flow spelling
    # `RUN_PLANNER` ran fine and must be recognized here too.  Only the verb:
    # arguments are case-sensitive to the engine (`run_planner HIER` would
    # have been refused), and an `open_bdb` PATH must never be case-folded.
    set verb [string tolower [lindex [split $ln] 0]]
    if {$verb eq "run_planner" && [lindex [split $ln] 1] eq "hier"} {
        set is_hier 1
    }
    if {$verb eq "run_nuts"} { set routed 1 }
    if {$verb eq "open_bdb"} {
        set p [lindex [split $ln] 1]
        # `ckpt_live` distinguishes "a file-backed BDB was SEEN" from "the
        # file-backed BDB is the one still OPEN": a flow that opens
        # `:memory:` after it (an armed BDB followed by the flow's own
        # open) replaced it, and pins made at the prompt then go to memory
        # — a checkpoint claim on the dead file would be a lie.
        if {$p ne ":memory:"} {
            set ckpt $p
            set ckpt_live 1
        } else {
            set ckpt_live 0
        }
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
if {$ckpt ne "" && !$ckpt_live} {
    # A file-backed BDB was seen but the flow reopened `:memory:` after it
    # (an armed BDB followed by the flow's own open is the usual shape):
    # the file holds what ran BEFORE that open, and pins made at this
    # prompt go to the memory BDB — durable only via an explicit
    # `save <path>` snapshot of the live session.
    puts "$tag: NOTE -- the flow reopened :memory: after $ckpt; prompt pins\
          are NOT durable there (`save` snapshots the live session)"
    set snap_base [file rootname $tag].bdb
} elseif {$ckpt ne ""} {
    puts "$tag: checkpoint $ckpt -- pins persist; rerun the flow (or resume\
          with open_bdb + load_pipeline) and they hold"
    if {$armed ne "" && $ckpt ne $armed} {
        # The flow opened its OWN BDB after the armed one, so the armed file
        # holds only what ran before that open — the flow's checkpoint is
        # the real one, and pretending otherwise would promise persistence
        # in a file the routing never reached.
        puts "$tag: NOTE -- the flow opened its own BDB after the armed\
              $armed; the flow's checkpoint above is the live one"
    }
    set snap_base $ckpt
} else {
    # `save_bdb <path>` snapshots the OPEN BDB (it refuses with `open_bdb
    # first` otherwise), and opening one now would not backfill the rows the
    # pipeline persists as it runs — so a flow that never opened a BDB has
    # no snapshot to offer, and promising one here handed the user a verb
    # that could only fail.  Say what is true instead.
    puts "$tag: no file-backed BDB -- pins and edits die with this session\
          (a snapshot needs a BDB the flow itself opened; see\
          flow/tcl/design.tcl for a checkpointing flow)"
    set snap_base [file rootname $tag].bdb
}

# A replay that stops partway is a FAILURE, not a finished route: the session
# then holds a mix of old and new state, and a verdict read off it would be
# stale.  So the error is RE-RAISED (the prompt's catch prints it and keeps
# `pins_dirty` set, since its clear follows the route call), and the sticky
# flag below keeps the exit code honest even when the user routes on and
# quits with clean pins — only a replay that runs to the end clears it.
set replay_failed 0
proc replay_tail {} {
    if {![llength $::tail]} {
        puts "[set ::tag]: the flow never planned -- no replan recipe to replay"
        return
    }
    set ::replay_failed 1
    foreach ln $::tail {
        puts "replay> $ln"
        if {[catch {buda::do $ln} err]} {
            error "replay stopped at `$ln`: $err"
        }
    }
    set ::replay_failed 0
}

set pins_dirty [prompt::run $tag "[file rootname $tag]>" replay_tail \
                    $snap_base $first_bus]
if {$pins_dirty} {
    puts "$tag: pins changed since the last route -- re-planning so the\
          checkpoint stays coherent"
    if {[catch {replay_tail} err]} {
        puts stderr "$tag: $err"
    }
}
if {$replay_failed} {
    catch {buda::stop}
    puts stderr "$tag: FAILED -- the last replan stopped partway, so the\
          session's state is not the flow's routed result"
    exit 1
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
