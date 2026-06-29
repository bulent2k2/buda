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

        .def("run_sa",
             [](PlacementOptimizer& self,
                int max_iter, double t_init, double t_min, double alpha,
                double w_wl, double w_area, double w_ovlp, int seed,
                double time_budget_s, int patience,
                py::object progress_fn) -> OptimizerResult {
                 // Wrap the Python callable in a shared_ptr whose custom deleter
                 // acquires the GIL before Py_DECREF.  This lets the std::function
                 // be safely destroyed inside C++ code that holds no GIL.
                 PlacementOptimizer::ProgressFn cb;
                 if (!progress_fn.is_none()) {
                     // release() steals our reference without changing the refcount.
                     auto sptr = std::shared_ptr<PyObject>(
                         progress_fn.release().ptr(),
                         [](PyObject* p) { py::gil_scoped_acquire g; Py_XDECREF(p); });
                     cb = [sptr](int cur, int total) {
                         py::gil_scoped_acquire gil;
                         py::reinterpret_borrow<py::object>(sptr.get())(cur, total);
                     };
                 }
                 py::gil_scoped_release release;
                 return self.run_sa(max_iter, t_init, t_min, alpha,
                                    w_wl, w_area, w_ovlp, seed,
                                    time_budget_s, patience, std::move(cb));
             },
             py::arg("max_iter")      = 50000,
             py::arg("t_init")        = 1.0,
             py::arg("t_min")         = 1e-4,
             py::arg("alpha")         = 0.995,
             py::arg("w_wl")          = 1.0,
             py::arg("w_area")        = 0.1,
             py::arg("w_ovlp")        = 10.0,
             py::arg("seed")          = 42,
             py::arg("time_budget_s") = 0.0,
             py::arg("patience")      = 0,
             py::arg("progress_fn")   = py::none())

        .def("run_ga",
             [](PlacementOptimizer& self,
                int population, int generations,
                double mutation_rate, double crossover_rate,
                double w_wl, double w_area, double w_ovlp, int seed,
                double time_budget_s, int patience,
                py::object progress_fn) -> OptimizerResult {
                 PlacementOptimizer::ProgressFn cb;
                 if (!progress_fn.is_none()) {
                     auto sptr = std::shared_ptr<PyObject>(
                         progress_fn.release().ptr(),
                         [](PyObject* p) { py::gil_scoped_acquire g; Py_XDECREF(p); });
                     cb = [sptr](int cur, int total) {
                         py::gil_scoped_acquire gil;
                         py::reinterpret_borrow<py::object>(sptr.get())(cur, total);
                     };
                 }
                 py::gil_scoped_release release;
                 return self.run_ga(population, generations,
                                    mutation_rate, crossover_rate,
                                    w_wl, w_area, w_ovlp, seed,
                                    time_budget_s, patience, std::move(cb));
             },
             py::arg("population")    = 80,
             py::arg("generations")   = 400,
             py::arg("mutation_rate") = 0.15,
             py::arg("crossover_rate") = 0.8,
             py::arg("w_wl")          = 1.0,
             py::arg("w_area")        = 0.1,
             py::arg("w_ovlp")        = 10.0,
             py::arg("seed")          = 42,
             py::arg("time_budget_s") = 0.0,
             py::arg("patience")      = 0,
             py::arg("progress_fn")   = py::none());
}
