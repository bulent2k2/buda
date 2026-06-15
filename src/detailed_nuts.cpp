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

#include "detailed_nuts.h"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <tuple>

namespace buda {

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
// When abstract_pos is NaN (sentinel "unset"), the code falls back to the
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
            std::cout << "[DetailedNUTS] Warning: Layer " << layer
                      << " has no track pattern defined. Skipping " << indices.size()
                      << " segment(s)." << std::endl;
            for (int idx : indices) result.num_unplaced += bus_segs[idx].bit_width;
            continue;
        }

        // Sort by abstract_pos; NaN (unset) sorts last — those fall back
        // to LO_HI/HI_LO window search after anchored segments are placed.
        std::sort(indices.begin(), indices.end(), [&](int a, int b) {
            return bus_segs[a].abstract_pos < bus_segs[b].abstract_pos;
        });

        const RoutingGrid& grid = stack_.get_layer_grid(layer);

        // Assignments already made on this layer; used to reserve tracks.
        struct LayerAssignment {
            int bundle_id;
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

            // Cross-layer corner bound (carried from abstract NUTS): keep only
            // signal tracks on this trunk's committed side of the split, so it
            // cannot snap onto the other trunk's side and short the stubs that
            // hang off both.  Default bounds are ±inf (no-op).  If the bounded
            // side lacks bit_width tracks, the n_sig check below reports the bits
            // unplaced — an honest spacing failure rather than a silent short.
            if (bs.track_lo_bound > -std::numeric_limits<double>::infinity() ||
                bs.track_hi_bound <  std::numeric_limits<double>::infinity()) {
                std::vector<std::pair<double, TrackSlot>> kept;
                kept.reserve(signal_tracks.size());
                for (auto& t : signal_tracks)
                    if (t.first >= bs.track_lo_bound && t.first <= bs.track_hi_bound)
                        kept.push_back(t);
                signal_tracks.swap(kept);
            }
            int n_sig = (int)signal_tracks.size();

            if (n_sig < bs.bit_width) {
                std::cout << "[DetailedNUTS] Warning: Layer " << layer
                          << " has insufficient signal tracks (" << n_sig
                          << ") for bus width " << bs.bit_width
                          << " in interval [" << bs.interval_lo << ", " << bs.interval_hi << "]"
                          << std::endl;
                result.num_unplaced += bs.bit_width;
                continue;
            }

            // Collect track positions already reserved by competing segments.
            // Segments from the SAME bundle are allowed to share tracks.
            std::set<double> reserved;
            for (const auto& asgn : layer_assigns) {
                if (asgn.bundle_id == bs.bundle_id) continue;

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
            // 3. Choose bit_width signal tracks from the available set.//
            //                                                           //
            // Path A — anchor + non-timing-critical:                   //
            //   Pick the N available tracks whose positions are closest //
            //   to abstract_pos (no consecutiveness constraint).  This //
            //   avoids fragmentation when reserved tracks split the     //
            //   space.  Sort chosen tracks by position for assignment.  //
            //                                                           //
            // Path B — timing-critical or fallback (abstract_pos is NaN): //
            //   Scan windows of N consecutive signal-track indices.     //
            //   Timing-critical also requires physical contiguity       //
            //   (no non-signal track between adjacent pair).            //
            //   Fallback: first valid window (LO_HI) or last (HI_LO).  //
            // ------------------------------------------------------- //
            const bool use_anchor = !std::isnan(bs.abstract_pos);
            const int  bw = bs.bit_width;

            // chosen_indices: the bw signal-track indices to use,
            // already sorted by track position (ascending).
            std::vector<int> chosen_indices;

            if (use_anchor && !bs.timing_critical) {
                // Path A: N closest available tracks.
                std::vector<int> avail;
                avail.reserve(n_sig);
                for (int k = 0; k < n_sig; ++k)
                    if (!reserved.count(signal_tracks[k].first))
                        avail.push_back(k);

                if ((int)avail.size() < bw) {
                    std::cout << "[DetailedNUTS] Warning: Layer " << layer
                              << " has " << avail.size() << " unreserved tracks"
                              << " (need " << bw << ") in interval ["
                              << bs.interval_lo << ", " << bs.interval_hi
                              << "] — reservation conflict (bundle " << bs.bundle_id << ")"
                              << std::endl;
                    result.num_unplaced += bw;
                    continue;
                }

                // Sort available by distance from abstract_pos.
                std::sort(avail.begin(), avail.end(), [&](int a, int b) {
                    return std::abs(signal_tracks[a].first - bs.abstract_pos) <
                           std::abs(signal_tracks[b].first - bs.abstract_pos);
                });

                // Take the bw closest; sort them by track index (= by position).
                chosen_indices.assign(avail.begin(), avail.begin() + bw);
                std::sort(chosen_indices.begin(), chosen_indices.end());

            } else {
                // Path B: window-based (timing-critical or no anchor).
                int best_start = -1;
                double best_dist = std::numeric_limits<double>::max();

                for (int j = 0; j + bw <= n_sig; ++j) {
                    bool avail = true;
                    for (int k = j; k < j + bw; ++k) {
                        if (reserved.count(signal_tracks[k].first)) { avail = false; break; }
                    }
                    if (!avail) continue;

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
                        int mid_k = j + (bw - 1) / 2;
                        double dist = std::abs(signal_tracks[mid_k].first - bs.abstract_pos);
                        if (dist < best_dist) { best_dist = dist; best_start = j; }
                    } else {
                        if (bs.bit_order == "LO_HI") { best_start = j; break; }
                        else                         { best_start = j; }
                    }
                }

                if (best_start < 0) {
                    std::cout << "[DetailedNUTS] Warning: Layer " << layer
                              << " no valid window of " << bw << " tracks in interval ["
                              << bs.interval_lo << ", " << bs.interval_hi
                              << "] after reservation (bundle " << bs.bundle_id << ")"
                              << std::endl;
                    result.num_unplaced += bw;
                    continue;
                }

                for (int k = 0; k < bw; ++k)
                    chosen_indices.push_back(best_start + k);
                // chosen_indices already in ascending order.
            }

            // Emit NetSegments.
            // For LO_HI: bit_index=0 → chosen_indices[0] (lowest position).
            // For HI_LO: bit_index=0 → chosen_indices[bw-1] (highest position).
            std::vector<double> assigned;
            for (int bit = 0; bit < bw; ++bit) {
                int ci = (bs.bit_order == "HI_LO") ? (bw - 1 - bit) : bit;
                int ti = chosen_indices[ci];
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
            layer_assigns.push_back({bs.bundle_id, bs.span_lo, bs.span_hi,
                                     bs.interval_lo, bs.interval_hi,
                                     std::move(assigned)});
        }
    }

    // ------------------------------------------------------------------ //
    // 4. Span-adjustment post-pass: extend bit-wire endpoints to reach    //
    //    all connected perpendicular segments' exact track_positions.     //
    // ------------------------------------------------------------------ //
    {
        using Key = std::tuple<int,int,int>; // bundle_id, seg_idx, bit_index
        std::map<Key, int> idx_map;
        for (int i = 0; i < (int)result.net_segments.size(); ++i) {
            const auto& ns = result.net_segments[i];
            idx_map[{ns.bundle_id, ns.seg_idx, ns.bit_index}] = i;
        }

        std::map<std::pair<int,int>, const BusSegment*> bs_map;
        for (const auto& bs : bus_segs)
            bs_map[{bs.bundle_id, bs.seg_idx}] = &bs;

        for (auto& ns : result.net_segments) {
            const auto* bs_ptr = bs_map[{ns.bundle_id, ns.seg_idx}];
            if (!bs_ptr) continue;

            // Endpoint connections snap their end of the wire to the
            // connected bit's exact position — extending OR retracting:
            // each bit's stub lands at a different track, so the abstract
            // span end is wrong in both directions for most bits.
            // Mid-span connections never gate the ends; they only require
            // the span to keep covering the connected bit's position.
            bool   has_ep_lo = false, has_ep_hi = false;
            double ep_lo =  std::numeric_limits<double>::infinity();
            double ep_hi = -std::numeric_limits<double>::infinity();
            double cover_lo =  std::numeric_limits<double>::infinity();
            double cover_hi = -std::numeric_limits<double>::infinity();

            for (const auto& conn : bs_ptr->connections) {
                auto it = idx_map.find({ns.bundle_id, conn.seg_idx, ns.bit_index});
                if (it == idx_map.end()) continue;

                const auto& other_ns = result.net_segments[it->second];
                const double other_pos = other_ns.track_position;

                if (conn.is_endpoint) {
                    if (conn.lo_end) {
                        has_ep_lo = true;
                        ep_lo = std::min(ep_lo, other_pos);
                    } else {
                        has_ep_hi = true;
                        ep_hi = std::max(ep_hi, other_pos);
                    }
                } else {
                    cover_lo = std::min(cover_lo, other_pos);
                    cover_hi = std::max(cover_hi, other_pos);
                }
            }

            // Ends with no endpoint conn (e.g. a BUSTERM face) keep the
            // abstract span.
            if (has_ep_lo) ns.span_lo = ep_lo;
            if (has_ep_hi) ns.span_hi = ep_hi;
            if (cover_lo < ns.span_lo) ns.span_lo = cover_lo;
            if (cover_hi > ns.span_hi) ns.span_hi = cover_hi;
        }
    }

    return result;
}

} // namespace buda
