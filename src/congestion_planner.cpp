/*
 * Copyright 2026 Ben Bulent Basaran
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "congestion_planner.h"
#include "conn_topology.h"
#include <iostream>
#include <algorithm>
#include <cstdlib>
#include <limits>
#include <map>
#include <numeric>
#include <cmath>
#include <unordered_map>
#include <atomic>
#include <thread>
#include <chrono>

namespace buda {

// NDR (phase 1): a governed bundle's per-segment charge is its member bits'
// GROUP demand in slots — the single-sourced conversion of requirement R4
// (ndr.h) shared with abstract-NUTS width and DNUTS admission, so no two
// stages can disagree about whether a rule-governed group fits.  Identity
// when the spec is inactive: the whole feature's byte-identity guarantee.
static inline int ndr_units(const BundleInput& in, int n) {
    return (n > 0 && in.ndr.active()) ? ndr_group_demand(in.ndr, n) : n;
}

// Per-candidate scoring overlay (plan_bundle).  While a candidate is being
// scored, its own within-candidate charges live here instead of in cuts_ —
// the committed cut state stays READ-ONLY for the whole scoring pass, which
// is what makes scoring candidates on worker threads race-free (each thread
// owns one overlay; the base is shared read-only).  Values are stored exactly
// as the historical in-place charge stored them: the first touch of a band
// copies its current usage, later charges of the same band `+=` — so every
// read through cand_usage_ returns the bit-identical double the
// charge-then-undo implementation produced, and per-candidate rollback is
// overlay.clear() (an O(1) epoch bump — see CandOverlay in the header).
thread_local CongestionPlanner::CandOverlay* CongestionPlanner::t_cand_overlay_ = nullptr;

// Process-wide cut-grid generation source (rebuild_cuts_ stamps cuts_gen_).
static std::atomic<uint64_t> g_cuts_generation{0};

CongestionPlanner::CandOverlay& CongestionPlanner::thread_overlay_() const {
    static thread_local CandOverlay ov;
    const size_t n = band_base_.empty() ? 0 : band_base_.back();
    if (ov.gen != cuts_gen_ || ov.val.size() != n) {
        ov.val.assign(n, 0.0);
        ov.stamp.assign(n, 0);
        ov.epoch = 0;
        ov.gen   = cuts_gen_;
    }
    return ov;
}

double CongestionPlanner::cand_usage_(int ci, int b) const {
    if (t_cand_overlay_) {
        const size_t k = band_base_[ci] + (size_t)b;
        if (t_cand_overlay_->stamp[k] == t_cand_overlay_->epoch)
            return t_cand_overlay_->val[k];
    }
    return cuts_[ci].usage(b);
}
CongestionPlanner::CongestionPlanner(const Floorplan& fp, const LayerStack& ls)
    : floorplan_(fp), layers_(ls) {
    // Study hook for corpus sweeps (issue #518), mirroring BUDA_KSEGS_REL:
    // flip band_span_charge on for every planner in a run without editing 29
    // flow scripts.  An explicit `set_planner_param band_span_charge` still
    // wins, since it runs after construction.
    if (const char* e = std::getenv("BUDA_BAND_SPAN_CHARGE"))
        band_span_charge_ = std::atoi(e);
    // Same study hook for kPeak, so a corpus sweep can pair the two without
    // editing 29 flow scripts (mirrors BUDA_KSEGS_REL).
    if (const char* e = std::getenv("BUDA_KPEAK"))
        kPeak_ = std::atof(e);
}

void CongestionPlanner::set_planner_param(const std::string& name, double value) {

    if      (name == "kCong")             kCong_             = value;
    else if (name == "kSpan")             kSpan_             = value;
    else if (name == "base_cost_non_top") base_cost_non_top_ = value;
    else if (name == "kWL")               kWL_               = value;
    else if (name == "kSegs")             kSegs_             = value;
    else if (name == "kSegsRel")          kSegsRel_          = value;
    else if (name == "kSegsGate")         kSegsGate_         = value;
    else if (name == "healersAhead")      healersAhead_      = value;
    else if (name == "kBalance")          kBalance_          = value;
    else if (name == "kHeight")           kHeight_           = value;
    else if (name == "kPeak")             kPeak_             = value;
    else if (name == "kWLSpread")         kWLSpread_         = value;
    else if (name == "base_span_ref")     base_span_ref_     = value;
    else if (name == "track_cap_slack")   track_cap_slack_   = value;
    else if (name == "refine_passes")     refine_passes_     = (int)value;
    else if (name == "nontop_dead_span_gate") nontop_dead_span_gate_ = (value != 0.0);
    else if (name == "charge_pull_target")    charge_pull_target_    = (int)value;
    else if (name == "band_span_charge")      band_span_charge_      = (int)value;
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
// Blockages are supplied as keepout zones: user-defined zones plus the implicit
// solid-leaf-cell zones on LOW layers (Floorplan::low_layer_keepouts).  TOP
// layers see only the user zones, so they tile cells freely; containers are
// absent from the zone list and keep their full channel capacity (Gap 2).
// A zone with EMPTY layer_ids blocks every layer — the same convention as the
// topology predicates and NUTS's keepout_occupied (keepout-model audit).
// The cut position is passed DOUBLED (cut_coord_2x) so the coverage test is
// exact: the rounded cut_coord truncates a width-1 channel's half-integer
// midpoint onto its lower grid line, where a keepout that merely ABUTS the
// channel passed the closed covers test and falsely zeroed the band — STRICT
// then priced a physically routable band as hard overflow (audit C3-01; the
// same truncation hazard cut_coord_2x fixed for segment matching).
static double band_available_length(
        int cut_coord_2x, bool is_vcut,
        const std::vector<KeepoutZone>& keepouts,
        int layer_id,
        int band_lo, int band_hi)
{
    std::vector<std::pair<int,int>> blocked;
    for (const auto& koz : keepouts) {
        if (!koz.layer_ids.empty() && !koz.layer_ids.count(layer_id)) continue;
        const Rect& r = koz.bbox;
        bool covers = is_vcut
            ? (cut_coord_2x >= 2 * r.x1 && cut_coord_2x <= 2 * r.x2)
            : (cut_coord_2x >= 2 * r.y1 && cut_coord_2x <= 2 * r.y2);
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
    rebuild_cuts_();
    share_used_.clear();      // fresh books with fresh cuts (Phase 3)
    warn_above_top_layers_();
}

// Config-smell warning: a layer declared non-TOP but sitting ABOVE the TOP
// band in its direction is almost certainly a mis-declaration — it is a high,
// precious top-level metal, and the planner now treats it as TOP (denies the
// offload discount) rather than as a cheap low layer.  Warn once so the stack
// gets declared sanely (mark it TOP in def_layer).  Printed once per planner.
void CongestionPlanner::warn_above_top_layers_() {
    if (warned_above_top_) return;
    warned_above_top_ = true;
    std::vector<int> above;
    for (auto dir : {LayerDir::HORIZONTAL, LayerDir::VERTICAL})
        for (int lid : layers_.get_layer_ids_by_dir(dir))
            if (layers_.is_above_top(lid)) above.push_back(lid);
    if (above.empty()) return;
    std::cout << "[Planner] WARNING: layer(s)";
    for (int lid : above) {
        const Layer* L = layers_.get_layer(lid);
        std::cout << " " << (L ? L->name : ("M" + std::to_string(lid)));
    }
    std::cout << " are declared non-TOP but sit ABOVE the TOP band — a metal "
                 "above the TOP layers is a high top-level metal, not a cheap "
                 "low layer, yet the planner costs a non-TOP layer as a cheap "
                 "stub-offload target (base_cost_non_top).  Mark them TOP in "
                 "def_layer so the cost model treats them as the top-level "
                 "metal they are.\n";
}

void CongestionPlanner::rebuild_cuts_() {
    // Cut indices and band indices both move here, so any recorded charge
    // distribution is stale — drop it rather than replay it onto new bands.
    charge_log_.clear();
    cuts_.clear();
    // Injected-demand records key cuts by index; a rebuild reorders/resizes
    // cuts_, so the records are meaningless afterward (audit C3-03).  Drop
    // them here rather than letting apply_injected_ mischarge a reordered cut.
    injected_.clear();
    if (x_grid_.size() < 2 || y_grid_.size() < 2) return;

    auto blocks   = floorplan_.get_all_blocks();
    // Cache LEAF blocks only: every consumer (routed_extent,
    // low_seg_obstructed) skips hierarchy containers, and the per-iteration
    // is_container() string-set lookup was a measured chip-scale hotspot —
    // filter once here instead (same subset, same order: byte-identical).
    blocks_cache_.clear();
    for (const auto& b : blocks)
        if (!floorplan_.is_container(b.first)) blocks_cache_.push_back(b);
    int n_ybands = (int)y_grid_.size() - 1;
    int n_xbands = (int)x_grid_.size() - 1;

    auto v_layers = layers_.get_layer_ids_by_dir(LayerDir::VERTICAL);
    auto h_layers = layers_.get_layer_ids_by_dir(LayerDir::HORIZONTAL);
    if (v_layers.empty()) v_layers.push_back(5);
    if (h_layers.empty()) h_layers.push_back(4);

    // Keepouts seen by LOW (non-TOP) layers carry an implicit zone for every
    // solid leaf cell (Gap 2): a LOW segment cannot route over a cell, so the
    // band sitting on it has zero capacity.  Hierarchy containers stay
    // transparent — their internal channels keep capacity and accrue congestion
    // as LOW segments cross the child-edge cuts.  TOP layers (absent from
    // low_ids) cross cells freely, so the leaf zones never apply to them.
    std::vector<int> low_ids;
    for (int lid : v_layers) if (!layers_.is_top(lid)) low_ids.push_back(lid);
    for (int lid : h_layers) if (!layers_.is_top(lid)) low_ids.push_back(lid);
    auto keepouts = floorplan_.low_layer_keepouts(low_ids);

    // V-cuts: one per (X-channel midpoint, H-layer).
    // Perpendicular direction = Y → bands indexed by y_grid_.
    for (int i = 0; i + 1 < (int)x_grid_.size(); ++i) {
        int x_mid = (x_grid_[i] + x_grid_[i+1]) / 2;
        for (int lid : h_layers) {
            GlobalCut c;
            c.p1           = {x_mid, y_grid_.front()};
            c.p2           = {x_mid, y_grid_.back()};
            c.cut_coord    = x_mid;
            c.cut_coord_2x = x_grid_[i] + x_grid_[i+1];   // exact midpoint, doubled
            c.dir          = LayerDir::VERTICAL;
            c.layer_id  = lid;
            // Leaf cells reach LOW layers via `keepouts` (low_layer_keepouts),
            // so blocks no longer carve capacity directly.
            c.init_bands(n_ybands, [&](int b) {
                return band_available_length(c.cut_coord_2x, true, keepouts, lid,
                                             y_grid_[b], y_grid_[b+1]);
            });
            if (track_mode_for(lid))
                c.init_sig_ntrk([&](int b) {
                    // Exact midpoint sample (audit C3-01): the truncated
                    // x_mid shares band_available_length's abutting-keepout
                    // hazard inside count_signal_tracks_in's coverage test.
                    return grid_->get_layer_grid(lid).count_signal_tracks_in(
                        0.5 * c.cut_coord_2x,
                        (double)y_grid_[b], (double)y_grid_[b+1]);
                });
            cuts_.push_back(std::move(c));
        }
    }

    // H-cuts: one per (Y-channel midpoint, V-layer).
    // Perpendicular direction = X → bands indexed by x_grid_.
    for (int i = 0; i + 1 < (int)y_grid_.size(); ++i) {
        int y_mid = (y_grid_[i] + y_grid_[i+1]) / 2;
        for (int lid : v_layers) {
            GlobalCut c;
            c.p1           = {x_grid_.front(), y_mid};
            c.p2           = {x_grid_.back(),  y_mid};
            c.cut_coord    = y_mid;
            c.cut_coord_2x = y_grid_[i] + y_grid_[i+1];   // exact midpoint, doubled
            c.dir          = LayerDir::HORIZONTAL;
            c.layer_id  = lid;
            c.init_bands(n_xbands, [&](int b) {
                return band_available_length(c.cut_coord_2x, false, keepouts, lid,
                                             x_grid_[b], x_grid_[b+1]);
            });
            if (track_mode_for(lid))
                c.init_sig_ntrk([&](int b) {
                    return grid_->get_layer_grid(lid).count_signal_tracks_in(
                        0.5 * c.cut_coord_2x,
                        (double)x_grid_[b], (double)x_grid_[b+1]);
                });
            cuts_.push_back(std::move(c));
        }
    }

    // Rebuild the for_each_band range index (see cut_index_ in the header):
    // per (layer_id, dir), the (coord_2x, ci) pairs in ci order — which is
    // coordinate-ascending per layer by the build loops above, so the vector
    // is binary-searchable on coord_2x.
    // Flattened band offsets for the candidate scoring overlay (CandOverlay):
    // band_base_[ci] = first slot of cut ci; back() = total bands.  The fresh
    // generation stamp invalidates every thread's persistent overlay.
    band_base_.assign(cuts_.size() + 1, 0);
    for (int ci = 0; ci < (int)cuts_.size(); ++ci)
        band_base_[ci + 1] = band_base_[ci] + (size_t)cuts_[ci].num_bands();
    cuts_gen_ = ++g_cuts_generation;

    cut_index_.clear();
    for (int ci = 0; ci < (int)cuts_.size(); ++ci) {
        const GlobalCut& c = cuts_[ci];
        cut_index_[{c.layer_id, (int)c.dir}]
            .emplace_back((long)c.cut_coord_2x, ci);
    }

    // Report minimum per-band capacity per layer.  In SIGNAL_TRACKS mode the
    // figure is the minimum count of discrete SIGNAL tracks in a band (the unit
    // the planner now charges against); otherwise it is the geometric band
    // length minus keepouts.
    const bool tmode = (cap_mode_ == CapacityMode::SIGNAL_TRACKS && grid_ != nullptr);
    const char* unit = tmode ? "min_signal_tracks=" : "min_band_cap=";
    // Minimum over the bands of every cut on `lid`/`cut_dir` — track count in
    // SIGNAL_TRACKS mode (where the layer has a pattern), else geometric length.
    auto min_cap_for = [&](int lid, LayerDir cut_dir) -> double {
        double m = std::numeric_limits<double>::max();
        const bool ttrack = track_mode_for(lid);
        const auto& pgrid = (cut_dir == LayerDir::VERTICAL) ? y_grid_ : x_grid_;
        for (const auto& c : cuts_) {
            if (c.layer_id != lid || c.dir != cut_dir) continue;
            for (int b = 0; b < c.num_bands(); ++b) {
                double v;
                if (ttrack && b + 1 < (int)pgrid.size())
                    v = (double)grid_->get_layer_grid(lid)
                            .signal_tracks_in(0.5 * c.cut_coord_2x,
                                              (double)pgrid[b], (double)pgrid[b + 1]).size();
                else
                    v = c.cap(b);
                m = std::min(m, v);
            }
        }
        return m;
    };
    std::cout << "[Planner] Layer channel capacities"
              << (tmode ? " (signal-track mode):\n" : ":\n");
    for (int vid : v_layers) {
        double min_cap = min_cap_for(vid, LayerDir::HORIZONTAL);
        if (min_cap < std::numeric_limits<double>::max())
            std::cout << "  M" << vid << " (V)  " << unit << min_cap << "\n";
    }
    for (int hid : h_layers) {
        double min_cap = min_cap_for(hid, LayerDir::VERTICAL);
        if (min_cap < std::numeric_limits<double>::max())
            std::cout << "  M" << hid << " (H)  " << unit << min_cap << "\n";
    }
}

// ---------------------------------------------------------------------------
// Per-segment 2D score and apply
// ---------------------------------------------------------------------------

// Invoke fn(cut_index, band) for every cut/band this segment loads at the
// given layer — the single matching rule shared by scoring, application,
// contention collection, and victim-overlap ranking.
// For H-segments: V-cuts on that H-layer, in the Y-band of the segment.
// For V-segments: H-cuts on that V-layer, in the X-band of the segment.
//
// Non-TOP layers: segments run centre-to-centre, but the portion inside an
// ENDPOINT block is not routed on a block-obstructed layer — the connection
// lands on the block face.  Charging the in-block cuts would price every
// block-attached segment at cap=0 (9999) on every lower layer, making the
// non-TOP stack unusable for stubs.  Clamp the along-extent to the endpoint
// block faces before matching cuts.  Blocks merely crossed mid-span still
// block normally (capacity already excludes them).
void CongestionPlanner::routed_extent(const Segment& seg, int layer_id,
                                      int& lo, int& hi) const {
    bool is_h = (seg.start.y == seg.end.y);
    lo = is_h ? std::min(seg.start.x, seg.end.x) : std::min(seg.start.y, seg.end.y);
    hi = is_h ? std::max(seg.start.x, seg.end.x) : std::max(seg.start.y, seg.end.y);
    if (!layers_.is_top(layer_id)) {
        int perp = is_h ? seg.start.y : seg.start.x;
        for (const auto& [name, r] : blocks_cache_) {
            // Only true leaf cells clamp a non-TOP segment to their face (the
            // in-cell portion is internal pin access).  Hierarchy containers are
            // transparent (Gap 2) — already filtered out of blocks_cache_.
            (void)name;
            int rlo = is_h ? r.x1 : r.y1, rhi = is_h ? r.x2 : r.y2;
            int plo = is_h ? r.y1 : r.x1, phi = is_h ? r.y2 : r.x2;
            if (perp < plo || perp > phi) continue;
            bool lo_in = (lo >= rlo && lo <= rhi);
            bool hi_in = (hi >= rlo && hi <= rhi);
            if (lo_in && hi_in) { lo = hi; break; }  // fully inside: nothing routed here
            if (lo_in) lo = std::min(rhi, hi);       // left/bottom endpoint → block face
            if (hi_in) hi = std::max(rlo, lo);       // right/top endpoint → block face
        }
    }
}

void CongestionPlanner::for_each_cut_(const Segment& seg, int layer_id,
                                      int perp_pos_override,
                                      const std::function<void(int, bool, int)>& fn) const {
    bool is_h = (seg.start.y == seg.end.y);
    int  pp_h = (perp_pos_override != INT_MIN) ? perp_pos_override : seg.start.y;
    int  pp_v = (perp_pos_override != INT_MIN) ? perp_pos_override : seg.start.x;

    int lo, hi;
    routed_extent(seg, layer_id, lo, hi);

    // A zero-extent along-span (lo == hi) routes nothing here — e.g. a non-TOP
    // segment fully clamped inside its endpoint block above.  It must match no
    // cut; the half-open test did this implicitly, the closed test below needs
    // it explicit (a cut sitting exactly on that point would otherwise count).
    if (lo >= hi) return;

    // Closed interval [lo, hi]: a cut line is crossed by the segment iff the
    // cut midpoint lies anywhere on it, including its endpoints (Issue #22).
    // A half-open [lo, hi) silently drops a segment whose endpoint lands exactly
    // on a cut — which happens at every Z/U trunk junction, since cuts sit at
    // Hanan-cell midpoints and a trunk arm terminates on the midpoint cut it
    // connects to.  That under-counted real congestion (mirror arms cancelling,
    // a free bundle routing straight through a pinned bundle's hidden demand).
    // Closing the interval cannot double-count: an endpoint coincides with a cut
    // only at a trunk junction, where the two arms are perpendicular (counted on
    // different cut directions) or land in different perpendicular bands.
    //
    // The comparison runs in doubled coordinates (cut_coord_2x vs 2*lo / 2*hi)
    // so the exact half-integer midpoint of an odd-width cell is respected.
    // Using the rounded cut_coord would, on a width-1 cell whose midpoint
    // truncates onto the lower grid line, let a neighbour segment ending there
    // falsely charge this cell's cut.
    const long lo2 = 2L * lo, hi2 = 2L * hi;
    // Range lookup via cut_index_ (byte-identical to the historical full
    // scan: the per-(layer,dir) pairs are in ci order — the scan's own visit
    // order restricted to the matching cuts).  An H-seg crosses V-cuts, a
    // V-seg H-cuts.
    const auto it = cut_index_.find(
        {layer_id, (int)(is_h ? LayerDir::VERTICAL : LayerDir::HORIZONTAL)});
    if (it == cut_index_.end()) return;
    const auto& vec = it->second;
    auto lb = std::lower_bound(
        vec.begin(), vec.end(), std::make_pair(lo2, INT_MIN),
        [](const std::pair<long, int>& a, const std::pair<long, int>& b2) {
            return a.first < b2.first;
        });
    for (; lb != vec.end() && lb->first <= hi2; ++lb)
        fn(lb->second, /*is_vcut=*/is_h, is_h ? pp_h : pp_v);
}

void CongestionPlanner::for_each_band(const Segment& seg, int layer_id,
                                      int perp_pos_override,
                                      const std::function<void(int, int)>& fn) const {
    for_each_cut_(seg, layer_id, perp_pos_override,
                  [&](int ci, bool is_vcut, int pp) {
        int b = find_band(is_vcut, pp);
        if (b >= 0 && b < cuts_[ci].num_bands()) fn(ci, b);
    });
}

// Width-aware band charge (issue #518) — see the header for the rationale.
void CongestionPlanner::for_each_band_w(const Segment& seg, int layer_id,
                                        int perp_pos_override, double eff_width,
                                        const std::function<void(int, int, double)>& fn) const {
    // GEOMETRY uses the magnitude: a rip-up passes the charged demand NEGATED
    // (commit_plan's `sign`), and a negative width must still describe the same
    // footprint it was charged over — otherwise removal would fall through to
    // the single-band path and subtract the whole amount from the centre band
    // while the original was spread, leaving stale usage in the neighbours and
    // driving the centre negative (Codex #524 P1).  The sign rides the weights
    // via the caller's multiplication.
    const double footprint = std::fabs(eff_width);
    if (band_span_charge_ <= 0 || footprint <= 0.0) {
        for_each_band(seg, layer_id, perp_pos_override,
                      [&](int ci, int b) { fn(ci, b, 1.0); });
        return;
    }
    // Policy split (see the header): WHEN to spread, and HOW to allocate.
    const bool oversized_only = (band_span_charge_ == 1 || band_span_charge_ == 4);
    const bool greedy_fill    = (band_span_charge_ >= 3);
    const bool contiguous     = (band_span_charge_ == 5);

    const double half = 0.5 * footprint;
    // Scratch reused across cuts so a hot scoring loop does not allocate.
    std::vector<std::pair<int, double>> share;
    for_each_cut_(seg, layer_id, perp_pos_override,
                  [&](int ci, bool is_vcut, int pp) {
        const GlobalCut& c    = cuts_[ci];
        const auto&      grid = is_vcut ? y_grid_ : x_grid_;
        const int        nb   = std::min((int)grid.size() - 1, c.num_bands());
        if (nb <= 0) return;

        const int bc = find_band(is_vcut, pp);
        if (oversized_only) {
            // Spread ONLY a bus too wide for the band holding its centre —
            // #518's "no perp inside this band could ever hold it" case.
            // A bus that merely straddles a boundary was never mis-charged,
            // so spreading it only lowers its feasibility bar for free.
            if (bc < 0 || bc >= c.num_bands()) return;
            if (footprint <= (double)(grid[bc + 1] - grid[bc])) {
                fn(ci, bc, 1.0);
                return;
            }
        }

        if (greedy_fill && bc >= 0 && bc < c.num_bands()) {
            // Capacity-aware fill: saturate the preferred band, then spill
            // outward to the nearest band with room — NUTS's own preferred_fit
            // ("target the pull, spread to the nearest free track"), rather
            // than diluting the bus uniformly across bands that may already be
            // full.  Unlike the proportional rule this reads the CURRENT
            // usage, so demand routes around a locally-saturated neighbour
            // instead of piling phantom overflow onto it.
            auto free_of = [&](int b) {
                const double cap = usable_band_cap(c, b, is_vcut, INT_MIN, INT_MIN);
                return std::max(0.0, cap - cand_usage_(ci, b));
            };
            // The spill reach is deliberately UNBOUNDED (to the grid edges).
            // Capping it at one bus-width beyond the footprint was measured:
            // QoR fell from 2 better/1 worse to 0 better/3 worse and runtime
            // did not improve at all (+209% vs +207%) — the cost is
            // best_band_perp's relaxed candidate loop, not this walk.
            const int r_lo = 0, r_hi = nb - 1;

            share.clear();
            double remaining = footprint;
            const double take0 = std::min(remaining, free_of(bc));
            if (take0 > 0.0) { share.emplace_back(bc, take0); remaining -= take0; }
            int  lo = bc, hi = bc;
            bool wall_lo = false, wall_hi = false;
            while (remaining > 1e-9 &&
                   ((lo > r_lo && !wall_lo) || (hi < r_hi && !wall_hi))) {
                // Expand to whichever neighbour is geometrically nearer to pp.
                const bool can_lo = (lo > r_lo && !wall_lo);
                const bool can_hi = (hi < r_hi && !wall_hi);
                bool take_lo;
                if (can_lo && can_hi) {
                    const double d_lo = (double)pp - (double)grid[lo];
                    const double d_hi = (double)grid[hi + 1] - (double)pp;
                    take_lo = (d_lo <= d_hi);
                } else {
                    take_lo = can_lo;
                }
                const int b = take_lo ? --lo : ++hi;
                const double avail = free_of(b);
                // CONTIGUITY (mode 5).  Plain greedy fill spills to "the
                // nearest band with room", which lets the bus hop OVER a
                // saturated band and claim capacity on its far side — but
                // metal is contiguous and cannot skip a full band.  Treating a
                // zero-free band as a wall confines the bus to the contiguous
                // run of free capacity around its preferred band, which is the
                // conservatism the plain fill gives away.  On an empty layer
                // there are no walls, so #518's phantom overflow still
                // vanishes; the margin returns only under real congestion.
                if (contiguous && avail <= 1e-9) {
                    (take_lo ? wall_lo : wall_hi) = true;
                    continue;
                }
                const double take = std::min(remaining, avail);
                if (take > 0.0) { share.emplace_back(b, take); remaining -= take; }
            }
            // Nothing anywhere has room: the residue must still surface as
            // overflow, and it belongs on the band the bus actually prefers.
            if (remaining > 1e-9) {
                bool merged = false;
                for (auto& s : share)
                    if (s.first == bc) { s.second += remaining; merged = true; break; }
                if (!merged) share.emplace_back(bc, remaining);
            }
            for (const auto& [b, amt] : share) fn(ci, b, amt / footprint);
            return;
        }

        const double flo = pp - half, fhi = pp + half;
        // Clamp the footprint to the band range it can reach.  find_band
        // returns -1 off the grid, so fall back to the extremes: a footprint
        // hanging off the edge still charges the outermost band it touches
        // rather than vanishing.
        int b_lo = find_band(is_vcut, (int)std::floor(flo));
        int b_hi = find_band(is_vcut, (int)std::ceil(fhi));
        if (b_lo < 0) b_lo = (flo < (double)grid.front()) ? 0 : nb - 1;
        if (b_hi < 0) b_hi = (fhi > (double)grid.back())  ? nb - 1 : 0;
        b_lo = std::max(0, std::min(b_lo, nb - 1));
        b_hi = std::max(0, std::min(b_hi, nb - 1));
        if (b_lo > b_hi) std::swap(b_lo, b_hi);

        share.clear();
        double total = 0.0;
        for (int b = b_lo; b <= b_hi; ++b) {
            const double ov = std::min(fhi, (double)grid[b + 1])
                            - std::max(flo, (double)grid[b]);
            if (ov > 0.0) { share.emplace_back(b, ov); total += ov; }
        }
        // Degenerate footprint (zero overlap everywhere — e.g. a zero-width
        // band range): fall back to the centre band, i.e. legacy behaviour.
        if (total <= 0.0) {
            int b = find_band(is_vcut, pp);
            if (b >= 0 && b < c.num_bands()) fn(ci, b, 1.0);
            return;
        }
        for (const auto& [b, ov] : share) fn(ci, b, ov / total);
    });
}

// True if a non-TOP segment is obstructed by a leaf cell at this perp (Gap A).
// The endpoint-tail allowance in for_each_band assumes the in-cell portion of a
// block-attached stub is pin access on another layer — correct for a stub whose
// far end reaches an open channel.  But it accumulates across blocks and zeroes
// the whole along-extent when a (possibly trimmed) span ends up wholly inside a
// cell or crosses one mid-span, so such a segment looked *free* on LOW even
// though DetailedNUTS finds zero signal tracks over the cell.  This predicate
// flags exactly those cases so the layer-selection treats LOW as blocked and the
// bus routes over-the-cell on a TOP layer instead.
bool CongestionPlanner::low_seg_obstructed(const Segment& seg, int layer_id,
                                           int perp_pos_override) const {
    if (layers_.is_top(layer_id)) return false;
    bool is_h = (seg.start.y == seg.end.y);
    int perp = (perp_pos_override != INT_MIN) ? perp_pos_override
                                              : (is_h ? seg.start.y : seg.start.x);
    int lo = is_h ? std::min(seg.start.x, seg.end.x) : std::min(seg.start.y, seg.end.y);
    int hi = is_h ? std::max(seg.start.x, seg.end.x) : std::max(seg.start.y, seg.end.y);
    if (lo >= hi) return false;   // zero-length: nothing routed here

    // Endpoint leaf cells (at this perp): the ones owning a pin-access tail.
    const Rect* lo_cell = nullptr;
    const Rect* hi_cell = nullptr;
    for (const auto& [name, r] : blocks_cache_) {   // leaf blocks only
        (void)name;
        int rlo = is_h ? r.x1 : r.y1, rhi = is_h ? r.x2 : r.y2;
        int plo = is_h ? r.y1 : r.x1, phi = is_h ? r.y2 : r.x2;
        if (perp < plo || perp > phi) continue;
        if (lo >= rlo && lo <= rhi) lo_cell = &r;
        if (hi >= rlo && hi <= rhi) hi_cell = &r;
    }
    // Wholly inside one cell → no open-channel portion → unroutable on LOW.
    if (lo_cell && lo_cell == hi_cell) return true;

    // Trim the two pin-access tails back to their cell faces, then any leaf cell
    // still overlapping the open interior is a genuine mid-span crossing.
    int tlo = lo, thi = hi;
    if (lo_cell) tlo = std::min(is_h ? lo_cell->x2 : lo_cell->y2, hi);
    if (hi_cell) thi = std::max(is_h ? hi_cell->x1 : hi_cell->y1, tlo);
    if (tlo >= thi) {
        // Empty open interior.  Two DISTINCT endpoint cells with no gap
        // between their faces = an ABUTMENT CROSSING: the whole span lies
        // inside the two cells' union footprint, so a LOW layer has zero
        // unblocked signal tracks anywhere in the slide window — a
        // guaranteed DetailedNUTS open (big2's 72 stranded bits).  A single
        // endpoint tail meeting the far end, or tails meeting in a real gap,
        // stay routable as before (pin access reaches an open channel).
        return lo_cell && hi_cell && lo_cell != hi_cell;
    }

    for (const auto& [name, r] : blocks_cache_) {   // leaf blocks only
        (void)name;
        int rlo = is_h ? r.x1 : r.y1, rhi = is_h ? r.x2 : r.y2;
        int plo = is_h ? r.y1 : r.x1, phi = is_h ? r.y2 : r.x2;
        if (perp < plo || perp > phi) continue;
        if (rlo < thi && rhi > tlo) return true;   // overlaps interior → crossing
    }
    return false;
}

int CongestionPlanner::top_height_rank(int layer_id) const {
    const Layer* L = layers_.get_layer(layer_id);
    if (!L || !layers_.is_top(layer_id)) return 0;
    std::vector<int> ids;
    for (int lid : layers_.get_layer_ids_by_dir(L->dir))
        if (layers_.is_top(lid)) ids.push_back(lid);
    std::sort(ids.begin(), ids.end());
    auto it = std::find(ids.begin(), ids.end(), layer_id);
    return (it == ids.end()) ? 0 : (int)(it - ids.begin());
}

// ── Effective-TOP under a layer policy (hier_layer_caps.md §4.3) ────────────
bool CongestionPlanner::is_top_for(const BundleInput& in, int lid) const {
    if (in.allowed_layers.empty()) return layers_.is_top(lid);
    if (!in.allows_layer(lid)) return false;
    const Layer* L = layers_.get_layer(lid);
    if (!L) return false;
    bool any_top = false;
    int  hi      = -1;
    for (int a : in.allowed_layers) {
        const Layer* A = layers_.get_layer(a);
        if (!A || A->dir != L->dir) continue;
        if (layers_.is_top(a)) any_top = true;
        hi = std::max(hi, a);
    }
    // Allowed global-TOP layers exist in this direction: TOP-ness unchanged.
    // None: promote the highest allowed layer of the direction.
    return any_top ? layers_.is_top(lid) : (lid == hi);
}

int CongestionPlanner::top_height_rank_for(const BundleInput& in, int lid) const {
    if (in.allowed_layers.empty()) return top_height_rank(lid);
    const Layer* L = layers_.get_layer(lid);
    if (!L || !is_top_for(in, lid)) return 0;
    std::vector<int> ids;
    for (int a : layers_.get_layer_ids_by_dir(L->dir))
        if (is_top_for(in, a)) ids.push_back(a);
    std::sort(ids.begin(), ids.end());
    auto it = std::find(ids.begin(), ids.end(), lid);
    return (it == ids.end()) ? 0 : (int)(it - ids.begin());
}

int CongestionPlanner::effective_top_layer(const BundleInput& in, LayerDir dir) const {
    if (in.allowed_layers.empty()) return layers_.get_top_layer(dir);
    int best = -1;
    for (int a : in.allowed_layers) {
        const Layer* A = layers_.get_layer(a);
        if (!A || A->dir != dir) continue;
        if (is_top_for(in, a)) best = std::max(best, a);
    }
    return best;
}

// Score the marginal peak overflow from adding one segment at a specific layer.
double CongestionPlanner::score_segment(const Segment& seg, int layer_id,
                                   double eff_width, int perp_pos_override,
                                   int slide_lo, int slide_hi) const {
    // A LOW segment obstructed by a leaf cell at this perp is unroutable here
    // (Gap A): report a hard overflow so STRICT skips the layer and the bus
    // routes over-the-cell on TOP.
    if (low_seg_obstructed(seg, layer_id, perp_pos_override)) return 9999.0;
    bool   is_vcut_dir = (seg.start.y == seg.end.y);   // H-seg crosses V-cuts
    double peak = 0.0;
    // The footprint MUST be the same width that is charged (eff + pitch), not
    // the raw eff_width: commit_plan/apply_segment distribute over eff + pitch,
    // and deriving the weights here from a narrower footprint would let STRICT
    // score one set of bands and commit another (Codex #524 P1).
    const double charged = eff_width + track_pitch_;   // Gap 1
    for_each_band_w(seg, layer_id, perp_pos_override, charged,
                    [&](int ci, int b, double w) {
        const GlobalCut& c = cuts_[ci];
        double cap = usable_band_cap(c, b, is_vcut_dir, slide_lo, slide_hi);
        double ov  = (cand_usage_(ci, b) + charged * w) - cap;
        if (ov > peak) peak = ov;
    });

    return std::max(peak, 0.0);
}

// Variant C of the junction-extension gate (charge_pull_target follow-on
// (a2)): true iff the span crosses a band with (near-)ZERO usable capacity —
// a keepout-carved band the metal physically cannot exist in, the demo-b3
// signature.  Deliberately NOT any-overflow: gating the conservative
// extension on load pressure over-rejects stretched-but-fine survivors
// (measured: mix healed endpoint 0->2 ov / 21 opens under the any-overflow
// form), while a zero-capacity band is impossibility, not pressure.
bool CongestionPlanner::span_hits_dead_band(const Segment& seg, int layer_id,
                                            int perp_pos_override,
                                            int slide_lo, int slide_hi) const {
    bool is_vcut_dir = (seg.start.y == seg.end.y);
    bool dead = false;
    for_each_band(seg, layer_id, perp_pos_override, [&](int ci, int b) {
        const GlobalCut& c = cuts_[ci];
        if (usable_band_cap(c, b, is_vcut_dir, slide_lo, slide_hi) <= 1e-9)
            dead = true;
    });
    return dead;
}

// The band-set sibling of score_segment: records WHICH bands overflow rather
// than reducing to a scalar.  Bands whose overflow stems purely from the
// slide-window clamp (zero usage) still get recorded but are harmless for
// victim ranking — no committed bundle loads them, so they contribute zero
// overlap.
void CongestionPlanner::collect_overflow_bands(const Segment& seg, int layer_id,
                                               double eff_width, int perp_pos_override,
                                               int slide_lo, int slide_hi,
                                               std::set<std::pair<int,int>>& out) const {
    bool is_vcut_dir = (seg.start.y == seg.end.y);
    // Must stay the exact predicate score_segment peaks over, or the victim
    // ranking would chase bands the scorer never charged.
    const double charged = eff_width + track_pitch_;   // same footprint as score_segment
    for_each_band_w(seg, layer_id, perp_pos_override, charged,
                    [&](int ci, int b, double w) {
        const GlobalCut& c = cuts_[ci];
        double cap = usable_band_cap(c, b, is_vcut_dir, slide_lo, slide_hi);
        if ((cand_usage_(ci, b) + charged * w) - cap > 0.0) out.insert({ci, b});
    });
}

// Total effective width a committed plan contributes to the given band set.
// Rip-up victims are ranked by this: ripping a bundle that loads none of the
// contended bands cannot relieve the failing bundle.
double CongestionPlanner::plan_band_overlap(const BundleWrapper& bw,
                                            const PlanResult& plan,
                                            const std::set<std::pair<int,int>>& contended) const {
    const int nbits = (int)bw.input.original_bundle.get_net_names().size();
    const Topology& t = bw.input.candidates[plan.best_topo];
    double overlap = 0.0;
    for (int si = 0; si < (int)t.segments.size() && si < (int)plan.seg_layers.size(); ++si) {
        int pp  = (si < (int)plan.seg_perp.size()) ? plan.seg_perp[si] : INT_MIN;
        int lid = plan.seg_layers[si];
        const int    n = ndr_units(bw.input, seg_bit_count(t, si, nbits));
        const double w = (nbits > 0 && n != nbits)
                             ? bw.input.width * ((double)n / (double)nbits)
                             : bw.input.width;
        double eff = layers_.eff_bus_width(n, w, lid) + track_pitch_;  // +pitch: Gap 1
        for_each_band_w(t.segments[si], lid, pp, eff,
                        [&](int ci, int b, double wt) {
            if (contended.count({ci, b})) overlap += eff * wt;
        });
    }
    return overlap;
}

void CongestionPlanner::share_usage_of_(const BundleWrapper& bw, const Topology& t,
                                        const std::vector<int>& seg_layers,
                                        std::map<int,double>& out) const {
    // The budget's usage metric: the plan's summed effective widths per
    // SHARED layer — physical demand only (no +pitch margin), matching the
    // session-side budget units (s × tracks-in-bbox × bit_pitch).
    const int nbits = (int)bw.input.original_bundle.get_net_names().size();
    for (int si = 0; si < (int)t.segments.size() && si < (int)seg_layers.size(); ++si) {
        const int lid = seg_layers[si];
        if (lid < 0 || bw.input.share_of(lid) >= 1.0) continue;
        const int    n = ndr_units(bw.input, seg_bit_count(t, si, nbits));
        const double w = (nbits > 0 && n != nbits)
                             ? bw.input.width * ((double)n / (double)nbits)
                             : bw.input.width;
        out[lid] += layers_.eff_bus_width(n, w, lid);
    }
}

bool CongestionPlanner::share_budget_ok_(const BundleWrapper& bw, const Topology& t,
                                         const std::vector<int>& seg_layers) const {
    if (bw.input.share_group.empty() || bw.input.layer_shares.empty())
        return true;
    std::map<int,double> usage;
    share_usage_of_(bw, t, seg_layers, usage);
    for (const auto& [lid, u] : usage) {
        const double budget = bw.input.share_budget_of(lid);
        if (budget < 0.0) continue;              // no budget declared
        auto it = share_used_.find({bw.input.share_group, lid});
        const double used = (it == share_used_.end()) ? 0.0 : it->second;
        if (used + u > budget + 1e-9) return false;
    }
    return true;
}

double CongestionPlanner::usable_band_cap(const GlobalCut& c, int b, bool is_vcut,
                                          int slide_lo, int slide_hi) const {
    // Signal-track capacity (Gap A part 2): count the discrete SIGNAL tracks the
    // layer's pattern places inside the band — clamped to the slide window — times
    // the layer's bit pitch so the result is in the same width units the callers'
    // eff_bus_width demand is.  ntrk*bit_pitch vs nbits*bit_pitch reduces to the
    // exact integer test nbits <= ntrk, so a band whose width fit but whose track
    // count is short of the bit count now reports overflow (it would have been a
    // silent DetailedNUTS open).  The grid's own keepouts/overrides are honoured
    // by signal_tracks_in, matching what DetailedNUTS will actually place.
    if (track_mode_for(c.layer_id)) {
        const auto& tgrid = is_vcut ? y_grid_ : x_grid_;
        if (b + 1 >= (int)tgrid.size()) return 0.0;
        const int blo = tgrid[b], bhi = tgrid[b + 1];
        int lo = blo, hi = bhi;
        if (slide_lo != INT_MIN) {
            lo = std::max(lo, slide_lo);
            hi = std::min(hi, slide_hi);
        }
        if (lo >= hi) return 0.0;
        // Full-band (slide window does not narrow it): use the count cached once in
        // rebuild_cuts_.  Only a genuinely narrowed window walks the pattern.
        int ntrk = (lo <= blo && hi >= bhi && c.has_sig_ntrk())
            ? c.sig_ntrk(b)
            : grid_->get_layer_grid(c.layer_id)
                  .count_signal_tracks_in(0.5 * c.cut_coord_2x,
                                          (double)lo, (double)hi);
        double cap = ((double)ntrk + track_cap_slack_) *
                     layers_.eff_bus_width(1, 1.0, c.layer_id);   // * bit pitch
        // Fractional share (Phase 3 Tier 2): the wrapper under evaluation
        // sees s × supply on a layer its cell only fractionally leases —
        // the one multiply of hier_layer_caps.md §6.  Null context / share
        // 1.0 = unchanged.
        if (cur_share_input_ != nullptr)
            cap *= cur_share_input_->share_of(c.layer_id);
        if (cap <= 0.0) return 0.0;
        return cap + track_pitch_;
    }
    double cap = c.cap(b);
    if (slide_lo != INT_MIN) {
        const auto& grid = is_vcut ? y_grid_ : x_grid_;
        if (b + 1 < (int)grid.size()) {
            double win = std::min(grid[b + 1], slide_hi) -
                         std::max(grid[b],     slide_lo);
            cap = std::min(cap, std::max(win, 0.0));
        }
    }
    // Inter-bus pitch margin (Gap 1): a band hosting k buses needs (k-1)*pitch
    // beyond the summed eff widths.  Each segment is charged eff + pitch (see
    // score/cong/collect/apply), so granting one free pitch here leaves single-
    // bus bands unaffected while reserving spacing for the rest.  A physically
    // blocked band (cap 0 — e.g. a leaf-cell keepout) stays a hard block.
    if (cap <= 0.0) return 0.0;
    return cap + track_pitch_;
}

void CongestionPlanner::apply_segment(const Segment& seg, int layer_id, double eff_width,
                                      int perp_pos_override) {
    // The actual charge — must spread exactly as score_segment prices it, or
    // the books and the scorer disagree about where the demand went.  While
    // the candidate undo log records (plan_bundle), save each touched band's
    // pre-charge value for the exact-restore rollback.
    for_each_band_w(seg, layer_id, perp_pos_override, eff_width,
                    [&](int ci, int b, double w) {
        if (t_cand_overlay_) {
            // Scoring: charge the candidate's overlay, not cuts_.  First touch
            // seeds the band's committed value, so `+=` here is the same
            // arithmetic the in-place charge performed (bit-identical reads).
            auto&        ov = *t_cand_overlay_;
            const size_t k  = band_base_[ci] + (size_t)b;
            if (ov.stamp[k] != ov.epoch) {
                ov.stamp[k] = ov.epoch;
                ov.val[k]   = cuts_[ci].usage(b);
            }
            ov.val[k] += eff_width * w;
        } else {
            cuts_[ci].add_usage(b, eff_width * w);
        }
    });
}

// Park (sign=+1) or release (sign=-1) an unplanned bundle's demand as virtual
// usage on the TOP-layer bands inside its reservation region (the parent cell
// instance bbox).  Because congestion cost is overflow-based, this repels an
// earlier-planned bundle from a region band ONLY when the band cannot hold
// both of them — it is a "leave room" constraint, not a keep-out.
void CongestionPlanner::apply_reservation(const BundleWrapper& bw, double sign) {
    if (!bw.hier.has_reservation) return;
    const int nbits = (int)bw.input.original_bundle.get_net_names().size();
    // Under a layer policy the reservation parks on the cell's EFFECTIVE top
    // layers — a capped leaf must not reserve room on high metal it can never
    // use (hier_layer_caps.md F3).  Unmasked = the global top pair, unchanged.
    int top_h = effective_top_layer(bw.input, LayerDir::HORIZONTAL);
    int top_v = effective_top_layer(bw.input, LayerDir::VERTICAL);
    for (auto& c : cuts_) {
        // The bundle's H demand rides V-cuts on the TOP H layer; its V demand
        // rides H-cuts on the TOP V layer.  Fractional shares (Phase 3 Q2,
        // resolved: proportional, never over-reserve): a SHARED layer of the
        // matching direction additionally parks s × eff — the share is the
        // legal upper bound on the cell's eventual consumption there.  The
        // eff basis is the session LayerStack's (global-pattern) width — the
        // thinned view's is ~1/s inflated, so s × thinned would silently
        // rebuild the full-width over-reservation the resolution rejects.
        bool is_vcut = (c.dir == LayerDir::VERTICAL);
        int  lid     = is_vcut ? top_h : top_v;
        double frac  = 0.0;
        if (lid >= 0 && c.layer_id == lid) {
            // An effective-TOP layer that ALSO carries a share (thinned
            // inside the band) reserves the leased fraction, not full
            // width — share_of is 1.0 for unshared layers, so the plain
            // policy is unchanged (Codex #547).
            frac = bw.input.share_of(c.layer_id);
        } else if (!bw.input.layer_shares.empty()) {
            double s = bw.input.share_of(c.layer_id);
            if (s < 1.0 &&
                layers_.get_layer_dir(c.layer_id) ==
                    (is_vcut ? LayerDir::HORIZONTAL : LayerDir::VERTICAL))
                frac = s;
        }
        if (frac <= 0.0) continue;
        lid = c.layer_id;
        // Cut must lie inside the region along the cut axis.
        int clo = is_vcut ? bw.hier.res_x1 : bw.hier.res_y1;
        int chi = is_vcut ? bw.hier.res_x2 : bw.hier.res_y2;
        if (c.cut_coord < clo || c.cut_coord > chi) continue;
        const int    rn = ndr_units(bw.input, nbits);
        const double rw = (nbits > 0 && rn != nbits)
                              ? bw.input.width * ((double)rn / (double)nbits)
                              : bw.input.width;
        double eff = frac * (layers_.eff_bus_width(rn, rw, lid)
                             + track_pitch_);   // +pitch: Gap 1
        // Every band overlapping the region's perpendicular range could be
        // the bundle's eventual home, so each carries the reservation.
        const auto& grid = is_vcut ? y_grid_ : x_grid_;
        int plo = is_vcut ? bw.hier.res_y1 : bw.hier.res_x1;
        int phi = is_vcut ? bw.hier.res_y2 : bw.hier.res_x2;
        for (int b = 0; b + 1 < (int)grid.size() && b < c.num_bands(); ++b) {
            if (grid[b + 1] <= plo || grid[b] >= phi) continue;
            c.add_usage(b, sign * eff);
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
    if (low_seg_obstructed(seg, layer_id, perp_pos_override))
        return kCong_ * 9999.0;   // Gap A: LOW over a leaf cell is unroutable
    bool   is_vcut_dir = (seg.start.y == seg.end.y);   // H-seg crosses V-cuts
    double peak_cost   = 0.0;
    bool   blocked     = false;
    const double charged = eff_width + track_pitch_;   // same footprint as score_segment
    for_each_band_w(seg, layer_id, perp_pos_override, charged,
                    [&](int ci, int b, double w) {
        const GlobalCut& c = cuts_[ci];
        double cap = usable_band_cap(c, b, is_vcut_dir, slide_lo, slide_hi);
        if (cap <= 0.0) { blocked = true; return; }
        double ov = cand_usage_(ci, b) + charged * w - cap;
        if (ov <= 0.0) return;     // fits — no cost
        peak_cost = std::max(peak_cost, kCong_ * ov / cap);
    });
    if (blocked) return kCong_ * 9999.0;
    return peak_cost;
}

// Peak EXISTING band utilization over the bands this segment would use — the
// routability term behind `set_planner_param kPeak` (see the header).  Unlike
// cong_cost_segment this is NOT overflow-gated: a band others filled to 95%
// reports 0.95 even though this segment still fits, so candidate ranking can
// steer off nearly-full bands before they burst.
//
// Deliberately PRE-charge (usage/cap, the candidate's own eff_width excluded
// from the numerator): post-charge utilization was measured to be the wrong
// quantity — on an uncongested design it reduces to (eff+pitch)/cap, an
// intrinsic "how narrow is this channel" penalty that biases AGAINST the
// column channels the BITRUNK datapath trees deliberately use (datapath WL
// regressed ~2-20% across the sweep).  Pre-charge is zero on empty bands
// (the term is inert exactly when there is nothing to route around) and
// prices only contention accumulated from earlier-committed bundles — plus
// this candidate's own earlier segments, which plan_bundle applies to the
// running cut state, so a candidate stacking several stubs into one band
// prices its own pile-up too.  eff_width is still what steers WHICH band the
// charge would land in (best_band_perp upstream); it is only excluded from
// the price.  cap<=0 (blocked) bands are skipped — cong_cost_segment already
// hard-prices them.
double CongestionPlanner::peak_util_segment(const Segment& seg, int layer_id,
                                            int perp_pos_override,
                                            int slide_lo, int slide_hi,
                                            double tracks_needed,
                                            bool proportional_floor) const {
    bool   is_vcut_dir = (seg.start.y == seg.end.y);   // H-seg crosses V-cuts
    double peak = 0.0;
    // Deliberately NOT width-spread (issue #518): this term takes no eff_width
    // — it prices EXISTING fill, and its own absolute-supply floor below
    // already reasons about the span's real track pool.  Both knobs are
    // opt-in, so the pairing is an edge case; revisit if kPeak's default flips.
    for_each_band(seg, layer_id, perp_pos_override, [&](int ci, int b) {
        const GlobalCut& c = cuts_[ci];
        double cap = usable_band_cap(c, b, is_vcut_dir, slide_lo, slide_hi);
        if (cap <= 0.0) return;
        double util = cand_usage_(ci, b) / cap;
        if (util > peak) peak = util;
    });

    // ABSOLUTE-SUPPLY floor (the big2 stranding fix).  usage/cap is purely
    // RELATIVE, so a band can report util=0 — maximally attractive — while
    // its real signal-track supply along the segment's span is too small to
    // host the bus at all: empty because nothing CAN route there, and the
    // relative term then actively steers wide trunks into exactly those
    // windows (big2: two 56/60-bit trunks stranded on supply-poor M4, ~116
    // DNUTS opens).  When the layer has a def_track_pattern, count the real
    // span-wide SIGNAL tracks in the band's perpendicular window — the same
    // span-aware, keepout-aware pool DetailedNUTS itself places from
    // (count_signal_tracks_in_span) — and clamp util to >= 1 when it cannot
    // host tracks_needed bits: a can't-host band must never rank better
    // than a 100%-full one.  The along-extent uses the same endpoint-block
    // clamp as the band charge (routed_extent), so LOW-layer pin-access
    // tails don't false-floor short stubs.  No track_cap_slack here: the
    // slack is a quantisation allowance for the HARD integer capacity;
    // this is a soft steering term and an exact shortfall is exactly the
    // signal it exists to price.  tracks_needed <= 0 (unknown width)
    // or a pattern-less layer keeps the pure relative behavior.
    if (tracks_needed > 0.0 && (peak < 1.0 || proportional_floor)) {
        int  pp     = (perp_pos_override != INT_MIN)
                          ? perp_pos_override
                          : ((seg.start.y == seg.end.y) ? seg.start.y : seg.start.x);
        int supply = span_signal_supply(seg, layer_id, pp, slide_lo, slide_hi);
        if (supply >= 0) {
                    // Deliberately the STRICT span-clear pool, NOT DetailedNUTS's
                    // admission policy (which retries the midpoint pool when the
                    // span pool falls short).  That retry is an admission
                    // optimism whose safety net is cull_keepout_crossers: bits
                    // admitted via the midpoint pool whose final span still
                    // crosses the keepout are culled into opens.  Mirroring the
                    // retry here was measured and rejected — it un-floors bands
                    // whose span-pool shortfall is real, and the buses steered
                    // back into them strand (kPeak 0.1: mix opens 86 -> 134,
                    // hbundles/06 2 -> 42, worse than its 34 baseline).  The
                    // span-clear pool is the conservative predictor of what
                    // SURVIVES placement; the cost of its rare false positive is
                    // a slightly longer route, the cost of the fallback's false
                    // negative is opens — lexicographically worse.
                    //
                    // TWO floor shapes, chosen by the CALLER'S comparison
                    // scope.  The SEGMENT SCORE (plan_bundle) uses the flat
                    // 1.0: it compares across topologies and layers, whose
                    // other terms live on a small scale, and the globally
                    // proportional needed/supply clamp was measured and
                    // REJECTED there — the region above 1.0 leaked into that
                    // competition against overflow-priced alternatives and
                    // mix regressed decisively (kPeak 0.1: overlaps 20 -> 40,
                    // opens 86 -> 107), even though it correctly flipped a
                    // synthetic all-bands-floored scenario to the least-bad
                    // band.  The BAND CHOICE (best_band_perp) opts into the
                    // proportional shape (proportional_floor): its comparison
                    // is INTRA-segment by construction — same segment, same
                    // layer, same topology, only "which band inside the slide
                    // window" — so a large value cannot distort cross-option
                    // costs, and among bands that all fall short it steers
                    // seg_perp to the least-impossible one (7-for-8 beats
                    // 3-for-8) instead of tying at 1.0 and losing to
                    // whichever is nearest.  The denominator clamps at 0.5,
                    // NOT 1.0, so a ZERO-track band (POWER-only / keepout —
                    // positive geometric cap, no routable tracks) prices
                    // 2*needed, STRICTLY worse than a 1-track band's needed:
                    // best_pp starts at the window centre and stands on ties,
                    // so a 0-vs-1 tie would leave the anchor on the band
                    // where nothing can route at all.
                    if ((double)supply < tracks_needed)
                        peak = proportional_floor
                                   ? std::max(peak, tracks_needed /
                                                  std::max(0.5, (double)supply))
                                   : 1.0;
        }
    }
    return peak;
}

// Real span-wide SIGNAL-track supply — the DetailedNUTS pool (see header).
// -1 when no supply model applies (leave the width model in charge).
int CongestionPlanner::span_signal_supply(const Segment& seg, int layer_id,
                                          int pp, int slide_lo, int slide_hi,
                                          bool with_midpoint_fallback,
                                          bool use_raw_span) const {
    if (grid_ == nullptr || !grid_->has_layer(layer_id)) return -1;
    bool is_h = (seg.start.y == seg.end.y);
    int lo, hi;
    if (use_raw_span) {
        // The RAW segment along-span DetailedNUTS's BusSegment carries
        // (nuts.cpp:1195) — NOT the face-clamped routed_extent, which would
        // hide a keepout covering only the in-cell tail from the gate.
        lo = is_h ? std::min(seg.start.x, seg.end.x)
                  : std::min(seg.start.y, seg.end.y);
        hi = is_h ? std::max(seg.start.x, seg.end.x)
                  : std::max(seg.start.y, seg.end.y);
    } else {
        routed_extent(seg, layer_id, lo, hi);
    }
    if (lo >= hi) return -1;                     // nothing routed here
    const auto& pgrid = is_h ? y_grid_ : x_grid_;
    int b = find_band(/*is_vcut=*/is_h, pp);
    if (b < 0 || b + 1 >= (int)pgrid.size()) return -1;
    // The perp window NUTS/DNUTS can actually use: the Hanan band, intersected
    // with the slide window when one is known.
    double w_lo = pgrid[b], w_hi = pgrid[b + 1];
    if (slide_lo != INT_MIN) w_lo = std::max(w_lo, (double)slide_lo);
    if (slide_hi != INT_MIN) w_hi = std::min(w_hi, (double)slide_hi);
    if (w_lo >= w_hi) return -1;
    const RoutingGrid& g = grid_->get_layer_grid(layer_id);
    int span_pool = g.count_signal_tracks_in_span((double)lo, (double)hi,
                                                  w_lo, w_hi);
    if (!with_midpoint_fallback) return span_pool;
    // Mirror DetailedNUTS's admission: when the span-clear pool is short it
    // retries the along-MIDPOINT pool (a point query at (lo+hi)/2 across the
    // same perp window), and admits on whichever is larger.  The gate must
    // predict that, or it rejects a layer DNUTS would in fact place on.
    int mid_pool = g.count_signal_tracks_in(0.5 * ((double)lo + (double)hi),
                                            w_lo, w_hi);
    return std::max(span_pool, mid_pool);
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
                                      int slide_lo, int slide_hi,
                                      double tracks_needed) const {
    bool is_h = (seg.start.y == seg.end.y);
    const auto& grid = is_h ? y_grid_ : x_grid_;
    const int centre = (slide_lo + slide_hi) / 2;
    if ((int)grid.size() < 2) return centre;

    // Band-choice metric.  cong_cost_segment is overflow-only — every
    // below-capacity band in the slide window costs 0, and the nearest wins
    // the tie — so with kPeak enabled the EXISTING utilization must join the
    // metric here too, or the routability term would only price the band the
    // legacy tie-break already chose: the charge (and NUTS's seg_perp) could
    // still land in a nearly-full band with an empty one available in the
    // same window (Codex #252 P2).  Gated on kPeak_ so the default band
    // choice is bit-identical.  The supply floor here is PROPORTIONAL
    // (needed/supply) rather than the segment score's flat 1.0: this
    // comparison is intra-segment, so the large values cannot leak into
    // topology/layer competition, and among bands that all fall short the
    // charge (and NUTS's seg_perp) steers to the least-impossible one —
    // see the floor-shape comment in peak_util_segment.
    auto band_cost = [&](int pp) {
        double c = cong_cost_segment(seg, layer_id, eff_width, pp,
                                     slide_lo, slide_hi);
        if (kPeak_ > 0.0)
            c += kPeak_ * peak_util_segment(seg, layer_id, pp,
                                            slide_lo, slide_hi, tracks_needed,
                                            /*proportional_floor=*/true);
        return c;
    };

    int    best_pp   = centre;
    double best_cost = band_cost(centre);
    int    best_dist = 0;

    for (int b = 0; b + 1 < (int)grid.size(); ++b) {
        int win_lo = std::max(grid[b],     slide_lo);
        int win_hi = std::min(grid[b + 1], slide_hi);
        // "Band can't host the bus" — skip it.  Kept even under
        // band_span_charge, where the bus MAY occupy neighbouring bands and
        // this skip therefore looks too strict: relaxing it was implemented
        // and measured, and it lost on both axes — QoR 3 better/1 worse ->
        // 2 better/1 worse, and runtime +12% -> +207%, because every band
        // then enters the candidate loop and runs a full cost evaluation.
        // For an oversized bus the fallback (slide-window centre) is both
        // cheaper and a better spreading centre.  See band_span_charge.md.
        if (win_hi - win_lo < eff_width) continue;
        int pp = (win_lo + win_hi) / 2;              // centre of the usable window
        double cost = band_cost(pp);
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
// Per-bundle candidate scoring
// ---------------------------------------------------------------------------

// Honest-books junction prediction (charge_pull_target follow-on (a)).
// NUTS realizes junctions with the partners' PLACED positions: a stub's span
// stretches to reach its trunk's placed track (do_span_adjustments), and a
// single-junction segment's own track clamps into its rider's span (the
// anchor rule).  Both were invisible to plan-time scoring, which used nominal
// geometry — comprehensive_demo b3: a 35-unit nominal MST stub scored clean
// while its pulled trunk's predicted track (450) stretches it 200 units
// across the M4 keepout, stranding a bit.  With the pull targets now
// DETERMINISTIC (the breakpoint clamp), the stretch is predictable:
// return a copy of the topology's segments with each along-span EXTENDED
// (never retracted — conservative books) to every pulled junction partner's
// layer-independent predicted track (window bound tightened by an in-travel
// ConnSeg::pull_break; the per-layer bus-width clamp happens at charge time
// where eff is known).  `riders_out` (optional) collects the NUTS jn_map
// mirror — segments whose SEG conns land on each segment — for the
// single-rider anchor clamp (a1).  Callers gate on charge_pull_target_.
std::vector<Segment> CongestionPlanner::junction_extended_segments(
        const Topology& topo, const std::vector<ConnSeg>& conn_segs,
        std::vector<std::vector<int>>* riders_out) const {
    const int n = (int)conn_segs.size();
    std::vector<int> pred_perp(n, INT_MIN);
    if (riders_out) riders_out->assign(n, {});
    for (int i = 0; i < n; ++i) {
        const ConnSeg& c2 = conn_segs[i];
        if (riders_out)
            for (const auto& cn : c2.conns)
                if (cn.kind == SegConn::SEG && cn.seg_idx >= 0 &&
                    cn.seg_idx < n)
                    (*riders_out)[cn.seg_idx].push_back(i);
        if (c2.net_pull == 0) continue;
        const auto& pg = c2.horiz ? y_grid_ : x_grid_;
        if (pg.empty()) continue;
        const int lo = std::max(c2.perp_lo, pg.front());
        const int hi = std::min(c2.perp_hi, pg.back());
        if (lo > hi) continue;
        double pref = (c2.net_pull > 0) ? (double)hi : (double)lo;
        if (c2.pull_break != INT_MIN) {
            const double bp = (double)c2.pull_break;
            if (c2.net_pull > 0 && bp > c2.perp_pos && bp < pref)      pref = bp;
            else if (c2.net_pull < 0 && bp < c2.perp_pos && bp > pref) pref = bp;
        }
        pred_perp[i] = (int)std::lround(pref);
    }
    std::vector<Segment> out(topo.segments);
    for (int si = 0; si < (int)out.size() && si < n; ++si) {
        for (const auto& cn : conn_segs[si].conns) {
            if (cn.kind != SegConn::SEG) continue;
            const int j = cn.seg_idx;
            if (j < 0 || j >= n || pred_perp[j] == INT_MIN) continue;
            const int pp = pred_perp[j];
            Segment& sg = out[si];
            if (sg.start.y == sg.end.y) {
                const int lo = std::min(sg.start.x, sg.end.x);
                const int hi = std::max(sg.start.x, sg.end.x);
                sg.start.x = std::min(lo, pp);
                sg.end.x   = std::max(hi, pp);
            } else {
                const int lo = std::min(sg.start.y, sg.end.y);
                const int hi = std::max(sg.start.y, sg.end.y);
                sg.start.y = std::min(lo, pp);
                sg.end.y   = std::max(hi, pp);
            }
        }
    }
    return out;
}

// Score every candidate topology of one bundle against the CURRENT cut state
// and return the cheapest admissible one for the given mode.  Pure scoring:
// the cut state is restored before returning; the caller commits the winner
// with commit_plan().

// Score ONE candidate topology against the shared base cut state.  The
// candidate's within-charges go to `overlay` (cleared at entry — the
// per-candidate rollback), reads route through cand_usage_, and cuts_ is
// never written — so the evaluation is pure per candidate and safe to run
// concurrently on worker threads, each with its own overlay.  The body is
// the historical plan_bundle candidate-loop body verbatim; the loop's tail
// (debug-view recording, skips, compare) lives in plan_bundle's ordered
// reduction so the winner and every tie-break replay the sequential sweep.
CongestionPlanner::CandScore CongestionPlanner::score_candidate_(
        const BundleWrapper& bw, int ci, PlanMode mode,
        const CandCtx& ctx, CandOverlay& overlay) {
    CandScore out;
    constexpr double kOvEps = 1e-6;   // float noise only — any real overflow is hard
    const bool enforce_window   = ctx.enforce_window;
    const bool enforce_overflow = ctx.enforce_overflow;
    const int  nbits = ctx.nbits;
    auto seg_n = [&](const Topology& t, int si) {
        // NDR: a governed bundle's segments are counted in GROUP DEMAND
        // UNITS (ndr.h) rather than raw member bits, so every consumer
        // below prices the rule.  Identity when the spec is inactive
        // (R12 byte-identity).
        return ndr_units(bw.input, seg_bit_count(t, si, nbits));
    };
    auto seg_w = [&](const Topology& t, int si) {
        const int n = seg_n(t, si);
        return (nbits > 0 && n != nbits)
                   ? bw.input.width * ((double)n / (double)nbits)
                   : bw.input.width;
    };
    const auto&  h_layers_rev = *ctx.h_layers_rev;
    const auto&  v_layers_rev = *ctx.v_layers_rev;
    const auto&  layer_load   = *ctx.layer_load;
    const double max_h_load   = ctx.max_h_load;
    const double max_v_load   = ctx.max_v_load;

    overlay.clear();
    t_cand_overlay_ = &overlay;
    struct OverlayReset { ~OverlayReset() { t_cand_overlay_ = nullptr; } } ovr;

    const Topology& topo = bw.input.candidates[ci];

        // Greedy per-segment layer assignment within this topology.
        // Each segment independently gets the layer that minimises its
        // marginal overflow + affinity cost.  We apply each choice to the
        // running cut state so within-topology interactions are captured
        // (same-bundle segments rarely share a cut+band, but this is exact
        // for multicast trees whose H-spine and V-stubs can share bands).
        std::vector<int> seg_layers;
        std::vector<int> seg_perp;   // perp-centre overrides for band lookup
        std::vector<SegCost> seg_costs;   // per-segment breakdown (debug view only)
        double topo_overflow = 0.0;
        double topo_score    = 0.0;
        double topo_peak_fill = 0.0; // worst chosen-band fill (kSegs gate)
        bool   topo_infeasible = false;
        // This candidate's own running usage per SHARED layer — the budget
        // gate in the layer enumeration must count earlier segments of the
        // same candidate, not just the committed books (Phase 3 Q3).
        std::map<int,double> cand_share_use;

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

        // Honest-books junction prediction (charge_pull_target follow-on (a),
        // see junction_extended_segments).  The extended spans participate in
        // STRICT gating ONLY, and only through the DEAD-BAND check
        // (span_hits_dead_band): a layer is refused when the junction-extended
        // span crosses a zero-capacity (keepout-carved) band — the metal
        // physically cannot exist there once NUTS stretches the segment to its
        // pulled partner's predicted track (comprehensive_demo b3).  Soft
        // costs and the committed charge stay on the NOMINAL span, and load-
        // pressure overflow on the extension does NOT gate: both stronger
        // forms were measured and rejected (full extension: mix healed 0->2
        // ov, big2 WL +13%; any-overflow gate: mix 2 ov / 21 opens — the
        // dead-span-gate over-conservatism lesson).  riders feeds the (a1)
        // single-rider anchor clamp on the charged band.
        std::vector<Segment> ext_segs;
        std::vector<std::vector<int>> riders;
        if (charge_pull_target_ >= 2)
            ext_segs = junction_extended_segments(topo, conn_segs, &riders);

        for (int si = 0; si < (int)topo.segments.size(); ++si) {
            const Segment& seg = topo.segments[si];
            const Segment& gate_seg = (si < (int)ext_segs.size())
                                          ? ext_segs[si] : seg;
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
            // Pulled segments: NUTS's placement preference chain puts the
            // pull/face target ABOVE the planner's charged band (seg_perp is
            // consumed only by segments FREE of pull/face semantics), so
            // charging the cheapest/nearest band books capacity where the
            // metal will not go — and never charges where it will
            // (books-vs-metal: 141/185 pulled segments diverged >100 units
            // from their charged band on bigHalf, 123/148 on big2, worst
            // Δ3378).  The pull-breakpoint clamp made the target
            // DETERMINISTIC at plan time, so charge there: mirror NUTS
            // (build_nuts_maps + set_pull_targets) — the window bound in the
            // pull direction, tightened by an in-travel breakpoint, centre
            // clamped per layer so the bus width stays inside the window.
            int pull_anchor = INT_MIN;
            if (charge_pull_target_ >= 1 && slide_lo != INT_MIN &&
                si < (int)conn_segs.size()) {
                const ConnSeg& cs = conn_segs[si];
                if (cs.net_pull != 0) {
                    double pref = (cs.net_pull > 0) ? (double)slide_hi
                                                    : (double)slide_lo;
                    if (cs.pull_break != INT_MIN) {
                        const double bp = (double)cs.pull_break;
                        if (cs.net_pull > 0 && bp > cs.perp_pos && bp < pref)
                            pref = bp;
                        else if (cs.net_pull < 0 && bp < cs.perp_pos && bp > pref)
                            pref = bp;
                    }
                    pull_anchor = (int)std::lround(pref);
                }
            }
            auto band_perp = [&](int lid, double eff) {
                if (slide_lo == INT_MIN) return INT_MIN;   // no window: nominal lookup
                if (pull_anchor != INT_MIN) {
                    // Charge at the predicted pull target (bus-width clamped);
                    // a window too narrow for the bus falls back to the band
                    // choice (the window-feasibility check rejects it anyway).
                    const double half = eff / 2.0;
                    const double c_lo = slide_lo + half, c_hi = slide_hi - half;
                    if (c_lo <= c_hi) {
                        int anchor = (int)std::lround(
                            std::clamp((double)pull_anchor, c_lo, c_hi));
                        // Occupancy-aware anchor: NUTS TARGETS the pull, but its
                        // preferred_fit spreads to the nearest FREE track when
                        // the target is occupied — so charging at the anchor
                        // unconditionally over-concentrated every pulled segment
                        // on its window bound and booked phantom demand there,
                        // steering topology SELECTION to longer detours on an
                        // UNcongested design (big2: 26/80 bundles flipped to
                        // OOB/long trunks, est-WL +8%, endpoint still 0/0 — NUTS
                        // placed the short routes fine).  Charge at the anchor
                        // only when that band can host the bus; otherwise fall to
                        // the occupancy-aware best_band_perp, exactly as NUTS
                        // places.  Where the congestion is REAL (anchor AND every
                        // nearby band overflow) the fallback still overflows and
                        // STRICT escalates, so the honest concentration
                        // (mempool_tile/mix wins) is preserved.  Use
                        // score_segment (RAW overflow), NOT cong_cost_segment
                        // (kCong_-scaled): the STRICT ladder below rejects on
                        // score_segment's raw overflow, so the anchor
                        // feasibility test must use the same measure or a
                        // small/zero kCong would make cong_cost read 0 on a
                        // genuinely overflowing anchor — we'd return it, and
                        // STRICT would then reject the layer WITHOUT trying the
                        // free band best_band_perp would have found (Codex #364).
                        if (score_segment(seg, lid, eff, anchor,
                                          slide_lo, slide_hi) <= kOvEps)
                            return anchor;
                    }
                }
                int pp = best_band_perp(seg, lid, eff, slide_lo, slide_hi,
                                        (double)seg_n(topo, si));
                // (a1) single-rider junction anchor: NUTS clamps an unpulled
                // single-junction segment's preference into its rider's span
                // when the base falls outside it — mirror that on the charge.
                // The BASE must be the one NUTS uses: a segment
                // WITH busterm faces never consumes seg_perp (build_nuts_maps
                // leaves pull_map at the nominal when n_bt != 0), so its base
                // is the NOMINAL coordinate; only a face-free segment's base
                // is the charged band.  Base inside the rider's along-extent
                // keeps the base unchanged (the NUTS rule).
                if (charge_pull_target_ >= 2 && si < (int)riders.size() &&
                    riders[si].size() == 1) {
                    const int r = riders[si][0];
                    if (r >= 0 && r < (int)conn_segs.size() &&
                        conn_segs[r].horiz != conn_segs[si].horiz) {
                        int n_bt = 0;
                        for (const auto& cn : conn_segs[si].conns)
                            if (cn.kind == SegConn::BUSTERM) ++n_bt;
                        int base = (n_bt > 0) ? conn_segs[si].perp_pos : pp;
                        const int jlo = conn_segs[r].along_lo;
                        const int jhi = conn_segs[r].along_hi;
                        if (base < jlo || base > jhi)
                            base = std::clamp(base, jlo, jhi);
                        if (base != pp) {
                            const double half = eff / 2.0;
                            const double c_lo = slide_lo + half;
                            const double c_hi = slide_hi - half;
                            if (c_lo <= c_hi)
                                pp = (int)std::lround(
                                    std::clamp((double)base, c_lo, c_hi));
                        }
                    }
                }
                return pp;
            };

            int    best_lid = layers_rev[0];
            double best_s   = std::numeric_limits<double>::max();
            double best_ov  = 0.0;
            int    best_pp  = INT_MIN;
            // Cost components of the chosen layer, captured for the debug view
            // (costs_out); zero-cost otherwise (no overhead — plain writes).
            double best_cong = 0.0, best_span = 0.0, best_base = 0.0,
                   best_bal = 0.0, best_hgt = 0.0, best_pk = 0.0;

            // Respect manual layer overrides if present for this segment.
            if (si < (int)bw.input.pinned_seg_layers.size() && bw.input.pinned_seg_layers[si] != -1) {
                best_lid = bw.input.pinned_seg_layers[si];
                best_s   = 0.0; // Pinned choice is considered "perfect" cost for planning.
                double eff = layers_.eff_bus_width(seg_n(topo, si), seg_w(topo, si), best_lid);
                best_pp  = band_perp(best_lid, eff);
                best_ov  = score_segment(seg, best_lid, eff, best_pp, slide_lo, slide_hi);
                if (charge_pull_target_ >= 2 && enforce_overflow && best_ov <= kOvEps &&
                    span_hits_dead_band(gate_seg, best_lid, best_pp, slide_lo, slide_hi))
                    best_ov = 9999.0;
                if (enforce_overflow && best_ov > kOvEps) {
                    topo_infeasible = true;
                    if (ctx.want_contended)
                        collect_overflow_bands(gate_seg, best_lid, eff, best_pp,
                                               slide_lo, slide_hi, out.contended);
                }
            } else {
                // Iterate highest-ID first so equal-cost layers prefer higher metal.
                for (int lid : layers_rev) {
                    // Per-cell layer policy (hier_layer_caps.md): a governed
                    // bundle may only be ASSIGNED layers in its cell's band.
                    // This one skip is what the whole STRICT ladder, the
                    // replans and the trial paths inherit — empty mask (the
                    // default) changes nothing.
                    if (!bw.input.allows_layer(lid)) continue;
                    double eff  = layers_.eff_bus_width(seg_n(topo, si), seg_w(topo, si), lid);
                    // Scalar collective budget (Phase 3 Q3), STRICT: a
                    // shared layer whose group lease cannot host this
                    // segment on top of the books + this candidate's own
                    // earlier segments is not a choice — the enumeration
                    // steers to an in-band alternative INSIDE the mode,
                    // instead of every candidate failing on the exhausted
                    // layer and the ladder escalating past the promise.
                    if (enforce_overflow &&
                            !bw.input.share_group.empty() &&
                            bw.input.share_of(lid) < 1.0) {
                        const double budget = bw.input.share_budget_of(lid);
                        if (budget >= 0.0) {
                            auto itb = share_used_.find(
                                {bw.input.share_group, lid});
                            double used = (itb == share_used_.end())
                                              ? 0.0 : itb->second;
                            auto itc = cand_share_use.find(lid);
                            if (itc != cand_share_use.end())
                                used += itc->second;
                            if (used + eff > budget + 1e-9) continue;
                        }
                    }
                    int    pp   = band_perp(lid, eff);
                    double ov   = score_segment(seg, lid, eff, pp, slide_lo, slide_hi);
                    if (charge_pull_target_ >= 2 && enforce_overflow && ov <= kOvEps &&
                        span_hits_dead_band(gate_seg, lid, pp, slide_lo, slide_hi))
                        ov = 9999.0;   // extension crosses a keepout-dead band
                    // STRICT: overflow is a hard constraint.  An overflowing
                    // band physically cannot host the bus — NUTS would emit a
                    // real overlap — so the layer is not a choice, however
                    // cheap its soft cost.
                    if (enforce_overflow && ov > kOvEps) {
                        if (ctx.want_contended)
                            collect_overflow_bands(gate_seg, lid, eff, pp,
                                                   slide_lo, slide_hi, out.contended);
                        continue;
                    }
                    // Dead-span gate for NON-TOP layers (opt-in:
                    // set_planner_param nontop_dead_span_gate 1; default off).
                    // score_segment's per-cut capacity samples the endpoint-
                    // CLAMPED extent (for_each_band treats a non-TOP stub's in-
                    // cell tail as pin access on another layer), so a stub whose
                    // in-cell span sits over a leaf keepout on a LOW layer can
                    // pass the width/track check yet land where NO track is
                    // keepout-clear across the whole span DetailedNUTS places
                    // from — the abstract-span signal-track supply is 0.  When
                    // enabled, reject such a NON-TOP layer so STRICT escalates to
                    // a TOP layer that can host the bits.  Measured (bigHalf
                    // no-rr, signal_tracks): unplaced 566 -> 135 (−76%).
                    //
                    // OFF by default because the abstract span is a CONSERVATIVE
                    // overestimate of the final junction-adjusted bit spans, so
                    // span_pool==0 does NOT distinguish bigHalf's stubs (which
                    // genuinely cull — bits can't retract clear of the keepout)
                    // from rnr_mix's (whose final spans DO clear it and place):
                    // both read span_pool==0 at plan time, and gating both
                    // regresses rnr_mix's healed endpoint 0 -> 16 by over-
                    // escalating survivors onto TOP.  The always-on discriminator
                    // (keepout-covers-the-whole-routed-extent vs partial, a
                    // post-placement-aware predictor) is the follow-on tracked in
                    // wishlist-planner / opens item 4.  TOP layers are exempt.
                    if (nontop_dead_span_gate_ && enforce_overflow
                        && !is_top_for(bw.input, lid) && pp != INT_MIN
                        && seg_n(topo, si) > 0) {
                        int sup = span_signal_supply(seg, lid, pp,
                                                     slide_lo, slide_hi,
                                                     /*with_midpoint_fallback=*/false,
                                                     /*use_raw_span=*/true);
                        if (sup == 0) {          // dead span: no keepout-clear track
                            if (ctx.want_contended)
                                collect_overflow_bands(seg, lid, eff, pp,
                                                       slide_lo, slide_hi, out.contended);
                            continue;
                        }
                    }
                    double cong = cong_cost_segment(seg, lid, eff, pp, slide_lo, slide_hi);
                    double span = span_cost_for(seg_span, lid);
                    // Non-TOP penalty scaled by segment length: a short stub
                    // pays little to drop down a layer, so locals offload to
                    // lower layers instead of detouring on TOP — preserving
                    // TOP capacity for long-haul trunks (which pay in full).
                    double base = is_top_for(bw.input, lid) ? 0.0
                                : base_cost_non_top_ *
                                  ((span_ref_eff_ > 0.0)
                                       ? std::min(1.0, seg_span / span_ref_eff_)
                                       : 1.0);
                    // Load-balancing bias: prefer the less-loaded of the
                    // equal-cost same-direction TOP layers so H load spreads
                    // across the H layers (and V across the V layers) instead of
                    // piling on the highest metal.  Only TOP layers compete for
                    // balancing; LOW layers already carry the base penalty.
                    double bal = 0.0;
                    if (is_top_for(bw.input, lid)) {
                        bool is_hl  = (seg.start.y == seg.end.y);
                        double maxl = is_hl ? max_h_load : max_v_load;
                        auto   it   = layer_load.find(lid);
                        if (it != layer_load.end() && maxl > 0.0)
                            bal = kBalance_ * (it->second / maxl);
                    }
                    // Layer-height cost, the mirror image of `base` above: a
                    // SHORT segment pays kHeight_ per height rank to climb
                    // above the lowest same-direction TOP layer (each rank is
                    // a taller via stack), while a long trunk (span >= ref)
                    // pays 0 and keeps the TOP-most trunk preference.
                    double hgt = 0.0;
                    if (is_top_for(bw.input, lid) && span_ref_eff_ > 0.0)
                        hgt = kHeight_ * top_height_rank_for(bw.input, lid) *
                              std::max(0.0, 1.0 - seg_span / span_ref_eff_);
                    // Routability term (kPeak, default 0 = skipped entirely):
                    // pay for the worst band's EXISTING fill fraction (pre-
                    // charge — see peak_util_segment), so a candidate headed
                    // into a nearly-full band loses to one that avoids it —
                    // BEFORE overflow, which is the only point the kCong term
                    // above can see.
                    double pk = 0.0;
                    if (kPeak_ > 0.0)
                        pk = kPeak_ * peak_util_segment(seg, lid, pp,
                                                        slide_lo, slide_hi,
                                                        (double)seg_n(topo, si));
                    double s    = cong + span + base + bal + hgt + pk;
                    if (s < best_s) { best_s = s; best_lid = lid; best_ov = ov; best_pp = pp;
                                      best_cong = cong; best_span = span; best_base = base;
                                      best_bal = bal; best_hgt = hgt; best_pk = pk; }
                }
                if (best_s == std::numeric_limits<double>::max())
                    topo_infeasible = true;   // STRICT: every layer overflows
            }
            if (topo_infeasible) break;
            int perp_pos = best_pp;

            // kSegs headroom gate: record the chosen layer's PRE-CHARGE peak
            // band fill (the kPeak measure, absolute-supply floor included).
            // The candidate's worst fill fades the segment-count penalty —
            // see the wl_est term below.
            if (ksegs_eff_ > 0.0 && kSegsGate_ > 0.0) {
                double fill = peak_util_segment(seg, best_lid, best_pp,
                                                slide_lo, slide_hi,
                                                (double)seg_n(topo, si));
                topo_peak_fill = std::max(topo_peak_fill, fill);
            }

            // Feasibility: the bus (eff_width in the perpendicular direction)
            // must fit within the sliding range ConnTopology computed for this
            // segment — covers busterms (Pass 1) and spines/trunks (Pass 2).
            if (enforce_window && si < (int)conn_segs.size()) {
                const ConnSeg& cs = conn_segs[si];
                if (cs.perp_lo > -kSentinel && cs.perp_hi < kSentinel) {
                    double eff = layers_.eff_bus_width(seg_n(topo, si), seg_w(topo, si), best_lid);
                    if (static_cast<double>(cs.perp_hi - cs.perp_lo) < eff)
                        topo_infeasible = true;
                }
            }
            if (topo_infeasible) break;

            // Apply chosen layer so later segments in this topology see
            // the updated congestion state.  Charge eff + pitch so the band
            // books mirror NUTS inter-bus spacing (Gap 1).
            double eff = layers_.eff_bus_width(seg_n(topo, si), seg_w(topo, si), best_lid);
            apply_segment(seg, best_lid, eff + track_pitch_, perp_pos);
            if (bw.input.share_of(best_lid) < 1.0)
                cand_share_use[best_lid] += eff;
            seg_layers.push_back(best_lid);
            seg_perp.push_back(perp_pos);
            if (ctx.want_costs)
                seg_costs.push_back({si, best_lid, best_cong, best_span, best_base,
                                     best_bal, best_hgt, best_pk, best_s});
            topo_overflow = std::max(topo_overflow, best_ov);
            topo_score    = std::max(topo_score,    best_s);
        }

        // Wirelength term: with congestion/span/layer costs equal, shorter
        // topologies win — a detour must buy real congestion relief to be
        // worth its extra length.  With kWLSpread on and the candidate's WL
        // envelope annotated, ADD a realization-risk penalty proportional to
        // the envelope spread (wl_hi - wl_lo): NUTS realizations wander
        // within the slide/span DOF, so a wide-envelope shape (many
        // slide-coupled segments) realizes far above its nominal while a
        // tight shape realizes at or below it (see set_planner_param
        // "kWLSpread"; base stays the nominal — replacing it with wl_lo was
        // measured and rejected, it reshuffles near-ties corpus-wide).
        double wl_est = topo.estimated_wirelength;
        if (kWLSpread_ >= 0.0 && topo.wl_lo >= 0.0 && topo.wl_hi >= topo.wl_lo)
            wl_est += kWLSpread_ * (topo.wl_hi - topo.wl_lo);
        // Segment-count penalty (opt-in, default 0): each segment beyond the
        // wire itself costs junction vias PER BIT and realization DOF the WL
        // estimate never sees (b61: the 10-seg TRUNK+MST estimates 9% under
        // the 5-seg tree but realizes only 2% under it — with 2.25x the
        // vias).  Priced in wirelength-equivalents so the knob reads as
        // "one extra segment costs like kSegs units of wire".
        //
        // HEADROOM GATE (kSegsGate, EXPERIMENT — default 0 = flat): >0
        // fades the penalty with the candidate's worst chosen-band fill.
        // Measured WORSE than flat (see kSegsGate_ in the header): the gate
        // is per-candidate, so a candidate heading into FULL bands pays no
        // penalty — a perverse incentive that makes stress attractive.
        // Kept opt-in for reproducing the study.
        // G3b (audit): the two-level datapath trees BITRUNK_HVH/VHV exist in
        // the pool ONLY when the user passed `multi_trunk` — an explicit
        // request for exactly these many-segment shapes, whose 5-14% WL win
        // the generic penalty was measured to invert (datapath abstract WL
        // +16-23% at kSegsRel 0.02, multi losing its edge over plain).  An
        // explicit opt-in outranks a generic prior: exempt them.  The legacy
        // always-on BITRUNK_H is NOT gated behind the flag and stays priced.
        const bool opted_in_tree =
            topo.type.rfind("BITRUNK_HVH", 0) == 0 ||
            topo.type.rfind("BITRUNK_VHV", 0) == 0;
        if (ksegs_eff_ > 0.0 && !opted_in_tree) {
            double gate = (kSegsGate_ > 0.0)
                              ? std::max(0.0, 1.0 - topo_peak_fill)
                              : 1.0;
            // Taper-honest weight (audit G3a): charge each segment for its
            // MEMBER-BIT share (seg_bit_count / nbits) — the sum is the
            // per-bit average path length in segments, which is what the
            // junction vias actually scale with.  An untapered candidate
            // (empty seg_bits: every segment carries every bit) reduces to
            // n_segments exactly; a fan-in branch carrying 4 of 16 bits
            // counts 0.25, so a per-bit tapered tree is no longer charged
            // nseg x all-bits for structure most bits never traverse.
            double w_segs = (double)topo.segments.size();
            if (nbits > 0 && !topo.seg_bits.empty()) {
                w_segs = 0.0;
                for (int si = 0; si < (int)topo.segments.size(); ++si)
                    w_segs += (double)seg_n(topo, si) / (double)nbits;
            }
            wl_est += ksegs_eff_ * gate * w_segs;
        }
        topo_score += kWL_ * wl_est;

        // Package the evaluation; plan_bundle's ordered reduction applies
        // the debug-view recording, the infeasible/share skips and the serial
        // compare exactly as the historical in-loop tail did.
        out.score      = topo_score;
        out.overflow   = topo_overflow;
        out.wl_term    = kWL_ * wl_est;
        out.infeasible = topo_infeasible;
        out.seg_layers = std::move(seg_layers);
        out.seg_perp   = std::move(seg_perp);
        out.seg_costs  = std::move(seg_costs);
        // Scalar collective budget gate (Phase 3 Q3, STRICT only): a
        // candidate whose commit would push this wrapper's share_group past
        // a shared layer's budget is refused at the reduction and the ladder
        // moves on — ALLOW_OVERFLOW/BEST_EFFORT may still commit it (LOUD via
        // the ladder's own warnings + the §9.7 post-plan audit), never strand.
        if (!topo_infeasible && mode == PlanMode::STRICT &&
                !share_budget_ok_(bw, topo, out.seg_layers))
            out.share_refused = true;
        return out;

}

// plan_bundle's candidate-scoring worker count.  Byte-identity never depends
// on this (the ordered reduction is decision-identical at any thread count),
// so the gate is purely a performance threshold: tiny pools stay sequential
// because their evaluations are microseconds-scale and thread spawn is not.
int CongestionPlanner::resolved_plan_threads_(int ncand) const {
    // An EXPLICIT request (set_plan_threads / BUDA_PLAN_THREADS) is honored
    // as given, clamped only to the candidate count — the NUTS layer_threads
    // convention (an explicit setting bypasses the auto policy, so scaling
    // experiments above the auto cap are possible).  AUTO (no request) uses
    // hardware concurrency capped at 8, and only for pools of 8+ candidates
    // (tiny evaluations are microseconds-scale and thread spawn is not; the
    // gate is perf-only — byte-identity never depends on the thread count).
    int n = plan_threads_;
    if (n <= 0)
        if (const char* e = std::getenv("BUDA_PLAN_THREADS")) n = std::atoi(e);
    if (n > 0)
        return std::max(1, std::min(n, ncand));
    if (ncand < 8) return 1;                 // small-pool gate (auto only)
    unsigned hw = std::thread::hardware_concurrency();
    n = hw ? (int)hw : 1;
    // BUDA_THREADS (the CLI's machine-wide governor) is an AUTO-path CEILING,
    // not an explicit request: it lowers the pool without bypassing the
    // small-pool gate above — `buda --threads N` sets the per-engine var
    // (explicit) instead, and the flag-absent default sets only this one
    // (Codex #598 P1: a default that wrote BUDA_PLAN_THREADS masqueraded as
    // an explicit request and spawned pools for tiny candidate sets).
    if (const char* g = std::getenv("BUDA_THREADS")) {
        const int cap = std::atoi(g);
        if (cap > 0) n = std::min(n, cap);
    }
    return std::max(1, std::min({n, ncand, 8}));
}

CongestionPlanner::PlanResult CongestionPlanner::plan_bundle(
        const BundleWrapper& bw, PlanMode mode,
        std::set<std::pair<int,int>>* contended,
        std::vector<CandidateCost>* costs_out) {
    PlanResult res;
    if (bw.input.candidates.empty()) return res;

    // Fractional-share context (Phase 3 Tier 2): every capacity read below
    // sees this wrapper's s × supply on its shared layers.  Cleared on exit
    // (single-threaded per planner instance; parallel sweeps use per-thread
    // planner copies).
    cur_share_input_ = &bw.input;
    struct ShareCtxReset {
        const BundleInput*& p;
        ~ShareCtxReset() { p = nullptr; }
    } share_ctx_reset_{cur_share_input_};

    const bool enforce_window   = (mode != PlanMode::BEST_EFFORT);
    const bool enforce_overflow = (mode == PlanMode::STRICT);
    constexpr double kOvEps = 1e-6;   // float noise only — any real overflow is hard

    // Bit count for the honest per-layer width model (eff_bus_width);
    // 0 (hand-built wrappers without nets) falls back to width x dilution.
    const int nbits = (int)bw.input.original_bundle.get_net_names().size();
    // Tapered fan-in width model: a segment carrying a bit SUBSET (seg_bits)
    // is charged for its member bits only — count for pattern layers, a
    // proportional base width for the dilution fallback.  Non-fan-in
    // bundles (empty seg_bits) are byte-identical to the bundle-level model.
    auto seg_n = [&](const Topology& t, int si) {
        // NDR: a governed bundle's segments are counted in GROUP DEMAND
        // UNITS (ndr.h) rather than raw member bits, so every consumer
        // below prices the rule.  Identity when the spec is inactive
        // (R12 byte-identity).
        return ndr_units(bw.input, seg_bit_count(t, si, nbits));
    };
    auto seg_w = [&](const Topology& t, int si) {
        const int n = seg_n(t, si);
        return (nbits > 0 && n != nbits)
                   ? bw.input.width * ((double)n / (double)nbits)
                   : bw.input.width;
    };

    auto h_layers = layers_.get_layer_ids_by_dir(LayerDir::HORIZONTAL);
    auto v_layers = layers_.get_layer_ids_by_dir(LayerDir::VERTICAL);
    if (h_layers.empty()) h_layers.push_back(4);
    if (v_layers.empty()) v_layers.push_back(5);
    // Reversed copies: highest layer ID first so ties break toward higher metal.
    auto h_layers_rev = h_layers; std::reverse(h_layers_rev.begin(), h_layers_rev.end());
    auto v_layers_rev = v_layers; std::reverse(v_layers_rev.begin(), v_layers_rev.end());

    // Candidate indices to evaluate.  A GROUP pin (pinned_group) restricts the
    // search to a super-candidate's members; a SINGLE pin to one index; else the
    // full sweep.  With no group pin this reproduces the historical
    // [ci_lo, ci_hi) range exactly, so planning is byte-identical.
    std::vector<int> cand_indices;
    for (int ci : bw.input.pinned_group)
        if (ci >= 0 && ci < (int)bw.input.candidates.size())
            cand_indices.push_back(ci);
    if (cand_indices.empty()) {
        // Guard the single-pin path exactly as the pinned_group path above:
        // an out-of-range selected_topology_index (its default is -1, or a stale
        // pin after the pool shrank) must not index candidates[] out of bounds.
        // An incoherent pin is treated as "not usefully pinned" → full sweep,
        // turning a SIGSEGV into well-defined behavior (issue #454). The
        // supported CLI never hits this (select_topology sets the flag AND a
        // valid index together); the pybind fields are independently writable.
        const int n = (int)bw.input.candidates.size();
        const int sel = bw.plan.selected_topology_index;
        const bool valid_pin = bw.input.topology_pinned && sel >= 0 && sel < n;
        int ci_lo = valid_pin ? sel     : 0;
        int ci_hi = valid_pin ? sel + 1 : n;
        for (int ci = ci_lo; ci < ci_hi; ++ci) cand_indices.push_back(ci);
    }

    res.best_topo     = cand_indices.empty() ? 0 : cand_indices.front();
    double best_score = std::numeric_limits<double>::max();

    // Each topology candidate is scored from the same base cut state: the
    // within-candidate charges go into a per-thread scoring OVERLAY (see
    // score_candidate_ / t_cand_overlay) — cuts_ stays read-only for the
    // whole scoring pass and rollback is the overlay clear at each entry.

    // Per-layer committed load (summed band usage) at this bundle's turn, and the
    // max over each direction's layers, for the load-balancing tie-breaker.  The
    // load reflects only already-committed bundles (within-candidate charges are
    // restored per candidate), so it grows monotonically across the greedy
    // schedule and steers later bundles onto the layers earlier ones left empty.
    const auto t_ll0 = std::chrono::steady_clock::now();
    std::map<int,double> layer_load;
    for (const auto& c : cuts_) {
        double u = 0.0;
        for (int b = 0; b < c.num_bands(); ++b) u += c.usage(b);
        layer_load[c.layer_id] += u;
    }
    prof_layerload_us_ += (long long)std::chrono::duration_cast<
        std::chrono::microseconds>(std::chrono::steady_clock::now() - t_ll0)
        .count();
    ++prof_plan_calls_;
    double max_h_load = 1.0, max_v_load = 1.0;
    for (const auto& [lid, u] : layer_load) {
        const Layer* L = layers_.get_layer(lid);
        if (L && L->dir == LayerDir::HORIZONTAL) max_h_load = std::max(max_h_load, u);
        else                                     max_v_load = std::max(max_v_load, u);
    }

    // Scoring context shared by every candidate evaluation (read-only).
    CandCtx ctx;
    ctx.h_layers_rev     = &h_layers_rev;
    ctx.v_layers_rev     = &v_layers_rev;
    ctx.layer_load       = &layer_load;
    ctx.max_h_load       = max_h_load;
    ctx.max_v_load       = max_v_load;
    ctx.nbits            = nbits;
    ctx.enforce_window   = enforce_window;
    ctx.enforce_overflow = enforce_overflow;
    ctx.want_contended   = (contended != nullptr);
    ctx.want_costs       = (costs_out != nullptr);

    // Score every candidate.  Each evaluation reads the SAME base cut state
    // (cuts_ is read-only during scoring; a candidate's own charges live in
    // a per-thread overlay), so candidates may be scored on worker threads
    // with per-candidate results identical to the sequential loop; the
    // ordered reduction below then replays the sequential compare, so the
    // winner — and every tie-break — is identical to the serial sweep.
    const auto t_sc0 = std::chrono::steady_clock::now();
    std::vector<CandScore> scores(cand_indices.size());
    const int n_threads = resolved_plan_threads_((int)cand_indices.size());
    if (n_threads > 1) {
        std::atomic<size_t> next{0};
        auto worker = [&]() {
            CandOverlay& ov = thread_overlay_();   // persistent per thread
            for (size_t k; (k = next.fetch_add(1)) < cand_indices.size(); )
                scores[k] = score_candidate_(bw, cand_indices[k], mode, ctx, ov);
        };
        std::vector<std::thread> pool;
        pool.reserve(n_threads - 1);
        for (int t = 1; t < n_threads; ++t) pool.emplace_back(worker);
        worker();
        for (auto& th : pool) th.join();
    } else {
        CandOverlay& ov = thread_overlay_();       // persistent per thread
        for (size_t k = 0; k < cand_indices.size(); ++k)
            scores[k] = score_candidate_(bw, cand_indices[k], mode, ctx, ov);
    }
    prof_scoring_us_ += (long long)std::chrono::duration_cast<
        std::chrono::microseconds>(std::chrono::steady_clock::now() - t_sc0)
        .count();
    prof_cands_ += (long long)cand_indices.size();

    // Ordered reduction — the sequential loop's tail, verbatim semantics.
    for (size_t k = 0; k < cand_indices.size(); ++k) {
        const int ci = cand_indices[k];
        CandScore& cs = scores[k];
        if (contended)
            contended->insert(cs.contended.begin(), cs.contended.end());
        if (costs_out) {                       // debug cost view: record this candidate
            CandidateCost cc;
            cc.cand_index = ci;
            cc.total      = cs.score;
            cc.wl_term    = cs.wl_term;
            cc.seg_cost   = cs.score - cs.wl_term;   // max-over-segments seg score
            cc.feasible   = !cs.infeasible;
            cc.segs       = std::move(cs.seg_costs);
            costs_out->push_back(std::move(cc));
        }
        if (cs.infeasible || cs.share_refused) continue;

        bool is_better = false;
        if (cs.score < best_score - 1e-6) {
            is_better = true;
        } else if (std::abs(cs.score - best_score) < 1e-6) {
            // Tie-breaker: stable selection by index.
            if (ci < res.best_topo) is_better = true;
        }
        if (is_better) {
            best_score     = cs.score;
            res.score      = cs.score;
            res.overflow   = cs.overflow;
            res.best_topo  = ci;
            res.seg_layers = std::move(cs.seg_layers);
            res.seg_perp   = std::move(cs.seg_perp);
            res.found      = true;
        }
    }
    return res;
}

// Commit (sign=+1) or rip up (sign=-1) a planned bundle's per-segment demand
// in the cut state.
void CongestionPlanner::commit_plan(const BundleWrapper& bw, const PlanResult& plan,
                                    double sign) {
    const int nbits = (int)bw.input.original_bundle.get_net_names().size();
    const Topology& t = bw.input.candidates[plan.best_topo];
    // Scalar collective budget books (Phase 3 Q3): commit_plan is the single
    // charge/uncharge chokepoint, so keeping the per-(group, shared layer)
    // counter here makes every trial / rip-up / recharge path consistent by
    // construction.  No shares anywhere = no-op.
    if (!bw.input.share_group.empty() && !bw.input.layer_shares.empty()) {
        std::map<int,double> usage;
        share_usage_of_(bw, t, plan.seg_layers, usage);
        for (const auto& [lid, u] : usage)
            share_used_[{bw.input.share_group, lid}] += sign * u;
    }
    for (int si = 0; si < (int)t.segments.size() && si < (int)plan.seg_layers.size(); ++si) {
        int pp  = (si < (int)plan.seg_perp.size()) ? plan.seg_perp[si] : INT_MIN;
        int lid = plan.seg_layers[si];
        // Charge eff + pitch (Gap 1); sign rips up symmetrically.  Tapered
        // fan-in: charge each segment for its member bits only (seg_bits).
        // Deliberately the NOMINAL span even under charge_pull_target: the
        // junction-extended spans participate in overflow GATING only —
        // committing the conservative extension was measured and rejected
        // (mix healed endpoint 0->2 overlaps, big2 WL +13%).
        const int    n = ndr_units(bw.input, seg_bit_count(t, si, nbits));
        const double w = (nbits > 0 && n != nbits)
                             ? bw.input.width * ((double)n / (double)nbits)
                             : bw.input.width;
        const double demand = sign * (layers_.eff_bus_width(n, w, lid) + track_pitch_);

        if (band_span_charge_ <= 0) {          // legacy: single band, self-inverse
            apply_segment(t.segments[si], lid, demand, pp);
            continue;
        }
        // Spread charges must be reversed EXACTLY as they were applied (Codex
        // #524 P1).  For the proportional modes the distribution is pure
        // geometry and re-deriving it would suffice, but the greedy modes read
        // live band occupancy — which has moved on by the time a rip-up runs —
        // so replaying a recorded distribution is the only exact inverse.
        const auto key = std::make_pair(bw.input.original_bundle.id, si);
        if (sign < 0.0) {
            auto it = charge_log_.find(key);
            if (it != charge_log_.end()) {
                for (const auto& [ci, b, amt] : it->second)
                    cuts_[ci].add_usage(b, -amt);
                charge_log_.erase(it);
                continue;
            }
            // No record (e.g. charged before the knob was enabled): fall back
            // to re-deriving, which is still correct for the geometric modes.
        }
        std::vector<std::tuple<int, int, double>> rec;
        for_each_band_w(t.segments[si], lid, pp, demand,
                        [&](int ci, int b, double wt) {
            const double amt = demand * wt;
            cuts_[ci].add_usage(b, amt);
            rec.emplace_back(ci, b, amt);
        });
        if (sign > 0.0) charge_log_[key] = std::move(rec);
    }
}

BundleAssignment CongestionPlanner::make_assignment(const BundleWrapper& bw,
                                                    const PlanResult& plan) const {
    // Derive representative V/H layers for logging (last V/H seg wins).
    int rep_v = layers_.get_top_layer(LayerDir::VERTICAL);
    int rep_h = layers_.get_top_layer(LayerDir::HORIZONTAL);
    const Topology& winner = bw.input.candidates[plan.best_topo];
    for (int si = 0; si < (int)winner.segments.size() && si < (int)plan.seg_layers.size(); ++si) {
        bool is_h = (winner.segments[si].start.y == winner.segments[si].end.y);
        if (is_h) rep_h = plan.seg_layers[si];
        else      rep_v = plan.seg_layers[si];
    }
    BundleAssignment asn;
    asn.bundle_id  = bw.input.original_bundle.id;
    asn.topo_index = plan.best_topo;
    asn.v_layer_id = rep_v;
    asn.h_layer_id = rep_h;
    asn.seg_layers = plan.seg_layers;
    asn.seg_perp   = plan.seg_perp;
    return asn;
}

void CongestionPlanner::log_choice(const BundleWrapper& bw, const PlanResult& plan,
                                   const std::string& tag) const {
    const Topology& winner = bw.input.candidates[plan.best_topo];
    std::string seg_str;
    for (int si = 0; si < (int)winner.segments.size() && si < (int)plan.seg_layers.size(); ++si) {
        bool is_h = (winner.segments[si].start.y == winner.segments[si].end.y);
        if (si > 0) seg_str += ' ';
        seg_str += (is_h ? "H" : "V");
        seg_str += "→M" + std::to_string(plan.seg_layers[si]);
    }
    std::cout << "[Planner] Bundle " << bw.input.original_bundle.id
              << " (" << bw.input.width << " units wide)"
              << " -> topo " << (plan.best_topo + 1) << " of " << bw.input.candidates.size()
              << ": " << winner.type << tag
              << "  [" << seg_str << "]"
              << "  overflow=" << plan.overflow << "\n";
}

// ---------------------------------------------------------------------------
// Main optimiser — greedy fattest-bus-first, per-segment layer assignment
// ---------------------------------------------------------------------------

std::vector<BundleAssignment> CongestionPlanner::optimize_topologies(
        std::vector<BundleWrapper>& bundles, int /*max_iterations*/) {
    // A missing layer stack (e.g. a def_layer file that failed to `source`)
    // silently falls back to M4(H)/M5(V) in plan_bundle, routing on default metal
    // with no obvious cause.  Warn once at the run level so the misconfiguration
    // is visible (the fallback itself is kept for hand-built test wrappers).
    {
        bool no_h = layers_.get_layer_ids_by_dir(LayerDir::HORIZONTAL).empty();
        bool no_v = layers_.get_layer_ids_by_dir(LayerDir::VERTICAL).empty();
        if (no_h || no_v)
            std::cout << "[Planner] WARNING: no "
                      << (no_h && no_v ? "HORIZONTAL or VERTICAL"
                                       : no_h ? "HORIZONTAL" : "VERTICAL")
                      << " layers defined; defaulting to M4(H)/M5(V). "
                         "Did a def_layer or source fail?\n";
    }

    // Ensure base grid is populated from floorplan, then extend it from the
    // candidates (no-op when extend_grid_for was already called — the
    // pre-injection hook of the bottom-up negotiate price translation).
    extend_grid_for(bundles);

    // Resolve the span reference for non-TOP penalty scaling: unset means
    // 25% of the larger Hanan grid extent — segments longer than that pay
    // the full base_cost_non_top_, shorter ones proportionally less.
    span_ref_eff_ = base_span_ref_;
    if (span_ref_eff_ <= 0.0 && x_grid_.size() >= 2 && y_grid_.size() >= 2) {
        double ext_x = (double)(x_grid_.back() - x_grid_.front());
        double ext_y = (double)(y_grid_.back() - y_grid_.front());
        span_ref_eff_ = 0.25 * std::max(ext_x, ext_y);
    }

    // kSegs RELATIVE mode (kSegsRel / env BUDA_KSEGS_REL): price each
    // segment as a FRACTION of the design's max-possible HPWL (grid extent
    // W + H) so one value transfers across flow scales — the absolute 500
    // that healed big2 (~24k max-HPWL, ~2%) was an effective ~70% on
    // mempool_tile (~720).  Adds to any absolute kSegs.  The env var
    // supplies an experiment DEFAULT for the default-flip study (an
    // explicit set_planner_param kSegsRel wins over it; 0 = explicitly
    // off).
    ksegs_eff_ = kSegs_;
    double rel = kSegsRel_;
    bool rel_is_default = false;
    if (rel < 0.0) {
        // Unset: BUDA_KSEGS_REL overrides the COMPILED DEFAULT of 0.02 (the
        // audit's safe-Pareto point; env "0" disables for study runs).  Both
        // are non-explicit and pass through the gates below; an explicit
        // set_planner_param kSegsRel bypasses them (the user's own
        // calibration).
        const char* e = std::getenv("BUDA_KSEGS_REL");
        rel = (e != nullptr) ? std::atof(e) : kDefaultKSegsRel;
        rel_is_default = (rel > 0.0);
    }
    // Intent hierarchy (audit G3b): explicit set_planner_param > the
    // `multi_trunk` generation opt-in > the DEFAULT (compiled 0.02 / env override).  Gated two-level
    // trees (BITRUNK_HVH/VHV) exist in a pool only when the user passed
    // `multi_trunk` — a declaration that trees matter here — and the
    // measured greedy coupling means a default penalty degrades such flows
    // even with the trees themselves exempt (row datapath: neighbors'
    // penalty-shifted selections strand the field in a clean-but-worse
    // optimum ripup never touches, +15.7% WL).  So the ENV default stands
    // down for a design whose pools carry gated trees; an explicit
    // kSegs/kSegsRel still applies in full.
    if (rel_is_default) {
        // G1/G2 (audit): the default is only SAFE with healers in the
        // flow — the 07_wide_fan structural loser and big2's jagged alpha
        // response are both healed by ripup_reroute (and never without it).
        // A flow that intends to heal DECLARES healersAhead explicitly
        // (`set_planner_param healersAhead 1`, before run_planner — issue #444
        // replaced the old flow-script text scan so every execution structure
        // agrees); ripup's own re-plan sets it by construction.  Not declared
        // -> the default stands down.
        if (healersAhead_ <= 0.0) {
            std::cout << "[Planner] kSegsRel default suppressed: healersAhead "
                         "not declared (a flow that heals should set "
                         "'set_planner_param healersAhead 1'; an explicit "
                         "set_planner_param kSegs/kSegsRel still applies).\n";
            rel = 0.0;
        }
        // G4 (audit): a segment penalty is a DETOUR penalty — it was
        // measured to overwhelm kPeak's sub-capacity routability steering
        // (the U-detour off a loaded band costs 2 extra segments, and the
        // env-default penalty out-prices the kPeak term that exists to buy
        // exactly that detour).  kPeak is an explicit opt-in for
        // routability-first selection, so it outranks the default the
        // same way multi_trunk does below; a user setting kSegs/kSegsRel
        // EXPLICITLY alongside kPeak owns that calibration.
        if (rel > 0.0 && kPeak_ > 0.0) {
            std::cout << "[Planner] kSegsRel default suppressed: kPeak "
                         "routability steering is enabled (explicit "
                         "set_planner_param kSegs/kSegsRel still applies).\n";
            rel = 0.0;
        }
        for (const auto& bw : bundles) {
            if (rel == 0.0) break;
            for (const auto& cand : bw.input.candidates) {
                if (cand.type.rfind("BITRUNK_HVH", 0) == 0 ||
                    cand.type.rfind("BITRUNK_VHV", 0) == 0) {
                    std::cout << "[Planner] kSegsRel default suppressed: "
                                 "the pool carries multi_trunk two-level "
                                 "trees (explicit set_planner_param kSegs/"
                                 "kSegsRel still applies).\n";
                    rel = 0.0;
                    break;
                }
            }
        }
    }
    if (rel > 0.0) {
        // Scale from the DESIGN's Hanan extent (the floorplan grid), NOT the
        // working x_grid_/y_grid_ — those were just extended with candidate
        // endpoints, so an OOB/detour candidate in the pool would inflate
        // the "relative" penalty (Codex #331).  The design extent is stable
        // across pool changes, which is the whole point of kSegsRel.
        std::vector<int> fx, fy;
        floorplan_.get_hanan_grid(fx, fy);
        if (fx.size() >= 2 && fy.size() >= 2) {
            double hpwl_max = (double)(fx.back() - fx.front())
                            + (double)(fy.back() - fy.front());
            ksegs_eff_ += rel * hpwl_max;
        }
    }

    // Sort: locked (bottom-up template instance) wrappers first — their
    // assignment is already decided, committing it up front makes every
    // later bundle price and detour it; then higher priority (depth-0 before
    // depth-1, constrained first); within the same priority, widest first.
    std::vector<int> order(bundles.size());
    std::iota(order.begin(), order.end(), 0);
    std::sort(order.begin(), order.end(), [&](int a, int b) {
        if (bundles[a].hier.locked != bundles[b].hier.locked)
            return bundles[a].hier.locked;
        if (bundles[a].hier.priority != bundles[b].hier.priority)
            return bundles[a].hier.priority > bundles[b].hier.priority;
        return bundles[a].input.width > bundles[b].input.width;
    });

    // Park every bundle's reserved demand as virtual usage up front, so
    // earlier-planned bundles leave room inside reserved regions (cell
    // interiors).  Each bundle's reservation is released right before its
    // own turn — from then on its demand is real, not reserved.
    //
    // A candidate-less wrapper is EXCLUDED: it is skipped by the plan loop
    // below, so it never becomes metal and its "reservation" is demand that
    // no wire can ever consume.  Parking it anyway charged earlier-planned
    // bundles for phantom congestion — a top-down hier global plans before
    // every cell-local turn, so it saw the full parked set (issue #516:
    // one D1 bundle, 100 candidate-less D2 wrappers, overflow 346 -> 19.25
    // once the phantom is gone, with NUTS/DNUTS clean either way).
    for (int idx : order)
        if (!bundles[idx].input.candidates.empty())
            apply_reservation(bundles[idx], +1.0);

    std::vector<BundleAssignment> assignments;
    assignments.reserve(bundles.size());

    // Per-level statistics for the planning summary (printed when the set
    // spans hierarchy levels).  Stage: 0=STRICT 1=rip-up 2=soft 3=best-effort.
    struct LevelStats {
        int n = 0;
        int by_stage[4] = {0, 0, 0, 0};
        double max_overflow = 0.0;
        std::map<int,int> layer_hist;   // layer id → segment count
    };
    std::map<int, LevelStats> level_stats;

    // Bundles committed so far, in commit order, with the exact plan applied
    // to the cut state (so it can be ripped up) and where its assignment
    // lives (so a replan can overwrite it).
    struct Committed { int bundle_idx; int asn_idx; PlanResult plan; };
    std::vector<Committed> committed;

    for (int idx : order) {
        auto& bw = bundles[idx];
        // Skip BEFORE releasing: a candidate-less wrapper never parked a
        // reservation above, so releasing one here would subtract demand
        // that was never added and drive the band usage negative.  Park and
        // release must stay gated on the same predicate.
        if (bw.input.candidates.empty()) continue;
        // Release this bundle's own reservation: its demand is now planned
        // for real.
        apply_reservation(bw, -1.0);

        // (1) Overflow is a hard constraint: first look for a candidate that
        //     is both slide-feasible and overflow-free.  A detour only loses
        //     to soft costs (wirelength/span) against other overflow-free
        //     candidates — never against one that NUTS cannot place.
        //     On failure, `contended` holds the (cut,band) pairs whose
        //     overflow disqualified candidates — the bands rip-up must relieve.
        std::set<std::pair<int,int>> contended;
        PlanResult plan = plan_bundle(bw, PlanMode::STRICT, &contended);
        bool already_committed = false;
        int  stage = 0;   // STRICT

        // (2) Rip-up & replan: no candidate is overflow-free against the
        //     current usage.  Try freeing capacity by replanning one earlier
        //     bundle.  Victims are ranked by the demand they hold on the
        //     contended bands (most relief first; e.g. the one global trunk
        //     crossing a cell whose local bundle just failed); zero-overlap
        //     victims cannot help and are skipped.  Ties break toward the
        //     most recently committed (lowest priority / narrowest).
        //     Accept only if BOTH bundles end up overflow-free; otherwise
        //     restore the victim exactly and try the next one.
        if (!plan.found) {
            std::vector<std::pair<double,int>> ranked;   // (overlap, committed idx)
            for (int k = 0; k < (int)committed.size(); ++k) {
                // A locked (bottom-up) wrapper is never a rip-up victim:
                // its plan is a template copy shared by every sibling
                // instance and must not be moved unilaterally.
                if (bundles[committed[k].bundle_idx].hier.locked) continue;
                double ovl = plan_band_overlap(bundles[committed[k].bundle_idx],
                                               committed[k].plan, contended);
                if (ovl > 0.0) ranked.push_back({ovl, k});
            }
            std::sort(ranked.begin(), ranked.end(),
                      [](const std::pair<double,int>& a, const std::pair<double,int>& b) {
                          if (a.first != b.first) return a.first > b.first;
                          return a.second > b.second;
                      });
            for (const auto& [ovl, k] : ranked) {
                auto& cp = committed[k];
                auto& pw = bundles[cp.bundle_idx];
                commit_plan(pw, cp.plan, -1.0);             // rip up victim
                PlanResult mine = plan_bundle(bw, PlanMode::STRICT);
                if (mine.found) {
                    commit_plan(bw, mine);
                    PlanResult theirs = plan_bundle(pw, PlanMode::STRICT);
                    if (theirs.found) {
                        commit_plan(pw, theirs);
                        // Patch the victim's per-level layer mix (audit C3-02):
                        // the refine pass patches layer_hist on every accepted
                        // change, but the rip-up stage did not — so the
                        // '[Planner] Level summary' kept counting the victim's
                        // OLD segment layers. Subtract cp.plan's layers, add
                        // theirs, mirroring the refine pass.
                        {
                            LevelStats& vls = level_stats[pw.hier.level];
                            for (int lid : cp.plan.seg_layers) vls.layer_hist[lid] -= 1;
                            for (int lid : theirs.seg_layers)  vls.layer_hist[lid] += 1;
                        }
                        cp.plan = theirs;
                        assignments[cp.asn_idx] = make_assignment(pw, theirs);
                        std::cout << "[Planner] Rip-up: replanned bundle "
                                  << pw.input.original_bundle.id
                                  << " to free capacity for bundle "
                                  << bw.input.original_bundle.id << ":\n";
                        log_choice(pw, theirs,
                                   std::string(pw.input.topology_pinned ? " [pinned]" : "")
                                   + " [replanned]");
                        plan = mine;
                        already_committed = true;
                        stage = 1;   // rip-up
                        break;
                    }
                    commit_plan(bw, mine, -1.0);            // victim can't recover: undo us
                }
                commit_plan(pw, cp.plan);                   // restore victim
            }
        }

        // (3) Overflow is unavoidable even after rip-up: fall back to soft
        //     pricing so the least-cost overflowing candidate is committed.
        // Policy context for the ladder warnings (hier_layer_caps.md §9.2):
        // a governed bundle's escalation message names the band/shares so
        // the user sees WHY the layer set was small.  Empty for ungoverned
        // bundles — their messages are byte-identical.
        std::string pol_note;
        if (bw.input.layer_cap >= 0) {
            pol_note = " [cell band ";
            pol_note += (bw.input.layer_floor >= 0)
                            ? "[" + std::to_string(bw.input.layer_floor)
                            : std::string("[min");
            pol_note += ".." + std::to_string(bw.input.layer_cap) + "]]";
        }
        if (!bw.input.layer_shares.empty()) {
            pol_note += " [shared:";
            for (const auto& [lid, s] : bw.input.layer_shares)
                pol_note += " L" + std::to_string(lid) + "="
                          + std::to_string((int)std::lround(s * 100)) + "%";
            pol_note += "]";
        }
        if (!plan.found) {
            plan = plan_bundle(bw, PlanMode::ALLOW_OVERFLOW);
            if (plan.found) {
                stage = 2;   // soft overflow
                // A pinned bundle has only its one pinned candidate to commit, so
                // "least-cost candidate" would be misleading — there is no choice.
                if (bw.input.topology_pinned) {
                    std::cout << "[Planner] WARNING: Bundle " << bw.input.original_bundle.id
                              << ": pinned topology overflows and cannot be rerouted"
                              << " (rip-up of other bundles did not help); committing"
                              << " the pinned topology with overflow="
                              << plan.overflow << "." << pol_note << "\n";
                } else {
                    std::cout << "[Planner] WARNING: Bundle " << bw.input.original_bundle.id
                              << ": no overflow-free candidate (even after rip-up); "
                              << "committing least-cost candidate with overflow="
                              << plan.overflow << "." << pol_note << "\n";
                }
            }
        }

        // (4) Every candidate violates its slide windows (bus wider than the
        //     windows; e.g. sidecar pins saved under the old width model) —
        //     commit best-effort so the bundle still gets a layer assignment
        //     instead of an EMPTY seg_layers, which indexed out of bounds and
        //     crashed (flow/channel_stress.buda).
        if (!plan.found) {
            plan = plan_bundle(bw, PlanMode::BEST_EFFORT);
            if (plan.found) {
                stage = 3;   // best-effort
                std::cout << "[Planner] WARNING: Bundle " << bw.input.original_bundle.id
                          << ": no candidate fits its slide windows (bus width "
                          << "exceeds them); committing best-effort "
                          << bw.input.candidates[plan.best_topo].type
                          << (bw.input.topology_pinned ? " [pinned]" : "")
                          << "." << pol_note << "\n";
            }
        }

        if (!plan.found) continue;   // no candidates scored (empty range)

        // Commit the winning topology's per-segment choices to the cut state
        // (the rip-up path already did).
        if (!already_committed) commit_plan(bw, plan);

        committed.push_back({idx, (int)assignments.size(), plan});
        assignments.push_back(make_assignment(bw, plan));
        log_choice(bw, plan, bw.input.topology_pinned ? " [pinned]" : "");

        LevelStats& ls = level_stats[bw.hier.level];
        ls.n += 1;
        ls.by_stage[stage] += 1;
        ls.max_overflow = std::max(ls.max_overflow, plan.overflow);
        for (int lid : plan.seg_layers) ls.layer_hist[lid] += 1;
    }

    // ---- Refinement passes (opt-in: set_planner_param refine_passes N) ----
    // The level-ordering synthesis (docs/congestion_planner.md "Level
    // ordering"; opens.md item 8): one built-in negotiation iteration per
    // pass.  Pass 1 above plans top-down, protecting later cell-locals with
    // the pessimistic interior reservations; those reservations OVER-COUNT,
    // so an early global can detour around phantom congestion (the deep-first
    // A/B: hbundles/01/02 pick 3-segment Z shapes where a straight I_H fits).
    // By refinement time every reservation is released and every bundle's
    // demand is REAL, so revisiting the committed bundles DEEPEST-FIRST
    // (ascending hier priority — the reverse of the commit order, so the
    // widest globals re-decide last, seeing everything) lets a rip-up +
    // STRICT replan either find an improvement against full information or
    // deterministically re-derive the same plan (plan_bundle's tie-breaks
    // are stable), giving a natural fixpoint (early-out when a pass changes
    // nothing).  The A/B's deep-first failure mode (locals planning BLIND,
    // globals unprotected — hbundles/07/10) cannot occur: nothing here plans
    // blind, refinement only re-decides with all demand visible.  A bundle
    // whose replan finds nothing (STRICT infeasible against the current
    // state — e.g. it was committed at the overflow/best-effort stages)
    // keeps its original plan exactly.  Locked (bottom-up template) wrappers
    // are never revisited.  The per-level summary's layer mix is patched on
    // every accepted change; the stage counts keep describing the pass-1
    // ladder.
    if (refine_passes_ > 0) {
        std::vector<int> ref_order(committed.size());
        std::iota(ref_order.begin(), ref_order.end(), 0);
        // stable_sort: same priority + same width is the common case for
        // same-level siblings, and an unstable sort would make the revisit
        // order (and thus refine-enabled results) vary across STL
        // implementations.  Stability pins ties to committed order.
        std::stable_sort(ref_order.begin(), ref_order.end(), [&](int a, int b) {
            const auto& wa = bundles[committed[a].bundle_idx];
            const auto& wb = bundles[committed[b].bundle_idx];
            if (wa.hier.priority != wb.hier.priority)
                return wa.hier.priority < wb.hier.priority;
            return wa.input.width < wb.input.width;
        });
        for (int pass = 1; pass <= refine_passes_; ++pass) {
            int changed = 0;
            for (int k : ref_order) {
                auto& cp = committed[k];
                auto& bw = bundles[cp.bundle_idx];
                if (bw.hier.locked) continue;
                if (bw.input.topology_pinned) continue;      // user pin: keep
                commit_plan(bw, cp.plan, -1.0);              // rip up
                // Strictly-better-than-keeping accept rule.  Adopting any
                // STRICT replan proved too loose (hbundles/10: 23 lateral,
                // score-equal moves reshuffled NUTS packing, 7 -> 78 opens):
                // score BOTH options against the SAME ripped-up state — the
                // best plan KEEPING the old topology (temporary pin) vs the
                // unrestricted best — and adopt only when leaving the old
                // topology is STRICTLY better by the planner's own score.
                // Equal-score switches and same-topology relayering restore
                // the original plan exactly (a same-topo replan can never be
                // strictly better than the pinned probe: they are the same
                // search).  If the old topology is no longer STRICT-feasible
                // (it was committed at the overflow/best-effort stages), any
                // found replan is an improvement by definition.
                int  old_sel = bw.plan.selected_topology_index;
                bw.input.topology_pinned        = true;
                bw.plan.selected_topology_index = cp.plan.best_topo;
                PlanResult keep = plan_bundle(bw, PlanMode::STRICT);
                bw.input.topology_pinned        = false;
                bw.plan.selected_topology_index = old_sel;
                PlanResult np = plan_bundle(bw, PlanMode::STRICT);
                bool adopt = np.found &&
                             (!keep.found || np.score + 1e-9 < keep.score);
                if (!adopt) {
                    commit_plan(bw, cp.plan);                // restore exactly
                    continue;
                }
                commit_plan(bw, np);
                ++changed;
                LevelStats& ls = level_stats[bw.hier.level];
                for (int lid : cp.plan.seg_layers) ls.layer_hist[lid] -= 1;
                for (int lid : np.seg_layers)      ls.layer_hist[lid] += 1;
                cp.plan = np;
                assignments[cp.asn_idx] = make_assignment(bw, np);
                log_choice(bw, np, " [refined]");
            }
            std::cout << "[Planner] Refine pass " << pass << ": " << changed
                      << " of " << committed.size() << " bundle(s) changed.\n";
            if (changed == 0) break;                         // fixpoint
        }
        for (auto& [lvl, ls] : level_stats)
            for (auto it = ls.layer_hist.begin(); it != ls.layer_hist.end();)
                it = (it->second <= 0) ? ls.layer_hist.erase(it) : std::next(it);
    }

    // Per-level planning summary — printed when the set spans hierarchy
    // levels (run_planner hier), where local/global competition lives.
    if (level_stats.size() > 1 || (level_stats.size() == 1 && level_stats.begin()->first > 0)) {
        static const char* stage_names[4] = {"strict", "ripup", "overflow", "best_effort"};
        std::cout << "[Planner] Level summary:\n";
        for (const auto& [lvl, ls] : level_stats) {
            std::cout << "  D" << lvl << ": " << ls.n << " bundles ";
            for (int s = 0; s < 4; ++s)
                if (ls.by_stage[s] > 0)
                    std::cout << " " << stage_names[s] << ":" << ls.by_stage[s];
            std::cout << "  layers{";
            bool first = true;
            for (const auto& [lid, n] : ls.layer_hist) {
                if (!first) std::cout << ' ';
                std::cout << "M" << lid << ":" << n;
                first = false;
            }
            std::cout << "}";
            if (ls.max_overflow > 0.0)
                std::cout << "  max_overflow=" << ls.max_overflow;
            std::cout << "\n";
        }
    }

    return assignments;
}

// Synthetic PlanResult for a wrapper's COMMITTED assignment — the form
// commit_plan/plan_band_overlap consume for charging/ranking without scoring.
CongestionPlanner::PlanResult CongestionPlanner::fixed_plan_of_(const BundleWrapper& bw) {
    PlanResult fixed;
    fixed.found      = true;
    fixed.best_topo  = bw.plan.selected_topology_index;
    fixed.seg_layers = bw.plan.seg_layers;
    fixed.seg_perp   = bw.plan.seg_perp;
    return fixed;
}

// True when the wrapper carries a committed, chargeable assignment.
bool CongestionPlanner::has_committed_plan_(const BundleWrapper& bw) {
    const int sel = bw.plan.selected_topology_index;
    return sel >= 0 && sel < (int)bw.input.candidates.size() &&
           !bw.plan.seg_layers.empty();
}

void CongestionPlanner::recharge_committed_(
        const std::vector<BundleWrapper>& bundles, const BundleWrapper* exclude) {
    // Rebuild band usage from every OTHER bundle's committed assignment —
    // charging only, no scoring.  This is what makes a ripup/negotiation step
    // O(one bundle's candidates) instead of a full-design optimize_topologies.
    // No reservations: every bundle is already planned, so all demand is real.
    // Injected measured-congestion demand rides on top (the reset wiped it).
    for (auto& cut : cuts_) cut.reset_usage();
    // Usage is back to zero, so every recorded distribution describes a charge
    // that no longer exists; the loop below re-records as it re-commits.
    charge_log_.clear();
    share_used_.clear();      // the share books rebuild with the re-commits
    for (const auto& bw : bundles) {
        if (&bw == exclude || !has_committed_plan_(bw)) continue;
        commit_plan(bw, fixed_plan_of_(bw));
    }
    apply_injected_(+1.0);
}

void CongestionPlanner::recharge_committed(
        const std::vector<BundleWrapper>& bundles) {
    if (cuts_.empty()) return;
    recharge_committed_(bundles, nullptr);
}

std::optional<BundleAssignment> CongestionPlanner::replan_bundle(
        std::vector<BundleWrapper>& bundles, int target_bundle_id) {
    // Needs the state a prior optimize_topologies established on this instance:
    // the (possibly extended) grid + cuts, and the non-TOP span reference.
    if (x_grid_.empty() || cuts_.empty() || span_ref_eff_ <= 0.0) return std::nullopt;

    BundleWrapper* target = nullptr;
    for (auto& bw : bundles)
        if (bw.input.original_bundle.id == target_bundle_id) { target = &bw; break; }
    if (!target || target->input.candidates.empty()) return std::nullopt;
    if (target->hier.locked) return std::nullopt;   // template copy: not movable
    const int tsel = target->plan.selected_topology_index;
    if (tsel < 0 || tsel >= (int)target->input.candidates.size()) return std::nullopt;

    recharge_committed_(bundles, target);

    // Escalation ladder for the target, minus rip-up (stage 2): a ripup TRIAL
    // may not move other bundles, and its hill-climb IS the outer rip-up.
    // (Negotiation, which owns its acceptance guard, uses replan_bundle_ripup.)
    PlanResult plan = plan_bundle(*target, PlanMode::STRICT);
    if (!plan.found) plan = plan_bundle(*target, PlanMode::ALLOW_OVERFLOW);
    if (!plan.found) plan = plan_bundle(*target, PlanMode::BEST_EFFORT);
    if (!plan.found) return std::nullopt;
    commit_plan(*target, plan);
    return make_assignment(*target, plan);
}

std::optional<std::vector<BundleAssignment>>
CongestionPlanner::replan_candidates(
        std::vector<BundleWrapper>& bundles, int target_bundle_id,
        const std::vector<int>& tidxs) {
    if (x_grid_.empty() || cuts_.empty() || span_ref_eff_ <= 0.0)
        return std::nullopt;
    BundleWrapper* target = nullptr;
    for (auto& bw : bundles)
        if (bw.input.original_bundle.id == target_bundle_id) {
            target = &bw; break;
        }
    if (!target || target->input.candidates.empty()) return std::nullopt;
    if (target->hier.locked) return std::nullopt;   // template copy
    const int  old_sel = target->plan.selected_topology_index;
    const bool old_pin = target->input.topology_pinned;
    // The O(all bundles) recharge happens ONCE; the per-candidate ladder
    // below never commits, so every candidate scores against this same
    // others-only usage — identical to what each separate replan_bundle
    // call saw (its recharge wiped the previous call's commit).
    recharge_committed_(bundles, target);
    std::vector<BundleAssignment> out;
    out.reserve(tidxs.size());
    for (int tidx : tidxs) {
        if (tidx < 0 || tidx >= (int)target->input.candidates.size()) {
            target->plan.selected_topology_index = old_sel;
            target->input.topology_pinned = old_pin;
            return std::nullopt;
        }
        target->plan.selected_topology_index = tidx;
        target->input.topology_pinned = true;
        PlanResult plan = plan_bundle(*target, PlanMode::STRICT);
        if (!plan.found) plan = plan_bundle(*target, PlanMode::ALLOW_OVERFLOW);
        if (!plan.found) plan = plan_bundle(*target, PlanMode::BEST_EFFORT);
        if (!plan.found) {
            target->plan.selected_topology_index = old_sel;
            target->input.topology_pinned = old_pin;
            return std::nullopt;
        }
        out.push_back(make_assignment(*target, plan));
    }
    target->plan.selected_topology_index = old_sel;
    target->input.topology_pinned = old_pin;
    return out;
}

std::vector<CongestionPlanner::CandidateCost>
CongestionPlanner::candidate_costs(
        std::vector<BundleWrapper>& bundles, int target_bundle_id) {
    // Read-only debug scorer: return the planner cost of EVERY candidate of one
    // bundle against the current committed state, with the per-segment and
    // WL/seg-score breakdown.  Charges nothing (plan_bundle restores cuts_ per
    // candidate), so the committed plan is unchanged on return.  Needs the state
    // a prior optimize_topologies established (grid + cuts + span reference); an
    // empty return tells the caller to fall back to the intrinsic estimate.
    std::vector<CandidateCost> out;
    if (x_grid_.empty() || cuts_.empty() || span_ref_eff_ <= 0.0) return out;

    BundleWrapper* target = nullptr;
    for (auto& bw : bundles)
        if (bw.input.original_bundle.id == target_bundle_id) { target = &bw; break; }
    if (!target || target->input.candidates.empty()) return out;

    // Charge every OTHER bundle's committed assignment so each candidate is
    // scored against the true congestion the planner saw (the hybrid view's
    // "real cost" source); the target itself is excluded so it doesn't pay for
    // its own committed metal.
    recharge_committed_(bundles, target);

    // Score ALL candidates: clear both pin forms so plan_bundle sweeps the full
    // pool (cand_indices == [0, n)).  Restore the pin state afterward.
    const int  old_sel = target->plan.selected_topology_index;
    const bool old_pin = target->input.topology_pinned;
    const auto old_grp = target->input.pinned_group;
    target->input.topology_pinned = false;
    target->input.pinned_group.clear();

    // Two passes so the debug view shows the SAME layer/cost the planner would
    // actually charge (issue: BEST_EFFORT alone under-reports congested
    // candidates).  Under STRICT, plan_bundle SKIPS an overflowing layer and the
    // dead-span gate fires, so a feasible candidate's chosen layer + cost match
    // what the optimizer's STRICT sweep committed.  STRICT breaks out of a truly
    // infeasible candidate (every layer of some segment overflows), leaving a
    // PARTIAL score — so a second BEST_EFFORT pass (no overflow/window
    // enforcement, never breaks) supplies a COMPLETE, comparable breakdown for
    // exactly those, flagged infeasible.  Both passes score against the same
    // recharged others-only state (plan_bundle restores cuts_ to the entry
    // snapshot per candidate), and both sweep the full pool, so each records one
    // entry per candidate.
    std::vector<CandidateCost> strict, effort;
    plan_bundle(*target, PlanMode::STRICT,      nullptr, &strict);
    plan_bundle(*target, PlanMode::BEST_EFFORT, nullptr, &effort);

    target->plan.selected_topology_index = old_sel;
    target->input.topology_pinned = old_pin;
    target->input.pinned_group = old_grp;

    // Merge: a STRICT-feasible candidate keeps its real STRICT cost/layer; an
    // infeasible one takes the complete BEST_EFFORT breakdown, flagged
    // !feasible (the caller sinks it below the feasible candidates, mirroring
    // the ladder — the planner only reaches such a candidate under duress).
    std::map<int, CandidateCost> eff_by_idx;
    for (auto& e : effort) eff_by_idx[e.cand_index] = std::move(e);
    out.reserve(strict.size());
    for (auto& s : strict) {
        if (s.feasible) { out.push_back(std::move(s)); continue; }
        auto it = eff_by_idx.find(s.cand_index);
        if (it != eff_by_idx.end()) {
            it->second.feasible = false;      // STRICT rejected it (overflow)
            out.push_back(std::move(it->second));
        } else {
            out.push_back(std::move(s));      // no fallback row (shouldn't happen)
        }
    }

    // Restore the full committed state (re-include the target) so a subsequent
    // planner/NUTS/ripup call sees exactly the books it had before this probe.
    recharge_committed_(bundles, nullptr);
    return out;
}

std::vector<BundleAssignment> CongestionPlanner::replan_bundle_ripup(
        std::vector<BundleWrapper>& bundles, int target_bundle_id) {
    // replan_bundle WITH the ladder's victim rip-up stage (wishlist-healer item
    // 1 v2b): when the target has no overflow-free candidate against the
    // committed+injected demand, rip up the committed bundle holding the most
    // demand on the contended bands, replan the pair, and accept only if BOTH
    // end up overflow-free — the same dance optimize_topologies runs, applied
    // to a single negotiation step.  Returns the target's assignment first,
    // then the moved victim's (if any); empty = unplannable (caller skips).
    std::vector<BundleAssignment> out;
    if (x_grid_.empty() || cuts_.empty() || span_ref_eff_ <= 0.0) return out;

    // Env-gated per-call profile (BUDA_REPLAN_PROF=1): where does a call's
    // time go — the recharge-all, the STRICT plan, the victim ladder (and how
    // many victims / plan_bundle sweeps it grinds), or the fallbacks?
    static const bool kProf = [] {
        const char* e = std::getenv("BUDA_REPLAN_PROF");
        return e && *e && *e != '0';
    }();
    using clock_ = std::chrono::steady_clock;
    auto ms_since = [](clock_::time_point t0) {
        return std::chrono::duration<double, std::milli>(clock_::now() - t0)
            .count();
    };
    const auto t_call = clock_::now();

    BundleWrapper* target = nullptr;
    for (auto& bw : bundles)
        if (bw.input.original_bundle.id == target_bundle_id) { target = &bw; break; }
    if (!target || target->input.candidates.empty()) return out;
    if (target->hier.locked) return out;            // template copy: not movable
    const int tsel = target->plan.selected_topology_index;
    if (tsel < 0 || tsel >= (int)target->input.candidates.size()) return out;

    auto t0 = clock_::now();
    recharge_committed_(bundles, target);
    const double ms_recharge = ms_since(t0);

    std::set<std::pair<int,int>> contended;
    t0 = clock_::now();
    PlanResult plan = plan_bundle(*target, PlanMode::STRICT, &contended);
    const double ms_strict = ms_since(t0);
    const bool strict_found = plan.found;
    bool committed = false;
    BundleAssignment victim_asn;
    bool moved_victim = false;
    double ms_ladder = 0.0;
    int n_ranked = 0, n_victims = 0, n_ladder_plans = 0;
    if (!plan.found && !contended.empty()) {
        t0 = clock_::now();
        std::vector<std::pair<double, BundleWrapper*>> ranked;
        for (auto& bw : bundles) {
            if (&bw == target || !has_committed_plan_(bw)) continue;
            if (bw.hier.locked) continue;   // template copy: never a victim
            double ovl = plan_band_overlap(bw, fixed_plan_of_(bw), contended);
            if (ovl > 0.0) ranked.push_back({ovl, &bw});
        }
        std::sort(ranked.begin(), ranked.end(),
                  [](const auto& a, const auto& b) { return a.first > b.first; });
        n_ranked = (int)ranked.size();
        for (auto& [ovl, pw] : ranked) {
            ++n_victims;
            const PlanResult fixed = fixed_plan_of_(*pw);
            commit_plan(*pw, fixed, -1.0);              // rip up the blocker
            PlanResult mine = plan_bundle(*target, PlanMode::STRICT);
            ++n_ladder_plans;
            if (mine.found) {
                commit_plan(*target, mine);
                PlanResult theirs = plan_bundle(*pw, PlanMode::STRICT);
                ++n_ladder_plans;
                if (theirs.found) {                     // both overflow-free
                    commit_plan(*pw, theirs);
                    victim_asn   = make_assignment(*pw, theirs);
                    moved_victim = true;
                    plan         = mine;
                    committed    = true;
                    break;
                }
                commit_plan(*target, mine, -1.0);       // victim can't recover
            }
            commit_plan(*pw, fixed);                    // restore and try next
        }
        ms_ladder = ms_since(t0);
    }
    t0 = clock_::now();
    int n_fallback = 0;
    if (!plan.found) { plan = plan_bundle(*target, PlanMode::ALLOW_OVERFLOW); ++n_fallback; }
    if (!plan.found) { plan = plan_bundle(*target, PlanMode::BEST_EFFORT); ++n_fallback; }
    const double ms_fallback = ms_since(t0);
    if (kProf) {
        // std::cerr: negotiate silences the iteration body's stdout (Python
        // redirect + ostream_redirect), so cout lines would be swallowed.
        std::cerr << "[ReplanProf] b" << target_bundle_id
                  << " total=" << ms_since(t_call)
                  << "ms recharge=" << ms_recharge
                  << " strict=" << ms_strict << "(found=" << strict_found
                  << " contended=" << contended.size() << ")"
                  << " ladder=" << ms_ladder << "(ranked=" << n_ranked
                  << " tried=" << n_victims << " plans=" << n_ladder_plans
                  << " committed=" << committed << ")"
                  << " fallback=" << ms_fallback << "(n=" << n_fallback << ")"
                  << " pb_cum[calls=" << prof_plan_calls_
                  << " cands=" << prof_cands_
                  << " layerload=" << prof_layerload_us_ / 1000.0
                  << "ms scoring=" << prof_scoring_us_ / 1000.0 << "ms]\n";
    }
    if (!plan.found) return out;
    if (!committed) commit_plan(*target, plan);
    out.push_back(make_assignment(*target, plan));
    if (moved_victim) out.push_back(victim_asn);
    return out;
}

void CongestionPlanner::extend_grid_for(
        const std::vector<BundleWrapper>& bundles) {
    // Ensure base grid is populated from floorplan.
    if (x_grid_.empty()) build_congestion_map();

    // Extend the Hanan grid only with segment endpoint coordinates that fall
    // OUTSIDE the current grid's range.  Topology generators place in-grid
    // segments at Hanan-cell midpoints; inserting those as new grid lines would
    // split cells into tiny sub-bands with zero capacity and cause violations.
    // Out-of-range coordinates (e.g. U-shape trunks beyond the chip boundary)
    // have no covering cell at all and would receive the ±50 fallback interval.
    //
    // Public (and idempotent) because a rebuild_cuts_ here WIPES injected
    // demand: a caller that wants inject_band_demand to survive the
    // optimize_topologies run (the bottom-up negotiate price translation)
    // must pre-extend with the same bundles BEFORE injecting, so the
    // optimizer's own call finds nothing new and never rebuilds.
    size_t nx0 = x_grid_.size(), ny0 = y_grid_.size();
    auto extend_oob = [](std::vector<int>& grid, int val) {
        if (grid.size() < 2) return;
        if (val >= grid.front() && val <= grid.back()) return; // inside — skip
        auto it = std::lower_bound(grid.begin(), grid.end(), val);
        if (it == grid.end() || *it != val) grid.insert(it, val);
    };
    for (const auto& bw : bundles) {
        for (const auto& cand : bw.input.candidates) {
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
        rebuild_cuts_();
    }
}

void CongestionPlanner::inject_band_demand(int layer_id,
                                           double span_lo, double span_hi,
                                           double perp_lo, double perp_hi,
                                           double amount) {
    if (cuts_.empty() || amount <= 0.0) return;
    const Layer* layer = layers_.get_layer(layer_id);
    if (!layer) return;
    // A synthetic segment along the layer's routing direction at the overlap
    // rectangle's perpendicular centre reuses the exact geometry->band rule
    // every real charge goes through (for_each_band).
    const int perp_c = (int)std::llround(0.5 * (perp_lo + perp_hi));
    const int s_lo   = (int)std::llround(span_lo);
    const int s_hi   = (int)std::llround(span_hi);
    Segment seg;
    if (layer->dir == LayerDir::HORIZONTAL) {
        seg.start = Point{s_lo, perp_c};
        seg.end   = Point{s_hi, perp_c};
    } else {
        seg.start = Point{perp_c, s_lo};
        seg.end   = Point{perp_c, s_hi};
    }
    seg.layer_hint = layer_id;
    for_each_band(seg, layer_id, perp_c, [&](int ci, int b) {
        injected_.emplace_back(ci, b, amount);
        cuts_[ci].add_usage(b, amount);       // visible to direct cost queries
    });
}

void CongestionPlanner::clear_injected_demand() {
    apply_injected_(-1.0);
    injected_.clear();
}

std::vector<std::pair<int, double>> CongestionPlanner::band_occupants(
        const std::vector<BundleWrapper>& bundles, int layer_id,
        double span_lo, double span_hi,
        double perp_lo, double perp_hi, int top_k,
        const std::vector<std::tuple<int, int, int>>& placed) const {
    std::vector<std::pair<int, double>> out;
    if (cuts_.empty() || top_k <= 0) return out;
    const Layer* layer = layers_.get_layer(layer_id);
    if (!layer) return out;
    // The same synthetic rectangle->bands mapping inject_band_demand uses.
    const int perp_c = (int)std::llround(0.5 * (perp_lo + perp_hi));
    const int s_lo   = (int)std::llround(span_lo);
    const int s_hi   = (int)std::llround(span_hi);
    Segment seg;
    if (layer->dir == LayerDir::HORIZONTAL) {
        seg.start = Point{s_lo, perp_c};
        seg.end   = Point{s_hi, perp_c};
    } else {
        seg.start = Point{perp_c, s_lo};
        seg.end   = Point{perp_c, s_hi};
    }
    seg.layer_hint = layer_id;
    std::set<std::pair<int,int>> bands;
    for_each_band(seg, layer_id, perp_c, [&](int ci, int b) {
        bands.insert({ci, b});
    });
    if (bands.empty()) return out;
    // Optional PLACED-position overlay (the charge_pull_target arc's honest-
    // books mode): rank holders by where the metal actually IS, not where the
    // plan charged it — NUTS's preference chain (pull/face/junction) outranks
    // the charged band, so under divergence a plan-based ranking misses the
    // bundle physically holding the contended bands (and the global pass
    // starves).  Empty overlay = plan-based ranking, bit-identical legacy.
    std::map<std::pair<int, int>, int> placed_perp;
    for (const auto& [bid, si, pp] : placed) placed_perp[{bid, si}] = pp;
    for (const auto& bw : bundles) {
        if (bw.hier.locked || !has_committed_plan_(bw)) continue;
        PlanResult plan = fixed_plan_of_(bw);
        if (!placed_perp.empty()) {
            const int bid = bw.input.original_bundle.id;
            for (int si = 0; si < (int)plan.seg_perp.size(); ++si) {
                auto it = placed_perp.find({bid, si});
                if (it != placed_perp.end()) plan.seg_perp[si] = it->second;
            }
        }
        double d = plan_band_overlap(bw, plan, bands);
        if (d > 0.0) out.push_back({bw.input.original_bundle.id, d});
    }
    std::sort(out.begin(), out.end(),
              [](const auto& a, const auto& b) { return a.second > b.second; });
    if ((int)out.size() > top_k) out.resize(top_k);
    return out;
}

void CongestionPlanner::apply_injected_(double sign) {
    for (const auto& [ci, b, amount] : injected_)
        // Guard the BAND index too, not just the cut index (audit C3-03): a
        // cuts_ rebuild can shrink a cut's band count, turning a stale record
        // into an out-of-bounds write into band_usage_.  (rebuild_cuts_ also
        // clears injected_ now, so cross-rebuild records can't mischarge a
        // reordered cut — this is the belt-and-braces bound.)
        if (ci >= 0 && ci < (int)cuts_.size() &&
            b >= 0 && b < cuts_[ci].num_bands())
            cuts_[ci].add_usage(b, sign * amount);
}

} // namespace buda
