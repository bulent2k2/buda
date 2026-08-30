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
# flow/tcl/tpu_lib.tcl — a TPU-shaped systolic array, parameterized.
#
# The corpus had no genuine ARRAY.  `flow/chip` is arrayed but assembled
# from heterogeneous cells; `flow/ariane133` is real but a CPU core; and
# neither is a mesh.  The gap matters because the whole bottom-up family
# (`set_bottom_up`, rotation classes, `align_bottom_up`,
# `check_template_tracks`, solve-once-copy) keys on MANY CONGRUENT INSTANCES
# OF ONE CELL, and nothing here had them at scale.
#
# WHY THIS IS GENERATED RATHER THAN IMPORTED.  The obvious move is to fetch
# a real ML accelerator netlist — NVDLA is available through the very
# channel `flow/ariane133` already uses.  Measured, that does not work: a
# synthesized netlist is UNIQUIFIED, so every replica is its own module and
# nothing is replicated at all.
#
#     netlist                       modules  hier-inst  types with >=2 inst
#     NV_NVDLA_partition_c.v            307        306                    0
#     ariane.v (imported today)         127        125                    0
#
# Raw parameterized RTL is no better: BUDA's `import_verilog` does not
# elaborate `generate`, so a 4-PE array imports as ONE instance with its
# neighbour links dropped (`BUDA-1610`), and says so only in a warning.
# So the array is COMPUTED here, which is what the Tcl front end is for.
# The honest cost: this exercises the ENGINE, not the READER — for reader
# coverage `flow/ariane133` remains the vehicle, precisely because it is
# somebody else's file.
#
# THE SHAPE, and why it maps onto the hier flow so cleanly:
#
#            wbuf_0  wbuf_1  ...  wbuf_{N-1}      <- north edge: weights in
#              |       |            |
#   feed_0 -> pe_0_0 -pe_0_1- ... -pe_0_{N-1}     } row_cell instance 0
#   feed_1 -> pe_1_0 -pe_1_1- ... -pe_1_{N-1}     } row_cell instance 1
#     ...        |       |            |
#              acc_0   acc_1  ...  acc_{N-1}      <- south edge: psums out
#                |       |            |
#              pipe stages (PIPE deep, PW wide)   <- the deep tail
#
#   * WEST->EAST activation chains live INSIDE a row -> CELL-LOCAL, so the
#     bundler templates one row and replicates it N times;
#   * NORTH->SOUTH psum and weight chains cross row instances -> CROSS-LEVEL;
#   * every link is NEAREST-NEIGHBOUR, which is what makes it systolic and
#     what makes congruence real rather than nominal.
#
# EVERYTHING is a parameter (see `configure`): N, the PE and edge block
# sizes, every pitch and margin, the three bus widths, and the tail depth.
# Nothing below hard-codes a number, so a larger experiment is an argument.
# ============================================================

namespace eval tpu_vehicle {
    # ── the knobs.  `configure` is the ONLY writer; the flow reads them.
    # 0 means AUTO: derived in `configure` from the bus widths and the
    # track pitch.  Sizes are not free parameters — a bus has to LAND on a
    # face, so a block narrower than its own bus is unroutable however much
    # channel it is given.  Measured the hard way: PEW 60 against a 24-bit
    # psum on a 4.0 bit pitch (96 units of face needed) stranded 672 of 832
    # bits, and WIDENING THE CHANNEL made it worse (1272), because the
    # channel was never the binding constraint.  Auto-sizing makes the
    # default self-consistent for any N/PW/AW; every value stays overridable
    # for the experiment that wants a deliberately tight design.
    variable P
    array set P {
        N        8
        PEW      0      PEH      0
        PPX      0      PPY      0
        ROWM    12      ROWGAP   0
        EDGEW    0      EDGEH    0
        EDGEGAP 48
        X0      60      Y0     120
        AW       8
        PW      24
        WW       8
        PIPE     2
        PIPEGAP 48
        BITPITCH 4
        PEPAD   24
        CHAN    48
        ALIGN    0
        YPERIOD 306
    }
}

# ── parameters ────────────────────────────────────────────────────────────
# `configure {N 16 PEW 80 ...}` — a dict, so a caller overrides exactly what
# it means to and inherits the rest.  Derived geometry is recomputed here
# rather than at use, so every consumer (hierarchy, placement, die) reads one
# consistent set and a driver cannot half-apply an override.
proc tpu_vehicle::configure {{overrides {}}} {
    variable P
    foreach {k v} $overrides {
        if {![info exists P($k)]} { error "tpu_vehicle: unknown parameter '$k'" }
        set P($k) $v
    }
    if {$P(N) < 2} { error "tpu_vehicle: N must be >= 2 (got $P(N))" }

    # ── auto-sized geometry (0 = auto) ────────────────────────────────────
    # A block's FACE has to host the bits that land on it: the north/south
    # faces carry the psum and weight chains, the east/west faces the
    # activation chain.  `BITPITCH` is the coarse (TOP-layer) bit pitch of
    # the declared stack — 4.0 for the M5/M6 patterns in `declare_stack` —
    # so it is a knob rather than a constant, and a different stack changes
    # one number instead of every size.
    set vbits [expr {$P(PW) + $P(WW)}]           ;# land on north/south
    set hbits $P(AW)                             ;# land on east/west
    if {$P(PEW) == 0} {
        set P(PEW) [expr {int(ceil($vbits*$P(BITPITCH))) + $P(PEPAD)}]
    }
    if {$P(PEH) == 0} {
        set P(PEH) [expr {int(ceil($hbits*$P(BITPITCH))) + $P(PEPAD)}]
    }
    if {$P(EDGEW) == 0} { set P(EDGEW) $P(PEW) }
    if {$P(EDGEH) == 0} { set P(EDGEH) $P(PEH) }
    if {$P(PPX) == 0}   { set P(PPX)   [expr {$P(PEW) + $P(CHAN)}] }
    # ROWGAP: compact by default, SNAPPED when the caller intends to solve
    # one row and copy it.  Congruent instances must see identical tracks,
    # which means the row pitch has to be a whole number of track periods —
    # `align_bottom_up` can only nudge, and with a compact gap every nudge it
    # tried collided with the next row and was reverted (measured: 7 of 8
    # instances left misaligned, and `check_template_tracks` then refused
    # DNUTS, which is the right answer to a design that is not congruent).
    #
    # YPERIOD is the engine's own y-period for THIS stack — it prints it on
    # the `[Align]` line — rather than something recomputed here, because it
    # is not simply the LCM of the H pitches (306, where LCM(18,32) is 288)
    # and a wrong guess would be silent.  It is a parameter for that reason:
    # change `declare_stack` and this changes with it.  Nothing rests on it
    # being right, though — `check_template_tracks` measures the real tracks
    # and refuses loudly if it is not.
    if {$P(ROWGAP) == 0} {
        if {$P(ALIGN)} {
            set rh [expr {2*$P(ROWM) + $P(PEH)}]
            set per $P(YPERIOD)
            set rpy [expr {int(ceil(double($rh + $P(CHAN))/$per)) * $per}]
            set P(ROWGAP) [expr {$rpy - $rh}]
        } else {
            set P(ROWGAP) $P(CHAN)
        }
    }

    # A pitch smaller than the block it steps is an overlap, not a design.
    if {$P(PPX) < $P(PEW)} {
        error "tpu_vehicle: PPX ($P(PPX)) < PEW ($P(PEW)) -- PEs would overlap"
    }
    # A face narrower than the bus that lands on it cannot be routed, and the
    # failure is a strand at DNUTS rather than anything at declaration — so
    # say it HERE, where the number that is wrong is still on screen.
    set need_w [expr {int(ceil($vbits*$P(BITPITCH)))}]
    set need_h [expr {int(ceil($hbits*$P(BITPITCH)))}]
    if {$P(PEW) < $need_w} {
        puts stderr "tpu_vehicle: WARNING -- PEW $P(PEW) is narrower than the\
              $vbits bits (psum $P(PW) + weight $P(WW)) that land on its\
              north/south face at pitch $P(BITPITCH) (needs >= $need_w);\
              expect stranded bits"
    }
    if {$P(PEH) < $need_h} {
        puts stderr "tpu_vehicle: WARNING -- PEH $P(PEH) is shorter than the\
              $hbits activation bits that land on its east/west face at\
              pitch $P(BITPITCH) (needs >= $need_h); expect stranded bits"
    }
    # PPY defaults to "as tall as a PE" — a row is one PE tall, so the row
    # pitch is what separates rows; kept as a knob for taller PE variants.
    if {$P(PPY) == 0} { set P(PPY) $P(PEH) }

    # A row cell: N PEs west->east, margin all round.
    set P(RW) [expr {2*$P(ROWM) + ($P(N)-1)*$P(PPX) + $P(PEW)}]
    set P(RH) [expr {2*$P(ROWM) + $P(PEH)}]
    set P(RPY) [expr {$P(RH) + $P(ROWGAP)}]
    # The die: the array plus both edges plus the tail, with X0/Y0 of slack.
    set P(DIEW) [expr {$P(X0) + $P(RW) + $P(EDGEW) + $P(EDGEGAP) + $P(X0)}]
    set P(DIEH) [expr {$P(Y0) + $P(N)*$P(RPY) + $P(EDGEGAP) + $P(EDGEH)
                       + ($P(PIPE)+1)*$P(PIPEGAP) + $P(PIPE)*$P(EDGEH)
                       + $P(Y0)}]
    return [array get P]
}

proc tpu_vehicle::get {k} {
    variable P
    if {![info exists P($k)]} { error "tpu_vehicle: no parameter '$k'" }
    return $P($k)
}

# The x of column c, in TOP-LEVEL coordinates (the row's own origin plus the
# in-row offset) — shared by the north wbuf, the south acc and the tail, so
# a column's blocks line up by construction rather than by three copies of
# the same arithmetic.
proc tpu_vehicle::col_x {c} {
    variable P
    return [expr {$P(X0) + $P(ROWM) + $c*$P(PPX)}]
}

# ── the metal stack ───────────────────────────────────────────────────────
# Declared by EVERY session including a resuming one (array_lib's rule: the
# stack is technology, and `load_pipeline` needs it re-declared).
proc tpu_vehicle::declare_stack {} {
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

# ── hierarchy: one PE cell, one ROW cell, N of each ───────────────────────
# Two levels of replication on purpose.  `pe_cell` repeats N times inside
# `row_cell` (cell-local), and `row_cell` repeats N times at the top — so the
# same design exercises the cell-local template path AND the per-instance
# expansion, with N^2 PEs total.
proc tpu_vehicle::build_hierarchy {} {
    variable P
    set N $P(N)

    buda::set_die $P(DIEW) $P(DIEH)

    buda::add_cell pe_cell   $P(PEW)   $P(PEH)
    buda::add_cell row_cell  $P(RW)    $P(RH)
    buda::add_cell feed_cell $P(EDGEW) $P(EDGEH)
    buda::add_cell wbuf_cell $P(EDGEW) $P(EDGEH)
    buda::add_cell acc_cell  $P(EDGEW) $P(EDGEH)

    # the row: N PEs, west to east
    for {set c 0} {$c < $N} {incr c} {
        buda::add_inst_to_cell row_cell pe_$c pe_cell \
            [expr {$P(ROWM) + $c*$P(PPX)}] $P(ROWM)
    }

    # N rows, stacked north to south; a west feeder beside each
    for {set r 0} {$r < $N} {incr r} {
        set y [expr {$P(Y0) + $r*$P(RPY)}]
        buda::add_inst row_$r row_cell - $P(X0) $y
        buda::add_inst feed_$r feed_cell - \
            [expr {$P(X0) - $P(EDGEW) - $P(EDGEGAP)}] \
            [expr {$y + $P(ROWM)}]
    }

    # north edge: one weight buffer per column
    for {set c 0} {$c < $N} {incr c} {
        buda::add_inst wbuf_$c wbuf_cell - [col_x $c] \
            [expr {$P(Y0) - $P(EDGEH) - $P(EDGEGAP)}]
    }

    # south edge: one accumulator per column, then the deep tail
    set accy [expr {$P(Y0) + $N*$P(RPY) + $P(EDGEGAP)}]
    for {set c 0} {$c < $N} {incr c} {
        buda::add_inst acc_$c acc_cell - [col_x $c] $accy
    }
    for {set s 0} {$s < $P(PIPE)} {incr s} {
        set y [expr {$accy + ($s+1)*($P(EDGEH) + $P(PIPEGAP))}]
        for {set c 0} {$c < $N} {incr c} {
            buda::add_inst pipe_${s}_$c acc_cell - [col_x $c] $y
        }
    }
}

proc tpu_vehicle::derive_interface {} { buda::derive_busterms 2 }

proc tpu_vehicle::load_blocks {} {
    buda::add_blocks_from_bdb 0
    buda::add_blocks_from_bdb 1 skip
    buda::add_blocks_from_bdb 2 skip
}

# ── the buses, generated ──────────────────────────────────────────────────
# Three families, and the split is the point rather than an accident of
# naming: the ACTIVATION chain is confined to one row (cell-local template),
# while the PSUM and WEIGHT chains cross row instances (cross-level).  A
# systolic array is exactly a design where most wires are nearest-neighbour,
# so this is where the hier flow should shine or fail visibly.
proc tpu_vehicle::build_buses {} {
    variable P
    set N $P(N)
    buda::bdb_net_mode on

    # 1. west->east activations, INSIDE each row -> cell-local template
    for {set r 0} {$r < $N} {incr r} {
        for {set c 0} {$c < $N - 1} {incr c} {
            buda::add_bus "a_${r}_${c}\[$P(AW)\]" \
                row_$r/pe_$c.a_out row_$r/pe_[expr {$c+1}].a_in
        }
    }
    # 2. north->south partial sums and weights, ACROSS rows -> cross-level
    for {set r 0} {$r < $N - 1} {incr r} {
        for {set c 0} {$c < $N} {incr c} {
            buda::add_bus "p_${r}_${c}\[$P(PW)\]" \
                row_$r/pe_$c.p_out row_[expr {$r+1}]/pe_$c.p_in
            buda::add_bus "w_${r}_${c}\[$P(WW)\]" \
                row_$r/pe_$c.w_out row_[expr {$r+1}]/pe_$c.w_in
        }
    }
    # 3. the edges: activations in from the west, weights in from the north,
    #    partial sums out to the south, then down the tail.
    for {set r 0} {$r < $N} {incr r} {
        buda::add_bus "fa_${r}\[$P(AW)\]" feed_$r.out row_$r/pe_0.a_in
    }
    for {set c 0} {$c < $N} {incr c} {
        buda::add_bus "fw_${c}\[$P(WW)\]" wbuf_$c.out row_0/pe_$c.w_in
        buda::add_bus "fp_${c}\[$P(PW)\]" \
            row_[expr {$N-1}]/pe_$c.p_out acc_$c.in
        for {set s 0} {$s < $P(PIPE)} {incr s} {
            set src [expr {$s == 0 ? "acc_$c" : "pipe_[expr {$s-1}]_$c"}]
            buda::add_bus "tp_${s}_${c}\[$P(PW)\]" $src.out pipe_${s}_$c.in
        }
    }
}

# ── size, for the banner and for the tests ────────────────────────────────
proc tpu_vehicle::describe {} {
    variable P
    set N $P(N)
    set pes [expr {$N*$N}]
    set buses [expr {$N*($N-1) + 2*($N-1)*$N + $N + 2*$N + $N*$P(PIPE)}]
    set nets [expr {$N*($N-1)*$P(AW) + ($N-1)*$N*($P(PW)+$P(WW))
                    + $N*$P(AW) + $N*$P(WW) + $N*$P(PW)
                    + $N*$P(PIPE)*$P(PW)}]
    return "N=$N -> $pes PEs in $N rows, $buses buses, $nets nets,\
            die $P(DIEW)x$P(DIEH)"
}

# ── verdict helpers (array_lib's rule: three legs, -1 is dirty) ───────────
proc tpu_vehicle::is_dirty {} {
    return [expr {[buda::query overlaps] > 0 || [buda::query unplaced] > 0
                  || [buda::query violations] != 0}]
}

proc tpu_vehicle::heal_if_dirty {who} {
    if {![tpu_vehicle::is_dirty]} { return 0 }
    puts "$who: dirty ([buda::query overlaps] overlaps,\
          [buda::query unplaced] unplaced,\
          [buda::query violations] audit violations) -- healing"
    buda::negotiate_congestion 10
    buda::ripup_reroute 20
    buda::check_design dnuts
    return 1
}

proc tpu_vehicle::verdict {who} {
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
