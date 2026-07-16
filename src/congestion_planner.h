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
#include <optional>
#include <set>
#include <utility>
#include "bundler.h"
#include "topology.h"
#include "layering.h"
#include "routing_grid.h"
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

    // Signal-track count per FULL band, precomputed once in SIGNAL_TRACKS mode so
    // usable_band_cap can skip the per-call pattern walk when the slide window does
    // not narrow the band (the dominant case).  Empty in width mode.
    void init_sig_ntrk(const std::function<int(int)>& fn) {
        band_sig_ntrk_.resize(num_bands());
        for (int b = 0; b < num_bands(); ++b) band_sig_ntrk_[b] = fn(b);
    }
    bool has_sig_ntrk() const { return !band_sig_ntrk_.empty(); }
    int  sig_ntrk(int b) const { return band_sig_ntrk_[b]; }

private:
    std::vector<double> band_cap_;    // capacity per perpendicular Hanan band
    std::vector<double> band_usage_;  // accumulated demand per band
    std::vector<int>    band_sig_ntrk_;  // cached full-band SIGNAL-track count (track mode)
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
    // Bottom-up template instance: the assignment was decided once in the
    // cell-local solve and copied here (pinned index + pinned layers).  A
    // locked wrapper is planned FIRST (its plan is charged so later bundles
    // detour) and is never chosen as a rip-up victim or a replan/negotiate
    // target — moving one instance would break template uniformity.
    bool locked = false;
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

// How a Hanan band's capacity is measured.
//   WIDTH         — geometric band length minus keepouts (the default model;
//                   continuous layout units).
//   SIGNAL_TRACKS — count of discrete SIGNAL tracks the layer's TrackPattern
//                   places inside the band (× the layer's bit pitch so it stays
//                   comparable to the eff_bus_width demand).  Quantises capacity
//                   exactly as DetailedNUTS does, so a band whose width fit but
//                   whose integer signal-track count is short of the bit count
//                   surfaces as planner overflow instead of a silent DNUTS open.
//                   Opt-in (`run_planner ... signal_tracks`); requires a
//                   RoutingGridStack with def_track_pattern layers.
enum class CapacityMode { WIDTH, SIGNAL_TRACKS };

class CongestionPlanner {
public:
    CongestionPlanner(const Floorplan& fp, const LayerStack& layers);

    // Opt-in signal-track capacity model (Gap A part 2).  Both must be set
    // before build_congestion_map(); the grid pointer must outlive the planner.
    // A layer without a grid entry transparently keeps the WIDTH model.
    void set_routing_grid(const RoutingGridStack* grid) { grid_ = grid; }
    void set_capacity_mode(CapacityMode m) { cap_mode_ = m; }
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
    //   "kPeak"            — peak-band-utilization cost (default 0 = OFF):
    //                        each segment additionally pays kPeak * its worst
    //                        band's EXISTING fill fraction (usage/cap,
    //                        pre-charge — see peak_util_segment for why
    //                        post-charge was measured and rejected).  The
    //                        overflow-only kCong term is ZERO below capacity,
    //                        so without this a candidate headed into a band
    //                        others filled to 95% scores the same as one
    //                        using an empty band — routability-blind
    //                        selection (wishlist-planner "Selection basis"
    //                        lever 1).  With kPeak set, selection (and the
    //                        slide-window band choice in best_band_perp)
    //                        steers off nearly-full bands before any
    //                        overflow materializes.
    //   "kWLSpread"        — realization-risk wirelength penalty (default
    //                        -1 = OFF): when >= 0 and a candidate carries the
    //                        annotated slide/span WL envelope [wl_lo, wl_hi]
    //                        (session-side, gated on this knob), the kWL term
    //                        scores  nominal + kWLSpread * (wl_hi - wl_lo).
    //                        NUTS realizations wander within the DOF
    //                        envelope (corpus fill mean ~15% over 290
    //                        bundles), so a wide-envelope shape (many
    //                        slide-coupled segments) realizes far ABOVE its
    //                        nominal while a tight 2-seg shape realizes at
    //                        or below it — the nominal alone inverts the
    //                        true ranking (flow/big_data_test/b44.buda: a
    //                        6-seg TRUNK_H+MST at nominal 3510 / spread 8650
    //                        realizes 4510, beaten by a 2-seg TRUNK_V at
    //                        nominal 4010 / spread 1500 realizing 3715).
    //                        The base stays the nominal — an envelope-point
    //                        REPLACEMENT (wl_lo + fill*spread) was measured
    //                        and rejected: switching the base to wl_lo
    //                        reshuffles near-ties corpus-wide (big2 +27% WL)
    //                        while the spread penalty alone keeps same-
    //                        spread orderings identical.  Candidates without
    //                        the annotation score the plain nominal.
    void set_planner_param(const std::string& name, double value);
    // Minimum inter-bus spacing, mirroring NUTSEngine::set_track_pitch.  The
    // band books reserve one pitch of margin per additional bus in a band so a
    // band the planner books full is actually packable by NUTS (Gap 1).
    void set_track_pitch(double pitch) { track_pitch_ = pitch; }
    void build_congestion_map();
    std::vector<BundleAssignment> optimize_topologies(
            std::vector<BundleWrapper>& bundles, int max_iterations);
    // Incremental single-bundle replan for ripup_reroute trials: rebuild band
    // usage by CHARGING every other wrapper's committed assignment (no candidate
    // scoring), then run the escalation ladder for the target only — minus the
    // rip-up stage, because a trial asks "does moving ONLY this bundle help"
    // and must not move others.  Returns the target's winning assignment (the
    // caller applies it to the wrapper, exactly like optimize_topologies'
    // results — the pybind list->vector conversion is by copy, so C++-side
    // wrapper writes would be lost).  Requires a prior optimize_topologies on
    // this planner instance (grid, cuts, span_ref).  nullopt if that
    // precondition is missing or the target has no plannable candidate —
    // callers fall back to a full replan.
    std::optional<BundleAssignment> replan_bundle(
            std::vector<BundleWrapper>& bundles, int target_bundle_id);
    // Batched screen replan (RR round 5): recharge every other wrapper's
    // committed assignment ONCE, then plan the target PINNED to each
    // candidate index in turn against that fixed usage — with NO
    // commit_plan (a screen readback, not a route change), so every
    // candidate scores against the identical others-only band state:
    // exactly what a sequence of replan_bundle calls sees, since each of
    // those recharges away its predecessor's commit.  The recharge is the
    // O(all bundles) part of replan_bundle, so batching it is what takes a
    // screen from ~replan-cost to ~one-candidate-ladder cost.  The
    // target's selection/pin are restored before returning.  nullopt on
    // replan_bundle's preconditions, an out-of-range index, or a candidate
    // with no plannable ladder stage (callers fall back to unscreened).
    std::optional<std::vector<BundleAssignment>> replan_candidates(
            std::vector<BundleWrapper>& bundles, int target_bundle_id,
            const std::vector<int>& tidxs);
    // replan_bundle WITH the ladder's victim rip-up stage (wishlist-ripup item
    // 1 v2b): if the target has no overflow-free candidate, rip up the
    // committed bundle holding the most demand on the contended bands and
    // replan the pair (accepted only if both end up overflow-free).  Returns
    // the target's assignment first, then the moved victim's (if any); empty =
    // unplannable.  Used by negotiate_congestion, whose acceptance guard owns
    // the outer safety; ripup TRIALS keep plain replan_bundle (a trial must
    // not move other bundles).
    std::vector<BundleAssignment> replan_bundle_ripup(
            std::vector<BundleWrapper>& bundles, int target_bundle_id);
    // Measured-congestion feedback (negotiate_congestion, wishlist-ripup item
    // 1): map a REAL NUTS overlap rectangle back onto the planner's bands and
    // charge it as extra demand, so the next replan_bundle prices the actual
    // contention the width/track model under-predicted (it reported
    // overflow=0 there).  `amount` is the caller's pressure (typically the
    // overlap's perpendicular extent, history-scaled per iteration).  Records
    // are re-applied by every replan_bundle after it recharges committed
    // assignments (its usage reset would otherwise wipe them); clear starts
    // the next feedback iteration fresh.
    void inject_band_demand(int layer_id, double span_lo, double span_hi,
                            double perp_lo, double perp_hi, double amount);
    void clear_injected_demand();
    // Rebuild band usage from every wrapper's COMMITTED assignment (charging
    // only, no scoring) + the injected demand.  For callers that changed
    // wrapper state without going through a replan — e.g. ripup's
    // commit-by-forward-restore — so direct cut readers (the visualizer's
    // congestion overlay via get_cuts) see the committed route, not the last
    // trial's recharge.  No-op before build_congestion_map.
    void recharge_committed(const std::vector<BundleWrapper>& bundles);
    // Rank committed bundles by their demand on the bands covered by a
    // measured contention rectangle (the ripup global-occupant pass): map the
    // rectangle onto (cut, band) pairs exactly as inject_band_demand does,
    // score every committed, unlocked wrapper with plan_band_overlap on that
    // set, and return the top_k (bundle_id, demand) pairs, demand descending.
    // Read-only (no recharge — a heuristic ranking against the current cut
    // state; the caller's trial measures reality).  Empty when the planner
    // has no cuts yet.
    std::vector<std::pair<int, double>> band_occupants(
            const std::vector<BundleWrapper>& bundles, int layer_id,
            double span_lo, double span_hi,
            double perp_lo, double perp_hi, int top_k) const;
    const std::vector<GlobalCut>& get_cuts() const { return cuts_; }
    const std::vector<int>& get_x_grid() const { return x_grid_; }
    const std::vector<int>& get_y_grid() const { return y_grid_; }

private:
    // Injected measured-congestion demand: (cut index, band, amount), applied
    // on top of the recharged committed assignments in replan_bundle.
    std::vector<std::tuple<int, int, double>> injected_;
    void apply_injected_(double sign);
    // Shared by replan_bundle / replan_bundle_ripup: reset band usage and
    // recharge every committed assignment except `exclude`, then re-apply
    // injected measured-congestion demand.
    void recharge_committed_(const std::vector<BundleWrapper>& bundles,
                             const BundleWrapper* exclude);

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
    // Peak EXISTING utilization (usage/cap, pre-charge — see the .cpp comment
    // for why post-charge was measured and rejected) over the bands this
    // segment would use.  0 when every band is empty or the segment crosses
    // none; can exceed 1 on already-overflowing bands.  Only evaluated when
    // kPeak_ > 0, so the default path costs nothing extra.
    // tracks_needed (> 0 = the bundle's bit count) arms the ABSOLUTE-supply
    // floor: when the planner has a routing grid with this layer patterned
    // and the band's real span-wide signal-track supply cannot host that
    // many bits, util is clamped to >= 1 — an empty-because-unroutable band
    // must never look cheaper than a full one (the big2 kPeak stranding fix;
    // see the .cpp comment).  proportional_floor selects the floor SHAPE by
    // the caller's comparison scope: false (the segment score, which
    // compares across topologies/layers) keeps the flat 1.0; true (the
    // intra-segment band choice, best_band_perp) prices a shortfall as
    // needed/supply so bands that all fall short still rank by how
    // impossible they are — see the floor-shape comment in the .cpp.
    double peak_util_segment(const Segment& seg, int layer_id,
                             int perp_pos_override = INT_MIN,
                             int slide_lo = INT_MIN, int slide_hi = INT_MIN,
                             double tracks_needed = 0.0,
                             bool proportional_floor = false) const;
    // Real span-wide SIGNAL-track supply for `seg` on `layer_id` at
    // perpendicular position `pp` — the exact pool DetailedNUTS places from
    // (routed_extent along-span × the Hanan band at pp ∩ the slide window,
    // via count_signal_tracks_in_span; grid keepouts/overrides honoured).
    // Returns -1 when no supply model applies (no grid, layer un-patterned,
    // or a degenerate window) so the caller leaves the width model in
    // charge.  Shared by peak_util_segment's floor and the non-TOP
    // span-supply gate in plan_bundle.  with_midpoint_fallback mirrors
    // DetailedNUTS's admission — when the strict span-clear pool is short it
    // retries the along-MIDPOINT pool (detailed_nuts.cpp) — so the gate
    // rejects only what DNUTS will actually reject; the kPeak floor keeps the
    // default strict pool (its midpoint-retry variant was measured & rejected,
    // PR #257).  use_raw_span uses the RAW segment along-extent (min/max of
    // the endpoints) that DetailedNUTS's BusSegment carries (nuts.cpp:1195),
    // NOT the endpoint-face-clamped routed_extent: the dead-span GATE must see
    // the whole span DNUTS places over, or a keepout covering only the
    // clamped-away in-cell tail hides from it (Codex #304).  The kPeak floor
    // keeps routed_extent (its clamp deliberately avoids false-flooring
    // pin-access tails on the soft steer).
    int span_signal_supply(const Segment& seg, int layer_id, int pp,
                           int slide_lo, int slide_hi,
                           bool with_midpoint_fallback = false,
                           bool use_raw_span = false) const;
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

    // The segment's routed along-extent on this layer: the raw span, clamped
    // to endpoint leaf-cell faces on non-TOP layers (the in-cell portion is
    // pin access, not routed wire).  lo >= hi means nothing is routed here.
    // The single clamp rule shared by for_each_band's cut matching and
    // peak_util_segment's absolute-supply floor.
    void routed_extent(const Segment& seg, int layer_id, int& lo, int& hi) const;
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
    // Synthetic committed-assignment PlanResult / chargeability check, shared
    // by recharge_committed_ and the ripup victim ranking.
    static PlanResult fixed_plan_of_(const BundleWrapper& bw);
    static bool has_committed_plan_(const BundleWrapper& bw);
    // Park (sign=+1) or release (sign=-1) the bundle's reserved demand as
    // virtual usage on TOP-layer bands inside its reservation region.
    void apply_reservation(const BundleWrapper& bw, double sign);

    int    find_band(bool is_vcut, int perp_pos) const;

    // True when capacity for this layer should be measured in signal-track
    // count: SIGNAL_TRACKS mode is on AND the layer has a def_track_pattern grid
    // (which also guarantees a measured bit pitch).  Layers without a pattern
    // fall back to the WIDTH model.
    bool   track_mode_for(int layer_id) const {
        return cap_mode_ == CapacityMode::SIGNAL_TRACKS &&
               grid_ != nullptr && grid_->has_layer(layer_id);
    }

    // True if a non-TOP (LOW) segment cannot route on layer_id at the given
    // perpendicular position because its routed extent — after excluding the
    // pin-access tails at the two endpoint leaf cells it attaches to — lies over
    // a leaf cell.  Two cases: a mid-span cell crossing, or a segment wholly
    // inside one cell.  Either way the bus must route over-the-cell on a TOP
    // layer, so the LOW layer is infeasible (Gap A).  TOP layers always return
    // false (they tile cells freely).  perp_pos_override == INT_MIN uses the
    // segment's nominal perpendicular coordinate.
    bool   low_seg_obstructed(const Segment& seg, int layer_id,
                              int perp_pos_override) const;

    // Height rank of a TOP layer among the same-direction TOP layers,
    // ascending by layer id (lowest TOP metal = 0).  Drives the kHeight_
    // short-segment cost: via-stack depth grows with every metal above the
    // lowest same-direction TOP layer, so short runs should not float to the
    // top of the stack for free.  Non-TOP layers return 0 (they never pay).
    int    top_height_rank(int layer_id) const;
    // Warn once (build_congestion_map) about non-TOP layers declared ABOVE
    // the TOP band — a config smell the planner now costs as TOP.
    void   warn_above_top_layers_();

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
    // tracks_needed feeds peak_util_segment's absolute-supply floor (kPeak
    // only), so the band choice also steers away from supply-poor bands.
    int best_band_perp(const Segment& seg, int layer_id, double eff_width,
                       int slide_lo, int slide_hi,
                       double tracks_needed = 0.0) const;

    const Floorplan&  floorplan_;
    const LayerStack& layers_;
    // Opt-in signal-track capacity model (Gap A part 2).  nullptr / WIDTH = the
    // default geometric-length model; set via set_routing_grid/set_capacity_mode.
    const RoutingGridStack* grid_ = nullptr;
    CapacityMode cap_mode_ = CapacityMode::WIDTH;
    // Extra signal tracks granted per band in SIGNAL_TRACKS mode — a quantisation
    // slack so exact integer counts do not reject a feasible route by one track.
    // Tunable via set_planner_param("track_cap_slack"); default 0 (exact).
    double track_cap_slack_ = 0.0;
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
    // Realization-risk WL spread penalty (see set_planner_param "kWLSpread").
    // < 0 = OFF (kWL scores the plain nominal estimated_wirelength).
    double kWLSpread_         = -1.0;
    // Same-direction TOP-layer load-balancing weight.  Without it the planner
    // breaks ties toward the highest metal (span/base costs are 0 on a TOP layer
    // with no span window), piling every H bus on the top H layer and every V
    // bus on the top V layer — where nearly all NUTS overlaps then land.  A mild
    // bias toward the less-loaded equal-cost layer spreads the load.  Cost term:
    // kBalance_ * (layer's committed load / max same-dir layer load), in [0,1].
    // 0.01: on big2 this cuts NUTS overlaps 41->9 and DNUTS unplaced 272->60 by
    // spreading TOP load; the plateau holds to ~0.015, then over-balancing starts
    // pushing buses onto LOW layers and the unplaced count climbs again.
    double kBalance_         = 0.01;
    // Layer-height cost for SHORT segments on TOP layers — the mirror image
    // of the span-scaled base_cost_non_top_ below.  Without it the equal-cost
    // tie among TOP layers resolves to the HIGHEST metal (the layers_rev
    // iteration order — right for long trunks, wasteful for a 20-unit stub
    // that then needs the tallest via stack in the design).  Cost term, TOP
    // layers only: kHeight_ * height_rank * max(0, 1 - span/span_ref_eff_),
    // where height_rank is the layer's index among the same-direction TOP
    // layers ascending (lowest TOP metal = 0).  Long trunks (span >= ref) pay
    // 0 and keep their TOP-most preference; short stubs prefer the lowest
    // feasible TOP layer.  0.05: decisively above the kBalance_ tie-noise
    // (<= 0.01) so the steering wins ties, far below base_cost_non_top_ and
    // any real congestion overflow (kCong_ * ov/cap), so it never overrides
    // capacity.  Set to 0 via `set_planner_param kHeight 0` for the legacy
    // highest-metal tie-break.
    double kHeight_          = 0.05;
    // Peak-band-utilization weight (routability-aware selection, lever 1 of
    // the wishlist-planner "Selection basis" item).  The kCong_ term above is
    // overflow-only — ZERO below capacity — so candidate ranking is blind to
    // how full a band gets until it bursts.  With kPeak_ > 0 each segment
    // additionally pays kPeak_ * peak_util_segment(...): its worst band's
    // EXISTING fill fraction (usage/cap, pre-charge — see peak_util_segment
    // for why post-charge was measured and rejected), and the same term
    // joins best_band_perp's slide-window band choice.  This is what lets a
    // candidate avoiding a loaded corridor outrank one that squeezes into
    // it.  Default 0 = OFF: the term is not even evaluated, so existing
    // flows are bit-identical.  Suggested starting value when opting in:
    // 0.05-0.1 (a full band then costs about as much as 50-100 units of
    // extra estimated wirelength at the default kWL 0.001; 0.2 was measured
    // to over-steer on tc3a).
    double kPeak_            = 0.0;
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
    // Refinement passes after the main commit loop (opt-in, default 0 = the
    // loop is skipped entirely — existing flows bit-identical).  Each pass
    // revisits every committed, unlocked bundle DEEPEST-FIRST against the
    // now-REAL usage of everyone else (reservations all released): rip up,
    // STRICT replan, adopt (or restore exactly when STRICT finds nothing).
    // The level-ordering synthesis — one built-in negotiation iteration per
    // pass, with a fixpoint early-out.  See the optimize_topologies comment
    // and docs/congestion_planner.md "Level ordering".
    int    refine_passes_     = 0;
    // Opt-in dead-span gate (default off — existing flows bit-identical).
    // When set, a NON-TOP layer is refused for a segment whose abstract span
    // has ZERO keepout-clear signal tracks in the chosen band (a guaranteed
    // DetailedNUTS open), forcing STRICT escalation to a TOP layer.  See the
    // gate comment in plan_bundle for why it is opt-in (span_pool==0 cannot
    // distinguish genuine culls from survivors whose final adjusted spans
    // clear the keepout).
    bool   nontop_dead_span_gate_ = false;
    // One-shot guard for the above-TOP config-smell warning.
    bool   warned_above_top_ = false;
};

} // namespace buda
