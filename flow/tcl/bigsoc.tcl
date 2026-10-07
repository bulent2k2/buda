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
# flow/tcl/bigsoc.tcl — a large SoC from a NETLIST and a PDK, end to end.
#
#   btcl flow/tcl/bigsoc.tcl                     # NQ=2: the smoke size
#   btcl flow/tcl/bigsoc.tcl 4 -NC 4 -N 8        # bigger: THE DIAL
#   btcl flow/tcl/bigsoc.tcl 2 -dry              # the census, no engine
#   btcl flow/tcl/bigsoc.tcl 2 -emit soc.v       # the Verilog, then stop
#   btcl flow/tcl/bigsoc.tcl 2 -bottomup         # solve each template once, copy it
#   btcl flow/tcl/bigsoc.tcl 2 -fp "gap 24 wl 0.5"   # auto_floorplan options
#   btcl flow/tcl/bigsoc.tcl 2 -save ck.bdb      # keep the routed checkpoint
#
# WHAT THIS VEHICLE IS FOR.  `soc.tcl` and `tpu.tcl` draw their floorplan
# in Tcl, from the bus widths, and build it straight into the BDB.  This
# one starts where a chip team starts — a Verilog netlist
# (`bigsoc_lib.tcl`, authored with `hnet.tcl` and EMITTED, then read back
# with `import_verilog`, so the reader is on the path) and a PDK
# (`flow/mockpdk/`) — and lets the engine make the first floorplan:
# `auto_floorplan` sizes every leaf from its pins and the PDK's area model,
# packs every container, stamps every instance, sets the die.  Then the
# hierarchical routing flow runs on it, top-down or bottom-up, exactly as
# on the hand-drawn vehicles, and the verdict says what that first
# floorplan is worth.
#
# Knobs: every entry of `bigsoc::configure` as `-NAME value` (NQ first as a
# bare integer); an unknown knob is an ERROR, because a typo in a sweep
# that runs for an hour must not report on a design nobody asked for.
#
#   -dry            print the census and exit (no engine)
#   -emit FILE      write the Verilog and exit
#   -bottomup       set_bottom_up * + align_bottom_up, check_template_tracks
#                   on_mismatch independent (soc.tcl's recipe)
#   -noheal         no healer rounds (the healerless table)
#   -pdk FILE       the area model (default flow/mockpdk/mock.pdk)
#   -fp "TOKENS"    extra auto_floorplan options, verbatim
#   -save FILE      the BDB the flow runs in (default :memory:) -- a file
#                   keeps the routed result for the judge and the tools
#   -caps           reserve_top_layers 2 (the top pair for the top)
#   -iterate K      the TOP-DOWN half of the loop: up to K rounds, each a
#                   fresh floorplan from what the previous route measured
#                   -- every cell whose instances the rest of the design
#                   fills past -demand percent on a TOP layer
#                   (`buda::query demand`) grows by -grow, and the channel
#                   (gap + margin) by -widen -- routed HEALERLESS to judge
#                   the floorplan rather than the healers; the last round
#                   heals.  Stops early when a round is clean.
#   -demand P       the demand threshold (default 80)
#   -grow F         the per-round growth of a starved cell (default 1.25)
#   -widen F        the per-round channel factor (default 1.5)
# ============================================================

set repo [file dirname [file dirname [file dirname [file normalize [info script]]]]]
source [file join $repo tools buda.tcl]
source [file join $repo flow tcl bigsoc_lib.tcl]

set overrides {}
set dry 0
set emit ""
set bottomup 0
set heal 1
set pdk [file join $repo flow mockpdk mock.pdk]
set fpopts {}
set save :memory:
set caps 0
set iterate 1
set demand_pct 80
set grow_f 1.25
set widen_f 1.5
set argi 0
if {$argc > 0 && [string is integer -strict [lindex $argv 0]]} {
    lappend overrides NQ [lindex $argv 0]
    incr argi
}
while {$argi < $argc} {
    set opt [lindex $argv $argi]
    switch -- $opt {
        -dry      { set dry 1; incr argi }
        -bottomup { set bottomup 1; incr argi }
        -noheal   { set heal 0; incr argi }
        -caps     { set caps 1; incr argi }
        -emit - -pdk - -fp - -save - -iterate - -demand - -grow - -widen {
            if {$argi + 1 >= $argc} { error "bigsoc.tcl: $opt needs a value" }
            set v [lindex $argv [expr {$argi+1}]]
            switch -- $opt {
                -emit    { set emit $v }
                -pdk     { set pdk $v }
                -fp      { set fpopts $v }
                -save    { set save $v }
                -iterate { set iterate $v }
                -demand  { set demand_pct $v }
                -grow    { set grow_f $v }
                -widen   { set widen_f $v }
            }
            incr argi 2
        }
        default {
            if {[string index $opt 0] ne "-"} {
                error "bigsoc.tcl: unexpected argument '$opt' (NQ comes first)"
            }
            if {$argi + 1 >= $argc} { error "bigsoc.tcl: $opt needs a value" }
            lappend overrides [string range $opt 1 end] [lindex $argv [expr {$argi+1}]]
            incr argi 2
        }
    }
}

bigsoc::configure $overrides
bigsoc::build
puts [bigsoc::banner "bigsoc.tcl"]
if {$emit ne ""} {
    bigsoc::emit $emit
    puts "bigsoc.tcl: wrote $emit"
    exit 0
}
if {$dry} { exit 0 }

# The netlist goes through a FILE: the emitted Verilog is what the engine
# reads, so the reader is on the path and the design the flow routes is
# the design the file says.
if {$save eq ":memory:"} {
    set ch [file tempfile vfile bigsoc_[bigsoc::get NQ].v]
    close $ch
} else {
    set vfile [file join [file dirname [file normalize $save]] bigsoc_[bigsoc::get NQ].v]
}
bigsoc::emit $vfile

# One ROUND: a session, the stack, the netlist, a floorplan under the
# round's growth and channel, the hier flow, the audit.  Returns the
# demand rows of the routed result (for the next round to read) with the
# verdict; the session is left open for the caller to finish.
proc bigsoc::round {vfile save pdk fpopts grows gap bottomup caps heal} {
    set repo [file dirname [file dirname [file dirname [file normalize [info script]]]]]
    buda::start
    buda::source [file join $repo flow mockpdk stack.buda]
    buda::set_planner_param healersAhead 1
    buda::open_bdb $save
    buda::import_verilog $vfile

    # THE FIRST FLOORPLAN: every size and every position from the netlist
    # and the PDK, nothing drawn by hand -- plus what the previous round
    # measured, as growth and channel.
    set opts [list pdk $pdk gap $gap margin $gap]
    if {[dict size $grows]} {
        lappend opts grow [join [lmap {c f} $grows {format %s=%s $c $f}] ,]
    }
    buda::auto_floorplan {*}$opts {*}$fpopts

    if {$bottomup} {
        buda::set_bottom_up *
        buda::align_bottom_up
    }
    if {$caps} { buda::reserve_top_layers 2 }

    # the hier flow, soc.tcl's: the deepest leaf is four levels down
    buda::derive_busterms 4
    buda::add_blocks_from_bdb 0
    foreach d {1 2 3} { buda::add_blocks_from_bdb $d skip }
    buda::run_hier_bundler depth 4
    set nb [buda::query bundles]
    if {$nb == 0} { error "bigsoc.tcl: nothing bundled" }
    puts "bigsoc.tcl: $nb bundles"
    buda::dump_hbundles

    buda::generate_hier_topologies
    buda::run_planner hier 5
    buda::run_nuts
    buda::check_design nuts
    if {$bottomup} { buda::check_template_tracks on_mismatch independent }
    buda::run_detailed_nuts
    buda::check_design dnuts
    puts "bigsoc.tcl: first audit -- [bigsoc::_state]"
    set healed 0
    if {$heal} { set healed [bigsoc::heal_if_dirty "bigsoc.tcl"] }
    buda::report_wirelength
    return [buda::query demand]
}

# The starved cells of a routed result: every cell one of whose instances
# the rest of the design fills past `pct` percent on a TOP layer.
proc bigsoc::starved {rows pct} {
    set out [dict create]
    foreach r $rows {
        lassign $r inst cell layer bits used supply p
        if {$layer in {M5 M6 M7} && $p >= $pct} { dict set out $cell 1 }
    }
    return [lsort [dict keys $out]]
}

set grows [dict create]
set gap 16
for {set round 1} {$round <= $iterate} {incr round} {
    set last [expr {$round == $iterate}]
    if {$iterate > 1} {
        puts "=== bigsoc.tcl: round $round of $iterate -- gap $gap, grown: [expr {[dict size $grows] ? $grows : {none}}] ==="
    }
    set rows [bigsoc::round $vfile $save $pdk $fpopts $grows $gap $bottomup $caps \
                  [expr {$heal && $last}]]
    if {$last || ![bigsoc::is_dirty]} {
        if {!$last} { puts "bigsoc.tcl: round $round is clean -- stopping early" }
        break
    }
    puts "bigsoc.tcl: round $round -- [bigsoc::_state]"
    foreach c [bigsoc::starved $rows $demand_pct] {
        set f [expr {[dict exists $grows $c] ? [dict get $grows $c] : 1.0}]
        dict set grows $c [format %.4g [expr {$f * $grow_f}]]
    }
    set gap [expr {int(ceil($gap * $widen_f))}]
    buda::stop
}
if {$save ne ":memory:"} { buda::save_bdb }
bigsoc::verdict "bigsoc.tcl"
