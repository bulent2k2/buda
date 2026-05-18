#include "detailed_nuts.h"
#include <map>
#include <stdexcept>
#include <tuple>

namespace interconnect {

DetailedNUTSEngine::DetailedNUTSEngine(const RoutingGridStack& stack)
    : stack_(stack) {}

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

    // Span-adjustment post-pass: extend each bit-wire endpoint that connects to
    // a perpendicular segment (lo_adj_seg_idx / hi_adj_seg_idx) to the exact
    // track_position of the same bit on that adjacent segment.
    // Endpoints that terminate at a block face (adj = -1) are left unchanged.
    {
        using Key = std::tuple<int,int,int>; // bundle_id, seg_idx, bit_index
        std::map<Key, int>              idx_map;
        std::map<std::pair<int,int>, const BusSegment*> bs_map;

        for (int i = 0; i < (int)result.net_segments.size(); ++i) {
            const auto& ns = result.net_segments[i];
            idx_map[{ns.bundle_id, ns.seg_idx, ns.bit_index}] = i;
        }
        for (const auto& bs : bus_segs)
            bs_map[{bs.bundle_id, bs.seg_idx}] = &bs;

        for (auto& ns : result.net_segments) {
            auto bit = ns.bundle_id ? ns.bundle_id : 0; (void)bit; // suppress unused
            const auto* bs_ptr = bs_map[{ns.bundle_id, ns.seg_idx}];
            if (!bs_ptr) continue;

            if (bs_ptr->lo_adj_seg_idx >= 0) {
                auto it = idx_map.find({ns.bundle_id, bs_ptr->lo_adj_seg_idx, ns.bit_index});
                if (it != idx_map.end())
                    ns.span_lo = result.net_segments[it->second].track_position;
            }
            if (bs_ptr->hi_adj_seg_idx >= 0) {
                auto it = idx_map.find({ns.bundle_id, bs_ptr->hi_adj_seg_idx, ns.bit_index});
                if (it != idx_map.end())
                    ns.span_hi = result.net_segments[it->second].track_position;
            }
        }
    }

    return result;
}

} // namespace interconnect
