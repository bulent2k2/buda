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
#include "nuts.h"
#include <functional>
#include <map>
#include <set>
#include <utility>
#include <vector>

// Dogleg fallback of abstract NUTS (docs/internal/nuts_dnuts_refactor.md
// Phase E): when a genuinely CYCLIC same-layer vertical constraint survives
// the corner pass, split one trunk on the cycle into two collinear pieces on
// different tracks joined by a perpendicular jog, so the pieces order
// independently against their neighbours and the cycle breaks.  This is
// topology surgery — it mutates the selected Topology and its plan arrays
// (seg_layers / seg_net_pull / seg_slide_* / seg_perp) and re-derives
// seg_conns — so it lives beside the packer, not inside it; run() calls
// run_dogleg_fallback() once, after its first full solve.

namespace buda {

// A fully-specified plan to break a vertical-constraint cycle by doglegging one
// trunk: split it at a column between col1 and col2 so its two pieces straddle
// their respective neighbour trunks (high1 at col1 vs neighbor1, high2 at col2 vs
// neighbor2).  high1 != high2 — that contradiction is why no single track for the
// trunk works.  For a 2-cycle neighbor1 == neighbor2; for a longer cycle they
// differ (each piece orders against a different trunk on the cycle).
struct CycleEdge { std::pair<int,int> from, to; double col; };   // "from below to" at col
struct DoglegPlan {
    std::pair<int,int> split_trunk;
    int    layer;
    double col1; bool high1; std::pair<int,int> neighbor1;
    double col2; bool high2; std::pair<int,int> neighbor2;
    // The full cycle's ordering edges.  Seeding ALL of them (with the split
    // trunk redirected to its covering piece) imposes the complete vertical
    // order — not just the split trunk's two constraints — so the trunks the
    // split does not touch (their mutual edge) are ordered too.
    std::vector<CycleEdge> cycle_edges;
};

// One full placement of a (possibly dogleg-mutated) bundle set — run()'s
// `solve` closure: extract segments, build the context, orientation fixpoint,
// cycle classification, repair/corner safety net.  The dogleg trial loop
// re-invokes it per candidate split.
struct DoglegSolveOut {
    NUTSResult              result;
    std::vector<DoglegPlan> plans;
    // Partner-reach window pruning counts for THIS solve.  They ride the
    // result rather than a caller-side variable because run_dogleg_fallback
    // may adopt an earlier trial or keep the original -- so "the last solve"
    // is not "the accepted solve", and a count read from the wrong one would
    // describe a placement that was thrown away (Codex P2 on #810).
    int n_reach_pruned = 0;
    int n_reach_doomed = 0;
};
using DoglegSolveFn = std::function<DoglegSolveOut(
    const std::vector<BundleWrapper>&,
    const std::map<int, LayerConstraints>&)>;

// Build the same-layer vertical-constraint graph from co-located stub pairs
// and return, for the FIRST directed cycle found, one plan per trunk on the
// cycle (so the caller can split whichever is cheapest).
std::vector<DoglegPlan> detect_dogleg_plans(
    const std::vector<TrackSegment>& segments,
    const std::map<std::pair<int,int>, std::vector<SpanAdjConn>>& rev_conn_map,
    const std::set<std::pair<int,int>>& trunk_set);

// The dogleg fallback loop: try each detected plan (trial copy of the
// bundles, apply the split, seed the full cycle ordering, re-solve via
// `solve`), keep the cheapest strict improvement, iterate.  Mutates `bundles`
// and `out` in place; returns the ids of the bundles whose topology was
// split (for run()'s export of the dogleg_* adoption maps).
std::set<int> run_dogleg_fallback(std::vector<BundleWrapper>& bundles,
                                  DoglegSolveOut& out,
                                  const DoglegSolveFn& solve,
                                  double track_pitch,
                                  const Floorplan& fp);

} // namespace buda
