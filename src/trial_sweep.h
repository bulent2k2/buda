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

// trial_sweep.h — the batched PARALLEL trial sweep (rnr runtime P1,
// docs/internal/rnr_runtime_parallelism.md).
//
// ripup_reroute's deferred stall-certificate sweep evaluates k independent
// ('idx', tidx) moves against the SAME committed baseline; when nothing
// improves, ALL k must be evaluated (the certificate), which makes the sweep
// embarrassingly parallel.  Each worker evaluates one move on PRIVATE deep
// copies (wrapper vector, planner clone) so the shared baseline is read-only:
//
//   pin the move -> incremental replan (CongestionPlanner::replan_bundle on
//   the clone) -> NUTS (fixed bottom-up copies injected, dogleg fallback on
//   the private copy, trial-faithful skip_tighten for stage a) -> for stage b
//   apply the solve's dogleg adoptions to the private wrappers, build the
//   BusSegments, and run the DetailedNUTS copy plan (reference solve +
//   oriented sibling copies + rest solve — the exact port of the session's
//   _run_detailed_nuts merge path, vias off / plain-path abort like a fast
//   trial) -> metric.
//
// METRIC FAITHFULNESS is the load-bearing contract: the returned per-move
// metrics implement the sequential fast-trial semantics exactly (stage-a
// skip_tighten; stage-b vias-off is metric-neutral and the plain-path abort
// only clamps counts already past the bar, so accept/reject decisions are
// identical), and the stage-b primary adds the moved bundle's DISCONNECTED
// term on top of the caller-provided base (the N1 memo's decomposition: other
// bundles' contributions cannot change with this move; a trial dogleg
// adoption's drift is excluded by the dogleg pass's non-severing guarantee,
// #405).  The caller REPLAYS any winning move through the normal sequential
// trial before committing, so a wrong commit is structurally impossible —
// the sweep's metrics only carry the stall certificate and the pick order.
//
// FULL-TRIAL mode (`full_trials`, the refine_selection port): tighten_pulls
// runs in BOTH stages (a WL-fair trial — refine's accept reads realized WL,
// which a tighten-skipped solve would bias against the move), and the
// outcome additionally carries the NUTS interval-violation count and the
// realized abstract WL (sum of placed span lengths, raw double), matching
// the sequential full trial's 4-component refine metric.  The caller
// disables the DNUTS abort bar (abort_unplaced = -1) so the opens count is
// exact for the componentwise parity test.
//
// Threads never touch Python state (the binding releases the GIL): shared
// inputs are const (Floorplan/LayerStack/RoutingGridStack reads;
// Topology::analysis_cache_ is per-copy with an atomically refcounted const
// payload), and std::cout is silenced for the sweep (engines print).

#pragma once

#include <array>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <tuple>
#include <vector>

#include "congestion_planner.h"
#include "layering.h"
#include "nuts.h"
#include "routing_grid.h"
#include "topology.h"

namespace buda {

struct SweepMove {
    int bundle_id = -1;
    int tidx      = -1;
};

// One per move: (primary, secondary, viols, wl, ok).
//   stage a: primary = NUTS overlaps, secondary = 0.
//   stage b: primary = DNUTS unplaced + base_disc + moved-bundle disc,
//            secondary = NUTS overlaps.
//   viols = NUTS interval violations, wl = sum of placed segment span
//   lengths (the raw double — the caller applies its own rounding so the
//   Python-side int(round(...)) reproduces exactly).  Both are consumed by
//   refine_selection's componentwise accept; the ripup sweeps ignore them.
//   ok = false -> this move could not be evaluated here (incremental replan
//   unavailable / bad index); the caller must trial it sequentially.
struct SweepOutcome {
    int    primary   = 0;
    int    secondary = 0;
    int    viols     = 0;
    double wl        = 0.0;
    bool   ok        = false;
};

// Stage-b context; enabled=false = stage a (NUTS metric only).
struct SweepDnutsCtx {
    bool enabled = false;
    const RoutingGridStack* grid = nullptr;
    std::string bit_order = "LO_HI";
    int abort_unplaced = -1;              // plain path only (fast-trial bar)
    // Bottom-up DNUTS copy plan (all empty = plain single-engine path);
    // the session's cached _bottom_up_dnuts_plan, passed through verbatim.
    std::set<int> ref_ids, skip_ids;
    struct CopySpec {
        int ref_bid = -1, sib_bid = -1;
        std::string orient;
        int cw = 0, ch = 0, rx = 0, ry = 0, sx = 0, sy = 0;
    };
    std::vector<CopySpec> copy_specs;
    // (bundle_id, seg_idx) -> horiz of the bottom-up FIXED TrackSegments
    // (the copy loop's orientation source, as in _run_detailed_nuts).
    std::map<std::pair<int, int>, bool> horiz_of;
};

// One fixed-context screen request: score `tidxs` for `bundle_id` against
// the shared baseline (the exact _rr_screen_scores shape — fresh engine,
// skip tighten/doglegs, add_fixed_segments_except, private planner clone).
struct ScreenJob {
    int bundle_id = -1;
    std::vector<int> tidxs;
    bool clear_dogleg = false;
};

// Batched PARALLEL fixed-context screening (the refine/ripup chunk builds'
// dominant sequential cost at chip scale): one worker per job, private
// planner clone + engine per job, shared inputs const.  Returns per job the
// screen rows [(tidx, overlaps, violations)], or nullopt when the
// incremental replan is unavailable (the caller falls back to the
// unscreened order, exactly as the sequential per-bundle call does).
// Deterministic per (baseline, job) — screening order does not matter, so
// batching is decision-identical to the sequential per-bundle calls.
std::vector<std::optional<std::vector<std::array<int, 3>>>> parallel_screen(
    const std::vector<BundleWrapper>& bundles,      // committed baseline
    const std::vector<ScreenJob>&     jobs,
    const CongestionPlanner&          planner,      // cloned per job
    const Floorplan&                  fp,
    const LayerStack&                 layers,
    double                            track_pitch,
    const NUTSResult&                 baseline,     // frozen occupancy
    const std::vector<int>&           extra_x,
    const std::vector<int>&           extra_y,
    int                               n_threads);

std::vector<SweepOutcome> parallel_sweep(
    const std::vector<BundleWrapper>& bundles,      // committed baseline
    const std::vector<SweepMove>&     moves,
    const CongestionPlanner&          planner,      // cloned per worker
    const Floorplan&                  fp,
    const LayerStack&                 layers,
    double                            track_pitch,
    const std::vector<TrackSegment>&  fixed_segs,   // bottom-up copies
    const std::vector<int>&           extra_x,
    const std::vector<int>&           extra_y,
    bool                              stage_b,
    bool                              skip_tighten_stage_a,
    bool                              full_trials,
    const std::set<int>&              dogleg_slot_bids,
    const std::map<int, int>&         base_disc,    // bid -> others' disc
    const std::map<int, int>&         net_counts,   // bid -> its bit count
    const SweepDnutsCtx&              dn,
    int                               n_threads);

} // namespace buda
