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

#include "busterm.h"
#include <cstdio>
#include <unordered_map>

namespace buda {

// ── JSON helpers (minimal: no external deps) ──────────────────────────────

// Encode a vector of (x1,y1,x2,y2) tuples as [[x1,y1,x2,y2],...].
std::string encode_rects_json(
    const std::vector<std::tuple<double,double,double,double>>& rects) {
    if (rects.empty()) return "";
    std::string out = "[";
    for (const auto& [x1,y1,x2,y2] : rects) {
        char buf[128];
        std::snprintf(buf, sizeof(buf), "[%g,%g,%g,%g],", x1, y1, x2, y2);
        out += buf;
    }
    out.back() = ']';  // replace trailing comma
    return out;
}

// Decode "[[x1,y1,x2,y2],...]" into a vector.  Returns empty on parse failure.
std::vector<std::tuple<double,double,double,double>>
decode_rects_json(const std::string& s) {
    std::vector<std::tuple<double,double,double,double>> result;
    if (s.empty() || s[0] != '[') return result;
    const char* p = s.c_str() + 1;
    while (*p == '[') {
        double x1, y1, x2, y2;
        int consumed = 0;
        if (std::sscanf(p, "[%lf,%lf,%lf,%lf]%n", &x1, &y1, &x2, &y2, &consumed) == 4) {
            result.emplace_back(x1, y1, x2, y2);
            p += consumed;
            if (*p == ',') ++p;
        } else {
            break;
        }
    }
    return result;
}

// ── BustermGen ─────────────────────────────────────────────────────────────

BustermGen::BustermGen(BDB& db) : _db(db) {}

HierBusterm BustermGen::_from_component(const ComponentRow& comp,
    const std::unordered_set<std::string>& cells_with_pins) const {
    HierBusterm bt;
    bt.id         = "bt:" + comp.name;
    bt.hier_path  = comp.name;
    bt.depth      = comp.depth;
    bt.x1 = comp.x1; bt.y1 = comp.y1;
    bt.x2 = comp.x2; bt.y2 = comp.y2;
    if (comp.is_leaf && cells_with_pins.count(comp.cell))
        bt.resolution = BustermResolution::PORT;
    else if (comp.is_leaf)
        bt.resolution = BustermResolution::SPATIAL_CLUSTER;
    else
        bt.resolution = BustermResolution::BLOCK;
    return bt;
}

void BustermGen::derive(int max_depth) {
    _max_depth = max_depth;
    _db.clear_busterms();

    // Build set of cell names that have declared cell_pins (PORT resolution).
    std::unordered_set<std::string> cells_with_pins;
    for (const auto& cp : _db.all_cell_pins())
        cells_with_pins.insert(cp.cell);

    // Build comp_id → ComponentRow map for parent lookup.
    auto all_comps = _db.all_components();
    std::unordered_map<int, ComponentRow> by_id;
    by_id.reserve(all_comps.size());
    for (const auto& c : all_comps)
        by_id[c.id] = c;

    for (const auto& comp : all_comps) {
        if (comp.depth > max_depth) continue;
        if (comp.x1 < 0) continue;  // unplaced; skip

        HierBusterm hbt = _from_component(comp, cells_with_pins);

        // Resolve parent busterm id via parent component.
        if (comp.parent_id >= 0) {
            auto it = by_id.find(comp.parent_id);
            if (it != by_id.end())
                hbt.parent_id = "bt:" + it->second.name;
        }

        BustermRow row;
        row.id         = hbt.id;
        row.comp_id    = comp.id;
        row.hier_path  = hbt.hier_path;
        row.depth      = hbt.depth;
        row.x1         = hbt.x1;
        row.y1         = hbt.y1;
        row.x2         = hbt.x2;
        row.y2         = hbt.y2;
        row.resolution = (hbt.resolution == BustermResolution::PORT)
                         ? "PORT"
                         : (hbt.resolution == BustermResolution::SPATIAL_CLUSTER)
                           ? "SPATIAL_CLUSTER"
                           : "BLOCK";
        row.parent_id  = hbt.parent_id;
        row.rects      = encode_rects_json(hbt.rects);  // empty unless caller populated rects
        _db.add_busterm(row);
    }
}

void BustermGen::refine() {
    derive(_max_depth);
}

std::vector<HierBusterm> BustermGen::all() const {
    std::vector<HierBusterm> result;
    for (const auto& row : _db.all_busterms()) {
        HierBusterm hbt;
        hbt.id        = row.id;
        hbt.hier_path = row.hier_path;
        hbt.depth     = row.depth;
        hbt.x1 = row.x1; hbt.y1 = row.y1;
        hbt.x2 = row.x2; hbt.y2 = row.y2;
        if (row.resolution == "SPATIAL_CLUSTER")
            hbt.resolution = BustermResolution::SPATIAL_CLUSTER;
        else if (row.resolution == "PORT")
            hbt.resolution = BustermResolution::PORT;
        else
            hbt.resolution = BustermResolution::BLOCK;
        hbt.parent_id = row.parent_id;
        hbt.rects     = decode_rects_json(row.rects);
        result.push_back(hbt);
    }
    return result;
}

}  // namespace buda
