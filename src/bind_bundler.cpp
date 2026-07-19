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

// bind_bundler.cpp — Python bindings for bundle types and bundlers.
// Registers: Strategy enum, HBundle, BundleAssignment, BundleWrapper,
//            Netlist, Bundler, HierarchicalBundler.
// bind_db(m) must be called before this so BDB is already registered.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "bundler.h"

namespace py = pybind11;
using namespace buda;

void bind_bundler(py::module_& m) {
    py::enum_<Strategy>(m, "Strategy")
        .value("STRICT",        Strategy::STRICT)
        .value("CONVERGENT",    Strategy::CONVERGENT)
        .value("BIDIRECTIONAL", Strategy::BIDIRECTIONAL)
        .value("COMBINED",      Strategy::COMBINED);

    py::class_<HBundle>(m, "HBundle")
        .def(py::init<>())
        .def_readwrite("id",                 &HBundle::id)
        .def_readwrite("net_names",          &HBundle::net_names)
        .def_readwrite("reason",             &HBundle::reason)
        .def_readwrite("num_terminals",      &HBundle::num_terminals)
        .def_readwrite("level",              &HBundle::level)
        .def_readwrite("cell_context",       &HBundle::cell_context)
        .def_readwrite("instances",          &HBundle::instances)
        .def_readwrite("parent_id",          &HBundle::parent_id)
        .def_readwrite("child_ids",          &HBundle::child_ids)
        .def_readwrite("entry_busterm_ids",  &HBundle::entry_busterm_ids)
        .def_readwrite("exit_busterm_ids",   &HBundle::exit_busterm_ids)
        .def_readwrite("drv_spec_depth",     &HBundle::drv_spec_depth)
        .def_readwrite("rcv_spec_depth",     &HBundle::rcv_spec_depth)
        .def_readwrite("drv_spec_path",      &HBundle::drv_spec_path)
        .def_readwrite("net_drivers",        &HBundle::net_drivers)
        .def_readwrite("net_receivers",      &HBundle::net_receivers)
        .def_readwrite("rcv_spec_paths",     &HBundle::rcv_spec_paths)
        .def("get_net_names",                &HBundle::get_net_names);

    py::class_<Netlist>(m, "Netlist")
        .def(py::init<>())
        .def("add_net", &Netlist::add_net);

    py::class_<Bundler>(m, "Bundler")
        .def(py::init<>())
        .def("set_strategy", &Bundler::set_strategy)
        .def("run",          &Bundler::run);

    // BDB must already be registered (bind_db called first).
    py::class_<HierarchicalBundler>(m, "HierarchicalBundler")
        // keep_alive<1,2>: stores BDB& (bundler.h `BDB& _db`) — the Python
        // BDB must outlive the bundler (audit C7-02).
        .def(py::init<BDB&>(), py::keep_alive<1, 2>())
        .def("set_strategy", &HierarchicalBundler::set_strategy)
        .def("set_bundling_overrides", &HierarchicalBundler::set_bundling_overrides)
        .def("run", &HierarchicalBundler::run, py::arg("max_depth") = 1);
}
