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
# flow/tcl/bigsoc_lib.tcl — a LARGE SoC, authored as a NETLIST.
#
# `soc_lib.tcl` and `tpu_lib.tcl` each build their design straight into the
# BDB, every block at a coordinate the Tcl computed from the bus widths —
# the engine path, and a floorplan the vehicle's author drew.  This one is
# the FRONT-END path: the design is a hierarchical netlist (`hnet.tcl`),
# emitted as structural Verilog, and NOTHING here says where anything goes
# or how big it is.  Sizes and placement come from `auto_floorplan`, from
# the netlist's own pins (the face rule) and the mock PDK's area model
# (`flow/mockpdk/mock.pdk`), level by level — which is the one piece a
# Verilog-in flow was missing (docs/AUTO_FLOORPLAN.md).
#
# THE SHAPE: the SoC vehicle's quadrants of clusters, with a second and a
# third level of cache, a TPU for an NPU, and memory controllers:
#
#   soc
#   |- quad_<q>  x NQ                          quad_cell
#   |   |- cl_<c>  x NC                        cluster_cell
#   |   |   |- core: dec alu mul regf          core_cell
#   |   |   |   `- l0: tag l0bank_<b> x NB0    l0_cell        <- LEVEL-0 cache, in the core (leaves at depth 4)
#   |   |   |- l1i, l1d: tag + bank_<b> x NB   l1_cell
#   |   |   `- rtr: fi_in xbar fi_out          rtr_cell
#   |   `- l2: tag ctl bank_<b> x NB2          l2_cell        <- LEVEL-2 cache, per quadrant
#   |- l3_<s>  x NL3: tag ctl bank_<b> x NB3   l3_cell        <- LEVEL-3 cache, slices at the top
#   |- mem_<m> x NMC: memctl phy               mem_cell       <- memory controllers
#   |- npu: row_<r> x N (pe x N), feed, wbuf,  npu_cell       <- the TPU (tpu_lib's array) with a DMA
#   |        acc, pipe_<s>_<c>, dma
#   `- io: bridge + p_<k> x NIO                io_blk_cell
#
# Wiring is the SoC's (soc_lib.tcl's lessons kept: a CHAIN through the
# clusters, never a star on a controller; every bank wired; the bridge
# injecting at the chain head) extended upward: each quadrant's L2 talks to
# ONE L3 slice over a dedicated pair of ports (so an L3 face carries NQ/NL3
# links, each on its own pin), the slices form a ring, each slice has its
# memory controller, and the NPU's DMA hangs off slice 0.  Every container
# but the top is a TEMPLATE instantiated several times, so the bottom-up
# family has something to copy at four depths.
#
# Knobs (`configure`, every one `-NAME value` on the command line):
#   NQ NC            quadrants, clusters per quadrant        (the dial)
#   NB0 NB NB2 NB3   banks per L0 (inside the core) / L1 / L2 / L3 slice
#   NL3 NMC          L3 slices, memory controllers (NMC <= NL3)
#   N PIPE           the TPU's N x N array and tail depth
#   NIO              peripherals
#   DW AW IW CW      data / address / instruction / control widths
#   TAW PW WW        the TPU's activation / psum / weight widths
# ============================================================

set _bigsoc_dir [file dirname [file normalize [info script]]]
source [file join $_bigsoc_dir hnet.tcl]

namespace eval bigsoc {
    variable P
    array set P {
        NQ 2   NC 2   NB0 1  NB 2   NB2 4   NB3 4   NL3 2   NMC 2
        N 4    PIPE 1 NIO 4
        DW 32  AW 16  IW 32  CW 8
        TAW 8  PW 24  WW 8
    }
}

proc bigsoc::configure {{overrides {}}} {
    variable P
    foreach {k v} $overrides {
        if {![info exists P($k)]} { error "bigsoc: unknown parameter '$k'" }
        set P($k) $v
    }
    foreach k [array names P] {
        if {![string is integer -strict $P($k)] || $P($k) < 1} {
            error "bigsoc: $k must be an integer >= 1 (got '$P($k)')"
        }
    }
    if {$P(NMC) > $P(NL3)} { error "bigsoc: NMC ($P(NMC)) must not exceed NL3 ($P(NL3)) -- a slice has one controller" }
    if {$P(N) < 2} { error "bigsoc: N must be >= 2" }
}

proc bigsoc::get {k} {
    variable P
    if {![info exists P($k)]} { error "bigsoc: no parameter '$k'" }
    return $P($k)
}

# ── the netlist ───────────────────────────────────────────────────────────
proc bigsoc::build {} {
    variable P
    hnet::reset
    set DW $P(DW); set AW $P(AW); set IW $P(IW); set CW $P(CW)
    set TAW $P(TAW); set PW $P(PW); set WW $P(WW)

    # ── leaves: the interface and nothing else
    hnet::cell dec_cell
    hnet::port dec_cell i_in $IW input
    hnet::port dec_cell out $IW output
    hnet::port dec_cell a_out $AW output
    hnet::cell alu_cell
    hnet::port alu_cell i_in $IW input
    hnet::port alu_cell r_in $DW input
    hnet::port alu_cell out $DW output
    hnet::cell mul_cell
    hnet::port mul_cell out $DW output
    hnet::cell regf_cell
    hnet::port regf_cell m_in $DW input
    hnet::port regf_cell a_in $DW input
    hnet::port regf_cell d_in $DW input
    hnet::port regf_cell out $DW output
    hnet::port regf_cell a_out $AW output
    hnet::cell sram_cell
    hnet::port sram_cell a_in $AW input
    hnet::port sram_cell out $DW output
    hnet::cell l0bank_cell
    hnet::port l0bank_cell a_in $AW input
    hnet::port l0bank_cell out $DW output
    hnet::cell tag_cell
    hnet::port tag_cell a_in $AW input
    hnet::port tag_cell b_out $AW output
    hnet::port tag_cell d_in $DW input
    hnet::port tag_cell d_out $DW output
    hnet::port tag_cell out $DW output
    hnet::port tag_cell m_a_out $AW output
    hnet::port tag_cell m_d_in $DW input
    hnet::cell xbar_cell
    hnet::port xbar_cell in $DW input
    hnet::port xbar_cell q_in $DW input
    hnet::port xbar_cell q_out $DW output
    hnet::port xbar_cell r_in $AW input
    hnet::port xbar_cell r_out $AW output
    hnet::port xbar_cell p_in $CW input
    hnet::port xbar_cell m_in $DW input
    hnet::cell fifo_cell
    hnet::port fifo_cell in $DW input
    hnet::port fifo_cell out $DW output
    hnet::cell l2bank_cell
    hnet::port l2bank_cell a_in $AW input
    hnet::port l2bank_cell out $DW output
    hnet::cell l2ctl_cell
    foreach {nm w d} [list in $DW input d_out $DW output b_out $AW output d_in $DW input \
                           a_out $AW output t_in $DW input up_out $DW output up_in $DW input] {
        hnet::port l2ctl_cell $nm $w $d
    }
    hnet::cell l3bank_cell
    hnet::port l3bank_cell a_in $AW input
    hnet::port l3bank_cell out $DW output
    hnet::cell l3ctl_cell
    foreach {nm w d} [list b_out $AW output d_in $DW input a_out $AW output t_in $DW input \
                           r_in $DW input r_out $DW output m_out $DW output m_in $DW input \
                           n_in $DW input n_out $DW output] {
        hnet::port l3ctl_cell $nm $w $d
    }
    set nqps [expr {int(ceil(double($P(NQ)) / $P(NL3)))}]
    for {set k 0} {$k < $nqps} {incr k} {
        hnet::port l3ctl_cell q_in_$k $DW input
        hnet::port l3ctl_cell q_out_$k $DW output
    }
    hnet::cell memctl_cell
    foreach {nm w d} [list in $DW input out $DW output phy_out $DW output phy_in $DW input] {
        hnet::port memctl_cell $nm $w $d
    }
    hnet::cell phy_cell
    hnet::port phy_cell in $DW input
    hnet::port phy_cell out $DW output
    hnet::cell io_cell
    hnet::port io_cell in $CW input
    hnet::port io_cell out $CW output
    hnet::cell bridge_cell
    hnet::port bridge_cell out $CW output
    hnet::port bridge_cell n_out $DW output
    # the TPU's leaves (tpu_lib.tcl's)
    hnet::cell pe_cell
    foreach {nm w d} [list a_in $TAW input a_out $TAW output p_in $PW input p_out $PW output \
                           w_in $WW input w_out $WW output] {
        hnet::port pe_cell $nm $w $d
    }
    hnet::cell feed_cell
    hnet::port feed_cell in $TAW input
    hnet::port feed_cell out $TAW output
    hnet::cell wbuf_cell
    hnet::port wbuf_cell in $WW input
    hnet::port wbuf_cell out $WW output
    hnet::cell acc_cell
    hnet::port acc_cell in $PW input
    hnet::port acc_cell out $PW output
    hnet::cell dma_cell
    hnet::port dma_cell m_out $DW output
    hnet::port dma_cell m_in $DW input
    for {set r 0} {$r < $P(N)} {incr r} { hnet::port dma_cell a_out_$r $TAW output }
    for {set c 0} {$c < $P(N)} {incr c} {
        hnet::port dma_cell w_out_$c $WW output
        hnet::port dma_cell p_in_$c $PW input
    }

    # ── L0: a tag and NB0 banks INSIDE the core -- the register file reads
    # it, its misses go out through the core's ports to the L1.  A fifth
    # level of cells above the standard cells (soc / quad / cluster / core /
    # l0 / bank).
    hnet::cell l0_cell
    foreach {nm w d} [list a_in $AW input d_out $DW output m_a_out $AW output m_d_in $DW input] {
        hnet::port l0_cell $nm $w $d
    }
    hnet::inst l0_cell tag tag_cell
    hnet::net l0_cell .a_in tag.a_in
    hnet::net l0_cell .d_out tag.d_out
    hnet::net l0_cell .m_a_out tag.m_a_out
    hnet::net l0_cell .m_d_in tag.m_d_in
    for {set b 0} {$b < $P(NB0)} {incr b} {
        hnet::inst l0_cell bank_$b l0bank_cell
        hnet::net l0_cell tag.b_out bank_$b.a_in
        hnet::net l0_cell bank_$b.out tag.d_in
    }

    # ── core: soc_lib's four buses, the L0, the cache ports out
    hnet::cell core_cell
    foreach {nm w d} [list ia_out $AW output da_out $AW output i_in $IW input d_in $DW input] {
        hnet::port core_cell $nm $w $d
    }
    foreach {i c} {dec dec_cell alu alu_cell mul mul_cell regf regf_cell l0 l0_cell} { hnet::inst core_cell $i $c }
    hnet::net core_cell dec.out alu.i_in
    hnet::net core_cell mul.out regf.m_in
    hnet::net core_cell regf.out alu.r_in
    hnet::net core_cell alu.out regf.a_in
    hnet::net core_cell .ia_out dec.a_out
    hnet::net core_cell .i_in dec.i_in
    hnet::net core_cell regf.a_out l0.a_in
    hnet::net core_cell l0.d_out regf.d_in
    hnet::net core_cell .da_out l0.m_a_out
    hnet::net core_cell .d_in l0.m_d_in

    # ── L1: a tag and NB banks, every bank addressed by the tag and read
    # back through it (never straight to the core: soc_lib's star)
    hnet::cell l1_cell
    foreach {nm w d} [list a_in $AW input d_out $DW output out $DW output] { hnet::port l1_cell $nm $w $d }
    hnet::inst l1_cell tag tag_cell
    hnet::net l1_cell .a_in tag.a_in
    hnet::net l1_cell .d_out tag.d_out
    hnet::net l1_cell .out tag.out
    for {set b 0} {$b < $P(NB)} {incr b} {
        hnet::inst l1_cell bank_$b sram_cell
        hnet::net l1_cell tag.b_out bank_$b.a_in
        hnet::net l1_cell bank_$b.out tag.d_in
    }

    # ── router: inbound fifo -> crossbar -> outbound fifo
    hnet::cell rtr_cell
    foreach {nm w d} [list in $DW input nl_in $DW input nl_out $DW output nr_in $AW input \
                           nr_out $AW output pc_in $CW input mr_in $DW input] {
        hnet::port rtr_cell $nm $w $d
    }
    foreach {i c} {fi_in fifo_cell xbar xbar_cell fi_out fifo_cell} { hnet::inst rtr_cell $i $c }
    hnet::net rtr_cell fi_in.out xbar.q_in
    hnet::net rtr_cell xbar.q_out fi_out.in
    hnet::net rtr_cell .in xbar.in
    hnet::net rtr_cell .nl_in fi_in.in
    hnet::net rtr_cell .nl_out fi_out.out
    hnet::net rtr_cell .nr_in xbar.r_in
    hnet::net rtr_cell .nr_out xbar.r_out
    hnet::net rtr_cell .pc_in xbar.p_in
    hnet::net rtr_cell .mr_in xbar.m_in

    # ── cluster: a core, its two caches, its router
    hnet::cell cluster_cell
    foreach {nm w d} [list nl_in $DW input nl_out $DW output nr_in $AW input nr_out $AW output \
                           pc_in $CW input mr_in $DW input] {
        hnet::port cluster_cell $nm $w $d
    }
    foreach {i c} {core core_cell l1i l1_cell l1d l1_cell rtr rtr_cell} { hnet::inst cluster_cell $i $c }
    hnet::net cluster_cell core.ia_out l1i.a_in
    hnet::net cluster_cell core.da_out l1d.a_in
    hnet::net cluster_cell l1i.d_out core.i_in
    hnet::net cluster_cell l1d.d_out core.d_in
    hnet::net cluster_cell l1d.out rtr.in
    foreach p {nl_in nl_out nr_in nr_out pc_in mr_in} { hnet::net cluster_cell .$p rtr.$p }

    # ── L2: a controller, a tag and NB2 banks (soc_lib's L2, with a link up)
    hnet::cell l2_cell
    foreach {nm w d} [list in $DW input out $DW output up_out $DW output up_in $DW input] {
        hnet::port l2_cell $nm $w $d
    }
    foreach {i c} {tag tag_cell ctl l2ctl_cell} { hnet::inst l2_cell $i $c }
    hnet::net l2_cell .in ctl.in
    hnet::net l2_cell .out ctl.d_out
    hnet::net l2_cell .up_out ctl.up_out
    hnet::net l2_cell .up_in ctl.up_in
    hnet::net l2_cell ctl.a_out tag.a_in
    hnet::net l2_cell tag.d_out ctl.t_in
    for {set b 0} {$b < $P(NB2)} {incr b} {
        hnet::inst l2_cell bank_$b l2bank_cell
        hnet::net l2_cell ctl.b_out bank_$b.a_in
        hnet::net l2_cell bank_$b.out ctl.d_in
    }

    # ── quadrant: NC clusters chained, the chain's head into the L2
    hnet::cell quad_cell
    foreach {nm w d} [list pn_in $DW input up_out $DW output up_in $DW input] { hnet::port quad_cell $nm $w $d }
    for {set c 0} {$c < $P(NC)} {incr c} {
        hnet::port quad_cell pc_in_$c $CW input
        hnet::inst quad_cell cl_$c cluster_cell
        hnet::net quad_cell .pc_in_$c cl_$c.pc_in
    }
    hnet::inst quad_cell l2 l2_cell
    hnet::net quad_cell .pn_in cl_0.nl_in
    for {set c 0} {$c < $P(NC) - 1} {incr c} {
        hnet::net quad_cell cl_$c.nl_out cl_[expr {$c+1}].nl_in
        hnet::net quad_cell cl_[expr {$c+1}].nr_out cl_$c.nr_in
    }
    set head cl_[expr {$P(NC)-1}]
    hnet::net quad_cell $head.nl_out l2.in
    hnet::net quad_cell l2.out $head.mr_in
    hnet::net quad_cell l2.up_out .up_out
    hnet::net quad_cell .up_in l2.up_in

    # ── L3 slice: a controller, a tag and NB3 banks; NQ/NL3 quadrant links,
    # the ring, the memory link, and (slice 0) the NPU link
    hnet::cell l3_cell
    foreach {nm w d} [list r_in $DW input r_out $DW output m_out $DW output m_in $DW input \
                           n_in $DW input n_out $DW output] {
        hnet::port l3_cell $nm $w $d
    }
    foreach {i c} {tag tag_cell ctl l3ctl_cell} { hnet::inst l3_cell $i $c }
    foreach p {r_in r_out m_out m_in n_in n_out} { hnet::net l3_cell .$p ctl.$p }
    for {set k 0} {$k < $nqps} {incr k} {
        hnet::port l3_cell q_in_$k $DW input
        hnet::port l3_cell q_out_$k $DW output
        hnet::net l3_cell .q_in_$k ctl.q_in_$k
        hnet::net l3_cell .q_out_$k ctl.q_out_$k
    }
    hnet::net l3_cell ctl.a_out tag.a_in
    hnet::net l3_cell tag.d_out ctl.t_in
    for {set b 0} {$b < $P(NB3)} {incr b} {
        hnet::inst l3_cell bank_$b l3bank_cell
        hnet::net l3_cell ctl.b_out bank_$b.a_in
        hnet::net l3_cell bank_$b.out ctl.d_in
    }

    # ── memory controller + PHY
    hnet::cell mem_cell
    hnet::port mem_cell in $DW input
    hnet::port mem_cell out $DW output
    hnet::inst mem_cell memctl memctl_cell
    hnet::inst mem_cell phy phy_cell
    hnet::net mem_cell .in memctl.in
    hnet::net mem_cell .out memctl.out
    hnet::net mem_cell memctl.phy_out phy.in
    hnet::net mem_cell phy.out memctl.phy_in

    # ── the NPU: tpu_lib's array as a cell, with a DMA at its edges
    set N $P(N)
    hnet::cell row_cell
    hnet::port row_cell a_in $TAW input
    for {set c 0} {$c < $N} {incr c} {
        hnet::port row_cell p_in_$c $PW input
        hnet::port row_cell p_out_$c $PW output
        hnet::port row_cell w_in_$c $WW input
        hnet::port row_cell w_out_$c $WW output
        hnet::inst row_cell pe_$c pe_cell
    }
    hnet::net row_cell .a_in pe_0.a_in
    for {set c 0} {$c < $N - 1} {incr c} { hnet::net row_cell pe_$c.a_out pe_[expr {$c+1}].a_in }
    for {set c 0} {$c < $N} {incr c} {
        hnet::net row_cell .p_in_$c pe_$c.p_in
        hnet::net row_cell pe_$c.p_out .p_out_$c
        hnet::net row_cell .w_in_$c pe_$c.w_in
        hnet::net row_cell pe_$c.w_out .w_out_$c
    }
    hnet::cell npu_cell
    hnet::port npu_cell m_out $DW output
    hnet::port npu_cell m_in $DW input
    hnet::inst npu_cell dma dma_cell
    hnet::net npu_cell .m_out dma.m_out
    hnet::net npu_cell .m_in dma.m_in
    for {set r 0} {$r < $N} {incr r} {
        hnet::inst npu_cell row_$r row_cell
        hnet::inst npu_cell feed_$r feed_cell
        hnet::net npu_cell dma.a_out_$r feed_$r.in
        hnet::net npu_cell feed_$r.out row_$r.a_in
    }
    for {set c 0} {$c < $N} {incr c} {
        hnet::inst npu_cell wbuf_$c wbuf_cell
        hnet::net npu_cell dma.w_out_$c wbuf_$c.in
        hnet::net npu_cell wbuf_$c.out row_0.w_in_$c
        for {set r 0} {$r < $N - 1} {incr r} {
            hnet::net npu_cell row_$r.p_out_$c row_[expr {$r+1}].p_in_$c
            hnet::net npu_cell row_$r.w_out_$c row_[expr {$r+1}].w_in_$c
        }
        hnet::inst npu_cell acc_$c acc_cell
        hnet::net npu_cell row_[expr {$N-1}].p_out_$c acc_$c.in
        set prev acc_$c
        for {set s 0} {$s < $P(PIPE)} {incr s} {
            hnet::inst npu_cell pipe_${s}_$c acc_cell
            hnet::net npu_cell $prev.out pipe_${s}_$c.in
            set prev pipe_${s}_$c
        }
        hnet::net npu_cell $prev.out dma.p_in_$c
    }

    # ── the peripherals
    hnet::cell io_blk_cell
    hnet::port io_blk_cell n_out $DW output
    hnet::inst io_blk_cell bridge bridge_cell
    hnet::net io_blk_cell .n_out bridge.n_out
    set fan [list bridge.out]
    for {set k 0} {$k < $P(NIO)} {incr k} {
        hnet::port io_blk_cell pc_out_$k $CW output
        hnet::inst io_blk_cell p_$k io_cell
        hnet::net io_blk_cell p_$k.out .pc_out_$k
        lappend fan p_$k.in
    }
    hnet::net io_blk_cell {*}$fan

    # ── the top
    hnet::cell soc
    for {set q 0} {$q < $P(NQ)} {incr q} { hnet::inst soc quad_$q quad_cell }
    for {set s 0} {$s < $P(NL3)} {incr s} { hnet::inst soc l3_$s l3_cell }
    for {set m 0} {$m < $P(NMC)} {incr m} { hnet::inst soc mem_$m mem_cell }
    hnet::inst soc npu npu_cell
    hnet::inst soc io io_blk_cell
    for {set q 0} {$q < $P(NQ)} {incr q} {
        set s [expr {$q % $P(NL3)}]
        set k [expr {$q / $P(NL3)}]
        hnet::net soc quad_$q.up_out l3_$s.q_in_$k
        hnet::net soc l3_$s.q_out_$k quad_$q.up_in
    }
    for {set s 0} {$s < $P(NL3)} {incr s} {
        if {$P(NL3) > 1} {
            hnet::net soc l3_$s.r_out l3_[expr {($s + 1) % $P(NL3)}].r_in
        }
        set m [expr {$s % $P(NMC)}]
        if {$s < $P(NMC)} {
            hnet::net soc l3_$s.m_out mem_$m.in
            hnet::net soc mem_$m.out l3_$s.m_in
        }
    }
    hnet::net soc npu.m_out l3_0.n_in
    hnet::net soc l3_0.n_out npu.m_in
    hnet::net soc io.n_out quad_0.pn_in
    for {set k 0} {$k < $P(NIO)} {incr k} {
        set q [expr {$k % $P(NQ)}]
        set c [expr {($k / $P(NQ)) % $P(NC)}]
        hnet::net soc io.pc_out_$k quad_$q.pc_in_$c
    }
    hnet::top soc
}

# ── the NPU's geometry, by the ARRAY rule the repository already has ─────
# A systolic array is not a job for a placer: `tpu_lib.tcl` places it by
# rule (PEs at a pitch along a row, rows stacked, feeders west, weight
# buffers north, accumulators south on their columns, the tail below), and
# that rule is reused here VERBATIM -- `tpu_vehicle::configure` sizes the
# blocks from the same widths and `leaf_instances` walks the same
# placement -- written into the BDB as FIXED templates (`resize_cell` +
# `add_inst_to_cell`) that `auto_floorplan fixed ...` stamps untouched,
# while everything around the array is placed by the engine.  The DMA
# (not in tpu_lib) sits below the tail, as wide as the array.
proc bigsoc::npu_fixed_templates {} {
    variable P
    set dir [file dirname [file normalize [info script]]]
    if {![llength [info procs ::tpu_vehicle::configure]]} {
        uplevel #0 [list source [file join $dir tpu_lib.tcl]]
    }
    tpu_vehicle::configure [list N $P(N) AW $P(TAW) PW $P(PW) WW $P(WW) PIPE $P(PIPE) EDGEIN 1 X0 24 Y0 24]
    array set T [tpu_vehicle::configure]
    foreach cs [tpu_vehicle::cell_sizes] {
        lassign $cs cell w h
        buda::resize_cell $cell $w $h
    }
    # the row: N PEs west to east
    buda::resize_cell row_cell $T(RW) $T(RH)
    for {set c 0} {$c < $T(N)} {incr c} {
        buda::add_inst_to_cell row_cell pe_$c pe_cell [expr {$T(ROWM) + $c*$T(PPX)}] $T(ROWM)
    }
    # the array with its edges and tail, every instance where tpu_lib puts
    # it; the DMA below the tail.  tpu_lib's DIEH budgets the tail; the
    # cell is that plus the DMA.
    set dmah [expr {$T(ACCH) + 2*$T(PEPAD)}]
    set W $T(DIEW)
    set H [expr {$T(DIEH) + $dmah + $T(PIPEGAP)}]
    buda::resize_cell dma_cell [expr {$W - 2*$T(X0)}] $dmah
    buda::resize_cell npu_cell $W $H
    foreach inst [tpu_vehicle::leaf_instances] {
        lassign $inst name cell x y
        if {[string match "row_*/pe_*" $name]} { continue }
        buda::add_inst_to_cell npu_cell $name $cell $x $y
    }
    for {set r 0} {$r < $T(N)} {incr r} {
        buda::add_inst_to_cell npu_cell row_$r row_cell $T(AX) [expr {$T(AY) + $r*$T(RPY)}]
    }
    buda::add_inst_to_cell npu_cell dma dma_cell $T(X0) [expr {$T(DIEH) + $T(PIPEGAP)}]
    return [list pe_cell feed_cell wbuf_cell acc_cell dma_cell row_cell npu_cell]
}

proc bigsoc::banner {what} {
    variable P
    array set c [hnet::census]
    return "=== $what: NQ=$P(NQ) NC=$P(NC) N=$P(N) NL3=$P(NL3) -- $c(cells) cell types,\
            $c(insts) instances, $c(leaves) leaf instances, $c(nets) buses,\
            $c(bits) bits ==="
}

proc bigsoc::emit {path} {
    variable P
    set b "// bigsoc.v -- generated by flow/tcl/bigsoc.tcl -emit; do not edit.\n//\n"
    append b "// A large SoC: NQ=$P(NQ) quadrants of NC=$P(NC) clusters (core + L1i/L1d), an L2 per\n"
    append b "// quadrant, NL3=$P(NL3) L3 slices, NMC=$P(NMC) memory controllers, an NPU (a\n"
    append b "// $P(N)x$P(N) TPU array) and $P(NIO) peripherals.  Widths DW=$P(DW) AW=$P(AW) IW=$P(IW) CW=$P(CW).\n"
    append b "// Regenerate with:  btcl flow/tcl/bigsoc.tcl $P(NQ) -NC $P(NC) -N $P(N) -emit <file>\n"
    hnet::emit_verilog $path $b
}

# ── verdict helpers (soc_lib's three legs) ───────────────────────────────
proc bigsoc::_state {} {
    return "[buda::query overlaps] overlaps, [buda::query unplaced] unplaced,\
            [buda::query violations] audit violations"
}
proc bigsoc::is_dirty {} {
    return [expr {[buda::query overlaps] > 0 || [buda::query unplaced] > 0
                  || [buda::query violations] != 0}]
}
proc bigsoc::heal_if_dirty {who} {
    if {![is_dirty]} { return 0 }
    puts "$who: dirty ([_state]) -- healing"
    buda::negotiate_congestion 10
    buda::ripup_reroute 20
    buda::check_design dnuts
    if {![is_dirty]} { return 1 }
    puts "$who: still dirty ([_state]) -- second round"
    buda::refine_selection
    buda::negotiate_congestion 10
    buda::ripup_reroute 20
    buda::refine_selection
    buda::check_design dnuts
    return 1
}
proc bigsoc::verdict_abstract {who} {
    set ov [buda::query overlaps]
    set vi [buda::query violations]
    set se [buda::query seats]
    buda::stop
    if {$ov != 0 || $vi != 0} {
        puts stderr "$who: FAILED (abstract) -- $ov overlaps, $vi audit violations, $se seat faults"
        exit 1
    }
    puts "$who: clean (abstract) -- 0 overlaps, 0 audit violations, $se seat faults"
    return 0
}
proc bigsoc::verdict {who} {
    set ov [buda::query overlaps]
    set un [buda::query unplaced]
    set vi [buda::query violations]
    buda::stop
    if {$ov != 0 || $un != 0 || $vi != 0} {
        puts stderr "$who: FAILED -- $ov overlaps, $un unplaced, $vi audit violations"
        exit 1
    }
    puts "$who: clean -- 0 overlaps, 0 unplaced, 0 audit violations"
    return 0
}
