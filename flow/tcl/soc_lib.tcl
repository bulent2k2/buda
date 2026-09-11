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
#   * MANY DIFFERENT CELL TYPES, most of them appearing ONCE.  A mesh
#     measures solve-once-copy; it says nothing about a design where the
#     planner meets a new floorplan at every block.
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
# `flow/librelane/tier1c/` is the imported twin of this shape.
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
#   `- io                  x 1         -> io_<k> x NIO, bridge
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
    foreach {k min} {NQ 1 NC 1 NBANK 1 NBANK2 1 NIO 1} {
        if {$P($k) < $min} { error "soc_vehicle: $k must be >= $min (got $P($k))" }
    }

    # ── the CHANNEL: a constant, and that is the finding ──────────────────
    # A face IS derived from the bits that land on it (below).  A CHANNEL is
    # not, and the arc that establishes it is worth keeping because every
    # step of it looked right:
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
    #   GAP     NQ=8 detailed WL     NQ=16 detailed WL
    #    16         2,076,658            4,326,186
    #    48         2,475,780            5,047,839
    #    96         2,954,709            6,125,943
    #   144         3,469,229            7,137,372     (every row CLEAN)
    #
    # -- which is `tpu.tcl`'s recorded lesson in the direction it recorded
    # it: the channel was never the binding constraint, so widening it only
    # inflates the die and makes every wire longer.
    #
    # Where the design DOES fail, a channel is not the lever either.  Swept
    # at DW=128 (a 4x datapath), NQ=4, in bits left unplaced:
    #
    #   GAP        16  24  32  40  48  56  64  80  96
    #   unplaced   65  62  60  60  58  55  53  50  46
    #
    # Never zero at any width tried (NQ=1 runs the same way, 65 down to 31 at
    # GAP 160), and the bits are CULLED FOR CROSSING A KEEPOUT -- one
    # cross-level NoC leg, `<cluster>/rtr/fi_out -> l2/mc` -- which no gap
    # width addresses.  A wider gap shifts every block's track phase, so it
    # moves the count without feeding the constraint.  So GAP and M are
    # plain constants that work across the whole NQ dial at the default bus
    # widths, and a design changing DW far from the default sweeps `-GAP`
    # and MEASURES the result rather than trusting an arithmetic.
    #
    # These numbers are re-runnable and have been re-run, which is the last
    # part of the lesson: an earlier sweep here read as NON-monotone (clean
    # at GAP 40/48/80 and stranded at the rest), and a second healer round
    # added to `heal_if_dirty` afterwards changed the answer at every point.
    # A recorded measurement nothing re-runs decays into a claim, so
    # `test_a_wider_channel_is_not_the_lever_a_wider_bus_needs` runs the
    # cheap end of both directions on every test run.

    # ── leaf cells: (bits landing on a vertical face, on a horizontal face)
    # A vertical (north/south) face has to host the bits arriving from above
    # or below, so it constrains WIDTH; a horizontal face constrains HEIGHT.
    variable LEAF
    array set LEAF [list \
        dec_cell    [list [expr {$P(IW)}]            [expr {2*$P(CW)}]     ] \
        alu_cell    [list [expr {2*$P(DW)}]          [expr {$P(DW)}]       ] \
        mul_cell    [list [expr {2*$P(DW)}]          [expr {$P(DW)}]       ] \
        regf_cell   [list [expr {$P(DW)}]            [expr {2*$P(DW)}]     ] \
        sram_cell   [list [expr {$P(DW)}]            [expr {$P(AW)}]       ] \
        tag_cell    [list [expr {$P(AW)}]            [expr {$P(CW)}]       ] \
        xbar_cell   [list [expr {2*$P(DW)}]          [expr {2*$P(DW)}]     ] \
        fifo_cell   [list [expr {$P(DW)}]            [expr {$P(DW)}]       ] \
        io_cell     [list [expr {$P(CW)}]            [expr {$P(CW)}]       ] \
        bridge_cell [list [expr {2*$P(CW)}]          [expr {$P(DW)}]       ] \
        memctl_cell [list [expr {$P(DW) + $P(AW)}]   [expr {$P(DW)}]       ] \
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
    for {set q 0} {$q < $P(NQ)} {incr q} {
        lassign [lindex $qpos $q] x y
        buda::add_inst quad_$q quad_cell - $x $y
    }
    set by [expr {$P(M) + $P(TOPH) + $P(M)}]
    set bpos [_pack_pos {l2_cell io_blk_cell}]
    lassign [lindex $bpos 0] lx ly
    lassign [lindex $bpos 1] ix iy
    buda::add_inst l2 l2_cell     - $lx [expr {$by + $ly}]
    buda::add_inst io io_blk_cell - $ix [expr {$by + $iy}]
}

# Instantiate `names` (cells `cells`) inside `parent`, in the same packed
# grid `_pack` sized the parent from — one walk, so the declared size and
# where the children land cannot disagree.
proc soc_vehicle::_fill {parent cells names} {
    set pos [_pack_pos $cells]
    foreach c $cells n $names xy $pos {
        lassign $xy x y
        buda::add_inst_to_cell $parent $n $c $x $y
    }
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
    buda::bdb_net_mode on

    for {set q 0} {$q < $P(NQ)} {incr q} {
        for {set c 0} {$c < $P(NC)} {incr c} {
            set cl quad_$q/cl_$c

            # 1. INSIDE a core -- the deepest cell-local template.  Solved
            #    once per `core_cell` and copied to every occurrence.
            buda::add_bus "i_${q}_${c}\[$P(IW)\]" $cl/core/dec.out $cl/core/alu.i_in
            buda::add_bus "m_${q}_${c}\[$P(DW)\]" $cl/core/mul.out $cl/core/regf.m_in
            buda::add_bus "r_${q}_${c}\[$P(DW)\]" $cl/core/regf.out $cl/core/alu.r_in
            buda::add_bus "x_${q}_${c}\[$P(DW)\]" $cl/core/alu.out $cl/core/regf.a_in

            # 2. INSIDE a cluster, core <-> its two caches -- a cell-local
            #    template one level UP, whose own children are templates.
            buda::add_bus "ia_${q}_${c}\[$P(AW)\]" $cl/core/dec.a_out $cl/l1i/tag.a_in
            buda::add_bus "id_${q}_${c}\[$P(IW)\]" $cl/l1i/bank_0.out  $cl/core/dec.i_in
            buda::add_bus "da_${q}_${c}\[$P(AW)\]" $cl/core/regf.a_out $cl/l1d/tag.a_in
            buda::add_bus "dd_${q}_${c}\[$P(DW)\]" $cl/l1d/bank_0.out  $cl/core/regf.d_in

            # 3. cluster -> its own router: cell-local again, one level up.
            buda::add_bus "rq_${q}_${c}\[$P(DW)\]" $cl/l1d/tag.out $cl/rtr/xbar.in
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
        buda::add_bus "nr_${i}\[$P(AW)\]" $b/rtr/xbar.r_out $a/rtr/xbar.r_in
    }
    set head [lindex $chain end]
    buda::add_bus "ml\[$P(DW)\]" $head/rtr/fi_out.out l2/mc.in
    buda::add_bus "mr\[$P(DW)\]" l2/bank_0.out $head/rtr/fi_in.in

    # 4. the peripherals: shallow (depth 1) reaching a router four levels
    #    down -- the widest level span in the design.
    for {set k 0} {$k < $P(NIO)} {incr k} {
        set q [expr {$k % $P(NQ)}]
        set c [expr {($k / $P(NQ)) % $P(NC)}]
        buda::add_bus "pc_${k}\[$P(CW)\]" io/p_$k.out \
            quad_$q/cl_$c/rtr/xbar.p_in
        buda::add_bus "pb_${k}\[$P(CW)\]" io/bridge.out io/p_$k.in
    }
}

# ── size, for the banner and for the tests ────────────────────────────────
proc soc_vehicle::describe {} {
    variable P
    set cl [expr {$P(NQ)*$P(NC)}]
    set leaves [expr {$cl*(4 + 2*(1+$P(NBANK)) + 3)
                      + 2 + $P(NBANK2) + 1 + $P(NIO)}]
    set buses  [expr {$cl*9 + 2*($cl-1) + 2 + 2*$P(NIO)}]
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
    # residue it cannot improve -- measured here, where it does not rescue
    # the `-bottomup` overlaps, which is why that flag widens the channel
    # instead (see `soc.tcl`).
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
