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

#include "nuts_dogleg.h"
#include "nuts_geom.h"
#include "conn_topology.h"
#include "verify.h"
#include <algorithm>
#include <cmath>
#include <functional>
#include <iostream>
#include <limits>
#include <map>
#include <set>
#include <vector>

namespace buda {

// Build the same-layer vertical-constraint graph from co-located stub pairs and
// return, for the FIRST directed cycle found, one plan per trunk on the cycle
// (so the caller can split whichever is cheapest).  The graph is built from
// geometry, not from a single placement — a true cycle only ever exposes one
// contradictory column at a time, so the structural view is necessary.
std::vector<DoglegPlan> detect_dogleg_plans(
    const std::vector<TrackSegment>& segments,
    const std::map<std::pair<int,int>, std::vector<SpanAdjConn>>& rev_conn_map,
    const std::set<std::pair<int,int>>& trunk_set)
{
    using Key = std::pair<int,int>;
    // rev_conn_map records connectivity BOTH ways (a trunk's endpoint also
    // "follows" its end stub), so a trunk can appear as a follower of its own
    // stub.  Only genuine stubs (not trunks) define columns where a corner
    // overlap can occur, so skip any follower that is itself a trunk.
    std::map<Key, std::pair<Key,bool>> trunk_of;
    for (const auto& [tkey, conns] : rev_conn_map)
        for (const auto& sc : conns) {
            const Key fkey{sc.src_bid, sc.src_si};
            if (trunk_set.count(fkey)) continue;       // a trunk is not a stub
            trunk_of[fkey] = {tkey, sc.lo_end};
        }
    std::map<Key,int> idx_of;
    for (int i = 0; i < (int)segments.size(); ++i)
        idx_of[{segments[i].bundle_id, segments[i].seg_idx}] = i;
    // The far (block-side) end of a stub, fixed by the floorplan: the trunk whose
    // stub reaches LOWER must take the lower track.
    auto anchored_coord = [](const TrackSegment& s, bool lo_end) {
        return lo_end ? s.span_hi : s.span_lo;
    };

    // Co-locate stubs by their Hanan INTERVAL (the column they are constrained
    // to), not by nominal or placed position: the interval is placement-
    // independent, so it catches two wide (multi-bit) stubs that share a narrow
    // column even when NUTS shifted them to different tracks (and it doesn't
    // vanish once NUTS separates a conflicting pair).  The vertical constraint
    // then follows from which stub reaches farther (anchored end).
    struct Stub { Key key, trunk; double ilo, ihi, center, anchored; int trunk_layer; };
    std::vector<Stub> stubs;
    for (const auto& [k, tinfo] : trunk_of) {
        auto sit = idx_of.find(k);
        auto tit = idx_of.find(tinfo.first);
        if (sit == idx_of.end() || tit == idx_of.end()) continue;
        const TrackSegment& s = segments[sit->second];
        stubs.push_back({k, tinfo.first, s.interval_lo, s.interval_hi,
                         0.5 * (s.interval_lo + s.interval_hi),
                         anchored_coord(s, tinfo.second), segments[tit->second].layer});
    }

    // Directed edge lo_trunk → hi_trunk ("lo below hi") at a column, from every
    // co-located, distinct-bundle stub pair whose trunks share a layer.
    constexpr double kColTol = 2.0;
    std::map<Key, std::map<Key,double>> adj;   // from → {to → column}
    for (size_t a = 0; a < stubs.size(); ++a)
        for (size_t b = a + 1; b < stubs.size(); ++b) {
            if (stubs[a].key.first == stubs[b].key.first) continue;   // same bundle
            if (stubs[a].trunk == stubs[b].trunk) continue;
            if (stubs[a].trunk_layer != stubs[b].trunk_layer) continue;
            // Co-located: their Hanan intervals overlap (same column).
            if (stubs[a].ihi <= stubs[b].ilo || stubs[b].ihi <= stubs[a].ilo) continue;
            const bool a_lower = stubs[a].anchored < stubs[b].anchored;
            Key lo = a_lower ? stubs[a].trunk : stubs[b].trunk;
            Key hi = a_lower ? stubs[b].trunk : stubs[a].trunk;
            adj[lo].emplace(hi, 0.5 * (stubs[a].center + stubs[b].center));  // keep first column
        }

    // DFS for one directed cycle; record it as the node sequence v→…→u (with the
    // back edge u→v closing it).
    std::map<Key,int> color;            // 0 white, 1 gray, 2 black
    std::map<Key,Key> parent;
    std::vector<Key> cyc;
    std::function<bool(const Key&)> dfs = [&](const Key& u) -> bool {
        color[u] = 1;
        for (const auto& [v, col] : adj[u]) {
            (void)col;
            if (color[v] == 1) {            // back edge → cycle v…u
                std::vector<Key> rev;
                for (Key cur = u; cur != v; cur = parent[cur]) rev.push_back(cur);
                cyc.push_back(v);
                for (auto it = rev.rbegin(); it != rev.rend(); ++it) cyc.push_back(*it);
                return true;
            }
            if (color[v] == 0) { parent[v] = u; if (dfs(v)) return true; }
        }
        color[u] = 2;
        return false;
    };
    for (const auto& [n, _] : adj) {
        (void)_;
        if (color[n] == 0 && dfs(n)) break;
    }
    if (cyc.size() < 2) return {};
    const int n = (int)cyc.size();

    // The cycle's directed ordering edges (nodes[i] below nodes[i+1] at its col).
    std::vector<CycleEdge> cycle_edges;
    for (int i = 0; i < n; ++i)
        cycle_edges.push_back({cyc[i], cyc[(i + 1) % n], adj[cyc[i]][cyc[(i + 1) % n]]});

    // One plan per trunk on the cycle: its incoming edge (prev below it → it must
    // be HIGH there) and outgoing edge (it below next → LOW there) give the two
    // contradictory columns and the two neighbour trunks.
    std::vector<DoglegPlan> plans;
    for (int i = 0; i < n; ++i) {
        const Key& ti   = cyc[i];
        const Key& prev = cyc[(i - 1 + n) % n];
        const Key& next = cyc[(i + 1) % n];
        const double col_in  = adj[prev][ti];   // prev → ti
        const double col_out = adj[ti][next];   // ti → next
        if (std::abs(col_in - col_out) <= kColTol) continue;  // can't dogleg at one column
        DoglegPlan p;
        p.split_trunk = ti;
        p.layer = segments[idx_of[ti]].layer;
        p.col1 = col_in;  p.high1 = true;  p.neighbor1 = prev;  // ti above prev at col_in
        p.col2 = col_out; p.high2 = false; p.neighbor2 = next;  // ti below next at col_out
        p.cycle_edges = cycle_edges;
        plans.push_back(p);
    }
    return plans;
}

// Split one trunk of a 2-cycle into two collinear pieces on different tracks,
// joined by a perpendicular jog (the BITRUNK_H shape), so the two pieces become
// INDEPENDENT trunks: each can be ordered against the other bundle's trunk on
// its own, breaking the cycle.  The trunk's two stubs are extended to meet their
// piece and the jog bridges the pieces; seg_layers is extended so the new
// segments keep the trunk/stub layers.  Mutates the selected Topology in place;
// returns the jog's new segment index, or -1 if the split is unsupported.
//
// (col1,high1)/(col2,high2) give, for THIS bundle's trunk, the two conflicting
// columns and whether it must take the higher track there.  high1 != high2 — that
// contradiction is the cycle, and it sets which piece jogs up vs down.
struct DoglegResult {
    bool ok = false;
    int  jog_si = -1;
    int  piece_l_si = -1;     // rewritten trunk segment, covers x <= jog_x
    int  piece_r_si = -1;     // appended segment, covers x >  jog_x
    int  jog_x = 0;           // split column: stubs left of it hang from piece_l, right from piece_r
    bool piece_l_high = false;
};
static DoglegResult apply_dogleg(BundleWrapper& bw, int trunk_si,
                                 double col1, bool high1, double col2, bool high2,
                                 int delta, const std::vector<int>& orig_net_pull,
                                 double orig_slide_lo, double orig_slide_hi)
{
    if (bw.plan.selected_topology_index < 0) return {};
    Topology& topo = bw.input.candidates[bw.plan.selected_topology_index];
    if (trunk_si < 0 || trunk_si >= (int)topo.segments.size()) return {};
    const Segment trunk = topo.segments[trunk_si];
    if (trunk.start.y != trunk.end.y) return {};   // only horizontal trunks for now
    const int y_t  = trunk.start.y;
    const int x_lo = std::min(trunk.start.x, trunk.end.x);
    const int x_hi = std::max(trunk.start.x, trunk.end.x);

    // Order the columns left/right and carry their required high-sides along.
    const bool col1_left = (col1 <= col2);
    const double colL = col1_left ? col1 : col2;
    const double colR = col1_left ? col2 : col1;
    const bool highL = col1_left ? high1 : high2;
    const bool highR = col1_left ? high2 : high1;
    if (x_hi - x_lo < 2) return {};
    int jog_x = (int)std::llround(0.5 * (colL + colR));
    jog_x = std::clamp(jog_x, x_lo + 1, x_hi - 1);

    const int yL = y_t + (highL ? delta : -delta);
    const int yR = y_t + (highR ? delta : -delta);
    // The two pieces must land on DIFFERENT tracks or the split separates nothing.
    // detect_dogleg_plans always emits high1=true/high2=false, so highL != highR and
    // yL != yR; guard anyway so a future change to that convention can't silently
    // collapse the pieces onto one track.
    if (yL == yR) return {};

    int h_layer = trunk.layer_hint;
    if (trunk_si < (int)bw.plan.seg_layers.size() && bw.plan.seg_layers[trunk_si] >= 0)
        h_layer = bw.plan.seg_layers[trunk_si];
    int v_layer = -1;                          // jog rides a perpendicular (stub) layer
    for (int si = 0; si < (int)topo.segments.size(); ++si) {
        const Segment& s = topo.segments[si];
        if (s.start.x == s.end.x) {            // a vertical stub
            v_layer = (si < (int)bw.plan.seg_layers.size() && bw.plan.seg_layers[si] >= 0)
                          ? bw.plan.seg_layers[si] : s.layer_hint;
            break;
        }
    }
    if (v_layer < 0) return {};

    // Rewrite the trunk as the left piece; append the right piece and the jog.
    // The jog is marked so it is exempt from sibling alignment.
    topo.segments[trunk_si] = Segment{ Point{x_lo, yL}, Point{jog_x, yL}, h_layer };
    const int piece_r_idx = (int)topo.segments.size();
    topo.segments.push_back(Segment{ Point{jog_x, yR}, Point{x_hi, yR}, h_layer });
    const int jog_idx = (int)topo.segments.size();
    Segment jog{ Point{jog_x, yL}, Point{jog_x, yR}, v_layer };
    jog.is_jog = true;
    topo.segments.push_back(jog);

    auto set_layer = [&](int idx, int lid) {
        if ((int)bw.plan.seg_layers.size() <= idx) bw.plan.seg_layers.resize(idx + 1, -1);
        bw.plan.seg_layers[idx] = lid;
    };
    set_layer(trunk_si, h_layer);
    set_layer(piece_r_idx, h_layer);
    set_layer(jog_idx, v_layer);

    // Tapered fan-in membership rides the split: both trunk pieces and the
    // jog carry exactly the bits the original trunk carried (seg_bits is
    // index-keyed; the split appends, so existing keys stay valid).
    auto sb = topo.seg_bits.find(trunk_si);
    if (sb != topo.seg_bits.end()) {
        topo.seg_bits[piece_r_idx] = sb->second;
        topo.seg_bits[jog_idx]     = sb->second;
    }

    // Pin net_pull (ConnTopology would recompute the split bundle's pulls wrongly):
    // stubs keep their pre-split value, both sub-trunks inherit the trunk's, the
    // jog is net-zero.  Sliding a sub-trunk toward a face only stretches the jog
    // as much as it shortens the stub (net-zero wirelength), so the trunk pull is
    // the right thing for both pieces.
    const int INT_MIN_ = std::numeric_limits<int>::min();
    const int trunk_pull = (trunk_si < (int)orig_net_pull.size()) ? orig_net_pull[trunk_si] : 0;
    auto& snp = bw.plan.seg_net_pull;
    snp.assign(topo.segments.size(), INT_MIN_);
    for (int i = 0; i < (int)orig_net_pull.size() && i < (int)snp.size(); ++i)
        if (i != trunk_si) snp[i] = orig_net_pull[i];   // stubs preserve their pull
    snp[trunk_si]    = trunk_pull;                       // left piece inherits trunk
    snp[piece_r_idx] = trunk_pull;                       // right piece inherits trunk
    snp[jog_idx]     = 0;                                // jog is net-zero

    // Pin slide windows: each sub-trunk inherits the ORIGINAL trunk's slide
    // range (ConnTopology, seeing only a subset of stubs per piece, would give a
    // narrower one), and the jog is clamped to the trunk's stub extent [x_lo,
    // x_hi] so it cannot slide beyond any stub/busterm the trunk connected to.
    const double kNaN = std::numeric_limits<double>::quiet_NaN();
    auto& slo = bw.plan.seg_slide_lo;
    auto& shi = bw.plan.seg_slide_hi;
    slo.assign(topo.segments.size(), kNaN);
    shi.assign(topo.segments.size(), kNaN);
    slo[trunk_si]    = orig_slide_lo;  shi[trunk_si]    = orig_slide_hi;  // left piece
    slo[piece_r_idx] = orig_slide_lo;  shi[piece_r_idx] = orig_slide_hi;  // right piece
    slo[jog_idx]     = x_lo;           shi[jog_idx]     = x_hi;           // jog footprint

    // Clear the rewritten piece's stale planner band: seg_perp[trunk_si] still
    // names the ORIGINAL trunk's charged band (≈ the old single track), which
    // build_nuts_maps would prefer over the new high/low nominal and undo the
    // split.  INT_MIN ⇒ fall back to the segment's own (jogged) nominal.  The
    // appended pieces have no seg_perp entry, so they already use their nominal.
    if (trunk_si < (int)bw.plan.seg_perp.size())
        bw.plan.seg_perp[trunk_si] = std::numeric_limits<int>::min();

    // Extend each of the trunk's stubs (a vertical segment with an endpoint at
    // the old trunk y) up/down to meet whichever piece now covers its column —
    // left piece (yL) if it sits left of the jog, right piece (yR) otherwise —
    // so the nominal topology stays connected.  (void)colL/colR: ordering is by
    // jog_x, not exact column.
    (void)colL; (void)colR;
    // Only retarget verticals that actually JUNCTIONED the split trunk (audit
    // C2-03).  A raw "any vertical with an endpoint at y_t" test also moved
    // verticals connected to a DIFFERENT horizontal that merely shares row y_t,
    // breaking their real connectivity.  Use the pre-split seg_conns (this runs
    // before the annotate_seg_conns re-derivation below): a stub is a trunk
    // partner iff it appears in trunk_si's conn lists or lists trunk_si in its
    // own — plus the x-in-[x_lo,x_hi] gate the trunk's along-extent implies.
    std::set<int> trunk_partners;
    for (const auto& [key, others] : topo.seg_conns) {
        if (key.first == trunk_si)
            trunk_partners.insert(others.begin(), others.end());
        else if (std::find(others.begin(), others.end(), trunk_si) != others.end())
            trunk_partners.insert(key.first);
    }
    const bool have_conns = !topo.seg_conns.empty();
    for (int si = 0; si < (int)topo.segments.size(); ++si) {
        Segment& s = topo.segments[si];
        if (s.start.x != s.end.x) continue;                 // only vertical stubs
        if (s.is_jog) continue;                             // skip the jog we appended
        // Skip by the is_jog flag, NOT by x==jog_x: an ORIGINAL stub may also sit
        // at the rounded jog column (multicast/odd-grid).  Such a stub still has an
        // endpoint at y_t and must be extended to yL like any left-of-jog stub.
        const int sx = s.start.x;
        // Junction gate: a real trunk stub is a seg_conns partner of trunk_si AND
        // lands within the trunk's along-extent.  When conns are absent (legacy),
        // fall back to the x-extent gate alone rather than moving everything.
        const bool in_extent = (sx >= x_lo && sx <= x_hi);
        const bool partner   = trunk_partners.count(si) != 0;
        if (have_conns ? !(partner && in_extent) : !in_extent) continue;
        const int new_y = (sx <= jog_x) ? yL : yR;
        if (s.start.y == y_t)      s.start.y = new_y;
        else if (s.end.y == y_t)   s.end.y   = new_y;
    }
    // The split invalidated the generation-time seg_conns records (the trunk
    // index is now the left piece; a right piece + jog were appended; stub
    // endpoints moved).  Re-derive them ONCE on the post-split nominal geometry
    // — the deliberate annotation point for this mutation, since ConnTopology no
    // longer infers junctions geometrically (topo-truth Phase 4).  seg_busterms
    // is untouched, exactly as before (the split never lands on a tapped
    // endpoint).
    annotate_seg_conns(topo);
    // piece_l_high reports which piece sits on the higher track, derived from the
    // EMITTED geometry (yL vs yR) — the single source of truth — rather than highL,
    // so the no-swap seed edge can never disagree with the tracks actually placed.
    return DoglegResult{ true, jog_idx, trunk_si, piece_r_idx, jog_x, (yL > yR) };
}

std::set<int> run_dogleg_fallback(std::vector<BundleWrapper>& bundles,
                                  DoglegSolveOut& out,
                                  const DoglegSolveFn& solve,
                                  double track_pitch,
                                  const Floorplan& fp)
{
    // Dogleg fallback: a genuine vertical-constraint cycle survives the corner
    // pass.  Split one trunk on the cycle across two tracks (joined by a jog) so
    // its two pieces become INDEPENDENT trunks that straddle their neighbours —
    // one piece above the neighbour at one column, one below the neighbour at the
    // other — breaking the cycle.  We seed that straddle ordering directly as a
    // same-layer constraint, since the corner pass would only discover one edge
    // at a time and revert.  Each detected cycle yields one plan per trunk on it;
    // try each and keep the cheapest (fewer overlaps, then shorter jog).
    //
    // Gate on a SMALL residual: the dogleg cleans up the few genuinely cyclic
    // overlaps the corner pass can't, not heavy congestion.  When many overlaps
    // remain the placement is still settling (e.g. an intermediate run_nuts a
    // later post_nuts / re-pitch pass will resolve); doglegging there only
    // perturbs a flow that otherwise converges to zero.
    const int kMaxDoglegs  = 8;
    const int kMaxResidual = 4;
    std::set<int> doglegged_bids;   // bundles whose topology the dogleg mutated
    for (int dl = 0; dl < kMaxDoglegs && !out.plans.empty()
                     && out.result.num_overlaps <= kMaxResidual; ++dl) {
        const std::vector<DoglegPlan> plans = out.plans;
        auto find_bw = [&](int bid) -> int {
            for (int i = 0; i < (int)bundles.size(); ++i)
                if (bundles[i].input.original_bundle.id == bid) return i;
            return -1;
        };
        bool applied = false;
        std::vector<BundleWrapper> best_bundles;
        DoglegSolveOut                   best_out;
        double                     best_jog  = std::numeric_limits<double>::max();
        double                     best_span = -1.0;
        int                        best_bid  = -1;
        for (const DoglegPlan& p : plans) {
            int bw_idx = find_bw(p.split_trunk.first);
            if (bw_idx < 0) continue;
            // Don't split a bundle twice: apply_dogleg re-assigns (overwrites) the
            // whole seg_net_pull / seg_slide_* arrays, which would wipe an earlier
            // iteration's pins for this bundle.  A second cycle through it is left to
            // a later iteration on a different bundle, or to the BEST_EFFORT residual.
            if (doglegged_bids.count(p.split_trunk.first)) continue;
            // Trunk geometry: its slide window (interval) must hold two sub-trunks,
            // and a longer span gives the jog more room to slide — so among the
            // cycle's trunks we prefer the one with the longest span (tie-broken on
            // a shorter jog), skipping any whose slide window is too narrow.
            double trunk_w = 1.0, trunk_span = 0.0, trunk_slide = 0.0;
            double trunk_slide_lo = 0.0, trunk_slide_hi = 0.0;
            for (const auto& ts : out.result.segments)
                if (ts.bundle_id == p.split_trunk.first && ts.seg_idx == p.split_trunk.second) {
                    trunk_w        = ts.width;
                    trunk_span     = sp_hi(ts) - sp_lo(ts);   // ordered length
                    trunk_slide_lo = ts.interval_lo;
                    trunk_slide_hi = ts.interval_hi;
                    trunk_slide    = ts.interval_hi - ts.interval_lo;
                }
            if (trunk_slide < 2.0 * trunk_w + 2.0 * track_pitch) continue;  // can't host two pieces
            // Capture this bundle's pre-split net_pull per seg_idx, so apply_dogleg
            // can pin stubs (preserve) and sub-trunks (inherit the trunk).
            std::vector<int> orig_net_pull;
            for (const auto& ts : out.result.segments)
                if (ts.bundle_id == p.split_trunk.first) {
                    if (ts.seg_idx >= (int)orig_net_pull.size())
                        orig_net_pull.resize(ts.seg_idx + 1, 0);
                    orig_net_pull[ts.seg_idx] = ts.net_pull;
                }
            // Seed the jog tall enough that the pieces clear the neighbour trunks
            // between them: separation ≳ bus width + pitch each side.
            const int delta = (int)std::ceil(trunk_w + track_pitch + 2.0);
            std::vector<BundleWrapper> trial = bundles;
            DoglegResult dr = apply_dogleg(trial[bw_idx], p.split_trunk.second,
                                           p.col1, p.high1, p.col2, p.high2, delta,
                                           orig_net_pull, trunk_slide_lo, trunk_slide_hi);
            if (!dr.ok) continue;

            // Reject a split that SEVERS the bundle into electrical islands
            // (issue #399).  apply_dogleg retargets each trunk stub to the piece
            // covering its column, but a stub the split leaves stranded on the
            // old trunk row — which no piece now occupies — becomes a separate
            // island (e.g. bigHalf bundle 67's io_pad_br arm).  Such a topology
            // scores BETTER on the (opens, overlaps) metric the heal optimises
            // (a missing wire = fewer opens, less WL), so without this guard the
            // trial could commit to it.  Run the SAME island check generation's
            // filter_uncovered uses (declared-feedthru islands exempt), on the
            // post-split nominal geometry, and skip a plan that disconnects.
            {
                const BundleWrapper& tb = trial[bw_idx];
                const Topology& split_topo =
                    tb.input.candidates[tb.plan.selected_topology_index];
                ConnTopology ct; ct.build(split_topo, fp);
                bool disconnected = false;
                for (const auto& v : check_topo(ct, split_topo, fp, -1).violations)
                    if (v.kind == ViolationKind::DISCONNECTED
                            && !disconnected_islands_bridged(ct, split_topo, fp)) {
                        disconnected = true; break;
                    }
                if (disconnected) {
                    std::cout << "[Dogleg] skipped split of bundle "
                              << p.split_trunk.first
                              << " — would disconnect the bundle (issue #399).\n";
                    continue;
                }
            }

            // Seed the FULL cycle ordering (preds[X] = segments below X), with
            // the split trunk redirected to whichever piece covers each edge's
            // column (left of the jog → piece_l, right → piece_r).  This imposes
            // the complete vertical order, including the edge between the two
            // trunks the split does not touch, which the corner pass can't.
            const std::pair<int,int> piece_l{p.split_trunk.first, dr.piece_l_si};
            const std::pair<int,int> piece_r{p.split_trunk.first, dr.piece_r_si};
            auto redirect = [&](const std::pair<int,int>& node, double col) {
                if (node != p.split_trunk) return node;
                return (col <= dr.jog_x) ? piece_l : piece_r;
            };
            // The split only breaks the cycle if the trunk's two contradictory edges
            // (col1/col2) land on DIFFERENT pieces.  They are guaranteed > kColTol
            // apart and jog_x is their midpoint, so this normally holds; reject the
            // plan if it doesn't (an N>=3 cycle with both edges on one side of the
            // jog, or a column near jog_x) rather than seed a cycle-preserving order.
            if (redirect(p.split_trunk, p.col1) == redirect(p.split_trunk, p.col2))
                continue;
            std::map<int, LayerConstraints> seed;
            for (const CycleEdge& e : p.cycle_edges) {
                const auto a = redirect(e.from, e.col);   // a below b
                const auto b = redirect(e.to,   e.col);
                if (a != b) seed[p.layer].preds[b].insert(a);
            }
            // Pin the two sub-trunks' relative order so they can never swap: the
            // high piece must sit above the low piece.  They don't overlap in the
            // routing direction (they only touch at the jog), so nothing else
            // enforces this — one explicit edge keeps the jog from inverting.
            seed[p.layer].preds[dr.piece_l_high ? piece_l : piece_r]
                         .insert(dr.piece_l_high ? piece_r : piece_l);
            DoglegSolveOut t = solve(trial, seed);

            // Jog length of the placed jog segment (tie-break; shorter is cheaper).
            double jog_len = std::numeric_limits<double>::max();
            for (const auto& ts : t.result.segments)
                if (ts.bundle_id == p.split_trunk.first && ts.seg_idx == dr.jog_si)
                    jog_len = sp_hi(ts) - sp_lo(ts);   // ordered length
            // Prefer: fewer overlaps, then the LONGER trunk (more jog room), then
            // the shorter jog.
            const int ov = t.result.num_overlaps;
            const bool better =
                !applied ||
                ov <  best_out.result.num_overlaps ||
                (ov == best_out.result.num_overlaps && trunk_span > best_span + 1e-6) ||
                (ov == best_out.result.num_overlaps && std::abs(trunk_span - best_span) <= 1e-6
                                                    && jog_len < best_jog);
            if (better) {
                applied      = true;
                best_bundles = std::move(trial);
                best_out     = std::move(t);
                best_jog     = jog_len;
                best_span    = trunk_span;
                best_bid     = p.split_trunk.first;
            }
        }
        if (applied && best_out.result.num_overlaps < out.result.num_overlaps) {
            std::cout << "[NUTS] dogleg: split a trunk to break a cyclic vertical "
                         "constraint on layer " << plans.front().layer
                      << " (overlaps " << out.result.num_overlaps
                      << " -> " << best_out.result.num_overlaps << ").\n";
            bundles = std::move(best_bundles);
            out     = std::move(best_out);
            if (best_bid >= 0) doglegged_bids.insert(best_bid);
        } else {
            break;   // no dogleg helped — leave the residual to BEST_EFFORT
        }
    }
    return doglegged_bids;
}

} // namespace buda
