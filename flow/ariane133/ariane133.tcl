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
# flow/ariane133/ariane133.tcl — fetch the inputs, then route them.
#
#   bin/btcl flow/ariane133/ariane133.tcl              # fetch + the healer flow
#   bin/btcl flow/ariane133/ariane133.tcl base         # fetch + the plain flow
#   bin/btcl -v flow/ariane133/ariane133.tcl           # …and a viewer at the end
#   bin/btcl flow/ariane133/ariane133.tcl heal -nofetch   # offline: verify only
#   bin/btcl flow/ariane133/ariane133.tcl -strict         # also fail on a dirty audit
#
# WHY THIS EXISTS.  This vehicle has a prerequisite no other flow in the repo
# has: two of its three inputs are somebody else's files, fetched from
# upstream and deliberately not checked in (12 MB of netlist under a
# different licence — see ReadMe.md).  So the documented recipe is two
# commands, and a fresh clone that runs only the second one gets a flow that
# stops partway through.  Nothing about that is the flow's fault, and nothing
# in a `.buda` script can fix it either: a `.buda` is a flat command list, it
# has no way to run a fetcher.
#
# Tcl is the front end where that is expressible — it is a real language, so
# the prerequisite and the run can be ONE artifact.  This script is
# deliberately thin: it fetches (or verifies), hands the SAME `.buda` file the
# CLI would have run to the engine, and then asks the finished session for its
# numbers.  The routing recipe is not duplicated here — one design, one
# description of it, and this is a driver rather than a second copy.
#
# The verdict is the other half of what Tcl buys.  `bin/buda` reports the
# design's outcome in prose on the terminal; `buda::query` RETURNS it, so
# this script ends by branching on the real numbers and exits non-zero on a
# dirty endpoint.  That makes the pair — fetch and route — gateable by a
# harness in one command.
# ============================================================

# Everything this script PRINTS is ASCII, as in the repo's other Tcl flows.
# `encoding system` is iso8859-1 on a host with no locale set, so a non-ASCII
# literal in the source is read as latin-1 and then written to the utf-8
# stdout `buda::start` installs — arriving double-encoded.  The engine's own
# output is unaffected (it crosses the pipe as characters); this is only
# about literals living in a `.tcl` file.  Comments are safe: nothing prints
# them.
set here [file dirname [file normalize [info script]]]
set repo [file dirname [file dirname $here]]
source [file join $repo tools buda.tcl]

# ── arguments ─────────────────────────────────────────────────────────────
# `heal` (default) or `base`, plus the offline switch.  A flow may also be
# named by path, so a variant sitting beside these two needs no edit here.
set flow   heal
set fetch  1
set strict 0
foreach a $argv {
    switch -glob -- $a {
        heal    { set flow heal }
        base    { set flow base }
        -nofetch -
        -no-fetch { set fetch 0 }
        -strict { set strict 1 }
        -*      { puts stderr "ariane133.tcl: unknown option '$a'"; exit 2 }
        default { set flow $a }
    }
}
switch -- $flow {
    heal    { set script [file join $here ariane133_heal.buda] }
    base    { set script [file join $here ariane133.buda] }
    default { set script [file normalize $flow] }
}
if {![file exists $script]} {
    puts stderr "ariane133.tcl: no such flow: $script"
    exit 2
}

# ── step 1: the inputs ────────────────────────────────────────────────────
# `fetch.py` is idempotent and checksum-pinned: a file already present and
# correct is reported `ok` and NOT re-downloaded, so running this every time
# costs nothing offline once the inputs are there.  `--check` verifies and
# fetches nothing, which is what -nofetch wants — and it still FAILS on a
# missing file, because a flow that cannot read its inputs should not start.
set fetcher [file join $here fetch.py]
set mode [expr {$fetch ? "fetch" : "verify"}]
puts "== inputs ($mode) =============================================="
set cmd [list python3 $fetcher]
if {!$fetch} { lappend cmd --check }
# >@stdout keeps fetch.py's own progress on the terminal as it happens (a
# 12 MB download is worth watching); the non-zero exit is what we branch on.
if {[catch {exec {*}$cmd >@stdout 2>@stderr} err opts]} {
    if {[dict get $opts -errorcode] eq "NONE"} {
        # No exit status — the interpreter itself could not be run.
        puts stderr "\nariane133.tcl: could not run $fetcher: $err"
    } else {
        puts stderr "\nariane133.tcl: inputs are not in the expected state."
        if {!$fetch} {
            puts stderr "  Drop -nofetch to download them, or see ReadMe.md."
        }
    }
    exit 1
}

# ── step 2: the flow ──────────────────────────────────────────────────────
# The engine's own `source`, given an ABSOLUTE path: the bridge runs no
# script of its own, so a relative path there would resolve against the
# process CWD rather than this directory.  Inside the `.buda`, paths resolve
# against ITS directory as always, so the flow is unchanged by being driven
# from here.
puts "\n== flow ([file tail $script]) =================================="
buda::start
set t0 [clock milliseconds]
# A failing flow RAISES here — that is the bridge's contract, and it is the
# failure signal this script branches on.  A fail-fast command (the
# `require_file` at the top of both flows) comes back as the engine stopping
# with a non-zero code, and a command that merely PRINTS `Error:` raises too.
# Caught so the outcome is a sentence rather than a Tcl stack trace; the
# engine's own diagnostic has already been echoed above.
if {[catch {buda::source $script} err]} {
    puts stderr "\nariane133.tcl: the flow FAILED and did not finish."
    puts stderr "  [lindex [split $err \n] 0]"
    catch {buda::stop}
    exit 1
}
set secs [expr {([clock milliseconds] - $t0) / 1000.0}]

# ── step 3: the verdict ───────────────────────────────────────────────────
# What `bin/buda` prints, this can branch on.
#
# -1 is "never computed", which is NOT 0 and is NOT a fault either: these two
# flows deliberately differ in how far they go — `ariane133.buda` stops at
# abstract NUTS, so it HAS no unplaced-bit count, while the healer flow runs
# detailed NUTS and does.  Reading -1 as a failure would fail the base flow
# for doing exactly what it says it does; reading it as 0 would be the older
# and worse mistake of reporting a stage that never ran as clean.  It is
# reported as what it is, and the flow's own failure is the raise above.
set overlaps   [buda::query overlaps]
set unplaced   [buda::query unplaced]
set violations [buda::query violations]
set bundles    [buda::query bundles]

proc metric {n} { return [expr {$n < 0 ? "not run" : $n}] }

puts "\n== verdict ====================================================="
puts [format "  %-10s %s" bundles    [metric $bundles]]
puts [format "  %-10s %s" overlaps   [metric $overlaps]]
puts [format "  %-10s %s" unplaced   [metric $unplaced]]
puts [format "  %-10s %s" violations [metric $violations]]
puts [format "  %-10s %.1fs" runtime $secs]

# Overlaps and unplaced bits are the ROUTING verdict and gate the exit.
# `check_design` violations are the AUDIT leg, which the other two do not
# cover — a design can place every bit overlap-free and still be
# electrically wrong — so they are always REPORTED, and "clean endpoint" is
# never claimed over a dirty audit (Codex P1 on #743; `buda::query
# violations` exists because a flow gating on the other two called that
# clean, the same fault on #679).
#
# They do not gate the exit BY DEFAULT, and that is a deliberate split
# rather than an omission.  This vehicle's audit violations are structural
# and known: a floorplan DEF places no standard cells, so 16 pure-logic
# containers have no placed descendant and every bundle reaching one is
# counted (274 at the detailed stage, measured).  Gating on them would make
# this driver permanently red on a design behaving exactly as documented,
# which trains a reader to ignore the gate.  `bin/buda --strict-check` made
# the same call for the same reason — report by default, exit non-zero only
# when asked — and `-strict` here is that switch.  See ReadMe.md, "What is
# not clean, and why that is the input's shape".
set status 0
set ran 0
foreach {what n} [list overlaps $overlaps unplaced $unplaced] {
    if {$n > 0} {
        puts "  DIRTY: $n $what."
        set status 1
    } elseif {$n == 0} {
        incr ran
    }
}
if {$violations > 0} {
    puts "  AUDIT: $violations check_design violation(s)\
           [expr {$strict ? {-- -strict, so this fails the run}
                          : {-- reported, not gated (see ReadMe.md); -strict gates on it}}]."
    if {$strict} { set status 1 }
} elseif {$violations < 0} {
    puts "  AUDIT: no check_design was run, so nothing was demonstrated."
    if {$strict} { set status 1 }
}
if {$status == 0} {
    set routing [expr {$ran == 2 ? "clean endpoint"
                                 : "clean as far as this flow goes\
                                    (a stage it does not run has no count)"}]
    puts [expr {$violations > 0 ? "  routing $routing; the audit is not clean."
                                : "  $routing."}]
}

# `buda::stop` last: with `btcl -v` it carries the final viewer, so the
# verdict above is already on the terminal when the window opens.
buda::stop
exit $status
