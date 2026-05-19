#include "detailed_nuts.h"
#include <algorithm>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <tuple>

namespace interconnect {

DetailedNUTSEngine::DetailedNUTSEngine(const RoutingGridStack& stack)
    : stack_(stack) {}

bool DetailedNUTSEngine::signals_contiguous(
        double pos_a, double pos_b,
        const std::vector<std::pair<double, TrackSlot>>& all_tracks) {
    for (const auto& t : all_tracks)
        if (t.second.type != "SIGNAL" && t.first > pos_a && t.first < pos_b)
            return false;
    return true;
}

// ---------------------------------------------------------------------------
// Option B: ordered-anchor track assignment.
//
// Within each layer, bus segments are sorted by abstract_pos (the track
// position assigned by abstract NUTS).  For each segment in that order, we
// find the contiguous N-track window from the available (unreserved) signal
// tracks whose centre is closest to abstract_pos.  Segments that previously
// claimed tracks in the same span+interval block those tracks from reuse,
// preserving the topological ordering set by abstract NUTS.
//
// When abstract_pos < 0 (sentinel "unset"), the code falls back to the
// original behaviour: LO_HI picks the first valid window, HI_LO picks the
// last valid window.
// ---------------------------------------------------------------------------

DetailedNUTSResult DetailedNUTSEngine::run(
        const std::vector<BusSegment>& bus_segs) const {
    DetailedNUTSResult result;

    // ------------------------------------------------------------------ //
    // 1. Group segment indices by layer; sort each layer by abstract_pos. //
    // ------------------------------------------------------------------ //
    std::map<int, std::vector<int>> by_layer;
    for (int i = 0; i < (int)bus_segs.size(); ++i)
        by_layer[bus_segs[i].layer].push_back(i);

    for (auto& [layer, indices] : by_layer) {
        if (!stack_.has_layer(layer)) {
            for (int idx : indices) result.num_unplaced += bus_segs[idx].bit_width;
            continue;
        }

        // Sort by abstract_pos; unset (-1) sorts before 0, which places
        // them at the lo end — consistent with their fallback behaviour.
        std::sort(indices.begin(), indices.end(), [&](int a, int b) {
            return bus_segs[a].abstract_pos < bus_segs[b].abstract_pos;
        });

        const RoutingGrid& grid = stack_.get_layer_grid(layer);

        // Assignments already made on this layer; used to reserve tracks.
        struct LayerAssignment {
            double span_lo, span_hi, interval_lo, interval_hi;
            std::vector<double> track_positions;
        };
        std::vector<LayerAssignment> layer_assigns;

        // --------------------------------------------------------------- //
        // 2. Process each segment in abstract_pos order.                  //
        // --------------------------------------------------------------- //
        for (int idx : indices) {
            const BusSegment& bs = bus_segs[idx];
            double x = (bs.span_lo + bs.span_hi) / 2.0;

            auto signal_tracks = grid.signal_tracks_in(x, bs.interval_lo, bs.interval_hi);
            int n_sig = (int)signal_tracks.size();

            if (n_sig < bs.bit_width) {
                result.num_unplaced += bs.bit_width;
                continue;
            }

            // Collect track positions already reserved by competing segments.
            std::set<double> reserved;
            for (const auto& asgn : layer_assigns) {
                bool span_ov = asgn.span_lo < bs.span_hi && asgn.span_hi > bs.span_lo;
                bool itvl_ov = asgn.interval_lo < bs.interval_hi &&
                               asgn.interval_hi > bs.interval_lo;
                if (span_ov && itvl_ov)
                    for (double p : asgn.track_positions) reserved.insert(p);
            }

            // Cache timing-critical data once per segment.
            std::vector<std::pair<double, TrackSlot>> all_tracks_tc;
            if (bs.timing_critical) {
                const TrackPattern& pat =
                    grid.effective_pattern_at(x, bs.interval_lo);
                all_tracks_tc = pat.tracks_in_range(bs.interval_lo, bs.interval_hi);
            }

            // ------------------------------------------------------- //
            // 3. Find best window of bit_width consecutive signal       //
            //    tracks that are all unreserved.                        //
            //    "Best" when abstract_pos >= 0: window whose centre     //
            //    is closest to abstract_pos.                            //
            //    Fallback (abstract_pos < 0): first valid (LO_HI) or   //
            //    last valid (HI_LO) window.                             //
            // ------------------------------------------------------- //
            const bool use_anchor = (bs.abstract_pos >= 0.0);
            int best_start = -1;
            double best_dist  = std::numeric_limits<double>::max();

            const int bw = bs.bit_width;
            for (int j = 0; j + bw <= n_sig; ++j) {
                // All tracks in window must be available.
                bool avail = true;
                for (int k = j; k < j + bw; ++k) {
                    if (reserved.count(signal_tracks[k].first)) { avail = false; break; }
                }
                if (!avail) continue;

                // Timing-critical: no non-signal track between any adjacent pair.
                if (bs.timing_critical) {
                    for (int k = j; k < j + bw - 1; ++k) {
                        if (!signals_contiguous(signal_tracks[k].first,
                                                signal_tracks[k + 1].first,
                                                all_tracks_tc)) {
                            avail = false; break;
                        }
                    }
                    if (!avail) continue;
                }

                if (use_anchor) {
                    // Score by distance of window centre from abstract_pos.
                    int mid_k = j + (bw - 1) / 2;
                    double centre = signal_tracks[mid_k].first;
                    double dist   = std::abs(centre - bs.abstract_pos);
                    if (dist < best_dist) { best_dist = dist; best_start = j; }
                } else {
                    // Fallback: first valid for LO_HI, last valid for HI_LO.
                    if (bs.bit_order == "LO_HI") {
                        best_start = j;
                        break;  // first wins
                    } else {
                        best_start = j;  // keep updating → last wins
                    }
                }
            }

            if (best_start < 0) {
                result.num_unplaced += bs.bit_width;
                continue;
            }

            // Assign direction within the window.
            int start_idx = best_start;
            int direction = +1;
            if (bs.bit_order == "HI_LO") {
                start_idx = best_start + bw - 1;
                direction = -1;
            }

            std::vector<double> assigned;
            for (int bit = 0; bit < bw; ++bit) {
                int ti = start_idx + direction * bit;
                NetSegment ns;
                ns.bundle_id      = bs.bundle_id;
                ns.seg_idx        = bs.seg_idx;
                ns.bit_index      = bit;
                ns.track_position = signal_tracks[ti].first;
                ns.width          = signal_tracks[ti].second.width;
                ns.layer          = bs.layer;
                ns.span_lo        = bs.span_lo;
                ns.span_hi        = bs.span_hi;
                result.net_segments.push_back(ns);
                assigned.push_back(signal_tracks[ti].first);
            }
            layer_assigns.push_back({bs.span_lo, bs.span_hi,
                                     bs.interval_lo, bs.interval_hi,
                                     std::move(assigned)});
        }
    }

    // ------------------------------------------------------------------ //
    // 4. Span-adjustment post-pass: extend bit-wire endpoints that        //
    //    connect to a perpendicular segment to its exact track_position.  //
    // ------------------------------------------------------------------ //
    {
        using Key = std::tuple<int,int,int>; // bundle_id, seg_idx, bit_index
        std::map<Key, int>              idx_map;
        std::map<std::pair<int,int>, const BusSegment*> bs_map;

        for (int i = 0; i < (int)result.net_segments.size(); ++i) {
            const auto& ns = result.net_segments[i];
            idx_map[{ns.bundle_id, ns.seg_idx, ns.bit_index}] = i;
        }
        for (const auto& bs : bus_segs)
            bs_map[{bs.bundle_id, bs.seg_idx}] = &bs;

        for (auto& ns : result.net_segments) {
            const auto* bs_ptr = bs_map[{ns.bundle_id, ns.seg_idx}];
            if (!bs_ptr) continue;

            if (bs_ptr->lo_adj_seg_idx >= 0) {
                auto it = idx_map.find({ns.bundle_id, bs_ptr->lo_adj_seg_idx, ns.bit_index});
                if (it != idx_map.end())
                    ns.span_lo = result.net_segments[it->second].track_position;
            }
            if (bs_ptr->hi_adj_seg_idx >= 0) {
                auto it = idx_map.find({ns.bundle_id, bs_ptr->hi_adj_seg_idx, ns.bit_index});
                if (it != idx_map.end())
                    ns.span_hi = result.net_segments[it->second].track_position;
            }
        }
    }

    return result;
}

} // namespace interconnect
