#include "nuts.h"
#include "conn_topology.h"
#include <algorithm>
#include <iostream>
#include <limits>
#include <map>
#include <numeric>
#include <set>

namespace buda {

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

// Internal type: records how segment S's span must follow T's track position.
struct SpanAdjConn { int src_bid, src_si; bool lo_end; bool is_endpoint; };

// ---------------------------------------------------------------------------
// Shared map builders
// ---------------------------------------------------------------------------

static void build_nuts_maps(
    const std::vector<BundleWrapper>& bundles,
    const Floorplan& floorplan,
    std::map<std::pair<int,int>, double>&                         pull_map,
    std::map<std::pair<int,int>, std::pair<double,double>>&       slide_map,
    std::set<std::pair<int,int>>&                                trunk_set,
    std::map<std::pair<int,int>, std::vector<SpanAdjConn>>&       rev_conn_map,
    std::map<std::pair<int,int>, int>&                            net_pull_map)
{
    // Pass 1 — nominal perpendicular position from the topology.
    for (const auto& bw : bundles) {
        if (bw.candidates.empty() || bw.selected_topology_index < 0) continue;
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
        if (bw.candidates.empty() || bw.selected_topology_index < 0) continue;
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
                double mid = 0.5 * (cs.along_lo + cs.along_hi);
                bool lo_end   = (conn.at_pos <= mid);
                rev_conn_map[t_key].push_back({ bid, si, lo_end, conn.is_endpoint });
            }

            // Use cs.net_pull (computed by ConnTopology) to set the preferred
            // placement coordinate.  net_pull > 0 → slide toward perp_hi,
            // net_pull < 0 → slide toward perp_lo.
            net_pull_map[key] = cs.net_pull;
            if (cs.net_pull != 0) {
                constexpr double kSentinel = 5e8;
                double preferred;
                if (cs.net_pull > 0)
                    preferred = (cs.perp_hi < kSentinel) ? static_cast<double>(cs.perp_hi)
                                                         : pull_map[key]; // fallback
                else
                    preferred = (cs.perp_lo > -kSentinel) ? static_cast<double>(cs.perp_lo)
                                                          : pull_map[key]; // fallback
                pull_map[key] = preferred;
            }
        }
    }
}

static void relax_boundary_intervals(
    std::vector<TrackSegment>& segments,
    const std::map<std::pair<int,int>, double>& pull_map,
    const std::map<std::pair<int,int>, int>& net_pull_map,
    int only_layer = -1)
{
    // When the preferred (nominal) position is exactly at an interval boundary,
    // NUTS clamps the bus center to interval_hi - half_width (or interval_lo +
    // half_width), placing it half a bus-width inside the block.  Extend the
    // interval by one full width in the outward direction so the bus center can
    // land at the nominal block-face coordinate, preventing the connected trunk
    // from overstretching past the face.
    //
    // Only applies to topology-nominal preferred positions (net_pull == 0).
    // Net-pull driven segments already want to be at the boundary; extending
    // the interval there would move them in the wrong direction.
    for (auto& ts : segments) {
        if (only_layer >= 0 && ts.layer != only_layer) continue;
        auto key = std::make_pair(ts.bundle_id, ts.seg_idx);
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
    struct AdjReq { double center; bool lo_end; bool is_endpoint; };
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
                {ts->track_position, sc.lo_end, sc.is_endpoint});
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

        for (const auto& req : reqs) {
            if (req.lo_end) {
                has_lo = true;
                min_lo = std::min(min_lo, req.center);
                if (!req.is_endpoint) all_lo_endpoints = false;
            } else {
                has_hi = true;
                max_hi = std::max(max_hi, req.center);
                if (!req.is_endpoint) all_hi_endpoints = false;
            }
        }

        if (has_lo) {
            if (all_lo_endpoints) other->span_lo = min_lo;
            else other->span_lo = std::min(other->span_lo, min_lo);
        }
        if (has_hi) {
            if (all_hi_endpoints) other->span_hi = max_hi;
            else other->span_hi = std::max(other->span_hi, max_hi);
        }
    }
}

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

NUTSEngine::NUTSEngine(const Floorplan& fp, const LayerStack& ls) 
    : floorplan_(fp), layers_(ls) {}

void NUTSEngine::set_track_pitch(double pitch) {
    track_pitch_ = pitch;
}


std::vector<TrackSegment> NUTSEngine::extract_segments(
    const std::vector<BundleWrapper>& bundles,
    const std::vector<int>& x_grid,
    const std::vector<int>& y_grid) const
{
    std::vector<TrackSegment> result;
    for (const auto& bw : bundles) {
        if (bw.candidates.empty() || bw.selected_topology_index < 0) continue;
        const Topology& topo = bw.candidates[bw.selected_topology_index];
        for (int si = 0; si < (int)topo.segments.size(); ++si) {
            const Segment& seg = topo.segments[si];
            const bool is_horizontal = (seg.start.y == seg.end.y);
            TrackSegment ts;
            ts.bundle_id = bw.original_bundle.id;
            ts.seg_idx   = si;
            ts.horiz     = is_horizontal;

            int lid = 0;
            if (si < (int)bw.seg_layers.size() && bw.seg_layers[si] >= 0)
                lid = bw.seg_layers[si];
            else if (!is_horizontal && bw.assigned_v_layer >= 0)
                lid = bw.assigned_v_layer;
            else if (is_horizontal && bw.assigned_h_layer >= 0)
                lid = bw.assigned_h_layer;
            else
                lid = seg.layer_hint;

            ts.layer = lid;
            ts.width = bw.width * layers_.get_layer_dilution(lid);

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
                              const std::map<std::pair<int,int>, double>& pull_map) const {
    if (segs.empty()) return;
    struct Event {
        double pos; int type; int idx; int net_pull_abs = 0;
        bool operator<(const Event& o) const {
            if (pos != o.pos) return pos < o.pos;
            if (type != o.type) return type > o.type;  // END before START at same pos
            // For START events at same position: stronger pull gets placed first.
            return net_pull_abs > o.net_pull_abs;
        }
    };
    std::vector<Event> events;
    events.reserve(segs.size() * 2);
    for (int i = 0; i < (int)segs.size(); ++i) {
        int npa = std::abs(segs[i]->net_pull);
        events.push_back({segs[i]->span_lo, 0, i, npa});
        events.push_back({segs[i]->span_hi, 1, i, 0});
    }
    std::sort(events.begin(), events.end());
    
    // Incorporate KeepoutZones into 'occupied' list.
    auto kozs = floorplan_.get_keepout_zones();

    std::vector<int> active;
    for (const auto& ev : events) {
        if (ev.type == 1) {
            active.erase(std::remove(active.begin(), active.end(), ev.idx), active.end());
            continue;
        }
        TrackSegment* ts = segs[ev.idx];
        std::vector<std::pair<double,double>> occupied;
        
        // 1. Existing placed segments
        for (int ai : active) {
            if (segs[ai]->placed) {
                const double c = segs[ai]->track_position;
                const double h = segs[ai]->width / 2.0;
                occupied.push_back({c - h, c + h});
            }
        }
        
        // 2. KeepoutZones for this layer that intersect segment span
        for (const auto& koz : kozs) {
            if (!koz.layer_ids.count(ts->layer)) continue;
            
            bool intersects = false;
            if (ts->horiz) {
                // Horizontal segment on M-even: span in X, pos in Y
                // Blocks Y if span intersects koz.x
                intersects = (ts->span_lo < koz.bbox.x2 && ts->span_hi > koz.bbox.x1);
                if (intersects) occupied.push_back({static_cast<double>(koz.bbox.y1),
                                                    static_cast<double>(koz.bbox.y2)});
            } else {
                // Vertical segment on M-odd: span in Y, pos in X
                intersects = (ts->span_lo < koz.bbox.y2 && ts->span_hi > koz.bbox.y1);
                if (intersects) occupied.push_back({static_cast<double>(koz.bbox.x1),
                                                    static_cast<double>(koz.bbox.x2)});
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
        if (!std::isnan(pos)) ts->track_position = pos;
        else                  ts->track_position = (ts->interval_lo + ts->interval_hi) / 2.0;
        ts->placed = true;
        active.push_back(ev.idx);
    }
}

void NUTSEngine::set_extra_grid_points(std::vector<int> xs, std::vector<int> ys) {
    std::sort(xs.begin(), xs.end());
    xs.erase(std::unique(xs.begin(), xs.end()), xs.end());
    std::sort(ys.begin(), ys.end());
    ys.erase(std::unique(ys.begin(), ys.end()), ys.end());
    extra_x_ = std::move(xs);
    extra_y_ = std::move(ys);
}

NUTSResult NUTSEngine::run(const std::vector<BundleWrapper>& bundles) {
    std::vector<int> x_grid, y_grid;
    floorplan_.get_hanan_grid(x_grid, y_grid);
    merge_grid(x_grid, extra_x_);
    merge_grid(y_grid, extra_y_);
    NUTSResult result;
    result.segments = extract_segments(bundles, x_grid, y_grid);
    std::map<std::pair<int,int>, double>                         pull_map;
    std::map<std::pair<int,int>, std::pair<double,double>>       slide_map;
    std::set<std::pair<int,int>>                                 trunk_set;
    std::map<std::pair<int,int>, std::vector<SpanAdjConn>>       rev_conn_map;
    std::map<std::pair<int,int>, int>                            net_pull_map;
    build_nuts_maps(bundles, floorplan_, pull_map, slide_map, trunk_set, rev_conn_map, net_pull_map);
    apply_interval_constraints(result.segments, slide_map, trunk_set, net_pull_map, -1);
    relax_boundary_intervals(result.segments, pull_map, net_pull_map);
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
    // Final pass for all layers to catch cross-layer adjustments from the last-solved layer.
    for (auto& [layer_id, layer_segs] : by_layer) {
        do_span_adjustments(layer_segs, rev_conn_map, ts_ptr_map);
    }
    compute_metrics(result);
    std::cout << "[NUTS] " << result.segments.size() << " segments placed across "
              << by_layer.size() << " layer(s). "
              << "Interval violations: " << result.num_violations << ", "
              << "Track overlaps: " << result.num_overlaps << ".\n";
    return result;
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
    }
    std::map<std::pair<int,int>, double>                         pull_map;
    std::map<std::pair<int,int>, std::pair<double,double>>       slide_map;
    std::set<std::pair<int,int>>                                 trunk_set;
    std::map<std::pair<int,int>, std::vector<SpanAdjConn>>       rev_conn_map;
    std::map<std::pair<int,int>, int>                            net_pull_map;
    build_nuts_maps(bundles, floorplan_, pull_map, slide_map, trunk_set, rev_conn_map, net_pull_map);
    apply_interval_constraints(result.segments, slide_map, trunk_set, net_pull_map, layer_id);
    relax_boundary_intervals(result.segments, pull_map, net_pull_map, layer_id);
    std::map<std::pair<int,int>, TrackSegment*> ts_ptr_map;
    for (auto& ts : result.segments)
        ts_ptr_map[{ts.bundle_id, ts.seg_idx}] = &ts;
    std::vector<TrackSegment*> layer_segs;
    for (auto& ts : result.segments)
        if (ts.layer == layer_id) layer_segs.push_back(&ts);
    solve_layer(layer_segs, pull_map);
    // Final pass for all layers to catch cross-layer adjustments from the re-solved layer.
    std::vector<TrackSegment*> all_placed;
    for (auto& ts : result.segments) if (ts.placed) all_placed.push_back(&ts);
    do_span_adjustments(all_placed, rev_conn_map, ts_ptr_map);
    compute_metrics(result);
    std::cout << "[NUTS] rerun_layer(" << layer_id << "): "
              << layer_segs.size() << " segment(s) re-placed. "
              << "Violations: " << result.num_violations << ", "
              << "Overlaps: " << result.num_overlaps << ".\n";
    return result;
}

} // namespace buda
