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

#include "verify.h"
#include <algorithm>
#include <cmath>
#include <functional>
#include <limits>
#include <map>
#include <numeric>
#include <set>
#include <sstream>

namespace buda {

// ── keepout audit (KEEPOUT_CROSS) ─────────────────────────────────────────────

// The zone list every keepout-aware stage tests against: user zones plus the
// implicit solid-leaf-cell zones on non-TOP layers — the same construction as
// NUTSEngine::low_keepouts, and layer-equivalent to the grid keepouts
// DetailedNUTS places against (leaf zones carry the non-TOP id set; user
// zones their declared set; empty layer_ids = blocks every layer).
static std::vector<KeepoutZone> keepout_zones(const Floorplan& fp,
                                              const LayerStack& layers)
{
    std::vector<int> low_ids;
    for (int lid : layers.get_layer_ids_by_dir(LayerDir::VERTICAL))
        if (!layers.is_top(lid)) low_ids.push_back(lid);
    for (int lid : layers.get_layer_ids_by_dir(LayerDir::HORIZONTAL))
        if (!layers.is_top(lid)) low_ids.push_back(lid);
    return fp.low_layer_keepouts(low_ids);
}

// True when the zone applies to `layer` (empty layer_ids = every layer).
static bool zone_on_layer(const KeepoutZone& koz, int layer) {
    return koz.layer_ids.empty() || koz.layer_ids.count(layer) > 0;
}

// ── helpers ───────────────────────────────────────────────────────────────────

// True when `pos` falls within the block face extent in the perpendicular axis.
//   H segment (horiz=true):  pos = y, checked against rect's [y1, y2].
//   V segment (horiz=false): pos = x, checked against rect's [x1, x2].
// Returns true if a rect of the block covers pos (multi-rect aware).  For a
// multi-rect block, only rects the wire's ALONG extent actually reaches may
// vouch for the perpendicular position (audit C9-02): an L-shaped block's
// arms can have disjoint perp ranges, and a wire whose track sits in rect
// A's range while its span only touches rect B is a physical miss — the
// any-rect test read it as on-face.  Along overlap is INCLUSIVE (a stub
// legitimately ends touching the face line).  along_lo/along_hi accept
// either order (placed spans may be inverted); pass an unbounded extent to
// keep the position-only semantics (single-rect blocks are unaffected).
static bool perp_in_block_face(double pos, bool horiz,
                                const std::string& block_name,
                                const Floorplan& fp,
                                double along_lo, double along_hi)
{
    const double a_lo = std::min(along_lo, along_hi);
    const double a_hi = std::max(along_lo, along_hi);
    auto rects = fp.get_block_rects(block_name);
    if (rects.empty()) {
        Rect bb = fp.get_block_bounds(block_name);
        return horiz ? (pos >= bb.y1 && pos <= bb.y2)
                     : (pos >= bb.x1 && pos <= bb.x2);
    }
    for (const Rect& r : rects) {
        bool ok = horiz ? (pos >= r.y1 && pos <= r.y2)
                        : (pos >= r.x1 && pos <= r.x2);
        bool reach = horiz ? (a_lo <= r.x2 && a_hi >= r.x1)
                           : (a_lo <= r.y2 && a_hi >= r.y1);
        if (ok && reach) return true;
    }
    return false;
}

// True when segment `cs` at perpendicular position `perp` passes through rect `r`.
// "Passes through" means: perp is inside r's perp-face range, AND the along span
// overlaps r's along range.
static bool seg_spans_rect(const ConnSeg& cs, double perp, const Rect& r) {
    if (cs.horiz)
        return perp >= r.y1 && perp <= r.y2
            && cs.along_lo <= r.x2 && cs.along_hi >= r.x1;
    else
        return perp >= r.x1 && perp <= r.x2
            && cs.along_lo <= r.y2 && cs.along_hi >= r.y1;
}

// FEEDTHRU_RELAY: a block must not be silently used as a feedthrough relay --
// every stub landing on a block's face must be wire-connected to the others.
//
// Connectivity here is GEOMETRIC (shared endpoint or perpendicular T-junction),
// NOT ConnTopology's SEG conns and NOT "both tap the block's busterm": relay
// completion keeps exactly ONE busterm tap per block (so a tag-based test is
// blind to a genuine relay), and ConnTopology suppresses SEG inference at a
// busterm-tagged endpoint.  We therefore gather a block's incident segments by
// geometry (an endpoint on its face) and union them by wire touch.  A block
// whose incident segments span more than one wire component is relaying the bus
// through its own body -- the open we forbid.
//
// Not flagged: a straight trunk / combined rail CROSSING a block has no endpoint
// on the block's face (it extends beyond), so it is not gathered; multi-rect/TEG
// and opt-in feedthru blocks carry intentional internal routing and are skipped.
// The nominal ConnSeg geometry is the same at every stage, so this structural
// check serves check_topo / check_nuts / check_dnuts uniformly.
static void detect_feedthru_relay(const std::vector<ConnSeg>& segs,
                                  const Topology& topo, const Floorplan& fp,
                                  int bundle_id, const char* stage,
                                  ConnResult& result)
{
    int n = (int)segs.size();
    if (n < 2) return;
    auto ep_lo = [](const ConnSeg& s) -> std::pair<int,int> {
        return s.horiz ? std::make_pair(s.along_lo, s.perp_pos)
                       : std::make_pair(s.perp_pos, s.along_lo);
    };
    auto ep_hi = [](const ConnSeg& s) -> std::pair<int,int> {
        return s.horiz ? std::make_pair(s.along_hi, s.perp_pos)
                       : std::make_pair(s.perp_pos, s.along_hi);
    };
    auto pt_on = [](int px, int py, const ConnSeg& s) -> bool {
        return s.horiz ? (py == s.perp_pos && px >= s.along_lo && px <= s.along_hi)
                       : (px == s.perp_pos && py >= s.along_lo && py <= s.along_hi);
    };
    auto touch = [&](const ConnSeg& a, const ConnSeg& b) -> bool {
        if (a.horiz == b.horiz && a.perp_pos == b.perp_pos) {
            // Collinear: count as connected only if the wires OVERLAP (share a
            // span), NOT if they merely butt end-to-end at a single point.
            // ConnTopology::infer_connections skips collinear pairs, so it never
            // creates a SEG join for a butt-joint; NUTS would then place the two
            // segments independently and they could separate.  Treating a butt-
            // joint as connected here would let detect_feedthru_relay miss that
            // open -- the collinear-join (defect 2) gap, now reachable via the
            // perpendicular abutment crossing meeting a regular edge end-to-end.
            return std::max(a.along_lo, b.along_lo) < std::min(a.along_hi, b.along_hi);
        }
        auto al = ep_lo(a), ah = ep_hi(a), bl = ep_lo(b), bh = ep_hi(b);
        return pt_on(al.first, al.second, b) || pt_on(ah.first, ah.second, b)
            || pt_on(bl.first, bl.second, a) || pt_on(bh.first, bh.second, a);
    };
    auto on_block_face = [](int px, int py, const Rect& r) -> bool {
        return ((px == r.x1 || px == r.x2) && py >= r.y1 && py <= r.y2)
            || ((py == r.y1 || py == r.y2) && px >= r.x1 && px <= r.x2);
    };

    std::vector<int> uf(n);
    std::iota(uf.begin(), uf.end(), 0);
    std::function<int(int)> find = [&](int x){ return uf[x]==x ? x : uf[x]=find(uf[x]); };
    for (int i = 0; i < n; ++i)
        for (int j = i + 1; j < n; ++j)
            if (touch(segs[i], segs[j])) uf[find(i)] = find(j);

    // Scan EVERY floorplan block, not just topo.connected_block_names (src+dsts):
    // a malformed route can relay two stubs through an unrelated macro that is not a
    // declared connected block (ConnTopology tags such incidental endpoints too).
    // The inc>=2 gate below means only blocks actually hosting >=2 incident stubs are
    // examined, so this stays cheap and flags genuine multi-component relays only.
    for (const auto& [bname, r] : fp.get_all_blocks()) {
        if (fp.is_container(bname)) continue;                     // transparent envelope, not a leaf cell
        if (fp.get_block_rects(bname).size() > 1) continue;       // TEG: internal eq. OK
        if (std::find(topo.feedthru_blocks.begin(), topo.feedthru_blocks.end(),
                      bname) != topo.feedthru_blocks.end()) continue;   // opt-in feedthru
        std::vector<int> inc;
        for (int i = 0; i < n; ++i) {
            auto lo = ep_lo(segs[i]), hi = ep_hi(segs[i]);
            if (on_block_face(lo.first, lo.second, r) || on_block_face(hi.first, hi.second, r))
                inc.push_back(i);
        }
        if (inc.size() < 2) continue;
        int root = find(inc[0]);
        for (size_t k = 1; k < inc.size(); ++k) {
            if (find(inc[k]) == root) continue;
            ConnViolation v;
            v.kind = ViolationKind::FEEDTHRU_RELAY;
            v.bundle_id = bundle_id;
            v.seg_idx = inc[0]; v.seg_idx2 = inc[(int)k];
            v.block_name = bname;
            v.message = std::string("Block '") + bname + "' used as feedthrough relay: seg "
                + std::to_string(inc[0]) + " and seg " + std::to_string(inc[(int)k])
                + " land on its face but their wires do not connect (" + stage + ")";
            result.violations.push_back(std::move(v));
            break;  // one violation per block suffices
        }
    }
}

// ── detect_disconnected ───────────────────────────────────────────────────────
//
// Whole-graph connectivity: the wire graph must be ONE island.  Union segments
// joined by a SEG junction record (the authoritative annotation NUTS holds
// together) and segments tapping the SAME block (through-block continuity: a
// driver/receiver hosting several landings, a declared feedthru's split spine,
// or a multi-rect block's internal routing — the ILLEGITIMATE through-block
// joint is flagged separately as FEEDTHRU_RELAY, so counting it here never
// hides it).  2+ components = the net cannot be electrically complete even
// though every block is tapped and every junction touches — e.g. a TopoEdit
// session that removed the only bridging stub and committed with comps=2.
// Structural (conn records, not placement), so one detector serves every stage.
// The island model itself, shared by detect_disconnected (verify) and the
// declared-feedthru scoping of generation's DISCONNECTED gate
// (disconnected_islands_bridged below) — ONE union-find, never forked.
// Returns the root island id per segment (path-compressed).
static std::vector<int> island_roots(const std::vector<ConnSeg>& segs)
{
    int n = (int)segs.size();
    std::vector<int> uf(n);
    std::iota(uf.begin(), uf.end(), 0);
    std::function<int(int)> find = [&](int x){ return uf[x]==x ? x : uf[x]=find(uf[x]); };
    std::map<std::string, int> block_seen;   // block name -> first tapping seg
    for (int i = 0; i < n; ++i) {
        for (const auto& conn : segs[i].conns) {
            if (conn.kind == SegConn::SEG) {
                uf[find(i)] = find(conn.seg_idx);
            } else if (conn.kind == SegConn::BUSTERM) {
                auto [it, fresh] = block_seen.emplace(conn.block_name, i);
                if (!fresh) uf[find(i)] = find(it->second);
            }
        }
    }
    for (int i = 0; i < n; ++i) uf[i] = find(i);
    return uf;
}

// ── detect_antennas ───────────────────────────────────────────────────────────
//
// A segment must be attached to the rest of the route at TWO points at least:
// a real wire runs BETWEEN things (block face -> junction, junction ->
// junction, face -> face).  A segment attached at one point dangles from
// there — electrically inert metal that no route needs (an "antenna": it
// loads the net with capacitance and can violate antenna rules), and a sure
// sign the generator emitted a segment it should not have.  The canonical
// producer (issue #482) is an MST edge leg laid COLLINEAR on top of a trunk
// stub leaving the same block face: the relay single-tap model demotes the
// duplicate landing to a nullopt junction, and ConnTopology cannot infer a
// junction between collinear segments, so the leg's near end connects to
// nothing.
//
// "Attached at two points" counts DISTINCT ATTACHMENT POSITIONS, not conn
// records, and includes the three ways a segment can be electrically joined:
//
//   * a BUSTERM tap            -> attaches at its face coordinate
//   * a SEG junction           -> attaches at `at_pos` along this segment
//   * a PASS-THROUGH block     -> the segment crosses a connected block's
//                                 footprint without tapping it (the joint the
//                                 block-coverage checks above accept as a
//                                 valid connection); each such block is one
//                                 attachment, keyed by name
//
// Both refinements matter (Codex review on #483).  Without the pass-through
// term a trunk that crosses its driver block and joins one junction would be
// reported though it is properly connected — a false positive on otherwise
// clean designs.  Without position dedup, a segment whose end meets a
// MULTI-WAY junction collects one SegConn per neighbour, all at the same
// at_pos, so a record count of 2+ would hide the very shape this detects —
// every attachment at one physical point and the opposite end free.
//
// Structural (conn records + nominal geometry), like detect_disconnected — the
// same detector answers at every placed stage.  DISCONNECTED is the
// complementary global property (the graph splits); an antenna keeps the graph
// connected, it just hangs off it.
SegAttachment seg_attachment(const ConnSeg& cs, const Topology& topo,
                             const Floorplan& fp)
{
    SegAttachment a;
    // Distinct attachment POSITIONS along this segment (conn records at one
    // physical point count once) ...
    for (const auto& c : cs.conns) {
        if (c.kind == SegConn::BUSTERM) { ++a.n_busterm; a.positions.insert(c.face_coord); }
        else                            { ++a.n_seg;     a.positions.insert(c.at_pos); }
    }
    // ... plus every connected block this segment merely CROSSES (a
    // pass-through joint, which carries no conn record).
    for (const auto& bname : topo.connected_block_names) {
        auto rects = fp.get_block_rects(bname);
        if (rects.empty()) rects.push_back(fp.get_block_bounds(bname));
        for (const Rect& r : rects)
            if (seg_spans_rect(cs, (double)cs.perp_pos, r)) {
                a.through.insert(bname);
                break;
            }
    }
    return a;
}

// ── detect_bit_antennas ──────────────────────────────────────────────────────
//
// detect_antennas asks "is this segment attached?" and reads the BUS-level
// ConnSeg graph to answer it.  Once a tree is per-bit TAPERED (fan-in /
// fan-OUT), that graph stops being the whole story: a trunk attached at all 33
// of its stubs is perfectly healthy as a bus segment, while an individual BIT
// on it may connect to only two of those stubs and carry the rest of the trunk
// as dead metal at both ends.  That is an antenna in every sense that matters —
// electrically inert wire hanging off the route — and nothing was looking for
// it (issue: flow/rv/soc_conv_div.buda bundle 1, where 73.5% of the vertical
// metal dangled with `check_design` reporting Success).
//
// So this pass asks the OTHER question, per placed bit-wire: does the metal
// extend past everything this bit actually reaches?  A bit's legitimate reach
// is the union of
//   * its VIAS — the real per-bit junctions, and
//   * its segment's BUSTERM face landings — a tap is a legitimate end even
//     though no via marks it, and
//   * any connected block the segment PASSES THROUGH — coverage the route
//     depends on, and exactly what adjust_bit_spans re-extends to.
// Anything beyond the outermost of those is attached to nothing.
static void detect_bit_antennas(const std::vector<ConnSeg>& segs,
                                const DetailedNUTSResult& dnuts,
                                const Topology& topo, const Floorplan& fp,
                                const LayerStack& layers, int bundle_id,
                                ConnResult& result)
{
    const int n = (int)segs.size();
    if (n < 2) return;               // no junctions to be dangling FROM

    // Per-bit via positions, projected onto each incident segment's axis.
    std::map<std::pair<int,int>, std::vector<double>> reach;   // (seg,bit) -> coords
    for (const auto& v : dnuts.net_vias) {
        if (v.bundle_id != bundle_id) continue;
        for (int si : {v.from_seg, v.to_seg}) {
            if (si < 0 || si >= n) continue;
            reach[{si, v.bit_index}].push_back(segs[si].horiz ? v.x : v.y);
        }
    }

    // Legitimate reaches shared by every bit of a segment: busterm faces and
    // pass-through coverage.  Computed once per segment.
    std::vector<std::vector<double>> seg_reach(n);
    for (int si = 0; si < n; ++si) {
        const ConnSeg& cs = segs[si];
        for (const auto& c : cs.conns)
            if (c.kind == SegConn::BUSTERM)
                seg_reach[si].push_back((double)c.face_coord);
        for (const auto& bname : topo.connected_block_names) {
            auto rects = fp.get_block_rects(bname);
            if (rects.empty()) rects.push_back(fp.get_block_bounds(bname));
            for (const Rect& r : rects)
                if (seg_spans_rect(cs, (double)cs.perp_pos, r)) {
                    seg_reach[si].push_back(cs.horiz ? (double)r.x1 : (double)r.y1);
                    seg_reach[si].push_back(cs.horiz ? (double)r.x2 : (double)r.y2);
                    break;
                }
        }
    }

    // A via's own enclosure legitimately overhangs its junction; only call metal
    // dangling once it exceeds the widest wire on the layer, so the finding is
    // about routing structure and never about via geometry.
    for (const auto& ns : dnuts.net_segments) {
        if (ns.bundle_id != bundle_id || ns.is_shield || !ns.placed) continue;
        const int si = ns.seg_idx;
        if (si < 0 || si >= n) continue;

        std::vector<double> pts = seg_reach[si];
        auto it = reach.find({si, ns.bit_index});
        if (it != reach.end())
            pts.insert(pts.end(), it->second.begin(), it->second.end());
        if (pts.empty()) continue;          // nothing to measure against

        const double lo = *std::min_element(pts.begin(), pts.end());
        const double hi = *std::max_element(pts.begin(), pts.end());
        const double s_lo = std::min(ns.span_lo, ns.span_hi);
        const double s_hi = std::max(ns.span_lo, ns.span_hi);
        const double tol  = std::max(ns.width, 1.0);
        const double lead = lo - s_lo, trail = s_hi - hi;
        if (lead <= tol && trail <= tol) continue;

        ConnViolation v;
        v.kind      = ViolationKind::ANTENNA;
        v.bundle_id = bundle_id;
        v.seg_idx   = si;
        std::ostringstream msg;
        msg << "Seg " << si << " bit " << ns.bit_index << " on layer M"
            << ns.layer << " spans [" << s_lo << "," << s_hi
            << "] but this bit only reaches [" << lo << "," << hi << "] — "
            << (lead > tol ? lead : 0.0) << " + " << (trail > tol ? trail : 0.0)
            << " of dangling metal past its own attachments (dnuts, per-bit)";
        v.message = msg.str();
        result.violations.push_back(std::move(v));
    }
    (void)layers;
}

static void detect_antennas(const std::vector<ConnSeg>& segs,
                            const Topology& topo, const Floorplan& fp,
                            int bundle_id, const char* stage,
                            ConnResult& result)
{
    const int n = (int)segs.size();
    // A single-segment topology has no junctions by construction; a missing
    // block tap there is BUSTERM_OPEN's business, not an antenna.
    if (n < 2) return;
    for (int i = 0; i < n; ++i) {
        const ConnSeg& cs = segs[i];
        const SegAttachment a = seg_attachment(cs, topo, fp);
        if (a.count() >= 2) continue;
        ConnViolation v;
        v.kind      = ViolationKind::ANTENNA;
        v.bundle_id = bundle_id;
        v.seg_idx   = i;
        std::ostringstream msg;
        msg << "Segment " << i << " (" << (cs.horiz ? "H" : "V")
            << " along [" << cs.along_lo << "," << cs.along_hi
            << "] @ " << cs.perp_pos << ") attaches to the route at "
            << a.count() << " point(s) ("
            << a.n_busterm << " busterm, " << a.n_seg << " seg, "
            << a.through.size() << " pass-through)";
        if (!a.positions.empty()) msg << " at along=" << *a.positions.begin();
        msg << " — a dangling 'antenna' wire: the rest of it terminates in "
               "nothing (" << stage << ")";
        v.message = msg.str();
        result.violations.push_back(std::move(v));
    }

    // Tap-overhang antenna (issue #514): a terminal piece that exists ONLY to
    // tap a face of a block the wire already covers.  The attachment-count
    // rule above cannot see it — the far-face tap IS an attachment, so the
    // span ends exactly on a "valid" landing — but the piece between the tap
    // and the segment's nearest OTHER attachment runs over the tapped block's
    // own footprint and serves nothing: remove it and the block stays
    // covered (the remainder still overlaps the footprint — the same
    // seg_spans_rect coverage every block-coverage check accepts).  The
    // canonical producer was the spine-relay completion's face-extension
    // strategies (repro: mix.bdb clone bundle, spine extended 35 units past
    // its last junction to the block's FAR face); generation no longer emits
    // the shape, and this flags any persisted/hand-built instance LOUD.
    //
    // Guards keep the legitimate tap idioms out:
    //   - piece must lie INSIDE the tapped block (a normal outside-approach
    //     tap's terminal piece is outside the footprint);
    //   - redundancy is required (remainder or a sibling still covers the
    //     block) — a tap whose removal would open the block is a real tap;
    //   - exactly ONE busterm conn on the segment (multi-tap shapes — the
    //     ABUT shared-edge crossing — are their own contract);
    //   - declared feedthru blocks are exempt (their split-at-face landings
    //     are the feedthru contract, not overhang).
    for (int i = 0; i < n; ++i) {
        const ConnSeg& cs = segs[i];
        const SegConn* tap = nullptr;
        int nbt = 0;
        for (const auto& c : cs.conns)
            if (c.kind == SegConn::BUSTERM) { ++nbt; tap = &c; }
        if (nbt != 1) continue;
        if (std::find(topo.feedthru_blocks.begin(), topo.feedthru_blocks.end(),
                      tap->block_name) != topo.feedthru_blocks.end())
            continue;
        const double F = tap->at_pos;
        const bool at_lo = (F == (double)cs.along_lo);
        const bool at_hi = (F == (double)cs.along_hi);
        if (at_lo == at_hi) continue;      // mid-segment graze (or degenerate)
        // Nearest OTHER attachment position along this segment.
        double A = std::numeric_limits<double>::quiet_NaN();
        for (const auto& c : cs.conns) {
            if (&c == tap) continue;
            const double p = c.at_pos;
            if (std::isnan(A) || (at_lo ? p < A : p > A)) A = p;
        }
        if (std::isnan(A) || A == F) continue;   // no piece: count rule's business
        const double plo = std::min(F, A), phi = std::max(F, A);
        // The piece must lie inside the tapped block (rect-aware) at cs's perp.
        auto rects = fp.get_block_rects(tap->block_name);
        if (rects.empty()) rects.push_back(fp.get_block_bounds(tap->block_name));
        bool inside = false;
        for (const Rect& r : rects) {
            const int alo = cs.horiz ? r.x1 : r.y1, ahi = cs.horiz ? r.x2 : r.y2;
            const int blo = cs.horiz ? r.y1 : r.x1, bhi = cs.horiz ? r.y2 : r.x2;
            if (cs.perp_pos >= blo && cs.perp_pos <= bhi &&
                plo >= alo && phi <= ahi) { inside = true; break; }
        }
        if (!inside) continue;
        // Redundancy: with the piece removed, the TAPPED block must remain
        // covered — by this segment's trimmed remainder or by any sibling's
        // tap / pass-through — and so must EVERY OTHER connected block the
        // piece touches: overlapping or contained neighbours may be
        // pass-through-covered ONLY by the piece, which makes it
        // load-bearing metal even though the tapped block is fine without
        // it (Codex #517).
        ConnSeg trimmed = cs;
        if (at_lo) trimmed.along_lo = (int)A; else trimmed.along_hi = (int)A;
        auto covered_without_piece = [&](const std::string& bname,
                                         const std::vector<Rect>& brs) {
            for (const Rect& r : brs)
                if (seg_spans_rect(trimmed, (double)cs.perp_pos, r)) return true;
            for (int j = 0; j < n; ++j) {
                if (j == i) continue;
                for (const auto& c : segs[j].conns)
                    if (c.kind == SegConn::BUSTERM && c.block_name == bname)
                        return true;
                for (const Rect& r : brs)
                    if (seg_spans_rect(segs[j], (double)segs[j].perp_pos, r))
                        return true;
            }
            return false;
        };
        bool redundant = covered_without_piece(tap->block_name, rects);
        for (const auto& bname : topo.connected_block_names) {
            if (!redundant) break;
            if (bname == tap->block_name) continue;
            auto brs = fp.get_block_rects(bname);
            if (brs.empty()) brs.push_back(fp.get_block_bounds(bname));
            bool touches = false;
            for (const Rect& r : brs) {
                const int alo = cs.horiz ? r.x1 : r.y1, ahi = cs.horiz ? r.x2 : r.y2;
                const int blo = cs.horiz ? r.y1 : r.x1, bhi = cs.horiz ? r.y2 : r.x2;
                if (cs.perp_pos >= blo && cs.perp_pos <= bhi &&
                    plo <= ahi && phi >= alo) { touches = true; break; }
            }
            if (touches && !covered_without_piece(bname, brs))
                redundant = false;
        }
        if (!redundant) continue;          // the piece is load-bearing: keep it
        ConnViolation v;
        v.kind       = ViolationKind::ANTENNA;
        v.bundle_id  = bundle_id;
        v.seg_idx    = i;
        v.block_name = tap->block_name;
        std::ostringstream msg;
        msg << "Segment " << i << " (" << (cs.horiz ? "H" : "V")
            << " along [" << cs.along_lo << "," << cs.along_hi
            << "] @ " << cs.perp_pos << ") overhangs [" << plo << "," << phi
            << "] past its last junction, entirely over block '"
            << tap->block_name << "', only to tap its far face at " << F
            << " — the block stays covered without it: a tap-overhang "
               "'antenna' (" << stage << ")";
        v.message = msg.str();
        result.violations.push_back(std::move(v));
    }
}

static void detect_disconnected(const std::vector<ConnSeg>& segs,
                                int bundle_id, const char* stage,
                                ConnResult& result)
{
    int n = (int)segs.size();
    if (n < 2) return;
    std::vector<int> roots = island_roots(segs);
    std::set<int> comps(roots.begin(), roots.end());
    if ((int)comps.size() <= 1) return;
    ConnViolation v;
    v.kind = ViolationKind::DISCONNECTED;
    v.bundle_id = bundle_id;
    v.seg_idx  = *comps.begin();
    v.seg_idx2 = *std::next(comps.begin());
    std::ostringstream msg;
    msg << "Topology splits into " << comps.size() << " disconnected islands"
        << " (seg-graph roots:";
    for (int r : comps) msg << " " << r;
    msg << ") — the net cannot be electrically complete (" << stage << ")";
    v.message = msg.str();
    result.violations.push_back(std::move(v));
}

bool disconnected_islands_bridged(const ConnTopology& ct, const Topology& topo,
                                  const Floorplan& fp)
{
    if (topo.feedthru_blocks.empty()) return false;
    const auto& segs = ct.segs();
    int n = (int)segs.size();
    if (n < 2) return false;                     // cannot be split
    std::vector<int> roots = island_roots(segs); // detect_disconnected's model
    std::set<int> islands(roots.begin(), roots.end());
    if (islands.size() <= 1) return false;       // not split: nothing to exempt
    // Rects of the declared feedthru blocks (multi-rect blocks per rect).
    std::vector<Rect> ft_rects;
    for (const auto& bname : topo.feedthru_blocks) {
        auto rects = fp.get_block_rects(bname);
        if (rects.empty()) rects.push_back(fp.get_block_bounds(bname));
        ft_rects.insert(ft_rects.end(), rects.begin(), rects.end());
    }
    const std::set<std::string> ft(topo.feedthru_blocks.begin(),
                                   topo.feedthru_blocks.end());
    // An island is bridged when SOME member segment touches a declared
    // feedthru block: a BUSTERM conn to it (the split spine's face landings)
    // or inclusive geometric overlap with one of its rects (a stub landing in
    // the split gap over the block's footprint).
    std::set<int> bridged;
    for (int i = 0; i < n; ++i) {
        if (bridged.count(roots[i])) continue;
        bool touch = false;
        for (const auto& conn : segs[i].conns)
            if (conn.kind == SegConn::BUSTERM && ft.count(conn.block_name)) {
                touch = true;
                break;
            }
        for (size_t r = 0; !touch && r < ft_rects.size(); ++r)
            if (seg_spans_rect(segs[i], (double)segs[i].perp_pos, ft_rects[r]))
                touch = true;
        if (touch) bridged.insert(roots[i]);
    }
    for (int isl : islands)
        if (!bridged.count(isl)) return false;   // a genuinely open island
    return true;
}

// ── check_topo ────────────────────────────────────────────────────────────────

ConnResult check_topo(const ConnTopology& ct, const Topology& topo,
                      const Floorplan& fp, int bundle_id)
{
    ConnResult result;
    const auto& segs = ct.segs();
    int n = (int)segs.size();

    // 1. SEG connection continuity (each pair checked once, i < j).
    for (int i = 0; i < n; ++i) {
        const ConnSeg& cs = segs[i];
        for (const auto& conn : cs.conns) {
            if (conn.kind != SegConn::SEG) continue;
            int j = conn.seg_idx;
            if (j <= i) continue;
            const ConnSeg& other = segs[j];
            bool i_reach_j = (cs.perp_pos >= other.along_lo && cs.perp_pos <= other.along_hi);
            bool j_reach_i = (other.perp_pos >= cs.along_lo && other.perp_pos <= cs.along_hi);
            if (!i_reach_j || !j_reach_i) {
                ConnViolation v;
                v.kind = ViolationKind::SEG_OPEN;
                v.bundle_id = bundle_id; v.seg_idx = i; v.seg_idx2 = j;
                std::ostringstream msg;
                msg << "Seg " << i << " and Seg " << j << " disconnected (topo)";
                if (!i_reach_j)
                    msg << "; seg" << i << ".perp=" << cs.perp_pos
                        << " not in seg" << j << ".along=["
                        << other.along_lo << "," << other.along_hi << "]";
                if (!j_reach_i)
                    msg << "; seg" << j << ".perp=" << other.perp_pos
                        << " not in seg" << i << ".along=["
                        << cs.along_lo << "," << cs.along_hi << "]";
                v.message = msg.str();
                result.violations.push_back(std::move(v));
            }
        }
    }

    // 2. BUSTERM face validity: face_coord must be at a segment endpoint, and
    //    perp_pos must lie within the block's face extent.
    for (int i = 0; i < n; ++i) {
        const ConnSeg& cs = segs[i];
        for (const auto& conn : cs.conns) {
            if (conn.kind != SegConn::BUSTERM) continue;
            bool at_endpoint = (conn.face_coord == cs.along_lo ||
                                conn.face_coord == cs.along_hi);
            bool face_ok = perp_in_block_face((double)cs.perp_pos, cs.horiz,
                                              conn.block_name, fp,
                                              (double)cs.along_lo,
                                              (double)cs.along_hi);
            if (!at_endpoint || !face_ok) {
                ConnViolation v;
                v.kind = ViolationKind::BUSTERM_FACE;
                v.bundle_id = bundle_id; v.seg_idx = i;
                v.block_name = conn.block_name;
                std::ostringstream msg;
                msg << "Seg " << i << " BUSTERM to '" << conn.block_name << "'";
                if (!at_endpoint)
                    msg << "; face_coord=" << conn.face_coord
                        << " not at endpoint ["
                        << cs.along_lo << "," << cs.along_hi << "]";
                if (!face_ok)
                    msg << "; perp_pos=" << cs.perp_pos << " outside block face";
                v.message = msg.str();
                result.violations.push_back(std::move(v));
            }
        }
    }

    // 3. Block coverage: every block in connected_block_names must have either
    //    an explicit BUSTERM connection or a segment that passes through it.
    std::set<std::string> explicitly_connected;
    for (const auto& cs : segs)
        for (const auto& conn : cs.conns)
            if (conn.kind == SegConn::BUSTERM)
                explicitly_connected.insert(conn.block_name);

    for (const auto& bname : topo.connected_block_names) {
        if (explicitly_connected.count(bname)) continue;
        auto rects = fp.get_block_rects(bname);
        if (rects.empty()) rects.push_back(fp.get_block_bounds(bname));
        bool covered = false;
        for (const auto& cs : segs) {
            if (covered) break;
            for (const Rect& r : rects) {
                if (!seg_spans_rect(cs, (double)cs.perp_pos, r)) continue;
                // Robust-cover gate (issue #438): a nominal pass-through only counts
                // if the segment can actually be PLACED to cover the block — its
                // slide window [perp_lo, perp_hi] must reach the block's perp-extent.
                // A boundary graze whose window is pushed entirely off the face
                // (tc3a bundle 68: seg1 grazes blk_10's left edge x=809 but its
                // window [740,806] — capped by the min-stub floor — can never reach
                // x∈[809,969]) is NOT a real cover: NUTS seats the trunk off the face
                // and the block's bits open. Normal covers are unaffected — their
                // nominal perp_pos lies inside the window, so the window necessarily
                // reaches the block. Dropping the fragile candidate lets a robust
                // interior-pass-through sibling win (filter_uncovered keeps the pool
                // if EVERY candidate is broken, so a bundle is never stranded).
                int blo = cs.horiz ? r.y1 : r.x1;
                int bhi = cs.horiz ? r.y2 : r.x2;
                if (cs.perp_hi < blo || cs.perp_lo > bhi) continue;  // window can't reach
                covered = true;
                break;
            }
        }
        if (!covered) {
            ConnViolation v;
            v.kind = ViolationKind::BUSTERM_OPEN;
            v.bundle_id = bundle_id; v.seg_idx = -1;
            v.block_name = bname;
            v.message = "Block '" + bname
                + "' has no BUSTERM connection and no pass-through segment";
            result.violations.push_back(std::move(v));
        }
    }

    // 4. FEEDTHRU_RELAY: every block's incident stubs must be wire-connected, not
    //    relayed through the block's body (shared helper, geometric wire touch).
    detect_feedthru_relay(segs, topo, fp, bundle_id, "topo", result);

    // 5. Whole-graph connectivity: one island only.
    detect_disconnected(segs, bundle_id, "topo", result);

    return result;
}

// ── check_nuts ────────────────────────────────────────────────────────────────

ConnResult check_nuts(const ConnTopology& ct, const NUTSResult& nuts,
                      const Topology& topo, const Floorplan& fp,
                      const LayerStack& layers, int bundle_id,
                      const Floorplan* zone_fp)
{
    ConnResult result;
    const auto& segs = ct.segs();
    int n = (int)segs.size();

    std::map<int, const TrackSegment*> ts_map;
    for (const auto& ts : nuts.segments)
        if (ts.bundle_id == bundle_id)
            ts_map[ts.seg_idx] = &ts;

    // 0. Layer-direction validity: a segment on a layer whose routing
    //    direction doesn't match its orientation is unbuildable, and its
    //    track_position came from the wrong axis — the coordinate-based
    //    checks below can then pass coincidentally.
    for (const auto& [si, tsp] : ts_map) {
        const TrackSegment& ts = *tsp;
        bool dir_ok = layers.has_layer(ts.layer) &&
            (layers.get_layer_dir(ts.layer) == LayerDir::HORIZONTAL) == ts.horiz;
        if (!dir_ok) {
            ConnViolation v;
            v.kind = ViolationKind::LAYER_DIR;
            v.bundle_id = bundle_id; v.seg_idx = si;
            std::ostringstream msg;
            msg << "Seg " << si << " runs " << (ts.horiz ? "H" : "V")
                << " but is on layer M" << ts.layer << " ("
                << (!layers.has_layer(ts.layer) ? "undefined"
                    : layers.get_layer_dir(ts.layer) == LayerDir::HORIZONTAL ? "H" : "V")
                << ") — unbuildable (nuts)";
            v.message = msg.str();
            result.violations.push_back(std::move(v));
        }
    }

    // 1. SEG connection continuity.
    for (int i = 0; i < n; ++i) {
        auto it_i = ts_map.find(i);
        if (it_i == ts_map.end() || !it_i->second->placed) continue;
        const TrackSegment& ts_i = *it_i->second;
        const ConnSeg& cs = segs[i];
        for (const auto& conn : cs.conns) {
            if (conn.kind != SegConn::SEG) continue;
            int j = conn.seg_idx;
            if (j <= i) continue;
            auto it_j = ts_map.find(j);
            if (it_j == ts_map.end() || !it_j->second->placed) continue;
            const TrackSegment& ts_j = *it_j->second;
            // span_lo/span_hi encode nominal endpoint identity, so they may be
            // stored with lo > hi after placement swaps the two ends.  Reach is
            // a geometric (order-independent) test, so compare against the
            // ordered [min,max] extent.
            const double i_lo = std::min(ts_i.span_lo, ts_i.span_hi);
            const double i_hi = std::max(ts_i.span_lo, ts_i.span_hi);
            const double j_lo = std::min(ts_j.span_lo, ts_j.span_hi);
            const double j_hi = std::max(ts_j.span_lo, ts_j.span_hi);
            bool i_reach_j = (i_lo <= ts_j.track_position &&
                               ts_j.track_position <= i_hi);
            bool j_reach_i = (j_lo <= ts_i.track_position &&
                               ts_i.track_position <= j_hi);
            if (!i_reach_j || !j_reach_i) {
                ConnViolation v;
                v.kind = ViolationKind::SEG_OPEN;
                v.bundle_id = bundle_id; v.seg_idx = i; v.seg_idx2 = j;
                std::ostringstream msg;
                msg << "Seg " << i << " and Seg " << j << " disconnected (nuts)";
                if (!i_reach_j)
                    msg << "; track_pos[" << j << "]=" << ts_j.track_position
                        << " not in span[" << i << "]=["
                        << i_lo << "," << i_hi << "]";
                if (!j_reach_i)
                    msg << "; track_pos[" << i << "]=" << ts_i.track_position
                        << " not in span[" << j << "]=["
                        << j_lo << "," << j_hi << "]";
                v.message = msg.str();
                result.violations.push_back(std::move(v));
            }
        }
    }

    // 2. BUSTERM face check at placed track_position.
    for (int i = 0; i < n; ++i) {
        auto it = ts_map.find(i);
        if (it == ts_map.end() || !it->second->placed) continue;
        const TrackSegment& ts = *it->second;
        const ConnSeg& cs = segs[i];
        for (const auto& conn : cs.conns) {
            if (conn.kind != SegConn::BUSTERM) continue;
            if (!perp_in_block_face(ts.track_position, cs.horiz, conn.block_name,
                                    fp, ts.span_lo, ts.span_hi)) {
                ConnViolation v;
                v.kind = ViolationKind::BUSTERM_FACE;
                v.bundle_id = bundle_id; v.seg_idx = i;
                v.block_name = conn.block_name;
                std::ostringstream msg;
                msg << "Seg " << i << " BUSTERM to '" << conn.block_name
                    << "'; track_pos=" << ts.track_position << " outside block face (nuts)";
                v.message = msg.str();
                result.violations.push_back(std::move(v));
            }
            // The placed segment's along-span must still REACH the busterm face.
            // NUTS span adjustment follows connected segments' placed positions and
            // can pull a connected end past face_coord, detaching the block even
            // though track_position (perpendicular) still lands within the face
            // extent — the perp check above passes coincidentally.  face_coord is on
            // the along axis (it equals an along endpoint nominally; see check_topo),
            // so require it to stay within the placed along extent.
            const double s_lo = std::min(ts.span_lo, ts.span_hi);
            const double s_hi = std::max(ts.span_lo, ts.span_hi);
            if (conn.face_coord < s_lo || conn.face_coord > s_hi) {
                ConnViolation v;
                v.kind = ViolationKind::BUSTERM_OPEN;
                v.bundle_id = bundle_id; v.seg_idx = i;
                v.block_name = conn.block_name;
                std::ostringstream msg;
                msg << "Seg " << i << " BUSTERM to '" << conn.block_name
                    << "'; along-span [" << s_lo << "," << s_hi
                    << "] no longer reaches face_coord=" << conn.face_coord << " (nuts)";
                v.message = msg.str();
                result.violations.push_back(std::move(v));
            }
        }
    }

    // 3. Block coverage: pass-through blocks must still be spanned at placed positions.
    std::set<std::string> explicitly_connected;
    for (const auto& cs : segs)
        for (const auto& conn : cs.conns)
            if (conn.kind == SegConn::BUSTERM)
                explicitly_connected.insert(conn.block_name);

    for (const auto& bname : topo.connected_block_names) {
        if (explicitly_connected.count(bname)) continue;
        auto rects = fp.get_block_rects(bname);
        if (rects.empty()) rects.push_back(fp.get_block_bounds(bname));
        bool covered = false;
        for (const auto& ts : nuts.segments) {
            if (ts.bundle_id != bundle_id || !ts.placed || covered) continue;
            if (ts.seg_idx < 0 || ts.seg_idx >= n) continue;
            const ConnSeg& cs = segs[ts.seg_idx];
            // span_lo/span_hi may be stored reversed (nominal endpoint identity);
            // crossing a block is an ordered-extent test.
            const double s_lo = std::min(ts.span_lo, ts.span_hi);
            const double s_hi = std::max(ts.span_lo, ts.span_hi);
            for (const Rect& r : rects) {
                bool through = cs.horiz
                    ? (ts.track_position >= r.y1 && ts.track_position <= r.y2
                       && s_lo <= r.x2 && s_hi >= r.x1)
                    : (ts.track_position >= r.x1 && ts.track_position <= r.x2
                       && s_lo <= r.y2 && s_hi >= r.y1);
                if (through) { covered = true; break; }
            }
        }
        if (!covered) {
            ConnViolation v;
            v.kind = ViolationKind::BUSTERM_OPEN;
            v.bundle_id = bundle_id; v.seg_idx = -1;
            v.block_name = bname;
            v.message = "Block '" + bname
                + "' has no pass-through segment at placed track positions (nuts)";
            result.violations.push_back(std::move(v));
        }
    }

    // 4. KEEPOUT_CROSS: a placed bus segment whose physical extent
    //    [pos ± w/2] lies on a keepout overlapping its span.  Placement avoids
    //    keepout-occupied intervals, but the exhausted-window fallback commits
    //    the interval centre regardless — the engine counts it
    //    (NUTSResult::num_keepout_conflicts) and this makes the audit see it
    //    too instead of blessing an unroutable placement.  Same semantics as
    //    count_keepout_conflicts (strict overlap on both axes; a zone with
    //    empty layer_ids blocks every layer).  Zones come from zone_fp — the
    //    floorplan the engine placed against (see verify.h).
    {
        const auto kozs = keepout_zones(zone_fp ? *zone_fp : fp, layers);
        for (const auto& [si, tsp] : ts_map) {
            const TrackSegment& ts = *tsp;
            if (!ts.placed) continue;
            const double s_lo = std::min(ts.span_lo, ts.span_hi);
            const double s_hi = std::max(ts.span_lo, ts.span_hi);
            const double e_lo = ts.track_position - ts.width / 2.0;
            const double e_hi = ts.track_position + ts.width / 2.0;
            for (const auto& koz : kozs) {
                if (!zone_on_layer(koz, ts.layer)) continue;
                const Rect& r = koz.bbox;
                const double k_p1 = ts.horiz ? r.y1 : r.x1;
                const double k_p2 = ts.horiz ? r.y2 : r.x2;
                const double k_a1 = ts.horiz ? r.x1 : r.y1;
                const double k_a2 = ts.horiz ? r.x2 : r.y2;
                if (s_lo < k_a2 && s_hi > k_a1 && e_lo < k_p2 && e_hi > k_p1) {
                    ConnViolation v;
                    v.kind = ViolationKind::KEEPOUT_CROSS;
                    v.bundle_id = bundle_id; v.seg_idx = si;
                    std::ostringstream msg;
                    msg << "Seg " << si << " on layer M" << ts.layer
                        << " placed ON keepout (" << r.x1 << "," << r.y1
                        << ")-(" << r.x2 << "," << r.y2 << "): extent ["
                        << e_lo << "," << e_hi << "] at track_pos="
                        << ts.track_position << " (nuts)";
                    v.message = msg.str();
                    result.violations.push_back(std::move(v));
                    break;  // one violation per segment suffices
                }
            }
        }
    }

    // FEEDTHRU_RELAY (structural; see detect_feedthru_relay).
    detect_feedthru_relay(segs, topo, fp, bundle_id, "nuts", result);
    detect_disconnected(segs, bundle_id, "nuts", result);
    // Dangling wires (structural; see detect_antennas — issue #482).
    detect_antennas(segs, topo, fp, bundle_id, "nuts", result);

    return result;
}

// ── check_dnuts ───────────────────────────────────────────────────────────────

ConnResult check_dnuts(const ConnTopology& ct, const DetailedNUTSResult& dnuts,
                       const Topology& topo, const Floorplan& fp,
                       const LayerStack& layers, int bundle_id, int num_bits,
                       const Floorplan* zone_fp)
{
    ConnResult result;
    const auto& segs = ct.segs();
    int n = (int)segs.size();

    std::map<std::pair<int,int>, const NetSegment*> ns_map;
    for (const auto& ns : dnuts.net_segments)
        if (ns.bundle_id == bundle_id)
            ns_map[{ns.seg_idx, ns.bit_index}] = &ns;

    // 0. Layer-direction validity (see check_nuts).  All bits of a segment
    //    share its layer, so report once per (seg, layer).
    std::set<std::pair<int,int>> dir_reported;
    for (const auto& [key, nsp] : ns_map) {
        int si = key.first;
        if (si < 0 || si >= n) continue;
        const NetSegment& ns = *nsp;
        bool seg_horiz = segs[si].horiz;
        bool dir_ok = layers.has_layer(ns.layer) &&
            (layers.get_layer_dir(ns.layer) == LayerDir::HORIZONTAL) == seg_horiz;
        if (!dir_ok && dir_reported.insert({si, ns.layer}).second) {
            ConnViolation v;
            v.kind = ViolationKind::LAYER_DIR;
            v.bundle_id = bundle_id; v.seg_idx = si;
            std::ostringstream msg;
            msg << "Seg " << si << " runs " << (seg_horiz ? "H" : "V")
                << " but its bit wires are on layer M" << ns.layer << " ("
                << (!layers.has_layer(ns.layer) ? "undefined"
                    : layers.get_layer_dir(ns.layer) == LayerDir::HORIZONTAL ? "H" : "V")
                << ") — unbuildable (dnuts)";
            v.message = msg.str();
            result.violations.push_back(std::move(v));
        }
    }

    // 1. BUSTERM face check at placed per-bit track_position.
    for (int i = 0; i < n; ++i) {
        const ConnSeg& cs = segs[i];
        for (const auto& conn : cs.conns) {
            if (conn.kind != SegConn::BUSTERM) continue;
            for (int bit = 0; bit < num_bits; ++bit) {
                auto it = ns_map.find({i, bit});
                if (it == ns_map.end()) continue;
                if (!perp_in_block_face(it->second->track_position, cs.horiz,
                                        conn.block_name, fp,
                                        it->second->span_lo,
                                        it->second->span_hi)) {
                    ConnViolation v;
                    v.kind = ViolationKind::BUSTERM_FACE;
                    v.bundle_id = bundle_id; v.seg_idx = i; v.bit_index = bit;
                    v.block_name = conn.block_name;
                    std::ostringstream msg;
                    msg << "Seg " << i << " Bit " << bit << " BUSTERM to '"
                        << conn.block_name << "'; track_pos="
                        << it->second->track_position << " outside block face (dnuts)";
                    v.message = msg.str();
                    result.violations.push_back(std::move(v));
                }
                // Along-span must still REACH the busterm face (see check_nuts):
                // a collapsed span detaches the block even when the perpendicular
                // per-bit track_position still lands within the face extent.
                const NetSegment& ns = *it->second;
                const double s_lo = std::min(ns.span_lo, ns.span_hi);
                const double s_hi = std::max(ns.span_lo, ns.span_hi);
                if (conn.face_coord < s_lo || conn.face_coord > s_hi) {
                    ConnViolation v;
                    v.kind = ViolationKind::BUSTERM_OPEN;
                    v.bundle_id = bundle_id; v.seg_idx = i; v.bit_index = bit;
                    v.block_name = conn.block_name;
                    std::ostringstream msg;
                    msg << "Seg " << i << " Bit " << bit << " BUSTERM to '"
                        << conn.block_name << "'; along-span [" << s_lo << "," << s_hi
                        << "] no longer reaches face_coord=" << conn.face_coord << " (dnuts)";
                    v.message = msg.str();
                    result.violations.push_back(std::move(v));
                }
            }
        }
    }

    // 2. SEG connection continuity per bit.
    for (int i = 0; i < n; ++i) {
        const ConnSeg& cs = segs[i];
        for (const auto& conn : cs.conns) {
            if (conn.kind != SegConn::SEG) continue;
            int j = conn.seg_idx;
            if (j <= i) continue;
            for (int bit = 0; bit < num_bits; ++bit) {
                auto it_i = ns_map.find({i, bit});
                auto it_j = ns_map.find({j, bit});
                if (it_i == ns_map.end() || it_j == ns_map.end()) continue;
                const NetSegment& ns_i = *it_i->second;
                const NetSegment& ns_j = *it_j->second;
                // span_lo/span_hi keep nominal endpoint identity (may be lo>hi);
                // reach is order-independent, so use the ordered [min,max] extent.
                const double i_lo = std::min(ns_i.span_lo, ns_i.span_hi);
                const double i_hi = std::max(ns_i.span_lo, ns_i.span_hi);
                const double j_lo = std::min(ns_j.span_lo, ns_j.span_hi);
                const double j_hi = std::max(ns_j.span_lo, ns_j.span_hi);
                bool i_reach_j = (i_lo <= ns_j.track_position &&
                                   ns_j.track_position <= i_hi);
                bool j_reach_i = (j_lo <= ns_i.track_position &&
                                   ns_i.track_position <= j_hi);
                if (!i_reach_j || !j_reach_i) {
                    ConnViolation v;
                    v.kind = ViolationKind::SEG_OPEN;
                    v.bundle_id = bundle_id; v.seg_idx = i; v.seg_idx2 = j;
                    v.bit_index = bit;
                    std::ostringstream msg;
                    msg << "Seg " << i << " and Seg " << j
                        << " Bit " << bit << " disconnected (dnuts)";
                    if (!i_reach_j)
                        msg << "; track_pos[" << j << "]=" << ns_j.track_position
                            << " not in span[" << i << "]=["
                            << i_lo << "," << i_hi << "]";
                    if (!j_reach_i)
                        msg << "; track_pos[" << i << "]=" << ns_i.track_position
                            << " not in span[" << j << "]=["
                            << j_lo << "," << j_hi << "]";
                    v.message = msg.str();
                    result.violations.push_back(std::move(v));
                }
            }
        }
    }

    // 3. Unplaced bits: every (seg_idx, bit) the segment CARRIES must have a
    //    NetSegment; absence means DetailedNUTS could not find a valid track.
    //    A tapered fan-in segment (Topology::seg_bits) carries only its
    //    member bits — the other bits are deliberately not emitted there and
    //    must not be reported unplaced.
    for (int i = 0; i < n; ++i) {
        const std::vector<int>* members = nullptr;
        auto sbit = topo.seg_bits.find(i);
        if (sbit != topo.seg_bits.end() && !sbit->second.empty())
            members = &sbit->second;
        for (int bit = 0; bit < num_bits; ++bit) {
            if (members && !std::binary_search(members->begin(),
                                               members->end(), bit))
                continue;                    // not carried by this segment
            if (ns_map.count({i, bit})) continue;
            ConnViolation v;
            v.kind = ViolationKind::UNPLACED;
            v.bundle_id = bundle_id; v.seg_idx = i; v.bit_index = bit;
            v.message = "Seg " + std::to_string(i) + " Bit " + std::to_string(bit)
                + " has no placed track (unplaced in DetailedNUTS)";
            result.violations.push_back(std::move(v));
        }
    }

    // 4. Pass-through block coverage per bit.
    std::set<std::string> explicitly_connected;
    for (const auto& cs : segs)
        for (const auto& conn : cs.conns)
            if (conn.kind == SegConn::BUSTERM)
                explicitly_connected.insert(conn.block_name);

    for (const auto& bname : topo.connected_block_names) {
        if (explicitly_connected.count(bname)) continue;
        auto rects = fp.get_block_rects(bname);
        if (rects.empty()) rects.push_back(fp.get_block_bounds(bname));

        // Which bits actually route THROUGH this pass-through block?  A tapered
        // fan-in segment (Topology::seg_bits) carries only its member bits, so
        // a bit whose covering segment does not carry it legitimately has no
        // wire here — requiring coverage for it is a spurious BUSTERM_OPEN
        // (audit C9-04), mirroring the taper handling of the UNPLACED check
        // above.  Derive the required bit set from the NOMINAL covering
        // segments' membership (a segment carrying every bit when it has no
        // seg_bits entry).  If NO segment nominally spans the block — a
        // generation inconsistency, not a taper case — fall back to requiring
        // every bit so a genuine open is still caught.
        std::vector<char> req(num_bits, 0);
        bool any_cover = false;
        for (int i = 0; i < n; ++i) {
            const ConnSeg& cs = segs[i];
            bool spans = false;
            for (const Rect& r : rects)
                if (seg_spans_rect(cs, (double)cs.perp_pos, r)) { spans = true; break; }
            if (!spans) continue;
            any_cover = true;
            auto sbit = topo.seg_bits.find(i);
            if (sbit != topo.seg_bits.end() && !sbit->second.empty()) {
                for (int b : sbit->second)
                    if (b >= 0 && b < num_bits) req[b] = 1;
            } else {
                std::fill(req.begin(), req.end(), 1);
            }
        }
        if (!any_cover) std::fill(req.begin(), req.end(), 1);

        for (int bit = 0; bit < num_bits; ++bit) {
            if (!req[bit]) continue;
            bool covered = false;
            for (int i = 0; i < n && !covered; ++i) {
                auto it = ns_map.find({i, bit});
                if (it == ns_map.end()) continue;
                const NetSegment& ns = *it->second;
                const ConnSeg& cs = segs[i];
                // Ordered extent: span_lo/span_hi may be stored reversed.
                const double s_lo = std::min(ns.span_lo, ns.span_hi);
                const double s_hi = std::max(ns.span_lo, ns.span_hi);
                for (const Rect& r : rects) {
                    bool through = cs.horiz
                        ? (ns.track_position >= r.y1 && ns.track_position <= r.y2
                           && s_lo <= r.x2 && s_hi >= r.x1)
                        : (ns.track_position >= r.x1 && ns.track_position <= r.x2
                           && s_lo <= r.y2 && s_hi >= r.y1);
                    if (through) { covered = true; break; }
                }
            }
            if (!covered) {
                ConnViolation v;
                v.kind = ViolationKind::BUSTERM_OPEN;
                v.bundle_id = bundle_id; v.seg_idx = -1; v.bit_index = bit;
                v.block_name = bname;
                v.message = "Block '" + bname + "' Bit " + std::to_string(bit)
                    + " has no pass-through segment at placed track positions (dnuts)";
                result.violations.push_back(std::move(v));
            }
        }
    }

    // 5. KEEPOUT_CROSS: a bit-wire whose track centre sits inside a keepout
    //    that overlaps its final span — the same predicate as DetailedNUTS's
    //    cull_keepout_crossers (closed on the perp axis, strict on the along
    //    axis), against the layer-equivalent zone list.  Pure
    //    defense-in-depth: the cull removes such bits before any result is
    //    returned, so a hit here means a stage regressed and emitted an
    //    illegal wire the audit must not bless.  Zones come from zone_fp —
    //    the floorplan the engine placed against (see verify.h).
    {
        const auto kozs = keepout_zones(zone_fp ? *zone_fp : fp, layers);
        for (const auto& [key, nsp] : ns_map) {
            const auto [si, bit] = key;
            if (si < 0 || si >= n) continue;
            const NetSegment& ns = *nsp;
            const bool horiz = segs[si].horiz;
            const double a_lo = std::min(ns.span_lo, ns.span_hi);
            const double a_hi = std::max(ns.span_lo, ns.span_hi);
            for (const auto& koz : kozs) {
                if (!zone_on_layer(koz, ns.layer)) continue;
                const Rect& r = koz.bbox;
                const double k_p1 = horiz ? r.y1 : r.x1;
                const double k_p2 = horiz ? r.y2 : r.x2;
                const double k_a1 = horiz ? r.x1 : r.y1;
                const double k_a2 = horiz ? r.x2 : r.y2;
                if (ns.track_position >= k_p1 && ns.track_position <= k_p2 &&
                    a_lo < k_a2 && a_hi > k_a1) {
                    ConnViolation v;
                    v.kind = ViolationKind::KEEPOUT_CROSS;
                    v.bundle_id = bundle_id; v.seg_idx = si; v.bit_index = bit;
                    std::ostringstream msg;
                    msg << "Seg " << si << " Bit " << bit << " on layer M"
                        << ns.layer << " crosses keepout (" << r.x1 << ","
                        << r.y1 << ")-(" << r.x2 << "," << r.y2
                        << ") at track_pos=" << ns.track_position << " (dnuts)";
                    v.message = msg.str();
                    result.violations.push_back(std::move(v));
                    break;  // one violation per bit suffices
                }
            }
        }
    }

    // FEEDTHRU_RELAY (structural; see detect_feedthru_relay).
    detect_feedthru_relay(ct.segs(), topo, fp, bundle_id, "dnuts", result);
    // Whole-graph connectivity (structural; see detect_disconnected).
    detect_disconnected(ct.segs(), bundle_id, "dnuts", result);
    // Dangling wires (structural; see detect_antennas — issue #482).
    detect_antennas(ct.segs(), topo, fp, bundle_id, "dnuts", result);

    // Per-BIT dangling metal, which the structural pass above cannot see.
    detect_bit_antennas(ct.segs(), dnuts, topo, fp, layers, bundle_id, result);

    // BIT_SHORT: two DIFFERENT bits of this bundle are two different NETS,
    // so their wires sharing a layer + track with overlapping (or touching —
    // closed span test, collinear ends butting up count) spans is a physical
    // short.  The abstract same-bundle track-sharing exemption assumed
    // "per-bit they are the same nets"; the tapered fan-in
    // (Topology::seg_bits) makes that conditional — two same-bundle segments
    // with non-identical bit subsets co-located by the exemption would put
    // different nets on one track, silently.  Defense-in-depth audit,
    // predicate lifted from tools/show_detailed_shorts.py; wires bucketed by
    // (layer, track) so the scan is near-linear.
    {
        std::map<std::pair<int, long long>,
                 std::vector<const NetSegment*>> by_track;
        for (const auto& [key, nsp] : ns_map)
            by_track[std::make_pair(
                         nsp->layer,
                         (long long)(nsp->track_position * 1e6 +
                                     (nsp->track_position >= 0 ? 0.5 : -0.5)))]
                .push_back(nsp);
        for (const auto& [tk, wires] : by_track) {
            for (size_t i = 0; i < wires.size(); ++i) {
                const NetSegment& a = *wires[i];
                for (size_t j = i + 1; j < wires.size(); ++j) {
                    const NetSegment& b = *wires[j];
                    if (a.bit_index == b.bit_index) continue;   // same net
                    // Within ONE segment the engine assigns distinct tracks
                    // by construction; the exemption gap this audits is two
                    // SEGMENTS co-located (and synthetic test fixtures place
                    // a segment's bits collapsed — not a short).
                    if (a.seg_idx == b.seg_idx) continue;
                    // STRICT overlap: collinear same-bundle segments MEET at
                    // junction points (zero-length touch) by construction —
                    // that contact is where same-bit wires join, not a short.
                    // A real exemption co-location overlaps over an extent.
                    // Compare NORMALIZED extents: bit-wire spans may carry
                    // span_lo > span_hi (detailed-NUTS span adjustment keeps
                    // nominal endpoint identity when placement swaps the two
                    // ends), and the raw ordering would read an overlapping
                    // inverted-span pair as disjoint (audit C9-01).
                    const double a_lo = std::min(a.span_lo, a.span_hi);
                    const double a_hi = std::max(a.span_lo, a.span_hi);
                    const double b_lo = std::min(b.span_lo, b.span_hi);
                    const double b_hi = std::max(b.span_lo, b.span_hi);
                    if (a_lo >= b_hi || b_lo >= a_hi)
                        continue;                               // disjoint/touch
                    ConnViolation v;
                    v.kind = ViolationKind::BIT_SHORT;
                    v.bundle_id = bundle_id;
                    v.seg_idx   = a.seg_idx;
                    v.seg_idx2  = b.seg_idx;
                    v.bit_index = a.bit_index;
                    std::ostringstream msg;
                    msg << "BIT_SHORT: bit " << a.bit_index << " (seg "
                        << a.seg_idx << ") and bit " << b.bit_index
                        << " (seg " << b.seg_idx
                        << ") are different nets but share layer M" << a.layer
                        << " track " << a.track_position
                        << " over span [" << std::max(a.span_lo, b.span_lo)
                        << ", " << std::min(a.span_hi, b.span_hi)
                        << "] (dnuts)";
                    v.message = msg.str();
                    result.violations.push_back(std::move(v));
                }
            }
        }
    }

    return result;
}

} // namespace buda
