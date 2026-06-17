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

#pragma once
#include <climits>
#include <functional>
#include <set>
#include <utility>
#include "bundler.h"
#include "topology.h"
#include "layering.h"
namespace buda {

// One Hanan-grid cut subdivided into perpendicular bands.
// V-cut (dir=VERTICAL):   x fixed, bands along Y grid → counts H-segments crossing it.
// H-cut (dir=HORIZONTAL): y fixed, bands along X grid → counts V-segments crossing it.
struct GlobalCut {
    Point    p1, p2;           // endpoints of the cut line (for visualisation)
    int      cut_coord = 0;    // x_mid (V-cut) or y_mid (H-cut), rounded to int
    // Exact midpoint doubled (= g_lo + g_hi of the cell along the cut axis).
    // cut_coord truncates the true half-integer midpoint of an odd-width cell;
    // for a width-1 cell that collapses it onto the lower grid line, so a
    // closed-interval segment match would falsely charge a neighbour ending
    // there.  Matching uses cut_coord_2x against 2*lo / 2*hi to stay exact.
    int      cut_coord_2x = 0;
    LayerDir dir;
    int      layer_id = 0;

    int    num_bands() const { return static_cast<int>(band_cap_.size()); }
    double cap(int b)  const { return band_cap_[b]; }
    double usage(int b) const { return band_usage_[b]; }
    // Read-only views for Python (returns by value, safe copy).
    std::vector<double> caps()   const { return band_cap_; }
    std::vector<double> usages() const { return band_usage_; }

    void init_bands(int n, const std::function<double(int)>& cap_fn) {
        band_cap_.resize(n);
        band_usage_.assign(n, 0.0);
        for (int b = 0; b < n; ++b) band_cap_[b] = cap_fn(b);
    }
    void reset_usage() { std::fill(band_usage_.begin(), band_usage_.end(), 0.0); }
    void add_usage(int b, double delta) { band_usage_[b] += delta; }

private:
    std::vector<double> band_cap_;    // capacity per perpendicular Hanan band
    std::vector<double> band_usage_;  // accumulated demand per band
};

struct BundleInput {
    HBundle original_bundle;
    std::vector<Topology> candidates;
    double width = 1.0;
    // Manual layer overrides per segment.  Values are layer IDs, or -1
    // for no override (let the planner decide).
    std::vector<int> pinned_seg_layers;
    // Legacy per-direction overrides (set by post_nuts; secondary to seg_layers).
    int assigned_v_layer = -1;
    int assigned_h_layer = -1;
    bool topology_pinned = false;
};

struct BundlePlan {
    int selected_topology_index = -1;
    // Per-segment layer assignments set by CongestionPlanner (primary).
    // Index matches topo.segments of the selected topology.
    std::vector<int> seg_layers;
    // Per-segment perpendicular band preference set by CongestionPlanner:
    // the centre of the Hanan band the slide-aware lookup charged the
    // segment to.  INT_MIN = no preference.  NUTS uses it as the preferred
    // placement so buses land in the bands whose capacity the planner
    // actually reserved (connectivity pulls still take precedence).
    std::vector<int> seg_perp;
    // Per-segment net_pull override (INT_MIN = use ConnTopology's computed
    // value).  Set by the dogleg pass: a split rewrites the topology so
    // ConnTopology would recompute the bundle's pulls wrongly, so we pin them —
    // stubs keep their pre-split pull, both sub-trunks inherit the trunk's, and
    // the jog is net-zero.
    std::vector<int> seg_net_pull;
    // Per-segment slide-window override (NaN = use ConnTopology's computed
    // range).  Set by the dogleg pass: each sub-trunk inherits the ORIGINAL
    // trunk's slide range (ConnTopology would give each piece a narrower range
    // from its stub subset), and the jog is clamped to the trunk's stub extent.
    std::vector<double> seg_slide_lo;
    std::vector<double> seg_slide_hi;
};

struct BundleHierMeta {
    int    level    = 0;
    double priority = 0.0;  // Higher = route first. Set by run_planner hier.
    // Demand reservation: while this bundle is still unplanned, its effective
    // bus width is parked as virtual usage on the TOP-layer bands inside this
    // region (the parent cell instance bbox, set by run_planner hier for
    // cell-local bundles).  Earlier-planned bundles then avoid bands that
    // cannot hold both them and this bundle.
    bool has_reservation = false;
    int  res_x1 = 0, res_y1 = 0, res_x2 = 0, res_y2 = 0;
};

struct BundleWrapper {
    BundleInput    input;
    BundlePlan     plan;
    BundleHierMeta hier;
};

struct BundleAssignment {
    int bundle_id;
    int topo_index;
    int v_layer_id;              // representative V layer (logging)
    int h_layer_id;              // representative H layer (logging)
    std::vector<int> seg_layers; // per-segment assignments (same order as topo.segments)
    std::vector<int> seg_perp;   // per-segment charged-band centres (INT_MIN = none)
};

class CongestionPlanner {
public:
    CongestionPlanner(const Floorplan& fp, const LayerStack& layers);
    // Tune global planner knobs.  Recognised names:
    //   "kCong"            — overflow cost coefficient: cost = kCong*(overflow/cap) (default 1.0)
    //   "kSpan"            — span-mismatch cost per layout-unit (default 0.001)
    //   "base_cost_non_top"— flat penalty for non-TOP layers (default 0.5)
    //   "kWL"              — wirelength cost per layout-unit (default 0.001);
    //                        steers ties toward shorter topologies so detours
    //                        only win when they avoid real congestion
    //   "base_span_ref"    — span at which a segment pays the full non-TOP
    //                        penalty; shorter segments pay proportionally
    //                        less (default: 25% of the larger grid extent)
    void set_planner_param(const std::string& name, double value);
    // Minimum inter-bus spacing, mirroring NUTSEngine::set_track_pitch.  The
    // band books reserve one pitch of margin per additional bus in a band so a
    // band the planner books full is actually packable by NUTS (Gap 1).
    void set_track_pitch(double pitch) { track_pitch_ = pitch; }
    void build_congestion_map();
    std::vector<BundleAssignment> optimize_topologies(
            std::vector<BundleWrapper>& bundles, int max_iterations);
    const std::vector<GlobalCut>& get_cuts() const { return cuts_; }
    const std::vector<int>& get_x_grid() const { return x_grid_; }
    const std::vector<int>& get_y_grid() const { return y_grid_; }

private:
    void rebuild_cuts_();
    // Overflow congestion cost: kCong * max(0, (usage+eff-cap)/cap).  Zero below capacity.
    // perp_pos_override: if != INT_MIN, replaces seg.start.x/y for the perpendicular band
    // lookup.  Pass the ConnTopology interval centre so grid-boundary stubs land in the
    // correct Hanan cell rather than the adjacent one chosen by find_band's half-open rule.
    // slide_lo/slide_hi: if set, the segment's slide window clamps each band's
    // capacity to the window's overlap with that band — demand confined to a
    // sub-band window (slide bounds are usually not Hanan lines) must not be
    // priced against the whole band.
    double cong_cost_segment(const Segment& seg, int layer_id, double eff_width,
                             int perp_pos_override = INT_MIN,
                             int slide_lo = INT_MIN, int slide_hi = INT_MIN) const;
    // Raw overflow for logging (usage+eff - cap, clamped to 0).
    double score_segment(const Segment& seg, int layer_id, double eff_width,
                         int perp_pos_override = INT_MIN,
                         int slide_lo = INT_MIN, int slide_hi = INT_MIN) const;
    void   apply_segment(const Segment& seg, int layer_id, double eff_width,
                         int perp_pos_override = INT_MIN);
    // Span-mismatch cost: kSpan(layer) * max(0, span_min-span, span-span_max).
    double span_cost_for(double seg_span, int layer_id) const;

    // Planning admissibility modes, in decreasing strictness:
    //   STRICT         — slide-window AND overflow-free required.  Overflow is
    //                    a hard constraint: an overflowing band cannot
    //                    physically host the bus, so NUTS would emit a real
    //                    overlap no matter how the soft costs balance.
    //   ALLOW_OVERFLOW — slide-window required; overflow priced softly via
    //                    cong_cost_segment.  Fallback when no overflow-free
    //                    plan exists even after rip-up.
    //   BEST_EFFORT    — no gates (legacy fallback for pinned bundles whose
    //                    slide windows are narrower than the bus).
    enum class PlanMode { STRICT, ALLOW_OVERFLOW, BEST_EFFORT };

    // One bundle's scored plan: winning candidate index plus per-segment
    // layer and perp-band choices.  Produced by plan_bundle (pure scoring —
    // cut state untouched on return); applied/reverted with commit_plan.
    struct PlanResult {
        bool   found     = false;
        int    best_topo = 0;
        double score     = 0.0;
        double overflow  = 0.0;
        std::vector<int> seg_layers;
        std::vector<int> seg_perp;
    };

    // contended (optional, STRICT only): receives the (cut_index, band) pairs
    // whose overflow disqualified a layer choice — i.e. the bands rip-up must
    // relieve for this bundle to become plannable.
    PlanResult plan_bundle(const BundleWrapper& bw, PlanMode mode,
                           std::set<std::pair<int,int>>* contended = nullptr);
    // sign=+1 applies the plan's demand to the cut state; sign=-1 rips it up.
    void commit_plan(const BundleWrapper& bw, const PlanResult& plan, double sign = 1.0);
    BundleAssignment make_assignment(const BundleWrapper& bw, const PlanResult& plan) const;
    void log_choice(const BundleWrapper& bw, const PlanResult& plan, const std::string& tag) const;

    // Invoke fn(cut_index, band) for every cut/band this segment loads —
    // the same matching rule as apply_segment, factored for reuse.
    void for_each_band(const Segment& seg, int layer_id, int perp_pos_override,
                       const std::function<void(int, int)>& fn) const;
    // Insert into `out` the (cut_index, band) pairs where placing the segment
    // would overflow (the band-set sibling of score_segment).
    void collect_overflow_bands(const Segment& seg, int layer_id, double eff_width,
                                int perp_pos_override, int slide_lo, int slide_hi,
                                std::set<std::pair<int,int>>& out) const;
    // Total effective width a committed plan contributes to the given bands.
    // Used to rank rip-up victims by how much relief ripping them offers.
    double plan_band_overlap(const BundleWrapper& bw, const PlanResult& plan,
                             const std::set<std::pair<int,int>>& contended) const;
    // Park (sign=+1) or release (sign=-1) the bundle's reserved demand as
    // virtual usage on TOP-layer bands inside its reservation region.
    void apply_reservation(const BundleWrapper& bw, double sign);

    int    find_band(bool is_vcut, int perp_pos) const;

    // Band capacity usable by a segment confined to [slide_lo, slide_hi]:
    // band_cap clamped by the window's overlap with the band.
    double usable_band_cap(const GlobalCut& c, int b, bool is_vcut,
                           int slide_lo, int slide_hi) const;

    // Slide-aware band choice: among the Hanan bands that overlap the
    // segment's slide interval [slide_lo, slide_hi] by at least eff_width
    // (i.e. NUTS could legally place the bus there), return a perpendicular
    // coordinate inside the band with the lowest peak congestion cost across
    // the cuts the segment crosses.  Ties break toward the interval centre.
    // Falls back to the interval centre when no band can host the bus.
    int best_band_perp(const Segment& seg, int layer_id, double eff_width,
                       int slide_lo, int slide_hi) const;

    const Floorplan&  floorplan_;
    const LayerStack& layers_;
    std::vector<GlobalCut> cuts_;
    std::vector<int> x_grid_, y_grid_;
    // Block footprints, cached at cut-rebuild time; used by for_each_band to
    // clamp block-attached segments to their endpoint-block faces on non-TOP
    // layers.
    std::vector<std::pair<std::string, Rect>> blocks_cache_;

    // Tunable cost coefficients.
    double kCong_             = 1.0;
    double kSpan_             = 0.001;
    double base_cost_non_top_ = 0.5;
    double kWL_               = 0.001;
    // Span reference for scaling the non-TOP penalty: a segment of length
    // base_span_ref_ (or longer) pays the full base_cost_non_top_; shorter
    // segments pay proportionally less, so short local stubs offload to
    // lower layers instead of detouring on TOP.  <= 0 = unset: derived per
    // optimize_topologies run as 25% of the larger Hanan grid extent.
    double base_span_ref_     = -1.0;
    double span_ref_eff_      = 0.0;   // resolved value for the current run
    // Inter-bus spacing (mirrors NUTSEngine::track_pitch_).  Each segment is
    // charged eff_width + track_pitch_ and each band granted cap + track_pitch_,
    // so k buses in a band reserve the (k-1)*pitch of separation NUTS enforces.
    double track_pitch_       = 1.0;
};

} // namespace buda
