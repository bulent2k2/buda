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

#include "routing_grid.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <stdexcept>

namespace buda {

// ---------------------------------------------------------------------------
// TrackPattern
// ---------------------------------------------------------------------------

double TrackPattern::unit_pitch() const {
    double total = 0.0;
    for (const auto& s : slots)
        total += s.width + s.space_after;
    return total;
}

double TrackPattern::signal_density() const {
    double up = unit_pitch();
    if (up <= 0.0) return 0.0;
    double sig = 0.0;
    for (const auto& s : slots)
        if (s.type == "SIGNAL") sig += s.width;
    return sig / up;
}

NdrLayerGeom TrackPattern::ndr_geom() const {
    NdrLayerGeom g;
    std::vector<std::pair<double, double>> run;
    // `pending` is the distance accumulated since the last SIGNAL slot: its
    // own space_after plus any non-signal slots crossed.  It becomes that
    // slot's gap once the NEXT signal slot in the same run is seen, so a run
    // that ends at a rail never records a gap into it.
    double pending = 0.0;
    for (const auto& s : slots) {
        if (s.type == "SIGNAL") {
            if (!run.empty()) run.back().second = pending;
            run.push_back({s.width, 0.0});
            pending = s.space_after;
        } else {
            // A rail breaks the run: metal cannot cross it.
            if (run.size() > 0) { g.runs.push_back(run); run.clear(); }
            pending = 0.0;
        }
    }
    if (!run.empty()) g.runs.push_back(run);
    // The pattern TILES, so the LAST run of one period physically abuts the
    // FIRST run of the next whenever no rail separates them — i.e. the
    // period both ends and starts on a signal slot.  This is independent of
    // how many runs the period has: `SIGNAL POWER SIGNAL` has two runs of
    // one, yet a legal TWO-slot wire straddles the boundary (Codex P2 on
    // #717 — the earlier `runs.size() == 1` guard reported max 1 and would
    // have falsely refused it).  Splicing unconditionally is still wrong,
    // so the condition is on the rail, not on the run count.
    if (!slots.empty() && slots.front().type == "SIGNAL" &&
        slots.back().type == "SIGNAL" && g.runs.size() >= 1) {
        auto  first = g.runs.front();
        auto& last  = g.runs.back();
        last.back().second = slots.back().space_after;  // the gap it bridges
        if (g.runs.size() == 1) {
            // One run spanning the whole period: it continues into its own
            // next copy, so one more period's worth is the repeating unit.
            const size_t n = last.size();
            for (size_t i = 0; i < n; ++i) last.push_back(last[i]);
            // …and it does not stop THERE either.  With no rail anywhere in
            // the period the stretch of signal slots is endless, so the run
            // stored here is a repeating UNIT rather than the whole run, and
            // consumers extend it on demand.  Splicing exactly one boundary
            // capped every all-signal layer at two periods' worth: measured,
            // a one-slot period reported a longest run of 2 and refused a
            // 3-slot metal rule on a layer that can host any width.
            g.unbounded = true;
            g.period_slots = (int)n;
            // …but "no rail" is not "no end".  A BOUNDED pattern — a DEF
            // `TRACKS … DO n` enumerates its tracks — has exactly as many
            // slots as were declared, and a run cannot exceed that.  Without
            // this ceiling a metal rule wider than the design's entire track
            // count read as realizable (Codex P2 on #757).
            if (bounded && unit_pitch() > 0.0) {
                const double span = bound_hi - bound_lo;
                const int periods = span <= 0.0 ? 1
                    : (int)std::floor(span / unit_pitch() + 1e-9) + 1;
                g.max_run = periods * (int)n;
            }
        } else {
            // Distinct runs: the joined run is last + first.
            for (const auto& e : first) last.push_back(e);
        }
    }
    return g;
}

double TrackPattern::dilution_factor() const {
    double sd = signal_density();
    return (sd > 0.0) ? 1.0 / sd : 1.0;
}

std::vector<std::pair<double, TrackSlot>>
TrackPattern::tracks_in_range(double lo, double hi) const {
    double up = unit_pitch();
    if (up <= 0.0 || slots.empty() || lo > hi) return {};

    // A bounded pattern enumerates its tracks rather than describing a rule,
    // so a query outside the declared extent must return nothing rather than
    // tiles that do not exist (Phase 3b).  Clamping the QUERY is enough: the
    // generator below only emits centres inside [lo, hi].
    if (bounded) {
        lo = std::max(lo, bound_lo);
        hi = std::min(hi, bound_hi);
        if (lo > hi) return {};
    }

    // Calculate the first unit index n such that origin + n*up could contain centres >= lo.
    // std::floor handles negative offsets correctly.
    int n_start = static_cast<int>(std::floor((lo - origin) / up)) - 1;

    std::vector<std::pair<double, TrackSlot>> result;
    for (int n = n_start; ; ++n) {
        double unit_start = origin + static_cast<double>(n) * up;
        if (unit_start > hi) break;

        double pos = unit_start;
        for (const auto& slot : slots) {
            double centre = pos + slot.width / 2.0;
            if (centre >= lo && centre <= hi)
                result.push_back({centre, slot});
            pos += slot.width + slot.space_after;
        }
    }
    return result;
}

// ---------------------------------------------------------------------------
// RoutingGrid
// ---------------------------------------------------------------------------

const TrackPattern& RoutingGrid::effective_pattern_at(double x, double y) const {
    for (const auto& ov : overrides_) {
        if (x >= static_cast<double>(ov.region.x1) &&
            x <= static_cast<double>(ov.region.x2) &&
            y >= static_cast<double>(ov.region.y1) &&
            y <= static_cast<double>(ov.region.y2))
            return ov.pattern;
    }
    return global_pattern_;
}

std::vector<std::pair<double, TrackSlot>>
RoutingGrid::signal_tracks_in(double x, double lo, double hi) const {
    // Override lookup in REAL coordinates: `x` is the ALONG coordinate (an X
    // value on a horizontal layer, a Y value on a vertical one) and [lo, hi]
    // is the PERP interval — map per orientation before consulting
    // effective_pattern_at(x, y), else the region test runs transposed on
    // vertical layers (audit C11-02).  Same single-point perp sample (lo) as
    // before; the slice-accurate walk is for_each_signal_track_in_span.
    const TrackPattern& pat = is_horizontal_
        ? effective_pattern_at(x, lo)
        : effective_pattern_at(lo, x);
    auto all = pat.tracks_in_range(lo, hi);
    std::vector<std::pair<double, TrackSlot>> result;
    result.reserve(all.size());
    for (auto& p : all) {
        if (p.second.type == "SIGNAL") {
            bool blocked = false;
            for (const auto& gk : keepouts_) {
            const Rect& koz = gk.bbox;
                // p.first is the fixed coordinate of the track (Y if horizontal, X if vertical).
                // x is the coordinate along the track span (X if horizontal, Y if vertical).
                double px = is_horizontal_ ? x : p.first;
                double py = is_horizontal_ ? p.first : x;
                if (px >= koz.x1 && px <= koz.x2 &&
                    py >= koz.y1 && py <= koz.y2) {
                    blocked = true;
                    break;
                }
            }
            if (!blocked) result.push_back(std::move(p));
        }
    }
    return result;
}

void RoutingGrid::for_each_signal_track_in_span(
    double along_lo, double along_hi, double perp_lo, double perp_hi,
    const std::function<void(double, const TrackSlot&)>& fn) const {
    if (along_lo > along_hi) std::swap(along_lo, along_hi);
    if (perp_lo > perp_hi) return;
    const double mid = (along_lo + along_hi) / 2.0;

    // Perp slices: cut [perp_lo, perp_hi] at the perp edges of every override
    // whose ALONG range contains the span midpoint (the same along test
    // effective_pattern_at applies).  Each slice's pattern is then resolved
    // at the SLICE midpoint, so a boundary-touching override claims only its
    // own side — the old single perp_lo sample let an override ending exactly
    // on a Hanan row claim the entire (physically global-pattern) band above.
    std::vector<double> cuts = {perp_lo, perp_hi};
    for (const auto& ov : overrides_) {
        const double a1 = is_horizontal_ ? (double)ov.region.x1 : (double)ov.region.y1;
        const double a2 = is_horizontal_ ? (double)ov.region.x2 : (double)ov.region.y2;
        if (mid < a1 || mid > a2) continue;
        const double p1 = is_horizontal_ ? (double)ov.region.y1 : (double)ov.region.x1;
        const double p2 = is_horizontal_ ? (double)ov.region.y2 : (double)ov.region.x2;
        if (p1 > perp_lo && p1 < perp_hi) cuts.push_back(p1);
        if (p2 > perp_lo && p2 < perp_hi) cuts.push_back(p2);
    }
    std::sort(cuts.begin(), cuts.end());
    cuts.erase(std::unique(cuts.begin(), cuts.end()), cuts.end());

    for (size_t si = 0; si + 1 < cuts.size(); ++si) {
        const double s_lo = cuts[si], s_hi = cuts[si + 1];
        // Interior boundaries are half-open (a centre exactly on a cut
        // belongs to the slice above); the window ends stay closed, so a
        // no-override query walks exactly the classic [perp_lo, perp_hi].
        const bool hi_closed = (si + 2 == cuts.size());
        const double sample = 0.5 * (s_lo + s_hi);
        const TrackPattern& pat = effective_pattern_at(
            is_horizontal_ ? mid : sample, is_horizontal_ ? sample : mid);
        const double up = pat.unit_pitch();
        if (up <= 0.0 || pat.slots.empty()) continue;
        int n_start = static_cast<int>(std::floor((s_lo - pat.origin) / up)) - 1;
        for (int n = n_start; ; ++n) {
            const double unit_start = pat.origin + static_cast<double>(n) * up;
            if (unit_start > s_hi) break;
            double pos = unit_start;
            for (const auto& slot : pat.slots) {
                const double centre = pos + slot.width / 2.0;
                pos += slot.width + slot.space_after;
                if (centre < s_lo) continue;
                if (hi_closed ? (centre > s_hi) : (centre >= s_hi)) continue;
                if (slot.type != "SIGNAL") continue;
                bool blocked = false;
                for (const auto& gk : keepouts_) {
            const Rect& koz = gk.bbox;
                    const double k_p1 = is_horizontal_ ? koz.y1 : koz.x1;
                    const double k_p2 = is_horizontal_ ? koz.y2 : koz.x2;
                    const double k_a1 = is_horizontal_ ? koz.x1 : koz.y1;
                    const double k_a2 = is_horizontal_ ? koz.x2 : koz.y2;
                    if (centre >= k_p1 && centre <= k_p2 &&
                        along_lo <= k_a2 && along_hi >= k_a1) {
                        blocked = true;
                        break;
                    }
                }
                if (!blocked) fn(centre, slot);
            }
        }
    }
}

std::vector<std::pair<double, TrackSlot>>
RoutingGrid::signal_tracks_in_span(double along_lo, double along_hi,
                                   double perp_lo, double perp_hi) const {
    std::vector<std::pair<double, TrackSlot>> result;
    for_each_signal_track_in_span(
        along_lo, along_hi, perp_lo, perp_hi,
        [&](double centre, const TrackSlot& slot) {
            result.push_back({centre, slot});
        });
    return result;
}

std::vector<PreRoutedSegment> RoutingGrid::preroutes_in(
    double perp_lo, double perp_hi, double along_lo, double along_hi,
    bool include_signal) const
{
    std::vector<PreRoutedSegment> out;
    int idx = 0;
    auto emit = [&](double centre, const TrackSlot& slot,
                    double a_lo, double a_hi) {
        if (slot.type == "SIGNAL" && !include_signal) return;
        if (a_lo > a_hi) return;                 // empty along window
        PreRoutedSegment pr;
        pr.track_position = centre;
        pr.width          = slot.width;
        pr.span_lo        = a_lo;
        pr.span_hi        = a_hi;
        pr.label          = slot.label.empty() ? slot.type : slot.label;
        pr.slot_type      = slot.type;
        pr.track_index    = idx++;
        out.push_back(std::move(pr));
    };

    // Rect coords, orientation-resolved (H layer: perp = y, along = x):
    // {perp_lo, perp_hi, along_lo, along_hi}.
    auto rect_windows = [&](const Rect& r) {
        return std::array<double, 4>{
            is_horizontal_ ? (double)r.y1 : (double)r.x1,
            is_horizontal_ ? (double)r.y2 : (double)r.x2,
            is_horizontal_ ? (double)r.x1 : (double)r.y1,
            is_horizontal_ ? (double)r.x2 : (double)r.y2};
    };

    // Remove [s_lo, s_hi] from every piece (a piece may split in two).
    auto subtract = [](std::vector<std::pair<double, double>>& pieces,
                       double s_lo, double s_hi) {
        std::vector<std::pair<double, double>> next;
        for (const auto& [lo, hi] : pieces) {
            if (s_hi <= lo || s_lo >= hi) {   // no overlap
                next.push_back({lo, hi});
                continue;
            }
            if (s_lo > lo) next.push_back({lo, s_lo});
            if (s_hi < hi) next.push_back({s_hi, hi});
        }
        pieces = std::move(next);
    };

    // SIGNAL bands additionally break at keepouts: signal_tracks_in /
    // count_signal_tracks_in reject a SIGNAL track point inside a keepout, so
    // a rail drawn through one would show track a bit cannot land on.
    // Non-SIGNAL slots are untouched — a pre-route is a physical rail, and
    // keepouts block signal placement, not the existing grid.
    auto subtract_keepouts = [&](double centre, const TrackSlot& slot,
                                 std::vector<std::pair<double, double>>& pieces) {
        if (slot.type != "SIGNAL") return;
        for (const auto& gk : keepouts_) {
            const Rect& koz = gk.bbox;
            const auto [k_perp_lo, k_perp_hi, k_along_lo, k_along_hi] =
                rect_windows(koz);
            if (centre < k_perp_lo || centre > k_perp_hi) continue;
            subtract(pieces, k_along_lo, k_along_hi);
        }
    };

    // Global pattern: the along window MINUS every override shadow whose perp
    // range contains the slot centre — inside an override region the effective
    // pattern (what the solver samples) is the override's, so an unsplit
    // global band there would advertise tracks that don't exist — MINUS the
    // keepout shadows for SIGNAL slots.
    for (const auto& [centre, slot] : global_pattern_.tracks_in_range(perp_lo, perp_hi)) {
        std::vector<std::pair<double, double>> pieces{{along_lo, along_hi}};
        for (const auto& ov : overrides_) {
            const auto [r_perp_lo, r_perp_hi, r_along_lo, r_along_hi] =
                rect_windows(ov.region);
            if (centre < r_perp_lo || centre > r_perp_hi) continue;
            subtract(pieces, r_along_lo, r_along_hi);
        }
        subtract_keepouts(centre, slot, pieces);
        for (const auto& [lo, hi] : pieces)
            emit(centre, slot, lo, hi);
    }

    // IDENTIFIED KEEPOUTS ARE RAILS (specialnets_scope.md §5(b)).  A DEF
    // SPECIALNETS strap is not obstruction that happens to be there — it is
    // metal belonging to a power net, i.e. exactly what a PreRoutedSegment
    // represents.  Emitting it here is what lets the NDR credit / bond / audit
    // predicates see a power grid on an IMPORTED design: a LEF states a wire's
    // width and says nothing about which tracks a grid takes, so a synthesized
    // pattern is all-signal and carries no rail for them to find.  The rails
    // are in the DEF and nowhere else.
    //
    // One insertion point, three consumers: `credit_at`'s rail_covers_span,
    // the R9 audit and emit_shield_bond_vias all query preroutes, so a strap
    // reaches every one of them through the machinery they already use —
    // which is the single-sourcing R4 asks for, rather than three lookups.
    //
    // ANONYMOUS keepouts (`net` empty — a macro's OBS, a LAYER blockage, a
    // hand-declared add_keepout) are NOT emitted: they block, but there is
    // nothing to say about whose metal they are, and a consumer asking "is
    // there a VDD rail here" must never be answered by a block footprint.
    // `include_signal` does not gate them either — a rail is not a signal
    // track, so it belongs in the non-SIGNAL half of the result.
    for (const auto& gk : keepouts_) {
        if (gk.net.empty()) continue;
        const auto [k_perp_lo, k_perp_hi, k_along_lo, k_along_hi] =
            rect_windows(gk.bbox);
        // Selected by METAL OVERLAP, not by centre containment.  A pattern
        // slot is picked by its centre because a slot is never wider than its
        // own period; a strap has no such bound.  `flow/def/chip.def` draws
        // 2000-unit M6 straps on an 800-unit pitch, so a strap can abut the
        // run while its centre lies outside a one-period window — and a
        // centre test would report no metal where metal is touching (Codex
        // #785).  The reported track_position stays the centre, which is
        // where the metal actually is centred.
        const double centre = 0.5 * (k_perp_lo + k_perp_hi);
        if (k_perp_hi < perp_lo || k_perp_lo > perp_hi) continue;
        const double a_lo = std::max(along_lo, k_along_lo);
        const double a_hi = std::min(along_hi, k_along_hi);
        if (a_lo > a_hi) continue;
        TrackSlot rail;
        // The slot TYPE is derived from the net so an identity test that
        // falls back to the type (ndr_rail_credits does) still resolves; the
        // LABEL is the net itself, which is the authoritative answer.
        rail.type  = ndr_shield_net_matches("GND", gk.net) ? "GROUND"
                   : ndr_shield_net_matches("VDD", gk.net) ? "POWER"
                   : "CUSTOM";
        rail.label = gk.net;
        rail.width = k_perp_hi - k_perp_lo;
        const size_t before = out.size();
        emit(centre, rail, a_lo, a_hi);
        for (size_t i = before; i < out.size(); ++i) out[i].is_strap = true;
    }

    // Overrides: local pattern within (region ∩ perp window), span clipped to
    // (region ∩ along window), keepout shadows subtracted for SIGNAL slots.
    for (const auto& ov : overrides_) {
        const auto [r_perp_lo, r_perp_hi, r_along_lo, r_along_hi] =
            rect_windows(ov.region);
        const double p_lo = std::max(perp_lo,  r_perp_lo);
        const double p_hi = std::min(perp_hi,  r_perp_hi);
        const double a_lo = std::max(along_lo, r_along_lo);
        const double a_hi = std::min(along_hi, r_along_hi);
        if (p_lo > p_hi || a_lo > a_hi) continue;
        for (const auto& [centre, slot] : ov.pattern.tracks_in_range(p_lo, p_hi)) {
            std::vector<std::pair<double, double>> pieces{{a_lo, a_hi}};
            subtract_keepouts(centre, slot, pieces);
            for (const auto& [lo, hi] : pieces)
                emit(centre, slot, lo, hi);
        }
    }
    return out;
}

int RoutingGrid::count_signal_tracks_in(double x, double lo, double hi) const {
    // Count-only twin of signal_tracks_in: same tiling walk + SIGNAL/keepout
    // filter, but never allocates (returns the count directly).  Kept in lockstep
    // with tracks_in_range's loop and signal_tracks_in's filter above —
    // including the orientation-aware override lookup (audit C11-02).
    const TrackPattern& pat = is_horizontal_
        ? effective_pattern_at(x, lo)
        : effective_pattern_at(lo, x);
    const double up = pat.unit_pitch();
    if (up <= 0.0 || pat.slots.empty() || lo > hi) return 0;
    int n_start = static_cast<int>(std::floor((lo - pat.origin) / up)) - 1;
    int cnt = 0;
    for (int n = n_start; ; ++n) {
        double unit_start = pat.origin + static_cast<double>(n) * up;
        if (unit_start > hi) break;
        double pos = unit_start;
        for (const auto& slot : pat.slots) {
            double centre = pos + slot.width / 2.0;
            if (centre >= lo && centre <= hi && slot.type == "SIGNAL") {
                bool blocked = false;
                for (const auto& gk : keepouts_) {
            const Rect& koz = gk.bbox;
                    double px = is_horizontal_ ? x : centre;
                    double py = is_horizontal_ ? centre : x;
                    if (px >= koz.x1 && px <= koz.x2 &&
                        py >= koz.y1 && py <= koz.y2) { blocked = true; break; }
                }
                if (!blocked) ++cnt;
            }
            pos += slot.width + slot.space_after;
        }
    }
    return cnt;
}

int RoutingGrid::count_signal_tracks_in_span(double along_lo, double along_hi,
                                             double perp_lo, double perp_hi) const {
    // Count-only view of the SAME walker as signal_tracks_in_span — one
    // implementation (for_each_signal_track_in_span), so the vector and count
    // twins cannot drift; this exists because the planner's kPeak supply
    // floor only needs the count, not a materialized vector.
    int cnt = 0;
    for_each_signal_track_in_span(along_lo, along_hi, perp_lo, perp_hi,
                                  [&](double, const TrackSlot&) { ++cnt; });
    return cnt;
}

// ---------------------------------------------------------------------------
// RoutingGridStack
// ---------------------------------------------------------------------------

void RoutingGridStack::define_layer(int layer_id, const TrackPattern& pattern, bool is_horizontal) {
    layers_[layer_id].init(pattern, is_horizontal);
}

void RoutingGridStack::add_override(int layer_id,
                                    int x1, int y1, int x2, int y2,
                                    const TrackPattern& pattern) {
    PatternOverride ov;
    ov.region   = Rect{x1, y1, x2, y2};
    ov.layer_id = layer_id;
    ov.pattern  = pattern;
    layers_[layer_id].add_pattern_override(std::move(ov));
}

RoutingGrid& RoutingGridStack::get_layer_grid(int layer_id) {
    auto it = layers_.find(layer_id);
    if (it == layers_.end())
        throw std::out_of_range("RoutingGridStack: layer " +
                                std::to_string(layer_id) + " not defined");
    return it->second;
}

const RoutingGrid& RoutingGridStack::get_layer_grid(int layer_id) const {
    auto it = layers_.find(layer_id);
    if (it == layers_.end())
        throw std::out_of_range("RoutingGridStack: layer " +
                                std::to_string(layer_id) + " not defined");
    return it->second;
}

std::vector<PreRoutedSegment> RoutingGridStack::preroutes(
    int layer_id, double perp_lo, double perp_hi,
    double along_lo, double along_hi, bool include_signal) const
{
    auto out = get_layer_grid(layer_id).preroutes_in(perp_lo, perp_hi,
                                                     along_lo, along_hi,
                                                     include_signal);
    for (auto& pr : out) pr.layer = layer_id;
    return out;
}

bool RoutingGridStack::has_layer(int layer_id) const {
    return layers_.count(layer_id) > 0;
}

// THE rail lookup — see routing_grid.h.  One implementation, so R5a crediting
// at DNUTS and the R9 audit of the placed result cannot answer differently.
std::optional<PreRoutedSegment> ndr_credit_rail(
    const RoutingGrid& grid, const NdrSpec& spec, double edge, int dir,
    double window, double along_lo, double along_hi, bool allow_gap)
{
    if (dir == 0 || window <= 0) return std::nullopt;
    const double eps = 1e-6;
    const double w_lo = dir > 0 ? edge + eps : edge - window;
    const double w_hi = dir > 0 ? edge + window : edge - eps;
    if (w_lo > w_hi) return std::nullopt;

    // Every non-SIGNAL piece in the window: pattern rails AND straps, since
    // preroutes_in emits an identified keepout as one.
    auto pieces = grid.preroutes_in(w_lo, w_hi, along_lo, along_hi, false);
    if (pieces.empty()) return std::nullopt;

    // Group by track position — a rail broken by an override shadow arrives
    // as several pieces of one rail, and it is their UNION that must cover
    // the run.  Judging a piece alone would deny credit to a rail that is
    // continuous, which is the same "absent metal" mistake in reverse.
    std::map<long long, std::vector<const PreRoutedSegment*>> by_pos;
    for (const auto& pr : pieces)
        by_pos[(long long)std::llround(pr.track_position * 1e6)].push_back(&pr);

    // Nearest first: the question is what lies IMMEDIATELY beyond the edge.
    std::vector<std::pair<double, long long>> order;
    for (const auto& [k, v] : by_pos)
        order.emplace_back(std::abs(v.front()->track_position - edge), k);
    std::sort(order.begin(), order.end());

    // ONLY THE NEAREST RAIL DECIDES.  Walking on to a farther matching rail
    // would credit across intervening foreign metal: a VSS strap hard against
    // a VDD-shielded run, with a VDD strap just beyond it, would suppress the
    // VDD shield even though what actually flanks the run is VSS.  The rail
    // that matters is the one the emitted shield would have sat against, and
    // that is the nearest one, whatever its net (Codex #785 — the audit this
    // predicate replaced took the nearest rail and rejected on a mismatch).
    const auto& group = by_pos[order.front().second];
    const PreRoutedSegment& rep = *group.front();
    // A SIGNAL track between the edge and the rail means a bit could have sat
    // there, so the rail is not immediately adjacent and the shield it would
    // credit is not the one that would be emitted.
    const double s_lo = dir > 0 ? edge + eps : rep.track_position + eps;
    const double s_hi = dir > 0 ? rep.track_position - eps : edge - eps;
    if (!allow_gap && s_lo < s_hi &&
        grid.count_signal_tracks_in(0.5 * (along_lo + along_hi), s_lo, s_hi) > 0)
        return std::nullopt;
    if (!ndr_rail_credits(spec, rep.label, rep.slot_type)) return std::nullopt;
    // …and its metal must actually RUN the length of the segment: a rail
    // broken across the span is absent metal where it matters.
    std::vector<std::pair<double, double>> iv;
    for (const auto* p : group) iv.emplace_back(p->span_lo, p->span_hi);
    std::sort(iv.begin(), iv.end());
    double cov = along_lo;
    for (const auto& [a, b] : iv) {
        if (a > cov + eps) break;
        cov = std::max(cov, b);
    }
    if (cov >= along_hi - eps) return rep;
    return std::nullopt;
}

} // namespace buda
