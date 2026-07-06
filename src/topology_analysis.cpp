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

#include "topology_analysis.h"
#include <algorithm>
#include <atomic>
#include <climits>
#include <cmath>
#include <map>
#include <set>
#include <string>

// The six derivation passes behind ConnTopology::build (see topology_analysis.h
// for the pass order and contract).  Bodies moved verbatim from the former
// ConnTopology private methods — the algorithms, iteration orders, and comments
// are unchanged (byte-identity gated by test_topo_analysis_golden.py).

namespace buda {

// ── Content fingerprint + cached analyze() (Phase B) ─────────────────────────

namespace {

inline void h_bytes(uint64_t& h, const void* p, size_t n) {
    const unsigned char* b = static_cast<const unsigned char*>(p);
    for (size_t i = 0; i < n; ++i) {          // FNV-1a 64
        h ^= b[i];
        h *= 1099511628211ULL;
    }
}
inline void h_i64(uint64_t& h, int64_t v)      { h_bytes(h, &v, sizeof v); }
inline void h_str(uint64_t& h, const std::string& s) {
    h_i64(h, (int64_t)s.size());
    h_bytes(h, s.data(), s.size());
}
inline void h_rect(uint64_t& h, const Rect& r) {
    h_i64(h, r.x1); h_i64(h, r.y1); h_i64(h, r.x2); h_i64(h, r.y2);
}
inline void h_seg(uint64_t& h, const Segment& s) {
    h_i64(h, s.start.x); h_i64(h, s.start.y);
    h_i64(h, s.end.x);   h_i64(h, s.end.y);
    h_i64(h, s.layer_hint); h_i64(h, s.is_jog); h_i64(h, s.edge_id);
}
inline void h_busterm(uint64_t& h, const Busterm& bt) {
    h_str(h, bt.block_name);
    h_rect(h, bt.bbox);
    h_rect(h, bt.orig_bbox);
    h_i64(h, (int64_t)bt.rects.size());
    for (const Rect& r : bt.rects) h_rect(h, r);
    h_i64(h, (int64_t)bt.teg_mode);
}

std::atomic<uint64_t> g_computes{0}, g_hits{0};

} // namespace

uint64_t topology_fingerprint(const Topology& topo) {
    uint64_t h = 1469598103934665603ULL;      // FNV offset basis
    // type / trunk_location / pass_through_count are not analysis inputs, but
    // they ARE persisted candidate identity: two shapes can realize identical
    // segments under different type labels (e.g. distinct trunk loci collapsing
    // to the same clipped spine), and the uid built on this fingerprint must
    // tell them apart.  For the cache they only make validation finer-grained
    // (never stale).
    h_str(h, topo.type);
    h_i64(h, topo.trunk_location);
    h_i64(h, topo.pass_through_count);
    h_i64(h, (int64_t)topo.segments.size());
    for (const Segment& s : topo.segments) h_seg(h, s);
    // std::map iteration is key-ordered — deterministic.  seg_busterms is
    // hashed in its CANONICAL form: an all-junction (nullopt, nullopt) entry is
    // semantically identical to a missing entry (infer_connections treats both
    // as "no tap") and is exactly what the BDB persistence omits (zero rows for
    // an all-junction segment — see single_source_topo_truth.md Phase 3), so
    // skipping it makes the fingerprint reload-stable: uid(generated) ==
    // uid(reloaded) even though annotate_endpoints default-inserts an entry
    // for every segment in memory.
    int64_t n_taps = 0;
    for (const auto& [si, eps] : topo.seg_busterms)
        if (eps.first.has_value() || eps.second.has_value()) ++n_taps;
    h_i64(h, n_taps);
    for (const auto& [si, eps] : topo.seg_busterms) {
        if (!eps.first.has_value() && !eps.second.has_value()) continue;
        h_i64(h, si);
        for (const auto* opt : {&eps.first, &eps.second}) {
            h_i64(h, opt->has_value());
            if (opt->has_value()) h_busterm(h, **opt);
        }
    }
    h_i64(h, (int64_t)topo.seg_conns.size());
    for (const auto& [key, partners] : topo.seg_conns) {
        h_i64(h, key.first); h_i64(h, key.second);
        h_i64(h, (int64_t)partners.size());
        for (int p : partners) h_i64(h, p);
    }
    h_i64(h, (int64_t)topo.connected_block_names.size());
    for (const auto& n : topo.connected_block_names) h_str(h, n);
    // Not read by today's passes, but part of the topology's routed content;
    // hashing them is trivial and future-proofs a pass that starts reading
    // them (a stale cache must be impossible by construction, not by audit).
    h_i64(h, (int64_t)topo.feedthru_blocks.size());
    for (const auto& n : topo.feedthru_blocks) h_str(h, n);
    h_i64(h, (int64_t)topo.bridge_segments.size());
    for (const auto& [name, seg] : topo.bridge_segments) {
        h_str(h, name);
        h_seg(h, seg);
    }
    return h;
}

std::shared_ptr<const TopoAnalysis> analyze(const Topology& topo,
                                            const Floorplan& fp) {
    const uint64_t tfp = topology_fingerprint(topo);
    if (auto c = topo.analysis_cache_;
        c && c->topo_fp == tfp && c->fp_uid == fp.uid()
          && c->fp_rev == fp.rev()) {
        ++g_hits;
        return c;
    }
    auto a = std::make_shared<TopoAnalysis>();
    a->topo_fp = tfp;
    a->fp_uid  = fp.uid();
    a->fp_rev  = fp.rev();
    derive_conn_segs   (topo, fp, a->segs);
    derive_slide_ranges(topo, fp, a->segs);
    tighten_passthrough(topo, fp, a->segs);
    pin_relay_taps     (topo, fp, a->segs);
    derive_net_pull    (topo, fp, a->segs);
    derive_along_flex  (topo, fp, a->segs);
    ++g_computes;
    topo.analysis_cache_ = a;
    return a;
}

std::pair<uint64_t, uint64_t> analysis_cache_counters() {
    return {g_computes.load(), g_hits.load()};
}

void analysis_cache_reset_counters() {
    g_computes = 0;
    g_hits = 0;
}

// ── derive_conn_segs (geometry re-encode + connection inference) ─────────────
//
// BOTH connection kinds are read from the topology's authoritative annotations
// — never re-derived from geometry (single-source-of-topo-truth):
//   (a) BUSTERM — topo.seg_busterms names which block each terminal endpoint
//       taps (Phase 2 retired the geometric block-face fallback).
//   (b) SEG     — topo.seg_conns names which segments each junction endpoint
//       lands on (Phase 4 retired the geometric touching-segment scan).
//
// When an endpoint of segment i lands on segment j (shared bend or T-junction),
// we add a SEG connection to both i (pointing at j) and j (pointing at i); the
// junction's at_pos/is_endpoint labels are derived from the pair's nominal
// coordinates.  A hand-built topology must be annotated first — one
// `annotate_topology(topo, fp)` call covers both kinds.

void derive_conn_segs(const Topology& topo, const Floorplan& fp,
                      std::vector<ConnSeg>& segs) {
    (void)fp;   // connectivity is read from topo.seg_busterms/seg_conns, not geometry
    segs.clear();
    segs.resize(topo.segments.size());

    for (int i = 0; i < (int)topo.segments.size(); i++) {
        const Segment& s = topo.segments[i];
        ConnSeg& cs = segs[i];
        cs.horiz    = (s.start.y == s.end.y);
        cs.layer_id = s.layer_hint;
        if (cs.horiz) {
            cs.along_lo = std::min(s.start.x, s.end.x);
            cs.along_hi = std::max(s.start.x, s.end.x);
            cs.perp_pos = s.start.y;
        } else {
            cs.along_lo = std::min(s.start.y, s.end.y);
            cs.along_hi = std::max(s.start.y, s.end.y);
            cs.perp_pos = s.start.x;
        }
        cs.perp_lo = INT_MIN / 2;
        cs.perp_hi = INT_MAX / 2;
    }

    int n = (int)segs.size();

    // Helper: add a SegConn to segs[i] if it isn't already present.
    auto add_conn = [&](int i, SegConn c) {
        for (const auto& x : segs[i].conns) {
            if (x.kind != c.kind) continue;
            if (c.kind == SegConn::BUSTERM && x.block_name == c.block_name) return;
            if (c.kind == SegConn::SEG     && x.seg_idx   == c.seg_idx)    return;
        }
        segs[i].conns.push_back(std::move(c));
    };

    for (int i = 0; i < n; i++) {
        const Segment& si = topo.segments[i];
        ConnSeg&       ci = segs[i];

        for (int ep = 0; ep < 2; ++ep) {
            const Point& P = (ep == 0) ? si.start : si.end;
            bool found = false;

            // (a) busterm from the authoritative annotation ------------------
            // topo.seg_busterms is the SINGLE source of busterm truth: populated
            // by topology.cpp's annotate_endpoints (or the generator seeding its
            // taps directly), it names exactly which block each terminal endpoint
            // taps.  A nullopt endpoint — OR a segment with no entry at all — is a
            // wire junction, not a block tap, so it falls through to the SEG
            // inference below.  ConnTopology deliberately does NOT geometrically
            // search block faces here: that per-endpoint guess (the old fallback,
            // retired in single-source-of-truth Phase 2) could mis-tap a block
            // whose corner merely grazes a bend.  A hand-built topology must be
            // annotated first — `buda.annotate_topology(topo, fp)` /
            // `annotate_topology` in C++ — before ConnTopology::build.  See
            // docs/internal/single_source_topo_truth.md.
            {
                auto it = topo.seg_busterms.find(i);
                if (it != topo.seg_busterms.end()) {
                    const auto& opt = (ep == 0) ? it->second.first
                                                : it->second.second;
                    if (opt.has_value()) {
                        SegConn c;
                        c.kind       = SegConn::BUSTERM;
                        c.block_name = opt->block_name;
                        c.face_coord = ci.horiz ? P.x : P.y;
                        c.seg_idx    = -1;
                        c.at_pos     = ci.horiz ? P.x : P.y;
                        add_conn(i, std::move(c));
                        found = true;
                    }
                }
            }
            if (found) continue;

            // (c) seg-to-seg connection ----------------------------------------
            // Read from topo.seg_conns — the authoritative junction annotation
            // recorded ONCE at generation (annotate_seg_conns; topo-truth
            // Phase 4).  ConnTopology no longer scans the geometry for touching
            // segments: an endpoint with no record is a free end (or a tap,
            // handled above), never a cue to go guessing.  The junction's
            // positional labels (at_pos / is_endpoint) are DERIVED here from the
            // identified pair's nominal coordinates — geometry as value, the
            // record as the only oracle.  Iteration i-ascending / ep {start,end}
            // / others-ascending reproduces the retired scan's conn order
            // exactly (downstream maps and tie-breaks depend on it).
            auto rec = topo.seg_conns.find({i, ep});
            if (rec == topo.seg_conns.end()) continue;
            for (int j : rec->second) {
                if (j < 0 || j >= n || j == i) continue;  // stale/foreign record
                const ConnSeg& cj = segs[j];
                // Connection from i to j: at_pos = position along i
                int at_i = ci.horiz ? P.x : P.y;
                {
                    SegConn c;
                    c.kind    = SegConn::SEG;
                    c.seg_idx = j;
                    c.at_pos  = at_i;
                    c.is_endpoint = (at_i == ci.along_lo || at_i == ci.along_hi);
                    add_conn(i, std::move(c));
                }
                // Reciprocal T-junction from j to i: at_pos = position along j
                int at_j = ci.horiz ? P.y : P.x;
                {
                    SegConn c;
                    c.kind    = SegConn::SEG;
                    c.seg_idx = i;
                    c.at_pos  = at_j;
                    c.is_endpoint = (at_j == cj.along_lo || at_j == cj.along_hi);
                    add_conn(j, std::move(c));
                }
            }
        }
    }
}

// ── derive_slide_ranges ───────────────────────────────────────────────────────
//
// Two-pass constraint propagation from busterms → segments.
//
// Pass 1 — direct busterm constraints:
//   When a segment endpoint lies on a block face, the block's extent in the
//   perpendicular direction constrains the segment's perp_slide.
//
//     H segment on block's x-face  →  perp (y) ∈ [rect.y1+m, rect.y2-m]
//     V segment on block's y-face  →  perp (x) ∈ [rect.x1+m, rect.x2-m]
//
// Pass 2 — indirect via stub connections:
//   When a spine segment S connects to a stub T, and T's far end is anchored
//   to busterm B at face_coord f, then S must be on the outward side of f:
//
//     S is H spine, T is V stub anchored at B.y_face:
//       f == B.y1 (bottom face) → S.y ≤ B.y1   (spine below the block)
//       f == B.y2 (top    face) → S.y ≥ B.y2   (spine above the block)
//     S is V spine, T is H stub anchored at B.x_face:
//       f == B.x1 (left  face) → S.x ≤ B.x1
//       f == B.x2 (right face) → S.x ≥ B.x2

void derive_slide_ranges(const Topology& topo, const Floorplan& fp,
                         std::vector<ConnSeg>& segs) {
    (void)topo;
    std::map<std::string, Rect> bmap;
    for (auto& [name, rect] : fp.get_all_blocks()) bmap[name] = rect;

    // ── Pass 1 ──
    for (auto& cs : segs) {
        for (const auto& conn : cs.conns) {
            if (conn.kind != SegConn::BUSTERM) continue;

            // Block may not exist in this floorplan (e.g. a cell-local busterm
            // name checked against a different floorplan); skip its constraint
            // rather than throwing — mirrors the graceful misses in the other
            // Floorplan accessors (get_block_rects/bounds/corner_margin).
            auto bm_it = bmap.find(conn.block_name);
            if (bm_it == bmap.end()) continue;
            Rect face_rect = bm_it->second;
            {
                const auto rects = fp.get_block_rects(conn.block_name);
                if (!rects.empty()) {
                    if (cs.horiz) {
                        int u_y1 = INT_MAX, u_y2 = INT_MIN;
                        for (const Rect& r : rects) {
                            if ((r.x1 == conn.face_coord || r.x2 == conn.face_coord)
                                    && cs.perp_pos >= r.y1 && cs.perp_pos <= r.y2) {
                                u_y1 = std::min(u_y1, r.y1);
                                u_y2 = std::max(u_y2, r.y2);
                            }
                        }
                        if (u_y1 <= u_y2)
                            face_rect = Rect(face_rect.x1, u_y1, face_rect.x2, u_y2);
                    } else {
                        int u_x1 = INT_MAX, u_x2 = INT_MIN;
                        for (const Rect& r : rects) {
                            if ((r.y1 == conn.face_coord || r.y2 == conn.face_coord)
                                    && cs.perp_pos >= r.x1 && cs.perp_pos <= r.x2) {
                                u_x1 = std::min(u_x1, r.x1);
                                u_x2 = std::max(u_x2, r.x2);
                            }
                        }
                        if (u_x1 <= u_x2)
                            face_rect = Rect(u_x1, face_rect.y1, u_x2, face_rect.y2);
                    }
                }
            }

            BlockCornerMargin cm = fp.get_block_corner_margin(conn.block_name);
            if (cs.horiz) {
                int m  = cm.dy;
                int lo = face_rect.y1 + m;
                int hi = face_rect.y2 - m;
                if (lo > hi || cs.perp_pos < lo || cs.perp_pos > hi) {
                    lo = face_rect.y1; hi = face_rect.y2;
                }
                cs.perp_lo = std::max(cs.perp_lo, lo);
                cs.perp_hi = std::min(cs.perp_hi, hi);
            } else {
                int m  = cm.dx;
                int lo = face_rect.x1 + m;
                int hi = face_rect.x2 - m;
                if (lo > hi || cs.perp_pos < lo || cs.perp_pos > hi) {
                    lo = face_rect.x1; hi = face_rect.x2;
                }
                cs.perp_lo = std::max(cs.perp_lo, lo);
                cs.perp_hi = std::min(cs.perp_hi, hi);
            }
        }
    }

    // ── Pass 2 ──
    bool changed = true;
    while (changed) {
        changed = false;
        for (auto& cs : segs) {
            for (const auto& conn : cs.conns) {
                if (conn.kind != SegConn::SEG) continue;
                const ConnSeg& stub = segs[conn.seg_idx];

                for (const auto& sc : stub.conns) {
                    if (sc.kind != SegConn::BUSTERM) continue;

                    if (cs.horiz && !stub.horiz) {
                        // cs = H spine, stub = V stub anchored at rect's y-face
                        int f = sc.face_coord;
                        int new_lo = cs.perp_lo, new_hi = cs.perp_hi;

                        // Only enforce push-out if this is an actual relay stub (length > 0).
                        // Direct connections (perp_pos == f) are exempt from Pass 2 push-out.
                        // The spine must stay on the OUTWARD side of the stub's face f,
                        // ideally with the full min-stub clearance m.  When the full
                        // f±m clearance would empty the slide window against a pass-1
                        // busterm bound (e.g. the spine sits on a block edge), relax to
                        // the face f itself — a shorter stub but still the correct side —
                        // and drop the constraint only if even the face would invert
                        // (connectivity wins over the stub-length floor).
                        if (std::abs(cs.perp_pos - f) > 0) {
                            int m = fp.get_min_stub_length(1 /*VERTICAL*/, stub.layer_id);
                            if (cs.perp_pos > f) {
                                if      (f + m <= new_hi) new_lo = std::max(new_lo, f + m);
                                else if (f     <= new_hi) new_lo = std::max(new_lo, f);
                            } else {
                                if      (f - m >= new_lo) new_hi = std::min(new_hi, f - m);
                                else if (f     >= new_lo) new_hi = std::min(new_hi, f);
                            }
                        }

                        if (new_lo <= new_hi
                            && (new_lo != cs.perp_lo || new_hi != cs.perp_hi)) {
                            cs.perp_lo = new_lo; cs.perp_hi = new_hi;
                            changed = true;
                        }
                    } else if (!cs.horiz && stub.horiz) {
                        // cs = V spine, stub = H stub anchored at rect's x-face
                        int f = sc.face_coord;
                        int new_lo = cs.perp_lo, new_hi = cs.perp_hi;

                        // See the H-spine branch: keep the outward-side constraint,
                        // relaxing the full min-stub clearance to the face when it
                        // would otherwise empty the busterm-bounded slide window.
                        if (std::abs(cs.perp_pos - f) > 0) {
                            int m = fp.get_min_stub_length(0 /*HORIZONTAL*/, stub.layer_id);
                            if (cs.perp_pos > f) {
                                if      (f + m <= new_hi) new_lo = std::max(new_lo, f + m);
                                else if (f     <= new_hi) new_lo = std::max(new_lo, f);
                            } else {
                                if      (f - m >= new_lo) new_hi = std::min(new_hi, f - m);
                                else if (f     >= new_lo) new_hi = std::min(new_hi, f);
                            }
                        }

                        if (new_lo <= new_hi
                            && (new_lo != cs.perp_lo || new_hi != cs.perp_hi)) {
                            cs.perp_lo = new_lo; cs.perp_hi = new_hi;
                            changed = true;
                        }
                    }
                }
            }
        }
    }
}

// ── tighten_passthrough ───────────────────────────────────────────────────────
//
// For every segment that spans a connected block without an explicit BUSTERM
// endpoint on that segment, tighten [perp_lo, perp_hi] so that NUTS cannot
// slide the segment out of that block's perpendicular face extent.
//
// "Spans" means: at the segment's nominal perp_pos, the along-range overlaps
// at least one rect of the block.  The constraint applied is the union perp
// span of all such matching rects (with corner margin), intersected into the
// already-computed [perp_lo, perp_hi].

void tighten_passthrough(const Topology& topo, const Floorplan& fp,
                         std::vector<ConnSeg>& segs)
{
    // Blocks with a BUSTERM conn on ANY segment get their connectivity from
    // that stub.  A segment that merely grazes such a block at its nominal
    // perp_pos is not the connection to it and must stay free to slide away
    // (e.g. a U-detour's source stub crossing the dst block's perp extent).
    // Only blocks connected purely by pass-through (suppressed stubs) need
    // the tightening below.
    std::set<std::string> explicitly_connected;
    for (const auto& s : segs)
        for (const auto& conn : s.conns)
            if (conn.kind == SegConn::BUSTERM)
                explicitly_connected.insert(conn.block_name);

    // A segment "covers" a pass-through block when it spans one of the block's
    // rects.  Two grades, by where the segment's perp_pos lands relative to the
    // rect face:
    //   STRICT — perp_pos in the OPEN interior; the wire genuinely crosses the
    //            block (e.g. the trunk spine).  A real cover.
    //   GRAZE  — perp_pos exactly on a rect face; the wire only rides the boundary
    //            edge it shares with a neighbour.  A degenerate cover.
    // The clamp below keeps a pass-through block covered by pinning its coverer's
    // slide to the block's perp extent.  That clamp is applied with the original
    // INCLUSIVE span test (so overlap-avoidance on grazing pass-throughs is
    // preserved) — EXCEPT for one surgical de-pinch case handled at the
    // intersection: if the clamp would collapse a segment to a single coordinate
    // (zero slide) AND the block already has a strict-interior coverer, the
    // segment is only grazing the block's shared edge and is not its cover, so
    // pinning it to the face needlessly makes a bus-carrying stub unplaceable
    // (an io_pad_tr-bound stub riding blk_12's top edge went to zero slide and
    // unplaceable at NUTS).  has_strict_cover lets us tell that case from a SOLE
    // graze cover, which must stay clamped (dropping it would disconnect the
    // block; filter_pinched then rejects the over-constrained candidate).
    auto spans_rect = [](const ConnSeg& s, const Rect& r, bool strict) {
        if (s.horiz) {
            bool perp = strict ? (s.perp_pos > r.y1 && s.perp_pos < r.y2)
                               : (s.perp_pos >= r.y1 && s.perp_pos <= r.y2);
            return perp && s.along_lo <= r.x2 && s.along_hi >= r.x1;
        }
        bool perp = strict ? (s.perp_pos > r.x1 && s.perp_pos < r.x2)
                           : (s.perp_pos >= r.x1 && s.perp_pos <= r.x2);
        return perp && s.along_lo <= r.y2 && s.along_hi >= r.y1;
    };

    std::map<std::string, bool> has_strict_cover;
    for (const auto& bname : topo.connected_block_names) {
        if (explicitly_connected.count(bname)) continue;
        auto rects = fp.get_block_rects(bname);
        if (rects.empty()) rects.push_back(fp.get_block_bounds(bname));
        bool strict = false;
        for (const auto& s : segs) {
            for (const Rect& r : rects)
                if (spans_rect(s, r, /*strict=*/true)) { strict = true; break; }
            if (strict) break;
        }
        has_strict_cover[bname] = strict;
    }

    for (auto& cs : segs) {
        for (const auto& bname : topo.connected_block_names) {
            if (explicitly_connected.count(bname)) continue;

            auto rects = fp.get_block_rects(bname);
            if (rects.empty()) rects.push_back(fp.get_block_bounds(bname));

            // Union perp span and along span of every rect this segment spans
            // (INCLUSIVE of the boundary face — unchanged from the original; the
            // grazing de-pinch is handled at the intersection below, not here, so
            // non-pinching clamps behave exactly as before).
            int span_lo = INT_MAX, span_hi = INT_MIN;
            int along_lo_B = INT_MAX, along_hi_B = INT_MIN;
            bool cs_strict = false;
            for (const Rect& r : rects) {
                bool through = spans_rect(cs, r, /*strict=*/false);
                if (!through) continue;
                if (spans_rect(cs, r, /*strict=*/true)) cs_strict = true;
                if (cs.horiz) {
                    span_lo = std::min(span_lo, r.y1); span_hi = std::max(span_hi, r.y2);
                    along_lo_B = std::min(along_lo_B, r.x1); along_hi_B = std::max(along_hi_B, r.x2);
                } else {
                    span_lo = std::min(span_lo, r.x1); span_hi = std::max(span_hi, r.x2);
                    along_lo_B = std::min(along_lo_B, r.y1); along_hi_B = std::max(along_hi_B, r.y2);
                }
            }
            if (span_lo > span_hi) continue; // segment doesn't span this block

            BlockCornerMargin cm = fp.get_block_corner_margin(bname);
            int margin = cs.horiz ? cm.dy : cm.dx;
            int lo = span_lo + margin;
            int hi = span_hi - margin;
            if (lo > hi) { lo = span_lo; hi = span_hi; } // margin too large: skip
            // Guard against inverting the window: a segment that taps another block
            // (busterm) AND passes through this one can have a pre-set window that
            // the pass-through span does not overlap once a margin is applied; keep
            // the range valid rather than assert.
            int nlo = std::max(cs.perp_lo, lo);
            int nhi = std::min(cs.perp_hi, hi);
            if (nlo > nhi) continue;
            // Surgical de-pinch (see the spans_rect comment above): if this clamp
            // would collapse cs to a single coordinate but the block already has a
            // strict-interior coverer and cs only grazes it, cs is not the block's
            // cover — skip rather than pin a bus-carrying stub to zero slide.  A
            // sole graze cover (no strict coverer) stays clamped to keep the block
            // connected.  Non-pinching clamps always apply, so nothing else moves.
            if (nlo == nhi && !cs_strict && has_strict_cover[bname]) continue;
            cs.perp_lo = nlo;
            cs.perp_hi = nhi;

            // When cs passes through B via a suppressed stub, the spine segment T
            // connected at cs's endpoint must stay on the far side of B so that
            // do_span_adjustments cannot retract cs.span_hi (or span_lo) below B's
            // near face, which would break the pass-through at placed positions.
            for (const auto& conn : cs.conns) {
                if (conn.kind != SegConn::SEG || !conn.is_endpoint) continue;
                ConnSeg& T = segs[conn.seg_idx];
                // Guard against inverting T's window: the far-side constraint is a
                // best-effort tightening (connectivity hint), but [perp_lo,perp_hi]
                // must stay a valid range.  When the constraint would empty the
                // window (T is already bounded to the near side by its own busterm),
                // skip it rather than assert -- mirrors the new_lo<=new_hi guards in
                // compute_slide_ranges.  Relevant once orthogonal relays cover a
                // block purely by pass-through (no busterm), exercising this widely.
                if (conn.at_pos == cs.along_hi) {
                    // T is at hi end; after span adj, span_hi = T.track_position.
                    // Need T.track_position >= along_lo_B.
                    if (along_lo_B <= T.perp_hi)
                        T.perp_lo = std::max(T.perp_lo, along_lo_B);
                } else if (conn.at_pos == cs.along_lo) {
                    // T is at lo end; after span adj, span_lo = T.track_position.
                    // Need T.track_position <= along_hi_B.
                    if (along_hi_B >= T.perp_lo)
                        T.perp_hi = std::min(T.perp_hi, along_hi_B);
                }
            }
        }
    }
}

// ── pin_relay_taps ────────────────────────────────────────────────────────────
//
// A relay block keeps exactly ONE busterm tap (complete_relay_junctions' single-
// tap model); its other landings are demoted to SEG junctions and the landings
// are chained by a JOG / extension connector.  A BUSTERM conn clamps only the tap
// segment's PERPENDICULAR slide — but the tap's ALONG reach to face_coord is set
// by the connector attached at that SAME endpoint: after NUTS span adjustment the
// tap's span end follows that connector's placed perp position.  If the connector
// is free to slide arbitrarily, NUTS drags the whole staircase off the block and
// the tap no longer reaches it — a silent open at NUTS / dNUTS.
//
// The relay block is NOT a feedthru of this bundle, so the JOG / extension is
// routed OVER THE CELL (OTC).  Bound each such connector's perpendicular slide to
// the block's footprint extent: it may slide anywhere over the cell but never off
// it.  As long as the connector stays within [face_lo, face_hi], the tap's span
// end (which follows it) stays over the cell and still touches the block, so the
// along-reach is preserved — WITHOUT a degenerate zero-slide pin (which would
// leave NUTS no room to place a positive-width bus).  Intersect with the
// connector's existing window so we only tighten, never widen past a real
// constraint.
void pin_relay_taps(const Topology& topo, const Floorplan& fp,
                    std::vector<ConnSeg>& segs) {
    (void)topo;
    std::map<std::string, Rect> bmap;
    for (auto& [name, rect] : fp.get_all_blocks()) bmap[name] = rect;

    int n = (int)segs.size();
    for (int i = 0; i < n; ++i) {
        ConnSeg& cs = segs[i];
        for (const auto& bc : cs.conns) {
            if (bc.kind != SegConn::BUSTERM) continue;
            int f = bc.face_coord;
            // The busterm sits at one of cs's along endpoints (see check_topo).
            if (f != cs.along_lo && f != cs.along_hi) continue;
            auto bm_it = bmap.find(bc.block_name);
            if (bm_it == bmap.end()) continue;            // block not in this floorplan
            const Rect& bb = bm_it->second;
            // OTC window = the cell footprint in the connector's perp direction.
            for (const auto& sc : cs.conns) {
                if (sc.kind != SegConn::SEG || !sc.is_endpoint) continue;
                if (sc.at_pos != f) continue;
                ConnSeg& T = segs[sc.seg_idx];
                if (T.horiz == cs.horiz) continue;        // need a bend, not collinear
                int lo = T.horiz ? bb.y1 : bb.x1;
                int hi = T.horiz ? bb.y2 : bb.x2;
                int nlo = std::max(T.perp_lo, lo);
                int nhi = std::min(T.perp_hi, hi);
                if (nlo > nhi) continue;                  // empty: don't violate invariant
                if (T.perp_pos < nlo || T.perp_pos > nhi) continue;  // nominal outside window
                T.perp_lo = nlo;
                T.perp_hi = nhi;
            }
        }
    }
}

// Is stub cs (index ci) the BINDING far-extreme stub of trunk nb on the side
// away from a busterm in direction sign (+1 toward perp_hi → cs must be a lowest
// stub; -1 → highest)?  Only the extreme stub sets that trunk endpoint; a stub
// with another strictly further from the busterm, or a co-located stub that
// reaches less far toward it, does not.  Equal-reach co-located stubs all bind
// (they shorten the trunk only by sliding together — e.g. a TRUNK_V's symmetric
// top branch stubs), so there is no index tie-break.
static bool stub_binds_trunk_end(const std::vector<ConnSeg>& segs, int ci,
                                 const ConnSeg& cs, const ConnSeg& nb, int sign) {
    for (const auto& sc : nb.conns) {
        if (sc.kind == SegConn::BUSTERM) {
            // A direct trunk tap is an IMMOVABLE endpoint-setter: a tap further
            // from the target busterm than cs already pins that trunk end, so cs
            // sliding cannot shorten the trunk.  Without this, a TRUNK_H tapping
            // blocks at both ends with an interior stub would treat the stub as
            // the binding endpoint (no SEG lies past it) and pull it spuriously.
            if (sign > 0) { if (sc.face_coord < cs.perp_pos) return false; }
            else          { if (sc.face_coord > cs.perp_pos) return false; }
            continue;
        }
        if (sc.seg_idx == ci) continue;
        const ConnSeg& o = segs[sc.seg_idx];
        if (sign > 0) {
            if (o.perp_pos < cs.perp_pos) return false;
            if (o.perp_pos == cs.perp_pos && o.perp_hi < cs.perp_hi) return false;
        } else {
            if (o.perp_pos > cs.perp_pos) return false;
            if (o.perp_pos == cs.perp_pos && o.perp_lo > cs.perp_lo) return false;
        }
    }
    return true;
}

void derive_net_pull(const Topology& topo, const Floorplan& fp,
                     std::vector<ConnSeg>& segs) {
    (void)topo; (void)fp;
    for (int ci = 0; ci < (int)segs.size(); ++ci) {
        ConnSeg& cs = segs[ci];
        int pos = 0, neg = 0;
        for (const auto& conn : cs.conns) {
            if (conn.kind != SegConn::SEG) continue;
            const ConnSeg& nb = segs[conn.seg_idx];
            for (const auto& sc : nb.conns) {
                if (sc.kind != SegConn::BUSTERM) continue;
                int sign = (sc.face_coord > cs.perp_pos) ? +1
                         : (sc.face_coord < cs.perp_pos) ? -1 : 0;
                if (sign == 0) continue;
                // Pull cs toward nb's busterm only when cs actually sets a trunk
                // endpoint — otherwise an interior or freely-sliding stub gets
                // dragged to its slide bound for no wirelength gain (the spurious
                // blk_29 / io_pad_tr right-drift in big2 bus_077's TRUNK_H, where
                // only blk_02's capped stub should pull).  Two ways cs sets an
                // endpoint:
                //   (a) BINDING far-extreme: no other stub is further from the
                //       busterm, so cs holds the trunk's near end; or
                //   (b) ANCHORED at its busterm-side bound (perp_pos == perp_hi
                //       for +1 / perp_lo for -1): its block face already holds it
                //       as close to the busterm as it can get, and the pull keeps
                //       it there against being dragged off — e.g. an aligned
                //       sibling sinking to its interval floor (flow tc3a B20's
                //       two top stubs both anchored), or a dogleg spine endpoint.
                bool anchored = (sign > 0) ? (cs.perp_pos >= cs.perp_hi)
                                           : (cs.perp_pos <= cs.perp_lo);
                if (anchored || stub_binds_trunk_end(segs, ci, cs, nb, sign))
                    (sign > 0) ? ++pos : ++neg;
            }
        }
        for (const auto& conn : cs.conns) {
            if (conn.kind != SegConn::SEG) continue;
            const ConnSeg& nb = segs[conn.seg_idx];
            bool nb_has_bt = false;
            for (const auto& sc : nb.conns)
                if (sc.kind == SegConn::BUSTERM) { nb_has_bt = true; break; }
            if (nb_has_bt) continue;
            // nb is a floating spine (no busterm of its own): the pull comes
            // from the far segments reachable through it.  Compare against each
            // far segment's anchored slide interval [perp_lo, perp_hi], not its
            // nominal position — a far stub can slide anywhere within its block
            // face, so only a perp_pos OUTSIDE that interval produces a pull.
            // NUTS turns any nonzero pull into "slide to my own range extreme";
            // emitting a pull while already inside the far interval makes the
            // segment overshoot past its target (see flow/pull2.buda).
            //
            // This spine contributes a SINGLE unit of pull, not one per far
            // segment: sliding cs only shortens the spine if cs is an extreme
            // endpoint of it (all far segments on one side).  Shortening the
            // spine is worth one unit regardless of how many segments hang off
            // the other end — so a multi-fanout (multicast) spine must not
            // multiply the pull.  If far segments lie on both sides, cs is
            // interior and exerts no pull.
            int far_hi = 0, far_lo = 0;
            for (const auto& sc : nb.conns) {
                if (sc.kind != SegConn::SEG || sc.seg_idx == ci) continue;
                const ConnSeg& far = segs[sc.seg_idx];
                if      (cs.perp_pos < far.perp_lo) ++far_hi;   // far is above/right of cs
                else if (cs.perp_pos > far.perp_hi) ++far_lo;   // far is below/left  of cs
            }
            if      (far_hi > 0 && far_lo == 0) ++pos;   // cs is the low endpoint → pull toward hi
            else if (far_lo > 0 && far_hi == 0) ++neg;   // cs is the high endpoint → pull toward lo
        }
        cs.net_pull = pos - neg;
    }
}

// ── derive_along_flex ─────────────────────────────────────────────────────────
//
// The SECOND trunk DOF (Stage C of the flexible-root re-arch): a spine's two
// ALONG endpoints move independently, so its length is a range resolved by pull
// at NUTS time rather than a fixed generated coordinate.  For each segment mark
// whether each end is contractible (no busterm tap anchors it there), record the
// nominal along-coverage floor it must always reach, and emit a signed WL hint.
//
// This is pure ANNOTATION — it reads the already-inferred conns and extents and
// writes only the along_* fields.  NUTS (do_span_adjustments, Stage B) is the
// sole consumer; until then it is provably inert (no existing field is touched).
void derive_along_flex(const Topology& topo, const Floorplan& fp,
                       std::vector<ConnSeg>& segs) {
    (void)topo; (void)fp;
    for (auto& cs : segs) {
        // A busterm tap sits at one of the segment's along endpoints (check_topo
        // invariant).  An end carrying a tap is ANCHORED to that block face and
        // must not contract; an end with no tap is flex — free to contract toward
        // the interior down to the coverage floor.
        bool bt_at_lo = false, bt_at_hi = false;
        // Coverage floor: the smallest / largest along-coord the segment must
        // reach so every junction and busterm face it carries stays connected.
        // Seed with the current extent so a segment with no conns keeps its span.
        int cover_lo = cs.along_hi, cover_hi = cs.along_lo;
        for (const auto& c : cs.conns) {
            if (c.kind == SegConn::BUSTERM) {
                if (c.face_coord <= cs.along_lo) bt_at_lo = true;
                if (c.face_coord >= cs.along_hi) bt_at_hi = true;
                cover_lo = std::min(cover_lo, c.face_coord);
                cover_hi = std::max(cover_hi, c.face_coord);
            } else { // SEG junction: the along-coord where the partner attaches
                cover_lo = std::min(cover_lo, c.at_pos);
                cover_hi = std::max(cover_hi, c.at_pos);
            }
        }
        // No conns at all: nothing to contract to — pin the floor to the extent.
        if (cover_lo > cover_hi) { cover_lo = cs.along_lo; cover_hi = cs.along_hi; }
        // Never claim a floor outside the generated extent (defensive; keeps the
        // contraction target inside the segment NUTS actually places).
        cover_lo = std::max(cover_lo, cs.along_lo);
        cover_hi = std::min(cover_hi, cs.along_hi);

        cs.along_flex_lo  = !bt_at_lo;
        cs.along_flex_hi  = !bt_at_hi;
        cs.along_cover_lo = cover_lo;
        cs.along_cover_hi = cover_hi;

        // Signed WL hint: a flex end with dead wire between the extreme coverage
        // and the generated endpoint can be contracted for a WL gain.
        int pull = 0;
        if (cs.along_flex_hi && cover_hi < cs.along_hi) ++pull;   // shrink hi end
        if (cs.along_flex_lo && cover_lo > cs.along_lo) --pull;   // shrink lo end
        cs.along_pull = pull;
    }
}

} // namespace buda
