#include "nuts.h"
#include "conn_topology.h"
#include <algorithm>
#include <iostream>
#include <limits>
#include <map>
#include <numeric>

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
            ts.layer     = seg.layer_hint;
            ts.width     = bw.width;

            const bool is_horizontal = (seg.start.y == seg.end.y);

            if (is_horizontal) {
                // Routing direction: x.  Perpendicular: y.
                ts.span_lo = std::min(seg.start.x, seg.end.x);
                ts.span_hi = std::max(seg.start.x, seg.end.x);

                // Hard interval: the Hanan grid cell that contains this y value.
                int cell = find_grid_cell(y_grid, seg.start.y);
                if (cell >= 0) {
                    ts.interval_lo = y_grid[cell];
                    ts.interval_hi = y_grid[cell + 1];
                } else {
                    // Segment sits outside the Hanan grid; give it some slack.
                    ts.interval_lo = seg.start.y - 50;
                    ts.interval_hi = seg.start.y + 50;
                }
            } else {
                // Vertical segment: routing direction y, perpendicular x.
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

// Given a sorted list of occupied intervals [lo, hi), find the leftmost
// position p in [lo, hi) such that [p, p+width) does not overlap any
// occupied interval and fits within [lo, hi).
// Returns -1.0 if no such position exists.
double NUTSEngine::first_fit(double lo, double hi, double width,
                              const std::vector<std::pair<double,double>>& occupied) const
{
    // Placement positions are CENTERLINES.  The stripe occupies
    // [center - width/2, center + width/2] and must fit within [lo, hi].
    // occupied stores [lo_edge, hi_edge] of already-placed stripes.
    const double half = width / 2.0;
    const double c_lo = lo + half;   // minimum valid centerline
    const double c_hi = hi - half;   // maximum valid centerline
    if (c_lo > c_hi) return -1.0;

    std::vector<double> candidates;
    candidates.push_back(c_lo);
    for (const auto& [occ_lo, occ_hi] : occupied)
        candidates.push_back(occ_hi + track_pitch_ + half);  // edge-to-edge gap

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

    return -1.0;  // interval infeasible
}

// ---------------------------------------------------------------------------
// Preferred-fit within interval
// ---------------------------------------------------------------------------
//
// Returns the valid placement position closest to 'preferred', or -1.0 if
// the interval is infeasible for this segment.
//
// Candidate positions tried (in arbitrary order; the closest valid wins):
//   • preferred itself
//   • lo (interval lower bound — ensures first-fit as fallback)
//   • just above each occupied interval:  occ_hi + track_pitch_
//   • just below each occupied interval:  occ_lo - width - track_pitch_
//
double NUTSEngine::preferred_fit(
    double lo, double hi, double width,
    const std::vector<std::pair<double,double>>& occupied,
    double preferred) const
{
    // All positions are CENTERLINES.  preferred is already a centerline.
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

    // Events: (position_in_routing_dir, type, segment_index_in_segs)
    // type 0 = segment starts, type 1 = segment ends
    struct Event {
        double pos;
        int    type;   // 0 = start, 1 = end
        int    idx;
        bool operator<(const Event& o) const {
            if (pos != o.pos) return pos < o.pos;
            return type > o.type;   // process ends before starts at same position
        }
    };

    std::vector<Event> events;
    events.reserve(segs.size() * 2);
    for (int i = 0; i < (int)segs.size(); ++i) {
        events.push_back({segs[i]->span_lo, 0, i});
        events.push_back({segs[i]->span_hi, 1, i});
    }
    std::sort(events.begin(), events.end());

    // Active set: indices into segs[] that are currently spanning the sweep position.
    std::vector<int> active;

    for (const auto& ev : events) {
        if (ev.type == 1) {
            active.erase(std::remove(active.begin(), active.end(), ev.idx), active.end());
            continue;
        }

        // Start event: assign a track to segs[ev.idx].
        TrackSegment* ts = segs[ev.idx];

        // Collect occupied intervals [lo_edge, hi_edge] from active placed segments.
        std::vector<std::pair<double,double>> occupied;
        for (int ai : active) {
            if (segs[ai]->placed) {
                const double c = segs[ai]->track_position;
                const double h = segs[ai]->width / 2.0;
                occupied.push_back({c - h, c + h});
            }
        }
        std::sort(occupied.begin(), occupied.end());

        // preferred is a centerline; preferred_fit works entirely in centerline space.
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
            // Best-effort: centre of interval (interval may be too narrow).
            ts->track_position = (ts->interval_lo + ts->interval_hi) / 2.0;
        }
        ts->placed = true;
        active.push_back(ev.idx);
    }
}

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------

NUTSResult NUTSEngine::run(const std::vector<BundleWrapper>& bundles) {
    std::vector<int> x_grid, y_grid;
    floorplan_.get_hanan_grid(x_grid, y_grid);

    NUTSResult result;
    result.segments = extract_segments(bundles, x_grid, y_grid);

    // -----------------------------------------------------------------------
    // Build pull map: (bundle_id, seg_idx) -> preferred perpendicular centre.
    //
    // Two-pass initialisation:
    //
    // Pass 1 — nominal position.
    //   Every segment starts with its topology-generator position as preferred.
    //   This keeps direct I-shape segments (two BUSTERM, no SEG connections)
    //   near their intended track rather than drifting to interval_lo.
    //
    // Pass 2 — connectivity override.
    //   For each segment S with SEG connections, collect BUSTERM at_pos values
    //   from every connected perpendicular segment T.  Each at_pos is a face
    //   coordinate along T's routing direction = S's perpendicular direction,
    //   so averaging them gives the position for S that minimises total stub
    //   wirelength.  Overrides the nominal only when targets exist.
    // -----------------------------------------------------------------------
    std::map<std::pair<int,int>, double> pull_map;

    // Pass 1 — nominal perpendicular position from the topology.
    for (const auto& bw : bundles) {
        if (bw.candidates.empty()) continue;
        const Topology& topo = bw.candidates[bw.selected_topology_index];
        int bid = bw.original_bundle.id;

        for (int si = 0; si < (int)topo.segments.size(); ++si) {
            const Segment& seg = topo.segments[si];
            bool is_h  = (seg.start.y == seg.end.y);
            double nom = is_h ? static_cast<double>(seg.start.y)   // H → perp = y
                               : static_cast<double>(seg.start.x);  // V → perp = x
            pull_map[{bid, si}] = nom;
        }
    }

    // Pass 2 — connectivity-based override via SEG→BUSTERM traversal.
    // Also captures ConnTopology slide ranges, identifies trunk segments, and
    // builds the reverse-connection map for per-layer span adjustment.
    //
    // slide_map    : (bid, si) -> (perp_lo, perp_hi) from ConnTopology
    // trunk_set    : segments with ≥2 SEG connections and 0 BUSTERM connections
    //               (i.e. spine/trunk segments, not stubs or direct I-shapes)
    // rev_conn_map : (bid, T_si) -> list of {S_bid, S_si, at_pos_in_S}
    //   "when T is placed, SET the span endpoint of S at at_pos_in_S to T's centre"
    //   at_pos_in_S is the nominal junction coordinate along S's routing direction.
    struct SpanAdjConn { int src_bid, src_si; double at_pos; };
    std::map<std::pair<int,int>, std::pair<double,double>>    slide_map;
    std::set<std::pair<int,int>>                              trunk_set;
    std::map<std::pair<int,int>, std::vector<SpanAdjConn>>    rev_conn_map;

    for (const auto& bw : bundles) {
        if (bw.candidates.empty()) continue;
        const Topology& topo = bw.candidates[bw.selected_topology_index];
        int bid = bw.original_bundle.id;

        ConnTopology ct;
        ct.build(topo, floorplan_);
        const auto& conn_segs = ct.segs();

        for (int si = 0; si < (int)conn_segs.size(); ++si) {
            const ConnSeg& cs = conn_segs[si];
            auto key = std::make_pair(bid, si);

            // Slide range from ConnTopology.
            slide_map[key] = { static_cast<double>(cs.perp_lo),
                               static_cast<double>(cs.perp_hi) };

            // Trunk detection: spine has ≥2 SEG connections and no BUSTERM.
            int n_seg = 0, n_bt = 0;
            for (const auto& c : cs.conns) {
                if (c.kind == SegConn::SEG)     ++n_seg;
                else                             ++n_bt;
            }
            if (n_seg >= 2 && n_bt == 0) trunk_set.insert(key);

            // Reverse-connection map: when segment T=(bid, conn.seg_idx) is placed,
            // SET the span endpoint of S=(bid, si) at at_pos to T's track centre.
            // at_pos is along S's routing direction (= T's perpendicular direction).
            for (const auto& conn : cs.conns) {
                if (conn.kind != SegConn::SEG) continue;
                auto t_key = std::make_pair(bid, conn.seg_idx);
                rev_conn_map[t_key].push_back(
                    { bid, si, static_cast<double>(conn.at_pos) });
            }

            // Pull target from connected block faces.
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
                // Use the median (L1 minimiser) rather than the mean so that a
                // majority of stubs pulling in one direction actually wins.
                // Example: faces at [100, 100, 150] → median 100, not mean 116.7.
                std::sort(targets.begin(), targets.end());
                double median;
                std::size_t n = targets.size();
                if (n % 2 == 1)
                    median = targets[n / 2];
                else
                    median = (targets[n / 2 - 1] + targets[n / 2]) / 2.0;
                pull_map[key] = median;   // overrides nominal
            }
        }
    }

    // -----------------------------------------------------------------------
    // Apply ConnTopology slide ranges and 10% trunk-channel margin.
    //
    // Step A — tighten: intersect each segment's NUTS interval with the
    //   ConnTopology perp_lo/perp_hi.  Values near ±INT_MAX/2 are sentinels
    //   meaning "unconstrained"; we skip those ends.
    //
    // Step B — 10% margin for trunks: a free-floating trunk should stay 10%
    //   of its channel width away from the channel boundary (Hanan grid edge)
    //   in both directions, unless doing so would leave no room for the track.
    //   This prevents trunks from hugging block faces.
    // -----------------------------------------------------------------------
    constexpr double kSentinel = 5e8;   // half of INT_MAX/2 ≈ 1.07e9

    for (auto& ts : result.segments) {
        auto key = std::make_pair(ts.bundle_id, ts.seg_idx);

        // Step A: tighten interval from ConnTopology slide range.
        auto sit = slide_map.find(key);
        if (sit != slide_map.end()) {
            auto [slo, shi] = sit->second;
            if (slo > -kSentinel) ts.interval_lo = std::max(ts.interval_lo, slo);
            if (shi <  kSentinel) ts.interval_hi = std::min(ts.interval_hi, shi);
        }

        // Step B: 10% inward margin for trunk segments.
        if (trunk_set.count(key)) {
            double span   = ts.interval_hi - ts.interval_lo;
            double margin = 0.1 * span;
            double new_lo = ts.interval_lo + margin;
            double new_hi = ts.interval_hi - margin;
            // Only apply if the margin leaves room for at least one track.
            if (new_hi - new_lo >= ts.width) {
                ts.interval_lo = new_lo;
                ts.interval_hi = new_hi;
            }
        }
    }

    // Index for fast span-adjustment lookups.
    std::map<std::pair<int,int>, TrackSegment*> ts_ptr_map;
    for (auto& ts : result.segments)
        ts_ptr_map[{ts.bundle_id, ts.seg_idx}] = &ts;

    // Group segment pointers by layer.
    std::map<int, std::vector<TrackSegment*>> by_layer;
    for (auto& ts : result.segments)
        by_layer[ts.layer].push_back(&ts);

    // Solve each layer independently, then immediately adjust the span
    // endpoints of every segment whose connected partner just moved.
    //
    // Span adjustment: when segment T on this layer is placed at track centre C,
    // identify each segment S that has a SEG connection to T.  The connection
    // carries at_pos — the nominal junction coordinate along S's routing
    // direction (= T's perpendicular direction = the direction of C).  We SET
    // the endpoint of S's span that is nearest to at_pos to C, which correctly
    // handles both extension (trunk moved away) and shrinking (trunk moved
    // closer than the original topology position).
    for (auto& [layer_id, layer_segs] : by_layer) {
        solve_layer(layer_segs, pull_map);

        for (const TrackSegment* ts : layer_segs) {
            if (!ts->placed) continue;
            auto it = rev_conn_map.find({ts->bundle_id, ts->seg_idx});
            if (it == rev_conn_map.end()) continue;
            // lo_edge / hi_edge: the two faces of the just-placed bus stripe T.
            // track_position is the centerline; stripe occupies ± width/2.
            // When S's span endpoint is adjusted to meet T, we align it to the
            // face of T furthest from S's body so the overlap is exactly one
            // bus-stripe wide with no protrusion:
            //   • trimming S's hi end → align to T's hi face
            //   • trimming S's lo end → align to T's lo face
            const double lo_edge = ts->track_position - ts->width / 2.0;
            const double hi_edge = ts->track_position + ts->width / 2.0;
            for (const auto& sc : it->second) {
                auto jt = ts_ptr_map.find({sc.src_bid, sc.src_si});
                if (jt == ts_ptr_map.end()) continue;
                TrackSegment* other = jt->second;

                if (!other->placed) {
                    // S not yet solved: EXTEND only — widen S's solve range to
                    // cover T's full stripe; never shrink it.
                    other->span_lo = std::min(other->span_lo, lo_edge);
                    other->span_hi = std::max(other->span_hi, hi_edge);
                    continue;
                }

                // S already placed: SET the endpoint of S nearest sc.at_pos so
                // the overlap is precisely one stripe wide.
                //
                // at_pos sits at or very near the endpoint for normal stubs, and
                // well inside the span for designed-overlap segments (spread Z).
                // Use the 11% threshold to distinguish SET from EXTEND.
                const double range = other->span_hi - other->span_lo;
                const double tol   = 0.11 * range;
                const double lo_d  = sc.at_pos - other->span_lo;
                const double hi_d  = other->span_hi - sc.at_pos;
                if (hi_d <= tol)
                    other->span_hi = hi_edge;
                else if (lo_d <= tol)
                    other->span_lo = lo_edge;
                else {
                    other->span_lo = std::min(other->span_lo, lo_edge);
                    other->span_hi = std::max(other->span_hi, hi_edge);
                }
            }
        }
    }

    // -----------------------------------------------------------------------
    // Metrics
    // -----------------------------------------------------------------------
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
            // Spans overlap?
            if (a.span_hi <= b.span_lo || b.span_hi <= a.span_lo) continue;
            // Stripes overlap? (centerline ± half-width)
            if (a.track_position + a.width / 2.0 > b.track_position - b.width / 2.0 &&
                b.track_position + b.width / 2.0 > a.track_position - a.width / 2.0)
                ++result.num_overlaps;
        }
    }

    std::cout << "[NUTS] " << result.segments.size() << " segments placed across "
              << by_layer.size() << " layer(s). "
              << "Interval violations: " << result.num_violations << ", "
              << "Track overlaps: " << result.num_overlaps << ".\n";

    return result;
}

} // namespace interconnect
