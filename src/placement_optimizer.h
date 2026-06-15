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

#include <cstddef>
#include <map>
#include <random>
#include <string>
#include <utility>
#include <vector>

namespace buda {

// ── Input types ──────────────────────────────────────────────────────────────

struct OptimizerBlock {
    std::string name;
    double w = 0.0, h = 0.0;     // current width / height (template)
    double x = 0.0, y = 0.0;     // initial / fixed position
    double orig_area = 0.0;       // w*h at add time; kept constant during reshape
    double min_w = 0.0;           // minimum width  (0 = no constraint)
    double min_h = 0.0;           // minimum height (0 = no constraint)
    bool fixed       = false;     // position locked — never moved or reshaped
    bool reshapeable = false;     // w/h may change (orig_area stays constant)
};

struct OptimizerPin {
    std::string comp_name;
    double local_x = 0.0, local_y = 0.0;  // offset from block origin
};

struct OptimizerNet {
    std::vector<OptimizerPin> pins;
};

// ── Output types ─────────────────────────────────────────────────────────────

struct PlacedBlock {
    std::string name;
    double x = 0.0, y = 0.0;    // grid-snapped top-left origin
    double w = 0.0, h = 0.0;    // final size (may differ from input if reshapeable)
};

struct OptimizerResult {
    std::vector<PlacedBlock> placements;
    double hpwl      = 0.0;  // half-perimeter wirelength
    double area      = 0.0;  // bounding-box area of all blocks
    double overlap   = 0.0;  // total pairwise overlap area (0 = legal)
    int    iterations = 0;
};

// ── Optimizer ─────────────────────────────────────────────────────────────────

class PlacementOptimizer {
public:
    PlacementOptimizer(double die_w, double die_h, double grid = 1.0);

    // Add a block with default constraints (moveable, not reshapeable).
    void add_block(const std::string& name, double w, double h);

    // Add a block with full constraint specification.
    // x, y: initial position (required if fixed=true; used as starting point otherwise).
    void add_block_ex(const std::string& name, double w, double h,
                      double x, double y,
                      double min_w, double min_h,
                      bool fixed, bool reshapeable);

    // Add a connectivity net.  pins = vector of (comp_name, (local_x, local_y)).
    void add_net(const std::vector<std::pair<std::string,
                                             std::pair<double,double>>>& pins);

    // ── Simulated Annealing ───────────────────────────────────────────────
    OptimizerResult run_sa(int    max_iter  = 50000,
                           double t_init    = 1.0,
                           double t_min     = 1e-4,
                           double alpha     = 0.995,
                           double w_wl      = 1.0,
                           double w_area    = 0.1,
                           double w_ovlp    = 10.0,
                           int    seed      = 42);

    // ── Genetic / Evolutionary Algorithm ─────────────────────────────────
    OptimizerResult run_ga(int    population    = 80,
                           int    generations   = 400,
                           double mutation_rate  = 0.15,
                           double crossover_rate = 0.8,
                           double w_wl           = 1.0,
                           double w_area         = 0.1,
                           double w_ovlp         = 10.0,
                           int    seed           = 42);

private:
    // Per-block mutable state during optimization (position + current size).
    struct BlockState {
        double x = 0.0, y = 0.0, w = 0.0, h = 0.0;
    };
    using Placement = std::vector<BlockState>;

    double    _snap(double v) const;
    Placement _random_placement(std::mt19937& rng) const;
    Placement _perturb(const Placement& p, std::mt19937& rng) const;
    void      _perturb_reshape(Placement& p, size_t idx, std::mt19937& rng) const;

    double _cost(const Placement& p,
                 double w_wl, double w_area, double w_ovlp) const;
    double _hpwl(const Placement& p) const;
    double _bbox_area(const Placement& p) const;
    double _total_overlap(const Placement& p) const;

    OptimizerResult _make_result(const Placement& p, int iters) const;

    // GA helpers
    size_t _tournament(const std::vector<double>& fitness,
                       size_t k, std::mt19937& rng) const;

    double _die_w, _die_h, _grid;
    std::vector<OptimizerBlock>       _blocks;
    std::vector<OptimizerNet>         _nets;
    std::map<std::string, size_t>     _name_to_idx;
};

}  // namespace buda
