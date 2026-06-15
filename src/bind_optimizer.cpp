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

// bind_optimizer.cpp — Python bindings for PlacementOptimizer (SA + GA).
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "placement_optimizer.h"

namespace py = pybind11;
using namespace buda;

void bind_optimizer(py::module_& m) {
    // ── Output row types ─────────────────────────────────────────────────────
    py::class_<PlacedBlock>(m, "PlacedBlock")
        .def(py::init<>())
        .def_readwrite("name", &PlacedBlock::name)
        .def_readwrite("x",    &PlacedBlock::x)
        .def_readwrite("y",    &PlacedBlock::y)
        .def_readwrite("w",    &PlacedBlock::w)
        .def_readwrite("h",    &PlacedBlock::h);

    py::class_<OptimizerResult>(m, "OptimizerResult")
        .def(py::init<>())
        .def_readwrite("placements", &OptimizerResult::placements)
        .def_readwrite("hpwl",       &OptimizerResult::hpwl)
        .def_readwrite("area",       &OptimizerResult::area)
        .def_readwrite("overlap",    &OptimizerResult::overlap)
        .def_readwrite("iterations", &OptimizerResult::iterations);

    // ── PlacementOptimizer ───────────────────────────────────────────────────
    py::class_<PlacementOptimizer>(m, "PlacementOptimizer")
        .def(py::init<double, double, double>(),
             py::arg("die_w"), py::arg("die_h"), py::arg("grid") = 1.0)

        .def("add_block", &PlacementOptimizer::add_block,
             py::arg("name"), py::arg("w"), py::arg("h"))

        .def("add_block_ex", &PlacementOptimizer::add_block_ex,
             py::arg("name"), py::arg("w"), py::arg("h"),
             py::arg("x") = 0.0, py::arg("y") = 0.0,
             py::arg("min_w") = 0.0, py::arg("min_h") = 0.0,
             py::arg("fixed") = false, py::arg("reshapeable") = false)

        .def("add_net", &PlacementOptimizer::add_net, py::arg("pins"))

        .def("run_sa", &PlacementOptimizer::run_sa,
             py::arg("max_iter")  = 50000,
             py::arg("t_init")    = 1.0,
             py::arg("t_min")     = 1e-4,
             py::arg("alpha")     = 0.995,
             py::arg("w_wl")      = 1.0,
             py::arg("w_area")    = 0.1,
             py::arg("w_ovlp")    = 10.0,
             py::arg("seed")      = 42)

        .def("run_ga", &PlacementOptimizer::run_ga,
             py::arg("population")    = 80,
             py::arg("generations")   = 400,
             py::arg("mutation_rate") = 0.15,
             py::arg("crossover_rate") = 0.8,
             py::arg("w_wl")          = 1.0,
             py::arg("w_area")        = 0.1,
             py::arg("w_ovlp")        = 10.0,
             py::arg("seed")          = 42);
}
