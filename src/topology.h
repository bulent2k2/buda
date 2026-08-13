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
#include <vector>
#include <string>
#include <algorithm>
#include <climits>
#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <map>
#include <memory>
#include <set>
#include <optional>
#include "bundler.h"
namespace buda {

class Floorplan;   // defined below; needed by free-function decls before it
struct TopoAnalysis;  // topology_analysis.h — cached derived analysis (Phase B)

// Project-level floor (epsilon) on the along-length of a STANDALONE abutment
// crossing segment — the single-segment ABUT_H/ABUT_V candidate that rescues two
// full-edge-abutting blocks.  Its length is minimized to the min-stub-length
// setting to save routing channel, but never zero: a zero-length wire has no
// conn-segs to pin it, carries no bus, and cannot be placed by NUTS.  See
// TopologyGenerator::generate_2pin's abutment fallback.
inline constexpr int kAbutmentSpanEpsilon = 2;

// How a multi-rect block handles a trunk that falls in the gap between its rects.
enum class TegMode {
    THRU,  // default: connect only the nearest rect; block's internal routing joins sides
    OVER,  // connect both rects with V stubs + an explicit bridge segment over the block top
};

struct Point { int x, y; };
struct Rect {
    int x1, y1, x2, y2;
    Point center() const { return { (x1+x2)/2, (y1+y2)/2 }; }
    // Nearest block face in the direction of 'toward'. Returns 'toward' itself
    // if the value falls inside the block (= trunk passes through the block).
    int face_x(int toward) const { return toward > x2 ? x2 : toward < x1 ? x1 : toward; }
    int face_y(int toward) const { return toward > y2 ? y2 : toward < y1 ? y1 : toward; }
    // Return a rect inset by (dx, dy) on each side.
    // Guard: if the margin would invert an axis, keep that axis at full extent.
    Rect shrink(int dx, int dy) const {
        int nx1 = x1+dx, nx2 = x2-dx, ny1 = y1+dy, ny2 = y2-dy;
        if (nx1 > nx2) { nx1 = x1; nx2 = x2; }
        if (ny1 > ny2) { ny1 = y1; ny2 = y2; }
        return Rect{nx1, ny1, nx2, ny2};
    }
};
struct Segment {
    Point start, end;
    int layer_hint = 0;
    // True for a dogleg JOG: the short perpendicular segment NUTS inserts to
    // bridge two collinear trunk pieces on different tracks.  Marked so build_
    // nuts_maps excludes it from same-bundle alignment (it must stay on its own
    // column, not snap onto a sibling stub's track).
    bool is_jog = false;
    // Identity of the MST edge this segment realizes, when it is a leg of an MST
    // candidate (MST_HV/MST_VH, TRUNK_*+MST).  -1 = not an MST-edge leg (trunk
    // spine, plain stub, jog, …).  Set at generation by the MST realizers; lets a
    // later stage identify all legs of one edge to flip its L/Z realization
    // in place without disturbing the rest of the tree.  Pure annotation until a
    // consumer exists (per-edge flip); carried through offset_topology.
    // PERSISTED: topology_segment.edge_id (BDB v14); the persist path
    // (buda_session) writes it and load_pipeline restores it, so a checkpointed
    // MST candidate reloads with its edge identity intact.  NOT hashed into the
    // topology fingerprint (inert w.r.t. candidate identity — a deterministic
    // function of the tree geometry).
    int edge_id = -1;
    // Optional generation-supplied PERPENDICULAR slide clamp, in absolute layout
    // coordinates on this segment's perpendicular axis (y for an H segment, x for
    // a V segment).  INT_MIN/INT_MAX sentinels = unclamped (the default), so this
    // is inert for every existing shape.  When set, derive_slide_ranges intersects
    // it into the segment's [perp_lo, perp_hi], and hence into the NUTS interval
    // constraint (nuts.cpp maps perp_lo/hi → interval_lo/hi).  Used by the overlap
    // corner-wrapping U's (add_overlap_corner_us) to pin the face-tap arm inside
    // the tapped block's EXCLUSIVE band so NUTS cannot slide it across the far
    // block and collapse the detour into a pass-through.  Carried by
    // offset_topology (shifted on the perp axis); NOT hashed into the topology
    // fingerprint (it is a deterministic function of the segment geometry + block
    // positions, so an identical-geometry cache hit already implies an identical
    // clamp).  PERSISTED: topology_segment.perp_clamp_lo/hi (BDB v16); the persist
    // path writes them and load_pipeline restores them, so a checkpointed U_OVL
    // candidate reloads clamped (an earlier resume-before-NUTS could otherwise
    // collapse it through the overlapped block).
    int perp_clamp_lo = INT_MIN;
    int perp_clamp_hi = INT_MAX;
};

// Orientation abstraction for topology generation.  A spine runs ALONG one axis
// and its perpendicular stubs run along the other; `along_horiz` picks which.
// Every generator that comes in an H and a V flavour (trunks, BITRUNK, and — as
// the centralization proceeds — L/Z/U) can be written ONCE against an Axis
// instead of copy-pasting an x<->y transpose.  The methods only extract
// coordinates from a *caller-chosen* Rect (orig_bbox / bbox / a best rect) —
// Axis deliberately does NOT decide which Rect, because that shrunk-vs-full face
// choice is each generator's margin/slide policy, not an orientation concern.
// This generalizes the ad-hoc RA/PA/RAface/mkpt/mkseg lambdas that used to live
// inside add_multi_trunk_candidates.
struct Axis {
    bool along_horiz;
    // Centre / extent along and perpendicular to the spine axis.
    int along_center(const Rect& r) const { return along_horiz ? (r.x1 + r.x2) / 2 : (r.y1 + r.y2) / 2; }
    int perp_center (const Rect& r) const { return along_horiz ? (r.y1 + r.y2) / 2 : (r.x1 + r.x2) / 2; }
    int along_lo(const Rect& r) const { return along_horiz ? r.x1 : r.y1; }
    int along_hi(const Rect& r) const { return along_horiz ? r.x2 : r.y2; }
    int perp_lo (const Rect& r) const { return along_horiz ? r.y1 : r.x1; }
    int perp_hi (const Rect& r) const { return along_horiz ? r.y2 : r.x2; }
    // Along / perpendicular coordinate of a Point.
    int along(const Point& p) const { return along_horiz ? p.x : p.y; }
    int perp (const Point& p) const { return along_horiz ? p.y : p.x; }
    // Nearest along-axis face of r in the direction of `toward`.
    int along_face(const Rect& r, int toward) const { return along_horiz ? r.face_x(toward) : r.face_y(toward); }
    int perp_face (const Rect& r, int toward) const { return along_horiz ? r.face_y(toward) : r.face_x(toward); }
    // Build a Point / Segment in (along, perp) space.  mkseg mirrors make_seg's
    // output exactly (start/end/layer_hint, default is_jog=false, edge_id=-1).
    Point mkpt(int along, int perp) const { return along_horiz ? Point{along, perp} : Point{perp, along}; }
    Segment mkseg(int a1, int p1, int a2, int p2, int layer) const {
        Segment s; s.start = mkpt(a1, p1); s.end = mkpt(a2, p2); s.layer_hint = layer; return s;
    }
};

// A busterm is a connection point on a block face.  Currently represented by
// the block name and its bounding box; can be refined to a pin location later.
struct Busterm {
    std::string       block_name;
    Rect              bbox;       // possibly margin-inset (union of all rects)
    Rect              orig_bbox;  // always the full physical extent (union)
    // Non-empty for multi-rect blocks: each element is one candidate connection
    // rectangle (unshrunk).  Empty = single-rect block (use orig_bbox as before).
    std::vector<Rect> rects;
    TegMode           teg_mode = TegMode::THRU;
};
// Per-segment busterm annotation: .first = busterm at segment start endpoint,
// .second = busterm at segment end endpoint.  nullopt means the endpoint is an
// internal junction (connects to another segment, not a block face).
using SegEndpoints = std::pair<std::optional<Busterm>, std::optional<Busterm>>;
struct Topology {
    std::string type;
    std::vector<Segment> segments;
    int estimated_wirelength  = 0;
    int trunk_location        = 0;
    int pass_through_count    = 0;  // blocks whose bbox contains the trunk (no stub generated)
    // Populated by generate_candidates so that
    // ConnTopology::infer_connections can identify busterm connections without
    // scanning the entire floorplan.  Key = segment index.
    std::map<int, SegEndpoints> seg_busterms;
    // Authoritative seg-to-seg junction annotation (single-source-of-topo-truth
    // Phase 4): (seg_idx, endpoint 0=start/1=end) -> the segments that endpoint
    // lands on (sorted ascending; usually one, more when 3+ segments meet at a
    // point).  Index-only — junction coordinates are always derived from the
    // identified segments' geometry, so offsetting/mutating segments can never
    // leave stale coordinates here.  Populated once at generation
    // (annotate_seg_conns); ConnTopology::infer_connections READS it and never
    // re-derives seg-to-seg connectivity from geometry.  A missing key means the
    // endpoint is a busterm tap or a free end — never a cue to go guessing.
    std::map<std::pair<int,int>, std::vector<int>> seg_conns;
    // Over-the-block bridge segments: block_name → bridge Segment.
    // Non-empty only when teg_mode=OVER and trunk falls in the gap between rects.
    std::map<std::string, Segment> bridge_segments;
    // All block names this topology must connect (src + all dsts).
    // Set by generate_candidates; used by connectivity verifier to detect
    // pass-through blocks that have no explicit BUSTERM endpoint connection.
    std::vector<std::string> connected_block_names;
    // Per-segment bit membership for FAN-IN bundles (tapered bus): segment
    // index → sorted global bit indices carried by that segment.  EMPTY map =
    // every segment carries every bit (all non-fan-in bundles — the universal
    // historical behavior).  DERIVED, never persisted and excluded from
    // topo_uid: re-computed from the bundle's per-net endpoints at plan/NUTS
    // time (derive_fanin_seg_bits); the dogleg split propagates the split
    // trunk's bits to its new pieces in place.
    std::map<int, std::vector<int>> seg_bits;
    // Per-segment BUSTERM TAP membership, the companion seg_bits was missing:
    // (segment index, tap ordinal 0=first / 1=second) → sorted global bit
    // indices whose OWN driver→receiver path terminates at that tapped block.
    //
    // seg_bits says which bits ride a segment; this says which of them the
    // segment's block TAPS actually belong to.  Without it a tap is applied to
    // every bit on the segment, so a trunk tapping a far receiver re-extends
    // bits that never go there — metal reaching nothing, invisible to every
    // check (flow/antenna_taper_passthru.buda).  EMPTY map = every tap serves
    // every bit (all non-tapered bundles — the historical behavior).
    //
    // A MISSING key means "serves every bit" — fail-SAFE, since it can only
    // keep a tap, never drop one.  It used to be reachable two ways, and only
    // one of them is a true statement about the topology: "untapered", and
    // "the walk did not reach this tap".  derive_fanin_seg_bits now SEEDS an
    // entry for every tap on a segment it gave bits to, so inside a tapered
    // topology the second reading cannot arise and an EMPTY list is a recorded
    // verdict — this tap serves no bit — rather than a gap.
    //
    // The second reading was never observed to fire.  The shape that would
    // produce it is a block tapped by two segments where the group routing to
    // it walks the other one; instrumenting the derivation found ZERO such
    // taps across the 41-flow corpus and every taper vehicle, and the shape
    // resisted synthesis (a group whose receiver is unreachable takes the
    // mark_all fallback, which marks every tap).  The seeding is therefore a
    // convention made explicit, measured inert — not a repair with a witness.
    //
    // DERIVED exactly like seg_bits: never persisted, excluded from topo_uid,
    // recomputed by derive_fanin_seg_bits.  The dogleg split leaves it alone —
    // as it leaves seg_busterms alone, and for the same reason: the split
    // APPENDS its pieces and never lands on a tapped endpoint, so existing keys
    // stay valid and the new pieces correctly have neither a tap nor a
    // membership.  (seg_bits IS propagated there; these two do not move
    // together through the dogleg, and reading across from it will mislead.)
    std::map<std::pair<int,int>, std::vector<int>> seg_busterm_bits;
    // Blocks the trunk is routed *through* on purpose (opt-in feedthru): the trunk
    // is split at the block's two crossed faces (no stub to the block) and the
    // block's own lower-level router bridges the gap.  Empty unless a straddling
    // block was marked feedthru for the trunk layer (Floorplan::get_feedthru).
    std::vector<std::string> feedthru_blocks;
    // Realization-aware wirelength envelope [wl_lo, wl_hi]: the abstract-WL
    // interval this candidate's slide/span DOF permit (joint slide minimum ..
    // loose outer bound — the same interval dump_topologies/report_wl show).
    // DERIVED annotation like seg_bits: never persisted, excluded from the
    // fingerprint/topo_uid; set by the session (Python _topology_wl_interval)
    // before planning when the planner's kWLSpread knob is enabled, so the WL
    // term can price realization risk (spread = wl_hi - wl_lo) on top of the
    // nominal segment-sum (which for slide-coupled MST shapes sits at the
    // envelope bottom and inverts the true ranking — flow/big_data_test/b44.buda).
    // -1 = unset (planner falls back to estimated_wirelength).
    double wl_lo = -1.0;
    double wl_hi = -1.0;

    // ── Derived-analysis cache (Phase B, docs/internal/topo_conn_unification.md).
    // Filled by topology_analysis.cpp's analyze(); validated by CONTENT
    // fingerprint, not by mutation discipline — Topology is an open struct
    // (its fields are freely assigned in C++ generators and via the pybind
    // setters), so a rev-counter protocol would silently break on any missed
    // bump.  analyze() instead re-fingerprints the analysis inputs on every
    // call and recomputes on mismatch: a stale cache is structurally
    // impossible, and no mutator anywhere needs to know this cache exists.
    // Copies share the (immutable) analysis — correct, since a copy has the
    // same content until mutated, at which point its fingerprint diverges.
    mutable std::shared_ptr<const TopoAnalysis> analysis_cache_;

    // Drop the cached analysis so the NEXT analyze() recomputes from scratch.
    // Needed ONLY for a post-analysis mutation of the one analysis input the
    // content fingerprint deliberately omits: Segment::perp_clamp_lo/hi (read
    // by analyze() pass 3, but left out of topology_fingerprint because hashing
    // it would churn every persisted topo_uid).  A generation-time clamp needs
    // no invalidation — it precedes the first analyze(); a POST-generation
    // clamp (set_drop_dangling clamp/clamp_drop) sets perp_clamp then calls
    // this.  No other mutator needs it (every other analysis input IS
    // fingerprinted, so analyze() self-invalidates on those).
    void clear_analysis_cache() { analysis_cache_.reset(); }
};

// ── Segment-index discipline helpers ────────────────────────────────────────
// The seg_busterms / seg_conns maps are keyed by segment index, so every
// generator must keep those keys consistent when it appends or front-inserts a
// segment.  These centralize that bookkeeping so no generator hand-rolls it
// (erase_segment is the removal counterpart in topology.cpp).

// Append `seg`, capturing its index BEFORE the push; seed the start-endpoint
// busterm when `seed` is non-null (the trunk/BITRUNK inline-seed flavour) and
// leave it null for the 2-pin flavour where annotate_endpoints fills it later.
// Returns the new segment's index.
inline int emit_tap_segment(Topology& t, const Segment& seg, const Busterm* seed) {
    int si = (int)t.segments.size();
    t.segments.push_back(seg);
    if (seed) t.seg_busterms[si].first = *seed;
    return si;
}

// Insert `spine` at index 0 and shift every index-keyed record up by one, so the
// already-seeded annotations stay attached to their segments (mechanizes the
// BITRUNK root-prepend).  Re-keys BOTH maps, mirroring erase_segment's discipline:
// today's caller prepends before seg_conns is derived (so that loop is a no-op),
// but as a shared helper it must also fix seg_conns — which is index-keyed on both
// sides (the (seg_idx,endpoint) key AND the partner-segment values) — so a caller
// that front-inserts after annotate_seg_conns cannot silently mis-wire junctions.
inline void prepend_segment(Topology& t, const Segment& spine) {
    t.segments.insert(t.segments.begin(), spine);
    std::map<int, SegEndpoints> shifted_bt;
    for (auto& kv : t.seg_busterms) shifted_bt[kv.first + 1] = kv.second;
    t.seg_busterms = std::move(shifted_bt);
    std::map<std::pair<int,int>, std::vector<int>> shifted_conns;
    for (auto& kv : t.seg_conns) {
        std::vector<int> partners;
        partners.reserve(kv.second.size());
        for (int p : kv.second) partners.push_back(p + 1);
        shifted_conns[{kv.first.first + 1, kv.first.second}] = std::move(partners);
    }
    t.seg_conns = std::move(shifted_conns);
    if (!t.seg_bits.empty()) {           // index-keyed taper membership
        std::map<int, std::vector<int>> shifted_bits;
        for (auto& kv : t.seg_bits) shifted_bits[kv.first + 1] = std::move(kv.second);
        t.seg_bits = std::move(shifted_bits);
    }
    // Tap membership, keyed (seg_idx, tap ordinal) — the same argument as
    // seg_conns above, and it bites harder: seg_busterm_serves_bit resolves the
    // ordinal against the SHIFTED seg_busterms and then reads this map, so an
    // unshifted entry would answer for segment si-1.  Both consumers read the
    // one predicate, so they would agree on the wrong answer and nothing would
    // report it — the placer retracts a bit off a real block face and the audit
    // concurs.  Unreachable today (the only caller prepends during generation,
    // before derive_fanin_seg_bits runs), which is exactly when to close it.
    if (!t.seg_busterm_bits.empty()) {
        std::map<std::pair<int,int>, std::vector<int>> shifted_taps;
        for (auto& kv : t.seg_busterm_bits)
            shifted_taps[{kv.first.first + 1, kv.first.second}] = std::move(kv.second);
        t.seg_busterm_bits = std::move(shifted_taps);
    }
}

// Return a deep copy of `t` with all geometry shifted by (dx, dy): every
// segment, every seg_busterms Busterm bbox (and orig_bbox / rects), and every
// bridge segment.  Crucially preserves seg_busterms so an offset (per-instance,
// hier) candidate keeps its authoritative endpoint annotation — otherwise
// ConnTopology falls back to a geometric face search that can mis-tap a block
// whose corner coincidentally grazes an L/Z bend (an unintentional feedthru).
// `trunk_location` is metadata (not a coordinate) and is copied unchanged.
// When `name_prefix` is non-empty, every cell-local block name (one with no '/')
// — in seg_busterms, connected_block_names, feedthru_blocks, and bridge keys —
// is qualified to `name_prefix + "/" + name` so it resolves against the global
// instance-coord floorplan; absolute names are left unchanged.
Topology offset_topology(const Topology& t, int dx, int dy,
                         const std::string& name_prefix = "");

// ── Orientation transforms (bottom-up + hier expansion) ─────────────────────
// The 8-orientation token convention is bdb.cpp/gds_io.cpp's: mirror about
// the X axis FIRST, then rotate CCW (N/W/S/E = 0/90/270-free letters; F* =
// mirrored).  OrientMap is the same transform in output-normalized form over
// a w×h cell box: optionally swap the axes (90/270), then reflect per OUTPUT
// axis — the transformed box has its lower-left at the origin, so a
// transformed instance's bbox lines up with the placed component's bbox.
struct OrientMap {
    bool swap = false;   // 90/270: axes exchange (box becomes h×w)
    bool rx = false;     // reflect the output x axis
    bool ry = false;     // reflect the output y axis
};
OrientMap orient_map(const std::string& orient);
// Token algebra: compose (outer ∘ inner) and invert, staying in the 8-token
// group.  Mirrors and 180° are involutions; 90/270 invert to each other.
std::string orient_compose(const std::string& outer, const std::string& inner);
std::string orient_inverse(const std::string& orient);

// Orientation-aware sibling of offset_topology: map every coordinate of `t`
// (segments incl. perp clamps, busterm rects, bridges) from the cell-local
// frame through `orient` over a cell_w×cell_h cell box, then shift by
// (dx, dy); block names are qualified with `name_prefix` exactly like
// offset_topology.  Supports all 8 orientations — under 90/270 an H segment
// becomes V (and vice versa), so any per-segment LAYER decisions made in the
// cell frame are direction-invalid and must be re-assigned by the caller
// (the top-down planner does; bottom-up copies therefore restrict to the
// direction-preserving set N/S/FN/FS).  orient "N" reduces to
// offset_topology.
Topology transform_topology(const Topology& t, const std::string& orient,
                            int cell_w, int cell_h, int dx, int dy,
                            const std::string& name_prefix = "");

// Explicitly (re)annotate a topology's seg_busterms from a floorplan's blocks —
// the authoritative endpoint annotation ConnTopology reads.  For hand-built or
// BDB-reloaded topologies that did not go through generate_candidates; call it
// once before ConnTopology::build.  Also (re)derives seg_conns (below), so one
// call fully annotates a hand-built topology.  (ConnTopology no longer
// geometrically guesses busterms OR seg-to-seg junctions — see
// docs/internal/single_source_topo_truth.md.)
void annotate_topology(Topology& topo, const Floorplan& fp);

// Remove segment `idx`, re-keying seg_busterms and seg_conns on both the key
// and the partner side (the removal counterpart of emit_tap_segment /
// prepend_segment's index discipline).  Shared by complete_relay_junctions and
// the TopoEdit operations (topo_edit.h).
void erase_segment(Topology& topo, int idx);

// Per-segment bit count for the tapered fan-in width model: the segment's
// member-bit count when seg_bits carries an entry for it, else the bundle's
// full bit count (all non-fan-in bundles, and any segment the derivation did
// not restrict).  Every width consumer (planner charging, NUTS extraction,
// DNUTS emission) goes through this so the taper cannot drift between stages.
inline int seg_bit_count(const Topology& t, int si, int nbits) {
    auto it = t.seg_bits.find(si);
    if (it != t.seg_bits.end() && !it->second.empty()) return (int)it->second.size();
    return nbits;
}

// Does the BUSTERM tap on segment `si` to block `block` serve bit `bit`?
//
// The ONE predicate both consumers of seg_busterm_bits read: DetailedNUTS
// (which re-extends a bit's span to a tap it serves, and retracts a stale end
// past one it does not) and check_dnuts (which requires a bit's span to reach
// the taps it serves).  Sharing it is the point — a tap the placer declines to
// reach for a bit must be a tap the audit declines to demand, or the two stages
// disagree and every tapered fan-out reads as a BUSTERM_OPEN.
//
// Resolves the tap ORDINAL by block name against seg_busterms, so it does not
// depend on any caller's conn ordering.  With nothing recorded — no membership
// for this (segment, ordinal), or no seg_busterms entry at all — every tap
// serves every bit, which is the untapered historical behavior.
bool seg_busterm_serves_bit(const Topology& topo, int si,
                            const std::string& block, int bit);

// Derive per-segment bit membership for a FAN-IN bundle (tapered bus).
// Inputs: one driver block + receiver block list PER BIT (index = the
// bundle's net order, i.e. the global bit index).  For each bit, walk the
// segment graph (seg_conns junctions) from the segments attaching its driver
// block (BUSTERM tap from seg_busterms, or a pass-through crossing of the
// block's rects) to the segments attaching each receiver, marking the
// BFS-forest path to the first attaching receiver segment as carrying the
// bit (a valid driver→receiver route; not necessarily the geometrically
// nearest attach point).  A bit whose driver or
// receiver attaches to no segment, or with no path, falls back to ALL
// segments (conservative full-width — the net-driver fidelity check reports
// it).  Writes topo.seg_bits and returns the list of fallen-back bit
// indices (empty = clean taper).
std::vector<int> derive_fanin_seg_bits(
    Topology& topo, const Floorplan& fp,
    const std::vector<std::string>& driver_per_bit,
    const std::vector<std::vector<std::string>>& receivers_per_bit);

// Explicitly (re)derive a topology's seg_conns from its segments: for each
// endpoint that is not a busterm tap (per seg_busterms), record which
// perpendicular segments it lands on.  The SAME zero-tolerance predicate
// ConnTopology used to run per build — executed ONCE, at generation (or after a
// deliberate geometry mutation such as a NUTS dogleg split), so every stage
// reads identical connectivity.  Clears and rebuilds the field (idempotent).
void annotate_seg_conns(Topology& topo);

// Flip one MST edge's L realization in place (per-edge L/Z DOF).  An axis-diagonal
// edge is realized as two `edge_id`-tagged legs meeting at a bend; this re-routes
// them with the bend at the OPPOSITE corner and swaps the legs' routing layers
// (H-first <-> V-first) — the same Manhattan length, a different congestion
// footprint.  The two Segment slots are reused (indices unchanged, so seg_busterms
// stays valid), but the junction geometry moves, so the caller must re-derive
// connectivity afterwards (annotate_seg_conns / annotate_topology).  It is an
// involution (flipping the same edge twice restores it).
//
// Returns false (no change) when the flip is not valid:
//   • `edge_id < 0` (the "not an MST-edge leg" sentinel) or an unknown id;
//   • the edge is not a clean 2-leg diagonal L (straight/shared realization, or
//     collinear legs with no alternate);
//   • the alternate bend lands INSIDE a block or on a block CORNER — this is
//     exactly the `corner_diagonal_L` case, whose two legs were deliberately
//     routed AROUND a shared block corner; the opposite bend is that corner, so
//     flipping it would route onto the obstacle the generator avoided.  The
//     Floorplan is consulted for this check.
bool flip_mst_edge(Topology& topo, int edge_id, int h_layer, int v_layer,
                   const Floorplan& fp);

// Per-block corner margin: keeps trunk/stub connections away from block corners.
// dx: margin along the horizontal direction (applied to top/bottom faces, extent in X).
// dy: margin along the vertical direction   (applied to left/right  faces, extent in Y).
// A margin of 0 means no constraint beyond the face extent (default).
struct BlockCornerMargin {
    int dx = 0;
    int dy = 0;
};

struct MinStubLength {
    int global = 20;
    std::map<int, int> per_dir;   // 0=HORIZONTAL, 1=VERTICAL
    std::map<int, int> per_layer;
};

// Opt-in feedthru: a block declared routable-through on a given trunk layer is not
// stubbed to and is allowed to be crossed (the block's own router bridges the gap).
// Feedthru is genuinely per-(block, layer): unlike MinStubLength's independent
// per_block/per_layer fallback axes, a true grid is needed so a block can be
// feedthru on one layer but not another.  Rules are resolved most-specific-first:
//   (block, layer) > (block, *) > (*, layer) > (*, *)
// i.e. a block-scoped rule beats a layer-scoped rule.  Each rule stores an explicit
// bool, so a more-specific `off` overrides a broader `on` and vice-versa.
struct FeedthruConfig {
    std::map<std::pair<std::string, int>, bool> per_block_layer;  // (block, layer)
    std::map<std::string, bool>                 per_block;         // (block, *)
    std::map<int, bool>                         per_layer;         // (*, layer)
    bool                                        global = false;    // (*, *)
};

// Per-direction outer margin for U-shape (and UU-shape) detour trunks.
// -1 means "auto" (topology generator uses its internal heuristic).
struct DetourChannelSpec {
    int north = -1;  // margin above the bundle bounding box (larger Y)
    int south = -1;  // margin below the bundle bounding box (smaller Y)
    int east  = -1;  // margin right of the bundle bounding box (larger X)
    int west  = -1;  // margin left  of the bundle bounding box (smaller X)
};

struct KeepoutZone {
    Rect bbox;
    std::set<int> layer_ids;
};

// Floorplan identity for the analysis cache (Phase B): every Floorplan carries
// a process-unique uid and a revision bumped by each mutator, so a cached
// analysis can name exactly which floorplan STATE it was computed against.
// Copy/assign takes a FRESH uid — two Floorplans that diverge after a copy
// must never alias each other's cache entries (same-uid + same-rev would).
struct FpUid {
    uint64_t v;
    FpUid() : v(next()) {}
    FpUid(const FpUid&) : v(next()) {}
    FpUid& operator=(const FpUid&) { v = next(); return *this; }
private:
    static uint64_t next() {
        static std::atomic<uint64_t> c{0};
        return ++c;
    }
};

class Floorplan {
public:
    // Cache key: which floorplan (uid) in which state (rev).  rev is bumped by
    // EVERY mutator below — all Floorplan state is behind methods, so unlike
    // the open Topology struct the revision discipline is airtight here.
    uint64_t uid() const { return uid_.v; }
    uint64_t rev() const { return rev_; }

    void add_block(const std::string& name, int x1, int y1, int x2, int y2);
    void add_keepout_zone(int x1, int y1, int x2, int y2, const std::vector<int>& layer_ids);
    const std::vector<KeepoutZone>& get_keepout_zones() const { return keepouts_; }
    // Mark a block as a hierarchy container (an envelope enclosing finer blocks)
    // rather than a solid leaf cell.  Containers are transparent to LOW layers:
    // their internal channels carry routing congestion (charged via the child
    // blocks' Hanan cuts), whereas leaf cells block LOW layers entirely.
    void set_container(const std::string& name, bool is_container = true);
    bool is_container(const std::string& name) const;
    // Keepouts seen by LOW (non-TOP) layers: the user-defined zones plus an
    // implicit zone for every solid leaf cell (each cell rect), tagged with the
    // given non-TOP layer ids.  A LOW segment cannot route over a leaf cell —
    // the cell behaves as a keepout for the whole lower stack — while TOP layers
    // (absent from low_layer_ids) cross cells freely.  Shared by the planner's
    // band-capacity model and abstract/detailed NUTS so all three agree.
    std::vector<KeepoutZone> low_layer_keepouts(const std::vector<int>& low_layer_ids) const;
    // Multi-rect block: stores each rect individually; add_block is called
    // internally with the union bounding box for backward compatibility.
    void add_block_rects(const std::string& name, const std::vector<Rect>& rects,
                         TegMode mode = TegMode::THRU);
    // Returns the individual rects for a multi-rect block, or empty for single-rect.
    std::vector<Rect> get_block_rects(const std::string& name) const;
    // TEG mode: controls over-the-block bridge generation for multi-rect blocks.
    void    set_block_teg_mode(const std::string& name, TegMode mode);
    TegMode get_block_teg_mode(const std::string& name) const;
    // Set corner margin for a previously-added block (absolute units).
    void set_block_corner_margin(const std::string& name, int dx, int dy);
    // Set global corner margin applied to all blocks that have no per-block override.
    void set_global_corner_margin(int dx, int dy);

    // Minimum stub length configuration (global, per-direction, per-layer).
    void set_min_stub_length(int val) { min_stub_len_.global = val; ++rev_; }
    void set_min_stub_length_dir(int dir, int val) { min_stub_len_.per_dir[dir] = val; ++rev_; }
    void set_min_stub_length_layer(int layer_id, int val) { min_stub_len_.per_layer[layer_id] = val; ++rev_; }

    int get_min_stub_length(int dir, int layer_id) const {
        if (min_stub_len_.per_layer.count(layer_id)) return min_stub_len_.per_layer.at(layer_id);
        if (min_stub_len_.per_dir.count(dir)) return min_stub_len_.per_dir.at(dir);
        return min_stub_len_.global;
    }

    // Feedthru config (global / per-block / per-layer / per-(block,layer)).
    void set_feedthru(bool v)                                  { feedthru_.global = v; ++rev_; }
    void set_feedthru_block(const std::string& name, bool v)   { feedthru_.per_block[name] = v; ++rev_; }
    void set_feedthru_layer(int layer_id, bool v)              { feedthru_.per_layer[layer_id] = v; ++rev_; }
    void set_feedthru_block_layer(const std::string& name, int layer_id, bool v) {
        feedthru_.per_block_layer[{name, layer_id}] = v; ++rev_;
    }
    // Resolve feedthru for a block on a trunk layer, most-specific-first.
    bool get_feedthru(const std::string& name, int layer_id) const {
        auto it = feedthru_.per_block_layer.find({name, layer_id});
        if (it != feedthru_.per_block_layer.end()) return it->second;
        if (feedthru_.per_block.count(name))     return feedthru_.per_block.at(name);
        if (feedthru_.per_layer.count(layer_id)) return feedthru_.per_layer.at(layer_id);
        return feedthru_.global;
    }
    // True iff any feedthru rule has been set — lets the generator skip the
    // crossed-block scan entirely on the common (no-feedthru) path.
    bool feedthru_active() const {
        return feedthru_.global || !feedthru_.per_block.empty()
            || !feedthru_.per_layer.empty() || !feedthru_.per_block_layer.empty();
    }

    // Detour channel outer margin.
    // dirs is any combination of N/S/E/W, or the shorthands Y (N+S), X (E+W), A (all).
    // size < 0 resets the specified directions back to auto.
    void set_detour_channel(const std::string& dirs, int size);
    const DetourChannelSpec& get_detour_channel() const { return detour_channel_; }

    Rect get_block_bounds(const std::string& name) const;
    // True iff a block with this exact name has been registered (get_block_bounds
    // silently returns a degenerate {0,0,0,0} for unknown names, so callers that
    // need to validate an endpoint must use this instead).
    bool has_block(const std::string& name) const;
    // Returns per-block margin if set, else global margin, else {0,0}.
    BlockCornerMargin get_block_corner_margin(const std::string& name) const;
    void get_hanan_grid(std::vector<int>& x_coords, std::vector<int>& y_coords) const;
    std::vector<std::pair<std::string, Rect>> get_all_blocks() const {
        std::vector<std::pair<std::string, Rect>> res;
        for(auto const& [key, val] : blocks_) res.push_back({key, val});
        return res;
    }
private:
    FpUid uid_;
    uint64_t rev_ = 0;
    std::map<std::string, Rect>          blocks_;
    std::map<std::string, std::vector<Rect>> block_rects_;  // only for multi-rect blocks
    std::set<std::string>                containers_;       // hierarchy envelopes (not leaf cells)
    std::map<std::string, TegMode>       teg_modes_;
    std::map<std::string, BlockCornerMargin> corner_margins_;
    BlockCornerMargin global_corner_margin_{};
    MinStubLength min_stub_len_;
    FeedthruConfig feedthru_;
    DetourChannelSpec detour_channel_;
    std::vector<KeepoutZone> keepouts_;
};
class TopologyGenerator {
public:
    explicit TopologyGenerator(const Floorplan& fp) : floorplan_(fp) {
        // Opt-in defaults from the environment so a whole corpus can be A/B'd
        // without editing every flow (a later set_*(v) still wins) — the
        // default-flip study harness for both opt-in generation knobs.
        if (const char* e = std::getenv("BUDA_SPINE_RELAYS"))
            allow_spine_relays_ = (std::string(e) == "1");
        if (const char* e = std::getenv("BUDA_MULTI_TRUNK"))
            allow_multi_trunk_ = (std::string(e) == "1");
        if (const char* e = std::getenv("BUDA_MST_LEG_TRIM"))
            allow_mst_leg_trim_ = (std::string(e) == "1");
    }

    // Busterm mode (default true): route segments terminate at the nearest
    // block face rather than at the block centre.  Set to false to restore
    // the original centre-to-centre behaviour.
    void set_busterm_mode(bool v) { use_busterm_ = v; }

    // Override the default H and V layer IDs used for segment layer_hint.
    // Defaults: h=4 (M4), v=5 (M5).  Call this after determining which layers
    // are actually defined in the layer stack so no undefined layer is emitted.
    void set_layer_ids(int h, int v) {
        h_layer_ = h; v_layer_ = v;
        all_h_layers_ = {h}; all_v_layers_ = {v};
    }

    // Inform the generator of ALL available H and V layer IDs so keepout
    // avoidance can distinguish "keepout on one H layer" (planner re-assigns)
    // from "keepout on ALL H layers" (trunk position must be skipped).
    // Must be called after set_layer_ids if multiple H or V layers exist.
    void set_all_h_layers(const std::vector<int>& layers) { all_h_layers_ = layers; }
    void set_all_v_layers(const std::vector<int>& layers) { all_v_layers_ = layers; }

    // Double-detour mode (default false): add UU_VHV / UU_HVH topologies where
    // the src stub is an L-shape that exits a SIDE face of the src block rather
    // than the primary face used by the standard U shape.  Useful when the normal
    // U route is congested.  Only meaningful in busterm mode.
    void set_double_detour(bool v) { allow_double_detour_ = v; }

    // Multi-trunk mode (default false): for high-fan-out nets over regular
    // datapath-like placements, emit two-level BITRUNK_HVH / BITRUNK_VHV trees —
    // a root spine (one orientation) feeding perpendicular branch trunks, each
    // tapping a cluster of aligned leaf blocks — in BOTH orientations, so the
    // planner can pick the H/T shape that beats a single spine.  Opt-in so
    // default candidate rankings are unchanged.
    void set_multi_trunk(bool v) { allow_multi_trunk_ = v; }

    // Hanan-line trunk loci (default TRUE since the default flip): ALSO
    // sample n-pin trunk loci ON the in-bbox Hanan lines themselves, not just
    // at channel midpoints, so a block-edge-aligned trunk can nominal at the
    // geometric WL floor (the b44 +500 overshoot —
    // docs/internal/wishlist-topo.md "Nominal-WL comparability", piece (a)).
    // Flipped default-on after the degenerate-loci gate landed (face-graze
    // junction repair + loci pinch gate + DISCONNECTED island gate — see
    // docs/internal/hanan_loci_flip_audit.md); scripts opt OUT per command
    // with `no_hanan_loci` (the CLI calls set_hanan_loci(false)), and the
    // legacy `hanan_loci` flag is still accepted as a keep-on no-op.
    void set_hanan_loci(bool v) { allow_hanan_loci_ = v; }

    // Star→spine relay completion (default false — bit-identical when off;
    // opt in per generator via this setter or corpus-wide via the study-run
    // env `BUDA_SPINE_RELAYS=1`.  A per-command `.buda` token is a follow-up).
    // At a degree-≥3 MST relay whose incident stubs split as ≥2 parallel
    // (majority) + exactly 1 perpendicular (minority), complete_relay_junctions
    // builds a single collector SPINE (along the minority axis) that all the
    // majority stubs T-tap independently, instead of chaining the landings with
    // over-the-cell bracket connectors.  The minority stub is extended across
    // the block to the opposite face — that face landing is the block's single
    // busterm-conn and keeps a bounded slide, while every majority tap keeps its
    // OWN full perpendicular slide (never merged into one shared track).  Fewer
    // segments and more NUTS freedom at star hubs; see
    // docs/internal/wishlist-topo.md "star→spine relay completion".
    void set_spine_relays(bool v) { allow_spine_relays_ = v; }

    // Shared-leg trim for MST edges (default false — byte-identical when off;
    // opt in per generator via this setter, from a script with
    // `set_trim_mst_legs on`, or corpus-wide via the study env
    // BUDA_MST_LEG_TRIM=1).
    //
    // realize_mst_edge routes each edge on its own, so two edges incident on
    // the SAME block start at the same face point; when both go L-shaped with
    // the same first axis the shorter one's leg lies inside the longer one's,
    // and the longer leg's prefix is a duplicate with a FREE END.  Its real
    // cost is downstream — it pushes the leg's end PAST the divergence
    // junction, making that junction a mid-span conn, and DetailedNUTS only
    // snaps a bit to its own via at an ENDPOINT conn.  Trimming cuts each leg
    // back to the shorter sibling's far end (that edge's own bend, or a block
    // face, so connectivity holds by construction).
    //
    // OPT-IN because the mutation is not local to the wire it fixes.
    // Candidates are WL-SORTED, so shortening one re-sorts the pool, and a
    // re-sort changes which candidates clear generation gates: measured on
    // chip3_topdown, 9 of 640 bundles change selected topology TYPE and 3
    // change pool SIZE (control, main vs main: 0).  Downstream, leaf bundles
    // elsewhere lose tracks they were getting — chip3_topdown 260 -> 636
    // unplaced bits.  Not bad geometry (the cut point is sound); a moved
    // search space.  Same reason set_prune_dominated / set_dedup_loci /
    // set_drop_dangling are opt-in, and the same caveat applies: trimming
    // renumbers candidate indices, so select_topology pins must come from an
    // opted-in run.
    //
    // Covers the START-shared half only.  Legs that END at the shared node
    // (which happens when that node holds the higher MST index, since
    // compute_mst emits u<v and realize_mst_edge routes u->v) are NOT trimmed,
    // nor is the same geometry when TRUNK stub generation produces it; both
    // are pinned as strict xfails in test_mst_shared_leg_prefix.py.
    void set_mst_leg_trim(bool v) { allow_mst_leg_trim_ = v; }

    // Unified entry point: 1 dst → L/Z/U shapes; N dsts → trunk+branch shapes.
    std::vector<Topology> generate_candidates(
        const std::string& src_name,
        const std::vector<std::string>& dst_names);

    // Uniform coverage gate: drop candidates that leave a connected_block_names
    // block with no busterm tap and no pass-through segment (verify's
    // BUSTERM_OPEN — a silent open the planner cannot detect).  Applied by
    // generate_candidates on every generation path; public for direct testing.
    // Never strands a bundle: if every candidate is uncovered, the list is kept
    // unchanged and a warning is printed (the planner ladder still commits one).
    void filter_uncovered(std::vector<Topology>& candidates) const;

    // Slide-safe endpoint-anchoring gate for BITRUNK candidates.  A BITRUNK
    // trunk may "cover" an endpoint block only by grazing/passing over it at the
    // NOMINAL position (no busterm face tap); check_topo accepts that, but a
    // NUTS-time slide moves the trunk off the block and every bit opens
    // (BUSTERM_OPEN — bigHalf bus_038).  Drop a BITRUNK candidate whose any
    // endpoint block is covered ONLY by a pass-through whose perp slide window
    // is NOT contained in that block's perp extent (so it can slide away).
    // Anchored blocks and slide-bounded pass-throughs survive.  Keep-if-only-
    // option, like filter_uncovered — never strands a bundle.  Scoped to
    // BITRUNK types, so non-BITRUNK pools are byte-identical.
    void filter_unanchored_bitrunk(std::vector<Topology>& candidates) const;

    // Where generation notes (the "[TopoGen] …" drop/warning lines) go.
    // Default std::cerr — byte-identical to the historical prints.  The
    // parallel batch fan-out (generate_candidates_batch, bind_routing.cpp)
    // points each task's generator at a private buffer and replays the
    // notes in task order, so parallel generation prints exactly the
    // sequential lines (chip_flow_parallelism.md P5).
    void set_note_stream(std::ostream* os) { notes_ = os; }

private:
    std::ostream& notes() const { return *notes_; }
    std::ostream* notes_ = &std::cerr;
    // Internal dispatch targets.
    std::vector<Topology> generate_2pin(
        const std::string& src_name,
        const std::string& dst_name);
    std::vector<Topology> generate_npin(
        const std::string& src_name,
        const std::vector<std::string>& dst_names);
    void filter_pinched(std::vector<Topology>& candidates);
    // Shared post-emission pipeline both dispatch targets flow through:
    // seg-conn annotation → sort → keepout cull (drop a candidate whose ANY
    // segment is blocked on all same-direction layers by explicit keepout
    // zones) → pinch filter → connected_block_names fill.  One place, so a
    // filter fix or a new gate lands once instead of per-path (previously
    // only generate_2pin ran the keepout cull; an n-pin MST/BITRUNK/stub
    // segment through a fully-blocked zone survived to the planner — a
    // silent DNUTS open).  Emission-side gates (trunk-locus keepout probes,
    // min-stub, per-shape structural checks) remain each path's own.
    void finalize_candidates(std::vector<Topology>& candidates,
                             const std::vector<std::string>& block_names);
    const Floorplan& floorplan_;
    bool use_busterm_         = true;
    bool allow_double_detour_ = false;
    bool allow_multi_trunk_   = false;
    bool allow_hanan_loci_    = true;
    bool allow_spine_relays_  = false;
    bool allow_mst_leg_trim_  = false;
    int  h_layer_             = 4;
    int  v_layer_             = 5;
    std::vector<int> all_h_layers_ = {4};
    std::vector<int> all_v_layers_ = {5};

    void add_l_shapes(const Busterm& src, const Busterm& dst, std::vector<Topology>& results);
    // Two blocks that partially OVERLAP admit L routes around their free outer
    // corners (each leg tapping an exclusive, non-overlapping face) that the
    // centre-projection add_l_shapes degenerates away.  Emits the two free-corner
    // L's (L_OVL_*) so the planner has a route that avoids the shared band.
    void add_overlap_corner_ls(const Busterm& src, const Busterm& dst, std::vector<Topology>& results);
    // Corner-wrapping U's (and, under double_detour, UU's) for a partially-
    // overlapping block pair — the replacement for the generic pass-through U's,
    // which for overlapping endpoints cross a block and double back.  Detour
    // columns/rows come from the channel grids (as add_u_shapes).
    void add_overlap_corner_us(const Busterm& src, const Busterm& dst,
                               const std::vector<int>& x_grid, const std::vector<int>& y_grid,
                               std::vector<Topology>& results);
    void add_z_shapes(const Busterm& src, const Busterm& dst, const std::vector<int>& x_grid, const std::vector<int>& y_grid, std::vector<Topology>& results);
    void add_u_shapes(const Busterm& src, const Busterm& dst, const std::vector<int>& x_grid, const std::vector<int>& y_grid, std::vector<Topology>& results);
    void add_uu_shapes(const Busterm& src, const Busterm& dst, const std::vector<int>& x_grid, const std::vector<int>& y_grid, std::vector<Topology>& results);
    // Axis-parameterized trunk generator; add_trunk_h/add_trunk_v are thin
    // forwarders (H = along-x spine, no stub suppression; V = along-y spine, with
    // same-side stub suppression).
    void add_trunk(const Axis& axis, bool suppress_stubs,
                   const std::vector<Point>& pins, const std::vector<Busterm>& blocks,
                   int locus, bool out_of_bbox, std::vector<Topology>& results);
    void add_trunk_h(const std::vector<Point>& pins, const std::vector<Busterm>& blocks,
                     int y_trunk, bool out_of_bbox, std::vector<Topology>& results);
    void add_trunk_v(const std::vector<Point>& pins, const std::vector<Busterm>& blocks,
                     int x_trunk, bool out_of_bbox, std::vector<Topology>& results);
    void add_mst_candidates(const std::vector<Busterm>& blocks, std::vector<Topology>& results);
    void add_trunk_mst_candidates(const std::vector<Busterm>& blocks, std::vector<Topology>& results);
    // Realize ONE MST edge between two already-chosen rects into `out` — the
    // shared geometry of the standalone MST path and the trunk+MST hybrid
    // (abutment -> corner-L -> straight -> diagonal-L cascade).  `prefer_h_first`
    // is the candidate's strategy orientation (H-then-V when true: MST_HV /
    // !is_h trunk).  `gate_straight` also rejects a too-short STRAIGHT leg — the
    // hybrid gates straight legs, the standalone path historically does NOT (only
    // its diagonal legs), so the flag preserves each caller byte-for-byte.
    // Returns false iff a gated leg is under the min-stub floor (caller drops the
    // whole candidate).  Rect SELECTION stays in the caller (standalone picks the
    // closest rect pair across multi-rect blocks; the hybrid pre-picks one rect).
    bool realize_mst_edge(const Rect& r_u, const Rect& r_v,
                          bool prefer_h_first, bool gate_straight,
                          int m_h, int m_v, std::vector<Segment>& out) const;
    void add_multi_trunk_candidates(const std::vector<Point>& pins, const std::vector<Busterm>& blocks, std::vector<Topology>& results);
    // Keepout helpers (used by generate_2pin and generate_npin)
    bool segment_blocked_on_all_layers(const Segment& seg) const;
    // Smart per-edge MST realization: an axis-diagonal edge p1->p2 has two L's
    // (H-leg first vs V-leg first) of IDENTICAL Manhattan length, so the choice is
    // a routability decision, not a wirelength one.  Return the H-first flag to
    // use: keep `default_h_first` unless one of its legs is fully keepout-blocked
    // while the alternate is clear (then flip).  Both blocked / both clear -> keep
    // the default (preserves MST_HV/MST_VH candidate diversity).
    bool choose_edge_h_first(const Point& p1, const Point& p2,
                             bool default_h_first) const;
};
}
