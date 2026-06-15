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
    b.x1 = sx; b.y1 = sy; b.x2 = sx + w; b.y2 = sy + h;
    if (b.has_local) {
        std::string parent = _parent_path(name);
        if (!parent.empty()) {
            const Block& p = _block_or_throw(parent);
            b.local_x = b.x1 - p.x1;
            b.local_y = b.y1 - p.y1;
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
    double bottom = _block_or_throw(names.front()).y1;
    for (const auto& n : names)
        bottom = std::min(bottom, _block_or_throw(n).y1);

    for (const auto& n : names) {
        Block& b = _block_or_throw(n);
        double h = b.y2 - b.y1;
        b.y1 = bottom;
        b.y2 = bottom + h;
        if (b.has_local) {
            std::string parent = _parent_path(n);
            if (!parent.empty()) {
                const Block& p = _block_or_throw(parent);
                b.local_y = b.y1 - p.y1;
            }
        }
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
                  return std::count(a->name.begin(), a->name.end(), '/') <
                         std::count(b->name.begin(), b->name.end(), '/');
              });

    std::map<std::string, bool> exists;
    for (const auto& c : db.all_components())
        exists[c.name] = true;

    for (const Block* b : ordered) {
        double w = b->x2 - b->x1;
        double h = b->y2 - b->y1;
        std::string cell   = _leaf_name(b->name) + "_cell";
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
