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
# flow/tcl/array_save.tcl — session 1 of a design iteration: BUILD.
#
#   tclsh flow/tcl/array_save.tcl ?ROWS COLS?          (default 2 2)
#
#   BUDA_ARRAY_BDB=<path>   the working database  (default flow/tcl/out/array.bdb)
#   BUDA_ARRAY_PIN=…        candidates to pin     (see array_lib.tcl)
#   BUDA_ARRAY_VIZ=1        open the viewer when the route is done
#
# `array.tcl` builds the same design and throws it away: it opens `:memory:`,
# so the topologies, the plan and any candidate you picked by hand exist only
# until the process ends.  That is fine for a self-checking regression vehicle
# and useless for actually working on a design — you look at the route, decide
# a bundle should take a different candidate, and there is nowhere to put that
# decision.
#
# So this one opens a FILE.  Everything the pipeline persists — bundles,
# every candidate topology, the planner's selection and layers, the abstract
# and detailed routing, and `topology.is_pinned` — lands in it as the flow
# runs, and `array_resume.tcl` picks it up in a new process and re-plans.
# The pair is the iteration loop:
#
#     tclsh flow/tcl/array_save.tcl 3 2          # build + route + look
#     BUDA_ARRAY_PIN=diag=14 tclsh flow/tcl/array_resume.tcl   # re-plan
#     BUDA_ARRAY_PIN=diag=7  tclsh flow/tcl/array_resume.tcl   # ...and again
#
# What a pin is worth is measurable: unpinned, this design's `diag` bundle
# plans as TRUNK_V@x240; pinned to 14 it keeps TRUNK_H+MST@y480 through the
# resume, the re-plan and the checkpoint after it.
# ============================================================

set rows 2
set cols 2
if {$argc >= 2} {
    set rows [lindex $argv 0]
    set cols [lindex $argv 1]
}

set repo [file dirname [file dirname [file dirname [file normalize [info script]]]]]
source [file join $repo tools buda.tcl]
source [file join $repo flow tcl array_lib.tcl]

set bdb [array_vehicle::bdb_path $repo]

# Session 1 BUILDS, so it starts from an empty database: `add_inst` into a BDB
# that already holds the design is a duplicate-instance error, and a half-
# rebuilt checkpoint is worse than none.  Only ever removes the working `.bdb`
# it is about to write — anything else is the user's file and is refused.
if {[file exists $bdb]} {
    if {[file extension $bdb] ne ".bdb"} {
        error "array_save.tcl: refusing to overwrite $bdb (not a .bdb) --\
               this flow REBUILDS the design and needs an empty database"
    }
    puts "array_save.tcl: rebuilding -- removing the previous $bdb"
    file delete $bdb
    file delete ${bdb}-wal
    file delete ${bdb}-shm
}
file mkdir [file dirname $bdb]

buda::start
array_vehicle::declare_stack

# ── the design ────────────────────────────────────────────────────────────
buda::open_bdb $bdb
array_vehicle::build_hierarchy $rows $cols
array_vehicle::derive_interface
array_vehicle::load_blocks
array_vehicle::build_buses $rows $cols

# ── the hier pipeline ─────────────────────────────────────────────────────
# CONVERGENT so the f_* buses merge into ONE cross-level fan-in bundle.
buda::run_hier_bundler depth 2 CONVERGENT

set nb [buda::query bundles]
if {$nb == 0} { error "nothing bundled" }
puts "array_save.tcl: $rows x $cols tiles -> $nb bundles -> $bdb"

buda::generate_hier_topologies

# Pins go on BEFORE the planner, so what gets checkpointed is COHERENT: the
# pin, the selection it forces, the layers, and the routing all describe one
# design.  Pinning after the route would leave the saved metal belonging to
# the candidate that was replaced.
array_vehicle::apply_pins

buda::run_planner hier 5
buda::run_nuts
buda::check_design nuts

buda::run_detailed_nuts
buda::check_design dnuts

array_vehicle::heal_if_dirty "array_save.tcl"
buda::report_wirelength

# Look at it.  A candidate picked in the explorer is a PREVIEW — the viewer's
# re-run buttons deliberately never write a checkpoint — so the title bar's
# `topo N/n` is the number to bring back, not something the window can save
# for you.  (That N is 1-based and is exactly what `select_topology` takes;
# `dump_topologies` numbers its own table from 0, so add one when reading it
# there.)
array_vehicle::show
puts "array_save.tcl: to keep a candidate you picked in the explorer, re-plan\
      with it:\n    BUDA_ARRAY_PIN=<bus>=<topo N from the title bar> tclsh\
      flow/tcl/array_resume.tcl"

# The working BDB is a binary file the engine has been writing all along, so
# it is already saved — `save_bdb` with no argument would only tell you so.
# This is the extra, deliberate artifact: a DIFFABLE text snapshot of the
# checkpoint, so two iterations can be compared instead of just replaced.
buda::save_bdb [array_vehicle::snapshot_path $bdb]

array_vehicle::verdict "array_save.tcl"
