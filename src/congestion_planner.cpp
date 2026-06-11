#include "congestion_planner.h"
#include "conn_topology.h"
#include <iostream>
#include <algorithm>
#include <limits>
#include <numeric>
#include <cmath>

namespace buda {
CongestionPlanner::CongestionPlanner(const Floorplan& fp, const LayerStack& ls)
    : floorplan_(fp), layers_(ls) {}

void CongestionPlanner::set_planner_param(const std::string& name, double value) {

    if      (name == "kCong")             kCong_             = value;
    else if (name == "kSpan")             kSpan_             = value;
    else if (name == "base_cost_non_top") base_cost_non_top_ = value;
    else if (name == "kWL")               kWL_               = value;
    else std::cout << "[Planner] Warning: unknown param '" << name << "'\n";
}

// ---------------------------------------------------------------------------
// Band lookup
// ---------------------------------------------------------------------------

// For a V-cut (is_vcut=true) the perpendicular direction is Y → use y_grid_.
// For an H-cut (is_vcut=false) the perpendicular direction is X → use x_grid_.
// Returns band index b such that grid[b] <= perp_pos < grid[b+1], or -1.
int CongestionPlanner::find_band(bool is_vcut, int perp_pos) const {
    const auto& grid = is_vcut ? y_grid_ : x_grid_;
    int n = (int)grid.size();
    if (n < 2) return -1;
    // Binary search.
    int lo = 0, hi = n - 2;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        if      (perp_pos <  grid[mid])   hi = mid - 1;
        else if (perp_pos >= grid[mid+1]) lo = mid + 1;
        else                               return mid;
    }
    // Edge: exactly on the last grid line.
    if (perp_pos == grid[n-1]) return n - 2;
    return -1;
}

// ---------------------------------------------------------------------------
// Cut construction — 2D (per-band capacity)
// ---------------------------------------------------------------------------

// Available perpendicular length within a single Hanan band at a cut.
static double band_available_length(
        int cut_coord, bool is_vcut,
        const std::vector<std::pair<std::string,Rect>>& blocks,
        const std::vector<KeepoutZone>& keepouts,
        int layer_id,
        int band_lo, int band_hi)
{
    std::vector<std::pair<int,int>> blocked;
    // 1. Block footprints
    for (const auto& [name, r] : blocks) {
        bool covers = is_vcut
            ? (cut_coord >= r.x1 && cut_coord <= r.x2)
            : (cut_coord >= r.y1 && cut_coord <= r.y2);
        if (!covers) continue;
        int lo = is_vcut ? r.y1 : r.x1;
        int hi = is_vcut ? r.y2 : r.x2;
        int clo = std::max(lo, band_lo);
        int chi = std::min(hi, band_hi);
        if (clo < chi) blocked.push_back({clo, chi});
    }
    // 2. Keepout zones for this layer
    for (const auto& koz : keepouts) {
        if (!koz.layer_ids.count(layer_id)) continue;
        const Rect& r = koz.bbox;
        bool covers = is_vcut
            ? (cut_coord >= r.x1 && cut_coord <= r.x2)
            : (cut_coord >= r.y1 && cut_coord <= r.y2);
        if (!covers) continue;
        int lo = is_vcut ? r.y1 : r.x1;
        int hi = is_vcut ? r.y2 : r.x2;
        int clo = std::max(lo, band_lo);
        int chi = std::min(hi, band_hi);
        if (clo < chi) blocked.push_back({clo, chi});
    }

    std::sort(blocked.begin(), blocked.end());
    double avail = static_cast<double>(band_hi - band_lo);
    int cur = band_lo;
    for (auto [lo, hi] : blocked) {
        if (lo > cur) cur = lo;
        if (hi > cur) { avail -= (hi - cur); cur = hi; }
    }
    return std::max(avail, 0.0);
}

void CongestionPlanner::build_congestion_map() {
    floorplan_.get_hanan_grid(x_grid_, y_grid_);
    _rebuild_cuts();
}

void CongestionPlanner::_rebuild_cuts() {
    cuts_.clear();
    if (x_grid_.size() < 2 || y_grid_.size() < 2) return;

    auto blocks   = floorplan_.get_all_blocks();
    auto keepouts = floorplan_.get_keepout_zones();
    int n_ybands = (int)y_grid_.size() - 1;
    int n_xbands = (int)x_grid_.size() - 1;

    auto v_layers = layers_.get_layer_ids_by_dir(LayerDir::VERTICAL);
    auto h_layers = layers_.get_layer_ids_by_dir(LayerDir::HORIZONTAL);
    if (v_layers.empty()) v_layers.push_back(5);
    if (h_layers.empty()) h_layers.push_back(4);

    // V-cuts: one per (X-channel midpoint, H-layer).
    // Perpendicular direction = Y → bands indexed by y_grid_.
    for (int i = 0; i + 1 < (int)x_grid_.size(); ++i) {
        int x_mid = (x_grid_[i] + x_grid_[i+1]) / 2;
        for (int lid : h_layers) {
            bool is_top = layers_.get_layer_type(lid) == LayerType::TOP;
            GlobalCut c;
            c.p1        = {x_mid, y_grid_.front()};
            c.p2        = {x_mid, y_grid_.back()};
            c.cut_coord = x_mid;
            c.dir       = LayerDir::VERTICAL;
            c.layer_id  = lid;
            c.band_cap.resize(n_ybands);
            c.band_usage.assign(n_ybands, 0.0);
            for (int b = 0; b < n_ybands; ++b) {
                if (is_top) {
                    c.band_cap[b] = band_available_length(
                            x_mid, true, {}, keepouts, lid, y_grid_[b], y_grid_[b+1]);
                } else {
                    c.band_cap[b] = band_available_length(
                            x_mid, true, blocks, keepouts, lid, y_grid_[b], y_grid_[b+1]);
                }
            }
            cuts_.push_back(std::move(c));
        }
    }

    // H-cuts: one per (Y-channel midpoint, V-layer).
    // Perpendicular direction = X → bands indexed by x_grid_.
    for (int i = 0; i + 1 < (int)y_grid_.size(); ++i) {
        int y_mid = (y_grid_[i] + y_grid_[i+1]) / 2;
        for (int lid : v_layers) {
            bool is_top = layers_.get_layer_type(lid) == LayerType::TOP;
            GlobalCut c;
            c.p1        = {x_grid_.front(), y_mid};
            c.p2        = {x_grid_.back(),  y_mid};
            c.cut_coord = y_mid;
            c.dir       = LayerDir::HORIZONTAL;
            c.layer_id  = lid;
            c.band_cap.resize(n_xbands);
            c.band_usage.assign(n_xbands, 0.0);
            for (int b = 0; b < n_xbands; ++b) {
                if (is_top) {
                    c.band_cap[b] = band_available_length(
                            y_mid, false, {}, keepouts, lid, x_grid_[b], x_grid_[b+1]);
                } else {
                    c.band_cap[b] = band_available_length(
                            y_mid, false, blocks, keepouts, lid, x_grid_[b], x_grid_[b+1]);
                }
            }
            cuts_.push_back(std::move(c));
        }
    }

    // Report minimum per-band capacity per layer.
    std::cout << "[Planner] Layer channel capacities:\n";
    for (int vid : v_layers) {
        double min_cap = std::numeric_limits<double>::max();
        for (const auto& c : cuts_)
            if (c.layer_id == vid && c.dir == LayerDir::HORIZONTAL)
                for (double bc : c.band_cap) min_cap = std::min(min_cap, bc);
        if (min_cap < std::numeric_limits<double>::max())
            std::cout << "  M" << vid << " (V)  min_band_cap=" << min_cap << "\n";
    }
    for (int hid : h_layers) {
        double min_cap = std::numeric_limits<double>::max();
        for (const auto& c : cuts_)
            if (c.layer_id == hid && c.dir == LayerDir::VERTICAL)
                for (double bc : c.band_cap) min_cap = std::min(min_cap, bc);
        if (min_cap < std::numeric_limits<double>::max())
            std::cout << "  M" << hid << " (H)  min_band_cap=" << min_cap << "\n";
    }
}

// ---------------------------------------------------------------------------
// Geometry helpers
// ---------------------------------------------------------------------------

static bool v_seg_crosses_hcut(int y1, int y2, int cy) {
    int lo = std::min(y1, y2), hi = std::max(y1, y2);
    return cy >= lo && cy < hi;   // lo-inclusive: segment starting at cut is counted
}
static bool h_seg_crosses_vcut(int x1, int x2, int cx) {
    int lo = std::min(x1, x2), hi = std::max(x1, x2);
    return cx >= lo && cx < hi;   // lo-inclusive: segment starting at cut is counted
}

// ---------------------------------------------------------------------------
// Per-segment 2D score and apply
// ---------------------------------------------------------------------------

// Score the marginal peak overflow from adding one segment at a specific layer.
// For H-segments: checks V-cuts on that H-layer, in the Y-band of the segment.
// For V-segments: checks H-cuts on that V-layer, in the X-band of the segment.
double CongestionPlanner::score_segment(const Segment& seg, int layer_id,
                                   double eff_width, int perp_pos_override,
                                   int slide_lo, int slide_hi) const {
    bool   is_h = (seg.start.y == seg.end.y);
    int    pp_h = (perp_pos_override != INT_MIN) ? perp_pos_override : seg.start.y;
    int    pp_v = (perp_pos_override != INT_MIN) ? perp_pos_override : seg.start.x;
    double peak = 0.0;
    for (const auto& c : cuts_) {
        if (c.layer_id != layer_id) continue;
        if (is_h && c.dir == LayerDir::VERTICAL) {
            if (!h_seg_crosses_vcut(seg.start.x, seg.end.x, c.cut_coord)) continue;
            int b = find_band(/*is_vcut=*/true, pp_h);
            if (b < 0 || b >= (int)c.band_cap.size()) continue;
            double cap = usable_band_cap(c, b, /*is_vcut=*/true, slide_lo, slide_hi);
            double ov = (c.band_usage[b] + eff_width) - cap;
            if (ov > peak) peak = ov;
        } else if (!is_h && c.dir == LayerDir::HORIZONTAL) {
            if (!v_seg_crosses_hcut(seg.start.y, seg.end.y, c.cut_coord)) continue;
            int b = find_band(/*is_vcut=*/false, pp_v);
            if (b < 0 || b >= (int)c.band_cap.size()) continue;
            double cap = usable_band_cap(c, b, /*is_vcut=*/false, slide_lo, slide_hi);
            double ov = (c.band_usage[b] + eff_width) - cap;
            if (ov > peak) peak = ov;
        }
    }
    return std::max(peak, 0.0);
}

double CongestionPlanner::usable_band_cap(const GlobalCut& c, int b, bool is_vcut,
                                          int slide_lo, int slide_hi) const {
    double cap = c.band_cap[b];
    if (slide_lo == INT_MIN) return cap;
    const auto& grid = is_vcut ? y_grid_ : x_grid_;
    if (b + 1 >= (int)grid.size()) return cap;
    double win = std::min(grid[b + 1], slide_hi) -
                 std::max(grid[b],     slide_lo);
    return std::min(cap, std::max(win, 0.0));
}

void CongestionPlanner::apply_segment(const Segment& seg, int layer_id, double eff_width,
                                      int perp_pos_override) {
    bool is_h = (seg.start.y == seg.end.y);
    int  pp_h = (perp_pos_override != INT_MIN) ? perp_pos_override : seg.start.y;
    int  pp_v = (perp_pos_override != INT_MIN) ? perp_pos_override : seg.start.x;
    for (auto& c : cuts_) {
        if (c.layer_id != layer_id) continue;
        if (is_h && c.dir == LayerDir::VERTICAL) {
            if (!h_seg_crosses_vcut(seg.start.x, seg.end.x, c.cut_coord)) continue;
            int b = find_band(true, pp_h);
            if (b >= 0 && b < (int)c.band_cap.size()) c.band_usage[b] += eff_width;
        } else if (!is_h && c.dir == LayerDir::HORIZONTAL) {
            if (!v_seg_crosses_hcut(seg.start.y, seg.end.y, c.cut_coord)) continue;
            int b = find_band(false, pp_v);
            if (b >= 0 && b < (int)c.band_cap.size()) c.band_usage[b] += eff_width;
        }
    }
}

// ---------------------------------------------------------------------------
// Span-aware cost helpers
// ---------------------------------------------------------------------------

// Overflow congestion cost: kCong * max(0, (usage+eff-cap)/cap).
// Returns zero when the segment fits within the cut-band capacity, and a
// positive cost proportional to the overflow only when it doesn't.
// This means Z/U topologies are only preferred over I when I genuinely
// overflows a cut — not merely because they exploit cut-boundary effects.
double CongestionPlanner::cong_cost_segment(const Segment& seg, int layer_id,
                                       double eff_width, int perp_pos_override,
                                       int slide_lo, int slide_hi) const {
    bool   is_h      = (seg.start.y == seg.end.y);
    int    pp_h = (perp_pos_override != INT_MIN) ? perp_pos_override : seg.start.y;
    int    pp_v = (perp_pos_override != INT_MIN) ? perp_pos_override : seg.start.x;
    double peak_cost = 0.0;
    for (const auto& c : cuts_) {
        if (c.layer_id != layer_id) continue;
        int b = -1;
        bool is_vcut = false;
        if (is_h && c.dir == LayerDir::VERTICAL) {
            if (!h_seg_crosses_vcut(seg.start.x, seg.end.x, c.cut_coord)) continue;
            is_vcut = true;
            b = find_band(/*is_vcut=*/true, pp_h);
        } else if (!is_h && c.dir == LayerDir::HORIZONTAL) {
            if (!v_seg_crosses_hcut(seg.start.y, seg.end.y, c.cut_coord)) continue;
            b = find_band(/*is_vcut=*/false, pp_v);
        }
        if (b < 0 || b >= (int)c.band_cap.size()) continue;
        double cap = usable_band_cap(c, b, is_vcut, slide_lo, slide_hi);
        if (cap <= 0.0) { return kCong_ * 9999.0; }
        double ov = c.band_usage[b] + eff_width - cap;
        if (ov <= 0.0) continue;   // fits — no cost
        double cost = kCong_ * ov / cap;
        peak_cost   = std::max(peak_cost, cost);
    }
    return peak_cost;
}

// Slide-aware band choice.  cong_cost_segment charges the whole bus to the
// single band containing the lookup coordinate; using the slide-interval
// centre for that lookup is a point estimate that can land in an arbitrarily
// narrow band even though NUTS may slide the bus into a wide neighbouring
// band (e.g. a chip-to-chip I_H centring on the thin sliver between two
// block rows).  Scan every band the slide interval overlaps by at least
// eff_width and return the cheapest usable coordinate instead.
int CongestionPlanner::best_band_perp(const Segment& seg, int layer_id,
                                      double eff_width,
                                      int slide_lo, int slide_hi) const {
    bool is_h = (seg.start.y == seg.end.y);
    const auto& grid = is_h ? y_grid_ : x_grid_;
    const int centre = (slide_lo + slide_hi) / 2;
    if ((int)grid.size() < 2) return centre;

    int    best_pp   = centre;
    double best_cost = cong_cost_segment(seg, layer_id, eff_width, centre,
                                         slide_lo, slide_hi);
    int    best_dist = 0;

    for (int b = 0; b + 1 < (int)grid.size(); ++b) {
        int win_lo = std::max(grid[b],     slide_lo);
        int win_hi = std::min(grid[b + 1], slide_hi);
        if (win_hi - win_lo < eff_width) continue;   // band can't host the bus
        int pp = (win_lo + win_hi) / 2;              // centre of the usable window
        double cost = cong_cost_segment(seg, layer_id, eff_width, pp,
                                        slide_lo, slide_hi);
        int dist = std::abs(pp - centre);
        if (cost < best_cost - 1e-9 ||
            (std::abs(cost - best_cost) < 1e-9 && dist < best_dist)) {
            best_cost = cost;
            best_pp   = pp;
            best_dist = dist;
        }
    }
    return best_pp;
}

// Span-mismatch cost: kSpan(layer) * excess outside [span_min, span_max].
double CongestionPlanner::span_cost_for(double seg_span, int layer_id) const {
    const Layer* layer = layers_.get_layer(layer_id);
    if (!layer) return 0.0;
    double k      = (layer->kspan_override >= 0.0) ? layer->kspan_override : kSpan_;
    double excess = std::max({0.0,
                              (double)layer->span_min - seg_span,
                              seg_span - (double)layer->span_max});
    return k * excess;
}

// ---------------------------------------------------------------------------
// Main optimiser — greedy fattest-bus-first, per-segment layer assignment
// ---------------------------------------------------------------------------

std::vector<BundleAssignment> CongestionPlanner::optimize_topologies(
        std::vector<BundleWrapper>& bundles, int /*max_iterations*/) {
    // Ensure base grid is populated from floorplan.
    if (x_grid_.empty()) build_congestion_map();

    // Extend the Hanan grid only with segment endpoint coordinates that fall
    // OUTSIDE the current grid's range.  Topology generators place in-grid
    // segments at Hanan-cell midpoints; inserting those as new grid lines would
    // split cells into tiny sub-bands with zero capacity and cause violations.
    // Out-of-range coordinates (e.g. U-shape trunks beyond the chip boundary)
    // have no covering cell at all and would receive the ±50 fallback interval.
    {
        size_t nx0 = x_grid_.size(), ny0 = y_grid_.size();
        auto extend_oob = [](std::vector<int>& grid, int val) {
            if (grid.size() < 2) return;
            if (val >= grid.front() && val <= grid.back()) return; // inside — skip
            auto it = std::lower_bound(grid.begin(), grid.end(), val);
            if (it == grid.end() || *it != val) grid.insert(it, val);
        };
        for (const auto& bw : bundles) {
            for (const auto& cand : bw.candidates) {
                for (const auto& seg : cand.segments) {
                    extend_oob(x_grid_, seg.start.x);
                    extend_oob(x_grid_, seg.end.x);
                    extend_oob(y_grid_, seg.start.y);
                    extend_oob(y_grid_, seg.end.y);
                }
            }
        }
        if (x_grid_.size() != nx0 || y_grid_.size() != ny0) {
            std::cout << "[Planner] Grid extended: "
                      << (x_grid_.size() - nx0) << " X, "
                      << (y_grid_.size() - ny0) << " Y points from topology candidates.\n";
            _rebuild_cuts();
        }
    }

    int top_h = layers_.get_top_layer(LayerDir::HORIZONTAL);
    int top_v = layers_.get_top_layer(LayerDir::VERTICAL);

    auto h_layers = layers_.get_layer_ids_by_dir(LayerDir::HORIZONTAL);
    auto v_layers = layers_.get_layer_ids_by_dir(LayerDir::VERTICAL);
    if (h_layers.empty()) { h_layers.push_back(4); top_h = 4; }
    if (v_layers.empty()) { v_layers.push_back(5); top_v = 5; }

    // Reversed copies: highest layer ID first so ties break toward higher metal.
    auto h_layers_rev = h_layers; std::reverse(h_layers_rev.begin(), h_layers_rev.end());
    auto v_layers_rev = v_layers; std::reverse(v_layers_rev.begin(), v_layers_rev.end());

    // Sort: higher priority first (depth-0 before depth-1, constrained first);
    // within the same priority, process widest buses first.
    std::vector<int> order(bundles.size());
    std::iota(order.begin(), order.end(), 0);
    std::sort(order.begin(), order.end(), [&](int a, int b) {
        if (bundles[a].priority != bundles[b].priority)
            return bundles[a].priority > bundles[b].priority;
        return bundles[a].width > bundles[b].width;
    });

    std::vector<BundleAssignment> assignments;
    assignments.reserve(bundles.size());

    for (int idx : order) {
        auto& bw = bundles[idx];
        if (bw.candidates.empty()) continue;
        // Bit count for the honest per-layer width model (eff_bus_width);
        // 0 (hand-built wrappers without nets) falls back to width x dilution.
        const int nbits = (int)bw.original_bundle.get_net_names().size();

        int    best_topo     = bw.topology_pinned ? bw.selected_topology_index : 0;
        double best_score    = std::numeric_limits<double>::max();
        double best_overflow = 0.0;
        bool   have_winner   = false;
        bool   best_effort   = false;
        std::vector<int> best_seg_layers;
        std::vector<int> best_seg_perp;  // ConnTopology perp-centre per segment, for winner commit

        // Snapshot cut state so each topology candidate is scored from the same base.
        auto cuts_snapshot = cuts_;

        int ci_lo = bw.topology_pinned ? bw.selected_topology_index     : 0;
        int ci_hi = bw.topology_pinned ? bw.selected_topology_index + 1 : (int)bw.candidates.size();

        // Pass 0 enforces slide-window feasibility.  If every candidate is
        // infeasible (a pinned bundle scores exactly one), pass 1 re-scores
        // with the feasibility gate off so the bundle still gets a layer
        // assignment instead of committing an EMPTY best_seg_layers — which
        // indexed out of bounds and crashed (flow/channel_stress.buda:
        // sidecar pins saved under the old width model).
        for (int pass = 0; pass < 2 && !have_winner; ++pass) {
        const bool enforce_feasibility = (pass == 0);
        best_effort = !enforce_feasibility;
        for (int ci = ci_lo; ci < ci_hi; ++ci) {
            const Topology& topo = bw.candidates[ci];

            // Greedy per-segment layer assignment within this topology.
            // Each segment independently gets the layer that minimises its
            // marginal overflow + affinity cost.  We apply each choice to the
            // running cut state so within-topology interactions are captured
            // (same-bundle segments rarely share a cut+band, but this is exact
            // for multicast trees whose H-spine and V-stubs can share bands).
            std::vector<int> seg_layers;
            std::vector<int> seg_perp;   // perp-centre overrides for band lookup
            double topo_overflow = 0.0;
            double topo_score    = 0.0;
            bool   topo_infeasible = false;

            // Build ConnTopology for this candidate to obtain authoritative
            // perp_lo/perp_hi ranges (including spines/trunks via Pass 2).
            // The interval centre is also used as the perp-band lookup key so
            // that stubs whose nominal x/y lands on a Hanan grid boundary are
            // credited to the correct cell (the one NUTS's interval places them in)
            // rather than the adjacent cell chosen by find_band's half-open rule.
            ConnTopology ct;
            ct.build(topo, floorplan_);
            const auto& conn_segs = ct.segs();
            constexpr int kSentinel = INT_MAX / 2;

            for (int si = 0; si < (int)topo.segments.size(); ++si) {
                const Segment& seg = topo.segments[si];
                bool  is_h         = (seg.start.y == seg.end.y);
                const auto& layers_rev = is_h ? h_layers_rev : v_layers_rev;
                double seg_span = is_h
                    ? (double)std::abs(seg.end.x - seg.start.x)
                    : (double)std::abs(seg.end.y - seg.start.y);

                // Derive the perpendicular-band lookup window from the ConnTopology
                // slide range.  The segment can slide anywhere within it, so the
                // congestion charge goes to the cheapest band that can host the bus
                // (best_band_perp) rather than a point estimate at the centre —
                // which can land in an arbitrarily narrow band the bus would never
                // use.  Sentinel (unbounded) sides are clamped to the grid extent.
                int slide_lo = INT_MIN, slide_hi = INT_MIN;
                if (si < (int)conn_segs.size()) {
                    const ConnSeg& cs = conn_segs[si];
                    const auto& pgrid = is_h ? y_grid_ : x_grid_;
                    if (!pgrid.empty()) {
                        slide_lo = std::max(cs.perp_lo, pgrid.front());
                        slide_hi = std::min(cs.perp_hi, pgrid.back());
                        if (slide_lo > slide_hi) { slide_lo = INT_MIN; slide_hi = INT_MIN; }
                    }
                }
                auto band_perp = [&](int lid, double eff) {
                    if (slide_lo == INT_MIN) return INT_MIN;   // no window: nominal lookup
                    return best_band_perp(seg, lid, eff, slide_lo, slide_hi);
                };

                int    best_lid = layers_rev[0];
                double best_s   = std::numeric_limits<double>::max();
                double best_ov  = 0.0;
                int    best_pp  = INT_MIN;

                // Respect manual layer overrides if present for this segment.
                if (si < (int)bw.pinned_seg_layers.size() && bw.pinned_seg_layers[si] != -1) {
                    best_lid = bw.pinned_seg_layers[si];
                    best_s   = 0.0; // Pinned choice is considered "perfect" cost for planning.
                    double eff = layers_.eff_bus_width(nbits, bw.width, best_lid);
                    best_pp  = band_perp(best_lid, eff);
                    best_ov  = score_segment(seg, best_lid, eff, best_pp, slide_lo, slide_hi);
                } else {
                    // Iterate highest-ID first so equal-cost layers prefer higher metal.
                    for (int lid : layers_rev) {
                        double eff  = layers_.eff_bus_width(nbits, bw.width, lid);
                        int    pp   = band_perp(lid, eff);
                        double cong = cong_cost_segment(seg, lid, eff, pp, slide_lo, slide_hi);
                        double span = span_cost_for(seg_span, lid);
                        double base = layers_.is_top(lid) ? 0.0 : base_cost_non_top_;
                        double s    = cong + span + base;
                        double ov   = score_segment(seg, lid, eff, pp, slide_lo, slide_hi);
                        if (s < best_s) { best_s = s; best_lid = lid; best_ov = ov; best_pp = pp; }
                    }
                }
                int perp_pos = best_pp;

                // Feasibility: the bus (eff_width in the perpendicular direction)
                // must fit within the sliding range ConnTopology computed for this
                // segment — covers busterms (Pass 1) and spines/trunks (Pass 2).
                if (enforce_feasibility && si < (int)conn_segs.size()) {
                    const ConnSeg& cs = conn_segs[si];
                    if (cs.perp_lo > -kSentinel && cs.perp_hi < kSentinel) {
                        double eff = layers_.eff_bus_width(nbits, bw.width, best_lid);
                        if (static_cast<double>(cs.perp_hi - cs.perp_lo) < eff)
                            topo_infeasible = true;
                    }
                }
                if (topo_infeasible) break;

                // Apply chosen layer so later segments in this topology see
                // the updated congestion state.
                double eff = layers_.eff_bus_width(nbits, bw.width, best_lid);
                apply_segment(seg, best_lid, eff, perp_pos);
                seg_layers.push_back(best_lid);
                seg_perp.push_back(perp_pos);
                topo_overflow = std::max(topo_overflow, best_ov);
                topo_score    = std::max(topo_score,    best_s);
            }

            // Wirelength term: with congestion/span/layer costs equal, shorter
            // topologies win — a detour must buy real congestion relief to be
            // worth its extra length.
            topo_score += kWL_ * topo.estimated_wirelength;

            if (topo_infeasible) {
                cuts_ = cuts_snapshot;
                continue;
            }

            bool is_better = false;
            if (topo_score < best_score - 1e-6) {
                is_better = true;
            } else if (std::abs(topo_score - best_score) < 1e-6) {
                // Tie-breaker: stable selection by index.
                if (ci < best_topo) is_better = true;
            }

            if (is_better) {
                best_score      = topo_score;
                best_overflow   = topo_overflow;
                best_topo       = ci;
                best_seg_layers = seg_layers;
                best_seg_perp   = seg_perp;
                have_winner     = true;
            }

            // Roll back to snapshot before scoring the next candidate.
            cuts_ = cuts_snapshot;
        }
        }  // feasibility passes

        if (!have_winner) continue;   // no candidates scored (empty range)
        if (best_effort)
            std::cout << "[Planner] WARNING: Bundle " << bw.original_bundle.id
                      << ": no candidate fits its slide windows (bus width "
                      << "exceeds them); committing best-effort "
                      << bw.candidates[best_topo].type
                      << (bw.topology_pinned ? " [pinned]" : "") << ".\n";

        // Commit the winning topology's per-segment choices to the cut state.
        {
            const Topology& winner = bw.candidates[best_topo];
            for (int si = 0; si < (int)winner.segments.size(); ++si) {
                int pp = (si < (int)best_seg_perp.size()) ? best_seg_perp[si] : INT_MIN;
                apply_segment(winner.segments[si],
                              best_seg_layers[si],
                              layers_.eff_bus_width(nbits, bw.width, best_seg_layers[si]),
                              pp);
            }
        }

        // Derive representative V/H layers for logging (last V/H seg wins).
        int rep_v = top_v, rep_h = top_h;
        {
            const Topology& winner = bw.candidates[best_topo];
            for (int si = 0; si < (int)winner.segments.size(); ++si) {
                bool is_h = (winner.segments[si].start.y == winner.segments[si].end.y);
                if (is_h) rep_h = best_seg_layers[si];
                else      rep_v = best_seg_layers[si];
            }
        }

        BundleAssignment asn;
        asn.bundle_id  = bw.original_bundle.id;
        asn.topo_index = best_topo;
        asn.v_layer_id = rep_v;
        asn.h_layer_id = rep_h;
        asn.seg_layers = best_seg_layers;
        assignments.push_back(asn);

        // Per-segment summary for console.
        std::string seg_str;
        {
            const Topology& winner = bw.candidates[best_topo];
            for (int si = 0; si < (int)winner.segments.size(); ++si) {
                bool is_h = (winner.segments[si].start.y == winner.segments[si].end.y);
                if (si > 0) seg_str += ' ';
                seg_str += (is_h ? "H" : "V");
                seg_str += "→M" + std::to_string(best_seg_layers[si]);
            }
        }
        std::cout << "[Planner] Bundle " << bw.original_bundle.id
                  << " (" << bw.width << " units wide)"
                  << " -> topo " << (best_topo + 1) << " of " << bw.candidates.size()
                  << ": " << bw.candidates[best_topo].type
                  << (bw.topology_pinned ? " [pinned]" : "")
                  << "  [" << seg_str << "]"
                  << "  overflow=" << best_overflow << "\n";
    }

    return assignments;
}

} // namespace buda
