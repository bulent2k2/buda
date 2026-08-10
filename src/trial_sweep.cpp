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

#include "trial_sweep.h"

#include <atomic>
#include <cmath>
#include <iostream>
#include <mutex>
#include <thread>

#include "conn_topology.h"
#include "detailed_nuts.h"
#include "verify.h"

namespace buda {

namespace {

// Engines print [NUTS]/[DetailedNUTS] progress via std::cout; the sweep runs
// them from worker threads with the GIL released, so silence cout globally
// for the sweep's duration (matching the sequential trials' redirect-to-sink).
// The redirection swaps the PROCESS-GLOBAL cout buffer, so overlapping sweeps
// from two Python threads would restore each other's dangling NullBuf —
// parallel_sweep serializes itself on g_sweep_mutex for its whole body
// (Codex #502 P1), which also keeps the pool + outcome writes single-sweep.
std::mutex g_sweep_mutex;
struct NullBuf : std::streambuf {
    int overflow(int c) override { return c; }
};
struct CoutSilencer {
    NullBuf nb;
    std::streambuf* old;
    CoutSilencer()  : old(std::cout.rdbuf(&nb)) {}
    ~CoutSilencer() { std::cout.rdbuf(old); }
};

BundleWrapper* find_wrapper(std::vector<BundleWrapper>& b, int bid) {
    for (auto& w : b)
        if (w.input.original_bundle.id == bid) return &w;
    return nullptr;
}

// The trial-side dogleg adoption on the PRIVATE copy — the metric-relevant
// half of the session's _adopt_doglegs (slot bookkeeping is a cross-solve
// concern; one solve happens here): select the split topology with its
// layers + placement overrides so make_bus_segments derives the post-split
// connectivity, exactly as the sequential trial's adopted state does.
void apply_doglegs(std::vector<BundleWrapper>& b, const NUTSResult& nr) {
    for (const auto& [bid, topo] : nr.dogleg_topologies) {
        BundleWrapper* w = find_wrapper(b, bid);
        if (!w) continue;
        w->input.candidates.push_back(topo);
        w->plan.selected_topology_index = (int)w->input.candidates.size() - 1;
        auto it = nr.dogleg_seg_layers.find(bid);
        if (it != nr.dogleg_seg_layers.end()) w->plan.seg_layers = it->second;
        auto np = nr.dogleg_seg_net_pull.find(bid);
        if (np != nr.dogleg_seg_net_pull.end())
            w->plan.seg_net_pull = np->second;
        auto sp = nr.dogleg_seg_perp.find(bid);
        if (sp != nr.dogleg_seg_perp.end()) w->plan.seg_perp = sp->second;
        auto lo = nr.dogleg_seg_slide_lo.find(bid);
        auto hi = nr.dogleg_seg_slide_hi.find(bid);
        if (lo != nr.dogleg_seg_slide_lo.end() &&
            hi != nr.dogleg_seg_slide_hi.end()) {
            w->plan.seg_slide_lo = lo->second;
            w->plan.seg_slide_hi = hi->second;
        }
    }
}

// _rr_topo_disconnected, 1:1 — True iff the topology is
// unbridged-DISCONNECTED (the stage-b metric's severed-bus term).
bool topo_disconnected(const Topology& topo, const Floorplan& fp, int bid) {
    ConnTopology ct;
    ct.build(topo, fp);
    bool disc = false;
    for (const auto& v : check_topo(ct, topo, fp, bid).violations)
        if (v.kind == ViolationKind::DISCONNECTED) { disc = true; break; }
    return disc && !disconnected_islands_bridged(ct, topo, fp);
}

// The _run_detailed_nuts solve path (fast-trial shape: vias OFF; the
// plain path honors the abort bar, the bottom-up merge path ignores it —
// partial r1/r2 results cannot be merged meaningfully).  Returns the
// merged unplaced count, the only DNUTS value the metric reads.
int run_dnuts(const std::vector<BusSegment>& bus, const SweepDnutsCtx& dn) {
    if (dn.ref_ids.empty() && dn.skip_ids.empty()) {
        DetailedNUTSEngine e(*dn.grid);
        return e.run(bus, /*emit_vias=*/false, dn.abort_unplaced)
            .num_unplaced;
    }
    std::vector<BusSegment> ref_segs, rest_segs;
    for (const auto& b : bus) {
        if (dn.ref_ids.count(b.bundle_id))       ref_segs.push_back(b);
        else if (!dn.skip_ids.count(b.bundle_id)) rest_segs.push_back(b);
    }
    // Reference solve on the share-thinned view when declared — the
    // sequential merge path's grid CLONE (see SweepDnutsCtx::ref_grid).
    DetailedNUTSEngine e1(dn.ref_grid ? *dn.ref_grid : *dn.grid);
    DetailedNUTSResult r1 = e1.run(ref_segs, /*emit_vias=*/false);
    std::map<int, int> exp_bits, placed_bits;
    for (const auto& b : ref_segs) exp_bits[b.bundle_id] += b.bit_width;
    for (const auto& ns : r1.net_segments) placed_bits[ns.bundle_id] += 1;
    std::vector<NetSegment> copies;
    int extra_unplaced = 0;
    for (const auto& cs : dn.copy_specs) {
        for (const auto& ns : r1.net_segments)
            if (ns.bundle_id == cs.ref_bid) {
                auto hit = dn.horiz_of.find({cs.ref_bid, ns.seg_idx});
                copies.push_back(transform_net_segment(
                    ns, cs.orient, cs.cw, cs.ch, cs.rx, cs.ry, cs.sx, cs.sy,
                    cs.sib_bid,
                    hit != dn.horiz_of.end() ? hit->second : true));
            }
        auto e = exp_bits.find(cs.ref_bid);
        auto p = placed_bits.find(cs.ref_bid);
        extra_unplaced += (e != exp_bits.end() ? e->second : 0)
                        - (p != placed_bits.end() ? p->second : 0);
    }
    DetailedNUTSEngine e2(*dn.grid);
    std::vector<NetSegment> fixed(r1.net_segments);
    fixed.insert(fixed.end(), copies.begin(), copies.end());
    e2.add_fixed_bits(fixed);
    DetailedNUTSResult r2 = e2.run(rest_segs, /*emit_vias=*/false);
    return r1.num_unplaced + extra_unplaced + r2.num_unplaced;
}

// Realized abstract WL — the refine metric's wl_now(), 1:1: the raw double
// sum of placed span lengths in segment order (the caller rounds, so the
// Python int(round(...)) sees the identical double).
double placed_wl(const NUTSResult& nr) {
    double wl = 0.0;
    for (const auto& ts : nr.segments)
        if (ts.placed) wl += std::abs(ts.span_hi - ts.span_lo);
    return wl;
}

SweepOutcome eval_move(const std::vector<BundleWrapper>& baseline,
                       const SweepMove& m,
                       const CongestionPlanner& planner_proto,
                       const Floorplan& fp, const LayerStack& layers,
                       double pitch,
                       const std::vector<TrackSegment>& fixed_segs,
                       const std::vector<int>& extra_x,
                       const std::vector<int>& extra_y,
                       bool stage_b, bool skip_tighten_a, bool full_trials,
                       const std::set<int>& dogleg_slot_bids,
                       const std::map<int, int>& base_disc,
                       const std::map<int, int>& net_counts,
                       const SweepDnutsCtx& dn) {
    SweepOutcome out;
    std::vector<BundleWrapper> b = baseline;          // private deep copy
    BundleWrapper* w = find_wrapper(b, m.bundle_id);
    if (!w || m.tidx < 0 || m.tidx >= (int)w->input.candidates.size())
        return out;                                    // ok=false
    // Pin the move — the _rr_trial mutation, incl. clearing the dogleg
    // per-segment overrides when moving a bundle off its adopted split.
    w->plan.selected_topology_index = m.tidx;
    w->input.topology_pinned = true;
    if (dogleg_slot_bids.count(m.bundle_id)) {
        w->plan.seg_net_pull.clear();
        w->plan.seg_slide_lo.clear();
        w->plan.seg_slide_hi.clear();
    }
    // Incremental replan on a private planner clone (cuts state is mutated
    // by the recharge; the clone shares only const refs with the session's).
    CongestionPlanner pl = planner_proto;
    pl.set_plan_threads(1);   // the move fan-out owns the cores (no nested pools)
    auto asn = pl.replan_bundle(b, m.bundle_id);
    if (!asn) return out;                              // sequential fallback
    w->plan.selected_topology_index = asn->topo_index;
    w->input.assigned_v_layer = asn->v_layer_id;
    w->input.assigned_h_layer = asn->h_layer_id;
    w->plan.seg_layers = asn->seg_layers;
    w->plan.seg_perp   = asn->seg_perp;
    NUTSEngine nuts(fp, layers);
    nuts.set_track_pitch(pitch);
    nuts.set_layer_threads(1);   // the move fan-out owns the cores (P3 nests off)
    // Full trials (refine): tighten runs in both stages — a WL-fair solve.
    nuts.set_skip_tighten(full_trials ? false : (!stage_b && skip_tighten_a));
    if (!fixed_segs.empty()) nuts.add_fixed_segments(fixed_segs);
    nuts.set_extra_grid_points(extra_x, extra_y);
    NUTSResult nr = nuts.run(b);
    out.viols = nr.num_violations;
    out.wl    = placed_wl(nr);
    if (!stage_b) {
        out.primary = nr.num_overlaps;
        out.ok = true;
        return out;
    }
    apply_doglegs(b, nr);
    std::vector<BusSegment> bus = make_bus_segments(b, nr, fp, dn.bit_order);
    int unplaced = run_dnuts(bus, dn);
    // The moved bundle's DISCONNECTED term on its FINAL selected candidate
    // (a dogleg adoption above may have replaced the trial candidate —
    // exactly what the sequential metric evaluates).
    int disc_bits = 0;
    const int fsel = w->plan.selected_topology_index;
    if (fsel >= 0 && fsel < (int)w->input.candidates.size() &&
        topo_disconnected(w->input.candidates[fsel], fp, m.bundle_id)) {
        auto nc = net_counts.find(m.bundle_id);
        disc_bits = nc != net_counts.end() ? nc->second : 0;
    }
    auto bd = base_disc.find(m.bundle_id);
    out.primary   = unplaced + (bd != base_disc.end() ? bd->second : 0)
                  + disc_bits;
    out.secondary = nr.num_overlaps;
    out.ok = true;
    return out;
}

} // namespace

std::vector<std::optional<std::vector<std::array<int, 3>>>> parallel_screen(
    const std::vector<BundleWrapper>& bundles,
    const std::vector<ScreenJob>&     jobs,
    const CongestionPlanner&          planner,
    const Floorplan&                  fp,
    const LayerStack&                 layers,
    double                            track_pitch,
    const NUTSResult&                 baseline,
    const std::vector<int>&           extra_x,
    const std::vector<int>&           extra_y,
    int                               n_threads) {
    std::vector<std::optional<std::vector<std::array<int, 3>>>>
        out(jobs.size());
    if (jobs.empty()) return out;
    std::lock_guard<std::mutex> sweep_lock(g_sweep_mutex);
    CoutSilencer silence;
    unsigned hw = std::thread::hardware_concurrency();
    int nt = n_threads > 0 ? n_threads : (hw ? (int)hw : 2);
    nt = std::min<int>(nt, (int)jobs.size());
    std::atomic<size_t> next{0};
    auto worker = [&]() {
        for (size_t i = next.fetch_add(1); i < jobs.size();
             i = next.fetch_add(1)) {
            try {
                const ScreenJob& j = jobs[i];
                // The exact _rr_screen_scores engine shape, one per job:
                // fresh engine, screen flags, frozen occupancy minus the
                // target, planner grids as extras — plus a PRIVATE planner
                // clone (replan_candidates leaves the planner unchanged,
                // but a clone makes cross-job isolation structural).
                NUTSEngine eng(fp, layers);
                eng.set_track_pitch(track_pitch);
                eng.set_skip_tighten(true);
                eng.set_skip_doglegs(true);
                eng.set_layer_threads(1);   // job fan-out owns the cores
                eng.set_extra_grid_points(extra_x, extra_y);
                eng.add_fixed_segments_except(baseline, j.bundle_id);
                CongestionPlanner pl = planner;
                pl.set_plan_threads(1);
                out[i] = eng.screen_candidates(bundles, j.bundle_id,
                                               j.tidxs, pl, j.clear_dogleg);
            } catch (...) {
                out[i] = std::nullopt;    // sequential/unscreened fallback
            }
        }
    };
    if (nt <= 1) {
        worker();
    } else {
        std::vector<std::thread> pool;
        pool.reserve(nt);
        // Same exception-safe construction as parallel_sweep (Codex #502).
        try {
            for (int t = 0; t < nt; ++t) pool.emplace_back(worker);
        } catch (const std::system_error&) {
        }
        if (pool.empty()) worker();
        for (auto& th : pool) th.join();
    }
    return out;
}

std::vector<SweepOutcome> parallel_sweep(
    const std::vector<BundleWrapper>& bundles,
    const std::vector<SweepMove>&     moves,
    const CongestionPlanner&          planner,
    const Floorplan&                  fp,
    const LayerStack&                 layers,
    double                            track_pitch,
    const std::vector<TrackSegment>&  fixed_segs,
    const std::vector<int>&           extra_x,
    const std::vector<int>&           extra_y,
    bool                              stage_b,
    bool                              skip_tighten_stage_a,
    bool                              full_trials,
    const std::set<int>&              dogleg_slot_bids,
    const std::map<int, int>&         base_disc,
    const std::map<int, int>&         net_counts,
    const SweepDnutsCtx&              dn,
    int                               n_threads) {
    std::vector<SweepOutcome> out(moves.size());
    if (moves.empty()) return out;
    std::lock_guard<std::mutex> sweep_lock(g_sweep_mutex);
    CoutSilencer silence;
    unsigned hw = std::thread::hardware_concurrency();
    int nt = n_threads > 0 ? n_threads : (hw ? (int)hw : 2);
    nt = std::min<int>(nt, (int)moves.size());
    std::atomic<size_t> next{0};
    auto worker = [&]() {
        for (size_t i = next.fetch_add(1); i < moves.size();
             i = next.fetch_add(1)) {
            try {
                out[i] = eval_move(bundles, moves[i], planner, fp, layers,
                                   track_pitch, fixed_segs, extra_x, extra_y,
                                   stage_b, skip_tighten_stage_a, full_trials,
                                   dogleg_slot_bids, base_disc, net_counts,
                                   dn);
            } catch (...) {
                out[i] = SweepOutcome{};   // ok=false -> sequential fallback
            }
        }
    };
    if (nt <= 1) {
        worker();
    } else {
        std::vector<std::thread> pool;
        pool.reserve(nt);
        // Exception-safe construction (Codex #502 P1): a thread/resource
        // limit can make emplace_back throw after earlier workers started —
        // unwinding would destroy joinable threads (std::terminate).  Keep
        // whatever workers DID start (the shared atomic index means fewer
        // workers still drain every move) and join them on every path; if
        // none could start, run the worker inline (sequential fallback).
        try {
            for (int t = 0; t < nt; ++t) pool.emplace_back(worker);
        } catch (const std::system_error&) {
        }
        if (pool.empty()) worker();
        for (auto& th : pool) th.join();
    }
    return out;
}

} // namespace buda
