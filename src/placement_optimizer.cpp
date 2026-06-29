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

#include "placement_optimizer.h"

#include <algorithm>
#include <cassert>
#include <chrono>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace buda {

// ── Constructor ──────────────────────────────────────────────────────────────

PlacementOptimizer::PlacementOptimizer(double die_w, double die_h, double grid)
    : _die_w(die_w), _die_h(die_h), _grid(grid > 0.0 ? grid : 1.0) {}

// ── Block / net registration ─────────────────────────────────────────────────

void PlacementOptimizer::add_block(const std::string& name, double w, double h) {
    add_block_ex(name, w, h, 0.0, 0.0, 0.0, 0.0, false, false);
}

void PlacementOptimizer::add_block_ex(const std::string& name,
                                       double w, double h,
                                       double x, double y,
                                       double min_w, double min_h,
                                       bool fixed, bool reshapeable) {
    if (w <= 0.0 || h <= 0.0)
        throw std::runtime_error("add_block_ex: size must be positive for " + name);
    if (_name_to_idx.count(name))
        throw std::runtime_error("add_block_ex: duplicate block name " + name);
    _name_to_idx[name] = _blocks.size();
    OptimizerBlock b;
    b.name        = name;
    b.w           = w; b.h = h;
    b.x           = x; b.y = y;
    b.orig_area   = w * h;
    b.min_w       = min_w;
    b.min_h       = min_h;
    b.fixed       = fixed;
    b.reshapeable = reshapeable;
    _blocks.push_back(b);
}

void PlacementOptimizer::add_net(
    const std::vector<std::pair<std::string, std::pair<double,double>>>& pins) {
    OptimizerNet net;
    for (const auto& [cname, lxy] : pins) {
        if (_name_to_idx.count(cname))
            net.pins.push_back({cname, lxy.first, lxy.second});
        // pins referencing unknown blocks are silently skipped
    }
    if (net.pins.size() >= 2)
        _nets.push_back(std::move(net));
}

// ── Private helpers ───────────────────────────────────────────────────────────

double PlacementOptimizer::_snap(double v) const {
    return std::round(v / _grid) * _grid;
}

PlacementOptimizer::Placement
PlacementOptimizer::_random_placement(std::mt19937& rng) const {
    Placement p(_blocks.size());
    for (size_t i = 0; i < _blocks.size(); i++) {
        const auto& b = _blocks[i];
        if (b.fixed) {
            p[i] = {b.x, b.y, b.w, b.h};
        } else {
            double max_x = std::max(0.0, _die_w - b.w);
            double max_y = std::max(0.0, _die_h - b.h);
            std::uniform_real_distribution<double> rx(0.0, max_x);
            std::uniform_real_distribution<double> ry(0.0, max_y);
            p[i] = {_snap(rx(rng)), _snap(ry(rng)), b.w, b.h};
        }
    }
    return p;
}

PlacementOptimizer::Placement
PlacementOptimizer::_perturb(const Placement& p, std::mt19937& rng) const {
    // Collect eligible block sets
    std::vector<size_t> moveable, swappable, reshapeable;
    for (size_t i = 0; i < _blocks.size(); i++) {
        if (_blocks[i].fixed) continue;
        moveable.push_back(i);
        if (_blocks[i].reshapeable) reshapeable.push_back(i);
    }
    if (moveable.empty()) return p;  // nothing to do

    // Build available move types
    std::vector<int> types = {0};                          // 0 = random move
    if (moveable.size() >= 2) types.push_back(1);         // 1 = swap
    if (!reshapeable.empty()) types.push_back(2);          // 2 = reshape

    std::uniform_int_distribution<size_t> pick_type(0, types.size() - 1);
    int move_type = types[pick_type(rng)];

    Placement q = p;
    std::uniform_int_distribution<size_t> pick_mv(0, moveable.size() - 1);

    if (move_type == 0) {
        // Random move: relocate one non-fixed block
        size_t idx = moveable[pick_mv(rng)];
        double max_x = std::max(0.0, _die_w - q[idx].w);
        double max_y = std::max(0.0, _die_h - q[idx].h);
        std::uniform_real_distribution<double> rx(0.0, max_x);
        std::uniform_real_distribution<double> ry(0.0, max_y);
        q[idx].x = _snap(rx(rng));
        q[idx].y = _snap(ry(rng));
    } else if (move_type == 1) {
        // Swap: exchange positions of two non-fixed blocks
        size_t a = moveable[pick_mv(rng)];
        size_t b_idx;
        do { b_idx = moveable[pick_mv(rng)]; } while (b_idx == a);
        std::swap(q[a].x, q[b_idx].x);
        std::swap(q[a].y, q[b_idx].y);
    } else {
        // Reshape: change aspect ratio of one reshapeable block
        std::uniform_int_distribution<size_t> pick_rs(0, reshapeable.size() - 1);
        size_t idx = reshapeable[pick_rs(rng)];
        _perturb_reshape(q, idx, rng);
    }
    return q;
}

void PlacementOptimizer::_perturb_reshape(Placement& p, size_t idx,
                                           std::mt19937& rng) const {
    const OptimizerBlock& b = _blocks[idx];
    double area  = b.orig_area;
    double min_w = std::max(b.min_w, _grid);
    double min_h = std::max(b.min_h, _grid);
    double max_w = std::min(_die_w, area / min_h);
    if (max_w < min_w) return;  // no room to reshape

    // Sample new width uniformly in log space (preserves area over orders of magnitude)
    std::uniform_real_distribution<double> r_log(std::log(min_w), std::log(max_w));
    double new_w = _snap(std::exp(r_log(rng)));
    if (new_w < _grid) new_w = _grid;
    double new_h = _snap(area / new_w);
    if (new_h < min_h || new_h > _die_h || new_w > _die_w) return;

    p[idx].w = new_w;
    p[idx].h = new_h;
    // Clamp position so block still fits inside die
    p[idx].x = std::min(p[idx].x, _die_w - new_w);
    p[idx].y = std::min(p[idx].y, _die_h - new_h);
    p[idx].x = std::max(p[idx].x, 0.0);
    p[idx].y = std::max(p[idx].y, 0.0);
}

double PlacementOptimizer::_hpwl(const Placement& p) const {
    double total = 0.0;
    for (const auto& net : _nets) {
        double x_lo = std::numeric_limits<double>::max();
        double x_hi = std::numeric_limits<double>::lowest();
        double y_lo = x_lo, y_hi = x_hi;
        bool   valid = false;
        for (const auto& pin : net.pins) {
            auto it = _name_to_idx.find(pin.comp_name);
            if (it == _name_to_idx.end()) continue;
            size_t i = it->second;
            double px = p[i].x + pin.local_x;
            double py = p[i].y + pin.local_y;
            x_lo = std::min(x_lo, px); x_hi = std::max(x_hi, px);
            y_lo = std::min(y_lo, py); y_hi = std::max(y_hi, py);
            valid = true;
        }
        if (valid) total += (x_hi - x_lo) + (y_hi - y_lo);
    }
    return total;
}

double PlacementOptimizer::_bbox_area(const Placement& p) const {
    if (p.empty()) return 0.0;
    double x1 = p[0].x,       y1 = p[0].y;
    double x2 = p[0].x + p[0].w, y2 = p[0].y + p[0].h;
    for (size_t i = 1; i < p.size(); i++) {
        x1 = std::min(x1, p[i].x);
        y1 = std::min(y1, p[i].y);
        x2 = std::max(x2, p[i].x + p[i].w);
        y2 = std::max(y2, p[i].y + p[i].h);
    }
    return (x2 - x1) * (y2 - y1);
}

double PlacementOptimizer::_total_overlap(const Placement& p) const {
    double total = 0.0;
    for (size_t i = 0; i < p.size(); i++) {
        for (size_t j = i + 1; j < p.size(); j++) {
            double ox = std::min(p[i].x + p[i].w, p[j].x + p[j].w)
                      - std::max(p[i].x, p[j].x);
            double oy = std::min(p[i].y + p[i].h, p[j].y + p[j].h)
                      - std::max(p[i].y, p[j].y);
            if (ox > 0.0 && oy > 0.0) total += ox * oy;
        }
    }
    return total;
}

double PlacementOptimizer::_cost(const Placement& p,
                                  double w_wl, double w_area, double w_ovlp) const {
    double c = 0.0;
    if (!_nets.empty())
        c += w_wl * _hpwl(p);
    double die_area = (_die_w > 0.0 && _die_h > 0.0) ? _die_w * _die_h : 1.0;
    c += w_area * _bbox_area(p) / die_area;
    c += w_ovlp * _total_overlap(p);
    return c;
}

OptimizerResult PlacementOptimizer::_make_result(const Placement& p, int iters) const {
    OptimizerResult r;
    r.placements.reserve(_blocks.size());
    for (size_t i = 0; i < _blocks.size(); i++)
        r.placements.push_back({_blocks[i].name, p[i].x, p[i].y, p[i].w, p[i].h});
    r.hpwl      = _hpwl(p);
    r.area      = _bbox_area(p);
    r.overlap   = _total_overlap(p);
    r.iterations = iters;
    return r;
}

size_t PlacementOptimizer::_tournament(const std::vector<double>& fitness,
                                        size_t k, std::mt19937& rng) const {
    std::uniform_int_distribution<size_t> pick(0, fitness.size() - 1);
    size_t best = pick(rng);
    for (size_t i = 1; i < k; i++) {
        size_t cand = pick(rng);
        if (fitness[cand] > fitness[best]) best = cand;
    }
    return best;
}

// ── Simulated Annealing ───────────────────────────────────────────────────────

OptimizerResult PlacementOptimizer::run_sa(int max_iter, double t_init, double t_min,
                                            double alpha, double w_wl, double w_area,
                                            double w_ovlp, int seed,
                                            double time_budget_s, int patience,
                                            ProgressFn progress_fn) {
    if (_blocks.empty()) return {};
    using clock = std::chrono::steady_clock;
    auto elapsed = [](clock::time_point t0) {
        return std::chrono::duration<double>(clock::now() - t0).count();
    };

    std::mt19937 rng(static_cast<unsigned>(seed));
    std::uniform_real_distribution<double> uniform01(0.0, 1.0);

    // Choose the iteration target and cooling schedule.  With a time budget,
    // calibrate per-iteration cost on a scratch placement and size the run (and
    // anneal alpha) to fit; max_iter, when > 0, is a hard ceiling.
    int    n_target  = max_iter;
    double alpha_eff = alpha;
    if (time_budget_s > 0.0) {
        std::mt19937 cal_rng(static_cast<unsigned>(seed) ^ 0x9e3779b9u);
        Placement cp = _random_placement(cal_rng);
        double cc = _cost(cp, w_wl, w_area, w_ovlp);
        const int cal_n = 32;
        auto c0 = clock::now();
        for (int i = 0; i < cal_n; i++) {
            Placement p2 = _perturb(cp, cal_rng);
            double nc = _cost(p2, w_wl, w_area, w_ovlp);
            if (nc < cc) { cp = std::move(p2); cc = nc; }
        }
        double per_iter = std::max(1e-9, elapsed(c0) / cal_n);
        long est = static_cast<long>(std::ceil(time_budget_s / per_iter));
        n_target = (max_iter > 0) ? std::min<long>(max_iter, est) : est;
        n_target = std::max(n_target, 1);
        alpha_eff = std::pow(t_min / t_init, 1.0 / std::max(1, n_target));
    }
    if (n_target <= 0) return {};   // neither an iteration nor a time bound

    Placement p    = _random_placement(rng);
    Placement best = p;
    double cur_cost  = _cost(p, w_wl, w_area, w_ovlp);
    double best_cost = cur_cost;
    double T = t_init;

    const int report_every = std::max(1, n_target / 20);
    // patience checkpoint size: ~2% of the run, capped so a huge iteration cap
    // doesn't make each window enormous.
    const int window  = std::min(std::max(1, n_target / 50), 200);
    double win_best   = best_cost;
    int    stale      = 0;
    const double rel_eps = 1e-4;
    auto t_start = clock::now();

    int iter = 0;
    for (; iter < n_target; iter++) {
        Placement p2    = _perturb(p, rng);
        double new_cost = _cost(p2, w_wl, w_area, w_ovlp);
        double dc       = new_cost - cur_cost;
        if (dc < 0.0 || uniform01(rng) < std::exp(-dc / T)) {
            p        = std::move(p2);
            cur_cost = new_cost;
            if (cur_cost < best_cost) {
                best      = p;
                best_cost = cur_cost;
            }
        }
        T = std::max(T * alpha_eff, t_min);
        if (progress_fn && (iter + 1) % report_every == 0)
            progress_fn(iter + 1, n_target);
        // Soft wall-clock cap.
        if (time_budget_s > 0.0 && elapsed(t_start) >= time_budget_s) {
            iter++; break;
        }
        // Convergence early-stop: no meaningful best improvement for `patience`
        // consecutive windows.
        if (patience > 0 && (iter + 1) % window == 0) {
            if (best_cost < win_best * (1.0 - rel_eps)) { win_best = best_cost; stale = 0; }
            else if (++stale >= patience) { iter++; break; }
        }
    }
    return _make_result(best, iter);
}

// ── Genetic / Evolutionary Algorithm ─────────────────────────────────────────

OptimizerResult PlacementOptimizer::run_ga(int population, int generations,
                                            double mutation_rate, double crossover_rate,
                                            double w_wl, double w_area, double w_ovlp,
                                            int seed, double time_budget_s, int patience,
                                            ProgressFn progress_fn) {
    if (_blocks.empty()) return {};
    if (population < 2) population = 2;
    using clock = std::chrono::steady_clock;
    auto elapsed = [](clock::time_point t0) {
        return std::chrono::duration<double>(clock::now() - t0).count();
    };
    std::mt19937 rng(static_cast<unsigned>(seed));
    std::uniform_real_distribution<double> uniform01(0.0, 1.0);

    // With a time budget the generation count is calibrated in-loop after gen 0;
    // start effectively unbounded (capped by `generations` when it is > 0).
    int n_target = (time_budget_s > 0.0)
                       ? (generations > 0 ? generations : std::numeric_limits<int>::max())
                       : generations;
    if (n_target <= 0 && time_budget_s <= 0.0) return {};

    // Initialise population with random placements
    std::vector<Placement> pop(static_cast<size_t>(population));
    for (auto& ind : pop) ind = _random_placement(rng);

    // Compute fitness (higher is better) and track global best
    auto eval_fitness = [&](std::vector<double>& fit) {
        for (int i = 0; i < population; i++) {
            double c = _cost(pop[static_cast<size_t>(i)], w_wl, w_area, w_ovlp);
            fit[static_cast<size_t>(i)] = 1.0 / (1.0 + c);
        }
    };
    std::vector<double> fitness(static_cast<size_t>(population));
    eval_fitness(fitness);

    Placement best_ind  = pop[0];
    double    best_cost = _cost(pop[0], w_wl, w_area, w_ovlp);
    for (int i = 1; i < population; i++) {
        double c = _cost(pop[static_cast<size_t>(i)], w_wl, w_area, w_ovlp);
        if (c < best_cost) { best_cost = c; best_ind = pop[static_cast<size_t>(i)]; }
    }

    const size_t n = _blocks.size();

    const int report_every_ga = std::max(1, (n_target == std::numeric_limits<int>::max()
                                             ? 20 : n_target) / 20);
    double prev_best = best_cost;
    int    stale     = 0;
    const double rel_eps = 1e-4;
    auto t_start = clock::now();

    int gen = 0;
    for (; gen < n_target; gen++) {
        std::vector<Placement> new_pop;
        new_pop.reserve(static_cast<size_t>(population));
        new_pop.push_back(best_ind);  // elitism: carry best individual unchanged

        while (static_cast<int>(new_pop.size()) < population) {
            size_t p1_idx = _tournament(fitness, 3, rng);
            Placement child = pop[p1_idx];

            if (uniform01(rng) < crossover_rate) {
                // Cut-point crossover: take second half's positions from another parent
                size_t p2_idx = _tournament(fitness, 3, rng);
                size_t cut = n / 2;
                for (size_t i = cut; i < n; i++) {
                    if (!_blocks[i].fixed)
                        child[i] = pop[p2_idx][i];
                }
            }
            if (uniform01(rng) < mutation_rate)
                child = _perturb(child, rng);

            new_pop.push_back(std::move(child));
        }

        pop = std::move(new_pop);
        eval_fitness(fitness);

        for (int i = 0; i < population; i++) {
            double c = _cost(pop[static_cast<size_t>(i)], w_wl, w_area, w_ovlp);
            if (c < best_cost) { best_cost = c; best_ind = pop[static_cast<size_t>(i)]; }
        }
        if (progress_fn && (gen + 1) % report_every_ga == 0)
            progress_fn(gen + 1, n_target);

        // Calibrate the generation count after the first generation's wall time.
        if (gen == 0 && time_budget_s > 0.0) {
            double per_gen = std::max(1e-9, elapsed(t_start));
            long est = static_cast<long>(std::ceil(time_budget_s / per_gen));
            n_target = (generations > 0) ? std::min<long>(generations, est) : est;
            n_target = std::max(n_target, 1);
        }
        // Soft wall-clock cap.
        if (time_budget_s > 0.0 && elapsed(t_start) >= time_budget_s) { gen++; break; }
        // Convergence early-stop: no meaningful improvement for `patience` gens.
        if (patience > 0) {
            if (best_cost < prev_best * (1.0 - rel_eps)) { prev_best = best_cost; stale = 0; }
            else if (++stale >= patience) { gen++; break; }
        }
    }
    return _make_result(best_ind, gen);
}

}  // namespace buda
