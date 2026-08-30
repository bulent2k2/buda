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
# flow/tcl/tpu.tcl — the TPU-shaped systolic array, end to end.
#
#   btcl flow/tcl/tpu.tcl                       # 8x8, top-down
#   btcl flow/tcl/tpu.tcl 16                    # 16x16 = 256 PEs
#   btcl flow/tcl/tpu.tcl 8 -bottomup           # solve one row, copy it
#   btcl flow/tcl/tpu.tcl 4 -PEW 80 -PPX 110 -PW 32 -PIPE 4
#   btcl flow/tcl/tpu.tcl 8 -dry                # print the size, build nothing
#   btcl flow/tcl/tpu.tcl 8 -emit flow/tpu      # write tpu.v/.def/.lef, stop
#
# EVERY knob in `tpu_vehicle::configure` is settable as `-<NAME> <value>`
# (N, PEW/PEH, PPX/PPY, ROWM/ROWGAP, EDGEW/EDGEH/EDGEGAP, X0/Y0, AW/PW/WW,
# PIPE/PIPEGAP), so a larger experiment is an argument rather than an edit.
# The design itself is `tpu_lib.tcl` — one source, so a future save/resume
# driver cannot drift from this one (array_lib.tcl's rule).
#
# `-bottomup` is the experiment this vehicle exists for: N congruent
# `row_cell` instances mean the cell-local interconnect can be solved ONCE
# and copied, which is the path `flow/chip` exercises with heterogeneous
# cells and nothing exercises on a real mesh.  It runs `align_bottom_up`
# first, since copied instances must share a track phase.
# ============================================================

set repo [file dirname [file dirname [file dirname [file normalize [info script]]]]]
source [file join $repo tools buda.tcl]
source [file join $repo flow tcl tpu_lib.tcl]

# ── the command line ──────────────────────────────────────────────────────
# A bare leading integer is N (the common case); everything else is
# -NAME value, validated by `configure` — an unknown knob is an ERROR
# rather than a silently ignored word, because a typo in a sweep that runs
# for an hour must not report on a design nobody asked for.
set overrides {}
set bottomup 0
set dry 0
set emit ""
set argi 0
if {$argc > 0 && [string is integer -strict [lindex $argv 0]]} {
    lappend overrides N [lindex $argv 0]
    incr argi
}
while {$argi < $argc} {
    set opt [lindex $argv $argi]
    switch -- $opt {
        -bottomup { set bottomup 1; incr argi }
        -dry      { set dry 1; incr argi }
        -emit {
            if {$argi + 1 >= $argc} { error "tpu.tcl: -emit needs a directory" }
            set emit [lindex $argv [expr {$argi+1}]]
            incr argi 2
        }
        default {
            if {[string index $opt 0] ne "-"} {
                error "tpu.tcl: unexpected argument '$opt' (N comes first)"
            }
            if {$argi + 1 >= $argc} {
                error "tpu.tcl: $opt needs a value"
            }
            lappend overrides [string range $opt 1 end] [lindex $argv [expr {$argi+1}]]
            incr argi 2
        }
    }
}

# `-bottomup` changes the GEOMETRY, not just the flow: congruent instances
# need the row pitch on a track period, so the intent has to reach
# `configure` before it sizes anything.
if {$bottomup} { lappend overrides ALIGN 1 }
tpu_vehicle::configure $overrides
puts "tpu.tcl: [tpu_vehicle::describe]"

# `-emit` writes the design as Verilog + DEF + LEF and stops: the point is
# the IMPORT path (`flow/tpu/tpu.buda`), which no other vehicle reaches with
# an arrayed design.  Same `P` the flow builds from, so the two cannot drift.
if {$emit ne ""} {
    tpu_vehicle::emit_all $emit
    exit 0
}
if {$dry} { exit 0 }

buda::start

tpu_vehicle::declare_stack
buda::open_bdb :memory:
tpu_vehicle::build_hierarchy

if {$bottomup} {
    # Mark BEFORE deriving busterms: `align_bottom_up` nudges instances onto
    # a shared track phase and must run while the floorplan is still the
    # only thing that has been derived from these coordinates.
    buda::set_bottom_up row_cell
    buda::align_bottom_up
}

tpu_vehicle::derive_interface
tpu_vehicle::load_blocks
tpu_vehicle::build_buses

# ── the hier pipeline ─────────────────────────────────────────────────────
buda::run_hier_bundler depth 2

set nb [buda::query bundles]
if {$nb == 0} { error "tpu.tcl: nothing bundled" }
puts "tpu.tcl: $nb bundles"
# The structure, printed: a mesh's whole point is that most interconnect is
# cell-local and templated, and this is where that shows.
buda::dump_hbundles

buda::generate_hier_topologies
buda::run_planner hier 5
buda::run_nuts
buda::check_design nuts

if {$bottomup} { buda::check_template_tracks }

buda::run_detailed_nuts
buda::check_design dnuts

tpu_vehicle::heal_if_dirty "tpu.tcl"
buda::report_wirelength

tpu_vehicle::verdict "tpu.tcl"
