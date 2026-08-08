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
#include <cstdint>
#include <functional>
#include <map>
#include <optional>
#include <set>
#include <tuple>
#include <utility>
#include "bundler.h"
#include "ndr.h"
#include "topology.h"
#include "conn_topology.h"
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
    // Non-default rule resolved for this bundle (phase 1: rule-uniform after
    // the bundler's rule-class split; inactive = byte-identical).  Charged
    // through ndr_group_demand at every eff_bus_width site — see ndr.h.
    NdrSpec ndr;
    // Manual layer overrides per segment.  Values are layer IDs, or -1
    // for no override (let the planner decide).
    std::vector<int> pinned_seg_layers;
    // Legacy per-direction overrides (set by post_nuts; secondary to seg_layers).
    int assigned_v_layer = -1;
    int assigned_h_layer = -1;
    bool topology_pinned = false;
    // Group-pin (select_topology group:<rep>): restrict the planner's candidate
    // search to this set of candidate indices — a "super-candidate" of
    // nominal-locus variants the user pinned as a family, leaving the planner to
    // refine WHICH member (perp/skeleton) wins.  Empty = no group pin, so the
    // selection loop falls back to the historical single-pin / full sweep and is
    // byte-identical.  Takes precedence over topology_pinned when non-empty.
    std::vector<int> pinned_group;
    // ── Per-cell layer policy, binary form (Phase 1 of
    // docs/internal/hier_layer_caps.md) ──────────────────────────────────────
    // The layers this bundle's segments may be ASSIGNED, resolved from the
    // owning cell's band [layer_floor .. layer_cap] (the owning-frame rule:
    // a bundle is governed by the cell whose frame plans it; cross-level
    // bundles take the common ancestor's policy).  EMPTY = unrestricted —
    // every enforcement site short-circuits on empty, which is the whole
    // feature's byte-identity guarantee.  Sorted ascending (binary_search).
    // Enforced in plan_bundle's layer enumeration — the single choke point
    // the STRICT ladder, replan_bundle/replan_bundle_ripup, negotiate and
    // ripup trials all flow through — so no consumer can silently assign a
    // governed segment outside its cell's band.  Explicit pinned_seg_layers
    // stay honored even above the cap (user pins are inviolable; check_design
    // gains a LAYER_CAP advisory for them in a later phase).
    std::vector<int> allowed_layers;
    int layer_cap   = -1;   // declared ceiling (reporting; -1 = none)
    int layer_floor = -1;   // declared -min floor (reporting; -1 = none)
    bool allows_layer(int lid) const {
        return allowed_layers.empty() ||
               std::binary_search(allowed_layers.begin(), allowed_layers.end(), lid);
    }
    // ── Fractional layer shares (Phase 3, hier_layer_caps.md §6) ──────────
    // (layer_id, share in (0,1)) for the layers the owning cell may use only
    // fractionally, sorted by layer_id.  Empty = no shares (byte-identical).
    // Tier-2 enforcement (global-frame bundles): usable_band_cap's
    // track-mode capacity is scaled by share_of(lid) for the wrapper under
    // evaluation, and the SCALAR COLLECTIVE BUDGET refuses a STRICT
    // candidate whose commit would push share_group's total usage on a
    // shared layer past its budget (Q3 resolution: one counter per
    // (cell-instance, shared layer); commit_plan keeps the books, so every
    // trial/recharge/rip-up path stays consistent by construction).
    std::vector<std::pair<int,double>> layer_shares;
    // Collective-budget identity: "<cell>@<instance>" for expanded
    // per-instance wrappers ("" = no budget tracking for this wrapper).
    std::string share_group;
    // Per shared layer: the budget in width units — s × (signal tracks in
    // the instance bbox × bit_pitch) — computed session-side where the
    // routing grid and instance bbox are both known.  Sorted by layer_id.
    std::vector<std::pair<int,double>> share_budgets;
    double share_of(int lid) const {
        for (const auto& [l, s] : layer_shares)
            if (l == lid) return s;
        return 1.0;
    }
    double share_budget_of(int lid) const {   // <0 = no budget declared
        for (const auto& [l, b] : share_budgets)
            if (l == lid) return b;
        return -1.0;
    }
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
    //   "kSegs"            — segment-count penalty in wirelength-equivalent
    //                        units per segment (default 0 = OFF): the kWL
    //                        term scores  wl + kSegs * n_segments, demoting
    //                        many-segment trees whose junction vias (per
    //                        bit!) and realization DOF the WL estimate
    //                        omits — b61: 10-seg TRUNK+MST estimates 9%
    //                        under the 5-seg tree, realizes only 2% under
    //                        it, with 2.25x the detailed vias
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
    // Worker threads for plan_bundle's candidate scoring (chip-flow P1).
    // Candidates are scored against the SAME read-only base cut state (the
    // within-candidate charges live in per-thread overlays), and the winner
    // is picked by an ordered reduction that replays the sequential compare —
    // so results are identical to the sequential loop.  0 = auto (hardware
    // concurrency capped at 8); an explicit n (here or via the
    // BUDA_PLAN_THREADS env var) is honored as given, clamped only to the
    // candidate count — the NUTS layer_threads convention, so scaling
    // experiments above the auto cap are possible.  1 = sequential.
    // parallel_sweep's trial workers set 1 — the move fan-out already owns
    // the cores (no nested pools, as NUTS layer_threads).
    void set_plan_threads(int n) { plan_threads_ = n; }
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

    // ── Debug cost inspection (the topology explorer's `debug` view) ──────────
    // Per-segment cost breakdown at the segment's best-scored layer — the exact
    // terms plan_bundle minimizes (see its scoring loop).
    struct SegCost {
        int    seg_idx = 0;
        int    layer   = -1;
        double cong    = 0.0;   // kCong overflow
        double span    = 0.0;   // span-mismatch
        double non_top = 0.0;   // non-TOP layer penalty
        double balance = 0.0;   // TOP load-balance bias
        double height  = 0.0;   // short-seg via-height
        double peak    = 0.0;   // kPeak routability
        double total   = 0.0;   // sum of the six above (the segment's score)
    };
    // One candidate's planner cost, as plan_bundle scores it: the topology
    // score is max-over-segments of SegCost.total, plus the wirelength term.
    struct CandidateCost {
        int    cand_index = -1;
        double total      = 0.0;   // the value plan_bundle compares (lower wins)
        double seg_cost   = 0.0;   // max over segments of SegCost.total
        double wl_term    = 0.0;   // kWL * (wl_est [+ kWLSpread envelope] [+ kSegs])
        bool   feasible   = true;  // false if a segment overflowed every layer / window infeasible
        std::vector<SegCost> segs;
    };
    // Score EVERY candidate of one bundle against the planner's CURRENT
    // committed band state (recharged others-only — the target's own committed
    // charge excluded), returning per-candidate + per-segment cost breakdowns.
    // Scored under the planner's OWN STRICT layer selection (an overflowing
    // layer is skipped, the dead-span gate fires) so a feasible candidate's
    // cost/layer match what the optimizer committed; a STRICT-infeasible
    // candidate takes a complete BEST_EFFORT breakdown flagged !feasible.
    // Read-only: recharges committed around the call and plan_bundle leaves the
    // cut state untouched, so the planner is unchanged on return.  Empty when
    // the planner isn't set up yet (no grid) — the pre-plan case, where the
    // caller falls back to the intrinsic wirelength cost.
    std::vector<CandidateCost> candidate_costs(
            std::vector<BundleWrapper>& bundles, int target_bundle_id);

    // replan_bundle WITH the ladder's victim rip-up stage (wishlist-healer item
    // 1 v2b): if the target has no overflow-free candidate, rip up the
    // committed bundle holding the most demand on the contended bands and
    // replan the pair (accepted only if both end up overflow-free).  Returns
    // the target's assignment first, then the moved victim's (if any); empty =
    // unplannable.  Used by negotiate_congestion, whose acceptance guard owns
    // the outer safety; ripup TRIALS keep plain replan_bundle (a trial must
    // not move other bundles).
    std::vector<BundleAssignment> replan_bundle_ripup(
            std::vector<BundleWrapper>& bundles, int target_bundle_id);
    // Measured-congestion feedback (negotiate_congestion, wishlist-healer item
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
    // Extend the Hanan grid with the bundles' out-of-range candidate
    // endpoints (the exact pass optimize_topologies runs first) — public and
    // idempotent so a caller can pre-extend BEFORE inject_band_demand: the
    // extension's cuts rebuild wipes injected records, so injections meant
    // to price an upcoming optimize_topologies run must come after it (the
    // bottom-up negotiate price translation).
    void extend_grid_for(const std::vector<BundleWrapper>& bundles);
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
            double perp_lo, double perp_hi, int top_k,
            const std::vector<std::tuple<int, int, int>>& placed = {}) const;
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
    // ── Victim-ladder footprint pruning (replan_bundle_ripup, B1) ──────────
    // Conservative rectangle over every (cut, band) whose USAGE a candidate's
    // scoring could read: cuts of layer_id in the crossing direction (vcut)
    // whose coord lies on the segment's raw along-span [lo2, hi2] (doubled
    // coords, closed — routed_extent only ever shrinks), bands overlapping
    // [p_lo, p_hi] (slide window ∪ nominal perp, inflated by half the charged
    // width eff+pitch, ±1 rounding pad).  Capacities/patterns/keepouts are
    // static during a trial, so usage is the only live input to feasibility.
    struct BandRect {
        int    layer_id;
        bool   vcut;         // matching cuts' direction (H-seg → V-cuts)
        long   lo2, hi2;     // along-span in doubled coords (cut_coord_2x)
        double p_lo, p_hi;   // perpendicular usage-read range
    };
    // The candidate-index sweep plan_bundle runs (pinned_group > single pin >
    // full), factored so the ladder's footprint pass and plan_bundle can never
    // disagree about which candidates a sweep visits.
    std::vector<int> collect_cand_indices_(const BundleWrapper& bw) const;
    // Conservative usage-read footprint of one candidate (see BandRect).
    std::vector<BandRect> cand_band_footprint_(const BundleWrapper& bw,
                                               int ci) const;
    bool footprint_hits_(const std::vector<BandRect>& foot,
                         const std::vector<std::pair<int,int>>& bands) const;
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
    // costs_out (optional): when non-null, records a CandidateCost per scored
    // candidate (with per-segment breakdown) — the read-only source for the
    // debug cost view.  Null (the default) is byte-identical to before.
    // cand_mask (optional): when non-null, restrict the sweep to candidate
    // indices with mask[ci] != 0 (applied AFTER the pin/group selection, so a
    // pinned index outside the mask scores nothing).  Null (the default) is
    // byte-identical to before.  Used by replan_bundle_ripup's victim-ladder
    // footprint pruning, whose soundness argument requires the mask to be a
    // superset of the feasible set — see the ladder comment there.
    PlanResult plan_bundle(const BundleWrapper& bw, PlanMode mode,
                           std::set<std::pair<int,int>>* contended = nullptr,
                           std::vector<CandidateCost>* costs_out = nullptr,
                           const std::vector<char>* cand_mask = nullptr);
    // sign=+1 applies the plan's demand to the cut state; sign=-1 rips it up.
    void commit_plan(const BundleWrapper& bw, const PlanResult& plan, double sign = 1.0);
    // The (cut, band) set a committed plan's rip-up frees: the recorded
    // charge_log_ distributions when present (the exact inverse commit_plan
    // replays), else the same for_each_band_w geometry commit_plan derives.
    // (Declared here, after PlanResult; ladder-pruning siblings live beside
    // BandRect above.)
    void collect_charged_bands_(const BundleWrapper& bw, const PlanResult& plan,
                                std::vector<std::pair<int,int>>& out) const;
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
    // Every cut this segment crosses, with the perpendicular position and cut
    // orientation resolved.  The delicate closed-interval matching rule lives
    // here ONCE; for_each_band and for_each_band_w are both thin wrappers.
    void for_each_cut_(const Segment& seg, int layer_id, int perp_pos_override,
                       const std::function<void(int, bool, int)>& fn) const;
    // Width-aware sibling of for_each_band (issue #518): a bus of eff_width
    // centred at perp occupies [perp - w/2, perp + w/2], which need not lie
    // inside one Hanan band — charging it all to the band holding its centre
    // makes any bus wider than that band overflow BY CONSTRUCTION, even on an
    // empty layer.  Invokes fn(cut, band, weight) over the bands the footprint
    // actually covers, weight = that band's share of the footprint.
    //
    // Weights are NORMALIZED to sum to 1 per cut, so the total charge is
    // exactly eff_width either way: this redistributes demand, it never
    // creates or destroys it (a footprint hanging off the grid edge would
    // otherwise silently under-charge and mask real congestion).
    //
    // With band_span_charge_ off — the default — this delegates to
    // for_each_band with weight 1.0, i.e. literally the old code path.
    void for_each_band_w(const Segment& seg, int layer_id, int perp_pos_override,
                         double eff_width,
                         const std::function<void(int, int, double)>& fn) const;
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
    // Honest-books junction prediction (charge_pull_target follow-on (a2)):
    // topology segments with each along-span extended to every pulled
    // partner's deterministic predicted track; riders_out (optional) = the
    // NUTS jn_map mirror for the single-rider anchor clamp (a1).
    std::vector<Segment> junction_extended_segments(
            const Topology& topo, const std::vector<ConnSeg>& conn_segs,
            std::vector<std::vector<int>>* riders_out) const;
    // True iff the span crosses a (near-)zero-capacity band — the keepout-
    // carved impossibility the junction-extension gate fires on (variant C).
    bool span_hits_dead_band(const Segment& seg, int layer_id,
                             int perp_pos_override,
                             int slide_lo, int slide_hi) const;
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
    // ── Effective-TOP under a layer policy (hier_layer_caps.md §4.3) ─────────
    // ECONOMIC TOP-ness for one bundle: global is_top when unmasked; under a
    // mask, the allowed globally-TOP layers of the lid's direction if any
    // exist, else the HIGHEST allowed layer of that direction is promoted.
    // A scoring context only — physical predicates (leaf clamping in
    // routed_extent, NUTS low-layer keepouts) deliberately stay on the global
    // Layer::type: promotion cannot make M2 stop being low metal.
    bool is_top_for(const BundleInput& in, int lid) const;
    // Height rank within the bundle's effective-TOP set of lid's direction
    // (the kHeight via-stack term); global rank when unmasked.
    int  top_height_rank_for(const BundleInput& in, int lid) const;
    // The bundle's effective TOP layer of a direction (reservations target
    // this instead of the global top under a mask).  -1 if none allowed.
    int  effective_top_layer(const BundleInput& in, LayerDir dir) const;

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
    // Candidate scoring overlay (plan_bundle): while a candidate is being
    // scored, its within-candidate charges live in a per-thread overlay map
    // (see t_cand_overlay in the .cpp) instead of being applied to cuts_ and
    // undone — cuts_ stays READ-ONLY for the whole scoring pass.  The overlay
    // stores exactly the values the historical charge-then-undo produced
    // (first touch copies the band's current usage, later charges +=), so
    // every read through cand_usage_ returns the bit-identical double, and
    // rollback is overlay.clear().  This is what makes scoring candidates on
    // worker threads race-free: threads share the read-only base cuts_ and
    // each owns its overlay.  (Succeeds the cand_undo_ log, which itself
    // replaced the cuts_ deep copy that dominated chip-scale planning.)
    // Band usage as the CURRENTLY-SCORED candidate sees it: its own overlay
    // charge when present, else the committed base value.
    double cand_usage_(int ci, int b) const;
    // The overlay itself: an epoch-stamped flat array over ALL bands
    // (flattened by band_base_), so a read is one branch + one array access
    // and the per-candidate rollback is ++epoch — O(1), no hashing.  A map
    // was measured ~15% slower serially (a hash probe on every hot read).
    struct CandOverlay {
        std::vector<double>   val;
        std::vector<uint32_t> stamp;
        uint32_t epoch = 0;
        uint64_t gen   = 0;             // cuts_gen_ this overlay is sized for
        void clear() {                  // start a new candidate
            if (++epoch == 0) {         // u32 wrap: re-zero once per 4G clears
                std::fill(stamp.begin(), stamp.end(), 0);
                epoch = 1;
            }
        }
    };
    // The calling thread's persistent overlay, (re)sized for THIS planner's
    // cut grid.  thread_local so (a) worker threads each get their own with
    // zero setup per plan_bundle call, and (b) planner copies (trial sweeps)
    // share it rather than duplicating scratch — the generation stamp
    // (cuts_gen_) re-zeros it whenever it last served a different grid.
    CandOverlay& thread_overlay_() const;
    // The scoring thread's active overlay (null outside scoring).  A static
    // thread_local member so worker threads each see their own slot while
    // the type stays private.
    static thread_local CandOverlay* t_cand_overlay_;
    // One candidate's packaged evaluation (score_candidate_): everything the
    // ordered reduction in plan_bundle needs to reproduce the sequential
    // loop's compare, skips, debug-view recording and contended collection.
    struct CandScore {
        bool   infeasible    = false;
        bool   share_refused = false;   // STRICT collective-budget gate
        double score    = 0.0;          // includes the kWL term
        double overflow = 0.0;
        double wl_term  = 0.0;
        std::vector<int>     seg_layers;
        std::vector<int>     seg_perp;
        std::vector<SegCost> seg_costs;             // want_costs only
        std::set<std::pair<int,int>> contended;     // want_contended only
    };
    // Read-only context shared by every candidate evaluation of one
    // plan_bundle call.
    struct CandCtx {
        const std::vector<int>*     h_layers_rev = nullptr;
        const std::vector<int>*     v_layers_rev = nullptr;
        const std::map<int,double>* layer_load   = nullptr;
        double max_h_load = 1.0, max_v_load = 1.0;
        int    nbits = 0;
        bool   enforce_window = true, enforce_overflow = true;
        bool   want_contended = false, want_costs = false;
    };
    // Score ONE candidate against the shared read-only base cut state; the
    // candidate's own charges go to `overlay` (cleared at entry).  Pure per
    // candidate — safe to run concurrently on worker threads, each thread
    // with its own overlay.
    CandScore score_candidate_(const BundleWrapper& bw, int ci, PlanMode mode,
                               const CandCtx& ctx, CandOverlay& overlay);
    // plan_bundle's worker-thread count: set_plan_threads > 0 wins; else
    // BUDA_PLAN_THREADS; else hardware concurrency; clamped to
    // [1, min(ncand, 8)], and 1 below the small-pool gate.
    int resolved_plan_threads_(int ncand) const;
    int plan_threads_ = 0;
    // Flattened band indexing for CandOverlay: band_base_[ci] is the first
    // slot of cut ci's bands; band_base_.back() is the total.  Rebuilt with
    // cuts_ (rebuild_cuts_ is the single builder).
    std::vector<size_t> band_base_;
    // Monotonic id of this planner's cut grid (stamped by rebuild_cuts_ from
    // a process-wide counter): the overlay validity token.  Copied clones
    // share it — their band_base_ is identical, which is all the overlay
    // needs (values are seeded per touch from the CURRENT planner's cuts_).
    uint64_t cuts_gen_ = 0;
    // ── Fractional-share state (Phase 3, hier_layer_caps.md §6 Tier 2) ────
    // Share context of the wrapper plan_bundle is currently evaluating:
    // usable_band_cap's track-mode branch scales capacity by
    // cur_share_input_->share_of(layer) so a shared bundle sees s × supply.
    // Null outside plan_bundle = full capacity (byte-identical).
    const BundleInput* cur_share_input_ = nullptr;
    // Scalar collective budget books (Q3 resolution): committed usage per
    // (share_group, shared layer), in the same width units as the budgets.
    // Maintained EXCLUSIVELY by commit_plan(±1) and reset alongside band
    // usage (build_congestion_map / recharge_committed_), so every trial,
    // rip-up and recharge path stays consistent by construction.
    std::map<std::pair<std::string,int>, double> share_used_;
    // Sum of a plan's effective widths per SHARED layer (the budget's usage
    // metric; excludes the +pitch margin so books match physical demand).
    void share_usage_of_(const BundleWrapper& bw, const Topology& t,
                         const std::vector<int>& seg_layers,
                         std::map<int,double>& out) const;
    // False iff committing (t, seg_layers) would push bw's share_group past
    // any shared layer's budget (the STRICT-mode candidate gate).
    bool share_budget_ok_(const BundleWrapper& bw, const Topology& t,
                          const std::vector<int>& seg_layers) const;
    // for_each_band range index: per (layer_id, dir) the cuts' (coord_2x, ci)
    // pairs, ci-ascending (the build order is coordinate-ascending per layer,
    // so the pairs are sorted by BOTH — a binary search finds the segment's
    // [lo2, hi2] span range without scanning every cut of every layer, and
    // iterating the range preserves the full scan's ci visit order exactly).
    // Rebuilt with cuts_ (rebuild_cuts_); copies stay valid (indices, not
    // pointers).
    std::map<std::pair<int, int>, std::vector<std::pair<long, int>>> cut_index_;
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
    // Segment-count penalty in wirelength-equivalent units per segment
    // (opt-in, default 0 = off): the kWL term scores
    // estimated_wirelength + kSegs * gate * n_segments, so a many-segment
    // tree must buy real wirelength to beat a simpler shape (each segment
    // costs junction vias per bit and realization DOF the WL estimate
    // omits).
    //
    // kSegsGate (EXPERIMENT, default 0 = flat penalty): >0 fades the
    // penalty with the candidate's worst chosen-band fill
    // (peak_util_segment).  MEASURED WORSE and left off: the gate is
    // per-candidate, so a candidate heading into FULL bands pays no
    // penalty — stress becomes attractive (07_wide_fan_stress balloons
    // 183->272 selected segments, 0->11 unplaced, worse than baseline;
    // mempool_tile gives back most of flat kSegs' -54% detWL win).  The
    // sound alternative — a bundle-level candidate-independent gate —
    // would fade the penalty exactly where flat kSegs wins BIGGEST
    // (stressed designs: fewer segments = less total demand); only the
    // measured-metric ripup loop can tell load-bearing tree DOF from
    // gratuitous complexity, and flat kSegs + ripup heals every measured
    // regression (07: 0/0 at +5.8% WL, -25% vias; channel_stress: 0/0).
    double kSegs_             = 0.0;
    // Declared by the flow (`set_planner_param healersAhead 1`, before
    // run_planner — issue #444 replaced the old flow-script text scan) or set
    // by ripup's own re-plan — the audit G1/G2 gate: the non-explicit kSegsRel
    // default (compiled 0.02 / env override) applies only then (the penalty's
    // two measured correctness regressions are both ripup-healed; without
    // healers they are real).  Settable
    // explicitly for harnesses.
    double healersAhead_      = 0.0;
    // Relative kSegs (kSegsRel): fraction of the design's max-possible HPWL
    // (grid extent W + H) added per segment — scale-free across flows.
    // < 0 = unset (resolves to env BUDA_KSEGS_REL else kDefaultKSegsRel);
    // 0 = explicitly off.  Resolved into ksegs_eff_ when the grids are
    // known (beside span_ref_eff_).
    double kSegsRel_          = -1.0;
    // The compiled default for an UNSET kSegsRel (audit verdict: the
    // safe-Pareto point, gated on healers / no multi_trunk / no kPeak so it
    // engages exactly where it was measured to only win).  BUDA_KSEGS_REL
    // overrides it for study runs ("0" disables); an explicit
    // set_planner_param bypasses the gates entirely.
    static constexpr double kDefaultKSegsRel = 0.02;
    double ksegs_eff_         = 0.0;
    double kSegsGate_         = 0.0;
    // Realization-risk WL spread penalty (see set_planner_param "kWLSpread").
    // < 0 = OFF (kWL scores the plain nominal estimated_wirelength).
    double kWLSpread_         = -1.0;
    // Honest-books mode LEVEL (see set_planner_param "charge_pull_target"):
    //   0 (default) — legacy charging, bit-identical.
    //   1 — charge a pulled segment at its DETERMINISTIC predicted pull
    //       target (window bound tightened by an in-travel ConnSeg::pull_break)
    //       instead of best_band_perp, and rank band_occupants by the
    //       caller-supplied PLACED positions (PR #319).
    //   2 — + the junction prediction (follow-on (a)): the single-rider
    //       anchor clamp on the charged band (a1) and the STRICT dead-band
    //       gate over junction-extended spans (a2, span_hits_dead_band) —
    //       heals comprehensive_demo's b3 keepout strand; per-flow tradeoffs
    //       measured and documented in wishlist-planner.
    int  charge_pull_target_  = 0;
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
    // Issue #518: spread a bus's band charge over the bands its width actually
    // covers instead of dumping it all on the band holding its centre.
    // Opt-in (`set_planner_param band_span_charge N`, env BUDA_BAND_SPAN_CHARGE
    // for sweeps).  Two independent policy axes — WHEN to spread, and HOW to
    // allocate the bus across the bands it covers:
    //   0 = off (the legacy single-band charge; escape hatch)
    //   1 = oversized-only  + proportional
    //   2 = always          + proportional
    //   3 = always          + greedy capacity-aware fill
    //   4 = oversized-only  + greedy capacity-aware fill
    // "oversized-only" spreads just the bus too wide for its own band (#518's
    // overflows-by-construction case) and leaves every bus that already fits
    // on the legacy path.  "greedy fill" saturates the preferred band, then
    // spills to the nearest band with room — NUTS's preferred_fit — instead of
    // diluting uniformly across bands that may already be full.
    //
    // Measured (29-flow corpus, same build, AFTER the two rip-up/footprint
    // accounting fixes — the pre-fix numbers ranked these modes differently
    // and were wrong): mode 1 = 3 better/1 worse at +7.1%, mode 2 = 0/6,
    // mode 3 = 2/3, mode 4 = 2/2, mode 5 = 2/3.  **Mode 1 is the best** —
    // targeting only the genuinely mis-charged (oversized) bus beats
    // spreading every bus, because spreading one that already fits merely
    // lowers its feasibility bar and the planner over-packs.  Still not the
    // default: mode 1 turns one clean flow broken (rnr/mix2 0/0/0 -> 0/16/1).
    // See docs/internal/band_span_charge.md.
    int    band_span_charge_      = 1;   // DEFAULT: oversized-only + proportional
    // Per-(bundle,segment) record of how a committed charge was distributed,
    // so a rip-up can subtract EXACTLY what was added.  Only populated when
    // band_span_charge_ is on; the legacy single-band charge is its own
    // inverse and needs no record.  Cleared whenever the cut state is rebuilt
    // or its usage reset, since the indices and the baseline both move.
    std::map<std::pair<int, int>, std::vector<std::tuple<int, int, double>>> charge_log_;
    // One-shot guard for the above-TOP config-smell warning.
    bool   warned_above_top_ = false;
    // BUDA_REPLAN_PROF instrumentation accumulators (B1): time inside
    // plan_bundle's serial layer_load setup vs the candidate-scoring block,
    // reported cumulatively on each [ReplanProf] line.  Untouched when the
    // env is unset.  Deliberately LAST in the class: inserting them mid-class
    // shifted the hot tuning members' offsets/cache lines and measurably
    // slowed the scoring loops (chip_stack_topdown run_planner +2.6 s).
    long long prof_layerload_us_ = 0;
    long long prof_scoring_us_   = 0;
    long long prof_plan_calls_   = 0;
    long long prof_cands_        = 0;
};

} // namespace buda
