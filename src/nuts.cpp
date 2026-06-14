#include "nuts.h"
#include "conn_topology.h"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <limits>
#include <map>
#include <numeric>
#include <set>

namespace buda {

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
    AlignMap&                                                     align_map)
{
    // Pass 1 — nominal perpendicular position from the topology.
    for (const auto& bw : bundles) {
        if (bw.input.candidates.empty() || bw.plan.selected_topology_index < 0) continue;
        const Topology& topo = bw.input.candidates[bw.plan.selected_topology_index];
        int bid = bw.input.original_bundle.id;
        for (int si = 0; si < (int)topo.segments.size(); ++si) {
            const Segment& seg = topo.segments[si];
            bool is_h  = (seg.start.y == seg.end.y);
            double nom = is_h ? static_cast<double>(seg.start.y)
                               : static_cast<double>(seg.start.x);
            pull_map[{bid, si}] = nom;
        }
    }

    // Pass 2 — connectivity-based override.
    for (const auto& bw : bundles) {
        if (bw.input.candidates.empty() || bw.plan.selected_topology_index < 0) continue;
        const Topology& topo = bw.input.candidates[bw.plan.selected_topology_index];
        int bid = bw.input.original_bundle.id;

        ConnTopology ct;
        ct.build(topo, floorplan);
        const auto& conn_segs = ct.segs();

        for (int si = 0; si < (int)conn_segs.size(); ++si) {
            const ConnSeg& cs = conn_segs[si];
            auto key = std::make_pair(bid, si);

            slide_map[key] = { static_cast<double>(cs.perp_lo),
                               static_cast<double>(cs.perp_hi) };

            int n_seg = 0, n_bt = 0;
            for (const auto& c : cs.conns) {
                if (c.kind == SegConn::SEG) ++n_seg;
                else                        ++n_bt;
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
            // net_pull < 0 → slide toward perp_lo.
            net_pull_map[key] = cs.net_pull;
            if (cs.net_pull != 0) {
                constexpr double kSentinel = 5e8;
                double preferred;
                if (cs.net_pull > 0)
                    preferred = (cs.perp_hi < kSentinel) ? static_cast<double>(cs.perp_hi)
                                                         : pull_map[key]; // fallback
                else
                    preferred = (cs.perp_lo > -kSentinel) ? static_cast<double>(cs.perp_lo)
                                                          : pull_map[key]; // fallback
                pull_map[key] = preferred;
            } else if (n_bt == 0 &&
                       si < (int)bw.plan.seg_perp.size() &&
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
    }

    // Pass 3 — alignment siblings: segments of one bundle that connect to the
    // same perpendicular segment (e.g. a multicast trunk's stubs on opposite
    // sides).  rev_conn_map[T] lists the segs whose span follows T; any two of
    // them sharing T are siblings.
    for (const auto& [t_key, followers] : rev_conn_map) {
        (void)t_key;
        for (size_t i = 0; i < followers.size(); ++i)
            for (size_t j = i + 1; j < followers.size(); ++j) {
                if (followers[i].src_bid != followers[j].src_bid) continue;
                auto ka = std::make_pair(followers[i].src_bid, followers[i].src_si);
                auto kb = std::make_pair(followers[j].src_bid, followers[j].src_si);
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

// `changed`, if non-null, collects the keys of segments whose span this call
// actually grew/shrank — the "stretched" set the corner-overlap pass keys off.
static void do_span_adjustments(
    const std::vector<TrackSegment*>&                               layer_segs,
    const std::map<std::pair<int,int>, std::vector<SpanAdjConn>>&   rev_conn_map,
    std::map<std::pair<int,int>, TrackSegment*>&                     ts_ptr_map,
    bool                                                             only_unplaced = false,
    std::set<std::pair<int,int>>*                                   changed = nullptr)
{
    struct AdjReq { double center; bool lo_end; bool is_endpoint; };
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
                {ts->track_position, sc.lo_end, sc.is_endpoint});
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

        for (const auto& req : reqs) {
            if (req.lo_end) {
                has_lo = true;
                min_lo = std::min(min_lo, req.center);
                if (!req.is_endpoint) all_lo_endpoints = false;
            } else {
                has_hi = true;
                max_hi = std::max(max_hi, req.center);
                if (!req.is_endpoint) all_hi_endpoints = false;
            }
        }

        const double old_lo = other->span_lo, old_hi = other->span_hi;
        if (has_lo) {
            if (all_lo_endpoints) other->span_lo = min_lo;
            else other->span_lo = std::min(other->span_lo, min_lo);
        }
        if (has_hi) {
            if (all_hi_endpoints) other->span_hi = max_hi;
            else other->span_hi = std::max(other->span_hi, max_hi);
        }
        if (changed && (other->span_lo != old_lo || other->span_hi != old_hi))
            changed->insert(key);
    }
}

// KeepoutZones on the segment's layer that intersect its span, as occupied
// perpendicular intervals.
static void keepout_occupied(const std::vector<KeepoutZone>& kozs,
                             const TrackSegment* t,
                             std::vector<std::pair<double,double>>& occ)
{
    for (const auto& koz : kozs) {
        if (!koz.layer_ids.count(t->layer)) continue;
        if (t->horiz) {
            // Horizontal segment: span in X, pos in Y.
            if (t->span_lo < koz.bbox.x2 && t->span_hi > koz.bbox.x1)
                occ.push_back({static_cast<double>(koz.bbox.y1),
                               static_cast<double>(koz.bbox.y2)});
        } else {
            // Vertical segment: span in Y, pos in X.
            if (t->span_lo < koz.bbox.y2 && t->span_hi > koz.bbox.y1)
                occ.push_back({static_cast<double>(koz.bbox.x1),
                               static_cast<double>(koz.bbox.x2)});
        }
    }
}

// Physical overlap test for two placed segments at their current (adjusted)
// spans.  Same-bundle pairs never conflict: their bits are the same nets and
// may share tracks (DetailedNUTS reservation exempts same-bundle segments).
static bool segs_overlap(const TrackSegment& a, const TrackSegment& b)
{
    if (a.layer != b.layer || !a.placed || !b.placed) return false;
    if (a.bundle_id == b.bundle_id) return false;
    if (a.span_hi <= b.span_lo || b.span_hi <= a.span_lo) return false;
    return a.track_position + a.width / 2.0 > b.track_position - b.width / 2.0 &&
           b.track_position + b.width / 2.0 > a.track_position - a.width / 2.0;
}

// All overlapping segment-index pairs {i<j}, via a per-layer span sweep-line:
// segments conflict only within a layer and only where their spans overlap, so
// sort each layer by span_lo and compare an entering segment against just the
// still-active set (segments whose span has not yet ended).  O(n log n + k) vs
// the former O(n^2); the emitted pair set is identical (segs_overlap stays the
// pairwise predicate, so same-bundle / track-disjoint pairs are filtered out).
static std::vector<std::pair<int,int>> find_overlaps(
    const std::vector<TrackSegment>& segments)
{
    std::map<int, std::vector<int>> by_layer;
    for (int i = 0; i < (int)segments.size(); ++i)
        if (segments[i].placed) by_layer[segments[i].layer].push_back(i);

    std::vector<std::pair<int,int>> pairs;
    std::vector<int> active;
    for (auto& [layer, idx] : by_layer) {
        std::sort(idx.begin(), idx.end(), [&](int a, int b) {
            return segments[a].span_lo < segments[b].span_lo;
        });
        active.clear();
        for (int i : idx) {
            const double lo_i = segments[i].span_lo;
            // Evict segments whose span ended at/before i starts.
            active.erase(std::remove_if(active.begin(), active.end(),
                [&](int a) { return segments[a].span_hi <= lo_i; }), active.end());
            for (int a : active)
                if (segs_overlap(segments[i], segments[a]))
                    pairs.push_back({std::min(i, a), std::max(i, a)});
            active.push_back(i);
        }
    }
    return pairs;
}

static void compute_metrics(NUTSResult& result)
{
    result.num_violations = 0;
    result.num_overlaps   = 0;
    result.overlaps_per_layer.clear();
    result.overlap_details.clear();

    for (const auto& ts : result.segments) {
        if (!ts.placed) continue;
        if (ts.track_position - ts.width / 2.0 < ts.interval_lo ||
            ts.track_position + ts.width / 2.0 > ts.interval_hi)
            ++result.num_violations;
    }

    for (auto [i, j] : find_overlaps(result.segments)) {
        const auto& a = result.segments[i];
        const auto& b = result.segments[j];
        ++result.num_overlaps;
        ++result.overlaps_per_layer[a.layer];
        OverlapDetail od;
        od.layer   = a.layer;
        od.bid_a   = a.bundle_id;  od.seg_a = a.seg_idx;
        od.bid_b   = b.bundle_id;  od.seg_b = b.seg_idx;
        od.span_lo = std::max(a.span_lo, b.span_lo);
        od.span_hi = std::min(a.span_hi, b.span_hi);
        od.perp_lo = std::max(a.track_position - a.width / 2.0,
                              b.track_position - b.width / 2.0);
        od.perp_hi = std::min(a.track_position + a.width / 2.0,
                              b.track_position + b.width / 2.0);
        result.overlap_details.push_back(od);
    }
}

void NUTSEngine::repair_overlaps(
    std::vector<TrackSegment>& segments,
    const std::map<std::pair<int,int>, double>&                pull_map,
    const std::map<std::pair<int,int>, int>&                   net_pull_map,
    const AlignMap&                                            align_map,
    const std::map<std::pair<int,int>, std::vector<SpanAdjConn>>& rev_conn_map,
    std::map<std::pair<int,int>, TrackSegment*>&               ts_ptr_map) const
{
    auto initial = find_overlaps(segments);
    if (initial.empty()) return;

    // Snapshot for the non-regression guard: a move re-adjusts follower
    // spans, which can surface new overlaps elsewhere.
    struct Snap { double pos, lo, hi; };
    std::vector<Snap> snapshot;
    snapshot.reserve(segments.size());
    for (const auto& ts : segments)
        snapshot.push_back({ts.track_position, ts.span_lo, ts.span_hi});

    const auto kozs = low_keepouts();
    auto pull_of = [&](const TrackSegment& ts) {
        auto it = net_pull_map.find({ts.bundle_id, ts.seg_idx});
        return it == net_pull_map.end() ? 0 : it->second;
    };

    // Per-move snapshot: a move re-adjusts follower spans globally, so a
    // locally good move can surface overlaps elsewhere.  Each move is
    // accepted only if the global overlap count strictly drops; otherwise
    // just that move is rolled back — earlier accepted moves are kept.
    auto take_snap = [&](std::vector<Snap>& s) {
        s.clear();
        s.reserve(segments.size());
        for (const auto& ts : segments)
            s.push_back({ts.track_position, ts.span_lo, ts.span_hi});
    };
    auto restore_snap = [&](const std::vector<Snap>& s) {
        for (size_t k = 0; k < segments.size(); ++k) {
            segments[k].track_position = s[k].pos;
            segments[k].span_lo        = s[k].lo;
            segments[k].span_hi        = s[k].hi;
        }
    };

    int moved = 0;
    std::vector<Snap> pre_move;
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
                if (&o == victim || !o.placed) continue;
                if (o.layer != victim->layer) continue;
                if (o.bundle_id == victim->bundle_id) continue;
                if (o.span_lo < victim->span_hi && victim->span_lo < o.span_hi) {
                    const double h = o.width / 2.0;
                    occ.push_back({o.track_position - h, o.track_position + h});
                }
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
            take_snap(pre_move);
            victim->track_position = pos;
            // Settle followers of the moved segment (and theirs, cheaply).
            std::vector<TrackSegment*> all_placed;
            for (auto& ts : segments) if (ts.placed) all_placed.push_back(&ts);
            do_span_adjustments(all_placed, rev_conn_map, ts_ptr_map);
            if (find_overlaps(segments).size() >= before) {
                restore_snap(pre_move);   // this move made things no better
                continue;
            }
            ++moved;
            progress = true;
        }
        if (!progress) break;
    }

    auto remaining = find_overlaps(segments);
    if (remaining.size() >= initial.size()) {
        // No strict improvement: restore the pre-repair state.
        restore_snap(snapshot);
        return;
    }
    if (moved > 0)
        std::cout << "[NUTS] overlap repair: moved " << moved
                  << " segment(s), overlaps " << initial.size()
                  << " -> " << remaining.size() << ".\n";
}

void NUTSEngine::resolve_corner_overlaps(
    std::vector<TrackSegment>& segments,
    const std::map<std::pair<int,int>, double>&                pull_map,
    const std::map<std::pair<int,int>, int>&                   net_pull_map,
    const AlignMap&                                            align_map,
    const std::map<std::pair<int,int>, std::vector<SpanAdjConn>>& rev_conn_map,
    std::map<std::pair<int,int>, TrackSegment*>&               ts_ptr_map,
    const std::set<std::pair<int,int>>&                        stretched) const
{
    using Key = std::pair<int,int>;
    if (stretched.empty()) return;                 // nothing grew → no corner overlaps
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

    std::map<int, OrderConstraints> by_layer_preds;  // trunk layer → ordering DAG
    auto add_edge = [&](const Key& lo, const Key& hi) -> bool {
        auto it = idx_of.find(hi);
        if (it == idx_of.end()) return false;
        return by_layer_preds[segments[it->second].layer][hi].insert(lo).second;
    };

    std::set<Key> stretched_now = stretched;

    for (int iter = 0; iter < 6; ++iter) {
        auto pairs = find_overlaps(segments);
        if (pairs.empty()) break;

        bool new_edge = false;
        std::set<int> dirty_layers;
        for (auto [i, j] : pairs) {
            Key kp{segments[i].bundle_id, segments[i].seg_idx};
            Key kq{segments[j].bundle_id, segments[j].seg_idx};
            if (!stretched_now.count(kp) && !stretched_now.count(kq)) continue;
            auto tp = trunk_of.find(kp), tq = trunk_of.find(kq);
            if (tp == trunk_of.end() || tq == trunk_of.end()) continue;
            Key trunk_p = tp->second.first, trunk_q = tq->second.first;
            if (trunk_p == trunk_q) continue;
            auto pit = idx_of.find(trunk_p), qit = idx_of.find(trunk_q);
            if (pit == idx_of.end() || qit == idx_of.end()) continue;
            if (segments[pit->second].layer != segments[qit->second].layer) continue;
            double ap = anchored_coord(segments[i], tp->second.second);
            double aq = anchored_coord(segments[j], tq->second.second);
            // Lower anchored end ⇒ its trunk takes the lower track.
            Key lo_trunk = (ap < aq) ? trunk_p : trunk_q;
            Key hi_trunk = (ap < aq) ? trunk_q : trunk_p;
            if (add_edge(lo_trunk, hi_trunk)) {
                new_edge = true;
                dirty_layers.insert(segments[idx_of[hi_trunk]].layer);
            }
        }
        if (!new_edge) break;     // no resolvable corner overlap (or a cycle)

        // Snapshot for the stop-&-reverse guard.
        struct Snap { double pos, lo, hi; bool placed; };
        std::vector<Snap> snap; snap.reserve(segments.size());
        for (const auto& ts : segments)
            snap.push_back({ts.track_position, ts.span_lo, ts.span_hi, ts.placed});
        const size_t before = pairs.size();

        // Re-solve each affected trunk layer under the accumulated constraints.
        for (int layer : dirty_layers) {
            std::vector<TrackSegment*> layer_segs;
            for (auto& ts : segments)
                if (ts.layer == layer) {
                    ts.track_position = std::numeric_limits<double>::quiet_NaN();
                    ts.placed = false;
                    layer_segs.push_back(&ts);
                }
            solve_layer(layer_segs, pull_map, align_map, by_layer_preds[layer]);
        }
        // Re-fit connected spans (refresh the stretched set) and repair residue.
        std::vector<TrackSegment*> all_placed;
        for (auto& ts : segments) if (ts.placed) all_placed.push_back(&ts);
        stretched_now.clear();
        do_span_adjustments(all_placed, rev_conn_map, ts_ptr_map, false, &stretched_now);
        repair_overlaps(segments, pull_map, net_pull_map, align_map, rev_conn_map, ts_ptr_map);

        if (find_overlaps(segments).size() >= before) {
            for (size_t k = 0; k < segments.size(); ++k) {   // stop & reverse
                segments[k].track_position = snap[k].pos;
                segments[k].span_lo        = snap[k].lo;
                segments[k].span_hi        = snap[k].hi;
                segments[k].placed         = snap[k].placed;
            }
            break;
        }
        std::cout << "[NUTS] corner-overlap pass: overlaps " << before
                  << " -> " << find_overlaps(segments).size() << ".\n";
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


std::vector<TrackSegment> NUTSEngine::extract_segments(
    const std::vector<BundleWrapper>& bundles,
    const std::vector<int>& x_grid,
    const std::vector<int>& y_grid) const
{
    std::vector<TrackSegment> result;
    for (const auto& bw : bundles) {
        if (bw.input.candidates.empty() || bw.plan.selected_topology_index < 0) continue;
        const Topology& topo = bw.input.candidates[bw.plan.selected_topology_index];
        for (int si = 0; si < (int)topo.segments.size(); ++si) {
            const Segment& seg = topo.segments[si];
            const bool is_horizontal = (seg.start.y == seg.end.y);
            TrackSegment ts;
            ts.bundle_id = bw.input.original_bundle.id;
            ts.seg_idx   = si;
            ts.horiz     = is_horizontal;

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
            ts.width = layers_.eff_bus_width(
                (int)bw.input.original_bundle.get_net_names().size(), bw.input.width, lid);

            if (ts.horiz) {
                ts.span_lo = std::min(seg.start.x, seg.end.x);
                ts.span_hi = std::max(seg.start.x, seg.end.x);
                // Unlock Hanan bands: use full chip boundary as initial interval.
                ts.interval_lo = static_cast<double>(y_grid.front());
                ts.interval_hi = static_cast<double>(y_grid.back());
            } else {
                ts.span_lo = std::min(seg.start.y, seg.end.y);
                ts.span_hi = std::max(seg.start.y, seg.end.y);
                ts.interval_lo = static_cast<double>(x_grid.front());
                ts.interval_hi = static_cast<double>(x_grid.back());
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
    double best      = std::numeric_limits<double>::quiet_NaN();
    double best_dist = std::numeric_limits<double>::max();
    for (double c : candidates) {
        if (!valid(c)) continue;
        double dist = std::abs(c - preferred);
        if (dist < best_dist) { best_dist = dist; best = c; }
    }
    return best;
}

void NUTSEngine::solve_layer(std::vector<TrackSegment*>& segs,
                              const std::map<std::pair<int,int>, double>& pull_map,
                              const AlignMap& align_map,
                              const OrderConstraints& order_preds) const {
    if (segs.empty()) return;
    // Same-layer lookup for alignment siblings (and ordering-constraint phase 0).
    std::map<std::pair<int,int>, TrackSegment*> layer_map;
    for (TrackSegment* ts : segs)
        layer_map[{ts->bundle_id, ts->seg_idx}] = ts;
    // Incorporate KeepoutZones into 'occupied' list (user zones + leaf-cell
    // zones on LOW layers; TOP segments are filtered out by layer_ids).
    auto kozs = low_keepouts();

    auto add_keepout_occ = [&](const TrackSegment* t,
                               std::vector<std::pair<double,double>>& occ) {
        keepout_occupied(kozs, t, occ);
    };

    // Occupancy a candidate placement must avoid: every already-placed segment
    // of another bundle whose span overlaps ts (same-bundle bits may share
    // tracks), plus keepouts.  Span-overlap rather than a sweep active set, so
    // pull anchors pre-placed in phase 1 are seen when packing phase 2 — and
    // for a single pull-free pass this is equivalent to the old active-set
    // sweep, since in span order an overlapping earlier segment is exactly one
    // that has started and not yet ended.
    auto build_occupied = [&](const TrackSegment* ts,
                              std::vector<std::pair<double,double>>& occ) {
        for (const TrackSegment* o : segs) {
            if (o == ts || !o->placed || o->bundle_id == ts->bundle_id) continue;
            if (o->span_lo < ts->span_hi && ts->span_lo < o->span_hi) {
                const double h = o->width / 2.0;
                occ.push_back({o->track_position - h, o->track_position + h});
            }
        }
        add_keepout_occ(ts, occ);
    };

    // Local repack: when no gap fits ts, earlier centre-seeking placements
    // may have fragmented a window that has room for everyone (two 51-wide
    // trunks in a 110-wide shared slide window: the first at the centre
    // leaves two 29.5 slivers).  Re-place ts together with the active
    // segments contending for its interval, packing from the low edge,
    // against everything else already placed.  Commits only on full success.
    auto try_repack = [&](TrackSegment* ts) -> bool {
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
            if (o->interval_lo < ts->interval_hi &&
                ts->interval_lo < o->interval_hi)
                members.push_back(o);
        }
        if (members.size() < 2) return false;
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

        std::vector<std::pair<TrackSegment*,double>> repacked;
        for (TrackSegment* m : members) {
            std::vector<std::pair<double,double>> occ;
            add_keepout_occ(m, occ);
            // Placed segments outside the repack set (including ones whose
            // sweep interval already ended) that overlap m's span.
            // Same-bundle segments never conflict (bits may share tracks).
            for (const TrackSegment* o : segs) {
                if (!o->placed || member_set.count(o)) continue;
                if (o->bundle_id == m->bundle_id) continue;
                if (o->span_lo < m->span_hi && m->span_lo < o->span_hi) {
                    const double h = o->width / 2.0;
                    occ.push_back({o->track_position - h, o->track_position + h});
                }
            }
            for (const auto& [pm, ppos] : repacked) {
                if (pm->bundle_id == m->bundle_id) continue;
                // Members conflict only where their spans overlap — same
                // physical criterion as the outside-set obstacles above.
                // Without this, disjoint-span members sharing one window
                // (e.g. blk-local buses left and right of a cross-chip
                // trunk, all at the same Hanan band) are packed as if
                // simultaneous and wedge a window that has room for all.
                if (!(pm->span_lo < m->span_hi && m->span_lo < pm->span_hi))
                    continue;
                const double h = pm->width / 2.0;
                occ.push_back({ppos - h, ppos + h});
            }
            std::sort(occ.begin(), occ.end());
            double p = first_fit(m->interval_lo, m->interval_hi, m->width, occ);
            if (std::isnan(p)) return false;   // window truly full: keep old state
            repacked.push_back({m, p});
        }
        for (const auto& [pm, ppos] : repacked) pm->track_position = ppos;
        return true;
    };

    // Place one segment at its preferred track, avoiding current occupancy.
    // lb (optional) is a hard lower bound on the track CENTER — used by phase 0
    // to keep a constrained segment above its predecessors.
    auto place_seg = [&](TrackSegment* ts,
                         double lb = -std::numeric_limits<double>::infinity(),
                         bool pack_low = false) {
        std::vector<std::pair<double,double>> occupied;
        build_occupied(ts, occupied);
        std::sort(occupied.begin(), occupied.end());

        // Honor the ordering lower bound by raising the band's low edge passed
        // to preferred_fit (without mutating the stored hard interval).
        double eff_lo = ts->interval_lo;
        if (lb > -std::numeric_limits<double>::infinity())
            eff_lo = std::max(eff_lo, lb - ts->width / 2.0);
        const double c_lo = eff_lo + ts->width / 2.0;
        const double c_hi = ts->interval_hi - ts->width / 2.0;
        auto key = std::make_pair(ts->bundle_id, ts->seg_idx);

        // Alignment: a placed same-layer sibling (same bundle, connected to the
        // same perpendicular segment) whose position fits this segment's
        // interval — sharing its band saves a whole track.
        double preferred = std::numeric_limits<double>::quiet_NaN();
        auto ait = align_map.find(key);
        if (ait != align_map.end()) {
            for (const auto& sk : ait->second) {
                auto lit = layer_map.find(sk);
                if (lit == layer_map.end() || !lit->second->placed) continue;
                double p = lit->second->track_position;
                if (p >= c_lo && p <= c_hi) { preferred = p; break; }
            }
        }
        if (std::isnan(preferred)) {
            auto it = pull_map.find(key);
            preferred = (it != pull_map.end())
                        ? it->second
                        : (ts->interval_lo + ts->interval_hi) / 2.0;
        }
        preferred = std::clamp(preferred, c_lo, c_hi);
        // Ordering-constrained segments pack to their lowest feasible track
        // (bottom-edge assignment) so segments that must sit above them still
        // fit; others seek their preferred track.
        double pos = pack_low
            ? first_fit(eff_lo, ts->interval_hi, ts->width, occupied)
            : preferred_fit(eff_lo, ts->interval_hi, ts->width, occupied, preferred);
        if (!std::isnan(pos))            ts->track_position = pos;
        else if (!pack_low && try_repack(ts)) { /* repacked */ }
        else                             ts->track_position = (eff_lo + ts->interval_hi) / 2.0;
        ts->placed = true;
    };

    // Phase 0 — ordering-constrained segments (corner-overlap resolution): place
    // each in dependency order (after every segment that must sit below it),
    // clamped just above its placed predecessors.  They become placed anchors
    // the normal anchor/sweep phases then avoid.
    std::set<std::pair<int,int>> constrained;
    for (const auto& [k, preds] : order_preds) {
        if (layer_map.count(k)) constrained.insert(k);
        for (const auto& p : preds)
            if (layer_map.count(p)) constrained.insert(p);
    }
    if (!constrained.empty()) {
        std::set<std::pair<int,int>> done;
        auto lb_of = [&](const std::pair<int,int>& k, const TrackSegment* ts) {
            double lb = -std::numeric_limits<double>::infinity();
            auto it = order_preds.find(k);
            if (it != order_preds.end())
                for (const auto& p : it->second) {
                    auto lit = layer_map.find(p);
                    if (lit == layer_map.end() || !lit->second->placed) continue;
                    lb = std::max(lb, lit->second->track_position
                                      + lit->second->width / 2.0
                                      + ts->width / 2.0 + track_pitch_);
                }
            return lb;
        };
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
                TrackSegment* ts = layer_map[*it];
                place_seg(ts, lb_of(*it, ts), /*pack_low=*/true);
                done.insert(*it);
                it = todo.erase(it);
                progress = true;
            }
        }
        // Cycle fallback: place whatever's left with its already-placed preds.
        for (const auto& k : todo) {
            TrackSegment* ts = layer_map[k];
            place_seg(ts, lb_of(k, ts), /*pack_low=*/true);
            done.insert(k);
        }
    }

    // Phase 1 — anchor pulled segments first at their preferred (pull-extreme)
    // positions, strongest pull then tightest interval first, so the pull-free
    // segments fill the residual gaps in phase 2.  A pull-free trunk then slides
    // off any track a pulled bus needs, instead of grabbing it by sweep order
    // and forcing the pulled bus to detour (item A: planner6 Bundle 3/4).
    std::vector<TrackSegment*> pulled, free_segs;
    for (TrackSegment* ts : segs) {
        if (constrained.count({ts->bundle_id, ts->seg_idx})) continue;  // placed in phase 0
        (ts->net_pull != 0 ? pulled : free_segs).push_back(ts);
    }
    std::stable_sort(pulled.begin(), pulled.end(),
        [](const TrackSegment* a, const TrackSegment* b) {
            int pa = std::abs(a->net_pull), pb = std::abs(b->net_pull);
            if (pa != pb) return pa > pb;                       // strongest pull first
            return (a->interval_hi - a->interval_lo)
                 < (b->interval_hi - b->interval_lo);           // tightest window first
        });
    for (TrackSegment* ts : pulled) place_seg(ts);

    // Phase 2 — sweep the pull-free segments in span order; each sees the
    // anchors and earlier free segments via build_occupied and slides to the
    // nearest free track.
    std::stable_sort(free_segs.begin(), free_segs.end(),
        [](const TrackSegment* a, const TrackSegment* b) {
            return a->span_lo < b->span_lo;
        });
    for (TrackSegment* ts : free_segs) place_seg(ts);
}

void NUTSEngine::set_extra_grid_points(std::vector<int> xs, std::vector<int> ys) {
    std::sort(xs.begin(), xs.end());
    xs.erase(std::unique(xs.begin(), xs.end()), xs.end());
    std::sort(ys.begin(), ys.end());
    ys.erase(std::unique(ys.begin(), ys.end()), ys.end());
    extra_x_ = std::move(xs);
    extra_y_ = std::move(ys);
}

NUTSResult NUTSEngine::run(const std::vector<BundleWrapper>& bundles) {
    std::vector<int> x_grid, y_grid;
    floorplan_.get_hanan_grid(x_grid, y_grid);
    merge_grid(x_grid, extra_x_);
    merge_grid(y_grid, extra_y_);
    NUTSResult result;
    result.segments = extract_segments(bundles, x_grid, y_grid);
    std::map<std::pair<int,int>, double>                         pull_map;
    std::map<std::pair<int,int>, std::pair<double,double>>       slide_map;
    std::set<std::pair<int,int>>                                 trunk_set;
    std::set<std::pair<int,int>>                                 busterm_set;
    std::map<std::pair<int,int>, std::vector<SpanAdjConn>>       rev_conn_map;
    std::map<std::pair<int,int>, int>                            net_pull_map;
    AlignMap                                                     align_map;
    build_nuts_maps(bundles, floorplan_, pull_map, slide_map, trunk_set, busterm_set, rev_conn_map, net_pull_map, align_map);
    apply_interval_constraints(result.segments, slide_map, trunk_set, net_pull_map, -1);
    relax_boundary_intervals(result.segments, pull_map, net_pull_map, busterm_set);
    std::map<std::pair<int,int>, TrackSegment*> ts_ptr_map;
    for (auto& ts : result.segments)
        ts_ptr_map[{ts.bundle_id, ts.seg_idx}] = &ts;
    std::map<int, std::vector<TrackSegment*>> by_layer;
    for (auto& ts : result.segments)
        by_layer[ts.layer].push_back(&ts);
    std::set<std::pair<int,int>> stretched;   // spans grown by span adjustment
    for (auto& [layer_id, layer_segs] : by_layer) {
        solve_layer(layer_segs, pull_map, align_map);
        do_span_adjustments(layer_segs, rev_conn_map, ts_ptr_map, false, &stretched);
    }
    // Final pass for all layers to catch cross-layer adjustments from the last-solved layer.
    for (auto& [layer_id, layer_segs] : by_layer) {
        do_span_adjustments(layer_segs, rev_conn_map, ts_ptr_map, false, &stretched);
    }
    // The final adjustments can extend spans of layers packed earlier,
    // materialising overlaps after their solve — repair them in place.
    repair_overlaps(result.segments, pull_map, net_pull_map, align_map,
                    rev_conn_map, ts_ptr_map);
    // Corner overlaps (perp-locked stubs grown into each other) need trunk
    // reordering, not victim moves — resolve them via ordering constraints.
    resolve_corner_overlaps(result.segments, pull_map, net_pull_map, align_map,
                            rev_conn_map, ts_ptr_map, stretched);
    compute_metrics(result);
    std::cout << "[NUTS] " << result.segments.size() << " segments placed across "
              << by_layer.size() << " layer(s). "
              << "Interval violations: " << result.num_violations << ", "
              << "Track overlaps: " << result.num_overlaps << ".\n";
    return result;
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
    for (auto& ts : result.segments) {
        if (ts.layer != layer_id) continue;
        ts.track_position = std::numeric_limits<double>::quiet_NaN();
        ts.placed         = false;
    }
    std::map<std::pair<int,int>, double>                         pull_map;
    std::map<std::pair<int,int>, std::pair<double,double>>       slide_map;
    std::set<std::pair<int,int>>                                 trunk_set;
    std::set<std::pair<int,int>>                                 busterm_set;
    std::map<std::pair<int,int>, std::vector<SpanAdjConn>>       rev_conn_map;
    std::map<std::pair<int,int>, int>                            net_pull_map;
    AlignMap                                                     align_map;
    build_nuts_maps(bundles, floorplan_, pull_map, slide_map, trunk_set, busterm_set, rev_conn_map, net_pull_map, align_map);
    apply_interval_constraints(result.segments, slide_map, trunk_set, net_pull_map, layer_id);
    relax_boundary_intervals(result.segments, pull_map, net_pull_map, busterm_set, layer_id);
    std::map<std::pair<int,int>, TrackSegment*> ts_ptr_map;
    for (auto& ts : result.segments)
        ts_ptr_map[{ts.bundle_id, ts.seg_idx}] = &ts;
    std::vector<TrackSegment*> layer_segs;
    for (auto& ts : result.segments)
        if (ts.layer == layer_id) layer_segs.push_back(&ts);
    solve_layer(layer_segs, pull_map, align_map);
    // Final pass for all layers to catch cross-layer adjustments from the re-solved layer.
    std::vector<TrackSegment*> all_placed;
    for (auto& ts : result.segments) if (ts.placed) all_placed.push_back(&ts);
    std::set<std::pair<int,int>> stretched;
    do_span_adjustments(all_placed, rev_conn_map, ts_ptr_map, false, &stretched);
    repair_overlaps(result.segments, pull_map, net_pull_map, align_map,
                    rev_conn_map, ts_ptr_map);
    resolve_corner_overlaps(result.segments, pull_map, net_pull_map, align_map,
                            rev_conn_map, ts_ptr_map, stretched);
    compute_metrics(result);
    std::cout << "[NUTS] rerun_layer(" << layer_id << "): "
              << layer_segs.size() << " segment(s) re-placed. "
              << "Violations: " << result.num_violations << ", "
              << "Overlaps: " << result.num_overlaps << ".\n";
    return result;
}

} // namespace buda
