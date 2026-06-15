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

#include <map>
#include <string>
#include <vector>

#include "bdb.h"

namespace buda {

struct FloorplanBlockRow {
    std::string name;
    double x1 = 0.0;
    double y1 = 0.0;
    double x2 = 0.0;
    double y2 = 0.0;
};

struct FloorplanIssue {
    std::string kind;      // ERROR | OVERLAP | OUTSIDE_DIE
    std::string block_a;
    std::string block_b;
    std::string message;
};

class FloorplannerEngine {
public:
    void set_die(double w, double h);
    void set_grid(double grid);

    double die_w() const { return _die_w; }
    double die_h() const { return _die_h; }
    double grid()  const { return _grid; }

    void add_block(const std::string& name,
                   double x1, double y1, double x2, double y2);
    void add_child_block(const std::string& name,
                         double local_x, double local_y,
                         double w, double h);

    void move_block_raw(const std::string& name, double x, double y);
    void resize_block_raw(const std::string& name,
                          double x1, double y1, double x2, double y2);
    void move_child_local(const std::string& name, double local_x, double local_y);
    void align_bottom(const std::vector<std::string>& names);

    FloorplanBlockRow get_block(const std::string& name) const;
    std::pair<double, double> get_child_local_origin(const std::string& name) const;
    std::vector<FloorplanIssue> validate() const;

    void write_bdb(BDB& db) const;

private:
    struct Block {
        std::string name;
        double x1 = 0.0, y1 = 0.0, x2 = 0.0, y2 = 0.0;
        double local_x = 0.0, local_y = 0.0;
        bool has_local = false;
    };

    double _die_w = 0.0;
    double _die_h = 0.0;
    double _grid = 1.0;
    std::map<std::string, Block> _blocks;
    std::vector<FloorplanIssue> _errors;

    double _snap(double v) const;
    const Block& _block_or_throw(const std::string& name) const;
    Block& _block_or_throw(const std::string& name);
    static std::string _parent_path(const std::string& name);
    static std::string _leaf_name(const std::string& name);
};

}  // namespace buda
