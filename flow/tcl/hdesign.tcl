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
# flow/tcl/hdesign.tcl — the HIERARCHICAL iteration loop, one file,
# run again and again.
#
#   bin/btcl -v flow/tcl/hdesign.tcl ?options?     # session 1: build, route, iterate
#   bin/btcl -v flow/tcl/hdesign.tcl               # session 2..N: resume, iterate more
#
# Options (session 1 sets the design's premise; see below for what a
# RESUME may change):
#
#   -mode topdown|bottomup|hybrid   how cell-local interconnect is planned
#           topdown  (default) nothing marked: every bundle planned top-down
#           bottomup set_bottom_up * — every eligible cell's local
#                    interconnect is solved ONCE and copied to each instance
#           hybrid   set_bottom_up blk_cell — only the deepest cell with
#                    local interconnect goes bottom-up; the chip level and
#                    every cross-level bundle stay top-down
#   -reserve N      reserve_top_layers N: the top N layers of the declared
#                   stack are kept for the TOP level, every cell below is
#                   capped just under them (0 = off; try 2 — M6+M7 here)
#   -share C=L=PCT  set_cell_layer_share C L PCT: lease the cell PCT% of a
#                   layer ABOVE its cap — the wiring-limited escape valve
#                   (repeatable, e.g. -share blk_cell=M5=50)
#   -h | -help      print this header and exit
#
#   BUDA_HDESIGN_BDB=<path>    the working checkpoint
#                              (default flow/tcl/out/hdesign.bdb)
#   BUDA_HDESIGN_FRESH=1      discard the checkpoint and rebuild
#
# The design is flow/hbundles/08_cross_level.buda's: THREE levels
# (chip_cell -> blk_cell -> leaf_cell, 2/4/8 instances), with every kind of
# bundle a hierarchy can hold — cell-LOCAL templates (b_lohi_*: one D2
# template solved in blk_cell's frame, 4 instances), same-level bundles at
# D0 and D1 (x_top/x_bot/l_tb), and seven CROSS-LEVEL buses (blk<->leaf and
# chip<->leaf, xl_*) that route in custom endpoint frames.  `pin b_lohi 2`
# pins the TEMPLATE — the hint resolves through the expansion map — so one
# decision re-routes all four instances.
#
# What a RESUME may change, and what it may not:
#   * `-reserve` / `-share` are PLANNING policy: they persist in the
#     checkpoint, and changing them on a resume is the experiment loop —
#     the engine VOIDS (loudly) any restored plan a tightened cap outlaws,
#     and the session re-plans under the new policy.
#   * `-mode` is a BUILD premise: bottom-up marks come with placement
#     ALIGNMENT (align_bottom_up nudges instances onto a common track phase
#     BEFORE busterms are derived), and a checkpoint's geometry cannot be
#     re-aligned under routing that was generated against it.  A resume with
#     a different -mode is refused with the rebuild recipe.
#
# After the route the same PROMPT as flow/tcl/design.tcl reads stdin:
#   topos/explore/show, pin <bus> <N> (1-based, durable at once), unpin,
#   replan, save, done (auto-replans if pins changed), and any
#   engine-registry command verbatim.  EOF just finishes the session.
# ============================================================

set repo [file dirname [file dirname [file dirname [file normalize [info script]]]]]
source [file join $repo tools buda.tcl]
source [file join $repo flow tcl prompt.tcl]

# ── options ───────────────────────────────────────────────────────────────
set mode topdown
set reserve 0
set shares {}
for {set i 0} {$i < [llength $argv]} {incr i} {
    set a [lindex $argv $i]
    switch -- $a {
        -mode {
            set mode [lindex $argv [incr i]]
            if {$mode ni {topdown bottomup hybrid}} {
                puts stderr "hdesign.tcl: -mode must be topdown|bottomup|hybrid, got '$mode'"
                exit 2
            }
        }
        -reserve {
            set reserve [lindex $argv [incr i]]
            if {![string is integer -strict $reserve] || $reserve < 0} {
                puts stderr "hdesign.tcl: -reserve takes a non-negative integer, got '$reserve'"
                exit 2
            }
        }
        -share {
            set s [lindex $argv [incr i]]
            if {[llength [split $s =]] != 3} {
                puts stderr "hdesign.tcl: -share takes cell=layer=pct, got '$s'"
                exit 2
            }
            lappend shares $s
        }
        -h - -help {
            # The banner block between the two `# ====` rulers IS the help
            # (skipping the license header above it).
            set f [open [info script]]
            set inside 0
            foreach ln [split [read $f] \n] {
                if {[string match "# =*" $ln]} {
                    if {$inside} break
                    set inside 1
                    continue
                }
                if {$inside} { puts [string range $ln 2 end] }
            }
            close $f
            exit 0
        }
        default {
            puts stderr "hdesign.tcl: unknown option '$a' (try -h)"
            exit 2
        }
    }
}

# ── the checkpoint ────────────────────────────────────────────────────────
set bdb [expr {[info exists ::env(BUDA_HDESIGN_BDB)] && $::env(BUDA_HDESIGN_BDB) ne ""
               ? $::env(BUDA_HDESIGN_BDB)
               : [file join $repo flow tcl out hdesign.bdb]}]
set fresh [expr {[info exists ::env(BUDA_HDESIGN_FRESH)]
                 && $::env(BUDA_HDESIGN_FRESH) ne "0"}]
if {$fresh && [file exists $bdb]} {
    puts "hdesign.tcl: BUDA_HDESIGN_FRESH=1 -- removing $bdb and rebuilding"
    file delete $bdb ${bdb}-wal ${bdb}-shm ${bdb}.opts
}
set resuming [file exists $bdb]
file mkdir [file dirname $bdb]

# The -mode guard: a build premise must match the checkpoint it reopens.
# The sidecar records what session 1 built with; policy options (-reserve/
# -share) are free to differ — that IS the experiment — but a mode change
# needs a rebuild, and saying so beats a template-track mismatch five
# stages later.
set opts_file ${bdb}.opts
if {$resuming && [file exists $opts_file]} {
    set f [open $opts_file]; set built_mode [string trim [read $f]]; close $f
    if {$built_mode ne $mode} {
        puts stderr "hdesign.tcl: checkpoint was built -mode $built_mode; a\
                     resume cannot change it to '$mode' (alignment happens\
                     before busterms are derived).  Rebuild:\
                     BUDA_HDESIGN_FRESH=1 bin/btcl flow/tcl/hdesign.tcl -mode $mode"
        exit 2
    }
}

buda::start

# ── the technology (every session, identical by construction) ─────────────
buda::source [file join $repo flow tracks tracks.buda]
buda::corner_margin dx 5 dy 5
buda::set_min_stub_length 2
buda::detour_channel S 100                ;# 08's b6 detour
buda::set_planner_param healersAhead 1    ;# this flow heals on demand

buda::open_bdb $bdb

if {!$resuming} {
    # ── the design: flow/hbundles/08_cross_level.buda, verbatim ───────────
    buda::add_cell chip_cell 400 500
    buda::add_cell blk_cell  180 230
    buda::add_cell leaf_cell  70 130
    buda::add_inst_to_cell chip_cell top blk_cell  10 260
    buda::add_inst_to_cell chip_cell bot blk_cell  10  10
    buda::add_inst_to_cell blk_cell  lo  leaf_cell 10  50
    buda::add_inst_to_cell blk_cell  hi  leaf_cell 90  50
    buda::add_inst left  chip_cell -   0 0
    buda::add_inst right chip_cell - 450 0

    # Bottom-up marks + ALIGNMENT go here, before busterms are derived —
    # align_bottom_up nudges instances onto a common track phase, and a
    # busterm derived first would describe the pre-nudge geometry.  08's
    # geometry cannot fully align (the right chip is 450 off in x against
    # an 11808 x-period, and the +56 y-nudge would push a blk out of its
    # parent, so the moves auto-revert) — `on_mismatch independent` is the
    # engine's own remedy: aligned instances copy the template's solve,
    # the misaligned ones solve individually.  Persists in BDB meta.
    switch -- $mode {
        bottomup { buda::set_bottom_up * }
        hybrid   { buda::set_bottom_up blk_cell }
    }
    if {$mode ne "topdown"} {
        buda::align_bottom_up
        buda::check_template_tracks on_mismatch independent
    }

    buda::derive_busterms 2
    buda::add_blocks_from_bdb 0
    buda::add_blocks_from_bdb 1 skip
    buda::add_blocks_from_bdb 2 skip

    buda::bdb_net_mode on
    # Same-level: D0 cross-chip, D1 intra-chip, D2 cell-local template x4.
    buda::add_bus "x_top\[4\]"      left/top/hi.out   right/top/lo.in
    buda::add_bus "x_bot\[4\]"      left/bot/hi.out   right/bot/lo.in
    buda::add_bus "l_tb\[4\]"       left/top/lo.out   left/bot/hi.in
    buda::add_bus "b_lohi_lt\[4\]"  left/top/lo.out   left/top/hi.in
    buda::add_bus "b_lohi_lb\[4\]"  left/bot/lo.out   left/bot/hi.in
    buda::add_bus "b_lohi_rt\[4\]"  right/top/lo.out  right/top/hi.in
    buda::add_bus "b_lohi_rb\[4\]"  right/bot/lo.out  right/bot/hi.in
    # Cross-level: blk<->leaf, chip<->leaf — one endpoint deeper than the
    # other, routed in custom endpoint frames.
    buda::add_bus "xl_b2l_ht\[4\]"  left/top.out      right/top/lo.in
    buda::add_bus "xl_b2l_hb\[4\]"  left/bot.out      right/bot/lo.in
    buda::add_bus "xl_l2b_dt\[4\]"  right/top/hi.out  left/bot.in
    buda::add_bus "xl_l2b_db\[4\]"  right/bot/hi.out  left/top.in
    buda::add_bus "xl_ic_b2l\[4\]"  left/top.out      left/bot/lo.in
    buda::add_bus "xl_ic_l2b\[4\]"  left/bot/hi.out   left/top.in
    buda::add_bus "xl_c2l\[4\]"     left.out          right/bot/hi.in
} else {
    # ── resume: geometry projections only ─────────────────────────────────
    # Cells, instances, buses, busterms, marks, caps and shares are all IN
    # the checkpoint (re-running add_inst into a populated BDB is a
    # duplicate-instance error).  What load_pipeline needs present is the
    # stack (above) and the flat floorplan projection of the hierarchy.
    buda::add_blocks_from_bdb 0
    buda::add_blocks_from_bdb 1 skip
    buda::add_blocks_from_bdb 2 skip
}

# ── layer policy (every session: changing it IS the experiment) ───────────
# reserve_top_layers persists and `off` frees what an earlier session
# reserved, so a resume dropping -reserve really drops it; a resume
# tightening it VOIDS the restored plan (loudly) and re-plans below.
if {$reserve > 0} {
    buda::reserve_top_layers $reserve
} elseif {$resuming} {
    buda::set_layer_caps_by_depth off
}
foreach s $shares {
    lassign [split $s =] cell layer pct
    buda::set_cell_layer_share $cell $layer $pct
}

# ── build or resume the pipeline ──────────────────────────────────────────
if {$resuming} {
    buda::load_pipeline
    set nb [buda::query bundles]
    if {$nb == 0} {
        error "hdesign.tcl: $bdb restored no bundles -- not this flow's\
               checkpoint?  BUDA_HDESIGN_FRESH=1 rebuilds it"
    }
    puts "hdesign.tcl: RESUMED $nb bundles from $bdb -- starting at the planner"
} else {
    buda::run_hier_bundler depth 2
    buda::generate_hier_topologies
    puts "hdesign.tcl: BUILT [buda::query bundles] bundles (-mode $mode) -> $bdb"
    set f [open $opts_file w]; puts $f $mode; close $f
}

# ── route (and re-route: `replan` at the prompt lands here too) ───────────
proc route {} {
    buda::run_planner hier 5
    buda::run_nuts
    buda::check_design nuts
    buda::run_detailed_nuts
    buda::check_design dnuts
    if {[buda::query overlaps] > 0 || [buda::query unplaced] > 0
        || [buda::query violations] != 0} {
        puts "hdesign.tcl: dirty ([buda::query overlaps] overlaps,\
              [buda::query unplaced] unplaced,\
              [buda::query violations] audit violations) -- healing"
        buda::negotiate_congestion 10
        buda::ripup_reroute 20
        buda::check_design dnuts
    }
    buda::report_wirelength
}
route

# ── the prompt (shared with design.tcl; replan goes through `hier`) ───────
# The loop lives in flow/tcl/prompt.tcl — it was character-for-character the
# same in both files, so it is now written once and handed this script's own
# `route`.  A pin's hint resolves through the hierarchy: `pin b_lohi 2`
# matches the D2 TEMPLATE behind the per-instance expansion, so one pin
# re-routes all four blk instances; `pin xl_c2l 3` pins a cross-level bundle
# the same way.
set pins_dirty [prompt::run hdesign.tcl "hdesign>" route $bdb b_lohi]
if {$pins_dirty} {
    puts "hdesign.tcl: pins changed since the last route -- re-planning so the\
          checkpoint stays coherent"
    route
}

# The working `.bdb` has been written in place all along; the snapshot is
# the DIFFABLE record of this iteration.
buda::save_bdb ${bdb}.sql
puts "hdesign.tcl: checkpoint $bdb (snapshot ${bdb}.sql) -- run me again to\
      keep iterating"

set ov [buda::query overlaps]
set un [buda::query unplaced]
set vi [buda::query violations]
buda::stop
if {$ov != 0 || $un != 0 || $vi != 0} {
    puts stderr "hdesign.tcl: FAILED -- $ov overlaps, $un unplaced, $vi audit violations"
    exit 1
}
puts "hdesign.tcl: clean -- 0 overlaps, 0 unplaced, 0 audit violations"
