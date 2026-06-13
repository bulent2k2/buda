#include "congestion_planner.h"
#include "conn_topology.h"
#include <iostream>
#include <algorithm>
#include <limits>
#include <map>
#include <numeric>
#include <cmath>

namespace buda {
CongestionPlanner::CongestionPlanner(const Floorplan& fp, const LayerStack& ls)
    : floorplan_(fp), layers_(ls) {}

void CongestionPlanner::set_planner_param(const std::string& name, double value) {

    if      (name == "kCong")             kCong_             = value;
    else if (name == "kSpan")             kSpan_             = value;
    else if (name == "base_cost_non_top") base_cost_non_top_ = value;
    else if (name == "kWL")               kWL_               = value;
    else if (name == "base_span_ref")     base_span_ref_     = value;
    else std::cout << "[Planner] Warning: unknown param '" << name << "'\n";
}

// ---------------------------------------------------------------------------
// Band lookup
// ---------------------------------------------------------------------------

// For a V-cut (is_vcut=true) the perpendicular direction is Y → use y_grid_.
// For an H-cut (is_vcut=false) the perpendicular direction is X → use x_grid_.
// Returns band index b such that grid[b] <= perp_pos < grid[b+1], or -1.
int CongestionPlanner::find_band(bool is_vcut, int perp_pos) const {
    const auto& grid = is_vcut ? y_grid_ : x_grid_;
    int n = (int)grid.size();
    if (n < 2) return -1;
    // Binary search.
    int lo = 0, hi = n - 2;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        if      (perp_pos <  grid[mid])   hi = mid - 1;
        else if (perp_pos >= grid[mid+1]) lo = mid + 1;
        else                               return mid;
    }
    // Edge: exactly on the last grid line.
    if (perp_pos == grid[n-1]) return n - 2;
    return -1;
}

// ---------------------------------------------------------------------------
// Cut construction — 2D (per-band capacity)
// ---------------------------------------------------------------------------

// Available perpendicular length within a single Hanan band at a cut.
static double band_available_length(
        int cut_coord, bool is_vcut,
        const std::vector<std::pair<std::string,Rect>>& blocks,
        const std::vector<KeepoutZone>& keepouts,
        int layer_id,
        int band_lo, int band_hi)
{
    std::vector<std::pair<int,int>> blocked;
    // 1. Block footprints
    for (const auto& [name, r] : blocks) {
        bool covers = is_vcut
            ? (cut_coord >= r.x1 && cut_coord <= r.x2)
            : (cut_coord >= r.y1 && cut_coord <= r.y2);
        if (!covers) continue;
        int lo = is_vcut ? r.y1 : r.x1;
        int hi = is_vcut ? r.y2 : r.x2;
        int clo = std::max(lo, band_lo);
        int chi = std::min(hi, band_hi);
        if (clo < chi) blocked.push_back({clo, chi});
    }
    // 2. Keepout zones for this layer
    for (const auto& koz : keepouts) {
        if (!koz.layer_ids.count(layer_id)) continue;
        const Rect& r = koz.bbox;
        bool covers = is_vcut
            ? (cut_coord >= r.x1 && cut_coord <= r.x2)
            : (cut_coord >= r.y1 && cut_coord <= r.y2);
        if (!covers) continue;
        int lo = is_vcut ? r.y1 : r.x1;
        int hi = is_vcut ? r.y2 : r.x2;
        int clo = std::max(lo, band_lo);
        int chi = std::min(hi, band_hi);
        if (clo < chi) blocked.push_back({clo, chi});
    }

    std::sort(blocked.begin(), blocked.end());
    double avail = static_cast<double>(band_hi - band_lo);
    int cur = band_lo;
    for (auto [lo, hi] : blocked) {
        if (lo > cur) cur = lo;
        if (hi > cur) { avail -= (hi - cur); cur = hi; }
    }
    return std::max(avail, 0.0);
}

void CongestionPlanner::build_congestion_map() {
    floorplan_.get_hanan_grid(x_grid_, y_grid_);
    rebuild_cuts_();
}

void CongestionPlanner::rebuild_cuts_() {
    cuts_.clear();
    if (x_grid_.size() < 2 || y_grid_.size() < 2) return;

    auto blocks   = floorplan_.get_all_blocks();
    auto keepouts = floorplan_.get_keepout_zones();
    blocks_cache_ = blocks;
    int n_ybands = (int)y_grid_.size() - 1;
    int n_xbands = (int)x_grid_.size() - 1;

    auto v_layers = layers_.get_layer_ids_by_dir(LayerDir::VERTICAL);
    auto h_layers = layers_.get_layer_ids_by_dir(LayerDir::HORIZONTAL);
    if (v_layers.empty()) v_layers.push_back(5);
    if (h_layers.empty()) h_layers.push_back(4);

    // V-cuts: one per (X-channel midpoint, H-layer).
    // Perpendicular direction = Y → bands indexed by y_grid_.
    for (int i = 0; i + 1 < (int)x_grid_.size(); ++i) {
        int x_mid = (x_grid_[i] + x_grid_[i+1]) / 2;
        for (int lid : h_layers) {
            bool is_top = layers_.get_layer_type(lid) == LayerType::TOP;
            GlobalCut c;
            c.p1        = {x_mid, y_grid_.front()};
            c.p2        = {x_mid, y_grid_.back()};
            c.cut_coord = x_mid;
            c.dir       = LayerDir::VERTICAL;
            c.layer_id  = lid;
            c.init_bands(n_ybands, [&](int b) {
                return is_top
                    ? band_available_length(x_mid, true, {}, keepouts, lid, y_grid_[b], y_grid_[b+1])
                    : band_available_length(x_mid, true, blocks, keepouts, lid, y_grid_[b], y_grid_[b+1]);
            });
            cuts_.push_back(std::move(c));
        }
    }

    // H-cuts: one per (Y-channel midpoint, V-layer).
    // Perpendicular direction = X → bands indexed by x_grid_.
    for (int i = 0; i + 1 < (int)y_grid_.size(); ++i) {
        int y_mid = (y_grid_[i] + y_grid_[i+1]) / 2;
        for (int lid : v_layers) {
            bool is_top = layers_.get_layer_type(lid) == LayerType::TOP;
            GlobalCut c;
            c.p1        = {x_grid_.front(), y_mid};
            c.p2        = {x_grid_.back(),  y_mid};
            c.cut_coord = y_mid;
            c.dir       = LayerDir::HORIZONTAL;
            c.layer_id  = lid;
            c.init_bands(n_xbands, [&](int b) {
                return is_top
                    ? band_available_length(y_mid, false, {}, keepouts, lid, x_grid_[b], x_grid_[b+1])
                    : band_available_length(y_mid, false, blocks, keepouts, lid, x_grid_[b], x_grid_[b+1]);
            });
            cuts_.push_back(std::move(c));
        }
    }

    // Report minimum per-band capacity per layer.
    std::cout << "[Planner] Layer channel capacities:\n";
    for (int vid : v_layers) {
        double min_cap = std::numeric_limits<double>::max();
        for (const auto& c : cuts_)
            if (c.layer_id == vid && c.dir == LayerDir::HORIZONTAL)
                for (int b = 0; b < c.num_bands(); ++b) min_cap = std::min(min_cap, c.cap(b));
        if (min_cap < std::numeric_limits<double>::max())
            std::cout << "  M" << vid << " (V)  min_band_cap=" << min_cap << "\n";
    }
    for (int hid : h_layers) {
        double min_cap = std::numeric_limits<double>::max();
        for (const auto& c : cuts_)
            if (c.layer_id == hid && c.dir == LayerDir::VERTICAL)
                for (int b = 0; b < c.num_bands(); ++b) min_cap = std::min(min_cap, c.cap(b));
        if (min_cap < std::numeric_limits<double>::max())
            std::cout << "  M" << hid << " (H)  min_band_cap=" << min_cap << "\n";
    }
}

// ---------------------------------------------------------------------------
// Per-segment 2D score and apply
// ---------------------------------------------------------------------------

// Invoke fn(cut_index, band) for every cut/band this segment loads at the
// given layer — the single matching rule shared by scoring, application,
// contention collection, and victim-overlap ranking.
// For H-segments: V-cuts on that H-layer, in the Y-band of the segment.
// For V-segments: H-cuts on that V-layer, in the X-band of the segment.
//
// Non-TOP layers: segments run centre-to-centre, but the portion inside an
// ENDPOINT block is not routed on a block-obstructed layer — the connection
// lands on the block face.  Charging the in-block cuts would price every
// block-attached segment at cap=0 (9999) on every lower layer, making the
// non-TOP stack unusable for stubs.  Clamp the along-extent to the endpoint
// block faces before matching cuts.  Blocks merely crossed mid-span still
// block normally (capacity already excludes them).
void CongestionPlanner::for_each_band(const Segment& seg, int layer_id,
                                      int perp_pos_override,
                                      const std::function<void(int, int)>& fn) const {
    bool is_h = (seg.start.y == seg.end.y);
    int  pp_h = (perp_pos_override != INT_MIN) ? perp_pos_override : seg.start.y;
    int  pp_v = (perp_pos_override != INT_MIN) ? perp_pos_override : seg.start.x;

    int lo = is_h ? std::min(seg.start.x, seg.end.x) : std::min(seg.start.y, seg.end.y);
    int hi = is_h ? std::max(seg.start.x, seg.end.x) : std::max(seg.start.y, seg.end.y);
    if (!layers_.is_top(layer_id)) {
        int perp = is_h ? seg.start.y : seg.start.x;
        for (const auto& [name, r] : blocks_cache_) {
            int rlo = is_h ? r.x1 : r.y1, rhi = is_h ? r.x2 : r.y2;
            int plo = is_h ? r.y1 : r.x1, phi = is_h ? r.y2 : r.x2;
            if (perp < plo || perp > phi) continue;
            bool lo_in = (lo >= rlo && lo <= rhi);
            bool hi_in = (hi >= rlo && hi <= rhi);
            if (lo_in && hi_in) { lo = hi; break; }  // fully inside: nothing routed here
            if (lo_in) lo = std::min(rhi, hi);       // left/bottom endpoint → block face
            if (hi_in) hi = std::max(rlo, lo);       // right/top endpoint → block face
        }
    }

    for (int ci = 0; ci < (int)cuts_.size(); ++ci) {
        const GlobalCut& c = cuts_[ci];
        if (c.layer_id != layer_id) continue;
        if (is_h && c.dir == LayerDir::VERTICAL) {
            if (!(c.cut_coord >= lo && c.cut_coord < hi)) continue;
            int b = find_band(/*is_vcut=*/true, pp_h);
            if (b >= 0 && b < c.num_bands()) fn(ci, b);
        } else if (!is_h && c.dir == LayerDir::HORIZONTAL) {
            if (!(c.cut_coord >= lo && c.cut_coord < hi)) continue;
            int b = find_band(/*is_vcut=*/false, pp_v);
            if (b >= 0 && b < c.num_bands()) fn(ci, b);
        }
    }
}

// Score the marginal peak overflow from adding one segment at a specific layer.
double CongestionPlanner::score_segment(const Segment& seg, int layer_id,
                                   double eff_width, int perp_pos_override,
                                   int slide_lo, int slide_hi) const {
    bool   is_vcut_dir = (seg.start.y == seg.end.y);   // H-seg crosses V-cuts
    double peak = 0.0;
    for_each_band(seg, layer_id, perp_pos_override, [&](int ci, int b) {
        const GlobalCut& c = cuts_[ci];
        double cap = usable_band_cap(c, b, is_vcut_dir, slide_lo, slide_hi);
        double ov  = (c.usage(b) + eff_width) - cap;
        if (ov > peak) peak = ov;
    });
    return std::max(peak, 0.0);
}

// The band-set sibling of score_segment: records WHICH bands overflow rather
// than reducing to a scalar.  Bands whose overflow stems purely from the
// slide-window clamp (zero usage) still get recorded but are harmless for
// victim ranking — no committed bundle loads them, so they contribute zero
// overlap.
void CongestionPlanner::collect_overflow_bands(const Segment& seg, int layer_id,
                                               double eff_width, int perp_pos_override,
                                               int slide_lo, int slide_hi,
                                               std::set<std::pair<int,int>>& out) const {
    bool is_vcut_dir = (seg.start.y == seg.end.y);
    for_each_band(seg, layer_id, perp_pos_override, [&](int ci, int b) {
        const GlobalCut& c = cuts_[ci];
        double cap = usable_band_cap(c, b, is_vcut_dir, slide_lo, slide_hi);
        if ((c.usage(b) + eff_width) - cap > 0.0) out.insert({ci, b});
    });
}

// Total effective width a committed plan contributes to the given band set.
// Rip-up victims are ranked by this: ripping a bundle that loads none of the
// contended bands cannot relieve the failing bundle.
double CongestionPlanner::plan_band_overlap(const BundleWrapper& bw,
                                            const PlanResult& plan,
                                            const std::set<std::pair<int,int>>& contended) const {
    const int nbits = (int)bw.original_bundle.get_net_names().size();
    const Topology& t = bw.candidates[plan.best_topo];
    double overlap = 0.0;
    for (int si = 0; si < (int)t.segments.size() && si < (int)plan.seg_layers.size(); ++si) {
        int pp  = (si < (int)plan.seg_perp.size()) ? plan.seg_perp[si] : INT_MIN;
        int lid = plan.seg_layers[si];
        double eff = layers_.eff_bus_width(nbits, bw.width, lid);
        for_each_band(t.segments[si], lid, pp, [&](int ci, int b) {
            if (contended.count({ci, b})) overlap += eff;
        });
    }
    return overlap;
}

double CongestionPlanner::usable_band_cap(const GlobalCut& c, int b, bool is_vcut,
                                          int slide_lo, int slide_hi) const {
    double cap = c.cap(b);
    if (slide_lo == INT_MIN) return cap;
    const auto& grid = is_vcut ? y_grid_ : x_grid_;
    if (b + 1 >= (int)grid.size()) return cap;
    double win = std::min(grid[b + 1], slide_hi) -
                 std::max(grid[b],     slide_lo);
    return std::min(cap, std::max(win, 0.0));
}

void CongestionPlanner::apply_segment(const Segment& seg, int layer_id, double eff_width,
                                      int perp_pos_override) {
    for_each_band(seg, layer_id, perp_pos_override, [&](int ci, int b) {
        cuts_[ci].add_usage(b, eff_width);
    });
}

// Park (sign=+1) or release (sign=-1) an unplanned bundle's demand as virtual
// usage on the TOP-layer bands inside its reservation region (the parent cell
// instance bbox).  Because congestion cost is overflow-based, this repels an
// earlier-planned bundle from a region band ONLY when the band cannot hold
// both of them — it is a "leave room" constraint, not a keep-out.
void CongestionPlanner::apply_reservation(const BundleWrapper& bw, double sign) {
    if (!bw.has_reservation) return;
    const int nbits = (int)bw.original_bundle.get_net_names().size();
    int top_h = layers_.get_top_layer(LayerDir::HORIZONTAL);
    int top_v = layers_.get_top_layer(LayerDir::VERTICAL);
    for (auto& c : cuts_) {
        // The bundle's H demand rides V-cuts on the TOP H layer; its V demand
        // rides H-cuts on the TOP V layer.
        bool is_vcut = (c.dir == LayerDir::VERTICAL);
        int  lid     = is_vcut ? top_h : top_v;
        if (lid < 0 || c.layer_id != lid) continue;
        // Cut must lie inside the region along the cut axis.
        int clo = is_vcut ? bw.res_x1 : bw.res_y1;
        int chi = is_vcut ? bw.res_x2 : bw.res_y2;
        if (c.cut_coord < clo || c.cut_coord > chi) continue;
        double eff = layers_.eff_bus_width(nbits, bw.width, lid);
        // Every band overlapping the region's perpendicular range could be
        // the bundle's eventual home, so each carries the reservation.
        const auto& grid = is_vcut ? y_grid_ : x_grid_;
        int plo = is_vcut ? bw.res_y1 : bw.res_x1;
        int phi = is_vcut ? bw.res_y2 : bw.res_x2;
        for (int b = 0; b + 1 < (int)grid.size() && b < c.num_bands(); ++b) {
            if (grid[b + 1] <= plo || grid[b] >= phi) continue;
            c.add_usage(b, sign * eff);
        }
    }
}

// ---------------------------------------------------------------------------
// Span-aware cost helpers
// ---------------------------------------------------------------------------

// Overflow congestion cost: kCong * max(0, (usage+eff-cap)/cap).
// Returns zero when the segment fits within the cut-band capacity, and a
// positive cost proportional to the overflow only when it doesn't.
// This means Z/U topologies are only preferred over I when I genuinely
// overflows a cut — not merely because they exploit cut-boundary effects.
double CongestionPlanner::cong_cost_segment(const Segment& seg, int layer_id,
                                       double eff_width, int perp_pos_override,
                                       int slide_lo, int slide_hi) const {
    bool   is_vcut_dir = (seg.start.y == seg.end.y);   // H-seg crosses V-cuts
    double peak_cost   = 0.0;
    bool   blocked     = false;
    for_each_band(seg, layer_id, perp_pos_override, [&](int ci, int b) {
        const GlobalCut& c = cuts_[ci];
        double cap = usable_band_cap(c, b, is_vcut_dir, slide_lo, slide_hi);
        if (cap <= 0.0) { blocked = true; return; }
        double ov = c.usage(b) + eff_width - cap;
        if (ov <= 0.0) return;     // fits — no cost
        peak_cost = std::max(peak_cost, kCong_ * ov / cap);
    });
    if (blocked) return kCong_ * 9999.0;
    return peak_cost;
}

// Slide-aware band choice.  cong_cost_segment charges the whole bus to the
// single band containing the lookup coordinate; using the slide-interval
// centre for that lookup is a point estimate that can land in an arbitrarily
// narrow band even though NUTS may slide the bus into a wide neighbouring
// band (e.g. a chip-to-chip I_H centring on the thin sliver between two
// block rows).  Scan every band the slide interval overlaps by at least
// eff_width and return the cheapest usable coordinate instead.
int CongestionPlanner::best_band_perp(const Segment& seg, int layer_id,
                                      double eff_width,
                                      int slide_lo, int slide_hi) const {
    bool is_h = (seg.start.y == seg.end.y);
    const auto& grid = is_h ? y_grid_ : x_grid_;
    const int centre = (slide_lo + slide_hi) / 2;
    if ((int)grid.size() < 2) return centre;

    int    best_pp   = centre;
    double best_cost = cong_cost_segment(seg, layer_id, eff_width, centre,
                                         slide_lo, slide_hi);
    int    best_dist = 0;

    for (int b = 0; b + 1 < (int)grid.size(); ++b) {
        int win_lo = std::max(grid[b],     slide_lo);
        int win_hi = std::min(grid[b + 1], slide_hi);
        if (win_hi - win_lo < eff_width) continue;   // band can't host the bus
        int pp = (win_lo + win_hi) / 2;              // centre of the usable window
        double cost = cong_cost_segment(seg, layer_id, eff_width, pp,
                                        slide_lo, slide_hi);
        int dist = std::abs(pp - centre);
        if (cost < best_cost - 1e-9 ||
            (std::abs(cost - best_cost) < 1e-9 && dist < best_dist)) {
            best_cost = cost;
            best_pp   = pp;
            best_dist = dist;
        }
    }
    return best_pp;
}

// Span-mismatch cost: kSpan(layer) * excess outside [span_min, span_max].
double CongestionPlanner::span_cost_for(double seg_span, int layer_id) const {
    const Layer* layer = layers_.get_layer(layer_id);
    if (!layer) return 0.0;
    double k      = (layer->kspan_override >= 0.0) ? layer->kspan_override : kSpan_;
    double excess = std::max({0.0,
                              (double)layer->span_min - seg_span,
                              seg_span - (double)layer->span_max});
    return k * excess;
}

// ---------------------------------------------------------------------------
// Per-bundle candidate scoring
// ---------------------------------------------------------------------------

// Score every candidate topology of one bundle against the CURRENT cut state
// and return the cheapest admissible one for the given mode.  Pure scoring:
// the cut state is restored before returning; the caller commits the winner
// with commit_plan().
CongestionPlanner::PlanResult CongestionPlanner::plan_bundle(
        const BundleWrapper& bw, PlanMode mode,
        std::set<std::pair<int,int>>* contended) {
    PlanResult res;
    if (bw.candidates.empty()) return res;

    const bool enforce_window   = (mode != PlanMode::BEST_EFFORT);
    const bool enforce_overflow = (mode == PlanMode::STRICT);
    constexpr double kOvEps = 1e-6;   // float noise only — any real overflow is hard

    // Bit count for the honest per-layer width model (eff_bus_width);
    // 0 (hand-built wrappers without nets) falls back to width x dilution.
    const int nbits = (int)bw.original_bundle.get_net_names().size();

    auto h_layers = layers_.get_layer_ids_by_dir(LayerDir::HORIZONTAL);
    auto v_layers = layers_.get_layer_ids_by_dir(LayerDir::VERTICAL);
    if (h_layers.empty()) h_layers.push_back(4);
    if (v_layers.empty()) v_layers.push_back(5);
    // Reversed copies: highest layer ID first so ties break toward higher metal.
    auto h_layers_rev = h_layers; std::reverse(h_layers_rev.begin(), h_layers_rev.end());
    auto v_layers_rev = v_layers; std::reverse(v_layers_rev.begin(), v_layers_rev.end());

    res.best_topo     = bw.topology_pinned ? bw.selected_topology_index : 0;
    double best_score = std::numeric_limits<double>::max();

    // Snapshot cut state so each topology candidate is scored from the same base.
    auto cuts_snapshot = cuts_;

    int ci_lo = bw.topology_pinned ? bw.selected_topology_index     : 0;
    int ci_hi = bw.topology_pinned ? bw.selected_topology_index + 1 : (int)bw.candidates.size();

    for (int ci = ci_lo; ci < ci_hi; ++ci) {
        const Topology& topo = bw.candidates[ci];

        // Greedy per-segment layer assignment within this topology.
        // Each segment independently gets the layer that minimises its
        // marginal overflow + affinity cost.  We apply each choice to the
        // running cut state so within-topology interactions are captured
        // (same-bundle segments rarely share a cut+band, but this is exact
        // for multicast trees whose H-spine and V-stubs can share bands).
        std::vector<int> seg_layers;
        std::vector<int> seg_perp;   // perp-centre overrides for band lookup
        double topo_overflow = 0.0;
        double topo_score    = 0.0;
        bool   topo_infeasible = false;

        // Build ConnTopology for this candidate to obtain authoritative
        // perp_lo/perp_hi ranges (including spines/trunks via Pass 2).
        // The interval centre is also used as the perp-band lookup key so
        // that stubs whose nominal x/y lands on a Hanan grid boundary are
        // credited to the correct cell (the one NUTS's interval places them in)
        // rather than the adjacent cell chosen by find_band's half-open rule.
        ConnTopology ct;
        ct.build(topo, floorplan_);
        const auto& conn_segs = ct.segs();
        constexpr int kSentinel = INT_MAX / 2;

        for (int si = 0; si < (int)topo.segments.size(); ++si) {
            const Segment& seg = topo.segments[si];
            bool  is_h         = (seg.start.y == seg.end.y);
            const auto& layers_rev = is_h ? h_layers_rev : v_layers_rev;
            double seg_span = is_h
                ? (double)std::abs(seg.end.x - seg.start.x)
                : (double)std::abs(seg.end.y - seg.start.y);

            // Derive the perpendicular-band lookup window from the ConnTopology
            // slide range.  The segment can slide anywhere within it, so the
            // congestion charge goes to the cheapest band that can host the bus
            // (best_band_perp) rather than a point estimate at the centre —
            // which can land in an arbitrarily narrow band the bus would never
            // use.  Sentinel (unbounded) sides are clamped to the grid extent.
            int slide_lo = INT_MIN, slide_hi = INT_MIN;
            if (si < (int)conn_segs.size()) {
                const ConnSeg& cs = conn_segs[si];
                const auto& pgrid = is_h ? y_grid_ : x_grid_;
                if (!pgrid.empty()) {
                    slide_lo = std::max(cs.perp_lo, pgrid.front());
                    slide_hi = std::min(cs.perp_hi, pgrid.back());
                    if (slide_lo > slide_hi) { slide_lo = INT_MIN; slide_hi = INT_MIN; }
                }
            }
            auto band_perp = [&](int lid, double eff) {
                if (slide_lo == INT_MIN) return INT_MIN;   // no window: nominal lookup
                return best_band_perp(seg, lid, eff, slide_lo, slide_hi);
            };

            int    best_lid = layers_rev[0];
            double best_s   = std::numeric_limits<double>::max();
            double best_ov  = 0.0;
            int    best_pp  = INT_MIN;

            // Respect manual layer overrides if present for this segment.
            if (si < (int)bw.pinned_seg_layers.size() && bw.pinned_seg_layers[si] != -1) {
                best_lid = bw.pinned_seg_layers[si];
                best_s   = 0.0; // Pinned choice is considered "perfect" cost for planning.
                double eff = layers_.eff_bus_width(nbits, bw.width, best_lid);
                best_pp  = band_perp(best_lid, eff);
                best_ov  = score_segment(seg, best_lid, eff, best_pp, slide_lo, slide_hi);
                if (enforce_overflow && best_ov > kOvEps) {
                    topo_infeasible = true;
                    if (contended)
                        collect_overflow_bands(seg, best_lid, eff, best_pp,
                                               slide_lo, slide_hi, *contended);
                }
            } else {
                // Iterate highest-ID first so equal-cost layers prefer higher metal.
                for (int lid : layers_rev) {
                    double eff  = layers_.eff_bus_width(nbits, bw.width, lid);
                    int    pp   = band_perp(lid, eff);
                    double ov   = score_segment(seg, lid, eff, pp, slide_lo, slide_hi);
                    // STRICT: overflow is a hard constraint.  An overflowing
                    // band physically cannot host the bus — NUTS would emit a
                    // real overlap — so the layer is not a choice, however
                    // cheap its soft cost.
                    if (enforce_overflow && ov > kOvEps) {
                        if (contended)
                            collect_overflow_bands(seg, lid, eff, pp,
                                                   slide_lo, slide_hi, *contended);
                        continue;
                    }
                    double cong = cong_cost_segment(seg, lid, eff, pp, slide_lo, slide_hi);
                    double span = span_cost_for(seg_span, lid);
                    // Non-TOP penalty scaled by segment length: a short stub
                    // pays little to drop down a layer, so locals offload to
                    // lower layers instead of detouring on TOP — preserving
                    // TOP capacity for long-haul trunks (which pay in full).
                    double base = layers_.is_top(lid) ? 0.0
                                : base_cost_non_top_ *
                                  ((span_ref_eff_ > 0.0)
                                       ? std::min(1.0, seg_span / span_ref_eff_)
                                       : 1.0);
                    double s    = cong + span + base;
                    if (s < best_s) { best_s = s; best_lid = lid; best_ov = ov; best_pp = pp; }
                }
                if (best_s == std::numeric_limits<double>::max())
                    topo_infeasible = true;   // STRICT: every layer overflows
            }
            if (topo_infeasible) break;
            int perp_pos = best_pp;

            // Feasibility: the bus (eff_width in the perpendicular direction)
            // must fit within the sliding range ConnTopology computed for this
            // segment — covers busterms (Pass 1) and spines/trunks (Pass 2).
            if (enforce_window && si < (int)conn_segs.size()) {
                const ConnSeg& cs = conn_segs[si];
                if (cs.perp_lo > -kSentinel && cs.perp_hi < kSentinel) {
                    double eff = layers_.eff_bus_width(nbits, bw.width, best_lid);
                    if (static_cast<double>(cs.perp_hi - cs.perp_lo) < eff)
                        topo_infeasible = true;
                }
            }
            if (topo_infeasible) break;

            // Apply chosen layer so later segments in this topology see
            // the updated congestion state.
            double eff = layers_.eff_bus_width(nbits, bw.width, best_lid);
            apply_segment(seg, best_lid, eff, perp_pos);
            seg_layers.push_back(best_lid);
            seg_perp.push_back(perp_pos);
            topo_overflow = std::max(topo_overflow, best_ov);
            topo_score    = std::max(topo_score,    best_s);
        }

        // Wirelength term: with congestion/span/layer costs equal, shorter
        // topologies win — a detour must buy real congestion relief to be
        // worth its extra length.
        topo_score += kWL_ * topo.estimated_wirelength;

        if (topo_infeasible) {
            cuts_ = cuts_snapshot;
            continue;
        }

        bool is_better = false;
        if (topo_score < best_score - 1e-6) {
            is_better = true;
        } else if (std::abs(topo_score - best_score) < 1e-6) {
            // Tie-breaker: stable selection by index.
            if (ci < res.best_topo) is_better = true;
        }

        if (is_better) {
            best_score     = topo_score;
            res.score      = topo_score;
            res.overflow   = topo_overflow;
            res.best_topo  = ci;
            res.seg_layers = seg_layers;
            res.seg_perp   = seg_perp;
            res.found      = true;
        }

        // Roll back to snapshot before scoring the next candidate.
        cuts_ = cuts_snapshot;
    }
    return res;
}

// Commit (sign=+1) or rip up (sign=-1) a planned bundle's per-segment demand
// in the cut state.
void CongestionPlanner::commit_plan(const BundleWrapper& bw, const PlanResult& plan,
                                    double sign) {
    const int nbits = (int)bw.original_bundle.get_net_names().size();
    const Topology& t = bw.candidates[plan.best_topo];
    for (int si = 0; si < (int)t.segments.size() && si < (int)plan.seg_layers.size(); ++si) {
        int pp  = (si < (int)plan.seg_perp.size()) ? plan.seg_perp[si] : INT_MIN;
        int lid = plan.seg_layers[si];
        apply_segment(t.segments[si], lid,
                      sign * layers_.eff_bus_width(nbits, bw.width, lid), pp);
    }
}

BundleAssignment CongestionPlanner::make_assignment(const BundleWrapper& bw,
                                                    const PlanResult& plan) const {
    // Derive representative V/H layers for logging (last V/H seg wins).
    int rep_v = layers_.get_top_layer(LayerDir::VERTICAL);
    int rep_h = layers_.get_top_layer(LayerDir::HORIZONTAL);
    const Topology& winner = bw.candidates[plan.best_topo];
    for (int si = 0; si < (int)winner.segments.size() && si < (int)plan.seg_layers.size(); ++si) {
        bool is_h = (winner.segments[si].start.y == winner.segments[si].end.y);
        if (is_h) rep_h = plan.seg_layers[si];
        else      rep_v = plan.seg_layers[si];
    }
    BundleAssignment asn;
    asn.bundle_id  = bw.original_bundle.id;
    asn.topo_index = plan.best_topo;
    asn.v_layer_id = rep_v;
    asn.h_layer_id = rep_h;
    asn.seg_layers = plan.seg_layers;
    asn.seg_perp   = plan.seg_perp;
    return asn;
}

void CongestionPlanner::log_choice(const BundleWrapper& bw, const PlanResult& plan,
                                   const std::string& tag) const {
    const Topology& winner = bw.candidates[plan.best_topo];
    std::string seg_str;
    for (int si = 0; si < (int)winner.segments.size() && si < (int)plan.seg_layers.size(); ++si) {
        bool is_h = (winner.segments[si].start.y == winner.segments[si].end.y);
        if (si > 0) seg_str += ' ';
        seg_str += (is_h ? "H" : "V");
        seg_str += "→M" + std::to_string(plan.seg_layers[si]);
    }
    std::cout << "[Planner] Bundle " << bw.original_bundle.id
              << " (" << bw.width << " units wide)"
              << " -> topo " << (plan.best_topo + 1) << " of " << bw.candidates.size()
              << ": " << winner.type << tag
              << "  [" << seg_str << "]"
              << "  overflow=" << plan.overflow << "\n";
}

// ---------------------------------------------------------------------------
// Main optimiser — greedy fattest-bus-first, per-segment layer assignment
// ---------------------------------------------------------------------------

std::vector<BundleAssignment> CongestionPlanner::optimize_topologies(
        std::vector<BundleWrapper>& bundles, int /*max_iterations*/) {
    // Ensure base grid is populated from floorplan.
    if (x_grid_.empty()) build_congestion_map();

    // Extend the Hanan grid only with segment endpoint coordinates that fall
    // OUTSIDE the current grid's range.  Topology generators place in-grid
    // segments at Hanan-cell midpoints; inserting those as new grid lines would
    // split cells into tiny sub-bands with zero capacity and cause violations.
    // Out-of-range coordinates (e.g. U-shape trunks beyond the chip boundary)
    // have no covering cell at all and would receive the ±50 fallback interval.
    {
        size_t nx0 = x_grid_.size(), ny0 = y_grid_.size();
        auto extend_oob = [](std::vector<int>& grid, int val) {
            if (grid.size() < 2) return;
            if (val >= grid.front() && val <= grid.back()) return; // inside — skip
            auto it = std::lower_bound(grid.begin(), grid.end(), val);
            if (it == grid.end() || *it != val) grid.insert(it, val);
        };
        for (const auto& bw : bundles) {
            for (const auto& cand : bw.candidates) {
                for (const auto& seg : cand.segments) {
                    extend_oob(x_grid_, seg.start.x);
                    extend_oob(x_grid_, seg.end.x);
                    extend_oob(y_grid_, seg.start.y);
                    extend_oob(y_grid_, seg.end.y);
                }
            }
        }
        if (x_grid_.size() != nx0 || y_grid_.size() != ny0) {
            std::cout << "[Planner] Grid extended: "
                      << (x_grid_.size() - nx0) << " X, "
                      << (y_grid_.size() - ny0) << " Y points from topology candidates.\n";
            rebuild_cuts_();
        }
    }

    // Resolve the span reference for non-TOP penalty scaling: unset means
    // 25% of the larger Hanan grid extent — segments longer than that pay
    // the full base_cost_non_top_, shorter ones proportionally less.
    span_ref_eff_ = base_span_ref_;
    if (span_ref_eff_ <= 0.0 && x_grid_.size() >= 2 && y_grid_.size() >= 2) {
        double ext_x = (double)(x_grid_.back() - x_grid_.front());
        double ext_y = (double)(y_grid_.back() - y_grid_.front());
        span_ref_eff_ = 0.25 * std::max(ext_x, ext_y);
    }

    // Sort: higher priority first (depth-0 before depth-1, constrained first);
    // within the same priority, process widest buses first.
    std::vector<int> order(bundles.size());
    std::iota(order.begin(), order.end(), 0);
    std::sort(order.begin(), order.end(), [&](int a, int b) {
        if (bundles[a].priority != bundles[b].priority)
            return bundles[a].priority > bundles[b].priority;
        return bundles[a].width > bundles[b].width;
    });

    // Park every bundle's reserved demand as virtual usage up front, so
    // earlier-planned bundles leave room inside reserved regions (cell
    // interiors).  Each bundle's reservation is released right before its
    // own turn — from then on its demand is real, not reserved.
    for (int idx : order) apply_reservation(bundles[idx], +1.0);

    std::vector<BundleAssignment> assignments;
    assignments.reserve(bundles.size());

    // Per-level statistics for the planning summary (printed when the set
    // spans hierarchy levels).  Stage: 0=STRICT 1=rip-up 2=soft 3=best-effort.
    struct LevelStats {
        int n = 0;
        int by_stage[4] = {0, 0, 0, 0};
        double max_overflow = 0.0;
        std::map<int,int> layer_hist;   // layer id → segment count
    };
    std::map<int, LevelStats> level_stats;

    // Bundles committed so far, in commit order, with the exact plan applied
    // to the cut state (so it can be ripped up) and where its assignment
    // lives (so a replan can overwrite it).
    struct Committed { int bundle_idx; int asn_idx; PlanResult plan; };
    std::vector<Committed> committed;

    for (int idx : order) {
        auto& bw = bundles[idx];
        // Release this bundle's own reservation: its demand is now planned
        // for real (or dropped, if it has no candidates).
        apply_reservation(bw, -1.0);
        if (bw.candidates.empty()) continue;

        // (1) Overflow is a hard constraint: first look for a candidate that
        //     is both slide-feasible and overflow-free.  A detour only loses
        //     to soft costs (wirelength/span) against other overflow-free
        //     candidates — never against one that NUTS cannot place.
        //     On failure, `contended` holds the (cut,band) pairs whose
        //     overflow disqualified candidates — the bands rip-up must relieve.
        std::set<std::pair<int,int>> contended;
        PlanResult plan = plan_bundle(bw, PlanMode::STRICT, &contended);
        bool already_committed = false;
        int  stage = 0;   // STRICT

        // (2) Rip-up & replan: no candidate is overflow-free against the
        //     current usage.  Try freeing capacity by replanning one earlier
        //     bundle.  Victims are ranked by the demand they hold on the
        //     contended bands (most relief first; e.g. the one global trunk
        //     crossing a cell whose local bundle just failed); zero-overlap
        //     victims cannot help and are skipped.  Ties break toward the
        //     most recently committed (lowest priority / narrowest).
        //     Accept only if BOTH bundles end up overflow-free; otherwise
        //     restore the victim exactly and try the next one.
        if (!plan.found) {
            std::vector<std::pair<double,int>> ranked;   // (overlap, committed idx)
            for (int k = 0; k < (int)committed.size(); ++k) {
                double ovl = plan_band_overlap(bundles[committed[k].bundle_idx],
                                               committed[k].plan, contended);
                if (ovl > 0.0) ranked.push_back({ovl, k});
            }
            std::sort(ranked.begin(), ranked.end(),
                      [](const std::pair<double,int>& a, const std::pair<double,int>& b) {
                          if (a.first != b.first) return a.first > b.first;
                          return a.second > b.second;
                      });
            for (const auto& [ovl, k] : ranked) {
                auto& cp = committed[k];
                auto& pw = bundles[cp.bundle_idx];
                commit_plan(pw, cp.plan, -1.0);             // rip up victim
                PlanResult mine = plan_bundle(bw, PlanMode::STRICT);
                if (mine.found) {
                    commit_plan(bw, mine);
                    PlanResult theirs = plan_bundle(pw, PlanMode::STRICT);
                    if (theirs.found) {
                        commit_plan(pw, theirs);
                        cp.plan = theirs;
                        assignments[cp.asn_idx] = make_assignment(pw, theirs);
                        std::cout << "[Planner] Rip-up: replanned bundle "
                                  << pw.original_bundle.id
                                  << " to free capacity for bundle "
                                  << bw.original_bundle.id << ":\n";
                        log_choice(pw, theirs,
                                   std::string(pw.topology_pinned ? " [pinned]" : "")
                                   + " [replanned]");
                        plan = mine;
                        already_committed = true;
                        stage = 1;   // rip-up
                        break;
                    }
                    commit_plan(bw, mine, -1.0);            // victim can't recover: undo us
                }
                commit_plan(pw, cp.plan);                   // restore victim
            }
        }

        // (3) Overflow is unavoidable even after rip-up: fall back to soft
        //     pricing so the least-cost overflowing candidate is committed.
        if (!plan.found) {
            plan = plan_bundle(bw, PlanMode::ALLOW_OVERFLOW);
            if (plan.found) {
                stage = 2;   // soft overflow
                std::cout << "[Planner] WARNING: Bundle " << bw.original_bundle.id
                          << ": no overflow-free candidate (even after rip-up); "
                          << "committing least-cost with overflow="
                          << plan.overflow << ".\n";
            }
        }

        // (4) Every candidate violates its slide windows (bus wider than the
        //     windows; e.g. sidecar pins saved under the old width model) —
        //     commit best-effort so the bundle still gets a layer assignment
        //     instead of an EMPTY seg_layers, which indexed out of bounds and
        //     crashed (flow/channel_stress.buda).
        if (!plan.found) {
            plan = plan_bundle(bw, PlanMode::BEST_EFFORT);
            if (plan.found) {
                stage = 3;   // best-effort
                std::cout << "[Planner] WARNING: Bundle " << bw.original_bundle.id
                          << ": no candidate fits its slide windows (bus width "
                          << "exceeds them); committing best-effort "
                          << bw.candidates[plan.best_topo].type
                          << (bw.topology_pinned ? " [pinned]" : "") << ".\n";
            }
        }

        if (!plan.found) continue;   // no candidates scored (empty range)

        // Commit the winning topology's per-segment choices to the cut state
        // (the rip-up path already did).
        if (!already_committed) commit_plan(bw, plan);

        committed.push_back({idx, (int)assignments.size(), plan});
        assignments.push_back(make_assignment(bw, plan));
        log_choice(bw, plan, bw.topology_pinned ? " [pinned]" : "");

        LevelStats& ls = level_stats[bw.level];
        ls.n += 1;
        ls.by_stage[stage] += 1;
        ls.max_overflow = std::max(ls.max_overflow, plan.overflow);
        for (int lid : plan.seg_layers) ls.layer_hist[lid] += 1;
    }

    // Per-level planning summary — printed when the set spans hierarchy
    // levels (run_planner hier), where local/global competition lives.
    if (level_stats.size() > 1 || (level_stats.size() == 1 && level_stats.begin()->first > 0)) {
        static const char* stage_names[4] = {"strict", "ripup", "overflow", "best_effort"};
        std::cout << "[Planner] Level summary:\n";
        for (const auto& [lvl, ls] : level_stats) {
            std::cout << "  D" << lvl << ": " << ls.n << " bundles ";
            for (int s = 0; s < 4; ++s)
                if (ls.by_stage[s] > 0)
                    std::cout << " " << stage_names[s] << ":" << ls.by_stage[s];
            std::cout << "  layers{";
            bool first = true;
            for (const auto& [lid, n] : ls.layer_hist) {
                if (!first) std::cout << ' ';
                std::cout << "M" << lid << ":" << n;
                first = false;
            }
            std::cout << "}";
            if (ls.max_overflow > 0.0)
                std::cout << "  max_overflow=" << ls.max_overflow;
            std::cout << "\n";
        }
    }

    return assignments;
}

} // namespace buda
