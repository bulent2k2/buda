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
# flow/tcl/soc_lib.tcl — an SoC-shaped design: DEEP and DIVERSE.
#
# WHAT THE CORPUS WAS MISSING, and it is not size.  `flow/tcl/tpu.tcl` is a
# MESH: one cell tiled N x N, so every leaf is the same cell at the same
# depth.  `flow/chip` is heterogeneous but UNIFORM IN DEPTH — every leaf sits
# at the same level.  `flow/ariane133` is a real design read from somebody
# else's files, and a synthesized netlist is uniquified, so nothing repeats.
# Between them, two shapes a real SoC has were unexercised:
#
#   * MANY DIFFERENT CELL TYPES, repeating a DIFFERENT NUMBER OF TIMES each.
#     A mesh measures solve-once-copy on one cell tiled N times, so every
#     count is the same count; here they span an order of magnitude, and the
#     two extremes are separate code paths — `set_bottom_up *` copies a
#     template to many instances and FREEZES a single-instance cell as a
#     keepout with nothing to copy.  Measured, `soc.tcl 2 -census`:
#
#       sram_cell 20   tag_cell 9   fifo_cell 8
#       alu_cell 4   dec_cell 4   io_cell 4   mul_cell 4   regf_cell 4
#       xbar_cell 4                                  <- one per router
#       bridge_cell 1   memctl_cell 1                <- the singletons
#
#     ELEVEN types with TWO singletons, which is what the census says and is
#     narrower than the claim this said first: "most of them appearing ONCE",
#     with `xbar_cell` named as one of them when it has an instance per
#     router (Codex P2, #930).  `leaf_census` derives from the same argument
#     `_fill` builds the instances from, so the sentence above is now a
#     measurement and a test pins it.
#   * RAGGED DEPTH.  Here a leaf sits 2, 3 or 4 levels down depending on
#     which subsystem it is in — an ALU is inside a core inside a cluster
#     inside a quadrant, while a UART is one level under the top.  That is
#     what `set_layer_caps_by_depth` and `reserve_top_layers` are ABOUT
#     (a cell's band follows how deep its own content goes), and with a
#     uniform-depth vehicle their per-level behaviour collapses to one case.
#
# GENERATED, for `tpu_lib.tcl`'s reason and with its honest cost: this
# exercises the ENGINE, not the reader.  What is different here is that the
# generation is not forced — the mesh had to be generated because repetition
# does not survive synthesis, while a DIVERSE hierarchy survives it fine.
# It is generated because SIZE MUST BE A DIAL: the crossover the LibreLane
# study measures needs a curve, and a real SoC gives one point.
# An imported twin of this shape under `flow/librelane/` is the natural next
# tier and does NOT exist yet; this file is the generated half.
#
# THE SHAPE.  Cells nest four deep on one branch and two on another:
#
#   soc
#   |- quad_<q>            x NQ        \
#   |   `- cl_<c>          x NC         | the deep branch: a leaf ALU is at
#   |       |- core                     | component depth 3
#   |       |   |- dec  alu  mul  regf  /
#   |       |- l1i, l1d   -> sram_<b> x NBANK, tag
#   |       `- rtr        -> xbar, fifo x2
#   |- l2                  x 1         -> sram_<b> x NBANK2, tag, memctl
#   `- io                  x 1         -> p_<k> x NIO, bridge
#
# so `sram_cell` and `cluster_cell` REPEAT (the bottom-up family has
# something to work on) while `l2_cell`, `io_blk_cell`, `memctl_cell` and
# `bridge_cell` appear ONCE (the top-down path does).  Both in one design is
# the point: a real SoC is neither all array nor all singleton.
# ============================================================

namespace eval soc_vehicle {
    variable P
    # NOTE: no `;#` comments inside this list.  Tcl does not treat `#` as a
    # comment inside braces, so each one would become KEY/VALUE ELEMENTS and
    # every parameter after an odd-word comment would silently shift -- which
    # is exactly what happened here once, leaving `GAP` undefined and `at`,
    # `the` and `DIAL` as accepted knob names.  Comments go above the list.
    #
    #   NQ/NC              quadrants at the top (THE DIAL) and clusters in one
    #   NBANK/NBANK2       sram banks per L1 cache / in the L2
    #   NIO                peripherals in the io block
    #   DW/AW/IW/CW        data / address / instruction / control bus widths
    #   BITPITCH           TOP-layer bit pitch of the declared stack
    #   PAD                slack added to every derived leaf dimension
    #   M/GAP              margin inside a cell / gap between siblings --
    #                      constants, deliberately NOT derived (see configure)
    array set P {
        NQ       2      NC       2
        NBANK    2      NBANK2   4
        NIO      4
        DW      32      AW      16
        IW      32      CW       8
        BITPITCH 4.0    PAD     24
        M       16      GAP     16
    }
}

# ── parameters ────────────────────────────────────────────────────────────
# Every derived size is computed HERE, bottom-up, so one consistent set
# reaches the hierarchy, the buses and the die (tpu_lib's rule).  A leaf's
# size is DERIVED from the bits that land on its faces, which is the lesson
# `tpu.tcl` paid for: a block narrower than its own bus strands the bits at
# DNUTS, and widening the CHANNEL does not help because the channel was
# never the binding constraint.
proc soc_vehicle::configure {{overrides {}}} {
    variable P
    foreach {k v} $overrides {
        if {![info exists P($k)]} { error "soc_vehicle: unknown parameter '$k'" }
        set P($k) $v
    }
    # Every knob here is a COUNT -- of quadrants, clusters, banks, pads, or
    # of BITS.  Zero is the one value that passes every later check while
    # deleting the thing it counts: `-DW 0` names a bus `m_0_0[0]`, `add_bus`
    # expands `[N]` over `0..N-1` and so creates no net, `check_bus_faces`
    # accepts a zero contribution, and `describe` still counts the
    # declaration -- so the sweep reports a clean design over a datapath that
    # is not there (Codex P2, #930).  A width is refused for the same reason
    # a multiplicity is.
    foreach k {NQ NC NBANK NBANK2 NIO DW AW IW CW} {
        if {![string is integer -strict $P($k)] || $P($k) < 1} {
            error "soc_vehicle: $k must be an integer >= 1 (got '$P($k)')"
        }
    }

    # ── the CHANNEL: a constant, and that is the finding ──────────────────
    # A face IS derived from the bits that land on it (below).  A CHANNEL is
    # not -- and this vehicle has now paid for the difference THREE times,
    # each time with the same shape: a symptom that reads as congestion, a
    # channel that measurably moves it, and a FACE underneath.
    #
    # The first cut derived every face and left gaps flat, and
    # `check_design` reported a supply-doomed seat -- 19 signal tracks in a
    # 32-bit bus's placed window.  A seat's window is a Hanan band, i.e. the
    # gap between two blocks, so the reading was that a gap must host a whole
    # bus (~136 units at the M6 per-bit channel).  Deriving it that way DOES
    # clear the symptom.  It is still the wrong fix, and only measurement
    # says so: the real cause was a STAR on the memory controller (see
    # `build_buses`), and with the NoC chain in its place a wider channel
    # buys NO routing at all and costs wire, monotonically --
    #
    #   GAP=M   NQ=8 detailed WL     NQ=16 detailed WL
    #    16         1,968,672            3,935,746
    #    48         2,826,579            5,553,364
    #    96         4,024,854            8,034,294
    #   144         5,290,204           10,561,015     (every row CLEAN)
    #
    # -- which is `tpu.tcl`'s recorded lesson in the direction it recorded
    # it: the channel was never the binding constraint, so widening it only
    # inflates the die and makes every wire longer.
    #
    # WHERE THE DESIGN USED TO FAIL, a channel was not the lever either, and
    # this is the part that had to be re-run.  Swept at DW=128 (a 4x
    # datapath), NQ=4, the table here read 65 bits unplaced at GAP 16 and
    # never reached zero at any width tried (46 at 96; NQ=1 ran the same
    # way, 65 down to 31 at GAP 160), and concluded the bits were CULLED FOR
    # CROSSING A KEEPOUT on one cross-level NoC leg.  The advice was right
    # and the CAUSE was wrong: three stars were still in the netlist
    # (`tag.d_in`, `mc.d_in`, `xbar.p_in` -- see the SUM note below), and
    # with those faces sized from what lands on them `-DW 128` is CLEAN at
    # every gap, with the channel still pure cost:
    #
    #   GAP=M (NQ=4, DW=128)  16          32          64          96
    #   detailed WL        9,253,086   9,865,420  11,400,415  12,698,056
    #                                                     (every row CLEAN)
    #
    # -- `-DW` ALONE, since `IW` sizes `dec_cell` and every cell enclosing it
    # and would make this a different design from the one written down.
    #
    # So GAP and M are plain constants across the whole NQ dial AND across a
    # 4x datapath, which is a stronger claim than the one it replaced -- and
    # it was reached by removing faces, never by tuning a gap.
    #
    # These numbers are re-runnable and have now been re-run TWICE, which is
    # the durable part of the lesson.  An earlier sweep here read as
    # NON-monotone (clean at GAP 40/48/80, stranded at the rest); a second
    # healer round added to `heal_if_dirty` changed the answer at every
    # point; and the star fix then turned a whole failing table CLEAN.  A
    # recorded measurement nothing re-runs decays into a claim, so
    # `test_a_wider_channel_is_not_the_lever_a_wider_bus_needs` runs the
    # cheap end of both directions on every test run.

    # ── leaf cells: (bits landing on a vertical face, on a horizontal face)
    # A vertical (north/south) face has to host the bits arriving from above
    # or below, so it constrains WIDTH; a horizontal face constrains HEIGHT.
    #
    # The rule has to hold for EVERY endpoint of a bus, not just the one
    # whose knob names it, and it has to be read on the SUM at each pin, not
    # one bus at a time.  This table broke both readings in turn.
    #
    # PER BUS first: `-IW` grew `dec_cell` and every container above it while
    # both OTHER ends of the IW buses stayed sized from `DW` -- `sram_cell`
    # drives `l1id_*[IW]` out of each `l1i` bank, `alu_cell` receives
    # `i_[IW]` -- and `xbar_cell` was `2*DW` on both axes while `nr_[AW]`
    # joins two of them directly and `pc_[CW]` arrives from an io pad
    # (Codex P2 x2, #930).  The `sram_cell` citation here read `id_[IW]`
    # until the bank wiring below landed: `id_` now leaves `l1i/tag.d_out`,
    # so the IW dependency on a bank runs through the bank-to-tag read bus
    # and the sentence named a bus that no longer touches an SRAM (Codex P2
    # again) -- the SIZE was right, the traced dependency was not, which is
    # the half a future face change reads.
    #
    # PER PIN second, and it is the one that mattered: a pin is ONE PLACE, so
    # what has to fit there is every bit that lands on it.  Wiring the banks
    # (each `NBANK` bank drives one `tag.d_in`, each `NBANK2` bank one
    # `mc.d_in`, every peripheral of a cluster one `xbar.p_in`) re-created
    # the STAR of `build_buses` 3b in three more places -- and each of those
    # buses passed a per-bus check.  `check_bus_faces` accumulates per
    # ENDPOINT PATH now, so the guard sees what the netlist actually asks of
    # a face.
    #
    # What it cost to get wrong was never the sizes -- it was a CAUSAL CLAIM,
    # made twice.  `-AW 128` was first reported as 128 unplaced on a
    # SUPPLY-DOOMED seat and called "the channel from the other side"; then
    # `-DW`/`-CW` were reported as a keepout cull and a dead span "not
    # diagnosed further here".  Every one of them was a face:
    #
    #   knob at 128    first cut    per-bus max    per-pin SUM
    #     -IW             512          clean          clean
    #     -AW             128          clean          clean
    #     -DW              65            65           clean
    #     -CW             741           166           clean
    #
    # (NQ=1, each knob alone; detailed WL now 887,223 / 1,365,880 /
    # 1,745,712 / 2,265,230, every one smaller than before the phantom
    # coefficients came out.)  A symptom the tool reports is not a cause:
    # the advisory named the seat every time and never once the reason for
    # it, and the two readings that sounded most like physics -- a keepout
    # cull, a dead span -- were the two that survived longest.

    # And the MIRROR of the face rule, which cost a fourth reading: a knob
    # may appear in a cell's size ONLY IF some bus of that width lands on
    # that cell.  Three terms broke it -- `dec_cell`'s `2*CW`, `tagpin`'s
    # `CW`, `bridge_cell`'s `DW` -- all PHANTOM dependencies, a knob sizing a
    # cell no bus of that knob's width ever touches (Codex P2, #930).  Only
    # the first was live: `-CW 128` grew the whole core/cluster stack for
    # nothing (die 4576x5600 against 4320x4704), so that experiment was
    # measuring unrelated whitespace and could credit a clean route to the
    # wrong geometry; the other two were dominated at the defaults and bind
    # at `NBANK`/`NIO` 1.  The defaults are UNCHANGED by their removal,
    # which is exactly why reading the table never found them.
    #
    # `test_no_cell_is_sized_from_a_knob_no_bus_brings_it` is the guard, and
    # it is the one test here that exists because a HAND audit found this
    # class twice running: it perturbs each knob and diffs the sizes (no
    # parsing), resolves each recorded bus endpoint through this file's own
    # `cell_at`, and requires depends ⊆ lands -- ONE REGIME PER KNOB, since
    # a term is invisible in any regime where its knob is not what binds.
    #
    # Two faces are sized by a SUM rather than by one bus, because a pin is
    # one place: every bank of a cache drives `tag.d_in`, every L2 bank
    # drives `mc.d_in`, and every peripheral of a cluster arrives at one
    # `xbar.p_in` — a star each, and the star is the mistake this vehicle has
    # now made three times (see 3b).  `NBANK`/`NBANK2`/`NIO` therefore grow
    # the cells they feed, which is what makes those knobs mean something.
    set tagpin [expr {$P(NBANK)*max($P(AW), $P(DW), $P(IW))}]
    set percl  [expr {int(ceil(double($P(NIO))/($P(NQ)*$P(NC))))}]

    variable LEAF
    array set LEAF [list \
        dec_cell    [list [expr {max($P(IW), $P(AW))}] \
                          [expr {max($P(IW), $P(AW))}]   ] \
        alu_cell    [list [expr {max($P(DW), $P(IW))}] \
                          [expr {max($P(DW), $P(IW))}]   ] \
        mul_cell    [list [expr {$P(DW)}]            [expr {$P(DW)}]       ] \
        regf_cell   [list [expr {max($P(DW), $P(AW))}] \
                          [expr {max($P(DW), $P(AW))}] ] \
        sram_cell   [list [expr {max($P(DW), $P(IW), $P(AW))}] \
                          [expr {max($P(DW), $P(IW), $P(AW))}] ] \
        tag_cell    [list [expr {$tagpin}]           [expr {$tagpin}]      ] \
        xbar_cell   [list [expr {max($P(DW), $P(AW), $percl*$P(CW))}] \
                          [expr {max($P(DW), $P(AW), $percl*$P(CW))}] ] \
        fifo_cell   [list [expr {2*$P(DW)}]          [expr {2*$P(DW)}]     ] \
        io_cell     [list [expr {$P(CW)}]            [expr {$P(CW)}]       ] \
        bridge_cell [list [expr {max($P(NIO)*$P(CW), $P(DW))}] \
                          [expr {max($P(NIO)*$P(CW), $P(DW))}] ] \
        memctl_cell [list [expr {$P(NBANK2)*max($P(DW), $P(AW))}] \
                          [expr {$P(NBANK2)*max($P(DW), $P(AW))}] ] \
    ]
    variable SZ
    array unset SZ
    foreach c [array names LEAF] {
        lassign $LEAF($c) vbits hbits
        set SZ($c) [list [_dim $vbits] [_dim $hbits]]
    }

    # ── container cells, bottom-up.  Each is a ROW of its children with a
    # margin all round; the row's own size is what the children need, so a
    # bigger DW grows every enclosing cell by construction.
    set SZ(core_cell)    [_pack {dec_cell alu_cell mul_cell regf_cell}]
    set SZ(l1_cell)      [_pack [concat tag_cell [lrepeat $P(NBANK)  sram_cell]]]
    set SZ(l2_cell)      [_pack [concat tag_cell memctl_cell \
                                       [lrepeat $P(NBANK2) sram_cell]]]
    set SZ(rtr_cell)     [_pack {fifo_cell xbar_cell fifo_cell}]
    set SZ(cluster_cell) [_pack {core_cell l1_cell l1_cell rtr_cell}]
    set SZ(quad_cell)    [_pack [lrepeat $P(NC) cluster_cell]]
    set SZ(io_blk_cell)  [_pack [concat bridge_cell [lrepeat $P(NIO) io_cell]]]

    # ── the die: the quadrants in a row, the l2 and the io block below.
    set top [_pack [lrepeat $P(NQ) quad_cell]]
    set bot [_pack {l2_cell io_blk_cell}]
    lassign $top tw th
    lassign $bot bw bh
    set P(DIEW) [expr {max($tw,$bw) + 2*$P(M)}]
    set P(DIEH) [expr {$th + $bh + 3*$P(M)}]
    set P(TOPW) $tw ; set P(TOPH) $th
    set P(BOTW) $bw ; set P(BOTH) $bh
}

# A derived leaf dimension: the bits that land on the face, at the stack's
# bit pitch, plus slack.  Integer, because a block bbox is one.
proc soc_vehicle::_dim {bits} {
    variable P
    return [expr {int(ceil($bits*$P(BITPITCH))) + $P(PAD)}]
}

# ── packing ───────────────────────────────────────────────────────────────
# Children are packed into a roughly-SQUARE grid (`ceil(sqrt(n))` columns,
# row-major), not a row.  The aspect ratio is not cosmetic here: a row at
# every level compounds, and the first cut of this vehicle came out 10512 x
# 672 — a 16:1 sliver whose every bus runs the long way, which would measure
# the shape of the packer rather than the shape of the design.  Column
# widths and row heights are the max of what lands in them, so children of
# different sizes (which is the whole point of this vehicle) still tile.
proc soc_vehicle::_cols {n} { return [expr {int(ceil(sqrt(double($n))))}] }

# {w h} of a cell holding `cells`, packed.
proc soc_vehicle::_pack {cells} {
    variable P
    lassign [_pack_geom $cells] w h _xs _ys
    return [list $w $h]
}

# The {x y} offsets of each child inside the parent, in order.
proc soc_vehicle::_pack_pos {cells} {
    variable P
    lassign [_pack_geom $cells] _w _h xs ys
    set out {}
    foreach x $xs y $ys { lappend out [list $x $y] }
    return $out
}

# The shared walk: column widths, row heights, then the offsets.  ONE
# function so a cell's declared size and where its children actually land
# cannot disagree — a mismatch there is a block outside its own parent, and
# the projection would route against one shape and place against another.
proc soc_vehicle::_pack_geom {cells} {
    variable P
    variable SZ
    set n [llength $cells]
    set nc [_cols $n]
    set nr [expr {int(ceil(double($n)/$nc))}]

    for {set c 0} {$c < $nc} {incr c} { set cw($c) 0 }
    for {set r 0} {$r < $nr} {incr r} { set rh($r) 0 }
    set i 0
    foreach c $cells {
        set col [expr {$i % $nc}] ; set row [expr {$i / $nc}]
        lassign $SZ($c) w h
        if {$w > $cw($col)} { set cw($col) $w }
        if {$h > $rh($row)} { set rh($row) $h }
        incr i
    }

    set xoff {} ; set x $P(M)
    for {set c 0} {$c < $nc} {incr c} {
        lappend xoff $x
        set x [expr {$x + $cw($c) + $P(GAP)}]
    }
    set yoff {} ; set y $P(M)
    for {set r 0} {$r < $nr} {incr r} {
        lappend yoff $y
        set y [expr {$y + $rh($r) + $P(GAP)}]
    }
    set tw [expr {$x - $P(GAP) + $P(M)}]
    set th [expr {$y - $P(GAP) + $P(M)}]

    # Each child CENTRED in its cell of the grid, so a short or narrow one
    # does not sit on its parent's own face.
    set xs {} ; set ys {} ; set i 0
    foreach c $cells {
        set col [expr {$i % $nc}] ; set row [expr {$i / $nc}]
        lassign $SZ($c) w h
        lappend xs [expr {[lindex $xoff $col] + ($cw($col) - $w)/2}]
        lappend ys [expr {[lindex $yoff $row] + ($rh($row) - $h)/2}]
        incr i
    }
    return [list $tw $th $xs $ys]
}

proc soc_vehicle::get {k} {
    variable P
    if {![info exists P($k)]} { error "soc_vehicle: no parameter '$k'" }
    return $P($k)
}

proc soc_vehicle::size {c} {
    variable SZ
    if {![info exists SZ($c)]} { error "soc_vehicle: no cell '$c'" }
    return $SZ($c)
}

# ── the metal stack ───────────────────────────────────────────────────────
# The same six-layer stack tpu_lib declares, for the same reason: a vehicle
# that also changed the technology would confound the shape it exists to
# measure.  Declared by EVERY session, a resuming one included.
proc soc_vehicle::declare_stack {} {
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
    buda::set_planner_param healersAhead 1
}

# ── hierarchy ─────────────────────────────────────────────────────────────
proc soc_vehicle::build_hierarchy {} {
    variable P
    variable SZ
    variable KIDS
    variable CELLOF
    array unset KIDS
    array unset CELLOF

    buda::set_die $P(DIEW) $P(DIEH)

    # every cell type, leaves first
    foreach c [lsort [array names SZ]] {
        lassign $SZ($c) w h
        buda::add_cell $c $w $h
    }

    _fill core_cell    {dec_cell alu_cell mul_cell regf_cell} {dec alu mul regf}
    _fill rtr_cell     {fifo_cell xbar_cell fifo_cell}        {fi_in xbar fi_out}

    set l1cells [concat tag_cell [lrepeat $P(NBANK) sram_cell]]
    set l1names {tag}
    for {set b 0} {$b < $P(NBANK)} {incr b} { lappend l1names bank_$b }
    _fill l1_cell $l1cells $l1names

    set l2cells [concat tag_cell memctl_cell [lrepeat $P(NBANK2) sram_cell]]
    set l2names {tag mc}
    for {set b 0} {$b < $P(NBANK2)} {incr b} { lappend l2names bank_$b }
    _fill l2_cell $l2cells $l2names

    set iocells [concat bridge_cell [lrepeat $P(NIO) io_cell]]
    set ionames {bridge}
    for {set k 0} {$k < $P(NIO)} {incr k} { lappend ionames p_$k }
    _fill io_blk_cell $iocells $ionames

    _fill cluster_cell {core_cell l1_cell l1_cell rtr_cell} {core l1i l1d rtr}

    set qc [lrepeat $P(NC) cluster_cell] ; set qn {}
    for {set c 0} {$c < $P(NC)} {incr c} { lappend qn cl_$c }
    _fill quad_cell $qc $qn

    # the top: the quadrants packed, the l2 and io block in a band below.
    set qpos [_pack_pos [lrepeat $P(NQ) quad_cell]]
    # The TOP instances, as the same {name cell} pairs `_fill` records for
    # every other level, appended where each one is actually instantiated.
    variable TOPKIDS
    set TOPKIDS {}
    for {set q 0} {$q < $P(NQ)} {incr q} {
        lassign [lindex $qpos $q] x y
        buda::add_inst quad_$q quad_cell - $x $y
        lappend TOPKIDS [list quad_$q quad_cell]
    }
    set by [expr {$P(M) + $P(TOPH) + $P(M)}]
    set bpos [_pack_pos {l2_cell io_blk_cell}]
    lassign [lindex $bpos 0] lx ly
    lassign [lindex $bpos 1] ix iy
    buda::add_inst l2 l2_cell     - $lx [expr {$by + $ly}]
    lappend TOPKIDS [list l2 l2_cell]
    buda::add_inst io io_blk_cell - $ix [expr {$by + $iy}]
    lappend TOPKIDS [list io io_blk_cell]
}

# Instantiate `names` (cells `cells`) inside `parent`, in the same packed
# grid `_pack` sized the parent from — one walk, so the declared size and
# where the children land cannot disagree.
proc soc_vehicle::_fill {parent cells names} {
    variable KIDS
    variable CELLOF
    # Recorded from the SAME arguments the instances are built from, so
    # `leaf_paths` and `leaf_census` report the design rather than a second
    # model of it — the claim about this vehicle's diversity is a measurement
    # (see the header), and a hand-kept twin of the structure is how such a
    # claim goes quietly false.
    #
    # ONE list of {name cell} PAIRS rather than two parallel lists, because a
    # `foreach n $KIDNAMES c $KIDS` over two arrays truncates silently to the
    # shorter: nothing would report the desync.
    foreach c $cells n $names {
        lappend KIDS($parent) [list $n $c]
        set CELLOF($parent,$n) $c
    }
    set pos [_pack_pos $cells]
    foreach c $cells n $names xy $pos {
        lassign $xy x y
        buda::add_inst_to_cell $parent $n $c $x $y
    }
}

proc soc_vehicle::cell_at {path} {
    # `quad_0/cl_0/core/dec` -> `dec_cell`, walked through the same name->cell
    # pairs `_fill` built the instances from.  Top-level instances are named
    # `quad_<q>`, `l2` and `io`.
    variable CELLOF
    set parts [split [lindex [split $path .] 0] /]
    set head [lindex $parts 0]
    if {[string match "quad_*" $head]} {
        set cell quad_cell
    } elseif {$head eq "l2"} {
        set cell l2_cell
    } elseif {$head eq "io"} {
        set cell io_blk_cell
    } else {
        return ""
    }
    foreach n [lrange $parts 1 end] {
        if {![info exists CELLOF($cell,$n)]} { return "" }
        set cell $CELLOF($cell,$n)
    }
    return $cell
}

proc soc_vehicle::check_bus_faces {bits args} {
    # The face rule, ENFORCED rather than asserted, and enforced on the SUM.
    # A pin is one place on one face, so what has to fit there is every bit
    # that lands on it — not the widest bus taken alone.  Checking one bus at
    # a time is what let the STAR through three times (memctl in 3b, and then
    # `tag.d_in`, which every bank of a banked cache drives): each bus fits,
    # the pin does not, and the tool reports a supply-doomed seat somewhere
    # downstream rather than the face that caused it.  So bits accumulate per
    # ENDPOINT PATH and the cell must host the running total on both faces
    # (both, because which face a bus arrives on is the placer's business).
    #
    # The sizes come from a declared table and the buses from `build_buses`,
    # two places that can drift — this is what stops them, and it is how the
    # `-IW`/`-AW`/`xbar_cell` misses (Codex, #930) would have been caught at
    # declaration instead of by a reviewer.
    variable SZ
    variable ACC
    foreach path $args {
        set cell [cell_at $path]
        if {$cell eq ""} {
            # A guard that cannot resolve its endpoint checks NOTHING, and
            # says so rather than passing.  This is the same silent-skip
            # shape the guard exists to remove.
            error "soc_vehicle: check_bus_faces cannot resolve '$path' to a\
                   cell -- run build_hierarchy first, or fix the path"
        }
        if {![info exists SZ($cell)]} { continue }
        if {![info exists ACC($path)]} { set ACC($path) 0 }
        incr ACC($path) $bits
        lassign $SZ($cell) w h
        set need [_dim $ACC($path)]
        if {$w < $need || $h < $need} {
            error "soc_vehicle: $cell is ${w}x${h} but $ACC($path) bits land\
                   on it at $path (this bus contributes ${bits}; the pin\
                   needs ${need}); widen its entry in LEAF"
        }
    }
}

proc soc_vehicle::leaf_paths {} {
    # Every LEAF INSTANCE's full path (`quad_0/cl_0/l1i/tag`, `l2/bank_3`),
    # walked from the top through the same name->cell pairs `_fill` recorded.
    # A cell with no recorded children is a leaf.  Requires
    # `build_hierarchy` to have run.
    #
    # This exists because an aggregate by cell TYPE cannot answer a question
    # about an OCCURRENCE.  `l2/tag` was instantiated and wired by nothing,
    # and the guard meant to catch exactly that reduced the design to types
    # before comparing -- so the four live L1 tags answered for `tag_cell`
    # and the dead one passed behind them (Codex P2, #930).  The same shape
    # hid the unwired SRAM banks one round earlier.  `leaf_census` is now
    # DERIVED from this list, so the census and the paths are one walk and
    # cannot disagree.
    variable KIDS
    variable TOPKIDS
    set out {}
    set stack {}
    foreach nc $TOPKIDS { lassign $nc n c ; lappend stack [list $c $n] }
    while {[llength $stack]} {
        set item [lindex $stack end]
        set stack [lrange $stack 0 end-1]
        lassign $item cell path
        if {![info exists KIDS($cell)]} { lappend out $path ; continue }
        foreach nc $KIDS($cell) {
            lassign $nc n c
            lappend stack [list $c $path/$n]
        }
    }
    return [lsort $out]
}

proc soc_vehicle::leaf_census {} {
    # cell type -> how many INSTANCES of it the built design holds.  Derived
    # from `leaf_paths` so there is ONE walk of the hierarchy.
    set n [dict create]
    foreach path [leaf_paths] { dict incr n [cell_at $path] }
    return $n
}

proc soc_vehicle::derive_interface {} { buda::derive_busterms 4 }

proc soc_vehicle::load_blocks {} {
    for {set d 0} {$d <= 3} {incr d} {
        if {$d == 0} { buda::add_blocks_from_bdb 0 } \
                else { buda::add_blocks_from_bdb $d skip }
    }
}

# ── the buses ─────────────────────────────────────────────────────────────
# Four families, and the split is the point (tpu_lib's rule): each family
# lands at a DIFFERENT level, so one design carries cell-local templates at
# two nesting depths AND cross-level bundles spanning up to four.
proc soc_vehicle::build_buses {} {
    variable P
    variable ACC
    array unset ACC
    buda::bdb_net_mode on

    for {set q 0} {$q < $P(NQ)} {incr q} {
        for {set c 0} {$c < $P(NC)} {incr c} {
            set cl quad_$q/cl_$c

            # 1. INSIDE a core -- the deepest cell-local template.  Solved
            #    once per `core_cell` and copied to every occurrence.
            buda::add_bus "i_${q}_${c}\[$P(IW)\]" $cl/core/dec.out $cl/core/alu.i_in
            check_bus_faces $P(IW) $cl/core/dec.out $cl/core/alu.i_in
            buda::add_bus "m_${q}_${c}\[$P(DW)\]" $cl/core/mul.out $cl/core/regf.m_in
            check_bus_faces $P(DW) $cl/core/mul.out $cl/core/regf.m_in
            buda::add_bus "r_${q}_${c}\[$P(DW)\]" $cl/core/regf.out $cl/core/alu.r_in
            check_bus_faces $P(DW) $cl/core/regf.out $cl/core/alu.r_in
            buda::add_bus "x_${q}_${c}\[$P(DW)\]" $cl/core/alu.out $cl/core/regf.a_in
            check_bus_faces $P(DW) $cl/core/alu.out $cl/core/regf.a_in

            # 2. INSIDE a cluster, core <-> its two caches -- a cell-local
            #    template one level UP, whose own children are templates.
            #
            #    EVERY bank is wired, which it was not: only `bank_0` was
            #    ever named, so at the defaults 11 of the 20 `sram_cell`
            #    instances carried no net -- `NBANK`/`NBANK2` added filler
            #    geometry and the census reported it as workload (Codex P2,
            #    #930).  A banked cache addresses each bank from its tag and
            #    reads each bank back, so that is what this declares: the
            #    address bus is CELL-LOCAL to `l1_cell` (a template at a
            #    level the vehicle had none at, solved once and copied to
            #    every l1i/l1d of every cluster), and each bank reads back
            #    through the tag -- through it, because wiring the banks
            #    STRAIGHT to the core is 3b's star again, and that is what
            #    the first cut of this did.
            buda::add_bus "ia_${q}_${c}\[$P(AW)\]" $cl/core/dec.a_out $cl/l1i/tag.a_in
            check_bus_faces $P(AW) $cl/core/dec.a_out $cl/l1i/tag.a_in
            buda::add_bus "da_${q}_${c}\[$P(AW)\]" $cl/core/regf.a_out $cl/l1d/tag.a_in
            check_bus_faces $P(AW) $cl/core/regf.a_out $cl/l1d/tag.a_in
            foreach {side dw} [list l1i $P(IW) l1d $P(DW)] {
                for {set b 0} {$b < $P(NBANK)} {incr b} {
                    buda::add_bus "${side}a_${q}_${c}_${b}\[$P(AW)\]" \
                        $cl/$side/tag.b_out $cl/$side/bank_$b.a_in
                    check_bus_faces $P(AW) \
                        $cl/$side/tag.b_out $cl/$side/bank_$b.a_in
                    buda::add_bus "${side}d_${q}_${c}_${b}\[$dw\]" \
                        $cl/$side/bank_$b.out $cl/$side/tag.d_in
                    check_bus_faces $dw \
                        $cl/$side/bank_$b.out $cl/$side/tag.d_in
                }
            }
            buda::add_bus "id_${q}_${c}\[$P(IW)\]" $cl/l1i/tag.d_out $cl/core/dec.i_in
            check_bus_faces $P(IW) $cl/l1i/tag.d_out $cl/core/dec.i_in
            buda::add_bus "dd_${q}_${c}\[$P(DW)\]" $cl/l1d/tag.d_out $cl/core/regf.d_in
            check_bus_faces $P(DW) $cl/l1d/tag.d_out $cl/core/regf.d_in

            # 3. cluster -> its own router: cell-local again, one level up.
            buda::add_bus "rq_${q}_${c}\[$P(DW)\]" $cl/l1d/tag.out $cl/rtr/xbar.in
            check_bus_faces $P(DW) $cl/l1d/tag.out $cl/rtr/xbar.in
        }
    }

    # 3b. the NoC: router to router along a chain through every cluster, and
    #     the HEAD of the chain to the L2.  The obvious wiring is a STAR --
    #     every cluster straight to `l2/mc` -- and it is what the first cut
    #     did.  It is wrong twice over, and the vehicle said so at NQ=4:
    #     72 bits unplaced with four bundles committing on planner overflow
    #     and NO supply-doomed seat, i.e. real congestion rather than a
    #     sizing fault.  Physically a memory controller is arbitrated, not
    #     wired to eight masters in parallel, and `memctl_cell`'s face is
    #     sized for ONE bus -- so a star puts NQ*NC*DW bits on a face built
    #     for DW, which is `tpu.tcl`'s face lesson in a third place.
    #
    #     A chain is the realistic fix and the more interesting route: one
    #     bus reaches the L2 whatever the dial says, and every hop between
    #     two clusters in DIFFERENT quadrants is a cross-level bundle.
    set chain {}
    for {set q 0} {$q < $P(NQ)} {incr q} {
        for {set c 0} {$c < $P(NC)} {incr c} { lappend chain quad_$q/cl_$c }
    }
    for {set i 0} {$i < [llength $chain] - 1} {incr i} {
        set a [lindex $chain $i] ; set b [lindex $chain [expr {$i+1}]]
        buda::add_bus "nl_${i}\[$P(DW)\]" $a/rtr/fi_out.out $b/rtr/fi_in.in
        check_bus_faces $P(DW) $a/rtr/fi_out.out $b/rtr/fi_in.in
        buda::add_bus "nr_${i}\[$P(AW)\]" $b/rtr/xbar.r_out $a/rtr/xbar.r_in
        check_bus_faces $P(AW) $b/rtr/xbar.r_out $a/rtr/xbar.r_in
    }
    set head [lindex $chain end]
    buda::add_bus "ml\[$P(DW)\]" $head/rtr/fi_out.out l2/mc.in
    check_bus_faces $P(DW) $head/rtr/fi_out.out l2/mc.in
    #     Every L2 bank is wired for the reason above: the controller
    #     addresses each one and each one reads back to the chain head.
    for {set b 0} {$b < $P(NBANK2)} {incr b} {
        buda::add_bus "l2a_${b}\[$P(AW)\]" l2/mc.b_out l2/bank_$b.a_in
        check_bus_faces $P(AW) l2/mc.b_out l2/bank_$b.a_in
        buda::add_bus "l2d_${b}\[$P(DW)\]" l2/bank_$b.out l2/mc.d_in
        check_bus_faces $P(DW) l2/bank_$b.out l2/mc.d_in
    }
    buda::add_bus "mr\[$P(DW)\]" l2/mc.d_out $head/rtr/fi_in.in
    check_bus_faces $P(DW) l2/mc.d_out $head/rtr/fi_in.in
    #     The L2 TAG is wired for the same reason, and was found the same
    #     way one level further in: `l2/tag` was instantiated and named by
    #     no bus at all, so a tag array sat in the L2 footprint, in the
    #     census and in the die with nothing on it (Codex P2, #930).  It is
    #     the bank fault again, and what HID it is worth more than the fix:
    #     the guard that was supposed to catch exactly this reduced the
    #     design to cell TYPES before comparing, so the four wired L1 tags
    #     answered for `tag_cell` and the dead L2 one passed behind them.
    #     An unwired INSTANCE is invisible to any check that aggregates by
    #     type, however exact that check is about the type.
    #
    #     A controller looks a line up before it reads a bank, so that is
    #     what this declares -- the address out to the tag, the tag's answer
    #     back.  The answer arrives on its OWN pin (`mc.t_in`, not
    #     `mc.d_in`): `d_in` already aggregates every bank, and a pin is one
    #     place (the SUM note in `configure`).  Both faces already hold it
    #     -- `NBANK2*max(DW,AW)` on the controller and `tagpin` on the tag
    #     are each >= AW for any legal dial -- so this wires a dead instance
    #     without moving a single size, which is the honest reading: the
    #     geometry was always paid for, only the workload was missing.
    buda::add_bus "l2ta\[$P(AW)\]" l2/mc.a_out l2/tag.a_in
    check_bus_faces $P(AW) l2/mc.a_out l2/tag.a_in
    buda::add_bus "l2tr\[$P(AW)\]" l2/tag.d_out l2/mc.t_in
    check_bus_faces $P(AW) l2/tag.d_out l2/mc.t_in
    #     And the OTHER end of the chain, found by the same guard in the same
    #     run: `nl_` wires hop i to hop i+1, so the FIRST hop's inbound fifo
    #     has nothing upstream of it and `chain[0]/rtr/fi_in` was a whole
    #     `fifo_cell` instance carrying no net -- at every size, since there
    #     is exactly one chain start.  A peripheral bridge is a NoC master,
    #     so it injects at the head of the chain, which is both the physical
    #     answer and the one that costs one bus.
    #
    #     NOT closed into a RING (`mr` back to `chain[0]`), which was the
    #     first idea and the cheaper-looking one: that leaves every
    #     `fi_in.in` with exactly ONE bus, which would make `fifo_cell`'s
    #     `2*DW` phantom -- and that coefficient is the vehicle's one
    #     MEASURED-honest multiplicity (the chain head takes `nl_` AND `mr`).
    #     A fix that wires a dead instance by deleting the aggregation
    #     another face is sized from trades one fault for the other.
    set start [lindex $chain 0]
    buda::add_bus "pn\[$P(DW)\]" io/bridge.n_out $start/rtr/fi_in.in
    check_bus_faces $P(DW) io/bridge.n_out $start/rtr/fi_in.in

    # 4. the peripherals: shallow (depth 1) reaching a router four levels
    #    down -- the widest level span in the design.
    for {set k 0} {$k < $P(NIO)} {incr k} {
        set q [expr {$k % $P(NQ)}]
        set c [expr {($k / $P(NQ)) % $P(NC)}]
        buda::add_bus "pc_${k}\[$P(CW)\]" io/p_$k.out \
            quad_$q/cl_$c/rtr/xbar.p_in
        check_bus_faces $P(CW) io/p_$k.out quad_$q/cl_$c/rtr/xbar.p_in
        buda::add_bus "pb_${k}\[$P(CW)\]" io/bridge.out io/p_$k.in
        check_bus_faces $P(CW) io/bridge.out io/p_$k.in
    }
}

# ── size, for the banner and for the tests ────────────────────────────────
proc soc_vehicle::describe {} {
    variable P
    set cl [expr {$P(NQ)*$P(NC)}]
    set leaves [expr {$cl*(4 + 2*(1+$P(NBANK)) + 3)
                      + 2 + $P(NBANK2) + 1 + $P(NIO)}]
    #   per cluster 9 + 4*NBANK, one pair per NoC hop, then the L2 (ml, mr
    #   and the two tag-lookup buses), the bridge's injection at the chain
    #   start, the L2 banks, and two per peripheral.
    set buses  [expr {$cl*(9 + 4*$P(NBANK)) + 2*($cl-1)
                      + 4 + 1 + 2*$P(NBANK2) + 2*$P(NIO)}]
    return [list clusters $cl leaves $leaves buses $buses \
                 die "$P(DIEW)x$P(DIEH)"]
}

proc soc_vehicle::banner {what} {
    variable P
    array set d [describe]
    puts "=== $what: NQ=$P(NQ) NC=$P(NC) -- $d(clusters) clusters,\
          $d(leaves) leaf instances, $d(buses) buses, die $d(die) ==="
}

# ── verdict helpers (array_lib's rule: three legs, -1 is dirty) ───────────
proc soc_vehicle::is_dirty {} {
    return [expr {[buda::query overlaps] > 0 || [buda::query unplaced] > 0
                  || [buda::query violations] != 0}]
}

proc soc_vehicle::heal_if_dirty {who} {
    if {![soc_vehicle::is_dirty]} { return 0 }
    puts "$who: dirty ([buda::query overlaps] overlaps,\
          [buda::query unplaced] unplaced,\
          [buda::query violations] audit violations) -- healing"
    buda::negotiate_congestion 10
    buda::ripup_reroute 20
    buda::check_design dnuts
    if {![soc_vehicle::is_dirty]} { return 1 }

    # A SECOND round, composed the way a stuck endpoint wants it: the
    # healers' metric is lexicographic (opens, overlaps), so they drive the
    # opens to zero and stop on an overlap residue they cannot better.
    # `refine_selection` re-pins on the MEASURED result and changes the
    # contention geometry they stalled on, and negotiate re-plans its targets
    # unpinned so refine's pins do not block it.  Byte-identical no-op on a
    # residue it cannot improve.
    #
    # This used to add "-- which is why `-bottomup` widens the channel
    # instead", and that advice OUTLIVED its cause: the copy-induced overlaps
    # it pointed at were the STAR FACES, the flag's automatic `GAP 24 M 24`
    # is gone (soc.tcl), and at the default channel a bottom-up run is clean
    # through NQ=8.  A note left beside a healer saying a geometry knob
    # is the remedy will send the next investigation to tune geometry for a
    # failure that no longer exists (Codex P2, #930).  Where a fixed copy
    # DOES need a channel (NQ>=16 under `-bottomup`) the caller names it, and
    # the measured curve lives in soc.tcl beside the removal.
    puts "$who: still dirty ([buda::query overlaps] overlaps,\
          [buda::query unplaced] unplaced) -- second round"
    buda::refine_selection
    buda::negotiate_congestion 10
    buda::ripup_reroute 20
    buda::refine_selection
    buda::check_design dnuts
    return 1
}

proc soc_vehicle::verdict {who} {
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
