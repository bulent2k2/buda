/*
 * Copyright 2026 Ben Bulent Basaran
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#pragma once
#include "topology.h"
#include "verify.h"
#include <string>

// ── TopoEdit — transactional expert edits on a Topology (Phase E3) ───────────
//
// Every operation follows one transaction shape:
//
//   mutate geometry  →  maintain the authoritative annotations (seg_busterms
//   seeding + the erase/re-key discipline + a full annotate_seg_conns
//   re-derivation, so geometry and junction records always agree)  →  cached
//   re-analysis (automatic — Phase B validates by content)  →  an immediate
//   EditVerdict (check_topo violations + zero-slide pinch), so the expert sees
//   exactly what a hand edit broke instead of silently corrupting annotations.
//
// UNDO MODEL: Topology is a value type — snapshot before, restore on reject
// (the same model ripup_reroute uses for flips).  Verify with topo_uid
// equality: a restored snapshot fingerprints identically.
//
// SLIDE OVERRIDE: deliberately NOT an operation here — the per-segment slide
// window override already exists as the planner/NUTS escape hatch
// (`plan.seg_slide_lo/hi` on the BundleWrapper, NaN = use the computed
// analysis), which is per-plan state, not topology content.  Span override IS
// topology content → edit_set_span below.
//
// A failed operation (applied == false) leaves the topology UNTOUCHED.

namespace buda {

struct EditVerdict {
    bool        applied = false;  // the mutation happened (false = topo untouched)
    std::string note;             // why it failed / what was done
    int         seg_idx = -1;     // index of a newly added segment (add ops)
    ConnResult  conn;             // check_topo on the post-edit topology
    bool        pinched = false;  // any zero-slide segment after the edit
    // SEG-connected components of the wire graph.  check_topo audits taps,
    // faces, and per-conn touch — NOT whole-graph connectivity (that is the
    // generator's clean-tree gate), so an edit that splits the tree in two
    // would otherwise pass; the editor must catch it.  1 = one connected
    // wire graph; 0 = no segments yet.
    int         components = 0;
    bool ok() const {
        return applied && conn.violations.empty() && !pinched && components <= 1;
    }
};

// Add a trunk segment on the given axis (horiz = along-x) at perpendicular
// position `perp_pos` — typically a Hanan grid line (pick one from
// Floorplan::get_hanan_grid).  Pass along_lo > along_hi to get the DEFAULT
// full span: the Hanan grid's extent on that axis.
EditVerdict edit_add_trunk(Topology& topo, const Floorplan& fp, bool horiz,
                           int perp_pos, int along_lo, int along_hi, int layer);

// Remove a segment; seg_busterms / seg_conns are re-keyed (erase_segment
// discipline) and junctions re-derived.
EditVerdict edit_remove_segment(Topology& topo, const Floorplan& fp, int seg_idx);

// Add a perpendicular stub from `block`'s nearest face to segment `to_seg`,
// at the centre of their along-overlap; the block end is seeded as a busterm
// tap (margin-inset bbox, multi-rect and TEG carried), the far end becomes a
// junction on `to_seg`.
EditVerdict edit_add_stub(Topology& topo, const Floorplan& fp,
                          const std::string& block, int to_seg, int layer);

// Override a segment's along-span (the perpendicular position is the slide
// window's business, not span's).  May legally break a junction or pull a tap
// off its face — the verdict reports what broke.
EditVerdict edit_set_span(Topology& topo, const Floorplan& fp, int seg_idx,
                          int along_lo, int along_hi);

// Connect two PERPENDICULAR segments: segment `i`'s nearest free endpoint is
// moved to the crossing of the two lines (and `j` is extended to reach the
// crossing if needed); the junction then falls out of re-derivation.  Refuses
// to move a busterm-tapped endpoint (use edit_set_span deliberately instead).
EditVerdict edit_connect(Topology& topo, const Floorplan& fp, int i, int j);

// Disconnect a junction between `i` and `j` by retracting `i`'s landing
// endpoint to `retract_to` (a coordinate along i's axis).  The result must
// stay a real segment (non-degenerate) and actually leave `j`.
EditVerdict edit_disconnect(Topology& topo, const Floorplan& fp, int i, int j,
                            int retract_to);

} // namespace buda
