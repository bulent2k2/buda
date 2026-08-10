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
# flow/tcl/array.tcl — the Tcl front end's own flow vehicle.
#
#   tclsh flow/tcl/array.tcl  ?ROWS COLS?          (default 2 2)
#
# A .buda script can only STATE a design; this flow COMPUTES one — which is
# the case the Tcl front end exists for.  Everything below is generated from
# two numbers: a ROWS x COLS array of `tile` instances, each tile a cell
# containing a 2 x 2 array of `leaf` cells, connected by buses a pair of
# nested loops emits:
#
#   * intra-tile (depth 2, cell-local): the SAME two buses inside every
#     tile — the hier bundler solves the pattern once as a cell TEMPLATE
#     and replicates it per instance;
#   * cross-tile neighbor chains (depth 0 with leaf-deep endpoints): each
#     tile's east edge leaf to its neighbor's west edge leaf, and the same
#     north-south — the cross-level case;
#   * one corner-to-corner diagonal (8-bit, the long wire);
#   * a fan-in: every tile's NE leaf drives the SW tile's corner leaf —
#     under CONVERGENT bundling these N buses become ONE fan-in bundle
#     routed as a per-bit tapered tree.
#
# The end of the flow is the other half of the point: `buda::query` returns
# VALUES, so the flow BRANCHES — healers run only if the measured result is
# dirty, and the script's exit code is the design's cleanliness, not just
# "the commands ran".
# ============================================================

set rows 2
set cols 2
if {$argc >= 2} {
    set rows [lindex $argv 0]
    set cols [lindex $argv 1]
}

# Resolve the repo root from THIS script (flow/tcl/ is two levels down), so
# the flow runs from any CWD — the same rule .buda scripts follow.
set repo [file dirname [file dirname [file dirname [file normalize [info script]]]]]
source [file join $repo tools buda.tcl]

buda::start

# ── geometry, computed ─────────────────────────────────────────────────────
# leaf 70x130; leaves at pitch 90/150 inside a tile with a 15 margin;
# tiles at pitch (tile+60)/(tile+70) with a 20 margin at the die edge.
set LW 70;  set LH 130
set LPX 90; set LPY 150
set TM 15
set TW [expr {2*$TM + $LPX + $LW}]
set TH [expr {2*$TM + $LPY + $LH}]
set TPX [expr {$TW + 60}]
set TPY [expr {$TH + 70}]

# ── layers + track patterns (the shared corpus stack, stated inline) ──────
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
buda::set_planner_param healersAhead 1   ;# this flow heals below, on demand

# ── hierarchy: one tile cell, ROWS x COLS instances of it ─────────────────
buda::open_bdb :memory:

buda::add_cell leaf_cell $LW $LH
buda::add_cell tile_cell $TW $TH
foreach r {0 1} {
    foreach c {0 1} {
        buda::add_inst_to_cell tile_cell l_${r}_${c} leaf_cell \
            [expr {$TM + $c*$LPX}] [expr {$TM + $r*$LPY}]
    }
}
for {set R 0} {$R < $rows} {incr R} {
    for {set C 0} {$C < $cols} {incr C} {
        buda::add_inst t_${R}_${C} tile_cell - \
            [expr {20 + $C*$TPX}] [expr {20 + $R*$TPY}]
    }
}

buda::derive_busterms 2
buda::add_blocks_from_bdb 0
buda::add_blocks_from_bdb 1 skip
buda::add_blocks_from_bdb 2 skip

# ── buses, generated ──────────────────────────────────────────────────────
buda::bdb_net_mode on

for {set R 0} {$R < $rows} {incr R} {
    for {set C 0} {$C < $cols} {incr C} {
        set t t_${R}_${C}
        # intra-tile, the SAME pattern in every tile (depth-2 templates):
        # one horizontal (SW -> SE) and one vertical (SW -> NW) 4-bit bus.
        buda::add_bus "bh_${R}_${C}\[4\]" $t/l_0_0.out $t/l_0_1.in
        buda::add_bus "bv_${R}_${C}\[4\]" $t/l_0_0.out $t/l_1_0.in
        # cross-tile neighbor chains, leaf-deep endpoints (depth 0):
        if {$C < $cols - 1} {
            buda::add_bus "h_${R}_${C}\[4\]" \
                $t/l_0_1.out t_${R}_[expr {$C+1}]/l_0_0.in
        }
        if {$R < $rows - 1} {
            buda::add_bus "v_${R}_${C}\[4\]" \
                $t/l_1_0.out t_[expr {$R+1}]_${C}/l_0_0.in
        }
        # fan-in: every tile's NE leaf drives the SW tile's SW leaf.
        if {$R != 0 || $C != 0} {
            buda::add_bus "f_${R}_${C}\[2\]" $t/l_1_1.out t_0_0/l_0_0.in
        }
    }
}
# one corner-to-corner diagonal — the long wire.
buda::add_bus "diag\[8\]" \
    t_0_0/l_1_1.out t_[expr {$rows-1}]_[expr {$cols-1}]/l_0_0.in

# ── the hier pipeline ─────────────────────────────────────────────────────
# CONVERGENT so the f_* buses merge into ONE cross-level fan-in bundle.
buda::run_hier_bundler depth 2 CONVERGENT

set nb [buda::query bundles]
if {$nb == 0} { error "nothing bundled" }
puts "array.tcl: $rows x $cols tiles -> $nb bundles"
buda::dump_hbundles

buda::generate_hier_topologies
buda::run_planner hier 5
buda::run_nuts
buda::check_design nuts

buda::run_detailed_nuts
buda::check_design dnuts

# ── branch on the measured result: heal only if dirty ─────────────────────
# Three legs, not two: overlaps and unplaced say the metal was PLACED, the
# audit says it is electrically RIGHT — a design can place every bit
# overlap-free and still hold a SEG_OPEN or BIT_SHORT the first two never
# see.  `violations` answers -1 until an audit has run, and -1 is dirty
# here: a design that was never audited has demonstrated nothing.
if {[buda::query overlaps] > 0 || [buda::query unplaced] > 0
        || [buda::query violations] != 0} {
    puts "array.tcl: dirty ([buda::query overlaps] overlaps,\
          [buda::query unplaced] unplaced,\
          [buda::query violations] audit violations) -- healing"
    buda::negotiate_congestion 10
    buda::ripup_reroute 20
    buda::check_design dnuts
}

buda::report_wirelength

set ov [buda::query overlaps]
set un [buda::query unplaced]
set vi [buda::query violations]
buda::stop

if {$ov != 0 || $un != 0 || $vi != 0} {
    puts stderr "array.tcl: FAILED -- $ov overlaps, $un unplaced,\
                 $vi audit violations"
    exit 1
}
puts "array.tcl: clean -- 0 overlaps, 0 unplaced, 0 audit violations"
