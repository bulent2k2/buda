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
// lo_end=true    → the lo end of S is near the junction; adjust span_lo toward T.
// lo_end=false   → the hi end of S is near the junction; adjust span_hi toward T.
// is_endpoint    → the junction is at an *endpoint* of S (stub tip meets trunk).
//                  Endpoint connections use SET semantics (span can shrink to
//                  trunk centre, fixing Z_VHV left-stub overextension).
//                  Interior (T-junction) connections use extend-only semantics so
//                  a long spine is never truncated by a hanging stub's position.
// The lo/hi decision is locked in at map-build time using the *nominal* span so
// that repeated rerun_layer calls cannot drift the decision as spans grow.
struct SpanAdjConn { int src_bid, src_si; bool lo_end; bool is_endpoint; };

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
                // Decide lo/hi end from the nominal span (along_lo/along_hi) so the
                // decision stays stable across repeated rerun_layer calls even after
                // prior span adjustments have moved span_lo or span_hi.
                double mid = 0.5 * (cs.along_lo + cs.along_hi);
                bool lo_end   = (conn.at_pos <= mid);
                // is_endpoint: junction is at one of S's endpoints (not interior).
                // Endpoint → SET semantics; interior T-junction → extend-only.
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

// ---------------------------------------------------------------------------
// Apply ConnTopology slide ranges and 10% trunk-channel margin.
//
// only_layer: if >= 0, apply only to segments on that layer; -1 = all layers.
// x_grid/y_grid: Hanan grids used to recover degenerate intervals.
//
// When a segment's nominal perp_pos lands exactly on a Hanan grid line, the
// Hanan cell assignment may produce a tight (e.g. [350,400]) interval while
// the ConnTopology slide starts or ends exactly at that grid line.  Applying
// the slide can collapse the interval to a single point [v,v] from which the
// bus of non-zero width cannot be placed without a forced violation.
//
// Recovery: if the result is degenerate AND slo == hanan_hi (slide lower
// bound clamped lo up to the hanan upper boundary), expand to the NEXT
// Hanan cell [hanan_hi, next].  Symmetrically, if shi == hanan_lo, expand
// to the PREVIOUS Hanan cell [prev, hanan_lo].
// ---------------------------------------------------------------------------
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

            // Recovery from degenerate interval caused by slide/Hanan boundary clash.
            if (ts.interval_lo >= ts.interval_hi) {
                double v = ts.interval_lo;  // collapsed gridline value

                // We need the grid for the perpendicular axis of this segment.
                // H segment: perpendicular = Y → use y_grid.
                // V segment: perpendicular = X → use x_grid.
                // Infer orientation from whether span_lo/span_hi were filled;
                // use the grid whose values bracket v on the correct side.
                for (int dir = 0; dir < 2; ++dir) {
                    const std::vector<int>* g = (dir == 0) ? y_grid : x_grid;
                    if (!g || g->size() < 2) continue;

                    // Case 1: slo clamped lo up to hanan_hi → need next cell [v, next].
                    if (std::abs(v - hanan_hi) < 0.5 && slo > -kSentinel) {
                        auto it = std::lower_bound(g->begin(), g->end(),
                                                   static_cast<int>(v + 0.5));
                        if (it != g->end() && std::next(it) != g->end()) {
                            double new_lo = static_cast<double>(*it);
                            double new_hi = static_cast<double>(*std::next(it));
                            if (new_lo >= v - 0.5) {   // sanity: starts at v
                                ts.interval_lo = new_lo;
                                ts.interval_hi = new_hi;
                                break;
                            }
                        }
                    }

                    // Case 2: shi pulled hi down to hanan_lo → need prev cell [prev, v].
                    if (std::abs(v - hanan_lo) < 0.5 && shi < kSentinel) {
                        auto it = std::lower_bound(g->begin(), g->end(),
                                                   static_cast<int>(v + 0.5));
                        if (it != g->begin()) {
                            --it;  // iterator to v itself
                            if (it != g->begin()) {
                                --it;  // iterator to prev
                                double new_lo = static_cast<double>(*it);
                                double new_hi = v;
                                ts.interval_lo = new_lo;
                                ts.interval_hi = new_hi;
                                break;
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
    // Stubs snap to the trunk's centre (track_position), not its far edge,
    // so the stub band doesn't visually penetrate past the trunk centre.
    // is_endpoint distinguishes stub-tip connections (SET semantics: span can
    // shrink to trunk centre) from interior T-junction connections on a spine
    // (extend-only: the spine must never be truncated by a stub's position).
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

    // ── Pass 2: apply all requests jointly per target ───────────────────
    for (auto& [key, reqs] : adj_map) {
        auto jt = ts_ptr_map.find(key);
        if (jt == ts_ptr_map.end()) continue;
        TrackSegment* other = jt->second;

        const double orig_lo = other->span_lo;
        const double orig_hi = other->span_hi;

        // Collect endpoint and interior requests separately.
        double lo_ep  = orig_hi, lo_int = orig_hi;   // sentinels: decrease via min
        double hi_ep  = orig_lo, hi_int = orig_lo;   // sentinels: increase via max
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

        // Endpoint connections: SET to trunk centre (may shrink or extend).
        if (has_lo_ep) final_lo = lo_ep;
        if (has_hi_ep) final_hi = hi_ep;

        // Interior T-junction connections: extend-only (spine must not shrink).
        if (has_lo_int) final_lo = std::min(final_lo, lo_int);
        if (has_hi_int) final_hi = std::max(final_hi, hi_int);

        other->span_lo = final_lo;
        other->span_hi = final_hi;
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
            // Layer priority: per-segment assignment > per-direction override > layer_hint.
            if (si < (int)bw.seg_layers.size() && bw.seg_layers[si] >= 0)
                ts.layer = bw.seg_layers[si];
            else if (!is_horizontal && bw.assigned_v_layer >= 0)
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
    merge_grid(x_grid, extra_x_);
    merge_grid(y_grid, extra_y_);

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
    apply_interval_constraints(result.segments, slide_map, trunk_set, layer_id,
                               &x_grid, &y_grid);

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
