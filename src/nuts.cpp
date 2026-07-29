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

#include "nuts.h"
#include "nuts_geom.h"
#include "nuts_dogleg.h"
#include "conn_topology.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <limits>
#include <map>
#include <mutex>
#include <numeric>
#include <set>
#include <thread>

namespace buda {

// File-scope so it can appear in lambda default arguments (a local variable
// may not — odr-using one in a default argument is ill-formed on conforming
// compilers).
static constexpr double kInf = std::numeric_limits<double>::infinity();

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Merge extra grid points (sorted) into an existing sorted grid.
static void merge_grid(std::vector<int>& grid, const std::vector<int>& extra) {
    for (int val : extra) {
        auto it = std::lower_bound(grid.begin(), grid.end(), val);
        if (it == grid.end() || *it != val) grid.insert(it, val);
    }
}

// Return the index i such that grid[i] <= v <= grid[i+1], or -1 if out of range.

// ---------------------------------------------------------------------------
// Shared map builders
// ---------------------------------------------------------------------------

static void build_nuts_maps(
    const std::vector<BundleWrapper>& bundles,
    const Floorplan& floorplan,
    std::map<std::pair<int,int>, double>&                         pull_map,
    std::map<std::pair<int,int>, std::pair<double,double>>&       slide_map,
    std::set<std::pair<int,int>>&                                trunk_set,
    std::set<std::pair<int,int>>&                                busterm_set,
    std::map<std::pair<int,int>, std::vector<SpanAdjConn>>&       rev_conn_map,
    std::map<std::pair<int,int>, int>&                            net_pull_map,
    AlignMap&                                                     align_map,
    std::map<std::pair<int,int>, std::vector<double>>&            busterm_face_map,
    std::map<std::pair<int,int>, std::vector<std::pair<double,double>>>& passthru_map)
{
    std::set<std::pair<int,int>> jog_set;   // dogleg jogs: excluded from alignment
    // Pass 1 — nominal perpendicular position from the topology.
    for (const auto& bw : bundles) {
        if (bw.input.candidates.empty() ||
            bw.plan.selected_topology_index < 0 ||
            bw.plan.selected_topology_index >= (int)bw.input.candidates.size())
            continue;   // out-of-range index is UB (audit C1-02)
        const Topology& topo = bw.input.candidates[bw.plan.selected_topology_index];
        int bid = bw.input.original_bundle.id;
        for (int si = 0; si < (int)topo.segments.size(); ++si) {
            const Segment& seg = topo.segments[si];
            bool is_h  = (seg.start.y == seg.end.y);
            double nom = is_h ? static_cast<double>(seg.start.y)
                               : static_cast<double>(seg.start.x);
            pull_map[{bid, si}] = nom;
            if (seg.is_jog) jog_set.insert({bid, si});
        }
    }

    // Pass 2 — connectivity-based override.
    for (const auto& bw : bundles) {
        if (bw.input.candidates.empty() ||
            bw.plan.selected_topology_index < 0 ||
            bw.plan.selected_topology_index >= (int)bw.input.candidates.size())
            continue;   // out-of-range index is UB (audit C1-02)
        const Topology& topo = bw.input.candidates[bw.plan.selected_topology_index];
        int bid = bw.input.original_bundle.id;

        ConnTopology ct;
        ct.build(topo, floorplan);
        const auto& conn_segs = ct.segs();

        // Blocks with a real tap somewhere in this topology: their coverage does
        // not depend on any crossing, so they need no pass-through anchor (the
        // coverage checks skip them for the same reason).
        std::set<std::string> tapped_blocks;
        for (const auto& cs : conn_segs)
            for (const auto& c : cs.conns)
                if (c.kind == SegConn::BUSTERM) tapped_blocks.insert(c.block_name);

        // The dogleg's per-segment overrides (seg_net_pull / seg_slide_*) are
        // indexed by the doglegged topology's segments.  A later run_planner may
        // select a DIFFERENT topology for this bundle without refreshing them, so
        // honor an override array only when its length still matches the current
        // topology — otherwise it would be applied to unrelated segments.
        const bool np_ok    = (bw.plan.seg_net_pull.size() == conn_segs.size());
        const bool slide_ok = (bw.plan.seg_slide_lo.size() == conn_segs.size() &&
                               bw.plan.seg_slide_hi.size() == conn_segs.size());
        // seg_perp is planner-managed and normally matches the topology; guard it
        // too so a stale dogleg seg_perp can never be applied to a different one.
        const bool perp_ok  = (bw.plan.seg_perp.size() == conn_segs.size());

        for (int si = 0; si < (int)conn_segs.size(); ++si) {
            const ConnSeg& cs = conn_segs[si];
            auto key = std::make_pair(bid, si);

            // A dogleg pins its sub-trunks' / jog's slide range (ConnTopology
            // would recompute a narrower range on the split topology); honor it.
            if (slide_ok && !std::isnan(bw.plan.seg_slide_lo[si]))
                slide_map[key] = { bw.plan.seg_slide_lo[si], bw.plan.seg_slide_hi[si] };
            else
                slide_map[key] = { static_cast<double>(cs.perp_lo),
                                   static_cast<double>(cs.perp_hi) };

            int n_seg = 0, n_bt = 0;
            for (const auto& c : cs.conns) {
                if (c.kind == SegConn::SEG) ++n_seg;
                else {
                    ++n_bt;
                    // Record the block-face along-coordinate so span adjustment can
                    // keep this segment reaching its tap after track slides.
                    busterm_face_map[key].push_back(static_cast<double>(c.face_coord));
                }
            }

            if (n_seg >= 2 && n_bt == 0) trunk_set.insert(key);
            if (n_bt >= 1)               busterm_set.insert(key);

            for (const auto& conn : cs.conns) {
                if (conn.kind != SegConn::SEG) continue;
                auto t_key = std::make_pair(bid, conn.seg_idx);
                double mid = 0.5 * (cs.along_lo + cs.along_hi);
                bool lo_end   = (conn.at_pos <= mid);
                rev_conn_map[t_key].push_back({ bid, si, lo_end, conn.is_endpoint });
            }

            // Use cs.net_pull (computed by ConnTopology) to set the preferred
            // placement coordinate.  net_pull > 0 → slide toward perp_hi,
            // net_pull < 0 → slide toward perp_lo.  A per-segment override
            // (bw.plan.seg_net_pull, set by the dogleg pass) pins the value when
            // ConnTopology would recompute it wrongly on the split topology.
            int eff_net_pull = cs.net_pull;
            if (np_ok && bw.plan.seg_net_pull[si] != INT_MIN)
                eff_net_pull = bw.plan.seg_net_pull[si];
            net_pull_map[key] = eff_net_pull;
            if (eff_net_pull != 0) {
                constexpr double kSentinel = 5e8;
                // Pull toward the slide-window bound.  When the dogleg pinned the
                // slide range (slide_map was set from seg_slide_* just above), pull
                // toward THAT bound — the original trunk's extent — not the per-piece
                // bound ConnTopology recomputes on the split, which narrows it and
                // would tug the piece back off the trunk's exported position.
                const bool slide_pinned =
                    (slide_ok && !std::isnan(bw.plan.seg_slide_lo[si]));
                const double hi = slide_pinned ? slide_map[key].second
                                               : static_cast<double>(cs.perp_hi);
                const double lo = slide_pinned ? slide_map[key].first
                                               : static_cast<double>(cs.perp_lo);
                double preferred;
                if (eff_net_pull > 0)
                    preferred = (hi < kSentinel) ? hi : pull_map[key];  // fallback
                else
                    preferred = (lo > -kSentinel) ? lo : pull_map[key]; // fallback
                // Breakpoint clamp: stop the pull at its wirelength saturation
                // point (ConnSeg::pull_break) instead of the window edge — on a
                // wide interior window the edge overshoots the point where the
                // pull's gain ends and stretches the coupled trunk between the
                // connectors (b44's TRUNK_H+MST: the seg1 connector's window
                // reaches 940 past its breakpoint; the overshoot was the whole
                // +1000/bit realization excess).  Only when the breakpoint lies
                // WITHIN the travel from nominal to the bound (a tightening,
                // never a new direction), and never for a dogleg-pinned slide
                // (its bound IS the exported trunk position, the intent).  A
                // dogleg net-pull PIN keeps the raw bound too — the pin's
                // PRESENCE is the signal (apply_dogleg pins every original
                // stub because the split topology's votes are not
                // authoritative), so any non-sentinel pin suppresses the
                // clamp even when its value happens to equal the recomputed
                // pull: equal aggregates do not mean the vote composition or
                // the breakpoint survived the split (Codex #315).
                const bool np_overridden =
                    (np_ok && bw.plan.seg_net_pull[si] != INT_MIN);
                if (!slide_pinned && !np_overridden && cs.pull_break != INT_MIN) {
                    const double bp = static_cast<double>(cs.pull_break);
                    if (eff_net_pull > 0 && bp > cs.perp_pos && bp < preferred)
                        preferred = bp;
                    else if (eff_net_pull < 0 && bp < cs.perp_pos && bp > preferred)
                        preferred = bp;
                }
                pull_map[key] = preferred;
            } else if (n_bt == 0 && perp_ok &&
                       bw.plan.seg_perp[si] != INT_MIN) {
                // Planner band preference: the slide-aware congestion lookup
                // charged this segment to a specific Hanan band (seg_perp =
                // centre of that band's usable window).  Prefer it over the
                // raw nominal so buses land in the bands whose capacity the
                // planner actually reserved — otherwise several buses pack
                // into one band by centre preference while the charged bands
                // sit empty.  Only for segments free of face semantics:
                // busterm stubs and net_pull-driven segments keep their
                // nominal/bound pulls (those encode face containment and
                // min-stub length).
                pull_map[key] = static_cast<double>(bw.plan.seg_perp[si]);
            }
        }

        // Pass-through coverage anchors (issue #496).  A block this bundle
        // covers only by a segment CROSSING it has no conn record, so the span
        // passes are free to move an end past it — and when the block lies
        // beyond the outermost junction, tighten_spans_to_reach's contraction
        // does not clip the crossing, it deletes it.
        //
        // Anchored only where the crossing is the block's SOLE coverage: one
        // untapped block, exactly one segment nominally crossing it.  A block
        // that several segments cross stays covered when any one of them
        // retracts, so anchoring all of them would keep phantom span for
        // nothing — measured: b44's pinned 6-seg TRUNK_H+MST realizes 4552
        // instead of 3510 (+30%) if every crossing is anchored, because its
        // extra crossings are redundant (Codex P2 on #499).  Scoping to sole
        // coverage is decided on NOMINAL geometry, so it is deterministic and
        // order-independent — unlike a post-placement "is it still covered?"
        // test, which would depend on the order spans are adjusted in.
        {
            std::map<std::string, std::vector<std::pair<int, std::pair<double,double>>>>
                crossings;                       // block -> [(seg, interval)]
            for (int si = 0; si < (int)conn_segs.size(); ++si) {
                const ConnSeg& cs = conn_segs[si];
                const double s_lo = std::min((double)cs.along_lo, (double)cs.along_hi);
                const double s_hi = std::max((double)cs.along_lo, (double)cs.along_hi);
                for (const auto& bname : topo.connected_block_names) {
                    if (tapped_blocks.count(bname)) continue;
                    auto rects = floorplan.get_block_rects(bname);
                    if (rects.empty()) rects.push_back(floorplan.get_block_bounds(bname));
                    for (const Rect& r : rects) {
                        const double p_lo = cs.horiz ? (double)r.y1 : (double)r.x1;
                        const double p_hi = cs.horiz ? (double)r.y2 : (double)r.x2;
                        const double a_lo = cs.horiz ? (double)r.x1 : (double)r.y1;
                        const double a_hi = cs.horiz ? (double)r.x2 : (double)r.y2;
                        if (cs.perp_pos < p_lo || cs.perp_pos > p_hi) continue;
                        if (s_lo > a_hi || s_hi < a_lo) continue;     // no crossing
                        crossings[bname].push_back(
                            {si, {std::max(s_lo, a_lo), std::min(s_hi, a_hi)}});
                        break;                    // one crossing per (seg, block)
                    }
                }
            }
            for (const auto& [bname, list] : crossings) {
                if (list.size() != 1) continue;   // redundantly covered — no anchor
                passthru_map[{bid, list[0].first}].push_back(list[0].second);
            }
        }
    }

    // Pass 3 — alignment siblings: segments of one bundle that connect to the
    // same perpendicular segment (e.g. a multicast trunk's stubs on opposite
    // sides).  rev_conn_map[T] lists the segs whose span follows T; any two of
    // them sharing T are siblings.
    for (const auto& [t_key, followers] : rev_conn_map) {
        // The two sub-trunks both follow the jog (T = the jog), so they appear as
        // siblings here.  Aligning them would place one piece on the other's track,
        // collapsing the split — and on a re-solve the seed's no-swap ordering is
        // gone, so this is the only thing standing between them.  Skip alignment
        // whenever the shared perpendicular T is itself a jog.
        if (jog_set.count(t_key)) continue;
        for (size_t i = 0; i < followers.size(); ++i)
            for (size_t j = i + 1; j < followers.size(); ++j) {
                if (followers[i].src_bid != followers[j].src_bid) continue;
                auto ka = std::make_pair(followers[i].src_bid, followers[i].src_si);
                auto kb = std::make_pair(followers[j].src_bid, followers[j].src_si);
                // A dogleg jog must NOT be aligned onto a sibling stub's track:
                // that would pull it off its own column and collapse the trunk
                // split it implements.  Only jogs are exempted; all genuine
                // sibling alignment (multicast/multi-trunk topologies) is kept.
                if (jog_set.count(ka) || jog_set.count(kb)) continue;
                align_map[ka].push_back(kb);
                align_map[kb].push_back(ka);
            }
    }
}

static void relax_boundary_intervals(
    std::vector<TrackSegment>& segments,
    const std::map<std::pair<int,int>, double>& pull_map,
    const std::map<std::pair<int,int>, int>& net_pull_map,
    const std::set<std::pair<int,int>>& busterm_set,
    int only_layer = -1)
{
    // When the preferred position equals an interval boundary, preferred_fit
    // clamps the bus center to interval_hi - half_width (or interval_lo +
    // half_width), so the bus edge — not its center — lands at the boundary.
    // This makes trunks overshoot block faces and pull-targets miss their mark.
    //
    // Fix: extend the interval by one full width outward so the bus CENTER can
    // land exactly at the preferred coordinate.
    //
    // Exception: segments with a direct busterm connection (endpoint stubs at a
    // block face). For these, preferred_fit already places the bus inner edge
    // AT the block face, which is correct — bits must lie within the block face
    // extent. Relaxing would shift the center to the face boundary, pushing half
    // the bus outside the block's perpendicular extent and causing DNUTS opens.
    //
    // Exception: net_pull-driven segments. Their preferred coordinate IS the
    // hard slide bound (perp_lo/perp_hi), which encodes min-stub-length for the
    // centerline. Edge-at-bound keeps every bit within [bound - width, bound],
    // so every per-bit stub keeps >= min_stub length. Center-at-bound would
    // spread bits half a width past the bound — past the block faces the stubs
    // descend from — making them unreachable (DNUTS opens, e.g. the U_VHV
    // detour trunk in flow/hbundles/08_cross_level.buda bundle 6).
    for (auto& ts : segments) {
        if (only_layer >= 0 && ts.layer != only_layer) continue;
        auto key = std::make_pair(ts.bundle_id, ts.seg_idx);
        if (busterm_set.count(key)) continue;   // block-face stubs: leave interval alone
        auto npit = net_pull_map.find(key);
        if (npit != net_pull_map.end() && npit->second != 0) continue;
        auto it = pull_map.find(key);
        if (it == pull_map.end()) continue;
        const double preferred = it->second;
        if (std::abs(preferred - ts.interval_hi) < 0.5) {
            ts.interval_hi += ts.width;
        } else if (std::abs(preferred - ts.interval_lo) < 0.5) {
            ts.interval_lo -= ts.width;
        }
    }
}

static void apply_interval_constraints(
    std::vector<TrackSegment>& segments,
    const std::map<std::pair<int,int>, std::pair<double,double>>& slide_map,
    const std::set<std::pair<int,int>>&                           trunk_set,
    const std::map<std::pair<int,int>, int>&                      net_pull_map,
    int only_layer = -1)
{
    constexpr double kSentinel = 5e8;
    for (auto& ts : segments) {
        if (only_layer >= 0 && ts.layer != only_layer) continue;
        auto key = std::make_pair(ts.bundle_id, ts.seg_idx);

        auto sit = slide_map.find(key);
        if (sit != slide_map.end()) {
            auto [slo, shi] = sit->second;
            if (slo > -kSentinel) ts.interval_lo = std::max(ts.interval_lo, slo);
            if (shi <  kSentinel) ts.interval_hi = std::min(ts.interval_hi, shi);
        }

        // Propagate net_pull into TrackSegment for use in solve_layer.
        auto npit = net_pull_map.find(key);
        if (npit != net_pull_map.end()) ts.net_pull = npit->second;

        if (trunk_set.count(key)) {
            double span   = ts.interval_hi - ts.interval_lo;
            double margin = 0.1 * span;
            // Apply margin only on the side opposite the pull direction so the
            // preferred edge stays reachable.  Symmetric margin for balanced trunks.
            int np = ts.net_pull;
            double new_lo = ts.interval_lo + (np >= 0 ? margin : 0.0);
            double new_hi = ts.interval_hi - (np <= 0 ? margin : 0.0);
            if (new_hi - new_lo >= ts.width) {
                ts.interval_lo = new_lo;
                ts.interval_hi = new_hi;
            }
        }
    }
}

static void do_span_adjustments(
    const std::vector<TrackSegment*>&                               layer_segs,
    const std::map<std::pair<int,int>, std::vector<SpanAdjConn>>&   rev_conn_map,
    std::map<std::pair<int,int>, TrackSegment*>&                     ts_ptr_map,
    bool                                                             only_unplaced = false)
{
    struct AdjReq { double center; bool lo_end; bool is_endpoint; bool from_jog; };
    std::map<std::pair<int,int>, std::vector<AdjReq>> adj_map;

    for (const TrackSegment* ts : layer_segs) {
        if (!ts->placed) continue;
        auto it = rev_conn_map.find({ts->bundle_id, ts->seg_idx});
        if (it == rev_conn_map.end()) continue;

        for (const auto& sc : it->second) {
            auto jt = ts_ptr_map.find({sc.src_bid, sc.src_si});
            if (jt == ts_ptr_map.end()) continue;
            TrackSegment* other = jt->second;
            if (only_unplaced && other->placed) continue;
            adj_map[{sc.src_bid, sc.src_si}].push_back(
                {ts->track_position, sc.lo_end, sc.is_endpoint, ts->is_jog});
        }
    }

    for (auto& [key, reqs] : adj_map) {
        auto jt = ts_ptr_map.find(key);
        if (jt == ts_ptr_map.end()) continue;
        TrackSegment* other = jt->second;

        bool has_lo = false, has_hi = false;
        double min_lo = std::numeric_limits<double>::infinity();
        double max_hi = -std::numeric_limits<double>::infinity();
        bool all_lo_endpoints = true, all_hi_endpoints = true;
        // A dogleg piece's inner end is DEFINED by the jog it meets: when the jog
        // slides, that end must track it (contract, not just extend).  The piece's
        // nominal span was set at the pre-slide split column, so extend-only would
        // leave it overstretched past a jog that slid inward.  Treat an endpoint
        // connection to a jog as authoritative for that end even when an interior
        // tap (e.g. a multicast driver stub on the split column) shares it.
        bool lo_jog = false, hi_jog = false;

        for (const auto& req : reqs) {
            if (req.lo_end) {
                has_lo = true;
                min_lo = std::min(min_lo, req.center);
                if (!req.is_endpoint) all_lo_endpoints = false;
                if (req.from_jog && req.is_endpoint) lo_jog = true;
            } else {
                has_hi = true;
                max_hi = std::max(max_hi, req.center);
                if (!req.is_endpoint) all_hi_endpoints = false;
                if (req.from_jog && req.is_endpoint) hi_jog = true;
            }
        }

        if (has_lo) {
            if (all_lo_endpoints || lo_jog) other->span_lo = min_lo;
            else other->span_lo = std::min(other->span_lo, min_lo);
        }
        if (has_hi) {
            if (all_hi_endpoints || hi_jog) other->span_hi = max_hi;
            else other->span_hi = std::max(other->span_hi, max_hi);
        }

        // Coverage guarantee — a maintained INVARIANT, not a per-call repair
        // (seg_junction_coplacement.md Part B): a segment must physically reach
        // EVERY placed junction partner, wherever placement put it.  Two ways
        // the old reqs-only loop broke this:
        //   1. The lo_end/hi_end split above is keyed on NOMINAL geometry, so a
        //      tap that placement moved across the trunk had a stale label and
        //      the trunk stopped short.
        //   2. `reqs` only carries partners from THIS call's layer slice, so a
        //      per-layer call could SET a trunk end from one tap while
        //      contracting past a cross-layer tap not in the slice (the
        //      tc3a_flat bundle-48 machine-dependent open: an H tap on another
        //      layer landed below the V trunk's endpoint-set span_lo and was
        //      never re-covered).
        // Fix: consult ALL of `other`'s junction partners via rev_conn_map (it
        // records both directions) against the global ts_ptr_map, extending the
        // geometric extent to include every placed partner's track.  Mapping
        // back through the current span ordering preserves endpoint identity
        // (possibly span_lo > span_hi); extend-only, so a legitimate jog
        // contraction is never undone.  Because this stays inside
        // do_span_adjustments, every revert-guarded pass (tighten_pulls,
        // repair_overlaps, resolve_corner_overlaps) re-establishes it after
        // moving any track — a partner's flip can open nothing.
        auto cover = [&](double center) {
            span_cover(other->span_lo, other->span_hi, center);
        };
        auto pit = rev_conn_map.find(key);
        if (pit != rev_conn_map.end()) {
            for (const auto& sc : pit->second) {
                auto pj = ts_ptr_map.find({sc.src_bid, sc.src_si});
                if (pj == ts_ptr_map.end() || !pj->second->placed) continue;
                cover(pj->second->track_position);
            }
        } else {
            for (const auto& req : reqs) cover(req.center);   // no partner map: old behavior
        }

        // BUSTERM face coverage: a segment that taps a block face must always
        // reach that face.  rev_conn_map carries only SEG connectivity, so the
        // span-setting above (which follows a connected stub's placed track) can
        // overwrite a face-tapped end — a silent open at the block face (e.g.
        // big2 bus_077 / blk_12, where the M6 trunk's hi end is pulled onto a
        // vertical stub's band ~5934 and drops the x=6100 tap).  Extend (never
        // contract) the span to include every recorded BUSTERM face_coord, exactly
        // like the SEG coverage guarantee above and in the SAME place: keeping it
        // inside do_span_adjustments (not a final post-pass) means repair_overlaps
        // / resolve_corner_overlaps / tighten_pulls all run on the re-anchored
        // spans and repair any overlap the extension materialises, instead of a
        // late pass leaving a shorted layout.  Applied per mutated `other` so
        // per-layer calls also cover cross-layer followers not in layer_segs.
        for (double fc : other->busterm_faces)
            span_cover(other->span_lo, other->span_hi, fc);

        // NOT extended here for pass-through crossings, deliberately.  The
        // symmetric hook (span_cover each passthru_spans interval, exactly as
        // above) was implemented and MEASURED: it costs abstract WL +0.37% and
        // runtime +18% corpus-wide (bigHalf 6.7s -> 53.6s, its span inflation
        // giving the healers more to clear) and fixes NOTHING the contraction
        // guard in tighten_spans_to_reach does not already fix — same 1
        // better / 0 worse endpoint either way.  The loss mechanism on the
        // corpus is contraction, not this span-setting: a segment with
        // junctions gets its ends set to the junction envelope here, and a
        // crossing beyond that envelope is deleted later, by the tighten.
        //
        // Residual case, knowingly left: if span-setting alone ever moves an
        // end past a crossing, the tighten cannot restore it (it only
        // contracts) and the block opens at check_dnuts — the same symptom
        // issue #496 describes, diagnosable the same way.  Re-add this hook if
        // that is ever observed; do not add it speculatively.

        // span_lo/span_hi intentionally keep NOMINAL endpoint identity (span_lo
        // is the lo_end coordinate, span_hi the hi_end) even when placement
        // leaves span_lo > span_hi: corner/dogleg logic derives the fixed anchor
        // as `lo_end ? span_hi : span_lo` and relies on that pairing.  Consumers
        // that need an ordered extent (the connectivity checks) take min/max
        // locally rather than mutating the stored bounds here.
    }
}

// Re-adjust every placed segment's span to its partners' current tracks — the
// universal "settle" step after any track mutation (each guarded move, a group
// re-solve, a layer rerun).
static void settle_spans(std::vector<TrackSegment>& segments, NutsContext& ctx)
{
    std::vector<TrackSegment*> all_placed;
    for (auto& ts : segments) if (ts.placed) all_placed.push_back(&ts);
    do_span_adjustments(all_placed, ctx.rev_conn_map, ctx.ts_ptr_map);
}

// Post-convergence span RE-TIGHTEN (Option B, relay_jog_phantom_span_2026-07.md).
// settle_spans' coverage guarantee is EXTEND-ONLY, so once a segment's span was
// stretched to cover a junction partner sitting at a TRANSIENT far position (a
// connector with an unbounded perp window that abstract NUTS clamped to the whole
// design extent and briefly flung across it), that stretch is never undone even
// after the partner settles back — a permanent phantom span tail.  After the solve
// has fully converged, re-derive each placed segment's span as the TIGHT min/max
// of the coordinates it must actually reach: its placed junction partners' tracks
// (rev_conn_map[ts] — the same set the coverage guarantee covers) plus its own
// busterm faces, and every block it covers by CROSSING.  This can only SHRINK a
// span to the exact envelope of what it connects to, so it never opens a
// junction; it removes the phantom overlap/WL the inflated span reports.  The
// reach set must include everything the span is responsible for: this comment
// used to assert that a pass-through block "sits BETWEEN the real endpoints, so
// it stays covered", which holds only while the block is inside the junction
// envelope — outside it, the contraction deleted the crossing (issue #496).  Endpoint identity is preserved (span_lo stays the
// lo-end coordinate even when placement leaves span_lo > span_hi).  MUST be the
// last span mutation before metrics — settle_spans/repair_overlaps are
// extend-only and would re-inflate.
static void tighten_spans_to_reach(std::vector<TrackSegment>& segments,
                                   NutsContext& ctx)
{
    std::map<std::pair<int,int>, const TrackSegment*> ptr;
    for (const auto& ts : segments)
        if (ts.placed) ptr[{ts.bundle_id, ts.seg_idx}] = &ts;

    for (auto& ts : segments) {
        if (!ts.placed) continue;
        double rmin =  std::numeric_limits<double>::infinity();
        double rmax = -std::numeric_limits<double>::infinity();
        auto it = ctx.rev_conn_map.find({ts.bundle_id, ts.seg_idx});
        if (it != ctx.rev_conn_map.end())
            for (const auto& sc : it->second) {
                auto pj = ptr.find({sc.src_bid, sc.src_si});
                if (pj == ptr.end()) continue;
                rmin = std::min(rmin, pj->second->track_position);
                rmax = std::max(rmax, pj->second->track_position);
            }
        for (double fc : ts.busterm_faces) {
            rmin = std::min(rmin, fc);
            rmax = std::max(rmax, fc);
        }
        // ...and every block this segment covers by CROSSING it.  The comment
        // above used to claim "a pass-through block sits BETWEEN the real
        // endpoints, so it stays covered"; that is false whenever the block
        // lies beyond the outermost junction, and then this contraction does
        // not clip the crossing — it deletes it, opening the block with no
        // warning at any stage before check_dnuts (issue #496).
        for (const auto& pt : ts.passthru_spans) {
            rmin = std::min(rmin, pt.first);
            rmax = std::max(rmax, pt.second);
        }
        if (!std::isfinite(rmin) || !std::isfinite(rmax)) continue;  // nothing to reach
        const double cur_lo = std::min(ts.span_lo, ts.span_hi);
        const double cur_hi = std::max(ts.span_lo, ts.span_hi);
        const double nlo = std::max(cur_lo, rmin);   // contract only
        const double nhi = std::min(cur_hi, rmax);
        if (nlo > nhi) continue;                      // reach set outside span: leave as-is
        if (ts.span_lo <= ts.span_hi) { ts.span_lo = nlo; ts.span_hi = nhi; }
        else                          { ts.span_lo = nhi; ts.span_hi = nlo; }
    }
}

// Band-level repack of spread-fit overlap clusters — defined after LayerSolver
// (whose repack machinery it drives); see docs/internal/nuts_band_repack.md.
// seed_cons: active corner constraints (see repair_overlaps), may be null.
static int repack_overlap_clusters(const NUTSEngine& eng, double track_pitch,
                                   std::vector<TrackSegment>& segments,
                                   NutsContext& ctx,
                                   const std::map<int, LayerConstraints>* seed_cons);

void NUTSEngine::repair_overlaps(std::vector<TrackSegment>& segments,
                                 NutsContext& ctx,
                                 const std::map<int, LayerConstraints>* seed_cons) const
{
    const auto& pull_map     = ctx.pull_map;
    const auto& net_pull_map = ctx.net_pull_map;
    const auto& align_map    = ctx.align_map;
    auto&       ts_ptr_map   = ctx.ts_ptr_map;
    auto initial = find_overlaps(segments);
    if (initial.empty()) return;

    // Snapshot for the non-regression guard: a move re-adjusts follower
    // spans, which can surface new overlaps elsewhere.
    PlacementSnapshot snapshot;
    snapshot.take(segments);

    const auto kozs = solver_keepouts();
    auto pull_of = [&](const TrackSegment& ts) {
        auto it = net_pull_map.find({ts.bundle_id, ts.seg_idx});
        return it == net_pull_map.end() ? 0 : it->second;
    };

    // Per-move snapshot: a move re-adjusts follower spans globally, so a
    // locally good move can surface overlaps elsewhere.  Each move is
    // accepted only if the global overlap count strictly drops; otherwise
    // just that move is rolled back — earlier accepted moves are kept.
    int moved = 0;
    PlacementSnapshot pre_move;
    for (int iter = 0; iter < 8; ++iter) {
        auto pairs = find_overlaps(segments);
        if (pairs.empty()) break;
        bool progress = false;
        for (auto [i, j] : pairs) {
            TrackSegment& a = segments[i];
            TrackSegment& b = segments[j];
            if (!segs_overlap(a, b)) continue;   // fixed by an earlier move

            // Victim: only an unpulled segment may move — a pull target is
            // semantic (block face / min-stub bound) and its edge-at-bound
            // placement is what keeps every bit within the face; relocating
            // it can push bits past the face even inside its interval.
            // Both pulled → the pair is not repairable here.
            TrackSegment* victim;
            int pa = std::abs(pull_of(a)), pb = std::abs(pull_of(b));
            if (pa == 0 && pb == 0) {
                double sa = (a.interval_hi - a.interval_lo) - a.width;
                double sb = (b.interval_hi - b.interval_lo) - b.width;
                victim = (sa > sb) ? &a : &b;   // larger slack moves
            } else if (pa == 0) {
                victim = &a;
            } else if (pb == 0) {
                victim = &b;
            } else {
                continue;
            }

            std::vector<std::pair<double,double>> occ;
            keepout_occupied(kozs, victim, occ);
            for (const auto& o : segments) {
                if (&o == victim) continue;
                occupancy_from(*victim, o, occ);
            }
            std::sort(occ.begin(), occ.end());

            const double c_lo = victim->interval_lo + victim->width / 2.0;
            const double c_hi = victim->interval_hi - victim->width / 2.0;
            auto vkey = std::make_pair(victim->bundle_id, victim->seg_idx);
            double preferred = std::numeric_limits<double>::quiet_NaN();
            auto ait = align_map.find(vkey);
            if (ait != align_map.end()) {
                for (const auto& sk : ait->second) {
                    auto lit = ts_ptr_map.find(sk);
                    if (lit == ts_ptr_map.end() || !lit->second->placed) continue;
                    if (lit->second->layer != victim->layer) continue;
                    double p = lit->second->track_position;
                    if (p >= c_lo && p <= c_hi) { preferred = p; break; }
                }
            }
            if (std::isnan(preferred)) {
                auto it = pull_map.find(vkey);
                preferred = (it != pull_map.end())
                            ? it->second
                            : (victim->interval_lo + victim->interval_hi) / 2.0;
            }
            preferred = std::clamp(preferred, c_lo, c_hi);

            double pos = preferred_fit(victim->interval_lo, victim->interval_hi,
                                       victim->width, occ, preferred);
            if (std::isnan(pos) || pos == victim->track_position) continue;

            const size_t before = find_overlaps(segments).size();
            pre_move.take(segments);
            victim->track_position = pos;
            // Settle followers of the moved segment (and theirs, cheaply).
            settle_spans(segments, ctx);
            if (find_overlaps(segments).size() >= before) {
                pre_move.restore(segments);   // this move made things no better
                continue;
            }
            ++moved;
            progress = true;
        }
        // Band-level cluster round (nuts_band_repack.md): whatever the
        // per-pair loop could not separate is, by construction, a set of
        // mutually-wedged segments — single-victim moves fail because every
        // gap a victim could take is blocked by another cluster member.
        // Re-place each residual overlap cluster AS A SET through
        // LayerSolver's repack machinery (each cluster individually guarded:
        // no global rise, strict in-cluster drop); collateral overlaps a
        // committed repack surfaces elsewhere are cleaned by the next
        // single-victim sweep of this loop.
        if (!progress) {
            const int m = repack_overlap_clusters(*this, track_pitch_,
                                                  segments, ctx, seed_cons);
            if (m > 0) { moved += m; progress = true; }
        }
        if (!progress) break;
    }

    auto remaining = find_overlaps(segments);
    if (remaining.size() >= initial.size()) {
        // No strict improvement: restore the pre-repair state.
        snapshot.restore(segments);
        return;
    }
    if (moved > 0)
        std::cout << "[NUTS] overlap repair: moved " << moved
                  << " segment(s), overlaps " << initial.size()
                  << " -> " << remaining.size() << ".\n";
}

// Resolve each segment's pull_target — the coordinate it wants to reach, clamped
// so the bus CENTRE keeps the whole width inside the hard interval.  Uses the
// RESOLVED pull_map value, not the raw interval edge: build_nuts_maps
// deliberately falls back to the nominal position when a net-pull direction had
// only the sentinel (unbounded) slide bound (apply_interval_constraints leaves
// the interval at the floorplan boundary there), so the raw edge would drag such
// a segment toward the chip edge.  NaN when the segment has no pull or its
// interval is narrower than its width.  Set once after the maps are built so
// tighten_pulls (and Python / the visualizer) read the code's true objective.
static void set_pull_targets(
    std::vector<TrackSegment>& segments,
    const std::map<std::pair<int,int>, double>& pull_map,
    const std::map<std::pair<int,int>, int>&    net_pull_map)
{
    for (auto& ts : segments) {
        auto npit = net_pull_map.find({ts.bundle_id, ts.seg_idx});
        const int np = (npit != net_pull_map.end()) ? npit->second : ts.net_pull;
        const double half = ts.width / 2.0;
        const double c_lo = ts.interval_lo + half, c_hi = ts.interval_hi - half;
        if (np == 0 || c_lo > c_hi) {
            ts.pull_target = std::numeric_limits<double>::quiet_NaN();
            continue;
        }
        auto it = pull_map.find({ts.bundle_id, ts.seg_idx});
        const double p = (it != pull_map.end())
                         ? it->second
                         : (np > 0 ? ts.interval_hi : ts.interval_lo);
        ts.pull_target = std::clamp(p, c_lo, c_hi);
    }
}

// Build the per-solve NutsContext and apply the interval prep to `segments`,
// exactly in the order run()/rerun_layer() did inline: build the maps, stamp
// each segment's busterm faces, clamp intervals to slide windows (+ trunk
// margin), relax boundary intervals, resolve pull targets, then index the
// segments.  only_layer restricts the two interval passes to that layer
// (rerun_layer's single-layer contract); -1 = all layers.  prep=false skips
// the three prep passes entirely (busterm faces and the index are still
// built): the warm rerun assembles a union of ALREADY-prepped segments —
// the target's by its scratch screen run, the context's by the run that
// produced the baseline — and apply_interval_constraints' trunk-margin
// shrink is NOT idempotent, so re-prepping would tighten every interval a
// second time.
static NutsContext build_context(const std::vector<BundleWrapper>& bundles,
                                 const Floorplan& floorplan,
                                 std::vector<TrackSegment>& segments,
                                 int only_layer = -1,
                                 bool prep = true)
{
    NutsContext ctx;
    build_nuts_maps(bundles, floorplan, ctx.pull_map, ctx.slide_map,
                    ctx.trunk_set, ctx.busterm_set, ctx.rev_conn_map,
                    ctx.net_pull_map, ctx.align_map, ctx.busterm_face_map,
                    ctx.passthru_map);
    for (auto& ts : segments) {
        auto bf = ctx.busterm_face_map.find({ts.bundle_id, ts.seg_idx});
        if (bf != ctx.busterm_face_map.end()) ts.busterm_faces = bf->second;
        auto pt = ctx.passthru_map.find({ts.bundle_id, ts.seg_idx});
        if (pt != ctx.passthru_map.end()) ts.passthru_spans = pt->second;
    }
    if (prep) {
        apply_interval_constraints(segments, ctx.slide_map, ctx.trunk_set,
                                   ctx.net_pull_map, only_layer);
        relax_boundary_intervals(segments, ctx.pull_map, ctx.net_pull_map,
                                 ctx.busterm_set, only_layer);
        set_pull_targets(segments, ctx.pull_map, ctx.net_pull_map);
    }
    for (auto& ts : segments)
        ctx.ts_ptr_map[{ts.bundle_id, ts.seg_idx}] = &ts;
    return ctx;
}

void NUTSEngine::tighten_pulls(std::vector<TrackSegment>& segments,
                               NutsContext& ctx,
                               int only_layer) const
{
    const auto& net_pull_map = ctx.net_pull_map;
    const auto& align_map    = ctx.align_map;
    const auto kozs = solver_keepouts();

    PlacementSnapshot pre_move;
    // True routed length = sum of every placed segment's span extent.  Sliding a
    // segment toward its pull contracts the follower spans connected to it, so
    // this is the quantity the pass minimises.
    auto total_wl = [&]() {
        double w = 0;
        for (const auto& ts : segments) if (ts.placed) w += sp_hi(ts) - sp_lo(ts);
        return w;
    };
    // net_pull (the slide direction that shortens connected stubs) for a segment,
    // preferring the planner/dogleg-managed value, falling back to the stored one.
    auto pull_of = [&](const TrackSegment& ts) {
        auto it = net_pull_map.find({ts.bundle_id, ts.seg_idx});
        return it == net_pull_map.end() ? ts.net_pull : it->second;
    };
    // Same-layer occupancy from other bundles whose span overlaps ts, plus
    // keepouts — exactly what a track for ts must avoid.  (Same-bundle siblings
    // are exempt, so aligned group members never block each other.)
    auto build_occ = [&](const TrackSegment& ts,
                         std::vector<std::pair<double,double>>& occ) {
        keepout_occupied(kozs, &ts, occ);
        for (const auto& o : segments) {
            if (&o == &ts) continue;
            occupancy_from(ts, o, occ);
        }
        std::sort(occ.begin(), occ.end());
    };

    // --- Build alignment groups once: connected components over align_map,
    // restricted to placed, same-layer (only_layer) segments.  Aligned siblings
    // hang off one trunk; the trunk's length is bound by min(member positions),
    // so moving one sibling alone leaves the others pinning the junction and the
    // move is wirelength-neutral (the deadlock this pass exists to break).  They
    // must move TOGETHER.  Pull-free siblings are included (dragged along) so
    // they can't re-pin the trunk.  Segments with no alignment edge form
    // singleton groups → identical to the original per-segment behaviour. ---
    std::map<std::pair<int,int>, int> idx_of;
    for (int i = 0; i < (int)segments.size(); ++i)
        idx_of[{segments[i].bundle_id, segments[i].seg_idx}] = i;
    auto eligible = [&](int i) {
        const auto& ts = segments[i];
        return ts.placed && (only_layer < 0 || ts.layer == only_layer);
    };
    std::vector<int> parent(segments.size());
    for (int i = 0; i < (int)parent.size(); ++i) parent[i] = i;
    auto find = [&](int x) {
        while (parent[x] != x) { parent[x] = parent[parent[x]]; x = parent[x]; }
        return x;
    };
    for (const auto& [k, sibs] : align_map) {
        auto it = idx_of.find(k);
        if (it == idx_of.end() || !eligible(it->second)) continue;
        for (const auto& sk : sibs) {
            auto jt = idx_of.find(sk);
            if (jt == idx_of.end() || !eligible(jt->second)) continue;
            if (segments[it->second].layer != segments[jt->second].layer) continue;
            parent[find(it->second)] = find(jt->second);
        }
    }
    std::map<int, std::vector<int>> groups;
    for (int i = 0; i < (int)segments.size(); ++i)
        if (eligible(i)) groups[find(i)].push_back(i);

    // Clamp a preferred coordinate into a segment's valid CENTRE range so
    // preferred_fit aims inside the interval — it does not add the far edge as a
    // fallback candidate, so an out-of-range target (e.g. a pull-free sibling's
    // group target above its own interval) would otherwise be rejected and the
    // segment bottom-edged.  Returns the input unchanged for a degenerate range.
    auto clamp_center = [](double p, double interval_lo, double interval_hi, double width) {
        const double c_lo = interval_lo + width / 2.0, c_hi = interval_hi - width / 2.0;
        return (c_lo <= c_hi) ? std::clamp(p, c_lo, c_hi) : p;
    };

    // Single-segment tightening move toward this segment's own pull_target.  Used
    // for singleton groups and as the per-member fallback for sign-clash groups,
    // preserving the original per-segment behaviour.
    auto try_single = [&](int idx) -> bool {
        TrackSegment& ts = segments[idx];
        if (std::isnan(ts.pull_target)) return false;
        const double pb  = ts.pull_target;                  // already centre-clamped
        const double cur = std::abs(ts.track_position - pb);
        if (cur <= 0.5) return false;
        std::vector<std::pair<double,double>> occ; build_occ(ts, occ);
        const double pos = preferred_fit(ts.interval_lo, ts.interval_hi, ts.width, occ, pb);
        if (std::isnan(pos) || std::abs(pos - pb) + 0.5 >= cur) return false;
        const size_t ov_before = find_overlaps(segments).size();
        const int    vi_before = count_violations(segments);
        const double wl_before = total_wl();
        pre_move.take(segments);
        ts.track_position = pos;
        settle_spans(segments, ctx);
        if (find_overlaps(segments).size() > ov_before ||
            count_violations(segments)    > vi_before ||
            total_wl() + 0.5 >= wl_before) { pre_move.restore(segments); return false; }
        return true;
    };

    // Attempt one ATOMIC move of an aligned group toward its pull.  Each member
    // goes to its own best toward pull (split) UNLESS a common shared track
    // reaches the same trunk bound (collapse → fewer tracks, better
    // detailed-NUTS sharing).  Kept only when it adds no overlap/violation and
    // strictly shortens total wirelength; reverted as a unit otherwise.
    auto try_group = [&](const std::vector<int>& members) -> bool {
        if (members.size() == 1) return try_single(members[0]);
        // Pull direction + a target, from the PULLED members; detect a sign clash.
        int dir = 0; bool clash = false; double group_target = 0; double best_dev = -1;
        for (int i : members) {
            const TrackSegment& ts = segments[i];
            if (std::isnan(ts.pull_target)) continue;
            const int np = pull_of(ts);
            const int s = (np > 0) ? 1 : (np < 0 ? -1 : 0);
            if (s == 0) continue;
            if (dir == 0) dir = s;
            else if (dir != s) clash = true;        // opposite pulls in one group
            const double dev = std::abs(ts.track_position - ts.pull_target);
            if (dev > best_dev) { best_dev = dev; group_target = ts.pull_target; }
        }
        // Opposite pulls can't co-move; fall back to safe per-segment moves so
        // each member still tightens individually (the outer loop only schedules
        // group roots, so without this they would never be visited).
        if (clash) {
            bool any = false;
            for (int i : members) any |= try_single(i);
            return any;
        }
        if (dir == 0 || best_dev <= 0.5) return false;

        // Individual best toward the target for each member (pull-free members are
        // dragged toward the same target so they can't re-pin the trunk).  Clamp
        // the target into each member's own centre range first.
        std::vector<double> Bm(members.size());
        for (size_t mi = 0; mi < members.size(); ++mi) {
            const TrackSegment& ts = segments[members[mi]];
            const double tgt = clamp_center(
                std::isnan(ts.pull_target) ? group_target : ts.pull_target,
                ts.interval_lo, ts.interval_hi, ts.width);
            std::vector<std::pair<double,double>> occ; build_occ(ts, occ);
            const double b = preferred_fit(ts.interval_lo, ts.interval_hi, ts.width, occ, tgt);
            Bm[mi] = std::isnan(b) ? ts.track_position : b;   // can't move → stays
        }
        // Trunk bound a split achieves = the laggard (least toward pull).
        double M = Bm[0];
        for (double b : Bm) M = (dir > 0) ? std::min(M, b) : std::max(M, b);

        // Common shared track over the tightest common interval / max width.
        double common_lo = -kInf, common_hi = kInf, maxw = 0;
        for (int i : members) {
            common_lo = std::max(common_lo, segments[i].interval_lo);
            common_hi = std::min(common_hi, segments[i].interval_hi);
            maxw      = std::max(maxw, segments[i].width);
        }
        std::vector<std::pair<double,double>> union_occ;
        for (int i : members) build_occ(segments[i], union_occ);
        std::sort(union_occ.begin(), union_occ.end());
        const double C = preferred_fit(common_lo, common_hi, maxw, union_occ,
                                       clamp_center(group_target, common_lo, common_hi, maxw));

        // Collapse to the shared track when it reaches the split's trunk bound
        // (no WL sacrificed); otherwise split, each member at its own best.
        const double tol = 1.0;
        const bool collapse = !std::isnan(C) &&
            (dir > 0 ? C >= M - tol : C <= M + tol);
        std::vector<double> tgtpos(members.size());
        for (size_t mi = 0; mi < members.size(); ++mi)
            tgtpos[mi] = collapse ? C : Bm[mi];

        bool changes = false;
        for (size_t mi = 0; mi < members.size(); ++mi)
            if (std::abs(tgtpos[mi] - segments[members[mi]].track_position) > 0.5)
                { changes = true; break; }
        if (!changes) return false;

        // Atomic commit under the global guards.
        const size_t ov_before = find_overlaps(segments).size();
        const int    vi_before = count_violations(segments);
        const double wl_before = total_wl();
        pre_move.take(segments);
        for (size_t mi = 0; mi < members.size(); ++mi)
            segments[members[mi]].track_position = tgtpos[mi];
        settle_spans(segments, ctx);
        if (find_overlaps(segments).size() > ov_before ||
            count_violations(segments)    > vi_before ||
            total_wl() + 0.5 >= wl_before) {
            pre_move.restore(segments);
            return false;
        }
        return true;
    };

    int moved = 0;
    const int kMaxIters = 6;
    for (int iter = 0; iter < kMaxIters; ++iter) {
        // Close the biggest pull gaps first: rank groups by their largest
        // pulled-member deviation from its pull target.
        std::vector<std::pair<double,int>> order;   // (deviation, group root)
        for (const auto& [root, members] : groups) {
            double gdev = -1;
            for (int i : members) {
                const auto& ts = segments[i];
                if (std::isnan(ts.pull_target)) continue;
                gdev = std::max(gdev, std::abs(ts.track_position - ts.pull_target));
            }
            if (gdev > 0.5) order.emplace_back(gdev, root);
        }
        if (order.empty()) break;
        std::sort(order.begin(), order.end(),
                  [](const auto& a, const auto& b) { return a.first > b.first; });

        bool progress = false;
        for (const auto& [gdev, root] : order)
            if (try_group(groups[root])) { ++moved; progress = true; }
        if (!progress) break;
    }
    if (moved > 0)
        std::cout << "[NUTS] wirelength tighten: pulled " << moved
                  << " group(s) toward their pull bound.\n";
}

void NUTSEngine::resolve_corner_overlaps(std::vector<TrackSegment>& segments,
                                         NutsContext& ctx) const
{
    const auto& rev_conn_map = ctx.rev_conn_map;
    using Key = std::pair<int,int>;
    // A corner overlap is geometric (two stubs hanging from distinct trunks); it
    // need not stem from a span change — aligned trunks can make stubs touch
    // end-to-end with no stretching at all — so the pass is not gated on a
    // "stretched" set, only on the presence of overlaps.
    if (find_overlaps(segments).empty()) return;

    // Follower → (trunk it follows, lo_end): rev_conn_map[T] lists segments whose
    // span follows T, so each such follower's trunk is T.
    std::map<Key, std::pair<Key,bool>> trunk_of;
    for (const auto& [tkey, conns] : rev_conn_map)
        for (const auto& sc : conns)
            trunk_of[{sc.src_bid, sc.src_si}] = {tkey, sc.lo_end};

    std::map<Key,int> idx_of;
    for (int i = 0; i < (int)segments.size(); ++i)
        idx_of[{segments[i].bundle_id, segments[i].seg_idx}] = i;

    // The trunk meets the follower at its lo_end; the OTHER end is anchored.
    auto anchored_coord = [](const TrackSegment& s, bool lo_end) {
        return lo_end ? s.span_hi : s.span_lo;
    };

    std::map<int, LayerConstraints> by_layer_cons;  // trunk layer → phase-0 constraints
    // Same-trunk-layer: hi must sit above lo (relative ordering edge).
    auto add_edge = [&](const Key& lo, const Key& hi) -> bool {
        auto it = idx_of.find(hi);
        if (it == idx_of.end()) return false;
        return by_layer_cons[segments[it->second].layer].preds[hi].insert(lo).second;
    };
    // Cross-trunk-layer: intersect a fixed track-bound [lo, hi] onto a trunk's
    // own layer; returns true if it tightened the bound.
    auto add_bound = [&](const Key& k, double lo, double hi) -> bool {
        auto it = idx_of.find(k);
        if (it == idx_of.end()) return false;
        auto& b = by_layer_cons[segments[it->second].layer].bounds;
        auto bit = b.find(k);
        if (bit == b.end()) { b[k] = {lo, hi}; return true; }
        double nlo = std::max(bit->second.first, lo);
        double nhi = std::min(bit->second.second, hi);
        bool changed = (nlo != bit->second.first || nhi != bit->second.second);
        bit->second = {nlo, nhi};
        return changed;
    };

    for (int iter = 0; iter < 6; ++iter) {
        auto pairs = find_overlaps(segments);
        if (pairs.empty()) break;

        // Snapshot the constraint set so a reverted iteration's new edges/bounds
        // are dropped (only committed constraints persist to detailed NUTS).
        auto cons_before = by_layer_cons;
        bool new_edge = false;
        std::set<int> dirty_layers;
        for (auto [i, j] : pairs) {
            Key kp{segments[i].bundle_id, segments[i].seg_idx};
            Key kq{segments[j].bundle_id, segments[j].seg_idx};
            auto tp = trunk_of.find(kp), tq = trunk_of.find(kq);
            if (tp == trunk_of.end() || tq == trunk_of.end()) continue;
            Key trunk_p = tp->second.first, trunk_q = tq->second.first;
            if (trunk_p == trunk_q) continue;
            auto pit = idx_of.find(trunk_p), qit = idx_of.find(trunk_q);
            if (pit == idx_of.end() || qit == idx_of.end()) continue;
            double ap = anchored_coord(segments[i], tp->second.second);
            double aq = anchored_coord(segments[j], tq->second.second);
            // Lower anchored end ⇒ its trunk takes the lower track.
            Key lo_trunk = (ap < aq) ? trunk_p : trunk_q;
            Key hi_trunk = (ap < aq) ? trunk_q : trunk_p;
            const TrackSegment& loT = segments[idx_of[lo_trunk]];
            const TrackSegment& hiT = segments[idx_of[hi_trunk]];

            if (loT.layer == hiT.layer) {
                // Same trunk layer: order them on that layer (bottom-edge pack).
                if (add_edge(lo_trunk, hi_trunk)) {
                    new_edge = true;
                    dirty_layers.insert(hiT.layer);
                }
            } else {
                // Cross trunk layer: the trunks can't be track-ordered against
                // each other (different metals), so nudge each within its own
                // layer to opposite sides of a split S, separated by g.  Abstract
                // gap g = 1 unit just sets the side; the real spacing is the
                // detailed-NUTS signal tracks (future work).
                const double g    = 1.0;
                const double lmin = loT.interval_lo + loT.width / 2.0;  // lo trunk's lowest
                const double hmax = hiT.interval_hi - hiT.width / 2.0;  // hi trunk's highest
                const double Slo  = lmin + g / 2.0;
                const double Shi  = hmax - g / 2.0;
                if (Slo > Shi) continue;   // intervals can't admit lo below hi
                const double S = std::clamp(
                    (loT.track_position + hiT.track_position) / 2.0, Slo, Shi);
                bool c1 = add_bound(lo_trunk, -kInf, S - g / 2.0);
                bool c2 = add_bound(hi_trunk, S + g / 2.0, kInf);
                if (c1 || c2) {
                    new_edge = true;
                    dirty_layers.insert(loT.layer);
                    dirty_layers.insert(hiT.layer);
                }
            }
        }
        if (!new_edge) break;     // no resolvable corner overlap (or a cycle)

        // Snapshot for the stop-&-reverse guard.
        PlacementSnapshot snap(/*with_placed=*/true);
        snap.take(segments);
        const size_t before      = pairs.size();
        const int    before_viol = count_violations(segments);

        // Re-solve each affected trunk layer under the accumulated constraints.
        for (int layer : dirty_layers) {
            std::vector<TrackSegment*> layer_segs;
            for (auto& ts : segments)
                if (ts.layer == layer) {
                    ts.track_position = std::numeric_limits<double>::quiet_NaN();
                    ts.placed = false;
                    layer_segs.push_back(&ts);
                }
            solve_layer(layer_segs, ctx, by_layer_cons[layer]);
        }
        // Re-fit connected spans to the new trunk positions and repair residue
        // (the cluster repack sees the ACTIVE constraints so it cannot move a
        // just-constrained trunk — they are not yet on the track bounds here).
        settle_spans(segments, ctx);
        repair_overlaps(segments, ctx, &by_layer_cons);

        const size_t after = find_overlaps(segments).size();
        // Accept only a strict overlap improvement that does not introduce a new
        // interval violation (an infeasible ordering must not trade a legal
        // overlap for a violation) — otherwise stop & reverse.
        if (after >= before || count_violations(segments) > before_viol) {
            snap.restore(segments);
            by_layer_cons = cons_before;   // this iteration's edges/bounds are reverted
            break;
        }
        std::cout << "[NUTS] corner-overlap pass: overlaps " << before
                  << " -> " << after << ".\n";
    }

    // Persist committed cross-layer split bounds onto the trunk TrackSegments,
    // so detailed NUTS snaps each trunk to its bounded side on real signal
    // tracks (the abstract g=1 only set the side).  Same-layer ordering edges
    // need no carry — detailed NUTS already separates same-layer trunks via
    // track reservation.
    for (const auto& [layer, cons] : by_layer_cons) {
        (void)layer;
        for (const auto& [k, b] : cons.bounds) {
            auto it = idx_of.find(k);
            if (it == idx_of.end()) continue;
            segments[it->second].track_lo_bound = b.first;
            segments[it->second].track_hi_bound = b.second;
        }
    }
}

NUTSEngine::NUTSEngine(const Floorplan& fp, const LayerStack& ls)
    : floorplan_(fp), layers_(ls) {}

void NUTSEngine::set_track_pitch(double pitch) {
    track_pitch_ = pitch;
}

std::vector<KeepoutZone> NUTSEngine::low_keepouts() const {
    std::vector<int> low_ids;
    for (int lid : layers_.get_layer_ids_by_dir(LayerDir::VERTICAL))
        if (!layers_.is_top(lid)) low_ids.push_back(lid);
    for (int lid : layers_.get_layer_ids_by_dir(LayerDir::HORIZONTAL))
        if (!layers_.is_top(lid)) low_ids.push_back(lid);
    return floorplan_.low_layer_keepouts(low_ids);
}

std::vector<KeepoutZone> NUTSEngine::solver_keepouts() const {
    auto kozs = low_keepouts();
    kozs.insert(kozs.end(), fixed_zones_.begin(), fixed_zones_.end());
    return kozs;
}

void NUTSEngine::add_fixed_segments(const std::vector<TrackSegment>& segs) {
    for (const auto& s : segs) {
        fixed_segments_.push_back(s);
        fixed_segments_.back().placed = true;
        fixed_bundle_ids_.insert(s.bundle_id);
        // Physical extent as an explicitly layer-tagged zone.  Rect is
        // integer-coordinate, so round OUTWARD — conservative: never lets a
        // free segment overlap the fixed one, at worst blocks <1 unit extra.
        KeepoutZone koz;
        koz.layer_ids = {s.layer};
        const double h = s.width / 2.0;
        // Widen the ALONG span by 1 unit (audit C2-02): keepout_occupied's
        // span test is STRICT, but the overlap metric (segs_overlap) treats
        // the along span as CLOSED — an end-to-end touch counts as a DRC. So
        // a free segment placed with its span ending exactly at a fixed
        // segment's span start, on the same track, passed the solver's
        // (STRICT) keepout gate yet compute_metrics then reported an overlap
        // the solver never saw. The +1 makes the touch a strict overlap here,
        // matching the metric. The perp/track axis stays STRICT (segs_overlap
        // uses a strict track test), so adjacent tracks are not over-blocked.
        if (s.horiz) {
            koz.bbox = Rect{(int)std::floor(sp_lo(s)) - 1,
                            (int)std::floor(s.track_position - h),
                            (int)std::ceil (sp_hi(s)) + 1,
                            (int)std::ceil (s.track_position + h)};
        } else {
            koz.bbox = Rect{(int)std::floor(s.track_position - h),
                            (int)std::floor(sp_lo(s)) - 1,
                            (int)std::ceil (s.track_position + h),
                            (int)std::ceil (sp_hi(s)) + 1};
        }
        fixed_zones_.push_back(std::move(koz));
    }
}

void NUTSEngine::add_fixed_segments_except(const NUTSResult& baseline,
                                           int exclude_bid) {
    std::vector<TrackSegment> keep;
    keep.reserve(baseline.segments.size());
    for (const auto& s : baseline.segments)
        if (s.bundle_id != exclude_bid && s.placed &&
            !std::isnan(s.track_position))
            keep.push_back(s);
    add_fixed_segments(keep);
}

TrackSegment transform_track_segment(const TrackSegment& ts,
                                     const std::string& orient,
                                     int cell_w, int cell_h,
                                     int src_x, int src_y,
                                     int dst_x, int dst_y,
                                     int new_bundle_id) {
    const OrientMap m = orient_map(orient);
    if (m.swap)
        throw std::runtime_error(
            "transform_track_segment: 90/270-degree orientations swap H<->V "
            "and thus the assigned layer — not supported for copies (needs "
            "the layer-pairing policy)");
    TrackSegment out = ts;
    out.bundle_id = new_bundle_id;
    // Axis roles in the source cell frame.
    const bool along_is_x = ts.horiz;
    const bool along_refl = along_is_x ? m.rx : m.ry;
    const bool perp_refl  = along_is_x ? m.ry : m.rx;
    const int  along_dim  = along_is_x ? cell_w : cell_h;
    const int  perp_dim   = along_is_x ? cell_h : cell_w;
    const int  along_src  = along_is_x ? src_x : src_y;
    const int  perp_src   = along_is_x ? src_y : src_x;
    const int  along_dst  = along_is_x ? dst_x : dst_y;
    const int  perp_dst   = along_is_x ? dst_y : dst_x;

    auto along = [&](double v) {
        const double loc = v - along_src;
        return (along_refl ? along_dim - loc : loc) + along_dst;
    };
    auto perp = [&](double v) {   // NaN and ±inf ride IEEE arithmetic
        const double loc = v - perp_src;
        return (perp_refl ? perp_dim - loc : loc) + perp_dst;
    };
    const double s_lo = along(ts.span_lo), s_hi = along(ts.span_hi);
    out.span_lo = std::min(s_lo, s_hi);
    out.span_hi = std::max(s_lo, s_hi);
    out.track_position = perp(ts.track_position);
    const double i_lo = perp(ts.interval_lo), i_hi = perp(ts.interval_hi);
    out.interval_lo = std::min(i_lo, i_hi);
    out.interval_hi = std::max(i_lo, i_hi);
    out.pull_target = perp(ts.pull_target);
    const double b_lo = perp(ts.track_lo_bound);
    const double b_hi = perp(ts.track_hi_bound);
    out.track_lo_bound = std::min(b_lo, b_hi);
    out.track_hi_bound = std::max(b_lo, b_hi);
    if (perp_refl) out.net_pull = -ts.net_pull;
    for (double& f : out.busterm_faces) f = along(f);
    for (auto& p : out.passthru_spans) {          // a reflection swaps the ends
        const double a = along(p.first), b = along(p.second);
        p = {std::min(a, b), std::max(a, b)};
    }
    return out;
}

TrackSegment offset_track_segment(const TrackSegment& ts, int dx, int dy,
                                  int new_bundle_id) {
    TrackSegment out = ts;
    out.bundle_id = new_bundle_id;
    const double along = ts.horiz ? dx : dy;   // routing-direction delta
    const double perp  = ts.horiz ? dy : dx;   // perpendicular delta
    out.span_lo     += along;
    out.span_hi     += along;
    out.track_position += perp;                // NaN + perp stays NaN
    out.interval_lo += perp;
    out.interval_hi += perp;
    if (!std::isnan(out.pull_target))    out.pull_target    += perp;
    if (std::isfinite(out.track_lo_bound)) out.track_lo_bound += perp;
    if (std::isfinite(out.track_hi_bound)) out.track_hi_bound += perp;
    for (double& f : out.busterm_faces) f += along;
    for (auto& p : out.passthru_spans) { p.first += along; p.second += along; }
    return out;
}


std::vector<TrackSegment> NUTSEngine::extract_segments(
    const std::vector<BundleWrapper>& bundles,
    const std::vector<int>& x_grid,
    const std::vector<int>& y_grid) const
{
    std::vector<TrackSegment> result;
    // A detour_channel reserves routable space OUTSIDE the block bounding box
    // (issue #58): a legitimately-detoured / OOB trunk lives there, but the
    // Hanan grid is built from blocks+keepouts only, so the interval seed
    // [grid.front, grid.back] excludes it and the trunk's placement window
    // collapses (empty/inverted) — it strands with unplaced bits.  Extend the
    // seed into the EXPLICIT channel below so that trunk can be seated.  Only
    // an explicit side (>= 0) counts; the -1 "auto" default leaves the seed at
    // the block bbox.  Bounded segments re-tighten to their slide window just
    // below, so ONLY free-slide (sentinel) OOB trunks use the extra room —
    // a design with no explicit channel is byte-identical.
    const DetourChannelSpec& dc = floorplan_.get_detour_channel();
    for (const auto& bw : bundles) {
        if (bw.input.candidates.empty() ||
            bw.plan.selected_topology_index < 0 ||
            bw.plan.selected_topology_index >= (int)bw.input.candidates.size())
            continue;   // out-of-range index is UB (audit C1-02)
        // A fixed (bottom-up copy) bundle is never re-solved: its already-
        // placed segments are appended to the result verbatim instead.
        if (fixed_bundle_ids_.count(bw.input.original_bundle.id)) continue;
        const Topology& topo = bw.input.candidates[bw.plan.selected_topology_index];
        for (int si = 0; si < (int)topo.segments.size(); ++si) {
            const Segment& seg = topo.segments[si];
            const bool is_horizontal = (seg.start.y == seg.end.y);
            TrackSegment ts;
            ts.bundle_id = bw.input.original_bundle.id;
            ts.seg_idx   = si;
            ts.horiz     = is_horizontal;
            ts.is_jog    = seg.is_jog;

            int lid = 0;
            if (si < (int)bw.plan.seg_layers.size() && bw.plan.seg_layers[si] >= 0)
                lid = bw.plan.seg_layers[si];
            else if (!is_horizontal && bw.input.assigned_v_layer >= 0)
                lid = bw.input.assigned_v_layer;
            else if (is_horizontal && bw.input.assigned_h_layer >= 0)
                lid = bw.input.assigned_h_layer;
            else
                lid = seg.layer_hint;

            ts.layer = lid;
            // Tapered fan-in: a segment carrying a bit SUBSET (seg_bits) is
            // as wide as its member bits only, matching the planner's charge.
            {
                const int nbits =
                    (int)bw.input.original_bundle.get_net_names().size();
                const int    n = seg_bit_count(topo, si, nbits);
                const double w = (nbits > 0 && n != nbits)
                                     ? bw.input.width * ((double)n / (double)nbits)
                                     : bw.input.width;
                ts.width = layers_.eff_bus_width(n, w, lid);
            }

            if (ts.horiz) {
                ts.span_lo = std::min(seg.start.x, seg.end.x);
                ts.span_hi = std::max(seg.start.x, seg.end.x);
                // Unlock Hanan bands: use full chip boundary as initial interval.
                // Fall back to the segment's own coordinate if the grid is empty
                // (degenerate input) so we never dereference an empty vector.
                ts.interval_lo = y_grid.empty() ? static_cast<double>(seg.start.y)
                                                : static_cast<double>(y_grid.front());
                ts.interval_hi = y_grid.empty() ? static_cast<double>(seg.start.y)
                                                : static_cast<double>(y_grid.back());
                if (!y_grid.empty()) {                 // #58: reach into the channel
                    if (dc.south >= 0) ts.interval_lo -= dc.south;   // smaller Y
                    if (dc.north >= 0) ts.interval_hi += dc.north;   // larger  Y
                }
            } else {
                ts.span_lo = std::min(seg.start.y, seg.end.y);
                ts.span_hi = std::max(seg.start.y, seg.end.y);
                ts.interval_lo = x_grid.empty() ? static_cast<double>(seg.start.x)
                                                : static_cast<double>(x_grid.front());
                ts.interval_hi = x_grid.empty() ? static_cast<double>(seg.start.x)
                                                : static_cast<double>(x_grid.back());
                if (!x_grid.empty()) {                 // #58: reach into the channel
                    if (dc.west >= 0) ts.interval_lo -= dc.west;    // smaller X
                    if (dc.east >= 0) ts.interval_hi += dc.east;    // larger  X
                }
            }
            result.push_back(ts);
        }
    }
    return result;
}

double NUTSEngine::first_fit(double lo, double hi, double width,
                              const std::vector<std::pair<double,double>>& occupied) const
{
    const double half = width / 2.0;
    const double c_lo = lo + half;
    const double c_hi = hi - half;
    if (c_lo > c_hi) return std::numeric_limits<double>::quiet_NaN();
    std::vector<double> candidates;
    candidates.push_back(c_lo);
    for (const auto& [occ_lo, occ_hi] : occupied)
        candidates.push_back(occ_hi + track_pitch_ + half);
    std::sort(candidates.begin(), candidates.end());
    for (double c : candidates) {
        if (c < c_lo) continue;
        if (c > c_hi) break;
        bool conflict = false;
        for (const auto& [occ_lo, occ_hi] : occupied) {
            if (c - half < occ_hi && c + half > occ_lo) { conflict = true; break; }
        }
        if (!conflict) return c;
    }
    return std::numeric_limits<double>::quiet_NaN();
}

double NUTSEngine::preferred_fit(
    double lo, double hi, double width,
    const std::vector<std::pair<double,double>>& occupied,
    double preferred) const
{
    const double half = width / 2.0;
    const double c_lo = lo + half;
    const double c_hi = hi - half;
    if (c_lo > c_hi) return std::numeric_limits<double>::quiet_NaN();
    std::vector<double> candidates;
    candidates.push_back(preferred);
    candidates.push_back(c_lo);
    for (const auto& [occ_lo, occ_hi] : occupied) {
        candidates.push_back(occ_hi + track_pitch_ + half);
        candidates.push_back(occ_lo - track_pitch_ - half);
    }
    auto valid = [&](double c) -> bool {
        if (c < c_lo || c > c_hi) return false;
        for (const auto& [occ_lo, occ_hi] : occupied)
            if (c - half < occ_hi && c + half > occ_lo) return false;
        return true;
    };
    // Deterministic selection (FP-determinism stopgap, see
    // seg_junction_coplacement.md Phase 0): under -O3 -march=native the same
    // solve can produce distances differing by ~1e-12 across CPUs (FMA
    // contraction on ARM vs SSE on x86), and a strict `<` over the candidate
    // list then picks different tracks per machine.  Epsilon-hysteresis,
    // first-candidate-wins: a later candidate replaces the incumbent only when
    // strictly better by more than kFpTieTol.  Exact ties keep today's
    // first-in-list winner (byte-identical behavior on any one machine — a
    // "prefer lower coordinate" tie key was tried and shifted real placements,
    // mix.buda DNUTS 60->138 unplaced), while sub-epsilon FP noise can no
    // longer flip the choice across machines: the candidate LIST order is
    // deterministic; only the distances wobble.
    constexpr double kFpTieTol = 1e-6;
    double best      = std::numeric_limits<double>::quiet_NaN();
    double best_dist = std::numeric_limits<double>::max();
    for (double c : candidates) {
        if (!valid(c)) continue;
        double dist = std::abs(c - preferred);
        if (std::isnan(best) || dist < best_dist - kFpTieTol) {
            best_dist = dist;
            best = c;
        }
    }
    return best;
}

// ---------------------------------------------------------------------------
// LayerSolver — one layer's placement pass (Phase D of the nuts/dnuts
// refactor).  Exactly the former 400-line solve_layer: the nested lambdas
// became methods, the closed-over locals became members; bodies verbatim.
// Placement flow: run_phase0() places corner-ordering-constrained anchors in
// dependency order, run_phases12() anchors pulled segments then sweeps the
// pull-free ones — all three share place_seg() / try_repack().
// ---------------------------------------------------------------------------
class LayerSolver {
public:
    LayerSolver(const NUTSEngine& eng,
                std::vector<TrackSegment*>& segs_in,
                NutsContext& ctx,
                const LayerConstraints& constraints)
        : eng_(eng), segs(segs_in),
          pull_map(ctx.pull_map), align_map(ctx.align_map),
          jn_map(ctx.rev_conn_map),   // junction edges (Part B)
          jn_segs(ctx.ts_ptr_map),    // global segment lookup
          order_preds(constraints.preds), order_bounds(constraints.bounds),
          // Incorporate KeepoutZones into 'occupied' list (user zones + leaf-cell
          // zones on LOW layers + fixed bottom-up segments' derived zones; TOP
          // segments are filtered out by layer_ids).
          kozs(eng.solver_keepouts())
    {
        // Same-layer lookup for alignment siblings (and ordering-constraint phase 0).
        for (TrackSegment* ts : segs)
            layer_map[{ts->bundle_id, ts->seg_idx}] = ts;

        // Segments placed by phase 0 to satisfy corner-ordering edges.  Built up
        // front so try_repack can treat them as fixed: a later non-constrained
        // repack must not relocate a constrained trunk and undo the vertical
        // constraint it was placed to enforce.
        for (const auto& [k, preds] : order_preds) {
            if (layer_map.count(k)) constrained.insert(k);
            for (const auto& p : preds)
                if (layer_map.count(p)) { constrained.insert(p); has_successor.insert(p); }
        }
        for (const auto& [k, b] : order_bounds)
            if (layer_map.count(k)) constrained.insert(k);
    }

    void run() {
        run_phase0();
        run_phases12();
    }

    // Band-level cluster entry (docs/internal/nuts_band_repack.md): repack a
    // caller-supplied member set — the connected component of an overlap
    // cluster — instead of deriving the set from one wedged segment.  Same
    // earliest-deadline-first ordering, same pull-aware -> dense two-phase
    // pack, same all-or-nothing commit as the placement-time try_repack.
    bool repack_cluster(std::vector<TrackSegment*> members) {
        // Corner-constrained phase-0 trunks stay FIXED, exactly as in
        // try_repack's member gathering: drop them from the repack set (they
        // remain obstacles via the non-member occupancy loop).
        members.erase(std::remove_if(members.begin(), members.end(),
            [&](TrackSegment* m) {
                return constrained.count({m->bundle_id, m->seg_idx}) != 0;
            }), members.end());
        if (members.size() < 2) return false;
        // Best-effort: a stuck member keeps its current track instead of
        // aborting the whole pack (one wedged neighbour must not veto the
        // cluster) — safe because the caller guards the commit on a strict
        // GLOBAL overlap drop and restores otherwise.
        return repack_members(std::move(members), /*best_effort=*/true);
    }

private:
    const NUTSEngine&                  eng_;
    std::vector<TrackSegment*>&        segs;
    const std::map<std::pair<int,int>, double>&                    pull_map;
    const AlignMap&                                                align_map;
    const std::map<std::pair<int,int>, std::vector<SpanAdjConn>>&  jn_map;
    const std::map<std::pair<int,int>, TrackSegment*>&             jn_segs;
    const std::map<std::pair<int,int>, std::set<std::pair<int,int>>>& order_preds;
    const std::map<std::pair<int,int>, std::pair<double,double>>&  order_bounds;
    const std::vector<KeepoutZone>     kozs;
    std::map<std::pair<int,int>, TrackSegment*> layer_map;
    std::set<std::pair<int,int>> constrained;
    std::set<std::pair<int,int>> has_successor;   // something ordered above it

    void add_keepout_occ(const TrackSegment* t,
                         std::vector<std::pair<double,double>>& occ) const {
        keepout_occupied(kozs, t, occ);
    }

    // Occupancy a candidate placement must avoid: every already-placed segment
    // of another bundle whose span overlaps ts (same-bundle bits may share
    // tracks), plus keepouts.  Span-overlap rather than a sweep active set, so
    // pull anchors pre-placed in phase 1 are seen when packing phase 2 — and
    // for a single pull-free pass this is equivalent to the old active-set
    // sweep, since in span order an overlapping earlier segment is exactly one
    // that has started and not yet ended.
    void build_occupied(const TrackSegment* ts,
                        std::vector<std::pair<double,double>>& occ) const {
        for (const TrackSegment* o : segs) {
            if (o == ts) continue;
            occupancy_from(*ts, *o, occ);   // closed-span predicate, see nuts_geom.h
        }
        add_keepout_occ(ts, occ);
    }

    // Local repack: when no gap fits ts, earlier centre-seeking placements
    // may have fragmented a window that has room for everyone (two 51-wide
    // trunks in a 110-wide shared slide window: the first at the centre
    // leaves two 29.5 slivers).  Re-place ts together with the active
    // segments contending for its interval, packing from the low edge,
    // against everything else already placed.  Commits only on full success.
    bool try_repack(TrackSegment* ts) {
        // Members: every placed segment contending for ts's window (interval
        // overlap) — including ones whose sweep span already ended.  An ended
        // segment doesn't conflict with ts directly, but it constrains other
        // members through span overlap; keeping it fixed can wedge the window
        // (e.g. planner3 M6: B3.seg1 at 483 forces B2.seg2 past 552, out of
        // its alignment sibling's reach).  Span-based obstacles below keep
        // every re-placement physically valid.
        std::vector<TrackSegment*> members{ts};
        for (TrackSegment* o : segs) {
            if (o == ts || !o->placed) continue;
            // Phase-0 constrained trunks stay fixed (they carry corner-ordering
            // edges); they remain obstacles via the non-member span-overlap loop.
            if (constrained.count({o->bundle_id, o->seg_idx})) continue;
            if (o->interval_lo < ts->interval_hi &&
                ts->interval_lo < o->interval_hi)
                members.push_back(o);
        }
        if (members.size() < 2) return false;
        return repack_members(std::move(members));
    }

    // The shared repack body behind try_repack (placement-time, member set
    // derived from one wedged segment; all-or-nothing) and repack_cluster
    // (repair-time, member set = an overlap cluster + its contention sweep;
    // best_effort — see repack_cluster).
    bool repack_members(std::vector<TrackSegment*> members,
                        bool best_effort = false) {
        std::set<const TrackSegment*> member_set(members.begin(), members.end());

        // Earliest deadline first: the member whose window ENDS soonest gets
        // the lowest position.  Ordering by window tightness instead can park
        // a flexible member at the bottom of its window, re-blocking the very
        // member that triggered the repack.  Tie: least slack first.
        std::stable_sort(members.begin(), members.end(),
            [](const TrackSegment* a, const TrackSegment* b) {
                if (a->interval_hi != b->interval_hi)
                    return a->interval_hi < b->interval_hi;
                return (a->interval_hi - a->interval_lo) - a->width
                     < (b->interval_hi - b->interval_lo) - b->width;
            });

        // Pack the members in deadline order.  In pull-aware mode each member
        // seeks its own pull target (pull_map) — so a phase-1 anchor swept into a
        // repack keeps its pull instead of being bottom-edged — while pull-free
        // members pack to the low edge (== first_fit) to leave maximal room.  In
        // dense mode every member first_fits to the low edge.  Returns the packed
        // positions, or empty on any infeasible member.
        auto pack = [&](bool pull_aware)
                    -> std::vector<std::pair<TrackSegment*,double>> {
            std::vector<std::pair<TrackSegment*,double>> repacked;
            for (TrackSegment* m : members) {
                // Committed cross-layer split bounds (corner resolution) are
                // hard CENTER bounds — clamp the member's window so a repair-
                // time repack can never move a bounded trunk across its split.
                // Defaults are ±inf, so placement-time behavior is unchanged.
                double w_lo = m->interval_lo, w_hi = m->interval_hi;
                if (m->track_lo_bound > -kInf)
                    w_lo = std::max(w_lo, m->track_lo_bound - m->width / 2.0);
                if (m->track_hi_bound < kInf)
                    w_hi = std::min(w_hi, m->track_hi_bound + m->width / 2.0);
                std::vector<std::pair<double,double>> occ;
                add_keepout_occ(m, occ);
                // Placed segments outside the repack set (including ones whose
                // sweep interval already ended) that overlap m's span.
                // Same-bundle segments never conflict (bits may share tracks).
                for (const TrackSegment* o : segs) {
                    if (member_set.count(o)) continue;
                    occupancy_from(*m, *o, occ);
                }
                for (const auto& [pm, ppos] : repacked) {
                    if (pm->bundle_id == m->bundle_id) continue;
                    // Members conflict only where their spans overlap — same
                    // physical criterion as the outside-set obstacles above.
                    // Without this, disjoint-span members sharing one window
                    // (e.g. blk-local buses left and right of a cross-chip
                    // trunk, all at the same Hanan band) are packed as if
                    // simultaneous and wedge a window that has room for all.
                    if (!(sp_lo(*pm) <= sp_hi(*m) && sp_lo(*m) <= sp_hi(*pm)))  // closed: touch = occupied
                        continue;
                    const double h = pm->width / 2.0;
                    occ.push_back({ppos - h, ppos + h});
                }
                std::sort(occ.begin(), occ.end());
                double p;
                if (pull_aware) {
                    auto pit = pull_map.find({m->bundle_id, m->seg_idx});
                    // Anchors keep their pull.  In the repair-time (best_effort)
                    // pack a pull-free member aims at its pull_map entry too:
                    // for planner-managed segments that is the CHARGED band
                    // centre (seg_perp) — the band whose signal-track supply
                    // the planner verified — so a cluster repack separates
                    // buses without drifting them onto bands DetailedNUTS
                    // cannot honor.  Placement-time behavior is unchanged
                    // (pull-free members pack low, as before).
                    double pref;
                    if (m->net_pull != 0 && pit != pull_map.end())
                        pref = pit->second;               // anchor: keep its pull
                    else if (best_effort)
                        pref = (pit != pull_map.end()) ? pit->second
                                                       : m->track_position;
                    else
                        pref = m->interval_lo;            // placement: pack low
                    // The pull target is an interval EDGE (interval_hi for an
                    // upward pull), which lies outside preferred_fit's valid centre
                    // range [c_lo, c_hi] and is not one of its baseline candidates —
                    // so without clamping, an upward-pulled member with a clear high
                    // edge falls back to c_lo and is bottom-packed.  Clamp into the
                    // centre range first, exactly as place_seg does.
                    const double half = m->width / 2.0;
                    const double c_lo = w_lo + half;
                    const double c_hi = w_hi - half;
                    if (c_lo <= c_hi) pref = std::clamp(pref, c_lo, c_hi);
                    p = eng_.preferred_fit(w_lo, w_hi, m->width, occ, pref);
                } else {
                    p = eng_.first_fit(w_lo, w_hi, m->width, occ);
                }
                if (std::isnan(p)) {
                    if (!best_effort) return {};   // all-or-nothing: pack failed
                    // Best-effort: the member stays where it is and becomes an
                    // obstacle for the rest of the pack.  Earlier members may
                    // have taken its spot — the caller's guard arbitrates.
                    p = m->track_position;
                }
                repacked.push_back({m, p});
            }
            return repacked;
        };

        // Honor pulls when the window has room for everyone at their pull; else
        // fall back to the dense low-edge pack (the proven feasibility path) so a
        // tight window still resolves its overlap rather than dropping ts.
        auto changed = [](const std::vector<std::pair<TrackSegment*,double>>& r) {
            for (const auto& [pm, ppos] : r)
                if (std::abs(ppos - pm->track_position) > 0.5) return true;
            return false;
        };
        auto repacked = pack(/*pull_aware=*/true);
        if (repacked.empty() || (best_effort && !changed(repacked)))
            repacked = pack(/*pull_aware=*/false);
        if (repacked.empty()) return false;   // window truly full: keep old state
        if (best_effort && !changed(repacked))
            return false;                     // nothing would move: keep old state
        for (const auto& [pm, ppos] : repacked) pm->track_position = ppos;
        return true;
    }

    // Place one segment at its preferred track, avoiding current occupancy.
    // lb/ub (optional) are hard bounds on the track CENTER — phase 0 uses them to
    // keep a constrained segment above its predecessors (lb) and/or on one side
    // of a cross-layer split (ub).  pack_low packs to the lowest feasible track
    // (same-layer ordering); otherwise placement seeks `target` if finite
    // (cross-layer: nudge toward the split bound), else the align/pull preference.
    void place_seg(TrackSegment* ts,
                   double lb = -kInf, double ub = kInf,
                   bool pack_low = false,
                   double target = std::numeric_limits<double>::quiet_NaN()) {
        std::vector<std::pair<double,double>> occupied;
        build_occupied(ts, occupied);
        std::sort(occupied.begin(), occupied.end());

        // Honor lb/ub by tightening the band edges passed to the fit (without
        // mutating the stored hard interval).
        double eff_lo = ts->interval_lo, eff_hi = ts->interval_hi;
        if (lb > -kInf) eff_lo = std::max(eff_lo, lb - ts->width / 2.0);
        if (ub <  kInf) eff_hi = std::min(eff_hi, ub + ts->width / 2.0);
        const double c_lo = eff_lo + ts->width / 2.0;
        const double c_hi = eff_hi - ts->width / 2.0;
        // pack_low → lowest feasible track (bottom-edge / first_fit).  The
        // preferred computation (and its clamp) only matters otherwise — skip it
        // for pack_low, where a lower bound can make c_lo > c_hi and
        // std::clamp(preferred, c_lo, c_hi) would be undefined.
        double pos;
        if (pack_low) {
            pos = eng_.first_fit(eff_lo, eff_hi, ts->width, occupied);
        } else {
            auto key = std::make_pair(ts->bundle_id, ts->seg_idx);
            // Cross-layer bounded trunks aim straight at the split-side bound;
            // others seek an alignment sibling, then their pull, then centre.
            double preferred = target;
            if (std::isnan(preferred)) {
                auto ait = align_map.find(key);
                if (ait != align_map.end()) {
                    for (const auto& sk : ait->second) {
                        auto lit = layer_map.find(sk);
                        if (lit == layer_map.end() || !lit->second->placed) continue;
                        double p = lit->second->track_position;
                        if (p >= c_lo && p <= c_hi) { preferred = p; break; }
                    }
                }
            }
            // Junction-anchored preference (Part B): this segment's track IS a
            // coordinate along each junction partner (tree edges meet at the
            // junction vertex).  When a placed partner's span does not already
            // cover the pull/centre preference, move the preference to the
            // nearest covered point — the junction is then honored where this
            // segment LANDS, instead of stretching the partner afterwards
            // (extension is real occupancy and the source of overstretch).
            if (std::isnan(preferred)) {
                auto jit = jn_map.find(key);
                // Restrict to segments with exactly ONE junction edge (a corner
                // stub landing on one partner — the bundle-48 shape).  Applying
                // the preference to multi-junction spines/stub-fans clustered
                // placements and regressed mix.buda DNUTS 60->74 unplaced.
                if (jit != jn_map.end() && jit->second.size() == 1) {
                    auto pit = pull_map.find(key);
                    const double base = (pit != pull_map.end())
                                        ? pit->second
                                        : (ts->interval_lo + ts->interval_hi) / 2.0;
                    for (const auto& sc : jit->second) {
                        auto pj = jn_segs.find({sc.src_bid, sc.src_si});
                        if (pj == jn_segs.end()) continue;
                        const TrackSegment* pt = pj->second;
                        // Direction check FIRST (horiz is immutable): a
                        // same-direction partner may be mid-solve on another
                        // thread of the same orientation group (P3), so its
                        // mutable placed/track fields must not be read.
                        if (pt->horiz == ts->horiz) continue;   // partners are perpendicular
                        if (!pt->placed) continue;
                        const double plo = std::min(pt->span_lo, pt->span_hi);
                        const double phi = std::max(pt->span_lo, pt->span_hi);
                        if (base >= plo && base <= phi) break;  // already covered: keep base
                        const double cand = std::clamp(base, plo, phi);
                        if (cand >= c_lo && cand <= c_hi) { preferred = cand; break; }
                    }
                }
            }
            if (std::isnan(preferred)) {
                auto it = pull_map.find(key);
                preferred = (it != pull_map.end())
                            ? it->second
                            : (ts->interval_lo + ts->interval_hi) / 2.0;
            }
            // Guard against an inverted range (bus wider than its interval):
            // std::clamp requires lo <= hi.  preferred_fit returns NaN there
            // anyway, so the value is irrelevant — just avoid the UB.
            if (c_lo <= c_hi) preferred = std::clamp(preferred, c_lo, c_hi);
            pos = eng_.preferred_fit(eff_lo, eff_hi, ts->width, occupied, preferred);
        }
        // try_repack re-places members with first_fit over their FULL intervals,
        // ignoring lb/ub — so it must not run for a phase-0 constrained placement
        // (pack_low or any finite bound/target), or it could relocate a bounded
        // trunk to the wrong side of its split.  Only unconstrained phase-1/2
        // placements may repack.
        const bool phase0 = pack_low || lb > -kInf || ub < kInf || !std::isnan(target);
        if (!std::isnan(pos))           ts->track_position = pos;
        else if (!phase0 && try_repack(ts)) { /* repacked */ }
        else {
            // No feasible track (e.g. an ordering lower bound pushed eff_lo past
            // the interval).  Fall back to the midpoint but clamp it into the
            // valid centre range so an infeasible ordering can never create an
            // interval violation — the unsatisfied ordering just leaves the
            // overlap for resolve_corner_overlaps' guard to reject.  Only the
            // pathological bus-wider-than-interval case (empty range) keeps the
            // raw midpoint, preserving prior behavior.
            double fb = (eff_lo + eff_hi) / 2.0;
            const double v_lo = ts->interval_lo + ts->width / 2.0;
            const double v_hi = ts->interval_hi - ts->width / 2.0;
            if (v_lo <= v_hi) fb = std::clamp(fb, v_lo, v_hi);
            ts->track_position = fb;
        }
        ts->placed = true;
    }

    // Lower bound from a phase-0 segment's placed predecessors: just above the
    // highest one, one pitch clear.
    double lb_of(const std::pair<int,int>& k, const TrackSegment* ts) const {
        double lb = -std::numeric_limits<double>::infinity();
        auto it = order_preds.find(k);
        if (it != order_preds.end())
            for (const auto& p : it->second) {
                auto lit = layer_map.find(p);
                if (lit == layer_map.end() || !lit->second->placed) continue;
                lb = std::max(lb, lit->second->track_position
                                  + lit->second->width / 2.0
                                  + ts->width / 2.0 + eng_.track_pitch_);
            }
        return lb;
    }

    // Place one phase-0 segment honoring both its relative-pred lower bound
    // and any fixed cross-layer bounds.  A bounded (cross-layer) trunk is
    // nudged toward its split-side bound via preferred_fit; a relative-pred
    // (same-layer) trunk packs to the bottom edge.
    void place_phase0(const std::pair<int,int>& k, TrackSegment* ts) {
        double lb = lb_of(k, ts);
        double ub = kInf;
        double target = std::numeric_limits<double>::quiet_NaN();
        auto bit = order_bounds.find(k);
        const bool bounded = (bit != order_bounds.end());
        if (bounded) {
            lb = std::max(lb, bit->second.first);
            ub = bit->second.second;
            // Aim at the finite (split-facing) bound for minimal movement.
            target = (ub < kInf) ? ub : bit->second.first;
        }
        place_seg(ts, lb, ub, /*pack_low=*/!bounded, target);
    }

    // Phase 0 — ordering-constrained segments (corner-overlap resolution): place
    // each in dependency order (after every segment that must sit below it),
    // clamped just above its placed predecessors.  They become placed anchors
    // the normal anchor/sweep phases then avoid.  (`constrained` built in ctor.)
    void run_phase0() {
        if (constrained.empty()) return;
        std::set<std::pair<int,int>> done;
        std::vector<std::pair<int,int>> todo(constrained.begin(), constrained.end());
        bool progress = true;
        while (!todo.empty() && progress) {
            progress = false;
            for (auto it = todo.begin(); it != todo.end();) {
                auto pit = order_preds.find(*it);
                bool ready = true;
                if (pit != order_preds.end())
                    for (const auto& p : pit->second)
                        if (constrained.count(p) && !done.count(p)) { ready = false; break; }
                if (!ready) { ++it; continue; }
                place_phase0(*it, layer_map[*it]);
                done.insert(*it);
                it = todo.erase(it);
                progress = true;
            }
        }
        // Cycle fallback: place whatever's left with its already-placed preds.
        for (const auto& k : todo) {
            place_phase0(k, layer_map[k]);
            done.insert(k);
        }

        // Cluster pull.  Bottom-edge packing honors a downward/zero net_pull but
        // not an upward one.  If the ordered cluster pulls net upward (e.g. a
        // dogleg whose two trunk pieces both inherit an up-pull), translate the
        // WHOLE cluster up rigidly — preserving every ordering and gap (so a
        // dogleg's jog length is unchanged) while shortening the up-pulled
        // stubs.  The shift is bounded by each member's own interval; the
        // members move together so they don't bound each other, and phase-0
        // anchors are avoided by the later phases.
        int pull_sum = 0;
        for (const auto& k : done) pull_sum += layer_map[k]->net_pull;
        if (pull_sum > 0) {
            double headroom = kInf;
            for (const auto& k : done) {
                const TrackSegment* ts = layer_map[k];
                // Cap the rigid shift by each member's own hard interval AND its
                // finite phase-0 upper bound: when solve_layer runs under
                // resolve_corner_overlaps' cross-layer constraints, order_bounds
                // pins a bounded trunk to a split side, and the cluster must not
                // ride past it (that would undo the corner split the pass relies on).
                double cap = ts->interval_hi - ts->width / 2.0;
                auto bit = order_bounds.find(k);
                if (bit != order_bounds.end() && bit->second.second < kInf)
                    cap = std::min(cap, bit->second.second);
                headroom = std::min(headroom, cap - ts->track_position);
            }
            if (headroom > 0)
                for (const auto& k : done) layer_map[k]->track_position += headroom;
        }
    }

    // Phase 1 — anchor pulled segments first at their preferred (pull-extreme)
    // positions, strongest pull then tightest interval first, so the pull-free
    // segments fill the residual gaps in phase 2.  A pull-free trunk then slides
    // off any track a pulled bus needs, instead of grabbing it by sweep order
    // and forcing the pulled bus to detour (item A: planner6 Bundle 3/4).
    // Phase 2 — sweep the pull-free segments in span order; each sees the
    // anchors and earlier free segments via build_occupied and slides to the
    // nearest free track.
    void run_phases12() {
        std::vector<TrackSegment*> pulled, free_segs;
        for (TrackSegment* ts : segs) {
            if (constrained.count({ts->bundle_id, ts->seg_idx})) continue;  // placed in phase 0
            (ts->net_pull != 0 ? pulled : free_segs).push_back(ts);
        }
        // FP-determinism: window widths are FP-derived and can differ by ~1e-12
        // across CPUs; a strict `<` on the raw doubles flips near-ties per machine.
        // Sort on a QUANTIZED integer key (1e-6 quantum) instead — a strict weak
        // ordering by construction (a raw epsilon comparator is not: sub-tolerance
        // deltas chain non-transitively, which is UB for stable_sort — Codex #152).
        // BUDA keys are integer/half-integer-valued reals, so ~1e-12 noise can never
        // move llround across a rounding boundary; equal keys keep the deterministic
        // input order via stable_sort — today's exact-tie behavior, on every machine.
        auto q = [](double v) { return std::llround(v * 1e6); };
        std::stable_sort(pulled.begin(), pulled.end(),
            [&q](const TrackSegment* a, const TrackSegment* b) {
                int pa = std::abs(a->net_pull), pb = std::abs(b->net_pull);
                if (pa != pb) return pa > pb;                       // strongest pull first
                return q(a->interval_hi - a->interval_lo)
                     < q(b->interval_hi - b->interval_lo);          // tightest window first
            });
        for (TrackSegment* ts : pulled) place_seg(ts);

        std::stable_sort(free_segs.begin(), free_segs.end(),
            [&q](const TrackSegment* a, const TrackSegment* b) {
                // Quantized integer key (see pulled sort); ordered: span_lo may be
                // reversed.  Equal keys keep the deterministic input order.
                return q(sp_lo(*a)) < q(sp_lo(*b));
            });
        for (TrackSegment* ts : free_segs) place_seg(ts);
    }
};

void NUTSEngine::solve_layer(std::vector<TrackSegment*>& segs,
                              NutsContext& ctx,
                              const LayerConstraints& constraints) const {
    if (segs.empty()) return;
    LayerSolver(*this, segs, ctx, constraints).run();
}

// Band-level repack of spread-fit overlap clusters (nuts_band_repack.md).
// Discover the residual overlap graph's connected components (edges are
// same-layer by segs_overlap construction), skip clusters the union window
// provably cannot host (over-capacity: the planner's problem, warned as
// ALLOW_OVERFLOW), and re-place each surviving cluster AS A SET through
// LayerSolver::repack_cluster — earliest-deadline-first, pull-aware with a
// dense fallback, non-members as obstacles, committed cross-layer track
// bounds respected.  Every cluster is individually guarded: accepted only
// when the GLOBAL overlap count strictly drops without new interval
// violations, else restored.  Returns the number of member segments of the
// accepted repacks (for the caller's summary line).
static int repack_overlap_clusters(const NUTSEngine& eng, double track_pitch,
                                   std::vector<TrackSegment>& segments,
                                   NutsContext& ctx,
                                   const std::map<int, LayerConstraints>* seed_cons)
{
    auto pairs = find_overlaps(segments);
    if (pairs.empty()) return 0;

    // Connected components over segment indices (union-find).
    std::vector<int> parent(segments.size());
    for (int i = 0; i < (int)parent.size(); ++i) parent[i] = i;
    std::function<int(int)> find = [&](int x) {
        while (parent[x] != x) { parent[x] = parent[parent[x]]; x = parent[x]; }
        return x;
    };
    for (auto [i, j] : pairs) parent[find(i)] = find(j);
    std::map<int, std::vector<int>> comps;
    std::set<int> in_overlap;
    for (auto [i, j] : pairs) { in_overlap.insert(i); in_overlap.insert(j); }
    for (int i : in_overlap) comps[find(i)].push_back(i);

    // Deterministic processing order: largest cluster first (big clusters
    // constrain small ones), ties by lowest member (bundle_id, seg_idx).
    std::vector<std::vector<int>> clusters;
    for (auto& [root, idx] : comps) {
        (void)root;
        std::sort(idx.begin(), idx.end(), [&](int a, int b) {
            return std::make_pair(segments[a].bundle_id, segments[a].seg_idx)
                 < std::make_pair(segments[b].bundle_id, segments[b].seg_idx);
        });
        clusters.push_back(idx);
    }
    std::sort(clusters.begin(), clusters.end(),
        [&](const std::vector<int>& a, const std::vector<int>& b) {
            if (a.size() != b.size()) return a.size() > b.size();
            const auto& sa = segments[a[0]];
            const auto& sb = segments[b[0]];
            return std::make_pair(sa.bundle_id, sa.seg_idx)
                 < std::make_pair(sb.bundle_id, sb.seg_idx);
        });

    const LayerConstraints no_cons;   // named: LayerSolver stores references
    int moved = 0;
    for (const auto& cluster : clusters) {
        const int layer = segments[cluster[0]].layer;

        // Spread-fit precheck: the members' union window must be able to host
        // every member stacked (Σwidth + inter-bus pitch).  A conservative
        // necessary condition — clusters whose members' spans merely touch
        // can exceed it yet still admit a span-aware pack (v1 skips those;
        // the repack itself would also fail safely, this just saves work).
        double lo = kInf, hi = -kInf, sumw = 0.0;
        for (int i : cluster) {
            lo = std::min(lo, segments[i].interval_lo);
            hi = std::max(hi, segments[i].interval_hi);
            sumw += segments[i].width;
        }
        if (sumw + (double)(cluster.size() - 1) * track_pitch > hi - lo)
            continue;   // over-capacity: not a packing problem

        std::vector<TrackSegment*> layer_segs;
        for (auto& ts : segments)
            if (ts.layer == layer) layer_segs.push_back(&ts);

        // Two attempts per cluster, each individually guarded:
        //   narrow — the cluster members only (least collateral: neighbours
        //            stay put as obstacles);
        //   wide   — the cluster PLUS every placed same-layer segment whose
        //            interval overlaps the union window (try_repack's
        //            contention sweep, seeded by all members: the members'
        //            windows may be full GIVEN the neighbours' positions, so
        //            the neighbours must move with them).
        std::vector<TrackSegment*> narrow;
        std::set<const TrackSegment*> in_members;
        for (int i : cluster) {
            narrow.push_back(&segments[i]);
            in_members.insert(&segments[i]);
        }
        std::vector<TrackSegment*> wide = narrow;
        for (TrackSegment* o : layer_segs) {
            if (!o->placed || in_members.count(o)) continue;
            if (o->interval_lo < hi && lo < o->interval_hi)
                wide.push_back(o);
        }

        // In-cluster overlap count: pairs whose BOTH endpoints are cluster
        // members (indexes into `segments` are stable across the repack).
        std::set<int> cset(cluster.begin(), cluster.end());
        auto in_cluster_ov = [&]() {
            size_t n = 0;
            for (auto [i, j] : find_overlaps(segments))
                if (cset.count(i) && cset.count(j)) ++n;
            return n;
        };

        // Active corner constraints for this layer (empty when none): the
        // solver's `constrained` set makes phase-0 trunks fixed obstacles.
        const LayerConstraints* cons = &no_cons;
        if (seed_cons) {
            auto cit = seed_cons->find(layer);
            if (cit != seed_cons->end()) cons = &cit->second;
        }

        for (const auto& members : {narrow, wide}) {
            const size_t ov_before = find_overlaps(segments).size();
            const size_t ic_before = in_cluster_ov();
            const int    vi_before = count_violations(segments);
            PlacementSnapshot snap;
            snap.take(segments);
            LayerSolver solver(eng, layer_segs, ctx, *cons);
            if (!solver.repack_cluster(members))
                continue;                   // nothing would move: state untouched
            settle_spans(segments, ctx);
            // Accept when the cluster itself strictly improves and the world
            // does not get worse — a committed repack may trade an in-cluster
            // overlap for collateral elsewhere (follower spans stretch), which
            // the caller's next single-victim sweep cleans up; the pass-level
            // non-regression snapshot in repair_overlaps backstops the total.
            if (find_overlaps(segments).size() > ov_before ||
                in_cluster_ov() >= ic_before ||
                count_violations(segments) > vi_before) {
                snap.restore(segments);     // repack made things no better
                continue;
            }
            moved += (int)members.size();
            std::cout << "[NUTS] cluster repack: separated a "
                      << cluster.size() << "-segment overlap cluster on layer "
                      << layer << ".\n";
            break;                          // this cluster is done
        }
    }
    return moved;
}

void NUTSEngine::set_extra_grid_points(std::vector<int> xs, std::vector<int> ys) {
    std::sort(xs.begin(), xs.end());
    xs.erase(std::unique(xs.begin(), xs.end()), xs.end());
    std::sort(ys.begin(), ys.end());
    ys.erase(std::unique(ys.begin(), ys.end()), ys.end());
    extra_x_ = std::move(xs);
    extra_y_ = std::move(ys);
}

void NUTSEngine::orientation_fixpoint(
    std::vector<TrackSegment>& segments,
    std::map<int, std::vector<TrackSegment*>>& by_layer,
    NutsContext& ctx,
    const std::map<int, LayerConstraints>& seed_cons) const
{
    const auto& pull_map     = ctx.pull_map;
    const auto& rev_conn_map = ctx.rev_conn_map;
    auto&       ts_ptr_map   = ctx.ts_ptr_map;
    if (by_layer.empty()) return;
    auto cons_for = [&](int lid) -> const LayerConstraints& {
        static const LayerConstraints kEmpty;
        auto it = seed_cons.find(lid);
        return it != seed_cons.end() ? it->second : kEmpty;
    };

    // Partition populated layers into the two orientation groups; lead with the
    // orientation of the lowest TOP layer (default rule), falling back to the
    // lowest populated layer when no TOP layer is present.
    std::vector<int> h_all = layers_.get_layer_ids_by_dir(LayerDir::HORIZONTAL);
    std::set<int>    h_set(h_all.begin(), h_all.end());
    std::vector<int> h_group, v_group;
    for (auto& [lid, segs] : by_layer) {
        (void)segs;
        if (h_set.count(lid)) h_group.push_back(lid);
        else                  v_group.push_back(lid);
    }
    int  lead_lid  = std::numeric_limits<int>::max();
    bool found_top = false;
    for (auto& [lid, segs] : by_layer) {
        (void)segs;
        const bool top = layers_.is_top(lid);
        if (top && !found_top)                       { found_top = true; lead_lid = lid; }
        else if (top == found_top && lid < lead_lid) { lead_lid = lid; }
    }
    const bool lead_is_h = h_set.count(lead_lid) > 0;
    const std::vector<int>& lead_group = lead_is_h ? h_group : v_group;
    const std::vector<int>& perp_group = lead_is_h ? v_group : h_group;

    // Solve every layer in a group (reset first — GLOBAL re-solve), then
    // propagate the freshly-pinned trunk positions to all connected spans so the
    // perpendicular group packs against true (already-stretched) extents.
    //
    // P3 (rnr runtime): the group's per-layer solves are INDEPENDENT — a
    // LayerSolver reads/writes only its own layer's segments (alignment
    // siblings resolve through the same-layer map, the junction preference
    // reads only perpendicular partners, whose group is not being solved,
    // and occupancy/keepouts/constraints are per layer) — so with 2+
    // populated layers they run on worker threads with results identical to
    // the sequential loop.  Thread budget: set_layer_threads > 0 wins (an
    // EXPLICIT count > 1 also bypasses the size gate below), else
    // BUDA_NUTS_THREADS (same bypass), else hardware concurrency; 1 =
    // sequential (parallel_sweep's workers pass 1 — the move fan-out owns
    // the cores).  Exception-safe construction as in trial_sweep.cpp:
    // started workers are kept and joined, zero started workers fall back
    // to inline.
    //
    // SIZE GATE (auto mode only): a layer solve on the current corpus is
    // ~ms — spawning threads for it costs more than it saves (measured:
    // mix2_fast_bottomup's residual sequential fixpoint bucket 0.82 ->
    // 1.07 s with unconditional threads).  The win is also Amdahl-bounded
    // by the group's HEAVIEST layer (the planner concentrates load on the
    // lowest TOP layer, so real groups are skewed — e.g. 553/245/180 segs):
    // parallel wall = max(layer) while sequential = sum, so the profit is
    // exactly the work OUTSIDE the heaviest layer.  Auto therefore threads
    // only when that parallelizable remainder — Σ segs² over the group's
    // non-largest layers, the O(segs²) placement-occupancy proxy — is worth
    // a spawn (kP3MinParallelWork ≈ two 256-seg layers).  The gate affects
    // SPEED only — parallel and sequential group solves produce identical
    // placements (independence above), so the threshold needs no
    // byte-identity guard.
    constexpr double kP3MinParallelWork = 131072.0;   // ≈ 2 × 256²
    int  nuts_threads = layer_threads_;
    bool gate_bypassed = nuts_threads > 1;
    if (nuts_threads <= 0) {
        if (const char* e = std::getenv("BUDA_NUTS_THREADS")) {
            nuts_threads = std::atoi(e);
            gate_bypassed = nuts_threads > 1;
        }
        if (nuts_threads <= 0) {
            unsigned hw = std::thread::hardware_concurrency();
            nuts_threads = hw ? (int)hw : 1;
        }
    }
    auto group_is_heavy = [&](const std::vector<int>& group) {
        double total = 0.0, largest = 0.0;
        for (int lid : group) {
            auto lit = by_layer.find(lid);
            if (lit == by_layer.end()) continue;
            const double w = (double)lit->second.size()
                           * (double)lit->second.size();
            total += w;
            largest = std::max(largest, w);
        }
        return total - largest >= kP3MinParallelWork;
    };
    auto solve_group = [&](const std::vector<int>& group) {
        const int nt = std::min<int>(nuts_threads, (int)group.size());
        if (nt <= 1 || group.size() < 2 ||
            (!gate_bypassed && !group_is_heavy(group))) {
            for (int lid : group) {
                for (auto* ts : by_layer[lid]) {
                    ts->track_position = std::numeric_limits<double>::quiet_NaN();
                    ts->placed = false;
                }
                solve_layer(by_layer[lid], ctx, cons_for(lid));
            }
        } else {
            // Exception containment (Codex #504 P2): an exception escaping a
            // child std::thread is std::terminate, and an inline throw would
            // unwind past the still-joinable pool (terminate again).  The
            // sequential path propagates solve errors normally — match it:
            // every worker (incl. the inline one) traps into a shared
            // exception_ptr (first one wins; later workers stop claiming
            // layers), the pool is ALWAYS joined, and the error is rethrown
            // after the join.
            std::atomic<size_t> next{0};
            std::atomic<bool>   failed{false};
            std::exception_ptr  err;
            std::mutex          err_mu;
            auto worker = [&]() {
                try {
                    for (size_t i = next.fetch_add(1);
                         i < group.size() && !failed.load();
                         i = next.fetch_add(1)) {
                        auto lit = by_layer.find(group[i]);  // const lookup:
                        if (lit == by_layer.end()) continue; // no op[] race
                        for (auto* ts : lit->second) {
                            ts->track_position =
                                std::numeric_limits<double>::quiet_NaN();
                            ts->placed = false;
                        }
                        solve_layer(lit->second, ctx, cons_for(group[i]));
                    }
                } catch (...) {
                    std::lock_guard<std::mutex> g(err_mu);
                    if (!err) err = std::current_exception();
                    failed.store(true);
                }
            };
            std::vector<std::thread> pool;
            pool.reserve(nt - 1);
            try {
                for (int t = 0; t < nt - 1; ++t) pool.emplace_back(worker);
            } catch (const std::system_error&) {
            }
            worker();                       // this thread is a worker too
            for (auto& th : pool) th.join();
            if (err) std::rethrow_exception(err);
        }
        settle_spans(segments, ctx);
    };

    auto take = [&]() {
        PlacementSnapshot s(/*with_placed=*/true);
        s.take(segments);
        return s;
    };
    auto restore = [&](const PlacementSnapshot& s) { s.restore(segments); };
    // Legacy per-layer order (ascending id, per-layer span adjustment, then a
    // final all-layer pass) — today's solve, honouring any seed constraints.
    auto legacy_solve = [&]() {
        for (auto& [lid, segs] : by_layer) {
            for (auto* ts : segs) {
                ts->track_position = std::numeric_limits<double>::quiet_NaN();
                ts->placed = false;
            }
            solve_layer(segs, ctx, cons_for(lid));
            do_span_adjustments(segs, rev_conn_map, ts_ptr_map);
        }
        for (auto& [lid, segs] : by_layer) {
            (void)lid;
            do_span_adjustments(segs, rev_conn_map, ts_ptr_map);
        }
    };
    auto state_hash = [&]() {
        size_t h = 0;
        auto mix = [&](long long v) {
            h ^= std::hash<long long>{}(v) + 0x9e3779b97f4a7c15ULL + (h << 6) + (h >> 2);
        };
        const long long unp = std::numeric_limits<long long>::min();
        for (const auto& ts : segments) {
            mix(ts.placed ? std::llround(ts.track_position) : unp);
            mix(std::llround(ts.span_lo));
            mix(std::llround(ts.span_hi));
        }
        return h;
    };
    // Distance of placed segments from their planner-reserved band (pull_map): a
    // proxy for detailed-NUTS feasibility — the planner sized those bands for the
    // bits, so straying risks a signal-track shortfall downstream.
    auto pull_deviation = [&]() {
        double dev = 0.0;
        for (const auto& ts : segments) {
            if (!ts.placed) continue;
            auto it = pull_map.find({ts.bundle_id, ts.seg_idx});
            if (it != pull_map.end()) dev += std::abs(ts.track_position - it->second);
        }
        return dev;
    };

    // Seed with the legacy order, then refine with alternating-group sweeps.
    // Each sweep is a deterministic function of the current spans, so the
    // trajectory reaches a fixed point or cycles — both caught by the repeated-
    // state set.  Adopt a sweep only when it STRICTLY reduces overlaps WITHOUT
    // pushing buses further from their reserved bands than legacy did: raw
    // overlap count alone is unfair (the repair/resolve passes run after this and
    // clean up legacy's residue), and a sweep that "wins" by evicting a bus from
    // its band only trades an abstract overlap for a detailed signal-track
    // shortfall.  Single orientation present ⇒ no alternation, just the seed.
    legacy_solve();
    size_t            best_ov    = find_overlaps(segments).size();
    const double      legacy_dev = pull_deviation();
    PlacementSnapshot best_snap  = take();
    std::set<size_t>  seen{state_hash()};

    if (!lead_group.empty() && !perp_group.empty()) {
        const int kMaxIters = 12;
        for (int iter = 0; iter < kMaxIters && best_ov > 0; ++iter) {
            solve_group(lead_group);
            solve_group(perp_group);
            const size_t n = find_overlaps(segments).size();
            const double d = pull_deviation();
            if (n < best_ov && d <= legacy_dev + 1e-6) { best_ov = n; best_snap = take(); }
            if (!seen.insert(state_hash()).second) break;   // repeated state — converged/cycle
        }
    }
    restore(best_snap);
}



// Junction infeasibility (Part B): a junction edge is infeasible when the
// landing segment's entire feasible centre range (its hard interval, i.e. the
// slide window) is DISJOINT from the spanning partner's NOMINAL span — the
// junction then closes only by stretching the partner beyond what the topology
// intended (the bundle-48 class).  Derived from the FINAL accepted state (the
// selected topologies' seg_conns — the one-true-source junction records — and
// the result's placed segments), never collected during placement: solve_layer
// also runs in tentative paths (orientation_fixpoint sweeps that are restored,
// dogleg trials, repair passes), and a signal gathered there would report
// compromises that the accepted placement no longer has.  Rewrites
// result.junction_infeasibilities, so rerun_layer refreshes stale entries too.
// #58 loud failure: an out-of-bbox trunk (>=2 SEG conns, no busterm) whose
// nominal perpendicular coordinate falls OUTSIDE the routable design boundary
// (the Hanan grid extent, extended by any EXPLICIT detour_channel — the same
// extension extract_segments seeds the interval with).  Such a trunk's window
// collapses (empty/inverted), so it cannot be seated and its bits strand at
// detailed NUTS.  The abstract-NUTS "0 overlaps" headline hides that, so we
// surface it loudly here and record it on the result (a pinned/ripup-promoted
// OOB trunk that needs a wider channel or a different topology).
static void derive_unseatable_trunks(
        const std::vector<BundleWrapper>& bundles,
        const Floorplan& floorplan,
        const std::vector<int>& x_grid,
        const std::vector<int>& y_grid,
        NUTSResult& result) {
    result.unseatable_trunks.clear();
    if (x_grid.empty() || y_grid.empty()) return;
    const DetourChannelSpec& dc = floorplan.get_detour_channel();
    const double x_lo = x_grid.front() - (dc.west  >= 0 ? dc.west  : 0);
    const double x_hi = x_grid.back()  + (dc.east  >= 0 ? dc.east  : 0);
    const double y_lo = y_grid.front() - (dc.south >= 0 ? dc.south : 0);
    const double y_hi = y_grid.back()  + (dc.north >= 0 ? dc.north : 0);
    for (const auto& bw : bundles) {
        const int sel = bw.plan.selected_topology_index;
        if (sel < 0 || sel >= (int)bw.input.candidates.size()) continue;
        const Topology& topo = bw.input.candidates[sel];
        const int bid = bw.input.original_bundle.id;
        ConnTopology ct;
        ct.build(topo, floorplan);
        const auto& conn_segs = ct.segs();
        for (int si = 0; si < (int)conn_segs.size() &&
                         si < (int)topo.segments.size(); ++si) {
            const ConnSeg& cs = conn_segs[si];
            int n_seg = 0, n_bt = 0;
            for (const auto& c : cs.conns) {
                if (c.kind == SegConn::SEG) ++n_seg; else ++n_bt;
            }
            if (!(n_seg >= 2 && n_bt == 0)) continue;   // trunk predicate (nuts.cpp)
            const Segment& seg = topo.segments[si];
            const bool is_h = (seg.start.y == seg.end.y);
            const double nom = is_h ? (double)seg.start.y : (double)seg.start.x;
            const double lo  = is_h ? y_lo : x_lo;
            const double hi  = is_h ? y_hi : x_hi;
            if (nom >= lo && nom <= hi) continue;        // seatable within boundary
            const double bound = (nom < lo) ? lo : hi;
            result.unseatable_trunks.push_back({bid, si, is_h, nom, bound});
            std::cout << "[NUTS] WARNING: bundle " << bid << " seg " << si
                      << " is an out-of-bbox trunk at " << (is_h ? "y=" : "x=")
                      << nom << ", beyond the routable boundary ["
                      << lo << ".." << hi << "] — it cannot be seated and its "
                      << "bits will strand at detailed NUTS; widen "
                      << "detour_channel or re-select this bundle's topology.\n";
        }
    }
}

static void derive_junction_infeasibilities(
        const std::vector<BundleWrapper>& bundles, NUTSResult& result) {
    result.junction_infeasibilities.clear();
    std::map<std::pair<int,int>, const TrackSegment*> ts_map;
    for (const auto& ts : result.segments)
        ts_map[{ts.bundle_id, ts.seg_idx}] = &ts;
    std::set<std::tuple<int,int,int>> seen;   // dedup the two edge directions
    for (const auto& bw : bundles) {
        const int sel = bw.plan.selected_topology_index;
        if (sel < 0 || sel >= (int)bw.input.candidates.size()) continue;
        const Topology& topo = bw.input.candidates[sel];
        const int bid = bw.input.original_bundle.id;
        for (const auto& [key, partners] : topo.seg_conns) {
            const int a_si = key.first;
            auto at = ts_map.find({bid, a_si});
            if (at == ts_map.end() || !at->second->placed) continue;
            const TrackSegment* a = at->second;
            const double c_lo = a->interval_lo + a->width / 2.0;
            const double c_hi = a->interval_hi - a->width / 2.0;
            if (c_lo > c_hi) continue;   // bus wider than interval: no window
            for (int b_si : partners) {
                if (b_si < 0 || b_si >= (int)topo.segments.size()) continue;
                auto bt = ts_map.find({bid, b_si});
                if (bt == ts_map.end() || !bt->second->placed) continue;
                const TrackSegment* b = bt->second;
                if (a->horiz == b->horiz) continue;   // junctions are perpendicular
                const Segment& bs = topo.segments[b_si];
                const double n1 = b->horiz ? bs.start.x : bs.start.y;
                const double n2 = b->horiz ? bs.end.x   : bs.end.y;
                if (c_hi >= std::min(n1, n2) && c_lo <= std::max(n1, n2))
                    continue;   // window reaches the partner's nominal span
                auto k = std::make_tuple(bid, std::min(a_si, b_si),
                                         std::max(a_si, b_si));
                if (!seen.insert(k).second) continue;
                result.junction_infeasibilities.push_back({bid, a_si, b_si});
                std::cout << "[NUTS] junction infeasible: bundle " << bid
                          << " seg " << a_si << " cannot reach seg " << b_si
                          << " within its slide window (closed by partner"
                          << " stretch; ripup may re-pin).\n";
            }
        }
    }
}

NUTSResult NUTSEngine::run(const std::vector<BundleWrapper>& bundles_in) {
    // Per-pass profiling (RR round-3 Phase 0; tail buckets round 5):
    // accumulate each stage's seconds so `pass_seconds` answers "where does
    // a solve's wall go" — including the stages OUTSIDE the solve lambda
    // (grid derivation, junction/keepout audits, reporting), which
    // dominate a fixed-context screen's ~ms run.  Observation only; no
    // placement decision reads it.
    std::map<std::string, double> pass_t;
    int n_solves = 0;
    using pclock = std::chrono::steady_clock;
    auto charge = [&pass_t](const char* key, pclock::time_point& t0) {
        auto t1 = pclock::now();
        pass_t[key] += std::chrono::duration<double>(t1 - t0).count();
        t0 = t1;
    };
    auto t_run = pclock::now();

    std::vector<int> x_grid, y_grid;
    floorplan_.get_hanan_grid(x_grid, y_grid);
    merge_grid(x_grid, extra_x_);
    merge_grid(y_grid, extra_y_);

    // The hier flow keeps geometry in the expanded bundle wrappers, not in the
    // flat Floorplan, so the Hanan grid (and the planner's extra grids) can be
    // empty here.  Derive a fallback grid from the bundles' own selected-topology
    // segment coordinates so interval bounds (the chip extent) are well-defined
    // and extract_segments never dereferences an empty grid.
    if (x_grid.empty() || y_grid.empty()) {
        std::set<int> xs, ys;
        for (const auto& bw : bundles_in) {
            if (bw.input.candidates.empty() || bw.plan.selected_topology_index < 0)
                continue;
            int idx = bw.plan.selected_topology_index;
            if (idx >= (int)bw.input.candidates.size()) continue;
            for (const auto& seg : bw.input.candidates[idx].segments) {
                xs.insert(seg.start.x); xs.insert(seg.end.x);
                ys.insert(seg.start.y); ys.insert(seg.end.y);
            }
        }
        if (x_grid.empty()) x_grid.assign(xs.begin(), xs.end());
        if (y_grid.empty()) y_grid.assign(ys.begin(), ys.end());
    }

    charge("grid", t_run);

    // One full placement of a (possibly dogleg-mutated) bundle set: extract
    // segments, build maps, run the orientation fixpoint, classify cycles, then
    // the repair/corner safety net.  Returned so the dogleg pass can re-place.
    auto solve = [&](const std::vector<BundleWrapper>& bs,
                     const std::map<int, LayerConstraints>& seed_cons) -> DoglegSolveOut {
        ++n_solves;
        auto t0 = pclock::now();
        NUTSResult result;
        result.segments = extract_segments(bs, x_grid, y_grid);
        charge("extract", t0);
        NutsContext ctx = build_context(bs, floorplan_, result.segments);
        std::map<int, std::vector<TrackSegment*>> by_layer;
        for (auto& ts : result.segments)
            by_layer[ts.layer].push_back(&ts);
        charge("context", t0);
        // Alternating orientation-group fixpoint: solve a whole orientation
        // group, propagate spans to the perpendicular group, solve it, propagate
        // back, and iterate — so each group packs against the other's already-
        // stretched spans instead of stale ones.  Replaces the per-layer loop.
        orientation_fixpoint(result.segments, by_layer, ctx, seed_cons);
        result.overlaps_post_fixpoint =
            (int)find_overlaps(result.segments).size();
        charge("fixpoint", t0);
        // Classify any genuinely cyclic vertical constraint NOW, on the raw
        // post-fixpoint overlaps: a 2-cycle shows both contradictory column
        // overlaps at once.  The corner pass below would half-resolve it —
        // fixing one column and leaving a single residual — which hides the
        // mutual-edge structure.  The 2-cycle filter guarantees the safety-net
        // passes could not have fixed these anyway.
        DoglegSolveOut out;
        out.plans = detect_dogleg_plans(result.segments, ctx.rev_conn_map, ctx.trunk_set);
        charge("dogleg_detect", t0);
        // The final adjustments can extend spans of layers packed earlier,
        // materialising overlaps after their solve — repair them in place.
        repair_overlaps(result.segments, ctx);
        charge("repair", t0);
        // Corner overlaps (perp-locked stubs colliding) need trunk adjustment,
        // not victim moves — resolve via same-layer ordering or cross-layer
        // split bounds.
        resolve_corner_overlaps(result.segments, ctx);
        charge("corner", t0);
        // Final opportunistic tighten: slide pulled segments toward their pull in
        // the settled layout (the sweep/repack only ever placed them by local
        // decisions and never revisited them when space opened next to the pull).
        // Fast-trial mode skips it: WL-only and overlap-non-increasing, so the
        // reported metric is an upper bound on the full solve's (see setter).
        if (!skip_tighten_)
            tighten_pulls(result.segments, ctx);
        charge("tighten", t0);
        // Collapse any extend-only phantom span tail now that placement has
        // converged — the last span mutation before metrics (Option B, see
        // relay_jog_phantom_span_2026-07.md).
        tighten_spans_to_reach(result.segments, ctx);
        // Merge the fixed (bottom-up copy) segments AFTER every pass that
        // could move a segment and BEFORE the metrics, so they are immovable
        // but fully accounted (an overlap against a fixed segment counts).
        result.segments.insert(result.segments.end(),
                               fixed_segments_.begin(), fixed_segments_.end());
        compute_metrics(result);
        charge("metrics", t0);
        out.result = std::move(result);
        return out;
    };

    // Copy-on-first-dogleg (rnr runtime N2): doglegs mutate topologies, so
    // the fallback needs a MUTABLE copy of the whole wrapper list — a deep
    // copy (every candidate topology of every bundle) that used to be paid
    // on EVERY non-screen solve.  The overwhelmingly common solve — a
    // healer trial re-solving a stable selection — detects no cycle plans,
    // so the first solve now runs straight from the caller's list (solve
    // takes const&) and the clone is made only when the fallback actually
    // has plans to apply.  run_dogleg_fallback's loop is gated on
    // !out.plans.empty(), so skipping the call entirely on empty plans is
    // behavior-identical.
    DoglegSolveOut out = solve(bundles_in, {});
    pass_t["bundle_copy"];   // keep the profile schema stable: 0.0 when the
                             // copy-on-first-dogleg path skips the clone
    std::vector<BundleWrapper> bundles_mut;
    bool copied = false;
    std::set<int> doglegged_bids;
    if (!skip_doglegs_ && !out.plans.empty()) {
        auto t_copy = pclock::now();
        bundles_mut = bundles_in;          // mutable: doglegs edit topologies
        copied = true;
        charge("bundle_copy", t_copy);
        // Dogleg fallback (nuts_dogleg.cpp): a genuine vertical-constraint
        // cycle survived the corner pass — split one trunk on the cycle
        // across two tracks joined by a jog and re-solve; returns the
        // mutated bundle ids.  Screen mode skips it: a screen's result is
        // discarded (candidate ordering only), so the topology surgery
        // must not happen (see setter).
        doglegged_bids = run_dogleg_fallback(bundles_mut, out, solve,
                                             track_pitch_, floorplan_);
    }
    const std::vector<BundleWrapper>& bundles =
        copied ? bundles_mut : bundles_in;

    // Export the dogleg-mutated topologies so the CLI can adopt them before it
    // rebuilds ConnTopology for detailed NUTS — otherwise the split bundle's
    // stubs keep their stale (pre-split) connectivity and detailed NUTS routes
    // them with corrupted spans.
    for (int bid : doglegged_bids)
        for (const auto& bw : bundles)
            if (bw.input.original_bundle.id == bid &&
                bw.plan.selected_topology_index >= 0 &&
                bw.plan.selected_topology_index < (int)bw.input.candidates.size()) {
                out.result.dogleg_topologies[bid] =
                    bw.input.candidates[bw.plan.selected_topology_index];
                out.result.dogleg_seg_layers[bid]    = bw.plan.seg_layers;
                out.result.dogleg_seg_net_pull[bid]  = bw.plan.seg_net_pull;
                out.result.dogleg_seg_perp[bid]      = bw.plan.seg_perp;
                out.result.dogleg_seg_slide_lo[bid]  = bw.plan.seg_slide_lo;
                out.result.dogleg_seg_slide_hi[bid]  = bw.plan.seg_slide_hi;
            }

    // Junction infeasibilities: derived from the FINAL accepted state (the
    // adopted bundle list — dogleg-split topologies included — against the
    // winning result), one line per distinct edge, like planner overflow, so
    // a compromised junction is never silent.
    auto t_tail = pclock::now();
    derive_junction_infeasibilities(bundles, out.result);
    // #58: flag any selected OOB trunk whose home is beyond the routable
    // boundary (Hanan extent + explicit detour_channel) — unseatable as pinned.
    derive_unseatable_trunks(bundles, floorplan_, x_grid, y_grid, out.result);
    charge("junctions", t_tail);
    // Keepout conflicts: report-only, so a bus committed onto a keepout by the
    // exhausted-window centre fallback is never silent (keepout-model audit).
    out.result.num_keepout_conflicts =
        count_keepout_conflicts(low_keepouts(), out.result.segments);
    charge("keepout_audit", t_tail);
    // Track overlaps first: it is the headline health metric, so it stays
    // visible even when a terminal/summary truncates the tail of this line.
    std::cout << "[NUTS] " << out.result.segments.size() << " segments placed. "
              << "Track overlaps: " << out.result.num_overlaps << ", "
              << "Interval violations: " << out.result.num_violations << ".\n";
    if (out.result.num_keepout_conflicts > 0)
        std::cout << "[NUTS] WARNING: " << out.result.num_keepout_conflicts
                  << " segment(s) placed ON a keepout (window exhausted; the "
                  << "bus cannot physically route there — re-pin or re-plan).\n";
    if (!out.result.unseatable_trunks.empty())
        std::cout << "[NUTS] WARNING: " << out.result.unseatable_trunks.size()
                  << " out-of-bbox trunk(s) unseatable within the routable "
                  << "boundary (detour_channel too small or absent; bits will "
                  << "strand at detailed NUTS — widen the channel or re-select).\n";
    charge("report", t_tail);
    // Stamp the accumulated per-pass profile onto the FINAL result (the
    // fallback's trial re-solves are already folded into the buckets).
    out.result.pass_seconds = std::move(pass_t);
    out.result.n_solves     = n_solves;
    return out.result;
}

NUTSResult NUTSEngine::rerun_layer(
    const NUTSResult&                  prev,
    const std::vector<BundleWrapper>&  bundles,
    int                                layer_id) const
{
    std::vector<int> x_grid, y_grid;
    floorplan_.get_hanan_grid(x_grid, y_grid);
    merge_grid(x_grid, extra_x_);
    merge_grid(y_grid, extra_y_);
    NUTSResult result = prev;
    // Hold the fixed (bottom-up copy) segments aside: they must be neither
    // reset nor re-placed, and build_context must not map them (a repair pass
    // could otherwise pick one as a victim).  Their derived zones still block
    // the re-solve via solver_keepouts(); re-appended before the metrics.
    std::vector<TrackSegment> fixed_held;
    if (!fixed_bundle_ids_.empty()) {
        auto& segs = result.segments;
        auto mid = std::stable_partition(segs.begin(), segs.end(),
            [&](const TrackSegment& t) {
                return fixed_bundle_ids_.count(t.bundle_id) == 0;
            });
        fixed_held.assign(mid, segs.end());
        segs.erase(mid, segs.end());
    }
    for (auto& ts : result.segments) {
        if (ts.layer != layer_id) continue;
        ts.track_position = std::numeric_limits<double>::quiet_NaN();
        ts.placed         = false;
        // This layer is about to be re-solved unconstrained (resolve_corner_
        // overlaps is intentionally not run here), so any cross-layer split
        // bound from the previous full solve is now stale — clear it, or
        // detailed NUTS would filter these trunks to an obsolete side.
        ts.track_lo_bound = -kInf;
        ts.track_hi_bound =  kInf;
        // Re-seed the FRESH interval baseline (full grid extent, exactly as
        // the initial extraction does): prev's intervals were already
        // clamped by the previous solve's prep, and the trunk-margin shrink
        // in apply_interval_constraints is NOT idempotent — re-prepping the
        // prepped interval shrank every trunk's hard window by another 20%
        // per invocation (0.8^n), silently diverging from the full solve's
        // constraints until the pinched windows forced overlaps a repair
        // could not clear (audit C1-01).  build_context(only_layer) below
        // then applies the prep to this layer exactly once.
        // Same detour-channel extension the initial extract_segments applies
        // (#58): without it a re-solved layer loses the reserved band and a
        // detour-seated OOB trunk becomes unplaceable again.  Gated on an
        // explicit channel side; bounded segments re-tighten in the prep below.
        const DetourChannelSpec& dc = floorplan_.get_detour_channel();
        if (ts.horiz) {
            if (!y_grid.empty()) {
                ts.interval_lo = static_cast<double>(y_grid.front());
                ts.interval_hi = static_cast<double>(y_grid.back());
                if (dc.south >= 0) ts.interval_lo -= dc.south;
                if (dc.north >= 0) ts.interval_hi += dc.north;
            }
        } else if (!x_grid.empty()) {
            ts.interval_lo = static_cast<double>(x_grid.front());
            ts.interval_hi = static_cast<double>(x_grid.back());
            if (dc.west >= 0) ts.interval_lo -= dc.west;
            if (dc.east >= 0) ts.interval_hi += dc.east;
        }
    }
    NutsContext ctx = build_context(bundles, floorplan_, result.segments, layer_id);
    std::vector<TrackSegment*> layer_segs;
    for (auto& ts : result.segments)
        if (ts.layer == layer_id) layer_segs.push_back(&ts);
    solve_layer(layer_segs, ctx, {});
    // Final pass for all layers to catch cross-layer adjustments from the re-solved layer.
    settle_spans(result.segments, ctx);
    repair_overlaps(result.segments, ctx);
    // Note: resolve_corner_overlaps is NOT run here.  It re-solves whole trunk
    // layers, which may differ from layer_id — that would violate rerun_layer's
    // single-layer contract.  Corner overlaps are resolved by the full run().
    // Tighten only this layer's pulled segments toward their pull bound (the
    // overlap / wirelength guards stay global, so cross-layer spans are honoured)
    // — keeps the single-layer contract while still recovering wirelength.
    tighten_pulls(result.segments, ctx, layer_id);
    tighten_spans_to_reach(result.segments, ctx);   // last span mutation before metrics
    result.segments.insert(result.segments.end(),
                           fixed_held.begin(), fixed_held.end());
    compute_metrics(result);
    result.num_keepout_conflicts =
        count_keepout_conflicts(low_keepouts(), result.segments);
    // Refresh the junction-infeasibility signal from the re-solved state: the
    // copy from prev may hold edges the rerun just fixed (stale) or miss ones
    // it introduced (dropped) — derive, don't inherit.
    derive_junction_infeasibilities(bundles, result);
    std::cout << "[NUTS] rerun_layer(" << layer_id << "): "
              << layer_segs.size() << " segment(s) re-placed. "
              << "Violations: " << result.num_violations << ", "
              << "Overlaps: " << result.num_overlaps << ".\n";
    return result;
}

std::optional<std::vector<std::array<int, 3>>> NUTSEngine::screen_candidates(
    const std::vector<BundleWrapper>& bundles_in,
    int target_bid,
    const std::vector<int>& tidxs,
    CongestionPlanner& planner,
    bool clear_dogleg_overrides)
{
    // One conversion + copy of the wrapper list for the WHOLE contender —
    // the per-candidate Python loop paid it per replan_bundle call, and at
    // the screen's ~ms budget that conversion dominated.  All mutations
    // land on this copy; the caller's wrappers are never touched.
    std::vector<BundleWrapper> bundles = bundles_in;
    BundleWrapper* w = nullptr;
    for (auto& bw : bundles)
        if (bw.input.original_bundle.id == target_bid) { w = &bw; break; }
    if (!w) return std::nullopt;
    if (clear_dogleg_overrides) {
        // Same hazard as _rr_trial: a dogleg-adopted target's per-segment
        // overrides index its split topology, not the screened candidate.
        w->plan.seg_net_pull.clear();
        w->plan.seg_slide_lo.clear();
        w->plan.seg_slide_hi.clear();
    }
    // Layers for ALL candidates in one planner call: the O(all bundles)
    // committed-usage recharge inside replan_bundle was the dominant
    // per-screen cost, and it is identical across one contender's
    // candidates — replan_candidates pays it once (no commits, so every
    // candidate scores against the same others-only usage, exactly as the
    // per-call sequence did).
    auto asns = planner.replan_candidates(bundles, target_bid, tidxs);
    if (!asns)
        return std::nullopt;       // caller falls back to unscreened order
    std::vector<std::array<int, 3>> out;
    out.reserve(tidxs.size());
    for (size_t i = 0; i < tidxs.size(); ++i) {
        const BundleAssignment& asn = (*asns)[i];
        w->plan.selected_topology_index = asn.topo_index;
        w->input.topology_pinned = true;
        w->input.assigned_v_layer = asn.v_layer_id;
        w->input.assigned_h_layer = asn.h_layer_id;
        w->plan.seg_layers = asn.seg_layers;
        w->plan.seg_perp = asn.seg_perp;
        NUTSResult res = run(bundles);
        out.push_back({tidxs[i], res.num_overlaps, res.num_violations});
    }
    return out;
}

NUTSResult NUTSEngine::rerun_bundle_warm(
    const NUTSResult&                  prev,
    const std::vector<BundleWrapper>&  bundles,
    int                                target_bid) const
{
    // Phase 1 — place ONLY the target against the frozen baseline: a scratch
    // engine copy carries the frozen zones (this method stays const and the
    // real engine's fixed set untouched).  extract_segments skips fixed
    // bundles, so run(bundles) extracts JUST the target — full grid parity
    // included, since the whole bundle list feeds run()'s fallback-grid
    // derivation — places it (orientation fixpoint over the target's own
    // segments only; every other segment is a keepout zone), and appends the
    // frozen context verbatim.  Doglegs skipped: a warm state must never
    // export topology surgery.  Tighten is deferred to phase 2 (a
    // target-only tighten against zones would be discarded work).
    NUTSEngine scratch(*this);
    scratch.set_skip_doglegs(true);
    scratch.set_skip_tighten(true);
    // The scratch copy INHERITS this engine's fixed set (bottom-up copies),
    // and `prev` holds the SAME segments again — run() appends fixed
    // segments to every result.  Freeze from prev only what is not already
    // fixed here, or the scratch would carry duplicate rows and zones and
    // the warm metrics would be unusable on bottom-up sessions (Codex
    // #296).  The inherited copies stay truly fixed in phase 1; in the
    // phase-2 union they are ordinary segments the safety passes may move
    // — acceptable for a discarded predictor, never a committed state.
    std::vector<TrackSegment> freeze;
    freeze.reserve(prev.segments.size());
    for (const auto& s : prev.segments)
        if (s.bundle_id != target_bid && s.placed &&
            !std::isnan(s.track_position) &&
            !fixed_bundle_ids_.count(s.bundle_id))
            freeze.push_back(s);
    scratch.add_fixed_segments(freeze);
    NUTSResult warm = scratch.run(bundles);

    // Phase 2 — unfreeze and run the safety passes on the REAL union, so
    // neighbours can adjust to the target's new position exactly as in a
    // full solve: settle cross-layer spans, repair materialised overlaps,
    // resolve corner overlaps (trunk ordering / split bounds), and tighten
    // unless the engine is in fast-trial mode.  No orientation fixpoint —
    // the baseline seed replaces the from-scratch proactive ordering, which
    // is the entire point: the passes' work now scales with the move's
    // blast radius, not the design.  prep=false: both sides' intervals are
    // already prepped (see build_context) and the trunk-margin shrink is
    // not idempotent.
    NutsContext ctx = build_context(bundles, floorplan_, warm.segments,
                                    /*only_layer=*/-1, /*prep=*/false);
    settle_spans(warm.segments, ctx);
    repair_overlaps(warm.segments, ctx);
    resolve_corner_overlaps(warm.segments, ctx);
    if (!skip_tighten_)
        tighten_pulls(warm.segments, ctx);
    tighten_spans_to_reach(warm.segments, ctx);   // last span mutation before metrics
    compute_metrics(warm);
    warm.num_keepout_conflicts =
        count_keepout_conflicts(low_keepouts(), warm.segments);
    derive_junction_infeasibilities(bundles, warm);
    return warm;
}

} // namespace buda
