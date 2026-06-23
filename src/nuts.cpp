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

#include "nuts.h"
#include "conn_topology.h"
#include <algorithm>
#include <cmath>
#include <functional>
#include <iostream>
#include <limits>
#include <map>
#include <numeric>
#include <set>

namespace buda {

// File-scope so it can appear in lambda default arguments (a local variable
// may not — odr-using one in a default argument is ill-formed on conforming
// compilers).
static constexpr double kInf = std::numeric_limits<double>::infinity();

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Merge extra grid points (sorted) into an existing sorted grid.
static void merge_grid(std::vector<int>& grid, const std::vector<int>& extra) {
    for (int val : extra) {
        auto it = std::lower_bound(grid.begin(), grid.end(), val);
        if (it == grid.end() || *it != val) grid.insert(it, val);
    }
}

// Return the index i such that grid[i] <= v <= grid[i+1], or -1 if out of range.

// ---------------------------------------------------------------------------
// Shared map builders
// ---------------------------------------------------------------------------

static void build_nuts_maps(
    const std::vector<BundleWrapper>& bundles,
    const Floorplan& floorplan,
    std::map<std::pair<int,int>, double>&                         pull_map,
    std::map<std::pair<int,int>, std::pair<double,double>>&       slide_map,
    std::set<std::pair<int,int>>&                                trunk_set,
    std::set<std::pair<int,int>>&                                busterm_set,
    std::map<std::pair<int,int>, std::vector<SpanAdjConn>>&       rev_conn_map,
    std::map<std::pair<int,int>, int>&                            net_pull_map,
    AlignMap&                                                     align_map)
{
    std::set<std::pair<int,int>> jog_set;   // dogleg jogs: excluded from alignment
    // Pass 1 — nominal perpendicular position from the topology.
    for (const auto& bw : bundles) {
        if (bw.input.candidates.empty() || bw.plan.selected_topology_index < 0) continue;
        const Topology& topo = bw.input.candidates[bw.plan.selected_topology_index];
        int bid = bw.input.original_bundle.id;
        for (int si = 0; si < (int)topo.segments.size(); ++si) {
            const Segment& seg = topo.segments[si];
            bool is_h  = (seg.start.y == seg.end.y);
            double nom = is_h ? static_cast<double>(seg.start.y)
                               : static_cast<double>(seg.start.x);
            pull_map[{bid, si}] = nom;
            if (seg.is_jog) jog_set.insert({bid, si});
        }
    }

    // Pass 2 — connectivity-based override.
    for (const auto& bw : bundles) {
        if (bw.input.candidates.empty() || bw.plan.selected_topology_index < 0) continue;
        const Topology& topo = bw.input.candidates[bw.plan.selected_topology_index];
        int bid = bw.input.original_bundle.id;

        ConnTopology ct;
        ct.build(topo, floorplan);
        const auto& conn_segs = ct.segs();

        // The dogleg's per-segment overrides (seg_net_pull / seg_slide_*) are
        // indexed by the doglegged topology's segments.  A later run_planner may
        // select a DIFFERENT topology for this bundle without refreshing them, so
        // honor an override array only when its length still matches the current
        // topology — otherwise it would be applied to unrelated segments.
        const bool np_ok    = (bw.plan.seg_net_pull.size() == conn_segs.size());
        const bool slide_ok = (bw.plan.seg_slide_lo.size() == conn_segs.size() &&
                               bw.plan.seg_slide_hi.size() == conn_segs.size());
        // seg_perp is planner-managed and normally matches the topology; guard it
        // too so a stale dogleg seg_perp can never be applied to a different one.
        const bool perp_ok  = (bw.plan.seg_perp.size() == conn_segs.size());

        for (int si = 0; si < (int)conn_segs.size(); ++si) {
            const ConnSeg& cs = conn_segs[si];
            auto key = std::make_pair(bid, si);

            // A dogleg pins its sub-trunks' / jog's slide range (ConnTopology
            // would recompute a narrower range on the split topology); honor it.
            if (slide_ok && !std::isnan(bw.plan.seg_slide_lo[si]))
                slide_map[key] = { bw.plan.seg_slide_lo[si], bw.plan.seg_slide_hi[si] };
            else
                slide_map[key] = { static_cast<double>(cs.perp_lo),
                                   static_cast<double>(cs.perp_hi) };

            int n_seg = 0, n_bt = 0;
            for (const auto& c : cs.conns) {
                if (c.kind == SegConn::SEG) ++n_seg;
                else                        ++n_bt;
            }
            if (n_seg >= 2 && n_bt == 0) trunk_set.insert(key);
            if (n_bt >= 1)               busterm_set.insert(key);

            for (const auto& conn : cs.conns) {
                if (conn.kind != SegConn::SEG) continue;
                auto t_key = std::make_pair(bid, conn.seg_idx);
                double mid = 0.5 * (cs.along_lo + cs.along_hi);
                bool lo_end   = (conn.at_pos <= mid);
                rev_conn_map[t_key].push_back({ bid, si, lo_end, conn.is_endpoint });
            }

            // Use cs.net_pull (computed by ConnTopology) to set the preferred
            // placement coordinate.  net_pull > 0 → slide toward perp_hi,
            // net_pull < 0 → slide toward perp_lo.  A per-segment override
            // (bw.plan.seg_net_pull, set by the dogleg pass) pins the value when
            // ConnTopology would recompute it wrongly on the split topology.
            int eff_net_pull = cs.net_pull;
            if (np_ok && bw.plan.seg_net_pull[si] != INT_MIN)
                eff_net_pull = bw.plan.seg_net_pull[si];
            net_pull_map[key] = eff_net_pull;
            if (eff_net_pull != 0) {
                constexpr double kSentinel = 5e8;
                // Pull toward the slide-window bound.  When the dogleg pinned the
                // slide range (slide_map was set from seg_slide_* just above), pull
                // toward THAT bound — the original trunk's extent — not the per-piece
                // bound ConnTopology recomputes on the split, which narrows it and
                // would tug the piece back off the trunk's exported position.
                const bool slide_pinned =
                    (slide_ok && !std::isnan(bw.plan.seg_slide_lo[si]));
                const double hi = slide_pinned ? slide_map[key].second
                                               : static_cast<double>(cs.perp_hi);
                const double lo = slide_pinned ? slide_map[key].first
                                               : static_cast<double>(cs.perp_lo);
                double preferred;
                if (eff_net_pull > 0)
                    preferred = (hi < kSentinel) ? hi : pull_map[key];  // fallback
                else
                    preferred = (lo > -kSentinel) ? lo : pull_map[key]; // fallback
                pull_map[key] = preferred;
            } else if (n_bt == 0 && perp_ok &&
                       bw.plan.seg_perp[si] != INT_MIN) {
                // Planner band preference: the slide-aware congestion lookup
                // charged this segment to a specific Hanan band (seg_perp =
                // centre of that band's usable window).  Prefer it over the
                // raw nominal so buses land in the bands whose capacity the
                // planner actually reserved — otherwise several buses pack
                // into one band by centre preference while the charged bands
                // sit empty.  Only for segments free of face semantics:
                // busterm stubs and net_pull-driven segments keep their
                // nominal/bound pulls (those encode face containment and
                // min-stub length).
                pull_map[key] = static_cast<double>(bw.plan.seg_perp[si]);
            }
        }
    }

    // Pass 3 — alignment siblings: segments of one bundle that connect to the
    // same perpendicular segment (e.g. a multicast trunk's stubs on opposite
    // sides).  rev_conn_map[T] lists the segs whose span follows T; any two of
    // them sharing T are siblings.
    for (const auto& [t_key, followers] : rev_conn_map) {
        // The two sub-trunks both follow the jog (T = the jog), so they appear as
        // siblings here.  Aligning them would place one piece on the other's track,
        // collapsing the split — and on a re-solve the seed's no-swap ordering is
        // gone, so this is the only thing standing between them.  Skip alignment
        // whenever the shared perpendicular T is itself a jog.
        if (jog_set.count(t_key)) continue;
        for (size_t i = 0; i < followers.size(); ++i)
            for (size_t j = i + 1; j < followers.size(); ++j) {
                if (followers[i].src_bid != followers[j].src_bid) continue;
                auto ka = std::make_pair(followers[i].src_bid, followers[i].src_si);
                auto kb = std::make_pair(followers[j].src_bid, followers[j].src_si);
                // A dogleg jog must NOT be aligned onto a sibling stub's track:
                // that would pull it off its own column and collapse the trunk
                // split it implements.  Only jogs are exempted; all genuine
                // sibling alignment (multicast/multi-trunk topologies) is kept.
                if (jog_set.count(ka) || jog_set.count(kb)) continue;
                align_map[ka].push_back(kb);
                align_map[kb].push_back(ka);
            }
    }
}

static void relax_boundary_intervals(
    std::vector<TrackSegment>& segments,
    const std::map<std::pair<int,int>, double>& pull_map,
    const std::map<std::pair<int,int>, int>& net_pull_map,
    const std::set<std::pair<int,int>>& busterm_set,
    int only_layer = -1)
{
    // When the preferred position equals an interval boundary, preferred_fit
    // clamps the bus center to interval_hi - half_width (or interval_lo +
    // half_width), so the bus edge — not its center — lands at the boundary.
    // This makes trunks overshoot block faces and pull-targets miss their mark.
    //
    // Fix: extend the interval by one full width outward so the bus CENTER can
    // land exactly at the preferred coordinate.
    //
    // Exception: segments with a direct busterm connection (endpoint stubs at a
    // block face). For these, preferred_fit already places the bus inner edge
    // AT the block face, which is correct — bits must lie within the block face
    // extent. Relaxing would shift the center to the face boundary, pushing half
    // the bus outside the block's perpendicular extent and causing DNUTS opens.
    //
    // Exception: net_pull-driven segments. Their preferred coordinate IS the
    // hard slide bound (perp_lo/perp_hi), which encodes min-stub-length for the
    // centerline. Edge-at-bound keeps every bit within [bound - width, bound],
    // so every per-bit stub keeps >= min_stub length. Center-at-bound would
    // spread bits half a width past the bound — past the block faces the stubs
    // descend from — making them unreachable (DNUTS opens, e.g. the U_VHV
    // detour trunk in flow/hbundles/08_cross_level.buda bundle 6).
    for (auto& ts : segments) {
        if (only_layer >= 0 && ts.layer != only_layer) continue;
        auto key = std::make_pair(ts.bundle_id, ts.seg_idx);
        if (busterm_set.count(key)) continue;   // block-face stubs: leave interval alone
        auto npit = net_pull_map.find(key);
        if (npit != net_pull_map.end() && npit->second != 0) continue;
        auto it = pull_map.find(key);
        if (it == pull_map.end()) continue;
        const double preferred = it->second;
        if (std::abs(preferred - ts.interval_hi) < 0.5) {
            ts.interval_hi += ts.width;
        } else if (std::abs(preferred - ts.interval_lo) < 0.5) {
            ts.interval_lo -= ts.width;
        }
    }
}

static void apply_interval_constraints(
    std::vector<TrackSegment>& segments,
    const std::map<std::pair<int,int>, std::pair<double,double>>& slide_map,
    const std::set<std::pair<int,int>>&                           trunk_set,
    const std::map<std::pair<int,int>, int>&                      net_pull_map,
    int only_layer = -1)
{
    constexpr double kSentinel = 5e8;
    for (auto& ts : segments) {
        if (only_layer >= 0 && ts.layer != only_layer) continue;
        auto key = std::make_pair(ts.bundle_id, ts.seg_idx);

        auto sit = slide_map.find(key);
        if (sit != slide_map.end()) {
            auto [slo, shi] = sit->second;
            if (slo > -kSentinel) ts.interval_lo = std::max(ts.interval_lo, slo);
            if (shi <  kSentinel) ts.interval_hi = std::min(ts.interval_hi, shi);
        }

        // Propagate net_pull into TrackSegment for use in solve_layer.
        auto npit = net_pull_map.find(key);
        if (npit != net_pull_map.end()) ts.net_pull = npit->second;

        if (trunk_set.count(key)) {
            double span   = ts.interval_hi - ts.interval_lo;
            double margin = 0.1 * span;
            // Apply margin only on the side opposite the pull direction so the
            // preferred edge stays reachable.  Symmetric margin for balanced trunks.
            int np = ts.net_pull;
            double new_lo = ts.interval_lo + (np >= 0 ? margin : 0.0);
            double new_hi = ts.interval_hi - (np <= 0 ? margin : 0.0);
            if (new_hi - new_lo >= ts.width) {
                ts.interval_lo = new_lo;
                ts.interval_hi = new_hi;
            }
        }
    }
}

static void do_span_adjustments(
    const std::vector<TrackSegment*>&                               layer_segs,
    const std::map<std::pair<int,int>, std::vector<SpanAdjConn>>&   rev_conn_map,
    std::map<std::pair<int,int>, TrackSegment*>&                     ts_ptr_map,
    bool                                                             only_unplaced = false)
{
    struct AdjReq { double center; bool lo_end; bool is_endpoint; bool from_jog; };
    std::map<std::pair<int,int>, std::vector<AdjReq>> adj_map;

    for (const TrackSegment* ts : layer_segs) {
        if (!ts->placed) continue;
        auto it = rev_conn_map.find({ts->bundle_id, ts->seg_idx});
        if (it == rev_conn_map.end()) continue;

        for (const auto& sc : it->second) {
            auto jt = ts_ptr_map.find({sc.src_bid, sc.src_si});
            if (jt == ts_ptr_map.end()) continue;
            TrackSegment* other = jt->second;
            if (only_unplaced && other->placed) continue;
            adj_map[{sc.src_bid, sc.src_si}].push_back(
                {ts->track_position, sc.lo_end, sc.is_endpoint, ts->is_jog});
        }
    }

    for (auto& [key, reqs] : adj_map) {
        auto jt = ts_ptr_map.find(key);
        if (jt == ts_ptr_map.end()) continue;
        TrackSegment* other = jt->second;

        bool has_lo = false, has_hi = false;
        double min_lo = std::numeric_limits<double>::infinity();
        double max_hi = -std::numeric_limits<double>::infinity();
        bool all_lo_endpoints = true, all_hi_endpoints = true;
        // A dogleg piece's inner end is DEFINED by the jog it meets: when the jog
        // slides, that end must track it (contract, not just extend).  The piece's
        // nominal span was set at the pre-slide split column, so extend-only would
        // leave it overstretched past a jog that slid inward.  Treat an endpoint
        // connection to a jog as authoritative for that end even when an interior
        // tap (e.g. a multicast driver stub on the split column) shares it.
        bool lo_jog = false, hi_jog = false;

        for (const auto& req : reqs) {
            if (req.lo_end) {
                has_lo = true;
                min_lo = std::min(min_lo, req.center);
                if (!req.is_endpoint) all_lo_endpoints = false;
                if (req.from_jog && req.is_endpoint) lo_jog = true;
            } else {
                has_hi = true;
                max_hi = std::max(max_hi, req.center);
                if (!req.is_endpoint) all_hi_endpoints = false;
                if (req.from_jog && req.is_endpoint) hi_jog = true;
            }
        }

        if (has_lo) {
            if (all_lo_endpoints || lo_jog) other->span_lo = min_lo;
            else other->span_lo = std::min(other->span_lo, min_lo);
        }
        if (has_hi) {
            if (all_hi_endpoints || hi_jog) other->span_hi = max_hi;
            else other->span_hi = std::max(other->span_hi, max_hi);
        }

        // Coverage guarantee: a segment must physically reach every segment
        // connected to it.  The lo_end/hi_end split above is keyed on NOMINAL
        // geometry, so when placement moves a tap across the trunk (e.g. a
        // keepout or congestion pushes a stub past the far end) its stale label
        // routes it to the wrong end and the trunk stops short — a real open.
        // Extend the geometric extent to include any out-of-range connection
        // center, mapping back to span_lo/span_hi by their current ordering so
        // endpoint identity (possibly span_lo > span_hi) is preserved.  This
        // only ever extends, so it cannot undo a legitimate jog contraction.
        for (const auto& req : reqs) {
            const double lo = std::min(other->span_lo, other->span_hi);
            const double hi = std::max(other->span_lo, other->span_hi);
            const bool ordered = (other->span_lo <= other->span_hi);
            if (req.center < lo) {
                (ordered ? other->span_lo : other->span_hi) = req.center;
            } else if (req.center > hi) {
                (ordered ? other->span_hi : other->span_lo) = req.center;
            }
        }
        // span_lo/span_hi intentionally keep NOMINAL endpoint identity (span_lo
        // is the lo_end coordinate, span_hi the hi_end) even when placement
        // leaves span_lo > span_hi: corner/dogleg logic derives the fixed anchor
        // as `lo_end ? span_hi : span_lo` and relies on that pairing.  Consumers
        // that need an ordered extent (the connectivity checks) take min/max
        // locally rather than mutating the stored bounds here.
    }
}

// KeepoutZones on the segment's layer that intersect its span, as occupied
// perpendicular intervals.
// span_lo/span_hi carry NOMINAL endpoint identity and may be stored with
// span_lo > span_hi after placement (see do_span_adjustments).  Geometric tests
// — overlap detection, keepout/occupancy, block coverage — need the ORDERED
// extent, so they take these instead of reading span_lo/span_hi directly.
static inline double sp_lo(const TrackSegment& s) { return std::min(s.span_lo, s.span_hi); }
static inline double sp_hi(const TrackSegment& s) { return std::max(s.span_lo, s.span_hi); }

static void keepout_occupied(const std::vector<KeepoutZone>& kozs,
                             const TrackSegment* t,
                             std::vector<std::pair<double,double>>& occ)
{
    for (const auto& koz : kozs) {
        if (!koz.layer_ids.count(t->layer)) continue;
        if (t->horiz) {
            // Horizontal segment: span in X, pos in Y.
            if (sp_lo(*t) < koz.bbox.x2 && sp_hi(*t) > koz.bbox.x1)
                occ.push_back({static_cast<double>(koz.bbox.y1),
                               static_cast<double>(koz.bbox.y2)});
        } else {
            // Vertical segment: span in Y, pos in X.
            if (sp_lo(*t) < koz.bbox.y2 && sp_hi(*t) > koz.bbox.y1)
                occ.push_back({static_cast<double>(koz.bbox.x1),
                               static_cast<double>(koz.bbox.x2)});
        }
    }
}

// Physical overlap test for two placed segments at their current (adjusted)
// spans.  Same-bundle pairs never conflict: their bits are the same nets and
// may share tracks (DetailedNUTS reservation exempts same-bundle segments).
static bool segs_overlap(const TrackSegment& a, const TrackSegment& b)
{
    if (a.layer != b.layer || !a.placed || !b.placed) return false;
    if (a.bundle_id == b.bundle_id) return false;
    // Abstract segments are bit-bundles, not single wires, so the two touch axes
    // differ.  ALONG the routing direction (span), an end-to-end touch means the
    // bits butt up collinearly → a DRC: the span test is CLOSED (touch counts).
    // PERPENDICULAR (track), a parallel touch just means two bundles sit edge to
    // edge; intra-bundle spacing covers it → not a DRC: the perp test stays
    // STRICT.  So: spans overlap-or-touch AND tracks strictly overlap.
    if (sp_hi(a) < sp_lo(b) || sp_hi(b) < sp_lo(a)) return false;
    return a.track_position + a.width / 2.0 > b.track_position - b.width / 2.0 &&
           b.track_position + b.width / 2.0 > a.track_position - a.width / 2.0;
}

// All overlapping segment-index pairs {i<j}, via a per-layer span sweep-line:
// segments conflict only within a layer and only where their spans overlap, so
// sort each layer by span_lo and compare an entering segment against just the
// still-active set (segments whose span has not yet ended).  O(n log n + k) vs
// the former O(n^2); the emitted pair set is identical (segs_overlap stays the
// pairwise predicate, so same-bundle / track-disjoint pairs are filtered out).
static std::vector<std::pair<int,int>> find_overlaps(
    const std::vector<TrackSegment>& segments)
{
    std::map<int, std::vector<int>> by_layer;
    for (int i = 0; i < (int)segments.size(); ++i)
        if (segments[i].placed) by_layer[segments[i].layer].push_back(i);

    std::vector<std::pair<int,int>> pairs;
    std::vector<int> active;
    for (auto& [layer, idx] : by_layer) {
        std::sort(idx.begin(), idx.end(), [&](int a, int b) {
            return sp_lo(segments[a]) < sp_lo(segments[b]);
        });
        active.clear();
        for (int i : idx) {
            const double lo_i = sp_lo(segments[i]);
            // Evict only segments whose span ended strictly before i starts —
            // a segment ending exactly at lo_i still TOUCHES i and must be
            // compared (segs_overlap treats touch as a conflict).  Ordered bounds:
            // span_lo/span_hi may be stored reversed (nominal endpoint identity).
            active.erase(std::remove_if(active.begin(), active.end(),
                [&](int a) { return sp_hi(segments[a]) < lo_i; }), active.end());
            for (int a : active)
                if (segs_overlap(segments[i], segments[a]))
                    pairs.push_back({std::min(i, a), std::max(i, a)});
            active.push_back(i);
        }
    }
    return pairs;
}

// Placed segments whose track (± half width) falls outside their hard interval.
static int count_violations(const std::vector<TrackSegment>& segments)
{
    int v = 0;
    for (const auto& ts : segments) {
        if (!ts.placed) continue;
        if (ts.track_position - ts.width / 2.0 < ts.interval_lo ||
            ts.track_position + ts.width / 2.0 > ts.interval_hi)
            ++v;
    }
    return v;
}

static void compute_metrics(NUTSResult& result)
{
    result.num_violations = count_violations(result.segments);
    result.num_overlaps   = 0;
    result.overlaps_per_layer.clear();
    result.overlap_details.clear();

    for (auto [i, j] : find_overlaps(result.segments)) {
        const auto& a = result.segments[i];
        const auto& b = result.segments[j];
        ++result.num_overlaps;
        ++result.overlaps_per_layer[a.layer];
        OverlapDetail od;
        od.layer   = a.layer;
        od.bid_a   = a.bundle_id;  od.seg_a = a.seg_idx;
        od.bid_b   = b.bundle_id;  od.seg_b = b.seg_idx;
        od.span_lo = std::max(sp_lo(a), sp_lo(b));   // ordered: spans may be reversed
        od.span_hi = std::min(sp_hi(a), sp_hi(b));
        od.perp_lo = std::max(a.track_position - a.width / 2.0,
                              b.track_position - b.width / 2.0);
        od.perp_hi = std::min(a.track_position + a.width / 2.0,
                              b.track_position + b.width / 2.0);
        result.overlap_details.push_back(od);
    }
}

void NUTSEngine::repair_overlaps(
    std::vector<TrackSegment>& segments,
    const std::map<std::pair<int,int>, double>&                pull_map,
    const std::map<std::pair<int,int>, int>&                   net_pull_map,
    const AlignMap&                                            align_map,
    const std::map<std::pair<int,int>, std::vector<SpanAdjConn>>& rev_conn_map,
    std::map<std::pair<int,int>, TrackSegment*>&               ts_ptr_map) const
{
    auto initial = find_overlaps(segments);
    if (initial.empty()) return;

    // Snapshot for the non-regression guard: a move re-adjusts follower
    // spans, which can surface new overlaps elsewhere.
    struct Snap { double pos, lo, hi; };
    std::vector<Snap> snapshot;
    snapshot.reserve(segments.size());
    for (const auto& ts : segments)
        snapshot.push_back({ts.track_position, ts.span_lo, ts.span_hi});

    const auto kozs = low_keepouts();
    auto pull_of = [&](const TrackSegment& ts) {
        auto it = net_pull_map.find({ts.bundle_id, ts.seg_idx});
        return it == net_pull_map.end() ? 0 : it->second;
    };

    // Per-move snapshot: a move re-adjusts follower spans globally, so a
    // locally good move can surface overlaps elsewhere.  Each move is
    // accepted only if the global overlap count strictly drops; otherwise
    // just that move is rolled back — earlier accepted moves are kept.
    auto take_snap = [&](std::vector<Snap>& s) {
        s.clear();
        s.reserve(segments.size());
        for (const auto& ts : segments)
            s.push_back({ts.track_position, ts.span_lo, ts.span_hi});
    };
    auto restore_snap = [&](const std::vector<Snap>& s) {
        for (size_t k = 0; k < segments.size(); ++k) {
            segments[k].track_position = s[k].pos;
            segments[k].span_lo        = s[k].lo;
            segments[k].span_hi        = s[k].hi;
        }
    };

    int moved = 0;
    std::vector<Snap> pre_move;
    for (int iter = 0; iter < 8; ++iter) {
        auto pairs = find_overlaps(segments);
        if (pairs.empty()) break;
        bool progress = false;
        for (auto [i, j] : pairs) {
            TrackSegment& a = segments[i];
            TrackSegment& b = segments[j];
            if (!segs_overlap(a, b)) continue;   // fixed by an earlier move

            // Victim: only an unpulled segment may move — a pull target is
            // semantic (block face / min-stub bound) and its edge-at-bound
            // placement is what keeps every bit within the face; relocating
            // it can push bits past the face even inside its interval.
            // Both pulled → the pair is not repairable here.
            TrackSegment* victim;
            int pa = std::abs(pull_of(a)), pb = std::abs(pull_of(b));
            if (pa == 0 && pb == 0) {
                double sa = (a.interval_hi - a.interval_lo) - a.width;
                double sb = (b.interval_hi - b.interval_lo) - b.width;
                victim = (sa > sb) ? &a : &b;   // larger slack moves
            } else if (pa == 0) {
                victim = &a;
            } else if (pb == 0) {
                victim = &b;
            } else {
                continue;
            }

            std::vector<std::pair<double,double>> occ;
            keepout_occupied(kozs, victim, occ);
            for (const auto& o : segments) {
                if (&o == victim || !o.placed) continue;
                if (o.layer != victim->layer) continue;
                if (o.bundle_id == victim->bundle_id) continue;
                if (sp_lo(o) <= sp_hi(*victim) && sp_lo(*victim) <= sp_hi(o)) {  // closed: touch = occupied
                    const double h = o.width / 2.0;
                    occ.push_back({o.track_position - h, o.track_position + h});
                }
            }
            std::sort(occ.begin(), occ.end());

            const double c_lo = victim->interval_lo + victim->width / 2.0;
            const double c_hi = victim->interval_hi - victim->width / 2.0;
            auto vkey = std::make_pair(victim->bundle_id, victim->seg_idx);
            double preferred = std::numeric_limits<double>::quiet_NaN();
            auto ait = align_map.find(vkey);
            if (ait != align_map.end()) {
                for (const auto& sk : ait->second) {
                    auto lit = ts_ptr_map.find(sk);
                    if (lit == ts_ptr_map.end() || !lit->second->placed) continue;
                    if (lit->second->layer != victim->layer) continue;
                    double p = lit->second->track_position;
                    if (p >= c_lo && p <= c_hi) { preferred = p; break; }
                }
            }
            if (std::isnan(preferred)) {
                auto it = pull_map.find(vkey);
                preferred = (it != pull_map.end())
                            ? it->second
                            : (victim->interval_lo + victim->interval_hi) / 2.0;
            }
            preferred = std::clamp(preferred, c_lo, c_hi);

            double pos = preferred_fit(victim->interval_lo, victim->interval_hi,
                                       victim->width, occ, preferred);
            if (std::isnan(pos) || pos == victim->track_position) continue;

            const size_t before = find_overlaps(segments).size();
            take_snap(pre_move);
            victim->track_position = pos;
            // Settle followers of the moved segment (and theirs, cheaply).
            std::vector<TrackSegment*> all_placed;
            for (auto& ts : segments) if (ts.placed) all_placed.push_back(&ts);
            do_span_adjustments(all_placed, rev_conn_map, ts_ptr_map);
            if (find_overlaps(segments).size() >= before) {
                restore_snap(pre_move);   // this move made things no better
                continue;
            }
            ++moved;
            progress = true;
        }
        if (!progress) break;
    }

    auto remaining = find_overlaps(segments);
    if (remaining.size() >= initial.size()) {
        // No strict improvement: restore the pre-repair state.
        restore_snap(snapshot);
        return;
    }
    if (moved > 0)
        std::cout << "[NUTS] overlap repair: moved " << moved
                  << " segment(s), overlaps " << initial.size()
                  << " -> " << remaining.size() << ".\n";
}

void NUTSEngine::tighten_pulls(
    std::vector<TrackSegment>& segments,
    const std::map<std::pair<int,int>, int>&                   net_pull_map,
    const std::map<std::pair<int,int>, std::vector<SpanAdjConn>>& rev_conn_map,
    std::map<std::pair<int,int>, TrackSegment*>&               ts_ptr_map,
    int only_layer) const
{
    const auto kozs = low_keepouts();

    struct Snap { double pos, lo, hi; };
    std::vector<Snap> pre_move;
    auto take_snap = [&](std::vector<Snap>& s) {
        s.clear(); s.reserve(segments.size());
        for (const auto& ts : segments)
            s.push_back({ts.track_position, ts.span_lo, ts.span_hi});
    };
    auto restore_snap = [&](const std::vector<Snap>& s) {
        for (size_t k = 0; k < segments.size(); ++k) {
            segments[k].track_position = s[k].pos;
            segments[k].span_lo        = s[k].lo;
            segments[k].span_hi        = s[k].hi;
        }
    };
    // True routed length = sum of every placed segment's span extent.  Sliding a
    // segment toward its pull contracts the follower spans connected to it, so
    // this is the quantity the pass minimises.
    auto total_wl = [&]() {
        double w = 0;
        for (const auto& ts : segments) if (ts.placed) w += sp_hi(ts) - sp_lo(ts);
        return w;
    };
    // net_pull (the slide direction that shortens connected stubs) for a segment,
    // preferring the planner/dogleg-managed value, falling back to the stored one.
    auto pull_of = [&](const TrackSegment& ts) {
        auto it = net_pull_map.find({ts.bundle_id, ts.seg_idx});
        return it == net_pull_map.end() ? ts.net_pull : it->second;
    };
    // The pull bound (interval edge the segment wants to reach), clamped so the
    // bus CENTRE keeps the whole width inside the hard interval.
    auto pull_bound = [&](const TrackSegment& ts, int np) {
        const double half = ts.width / 2.0;
        const double c_lo = ts.interval_lo + half, c_hi = ts.interval_hi - half;
        double p = (np > 0) ? ts.interval_hi : ts.interval_lo;
        return (c_lo <= c_hi) ? std::clamp(p, c_lo, c_hi) : ts.track_position;
    };
    // Same-layer occupancy from other bundles whose span overlaps ts, plus
    // keepouts — exactly what a track for ts must avoid.
    auto build_occ = [&](const TrackSegment& ts,
                         std::vector<std::pair<double,double>>& occ) {
        keepout_occupied(kozs, &ts, occ);
        for (const auto& o : segments) {
            if (&o == &ts || !o.placed || o.layer != ts.layer) continue;
            if (o.bundle_id == ts.bundle_id) continue;
            if (sp_lo(o) <= sp_hi(ts) && sp_lo(ts) <= sp_hi(o)) {  // closed: touch = occupied
                const double h = o.width / 2.0;
                occ.push_back({o.track_position - h, o.track_position + h});
            }
        }
        std::sort(occ.begin(), occ.end());
    };

    int moved = 0;
    const int kMaxIters = 6;
    for (int iter = 0; iter < kMaxIters; ++iter) {
        // Close the biggest pull gaps first: rank pulled, placed segments by
        // current distance from their pull bound.
        std::vector<std::pair<double,int>> order;
        for (int i = 0; i < (int)segments.size(); ++i) {
            const auto& ts = segments[i];
            if (!ts.placed) continue;
            if (only_layer >= 0 && ts.layer != only_layer) continue;
            int np = pull_of(ts);
            if (np == 0) continue;
            double dev = std::abs(ts.track_position - pull_bound(ts, np));
            if (dev > 0.5) order.emplace_back(dev, i);
        }
        if (order.empty()) break;
        std::sort(order.begin(), order.end(),
                  [](const auto& a, const auto& b) { return a.first > b.first; });

        bool progress = false;
        for (const auto& [dev0, i] : order) {
            TrackSegment& ts = segments[i];
            int np = pull_of(ts);
            if (np == 0) continue;
            const double pb  = pull_bound(ts, np);
            const double cur = std::abs(ts.track_position - pb);
            if (cur <= 0.5) continue;

            std::vector<std::pair<double,double>> occ;
            build_occ(ts, occ);
            double pos = preferred_fit(ts.interval_lo, ts.interval_hi, ts.width, occ, pb);
            if (std::isnan(pos) || std::abs(pos - pb) + 0.5 >= cur)
                continue;   // no closer-to-pull track is free

            const size_t ov_before = find_overlaps(segments).size();
            const int    vi_before = count_violations(segments);
            const double wl_before = total_wl();
            take_snap(pre_move);
            ts.track_position = pos;
            // Pull the followers' spans onto the new track, then keep the move
            // only if it genuinely shortened wiring without new shorts/violations.
            std::vector<TrackSegment*> all_placed;
            for (auto& s : segments) if (s.placed) all_placed.push_back(&s);
            do_span_adjustments(all_placed, rev_conn_map, ts_ptr_map);
            if (find_overlaps(segments).size() > ov_before ||
                count_violations(segments)    > vi_before ||
                total_wl() + 0.5 >= wl_before) {
                restore_snap(pre_move);
                continue;
            }
            ++moved;
            progress = true;
        }
        if (!progress) break;
    }
    if (moved > 0)
        std::cout << "[NUTS] wirelength tighten: pulled " << moved
                  << " segment(s) toward their pull bound.\n";
}

void NUTSEngine::resolve_corner_overlaps(
    std::vector<TrackSegment>& segments,
    const std::map<std::pair<int,int>, double>&                pull_map,
    const std::map<std::pair<int,int>, int>&                   net_pull_map,
    const AlignMap&                                            align_map,
    const std::map<std::pair<int,int>, std::vector<SpanAdjConn>>& rev_conn_map,
    std::map<std::pair<int,int>, TrackSegment*>&               ts_ptr_map) const
{
    using Key = std::pair<int,int>;
    // A corner overlap is geometric (two stubs hanging from distinct trunks); it
    // need not stem from a span change — aligned trunks can make stubs touch
    // end-to-end with no stretching at all — so the pass is not gated on a
    // "stretched" set, only on the presence of overlaps.
    if (find_overlaps(segments).empty()) return;

    // Follower → (trunk it follows, lo_end): rev_conn_map[T] lists segments whose
    // span follows T, so each such follower's trunk is T.
    std::map<Key, std::pair<Key,bool>> trunk_of;
    for (const auto& [tkey, conns] : rev_conn_map)
        for (const auto& sc : conns)
            trunk_of[{sc.src_bid, sc.src_si}] = {tkey, sc.lo_end};

    std::map<Key,int> idx_of;
    for (int i = 0; i < (int)segments.size(); ++i)
        idx_of[{segments[i].bundle_id, segments[i].seg_idx}] = i;

    // The trunk meets the follower at its lo_end; the OTHER end is anchored.
    auto anchored_coord = [](const TrackSegment& s, bool lo_end) {
        return lo_end ? s.span_hi : s.span_lo;
    };

    std::map<int, LayerConstraints> by_layer_cons;  // trunk layer → phase-0 constraints
    // Same-trunk-layer: hi must sit above lo (relative ordering edge).
    auto add_edge = [&](const Key& lo, const Key& hi) -> bool {
        auto it = idx_of.find(hi);
        if (it == idx_of.end()) return false;
        return by_layer_cons[segments[it->second].layer].preds[hi].insert(lo).second;
    };
    // Cross-trunk-layer: intersect a fixed track-bound [lo, hi] onto a trunk's
    // own layer; returns true if it tightened the bound.
    auto add_bound = [&](const Key& k, double lo, double hi) -> bool {
        auto it = idx_of.find(k);
        if (it == idx_of.end()) return false;
        auto& b = by_layer_cons[segments[it->second].layer].bounds;
        auto bit = b.find(k);
        if (bit == b.end()) { b[k] = {lo, hi}; return true; }
        double nlo = std::max(bit->second.first, lo);
        double nhi = std::min(bit->second.second, hi);
        bool changed = (nlo != bit->second.first || nhi != bit->second.second);
        bit->second = {nlo, nhi};
        return changed;
    };

    for (int iter = 0; iter < 6; ++iter) {
        auto pairs = find_overlaps(segments);
        if (pairs.empty()) break;

        // Snapshot the constraint set so a reverted iteration's new edges/bounds
        // are dropped (only committed constraints persist to detailed NUTS).
        auto cons_before = by_layer_cons;
        bool new_edge = false;
        std::set<int> dirty_layers;
        for (auto [i, j] : pairs) {
            Key kp{segments[i].bundle_id, segments[i].seg_idx};
            Key kq{segments[j].bundle_id, segments[j].seg_idx};
            auto tp = trunk_of.find(kp), tq = trunk_of.find(kq);
            if (tp == trunk_of.end() || tq == trunk_of.end()) continue;
            Key trunk_p = tp->second.first, trunk_q = tq->second.first;
            if (trunk_p == trunk_q) continue;
            auto pit = idx_of.find(trunk_p), qit = idx_of.find(trunk_q);
            if (pit == idx_of.end() || qit == idx_of.end()) continue;
            double ap = anchored_coord(segments[i], tp->second.second);
            double aq = anchored_coord(segments[j], tq->second.second);
            // Lower anchored end ⇒ its trunk takes the lower track.
            Key lo_trunk = (ap < aq) ? trunk_p : trunk_q;
            Key hi_trunk = (ap < aq) ? trunk_q : trunk_p;
            const TrackSegment& loT = segments[idx_of[lo_trunk]];
            const TrackSegment& hiT = segments[idx_of[hi_trunk]];

            if (loT.layer == hiT.layer) {
                // Same trunk layer: order them on that layer (bottom-edge pack).
                if (add_edge(lo_trunk, hi_trunk)) {
                    new_edge = true;
                    dirty_layers.insert(hiT.layer);
                }
            } else {
                // Cross trunk layer: the trunks can't be track-ordered against
                // each other (different metals), so nudge each within its own
                // layer to opposite sides of a split S, separated by g.  Abstract
                // gap g = 1 unit just sets the side; the real spacing is the
                // detailed-NUTS signal tracks (future work).
                const double g    = 1.0;
                const double lmin = loT.interval_lo + loT.width / 2.0;  // lo trunk's lowest
                const double hmax = hiT.interval_hi - hiT.width / 2.0;  // hi trunk's highest
                const double Slo  = lmin + g / 2.0;
                const double Shi  = hmax - g / 2.0;
                if (Slo > Shi) continue;   // intervals can't admit lo below hi
                const double S = std::clamp(
                    (loT.track_position + hiT.track_position) / 2.0, Slo, Shi);
                bool c1 = add_bound(lo_trunk, -kInf, S - g / 2.0);
                bool c2 = add_bound(hi_trunk, S + g / 2.0, kInf);
                if (c1 || c2) {
                    new_edge = true;
                    dirty_layers.insert(loT.layer);
                    dirty_layers.insert(hiT.layer);
                }
            }
        }
        if (!new_edge) break;     // no resolvable corner overlap (or a cycle)

        // Snapshot for the stop-&-reverse guard.
        struct Snap { double pos, lo, hi; bool placed; };
        std::vector<Snap> snap; snap.reserve(segments.size());
        for (const auto& ts : segments)
            snap.push_back({ts.track_position, ts.span_lo, ts.span_hi, ts.placed});
        const size_t before      = pairs.size();
        const int    before_viol = count_violations(segments);

        // Re-solve each affected trunk layer under the accumulated constraints.
        for (int layer : dirty_layers) {
            std::vector<TrackSegment*> layer_segs;
            for (auto& ts : segments)
                if (ts.layer == layer) {
                    ts.track_position = std::numeric_limits<double>::quiet_NaN();
                    ts.placed = false;
                    layer_segs.push_back(&ts);
                }
            solve_layer(layer_segs, pull_map, align_map, by_layer_cons[layer]);
        }
        // Re-fit connected spans to the new trunk positions and repair residue.
        std::vector<TrackSegment*> all_placed;
        for (auto& ts : segments) if (ts.placed) all_placed.push_back(&ts);
        do_span_adjustments(all_placed, rev_conn_map, ts_ptr_map);
        repair_overlaps(segments, pull_map, net_pull_map, align_map, rev_conn_map, ts_ptr_map);

        const size_t after = find_overlaps(segments).size();
        // Accept only a strict overlap improvement that does not introduce a new
        // interval violation (an infeasible ordering must not trade a legal
        // overlap for a violation) — otherwise stop & reverse.
        if (after >= before || count_violations(segments) > before_viol) {
            for (size_t k = 0; k < segments.size(); ++k) {
                segments[k].track_position = snap[k].pos;
                segments[k].span_lo        = snap[k].lo;
                segments[k].span_hi        = snap[k].hi;
                segments[k].placed         = snap[k].placed;
            }
            by_layer_cons = cons_before;   // this iteration's edges/bounds are reverted
            break;
        }
        std::cout << "[NUTS] corner-overlap pass: overlaps " << before
                  << " -> " << after << ".\n";
    }

    // Persist committed cross-layer split bounds onto the trunk TrackSegments,
    // so detailed NUTS snaps each trunk to its bounded side on real signal
    // tracks (the abstract g=1 only set the side).  Same-layer ordering edges
    // need no carry — detailed NUTS already separates same-layer trunks via
    // track reservation.
    for (const auto& [layer, cons] : by_layer_cons) {
        (void)layer;
        for (const auto& [k, b] : cons.bounds) {
            auto it = idx_of.find(k);
            if (it == idx_of.end()) continue;
            segments[it->second].track_lo_bound = b.first;
            segments[it->second].track_hi_bound = b.second;
        }
    }
}

NUTSEngine::NUTSEngine(const Floorplan& fp, const LayerStack& ls)
    : floorplan_(fp), layers_(ls) {}

void NUTSEngine::set_track_pitch(double pitch) {
    track_pitch_ = pitch;
}

std::vector<KeepoutZone> NUTSEngine::low_keepouts() const {
    std::vector<int> low_ids;
    for (int lid : layers_.get_layer_ids_by_dir(LayerDir::VERTICAL))
        if (!layers_.is_top(lid)) low_ids.push_back(lid);
    for (int lid : layers_.get_layer_ids_by_dir(LayerDir::HORIZONTAL))
        if (!layers_.is_top(lid)) low_ids.push_back(lid);
    return floorplan_.low_layer_keepouts(low_ids);
}


std::vector<TrackSegment> NUTSEngine::extract_segments(
    const std::vector<BundleWrapper>& bundles,
    const std::vector<int>& x_grid,
    const std::vector<int>& y_grid) const
{
    std::vector<TrackSegment> result;
    for (const auto& bw : bundles) {
        if (bw.input.candidates.empty() || bw.plan.selected_topology_index < 0) continue;
        const Topology& topo = bw.input.candidates[bw.plan.selected_topology_index];
        for (int si = 0; si < (int)topo.segments.size(); ++si) {
            const Segment& seg = topo.segments[si];
            const bool is_horizontal = (seg.start.y == seg.end.y);
            TrackSegment ts;
            ts.bundle_id = bw.input.original_bundle.id;
            ts.seg_idx   = si;
            ts.horiz     = is_horizontal;
            ts.is_jog    = seg.is_jog;

            int lid = 0;
            if (si < (int)bw.plan.seg_layers.size() && bw.plan.seg_layers[si] >= 0)
                lid = bw.plan.seg_layers[si];
            else if (!is_horizontal && bw.input.assigned_v_layer >= 0)
                lid = bw.input.assigned_v_layer;
            else if (is_horizontal && bw.input.assigned_h_layer >= 0)
                lid = bw.input.assigned_h_layer;
            else
                lid = seg.layer_hint;

            ts.layer = lid;
            ts.width = layers_.eff_bus_width(
                (int)bw.input.original_bundle.get_net_names().size(), bw.input.width, lid);

            if (ts.horiz) {
                ts.span_lo = std::min(seg.start.x, seg.end.x);
                ts.span_hi = std::max(seg.start.x, seg.end.x);
                // Unlock Hanan bands: use full chip boundary as initial interval.
                ts.interval_lo = static_cast<double>(y_grid.front());
                ts.interval_hi = static_cast<double>(y_grid.back());
            } else {
                ts.span_lo = std::min(seg.start.y, seg.end.y);
                ts.span_hi = std::max(seg.start.y, seg.end.y);
                ts.interval_lo = static_cast<double>(x_grid.front());
                ts.interval_hi = static_cast<double>(x_grid.back());
            }
            result.push_back(ts);
        }
    }
    return result;
}

double NUTSEngine::first_fit(double lo, double hi, double width,
                              const std::vector<std::pair<double,double>>& occupied) const
{
    const double half = width / 2.0;
    const double c_lo = lo + half;
    const double c_hi = hi - half;
    if (c_lo > c_hi) return std::numeric_limits<double>::quiet_NaN();
    std::vector<double> candidates;
    candidates.push_back(c_lo);
    for (const auto& [occ_lo, occ_hi] : occupied)
        candidates.push_back(occ_hi + track_pitch_ + half);
    std::sort(candidates.begin(), candidates.end());
    for (double c : candidates) {
        if (c < c_lo) continue;
        if (c > c_hi) break;
        bool conflict = false;
        for (const auto& [occ_lo, occ_hi] : occupied) {
            if (c - half < occ_hi && c + half > occ_lo) { conflict = true; break; }
        }
        if (!conflict) return c;
    }
    return std::numeric_limits<double>::quiet_NaN();
}

double NUTSEngine::preferred_fit(
    double lo, double hi, double width,
    const std::vector<std::pair<double,double>>& occupied,
    double preferred) const
{
    const double half = width / 2.0;
    const double c_lo = lo + half;
    const double c_hi = hi - half;
    if (c_lo > c_hi) return std::numeric_limits<double>::quiet_NaN();
    std::vector<double> candidates;
    candidates.push_back(preferred);
    candidates.push_back(c_lo);
    for (const auto& [occ_lo, occ_hi] : occupied) {
        candidates.push_back(occ_hi + track_pitch_ + half);
        candidates.push_back(occ_lo - track_pitch_ - half);
    }
    auto valid = [&](double c) -> bool {
        if (c < c_lo || c > c_hi) return false;
        for (const auto& [occ_lo, occ_hi] : occupied)
            if (c - half < occ_hi && c + half > occ_lo) return false;
        return true;
    };
    double best      = std::numeric_limits<double>::quiet_NaN();
    double best_dist = std::numeric_limits<double>::max();
    for (double c : candidates) {
        if (!valid(c)) continue;
        double dist = std::abs(c - preferred);
        if (dist < best_dist) { best_dist = dist; best = c; }
    }
    return best;
}

void NUTSEngine::solve_layer(std::vector<TrackSegment*>& segs,
                              const std::map<std::pair<int,int>, double>& pull_map,
                              const AlignMap& align_map,
                              const LayerConstraints& constraints) const {
    if (segs.empty()) return;
    const auto& order_preds = constraints.preds;
    const auto& order_bounds = constraints.bounds;
    // Same-layer lookup for alignment siblings (and ordering-constraint phase 0).
    std::map<std::pair<int,int>, TrackSegment*> layer_map;
    for (TrackSegment* ts : segs)
        layer_map[{ts->bundle_id, ts->seg_idx}] = ts;

    // Segments placed by phase 0 to satisfy corner-ordering edges.  Built up
    // front (before the lambdas) so try_repack can treat them as fixed: a later
    // non-constrained repack must not relocate a constrained trunk and undo the
    // vertical constraint it was placed to enforce.
    std::set<std::pair<int,int>> constrained;
    std::set<std::pair<int,int>> has_successor;   // something ordered above it
    for (const auto& [k, preds] : order_preds) {
        if (layer_map.count(k)) constrained.insert(k);
        for (const auto& p : preds)
            if (layer_map.count(p)) { constrained.insert(p); has_successor.insert(p); }
    }
    for (const auto& [k, b] : order_bounds)
        if (layer_map.count(k)) constrained.insert(k);

    // Incorporate KeepoutZones into 'occupied' list (user zones + leaf-cell
    // zones on LOW layers; TOP segments are filtered out by layer_ids).
    auto kozs = low_keepouts();

    auto add_keepout_occ = [&](const TrackSegment* t,
                               std::vector<std::pair<double,double>>& occ) {
        keepout_occupied(kozs, t, occ);
    };

    // Occupancy a candidate placement must avoid: every already-placed segment
    // of another bundle whose span overlaps ts (same-bundle bits may share
    // tracks), plus keepouts.  Span-overlap rather than a sweep active set, so
    // pull anchors pre-placed in phase 1 are seen when packing phase 2 — and
    // for a single pull-free pass this is equivalent to the old active-set
    // sweep, since in span order an overlapping earlier segment is exactly one
    // that has started and not yet ended.
    auto build_occupied = [&](const TrackSegment* ts,
                              std::vector<std::pair<double,double>>& occ) {
        for (const TrackSegment* o : segs) {
            if (o == ts || !o->placed || o->bundle_id == ts->bundle_id) continue;
            // Closed-span (<=) to match segs_overlap: a span that merely TOUCHES
            // ts is occupancy too, so ts is kept off o's track and the two don't
            // end up collinear (an end-to-end DRC).  Spans that are truly
            // disjoint (gap > 0) still don't block.
            if (sp_lo(*o) <= sp_hi(*ts) && sp_lo(*ts) <= sp_hi(*o)) {
                const double h = o->width / 2.0;
                occ.push_back({o->track_position - h, o->track_position + h});
            }
        }
        add_keepout_occ(ts, occ);
    };

    // Local repack: when no gap fits ts, earlier centre-seeking placements
    // may have fragmented a window that has room for everyone (two 51-wide
    // trunks in a 110-wide shared slide window: the first at the centre
    // leaves two 29.5 slivers).  Re-place ts together with the active
    // segments contending for its interval, packing from the low edge,
    // against everything else already placed.  Commits only on full success.
    auto try_repack = [&](TrackSegment* ts) -> bool {
        // Members: every placed segment contending for ts's window (interval
        // overlap) — including ones whose sweep span already ended.  An ended
        // segment doesn't conflict with ts directly, but it constrains other
        // members through span overlap; keeping it fixed can wedge the window
        // (e.g. planner3 M6: B3.seg1 at 483 forces B2.seg2 past 552, out of
        // its alignment sibling's reach).  Span-based obstacles below keep
        // every re-placement physically valid.
        std::vector<TrackSegment*> members{ts};
        for (TrackSegment* o : segs) {
            if (o == ts || !o->placed) continue;
            // Phase-0 constrained trunks stay fixed (they carry corner-ordering
            // edges); they remain obstacles via the non-member span-overlap loop.
            if (constrained.count({o->bundle_id, o->seg_idx})) continue;
            if (o->interval_lo < ts->interval_hi &&
                ts->interval_lo < o->interval_hi)
                members.push_back(o);
        }
        if (members.size() < 2) return false;
        std::set<const TrackSegment*> member_set(members.begin(), members.end());

        // Earliest deadline first: the member whose window ENDS soonest gets
        // the lowest position.  Ordering by window tightness instead can park
        // a flexible member at the bottom of its window, re-blocking the very
        // member that triggered the repack.  Tie: least slack first.
        std::stable_sort(members.begin(), members.end(),
            [](const TrackSegment* a, const TrackSegment* b) {
                if (a->interval_hi != b->interval_hi)
                    return a->interval_hi < b->interval_hi;
                return (a->interval_hi - a->interval_lo) - a->width
                     < (b->interval_hi - b->interval_lo) - b->width;
            });

        // Pack the members in deadline order.  In pull-aware mode each member
        // seeks its own pull target (pull_map) — so a phase-1 anchor swept into a
        // repack keeps its pull instead of being bottom-edged — while pull-free
        // members pack to the low edge (== first_fit) to leave maximal room.  In
        // dense mode every member first_fits to the low edge.  Returns the packed
        // positions, or empty on any infeasible member.
        auto pack = [&](bool pull_aware)
                    -> std::vector<std::pair<TrackSegment*,double>> {
            std::vector<std::pair<TrackSegment*,double>> repacked;
            for (TrackSegment* m : members) {
                std::vector<std::pair<double,double>> occ;
                add_keepout_occ(m, occ);
                // Placed segments outside the repack set (including ones whose
                // sweep interval already ended) that overlap m's span.
                // Same-bundle segments never conflict (bits may share tracks).
                for (const TrackSegment* o : segs) {
                    if (!o->placed || member_set.count(o)) continue;
                    if (o->bundle_id == m->bundle_id) continue;
                    if (sp_lo(*o) <= sp_hi(*m) && sp_lo(*m) <= sp_hi(*o)) {  // closed: touch = occupied
                        const double h = o->width / 2.0;
                        occ.push_back({o->track_position - h, o->track_position + h});
                    }
                }
                for (const auto& [pm, ppos] : repacked) {
                    if (pm->bundle_id == m->bundle_id) continue;
                    // Members conflict only where their spans overlap — same
                    // physical criterion as the outside-set obstacles above.
                    // Without this, disjoint-span members sharing one window
                    // (e.g. blk-local buses left and right of a cross-chip
                    // trunk, all at the same Hanan band) are packed as if
                    // simultaneous and wedge a window that has room for all.
                    if (!(sp_lo(*pm) <= sp_hi(*m) && sp_lo(*m) <= sp_hi(*pm)))  // closed: touch = occupied
                        continue;
                    const double h = pm->width / 2.0;
                    occ.push_back({ppos - h, ppos + h});
                }
                std::sort(occ.begin(), occ.end());
                double p;
                if (pull_aware) {
                    auto pit = pull_map.find({m->bundle_id, m->seg_idx});
                    double pref = (m->net_pull != 0 && pit != pull_map.end())
                                  ? pit->second           // anchor: keep its pull
                                  : m->interval_lo;        // free: pack low
                    // The pull target is an interval EDGE (interval_hi for an
                    // upward pull), which lies outside preferred_fit's valid centre
                    // range [c_lo, c_hi] and is not one of its baseline candidates —
                    // so without clamping, an upward-pulled member with a clear high
                    // edge falls back to c_lo and is bottom-packed.  Clamp into the
                    // centre range first, exactly as place_seg does.
                    const double half = m->width / 2.0;
                    const double c_lo = m->interval_lo + half;
                    const double c_hi = m->interval_hi - half;
                    if (c_lo <= c_hi) pref = std::clamp(pref, c_lo, c_hi);
                    p = preferred_fit(m->interval_lo, m->interval_hi, m->width, occ, pref);
                } else {
                    p = first_fit(m->interval_lo, m->interval_hi, m->width, occ);
                }
                if (std::isnan(p)) return {};   // this member can't fit: pack failed
                repacked.push_back({m, p});
            }
            return repacked;
        };

        // Honor pulls when the window has room for everyone at their pull; else
        // fall back to the dense low-edge pack (the proven feasibility path) so a
        // tight window still resolves its overlap rather than dropping ts.
        auto repacked = pack(/*pull_aware=*/true);
        if (repacked.empty()) repacked = pack(/*pull_aware=*/false);
        if (repacked.empty()) return false;   // window truly full: keep old state
        for (const auto& [pm, ppos] : repacked) pm->track_position = ppos;
        return true;
    };

    // Place one segment at its preferred track, avoiding current occupancy.
    // lb/ub (optional) are hard bounds on the track CENTER — phase 0 uses them to
    // keep a constrained segment above its predecessors (lb) and/or on one side
    // of a cross-layer split (ub).  pack_low packs to the lowest feasible track
    // (same-layer ordering); otherwise placement seeks `target` if finite
    // (cross-layer: nudge toward the split bound), else the align/pull preference.
    auto place_seg = [&](TrackSegment* ts,
                         double lb = -kInf, double ub = kInf,
                         bool pack_low = false,
                         double target = std::numeric_limits<double>::quiet_NaN()) {
        std::vector<std::pair<double,double>> occupied;
        build_occupied(ts, occupied);
        std::sort(occupied.begin(), occupied.end());

        // Honor lb/ub by tightening the band edges passed to the fit (without
        // mutating the stored hard interval).
        double eff_lo = ts->interval_lo, eff_hi = ts->interval_hi;
        if (lb > -kInf) eff_lo = std::max(eff_lo, lb - ts->width / 2.0);
        if (ub <  kInf) eff_hi = std::min(eff_hi, ub + ts->width / 2.0);
        const double c_lo = eff_lo + ts->width / 2.0;
        const double c_hi = eff_hi - ts->width / 2.0;
        // pack_low → lowest feasible track (bottom-edge / first_fit).  The
        // preferred computation (and its clamp) only matters otherwise — skip it
        // for pack_low, where a lower bound can make c_lo > c_hi and
        // std::clamp(preferred, c_lo, c_hi) would be undefined.
        double pos;
        if (pack_low) {
            pos = first_fit(eff_lo, eff_hi, ts->width, occupied);
        } else {
            auto key = std::make_pair(ts->bundle_id, ts->seg_idx);
            // Cross-layer bounded trunks aim straight at the split-side bound;
            // others seek an alignment sibling, then their pull, then centre.
            double preferred = target;
            if (std::isnan(preferred)) {
                auto ait = align_map.find(key);
                if (ait != align_map.end()) {
                    for (const auto& sk : ait->second) {
                        auto lit = layer_map.find(sk);
                        if (lit == layer_map.end() || !lit->second->placed) continue;
                        double p = lit->second->track_position;
                        if (p >= c_lo && p <= c_hi) { preferred = p; break; }
                    }
                }
            }
            if (std::isnan(preferred)) {
                auto it = pull_map.find(key);
                preferred = (it != pull_map.end())
                            ? it->second
                            : (ts->interval_lo + ts->interval_hi) / 2.0;
            }
            // Guard against an inverted range (bus wider than its interval):
            // std::clamp requires lo <= hi.  preferred_fit returns NaN there
            // anyway, so the value is irrelevant — just avoid the UB.
            if (c_lo <= c_hi) preferred = std::clamp(preferred, c_lo, c_hi);
            pos = preferred_fit(eff_lo, eff_hi, ts->width, occupied, preferred);
        }
        // try_repack re-places members with first_fit over their FULL intervals,
        // ignoring lb/ub — so it must not run for a phase-0 constrained placement
        // (pack_low or any finite bound/target), or it could relocate a bounded
        // trunk to the wrong side of its split.  Only unconstrained phase-1/2
        // placements may repack.
        const bool phase0 = pack_low || lb > -kInf || ub < kInf || !std::isnan(target);
        if (!std::isnan(pos))           ts->track_position = pos;
        else if (!phase0 && try_repack(ts)) { /* repacked */ }
        else {
            // No feasible track (e.g. an ordering lower bound pushed eff_lo past
            // the interval).  Fall back to the midpoint but clamp it into the
            // valid centre range so an infeasible ordering can never create an
            // interval violation — the unsatisfied ordering just leaves the
            // overlap for resolve_corner_overlaps' guard to reject.  Only the
            // pathological bus-wider-than-interval case (empty range) keeps the
            // raw midpoint, preserving prior behavior.
            double fb = (eff_lo + eff_hi) / 2.0;
            const double v_lo = ts->interval_lo + ts->width / 2.0;
            const double v_hi = ts->interval_hi - ts->width / 2.0;
            if (v_lo <= v_hi) fb = std::clamp(fb, v_lo, v_hi);
            ts->track_position = fb;
        }
        ts->placed = true;
    };

    // Phase 0 — ordering-constrained segments (corner-overlap resolution): place
    // each in dependency order (after every segment that must sit below it),
    // clamped just above its placed predecessors.  They become placed anchors
    // the normal anchor/sweep phases then avoid.  (`constrained` built above.)
    if (!constrained.empty()) {
        std::set<std::pair<int,int>> done;
        auto lb_of = [&](const std::pair<int,int>& k, const TrackSegment* ts) {
            double lb = -std::numeric_limits<double>::infinity();
            auto it = order_preds.find(k);
            if (it != order_preds.end())
                for (const auto& p : it->second) {
                    auto lit = layer_map.find(p);
                    if (lit == layer_map.end() || !lit->second->placed) continue;
                    lb = std::max(lb, lit->second->track_position
                                      + lit->second->width / 2.0
                                      + ts->width / 2.0 + track_pitch_);
                }
            return lb;
        };
        // Place one phase-0 segment honoring both its relative-pred lower bound
        // and any fixed cross-layer bounds.  A bounded (cross-layer) trunk is
        // nudged toward its split-side bound via preferred_fit; a relative-pred
        // (same-layer) trunk packs to the bottom edge.
        auto place_phase0 = [&](const std::pair<int,int>& k, TrackSegment* ts) {
            double lb = lb_of(k, ts);
            double ub = kInf;
            double target = std::numeric_limits<double>::quiet_NaN();
            auto bit = order_bounds.find(k);
            const bool bounded = (bit != order_bounds.end());
            if (bounded) {
                lb = std::max(lb, bit->second.first);
                ub = bit->second.second;
                // Aim at the finite (split-facing) bound for minimal movement.
                target = (ub < kInf) ? ub : bit->second.first;
            }
            place_seg(ts, lb, ub, /*pack_low=*/!bounded, target);
        };
        std::vector<std::pair<int,int>> todo(constrained.begin(), constrained.end());
        bool progress = true;
        while (!todo.empty() && progress) {
            progress = false;
            for (auto it = todo.begin(); it != todo.end();) {
                auto pit = order_preds.find(*it);
                bool ready = true;
                if (pit != order_preds.end())
                    for (const auto& p : pit->second)
                        if (constrained.count(p) && !done.count(p)) { ready = false; break; }
                if (!ready) { ++it; continue; }
                place_phase0(*it, layer_map[*it]);
                done.insert(*it);
                it = todo.erase(it);
                progress = true;
            }
        }
        // Cycle fallback: place whatever's left with its already-placed preds.
        for (const auto& k : todo) {
            place_phase0(k, layer_map[k]);
            done.insert(k);
        }

        // Cluster pull.  Bottom-edge packing honors a downward/zero net_pull but
        // not an upward one.  If the ordered cluster pulls net upward (e.g. a
        // dogleg whose two trunk pieces both inherit an up-pull), translate the
        // WHOLE cluster up rigidly — preserving every ordering and gap (so a
        // dogleg's jog length is unchanged) while shortening the up-pulled
        // stubs.  The shift is bounded by each member's own interval; the
        // members move together so they don't bound each other, and phase-0
        // anchors are avoided by the later phases.
        int pull_sum = 0;
        for (const auto& k : done) pull_sum += layer_map[k]->net_pull;
        if (pull_sum > 0) {
            double headroom = kInf;
            for (const auto& k : done) {
                const TrackSegment* ts = layer_map[k];
                // Cap the rigid shift by each member's own hard interval AND its
                // finite phase-0 upper bound: when solve_layer runs under
                // resolve_corner_overlaps' cross-layer constraints, order_bounds
                // pins a bounded trunk to a split side, and the cluster must not
                // ride past it (that would undo the corner split the pass relies on).
                double cap = ts->interval_hi - ts->width / 2.0;
                auto bit = order_bounds.find(k);
                if (bit != order_bounds.end() && bit->second.second < kInf)
                    cap = std::min(cap, bit->second.second);
                headroom = std::min(headroom, cap - ts->track_position);
            }
            if (headroom > 0)
                for (const auto& k : done) layer_map[k]->track_position += headroom;
        }
    }

    // Phase 1 — anchor pulled segments first at their preferred (pull-extreme)
    // positions, strongest pull then tightest interval first, so the pull-free
    // segments fill the residual gaps in phase 2.  A pull-free trunk then slides
    // off any track a pulled bus needs, instead of grabbing it by sweep order
    // and forcing the pulled bus to detour (item A: planner6 Bundle 3/4).
    std::vector<TrackSegment*> pulled, free_segs;
    for (TrackSegment* ts : segs) {
        if (constrained.count({ts->bundle_id, ts->seg_idx})) continue;  // placed in phase 0
        (ts->net_pull != 0 ? pulled : free_segs).push_back(ts);
    }
    std::stable_sort(pulled.begin(), pulled.end(),
        [](const TrackSegment* a, const TrackSegment* b) {
            int pa = std::abs(a->net_pull), pb = std::abs(b->net_pull);
            if (pa != pb) return pa > pb;                       // strongest pull first
            return (a->interval_hi - a->interval_lo)
                 < (b->interval_hi - b->interval_lo);           // tightest window first
        });
    for (TrackSegment* ts : pulled) place_seg(ts);

    // Phase 2 — sweep the pull-free segments in span order; each sees the
    // anchors and earlier free segments via build_occupied and slides to the
    // nearest free track.
    std::stable_sort(free_segs.begin(), free_segs.end(),
        [](const TrackSegment* a, const TrackSegment* b) {
            return sp_lo(*a) < sp_lo(*b);   // ordered: span_lo may be reversed
        });
    for (TrackSegment* ts : free_segs) place_seg(ts);
}

void NUTSEngine::set_extra_grid_points(std::vector<int> xs, std::vector<int> ys) {
    std::sort(xs.begin(), xs.end());
    xs.erase(std::unique(xs.begin(), xs.end()), xs.end());
    std::sort(ys.begin(), ys.end());
    ys.erase(std::unique(ys.begin(), ys.end()), ys.end());
    extra_x_ = std::move(xs);
    extra_y_ = std::move(ys);
}

void NUTSEngine::orientation_fixpoint(
    std::vector<TrackSegment>& segments,
    std::map<int, std::vector<TrackSegment*>>& by_layer,
    const std::map<std::pair<int,int>, double>& pull_map,
    const AlignMap& align_map,
    const std::map<std::pair<int,int>, std::vector<SpanAdjConn>>& rev_conn_map,
    std::map<std::pair<int,int>, TrackSegment*>& ts_ptr_map,
    const std::map<int, LayerConstraints>& seed_cons) const
{
    if (by_layer.empty()) return;
    auto cons_for = [&](int lid) -> const LayerConstraints& {
        static const LayerConstraints kEmpty;
        auto it = seed_cons.find(lid);
        return it != seed_cons.end() ? it->second : kEmpty;
    };

    // Partition populated layers into the two orientation groups; lead with the
    // orientation of the lowest TOP layer (default rule), falling back to the
    // lowest populated layer when no TOP layer is present.
    std::vector<int> h_all = layers_.get_layer_ids_by_dir(LayerDir::HORIZONTAL);
    std::set<int>    h_set(h_all.begin(), h_all.end());
    std::vector<int> h_group, v_group;
    for (auto& [lid, segs] : by_layer) {
        (void)segs;
        if (h_set.count(lid)) h_group.push_back(lid);
        else                  v_group.push_back(lid);
    }
    int  lead_lid  = std::numeric_limits<int>::max();
    bool found_top = false;
    for (auto& [lid, segs] : by_layer) {
        (void)segs;
        const bool top = layers_.is_top(lid);
        if (top && !found_top)                       { found_top = true; lead_lid = lid; }
        else if (top == found_top && lid < lead_lid) { lead_lid = lid; }
    }
    const bool lead_is_h = h_set.count(lead_lid) > 0;
    const std::vector<int>& lead_group = lead_is_h ? h_group : v_group;
    const std::vector<int>& perp_group = lead_is_h ? v_group : h_group;

    // Solve every layer in a group (reset first — GLOBAL re-solve), then
    // propagate the freshly-pinned trunk positions to all connected spans so the
    // perpendicular group packs against true (already-stretched) extents.
    auto solve_group = [&](const std::vector<int>& group) {
        for (int lid : group) {
            for (auto* ts : by_layer[lid]) {
                ts->track_position = std::numeric_limits<double>::quiet_NaN();
                ts->placed = false;
            }
            solve_layer(by_layer[lid], pull_map, align_map, cons_for(lid));
        }
        std::vector<TrackSegment*> all_placed;
        for (auto& ts : segments) if (ts.placed) all_placed.push_back(&ts);
        do_span_adjustments(all_placed, rev_conn_map, ts_ptr_map);
    };

    struct Snap { double pos, lo, hi; bool placed; };
    auto take = [&]() {
        std::vector<Snap> s; s.reserve(segments.size());
        for (const auto& ts : segments)
            s.push_back({ts.track_position, ts.span_lo, ts.span_hi, ts.placed});
        return s;
    };
    auto restore = [&](const std::vector<Snap>& s) {
        for (size_t k = 0; k < segments.size(); ++k) {
            segments[k].track_position = s[k].pos;
            segments[k].span_lo        = s[k].lo;
            segments[k].span_hi        = s[k].hi;
            segments[k].placed         = s[k].placed;
        }
    };
    // Legacy per-layer order (ascending id, per-layer span adjustment, then a
    // final all-layer pass) — today's solve, honouring any seed constraints.
    auto legacy_solve = [&]() {
        for (auto& [lid, segs] : by_layer) {
            for (auto* ts : segs) {
                ts->track_position = std::numeric_limits<double>::quiet_NaN();
                ts->placed = false;
            }
            solve_layer(segs, pull_map, align_map, cons_for(lid));
            do_span_adjustments(segs, rev_conn_map, ts_ptr_map);
        }
        for (auto& [lid, segs] : by_layer) {
            (void)lid;
            do_span_adjustments(segs, rev_conn_map, ts_ptr_map);
        }
    };
    auto state_hash = [&]() {
        size_t h = 0;
        auto mix = [&](long long v) {
            h ^= std::hash<long long>{}(v) + 0x9e3779b97f4a7c15ULL + (h << 6) + (h >> 2);
        };
        const long long unp = std::numeric_limits<long long>::min();
        for (const auto& ts : segments) {
            mix(ts.placed ? std::llround(ts.track_position) : unp);
            mix(std::llround(ts.span_lo));
            mix(std::llround(ts.span_hi));
        }
        return h;
    };
    // Distance of placed segments from their planner-reserved band (pull_map): a
    // proxy for detailed-NUTS feasibility — the planner sized those bands for the
    // bits, so straying risks a signal-track shortfall downstream.
    auto pull_deviation = [&]() {
        double dev = 0.0;
        for (const auto& ts : segments) {
            if (!ts.placed) continue;
            auto it = pull_map.find({ts.bundle_id, ts.seg_idx});
            if (it != pull_map.end()) dev += std::abs(ts.track_position - it->second);
        }
        return dev;
    };

    // Seed with the legacy order, then refine with alternating-group sweeps.
    // Each sweep is a deterministic function of the current spans, so the
    // trajectory reaches a fixed point or cycles — both caught by the repeated-
    // state set.  Adopt a sweep only when it STRICTLY reduces overlaps WITHOUT
    // pushing buses further from their reserved bands than legacy did: raw
    // overlap count alone is unfair (the repair/resolve passes run after this and
    // clean up legacy's residue), and a sweep that "wins" by evicting a bus from
    // its band only trades an abstract overlap for a detailed signal-track
    // shortfall.  Single orientation present ⇒ no alternation, just the seed.
    legacy_solve();
    size_t            best_ov    = find_overlaps(segments).size();
    const double      legacy_dev = pull_deviation();
    std::vector<Snap> best_snap  = take();
    std::set<size_t>  seen{state_hash()};

    if (!lead_group.empty() && !perp_group.empty()) {
        const int kMaxIters = 12;
        for (int iter = 0; iter < kMaxIters && best_ov > 0; ++iter) {
            solve_group(lead_group);
            solve_group(perp_group);
            const size_t n = find_overlaps(segments).size();
            const double d = pull_deviation();
            if (n < best_ov && d <= legacy_dev + 1e-6) { best_ov = n; best_snap = take(); }
            if (!seen.insert(state_hash()).second) break;   // repeated state — converged/cycle
        }
    }
    restore(best_snap);
}

// A fully-specified plan to break a vertical-constraint cycle by doglegging one
// trunk: split it at a column between col1 and col2 so its two pieces straddle
// their respective neighbour trunks (high1 at col1 vs neighbor1, high2 at col2 vs
// neighbor2).  high1 != high2 — that contradiction is why no single track for the
// trunk works.  For a 2-cycle neighbor1 == neighbor2; for a longer cycle they
// differ (each piece orders against a different trunk on the cycle).
struct CycleEdge { std::pair<int,int> from, to; double col; };   // "from below to" at col
struct DoglegPlan {
    std::pair<int,int> split_trunk;
    int    layer;
    double col1; bool high1; std::pair<int,int> neighbor1;
    double col2; bool high2; std::pair<int,int> neighbor2;
    // The full cycle's ordering edges.  Seeding ALL of them (with the split
    // trunk redirected to its covering piece) imposes the complete vertical
    // order — not just the split trunk's two constraints — so the trunks the
    // split does not touch (their mutual edge) are ordered too.
    std::vector<CycleEdge> cycle_edges;
};

// Build the same-layer vertical-constraint graph from co-located stub pairs and
// return, for the FIRST directed cycle found, one plan per trunk on the cycle
// (so the caller can split whichever is cheapest).  The graph is built from
// geometry, not from a single placement — a true cycle only ever exposes one
// contradictory column at a time, so the structural view is necessary.
static std::vector<DoglegPlan> detect_dogleg_plans(
    const std::vector<TrackSegment>& segments,
    const std::map<std::pair<int,int>, std::vector<SpanAdjConn>>& rev_conn_map,
    const std::set<std::pair<int,int>>& trunk_set)
{
    using Key = std::pair<int,int>;
    // rev_conn_map records connectivity BOTH ways (a trunk's endpoint also
    // "follows" its end stub), so a trunk can appear as a follower of its own
    // stub.  Only genuine stubs (not trunks) define columns where a corner
    // overlap can occur, so skip any follower that is itself a trunk.
    std::map<Key, std::pair<Key,bool>> trunk_of;
    for (const auto& [tkey, conns] : rev_conn_map)
        for (const auto& sc : conns) {
            const Key fkey{sc.src_bid, sc.src_si};
            if (trunk_set.count(fkey)) continue;       // a trunk is not a stub
            trunk_of[fkey] = {tkey, sc.lo_end};
        }
    std::map<Key,int> idx_of;
    for (int i = 0; i < (int)segments.size(); ++i)
        idx_of[{segments[i].bundle_id, segments[i].seg_idx}] = i;
    // The far (block-side) end of a stub, fixed by the floorplan: the trunk whose
    // stub reaches LOWER must take the lower track.
    auto anchored_coord = [](const TrackSegment& s, bool lo_end) {
        return lo_end ? s.span_hi : s.span_lo;
    };

    // Co-locate stubs by their Hanan INTERVAL (the column they are constrained
    // to), not by nominal or placed position: the interval is placement-
    // independent, so it catches two wide (multi-bit) stubs that share a narrow
    // column even when NUTS shifted them to different tracks (and it doesn't
    // vanish once NUTS separates a conflicting pair).  The vertical constraint
    // then follows from which stub reaches farther (anchored end).
    struct Stub { Key key, trunk; double ilo, ihi, center, anchored; int trunk_layer; };
    std::vector<Stub> stubs;
    for (const auto& [k, tinfo] : trunk_of) {
        auto sit = idx_of.find(k);
        auto tit = idx_of.find(tinfo.first);
        if (sit == idx_of.end() || tit == idx_of.end()) continue;
        const TrackSegment& s = segments[sit->second];
        stubs.push_back({k, tinfo.first, s.interval_lo, s.interval_hi,
                         0.5 * (s.interval_lo + s.interval_hi),
                         anchored_coord(s, tinfo.second), segments[tit->second].layer});
    }

    // Directed edge lo_trunk → hi_trunk ("lo below hi") at a column, from every
    // co-located, distinct-bundle stub pair whose trunks share a layer.
    constexpr double kColTol = 2.0;
    std::map<Key, std::map<Key,double>> adj;   // from → {to → column}
    for (size_t a = 0; a < stubs.size(); ++a)
        for (size_t b = a + 1; b < stubs.size(); ++b) {
            if (stubs[a].key.first == stubs[b].key.first) continue;   // same bundle
            if (stubs[a].trunk == stubs[b].trunk) continue;
            if (stubs[a].trunk_layer != stubs[b].trunk_layer) continue;
            // Co-located: their Hanan intervals overlap (same column).
            if (stubs[a].ihi <= stubs[b].ilo || stubs[b].ihi <= stubs[a].ilo) continue;
            const bool a_lower = stubs[a].anchored < stubs[b].anchored;
            Key lo = a_lower ? stubs[a].trunk : stubs[b].trunk;
            Key hi = a_lower ? stubs[b].trunk : stubs[a].trunk;
            adj[lo].emplace(hi, 0.5 * (stubs[a].center + stubs[b].center));  // keep first column
        }

    // DFS for one directed cycle; record it as the node sequence v→…→u (with the
    // back edge u→v closing it).
    std::map<Key,int> color;            // 0 white, 1 gray, 2 black
    std::map<Key,Key> parent;
    std::vector<Key> cyc;
    std::function<bool(const Key&)> dfs = [&](const Key& u) -> bool {
        color[u] = 1;
        for (const auto& [v, col] : adj[u]) {
            (void)col;
            if (color[v] == 1) {            // back edge → cycle v…u
                std::vector<Key> rev;
                for (Key cur = u; cur != v; cur = parent[cur]) rev.push_back(cur);
                cyc.push_back(v);
                for (auto it = rev.rbegin(); it != rev.rend(); ++it) cyc.push_back(*it);
                return true;
            }
            if (color[v] == 0) { parent[v] = u; if (dfs(v)) return true; }
        }
        color[u] = 2;
        return false;
    };
    for (const auto& [n, _] : adj) {
        (void)_;
        if (color[n] == 0 && dfs(n)) break;
    }
    if (cyc.size() < 2) return {};
    const int n = (int)cyc.size();

    // The cycle's directed ordering edges (nodes[i] below nodes[i+1] at its col).
    std::vector<CycleEdge> cycle_edges;
    for (int i = 0; i < n; ++i)
        cycle_edges.push_back({cyc[i], cyc[(i + 1) % n], adj[cyc[i]][cyc[(i + 1) % n]]});

    // One plan per trunk on the cycle: its incoming edge (prev below it → it must
    // be HIGH there) and outgoing edge (it below next → LOW there) give the two
    // contradictory columns and the two neighbour trunks.
    std::vector<DoglegPlan> plans;
    for (int i = 0; i < n; ++i) {
        const Key& ti   = cyc[i];
        const Key& prev = cyc[(i - 1 + n) % n];
        const Key& next = cyc[(i + 1) % n];
        const double col_in  = adj[prev][ti];   // prev → ti
        const double col_out = adj[ti][next];   // ti → next
        if (std::abs(col_in - col_out) <= kColTol) continue;  // can't dogleg at one column
        DoglegPlan p;
        p.split_trunk = ti;
        p.layer = segments[idx_of[ti]].layer;
        p.col1 = col_in;  p.high1 = true;  p.neighbor1 = prev;  // ti above prev at col_in
        p.col2 = col_out; p.high2 = false; p.neighbor2 = next;  // ti below next at col_out
        p.cycle_edges = cycle_edges;
        plans.push_back(p);
    }
    return plans;
}

// Split one trunk of a 2-cycle into two collinear pieces on different tracks,
// joined by a perpendicular jog (the BITRUNK_H shape), so the two pieces become
// INDEPENDENT trunks: each can be ordered against the other bundle's trunk on
// its own, breaking the cycle.  The trunk's two stubs are extended to meet their
// piece and the jog bridges the pieces; seg_layers is extended so the new
// segments keep the trunk/stub layers.  Mutates the selected Topology in place;
// returns the jog's new segment index, or -1 if the split is unsupported.
//
// (col1,high1)/(col2,high2) give, for THIS bundle's trunk, the two conflicting
// columns and whether it must take the higher track there.  high1 != high2 — that
// contradiction is the cycle, and it sets which piece jogs up vs down.
struct DoglegResult {
    bool ok = false;
    int  jog_si = -1;
    int  piece_l_si = -1;     // rewritten trunk segment, covers x <= jog_x
    int  piece_r_si = -1;     // appended segment, covers x >  jog_x
    int  jog_x = 0;           // split column: stubs left of it hang from piece_l, right from piece_r
    bool piece_l_high = false;
};
static DoglegResult apply_dogleg(BundleWrapper& bw, int trunk_si,
                                 double col1, bool high1, double col2, bool high2,
                                 int delta, const std::vector<int>& orig_net_pull,
                                 double orig_slide_lo, double orig_slide_hi)
{
    if (bw.plan.selected_topology_index < 0) return {};
    Topology& topo = bw.input.candidates[bw.plan.selected_topology_index];
    if (trunk_si < 0 || trunk_si >= (int)topo.segments.size()) return {};
    const Segment trunk = topo.segments[trunk_si];
    if (trunk.start.y != trunk.end.y) return {};   // only horizontal trunks for now
    const int y_t  = trunk.start.y;
    const int x_lo = std::min(trunk.start.x, trunk.end.x);
    const int x_hi = std::max(trunk.start.x, trunk.end.x);

    // Order the columns left/right and carry their required high-sides along.
    const bool col1_left = (col1 <= col2);
    const double colL = col1_left ? col1 : col2;
    const double colR = col1_left ? col2 : col1;
    const bool highL = col1_left ? high1 : high2;
    const bool highR = col1_left ? high2 : high1;
    if (x_hi - x_lo < 2) return {};
    int jog_x = (int)std::llround(0.5 * (colL + colR));
    jog_x = std::clamp(jog_x, x_lo + 1, x_hi - 1);

    const int yL = y_t + (highL ? delta : -delta);
    const int yR = y_t + (highR ? delta : -delta);
    // The two pieces must land on DIFFERENT tracks or the split separates nothing.
    // detect_dogleg_plans always emits high1=true/high2=false, so highL != highR and
    // yL != yR; guard anyway so a future change to that convention can't silently
    // collapse the pieces onto one track.
    if (yL == yR) return {};

    int h_layer = trunk.layer_hint;
    if (trunk_si < (int)bw.plan.seg_layers.size() && bw.plan.seg_layers[trunk_si] >= 0)
        h_layer = bw.plan.seg_layers[trunk_si];
    int v_layer = -1;                          // jog rides a perpendicular (stub) layer
    for (int si = 0; si < (int)topo.segments.size(); ++si) {
        const Segment& s = topo.segments[si];
        if (s.start.x == s.end.x) {            // a vertical stub
            v_layer = (si < (int)bw.plan.seg_layers.size() && bw.plan.seg_layers[si] >= 0)
                          ? bw.plan.seg_layers[si] : s.layer_hint;
            break;
        }
    }
    if (v_layer < 0) return {};

    // Rewrite the trunk as the left piece; append the right piece and the jog.
    // The jog is marked so it is exempt from sibling alignment.
    topo.segments[trunk_si] = Segment{ Point{x_lo, yL}, Point{jog_x, yL}, h_layer };
    const int piece_r_idx = (int)topo.segments.size();
    topo.segments.push_back(Segment{ Point{jog_x, yR}, Point{x_hi, yR}, h_layer });
    const int jog_idx = (int)topo.segments.size();
    Segment jog{ Point{jog_x, yL}, Point{jog_x, yR}, v_layer };
    jog.is_jog = true;
    topo.segments.push_back(jog);

    auto set_layer = [&](int idx, int lid) {
        if ((int)bw.plan.seg_layers.size() <= idx) bw.plan.seg_layers.resize(idx + 1, -1);
        bw.plan.seg_layers[idx] = lid;
    };
    set_layer(trunk_si, h_layer);
    set_layer(piece_r_idx, h_layer);
    set_layer(jog_idx, v_layer);

    // Pin net_pull (ConnTopology would recompute the split bundle's pulls wrongly):
    // stubs keep their pre-split value, both sub-trunks inherit the trunk's, the
    // jog is net-zero.  Sliding a sub-trunk toward a face only stretches the jog
    // as much as it shortens the stub (net-zero wirelength), so the trunk pull is
    // the right thing for both pieces.
    const int INT_MIN_ = std::numeric_limits<int>::min();
    const int trunk_pull = (trunk_si < (int)orig_net_pull.size()) ? orig_net_pull[trunk_si] : 0;
    auto& snp = bw.plan.seg_net_pull;
    snp.assign(topo.segments.size(), INT_MIN_);
    for (int i = 0; i < (int)orig_net_pull.size() && i < (int)snp.size(); ++i)
        if (i != trunk_si) snp[i] = orig_net_pull[i];   // stubs preserve their pull
    snp[trunk_si]    = trunk_pull;                       // left piece inherits trunk
    snp[piece_r_idx] = trunk_pull;                       // right piece inherits trunk
    snp[jog_idx]     = 0;                                // jog is net-zero

    // Pin slide windows: each sub-trunk inherits the ORIGINAL trunk's slide
    // range (ConnTopology, seeing only a subset of stubs per piece, would give a
    // narrower one), and the jog is clamped to the trunk's stub extent [x_lo,
    // x_hi] so it cannot slide beyond any stub/busterm the trunk connected to.
    const double kNaN = std::numeric_limits<double>::quiet_NaN();
    auto& slo = bw.plan.seg_slide_lo;
    auto& shi = bw.plan.seg_slide_hi;
    slo.assign(topo.segments.size(), kNaN);
    shi.assign(topo.segments.size(), kNaN);
    slo[trunk_si]    = orig_slide_lo;  shi[trunk_si]    = orig_slide_hi;  // left piece
    slo[piece_r_idx] = orig_slide_lo;  shi[piece_r_idx] = orig_slide_hi;  // right piece
    slo[jog_idx]     = x_lo;           shi[jog_idx]     = x_hi;           // jog footprint

    // Clear the rewritten piece's stale planner band: seg_perp[trunk_si] still
    // names the ORIGINAL trunk's charged band (≈ the old single track), which
    // build_nuts_maps would prefer over the new high/low nominal and undo the
    // split.  INT_MIN ⇒ fall back to the segment's own (jogged) nominal.  The
    // appended pieces have no seg_perp entry, so they already use their nominal.
    if (trunk_si < (int)bw.plan.seg_perp.size())
        bw.plan.seg_perp[trunk_si] = std::numeric_limits<int>::min();

    // Extend each of the trunk's stubs (a vertical segment with an endpoint at
    // the old trunk y) up/down to meet whichever piece now covers its column —
    // left piece (yL) if it sits left of the jog, right piece (yR) otherwise —
    // so the nominal topology stays connected (ConnTopology infers the junctions
    // geometrically).  (void)colL/colR: ordering is by jog_x, not exact column.
    (void)colL; (void)colR;
    for (auto& s : topo.segments) {
        if (s.start.x != s.end.x) continue;                 // only vertical stubs
        if (s.is_jog) continue;                             // skip the jog we appended
        // Skip by the is_jog flag, NOT by x==jog_x: an ORIGINAL stub may also sit
        // at the rounded jog column (multicast/odd-grid).  Such a stub still has an
        // endpoint at y_t and must be extended to yL like any left-of-jog stub, so
        // it touches the left piece's endpoint (jog_x, yL) and stays connected; the
        // jog itself (endpoints yL/yR, never y_t) would be untouched regardless.
        const int sx = s.start.x;
        const int new_y = (sx <= jog_x) ? yL : yR;
        if (s.start.y == y_t)      s.start.y = new_y;
        else if (s.end.y == y_t)   s.end.y   = new_y;
    }
    // piece_l_high reports which piece sits on the higher track, derived from the
    // EMITTED geometry (yL vs yR) — the single source of truth — rather than highL,
    // so the no-swap seed edge can never disagree with the tracks actually placed.
    return DoglegResult{ true, jog_idx, trunk_si, piece_r_idx, jog_x, (yL > yR) };
}

NUTSResult NUTSEngine::run(const std::vector<BundleWrapper>& bundles_in) {
    std::vector<int> x_grid, y_grid;
    floorplan_.get_hanan_grid(x_grid, y_grid);
    merge_grid(x_grid, extra_x_);
    merge_grid(y_grid, extra_y_);

    // One full placement of a (possibly dogleg-mutated) bundle set: extract
    // segments, build maps, run the orientation fixpoint, classify cycles, then
    // the repair/corner safety net.  Returned so the dogleg pass can re-place.
    struct SolveOut { NUTSResult result; std::vector<DoglegPlan> plans; };
    auto solve = [&](const std::vector<BundleWrapper>& bs,
                     const std::map<int, LayerConstraints>& seed_cons) -> SolveOut {
        NUTSResult result;
        result.segments = extract_segments(bs, x_grid, y_grid);
        std::map<std::pair<int,int>, double>                         pull_map;
        std::map<std::pair<int,int>, std::pair<double,double>>       slide_map;
        std::set<std::pair<int,int>>                                 trunk_set;
        std::set<std::pair<int,int>>                                 busterm_set;
        std::map<std::pair<int,int>, std::vector<SpanAdjConn>>       rev_conn_map;
        std::map<std::pair<int,int>, int>                            net_pull_map;
        AlignMap                                                     align_map;
        build_nuts_maps(bs, floorplan_, pull_map, slide_map, trunk_set, busterm_set, rev_conn_map, net_pull_map, align_map);
        apply_interval_constraints(result.segments, slide_map, trunk_set, net_pull_map, -1);
        relax_boundary_intervals(result.segments, pull_map, net_pull_map, busterm_set);
        std::map<std::pair<int,int>, TrackSegment*> ts_ptr_map;
        for (auto& ts : result.segments)
            ts_ptr_map[{ts.bundle_id, ts.seg_idx}] = &ts;
        std::map<int, std::vector<TrackSegment*>> by_layer;
        for (auto& ts : result.segments)
            by_layer[ts.layer].push_back(&ts);
        // Alternating orientation-group fixpoint: solve a whole orientation
        // group, propagate spans to the perpendicular group, solve it, propagate
        // back, and iterate — so each group packs against the other's already-
        // stretched spans instead of stale ones.  Replaces the per-layer loop.
        orientation_fixpoint(result.segments, by_layer, pull_map, align_map,
                             rev_conn_map, ts_ptr_map, seed_cons);
        // Classify any genuinely cyclic vertical constraint NOW, on the raw
        // post-fixpoint overlaps: a 2-cycle shows both contradictory column
        // overlaps at once.  The corner pass below would half-resolve it —
        // fixing one column and leaving a single residual — which hides the
        // mutual-edge structure.  The 2-cycle filter guarantees the safety-net
        // passes could not have fixed these anyway.
        SolveOut out;
        out.plans = detect_dogleg_plans(result.segments, rev_conn_map, trunk_set);
        // The final adjustments can extend spans of layers packed earlier,
        // materialising overlaps after their solve — repair them in place.
        repair_overlaps(result.segments, pull_map, net_pull_map, align_map,
                        rev_conn_map, ts_ptr_map);
        // Corner overlaps (perp-locked stubs colliding) need trunk adjustment,
        // not victim moves — resolve via same-layer ordering or cross-layer
        // split bounds.
        resolve_corner_overlaps(result.segments, pull_map, net_pull_map, align_map,
                                rev_conn_map, ts_ptr_map);
        // Final opportunistic tighten: slide pulled segments toward their pull in
        // the settled layout (the sweep/repack only ever placed them by local
        // decisions and never revisited them when space opened next to the pull).
        tighten_pulls(result.segments, net_pull_map, rev_conn_map, ts_ptr_map);
        compute_metrics(result);
        out.result = std::move(result);
        return out;
    };

    std::vector<BundleWrapper> bundles = bundles_in;   // mutable: doglegs edit topologies
    SolveOut out = solve(bundles, {});

    // Dogleg fallback: a genuine vertical-constraint cycle survives the corner
    // pass.  Split one trunk on the cycle across two tracks (joined by a jog) so
    // its two pieces become INDEPENDENT trunks that straddle their neighbours —
    // one piece above the neighbour at one column, one below the neighbour at the
    // other — breaking the cycle.  We seed that straddle ordering directly as a
    // same-layer constraint, since the corner pass would only discover one edge
    // at a time and revert.  Each detected cycle yields one plan per trunk on it;
    // try each and keep the cheapest (fewer overlaps, then shorter jog).
    //
    // Gate on a SMALL residual: the dogleg cleans up the few genuinely cyclic
    // overlaps the corner pass can't, not heavy congestion.  When many overlaps
    // remain the placement is still settling (e.g. an intermediate run_nuts a
    // later post_nuts / re-pitch pass will resolve); doglegging there only
    // perturbs a flow that otherwise converges to zero.
    const int kMaxDoglegs  = 8;
    const int kMaxResidual = 4;
    std::set<int> doglegged_bids;   // bundles whose topology the dogleg mutated
    for (int dl = 0; dl < kMaxDoglegs && !out.plans.empty()
                     && out.result.num_overlaps <= kMaxResidual; ++dl) {
        const std::vector<DoglegPlan> plans = out.plans;
        auto find_bw = [&](int bid) -> int {
            for (int i = 0; i < (int)bundles.size(); ++i)
                if (bundles[i].input.original_bundle.id == bid) return i;
            return -1;
        };
        bool applied = false;
        std::vector<BundleWrapper> best_bundles;
        SolveOut                   best_out;
        double                     best_jog  = std::numeric_limits<double>::max();
        double                     best_span = -1.0;
        int                        best_bid  = -1;
        for (const DoglegPlan& p : plans) {
            int bw_idx = find_bw(p.split_trunk.first);
            if (bw_idx < 0) continue;
            // Don't split a bundle twice: apply_dogleg re-assigns (overwrites) the
            // whole seg_net_pull / seg_slide_* arrays, which would wipe an earlier
            // iteration's pins for this bundle.  A second cycle through it is left to
            // a later iteration on a different bundle, or to the BEST_EFFORT residual.
            if (doglegged_bids.count(p.split_trunk.first)) continue;
            // Trunk geometry: its slide window (interval) must hold two sub-trunks,
            // and a longer span gives the jog more room to slide — so among the
            // cycle's trunks we prefer the one with the longest span (tie-broken on
            // a shorter jog), skipping any whose slide window is too narrow.
            double trunk_w = 1.0, trunk_span = 0.0, trunk_slide = 0.0;
            double trunk_slide_lo = 0.0, trunk_slide_hi = 0.0;
            for (const auto& ts : out.result.segments)
                if (ts.bundle_id == p.split_trunk.first && ts.seg_idx == p.split_trunk.second) {
                    trunk_w        = ts.width;
                    trunk_span     = sp_hi(ts) - sp_lo(ts);   // ordered length
                    trunk_slide_lo = ts.interval_lo;
                    trunk_slide_hi = ts.interval_hi;
                    trunk_slide    = ts.interval_hi - ts.interval_lo;
                }
            if (trunk_slide < 2.0 * trunk_w + 2.0 * track_pitch_) continue;  // can't host two pieces
            // Capture this bundle's pre-split net_pull per seg_idx, so apply_dogleg
            // can pin stubs (preserve) and sub-trunks (inherit the trunk).
            std::vector<int> orig_net_pull;
            for (const auto& ts : out.result.segments)
                if (ts.bundle_id == p.split_trunk.first) {
                    if (ts.seg_idx >= (int)orig_net_pull.size())
                        orig_net_pull.resize(ts.seg_idx + 1, 0);
                    orig_net_pull[ts.seg_idx] = ts.net_pull;
                }
            // Seed the jog tall enough that the pieces clear the neighbour trunks
            // between them: separation ≳ bus width + pitch each side.
            const int delta = (int)std::ceil(trunk_w + track_pitch_ + 2.0);
            std::vector<BundleWrapper> trial = bundles;
            DoglegResult dr = apply_dogleg(trial[bw_idx], p.split_trunk.second,
                                           p.col1, p.high1, p.col2, p.high2, delta,
                                           orig_net_pull, trunk_slide_lo, trunk_slide_hi);
            if (!dr.ok) continue;

            // Seed the FULL cycle ordering (preds[X] = segments below X), with
            // the split trunk redirected to whichever piece covers each edge's
            // column (left of the jog → piece_l, right → piece_r).  This imposes
            // the complete vertical order, including the edge between the two
            // trunks the split does not touch, which the corner pass can't.
            const std::pair<int,int> piece_l{p.split_trunk.first, dr.piece_l_si};
            const std::pair<int,int> piece_r{p.split_trunk.first, dr.piece_r_si};
            auto redirect = [&](const std::pair<int,int>& node, double col) {
                if (node != p.split_trunk) return node;
                return (col <= dr.jog_x) ? piece_l : piece_r;
            };
            // The split only breaks the cycle if the trunk's two contradictory edges
            // (col1/col2) land on DIFFERENT pieces.  They are guaranteed > kColTol
            // apart and jog_x is their midpoint, so this normally holds; reject the
            // plan if it doesn't (an N>=3 cycle with both edges on one side of the
            // jog, or a column near jog_x) rather than seed a cycle-preserving order.
            if (redirect(p.split_trunk, p.col1) == redirect(p.split_trunk, p.col2))
                continue;
            std::map<int, LayerConstraints> seed;
            for (const CycleEdge& e : p.cycle_edges) {
                const auto a = redirect(e.from, e.col);   // a below b
                const auto b = redirect(e.to,   e.col);
                if (a != b) seed[p.layer].preds[b].insert(a);
            }
            // Pin the two sub-trunks' relative order so they can never swap: the
            // high piece must sit above the low piece.  They don't overlap in the
            // routing direction (they only touch at the jog), so nothing else
            // enforces this — one explicit edge keeps the jog from inverting.
            seed[p.layer].preds[dr.piece_l_high ? piece_l : piece_r]
                         .insert(dr.piece_l_high ? piece_r : piece_l);
            SolveOut t = solve(trial, seed);

            // Jog length of the placed jog segment (tie-break; shorter is cheaper).
            double jog_len = std::numeric_limits<double>::max();
            for (const auto& ts : t.result.segments)
                if (ts.bundle_id == p.split_trunk.first && ts.seg_idx == dr.jog_si)
                    jog_len = sp_hi(ts) - sp_lo(ts);   // ordered length
            // Prefer: fewer overlaps, then the LONGER trunk (more jog room), then
            // the shorter jog.
            const int ov = t.result.num_overlaps;
            const bool better =
                !applied ||
                ov <  best_out.result.num_overlaps ||
                (ov == best_out.result.num_overlaps && trunk_span > best_span + 1e-6) ||
                (ov == best_out.result.num_overlaps && std::abs(trunk_span - best_span) <= 1e-6
                                                    && jog_len < best_jog);
            if (better) {
                applied      = true;
                best_bundles = std::move(trial);
                best_out     = std::move(t);
                best_jog     = jog_len;
                best_span    = trunk_span;
                best_bid     = p.split_trunk.first;
            }
        }
        if (applied && best_out.result.num_overlaps < out.result.num_overlaps) {
            std::cout << "[NUTS] dogleg: split a trunk to break a cyclic vertical "
                         "constraint on layer " << plans.front().layer
                      << " (overlaps " << out.result.num_overlaps
                      << " -> " << best_out.result.num_overlaps << ").\n";
            bundles = std::move(best_bundles);
            out     = std::move(best_out);
            if (best_bid >= 0) doglegged_bids.insert(best_bid);
        } else {
            break;   // no dogleg helped — leave the residual to BEST_EFFORT
        }
    }

    // Export the dogleg-mutated topologies so the CLI can adopt them before it
    // rebuilds ConnTopology for detailed NUTS — otherwise the split bundle's
    // stubs keep their stale (pre-split) connectivity and detailed NUTS routes
    // them with corrupted spans.
    for (int bid : doglegged_bids)
        for (const auto& bw : bundles)
            if (bw.input.original_bundle.id == bid &&
                bw.plan.selected_topology_index >= 0) {
                out.result.dogleg_topologies[bid] =
                    bw.input.candidates[bw.plan.selected_topology_index];
                out.result.dogleg_seg_layers[bid]    = bw.plan.seg_layers;
                out.result.dogleg_seg_net_pull[bid]  = bw.plan.seg_net_pull;
                out.result.dogleg_seg_perp[bid]      = bw.plan.seg_perp;
                out.result.dogleg_seg_slide_lo[bid]  = bw.plan.seg_slide_lo;
                out.result.dogleg_seg_slide_hi[bid]  = bw.plan.seg_slide_hi;
            }

    std::cout << "[NUTS] " << out.result.segments.size() << " segments placed. "
              << "Interval violations: " << out.result.num_violations << ", "
              << "Track overlaps: " << out.result.num_overlaps << ".\n";
    return out.result;
}

NUTSResult NUTSEngine::rerun_layer(
    const NUTSResult&                  prev,
    const std::vector<BundleWrapper>&  bundles,
    int                                layer_id) const
{
    std::vector<int> x_grid, y_grid;
    floorplan_.get_hanan_grid(x_grid, y_grid);
    merge_grid(x_grid, extra_x_);
    merge_grid(y_grid, extra_y_);
    NUTSResult result = prev;
    for (auto& ts : result.segments) {
        if (ts.layer != layer_id) continue;
        ts.track_position = std::numeric_limits<double>::quiet_NaN();
        ts.placed         = false;
        // This layer is about to be re-solved unconstrained (resolve_corner_
        // overlaps is intentionally not run here), so any cross-layer split
        // bound from the previous full solve is now stale — clear it, or
        // detailed NUTS would filter these trunks to an obsolete side.
        ts.track_lo_bound = -kInf;
        ts.track_hi_bound =  kInf;
    }
    std::map<std::pair<int,int>, double>                         pull_map;
    std::map<std::pair<int,int>, std::pair<double,double>>       slide_map;
    std::set<std::pair<int,int>>                                 trunk_set;
    std::set<std::pair<int,int>>                                 busterm_set;
    std::map<std::pair<int,int>, std::vector<SpanAdjConn>>       rev_conn_map;
    std::map<std::pair<int,int>, int>                            net_pull_map;
    AlignMap                                                     align_map;
    build_nuts_maps(bundles, floorplan_, pull_map, slide_map, trunk_set, busterm_set, rev_conn_map, net_pull_map, align_map);
    apply_interval_constraints(result.segments, slide_map, trunk_set, net_pull_map, layer_id);
    relax_boundary_intervals(result.segments, pull_map, net_pull_map, busterm_set, layer_id);
    std::map<std::pair<int,int>, TrackSegment*> ts_ptr_map;
    for (auto& ts : result.segments)
        ts_ptr_map[{ts.bundle_id, ts.seg_idx}] = &ts;
    std::vector<TrackSegment*> layer_segs;
    for (auto& ts : result.segments)
        if (ts.layer == layer_id) layer_segs.push_back(&ts);
    solve_layer(layer_segs, pull_map, align_map);
    // Final pass for all layers to catch cross-layer adjustments from the re-solved layer.
    std::vector<TrackSegment*> all_placed;
    for (auto& ts : result.segments) if (ts.placed) all_placed.push_back(&ts);
    do_span_adjustments(all_placed, rev_conn_map, ts_ptr_map);
    repair_overlaps(result.segments, pull_map, net_pull_map, align_map,
                    rev_conn_map, ts_ptr_map);
    // Note: resolve_corner_overlaps is NOT run here.  It re-solves whole trunk
    // layers, which may differ from layer_id — that would violate rerun_layer's
    // single-layer contract.  Corner overlaps are resolved by the full run().
    // Tighten only this layer's pulled segments toward their pull bound (the
    // overlap / wirelength guards stay global, so cross-layer spans are honoured)
    // — keeps the single-layer contract while still recovering wirelength.
    tighten_pulls(result.segments, net_pull_map, rev_conn_map, ts_ptr_map, layer_id);
    compute_metrics(result);
    std::cout << "[NUTS] rerun_layer(" << layer_id << "): "
              << layer_segs.size() << " segment(s) re-placed. "
              << "Violations: " << result.num_violations << ", "
              << "Overlaps: " << result.num_overlaps << ".\n";
    return result;
}

} // namespace buda
