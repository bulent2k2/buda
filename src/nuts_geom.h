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
#include <algorithm>
#include <map>
#include <utility>
#include <vector>

// Shared geometry & metrics primitives of the NUTS stages (abstract + detailed)
// — docs/internal/nuts_dnuts_refactor.md Phase A.  Everything here is a
// verbatim move of the former nuts.cpp file-statics, so both stages (and the
// repair passes) share ONE definition of ordered spans, overlap semantics and
// the extend-only span-cover contract.

namespace buda {

// span_lo/span_hi carry NOMINAL endpoint identity and may be stored with
// span_lo > span_hi after placement (see do_span_adjustments).  Geometric tests
// — overlap detection, keepout/occupancy, block coverage — need the ORDERED
// extent, so they take these instead of reading span_lo/span_hi directly.
inline double sp_lo(const TrackSegment& s) { return std::min(s.span_lo, s.span_hi); }
inline double sp_hi(const TrackSegment& s) { return std::max(s.span_lo, s.span_hi); }

// Extend a (possibly reversed) span so it covers coordinate `c`, preserving
// nominal endpoint identity: whichever stored field currently holds the
// extreme being pushed out is the one that moves, so a reversed span keeps
// both endpoints (extend-only — an in-range `c` changes nothing).  The single
// definition of the coverage-guarantee body used by the abstract span
// adjustment (SEG partners + BUSTERM faces) and the detailed bit-span pass.
inline void span_cover(double& span_lo, double& span_hi, double c) {
    const double lo = std::min(span_lo, span_hi);
    const double hi = std::max(span_lo, span_hi);
    const bool ordered = (span_lo <= span_hi);
    if (c < lo)      (ordered ? span_lo : span_hi) = c;
    else if (c > hi) (ordered ? span_hi : span_lo) = c;
}

// KeepoutZones on the segment's layer that intersect its span, as occupied
// perpendicular intervals.
inline void keepout_occupied(const std::vector<KeepoutZone>& kozs,
                             const TrackSegment* t,
                             std::vector<std::pair<double,double>>& occ)
{
    for (const auto& koz : kozs) {
        if (!koz.layer_ids.count(t->layer)) continue;
        if (t->horiz) {
            // Horizontal segment: span in X, pos in Y.
            if (sp_lo(*t) < koz.bbox.x2 && sp_hi(*t) > koz.bbox.x1)
                occ.push_back({static_cast<double>(koz.bbox.y1),
                               static_cast<double>(koz.bbox.y2)});
        } else {
            // Vertical segment: span in Y, pos in X.
            if (sp_lo(*t) < koz.bbox.y2 && sp_hi(*t) > koz.bbox.y1)
                occ.push_back({static_cast<double>(koz.bbox.x1),
                               static_cast<double>(koz.bbox.x2)});
        }
    }
}

// Physical overlap test for two placed segments at their current (adjusted)
// spans.  Same-bundle pairs never conflict: their bits are the same nets and
// may share tracks (DetailedNUTS reservation exempts same-bundle segments).
inline bool segs_overlap(const TrackSegment& a, const TrackSegment& b)
{
    if (a.layer != b.layer || !a.placed || !b.placed) return false;
    if (a.bundle_id == b.bundle_id) return false;
    // Abstract segments are bit-bundles, not single wires, so the two touch axes
    // differ.  ALONG the routing direction (span), an end-to-end touch means the
    // bits butt up collinearly → a DRC: the span test is CLOSED (touch counts).
    // PERPENDICULAR (track), a parallel touch just means two bundles sit edge to
    // edge; intra-bundle spacing covers it → not a DRC: the perp test stays
    // STRICT.  So: spans overlap-or-touch AND tracks strictly overlap.
    if (sp_hi(a) < sp_lo(b) || sp_hi(b) < sp_lo(a)) return false;
    return a.track_position + a.width / 2.0 > b.track_position - b.width / 2.0 &&
           b.track_position + b.width / 2.0 > a.track_position - a.width / 2.0;
}

// All overlapping segment-index pairs {i<j}, via a per-layer span sweep-line:
// segments conflict only within a layer and only where their spans overlap, so
// sort each layer by span_lo and compare an entering segment against just the
// still-active set (segments whose span has not yet ended).  O(n log n + k) vs
// the former O(n^2); the emitted pair set is identical (segs_overlap stays the
// pairwise predicate, so same-bundle / track-disjoint pairs are filtered out).
inline std::vector<std::pair<int,int>> find_overlaps(
    const std::vector<TrackSegment>& segments)
{
    std::map<int, std::vector<int>> by_layer;
    for (int i = 0; i < (int)segments.size(); ++i)
        if (segments[i].placed) by_layer[segments[i].layer].push_back(i);

    std::vector<std::pair<int,int>> pairs;
    std::vector<int> active;
    for (auto& [layer, idx] : by_layer) {
        std::sort(idx.begin(), idx.end(), [&](int a, int b) {
            return sp_lo(segments[a]) < sp_lo(segments[b]);
        });
        active.clear();
        for (int i : idx) {
            const double lo_i = sp_lo(segments[i]);
            // Evict only segments whose span ended strictly before i starts —
            // a segment ending exactly at lo_i still TOUCHES i and must be
            // compared (segs_overlap treats touch as a conflict).  Ordered bounds:
            // span_lo/span_hi may be stored reversed (nominal endpoint identity).
            active.erase(std::remove_if(active.begin(), active.end(),
                [&](int a) { return sp_hi(segments[a]) < lo_i; }), active.end());
            for (int a : active)
                if (segs_overlap(segments[i], segments[a]))
                    pairs.push_back({std::min(i, a), std::max(i, a)});
            active.push_back(i);
        }
    }
    return pairs;
}

// Placed segments whose track (± half width) falls outside their hard interval.
inline int count_violations(const std::vector<TrackSegment>& segments)
{
    int v = 0;
    for (const auto& ts : segments) {
        if (!ts.placed) continue;
        if (ts.track_position - ts.width / 2.0 < ts.interval_lo ||
            ts.track_position + ts.width / 2.0 > ts.interval_hi)
            ++v;
    }
    return v;
}

inline void compute_metrics(NUTSResult& result)
{
    result.num_violations = count_violations(result.segments);
    result.num_overlaps   = 0;
    result.overlaps_per_layer.clear();
    result.overlap_details.clear();

    for (auto [i, j] : find_overlaps(result.segments)) {
        const auto& a = result.segments[i];
        const auto& b = result.segments[j];
        ++result.num_overlaps;
        ++result.overlaps_per_layer[a.layer];
        OverlapDetail od;
        od.layer   = a.layer;
        od.bid_a   = a.bundle_id;  od.seg_a = a.seg_idx;
        od.bid_b   = b.bundle_id;  od.seg_b = b.seg_idx;
        od.span_lo = std::max(sp_lo(a), sp_lo(b));   // ordered: spans may be reversed
        od.span_hi = std::min(sp_hi(a), sp_hi(b));
        od.perp_lo = std::max(a.track_position - a.width / 2.0,
                              b.track_position - b.width / 2.0);
        od.perp_hi = std::min(a.track_position + a.width / 2.0,
                              b.track_position + b.width / 2.0);
        result.overlap_details.push_back(od);
    }
}

} // namespace buda
