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
};

// One per-bit layer transition between two connected segments' bit-wires.
// Fans out from the bundle-level symbolic bus-via: same key
// (bundle_id, from_seg, to_seg), one NetVia per bit_index. from_seg < to_seg
// always (the pair is deduped on (min, max)).
struct NetVia {
    int    bundle_id  = 0;
    int    from_seg   = 0;    // min of the connected seg pair
    int    to_seg     = 0;    // max of the connected seg pair
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

private:
    const RoutingGridStack& stack_;
    std::vector<NetSegment> fixed_bits_;

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
std::vector<BusSegment> make_bus_segments(
    const std::vector<BundleWrapper>& bundles,
    const NUTSResult& nuts_result,
    const Floorplan& floorplan,
    const std::string& bit_order = "LO_HI");

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
