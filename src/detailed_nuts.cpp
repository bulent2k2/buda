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
#include "conn_topology.h"
#include "nuts_geom.h"
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

void DetailedNUTSEngine::add_fixed_bits(const std::vector<NetSegment>& bits) {
    fixed_bits_.insert(fixed_bits_.end(), bits.begin(), bits.end());
}

NetSegment offset_net_segment(const NetSegment& ns, int dx, int dy,
                              int new_bundle_id, bool horiz) {
    NetSegment out = ns;
    out.bundle_id = new_bundle_id;
    const double along = horiz ? dx : dy;
    const double perp  = horiz ? dy : dx;
    out.span_lo        += along;
    out.span_hi        += along;
    out.track_position += perp;
    return out;
}

NetVia offset_net_via(const NetVia& v, int dx, int dy, int new_bundle_id) {
    NetVia out = v;
    out.bundle_id = new_bundle_id;
    out.x += dx;
    out.y += dy;
    return out;
}

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
    place_by_layer(bus_segs, result);
    adjust_bit_spans(bus_segs, result);
    // After spans are final (and before vias pair up bits): remove any bit
    // whose adjusted span crosses a keepout on its layer — an illegal wire
    // the placement-time sampling could not rule out.  Counted as unplaced,
    // so the opens feed the stage-b healing machinery.
    cull_keepout_crossers(result);
    emit_bit_vias(bus_segs, result);
    return result;
}

void DetailedNUTSEngine::cull_keepout_crossers(DetailedNUTSResult& result) const {
    std::vector<NetSegment> kept;
    kept.reserve(result.net_segments.size());
    int culled = 0;
    for (auto& ns : result.net_segments) {
        bool crossing = false;
        if (stack_.has_layer(ns.layer)) {
            const RoutingGrid& grid = stack_.get_layer_grid(ns.layer);
            const bool horiz = grid.is_horizontal();
            const double a_lo = std::min(ns.span_lo, ns.span_hi);
            const double a_hi = std::max(ns.span_lo, ns.span_hi);
            for (const Rect& k : grid.keepouts()) {
                const double k_p1 = horiz ? k.y1 : k.x1;
                const double k_p2 = horiz ? k.y2 : k.x2;
                const double k_a1 = horiz ? k.x1 : k.y1;
                const double k_a2 = horiz ? k.x2 : k.y2;
                if (ns.track_position >= k_p1 && ns.track_position <= k_p2 &&
                    a_lo < k_a2 && a_hi > k_a1) {
                    crossing = true;
                    break;
                }
            }
        }
        if (crossing) ++culled;
        else          kept.push_back(std::move(ns));
    }
    if (culled > 0) {
        result.net_segments.swap(kept);
        result.num_unplaced    += culled;
        result.num_keepout_bits = culled;
        std::cout << "[DetailedNUTS] WARNING: " << culled << " bit(s) removed — "
                  << "final span crosses a keepout on its layer (counted "
                  << "unplaced; an illegal wire is never kept silently).\n";
    }
}

void DetailedNUTSEngine::place_by_layer(
        const std::vector<BusSegment>& bus_segs,
        DetailedNUTSResult& result) const {
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

        // Pre-reserve the fixed bit-wires (bottom-up copies) on this layer:
        // one assignment per (bundle, seg) group, so every segment placed by
        // this run treats their tracks exactly like an earlier competing
        // assignment (same span/interval overlap test, same same-bundle
        // sharing exemption).
        {
            std::map<std::pair<int,int>, LayerAssignment> groups;
            for (const auto& nb : fixed_bits_) {
                if (nb.layer != layer) continue;
                auto [it, fresh] = groups.try_emplace({nb.bundle_id, nb.seg_idx});
                LayerAssignment& g = it->second;
                const double lo = std::min(nb.span_lo, nb.span_hi);
                const double hi = std::max(nb.span_lo, nb.span_hi);
                if (fresh) {
                    g.bundle_id   = nb.bundle_id;
                    g.span_lo     = lo;
                    g.span_hi     = hi;
                    g.interval_lo = nb.track_position - nb.width;
                    g.interval_hi = nb.track_position + nb.width;
                } else {
                    g.span_lo     = std::min(g.span_lo, lo);
                    g.span_hi     = std::max(g.span_hi, hi);
                    g.interval_lo = std::min(g.interval_lo,
                                             nb.track_position - nb.width);
                    g.interval_hi = std::max(g.interval_hi,
                                             nb.track_position + nb.width);
                }
                g.track_positions.push_back(nb.track_position);
            }
            for (auto& [k, g] : groups) layer_assigns.push_back(std::move(g));
        }

        // --------------------------------------------------------------- //
        // 2. Process each segment in abstract_pos order.                  //
        // --------------------------------------------------------------- //
        for (int idx : indices) {
            const BusSegment& bs = bus_segs[idx];
            double x = (bs.span_lo + bs.span_hi) / 2.0;

            // Track pools (keepout-model audit): PREFER tracks clear of every
            // keepout across the wire's whole abstract span — the old
            // single-sample query (signal_tracks_in at the span midpoint) let
            // a keepout that misses the midpoint go undetected and routed
            // bits straight through it.  The abstract span is a conservative
            // overestimate of the final junction-adjusted bit spans, so when
            // the span-clear pool is too small we fall back to the classic
            // midpoint pool rather than forfeit honest placements to false
            // positives — cull_keepout_crossers (post-adjustment) removes any
            // bit whose FINAL span still crosses a keepout.
            auto signal_tracks = grid.signal_tracks_in_span(
                bs.span_lo, bs.span_hi, bs.interval_lo, bs.interval_hi);
            if ((int)signal_tracks.size() < bs.bit_width)
                signal_tracks = grid.signal_tracks_in(x, bs.interval_lo,
                                                      bs.interval_hi);

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
}

// ---------------------------------------------------------------------- //
// 4. Span-adjustment post-pass: extend bit-wire endpoints to reach        //
//    all connected perpendicular segments' exact track_positions.         //
// ---------------------------------------------------------------------- //
void DetailedNUTSEngine::adjust_bit_spans(
        const std::vector<BusSegment>& bus_segs,
        DetailedNUTSResult& result) const {
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
            // abstract span.  span_lo/span_hi keep nominal endpoint identity
            // (set from lo_end/hi_end connections) and may end up span_lo >
            // span_hi when placement swaps the two ends; the dnuts connectivity
            // check takes min/max locally rather than reordering them.
            if (has_ep_lo) ns.span_lo = ep_lo;
            if (has_ep_hi) ns.span_hi = ep_hi;

            // Extend the span to cover mid-span (interior) connections.  Ordered
            // so a reversed span (span_lo > span_hi) keeps both endpoints: a raw
            // `cover_hi > span_hi` test would overwrite the nominal lo endpoint
            // (e.g. ends 80/20 + a tap at 90 -> [80,90], dropping the 20 end).
            // Mirrors the abstract-NUTS coverage pass in nuts.cpp.
            auto cover = [&](double c) {
                span_cover(ns.span_lo, ns.span_hi, c);
            };
            if (cover_lo !=  std::numeric_limits<double>::infinity()) cover(cover_lo);
            if (cover_hi != -std::numeric_limits<double>::infinity()) cover(cover_hi);

            // BUSTERM face coverage: the endpoint snap above (ns.span_* = ep_*)
            // can pull a face end onto a connected stub's per-bit track, dropping
            // the block-face tap the abstract span reached.  Re-extend each bit's
            // span to its block face (extend-only) so the tap always lands on the
            // block (big2 bus_077 / blk_12).
            for (double fc : bs_ptr->busterm_faces) cover(fc);
        }
    }
}

// ------------------------------------------------------------------ //
// 5. Per-bit via emission: one NetVia wherever bit i of a segment     //
//    meets bit i of a connected segment on a DIFFERENT layer.         //
//    Fans the symbolic bundle-level bus-via out to bit_width per-     //
//    bit vias (same (bundle, from_seg, to_seg) key + bit_index).      //
//    Runs after the span adjustment, which never moves                //
//    track_position — so the crossings below are final geometry.      //
// ------------------------------------------------------------------ //
void DetailedNUTSEngine::emit_bit_vias(
        const std::vector<BusSegment>& bus_segs,
        DetailedNUTSResult& result) const {
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

        std::set<std::tuple<int,int,int,int>> emitted; // (bid, lo_seg, hi_seg, bit)
        for (const auto& ns : result.net_segments) {
            auto bsit = bs_map.find({ns.bundle_id, ns.seg_idx});
            if (bsit == bs_map.end() || !bsit->second) continue;
            for (const auto& conn : bsit->second->connections) {
                auto it = idx_map.find({ns.bundle_id, conn.seg_idx, ns.bit_index});
                if (it == idx_map.end()) continue;      // other bit unplaced
                const NetSegment& other = result.net_segments[it->second];
                if (other.layer == ns.layer) continue;  // same layer -> no via
                const int lo_seg = std::min(ns.seg_idx, conn.seg_idx);
                const int hi_seg = std::max(ns.seg_idx, conn.seg_idx);
                if (!emitted.insert({ns.bundle_id, lo_seg, hi_seg,
                                     ns.bit_index}).second)
                    continue;                           // conns are symmetric
                if (!stack_.has_layer(ns.layer) || !stack_.has_layer(other.layer))
                    continue;
                const bool h_this  = stack_.get_layer_grid(ns.layer).is_horizontal();
                const bool h_other = stack_.get_layer_grid(other.layer).is_horizontal();
                double x, y;
                if (h_this != h_other) {                // H<->V bend or T-junction
                    const NetSegment& hs = h_this ? ns : other;
                    const NetSegment& vs = h_this ? other : ns;
                    x = vs.track_position;
                    y = hs.track_position;
                } else {
                    // Stacked same-orientation cross-layer pair: along-axis from
                    // the bundle-level junction (conn.at_pos), perpendicular from
                    // the lower-seg-index bit's track — the same approximation the
                    // bundle-level bus-via records (a buildable via needs a jog).
                    const NetSegment& lo = (ns.seg_idx == lo_seg) ? ns : other;
                    if (h_this) { x = conn.at_pos;         y = lo.track_position; }
                    else        { x = lo.track_position;   y = conn.at_pos; }
                }
                NetVia v;
                v.bundle_id  = ns.bundle_id;
                v.from_seg   = lo_seg;
                v.to_seg     = hi_seg;
                v.bit_index  = ns.bit_index;
                v.from_layer = (ns.seg_idx == lo_seg) ? ns.layer : other.layer;
                v.to_layer   = (ns.seg_idx == lo_seg) ? other.layer : ns.layer;
                v.x = x;
                v.y = y;
                result.net_vias.push_back(v);
            }
        }
    }
}

std::vector<BusSegment> make_bus_segments(
    const std::vector<BundleWrapper>& bundles,
    const NUTSResult& nuts_result,
    const Floorplan& floorplan,
    const std::string& bit_order)
{
    // Per-bundle bit width (net count) and per-segment connectivity from the
    // SELECTED topology's cached analysis — the same derivation the abstract
    // solve's build_nuts_maps used, so stage 9 sees the junctions stage 4
    // placed with (this loop is the former Python handoff, verbatim).
    std::map<int, int>                  bid_to_nbits;
    std::map<int, std::vector<ConnSeg>> bid_to_cs;
    for (const auto& w : bundles) {
        const int bid = w.input.original_bundle.id;
        bid_to_nbits[bid] =
            (int)w.input.original_bundle.get_net_names().size();
        const int sel = w.plan.selected_topology_index;
        if (w.input.candidates.empty() || sel < 0 ||
            sel >= (int)w.input.candidates.size()) {
            bid_to_cs[bid] = {};
            continue;
        }
        ConnTopology ct;
        ct.build(w.input.candidates[sel], floorplan);
        bid_to_cs[bid] = ct.segs();
    }

    std::vector<BusSegment> out;
    out.reserve(nuts_result.segments.size());
    for (const auto& ts : nuts_result.segments) {
        BusSegment bs;
        bs.bundle_id   = ts.bundle_id;
        bs.seg_idx     = ts.seg_idx;
        bs.layer       = ts.layer;
        bs.span_lo     = ts.span_lo;
        bs.span_hi     = ts.span_hi;
        bs.interval_lo = ts.interval_lo;
        bs.interval_hi = ts.interval_hi;
        auto nb = bid_to_nbits.find(ts.bundle_id);
        bs.bit_width   = (nb != bid_to_nbits.end()) ? nb->second : 1;
        bs.bit_order   = bit_order;
        bs.abstract_pos = ts.track_position;
        // Cross-layer corner split bounds (carried into detailed NUTS so the
        // trunk's bits snap to its committed side on real signal tracks).
        bs.track_lo_bound = ts.track_lo_bound;
        bs.track_hi_bound = ts.track_hi_bound;

        auto csit = bid_to_cs.find(ts.bundle_id);
        if (csit != bid_to_cs.end() &&
            ts.seg_idx < (int)csit->second.size()) {
            const ConnSeg& cs = csit->second[ts.seg_idx];
            for (const auto& conn : cs.conns) {
                if (conn.kind == SegConn::SEG) {
                    BusSegmentConn c;
                    c.seg_idx     = conn.seg_idx;
                    c.at_pos      = (double)conn.at_pos;
                    c.is_endpoint = conn.is_endpoint;
                    const double mid = 0.5 * (cs.along_lo + cs.along_hi);
                    c.lo_end      = (c.at_pos <= mid);
                    bs.connections.push_back(c);
                } else {  // BUSTERM: keep the block-face tap reachable per-bit
                    bs.busterm_faces.push_back((double)conn.face_coord);
                }
            }
        }
        out.push_back(std::move(bs));
    }
    return out;
}

} // namespace buda
