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

// bindings.cpp — pybind11 module entry point for the full `buda` module.
// BDB types are imported from buda_db (which registers them once in pybind11's
// global type registry) and re-exposed under the buda namespace so that
// buda.BDB == buda_db.BDB.  This avoids the double-registration crash that
// occurs when both modules are loaded in the same process.
#include <pybind11/pybind11.h>
#include <pybind11/iostream.h>

#include <cstdio>

namespace py = pybind11;

void bind_bundler(py::module_& m);
void bind_routing(py::module_& m);
void bind_nuts(py::module_& m);
void bind_optimizer(py::module_& m);

PYBIND11_MODULE(buda, m) {
    // Issue #31: line-buffer C stdout so that when fd 1 is a file (a `> out.log`
    // redirect), C++ std::cout output ([Planner]/[NUTS]/…) interleaves with
    // Python's eagerly-flushed prints in CHRONOLOGICAL order. Without this, a
    // redirected stdout is fully block-buffered on some platforms (notably
    // macOS), so a partial [Planner] block stays stuck in the C buffer and is
    // only flushed at program exit — landing out of order at the end of the log.
    // Line buffering costs one write() per line (negligible); a TTY is already
    // line-buffered. Runs first, before any module output. See buda_cli.py
    // main(), which line-buffers the Python side to match.
    std::setvbuf(stdout, nullptr, _IOLBF, 0);

    py::add_ostream_redirect(m, "ostream_redirect");

    // Import BDB-layer types from buda_db; merge public names into buda
    // so existing code using buda.BDB / buda.ComponentRow etc. continues
    // to work without change.
    auto db_mod = py::module_::import("buda_db");
    for (auto item : db_mod.attr("__dict__")) {
        std::string k = item.cast<std::string>();
        if (!k.empty() && k[0] != '_')
            m.attr(item.cast<py::str>()) = db_mod.attr(item.cast<py::str>());
    }

    // Registration order matters: bundler before routing (BundleWrapper uses HBundle).
    bind_bundler(m);    // Strategy, HBundle, BundleWrapper, Bundler, HierarchicalBundler
    bind_routing(m);    // geometry, Floorplan, LayerStack, TopologyGenerator, CongestionPlanner
    bind_nuts(m);       // NUTS, DetailedNUTS, RoutingGrid, ConnTopology, verify
    bind_optimizer(m);  // PlacementOptimizer (SA + GA), PlacedBlock, OptimizerResult
}
