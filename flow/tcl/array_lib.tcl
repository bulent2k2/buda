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
# flow/tcl/array_lib.tcl — the tile-array design, once.
#
# Three drivers now generate the SAME design: `array.tcl` (one session, end
# to end), `array_save.tcl` (session 1 of an iteration — route, look, pin,
# checkpoint) and `array_resume.tcl` (session 2 — reopen the checkpoint and
# re-plan).  A checkpoint is only worth anything if the session that reopens
# it declares the IDENTICAL stack and geometry: `load_pipeline` restores
# topologies whose coordinates are absolute and re-derives slide ranges from
# the floorplan, so a driver that drifted by one track pitch would resume
# onto a different design and say nothing.  So the declarations live here,
# in one place, and the drivers differ only in what they DO.
#
# Everything is a proc taking (rows, cols) where it needs them; nothing here
# runs at source time, so a driver stays in charge of the order.
# ============================================================

namespace eval array_vehicle {
    # leaf 70x130; leaves at pitch 90/150 inside a tile with a 15 margin;
    # tiles at pitch (tile+60)/(tile+70) with a 20 margin at the die edge.
    variable LW 70
    variable LH 130
    variable LPX 90
    variable LPY 150
    variable TM 15
    variable TW [expr {2*15 + 90 + 70}]
    variable TH [expr {2*15 + 150 + 130}]
    variable TPX [expr {(2*15 + 90 + 70) + 60}]
    variable TPY [expr {(2*15 + 150 + 130) + 70}]
}

# ── where the checkpoint lives ────────────────────────────────────────────
# One default both sessions compute the same way, so `array_save.tcl` and
# `array_resume.tcl` agree with no argument passed between them.  Deliberately
# NOT keyed on rows x cols: the resuming session does not know the shape — it
# reads the design out of the database, which is the point.
proc array_vehicle::bdb_path {repo} {
    if {[info exists ::env(BUDA_ARRAY_BDB)] && $::env(BUDA_ARRAY_BDB) ne ""} {
        return [file normalize $::env(BUDA_ARRAY_BDB)]
    }
    return [file join $repo flow tcl out array.bdb]
}

# The diffable text form beside the working binary: `array.bdb` ->
# `array.bdb.sql`.  `save_bdb` writes `.sql` through tools/bdb_serialize, so
# two iterations can be DIFFED rather than merely replacing one another —
# which is what makes a checkpoint useful to a person rather than only to the
# next process.
proc array_vehicle::snapshot_path {bdb} {
    return ${bdb}.sql
}

# ── the metal stack + the knobs that shape a plan ─────────────────────────
# Declared by EVERY session, including a resuming one: layers and track
# patterns are the technology, not design data, and `load_pipeline` requires
# them re-declared before it will rebuild anything.
proc array_vehicle::declare_stack {} {
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
    buda::set_planner_param healersAhead 1   ;# these flows heal on demand
}

# ── hierarchy: one tile cell, ROWS x COLS instances of it ─────────────────
# Requires an open BDB.  A resuming session does NOT call this: the cells and
# instances are what the checkpoint holds.
proc array_vehicle::build_hierarchy {rows cols} {
    variable LW; variable LH; variable LPX; variable LPY
    variable TM; variable TW; variable TH; variable TPX; variable TPY

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
}

# ── the routing interface: busterms + the flat floorplan projection ───────
# `derive_busterms` is for the session that BUILDS the hierarchy; a resuming
# session skips it (busterms are persisted) and calls `load_blocks` alone.
proc array_vehicle::derive_interface {} {
    buda::derive_busterms 2
}

proc array_vehicle::load_blocks {} {
    buda::add_blocks_from_bdb 0
    buda::add_blocks_from_bdb 1 skip
    buda::add_blocks_from_bdb 2 skip
}

# ── buses, generated ──────────────────────────────────────────────────────
#   * intra-tile (depth 2, cell-local): the SAME two buses inside every tile
#     — the hier bundler solves the pattern once as a cell TEMPLATE and
#     replicates it per instance;
#   * cross-tile neighbor chains (depth 0 with leaf-deep endpoints);
#   * one corner-to-corner diagonal (8-bit, the long wire);
#   * a fan-in: every tile's NE leaf drives the SW tile's corner leaf — under
#     CONVERGENT bundling these become ONE fan-in bundle, a per-bit tapered
#     tree.
proc array_vehicle::build_buses {rows cols} {
    buda::bdb_net_mode on

    for {set R 0} {$R < $rows} {incr R} {
        for {set C 0} {$C < $cols} {incr C} {
            set t t_${R}_${C}
            buda::add_bus "bh_${R}_${C}\[4\]" $t/l_0_0.out $t/l_0_1.in
            buda::add_bus "bv_${R}_${C}\[4\]" $t/l_0_0.out $t/l_1_0.in
            if {$C < $cols - 1} {
                buda::add_bus "h_${R}_${C}\[4\]" \
                    $t/l_0_1.out t_${R}_[expr {$C+1}]/l_0_0.in
            }
            if {$R < $rows - 1} {
                buda::add_bus "v_${R}_${C}\[4\]" \
                    $t/l_1_0.out t_[expr {$R+1}]_${C}/l_0_0.in
            }
            if {$R != 0 || $C != 0} {
                buda::add_bus "f_${R}_${C}\[2\]" $t/l_1_1.out t_0_0/l_0_0.in
            }
        }
    }
    # one corner-to-corner diagonal — the long wire.
    buda::add_bus "diag\[8\]" \
        t_0_0/l_1_1.out t_[expr {$rows-1}]_[expr {$cols-1}]/l_0_0.in
}

# ── the measured verdict, and healing only when it is dirty ───────────────
# Three legs, not two: overlaps and unplaced say the metal was PLACED, the
# audit says it is electrically RIGHT — a design can place every bit
# overlap-free and still hold a SEG_OPEN or BIT_SHORT the first two never
# see.  `violations` answers -1 until an audit has run, and -1 is dirty here:
# a design that was never audited has demonstrated nothing.
proc array_vehicle::is_dirty {} {
    return [expr {[buda::query overlaps] > 0 || [buda::query unplaced] > 0
                  || [buda::query violations] != 0}]
}

proc array_vehicle::heal_if_dirty {who} {
    if {![array_vehicle::is_dirty]} { return 0 }
    puts "$who: dirty ([buda::query overlaps] overlaps,\
          [buda::query unplaced] unplaced,\
          [buda::query violations] audit violations) -- healing"
    buda::negotiate_congestion 10
    buda::ripup_reroute 20
    buda::check_design dnuts
    return 1
}

# The flow's exit code IS the design's cleanliness, not "the commands ran".
# Read BEFORE `buda::stop`, since every query needs the engine.
proc array_vehicle::verdict {who} {
    set ov [buda::query overlaps]
    set un [buda::query unplaced]
    set vi [buda::query violations]
    buda::stop
    if {$ov != 0 || $un != 0 || $vi != 0} {
        puts stderr "$who: FAILED -- $ov overlaps, $un unplaced,\
                     $vi audit violations"
        exit 1
    }
    puts "$who: clean -- 0 overlaps, 0 unplaced, 0 audit violations"
    return 0
}

# ── the viewer, on request ────────────────────────────────────────────────
# `buda::visualize` opens the same window a `.buda` script opens and BLOCKS
# until it is closed, which is right for a person at a terminal and wrong for
# the tests that run these vehicles — hence a switch rather than a bare call
# or a commented-out line nobody can use:
#
#     BUDA_ARRAY_VIZ=1 tclsh flow/tcl/array_save.tcl 3 2
#
# On a host with no display it reports BUDA-1903 and carries on, so asking
# for it over ssh tells you why you got no window instead of nothing.
proc array_vehicle::viz_requested {} {
    return [expr {[info exists ::env(BUDA_ARRAY_VIZ)]
                  && $::env(BUDA_ARRAY_VIZ) ne "0"}]
}

proc array_vehicle::show {} {
    if {[array_vehicle::viz_requested]} { buda::visualize }
}

# ── pins ──────────────────────────────────────────────────────────────────
# `BUDA_ARRAY_PIN="diag=2,h_0_0=1"` — one `hint=candidate` per comma, the
# 1-based candidate index the topology explorer shows.
#
# This is the DURABLE form of a choice.  A pin made by hand in the explorer
# is a PREVIEW: the viewer's re-run buttons deliberately never write a
# checkpoint, so that pin lives in the session and dies with it — which is
# exactly how a hand-picked candidate gets lost between runs.  `buda::
# select_topology` persists `topology.is_pinned` the moment it is applied, so
# a pin issued here survives `save_bdb` and a resuming `run_planner` honors
# it.  Look in the GUI, then name the candidate here.
proc array_vehicle::apply_pins {} {
    if {![info exists ::env(BUDA_ARRAY_PIN)] || $::env(BUDA_ARRAY_PIN) eq ""} {
        return 0
    }
    set n 0
    foreach spec [split $::env(BUDA_ARRAY_PIN) ,] {
        set spec [string trim $spec]
        if {$spec eq ""} continue
        if {![regexp {^([^=]+)=(.+)$} $spec -> hint idx]} {
            error "BUDA_ARRAY_PIN: expected hint=index, got '$spec'"
        }
        # Errors raise (the bridge's convention), so an unknown bus or an
        # out-of-range candidate stops the flow instead of silently
        # checkpointing a design nobody chose.
        buda::select_topology [string trim $hint] [string trim $idx]
        puts "  pinned $hint -> candidate $idx"
        incr n
    }
    return $n
}
