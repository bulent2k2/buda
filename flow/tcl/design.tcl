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
# flow/tcl/design.tcl — ONE file, run again and again: the iteration loop.
#
#   bin/btcl -v flow/tcl/design.tcl        # session 1: build, route, iterate
#   bin/btcl -v flow/tcl/design.tcl        # session 2..N: resume, iterate more
#
#   BUDA_DESIGN_BDB=<path>    the working checkpoint (default flow/tcl/out/design.bdb)
#   BUDA_DESIGN_FRESH=1       discard the checkpoint and rebuild from scratch
#
# The array_save/array_resume pair proved a decision SURVIVES a process exit;
# this vehicle folds that pair into a single script a person can just keep
# running.  The file itself decides which session it is: no checkpoint means
# BUILD (declare the design, bundle, generate candidates), an existing one
# means RESUME (`load_pipeline` brings back the bundles, every candidate, the
# selection, the pins, and the routing — the flow starts at the PLANNER,
# because an iteration re-plans rather than rebuilds).
#
# After the route, the script drops into a small PROMPT:
#
#   topos ?hint?      list a bundle's candidates (1-based `topo` column)
#   explore ?hint?    open the topology explorer on a bundle (close it to return)
#   show              open the full design viewer (close it to return)
#   pin <bus> <n>     pin a candidate — 1-based, as the explorer title shows it
#   unpin <bus>|*     clear a pin
#   replan            re-run planner -> NUTS -> detailed, honoring the pins
#   save              write the diffable .sql snapshot now
#   done              finish the session (auto-replans first if pins changed)
#   <anything else>   sent to the engine verbatim (edit_topology ..., etc.)
#
# `pin` is the DURABLE form of a choice: `select_topology` writes
# `topology.is_pinned` into the checkpoint at once, so it binds THIS session's
# next `replan` and every future session's planner — even if the session is
# interrupted before the save at the bottom.  A candidate picked inside the
# explorer window is a PREVIEW by design; bring back the title bar's `topo
# N/n` and type `pin <bus> <N>` here.  (Candidate ids are 1-based everywhere
# they are shown or typed.)
#
# The prompt reads stdin, so the same file is scriptable end-to-end:
#
#   echo {pin d1 3
#   done} | bin/btcl flow/tcl/design.tcl
#
# and EOF simply finishes the session — which is what the tests do.
# ============================================================

set repo [file dirname [file dirname [file dirname [file normalize [info script]]]]]
source [file join $repo tools buda.tcl]

# ── the checkpoint ────────────────────────────────────────────────────────
set bdb [expr {[info exists ::env(BUDA_DESIGN_BDB)] && $::env(BUDA_DESIGN_BDB) ne ""
               ? $::env(BUDA_DESIGN_BDB)
               : [file join $repo flow tcl out design.bdb]}]
set fresh [expr {[info exists ::env(BUDA_DESIGN_FRESH)]
                 && $::env(BUDA_DESIGN_FRESH) ne "0"}]
if {$fresh && [file exists $bdb]} {
    puts "design.tcl: BUDA_DESIGN_FRESH=1 -- removing $bdb and rebuilding"
    file delete $bdb ${bdb}-wal ${bdb}-shm
}
set resuming [file exists $bdb]
file mkdir [file dirname $bdb]

buda::start

# ── the technology (every session, identical by construction) ─────────────
# `load_pipeline` needs the stack and the floorplan present to rebuild
# against, so the setup below runs on BUILD and RESUME alike — being ONE
# file is what guarantees two sessions cannot disagree about the design.
foreach {id nm dir kind oh} {
    2 M2 H {}  55.56   3 M3 V {}  55.56   4 M4 H {}  55.56
    5 M5 V TOP 50      6 M6 H TOP 52.94   7 M7 V TOP 56.10
} {
    buda::def_layer $id $nm $dir {*}$kind $oh
}
buda::def_track_pattern 2 -100 POWER 2 1 (SIGNAL 1 0.5)x4 GROUND 2 1 (SIGNAL 1 0.5)x4
buda::def_track_pattern 3    0 POWER 2 1 (SIGNAL 1 0.5)x4 GROUND 2 1 (SIGNAL 1 0.5)x4
buda::def_track_pattern 4 -200 POWER 2 1 (SIGNAL 1 0.5)x4 GROUND 2 1 (SIGNAL 1 0.5)x4
buda::def_track_pattern 5    0 POWER 3 1 (SIGNAL 2 1)x4 GROUND 3 1 (SIGNAL 2 1)x4
buda::def_track_pattern 6 -400 POWER 4 1 (SIGNAL 2 1)x4 GROUND 4 1 (SIGNAL 2 1)x4
buda::def_track_pattern 7 -600 POWER 6 2 (SIGNAL 3 2)x3 GROUND 2 1 (SIGNAL 3 2)x3
buda::corner_margin dx 5 dy 5
buda::set_min_stub_length 2
buda::set_planner_param healersAhead 1    ;# this flow heals on demand

buda::open_bdb $bdb

# ── the design (flat flow: blocks + buses, declared every session) ────────
# Setup, not pipeline: blocks and nets are session state the checkpoint's
# geometry is interpreted against, so a resume re-declares them; what a
# resume must NOT re-run is the bundler and the generator — those rows are
# exactly what the checkpoint holds.
buda::add_block cpu   50  50 250 250
buda::add_block mem0 550  50 750 250
buda::add_block mem1 550 350 750 550
buda::add_block dsp   50 350 250 550
buda::add_block io   330  20 470 120
buda::add_block noc  330 480 470 580

buda::add_bus "d0\[8\]"     cpu.d0 mem0.d0     ;# straight run, bottom
buda::add_bus "d1\[8\]"     cpu.d1 mem1.d1     ;# the diagonal: many candidates
buda::add_bus "w\[4\]"      dsp.w  mem1.w      ;# straight run, top
buda::add_bus "io_in\[4\]"  io.i   cpu.i
buda::add_bus "io_out\[4\]" cpu.o  io.o
buda::add_bus "n0\[4\]"     noc.a  dsp.a
buda::add_bus "n1\[4\]"     noc.b  mem1.b
buda::add_net irq io.irq dsp.irq

# ── build or resume ───────────────────────────────────────────────────────
if {$resuming} {
    buda::load_pipeline
    set nb [buda::query bundles]
    if {$nb == 0} {
        error "design.tcl: $bdb restored no bundles -- not this flow's\
               checkpoint?  BUDA_DESIGN_FRESH=1 rebuilds it"
    }
    puts "design.tcl: RESUMED $nb bundles from $bdb -- starting at the planner"
} else {
    buda::run_bundler STRICT
    buda::generate_topologies
    puts "design.tcl: BUILT [buda::query bundles] bundles -> $bdb"
}

# ── route (and re-route: `replan` at the prompt lands here too) ───────────
proc route {} {
    buda::run_planner 5
    buda::run_nuts
    buda::check_design nuts
    buda::run_detailed_nuts
    buda::check_design dnuts
    if {[buda::query overlaps] > 0 || [buda::query unplaced] > 0
        || [buda::query violations] != 0} {
        puts "design.tcl: dirty ([buda::query overlaps] overlaps,\
              [buda::query unplaced] unplaced,\
              [buda::query violations] audit violations) -- healing"
        buda::negotiate_congestion 10
        buda::ripup_reroute 20
        buda::check_design dnuts
    }
    buda::report_wirelength
}
route

# ── the prompt ────────────────────────────────────────────────────────────
# Every engine error is caught and printed: a typo must not cost the session.
# `pins_dirty` keeps the checkpoint COHERENT — a pin applied after the route
# would leave the saved metal belonging to the candidate it replaced, so
# `done` re-plans first when any pin changed since the last route.
set pins_dirty 0
puts "\ndesign.tcl: pin/edit prompt -- `topos d1` to look, `pin d1 <N>` to\
      choose (1-based), `replan` to apply, `done` to save and exit.\
      Anything else goes to the engine verbatim."
while {1} {
    puts -nonewline "design> "
    flush stdout
    if {[gets stdin line] < 0} { break }              ;# EOF ends the session
    set line [string trim $line]
    if {$line eq ""} { continue }
    # The list ops below (`lindex`, `llength`, `lrange`) all RAISE on a line
    # that is not a well-formed Tcl list — an unmatched brace or quote — and
    # this first one runs before the dispatch catch, so it must have its own:
    # a stray brace at an interactive prompt must cost a message, not the
    # whole session (and its snapshot / verdict).  Once it parses, the rest
    # of the list ops are safe.  (Comments inside a braced script still count
    # braces, so this comment spells the characters out rather than show them.)
    if {[catch {lindex $line 0} verb]} {
        puts "design.tcl: unparseable input ($verb) -- balance braces/quotes"
        continue
    }
    # `done` is checked OUTSIDE the catch: `break` raises TCL_BREAK, which a
    # catch around the switch would swallow instead of ending the loop.
    if {$verb in {done exit quit}} { break }
    if {[catch {
        switch -- $verb {
            pin {
                if {[llength $line] != 3} {
                    puts "usage: pin <bus> <topo-N>  (1-based, from `topos` or\
                          the explorer title bar)"
                } else {
                    buda::select_topology [lindex $line 1] [lindex $line 2]
                    set pins_dirty 1
                }
            }
            unpin {
                buda::unpin_topology [lindex $line 1]
                set pins_dirty 1
            }
            topos   { buda::dump_topologies {*}[lrange $line 1 end] }
            explore { buda::visualize_topologies {*}[lrange $line 1 end] }
            show    { buda::visualize }
            replan  { route; set pins_dirty 0 }
            save    { buda::save_bdb ${bdb}.sql }
            default {
                # Raw pass-through — but only for a command the ENGINE's own
                # registry knows: an unknown command is the CLI's fail-fast
                # (it kills the run, by design, so a script typo cannot be
                # silently skipped), and at an interactive prompt that would
                # cost the whole session.  `buda::commands` is that registry,
                # asked of the running engine at start, so it cannot drift.
                if {$verb in [buda::commands]} {
                    # Escape-hatch parity with the pin/unpin verbs: these
                    # engine commands change the durable pin too, so `done`
                    # must re-plan for the checkpoint to stay coherent —
                    # typed raw, they persisted the new selection while the
                    # saved metal still belonged to the old candidate.
                    # Marked BEFORE the send: a `select_topologies` list can
                    # pin some bundles and then raise on another, and a raise
                    # after the send would skip the mark — the cost of the
                    # conservative order is one redundant re-plan on a pin
                    # that failed outright, never an incoherent checkpoint.
                    if {$verb in {select_topology select_topologies
                                  unpin_topology}
                        || ($verb eq "edit_commit"
                            && "pin" in [lrange $line 1 end])} {
                        set pins_dirty 1
                    }
                    puts [buda::do $line]
                } else {
                    puts "design.tcl: unknown command '$verb' -- try `topos`,\
                          `pin`, `replan`, `done`, or any engine command"
                }
            }
        }
    } err]} {
        puts "design.tcl: $err"
    }
}
if {$pins_dirty} {
    puts "design.tcl: pins changed since the last route -- re-planning so the\
          checkpoint stays coherent"
    route
}

# The working `.bdb` has been written in place all along; the snapshot is the
# DIFFABLE record of this iteration, so two sessions can be compared instead
# of just replaced.
buda::save_bdb ${bdb}.sql
puts "design.tcl: checkpoint $bdb (snapshot ${bdb}.sql) -- run me again to\
      keep iterating"

# The exit code IS the design's cleanliness (read before stop: queries need
# the engine).
set ov [buda::query overlaps]
set un [buda::query unplaced]
set vi [buda::query violations]
buda::stop
if {$ov != 0 || $un != 0 || $vi != 0} {
    puts stderr "design.tcl: FAILED -- $ov overlaps, $un unplaced, $vi audit violations"
    exit 1
}
puts "design.tcl: clean -- 0 overlaps, 0 unplaced, 0 audit violations"
