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
#include <chrono>
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

NetSegment transform_net_segment(const NetSegment& ns,
                                 const std::string& orient,
                                 int cell_w, int cell_h,
                                 int src_x, int src_y, int dst_x, int dst_y,
                                 int new_bundle_id, bool horiz) {
    const OrientMap m = orient_map(orient);
    if (m.swap)
        throw std::runtime_error(
            "transform_net_segment: 90/270-degree orientations are not "
            "supported for copies (H<->V layer swap needs layer pairing)");
    NetSegment out = ns;
    out.bundle_id = new_bundle_id;
    const bool along_refl = horiz ? m.rx : m.ry;
    const bool perp_refl  = horiz ? m.ry : m.rx;
    const int  along_dim  = horiz ? cell_w : cell_h;
    const int  perp_dim   = horiz ? cell_h : cell_w;
    const int  along_src  = horiz ? src_x : src_y;
    const int  perp_src   = horiz ? src_y : src_x;
    const int  along_dst  = horiz ? dst_x : dst_y;
    const int  perp_dst   = horiz ? dst_y : dst_x;
    auto along = [&](double v) {
        const double loc = v - along_src;
        return (along_refl ? along_dim - loc : loc) + along_dst;
    };
    const double lo = along(ns.span_lo), hi = along(ns.span_hi);
    out.span_lo = std::min(lo, hi);
    out.span_hi = std::max(lo, hi);
    const double p = ns.track_position - perp_src;
    out.track_position = (perp_refl ? perp_dim - p : p) + perp_dst;
    return out;
}

NetVia transform_net_via(const NetVia& v, const std::string& orient,
                         int cell_w, int cell_h,
                         int src_x, int src_y, int dst_x, int dst_y,
                         int new_bundle_id) {
    const OrientMap m = orient_map(orient);
    if (m.swap)
        throw std::runtime_error(
            "transform_net_via: 90/270-degree orientations are not "
            "supported for copies (H<->V layer swap needs layer pairing)");
    NetVia out = v;
    out.bundle_id = new_bundle_id;
    const double x = v.x - src_x, y = v.y - src_y;
    out.x = (m.rx ? cell_w - x : x) + dst_x;
    out.y = (m.ry ? cell_h - y : y) + dst_y;
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
        const std::vector<BusSegment>& bus_segs, bool emit_vias,
        int abort_unplaced) const {
    // Per-pass profiling (RR round-3 Phase 0) — observation only.
    using pclock = std::chrono::steady_clock;
    DetailedNUTSResult result;
    auto t0 = pclock::now();
    auto charge = [&result, &t0](const char* key) {
        auto t1 = pclock::now();
        result.pass_seconds[key] +=
            std::chrono::duration<double>(t1 - t0).count();
        t0 = t1;
    };
    place_by_layer(bus_segs, result, abort_unplaced);
    charge("place");
    if (result.aborted)
        return result;   // certain rejection: metric already decided (see .h)
    adjust_bit_spans(bus_segs, result);
    charge("bit_spans");
    // After spans are final (and before vias pair up bits): remove any bit
    // whose adjusted span crosses a keepout on its layer — an illegal wire
    // the placement-time sampling could not rule out.  Counted as unplaced,
    // so the opens feed the stage-b healing machinery.
    cull_keepout_crossers(result);
    charge("keepout_cull");
    // Fast-trial mode: vias are pure output (the metric is placed by
    // place + cull), so an RR trial may skip them — see the header.
    if (emit_vias)
        emit_bit_vias(bus_segs, result);
    charge("vias");
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
        DetailedNUTSResult& result, int abort_unplaced) const {
    // Sound early abort (RR fast trials): checked after every unplaced
    // increment via this helper — see run()'s header comment.
    auto over_budget = [&]() {
        if (abort_unplaced >= 0 && result.num_unplaced > abort_unplaced) {
            result.aborted = true;
            return true;
        }
        return false;
    };
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
            for (int idx : indices) result.num_unplaced += bus_seg_nbits(bus_segs[idx]);
            if (over_budget()) return;
            continue;
        }

        // Sort by abstract_pos; NaN (unset) sorts last — those fall back
        // to LO_HI/HI_LO window search after anchored segments are placed.
        // NaN-safe comparator (audit C11-04): a raw `<` with NaN present is
        // not a strict weak ordering (NaN compares false both ways, so NaN
        // segments could sort FIRST and steal anchored tracks — and std::sort
        // over a non-SWO range is UB).  Order non-NaN strictly before NaN.
        std::sort(indices.begin(), indices.end(), [&](int a, int b) {
            const double pa = bus_segs[a].abstract_pos, pb = bus_segs[b].abstract_pos;
            const bool na = std::isnan(pa), nb = std::isnan(pb);
            if (na != nb) return !na;          // a non-NaN sorts before a NaN
            return pa < pb;                    // both real (or both NaN → equal)
        });

        const RoutingGrid& grid = stack_.get_layer_grid(layer);

        // Assignments already made on this layer; used to reserve tracks.
        // track_positions is ascending and bits_on_track is parallel to it
        // (the GLOBAL bit index riding each track) so a later same-bundle
        // segment can check per-track bit identity — the same-bundle sharing
        // exemption is only sound when a shared track carries the SAME bit
        // (net) on both segments.
        struct LayerAssignment {
            int bundle_id;
            double span_lo, span_hi, interval_lo, interval_hi;
            std::vector<double> track_positions;
            std::vector<int>    bits_on_track;
        };
        std::vector<LayerAssignment> layer_assigns;

        // Pre-reserve the fixed bit-wires (bottom-up copies) on this layer:
        // one assignment per (bundle, seg) group, so every segment placed by
        // this run treats their tracks exactly like an earlier competing
        // assignment (same span/interval overlap test, same same-bundle
        // sharing exemption).
        {
            struct FixedGroup {
                LayerAssignment a;
                std::vector<std::pair<double,int>> track_bits; // (track, bit)
            };
            std::map<std::pair<int,int>, FixedGroup> groups;
            for (const auto& nb : fixed_bits_) {
                if (nb.layer != layer) continue;
                auto [it, fresh] = groups.try_emplace({nb.bundle_id, nb.seg_idx});
                LayerAssignment& g = it->second.a;
                const double lo = std::min(nb.span_lo, nb.span_hi);
                const double hi = std::max(nb.span_lo, nb.span_hi);
                if (fresh) {
                    g.bundle_id   = nb.bundle_id;
                    g.span_lo     = lo;
                    g.span_hi     = hi;
                    // ± full width (not width/2) is INTENTIONAL over-
                    // approximation: the interval only gates which groups
                    // contribute their exact track_positions to the reserved
                    // set, so widening it is conservative-only (a bit is
                    // never kept off a track that isn't really reserved) and
                    // guards against float-edge misses on abutting windows.
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
                it->second.track_bits.push_back({nb.track_position, nb.bit_index});
            }
            for (auto& [k, fg] : groups) {
                std::sort(fg.track_bits.begin(), fg.track_bits.end());
                for (auto& [t, b] : fg.track_bits) {
                    fg.a.track_positions.push_back(t);
                    fg.a.bits_on_track.push_back(b);
                }
                layer_assigns.push_back(std::move(fg.a));
            }
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
            if ((int)signal_tracks.size() < bus_seg_nbits(bs))
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

            if (n_sig < bus_seg_nbits(bs)) {
                std::cout << "[DetailedNUTS] Warning: Layer " << layer
                          << " has insufficient signal tracks (" << n_sig
                          << ") for bus width " << bus_seg_nbits(bs)
                          << " in interval [" << bs.interval_lo << ", " << bs.interval_hi << "]"
                          << std::endl;
                result.num_unplaced += bus_seg_nbits(bs);
                if (over_budget()) return;
                continue;
            }

            // Collect track positions already reserved by competing segments.
            // Segments from the SAME bundle may share tracks — but only
            // BIT-FOR-BIT: bit k of two segments is the same net (sharing a
            // track is a join), while bit k and bit j≠k are different nets
            // (sharing a track is a short — the BIT_SHORT class check_dnuts
            // audits).  A same-bundle assignment whose span interacts with
            // this segment's is therefore a HAZARD, handled below: either
            // this segment adopts its exact per-bit track list (alignment) or
            // treats its tracks as reserved (disjoint windows).  The span
            // test is closed and dilated by the sibling's track extent: the
            // bit-span adjustment staggers each bit's endpoint to its
            // junction partner's per-bit track, so wires reach up to about
            // one bus extent past the abstract span endpoint.
            std::set<double> reserved;
            std::vector<const LayerAssignment*> hazards;
            // Normalized extent of this segment's span: make_bus_segments
            // deliberately preserves reversed NUTS spans (span_lo > span_hi,
            // see the span-adjustment pass), and a raw-order comparison would
            // miss a real overlap and leave a bit short unrepaired.
            const double bs_lo = std::min(bs.span_lo, bs.span_hi);
            const double bs_hi = std::max(bs.span_lo, bs.span_hi);
            for (const auto& asgn : layer_assigns) {
                if (asgn.bundle_id == bs.bundle_id) {
                    const double dil = asgn.track_positions.empty() ? 0.0
                        : asgn.track_positions.back() - asgn.track_positions.front();
                    const double a_lo = std::min(asgn.span_lo, asgn.span_hi);
                    const double a_hi = std::max(asgn.span_lo, asgn.span_hi);
                    if (a_lo <= bs_hi + dil && a_hi + dil >= bs_lo)
                        hazards.push_back(&asgn);
                    continue;
                }

                // Normalized extents, like the same-bundle branch above: an
                // inverted span (span_lo > span_hi, the documented placement-
                // swap state) must not read as disjoint — a missed cross-
                // bundle reservation puts different nets on one track
                // (audit C11-03).
                const double o_lo = std::min(asgn.span_lo, asgn.span_hi);
                const double o_hi = std::max(asgn.span_lo, asgn.span_hi);
                bool span_ov = o_lo < bs_hi && o_hi > bs_lo;
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
            // Tapered fan-in: only the member bits need tracks; bit_index is
            // emitted as the GLOBAL index so cross-segment pairing (vias,
            // span-follow) and net_names[bit_index] stay consistent.
            const int  bw = bus_seg_nbits(bs);

            // The GLOBAL bit that rides ascending-track rank i of this
            // segment (mirrors the emission mapping below: rank -> local bit
            // via bit_order, local -> global via bit_list).
            auto bit_on_rank = [&](int i) {
                const int b = (bs.bit_order == "HI_LO") ? (bw - 1 - i) : i;
                return bs.bit_list.empty() ? b : bs.bit_list[b];
            };

            // choose(): today's Path A / Path B track selection against a
            // given reserved set.  Factored so the same-bundle hazard
            // resolution below can re-run it with the hazard tracks added.
            // Returns the bw chosen signal-track indices ascending, or empty
            // on failure (prints the corresponding warning).
            auto choose = [&](const std::set<double>& resv) -> std::vector<int> {
                std::vector<int> chosen;
                if (use_anchor && !bs.timing_critical) {
                    // Path A: N closest available tracks.
                    std::vector<int> avail;
                    avail.reserve(n_sig);
                    for (int k = 0; k < n_sig; ++k)
                        if (!resv.count(signal_tracks[k].first))
                            avail.push_back(k);

                    if ((int)avail.size() < bw) {
                        std::cout << "[DetailedNUTS] Warning: Layer " << layer
                                  << " has " << avail.size() << " unreserved tracks"
                                  << " (need " << bw << ") in interval ["
                                  << bs.interval_lo << ", " << bs.interval_hi
                                  << "] — reservation conflict (bundle " << bs.bundle_id << ")"
                                  << std::endl;
                        return chosen;
                    }

                    // Sort available by distance from abstract_pos.
                    std::sort(avail.begin(), avail.end(), [&](int a, int b) {
                        return std::abs(signal_tracks[a].first - bs.abstract_pos) <
                               std::abs(signal_tracks[b].first - bs.abstract_pos);
                    });

                    // Take the bw closest; sort them by track index (= by position).
                    chosen.assign(avail.begin(), avail.begin() + bw);
                    std::sort(chosen.begin(), chosen.end());
                    return chosen;
                }

                // Path B: window-based (timing-critical or no anchor).
                int best_start = -1;
                double best_dist = std::numeric_limits<double>::max();

                for (int j = 0; j + bw <= n_sig; ++j) {
                    bool avail = true;
                    for (int k = j; k < j + bw; ++k) {
                        if (resv.count(signal_tracks[k].first)) { avail = false; break; }
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
                    return chosen;
                }

                for (int k = 0; k < bw; ++k)
                    chosen.push_back(best_start + k);
                // chosen already in ascending order.
                return chosen;
            };

            // Natural selection first — hazards invisible, exactly the
            // historical behaviour (byte-identical whenever it is already
            // short-free against every same-bundle sibling).
            std::vector<int> chosen_indices = choose(reserved);
            if (chosen_indices.empty()) {
                result.num_unplaced += bw;
                if (over_budget()) return;
                continue;
            }

            // Same-bundle hazard resolution (see the hazards comment above):
            // intervene ONLY when the natural choice actually conflicts —
            // some chosen track already carries a DIFFERENT bit (a different
            // net) of this bundle on a span-interacting sibling.
            if (!hazards.empty()) {
                auto conflicted = [&](const std::vector<int>& picks) {
                    for (int i = 0; i < (int)picks.size(); ++i) {
                        const double t   = signal_tracks[picks[i]].first;
                        const int    bit = bit_on_rank(i);
                        for (const LayerAssignment* H : hazards) {
                            auto lb = std::lower_bound(H->track_positions.begin(),
                                                       H->track_positions.end(), t);
                            if (lb != H->track_positions.end() && *lb == t &&
                                H->bits_on_track[lb - H->track_positions.begin()] != bit)
                                return true;
                        }
                    }
                    return false;
                };
                if (conflicted(chosen_indices)) {
                    // First repair — ALIGN: adopt a hazard sibling's exact
                    // track list when the per-bit mapping matches (same
                    // count, same bit on every rank) and every track is
                    // usable here (in this segment's pool — interval, span
                    // availability, and corner bounds already applied — not
                    // competitor-reserved, and carrying no conflicting bit
                    // on any other hazard).  Bit k then lands on the SAME
                    // track on both segments: their wires join at the
                    // junction as one net — the exemption's sound case.
                    std::vector<int> repaired;
                    if (use_anchor && !bs.timing_critical) {
                        std::map<double, int> pos_to_idx;
                        for (int k = 0; k < n_sig; ++k)
                            pos_to_idx[signal_tracks[k].first] = k;
                        for (const LayerAssignment* A : hazards) {
                            if ((int)A->track_positions.size() != bw) continue;
                            bool ok = true;
                            std::vector<int> idxs;
                            idxs.reserve(bw);
                            for (int i = 0; i < bw; ++i) {
                                if (A->bits_on_track[i] != bit_on_rank(i)) { ok = false; break; }
                                auto pit = pos_to_idx.find(A->track_positions[i]);
                                if (pit == pos_to_idx.end() ||
                                    reserved.count(A->track_positions[i])) { ok = false; break; }
                                idxs.push_back(pit->second);
                            }
                            if (ok && conflicted(idxs)) ok = false;   // vs the OTHER hazards
                            if (ok) { repaired = std::move(idxs); break; }
                        }
                    }
                    // Second repair — DISJOINT: repick with every hazard
                    // track reserved so the windows cannot interleave.  If
                    // that leaves too few tracks the bits go honestly
                    // unplaced (feeding the stage-b healing machinery)
                    // rather than silently shorting — the same policy as
                    // the cross-layer corner bound.
                    if (repaired.empty()) {
                        std::set<double> resv2 = reserved;
                        for (const LayerAssignment* H : hazards)
                            for (double p : H->track_positions) resv2.insert(p);
                        repaired = choose(resv2);
                    }
                    if (repaired.empty()) {
                        result.num_unplaced += bw;
                        if (over_budget()) return;
                        continue;
                    }
                    chosen_indices = std::move(repaired);
                }
            }

            // Emit NetSegments.
            // For LO_HI: bit_index=0 → chosen_indices[0] (lowest position).
            // For HI_LO: bit_index=0 → chosen_indices[bw-1] (highest position).
            for (int bit = 0; bit < bw; ++bit) {
                int ci = (bs.bit_order == "HI_LO") ? (bw - 1 - bit) : bit;
                int ti = chosen_indices[ci];
                NetSegment ns;
                ns.bundle_id      = bs.bundle_id;
                ns.seg_idx        = bs.seg_idx;
                ns.bit_index      = bs.bit_list.empty() ? bit : bs.bit_list[bit];
                ns.track_position = signal_tracks[ti].first;
                ns.width          = signal_tracks[ti].second.width;
                ns.layer          = bs.layer;
                ns.span_lo        = bs.span_lo;
                ns.span_hi        = bs.span_hi;
                result.net_segments.push_back(ns);
            }
            // Record ascending-track order with the parallel per-track bit
            // (rank i carries bit_on_rank(i) — the emission mapping above).
            std::vector<double> assigned;
            std::vector<int>    assigned_bits;
            for (int i = 0; i < bw; ++i) {
                assigned.push_back(signal_tracks[chosen_indices[i]].first);
                assigned_bits.push_back(bit_on_rank(i));
            }
            layer_assigns.push_back({bs.bundle_id, bs.span_lo, bs.span_hi,
                                     bs.interval_lo, bs.interval_hi,
                                     std::move(assigned), std::move(assigned_bits)});
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

        // A repair this stage performs on the caller's behalf is reported, not
        // applied silently — the same reason the keepout cull above announces
        // itself.  A non-zero count means the plain snap would have opened that
        // many (segment, bit) pass-through crossings.
        int n_passthru_restored = 0;

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

            // PASS-THROUGH coverage: the same snap can strand a block this
            // segment covers by crossing it, and does so more brutally — a block
            // beyond the outermost junction lies wholly outside the snapped
            // extent, so the trim does not merely clip the crossing, it deletes
            // it (issue #496).  Re-extend to the crossing, but only when it was
            // actually lost: a bit that still overlaps the block is already
            // covered by check_dnuts's test, and extending it anyway would move
            // metal on every pass-through segment in the corpus for no
            // correctness gain.  The interval is clipped to the abstract span at
            // construction, so this can only give back wire abstract NUTS
            // already reserved for this segment.
            for (const auto& pt : bs_ptr->passthru_spans) {
                const double s_lo = std::min(ns.span_lo, ns.span_hi);
                const double s_hi = std::max(ns.span_lo, ns.span_hi);
                if (s_lo <= pt.second && s_hi >= pt.first) continue;  // still covered
                cover(pt.first);
                cover(pt.second);
                ++n_passthru_restored;
            }
        }
        if (n_passthru_restored > 0)
            std::cout << "[DetailedNUTS] restored " << n_passthru_restored
                      << " pass-through crossing(s) the per-bit span snap had "
                      << "trimmed away (issue #496).\n";
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
    // Tapered fan-in: the selected topology's per-segment bit membership
    // (empty for every non-fan-in bundle).
    std::map<int, const std::map<int, std::vector<int>>*> bid_to_bits;
    // Per (bundle, seg): the along-intervals where the segment covers a
    // connected block by pass-through, clipped to its own abstract span.  See
    // BusSegment::passthru_spans (issue #496).
    std::map<std::pair<int,int>, std::vector<std::pair<double,double>>> bid_to_pt;
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
        const Topology& topo = w.input.candidates[sel];
        ConnTopology ct;
        ct.build(topo, floorplan);
        bid_to_cs[bid] = ct.segs();
        if (!topo.seg_bits.empty())
            bid_to_bits[bid] = &topo.seg_bits;

        // Pass-through coverage anchors, derived from the SAME nominal geometry
        // check_dnuts's coverage check uses, so the two cannot disagree about
        // which block needs which segment.
        std::set<std::string> tapped;
        for (const auto& cs : ct.segs())
            for (const auto& conn : cs.conns)
                if (conn.kind == SegConn::BUSTERM) tapped.insert(conn.block_name);
        for (const auto& bname : topo.connected_block_names) {
            if (tapped.count(bname)) continue;      // has a real tap; not our business
            auto rects = floorplan.get_block_rects(bname);
            if (rects.empty()) rects.push_back(floorplan.get_block_bounds(bname));
            for (int i = 0; i < (int)ct.segs().size(); ++i) {
                const ConnSeg& cs = ct.segs()[i];
                const double s_lo = std::min((double)cs.along_lo, (double)cs.along_hi);
                const double s_hi = std::max((double)cs.along_lo, (double)cs.along_hi);
                for (const Rect& r : rects) {
                    const double p_lo = cs.horiz ? (double)r.y1 : (double)r.x1;
                    const double p_hi = cs.horiz ? (double)r.y2 : (double)r.x2;
                    const double a_lo = cs.horiz ? (double)r.x1 : (double)r.y1;
                    const double a_hi = cs.horiz ? (double)r.x2 : (double)r.y2;
                    if (cs.perp_pos < p_lo || cs.perp_pos > p_hi) continue;
                    if (s_lo > a_hi || s_hi < a_lo) continue;      // no crossing
                    bid_to_pt[{bid, i}].emplace_back(std::max(s_lo, a_lo),
                                                     std::min(s_hi, a_hi));
                }
            }
        }
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
        // Tapered fan-in: carry the segment's member-bit subset (global
        // indices) so the engine places only those bits on this segment.
        auto bb = bid_to_bits.find(ts.bundle_id);
        if (bb != bid_to_bits.end()) {
            auto it = bb->second->find(ts.seg_idx);
            if (it != bb->second->end() &&
                (int)it->second.size() < bs.bit_width)
                bs.bit_list = it->second;
        }
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
        auto ptit = bid_to_pt.find({ts.bundle_id, ts.seg_idx});
        if (ptit != bid_to_pt.end()) bs.passthru_spans = ptit->second;
        out.push_back(std::move(bs));
    }
    return out;
}

} // namespace buda
