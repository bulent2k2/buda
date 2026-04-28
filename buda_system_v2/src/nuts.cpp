#include "nuts.h"
#include <algorithm>
#include <iostream>
#include <map>

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
    // The candidate starting positions are: lo itself, and just after each
    // occupied interval ends (with mandatory spacing applied).
    std::vector<double> candidates;
    candidates.push_back(lo);
    for (const auto& [occ_lo, occ_hi] : occupied)
        candidates.push_back(occ_hi + track_pitch_);

    std::sort(candidates.begin(), candidates.end());

    for (double pos : candidates) {
        if (pos < lo) continue;
        if (pos + width > hi) break;  // remaining candidates are only larger

        bool conflict = false;
        for (const auto& [occ_lo, occ_hi] : occupied) {
            if (pos < occ_hi && pos + width > occ_lo) { conflict = true; break; }
        }
        if (!conflict) return pos;
    }

    return -1.0;  // interval infeasible
}

// ---------------------------------------------------------------------------
// Per-layer sweep-line solver
// ---------------------------------------------------------------------------

void NUTSEngine::solve_layer(std::vector<TrackSegment*>& segs) const {
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

        // Collect occupied intervals from already-placed active segments.
        std::vector<std::pair<double,double>> occupied;
        for (int ai : active) {
            if (segs[ai]->placed)
                occupied.push_back({segs[ai]->track_position,
                                    segs[ai]->track_position + segs[ai]->width});
        }
        std::sort(occupied.begin(), occupied.end());

        double pos = first_fit(ts->interval_lo, ts->interval_hi, ts->width, occupied);

        if (pos >= 0.0) {
            ts->track_position = pos;
        } else {
            // Best-effort: center within interval (interval may be too narrow).
            ts->track_position = (ts->interval_lo + ts->interval_hi) / 2.0
                                 - ts->width / 2.0;
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

    // Group segment pointers by layer.
    std::map<int, std::vector<TrackSegment*>> by_layer;
    for (auto& ts : result.segments)
        by_layer[ts.layer].push_back(&ts);

    // Solve each layer independently (the problem parallelises here).
    for (auto& [layer_id, layer_segs] : by_layer)
        solve_layer(layer_segs);

    // -----------------------------------------------------------------------
    // Metrics
    // -----------------------------------------------------------------------
    for (const auto& ts : result.segments) {
        if (!ts.placed) continue;
        if (ts.track_position < ts.interval_lo ||
            ts.track_position + ts.width > ts.interval_hi)
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
            // Tracks overlap?
            if (a.track_position + a.width > b.track_position &&
                b.track_position + b.width > a.track_position)
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
