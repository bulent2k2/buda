#pragma once
#include <climits>
#include "bundler.h"
#include "topology.h"
#include "layering.h"
namespace buda {

// One Hanan-grid cut subdivided into perpendicular bands.
// V-cut (dir=VERTICAL):   x fixed, bands along Y grid → counts H-segments crossing it.
// H-cut (dir=HORIZONTAL): y fixed, bands along X grid → counts V-segments crossing it.
struct GlobalCut {
    Point    p1, p2;           // endpoints of the cut line (for visualisation)
    int      cut_coord = 0;    // x_mid (V-cut) or y_mid (H-cut)
    LayerDir dir;
    int      layer_id = 0;
    std::vector<double> band_cap;    // capacity per perpendicular Hanan band
    std::vector<double> band_usage;  // accumulated demand per band
};

struct BundleWrapper {
    HBundle original_bundle;
    std::vector<Topology> candidates;
    int selected_topology_index = -1;
    bool topology_pinned = false;
    double width = 1.0;
    double priority = 0.0;  // Higher = route first. Set by run_planner hier.
    // Per-segment layer assignments set by CongestionPlanner (primary).
    // Index matches topo.segments of the selected topology.
    std::vector<int> seg_layers;
    // Manual layer overrides per segment.  Values are layer IDs, or -1
    // for no override (let the planner decide).
    std::vector<int> pinned_seg_layers;
    // Legacy per-direction overrides (set by post_nuts; secondary to seg_layers).
    int assigned_v_layer = -1;
    int assigned_h_layer = -1;
};

struct BundleAssignment {
    int bundle_id;
    int topo_index;
    int v_layer_id;              // representative V layer (logging)
    int h_layer_id;              // representative H layer (logging)
    std::vector<int> seg_layers; // per-segment assignments (same order as topo.segments)
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
    void set_planner_param(const std::string& name, double value);
    void build_congestion_map();
    std::vector<BundleAssignment> optimize_topologies(
            std::vector<BundleWrapper>& bundles, int max_iterations);
    const std::vector<GlobalCut>& get_cuts() const { return cuts_; }
    const std::vector<int>& get_x_grid() const { return x_grid_; }
    const std::vector<int>& get_y_grid() const { return y_grid_; }

private:
    void _rebuild_cuts();
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

    PlanResult plan_bundle(const BundleWrapper& bw, PlanMode mode);
    // sign=+1 applies the plan's demand to the cut state; sign=-1 rips it up.
    void commit_plan(const BundleWrapper& bw, const PlanResult& plan, double sign = 1.0);
    BundleAssignment make_assignment(const BundleWrapper& bw, const PlanResult& plan) const;
    void log_choice(const BundleWrapper& bw, const PlanResult& plan, const std::string& tag) const;

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

    // Tunable cost coefficients.
    double kCong_             = 1.0;
    double kSpan_             = 0.001;
    double base_cost_non_top_ = 0.5;
    double kWL_               = 0.001;
};

} // namespace buda
