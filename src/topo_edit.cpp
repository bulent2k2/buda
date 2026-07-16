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

#include "topo_edit.h"
#include "conn_topology.h"
#include <algorithm>
#include <cstdlib>
#include <functional>
#include <set>
#include <vector>

namespace buda {

namespace {

EditVerdict fail(std::string note) {
    EditVerdict v;
    v.applied = false;
    v.note = std::move(note);
    return v;
}

// Post-mutation transaction tail: re-derive junctions from the (already
// tap-annotated) geometry, then judge the result — check_topo for opens /
// face violations / relays, plus the zero-slide pinch the generator's
// filter_pinched would reject.
EditVerdict finish(Topology& topo, const Floorplan& fp,
                   std::string note, int new_idx) {
    annotate_seg_conns(topo);
    EditVerdict v;
    v.applied = true;
    v.note = std::move(note);
    v.seg_idx = new_idx;
    ConnTopology ct;
    ct.build(topo, fp);
    v.conn = check_topo(ct, topo, fp, -1);
    for (const auto& cs : ct.segs())
        if (cs.perp_lo == cs.perp_hi) { v.pinched = true; break; }
    // SEG-graph components (see the EditVerdict field comment).
    const auto& segs = ct.segs();
    int n = (int)segs.size();
    std::vector<int> root(n);
    for (int k = 0; k < n; ++k) root[k] = k;
    std::function<int(int)> find = [&](int x) {
        return root[x] == x ? x : root[x] = find(root[x]);
    };
    for (int k = 0; k < n; ++k)
        for (const auto& cn : segs[k].conns)
            if (cn.kind == SegConn::SEG)
                root[find(k)] = find(cn.seg_idx);
    std::set<int> comps;
    for (int k = 0; k < n; ++k) comps.insert(find(k));
    v.components = (int)comps.size();
    return v;
}

bool seg_is_horiz(const Segment& s) { return s.start.y == s.end.y; }

// Does the (seg_idx, endpoint) carry a busterm tap?
bool endpoint_tapped(const Topology& topo, int si, int ep) {
    auto it = topo.seg_busterms.find(si);
    if (it == topo.seg_busterms.end()) return false;
    return ep == 0 ? it->second.first.has_value()
                   : it->second.second.has_value();
}

} // namespace

EditVerdict edit_add_trunk(Topology& topo, const Floorplan& fp, bool horiz,
                           int perp_pos, int along_lo, int along_hi, int layer) {
    std::vector<int> xs, ys;                    // one grid derivation, reused below
    fp.get_hanan_grid(xs, ys);
    if (along_lo > along_hi) {
        // Default: full span = the Hanan grid's extent on this axis.
        const std::vector<int>& g = horiz ? xs : ys;
        if (g.size() < 2)
            return fail("floorplan has no Hanan extent to span");
        along_lo = g.front();
        along_hi = g.back();
    }
    if (along_lo >= along_hi)
        return fail("degenerate trunk span");
    // Reject an EXACT geometric duplicate (same orientation + line + span) of
    // an existing segment — the repeated-Y slip that silently stacks identical
    // trunks.  Only the exact dup is refused: two trunks on the SAME line with
    // different spans are a legitimate pattern (disjoint spans each covering a
    // block pair; re-span with P / edit_set_span).
    for (size_t i = 0; i < topo.segments.size(); ++i) {
        const Segment& t = topo.segments[i];
        if (seg_is_horiz(t) != horiz) continue;
        const int t_perp = horiz ? t.start.y : t.start.x;
        if (t_perp != perp_pos) continue;
        const int t_lo = horiz ? std::min(t.start.x, t.end.x)
                               : std::min(t.start.y, t.end.y);
        const int t_hi = horiz ? std::max(t.start.x, t.end.x)
                               : std::max(t.start.y, t.end.y);
        if (t_lo == along_lo && t_hi == along_hi)
            return fail("identical trunk already exists (seg "
                        + std::to_string(i) + ") — re-span it with "
                        "edit_set_span/P, or remove it (X) first");
    }
    Segment s;
    s.start = horiz ? Point{along_lo, perp_pos} : Point{perp_pos, along_lo};
    s.end   = horiz ? Point{along_hi, perp_pos} : Point{perp_pos, along_hi};
    s.layer_hint = layer;
    // For an OUT-OF-BOUNDS trunk — one placed BEYOND the extreme Hanan line on
    // the perp axis (e.g. a detour spine sitting to the side of every block) —
    // seed its slide with the half-open Hanan cell: bound the block-facing side
    // at the nearest grid line, leave the outward side free.  Without this an
    // OOB trunk is a floating relay with a fully-unbounded perp range, so NUTS
    // has no finite pull target (it falls back to the nominal position) and
    // downstream pull inference sees no interval for its neighbours (a stub
    // tapping it can't tell it shortens the trunk) — the detour never tightens
    // toward the blocks.  Scoped to the OOB case: an INSIDE-grid trunk keeps its
    // free range (its stubs bound it via the Pass-1/2 push-out, and it may need
    // to slide across cells to align), and NO generated topology is touched
    // (only edit_add_trunk).  The user refines with `W`, which overrides via
    // seg_slide.
    {
        const std::vector<int>& g = horiz ? ys : xs;   // perp-axis grid lines
        int lo = INT_MIN, hi = INT_MAX;
        for (int c : g) {                              // g is sorted ascending
            if (c < perp_pos) lo = c;                  // nearest line below
            else if (c > perp_pos) { hi = c; break; }  // nearest line above
        }
        const bool has_lo = (lo != INT_MIN), has_hi = (hi != INT_MAX);
        if (has_lo != has_hi) {                        // OOB: exactly one side
            if (has_hi) s.perp_clamp_hi = hi;          // trunk below the blocks
            else        s.perp_clamp_lo = lo;          // trunk above the blocks
        }
    }
    int idx = (int)topo.segments.size();
    topo.segments.push_back(s);
    return finish(topo, fp, "trunk added", idx);
}

EditVerdict edit_remove_segment(Topology& topo, const Floorplan& fp, int seg_idx) {
    if (seg_idx < 0 || seg_idx >= (int)topo.segments.size())
        return fail("segment index out of range");
    erase_segment(topo, seg_idx);
    return finish(topo, fp, "segment removed", -1);
}

EditVerdict edit_add_stub(Topology& topo, const Floorplan& fp,
                          const std::string& block, int to_seg, int layer) {
    if (to_seg < 0 || to_seg >= (int)topo.segments.size())
        return fail("target segment index out of range");
    if (!fp.has_block(block))
        return fail("unknown block '" + block + "'");
    const Segment& j = topo.segments[to_seg];
    const bool j_horiz = seg_is_horiz(j);
    const Rect bb = fp.get_block_bounds(block);

    // The stub is perpendicular to the target: its along-position must fall in
    // the overlap of the block's extent and the target's span on the target's
    // axis; take the overlap centre (an expert can refine with edit_set_span).
    int j_lo = j_horiz ? std::min(j.start.x, j.end.x) : std::min(j.start.y, j.end.y);
    int j_hi = j_horiz ? std::max(j.start.x, j.end.x) : std::max(j.start.y, j.end.y);
    int b_lo = j_horiz ? bb.x1 : bb.y1;
    int b_hi = j_horiz ? bb.x2 : bb.y2;
    int o_lo = std::max(j_lo, b_lo), o_hi = std::min(j_hi, b_hi);
    if (o_lo > o_hi)
        return fail("block and target segment do not overlap on the stub axis");
    const int at = (o_lo + o_hi) / 2;

    const int j_perp = j_horiz ? j.start.y : j.start.x;
    const int face = j_horiz ? bb.face_y(j_perp) : bb.face_x(j_perp);
    if (face == j_perp)
        return fail(j_perp > (j_horiz ? bb.y1 : bb.x1) &&
                    j_perp < (j_horiz ? bb.y2 : bb.x2)
                        ? "target crosses the block (pass-through, no stub needed)"
                        : "zero-length stub (target touches the block face)");

    Segment s;
    s.start = j_horiz ? Point{at, face}   : Point{face, at};      // block end
    s.end   = j_horiz ? Point{at, j_perp} : Point{j_perp, at};    // junction end
    s.layer_hint = layer;
    int idx = (int)topo.segments.size();
    topo.segments.push_back(s);

    // Seed the tap exactly like the generators do: margin-inset bbox +
    // full extent + multi-rect + TEG, at the block-side endpoint (start).
    Busterm bt;
    bt.block_name = block;
    BlockCornerMargin cm = fp.get_block_corner_margin(block);
    bt.bbox      = bb.shrink(cm.dx, cm.dy);
    bt.orig_bbox = bb;
    bt.rects     = fp.get_block_rects(block);
    bt.teg_mode  = fp.get_block_teg_mode(block);
    topo.seg_busterms[idx].first = bt;

    // The edited topology must also claim the block, or check_topo won't
    // audit its coverage (idempotent when already present).
    if (std::find(topo.connected_block_names.begin(),
                  topo.connected_block_names.end(), block)
            == topo.connected_block_names.end())
        topo.connected_block_names.push_back(block);
    return finish(topo, fp, "stub added to '" + block + "'", idx);
}

EditVerdict edit_set_span(Topology& topo, const Floorplan& fp, int seg_idx,
                          int along_lo, int along_hi) {
    if (seg_idx < 0 || seg_idx >= (int)topo.segments.size())
        return fail("segment index out of range");
    if (along_lo >= along_hi)
        return fail("degenerate span");
    Segment& s = topo.segments[seg_idx];
    if (seg_is_horiz(s)) {
        s.start = Point{along_lo, s.start.y};
        s.end   = Point{along_hi, s.end.y};
    } else {
        s.start = Point{s.start.x, along_lo};
        s.end   = Point{s.end.x,   along_hi};
    }
    return finish(topo, fp, "span overridden", seg_idx);
}

EditVerdict edit_connect(Topology& topo, const Floorplan& fp, int i, int j) {
    int n = (int)topo.segments.size();
    if (i < 0 || i >= n || j < 0 || j >= n || i == j)
        return fail("segment index out of range");
    Segment& si = topo.segments[i];
    Segment& sj = topo.segments[j];
    const bool ih = seg_is_horiz(si), jh = seg_is_horiz(sj);
    if (ih == jh)
        return fail("segments are parallel — connect needs a perpendicular pair");
    // Crossing of the two (infinite) lines: i keeps its perp coordinate, and
    // its along coordinate becomes j's perp position (H i / V j → j's x;
    // V i / H j → j's y).
    const int target = ih ? sj.start.x : sj.start.y;
    // Which endpoint of i moves: the one nearer to the crossing along i's axis.
    const int a0 = ih ? si.start.x : si.start.y;
    const int a1 = ih ? si.end.x   : si.end.y;
    const int ep = (std::abs(a0 - target) <= std::abs(a1 - target)) ? 0 : 1;
    if (endpoint_tapped(topo, i, ep))
        return fail("endpoint is a busterm tap — use edit_set_span deliberately");
    Point& p = (ep == 0) ? si.start : si.end;
    const int other = (ep == 0) ? a1 : a0;
    if (other == target)
        return fail("connect would collapse the segment to zero length");
    p = ih ? Point{target, si.start.y} : Point{si.start.x, target};

    // Extend j to reach the crossing when it falls outside j's span (never
    // retract — that could break j's other junctions/taps silently).
    const int ic = ih ? si.start.y : si.start.x;              // crossing on j's axis
    int jlo = jh ? std::min(sj.start.x, sj.end.x) : std::min(sj.start.y, sj.end.y);
    int jhi = jh ? std::max(sj.start.x, sj.end.x) : std::max(sj.start.y, sj.end.y);
    if (ic < jlo || ic > jhi) {
        const int jep = std::abs((jh ? sj.start.x : sj.start.y) - ic)
                      <= std::abs((jh ? sj.end.x : sj.end.y) - ic) ? 0 : 1;
        if (endpoint_tapped(topo, j, jep))
            return fail("partner endpoint is a busterm tap — extend it with "
                        "edit_set_span first");
        Point& q = (jep == 0) ? sj.start : sj.end;
        q = jh ? Point{ic, sj.start.y} : Point{sj.start.x, ic};
    }
    return finish(topo, fp, "connected", i);
}

EditVerdict edit_disconnect(Topology& topo, const Floorplan& fp, int i, int j,
                            int retract_to) {
    int n = (int)topo.segments.size();
    if (i < 0 || i >= n || j < 0 || j >= n || i == j)
        return fail("segment index out of range");
    // Find which endpoint of i lands on j — from the authoritative records.
    int ep = -1;
    for (int e = 0; e < 2; ++e) {
        auto rec = topo.seg_conns.find({i, e});
        if (rec != topo.seg_conns.end()
                && std::find(rec->second.begin(), rec->second.end(), j)
                       != rec->second.end()) {
            ep = e;
            break;
        }
    }
    if (ep < 0)
        return fail("no junction between the two segments");
    if (endpoint_tapped(topo, i, ep))
        return fail("endpoint is a busterm tap, not a junction");
    Segment& si = topo.segments[i];
    const bool ih = seg_is_horiz(si);
    Point& p = (ep == 0) ? si.start : si.end;
    const Point& o = (ep == 0) ? si.end : si.start;
    const int cur = ih ? p.x : p.y;
    const int oth = ih ? o.x : o.y;
    if (retract_to == cur)
        return fail("retract_to equals the junction position — nothing moves");
    if (retract_to == oth)
        return fail("retract_to collapses the segment to zero length");
    p = ih ? Point{retract_to, p.y} : Point{p.x, retract_to};
    return finish(topo, fp, "disconnected", i);
}

EditVerdict edit_verdict(Topology& topo, const Floorplan& fp) {
    return finish(topo, fp, "status", -1);
}

} // namespace buda
