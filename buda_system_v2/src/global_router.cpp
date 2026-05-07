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

// ---------------------------------------------------------------------------
// Cut construction
// ---------------------------------------------------------------------------

// Return total unblocked perpendicular length at a cut through cut_coord.
// Uses midpoint cuts (cut_coord is already the channel midpoint) so blocks
// that span the channel interior correctly reduce available capacity.
static double available_length(int cut_coord, bool is_vertical_cut,
                                const std::vector<std::pair<std::string,Rect>>& blocks,
                                int perp_lo, int perp_hi) {
    std::vector<std::pair<int,int>> blocked;
    for (const auto& [name, r] : blocks) {
        bool covers = is_vertical_cut
            ? (cut_coord >= r.x1 && cut_coord <= r.x2)
            : (cut_coord >= r.y1 && cut_coord <= r.y2);
        if (covers) {
            int lo = is_vertical_cut ? r.y1 : r.x1;
            int hi = is_vertical_cut ? r.y2 : r.x2;
            blocked.push_back({std::max(lo, perp_lo), std::min(hi, perp_hi)});
        }
    }
    std::sort(blocked.begin(), blocked.end());
    // Merge intervals and subtract from total.
    double available = static_cast<double>(perp_hi - perp_lo);
    int cur_hi = perp_lo;
    for (auto [lo, hi] : blocked) {
        if (lo >= hi) continue;
        if (lo > cur_hi) cur_hi = lo;
        if (hi > cur_hi) {
            available -= static_cast<double>(hi - cur_hi);
            cur_hi = hi;
        }
    }
    return std::max(available, 0.0);
}

void GlobalRouter::build_congestion_map() {
    std::vector<int> xs, ys;
    floorplan_.get_hanan_grid(xs, ys);
    cuts_.clear();
    if (xs.size() < 2 || ys.size() < 2) return;

    auto blocks = floorplan_.get_all_blocks();
    int y_lo = ys.front(), y_hi = ys.back();
    int x_lo = xs.front(), x_hi = xs.back();

    auto v_layers = layers_.get_layer_ids_by_dir(LayerDir::VERTICAL);
    auto h_layers = layers_.get_layer_ids_by_dir(LayerDir::HORIZONTAL);
    if (v_layers.empty()) v_layers.push_back(5);
    if (h_layers.empty()) h_layers.push_back(4);

    // V cuts (crossed by H segments) — one per H layer.
    // Place cuts at MIDPOINTS of consecutive Hanan channels so the cut
    // coordinate lands inside any block that spans the channel.
    for (int i = 0; i + 1 < (int)xs.size(); ++i) {
        int x_mid = (xs[i] + xs[i+1]) / 2;
        double cap = available_length(x_mid, true, blocks, y_lo, y_hi);
        for (int lid : h_layers) {
            GlobalCut c;
            c.p1 = {x_mid, y_lo}; c.p2 = {x_mid, y_hi};
            c.dir = LayerDir::VERTICAL;
            c.layer_id = lid;
            c.capacity = cap;
            c.current_usage = 0.0;
            cuts_.push_back(c);
        }
    }

    // H cuts (crossed by V segments) — one per V layer.
    for (int i = 0; i + 1 < (int)ys.size(); ++i) {
        int y_mid = (ys[i] + ys[i+1]) / 2;
        double cap = available_length(y_mid, false, blocks, x_lo, x_hi);
        for (int lid : v_layers) {
            GlobalCut c;
            c.p1 = {x_lo, y_mid}; c.p2 = {x_hi, y_mid};
            c.dir = LayerDir::HORIZONTAL;
            c.layer_id = lid;
            c.capacity = cap;
            c.current_usage = 0.0;
            cuts_.push_back(c);
        }
    }

    // Debug: report minimum channel capacities per layer.
    std::cout << "[Planner] Layer channel capacities:\n";
    for (int vid : v_layers) {
        double min_cap = std::numeric_limits<double>::max();
        for (const auto& c : cuts_)
            if (c.layer_id == vid && c.dir == LayerDir::HORIZONTAL)
                min_cap = std::min(min_cap, c.capacity);
        if (min_cap < std::numeric_limits<double>::max())
            std::cout << "  M" << vid << " (V)  min_H-cut_cap=" << min_cap << "\n";
    }
    for (int hid : h_layers) {
        double min_cap = std::numeric_limits<double>::max();
        for (const auto& c : cuts_)
            if (c.layer_id == hid && c.dir == LayerDir::VERTICAL)
                min_cap = std::min(min_cap, c.capacity);
        if (min_cap < std::numeric_limits<double>::max())
            std::cout << "  M" << hid << " (H)  min_V-cut_cap=" << min_cap << "\n";
    }
}

// ---------------------------------------------------------------------------
// Geometry helpers
// ---------------------------------------------------------------------------

// Does a vertical segment (x fixed, y in [y1,y2]) cross horizontal cut at cy?
static bool v_seg_crosses_hcut(int seg_y1, int seg_y2, int cy) {
    int lo = std::min(seg_y1, seg_y2);
    int hi = std::max(seg_y1, seg_y2);
    return cy > lo && cy < hi;
}

// Does a horizontal segment (y fixed, x in [x1,x2]) cross vertical cut at cx?
static bool h_seg_crosses_vcut(int seg_x1, int seg_x2, int cx) {
    int lo = std::min(seg_x1, seg_x2);
    int hi = std::max(seg_x1, seg_x2);
    return cx > lo && cx < hi;
}

// Score (topo, v_layer, h_layer) — returns peak overflow across all relevant cuts.
// V-segments are scored against H-cuts on v_layer_id.
// H-segments are scored against V-cuts on h_layer_id.
double GlobalRouter::score_topology(const Topology& topo,
                                    int v_layer_id, int h_layer_id,
                                    double eff_v_width, double eff_h_width) const {
    double peak_overflow = 0.0;
    for (const auto& cut : cuts_) {
        double demand = 0.0;
        if (cut.layer_id == v_layer_id && cut.dir == LayerDir::HORIZONTAL) {
            // H-cut: counts crossing V-segments
            for (const auto& seg : topo.segments) {
                bool is_h = (seg.start.y == seg.end.y);
                if (!is_h && v_seg_crosses_hcut(seg.start.y, seg.end.y, cut.p1.y))
                    demand += eff_v_width;
            }
        } else if (cut.layer_id == h_layer_id && cut.dir == LayerDir::VERTICAL) {
            // V-cut: counts crossing H-segments
            for (const auto& seg : topo.segments) {
                bool is_h = (seg.start.y == seg.end.y);
                if (is_h && h_seg_crosses_vcut(seg.start.x, seg.end.x, cut.p1.x))
                    demand += eff_h_width;
            }
        } else {
            continue;
        }
        double overflow = (cut.current_usage + demand) - cut.capacity;
        if (overflow > peak_overflow) peak_overflow = overflow;
    }
    return peak_overflow;
}

// Apply (topo, v_layer, h_layer) demand to the relevant cuts.
void GlobalRouter::apply_topology(const Topology& topo,
                                  int v_layer_id, int h_layer_id,
                                  double eff_v_width, double eff_h_width) {
    for (auto& cut : cuts_) {
        if (cut.layer_id == v_layer_id && cut.dir == LayerDir::HORIZONTAL) {
            for (const auto& seg : topo.segments) {
                bool is_h = (seg.start.y == seg.end.y);
                if (!is_h && v_seg_crosses_hcut(seg.start.y, seg.end.y, cut.p1.y))
                    cut.current_usage += eff_v_width;
            }
        } else if (cut.layer_id == h_layer_id && cut.dir == LayerDir::VERTICAL) {
            for (const auto& seg : topo.segments) {
                bool is_h = (seg.start.y == seg.end.y);
                if (is_h && h_seg_crosses_vcut(seg.start.x, seg.end.x, cut.p1.x))
                    cut.current_usage += eff_h_width;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Main optimiser — greedy, fattest-bus-first
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Layer affinity helpers
// ---------------------------------------------------------------------------

// Given a set of non-TOP "alternate" layers (sorted ascending by ID) and a
// normalised span value in [0,1], return the index of the preferred alternate.
// Higher metal (higher ID) is lower resistance → preferred for longer spans.
// So span=0 → index 0 (lowest metal), span=1 → last index (highest metal).
static int preferred_alt_idx(double span_norm, int n_alts) {
    if (n_alts <= 1) return 0;
    int idx = (int)std::round(span_norm * (n_alts - 1));
    return std::clamp(idx, 0, n_alts - 1);
}

// Affinity cost for (layer, candidate) pair.
// TOP layer → 0.  Non-TOP → kBase + small mismatch term.
// kBase must be large enough to absorb any overflow the planner would
// tolerate on the TOP layer before switching (set to half a typical bus width).
static constexpr double kBase    = 0.5;
static constexpr double kMismatch = 0.001;  // per alt-index step of mismatch

// ---------------------------------------------------------------------------
// Main optimiser — greedy, fattest-bus-first
// ---------------------------------------------------------------------------

std::vector<BundleAssignment> GlobalRouter::optimize_topologies(
        std::vector<BundleWrapper>& bundles, int /*max_iterations*/) {
    if (cuts_.empty()) build_congestion_map();

    int top_h = layers_.get_top_layer(LayerDir::HORIZONTAL);
    int top_v = layers_.get_top_layer(LayerDir::VERTICAL);

    // h_layers / v_layers sorted ascending by ID.
    // Alternate (non-TOP) layers at lower IDs are lower metal (short spans);
    // at higher IDs are higher metal (long spans).
    auto h_layers = layers_.get_layer_ids_by_dir(LayerDir::HORIZONTAL);
    auto v_layers = layers_.get_layer_ids_by_dir(LayerDir::VERTICAL);
    if (h_layers.empty()) { h_layers.push_back(4); top_h = 4; }
    if (v_layers.empty()) { v_layers.push_back(5); top_v = 5; }

    // Collect non-TOP alternate layers for each direction.
    std::vector<int> alt_h, alt_v;
    for (int id : h_layers) if (id != top_h) alt_h.push_back(id);
    for (int id : v_layers) if (id != top_v) alt_v.push_back(id);
    // alt_* are already sorted ascending from get_layer_ids_by_dir.

    // Pre-compute max spans across all candidates for normalisation.
    double max_h_span = 1.0, max_v_span = 1.0;
    for (const auto& bw : bundles) {
        for (const auto& cand : bw.candidates) {
            for (const auto& seg : cand.segments) {
                bool is_h = (seg.start.y == seg.end.y);
                if (is_h)
                    max_h_span = std::max(max_h_span, (double)std::abs(seg.end.x - seg.start.x));
                else
                    max_v_span = std::max(max_v_span, (double)std::abs(seg.end.y - seg.start.y));
            }
        }
    }

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
        int    best_v_layer  = top_v;
        int    best_h_layer  = top_h;
        double best_score    = std::numeric_limits<double>::max();
        double best_overflow = 0.0;

        int ci_lo = bw.topology_pinned ? bw.selected_topology_index : 0;
        int ci_hi = bw.topology_pinned ? bw.selected_topology_index + 1 : (int)bw.candidates.size();
        for (int ci = ci_lo; ci < ci_hi; ++ci) {
            // Span of this candidate in each direction.
            double cand_h_span = 0, cand_v_span = 0;
            for (const auto& seg : bw.candidates[ci].segments) {
                bool is_h = (seg.start.y == seg.end.y);
                if (is_h)
                    cand_h_span = std::max(cand_h_span, (double)std::abs(seg.end.x - seg.start.x));
                else
                    cand_v_span = std::max(cand_v_span, (double)std::abs(seg.end.y - seg.start.y));
            }
            double h_norm = cand_h_span / max_h_span;  // 0=short, 1=long
            double v_norm = cand_v_span / max_v_span;

            for (int hid : h_layers) {
                double eff_h = bw.width;
                auto ith = layer_dilution_factors_.find(hid);
                if (ith != layer_dilution_factors_.end()) eff_h *= ith->second;

                // Affinity: 0 for TOP; kBase + mismatch for alternates.
                double h_aff = 0.0;
                if (hid != top_h && !alt_h.empty()) {
                    int actual_alt_idx = (int)(std::find(alt_h.begin(), alt_h.end(), hid) - alt_h.begin());
                    int pref_alt_idx   = preferred_alt_idx(h_norm, (int)alt_h.size());
                    h_aff = kBase + kMismatch * std::abs(actual_alt_idx - pref_alt_idx);
                }

                for (int vid : v_layers) {
                    double eff_v = bw.width;
                    auto itv = layer_dilution_factors_.find(vid);
                    if (itv != layer_dilution_factors_.end()) eff_v *= itv->second;

                    double v_aff = 0.0;
                    if (vid != top_v && !alt_v.empty()) {
                        int actual_alt_idx = (int)(std::find(alt_v.begin(), alt_v.end(), vid) - alt_v.begin());
                        int pref_alt_idx   = preferred_alt_idx(v_norm, (int)alt_v.size());
                        v_aff = kBase + kMismatch * std::abs(actual_alt_idx - pref_alt_idx);
                    }

                    double overflow = score_topology(bw.candidates[ci], vid, hid, eff_v, eff_h);
                    double s = overflow + h_aff + v_aff;
                    if (s < best_score) {
                        best_score    = s;
                        best_overflow = overflow;
                        best_topo     = ci;
                        best_v_layer  = vid;
                        best_h_layer  = hid;
                    }
                }
            }
        }

        {
            double eff_v = bw.width;
            auto itv = layer_dilution_factors_.find(best_v_layer);
            if (itv != layer_dilution_factors_.end()) eff_v *= itv->second;
            double eff_h = bw.width;
            auto ith = layer_dilution_factors_.find(best_h_layer);
            if (ith != layer_dilution_factors_.end()) eff_h *= ith->second;
            apply_topology(bw.candidates[best_topo], best_v_layer, best_h_layer, eff_v, eff_h);
        }

        assignments.push_back({bw.original_bundle.id, best_topo, best_v_layer, best_h_layer});

        std::cout << "[Planner] Bundle " << bw.original_bundle.id
                  << " (" << bw.width << " units wide)"
                  << " -> " << bw.candidates[best_topo].type
                  << (bw.topology_pinned ? " [pinned]" : "")
                  << "  V-layer=M" << best_v_layer
                  << "  H-layer=M" << best_h_layer
                  << "  overflow=" << best_overflow << "\n";
    }

    return assignments;
}

} // namespace interconnect
