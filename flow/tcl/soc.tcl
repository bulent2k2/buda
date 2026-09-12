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
# flow/tcl/soc.tcl — the SoC-shaped vehicle, end to end.
#
#   btcl flow/tcl/soc.tcl                    # NQ=2 quadrants
#   btcl flow/tcl/soc.tcl 4                  # 4 quadrants -- THE DIAL
#   btcl flow/tcl/soc.tcl 2 -NC 4 -NBANK 4   # wider clusters, bigger L1
#   btcl flow/tcl/soc.tcl 2 -bottomup        # solve one cluster, copy it
#   btcl flow/tcl/soc.tcl 8 -dry             # print the size, build nothing
#   btcl flow/tcl/soc.tcl 2 -caps            # reserve the top pair for the top
#   btcl flow/tcl/soc.tcl 2 -census          # instances per leaf cell type
#
# Every knob in `soc_vehicle::configure` is settable as `-<NAME> <value>`
# (NQ, NC, NBANK, NBANK2, NIO, DW/AW/IW/CW, BITPITCH, PAD, M, GAP), so a
# larger experiment is an argument rather than an edit.  The design is
# `soc_lib.tcl` — one source, so a future save/resume driver cannot drift
# from this one (array_lib.tcl's rule).
#
# WHY THIS VEHICLE, against `tpu.tcl`: a mesh is one cell tiled, so every
# leaf is the same cell at the same depth.  This is DIVERSE (eleven leaf cell
# types repeating 1 to 20 times each -- `-census` counts them) and RAGGED IN
# DEPTH (an ALU sits four levels down, a UART two) — see `soc_lib.tcl`'s
# header for what that buys.
#
# `-caps` is the experiment the ragged depth exists for: `reserve_top_layers`
# gives the top level the top pair and caps every cell below it by how deep
# its OWN content goes.  On a uniform-depth design that collapses to one
# case; here the quadrant branch and the peripheral branch get different
# bands from one declaration.
# ============================================================

set repo [file dirname [file dirname [file dirname [file normalize [info script]]]]]
source [file join $repo tools buda.tcl]
source [file join $repo flow tcl soc_lib.tcl]

# ── the command line ──────────────────────────────────────────────────────
# A bare leading integer is NQ (the common case); everything else is
# -NAME value, validated by `configure` — an unknown knob is an ERROR rather
# than a silently ignored word, because a typo in a sweep that runs for an
# hour must not report on a design nobody asked for.
set overrides {}
set bottomup 0
set caps 0
set bydepth ""
set dry 0
set census 0
set argi 0
if {$argc > 0 && [string is integer -strict [lindex $argv 0]]} {
    lappend overrides NQ [lindex $argv 0]
    incr argi
}
while {$argi < $argc} {
    set opt [lindex $argv $argi]
    switch -- $opt {
        -bottomup { set bottomup 1; incr argi }
        -caps     { set caps 1; incr argi }
        -bydepth {
            if {$argi + 1 >= $argc} {
                error "soc.tcl: -bydepth needs a cap list, e.g. -bydepth {M3 M4 M5}"
            }
            set bydepth [lindex $argv [expr {$argi+1}]]
            incr argi 2
        }
        -dry      { set dry 1; incr argi }
        -census   { set census 1; incr argi }
        default {
            if {[string index $opt 0] ne "-"} {
                error "soc.tcl: unexpected argument '$opt' (NQ comes first)"
            }
            if {$argi + 1 >= $argc} { error "soc.tcl: $opt needs a value" }
            lappend overrides [string range $opt 1 end] \
                              [lindex $argv [expr {$argi+1}]]
            incr argi 2
        }
    }
}

# `-bottomup` USED TO widen the channel here, and no longer does -- which is
# worth writing down, because the reason it did was a fault elsewhere.
#
# The copied cell-local routing is a fixed copy at every instance, so the
# residue it cannot clear is an OVERLAP rather than an open, and the flag
# quietly supplied `GAP 24 M 24` to clear two of them at NQ=4.  Every
# SIZING fault since has pushed that need further out, which is the pattern
# worth recording: the STAR faces took it from NQ=4 to NQ=8, and the PHANTOM
# COEFFICIENTS (`2*DW` on four cells, with nothing behind them) took it from
# NQ=8 to NQ=16.  At the default channel `-bottomup` is clean through NQ=8
# (2,175,864 detailed WL) and every gap at NQ=8 is clean:
#
#   NQ=8   GAP     16   24   32   48   64   96      all CLEAN
#
# It is NQ=16 where a fixed copy at the default channel first comes back
# DIRTY -- stated as the OBSERVATION it is, since what the run actually
# needs is argued at the end of this comment and is not a channel -- and
# there the gap sweep is genuinely NON-MONOTONE -- measured on the honestly
# sized design, not as an artefact this time:
#
#   NQ=16  GAP=M   16       24   32       48       96
#   result          X(3/8)  ok   X(0/8)   X(1/8)   ok   (overlaps/unplaced)
#
# That irregularity was reported once before and WITHDRAWN when its cause
# turned out to be the stars.  It is back on different evidence: with the
# faces honest and the coefficients gone, BOTH 32 and 48 are worse than 24.
#
# What that licenses needs care, because the first write-up overreached
# (Codex P2, #930).  Non-monotonicity rules out "INCREASE UNTIL CLEAN" as a
# search -- step up from a clean 24 and you land on a dirty 32 -- so a sweep
# must be a sweep and not a ramp.  It does NOT show that no fixed default
# exists, and the table above refutes that reading: GAP=M=96 is CLEAN at
# every size measured (NQ=2, 4, 8, 16 -- and NQ=32, measured since, so the
# range is wider than when this paragraph was written).  A conservative
# built-in value is available.
#
# The argument against building it in is COST, which is this file's own
# lesson pointed at the flag -- detailed WL against the cheapest clean gap
# at each size:
#
#   NQ= 2   591,230 ->  1,231,210   2.08x    (clean at the default 16)
#   NQ= 4 1,088,063 ->  2,210,154   2.03x    (clean at the default 16)
#   NQ= 8 2,175,864 ->  4,329,443   1.99x    (clean at the default 16)
#   NQ=16 4,648,190 ->  8,621,243   1.85x    (cheapest clean is 24)
#   NQ=32 8,938,821 -> 16,671,389   1.87x    (cheapest clean is 24)
#
# A default of 96 roughly DOUBLES the wire at every size -- including the
# three that need no channel at all -- to rescue the TWO that do (the NQ=32
# row was missing while the sentence claimed every measured size; Codex P2,
# #930).  That is
# the trade the "a wider channel buys no routing and costs wire" measurement
# above refuses, so the flag does not make it on the caller's behalf.  A
# bottom-up run AT NQ=16 asks for the channel EXPLICITLY
# (`soc.tcl 16 -bottomup -GAP 24 -M 24`) and MEASURES it.
#
# NQ=32 IS MEASURED NOW (Codex P2, #930: the table above had recorded an
# NQ=32 bottom-up run while this paragraph still said NQ=32 was never run
# bottom-up -- my own data refuting my own sentence, printed thirty lines
# apart).  Measured, `-bottomup` at NQ=32:
#
#   GAP=M   16                    24        96
#   result  X 3 ovl / 8 unpl      CLEAN     CLEAN
#   det WL  --                    8,938,821 16,671,389
#
# So NQ=32 behaves like NQ=16: dirty at the default channel, and the
# caller's own remedy `-GAP 24 -M 24` routes it for 46% LESS wire than the
# conservative 96, which costs 1.87x AS MUCH (8,938,821 against 16,671,389).
# Both directions, because this read "1.87x less wire" (Codex P2, #930) --
# an invalid construction: a ratio > 1 says how much MORE the dearer option
# costs, and "Nx less" means nothing arithmetically.  That is a STRONGER statement than the hedge
# it replaces, in both directions -- NQ=32 does need a channel, AND a gap
# that routes it is known.
#
# NQ=64 IS MEASURED NOW TOO, at the DEFAULT gap: 7 ovl / 8 unpl / 8 viol,
# detailed WL 16,654,709 over 2451 bundles / 76,096 bit-wires -- and the
# doomed seat is `bundle 2 seg 0` on M7, 7 tracks < 8 bits, i.e. THE SAME
# `pc_0` SEGMENT again.  So the whole advertised dial is now run bottom-up,
# and the recurring-segment reading holds across a genuine 4x span (NQ=16 to
# NQ=64: 32 -> 128 clusters, 427 -> 1675 leaves).  Whether a NAMED CHANNEL
# rescues NQ=64 as it does 16 and 32 is NOT yet measured -- that sweep is
# running -- so nothing is claimed about it here.  The rule needs no threshold either way: if a bottom-up run is
# dirty, SWEEP the channel.
#
# The MECHANISM is not established: the earlier claim that a fixed copy
# "lands each instance on whatever track phase the channel gives it" was an
# assertion rather than a measurement, and stays a hypothesis.
#
# The 48 row is why the recipe says MEASURE rather than naming a number: it
# was clean last revision, this line recommended it by name, and three added
# buses made it fail -- so the example itself decayed into advice for a
# configuration that does not route.  Two of the five gaps swept work here.
#
# AND THE CHANNEL IS NOT THE CAUSE -- the fifth time on this vehicle a
# channel reading turned out to be a seat or a face.  EVERY failing run
# strands the same eight bits of the same bundle:
#
#   hb-2  D0  cross-level  "DRV:io/p_0|REC:quad_0/cl_0/rtr/xbar"  nets=8
#
# i.e. `pc_0`, the first io pad into cluster 0's crossbar, 8 bits of CW.
# Measured -- and a doomed seat is reported for THAT SEGMENT at every gap
# INCLUDING the clean one (which seat differs -- see below the table):
#
#   NQ  GAP  seat reported for that segment         endpoint
#   16   16  bundle 2 seg 0  M7 (TOP)  7 < 8 bits   X 3 ovl / 8 unpl
#   16   24  bundle 2 seg 0  M4 (LOW)  0 < 8 bits   CLEAN
#   16   32  bundle 2 seg 0  M7 (TOP)  7 < 8 bits   X 0 ovl / 8 unpl
#   16   48  bundle 2 seg 0      (LOW)              X 1 ovl / 8 unpl
#   32   16  bundle 2 seg 0  M7 (TOP)  7 < 8 bits   X 3 ovl / 8 unpl
#
# WHAT REPEATS IS THE SEGMENT, NOT THE SEAT (Codex P2, #930).  Read the
# rows: the assigned layer moves M7/TOP -> M4/LOW -> M7/TOP, and changing
# GAP changes the placed span and slide window too.  A SEAT is defined by
# exactly those things -- `_report_doomed_seats` derives it from the
# segment's ASSIGNED LAYER and its span x slide window
# (src/buda_session/nutsflow.py) -- so two rows reporting different layers
# are different SEATS.  What the table establishes is that the same
# LOGICAL bundle/segment (`hb-2` seg 0, the 8 bits of `pc_0`) is
# repeatedly supply-doomed however the gap is set; what varies is both the
# seat it lands on AND whether the HEALERS clear it.  Calling the seat
# invariant overstated the evidence, and it did so one paragraph before
# admitting the healing mechanism is unknown -- the same fault as the
# withdrawn phase-lottery mechanism, in the sentence written to replace
# it.  The tool names the category itself every time --
# "static width-infeasibility, not reservation conflicts" -- so this is the
# #536 supply-doomed seat class and the gap only changes the geometry the
# healers then repair, which is why the curve is non-monotone and why no
# threshold predicted it.  WHY healing succeeds at 24 and not at 16/32/48 is
# NOT established, and this file just withdrew one asserted mechanism, so it
# will not supply another.
#
# THE REPEAT IS ALSO NOT SIZE-INDEPENDENT.  Two measurements bound it, and
# the second is the one that matters:
#
#   NQ=2/4/8  -bottomup            NO doomed seat reported at all
#   NQ=16     PLAIN (top-down)     clean, NO doomed seat reported at all
#   NQ=16     -bottomup            the seat, at every gap swept
#
# The same design, the same size, the same `pc_0` and the same `io_cell`
# face is CLEAN top-down.  So the seat is a product of the BOTTOM-UP FIXED
# COPY AT SCALE, not of a declared width -- which is why "a wider `io_cell`
# face" was dropped from the remedies below: it was an untested guess, and
# the top-down run is evidence against it.
#
# Trying to make the seat appear at NQ=1 -- to give the test a live mutation
# -- failed THREE times, and the failures are independent evidence for the
# same reading: an `io_cell` face /4 is REFUSED AT DECLARATION (a too-narrow
# face never reaches the router), and `-GAP 4`/`-GAP 8` and `-CW 64` all come
# back clean with no seat.  At that size the seat is reachable by NEITHER
# width NOR channel.
#
# The lever a 7-tracks-for-8-bits seat wants is the SEAT, and this is
# MEASURED now rather than asserted (NQ=16 -bottomup, one knob at a time):
#
#   remedy                             ovl  unpl  viol   detailed WL
#   none (default GAP 16)               3     8     8     4,319,399
#   set_max_bundle_bits 4 for pc_       3     0     0     4,284,321
#   -GAP 24 -M 24                       0     0     0     4,648,190
#   both                                0     0     0     4,762,874
#
# That DECOMPOSES the failure into two independent faults.  The seat lever
# removes EXACTLY the seat -- the 8 stranded bits and the 8 audit violations
# -- and no doomed seat is reported, at slightly LESS wire than doing
# nothing; the 3 overlaps are untouched, because they are the fixed-copy
# residue and a different problem.  The channel clears BOTH, which is
# precisely why it looked like the cause for five revisions: it is the
# remedy for the overlaps and it re-seats the doomed segment incidentally.
#
# The practical advice does NOT change, and saying so plainly matters more
# than the diagnosis being new: for a CLEAN endpoint the channel alone is
# still the cheapest point, and stacking both is redundant and costs 2.5%
# more wire than the channel by itself.  What the seat lever buys is a
# correct account of WHICH fault is which -- and a remedy for the stranding
# alone, at less wire than the default, for a methodology that can live with
# the 3 overlaps.  The channel guidance stays, labelled a workaround, not a
# cause.
#
# The atomicity rule it needed is kept as history: when the flag DID supply
# the pair it had to supply BOTH or NEITHER, since `-bottomup -GAP 16` left
# `M` at 24 and gave 4976x1576 where THAT revision's default geometry was
# 4720x1440 (both dies since re-measured away by the sizing fixes), while
# `-bottomup -M 16` leaked the other way -- a sweep meant to vary the channel
# varied two things.  Nothing supplies a knob behind the
# caller's back now.

soc_vehicle::configure $overrides
soc_vehicle::banner "soc.tcl"
if {$dry} { exit 0 }

buda::start

soc_vehicle::declare_stack
buda::open_bdb :memory:
soc_vehicle::build_hierarchy

# `-census` answers the one claim about this vehicle that is a COUNT rather
# than a shape: how many instances each leaf cell type has.  Derived from the
# structure the hierarchy was built from (`soc_lib.tcl`'s `_fill`), so it
# reports the design and not a model of it.
if {$census} {
    set n [soc_vehicle::leaf_census]
    foreach c [lsort [dict keys $n]] { puts "census $c [dict get $n $c]" }
    buda::stop
    exit 0
}

if {$bottomup} {
    # Mark BEFORE deriving busterms: `align_bottom_up` nudges instances onto
    # a shared track phase and must run while the floorplan is still the only
    # thing derived from these coordinates.  `*` marks every eligible cell,
    # which on a DIVERSE design is the interesting case — a cell with one
    # instance is solved once and frozen as a keepout rather than copied.
    buda::set_bottom_up *
    buda::align_bottom_up
}

# The band declarations this vehicle exists to exercise.
#
# `-caps` is STACK-RELATIVE (`reserve_top_layers 2`): the same line is
# correct whatever stack is declared, where an absolute band is only right
# for the stack it was written against.  It gives every cell below the top
# ONE band, so what it exercises here is the top/not-top split.
#
# `-bydepth` is the one that needs the ragged depth: a cell's LEVEL is
# intrinsic (how deep its own content goes), so `sram_cell` (1), `l1_cell`
# (2), `cluster_cell` (3) and `quad_cell` (4) take DIFFERENT caps from one
# declaration.  On a uniform-depth vehicle every cell is one level and the
# per-level behaviour collapses to the `-caps` case.
if {$caps} { buda::reserve_top_layers 2 }
if {$bydepth ne ""} { buda::set_layer_caps_by_depth {*}$bydepth }

soc_vehicle::derive_interface
soc_vehicle::load_blocks
soc_vehicle::build_buses

# ── the hier pipeline ─────────────────────────────────────────────────────
buda::run_hier_bundler depth 4

set nb [buda::query bundles]
if {$nb == 0} { error "soc.tcl: nothing bundled" }
puts "soc.tcl: $nb bundles"
# The structure, printed: this design's whole point is that its bundles land
# at SEVERAL levels, and this is where that shows.
buda::dump_hbundles

buda::generate_hier_topologies
buda::run_planner hier 5
buda::run_nuts
buda::check_design nuts

if {$bottomup} { buda::check_template_tracks on_mismatch independent }

buda::run_detailed_nuts
buda::check_design dnuts

soc_vehicle::heal_if_dirty "soc.tcl"
buda::report_wirelength

soc_vehicle::verdict "soc.tcl"
