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

// Merge extra grid points (sorted) into an existing sorted grid.
static void merge_grid(std::vector<int>& grid, const std::vector<int>& extra) {
    for (int val : extra) {
        auto it = std::lower_bound(grid.begin(), grid.end(), val);
        if (it == grid.end() || *it != val) grid.insert(it, val);
    }
}

// Return the index i such that grid[i] <= v <= grid[i+1], or -1 if out of range.
static int find_grid_cell(const std::vector<int>& grid, int v) {
    for (int i = 0; i + 1 < (int)grid.size(); ++i)
        if (v >= grid[i] && v <= grid[i + 1]) return i;
    return -1;
}

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
    std::set<std::pair<int,int>>                                 trunk_set,
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
                double mid = 0.5 * (cs.along_lo + cs.along_hi);
                bool lo_end   = (conn.at_pos <= mid);
                bool is_ep    = (conn.at_pos == cs.along_lo ||
                                 conn.at_pos == cs.along_hi);
                rev_conn_map[t_key].push_back({ bid, si, lo_end, is_ep });
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

static void apply_interval_constraints(
    std::vector<TrackSegment>& segments,
    const std::map<std::pair<int,int>, std::pair<double,double>>& slide_map,
    const std::set<std::pair<int,int>>&                           trunk_set,
    int only_layer = -1,
    const std::vector<int>* x_grid = nullptr,
    const std::vector<int>* y_grid = nullptr)
{
    constexpr double kSentinel = 5e8;
    for (auto& ts : segments) {
        if (only_layer >= 0 && ts.layer != only_layer) continue;
        auto key = std::make_pair(ts.bundle_id, ts.seg_idx);

        auto sit = slide_map.find(key);
        if (sit != slide_map.end()) {
            auto [slo, shi] = sit->second;
            double hanan_lo = ts.interval_lo;
            double hanan_hi = ts.interval_hi;
            if (slo > -kSentinel) ts.interval_lo = std::max(ts.interval_lo, slo);
            if (shi <  kSentinel) ts.interval_hi = std::min(ts.interval_hi, shi);

            if (ts.interval_lo >= ts.interval_hi) {
                double v = ts.interval_lo;
                const std::vector<int>* g = nullptr;
                if (y_grid && !y_grid->empty()) {
                    int c = find_grid_cell(*y_grid, static_cast<int>(hanan_lo + 0.5));
                    if (c >= 0 && std::abs((*y_grid)[c] - hanan_lo) < 0.5 &&
                                  std::abs((*y_grid)[c+1] - hanan_hi) < 0.5) {
                        g = y_grid;
                    }
                }
                if (!g && x_grid && !x_grid->empty()) {
                    int c = find_grid_cell(*x_grid, static_cast<int>(hanan_lo + 0.5));
                    if (c >= 0 && std::abs((*x_grid)[c] - hanan_lo) < 0.5 &&
                                  std::abs((*x_grid)[c+1] - hanan_hi) < 0.5) {
                        g = x_grid;
                    }
                }

                if (g && g->size() >= 2) {
                    if (std::abs(v - hanan_hi) < 1.5 && slo > -kSentinel) {
                        auto it = std::lower_bound(g->begin(), g->end(),
                                                   static_cast<int>(v + 0.5));
                        if (it != g->end() && std::next(it) != g->end()) {
                            double new_lo = static_cast<double>(*it);
                            double new_hi = static_cast<double>(*std::next(it));
                            if (new_lo >= v - 0.5) {
                                ts.interval_lo = new_lo;
                                ts.interval_hi = new_hi;
                            }
                        }
                    }
                    else if (std::abs(v - hanan_lo) < 1.5 && shi < kSentinel) {
                        auto it = std::lower_bound(g->begin(), g->end(),
                                                   static_cast<int>(v + 0.5));
                        if (it != g->begin()) {
                            --it;
                            if (it != g->begin()) {
                                double new_hi = static_cast<double>(*it);
                                double new_lo = static_cast<double>(*std::prev(it));
                                if (new_hi <= v + 0.5) {
                                    ts.interval_lo = new_lo;
                                    ts.interval_hi = new_hi;
                                }
                            }
                        }
                    }
                }
            }
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
        const double orig_lo = other->span_lo;
        const double orig_hi = other->span_hi;
        double lo_ep  = orig_hi, lo_int = orig_hi;
        double hi_ep  = orig_lo, hi_int = orig_lo;
        bool has_lo_ep = false, has_lo_int = false;
        bool has_hi_ep = false, has_hi_int = false;

        for (const auto& req : reqs) {
            if (req.lo_end) {
                if (req.is_endpoint) { lo_ep  = std::min(lo_ep,  req.center); has_lo_ep  = true; }
                else                 { lo_int = std::min(lo_int, req.center); has_lo_int = true; }
            } else {
                if (req.is_endpoint) { hi_ep  = std::max(hi_ep,  req.center); has_hi_ep  = true; }
                else                 { hi_int = std::max(hi_int, req.center); has_hi_int = true; }
            }
        }
        double final_lo = orig_lo, final_hi = orig_hi;
        if (has_lo_ep) final_lo = lo_ep;
        if (has_hi_ep) final_hi = hi_ep;
        if (has_lo_int) final_lo = std::min(final_lo, lo_int);
        if (has_hi_int) final_hi = std::max(final_hi, hi_int);
        other->span_lo = final_lo;
        other->span_hi = final_hi;
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

NUTSEngine::NUTSEngine(const Floorplan& fp) : floorplan_(fp) {}
void NUTSEngine::set_track_pitch(double pitch) { track_pitch_ = pitch; }

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
            TrackSegment ts;
            ts.bundle_id = bw.original_bundle.id;
            ts.seg_idx   = si;
            ts.width     = bw.width;
            const bool is_horizontal = (seg.start.y == seg.end.y);
            if (si < (int)bw.seg_layers.size() && bw.seg_layers[si] >= 0)
                ts.layer = bw.seg_layers[si];
            else if (!is_horizontal && bw.assigned_v_layer >= 0)
                ts.layer = bw.assigned_v_layer;
            else if (is_horizontal && bw.assigned_h_layer >= 0)
                ts.layer = bw.assigned_h_layer;
            else
                ts.layer = seg.layer_hint;
            ts.horiz = is_horizontal;
            if (ts.horiz) {
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

void NUTSEngine::solve_layer(std::vector<TrackSegment*>& segs,
                              const std::map<std::pair<int,int>, double>& pull_map) const {
    if (segs.empty()) return;
    struct Event {
        double pos; int type; int idx;
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
        if (pos >= 0.0) ts->track_position = pos;
        else           ts->track_position = (ts->interval_lo + ts->interval_hi) / 2.0;
        ts->placed = true;
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
    build_nuts_maps(bundles, floorplan_, pull_map, slide_map, trunk_set, rev_conn_map);
    apply_interval_constraints(result.segments, slide_map, trunk_set, -1, &x_grid, &y_grid);
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
        ts.track_position = -1.0;
        ts.placed         = false;
    }
    std::map<std::pair<int,int>, double>                         pull_map;
    std::map<std::pair<int,int>, std::pair<double,double>>       slide_map;
    std::set<std::pair<int,int>>                                 trunk_set;
    std::map<std::pair<int,int>, std::vector<SpanAdjConn>>       rev_conn_map;
    build_nuts_maps(bundles, floorplan_, pull_map, slide_map, trunk_set, rev_conn_map);
    apply_interval_constraints(result.segments, slide_map, trunk_set, layer_id,
                               &x_grid, &y_grid);
    std::map<std::pair<int,int>, TrackSegment*> ts_ptr_map;
    for (auto& ts : result.segments)
        ts_ptr_map[{ts.bundle_id, ts.seg_idx}] = &ts;
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
