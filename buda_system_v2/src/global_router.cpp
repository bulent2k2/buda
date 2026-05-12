#include "global_router.h"
#include <iostream>
#include <algorithm>
#include <limits>
#include <numeric>
#include <cmath>

namespace interconnect {

GlobalRouter::GlobalRouter(const Floorplan& fp, const LayerStack& ls)
    : floorplan_(fp), layers_(ls) {}

void GlobalRouter::set_layer_overhead(int layer_id, double overhead_percent) {
    if (overhead_percent >= 100.0) return;
    layer_dilution_factors_[layer_id] = 100.0 / (100.0 - overhead_percent);
}

void GlobalRouter::set_planner_param(const std::string& name, double value) {
    if      (name == "kCong")             kCong_             = value;
    else if (name == "kSpan")             kSpan_             = value;
    else if (name == "base_cost_non_top") base_cost_non_top_ = value;
    else std::cout << "[Planner] Warning: unknown param '" << name << "'\n";
}

double GlobalRouter::get_dilution(int layer_id) const {
    auto it = layer_dilution_factors_.find(layer_id);
    return (it != layer_dilution_factors_.end()) ? it->second : 1.0;
}

// ---------------------------------------------------------------------------
// Band lookup
// ---------------------------------------------------------------------------

// For a V-cut (is_vcut=true) the perpendicular direction is Y → use y_grid_.
// For an H-cut (is_vcut=false) the perpendicular direction is X → use x_grid_.
// Returns band index b such that grid[b] <= perp_pos < grid[b+1], or -1.
int GlobalRouter::find_band(bool is_vcut, int perp_pos) const {
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
        int band_lo, int band_hi)
{
    std::vector<std::pair<int,int>> blocked;
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
    std::sort(blocked.begin(), blocked.end());
    double avail = static_cast<double>(band_hi - band_lo);
    int cur = band_lo;
    for (auto [lo, hi] : blocked) {
        if (lo > cur) cur = lo;
        if (hi > cur) { avail -= (hi - cur); cur = hi; }
    }
    return std::max(avail, 0.0);
}

void GlobalRouter::build_congestion_map() {
    floorplan_.get_hanan_grid(x_grid_, y_grid_);
    _rebuild_cuts();
}

void GlobalRouter::_rebuild_cuts() {
    cuts_.clear();
    if (x_grid_.size() < 2 || y_grid_.size() < 2) return;

    auto blocks  = floorplan_.get_all_blocks();
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
            GlobalCut c;
            c.p1        = {x_mid, y_grid_.front()};
            c.p2        = {x_mid, y_grid_.back()};
            c.cut_coord = x_mid;
            c.dir       = LayerDir::VERTICAL;
            c.layer_id  = lid;
            c.band_cap.resize(n_ybands);
            c.band_usage.assign(n_ybands, 0.0);
            for (int b = 0; b < n_ybands; ++b)
                c.band_cap[b] = band_available_length(
                        x_mid, true, blocks, y_grid_[b], y_grid_[b+1]);
            cuts_.push_back(std::move(c));
        }
    }

    // H-cuts: one per (Y-channel midpoint, V-layer).
    // Perpendicular direction = X → bands indexed by x_grid_.
    for (int i = 0; i + 1 < (int)y_grid_.size(); ++i) {
        int y_mid = (y_grid_[i] + y_grid_[i+1]) / 2;
        for (int lid : v_layers) {
            GlobalCut c;
            c.p1        = {x_grid_.front(), y_mid};
            c.p2        = {x_grid_.back(),  y_mid};
            c.cut_coord = y_mid;
            c.dir       = LayerDir::HORIZONTAL;
            c.layer_id  = lid;
            c.band_cap.resize(n_xbands);
            c.band_usage.assign(n_xbands, 0.0);
            for (int b = 0; b < n_xbands; ++b)
                c.band_cap[b] = band_available_length(
                        y_mid, false, blocks, x_grid_[b], x_grid_[b+1]);
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
    return cy > lo && cy < hi;
}
static bool h_seg_crosses_vcut(int x1, int x2, int cx) {
    int lo = std::min(x1, x2), hi = std::max(x1, x2);
    return cx > lo && cx < hi;
}

// ---------------------------------------------------------------------------
// Per-segment 2D score and apply
// ---------------------------------------------------------------------------

// Score the marginal peak overflow from adding one segment at a specific layer.
// For H-segments: checks V-cuts on that H-layer, in the Y-band of the segment.
// For V-segments: checks H-cuts on that V-layer, in the X-band of the segment.
double GlobalRouter::score_segment(const Segment& seg, int layer_id,
                                   double eff_width) const {
    bool   is_h = (seg.start.y == seg.end.y);
    double peak = 0.0;
    for (const auto& c : cuts_) {
        if (c.layer_id != layer_id) continue;
        if (is_h && c.dir == LayerDir::VERTICAL) {
            if (!h_seg_crosses_vcut(seg.start.x, seg.end.x, c.cut_coord)) continue;
            int b = find_band(/*is_vcut=*/true, seg.start.y);
            if (b < 0 || b >= (int)c.band_cap.size()) continue;
            double ov = (c.band_usage[b] + eff_width) - c.band_cap[b];
            if (ov > peak) peak = ov;
        } else if (!is_h && c.dir == LayerDir::HORIZONTAL) {
            if (!v_seg_crosses_hcut(seg.start.y, seg.end.y, c.cut_coord)) continue;
            int b = find_band(/*is_vcut=*/false, seg.start.x);
            if (b < 0 || b >= (int)c.band_cap.size()) continue;
            double ov = (c.band_usage[b] + eff_width) - c.band_cap[b];
            if (ov > peak) peak = ov;
        }
    }
    return std::max(peak, 0.0);
}

void GlobalRouter::apply_segment(const Segment& seg, int layer_id, double eff_width) {
    bool is_h = (seg.start.y == seg.end.y);
    for (auto& c : cuts_) {
        if (c.layer_id != layer_id) continue;
        if (is_h && c.dir == LayerDir::VERTICAL) {
            if (!h_seg_crosses_vcut(seg.start.x, seg.end.x, c.cut_coord)) continue;
            int b = find_band(true, seg.start.y);
            if (b >= 0 && b < (int)c.band_cap.size()) c.band_usage[b] += eff_width;
        } else if (!is_h && c.dir == LayerDir::HORIZONTAL) {
            if (!v_seg_crosses_hcut(seg.start.y, seg.end.y, c.cut_coord)) continue;
            int b = find_band(false, seg.start.x);
            if (b >= 0 && b < (int)c.band_cap.size()) c.band_usage[b] += eff_width;
        }
    }
}

// ---------------------------------------------------------------------------
// Span-aware cost helpers
// ---------------------------------------------------------------------------

// Hyperbolic congestion cost: kCong * u/(1-u) where u = (existing+eff)/cap.
// Returns the peak cost across all cuts the segment crosses on the given layer.
double GlobalRouter::cong_cost_segment(const Segment& seg, int layer_id,
                                       double eff_width) const {
    bool   is_h      = (seg.start.y == seg.end.y);
    double peak_cost = 0.0;
    for (const auto& c : cuts_) {
        if (c.layer_id != layer_id) continue;
        int b = -1;
        if (is_h && c.dir == LayerDir::VERTICAL) {
            if (!h_seg_crosses_vcut(seg.start.x, seg.end.x, c.cut_coord)) continue;
            b = find_band(/*is_vcut=*/true, seg.start.y);
        } else if (!is_h && c.dir == LayerDir::HORIZONTAL) {
            if (!v_seg_crosses_hcut(seg.start.y, seg.end.y, c.cut_coord)) continue;
            b = find_band(/*is_vcut=*/false, seg.start.x);
        }
        if (b < 0 || b >= (int)c.band_cap.size()) continue;
        double cap = c.band_cap[b];
        if (cap <= 0.0) { return kCong_ * 9999.0; }
        double u    = std::min((c.band_usage[b] + eff_width) / cap, 0.9999);
        double cost = kCong_ * u / (1.0 - u);
        peak_cost   = std::max(peak_cost, cost);
    }
    return peak_cost;
}

// Span-mismatch cost: kSpan(layer) * excess outside [span_min, span_max].
double GlobalRouter::span_cost_for(double seg_span, int layer_id) const {
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

std::vector<BundleAssignment> GlobalRouter::optimize_topologies(
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

    // Process widest buses first so they claim the best paths early.
    std::vector<int> order(bundles.size());
    std::iota(order.begin(), order.end(), 0);
    std::sort(order.begin(), order.end(), [&](int a, int b) {
        return bundles[a].width > bundles[b].width;
    });

    std::vector<BundleAssignment> assignments;
    assignments.reserve(bundles.size());

    for (int idx : order) {
        auto& bw = bundles[idx];
        if (bw.candidates.empty()) continue;

        int    best_topo     = bw.topology_pinned ? bw.selected_topology_index : 0;
        double best_score    = std::numeric_limits<double>::max();
        double best_overflow = 0.0;
        std::vector<int> best_seg_layers;

        // Snapshot cut state so each topology candidate is scored from the same base.
        auto cuts_snapshot = cuts_;

        int ci_lo = bw.topology_pinned ? bw.selected_topology_index     : 0;
        int ci_hi = bw.topology_pinned ? bw.selected_topology_index + 1 : (int)bw.candidates.size();

        for (int ci = ci_lo; ci < ci_hi; ++ci) {
            const Topology& topo = bw.candidates[ci];

            // Greedy per-segment layer assignment within this topology.
            // Each segment independently gets the layer that minimises its
            // marginal overflow + affinity cost.  We apply each choice to the
            // running cut state so within-topology interactions are captured
            // (same-bundle segments rarely share a cut+band, but this is exact
            // for multicast trees whose H-spine and V-stubs can share bands).
            std::vector<int> seg_layers;
            double topo_overflow = 0.0;
            double topo_score    = 0.0;

            for (int si = 0; si < (int)topo.segments.size(); ++si) {
                const Segment& seg = topo.segments[si];
                bool  is_h         = (seg.start.y == seg.end.y);
                const auto& layers_rev = is_h ? h_layers_rev : v_layers_rev;
                double seg_span = is_h
                    ? (double)std::abs(seg.end.x - seg.start.x)
                    : (double)std::abs(seg.end.y - seg.start.y);

                int    best_lid = layers_rev[0];
                double best_s   = std::numeric_limits<double>::max();
                double best_ov  = 0.0;

                // Iterate highest-ID first so equal-cost layers prefer higher metal.
                for (int lid : layers_rev) {
                    double eff  = bw.width * get_dilution(lid);
                    double cong = cong_cost_segment(seg, lid, eff);
                    double span = span_cost_for(seg_span, lid);
                    double base = layers_.is_top(lid) ? 0.0 : base_cost_non_top_;
                    double s    = cong + span + base;
                    double ov   = score_segment(seg, lid, eff);  // raw overflow for logging
                    if (s < best_s) { best_s = s; best_lid = lid; best_ov = ov; }
                }

                // Apply chosen layer so later segments in this topology see
                // the updated congestion state.
                apply_segment(seg, best_lid, bw.width * get_dilution(best_lid));
                seg_layers.push_back(best_lid);
                topo_overflow = std::max(topo_overflow, best_ov);
                topo_score    = std::max(topo_score,    best_s);
            }

            if (topo_score < best_score) {
                best_score      = topo_score;
                best_overflow   = topo_overflow;
                best_topo       = ci;
                best_seg_layers = seg_layers;
            }

            // Roll back to snapshot before scoring the next candidate.
            cuts_ = cuts_snapshot;
        }

        // Commit the winning topology's per-segment choices to the cut state.
        {
            const Topology& winner = bw.candidates[best_topo];
            for (int si = 0; si < (int)winner.segments.size(); ++si)
                apply_segment(winner.segments[si],
                              best_seg_layers[si],
                              bw.width * get_dilution(best_seg_layers[si]));
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
                  << " -> " << bw.candidates[best_topo].type
                  << (bw.topology_pinned ? " [pinned]" : "")
                  << "  [" << seg_str << "]"
                  << "  overflow=" << best_overflow << "\n";
    }

    return assignments;
}

} // namespace interconnect
