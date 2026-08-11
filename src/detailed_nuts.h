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
#include <limits>
#include <map>
#include <string>
#include <tuple>
#include <vector>
#include "ndr.h"
#include "nuts.h"          // BundleWrapper / NUTSResult (make_bus_segments)
#include "placed_segment.h"
#include "routing_grid.h"

namespace buda {

struct BusSegmentConn {
    int    seg_idx     = -1;
    double at_pos      = 0.0;
    bool   is_endpoint = false;
    bool   lo_end      = false; // true if connection is at the lo-half of this segment
};

struct BusSegment {
    int         bundle_id         = 0;
    int         seg_idx           = 0;
    int         layer             = 0;
    double      span_lo           = 0.0;
    double      span_hi           = 0.0;
    double      interval_lo       = 0.0;
    double      interval_hi       = 0.0;
    int         bit_width         = 1;
    std::string bit_order         = "LO_HI";  // "LO_HI" or "HI_LO"
    bool        timing_critical   = false;
    // Tapered fan-in: the GLOBAL bit indices this segment carries (sorted
    // ascending).  Empty = all bits 0..bit_width-1 (every non-fan-in bundle).
    // The engine then needs only bit_list.size() tracks and emits
    // NetSegments with the global bit_index, so via pairing and
    // net_names[bit_index] resolution work unchanged across segments that
    // carry different subsets.  Populated by make_bus_segments from the
    // selected topology's seg_bits.
    std::vector<int> bit_list;

    // Explicit connectivity list for bit-wire span adjustment.
    std::vector<BusSegmentConn> connections;

    // Along-axis coordinates of this segment's BUSTERM block-face taps.  The
    // per-bit span-follow below snaps endpoints to connected SEG bits, which can
    // overwrite a face end (the abstract span already reaches it); these anchors
    // re-extend each bit's span to its block face so the tap survives (mirror of
    // TrackSegment::busterm_faces in the abstract stage).
    std::vector<double> busterm_faces;
    // Per-face bit membership, parallel to busterm_faces: the bits whose
    // own path terminates at that tapped block.  An EMPTY entry means the
    // face serves every bit (all non-tapered bundles — the historical
    // behavior).  From Topology::seg_busterm_bits; without it a tap
    // re-extends bits that never go to the block it taps.
    std::vector<std::vector<int>> busterm_face_bits;

    // Along-axis intervals where this segment covers a connected block by
    // PASS-THROUGH — it crosses the block's footprint with no tap of its own,
    // the joint every block-coverage check accepts as a valid connection.
    //
    // The same span-follow that can drop a face tap can drop one of these, and
    // more easily: a pass-through block beyond the segment's outermost junction
    // is covered by the abstract span but sits entirely outside the per-bit
    // extent the snap leaves behind (issue #496 — mix.buda bundle 90 covers
    // chip/i_dnuts2_2/u4 at x∈[1680,1730] with a segment whose junctions are at
    // 1095 and 1455, so every bit was trimmed to [1085,1479] and the block
    // opened).  Each interval is already CLIPPED to the abstract span, so
    // re-extending to it can never claim metal abstract NUTS did not reserve.
    //
    // Only blocks with no BUSTERM tap anywhere in the topology are listed —
    // check_dnuts's pass-through coverage check skips explicitly-tapped blocks,
    // and this must police exactly what that check requires.
    std::vector<std::pair<double,double>> passthru_spans;

    // Non-default rule for this bundle's bits (phase 1: rule-uniform per
    // bundle; inactive = byte-identical).  Drives the k-slot/guard/shield
    // placement branch in place_by_layer — see ndr.h.
    NdrSpec     ndr;

    // Abstract NUTS track_position used as anchor for Option B ordering; NaN = unset (fallback)
    double      abstract_pos      = std::numeric_limits<double>::quiet_NaN();

    // Cross-trunk-layer corner resolution: restrict this segment's signal-track
    // choice to [track_lo_bound, track_hi_bound] so it stays on the bounded side
    // of the split (carried from the abstract trunk's bound).  Default = unbounded.
    double      track_lo_bound    = -std::numeric_limits<double>::infinity();
    double      track_hi_bound    =  std::numeric_limits<double>::infinity();
};

// Member-bit count of a BusSegment under the tapered fan-in model: the
// bit_list size when a subset is declared, else the full bit_width.  Every
// track-count consumer in the engine goes through this.
inline int bus_seg_nbits(const BusSegment& bs) {
    return bs.bit_list.empty() ? bs.bit_width : (int)bs.bit_list.size();
}

// Track DEMAND of a BusSegment: member bits through the NDR group
// conversion (ndr.h) — the same arithmetic the planner charged, so
// admission and charging cannot disagree (R4).  Identity for un-governed
// segments, so every default-path comparison is byte-identical.
inline int bus_seg_demand(const BusSegment& bs) {
    return ndr_group_demand(bs.ndr, bus_seg_nbits(bs));
}

// MINIMUM possible demand under R5a crediting: a credit-enabled shielded
// spec may satisfy both END shields with adjacent matching rails, saving
// up to two SIGNAL slots.  Used only for the optimistic early admission
// gate (a pool that can't host even the best case is honestly refused);
// the seat search enforces the real per-seat credited demand.  Identity
// for non-credit specs, so their gates are byte-identical.
inline int bus_seg_min_demand(const BusSegment& bs) {
    return bs.ndr.credit_shields
        ? ndr_group_demand_credited(bs.ndr, bus_seg_nbits(bs), true, true)
        : bus_seg_demand(bs);
}

// One bit-wire; output of stage 9 (kind NET in the placed-segment hierarchy
// — see placed_segment.h; layer/span/track_position/width live on the base
// with the same names).  Rows are emitted only for bits that actually got a
// track (unplaced bits are counted in num_unplaced, not materialized), so
// `placed` is true on every emitted row.
struct NetSegment : PlacedSegmentBase {
    NetSegment() : PlacedSegmentBase(SegKind::NET) { placed = true; }
    int    bundle_id      = 0;
    int    seg_idx        = 0;
    int    bit_index      = 0;
    // NDR shield wire (phase 1): a first-class placed wire carrying the
    // rule's shield net, NOT a signal bit — bit_index is a NEGATIVE
    // per-segment shield ordinal (-1, -2, …) so it can never pair with a
    // signal bit in the via/span-follow machinery, and every net-identity
    // consumer (persistence, viz labels, WL reporting) must gate on this
    // flag before indexing net_names.
    bool   is_shield      = false;
};

// One per-bit layer transition between two connected segments' bit-wires.
// Fans out from the bundle-level symbolic bus-via: same key
// (bundle_id, from_seg, to_seg), one NetVia per bit_index. from_seg < to_seg
// always (the pair is deduped on (min, max)).
struct NetVia {
    int    bundle_id  = 0;
    int    from_seg   = 0;    // min of the connected seg pair
    // Max of the connected seg pair — EXCEPT on an NDR shield BOND strap
    // (emit_shield_bond_vias), where the far end is a power-grid RAIL, not
    // a routed segment, and one shield needs one row per crossing.  There
    // to_seg is the NEGATIVE strap ordinal -(k+1): it cannot collide with a
    // real segment index (>= 0) and it keeps the
    // (bundle_id, from_seg, to_seg, bit_index) primary key unique across a
    // shield's straps.  Consumers that read vias geometrically (GDS export,
    // the viz) are unaffected — they use from_layer/to_layer/x/y only.
    int    to_seg     = 0;
    int    bit_index  = 0;    // LOGICAL bit (bit_order already applied)
    int    from_layer = 0;    // layer of from_seg's bit-wire
    int    to_layer   = 0;    // layer of to_seg's bit-wire
    double x = 0.0, y = 0.0;  // per-bit crossing (µm)
};

struct DetailedNUTSResult {
    std::vector<NetSegment> net_segments;
    std::vector<NetVia>     net_vias;
    int num_unplaced = 0;
    // Bits removed by the post-placement keepout cull: their FINAL
    // (junction-adjusted) span crossed a keepout on their layer — a physical
    // violation the single-point track sampling used to place silently
    // (keepout-model audit).  Each is also counted in num_unplaced, so the
    // healing machinery (negotiate/ripup stage b) sees them as opens.
    int num_keepout_bits = 0;
    // How many of net_vias are NDR shield BOND straps (R6, opt-in `bond`).
    // Reported by run_detailed_nuts and asserted by the audit; 0 whenever no
    // rule opted in, which is every pre-bonding flow.
    int n_shield_bond_vias = 0;
    // Total per-bit trunk-jog wirelength between PAIR-ALIGN PARTNER segments
    // — same bundle, same bit count, overlapping intervals, one layer,
    // anchored and not timing-critical: exactly the lever-A pair predicate of
    // place_by_layer — measured on the FINAL placed tracks.  Bit i of a
    // straddling pair runs stubA @ tA_i, a trunk jog |tA_i - tB_i|, stubB @
    // tB_i; alignment collapses the jog to zero, so this sum is the
    // wirelength the pair-align re-solve TARGETS (an upper bound on its
    // aimed-for gain, not on incidental side effects).  0.0 = nothing to
    // align: the measured-accept heal skips its re-solve entirely instead of
    // paying a full solve to be rejected (the opens.md #8b WL-gain
    // predictor).  Cost: one pass over the placed bits — noise vs the solve.
    double pair_misalign_wl = 0.0;
    // Per-pass seconds of the run() that produced this result (the RR
    // round-3 profiling layer).  Keys: place / bit_spans / keepout_cull /
    // vias.  Pure observation: never read by any placement decision.
    std::map<std::string, double> pass_seconds;
    // True when placement stopped at an abort threshold (RR fast trials):
    // num_unplaced already exceeded the current metric mid-place, so the
    // trial is a CERTAIN rejection (unplaced only grows through place and
    // cull) — the remaining layers were skipped and net_segments is
    // PARTIAL.  Never set outside an abort-armed run.
    bool aborted = false;
};

// ── R6 shield bonding (opt-in per rule, `bond`) ──────────────────────────
// Strap every EMITTED shield in `result` to the power grid: a via wherever
// the shield crosses an identity-matching rail (ndr_shield_net_matches) on
// a PERPENDICULAR ADJACENT layer that has a grid.  Straps land in
// result.net_vias under the convention documented on NetVia::to_seg, so
// GDS export and the viz carry them with no new plumbing.
//
// IDEMPOTENT and FREE-STANDING by design.  It reads only PLACED shield
// geometry against a real grid, so it is re-runnable: existing straps are
// dropped first and recomputed.  That is what lets the bottom-up path
// re-derive a COPIED instance's straps against ITS OWN coordinates instead
// of transforming the reference's — a sibling's adjacent layer can carry a
// different pattern override or keepout, which check_template_tracks does
// not compare (it compares track pools on the ROUTED layer only), so a
// transformed strap could otherwise sit over no rail while the NDR_BOND
// audit read the row as proof of bonding.
//
// No `bond` rule among `bus_segs` = no work and no mutation.
void emit_shield_bond_vias(const RoutingGridStack& stack,
                           const std::vector<BusSegment>& bus_segs,
                           DetailedNUTSResult& result);

class DetailedNUTSEngine {
public:
    explicit DetailedNUTSEngine(const RoutingGridStack& stack);
    // Bottom-up template planning (stage c): pre-reserve the tracks used by
    // already-solved bit-wires (the reference-instance solve and its
    // per-instance copies) so this engine's run places every other bundle's
    // bits off them.  Reservations keep the same-bundle sharing exemption (a
    // fixed bit never blocks its own bundle).  Must be called before run().
    void add_fixed_bits(const std::vector<NetSegment>& bits);
    // emit_vias=false (RR fast trials): skip the per-bit via emission — pure
    // OUTPUT, never read by the stage-b metric (num_unplaced is computed by
    // place + cull), so the trial metric is IDENTICAL; a commit must re-run
    // with vias on (the session enforces this).
    //
    // abort_unplaced >= 0 (RR fast trials, round 3): SOUND early abort.
    // num_unplaced is non-decreasing through place and cull, so the moment
    // the running count exceeds the threshold (the committed metric's opens)
    // the trial is a certain rejection whatever the remaining layers do —
    // placement stops, result.aborted is set, and the post-place passes are
    // skipped (the partial result is only ever read for its metric and then
    // restored away; commits re-run full with the abort disarmed).
    DetailedNUTSResult run(const std::vector<BusSegment>& bus_segments,
                           bool emit_vias = true,
                           int abort_unplaced = -1) const;

    // Pairwise-overlap seating (prototype; see place_by_layer's header for
    // the mechanism and its measured net-negative unconditional result).
    // Toggled per-run so the measured-accept alignment heal can solve with it
    // ON, keep the result only when it does not increase unplaced/overlaps,
    // and restore otherwise.  Defaults to the BUDA_DNUTS_PAIR_ALIGN env (the
    // raw study path); set_pair_align overrides it on this engine.
    void set_pair_align(bool on) { pair_align_ = on; }

private:
    const RoutingGridStack& stack_;
    std::vector<NetSegment> fixed_bits_;
    bool pair_align_ = false;   // constructor seeds it from the env

    // The three stages of run(), in order (each mutates `result` in place):
    // per-layer bit placement in abstract_pos order (Option B), the per-bit
    // span-follow to connected bits' exact tracks (+ BUSTERM face re-extend),
    // and the per-bit via fan-out of the bundle-level symbolic bus-vias.
    void place_by_layer(const std::vector<BusSegment>& bus_segs,
                        DetailedNUTSResult& result,
                        int abort_unplaced = -1) const;
    void adjust_bit_spans(const std::vector<BusSegment>& bus_segs,
                          DetailedNUTSResult& result) const;
    // Post-placement keepout cull (keepout-model audit): remove every bit
    // whose FINAL adjusted span crosses a keepout on its layer, counting it
    // unplaced.  Runs on final spans, so it has zero false positives — the
    // exact complement of the placement-time preferred-pool heuristic.
    void cull_keepout_crossers(DetailedNUTSResult& result) const;
    void emit_bit_vias(const std::vector<BusSegment>& bus_segs,
                       DetailedNUTSResult& result) const;

    // Returns index into signal_tracks of the first contiguous window of size
    // bit_width, searching from lo to hi end.  Returns -1 if none found.
    static bool signals_contiguous(
        double pos_a, double pos_b,
        const std::vector<std::pair<double, TrackSlot>>& all_tracks);
};

// Build the stage-9 input from stage 4's output — the former Python handoff
// (buda_session/nutsflow.py::_run_detailed_nuts built BusSegments field by
// field and re-derived connectivity via a fresh ConnTopology), single-sourced
// here so the SEG-connection / lo_end / BUSTERM-face derivation exists once:
// every TrackSegment becomes a BusSegment (track_position -> abstract_pos,
// corner bounds carried), bit_width from the bundle's net count, and the
// per-segment connections/faces from the selected topology's cached analysis
// (ConnTopology::build) — exactly the values the abstract solve placed with.
// R1: `layers` (optional) supplies the per-signal-slot pitch each segment's
// ASSIGNED layer charges, so an ABSOLUTE NDR rule is resolved ONCE here and
// every stage-9 consumer — the admission helpers below, the run layout, the
// shield emission — reads an already-per-layer spec.  Without it the spec's
// stored quantization (the conservative maximum over the rule's layers) is
// carried through unchanged: over-charging, never under.
std::vector<BusSegment> make_bus_segments(
    const std::vector<BundleWrapper>& bundles,
    const NUTSResult& nuts_result,
    const Floorplan& floorplan,
    const std::string& bit_order = "LO_HI",
    const LayerStack* layers = nullptr);

// Bottom-up per-instance copy helpers (stage c) — the NetSegment/NetVia
// analogues of offset_track_segment: translate a solved reference-instance
// bit-wire / via by (dx, dy) and re-key it to a sibling instance's bundle.
// `horiz` is the wire direction (from the abstract segment): a horizontal
// wire's span shifts by dx and its track position by dy.
NetSegment offset_net_segment(const NetSegment& ns, int dx, int dy,
                              int new_bundle_id, bool horiz);
NetVia offset_net_via(const NetVia& v, int dx, int dy, int new_bundle_id);

// Orientation-aware siblings: map a solved bit-wire / via from a source
// frame (cell box cell_w×cell_h at (src_x, src_y)) through `orient` into a
// destination frame at (dst_x, dst_y).  Direction-preserving orientations
// only (N/S/FN/FS) — 90/270 throw, exactly like transform_track_segment.
NetSegment transform_net_segment(const NetSegment& ns,
                                 const std::string& orient,
                                 int cell_w, int cell_h,
                                 int src_x, int src_y, int dst_x, int dst_y,
                                 int new_bundle_id, bool horiz);
NetVia transform_net_via(const NetVia& v, const std::string& orient,
                         int cell_w, int cell_h,
                         int src_x, int src_y, int dst_x, int dst_y,
                         int new_bundle_id);

} // namespace buda
