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
# flow/tcl/converge.tcl — the E1/E5 loop driver (convergence ladder item 5).
#
#   btcl flow/tcl/converge.tcl soc 2 4 8            # healerless, every arm
#   btcl flow/tcl/converge.tcl soc 16 -heal         # the vehicle's own healing
#   btcl flow/tcl/converge.tcl soc 4 -step 2        # the blind policy's step
#   btcl flow/tcl/converge.tcl tpu 8 16 -arms blind,bu
#   btcl flow/tcl/converge.tcl soc 2 -nofloor        # the pure complement
#   btcl flow/tcl/converge.tcl soc 2 4 -primitive reserve -arms uniform,td,bu
#                                                   # E5: the corridor
#   btcl flow/tcl/converge.tcl soc 2 4 -primitive reserve -arms td,bu -handdown
#                                                   # 6c: the top's plan kept
#   btcl flow/tcl/converge.tcl soc 2 -primitive reserve -arms td,bu -handdown -yield
#                                                   # 6d: the block keeps its seat
#
# Four ways a block gets its layer budget, each run as a LOOP on the same
# vehicle at the same size, until the endpoint is clean or the arm runs out
# of moves (docs/internal/convergence_ladder.md, E1 and E5):
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
#   uniform E5's conventional arm: the block reserves F feedthrough tracks
#           per TOP layer, evenly spaced, by GUESS (`-uniform F` — the
#           same positional primitive the derived corridor uses, so one
#           audit prices both), is frozen, the top routes against it.
#           Round k doubles F from `-f0` until it passes `-fmax` — the
#           policy's own parameter swept, the strawman defence.
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
#   -primitive P   the derived arms' budget: share (default) | reserve —
#                  the positional `set_cell_layer_reserve` (ladder item 6)
#   -maxreserve N  the blind policy's ceiling (default 4: a band must keep
#                  an H and a V layer, and the six-layer stack has M2/M3
#                  under M4..M7)
#   -informed R    informed rounds per derived arm (default 2; 0 = the
#                  measurement alone — td's top-down round, bu's blind one)
#   -arms a,b,c    subset of blind,td,bu,uniform (default blind,td,bu; `bu`
#                  without `blind` runs blind round 1 only, as its
#                  measurement; `uniform` is E5's conventional arm)
#   -f0 F          the uniform arm's first track count (default 4)
#   -fmax F        the uniform arm's ceiling (default 32; F doubles per
#                  round and the sweep stops when it would pass this)
#   -nofloor       derive the PURE complement (no own-need floor): the
#                  derivation's own strawman defence
#   -yield         the reserve derivation's POLICY where the union covers a
#                  block's own seat (ladder item 6d, `derive_cell_layer_
#                  reserves yield`): give back the shortfall so the block
#                  keeps its bus and the top takes the loss — E5's top-down
#                  NQ = 2 fixpoint stranded the cores under a reservation
#                  no plan hand-down could repair.  `-primitive reserve`
#                  only (a share has its own floor, on by default)
#   -handdown      hand the top's PLAN down with the budget (ladder item
#                  6c): the measurement round writes its globally planned
#                  bundles' selection, layers and seats (`derive_top_plan`),
#                  every informed round sources the previous round's plan
#                  (`pin_plan` lines, held until its run_planner hier) and
#                  writes its own, so the blocks are routed under the SAME
#                  top the budget came from.  Two columns say what it
#                  bought: `plan` (pins applied, seats honoured by NUTS) and
#                  `fixpoint` (this round's derived budget AND derived plan
#                  equal the ones it ran under — the loop's own convergence
#                  test, over all of the loop's state; an informed round
#                  that reaches it stops the arm).  The td
#                  arm's measurement round then runs on the ALIGNED
#                  floorplan (`-align`), since a seat is geometry
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
                 \[-primitive share|reserve\] \[-f0 F\] \[-fmax F\]\
                 \[-handdown\] \[-out DIR\] \[-tag T\] \[-j N\]"
    exit 2
}
set vehicle [lindex $argv 0]
if {$vehicle ni {soc tpu}} { error "converge.tcl: vehicle must be soc|tpu, got '$vehicle'" }
set sizes {}
set heal 0; set step 1; set maxreserve 4; set informed 2; set nofloor 0
set primitive share; set f_start 4; set fmax 32; set handdown 0; set yield 0
set judge 0
set arms {blind td bu}; set out e1_out; set tag ""; set threads ""
set i 1
while {$i < $argc} {
    set a [lindex $argv $i]
    if {[string is integer -strict $a]} { lappend sizes $a; incr i; continue }
    set v [lindex $argv [expr {$i + 1}]]
    # Every option below `-nofloor` takes a value: a trailing option, or an
    # empty word after it, must not read as "given" (an empty `-arms`
    # passed validation and wrote an empty table — Codex P2 on #935).
    # An option-LOOKING next word — anything dash-prefixed that is not a
    # number — is a missing value too, whether or not it is an option this
    # driver knows: `-tag -heall` used to take the typo as the tag and run
    # the whole experiment (Codex P2 on #935).  A numeric `-1` still reaches
    # its own check (`-maxreserve takes a non-negative integer`).
    set optlike [expr {[string match -* $v] && ![string is integer -strict $v]}]
    if {$a ni {-heal -nofloor -handdown -yield -judge} && ([string trim $v] eq "" || $optlike)} {
        error "converge.tcl: $a needs a value (got '$v')"
    }
    switch -- $a {
        -heal       { set heal 1; incr i }
        -nofloor    { set nofloor 1; incr i }
        -handdown   { set handdown 1; incr i }
        -yield      { set yield 1; incr i }
        -judge      { set judge 1; incr i }
        -step       { set step $v; incr i 2 }
        -primitive  { set primitive $v; incr i 2 }
        -f0         { set f_start $v; incr i 2 }
        -fmax       { set fmax $v; incr i 2 }
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
if {$primitive ni {share reserve}} {
    error "converge.tcl: -primitive takes share|reserve, got '$primitive'"
}
# `-nofloor` is the SHARE derivation's control (the pure complement, no
# own-need floor); the reserve derivation has no floor to drop and refuses
# the token — which used to surface only after the first informed round's
# routing session had been paid for (Codex P2 on #936).
if {$nofloor && $primitive eq "reserve"} {
    error "converge.tcl: -nofloor is the share derivation's control and\
 does not apply to -primitive reserve"
}
# `-yield` is the RESERVE derivation's policy (the share derivation floors
# by the block's own need by default and has no tracks to give back).
if {$yield && $primitive ne "reserve"} {
    error "converge.tcl: -yield is the reserve derivation's policy and\
 needs -primitive reserve"
}
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
# The uniform sweep doubles from f0 until it passes fmax: a non-positive
# start would never advance, and a ceiling below the start runs nothing.
if {![string is integer -strict $f_start] || $f_start < 1} {
    error "converge.tcl: -f0 takes a positive integer, got '$f_start'"
}
if {![string is integer -strict $fmax] || $fmax < $f_start} {
    error "converge.tcl: -fmax takes an integer >= -f0 ($f_start), got '$fmax'"
}
# `-j` travels to every session as BUDA_THREADS_REQUEST and the server
# IGNORES a request it cannot read (prints, keeps the default) — so an
# unreadable count would run the whole experiment at a concurrency the
# command line did not ask for (Codex P2 on #935).  btcl's semantics: `max`
# or an integer, READABLE here and CLAMPED by the engine to [1, max] with
# its own warning — `-j 0` and `-j -3` are btcl's to clamp, not this
# driver's to refuse (Codex P2 on #935, round 11).
# Readable to the ENGINE, which reads it with Python's int(): an optional
# sign and decimal digits — Tcl's own integer syntax also admits `0x4` and
# `0b10`, which the engine would ignore (Codex P2 on #935, round 12).
if {$threads ne "" && $threads ne "max" && ![regexp {^[+-]?\d+$} $threads]} {
    error "converge.tcl: -j takes an integer or max, got '$threads'"
}
set arms [lsearch -all -inline -not -exact $arms ""]
if {![llength $arms]} { error "converge.tcl: -arms names no arm (blind, td, bu)" }
foreach a $arms { if {$a ni {blind td bu uniform}} { error "converge.tcl: unknown arm '$a'" } }
file mkdir $out
set script [file join $repo flow tcl $vehicle.tcl]
if {$threads ne ""} { set ::env(BUDA_THREADS_REQUEST) $threads }

# ── one session ───────────────────────────────────────────────────────────
# Runs the vehicle with the given words, keeps its log, reads its report.
# A dirty verdict is exit 1 (the vehicle's own rule) and NOT a failure here:
# the report is the result.  No report = the session crashed: stop, and
# name the log.
proc session {name size args} {
    global script out heal primitive judge repo
    set log [file join $out $name.log]
    set rep [file join $out $name.rep]
    file delete -force $rep
    set words [list $size {*}$args -report $rep -primitive $primitive]
    if {!$heal} { lappend words -noheal }
    # `-judge`: give this round's `open_bdb :memory:` a durable home so the
    # independent audit has tables to read.  The vehicle's text does not
    # change — the redirect is the same one `btcl -b` arms — and the engine
    # REFUSES an existing target (a `:memory:` open builds a fresh
    # database), so a re-run of the same round must clear it first.
    set ckpt [file join $out $name.bdb]
    if {$judge} {
        file delete -force $ckpt
        set ::env(BUDA_BDB_MEMORY_TO) $ckpt
    }
    set t0 [clock milliseconds]
    catch {exec [info nameofexecutable] $script {*}$words > $log 2>@1}
    set secs [expr {([clock milliseconds] - $t0) / 1000.0}]
    if {$judge} { unset -nocomplain ::env(BUDA_BDB_MEMORY_TO) }
    if {![file exists $rep]} {
        # A session that died before its report is a FAILED round, never a
        # row: a rejected policy (`reserve_top_layers N` past the stack's
        # ceiling prints `Error:` and the bridge raises on it — Codex P2 on
        # #935) stops the run here, with the engine's own reason.
        set why ""
        catch {
            set lf [open $log]; set text [read $lf]; close $lf
            regexp -line {^Error: .*$} $text why
        }
        error "converge.tcl: session '$name' left no report\
               [expr {$why ne "" ? "($why)" : ""}] — see $log"
    }
    set r [converge::read_report $rep]
    dict set r name $name
    dict set r secs $secs
    dict set r words $words
    if {$judge} { dict set r judge [judge_verdict $ckpt $out $name] }
    return $r
}

# The independent verdict for one round: `tools/independent_audit.py` over
# the round's own checkpoint, run as a separate process with no BUDA on its
# path.  That separation is the point of the column — a table whose rows are
# scored by the router alone is the router marking its own work — so this
# reports what the judge said and never substitutes its own opinion:
# `clean`, a violation count, or `—` when the judge declined to judge (exit
# 2), which is not the same as clean and must not read as it.
proc judge_verdict {ckpt out name} {
    global repo
    if {![file exists $ckpt]} { return "—" }
    set tool [file join $repo tools independent_audit.py]
    set js [file join $out $name.judge.json]
    set rc 0
    # `python3`, not `[info nameofexecutable]` — this driver runs under
    # tclsh and that is what the vehicle sessions are launched with, but the
    # judge is a Python file; handing it to tclsh returned "could not judge"
    # for every clean round, which is the reading that must never be
    # produced by a mistake in the harness.  Same spelling the Tcl bridge
    # uses to start the engine (`tools/buda.tcl`).
    if {[catch {exec python3 $tool $ckpt --json $js --quiet} err opts]} {
        set rc [lindex [dict get $opts -errorcode] 2]
    }
    if {$rc == 0} { return "clean" }
    if {$rc != 1 || ![file exists $js]} { return "—" }
    set fh [open $js]; set text [read $fh]; close $fh
    if {[regexp {"total":\s*(\d+)} $text -> n]} { return $n }
    return "?"
}

proc clean {r} {
    lassign [dict get $r verdict] ov un vi
    return [expr {$ov == 0 && $un == 0 && $vi == 0}]
}

# An informed round's fixpoint verdict for the summary: yes/no, `—` for a
# measurement round (nothing to compare against).
proc fixpoint_of {r} {
    if {![dict exists $r fixpoint]} { return "—" }
    return [expr {[lindex [dict get $r fixpoint] 0] ? "yes" : "no"}]
}

# The informed rounds shared by td and bu: round r sources F_{r-1}, derives
# F_r for the next, scope pinned to F_0's cells.  Returns the rounds' reports.
# The derivation's extra tokens, the same at every derive site: the share
# control (`nofloor`) or the reserve policy (`yield`).
proc derive_opts {} {
    global nofloor yield
    set t {}
    if {$nofloor} { lappend t nofloor }
    if {$yield} { lappend t yield }
    return $t
}

proc informed_rounds {prefix size f0} {
    global informed nofloor handdown
    set cells [converge::scope_of $f0]
    set rounds {}
    set prev $f0
    if {[converge::scope_empty $cells]} {
        # Nothing was derived FOR: an informed round would source an empty
        # budget and re-derive under the vehicle's default scope — a
        # broader one than the measurement's.  Say so; the summary then
        # reads the measurement itself, as with -informed 0.
        puts "converge.tcl: ${prefix}: the derivation's scope is empty (no cell\
              to hand a budget to) — no informed round run"
        return $rounds
    }
    for {set r 1} {$r <= $informed} {incr r} {
        set fr [file join [file dirname $f0] ${prefix}_shares_r$r.buda]
        set words [list -bottomup -shares $prev -derive $fr]
        if {$cells ne ""} { lappend words -derive_cells $cells }
        if {[derive_opts] ne ""} { lappend words -derive_opts [derive_opts] }
        if {$handdown} {
            # The previous round's top plan comes down with its budget,
            # and this round leaves its own for the next.
            lappend words -plan [plan_file $prefix [expr {$r - 1}] $f0] \
                          -derive_plan [plan_file $prefix $r $f0]
        }
        set rep [session ${prefix}_r$r $size {*}$words]
        dict set rep policy $prev
        # The fixpoint test: the budget this round DERIVED against the one
        # it RAN UNDER — and, under -handdown, the PLAN it derived against
        # the one it ran under, since both are the loop's state (a healer
        # can move a topology, a layer or a seat while the budget
        # re-derives the same; a pin that fell back to its type spec can
        # leave a different plan — Codex P1 on #939).  Equal means the next
        # round would run the same session again — the loop has converged
        # there, clean or not.
        lassign [converge::policy_diff $prev $fr] same ndiff ntotal
        set psame 1; set pdiff 0; set ptotal 0
        if {$handdown} {
            lassign [converge::policy_diff [plan_file $prefix [expr {$r - 1}] $f0] \
                                           [plan_file $prefix $r $f0]] psame pdiff ptotal
        }
        dict set rep fixpoint [list [expr {$same && $psame}] $ndiff $ntotal \
                                    $psame $pdiff $ptotal]
        lappend rounds $rep
        if {[clean $rep]} { break }
        if {$same && $psame} {
            set what "the budget it ran under ($ntotal line(s))"
            if {$handdown} { append what " and the plan ($ptotal line(s))" }
            puts "converge.tcl: ${prefix} round $r re-derives $what — a\
                  fixpoint, dirty; the arm stops"
            break
        }
        set prev $fr
    }
    return $rounds
}

# Where a round's top plan lives: beside the budget files, `<prefix>_plan_r<k>.buda`.
proc plan_file {prefix k f0} {
    return [file join [file dirname $f0] ${prefix}_plan_r$k.buda]
}

# ── the table ─────────────────────────────────────────────────────────────
set rows {}
proc row {size arm round policy rep policy_rep} {
    global rows vehicle heal judge
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
    # The handed-down plan's fate: pins applied of the lines sourced, seats
    # NUTS honoured of the seats handed down; `—` where none came down.
    set plan "—"
    if {[dict exists $rep plan_pins]} {
        lassign [dict get $rep plan_pins] pe pa ps po
        if {$pe > 0} { set plan "$pa/$pe pins, $ps/$po seats" }
    }
    # `yes (n)` when budget and plan both reproduce; otherwise what moved:
    # the budget's differing lines, the plan's, or both.
    set fix "—"
    if {[dict exists $rep fixpoint]} {
        lassign [dict get $rep fixpoint] fixed ndiff ntotal psame pdiff ptotal
        if {$fixed} {
            set fix "yes ($ntotal)"
        } else {
            set parts {}
            if {$ndiff > 0} { lappend parts "$ndiff of $ntotal" }
            if {!$psame} { lappend parts "plan $pdiff of $ptotal" }
            set fix "no ([join $parts {, }])"
        }
    }
    set r [list $vehicle $size [expr {$heal ? "on" : "off"}] $arm $round $policy \
                $plan $fix \
                "$fo/$fu/$fv" "$o/$u/$v" $wl_s [lindex [dict get $rep marks] 0] \
                $reserved $used $eff [format %.1f [dict get $rep secs]]]
    # The independent verdict sits NEXT TO the engine's own, never instead
    # of it: the two columns disagreeing on a row is the finding the judge
    # exists to make possible.
    if {$judge} { lappend r [expr {[dict exists $rep judge] ? [dict get $rep judge] : "—"}] }
    lappend rows $r
}

set summary {}
set notes {}

# What DISTINGUISHES this run from another on the same vehicle: the policy.
# The table has carried it since E1; the per-round artifacts (logs, reports,
# budget and plan files) carried only vehicle+size, so a healed run wrote
# over the healerless run's evidence file for file — which is how a 6d
# `yielded` column came to be unreadable against the run that produced it.
# ONE expression, used by both, so the table and its own artifacts cannot
# come to disagree about which run they are from.
#
# What that does and does not buy, stated per FILE rather than in one
# sentence, because the two are distinguished by different things (Codex on
# #940, whose point was that crediting `-arms` to both is true of only one):
# an ARTIFACT is distinguished by policy AND by the arm its own name
# carries, while the TABLE is distinguished by policy alone — `exp` tells a
# `uniform` sweep from the rest and nothing else does, so `-arms td,bu` and
# `-arms blind,td,bu` both land on one filename.  `-informed`, `-f0` and
# `-fmax` are in neither.  The table is the one place to close any of that.
set policy [expr {$heal ? "_healed" : "_healerless"}]_step$step[expr {$nofloor ? "_nofloor" : ""}][expr {$primitive eq "reserve" ? "_reserve" : ""}][expr {$handdown ? "_handdown" : ""}][expr {$yield ? "_yield" : ""}][expr {$judge ? "_judged" : ""}]
if {$tag ne ""} { append policy _$tag }

foreach size $sizes {
    # `label` is what a message calls this run (`soc16`); `p` is what its
    # files are called, which has to survive a second run beside it.
    set label ${vehicle}${size}
    set p ${label}${policy}
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
            # Round 1 doubles as bu's measurement, so it derives bu's seed —
            # only when bu is selected: a blind-only sweep must not pay for
            # (or time) a derivation no arm reads (Codex P2 on #935).
            if {$k == 1 && "bu" in $arms} {
                lappend words -derive [file join $out ${p}_bu_shares_r0.buda]
                if {[derive_opts] ne ""} { lappend words -derive_opts [derive_opts] }
                if {$handdown} {
                    lappend words -derive_plan [file join $out ${p}_bu_plan_r0.buda]
                }
            }
            set rep [session ${p}_blind_r$k $size {*}$words]
            if {$k == 1} { set blind1 $rep }
            # A blind round's reservation: the report's own `reserve`, no
            # shares — the policy rep is the round itself.
            row $size blind $k "reserve $reserve" $rep [expr {$reserve > 0 ? $rep : ""}]
            lappend blind_rounds $rep
            incr solved [lindex [dict get $rep marks] 0]
            if {![converge::blind_more $arms [clean $rep]]} { break }
            incr k
        }
        if {"blind" in $arms} {
            set last [lindex $blind_rounds end]
            lappend summary [list $size blind [llength $blind_rounds] $solved \
                                 [expr {[clean $last] ? "clean" : "dirty"}] \
                                 [join [dict get $last verdict] /] "—"]
        }
    }
    # ── uniform ── (E5's conventional arm: F per TOP layer, doubling)
    if {"uniform" in $arms} {
        set k 1
        set solved 0
        set urounds {}
        set F $f_start
        set ceiling ""
        while {$F <= $fmax} {
            # The sweep's ceiling is PHYSICAL before it is -fmax: `uniform F`
            # names F real tracks over EVERY marked cell, and the engine
            # refuses an F the smallest cell cannot host (the SoC's io_cell
            # has 14 M5 tracks, so F=16 is refused there).  That is the end
            # of the sweep, recorded — not a crashed session.
            if {[catch {session ${p}_uniform_r$k $size -bottomup -uniform $F} rep opts]} {
                if {![string match "*asks more tracks than the cell has*" $rep]} {
                    return -options $opts $rep
                }
                regexp {set_cell_layer_reserve: (.*?)\) — see } $rep -> ceiling
                puts "converge.tcl: ${label}: uniform sweep ends at F=$F — $ceiling"
                break
            }
            # The round's own `governed` rows price it: the policy rep is
            # the round itself, as for a blind round.
            row $size uniform $k "uniform $F" $rep $rep
            lappend urounds $rep
            incr solved [lindex [dict get $rep marks] 0]
            if {[clean $rep]} { break }
            set F [expr {$F * 2}]
            incr k
        }
        if {![llength $urounds]} {
            error "converge.tcl: ${label}: uniform $f_start is already past the\
                   smallest cell's supply ($ceiling) — lower -f0"
        }
        set last [lindex $urounds end]
        lappend summary [list $size uniform [llength $urounds] $solved \
                             [expr {[clean $last] ? "clean" : "dirty"}] \
                             [join [dict get $last verdict] /] "—"]
        if {$ceiling ne ""} {
            lappend notes "size $size, uniform: the sweep ended at F=$F, past\
                           the smallest cell's supply ($ceiling)"
        }
    }
    # ── td ──
    if {"td" in $arms} {
        set f0 [file join $out ${p}_td_shares_r0.buda]
        set words [list -derive $f0]
        if {[derive_opts] ne ""} { lappend words -derive_opts [derive_opts] }
        if {$handdown} {
            # The plan handed down is geometry, so the top-down round is
            # measured on the ALIGNED floorplan the informed rounds route
            # (converge_lib.tcl `-align`; on the SoC the alignment moves
            # nothing, on the mesh it moves every row by a phase).
            lappend words -derive_plan [file join $out ${p}_td_plan_r0.buda] -align
        }
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
        # `-informed 0`: the endpoint is the top-down round itself
        set last [expr {[llength $rounds] ? [lindex $rounds end] : $td0}]
        lappend summary [list $size td [expr {1 + [llength $rounds]}] $solved \
                             [expr {[clean $last] ? "clean" : "dirty"}] \
                             [join [dict get $last verdict] /] [fixpoint_of $last]]
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
        # `-informed 0`: the endpoint is the blind measurement itself
        set last [expr {[llength $rounds] ? [lindex $rounds end] : $blind1}]
        lappend summary [list $size bu [expr {1 + [llength $rounds]}] $solved \
                             [expr {[clean $last] ? "clean" : "dirty"}] \
                             [join [dict get $last verdict] /] [fixpoint_of $last]]
    }
}

# ── print ─────────────────────────────────────────────────────────────────
set hdr "| vehicle | size | heal | arm | round | policy | plan | fixpoint | first ovl/unpl/viol | final ovl/unpl/viol | detailed WL | classes | reserved | used | reserved÷used | s |"
set sep "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
if {$judge} {
    append hdr " judge |"
    append sep "---|"
}
set lines [list $hdr $sep]
foreach r $rows { lappend lines "| [join $r { | }] |" }
lappend lines "" "| size | arm | rounds | classes solved | endpoint | final ovl/unpl/viol | fixpoint |" "|---|---|---|---|---|---|---|"
foreach s $summary { lappend lines "| [join $s { | }] |" }
if {[llength $notes]} {
    lappend lines ""
    foreach n $notes { lappend lines "- $n" }
}
set text [join $lines \n]
puts $text
# The table is E5's when the conventional corridor arm ran, E1's otherwise.
set exp [expr {"uniform" in $arms ? "e5" : "e1"}]
# The same `policy` the artifacts are named by — built once, above.
set name ${exp}_${vehicle}${policy}
set f [open [file join $out $name.md] w]
puts $f "<!-- converge.tcl $argv -->"
puts $f $text
close $f
puts "converge.tcl: table written to [file join $out $name.md]"
