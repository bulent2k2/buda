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

// bind_db.cpp — Python bindings for the BDB physical design database layer.
// Registers: BDB row types, BustermGen, HierBusterm, BDB class.
// No routing-pipeline types here; those are in bind_routing.cpp / bind_nuts.cpp.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "bdb.h"
#include "busterm.h"

namespace py = pybind11;
using namespace buda;

void bind_db(py::module_& m) {
    // ── BustermResolution enum ─────────────────────────────────────────────
    py::enum_<BustermResolution>(m, "BustermResolution")
        .value("BLOCK",            BustermResolution::BLOCK)
        .value("SPATIAL_CLUSTER",  BustermResolution::SPATIAL_CLUSTER)
        .value("PORT",             BustermResolution::PORT);

    // ── HierBusterm ───────────────────────────────────────────────────────
    py::class_<HierBusterm>(m, "HierBusterm")
        .def(py::init<>())
        .def_readwrite("id",         &HierBusterm::id)
        .def_readwrite("hier_path",  &HierBusterm::hier_path)
        .def_readwrite("depth",      &HierBusterm::depth)
        .def_readwrite("x1",         &HierBusterm::x1)
        .def_readwrite("y1",         &HierBusterm::y1)
        .def_readwrite("x2",         &HierBusterm::x2)
        .def_readwrite("y2",         &HierBusterm::y2)
        .def_readwrite("resolution", &HierBusterm::resolution)
        .def_readwrite("parent_id",  &HierBusterm::parent_id)
        .def_readwrite("rects",      &HierBusterm::rects);

    // ── BustermGen ────────────────────────────────────────────────────────
    py::class_<BustermGen>(m, "BustermGen")
        .def(py::init<BDB&>(), py::arg("db"))
        .def("derive", &BustermGen::derive, py::arg("max_depth") = 0)
        .def("refine", &BustermGen::refine)
        .def("all",    &BustermGen::all);

    // ── BDB row types ──────────────────────────────────────────────────────
    py::class_<ComponentRow>(m, "ComponentRow")
        .def_readwrite("id",            &ComponentRow::id)
        .def_readwrite("name",          &ComponentRow::name)
        .def_readwrite("cell",          &ComponentRow::cell)
        .def_readwrite("parent_id",     &ComponentRow::parent_id)
        .def_readwrite("depth",         &ComponentRow::depth)
        .def_readwrite("x1",            &ComponentRow::x1)
        .def_readwrite("y1",            &ComponentRow::y1)
        .def_readwrite("x2",            &ComponentRow::x2)
        .def_readwrite("y2",            &ComponentRow::y2)
        .def_readwrite("is_leaf",       &ComponentRow::is_leaf)
        .def_readwrite("is_replicated", &ComponentRow::is_replicated);

    py::class_<NetRow>(m, "NetRow")
        .def_readwrite("id",   &NetRow::id)
        .def_readwrite("name", &NetRow::name);

    py::class_<PinRow>(m, "PinRow")
        .def_readwrite("net_id",   &PinRow::net_id)
        .def_readwrite("comp_id",  &PinRow::comp_id)
        .def_readwrite("pin_name", &PinRow::pin_name)
        .def_readwrite("dir",      &PinRow::dir)
        .def_readwrite("px",       &PinRow::px)
        .def_readwrite("py",       &PinRow::py);

    py::class_<GrpRow>(m, "GrpRow")
        .def_readwrite("id",        &GrpRow::id)
        .def_readwrite("name",      &GrpRow::name)
        .def_readwrite("color",     &GrpRow::color)
        .def_readwrite("parent_id", &GrpRow::parent_id);

    py::class_<CellRow>(m, "CellRow")
        .def_readwrite("name",   &CellRow::name)
        .def_readwrite("width",  &CellRow::width)
        .def_readwrite("height", &CellRow::height);

    py::class_<CellPinRow>(m, "CellPinRow")
        .def_readwrite("cell",     &CellPinRow::cell)
        .def_readwrite("pin_name", &CellPinRow::pin_name)
        .def_readwrite("dir",      &CellPinRow::dir)
        .def_readwrite("px",       &CellPinRow::px)
        .def_readwrite("py",       &CellPinRow::py);

    py::class_<BustermRow>(m, "BustermRow")
        .def(py::init<>())
        .def_readwrite("id",         &BustermRow::id)
        .def_readwrite("comp_id",    &BustermRow::comp_id)
        .def_readwrite("hier_path",  &BustermRow::hier_path)
        .def_readwrite("depth",      &BustermRow::depth)
        .def_readwrite("x1",         &BustermRow::x1)
        .def_readwrite("y1",         &BustermRow::y1)
        .def_readwrite("x2",         &BustermRow::x2)
        .def_readwrite("y2",         &BustermRow::y2)
        .def_readwrite("resolution", &BustermRow::resolution)
        .def_readwrite("parent_id",  &BustermRow::parent_id)
        .def_readwrite("rects",      &BustermRow::rects);

    // ── BDB ───────────────────────────────────────────────────────────────
    py::class_<BDB>(m, "BDB")
        .def(py::init<const std::string&>())
        .def("import_def_lef",  &BDB::import_def_lef,
             py::arg("def_path"), py::arg("lef_path"))
        .def("import_verilog",  &BDB::import_verilog, py::arg("v_path"))
        .def("compute_hpwl",    &BDB::compute_hpwl)
        .def("compute_fanout",  &BDB::compute_fanout)
        .def("compute_all",     &BDB::compute_all)
        .def("all_components",       &BDB::all_components)
        .def("components_at_depth",  &BDB::components_at_depth, py::arg("depth"))
        .def("pins_by_comp",         &BDB::pins_by_comp, py::arg("comp_id"))
        .def("add_busterm",          &BDB::add_busterm, py::arg("bt"))
        .def("clear_busterms",       &BDB::clear_busterms)
        .def("all_nets",        &BDB::all_nets)
        .def("all_pins",        &BDB::all_pins)
        .def("all_busterms",    &BDB::all_busterms)
        .def("all_bundles",     &BDB::all_bundles)
        .def("nets_by_hpwl",    &BDB::nets_by_hpwl,
             py::arg("lo"), py::arg("hi"))
        .def("comps_in_rect",   &BDB::comps_in_rect,
             py::arg("xl"), py::arg("yl"), py::arg("xh"), py::arg("yh"))
        .def("common_nets",     &BDB::common_nets,
             py::arg("bundle_id1"), py::arg("bundle_id2"))
        .def("new_group",       &BDB::new_group,
             py::arg("name"), py::arg("color"), py::arg("parent_id") = "")
        .def("add_grp_member",  &BDB::add_grp_member,
             py::arg("gid"), py::arg("kind"), py::arg("ref"))
        .def("remove_grp_member", &BDB::remove_grp_member,
             py::arg("gid"), py::arg("kind"), py::arg("ref"))
        .def("delete_group",    &BDB::delete_group, py::arg("gid"))
        .def("all_groups",      &BDB::all_groups)
        .def("units",           &BDB::units)
        .def("die_w",           &BDB::die_w)
        .def("die_h",           &BDB::die_h)
        .def("set_die",         &BDB::set_die, py::arg("w"), py::arg("h"))
        .def_static("db_path",  &BDB::db_path, py::arg("def_path"))
        .def("move_comp",       &BDB::move_comp,
             py::arg("name"), py::arg("x"), py::arg("y"))
        .def("set_comp_bbox",   &BDB::set_comp_bbox,
             py::arg("name"), py::arg("x1"), py::arg("y1"),
             py::arg("x2"), py::arg("y2"))
        .def("resize_cell",     &BDB::resize_cell,
             py::arg("cell"), py::arg("w"), py::arg("h"))
        .def("set_comp_cell",   &BDB::set_comp_cell,
             py::arg("comp_name"), py::arg("new_cell"))
        .def("add_comp",        &BDB::add_comp,
             py::arg("name"), py::arg("cell"), py::arg("parent_name"),
             py::arg("x1"), py::arg("y1"), py::arg("x2"), py::arg("y2"),
             py::arg("is_leaf") = true)
        .def("add_cell",        &BDB::add_cell,
             py::arg("name"), py::arg("w"), py::arg("h"))
        .def("all_cells",       &BDB::all_cells)
        .def("add_inst",        &BDB::add_inst,
             py::arg("inst_name"), py::arg("cell_name"), py::arg("parent_name"),
             py::arg("x"), py::arg("y"))
        .def("add_inst_to_cell", &BDB::add_inst_to_cell,
             py::arg("parent_cell"), py::arg("inst_name"),
             py::arg("child_cell"), py::arg("x"), py::arg("y"))
        .def("flip_comp",       &BDB::flip_comp,
             py::arg("name"), py::arg("flip_x"))
        .def("rotate_comp",     &BDB::rotate_comp,
             py::arg("name"), py::arg("degrees"))
        .def("add_cell_pin",    &BDB::add_cell_pin,
             py::arg("cell"), py::arg("pin_name"),
             py::arg("dir") = "INOUT", py::arg("px") = -1.0, py::arg("py") = -1.0)
        .def("all_cell_pins",   &BDB::all_cell_pins)
        .def("infer_pin_dirs_from_cell_pins", &BDB::infer_pin_dirs_from_cell_pins)
        .def("add_net_pins",    &BDB::add_net_pins,
             py::arg("net_name"), py::arg("drv"), py::arg("rcvs"))
        .def("add_net_pins_undirected", &BDB::add_net_pins_undirected,
             py::arg("net_name"), py::arg("pins"))
        .def("add_net_pins_inout", &BDB::add_net_pins_inout,
             py::arg("net_name"), py::arg("pins"));
}
