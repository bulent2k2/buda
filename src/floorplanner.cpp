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

#include "floorplanner.h"

#include <algorithm>
#include <cmath>
#include <set>
#include <stdexcept>

namespace buda {

void FloorplannerEngine::set_die(double w, double h) {
    if (w <= 0.0 || h <= 0.0) {
        _errors.push_back({
            "ERROR", "", "",
            "die size must be positive"
        });
        return;
    }
    _die_w = w;
    _die_h = h;
}

void FloorplannerEngine::set_grid(double grid) {
    if (grid <= 0.0) {
        _errors.push_back({
            "ERROR", "", "",
            "placement grid must be positive"
        });
        return;
    }
    _grid = grid;
}

void FloorplannerEngine::add_block(const std::string& name,
                                   double x1, double y1,
                                   double x2, double y2) {
    if (name.empty())
        throw std::runtime_error("add_block: empty name");
    if (x2 <= x1 || y2 <= y1)
        throw std::runtime_error("add_block: invalid bbox for " + name);
    Block b;
    b.name = name;
    b.x1 = x1; b.y1 = y1; b.x2 = x2; b.y2 = y2;
    _blocks[name] = b;
}

void FloorplannerEngine::add_child_block(const std::string& name,
                                         double local_x, double local_y,
                                         double w, double h) {
    if (w <= 0.0 || h <= 0.0)
        throw std::runtime_error("add_child_block: invalid size for " + name);
    std::string parent = _parent_path(name);
    if (parent.empty())
        throw std::runtime_error("add_child_block: child path has no parent: " + name);
    const Block& p = _block_or_throw(parent);

    Block b;
    b.name = name;
    b.local_x = local_x;
    b.local_y = local_y;
    b.has_local = true;
    b.x1 = p.x1 + local_x;
    b.y1 = p.y1 + local_y;
    b.x2 = b.x1 + w;
    b.y2 = b.y1 + h;
    _blocks[name] = b;
}

void FloorplannerEngine::move_block_raw(const std::string& name, double x, double y) {
    Block& b = _block_or_throw(name);
    double w = b.x2 - b.x1;
    double h = b.y2 - b.y1;
    double sx = _snap(x);
    double sy = _snap(y);
    double dx = sx - b.x1;
    double dy = sy - b.y1;
    b.x1 = sx; b.y1 = sy; b.x2 = sx + w; b.y2 = sy + h;
    if (b.has_local) {
        std::string parent = _parent_path(name);
        if (!parent.empty()) {
            const Block& p = _block_or_throw(parent);
            b.local_x = b.x1 - p.x1;
            b.local_y = b.y1 - p.y1;
        }
    }
    // Carry the whole sub-hierarchy: child blocks ("name/...") follow the parent.
    if (dx != 0.0 || dy != 0.0)
        _translate_descendants(name, dx, dy);
}

void FloorplannerEngine::rotate_block(const std::string& name, bool cw) {
    const Block& root = _block_or_throw(name);
    const double px = root.x1, py = root.y1;   // pivot = lower-left corner

    // The block plus every descendant ("name/...") rotate rigidly about the pivot.
    std::vector<std::string> subtree{name};
    const std::string prefix = name + "/";
    for (const auto& [n, _b] : _blocks)
        if (n.size() > prefix.size() && n.compare(0, prefix.size(), prefix) == 0)
            subtree.push_back(n);

    // Rotate each bbox's corners about the pivot; a 90° turn maps an axis-aligned
    // rect to an axis-aligned rect (w/h swapped).  y-up: CW (dx,dy)→(dy,-dx),
    // CCW (dx,dy)→(-dy,dx).
    for (const auto& n : subtree) {
        Block& b = _block_or_throw(n);
        double ax1 = b.x1 - px, ay1 = b.y1 - py;
        double ax2 = b.x2 - px, ay2 = b.y2 - py;
        double nx1, ny1, nx2, ny2;
        if (cw) {
            nx1 = px + ay1; nx2 = px + ay2;
            ny1 = py - ax2; ny2 = py - ax1;
        } else {
            nx1 = px - ay2; nx2 = px - ay1;
            ny1 = py + ax1; ny2 = py + ax2;
        }
        b.x1 = _snap(nx1); b.y1 = _snap(ny1);
        b.x2 = _snap(nx2); b.y2 = _snap(ny2);
        // Independent corner snapping can collapse a sub-grid extent; keep the
        // bbox non-degenerate (matches resize_block_raw's guard).
        if (b.x2 <= b.x1) b.x2 = b.x1 + _grid;
        if (b.y2 <= b.y1) b.y2 = b.y1 + _grid;
    }
    // Recompute local offsets from the (now-rotated) immediate parents.
    for (const auto& n : subtree) {
        Block& b = _block_or_throw(n);
        if (b.has_local) {
            std::string parent = _parent_path(n);
            if (!parent.empty()) {
                const Block& p = _block_or_throw(parent);
                b.local_x = b.x1 - p.x1;
                b.local_y = b.y1 - p.y1;
            }
        }
    }
}

void FloorplannerEngine::_translate_descendants(const std::string& name,
                                                double dx, double dy) {
    const std::string prefix = name + "/";
    for (auto& [n, b] : _blocks) {
        if (n.size() > prefix.size() && n.compare(0, prefix.size(), prefix) == 0) {
            b.x1 += dx; b.y1 += dy; b.x2 += dx; b.y2 += dy;
            // local_x/local_y are relative to the immediate parent, which shifts
            // by the same (dx, dy), so they remain correct — no recompute needed.
        }
    }
}

void FloorplannerEngine::resize_block_raw(const std::string& name,
                                          double x1, double y1,
                                          double x2, double y2) {
    Block& b = _block_or_throw(name);
    double sx1 = _snap(x1), sy1 = _snap(y1);
    double sx2 = _snap(x2), sy2 = _snap(y2);
    if (sx2 <= sx1) sx2 = sx1 + _grid;
    if (sy2 <= sy1) sy2 = sy1 + _grid;
    b.x1 = sx1; b.y1 = sy1; b.x2 = sx2; b.y2 = sy2;
    if (b.has_local) {
        std::string parent = _parent_path(name);
        if (!parent.empty()) {
            const Block& p = _block_or_throw(parent);
            b.local_x = b.x1 - p.x1;
            b.local_y = b.y1 - p.y1;
        }
    }
}

void FloorplannerEngine::move_child_local(const std::string& name,
                                          double local_x, double local_y) {
    Block& b = _block_or_throw(name);
    std::string parent = _parent_path(name);
    if (parent.empty())
        throw std::runtime_error("move_child_local: path has no parent: " + name);
    const Block& p = _block_or_throw(parent);
    double w = b.x2 - b.x1;
    double h = b.y2 - b.y1;
    b.local_x = local_x;
    b.local_y = local_y;
    b.has_local = true;
    b.x1 = p.x1 + local_x;
    b.y1 = p.y1 + local_y;
    b.x2 = b.x1 + w;
    b.y2 = b.y1 + h;
}

void FloorplannerEngine::align_bottom(const std::vector<std::string>& names) {
    if (names.empty()) return;
    double edge = _block_or_throw(names.front()).y1;
    for (const auto& n : names)
        edge = std::min(edge, _block_or_throw(n).y1);
    for (const auto& n : names) {
        Block& b = _block_or_throw(n);
        double h = b.y2 - b.y1;
        double dy = edge - b.y1;
        b.y1 = edge; b.y2 = edge + h;
        if (b.has_local) {
            auto p = _parent_path(n);
            if (!p.empty()) b.local_y = b.y1 - _block_or_throw(p).y1;
        }
        if (dy != 0.0) _translate_descendants(n, 0.0, dy);
    }
}

void FloorplannerEngine::align_top(const std::vector<std::string>& names) {
    if (names.empty()) return;
    double edge = _block_or_throw(names.front()).y2;
    for (const auto& n : names)
        edge = std::max(edge, _block_or_throw(n).y2);
    for (const auto& n : names) {
        Block& b = _block_or_throw(n);
        double h = b.y2 - b.y1;
        double dy = (edge - h) - b.y1;
        b.y2 = edge; b.y1 = edge - h;
        if (b.has_local) {
            auto p = _parent_path(n);
            if (!p.empty()) b.local_y = b.y1 - _block_or_throw(p).y1;
        }
        if (dy != 0.0) _translate_descendants(n, 0.0, dy);
    }
}

void FloorplannerEngine::align_left(const std::vector<std::string>& names) {
    if (names.empty()) return;
    double edge = _block_or_throw(names.front()).x1;
    for (const auto& n : names)
        edge = std::min(edge, _block_or_throw(n).x1);
    for (const auto& n : names) {
        Block& b = _block_or_throw(n);
        double w = b.x2 - b.x1;
        double dx = edge - b.x1;
        b.x1 = edge; b.x2 = edge + w;
        if (b.has_local) {
            auto p = _parent_path(n);
            if (!p.empty()) b.local_x = b.x1 - _block_or_throw(p).x1;
        }
        if (dx != 0.0) _translate_descendants(n, dx, 0.0);
    }
}

void FloorplannerEngine::align_right(const std::vector<std::string>& names) {
    if (names.empty()) return;
    double edge = _block_or_throw(names.front()).x2;
    for (const auto& n : names)
        edge = std::max(edge, _block_or_throw(n).x2);
    for (const auto& n : names) {
        Block& b = _block_or_throw(n);
        double w = b.x2 - b.x1;
        double dx = (edge - w) - b.x1;
        b.x2 = edge; b.x1 = edge - w;
        if (b.has_local) {
            auto p = _parent_path(n);
            if (!p.empty()) b.local_x = b.x1 - _block_or_throw(p).x1;
        }
        if (dx != 0.0) _translate_descendants(n, dx, 0.0);
    }
}

FloorplanBlockRow FloorplannerEngine::get_block(const std::string& name) const {
    const Block& b = _block_or_throw(name);
    return {b.name, b.x1, b.y1, b.x2, b.y2};
}

std::pair<double, double>
FloorplannerEngine::get_child_local_origin(const std::string& name) const {
    const Block& b = _block_or_throw(name);
    if (b.has_local)
        return {b.local_x, b.local_y};
    std::string parent = _parent_path(name);
    if (parent.empty())
        throw std::runtime_error("get_child_local_origin: path has no parent: " + name);
    const Block& p = _block_or_throw(parent);
    return {b.x1 - p.x1, b.y1 - p.y1};
}

std::vector<FloorplanIssue> FloorplannerEngine::validate() const {
    std::vector<FloorplanIssue> issues = _errors;

    for (const auto& [name, b] : _blocks) {
        if (_die_w > 0.0 && _die_h > 0.0 &&
            (b.x1 < 0.0 || b.y1 < 0.0 || b.x2 > _die_w || b.y2 > _die_h)) {
            issues.push_back({
                "OUTSIDE_DIE", name, "",
                name + " is outside the die"
            });
        }
    }

    for (auto it1 = _blocks.begin(); it1 != _blocks.end(); ++it1) {
        auto it2 = it1;
        ++it2;
        for (; it2 != _blocks.end(); ++it2) {
            const Block& a = it1->second;
            const Block& b = it2->second;
            // Skip ancestor/descendant pairs — child blocks are expected
            // to be contained within their parent's bounding box.
            auto is_prefix = [](const std::string& anc, const std::string& desc) {
                return desc.size() > anc.size() + 1 &&
                       desc[anc.size()] == '/' &&
                       desc.rfind(anc, 0) == 0;
            };
            if (is_prefix(a.name, b.name) || is_prefix(b.name, a.name))
                continue;
            if (a.x1 < b.x2 && a.x2 > b.x1 &&
                a.y1 < b.y2 && a.y2 > b.y1) {
                issues.push_back({
                    "OVERLAP", a.name, b.name,
                    "overlap between " + a.name + " and " + b.name
                });
            }
        }
    }

    return issues;
}

void FloorplannerEngine::write_bdb(BDB& db) const {
    if (_die_w > 0.0 && _die_h > 0.0)
        db.set_die(_die_w, _die_h);

    // Identify blocks that have at least one child (they are containers, not leaves).
    std::set<std::string> has_children;
    for (const auto& [name, _] : _blocks) {
        std::string parent = _parent_path(name);
        if (!parent.empty()) has_children.insert(parent);
    }

    std::vector<const Block*> ordered;
    ordered.reserve(_blocks.size());
    for (const auto& [_, b] : _blocks)
        ordered.push_back(&b);
    std::sort(ordered.begin(), ordered.end(),
              [](const Block* a, const Block* b) {
                  // Parents first (fewer '/'); a name tiebreak makes the order
                  // TOTAL so the ambiguous-cell disambiguation below is
                  // deterministic. Cell/comp writes are order-independent for
                  // non-ambiguous blocks, so this changes no existing output.
                  int ca = (int)std::count(a->name.begin(), a->name.end(), '/');
                  int cb = (int)std::count(b->name.begin(), b->name.end(), '/');
                  if (ca != cb) return ca < cb;
                  return a->name < b->name;
              });

    std::map<std::string, bool> exists;
    for (const auto& c : db.all_components())
        exists[c.name] = true;

    // A cell name derived from only the LEAF name collides when two blocks of
    // DIFFERENT size share a leaf under different parents ('top1/core' 10x10
    // vs 'top2/core' 20x20) — add_cell upserts, so the last writer's dims
    // overwrite the cell and the other component's metadata goes wrong (audit
    // C11-07). Pre-scan for leaf names carrying differing sizes and key those
    // off the FULL path instead; congruent same-leaf instances still share
    // one cell (order-independent, so no change to existing outputs).
    std::map<std::string, std::pair<double, double>> leaf_dims;
    std::set<std::string> ambiguous_leaf;
    for (const Block* b : ordered) {
        std::string leaf = _leaf_name(b->name);
        double w = b->x2 - b->x1, h = b->y2 - b->y1;
        auto it = leaf_dims.find(leaf);
        if (it == leaf_dims.end()) leaf_dims[leaf] = {w, h};
        else if (std::abs(it->second.first - w) > 1e-9 ||
                 std::abs(it->second.second - h) > 1e-9)
            ambiguous_leaf.insert(leaf);
    }
    // Reserve the fixed non-ambiguous cell names, then assign each ambiguous
    // block a cell name derived from its full path — but VERIFIED unique
    // against the reserved set and prior ambiguous names (PR #348 review): the
    // raw '/'->'_' flatten could otherwise collide with a real leaf-derived
    // name (e.g. 'top/core' -> 'top_core_cell', the same name a block literally
    // named 'top_core' gets), re-introducing the very cell-dims overwrite this
    // fix removes.  A '_<k>' suffix breaks any residual collision.
    std::set<std::string> used;
    for (const Block* b : ordered)
        if (!ambiguous_leaf.count(_leaf_name(b->name)))
            used.insert(_leaf_name(b->name) + "_cell");
    std::map<const Block*, std::string> cell_by_block;
    for (const Block* b : ordered) {
        std::string leaf = _leaf_name(b->name);
        if (!ambiguous_leaf.count(leaf)) { cell_by_block[b] = leaf + "_cell"; continue; }
        std::string p = b->name;
        std::replace(p.begin(), p.end(), '/', '_');
        std::string base = p + "_cell", name = base;
        int k = 2;
        while (used.count(name)) name = base + "_" + std::to_string(k++);
        used.insert(name);
        cell_by_block[b] = name;
    }

    for (const Block* b : ordered) {
        double w = b->x2 - b->x1;
        double h = b->y2 - b->y1;
        std::string cell   = cell_by_block[b];
        std::string parent = _parent_path(b->name);
        bool is_leaf = (has_children.count(b->name) == 0);
        db.add_cell(cell, w, h);
        if (exists[b->name]) {
            db.set_comp_bbox(b->name, b->x1, b->y1, b->x2, b->y2);
            if (!is_leaf)
                db.set_comp_is_leaf(b->name, false);
        } else {
            db.add_comp(b->name, cell, parent, b->x1, b->y1, b->x2, b->y2, is_leaf);
        }
    }
}

double FloorplannerEngine::_snap(double v) const {
    if (_grid <= 0.0) return v;
    return std::round(v / _grid) * _grid;
}

const FloorplannerEngine::Block&
FloorplannerEngine::_block_or_throw(const std::string& name) const {
    auto it = _blocks.find(name);
    if (it == _blocks.end())
        throw std::runtime_error("floorplanner block not found: " + name);
    return it->second;
}

FloorplannerEngine::Block&
FloorplannerEngine::_block_or_throw(const std::string& name) {
    auto it = _blocks.find(name);
    if (it == _blocks.end())
        throw std::runtime_error("floorplanner block not found: " + name);
    return it->second;
}

std::string FloorplannerEngine::_parent_path(const std::string& name) {
    size_t pos = name.find_last_of('/');
    if (pos == std::string::npos) return "";
    return name.substr(0, pos);
}

std::string FloorplannerEngine::_leaf_name(const std::string& name) {
    size_t pos = name.find_last_of('/');
    if (pos == std::string::npos) return name;
    return name.substr(pos + 1);
}

}  // namespace buda
