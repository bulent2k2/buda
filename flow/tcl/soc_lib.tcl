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
    #   LAYOUT             the TOP-LEVEL floorplan: `band` (the historical
    #                      one -- quadrants in a ceil(sqrt(NQ))-column grid,
    #                      the l2 + io pair in a band above, left-aligned;
    #                      every table in soc.md was measured on it) or
    #                      `compact` (the grid shape and the pair's place
    #                      chosen for die UTILIZATION -- see _top_geom)
    array set P {
        NQ       2      NC       2
        NBANK    2      NBANK2   4
        NIO      4
        DW      32      AW      16
        IW      32      CW       8
        BITPITCH 4.0    PAD     24
        M       16      GAP     16
        LAYOUT  band
        PACK    grid    ASPECT   1
        KEEP    12      CGAP    -1
        FACEPAD 10      FIX     {}
        FACES   0
        FIXSLACK 0.5
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
    if {$P(LAYOUT) ni {band compact}} {
        error "soc_vehicle: LAYOUT must be band or compact (got '$P(LAYOUT)')"
    }
    if {$P(PACK) ni {grid slice}} {
        error "soc_vehicle: PACK must be grid or slice (got '$P(PACK)')"
    }
    if {![string is double -strict $P(ASPECT)] || $P(ASPECT) < 1 || $P(ASPECT) > 4} {
        error "soc_vehicle: ASPECT must be a number in \[1, 4\] (got '$P(ASPECT)')"
    }
    if {$P(ASPECT) > 1 && $P(PACK) ne "slice"} {
        # A non-square leaf in the grid packer would only add whitespace:
        # its column and row are sized by the widest and tallest child.
        error "soc_vehicle: ASPECT > 1 needs PACK slice"
    }
    if {![string is integer -strict $P(CGAP)] || $P(CGAP) < -1} {
        error "soc_vehicle: CGAP must be an integer >= 0, or -1 for GAP (got '$P(CGAP)')"
    }
    if {$P(CGAP) >= 0 && $P(PACK) ne "slice"} {
        error "soc_vehicle: CGAP needs PACK slice"
    }
    if {![string is integer -strict $P(FACEPAD)] || $P(FACEPAD) < 0} {
        error "soc_vehicle: FACEPAD must be an integer >= 0 (got '$P(FACEPAD)')"
    }
    if {$P(FACES) ni {0 2 4}} {
        error "soc_vehicle: FACES must be 0, 2 or 4 (got '$P(FACES)')"
    }
    if {[catch {dict size $P(FIX)}]} {
        error "soc_vehicle: FIX must be a dict {cell plan ...} (got '$P(FIX)')"
    }
    if {[dict size $P(FIX)] && $P(PACK) ne "slice"} {
        error "soc_vehicle: FIX needs PACK slice"
    }
    dict for {c k} $P(FIX) {
        if {$c ni {core_cell l1_cell l2_cell rtr_cell io_blk_cell cluster_cell quad_cell}} {
            error "soc_vehicle: FIX names '$c', which is not a container"
        }
        if {$k ne "grid" && !([string is integer -strict $k] && $k >= 0)} {
            error "soc_vehicle: FIX plan for $c must be an index >= 0 or grid (got '$k')"
        }
    }
    if {![string is double -strict $P(FIXSLACK)] || $P(FIXSLACK) < 0} {
        error "soc_vehicle: FIXSLACK must be a number >= 0 (got '$P(FIXSLACK)')"
    }
    if {![string is integer -strict $P(KEEP)] || $P(KEEP) < 2} {
        error "soc_vehicle: KEEP must be an integer >= 2 (got '$P(KEEP)')"
    }

    # The MIRROR of that rule, on the PHYSICAL sizing knobs (Codex P2, #930).
    # The loop above guards every COUNT and left `BITPITCH`, `PAD`, `M` and
    # `GAP` unchecked -- four knobs, not the two the finding named, which is
    # why this is a loop over all of them rather than two `if`s.
    #
    # What goes wrong is NOT what went wrong for `-DW 0`, and the difference
    # is worth stating because it decides how hard to fail.  A routed run
    # with `-BITPITCH 0` or `-PAD -100` comes back LOUDLY dirty (measured at
    # NQ=1: 89 ovl / 1408 unplaced and 10 ovl / 1008 unplaced), so it does
    # not report success over a broken design the way `-DW 0` did.  Two other
    # things break instead:
    #
    #   (a) THE FACE GUARD IS COMPLICIT.  This vehicle's whole claim is that
    #       a too-narrow face is reported AT DECLARATION -- `tpu.tcl`'s
    #       lesson.  But `check_bus_faces` prices a face through `_dim`, the
    #       same `_dim` that just multiplied by zero, so the check computes
    #       the identical wrong number and passes.  A guard cannot audit the
    #       arithmetic it is written in, which is exactly the shape of every
    #       instrument failure in this PR.
    #   (b) `-dry` REPORTS A PLAUSIBLE DIE.  `-dry` is the advertised way to
    #       sweep geometry without paying for a route, so a harness reading
    #       it gets 1136x480 and no complaint at all.
    #
    # Per-knob rules, and ZERO IS MEASURED rather than assumed (NQ=1):
    #   BITPITCH  > 0   a pitch of zero makes a face stop depending on its
    #                   bits; it is a DOUBLE (4.0), so not `string is integer`
    #   PAD      >= 0   0 is legal and TIGHT -- it routes to 8 unplaced and
    #                   SAYS so (rc=1), an honest report, so it is allowed;
    #                   negative shrinks a face BELOW its own bits
    #   GAP      >= 0   0 is abutment: dirty (2 ovl / 160 unplaced) but
    #                   loudly, so allowed; negative would OVERLAP blocks
    #   M        >= 0   0 is CLEAN, measured
    foreach {k floor kind} {BITPITCH 0 pos PAD 0 nonneg GAP 0 nonneg M 0 nonneg} {
        set v $P($k)
        set numeric [expr {$k eq "BITPITCH" ? [string is double -strict $v]
                                           : [string is integer -strict $v]}]
        set bad [expr {!$numeric || ($kind eq "pos" ? $v <= $floor : $v < $floor)}]
        if {$bad} {
            set want [expr {$kind eq "pos" ? "> 0" : ">= 0"}]
            error "soc_vehicle: $k must be a number $want (got '$v')"
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
    variable TOP
    array unset SZ
    foreach c [array names LEAF] {
        lassign $LEAF($c) vbits hbits
        set SZ($c) [list [_dim $vbits] [_dim $hbits]]
    }

    # ── FACES: a leaf's faces hold every PIN, not just its heaviest one.
    # The rule above sizes a face from the worst single pin, which assumes
    # each pin gets a face to itself; a leaf with more pins than faces
    # cannot give it that.  Measured (soc.md, "Round 3"): `regf_cell` has
    # five (4 x DW + AW) on four faces, the local core run put 64 bits on
    # one 34-bit face, and `m`, `x` and `dd` -- all ending at `regf` -- were
    # 854 of the chip's 1,131 first-check bits at PAD 10.  So each leaf's
    # pins, as `build_buses` actually wires them, are spread over its faces
    # heaviest-first onto the lightest (a pin stays one place, on one face),
    # and with FACES 4 every face is sized for the heaviest face that
    # spread leaves; with FACES 2 only the two N/S faces are (the width),
    # the E/W pair keeping its per-pin size.  0 is the per-pin rule alone.
    if {$P(FACES)} {
        dict for {c load} [_pin_loads] {
            lassign $SZ($c) w h
            lassign $load l4 l2
            if {$P(FACES) == 4} {
                set d [_dim $l4]
                set SZ($c) [list [expr {max($w, $d)}] [expr {max($h, $d)}]]
            } else {
                set SZ($c) [list [expr {max($w, [_dim $l2])}] $h]
            }
        }
    }

    # ── PACK slice: every container and every leaf SHAPE chosen together
    # (`_slice_configure`, below); the grid packer is the default and is
    # untouched by it.
    if {$P(PACK) eq "slice"} {
        _slice_configure
        set P(DIEW) $TOP(diew)
        set P(DIEH) $TOP(dieh)
        return
    }
    variable PL
    array unset PL

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

    # ── the die: the quadrants, the l2 and the io block, placed by ONE
    # walk (`_top_geom`, per LAYOUT) that `build_hierarchy` instantiates
    # from -- so the declared die and where the top-level blocks land cannot
    # disagree (the same rule `_pack_geom` keeps for every cell).
    variable TOP
    array set TOP [_top_geom]
    set P(DIEW) $TOP(diew)
    set P(DIEH) $TOP(dieh)
}

# The top-level floorplan, as a dict: `diew dieh` (the die), `pos` (a list of
# {name cell x y}, one per top-level instance -- the NQ quadrants, then l2,
# then io), and what was decided (`nc nr holes band util`), where `util` is
# the top-level block area over the die area -- the number the empty
# corner of the historical layout is measured by.
#
#   band     the historical placement, byte for byte: the quadrants in a
#            ceil(sqrt(NQ))-column grid (`_pack`, so the last row may be
#            short -- NQ=32 is a 6x6 grid with FOUR empty slots), the l2 and
#            io in a band ABOVE the grid, left-aligned, and a margin M around
#            the grid on top of the M inside `_pack`.
#   compact  every column count 1..NQ is a candidate, and for each the l2 +
#            io pair either FILLS the grid's empty slots (when it fits the
#            hole the short last row leaves, at the row's right end) or sits
#            in a band above, CENTRED; the candidate with the highest
#            utilization -- the SMALLEST DIE, since every candidate holds
#            the same blocks, ranked on that integer area rather than on
#            the rounded ratio -- wins, the aspect ratio held to [1/2, 2]
#            so a 1x32 strip cannot win on area alone, ties to the aspect
#            nearest 1 and then the fewer columns.  Chosen, not asserted: whether the
#            hole or the band is the better home for the pair depends on how
#            big the pair is against a quadrant, which the widths decide.
proc soc_vehicle::_top_geom {} {
    variable P
    variable SZ
    set n $P(NQ)
    set M $P(M) ; set G $P(GAP)
    lassign $SZ(quad_cell) qw qh
    lassign $SZ(l2_cell) lw lh
    lassign $SZ(io_blk_cell) iw ih
    set qcells [lrepeat $n quad_cell]

    if {$P(LAYOUT) eq "band"} {
        lassign [_pack $qcells] tw th
        lassign [_pack {l2_cell io_blk_cell}] bw bh
        set diew [expr {max($tw,$bw) + 2*$M}]
        set dieh [expr {$th + $bh + 3*$M}]
        set pos {}
        set q 0
        foreach xy [_pack_pos $qcells] {
            lassign $xy x y
            lappend pos [list quad_$q quad_cell $x $y]
            incr q
        }
        set by [expr {$M + $th + $M}]
        set bpos [_pack_pos {l2_cell io_blk_cell}]
        lassign [lindex $bpos 0] lx ly
        lassign [lindex $bpos 1] ix iy
        lappend pos [list l2 l2_cell $lx [expr {$by + $ly}]]
        lappend pos [list io io_blk_cell $ix [expr {$by + $iy}]]
        set nc [_cols $n]
        set nr [expr {int(ceil(double($n)/$nc))}]
        return [list diew $diew dieh $dieh pos $pos nc $nc nr $nr \
                     holes [expr {$nc*$nr - $n}] band 1 \
                     util [_util $diew $dieh]]
    }

    # compact: rank every column count.
    set pw [expr {$lw + $G + $iw}]
    set ph [expr {max($lh, $ih)}]
    set best {}
    for {set nc 1} {$nc <= $n} {incr nc} {
        set nr [expr {int(ceil(double($n)/$nc))}]
        set gw [expr {$nc*$qw + ($nc-1)*$G}]
        set gh [expr {$nr*$qh + ($nr-1)*$G}]
        set holes [expr {$nc*$nr - $n}]
        set band 1
        if {$holes > 0} {
            set hw [expr {$holes*$qw + ($holes-1)*$G}]
            if {$pw <= $hw && $ph <= $qh} { set band 0 }
        }
        if {$band} {
            set diew [expr {max($gw, $pw) + 2*$M}]
            set dieh [expr {$gh + $G + $ph + 2*$M}]
        } else {
            set diew [expr {$gw + 2*$M}]
            set dieh [expr {$gh + 2*$M}]
        }
        # Ranked on the DIE AREA, an integer: every candidate holds the same
        # blocks, so the highest utilization is exactly the smallest die,
        # and the ratio is only formatted for the report -- ranking on the
        # three-decimal string tied candidates whose dies differ (Codex P2
        # on #932: at `16 -NC 2 -NBANK 8 -NBANK2 1 -NIO 32` a 3-column die
        # of 307,508,992 beat a 4-column one of 307,345,408 on the
        # tie-breakers, both reading 0.876).
        set aspect [expr {double($diew)/$dieh}]
        set ok [expr {$aspect >= 0.5 && $aspect <= 2.0}]
        set key [list $ok [expr {-$diew*$dieh}] [expr {-abs(log($aspect))}] [expr {-$nc}]]
        if {$best eq "" || [_key_better $key [lindex $best 0]]} {
            set best [list $key $nc $nr $holes $band $diew $dieh]
        }
    }
    lassign $best _key nc nr holes band diew dieh
    set gw [expr {$nc*$qw + ($nc-1)*$G}]
    set gh [expr {$nr*$qh + ($nr-1)*$G}]
    set pos {}
    for {set q 0} {$q < $n} {incr q} {
        set col [expr {$q % $nc}] ; set row [expr {$q / $nc}]
        lappend pos [list quad_$q quad_cell \
                         [expr {$M + $col*($qw + $G)}] \
                         [expr {$M + $row*($qh + $G)}]]
    }
    if {$band} {
        set yb [expr {$M + $gh + $G}]
        set x0 [expr {($diew - $pw)/2}]
        lappend pos [list l2 l2_cell $x0 [expr {$yb + ($ph - $lh)/2}]]
        lappend pos [list io io_blk_cell [expr {$x0 + $lw + $G}] \
                         [expr {$yb + ($ph - $ih)/2}]]
    } else {
        set hw [expr {$holes*$qw + ($holes-1)*$G}]
        set x0 [expr {$M + ($n % $nc)*($qw + $G) + ($hw - $pw)/2}]
        set y0 [expr {$M + ($nr-1)*($qh + $G)}]
        lappend pos [list l2 l2_cell $x0 [expr {$y0 + ($qh - $lh)/2}]]
        lappend pos [list io io_blk_cell [expr {$x0 + $lw + $G}] \
                         [expr {$y0 + ($qh - $ih)/2}]]
    }
    return [list diew $diew dieh $dieh pos $pos nc $nc nr $nr \
                 holes $holes band $band util [_util $diew $dieh]]
}

# Top-level block area over die area, to three places.
proc soc_vehicle::_util {diew dieh} {
    variable P
    variable SZ
    lassign $SZ(quad_cell) qw qh
    lassign $SZ(l2_cell) lw lh
    lassign $SZ(io_blk_cell) iw ih
    set blocks [expr {$P(NQ)*$qw*$qh + $lw*$lh + $iw*$ih}]
    return [format %.3f [expr {double($blocks)/($diew*$dieh)}]]
}

# Lexicographic "a beats b" over the ranking keys of `_top_geom`.
proc soc_vehicle::_key_better {a b} {
    foreach x $a y $b {
        if {$x > $y} { return 1 }
        if {$x < $y} { return 0 }
    }
    return 0
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

# ── the pin loads a leaf's faces must carry ───────────────────────────────
# Per leaf cell type, {l4 l2}: the heaviest face when the leaf's PINS (bits
# summed per pin, the way `check_bus_faces` sums them) are spread over four
# equal faces heaviest-first onto the lightest (l4), and the heaviest of the
# two N/S faces when the E/W pair keeps its per-pin size and takes what
# fits first (l2) -- worst over the type's instances.  Read off the SAME
# `build_buses` the design is wired by, with the engine calls and the face
# guard caught rather than run: a hand-kept table of who drives what is the
# twin this vehicle has been bitten by (phantom coefficients, stale
# citations).  Only leaves whose pins outnumber what the per-pin rule gives
# them change; at the defaults that is `regf_cell` alone.
proc soc_vehicle::_pin_loads {} {
    variable P
    variable LEAF
    define_cells 1
    namespace eval ::buda {}
    set saved {}
    foreach p {add_bus bdb_net_mode} {
        if {[llength [info commands ::buda::$p]]} {
            rename ::buda::$p ::buda::__pin_loads_$p
            lappend saved $p
        }
    }
    set ::soc_vehicle::_CAP {}
    proc ::buda::add_bus {name drv rcv} { lappend ::soc_vehicle::_CAP [list $name $drv $rcv] }
    proc ::buda::bdb_net_mode args {}
    rename check_bus_faces __check_bus_faces
    proc check_bus_faces args {}
    set err [catch {build_buses} msg opts]
    rename check_bus_faces ""
    rename __check_bus_faces check_bus_faces
    rename ::buda::add_bus "" ; rename ::buda::bdb_net_mode ""
    foreach p $saved { rename ::buda::__pin_loads_$p ::buda::$p }
    if {$err} { return -options $opts $msg }

    # bits per pin, per leaf instance
    array set pin {}
    foreach b $::soc_vehicle::_CAP {
        lassign $b name drv rcv
        regexp {\[(\d+)\]$} $name -> bits
        foreach end [list $drv $rcv] {
            if {![info exists pin($end)]} { set pin($end) 0 }
            incr pin($end) $bits
        }
    }
    array set inst {}
    foreach end [array names pin] {
        set path [lindex [split $end .] 0]
        lappend inst($path) $pin($end)
    }
    set out [dict create]
    foreach path [array names inst] {
        set c [cell_at $path]
        if {$c eq "" || ![info exists LEAF($c)]} { continue }
        lassign $LEAF($c) vb hb
        set loads [lsort -integer -decreasing $inst($path)]
        # four equal faces, heaviest pin onto the lightest face
        set f {0 0 0 0}
        foreach b $loads {
            set i [lsearch -exact $f [tcl::mathfunc::min {*}$f]]
            lset f $i [expr {[lindex $f $i] + $b}]
        }
        set l4 [tcl::mathfunc::max {*}$f]
        # E/W at their per-pin size take what fits, first-fit decreasing;
        # the rest onto the two N/S faces, heaviest onto the lighter
        set ew [list 0 0] ; set ns [list 0 0]
        foreach b $loads {
            set placed 0
            for {set i 0} {$i < 2} {incr i} {
                if {[lindex $ew $i] + $b <= $vb} {
                    lset ew $i [expr {[lindex $ew $i] + $b}] ; set placed 1 ; break
                }
            }
            if {!$placed} {
                set i [expr {[lindex $ns 0] <= [lindex $ns 1] ? 0 : 1}]
                lset ns $i [expr {[lindex $ns $i] + $b}]
            }
        }
        set l2 [tcl::mathfunc::max $hb {*}$ns]
        if {[dict exists $out $c]} {
            lassign [dict get $out $c] o4 o2
            set l4 [expr {max($l4, $o4)}] ; set l2 [expr {max($l2, $o2)}]
        }
        dict set out $c [list $l4 $l2]
    }
    return $out
}

# ── PACK slice: slicing floorplans with shape curves ──────────────────────
# The grid packer above puts children in a ceil(sqrt(n))-column grid whose
# columns and rows are as wide and tall as their largest member, which is
# exactly where a DIVERSE cell wastes area: at NQ = 8, PAD 10, GAP 4 a
# cluster is 844 x 972 around 454,420 units of leaf -- 55 % used -- and the
# sixteen clusters are 87 % of the die.  `PACK slice` packs every container
# as the best SLICING floorplan of its children instead (every way of
# cutting the set in two, H or V, recursively, Stockmeyer's shape-curve
# composition), and with `ASPECT` > 1 lets each leaf take a non-square shape
# of the SAME area, long side at most ASPECT times the short one.
#
# What is chosen is the smallest DIE.  Every container keeps its Pareto
# curve of (w, h) packings, not one point, so a parent can take a child
# shape that is not the child's own smallest; the one constraint the curves
# cannot carry is that a cell TYPE has ONE shape everywhere it occurs, so a
# type repeated inside one parent (two `l1_cell`s, four `io_cell`s) is
# enumerated over its options rather than composed, and the two leaves
# shared ACROSS parents (`tag_cell` and `sram_cell`, in both caches) are an
# outer loop.  ASPECT governs LEAVES; containers are held to 2:1 whatever
# it is -- the grid packer's own cells reach 1.8, and at 4:1 a cluster
# came out as a 510-wide column.
#
# A non-square leaf spends its padding: its short face must still host its
# bits at the bit pitch plus FACEPAD (`_leaf_shapes`, and `check_bus_faces`
# holds every face to the same), so how far a leaf can stretch GROWS with
# PAD -- at PAD 24 an `io_cell` (8 bits) reaches 1.5:1 and the 32-bit
# leaves nothing at all.
proc soc_vehicle::_container_cells {} {
    variable P
    return [list \
        core_cell    {dec_cell alu_cell mul_cell regf_cell} \
        l1_cell      [concat tag_cell [lrepeat $P(NBANK) sram_cell]] \
        l2_cell      [concat tag_cell memctl_cell [lrepeat $P(NBANK2) sram_cell]] \
        rtr_cell     {fifo_cell xbar_cell fifo_cell} \
        io_blk_cell  [concat bridge_cell [lrepeat $P(NIO) io_cell]] \
        cluster_cell {core_cell l1_cell l1_cell rtr_cell} \
        quad_cell    [lrepeat $P(NC) cluster_cell]]
}

# A leaf's candidate shapes {w h}: the face-derived square first, then each
# aspect up to ASPECT both ways, area kept (h rounded UP, so never smaller)
# -- and only while the SHORT face still hosts the leaf's bits at the bit
# pitch plus FACEPAD.  A stretch spends the leaf's PADDING and nothing
# else, and not all of it: measured at NQ = 8, PAD 24, GAP 12, CGAP 4,
# with no floor ASPECT 3 and 4 stranded 2,254 and 2,594 bits at the first
# check against 184 square and neither healed (the router lands a bus on
# whichever face is nearest, not on the one wide enough for it), and with
# the floor at the bits alone a 1.25 stretch of the 32-bit leaves -- 8
# units of slack left on the short face -- stranded 606 and timed out.
# FACEPAD's default 10 is the padding floor measured on square leaves
# (soc.md, "Compaction at NQ = 8").
proc soc_vehicle::_leaf_shapes {c} {
    variable P
    variable SZ
    variable LEAF
    lassign $SZ($c) d h0
    if {$h0 != $d} {
        # already non-square (FACES 2): the declared shape and its rotation
        return [list [list $d $h0] [list $h0 $d]]
    }
    lassign $LEAF($c) vb hb
    set floor [expr {int(ceil(max($vb, $hb)*$P(BITPITCH))) + $P(FACEPAD)}]
    set A [expr {$d*$d}]
    set out [list [list $d $d]]
    foreach k {1.25 1.5 2 2.5 3 4} {
        if {$k > $P(ASPECT) + 1e-9} { break }
        set w [expr {int(round(sqrt($A*$k)))}]
        set h [expr {int(ceil(double($A)/$w))}]
        if {min($w, $h) < $floor} { break }
        foreach s [list [list $w $h] [list $h $w]] {
            if {$s ni $out} { lappend out $s }
        }
    }
    return $out
}

# Pareto filter on {w h ...} (smaller w, smaller h), thinned to KEEP points
# spread along the curve plus the smallest-area one.
proc soc_vehicle::_pareto {pts} {
    variable P
    set cur {} ; set besth ""
    foreach p [lsort -integer -index 0 [lsort -integer -index 1 $pts]] {
        set h [lindex $p 1]
        if {$besth eq "" || $h < $besth} { lappend cur $p ; set besth $h }
    }
    set n [llength $cur]
    if {$n <= $P(KEEP)} { return $cur }
    set amin 0 ; set a0 ""
    for {set i 0} {$i < $n} {incr i} {
        lassign [lindex $cur $i] w h
        if {$a0 eq "" || $w*$h < $a0} { set a0 [expr {$w*$h}] ; set amin $i }
    }
    set keep [list $amin]
    set K [expr {$P(KEEP) - 1}]
    for {set j 0} {$j < $K} {incr j} {
        lappend keep [expr {int(round($j*($n-1.0)/($K-1)))}]
    }
    set out {}
    foreach i [lsort -integer -unique $keep] { lappend out [lindex $cur $i] }
    return $out
}

# The Pareto curve of every slicing packing of a child set (no margin).
# `opts` holds, per child, its options {w h ref}.  Returns points
# {w h plist}, plist per child (in order) {x y w h ref}.
proc soc_vehicle::_slice {opts G} {
    variable P
    set n [llength $opts]
    set full [expr {(1 << $n) - 1}]
    for {set i 0} {$i < $n} {incr i} {
        set pts {}
        foreach o [lindex $opts $i] {
            lassign $o w h ref
            lappend pts [list $w $h leaf $i $ref]
        }
        set F([expr {1 << $i}]) [_pareto $pts]
    }
    for {set m 1} {$m <= $full} {incr m} {
        if {[info exists F($m)]} { continue }
        set low [expr {$m & -$m}]
        set pts {}
        for {set a [expr {($m - 1) & $m}]} {$a > 0} {set a [expr {($a - 1) & $m}]} {
            if {!($a & $low)} { continue }
            set b [expr {$m ^ $a}]
            set ia 0
            foreach pa $F($a) {
                lassign $pa wa ha
                set ib 0
                foreach pb $F($b) {
                    lassign $pb wb hb
                    lappend pts [list [expr {$wa + $G + $wb}] [expr {max($ha, $hb)}] H $a $ia $b $ib] \
                                [list [expr {max($wa, $wb)}] [expr {$ha + $G + $hb}] V $a $ia $b $ib]
                    incr ib
                }
                incr ia
            }
        }
        set F($m) [_pareto $pts]
    }
    set out {}
    foreach p $F($full) {
        set pl [lrepeat $n {}]
        _slice_place F $p 0 0 pl $G
        lappend out [list [lindex $p 0] [lindex $p 1] $pl]
    }
    return $out
}

# Walk one point's slicing tree, each subtree centred across its cut.
proc soc_vehicle::_slice_place {Fn p x y pln G} {
    upvar 1 $Fn F $pln pl
    lassign $p w h kind a ia b ib
    if {$kind eq "leaf"} {
        lset pl $a [list $x $y $w $h $ia]
        return
    }
    set pa [lindex $F($a) $ia] ; set pb [lindex $F($b) $ib]
    lassign $pa wa ha ; lassign $pb wb hb
    if {$kind eq "H"} {
        _slice_place F $pa $x [expr {$y + ($h - $ha)/2}] pl $G
        _slice_place F $pb [expr {$x + $wa + $G}] [expr {$y + ($h - $hb)/2}] pl $G
    } else {
        _slice_place F $pa [expr {$x + ($w - $wa)/2}] $y pl $G
        _slice_place F $pb [expr {$x + ($w - $wb)/2}] [expr {$y + $ha + $G}] pl $G
    }
}

# A container's curve: its children packed, margin M all round, aspect held
# to 2:1.  `opt` maps each child cell to its options {w h ref};
# a child type occurring more than once is enumerated, not composed.
proc soc_vehicle::_container_curve {cell cells opt} {
    variable P
    set M $P(M)
    set cap 2.0
    set combos [list {}]
    foreach c [lsort -unique $cells] {
        if {[llength [lsearch -all -exact $cells $c]] < 2} { continue }
        set nc {}
        foreach cb $combos {
            foreach o [dict get $opt $c] { lappend nc [dict merge $cb [dict create $c $o]] }
        }
        set combos $nc
    }
    set all {}
    foreach cb $combos {
        set opts {}
        foreach c $cells {
            if {[dict exists $cb $c]} { lappend opts [list [dict get $cb $c]] } \
                                 else { lappend opts [dict get $opt $c] }
        }
        foreach p [_slice $opts [_gap_in $cell]] {
            lassign $p w h pl
            set W [expr {$w + 2*$M}] ; set H [expr {$h + 2*$M}]
            if {max($W, $H) > $cap*min($W, $H)} { continue }
            set pl2 {}
            foreach e $pl {
                lassign $e x y cw ch ref
                lappend pl2 [list [expr {$x + $M}] [expr {$y + $M}] $cw $ch $ref]
            }
            lappend all [list $W $H $pl2]
        }
    }
    set cur [_pareto $all]
    if {![llength $cur]} {
        error "soc_vehicle: no packing of $cell within aspect $cap"
    }
    return $cur
}

# The channel between siblings inside `cell`: CGAP in the two containers
# of containers (a cluster, a quadrant), GAP everywhere else.
proc soc_vehicle::_gap_in {cell} {
    variable P
    if {$P(CGAP) >= 0 && $cell in {cluster_cell quad_cell}} { return $P(CGAP) }
    return $P(GAP)
}

# A container's curve -- or, when FIX names it, the ONE point its fixed plan
# gives (`enum_plans` at FIXSLACK, or `grid`), with each container child at
# its only point if fixed too and at its smallest otherwise.  How a plan is
# CHOSEN is `soc_local.tcl`'s business (a plan routed alone, priced against
# the world round it); this only builds the design it names.
proc soc_vehicle::_curve_of {cell kids opt} {
    variable P
    variable SZ
    set cells [dict get $kids $cell]
    if {![dict exists $P(FIX) $cell]} { return [_container_curve $cell $cells $opt] }
    set pick {}
    foreach c [lsort -unique $cells] {
        set best ""
        foreach o [dict get $opt $c] {
            lassign $o w h
            if {$best eq "" || $w*$h < [lindex $best 0]*[lindex $best 1]} { set best $o }
        }
        dict set pick $c $best
        set SZ($c) [lrange $best 0 1]
    }
    set k [dict get $P(FIX) $cell]
    if {$k eq "grid"} {
        set plan [grid_plan $cell]
    } else {
        set plans [enum_plans $cell $P(FIXSLACK)]
        if {$k >= [llength $plans]} {
            error "soc_vehicle: FIX $cell $k, but it has [llength $plans] plans at FIXSLACK $P(FIXSLACK)"
        }
        set plan [lindex $plans $k]
    }
    lassign $plan W H pos
    set pl {}
    foreach c $cells xy $pos {
        lassign $xy x y
        lassign [dict get $pick $c] w h ref
        lappend pl [list $x $y $w $h $ref]
    }
    return [list [list $W $H $pl]]
}

# A curve as child options {w h index}.
proc soc_vehicle::_curve_opts {cur} {
    set out {} ; set i 0
    foreach p $cur { lappend out [list [lindex $p 0] [lindex $p 1] $i] ; incr i }
    return $out
}

proc soc_vehicle::_slice_configure {} {
    variable P
    variable SZ
    variable TOP
    variable PL
    variable CURVE
    array unset PL
    array unset CURVE
    set kids [_container_cells]

    set opt [dict create]
    foreach c {dec_cell alu_cell mul_cell regf_cell fifo_cell xbar_cell
               memctl_cell bridge_cell io_cell tag_cell sram_cell} {
        set l {}
        foreach s [_leaf_shapes $c] { lappend l [concat $s -1] }
        dict set opt $c $l
    }
    # the containers that do not hold a shared leaf, once
    foreach c {core_cell rtr_cell io_blk_cell} {
        set cur($c) [_curve_of $c $kids $opt]
        dict set opt $c [_curve_opts $cur($c)]
    }
    set best "" ; set bestarea ""
    foreach to [dict get $opt tag_cell] {
        foreach so [dict get $opt sram_cell] {
            set o $opt
            dict set o tag_cell [list $to]
            dict set o sram_cell [list $so]
            foreach c {l1_cell l2_cell cluster_cell quad_cell} {
                set cur($c) [_curve_of $c $kids $o]
                dict set o $c [_curve_opts $cur($c)]
            }
            set qi 0
            foreach q $cur(quad_cell) {
                set li 0
                foreach l $cur(l2_cell) {
                    set ii 0
                    foreach io $cur(io_blk_cell) {
                        set SZ(quad_cell)   [lrange $q 0 1]
                        set SZ(l2_cell)     [lrange $l 0 1]
                        set SZ(io_blk_cell) [lrange $io 0 1]
                        array set T [_top_geom]
                        set area [expr {$T(diew)*$T(dieh)}]
                        if {$bestarea eq "" || $area < $bestarea} {
                            set bestarea $area
                            set best [list $to $so $qi $li $ii [array get cur]]
                        }
                        incr ii
                    }
                    incr li
                }
                incr qi
            }
        }
    }
    lassign $best to so qi li ii curs
    array set CURVE $curs
    variable ASSIGNED
    array unset ASSIGNED
    set ASSIGNED(tag_cell) [lrange $to 0 1]
    set ASSIGNED(sram_cell) [lrange $so 0 1]
    foreach {c i} [list quad_cell $qi l2_cell $li io_blk_cell $ii] {
        _slice_assign $c $i $kids
    }
    array set TOP [_top_geom]
}

# Fix a container at one curve point and its children at the points that
# point was built from; a TYPE given two different shapes is an error.
proc soc_vehicle::_slice_assign {cell idx kids} {
    variable SZ
    variable PL
    variable CURVE
    variable ASSIGNED
    lassign [lindex $CURVE($cell) $idx] W H pl
    set SZ($cell) [list $W $H]
    set cells [dict get $kids $cell]
    set pos {}
    foreach c $cells e $pl {
        lassign $e x y w h ref
        lappend pos [list $x $y]
        if {[info exists ASSIGNED($c)] && $ASSIGNED($c) ne [list $w $h]} {
            error "soc_vehicle: $c shaped $ASSIGNED($c) and [list $w $h]"
        }
        set ASSIGNED($c) [list $w $h]
        set SZ($c) [list $w $h]
        if {$ref >= 0 && ![info exists PL($c)]} { _slice_assign $c $ref $kids }
    }
    set PL($cell) [list $cells $pos]
}

# ── the plans ─────────────────────────────────────────────────────────────
# Every SLICING arrangement of a container's children at their current
# shapes: every ORDERED split of every subset, cut H (A left of B) or V (A
# below B), each subtree centred across its cut -- the tree family
# `_slice` searches, but kept whole instead of reduced to a Pareto curve,
# since two plans of one shape can differ in everything a router cares
# about.  Identical children are interchangeable, so plans are deduplicated
# on the multiset {type, x, y}; each subset keeps only arrangements within
# `slack` of its own smallest.  Returns {W H pos sig} per plan (pos: per
# child, in child order, the offset inside the parent; sig: the slicing
# tree, children named by TYPE -- e.g. `V(H(core_cell,l1_cell),rtr_cell)`
# -- which identifies the arrangement: one tree at one set of child shapes
# is one geometry, and the name survives a change of sizes, so a caller can
# find the SAME arrangement in a perturbed plan list), smallest first.
proc soc_vehicle::enum_plans {cell slack} {
    variable P
    variable SZ
    set cells [dict get [_container_cells] $cell]
    set G [_gap_in $cell]
    set M $P(M)
    set n [llength $cells]
    set full [expr {(1 << $n) - 1}]
    for {set i 0} {$i < $n} {incr i} {
        lassign $SZ([lindex $cells $i]) w h
        set E([expr {1 << $i}]) [list [list $w $h [list [list $i 0 0]] [lindex $cells $i]]]
    }
    for {set m 1} {$m <= $full} {incr m} {
        if {[info exists E($m)]} { continue }
        set cand {}
        for {set a [expr {($m - 1) & $m}]} {$a > 0} {set a [expr {($a - 1) & $m}]} {
            set b [expr {$m ^ $a}]
            foreach la $E($a) {
                lassign $la wa ha pa sa
                foreach lb $E($b) {
                    lassign $lb wb hb pb sb
                    # H: a left of b
                    set w [expr {$wa + $G + $wb}] ; set h [expr {max($ha, $hb)}]
                    set pl {}
                    foreach e $pa { lassign $e i x y ; lappend pl [list $i $x [expr {$y + ($h - $ha)/2}]] }
                    foreach e $pb { lassign $e i x y ; lappend pl [list $i [expr {$x + $wa + $G}] [expr {$y + ($h - $hb)/2}]] }
                    lappend cand [list $w $h $pl "H($sa,$sb)"]
                    # V: a below b
                    set w [expr {max($wa, $wb)}] ; set h [expr {$ha + $G + $hb}]
                    set pl {}
                    foreach e $pa { lassign $e i x y ; lappend pl [list $i [expr {$x + ($w - $wa)/2}] $y] }
                    foreach e $pb { lassign $e i x y ; lappend pl [list $i [expr {$x + ($w - $wb)/2}] [expr {$y + $ha + $G}]] }
                    lappend cand [list $w $h $pl "V($sa,$sb)"]
                }
            }
        }
        set amin ""
        foreach c $cand {
            set ar [expr {[lindex $c 0]*[lindex $c 1]}]
            if {$amin eq "" || $ar < $amin} { set amin $ar }
        }
        set E($m) {}
        array unset seen
        foreach c $cand {
            lassign $c w h pl sig
            if {$w*$h > (1.0 + $slack)*$amin} { continue }
            set key [list $w $h [lsort [lmap e $pl {
                lassign $e i x y ; list [lindex $cells $i] $x $y }]]]
            if {[info exists seen($key)]} { continue }
            set seen($key) 1
            lappend E($m) $c
        }
    }
    set out {}
    foreach c $E($full) {
        lassign $c w h pl sig
        set pos [lrepeat $n {}]
        foreach e $pl { lassign $e i x y ; lset pos $i [list [expr {$x + $M}] [expr {$y + $M}]] }
        lappend out [list [expr {$w + 2*$M}] [expr {$h + 2*$M}] $pos $sig]
    }
    return [lsort -integer -command {apply {{a b} {
        expr {[lindex $a 0]*[lindex $a 1] - [lindex $b 0]*[lindex $b 1]}}}} $out]
}

# The grid packer's own arrangement of the same children, as a plan.
proc soc_vehicle::grid_plan {cell} {
    set cells [dict get [_container_cells] $cell]
    lassign [_pack_geom $cells] w h xs ys
    set pos {}
    foreach x $xs y $ys { lappend pos [list $x $y] }
    return [list $w $h $pos]
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

# Every cell type and every container's children -- the definitions,
# nothing instantiated at the top -- so a design whose top is ONE cell
# (`soc_local.tcl`) is built from the same walk as the whole SoC.
#
# `record_only` records the structure (KIDS, CELLOF) and neither places nor
# declares anything, so the buses can be walked before any container has a
# size (`_pin_loads`).
proc soc_vehicle::define_cells {{record_only 0}} {
    variable P
    variable SZ
    variable KIDS
    variable CELLOF
    variable RECORD_ONLY
    array unset KIDS
    array unset CELLOF
    set RECORD_ONLY $record_only

    # every cell type, leaves first
    if {!$record_only} {
        foreach c [lsort [array names SZ]] {
            lassign $SZ($c) w h
            buda::add_cell $c $w $h
        }
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
}

# ── hierarchy ─────────────────────────────────────────────────────────────
proc soc_vehicle::build_hierarchy {} {
    variable P
    buda::set_die $P(DIEW) $P(DIEH)
    define_cells

    # the top: the quadrants, the l2 and the io block where `_top_geom`
    # put them when `configure` sized the die (one walk, per LAYOUT).
    # The TOP instances, as the same {name cell} pairs `_fill` records for
    # every other level, appended where each one is actually instantiated.
    variable TOP
    variable TOPKIDS
    set TOPKIDS {}
    foreach inst $TOP(pos) {
        lassign $inst name cell x y
        buda::add_inst $name $cell - $x $y
        lappend TOPKIDS [list $name $cell]
    }
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
    variable RECORD_ONLY
    if {[info exists RECORD_ONLY] && $RECORD_ONLY} { return }
    variable PL
    if {[info exists PL($parent)]} {
        # PACK slice: the offsets `_slice_configure` sized the parent from,
        # for the SAME child list (checked, since a different list here
        # would place one shape and declare another).
        lassign $PL($parent) want pos
        if {$want ne $cells} {
            error "soc_vehicle: $parent was packed for {$want}, filled with {$cells}"
        }
    } else {
        set pos [_pack_pos $cells]
    }
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
    variable P
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
        # PACK slice may give a leaf a non-square shape of the same area,
        # spending its padding: then EVERY face must still host the pin's
        # bits at the bit pitch plus FACEPAD (see `_leaf_shapes`).
        set raw [expr {$need - $P(PAD) + min($P(PAD), $P(FACEPAD))}]
        set short [expr {$P(PACK) eq "slice" ? min($w, $h) < $raw
                                             : ($w < $need || $h < $need)}]
        if {$short} {
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
    variable TOP
    return [list clusters $cl leaves $leaves buses $buses \
                 die "$P(DIEW)x$P(DIEH)" layout $P(LAYOUT) \
                 grid "$TOP(nc)x$TOP(nr)" holes $TOP(holes) \
                 band $TOP(band) util $TOP(util)]
}

proc soc_vehicle::banner {what} {
    variable P
    array set d [describe]
    puts "=== $what: NQ=$P(NQ) NC=$P(NC) -- $d(clusters) clusters,\
          $d(leaves) leaf instances, $d(buses) buses, die $d(die)\
          layout $d(layout) grid $d(grid) holes $d(holes)\
          util $d(util) ==="
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
    # failure that no longer exists (Codex P2, #930).  And the channel is
    # not what such a run NEEDS: a dirty bottom-up run at NQ=16 or NQ=32
    # repeatedly supply-dooms ONE segment (8 bits of `pc_0`), which a gap
    # only re-seats incidentally -- so a channel is a MEASURED WORKAROUND
    # the caller names, never a cause (Codex P2 again, #930: this sentence
    # still said "needs a channel" after soc.tcl had retracted it).  The
    # measured curves, the seat, and the cheaper seat-scoped remedy all
    # live in soc.tcl beside the removal.
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
