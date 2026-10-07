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
# flow/tcl/hnet.tcl — author a HIERARCHICAL NETLIST in Tcl, emit it as
# structural Verilog.
#
# The vehicles before this one (`tpu_lib.tcl`, `soc_lib.tcl`) build their
# design straight into the BDB with `add_cell` / `add_inst_to_cell` /
# `add_bus`, every instance at a coordinate the Tcl computed.  That is the
# ENGINE path, and it presumes a floorplan.  The front end a chip team
# actually starts from is a NETLIST and a PDK, with no coordinate anywhere
# — so this library authors the netlist alone, as modules:
#
#   hnet::cell   NAME                       a module (a cell TYPE)
#   hnet::port   CELL NAME WIDTH DIR        one of its ports (input|output|inout)
#   hnet::inst   CELL INST CHILD_CELL       a child instance inside it
#   hnet::net    CELL {inst.port | .port} ...   a connection inside it:
#                                           child pins and/or the cell's own
#                                           ports, every endpoint the same
#                                           width; the first endpoint is the
#                                           driver by convention (a reader
#                                           infers direction from the port
#                                           declarations, not from this)
#   hnet::top    CELL                       which module is the top
#   hnet::emit_verilog PATH                 write it all out
#   hnet::census                            {cells N insts N leaves N nets N bits N}
#
# The Verilog is STRUCTURAL and FLAT PER MODULE — no `generate`, every
# instance written out, every port map explicit — because that is the only
# form `import_verilog` reads: it does not elaborate `generate`, so a
# parameterized array would import as ONE instance with its neighbour
# links dropped (BUDA-1610).  A module used N times is written ONCE and
# instantiated N times, which is exactly the repetition a synthesized
# netlist loses (uniquification) and the one thing the hierarchical flow
# needs most: `import_verilog` keeps it as N congruent instances of one
# cell, so `auto_floorplan` places the cell once and the bottom-up family
# solves it once.
#
# Unconnected ports are OMITTED from an instance's port map rather than
# written `.port()`: the empty form makes the reader count a connection
# naming no net (BUDA-1610) and elaborates an orphan net no bundle can
# carry (tpu_lib.tcl learned this on its first cut — 32 warnings and 256
# dangling nets, all noise).  A declared port no instance drives is still
# a port of the module, which is the price of a shared template whose
# edge instance leaves a side open (`flow/tpu/tpu.buda`'s one expected
# warning).
#
# Leaf modules (no children) are written as port-only shells — the
# interface and nothing else, which is all a planner needs and what makes
# the file byte-identical run to run.
# ============================================================

namespace eval hnet {
    variable CELLS {}        ;# ordered list of cell names
    variable PORTS           ;# PORTS(cell) = list of {name width dir}
    variable INSTS           ;# INSTS(cell) = list of {inst child_cell}
    variable NETS            ;# NETS(cell)  = list of {endpoints...}
    variable TOP ""
    array set PORTS {}
    array set INSTS {}
    array set NETS {}
}

proc hnet::reset {} {
    variable CELLS {}
    variable PORTS; variable INSTS; variable NETS
    array unset PORTS; array unset INSTS; array unset NETS
    array set PORTS {}; array set INSTS {}; array set NETS {}
    variable TOP ""
}

proc hnet::cell {name} {
    variable CELLS
    variable PORTS; variable INSTS; variable NETS
    if {$name in $CELLS} { error "hnet: cell '$name' declared twice" }
    lappend CELLS $name
    set PORTS($name) {}
    set INSTS($name) {}
    set NETS($name) {}
}

proc hnet::port {cell name width dir} {
    variable PORTS
    _cell_or_die $cell
    if {$dir ni {input output inout}} { error "hnet: port $cell.$name: dir must be input|output|inout" }
    if {![string is integer -strict $width] || $width < 1} {
        error "hnet: port $cell.$name: width must be an integer >= 1 (got '$width')"
    }
    foreach p $PORTS($cell) {
        if {[lindex $p 0] eq $name} { error "hnet: port $cell.$name declared twice" }
    }
    lappend PORTS($cell) [list $name $width $dir]
}

proc hnet::inst {cell inst child} {
    variable INSTS
    _cell_or_die $cell
    _cell_or_die $child
    if {$child eq $cell} { error "hnet: $cell cannot contain itself" }
    foreach i $INSTS($cell) {
        if {[lindex $i 0] eq $inst} { error "hnet: instance $cell/$inst declared twice" }
    }
    lappend INSTS($cell) [list $inst $child]
}

# A net inside `cell`: endpoints `inst.port` (a child's pin) or `.port`
# (the cell's own port).  Widths must agree; an endpoint naming an unknown
# instance or port is an error at declaration, where the typo is on
# screen, rather than a silently unconnected pin in the Verilog.
proc hnet::net {cell args} {
    variable NETS
    _cell_or_die $cell
    if {[llength $args] < 2} { error "hnet: net in $cell needs >= 2 endpoints" }
    set w ""
    set own {}
    foreach ep $args {
        set ew [_endpoint_width $cell $ep]
        if {$w eq ""} { set w $ew } elseif {$ew != $w} {
            error "hnet: net in $cell: '$ep' is $ew bits, the first endpoint '[lindex $args 0]' $w"
        }
        if {[lindex [split $ep .] 0] eq ""} { lappend own $ep }
    }
    # A net may name ONE of the cell's own ports: in the structural Verilog
    # this emits, a net touching an own port IS that port, and two ports on
    # one net could only be joined by an `assign`, which import_verilog
    # does not read -- the second port would be silently unconnected on
    # import (Codex P2 on #973).  Refused here, where the author sees it.
    if {[llength $own] > 1} {
        error "hnet: net in $cell names [llength $own] of its own ports ([join $own {, }]); a net may name one own port (join two ports through an instance, not a net)"
    }
    lappend NETS($cell) $args
}

proc hnet::top {cell} {
    variable TOP
    _cell_or_die $cell
    set TOP $cell
}

proc hnet::_cell_or_die {cell} {
    variable CELLS
    if {$cell ni $CELLS} { error "hnet: unknown cell '$cell' (declare it with hnet::cell first)" }
}

proc hnet::_port_of {cell name} {
    variable PORTS
    foreach p $PORTS($cell) {
        if {[lindex $p 0] eq $name} { return $p }
    }
    return ""
}

proc hnet::_endpoint_width {cell ep} {
    variable INSTS
    lassign [split $ep .] inst port
    if {$inst eq ""} {
        set p [_port_of $cell $port]
        if {$p eq ""} { error "hnet: $cell has no port '$port'" }
        return [lindex $p 1]
    }
    set child ""
    foreach i $INSTS($cell) {
        if {[lindex $i 0] eq $inst} { set child [lindex $i 1] }
    }
    if {$child eq ""} { error "hnet: $cell has no instance '$inst'" }
    set p [_port_of $child $port]
    if {$p eq ""} { error "hnet: $child (instance $cell/$inst) has no port '$port'" }
    return [lindex $p 1]
}

# Leaves: cells with no children.  Levels: a leaf is 1, a container 1 +
# the max of its children — the same intrinsic level `auto_floorplan`
# and `set_layer_caps_by_depth` compute.
proc hnet::leaves {} {
    variable CELLS; variable INSTS
    set out {}
    foreach c $CELLS { if {![llength $INSTS($c)]} { lappend out $c } }
    return $out
}
proc hnet::level {cell} {
    variable INSTS
    if {![llength $INSTS($cell)]} { return 1 }
    set m 0
    foreach i $INSTS($cell) { set m [expr {max($m, [level [lindex $i 1]])}] }
    return [expr {1 + $m}]
}

# Every instance path from the top, with its cell: {path cell} pairs.
proc hnet::instances {} {
    variable TOP; variable INSTS
    if {$TOP eq ""} { error "hnet: no top (hnet::top)" }
    set out {}
    set stack [list [list "" $TOP]]
    while {[llength $stack]} {
        lassign [lindex $stack end] path cell
        set stack [lrange $stack 0 end-1]
        foreach i $INSTS($cell) {
            lassign $i inst child
            set p [expr {$path eq "" ? $inst : "$path/$inst"}]
            lappend out [list $p $child]
            lappend stack [list $p $child]
        }
    }
    return [lsort -index 0 $out]
}

# {cells N insts N leaves N nets N bits N}: the design's size, counted off
# the structure (nets and bits are the ELABORATED totals: every net of
# every module times how often the module is instantiated).
proc hnet::census {} {
    variable CELLS; variable INSTS; variable NETS; variable TOP
    array set count {}
    set count($TOP) 1
    foreach c [_topo_order] {
        if {![info exists count($c)]} { continue }
        foreach i $INSTS($c) {
            set ch [lindex $i 1]
            if {![info exists count($ch)]} { set count($ch) 0 }
            incr count($ch) $count($c)
        }
    }
    set ninst 0; set nleaf 0; set nnets 0; set nbits 0
    foreach c $CELLS {
        if {![info exists count($c)]} { continue }
        if {$c ne $TOP} { incr ninst $count($c) }
        if {![llength $INSTS($c)]} { incr nleaf $count($c) }
        foreach n $NETS($c) {
            incr nnets $count($c)
            incr nbits [expr {$count($c) * [_endpoint_width $c [lindex $n 0]]}]
        }
    }
    return [list cells [llength $CELLS] insts $ninst leaves $nleaf nets $nnets bits $nbits]
}

# Cells parents-first (the top first), so instance counts propagate down.
proc hnet::_topo_order {} {
    variable CELLS; variable INSTS
    set order {}
    set seen [dict create]
    set stack {}
    # depth-first post-order, reversed
    proc ::hnet::_visit {c} {
        upvar 1 seen seen order order
        variable INSTS
        if {[dict exists $seen $c]} { return }
        dict set seen $c 1
        foreach i $INSTS($c) { _visit [lindex $i 1] }
        lappend order $c
    }
    foreach c $CELLS { _visit $c }
    return [lreverse $order]
}

# ── the Verilog ───────────────────────────────────────────────────────────
proc hnet::emit_verilog {path {banner ""}} {
    variable CELLS; variable PORTS; variable INSTS; variable NETS; variable TOP
    if {$TOP eq ""} { error "hnet: no top (hnet::top)" }
    set f [open $path w]
    if {$banner ne ""} { puts $f $banner }
    puts $f "// structural, every instance written out (hnet.tcl): the form import_verilog reads."
    puts $f ""
    # leaves first, then containers, the top LAST -- explicitly, not by
    # the order's accident: a reader takes the last module nobody
    # instantiates as the top (BUDA does), and a library cell declared
    # after the top and instantiated nowhere would otherwise be emitted
    # after it and be read as the design (Codex P1 on #973).
    set order {}
    foreach c [lreverse [_topo_order]] {
        if {$c ne $TOP} { lappend order $c }
    }
    lappend order $TOP
    foreach c $order {
        set pnames [lmap p $PORTS($c) {lindex $p 0}]
        puts $f "module $c ([join $pnames ", "]);"
        foreach p $PORTS($c) {
            lassign $p nm w dir
            set decl [format "  %-6s" $dir]
            if {$w > 1} { append decl " \[[expr {$w-1}]:0\]" }
            puts $f "$decl $nm;"
        }
        if {![llength $INSTS($c)]} {
            puts $f "endmodule\n"
            continue
        }
        # wires: one per net that touches no own port; a net touching an
        # own port IS that port (the first own-port endpoint names it)
        set wire_of [dict create]
        set k 0
        foreach n $NETS($c) {
            set own ""
            foreach ep $n {
                lassign [split $ep .] inst port
                if {$inst eq ""} { set own $port; break }
            }
            if {$own eq ""} {
                set own "w${k}"
                incr k
                set w [_endpoint_width $c [lindex $n 0]]
                set decl "  wire"
                if {$w > 1} { append decl " \[[expr {$w-1}]:0\]" }
                puts $f "$decl $own;"
            }
            foreach ep $n {
                lassign [split $ep .] inst port
                if {$inst ne ""} { dict set wire_of "$inst.$port" $own }
            }
        }
        foreach i $INSTS($c) {
            lassign $i inst child
            set conns {}
            foreach p $PORTS($child) {
                set pn [lindex $p 0]
                if {[dict exists $wire_of "$inst.$pn"]} {
                    lappend conns ".${pn}([dict get $wire_of "$inst.$pn"])"
                }
            }
            puts $f "  $child $inst ([join $conns ", "]);"
        }
        puts $f "endmodule\n"
    }
    close $f
}
