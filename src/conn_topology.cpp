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

#include "conn_topology.h"
#include "topology_analysis.h"
#include <algorithm>
#include <cassert>
#include <functional>
#include <numeric>
#include <set>

namespace buda {

// ── ConnTopology::build ───────────────────────────────────────────────────────
//
// The facade over the six analysis passes (topology_analysis.h) — the frozen
// consumer API.  Pass ORDER is part of the byte-identity contract; see
// docs/internal/topo_conn_unification.md.

void ConnTopology::build(const Topology& topo, const Floorplan& fp) {
    derive_conn_segs   (topo, fp, segs_);
    derive_slide_ranges(topo, fp, segs_);
    tighten_passthrough(topo, fp, segs_);
    pin_relay_taps     (topo, fp, segs_);
    derive_net_pull    (topo, fp, segs_);
    derive_along_flex  (topo, fp, segs_);

    for (const auto& cs : segs_) {
        assert(cs.along_lo <= cs.along_hi);
        assert(cs.perp_lo  <= cs.perp_hi);
        (void)cs;
    }
}

int manhattan_nearest(const Rect& a, const Rect& b) {
    int dx = std::max(0, std::max(a.x1 - b.x2, b.x1 - a.x2));
    int dy = std::max(0, std::max(a.y1 - b.y2, b.y1 - a.y2));
    return dx + dy;
}

Rect seg_bbox(const ConnSeg& cs) {
    if (cs.horiz)
        return Rect{ cs.along_lo, cs.perp_pos, cs.along_hi, cs.perp_pos };
    else
        return Rect{ cs.perp_pos, cs.along_lo, cs.perp_pos, cs.along_hi };
}

std::vector<MSTEdge> compute_mst(
    const std::vector<std::pair<std::string, Rect>>& nodes)
{
    int n = (int)nodes.size();
    if (n <= 1) return {};
    struct RawEdge { int u, v, dist; };
    std::vector<RawEdge> edges;
    edges.reserve(n * (n - 1) / 2);
    for (int i = 0; i < n; i++)
        for (int j = i + 1; j < n; j++)
            edges.push_back({i, j, manhattan_nearest(nodes[i].second,
                                                     nodes[j].second)});

    std::sort(edges.begin(), edges.end(),
              [](const RawEdge& a, const RawEdge& b){ return a.dist < b.dist; });

    std::vector<int> parent(n);
    std::iota(parent.begin(), parent.end(), 0);
    std::function<int(int)> find = [&](int x) {
        return parent[x] == x ? x : parent[x] = find(parent[x]);
    };

    std::vector<MSTEdge> mst;
    mst.reserve(n - 1);
    for (const auto& e : edges) {
        int pu = find(e.u), pv = find(e.v);
        if (pu == pv) continue;
        parent[pu] = pv;
        mst.push_back({e.u, e.v, e.dist,
                       nodes[e.u].first, nodes[e.v].first});
        if ((int)mst.size() == n - 1) break;
    }
    return mst;
}

std::vector<MSTEdge> ConnTopology::trunk_mst(int trunk_idx,
                                              const Floorplan& fp) const
{
    const ConnSeg& trunk = segs_[trunk_idx];
    std::set<std::string> connected;
    for (const auto& c : trunk.conns)
        if (c.kind == SegConn::BUSTERM) connected.insert(c.block_name);

    std::vector<std::pair<std::string, Rect>> nodes;
    nodes.emplace_back("trunk", seg_bbox(trunk));
    for (const auto& [name, rect] : fp.get_all_blocks())
        if (!connected.count(name))
            nodes.emplace_back(name, rect);

    return compute_mst(nodes);
}

} // namespace buda
