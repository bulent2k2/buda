# ============================================================
# soc_local.tcl — ONE cell of the SoC vehicle, routed alone
# ============================================================
#
#   btcl flow/tcl/soc_local.tcl <cell> [-plan <k>|grid] [-list] \
#        [-ref <inst>] [-bdb <file>] [-noheal] [-slack <f>] [-ring <r>] \
#        [-NAME value ...]
#
# What a slicing plan for a container is worth depends on what its children
# say to each other and to the world outside, and the whole-chip run cannot
# tell a plan's own cost from everything else in the die.  This builds a
# design whose top is ONE instance `u` of `<cell>` and routes it with the
# same hier flow `soc.tcl` runs:
#
#   * the INTERNAL buses are the ones `build_buses` gives a representative
#     instance of the cell (`-ref`, defaults below), prefix rewritten to `u`;
#   * every EXTERNAL bus -- one endpoint inside the instance, one outside --
#     ends on a PORT block in a ring round the cell, on the side where the
#     outside endpoint actually sits in the full-chip layout the same knobs
#     build, at its projection onto that side.  So the plan is priced
#     against the world it will live in, and a plan that turns the router's
#     back on the NoC pays for it here.
#
# `-ring r` is the die margin round the cell (default 36, the least that
# holds the ports): the room a cell's buses may detour into, which in the
# chip is its neighbours' TOP metal.
#
# `-plan k` picks the k-th slicing plan of the cell's children
# (`soc_vehicle::enum_plans`: every ordered slicing arrangement within
# `-slack` of the smallest, deduplicated, smallest first); `-plan grid` is
# the grid packer's own arrangement, the baseline; `-list` prints the plans
# and exits without routing.  The routed result lands in `-bdb` (a file,
# so `tools/cell_face_demand.py` can measure it from the stored bit-wires);
# the driver prints one `LOCAL` line with the verdict and the runtime.
#
# Knobs are soc.tcl's (`-PAD 10 -GAP 4 -M 4 -PACK slice ...`), validated by
# the same `configure`.
# ============================================================

set repo [file dirname [file dirname [file dirname [file normalize [info script]]]]]
source [file join $repo tools buda.tcl]
source [file join $repo flow tcl soc_lib.tcl]

# Absolute {cell x1 y1 x2 y2} of every instance path in the full-chip layout
# `configure` built -- where the outside endpoints of a cell's buses are.
proc soc_vehicle::abs_boxes {} {
    variable TOP
    set out [dict create]
    foreach inst $TOP(pos) {
        lassign $inst name cell x y
        _abs_walk $name $cell $x $y out
    }
    return $out
}
proc soc_vehicle::_abs_walk {path cell x y outn} {
    upvar 1 $outn out
    variable SZ
    variable PL
    lassign $SZ($cell) w h
    dict set out $path [list $cell $x $y [expr {$x + $w}] [expr {$y + $h}]]
    set kids [_container_cells]
    if {![dict exists $kids $cell]} { return }
    set cells [dict get $kids $cell]
    if {[info exists PL($cell)]} { lassign $PL($cell) _ pos } \
                            else { set pos [_pack_pos $cells] }
    variable KIDS
    foreach nc $KIDS($cell) xy $pos {
        lassign $nc nm c
        lassign $xy cx cy
        _abs_walk $path/$nm $c [expr {$x + $cx}] [expr {$y + $cy}] out
    }
}

# ── the command line ──────────────────────────────────────────────────────
set cell [lindex $argv 0]
set plan grid
set list_only 0
set ref ""
set bdb ""
set heal 1
set slack 0.15
set ring 36
set at local
set shift {0 0}
set overrides {NQ 8 LAYOUT compact}
set argi 1
while {$argi < $argc} {
    set opt [lindex $argv $argi]
    switch -- $opt {
        -plan   { set plan  [lindex $argv [incr argi]] ; incr argi }
        -list   { set list_only 1 ; incr argi }
        -ref    { set ref   [lindex $argv [incr argi]] ; incr argi }
        -bdb    { set bdb   [lindex $argv [incr argi]] ; incr argi }
        -slack  { set slack [lindex $argv [incr argi]] ; incr argi }
        -ring   { set ring  [lindex $argv [incr argi]] ; incr argi }
        -at     { set at    [lindex $argv [incr argi]] ; incr argi }
        -shift  { set shift [list [lindex $argv [incr argi]] [lindex $argv [incr argi]]] ; incr argi }
        -noheal { set heal 0 ; incr argi }
        default {
            if {[string index $opt 0] ne "-" || $argi + 1 >= $argc} {
                error "soc_local.tcl: bad argument '$opt'"
            }
            lappend overrides [string range $opt 1 end] [lindex $argv [incr argi]]
            incr argi
        }
    }
}
# A representative occurrence per container: one in the MIDDLE of the NoC
# chain with an io pad on it, so its router has every kind of neighbour --
# quadrant 1, or quadrant 0 at NQ 1 where there is no other (resolved after
# `configure`, which is what knows NQ).
set defref {
    core_cell quad_1/cl_0/core   l1_cell quad_1/cl_0/l1d   rtr_cell quad_1/cl_0/rtr
    cluster_cell quad_1/cl_0     quad_cell quad_1          l2_cell l2   io_blk_cell io
}
set ref_default [expr {$ref eq ""}]
if {$ref_default} {
    if {![dict exists $defref $cell]} { error "soc_local.tcl: '$cell' is not a container" }
    set ref [dict get $defref $cell]
}

# A plan is chosen through the vehicle's own `FIX` knob, so plan k here is
# the arrangement `soc.tcl -PACK slice -FIX {<cell> k}` builds -- the same
# child shapes, the same enumeration, one index meaning one geometry.
if {$plan ne "current" || $list_only} {
    set fixspec [expr {$list_only ? 0 : $plan}]
    set fix {}
    if {[dict exists $overrides FIX]} { set fix [dict get $overrides FIX] }
    dict set fix $cell $fixspec
    dict set overrides FIX $fix
    dict set overrides FIXSLACK $slack
    if {![dict exists $overrides PACK]} { dict set overrides PACK slice }
}
soc_vehicle::configure $overrides
if {$ref_default && [soc_vehicle::get NQ] < 2} {
    set ref [string map {quad_1 quad_0} $ref]
}

# The buses and the layout, from a DRY pass: the same `define_cells` and
# `build_buses` the SoC runs, with the engine calls caught rather than sent.
namespace eval buda {}
set ::CAP {}
foreach p {add_cell add_inst_to_cell bdb_net_mode} { proc buda::$p args {} }
proc buda::add_bus {name drv rcv} { lappend ::CAP [list $name $drv $rcv] }
soc_vehicle::define_cells
set ::soc_vehicle::TOPKIDS {}
soc_vehicle::build_buses
foreach p {add_cell add_inst_to_cell bdb_net_mode add_bus} { rename buda::$p "" }
set boxes [soc_vehicle::abs_boxes]
if {![dict exists $boxes $ref]} { error "soc_local.tcl: no instance '$ref'" }
if {[lindex [dict get $boxes $ref] 0] ne $cell} {
    error "soc_local.tcl: '$ref' is a [lindex [dict get $boxes $ref] 0], not a $cell"
}

# The plan.
set plans [soc_vehicle::enum_plans $cell $slack]
if {$list_only} {
    lassign [soc_vehicle::grid_plan $cell] w h pos
    puts "PLAN grid $w $h [expr {$w*$h}] $pos"
    set k 0
    foreach p $plans {
        lassign $p w h pos sig
        puts "PLAN $k $w $h [expr {$w*$h}] $pos SIG $sig"
        incr k
    }
    exit 0
}
# what `configure` built: the FIXed plan, PACK slice's own choice, or the
# grid packer's
if {[info exists ::soc_vehicle::PL($cell)]} {
    lassign $::soc_vehicle::SZ($cell) W H
    set pos [lindex $::soc_vehicle::PL($cell) 1]
} else {
    lassign [soc_vehicle::grid_plan $cell] W H pos
}

# ── the buses, split: internal (rewritten to `u`) and external (to ports)
proc under {path ref} { expr {[string first "$ref/" $path] == 0} }
proc localize {pin ref} { return "u[string range $pin [string length $ref] end]" }
lassign [dict get $boxes $ref] _ rx1 ry1 rx2 ry2
set rcx [expr {($rx1 + $rx2)/2.0}] ; set rcy [expr {($ry1 + $ry2)/2.0}]
set internal {} ; set external {}
foreach b $::CAP {
    lassign $b name drv rcv
    set din [under $drv $ref] ; set rin [under $rcv $ref]
    if {!$din && !$rin} { continue }
    regexp {\[(\d+)\]$} $name -> bits
    if {$din && $rin} {
        lappend internal [list $name [localize $drv $ref] [localize $rcv $ref]]
        continue
    }
    # the outside endpoint's instance, and where it sits
    set out [expr {$din ? $rcv : $drv}]
    set opath [lindex [split $out .] 0]
    lassign [dict get $boxes $opath] _ ox1 oy1 ox2 oy2
    set dx [expr {($ox1 + $ox2)/2.0 - $rcx}] ; set dy [expr {($oy1 + $oy2)/2.0 - $rcy}]
    set side [expr {abs($dx) >= abs($dy) ? ($dx > 0 ? "E" : "W") : ($dy > 0 ? "N" : "S")}]
    lappend external [list $name $bits $din [localize [expr {$din ? $drv : $rcv}] $ref] \
                           $side $dx $dy $out]
}

# ── the ports: a leaf per external bus, long enough to host the bus's bits
# (the face rule), at the projection of the outside endpoint onto its side,
# slid apart where two collide.  A side's ports stay inside the cell's own
# extent along that side, and ones that do not fit start a further TIER
# out (inner face RG + t x (D + 4) off the cell), the ring growing to hold
# them -- so no port leaves the die, and none can meet the next side's
# ports at a corner (a run slid past the cell's end used to do both).
set D 16 ; set RG 12
set tiers {}
set ntier 1
foreach side {N S E W} {
    set horiz [expr {$side in {N S}}]
    set span [expr {$horiz ? $W : $H}]
    set onside {}
    set k 0
    foreach e $external {
        lassign $e name bits din pin s dx dy
        if {$s ne $side} { incr k ; continue }
        set len [soc_vehicle::_dim $bits]
        set want [expr {int(round($span/2.0 + ($horiz ? $dx : $dy) - $len/2.0))}]
        set want [expr {max(0, min($span - $len, $want))}]
        lappend onside [list $want $len $k]
        incr k
    }
    set t 0 ; set cur 0
    foreach o [lsort -integer -index 0 $onside] {
        lassign $o want len k
        set a [expr {max($want, $cur)}]
        if {$a + $len > $span && $cur > 0} {
            incr t ; set cur 0
            set a [expr {max(0, min($span - $len, $want))}]
        }
        lappend tiers [list $side $t $a $len $k]
        set cur [expr {$a + $len + 4}]
    }
    set ntier [expr {max($ntier, $t + 1)}]
}
set R [expr {max($ring, $RG + $ntier*($D + 4) + 8)}]
# Where `u` sits: at the ring's corner (`-at local`), or at the exact spot
# its representative occupies in the chip (`-at chip`) -- the TRACK PHASE,
# since the patterns are anchored to the die origin and a seat one track
# short of its bus strands every bit -- plus `-shift dx dy`.
lassign $shift sdx sdy
if {$at eq "chip"} {
    set OX [expr {max($R, int($rx1)) + $sdx}] ; set OY [expr {max($R, int($ry1)) + $sdy}]
} else {
    set OX [expr {$R + $sdx}] ; set OY [expr {$R + $sdy}]
}
set DIEW [expr {$OX + $W + $R}] ; set DIEH [expr {$OY + $H + $R}]
set ports {}
foreach o $tiers {
    lassign $o side t a len k
    set off [expr {$RG + $t*($D + 4)}]
    switch $side {
        N { set x [expr {$OX + $a}] ; set y [expr {$OY + $H + $off}] ; set pw $len ; set ph $D }
        S { set x [expr {$OX + $a}] ; set y [expr {$OY - $off - $D}] ; set pw $len ; set ph $D }
        E { set x [expr {$OX + $W + $off}] ; set y [expr {$OY + $a}] ; set pw $D ; set ph $len }
        W { set x [expr {$OX - $off - $D}] ; set y [expr {$OY + $a}] ; set pw $D ; set ph $len }
    }
    lappend ports [list pt_$k $x $y $pw $ph]
}

# ── the run ───────────────────────────────────────────────────────────────
puts "=== soc_local.tcl: $cell as $ref, plan $plan ${W}x${H}, [llength $internal]\
      internal + [llength $external] external buses ==="
foreach e $external {
    lassign $e name bits din pin side dx dy out
    puts "EXT $name $bits [expr {$din ? "out" : "in"}] $pin $side $out"
}
buda::start
soc_vehicle::declare_stack
buda::open_bdb [expr {$bdb eq "" ? ":memory:" : $bdb}]
buda::set_die $DIEW $DIEH
soc_vehicle::define_cells
set shapes {}
foreach p $ports {
    lassign $p nm x y pw ph
    set pc port_${pw}x${ph}
    if {$pc ni $shapes} { buda::add_cell $pc $pw $ph ; lappend shapes $pc }
    buda::add_inst $nm $pc - $x $y
}
buda::add_inst u $cell - $OX $OY
soc_vehicle::derive_interface
soc_vehicle::load_blocks
buda::bdb_net_mode on
foreach b $internal { buda::add_bus {*}$b }
set k 0
foreach e $external {
    lassign $e name bits din pin
    if {$din} { buda::add_bus $name $pin pt_$k.p } else { buda::add_bus $name pt_$k.p $pin }
    incr k
}
set t0 [clock milliseconds]
buda::run_hier_bundler depth 4
buda::generate_hier_topologies
buda::run_planner hier 5
buda::run_nuts
buda::run_detailed_nuts
buda::check_design dnuts
set fo [buda::query overlaps] ; set fu [buda::query unplaced]
if {$heal} { soc_vehicle::heal_if_dirty "soc_local.tcl" }
buda::report_wirelength
set secs [expr {([clock milliseconds] - $t0)/1000.0}]
set eo [buda::query overlaps] ; set eu [buda::query unplaced] ; set ev [buda::query violations]
buda::stop
puts "LOCAL cell=$cell plan=$plan w=$W h=$H area=[expr {$W*$H}] first=${fo}o/${fu}u\
      end=${eo}o/${eu}u/${ev}v secs=$secs"
