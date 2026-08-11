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
# flow/tcl/array_resume.tcl — session 2 of a design iteration: RE-PLAN.
#
#   tclsh flow/tcl/array_resume.tcl
#
#   BUDA_ARRAY_BDB=<path>   the checkpoint to reopen (default flow/tcl/out/array.bdb)
#   BUDA_ARRAY_PIN=…        candidates to pin before re-planning
#   BUDA_ARRAY_VIZ=1        open the viewer when the route is done
#
# Note what is NOT here: no cells, no instances, no buses, no bundler, no
# topology generation.  All of it comes back out of the database that
# `array_save.tcl` wrote — `load_pipeline` restores the bundles, every
# candidate topology, the planner's selection and per-segment layers, any
# pre-plan pin, and the abstract routing.  This flow starts at the planner,
# which is the whole point: a design iteration re-plans, it does not rebuild.
#
# What IS here, and has to be: the technology.  Layers and track patterns are
# re-declared before the reopen, because `load_pipeline` needs the stack and
# the floorplan present to rebuild against — topology coordinates are
# absolute, and slide ranges and net-pull are recomputed from geometry rather
# than persisted.  They come from `array_lib.tcl`, the same source
# `array_save.tcl` used, so the two sessions cannot disagree about the design
# they are both talking about.
#
#     tclsh flow/tcl/array_save.tcl                            # session 1
#     BUDA_ARRAY_PIN=diag=14 tclsh flow/tcl/array_resume.tcl   # session 2
#
# The re-plan honors the pin, and the checkpoint is written again — so the
# next iteration starts from this one.
# ============================================================

set repo [file dirname [file dirname [file dirname [file normalize [info script]]]]]
source [file join $repo tools buda.tcl]
source [file join $repo flow tcl array_lib.tcl]

set bdb [array_vehicle::bdb_path $repo]
if {![file exists $bdb]} {
    error "array_resume.tcl: no checkpoint at $bdb --\
           run `tclsh [file join $repo flow tcl array_save.tcl]` first\
           (or point BUDA_ARRAY_BDB at one)"
}

buda::start

# The technology, identical to the session that wrote the checkpoint.
array_vehicle::declare_stack

# ── reopen and rehydrate ──────────────────────────────────────────────────
buda::open_bdb $bdb
# The flat floorplan projection of the stored hierarchy.  `derive_busterms`
# is deliberately NOT re-run: busterms are persisted, and re-deriving them
# would rebuild the routing interface the checkpointed bundles already refer
# to by id.
array_vehicle::load_blocks
buda::load_pipeline

set nb [buda::query bundles]
if {$nb == 0} {
    error "array_resume.tcl: $bdb restored no bundles -- is it a checkpoint\
           from before `run_hier_bundler`?"
}
puts "array_resume.tcl: restored $nb bundles from $bdb"

# ── the decision this iteration is about ──────────────────────────────────
# A pin applied here overrides whatever the checkpoint selected, and it is
# durable in its own right: `select_topology` writes `topology.is_pinned`
# immediately, so the next resume starts from this choice even if this run is
# interrupted before the save at the bottom.
set npins [array_vehicle::apply_pins]
if {$npins == 0} {
    puts "array_resume.tcl: no BUDA_ARRAY_PIN -- re-planning the checkpoint\
          as it stands (any pin already in it is still honored)"
}

# ── re-plan, from the planner all the way through ─────────────────────────
buda::run_planner hier 5
buda::run_nuts
buda::check_design nuts

buda::run_detailed_nuts
buda::check_design dnuts

array_vehicle::heal_if_dirty "array_resume.tcl"
buda::report_wirelength

array_vehicle::show

# Checkpoint again, so this iteration is the next one's starting point. The
# working `.bdb` has been written in place throughout; the snapshot is the
# diffable record of THIS iteration.
buda::save_bdb [array_vehicle::snapshot_path $bdb]

array_vehicle::verdict "array_resume.tcl"
