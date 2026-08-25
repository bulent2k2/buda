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

# ============================================================================
# tools/buda_prompt.tcl — the design-iteration prompt, once.
#
# `design.tcl` (flat) and `hdesign.tcl` (hierarchical) had a character-for-
# character identical prompt loop, differing only in the tag they print and in
# which `route` they call.  Two copies of a loop whose comments carry the
# reasoning (why `done` sits outside the catch, why `pins_dirty` is marked
# BEFORE the send, why an unknown verb is refused locally) is two places for
# that reasoning to drift — and the copies had already drifted in their prose.
#
#     set pins_dirty [prompt::run design.tcl design> route $bdb "topos d1"]
#
# Returns `pins_dirty`: 1 if any pin changed since the last route, so the
# CALLER decides what a dirty checkpoint costs (both callers re-plan).
# ============================================================================

namespace eval prompt {
    # The loop's OWN verbs, in the order `help` prints them.  A list, not a
    # switch buried in the body, because `help` must be derived from the same
    # data the dispatch uses — a help text maintained by hand is a help text
    # that lies the first time a verb is added.
    variable verbs {
        {topos    "?sel?"        "list a bundle's candidates (1-based `topo` column)"}
        {explore  "?sel?"        "open the topology explorer (close it to return)"}
        {pin      "<sel> <N|type>" "pin candidate N (1-based) or a TYPE (Z_VHV, TRUNK_H+MST@y1268)"}
        {unpin    "<sel>"        "drop a pin, letting the planner re-choose"}
        {replan   ""             "re-run the planner and route with the current pins"}
        {show     ""             "open the main viewer on the routed design"}
        {save     ""             "write the .sql snapshot now (re-plans first if pins changed)"}
        {commands "?glob?"       "list the ENGINE's commands (optionally filtered)"}
        {help     ""             "this list"}
        {done     ""             "re-plan if pins changed, save, and exit (also: exit, quit)"}
    }
}

# `<sel>` is whatever `select_topology` takes — a bundle ID, a net-name
# prefix, or an explicit `id:`/`net:` form.  Spelled out once, here, because
# the number `check_design` prints ("Bundle 8: ...") is a bundle ID and being
# able to type it straight back is the whole point.
proc prompt::_help {tag} {
    variable verbs
    puts "$tag verbs:"
    foreach v $verbs {
        lassign $v name args desc
        puts [format "  %-9s %-13s %s" $name $args $desc]
    }
    puts "\n  <sel> is a bundle ID (as `check_design` prints it), a net-name\
          prefix, or an explicit `id:N` / `net:PFX`."
    puts "  Anything else is passed to the ENGINE verbatim if its registry\
          knows it — `commands` lists those."
    puts "  The bridge's own procs are Tcl: `info commands ::buda::*`."
}

# The engine's registry, which the bridge asked of the running engine at
# start — so this cannot drift from what the engine will actually accept.
proc prompt::_commands {pat} {
    set names [lsort [buda::commands]]
    if {$pat ne ""} { set names [lsearch -all -inline -glob $names $pat] }
    if {$names eq ""} {
        puts "no engine command matches '$pat'"
        return
    }
    puts "[llength $names] engine command(s):"
    set line "  "
    foreach n $names {
        if {[string length $line] + [string length $n] > 76} {
            puts $line
            set line "  "
        }
        append line $n " "
    }
    if {[string trim $line] ne ""} { puts $line }
}

# tag       — the script's name, for its messages (design.tcl / hdesign.tcl)
# ps        — the prompt string (design> / hdesign>)
# route     — name of the caller's re-plan proc; `replan` and `done` call it
# bdb       — checkpoint path; `save` writes ${bdb}.sql
# example   — a bus name from THIS design, for the intro line
# guard     — OPTIONAL: a command called with the verb about to run — the
#             pin/unpin verbs' underlying commands, and EVERY raw engine
#             command (the guard decides which verbs mutate; this loop does
#             not pretend to know).  Returning 1 blocks it, and the guard
#             says why.  This is how a post-expansion INSPECTION session
#             (btcl -i hier nuts/dnuts) keeps a pin's persist — or a raw
#             `run_planner hier`, or a re-bundle — from writing the
#             expanded view over the checkpoint's template rows.  Empty
#             (the default, and the vehicles) = every verb allowed,
#             behavior unchanged.
proc prompt::run {tag ps route bdb example {guard ""}} {
    # `pins_dirty` keeps the checkpoint COHERENT — a pin applied after the
    # route would leave the saved metal belonging to the candidate it
    # replaced, so the caller re-plans when this comes back 1.
    set pins_dirty 0
    puts "\n$tag: pin/edit prompt -- `topos $example` to look, `pin $example\
          <N>` to choose (1-based), `replan` to apply, `done` to save and\
          exit.  `help` lists the verbs; anything else goes to the engine."
    while {1} {
        puts -nonewline "$ps "
        flush stdout
        if {[gets stdin line] < 0} { break }          ;# EOF ends the session
        set line [string trim $line]
        if {$line eq ""} { continue }
        # The list ops below (`lindex`, `llength`, `lrange`) all RAISE on a
        # line that is not a well-formed Tcl list — an unmatched brace or
        # quote — and this first one runs before the dispatch catch, so it
        # must have its own: a stray brace at an interactive prompt must cost
        # a message, not the whole session (and its snapshot / verdict).
        # Once it parses, the rest of the list ops are safe.  (Comments inside
        # a braced script still count braces, so this comment spells the
        # characters out rather than show them.)
        if {[catch {lindex $line 0} verb]} {
            puts "$tag: unparseable input ($verb) -- balance braces/quotes"
            continue
        }
        # `done` is checked OUTSIDE the catch: `break` raises TCL_BREAK, which
        # a catch around the switch would swallow instead of ending the loop.
        if {$verb in {done exit quit}} { break }
        if {[catch {
            switch -- $verb {
                pin {
                    if {[llength $line] != 3} {
                        puts "usage: pin <bus> <topo-N>  (1-based, from\
                              `topos` or the explorer title bar)"
                    } elseif {$guard ne ""
                              && [uplevel #0 $guard select_topology]} {
                        # blocked — the guard said why
                    } else {
                        buda::select_topology [lindex $line 1] [lindex $line 2]
                        set pins_dirty 1
                    }
                }
                unpin {
                    if {$guard ne ""
                            && [uplevel #0 $guard unpin_topology]} {
                        # blocked — the guard said why
                    } else {
                        buda::unpin_topology [lindex $line 1]
                        set pins_dirty 1
                    }
                }
                topos   { buda::dump_topologies {*}[lrange $line 1 end] }
                explore { buda::visualize_topologies {*}[lrange $line 1 end] }
                show    { buda::visualize }
                replan  { $route ; set pins_dirty 0 }
                save    {
                    # The same coherence rule `done` enforces, at the same
                    # moment it matters: a snapshot taken between a pin and
                    # its re-plan would hold the new selection over the OLD
                    # candidate's metal — exactly the incoherent checkpoint
                    # the exit path exists to prevent.  Re-plan first; a
                    # route that RAISES skips both the clear and the save
                    # (the catch below prints it), so a failed re-plan can
                    # never publish the stale snapshot either.
                    if {$pins_dirty} {
                        puts "$tag: pins changed since the last route --\
                              re-planning so the snapshot stays coherent"
                        $route
                        set pins_dirty 0
                    }
                    buda::save_bdb ${bdb}.sql
                }
                help    { prompt::_help $tag }
                commands { prompt::_commands [lindex $line 1] }
                default {
                    # Raw pass-through — but only for a command the ENGINE's
                    # own registry knows: an unknown command is the CLI's
                    # fail-fast (it kills the run, by design, so a script typo
                    # cannot be silently skipped), and at an interactive
                    # prompt that would cost the whole session.
                    # `buda::commands` is that registry, asked of the running
                    # engine at start, so it cannot drift.
                    if {$verb in [buda::commands]} {
                        # The guard sees EVERY raw engine command and
                        # decides — a blocked `pin` retyped as
                        # `select_topology` must not become the way around
                        # it, and neither must `run_planner hier` or a
                        # re-bundle/regenerate, which persist the same
                        # tables the named verbs are guarded for; which
                        # verbs mutate is the CALLER's knowledge, not this
                        # loop's.  (Plain if/else, not `continue`: inside
                        # this catch a `continue` would be swallowed as an
                        # exceptional return, the same trap the `done`
                        # handling documents above.)
                        if {$guard ne "" && [uplevel #0 $guard $verb]} {
                            # blocked — the guard said why
                        } else {
                        # Escape-hatch parity with the pin/unpin verbs: these
                        # engine commands change the durable pin too, so the
                        # caller must re-plan for the checkpoint to stay
                        # coherent — typed raw, they persisted the new
                        # selection while the saved metal still belonged to
                        # the old candidate.  Marked BEFORE the send: a
                        # `select_topologies` list can pin some bundles and
                        # then raise on another, and a raise after the send
                        # would skip the mark — the cost of the conservative
                        # order is one redundant re-plan on a pin that failed
                        # outright, never an incoherent checkpoint.
                        if {$verb in {select_topology select_topologies
                                      unpin_topology}
                            || ($verb eq "edit_commit"
                                && "pin" in [lrange $line 1 end])} {
                            set pins_dirty 1
                        }
                        # NOT `puts [buda::do ...]`: the bridge ECHOES a
                        # command's output as it arrives (`-echo`, on by
                        # default), so printing the return value too showed
                        # every pass-through command's output TWICE — while
                        # the named verbs above, which do not print it,
                        # showed it once.  One loop, two behaviours, and the
                        # duplicate looked like the engine had run the
                        # command twice.  A driver that turns the echo off
                        # reads the output with `buda::output`.
                        buda::do $line
                        }
                    } else {
                        puts "$tag: unknown command '$verb' -- try `help`, or\
                              any engine command (`commands` lists them)"
                    }
                }
            }
        } err]} {
            puts "$tag: $err"
        }
    }
    return $pins_dirty
}
