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
# flow/tcl/array.tcl — the Tcl front end's own flow vehicle.
#
#   tclsh flow/tcl/array.tcl  ?ROWS COLS?          (default 2 2)
#
# A .buda script can only STATE a design; this flow COMPUTES one — which is
# the case the Tcl front end exists for.  Everything below is generated from
# two numbers: a ROWS x COLS array of `tile` instances, each tile a cell
# containing a 2 x 2 array of `leaf` cells, connected by buses a pair of
# nested loops emits:
#
#   * intra-tile (depth 2, cell-local): the SAME two buses inside every
#     tile — the hier bundler solves the pattern once as a cell TEMPLATE
#     and replicates it per instance;
#   * cross-tile neighbor chains (depth 0 with leaf-deep endpoints): each
#     tile's east edge leaf to its neighbor's west edge leaf, and the same
#     north-south — the cross-level case;
#   * one corner-to-corner diagonal (8-bit, the long wire);
#   * a fan-in: every tile's NE leaf drives the SW tile's corner leaf —
#     under CONVERGENT bundling these N buses become ONE fan-in bundle
#     routed as a per-bit tapered tree.
#
# The design itself lives in `array_lib.tcl`, shared with `array_save.tcl` /
# `array_resume.tcl` — the same array, driven three ways.  This one runs it
# end to end in ONE session against `:memory:`, which is why nothing it
# decides outlives the process; the save/resume pair is the flow to copy when
# a decision has to survive.
#
# The end of the flow is the other half of the point: `buda::query` returns
# VALUES, so the flow BRANCHES — healers run only if the measured result is
# dirty, and the script's exit code is the design's cleanliness, not just
# "the commands ran".
# ============================================================

set rows 2
set cols 2
if {$argc >= 2} {
    set rows [lindex $argv 0]
    set cols [lindex $argv 1]
}

# Resolve the repo root from THIS script (flow/tcl/ is two levels down), so
# the flow runs from any CWD — the same rule .buda scripts follow.
set repo [file dirname [file dirname [file dirname [file normalize [info script]]]]]
source [file join $repo tools buda.tcl]
source [file join $repo flow tcl array_lib.tcl]

buda::start

# The design, the stack and the verdict helpers — one source, three drivers.
array_vehicle::declare_stack

buda::open_bdb :memory:
array_vehicle::build_hierarchy $rows $cols
array_vehicle::derive_interface
array_vehicle::load_blocks
array_vehicle::build_buses $rows $cols

# ── the hier pipeline ─────────────────────────────────────────────────────
# CONVERGENT so the f_* buses merge into ONE cross-level fan-in bundle.
buda::run_hier_bundler depth 2 CONVERGENT

set nb [buda::query bundles]
if {$nb == 0} { error "nothing bundled" }
puts "array.tcl: $rows x $cols tiles -> $nb bundles"
buda::dump_hbundles

buda::generate_hier_topologies
buda::run_planner hier 5
buda::run_nuts
buda::check_design nuts

buda::run_detailed_nuts
buda::check_design dnuts

array_vehicle::heal_if_dirty "array.tcl"

buda::report_wirelength

array_vehicle::show

array_vehicle::verdict "array.tcl"
