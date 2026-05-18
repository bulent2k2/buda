#include "detailed_nuts.h"
#include <cmath>
#include <map>
#include <stdexcept>
#include <tuple>

namespace interconnect {

DetailedNUTSEngine::DetailedNUTSEngine(const RoutingGridStack& stack,
                                       const LayerStack& layers)
    : stack_(stack) {
    for (int id : layers.get_layer_ids_by_dir(LayerDir::HORIZONTAL))
        layer_is_h_[id] = true;
    for (int id : layers.get_layer_ids_by_dir(LayerDir::VERTICAL))
        layer_is_h_[id] = false;
}

bool DetailedNUTSEngine::signals_contiguous(
        double pos_a, double pos_b,
        const std::vector<std::pair<double, TrackSlot>>& all_tracks) {
    for (const auto& t : all_tracks)
        if (t.second.type != "SIGNAL" && t.first > pos_a && t.first < pos_b)
            return false;
    return true;
}

int DetailedNUTSEngine::find_contiguous_window_lo(
        const std::vector<std::pair<double, TrackSlot>>& signal_tracks,
        const std::vector<std::pair<double, TrackSlot>>& all_tracks,
        int bit_width) {
    int n = static_cast<int>(signal_tracks.size());
    for (int i = 0; i <= n - bit_width; ++i) {
        bool ok = true;
        for (int j = i; j < i + bit_width - 1; ++j) {
            if (!signals_contiguous(signal_tracks[j].first,
                                    signal_tracks[j + 1].first, all_tracks)) {
                ok = false;
                break;
            }
        }
        if (ok) return i;
    }
    return -1;
}

int DetailedNUTSEngine::find_contiguous_window_hi(
        const std::vector<std::pair<double, TrackSlot>>& signal_tracks,
        const std::vector<std::pair<double, TrackSlot>>& all_tracks,
        int bit_width) {
    int n = static_cast<int>(signal_tracks.size());
    for (int i = n - bit_width; i >= 0; --i) {
        bool ok = true;
        for (int j = i; j < i + bit_width - 1; ++j) {
            if (!signals_contiguous(signal_tracks[j].first,
                                    signal_tracks[j + 1].first, all_tracks)) {
                ok = false;
                break;
            }
        }
        if (ok) return i;
    }
    return -1;
}

DetailedNUTSResult DetailedNUTSEngine::run(
        const std::vector<BusSegment>& bus_segs) const {
    DetailedNUTSResult result;

    for (const auto& bs : bus_segs) {
        if (!stack_.has_layer(bs.layer)) {
            result.num_unplaced += bs.bit_width;
            continue;
        }

        const RoutingGrid& grid = stack_.get_layer_grid(bs.layer);
        double x = (bs.span_lo + bs.span_hi) / 2.0;

        auto signal_tracks = grid.signal_tracks_in(x, bs.interval_lo, bs.interval_hi);
        int n_sig = static_cast<int>(signal_tracks.size());

        if (n_sig < bs.bit_width) {
            result.num_unplaced += bs.bit_width;
            continue;
        }

        int start_idx  = 0;
        int direction  = +1;   // +1 → ascending index (LO_HI), -1 → descending (HI_LO)

        if (bs.timing_critical) {
            const TrackPattern& pat = grid.effective_pattern_at(x, bs.interval_lo);
            auto all_tracks = pat.tracks_in_range(bs.interval_lo, bs.interval_hi);

            if (bs.bit_order == "LO_HI") {
                int w = find_contiguous_window_lo(signal_tracks, all_tracks, bs.bit_width);
                if (w < 0) { result.num_unplaced += bs.bit_width; continue; }
                start_idx = w;
                direction = +1;
            } else {
                int w = find_contiguous_window_hi(signal_tracks, all_tracks, bs.bit_width);
                if (w < 0) { result.num_unplaced += bs.bit_width; continue; }
                // bit_index=0 → highest track in window
                start_idx = w + bs.bit_width - 1;
                direction = -1;
            }
        } else {
            if (bs.bit_order == "LO_HI") {
                start_idx = 0;
                direction = +1;
            } else {
                start_idx = n_sig - 1;
                direction = -1;
            }
        }

        for (int bit = 0; bit < bs.bit_width; ++bit) {
            int idx = start_idx + direction * bit;
            NetSegment ns;
            ns.bundle_id      = bs.bundle_id;
            ns.seg_idx        = bs.seg_idx;
            ns.bit_index      = bit;
            ns.track_position = signal_tracks[idx].first;
            ns.width          = signal_tracks[idx].second.width;
            ns.layer          = bs.layer;
            ns.span_lo        = bs.span_lo;
            ns.span_hi        = bs.span_hi;
            result.net_segments.push_back(ns);
        }
    }

    // Span-adjustment post-pass: extend each bit-wire's nearer endpoint to the
    // track_position of the same-bit wire on the adjacent perpendicular segment.
    // This is the per-bit equivalent of NUTS do_span_adjustments — without it,
    // H and V legs of the same bit don't visually meet at the bend point.
    {
        using Key = std::tuple<int,int,int>; // bundle_id, seg_idx, bit_index
        std::map<Key, int> idx_map;
        for (int i = 0; i < (int)result.net_segments.size(); ++i) {
            const auto& ns = result.net_segments[i];
            idx_map[{ns.bundle_id, ns.seg_idx, ns.bit_index}] = i;
        }

        for (auto& ns : result.net_segments) {
            auto h_it = layer_is_h_.find(ns.layer);
            bool is_h = (h_it != layer_is_h_.end()) ? h_it->second : true;
            for (int d : {-1, +1}) {
                auto it = idx_map.find({ns.bundle_id, ns.seg_idx + d, ns.bit_index});
                if (it == idx_map.end()) continue;
                const NetSegment& nb = result.net_segments[it->second];
                auto nb_h_it = layer_is_h_.find(nb.layer);
                bool nb_is_h = (nb_h_it != layer_is_h_.end()) ? nb_h_it->second : true;
                if (nb_is_h == is_h)
                    continue; // same direction — no perpendicular junction
                double tp = nb.track_position;
                if (std::abs(tp - ns.span_hi) <= std::abs(tp - ns.span_lo))
                    ns.span_hi = tp;
                else
                    ns.span_lo = tp;
            }
        }
    }

    return result;
}

} // namespace interconnect
