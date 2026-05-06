#include "nuts.h"
#include "conn_topology.h"
#include <algorithm>
#include <iostream>
#include <limits>
#include <map>
#include <numeric>
#include <set>

namespace interconnect {

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Return the index i such that grid[i] <= v <= grid[i+1], or -1 if out of range.
static int find_grid_cell(const std::vector<int>& grid, int v) {
    for (int i = 0; i + 1 < (int)grid.size(); ++i)
        if (v >= grid[i] && v <= grid[i + 1]) return i;
    return -1;
}

// Internal type: records how segment S's span must follow T's track position.
struct SpanAdjConn { int src_bid, src_si; double at_pos; };

// ---------------------------------------------------------------------------
// Shared map builders
// ---------------------------------------------------------------------------
//
// Populates pull_map, slide_map, trunk_set, and rev_conn_map from bundles.
// Called by both run() and rerun_layer().
static void build_nuts_maps(
    const std::vector<BundleWrapper>& bundles,
    const Floorplan& floorplan,
    std::map<std::pair<int,int>, double>&                         pull_map,
    std::map<std::pair<int,int>, std::pair<double,double>>&       slide_map,
    std::set<std::pair<int,int>>&                                 trunk_set,
    std::map<std::pair<int,int>, std::vector<SpanAdjConn>>&       rev_conn_map)
{
    // Pass 1 — nominal perpendicular position from the topology.
    for (const auto& bw : bundles) {
        if (bw.candidates.empty()) continue;
        const Topology& topo = bw.candidates[bw.selected_topology_index];
        int bid = bw.original_bundle.id;
        for (int si = 0; si < (int)topo.segments.size(); ++si) {
            const Segment& seg = topo.segments[si];
            bool is_h  = (seg.start.y == seg.end.y);
            double nom = is_h ? static_cast<double>(seg.start.y)
                               : static_cast<double>(seg.start.x);
            pull_map[{bid, si}] = nom;
        }
    }

    // Pass 2 — connectivity-based override.
    for (const auto& bw : bundles) {
        if (bw.candidates.empty()) continue;
        const Topology& topo = bw.candidates[bw.selected_topology_index];
        int bid = bw.original_bundle.id;

        ConnTopology ct;
        ct.build(topo, floorplan);
        const auto& conn_segs = ct.segs();

        for (int si = 0; si < (int)conn_segs.size(); ++si) {
            const ConnSeg& cs = conn_segs[si];
            auto key = std::make_pair(bid, si);

            slide_map[key] = { static_cast<double>(cs.perp_lo),
                               static_cast<double>(cs.perp_hi) };

            int n_seg = 0, n_bt = 0;
            for (const auto& c : cs.conns) {
                if (c.kind == SegConn::SEG) ++n_seg;
                else                        ++n_bt;
            }
            if (n_seg >= 2 && n_bt == 0) trunk_set.insert(key);

            for (const auto& conn : cs.conns) {
                if (conn.kind != SegConn::SEG) continue;
                auto t_key = std::make_pair(bid, conn.seg_idx);
                rev_conn_map[t_key].push_back(
                    { bid, si, static_cast<double>(conn.at_pos) });
            }

            std::vector<double> targets;
            for (const auto& conn : cs.conns) {
                if (conn.kind != SegConn::SEG) continue;
                const ConnSeg& other = conn_segs[conn.seg_idx];
                for (const auto& other_conn : other.conns) {
                    if (other_conn.kind != SegConn::BUSTERM) continue;
                    targets.push_back(static_cast<double>(other_conn.at_pos));
                }
            }
            if (!targets.empty()) {
                std::sort(targets.begin(), targets.end());
                double median;
                std::size_t n = targets.size();
                if (n % 2 == 1)
                    median = targets[n / 2];
                else
                    median = (targets[n / 2 - 1] + targets[n / 2]) / 2.0;
                pull_map[key] = median;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Apply ConnTopology slide ranges and 10% trunk-channel margin.
//
// only_layer: if >= 0, apply only to segments on that layer; -1 = all layers.
// ---------------------------------------------------------------------------
static void apply_interval_constraints(
    std::vector<TrackSegment>& segments,
    const std::map<std::pair<int,int>, std::pair<double,double>>& slide_map,
    const std::set<std::pair<int,int>>&                           trunk_set,
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

        if (trunk_set.count(key)) {
            double span   = ts.interval_hi - ts.interval_lo;
            double margin = 0.1 * span;
            double new_lo = ts.interval_lo + margin;
            double new_hi = ts.interval_hi - margin;
            if (new_hi - new_lo >= ts.width) {
                ts.interval_lo = new_lo;
                ts.interval_hi = new_hi;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Span adjustments after solving one layer.
//
// For every segment T in layer_segs that was just placed, look up which
// segments S depend on T's position (rev_conn_map) and SET/EXTEND the
// endpoint of S closest to sc.at_pos so the segments meet edge-to-edge.
//
// only_unplaced: when true, skip S segments that are already placed.
//   Use this in rerun_layer so that previously-solved layers are not
//   disturbed by the span update (their track_positions are fixed, so
//   changing their spans would introduce geometric inconsistencies and
//   spurious overlaps in the already-solved layers).
// ---------------------------------------------------------------------------
static void do_span_adjustments(
    const std::vector<TrackSegment*>&                               layer_segs,
    const std::map<std::pair<int,int>, std::vector<SpanAdjConn>>&   rev_conn_map,
    std::map<std::pair<int,int>, TrackSegment*>&                     ts_ptr_map,
    bool                                                             only_unplaced = false)
{
    // ── Pass 1: collect adjustment requests per target segment ──────────
    struct AdjReq { double lo_edge, hi_edge, at_pos; };
    std::map<std::pair<int,int>, std::vector<AdjReq>> adj_map;

    for (const TrackSegment* ts : layer_segs) {
        if (!ts->placed) continue;
        auto it = rev_conn_map.find({ts->bundle_id, ts->seg_idx});
        if (it == rev_conn_map.end()) continue;

        const double lo_edge = ts->track_position - ts->width / 2.0;
        const double hi_edge = ts->track_position + ts->width / 2.0;

        for (const auto& sc : it->second) {
            auto jt = ts_ptr_map.find({sc.src_bid, sc.src_si});
            if (jt == ts_ptr_map.end()) continue;
            TrackSegment* other = jt->second;
            if (only_unplaced && other->placed) continue;
            adj_map[{sc.src_bid, sc.src_si}].push_back({lo_edge, hi_edge, sc.at_pos});
        }
    }

    // ── Pass 2: apply all requests jointly per target ───────────────────
    //
    // Use the segment's own span endpoints as sentinels so that spans can
    // both grow and shrink correctly regardless of processing order:
    //   new_lo starts at span_hi  (pos-INF for std::min → decreases toward lo)
    //   new_hi starts at span_lo  (neg-INF for std::max → increases toward hi)
    for (auto& [key, reqs] : adj_map) {
        auto jt = ts_ptr_map.find(key);
        if (jt == ts_ptr_map.end()) continue;
        TrackSegment* other = jt->second;

        const double orig_lo = other->span_lo;
        const double orig_hi = other->span_hi;
        const double range   = orig_hi - orig_lo;
        const double tol     = 0.11 * range;

        double new_lo = orig_hi;   // pos-INF: will decrease via std::min
        double new_hi = orig_lo;   // neg-INF: will increase via std::max

        for (const auto& req : reqs) {
            const double lo_d = req.at_pos - orig_lo;
            const double hi_d = orig_hi    - req.at_pos;

            if (hi_d <= tol)
                new_hi = std::max(new_hi, req.hi_edge);
            else if (lo_d <= tol)
                new_lo = std::min(new_lo, req.lo_edge);
            else {
                new_lo = std::min(new_lo, req.lo_edge);
                new_hi = std::max(new_hi, req.hi_edge);
            }
        }

        if (new_lo < orig_hi)   // at least one lo-end connection found
            other->span_lo = new_lo;
        if (new_hi > orig_lo)   // at least one hi-end connection found
            other->span_hi = new_hi;
    }
}

// ---------------------------------------------------------------------------
// Metrics: violations, overlaps, overlap_details.  Resets all counters first.
// ---------------------------------------------------------------------------
static void compute_metrics(NUTSResult& result)
{
    result.num_violations = 0;
    result.num_overlaps   = 0;
    result.overlaps_per_layer.clear();
    result.overlap_details.clear();

    for (const auto& ts : result.segments) {
        if (!ts.placed) continue;
        if (ts.track_position - ts.width / 2.0 < ts.interval_lo ||
            ts.track_position + ts.width / 2.0 > ts.interval_hi)
            ++result.num_violations;
    }

    const int n = (int)result.segments.size();
    for (int i = 0; i < n; ++i) {
        for (int j = i + 1; j < n; ++j) {
            const auto& a = result.segments[i];
            const auto& b = result.segments[j];
            if (a.layer != b.layer || !a.placed || !b.placed) continue;
            if (a.span_hi <= b.span_lo || b.span_hi <= a.span_lo) continue;
            if (a.track_position + a.width / 2.0 > b.track_position - b.width / 2.0 &&
                b.track_position + b.width / 2.0 > a.track_position - a.width / 2.0) {
                ++result.num_overlaps;
                ++result.overlaps_per_layer[a.layer];
                OverlapDetail od;
                od.layer   = a.layer;
                od.bid_a   = a.bundle_id;  od.seg_a = a.seg_idx;
                od.bid_b   = b.bundle_id;  od.seg_b = b.seg_idx;
                od.span_lo = std::max(a.span_lo, b.span_lo);
                od.span_hi = std::min(a.span_hi, b.span_hi);
                od.perp_lo = std::max(a.track_position - a.width / 2.0,
                                      b.track_position - b.width / 2.0);
                od.perp_hi = std::min(a.track_position + a.width / 2.0,
                                      b.track_position + b.width / 2.0);
                result.overlap_details.push_back(od);
            }
        }
    }
}

// ---------------------------------------------------------------------------
// NUTSEngine
// ---------------------------------------------------------------------------

NUTSEngine::NUTSEngine(const Floorplan& fp) : floorplan_(fp) {}

void NUTSEngine::set_track_pitch(double pitch) { track_pitch_ = pitch; }

// ---------------------------------------------------------------------------
// Segment extraction
// ---------------------------------------------------------------------------

std::vector<TrackSegment> NUTSEngine::extract_segments(
    const std::vector<BundleWrapper>& bundles,
    const std::vector<int>& x_grid,
    const std::vector<int>& y_grid) const
{
    std::vector<TrackSegment> result;

    for (const auto& bw : bundles) {
        if (bw.candidates.empty()) continue;
        const Topology& topo = bw.candidates[bw.selected_topology_index];

        for (int si = 0; si < (int)topo.segments.size(); ++si) {
            const Segment& seg = topo.segments[si];

            TrackSegment ts;
            ts.bundle_id = bw.original_bundle.id;
            ts.seg_idx   = si;
            ts.width     = bw.width;

            const bool is_horizontal = (seg.start.y == seg.end.y);
            // Use planner-assigned layers; fall back to topology layer_hint.
            if (!is_horizontal && bw.assigned_v_layer >= 0)
                ts.layer = bw.assigned_v_layer;
            else if (is_horizontal && bw.assigned_h_layer >= 0)
                ts.layer = bw.assigned_h_layer;
            else
                ts.layer = seg.layer_hint;

            if (is_horizontal) {
                ts.span_lo = std::min(seg.start.x, seg.end.x);
                ts.span_hi = std::max(seg.start.x, seg.end.x);

                int cell = find_grid_cell(y_grid, seg.start.y);
                if (cell >= 0) {
                    ts.interval_lo = y_grid[cell];
                    ts.interval_hi = y_grid[cell + 1];
                } else {
                    ts.interval_lo = seg.start.y - 50;
                    ts.interval_hi = seg.start.y + 50;
                }
            } else {
                ts.span_lo = std::min(seg.start.y, seg.end.y);
                ts.span_hi = std::max(seg.start.y, seg.end.y);

                int cell = find_grid_cell(x_grid, seg.start.x);
                if (cell >= 0) {
                    ts.interval_lo = x_grid[cell];
                    ts.interval_hi = x_grid[cell + 1];
                } else {
                    ts.interval_lo = seg.start.x - 50;
                    ts.interval_hi = seg.start.x + 50;
                }
            }

            result.push_back(ts);
        }
    }

    return result;
}

// ---------------------------------------------------------------------------
// First-fit within interval
// ---------------------------------------------------------------------------

double NUTSEngine::first_fit(double lo, double hi, double width,
                              const std::vector<std::pair<double,double>>& occupied) const
{
    const double half = width / 2.0;
    const double c_lo = lo + half;
    const double c_hi = hi - half;
    if (c_lo > c_hi) return -1.0;

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

    return -1.0;
}

// ---------------------------------------------------------------------------
// Preferred-fit within interval
// ---------------------------------------------------------------------------

double NUTSEngine::preferred_fit(
    double lo, double hi, double width,
    const std::vector<std::pair<double,double>>& occupied,
    double preferred) const
{
    const double half = width / 2.0;
    const double c_lo = lo + half;
    const double c_hi = hi - half;
    if (c_lo > c_hi) return -1.0;

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

    double best      = -1.0;
    double best_dist = std::numeric_limits<double>::max();
    for (double c : candidates) {
        if (!valid(c)) continue;
        double dist = std::abs(c - preferred);
        if (dist < best_dist) { best_dist = dist; best = c; }
    }
    return best;
}

// ---------------------------------------------------------------------------
// Per-layer sweep-line solver
// ---------------------------------------------------------------------------

void NUTSEngine::solve_layer(std::vector<TrackSegment*>& segs,
                              const std::map<std::pair<int,int>, double>& pull_map) const {
    if (segs.empty()) return;

    struct Event {
        double pos;
        int    type;   // 0 = start, 1 = end
        int    idx;
        bool operator<(const Event& o) const {
            if (pos != o.pos) return pos < o.pos;
            return type > o.type;
        }
    };

    std::vector<Event> events;
    events.reserve(segs.size() * 2);
    for (int i = 0; i < (int)segs.size(); ++i) {
        events.push_back({segs[i]->span_lo, 0, i});
        events.push_back({segs[i]->span_hi, 1, i});
    }
    std::sort(events.begin(), events.end());

    std::vector<int> active;

    for (const auto& ev : events) {
        if (ev.type == 1) {
            active.erase(std::remove(active.begin(), active.end(), ev.idx), active.end());
            continue;
        }

        TrackSegment* ts = segs[ev.idx];

        std::vector<std::pair<double,double>> occupied;
        for (int ai : active) {
            if (segs[ai]->placed) {
                const double c = segs[ai]->track_position;
                const double h = segs[ai]->width / 2.0;
                occupied.push_back({c - h, c + h});
            }
        }
        std::sort(occupied.begin(), occupied.end());

        double pos;
        {
            auto it = pull_map.find(std::make_pair(ts->bundle_id, ts->seg_idx));
            double preferred = (it != pull_map.end())
                               ? it->second
                               : (ts->interval_lo + ts->interval_hi) / 2.0;
            preferred = std::clamp(preferred,
                                   ts->interval_lo + ts->width / 2.0,
                                   ts->interval_hi - ts->width / 2.0);
            pos = preferred_fit(ts->interval_lo, ts->interval_hi,
                                ts->width, occupied, preferred);
        }

        if (pos >= 0.0) {
            ts->track_position = pos;
        } else {
            ts->track_position = (ts->interval_lo + ts->interval_hi) / 2.0;
        }
        ts->placed = true;
        active.push_back(ev.idx);
    }
}

// ---------------------------------------------------------------------------
// run() — full solve
// ---------------------------------------------------------------------------

NUTSResult NUTSEngine::run(const std::vector<BundleWrapper>& bundles) {
    std::vector<int> x_grid, y_grid;
    floorplan_.get_hanan_grid(x_grid, y_grid);

    NUTSResult result;
    result.segments = extract_segments(bundles, x_grid, y_grid);

    std::map<std::pair<int,int>, double>                         pull_map;
    std::map<std::pair<int,int>, std::pair<double,double>>       slide_map;
    std::set<std::pair<int,int>>                                 trunk_set;
    std::map<std::pair<int,int>, std::vector<SpanAdjConn>>       rev_conn_map;
    build_nuts_maps(bundles, floorplan_, pull_map, slide_map, trunk_set, rev_conn_map);

    apply_interval_constraints(result.segments, slide_map, trunk_set);

    std::map<std::pair<int,int>, TrackSegment*> ts_ptr_map;
    for (auto& ts : result.segments)
        ts_ptr_map[{ts.bundle_id, ts.seg_idx}] = &ts;

    std::map<int, std::vector<TrackSegment*>> by_layer;
    for (auto& ts : result.segments)
        by_layer[ts.layer].push_back(&ts);

    for (auto& [layer_id, layer_segs] : by_layer) {
        solve_layer(layer_segs, pull_map);
        do_span_adjustments(layer_segs, rev_conn_map, ts_ptr_map);
    }

    compute_metrics(result);

    std::cout << "[NUTS] " << result.segments.size() << " segments placed across "
              << by_layer.size() << " layer(s). "
              << "Interval violations: " << result.num_violations << ", "
              << "Track overlaps: " << result.num_overlaps << ".\n";

    return result;
}

// ---------------------------------------------------------------------------
// rerun_layer() — re-solve one layer, keeping all others as-is
// ---------------------------------------------------------------------------
//
// Resets the target layer's segments to their fresh topology-extracted state,
// then re-solves placement and applies span adjustments to connected segments
// (which may be on other layers).  All other layers retain their previously
// placed positions.  Metrics are recomputed for the full result.
NUTSResult NUTSEngine::rerun_layer(
    const NUTSResult&                  prev,
    const std::vector<BundleWrapper>&  bundles,
    int                                layer_id) const
{
    std::vector<int> x_grid, y_grid;
    floorplan_.get_hanan_grid(x_grid, y_grid);

    // Start from a copy of the previous result.
    NUTSResult result = prev;

    // Reset placement for the target layer only.
    // We intentionally keep span_lo/span_hi and interval_lo/hi unchanged: the
    // spans were adjusted by prior do_span_adjustments passes (e.g. M4 x-spans
    // were extended by post-M5 span adjustment) and must be preserved so that
    // re-solving only changes the perpendicular track_position, not the geometry.
    for (auto& ts : result.segments) {
        if (ts.layer != layer_id) continue;
        ts.track_position = -1.0;
        ts.placed         = false;
    }

    // Rebuild shared maps.
    std::map<std::pair<int,int>, double>                         pull_map;
    std::map<std::pair<int,int>, std::pair<double,double>>       slide_map;
    std::set<std::pair<int,int>>                                 trunk_set;
    std::map<std::pair<int,int>, std::vector<SpanAdjConn>>       rev_conn_map;
    build_nuts_maps(bundles, floorplan_, pull_map, slide_map, trunk_set, rev_conn_map);

    // Apply interval constraints only for the target layer.
    apply_interval_constraints(result.segments, slide_map, trunk_set, layer_id);

    // Build pointer map (covers all layers for span adjustment targets).
    std::map<std::pair<int,int>, TrackSegment*> ts_ptr_map;
    for (auto& ts : result.segments)
        ts_ptr_map[{ts.bundle_id, ts.seg_idx}] = &ts;

    // Collect target layer segment pointers.
    std::vector<TrackSegment*> layer_segs;
    for (auto& ts : result.segments)
        if (ts.layer == layer_id) layer_segs.push_back(&ts);

    solve_layer(layer_segs, pull_map);
    do_span_adjustments(layer_segs, rev_conn_map, ts_ptr_map);

    compute_metrics(result);

    std::cout << "[NUTS] rerun_layer(" << layer_id << "): "
              << layer_segs.size() << " segment(s) re-placed. "
              << "Violations: " << result.num_violations << ", "
              << "Overlaps: " << result.num_overlaps << ".\n";

    return result;
}

} // namespace interconnect
