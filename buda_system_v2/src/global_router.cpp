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

    // Place cuts at MIDPOINTS of consecutive Hanan channels so the cut
    // coordinate lands inside any block that spans the channel.
    for (int i = 0; i + 1 < (int)xs.size(); ++i) {
        int x_mid = (xs[i] + xs[i+1]) / 2;
        GlobalCut c;
        c.p1 = {x_mid, y_lo}; c.p2 = {x_mid, y_hi};
        c.dir = LayerDir::VERTICAL;
        c.capacity = available_length(x_mid, true, blocks, y_lo, y_hi);
        c.current_usage = 0.0;
        cuts_.push_back(c);
    }
    for (int i = 0; i + 1 < (int)ys.size(); ++i) {
        int y_mid = (ys[i] + ys[i+1]) / 2;
        GlobalCut c;
        c.p1 = {x_lo, y_mid}; c.p2 = {x_hi, y_mid};
        c.dir = LayerDir::HORIZONTAL;
        c.capacity = available_length(y_mid, false, blocks, x_lo, x_hi);
        c.current_usage = 0.0;
        cuts_.push_back(c);
    }

    // Debug: report the most constrained cuts.
    auto most_constrained = cuts_;
    std::sort(most_constrained.begin(), most_constrained.end(),
              [](const GlobalCut& a, const GlobalCut& b){ return a.capacity < b.capacity; });
    std::cout << "[Planner] Top constrained cuts (capacity):\n";
    for (int i = 0; i < std::min(4, (int)most_constrained.size()); ++i) {
        const auto& c = most_constrained[i];
        std::cout << "  " << (c.dir == LayerDir::VERTICAL ? "V" : "H")
                  << "-cut @ " << (c.dir == LayerDir::VERTICAL ? c.p1.x : c.p1.y)
                  << "  capacity=" << c.capacity << "\n";
    }
}

// ---------------------------------------------------------------------------
// Geometry helpers
// ---------------------------------------------------------------------------

// Does a horizontal segment (y fixed, x in [x1,x2]) cross vertical cut at cx?
static bool h_seg_crosses_vcut(int seg_x1, int seg_x2, int cx) {
    int lo = std::min(seg_x1, seg_x2);
    int hi = std::max(seg_x1, seg_x2);
    return cx > lo && cx < hi;
}

// Does a vertical segment (x fixed, y in [y1,y2]) cross horizontal cut at cy?
static bool v_seg_crosses_hcut(int seg_y1, int seg_y2, int cy) {
    int lo = std::min(seg_y1, seg_y2);
    int hi = std::max(seg_y1, seg_y2);
    return cy > lo && cy < hi;
}

// Compute total overflow a topology would add if assigned to a bundle of
// given effective_width, given the current cut utilizations.
// Returns the peak (cut_usage + demand - capacity) across all cuts, floored at 0.
double GlobalRouter::score_topology(const Topology& topo, double eff_width) const {
    double peak_overflow = 0.0;
    for (const auto& cut : cuts_) {
        double demand = 0.0;
        for (const auto& seg : topo.segments) {
            bool is_h = (seg.start.y == seg.end.y);
            if (is_h && cut.dir == LayerDir::VERTICAL) {
                if (h_seg_crosses_vcut(seg.start.x, seg.end.x, cut.p1.x))
                    demand += eff_width;
            } else if (!is_h && cut.dir == LayerDir::HORIZONTAL) {
                if (v_seg_crosses_hcut(seg.start.y, seg.end.y, cut.p1.y))
                    demand += eff_width;
            }
        }
        double overflow = (cut.current_usage + demand) - cut.capacity;
        if (overflow > peak_overflow) peak_overflow = overflow;
    }
    return peak_overflow;
}

// Apply a topology's demand to the cut map.
void GlobalRouter::apply_topology(const Topology& topo, double eff_width) {
    for (auto& cut : cuts_) {
        for (const auto& seg : topo.segments) {
            bool is_h = (seg.start.y == seg.end.y);
            if (is_h && cut.dir == LayerDir::VERTICAL) {
                if (h_seg_crosses_vcut(seg.start.x, seg.end.x, cut.p1.x))
                    cut.current_usage += eff_width;
            } else if (!is_h && cut.dir == LayerDir::HORIZONTAL) {
                if (v_seg_crosses_hcut(seg.start.y, seg.end.y, cut.p1.y))
                    cut.current_usage += eff_width;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Main optimiser — greedy, fattest-bus-first
// ---------------------------------------------------------------------------

void GlobalRouter::optimize_topologies(std::vector<BundleWrapper>& bundles,
                                        int /*max_iterations*/) {
    if (cuts_.empty()) build_congestion_map();

    // Process widest buses first so they claim the best paths early.
    std::vector<int> order(bundles.size());
    std::iota(order.begin(), order.end(), 0);
    std::sort(order.begin(), order.end(), [&](int a, int b) {
        return bundles[a].width > bundles[b].width;
    });

    for (int idx : order) {
        auto& bw = bundles[idx];
        if (bw.candidates.empty()) continue;

        // Effective width accounts for layer overhead.
        double eff_width = bw.width;
        // Use the first segment's layer hint for dilution factor lookup.
        if (!bw.candidates[0].segments.empty()) {
            int layer = bw.candidates[0].segments[0].layer_hint;
            auto it = layer_dilution_factors_.find(layer);
            if (it != layer_dilution_factors_.end())
                eff_width *= it->second;
        }

        // Score every candidate; pick the one with the least overflow.
        int best_idx  = 0;
        double best_score = std::numeric_limits<double>::max();
        for (int ci = 0; ci < (int)bw.candidates.size(); ++ci) {
            double s = score_topology(bw.candidates[ci], eff_width);
            if (s < best_score) { best_score = s; best_idx = ci; }
        }

        bw.selected_topology_index = best_idx;
        apply_topology(bw.candidates[best_idx], eff_width);

        std::cout << "[Planner] Bundle " << bw.original_bundle.id
                  << " (" << bw.width << " units wide)"
                  << " -> " << bw.candidates[best_idx].type
                  << "  overflow=" << best_score << "\n";
    }
}

} // namespace interconnect
