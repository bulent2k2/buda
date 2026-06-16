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

// bind_routing.cpp — Python bindings for the routing pipeline layer.
// Registers: geometry types (Point/Rect/Segment/Busterm/Topology), Floorplan,
//            LayerStack, TopologyGenerator, CongestionPlanner, FloorplannerEngine.
// bind_db(m) and bind_bundler(m) must be called before this.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "topology.h"
#include "layering.h"
#include "congestion_planner.h"
#include "floorplanner.h"

namespace py = pybind11;
using namespace buda;

void bind_routing(py::module_& m) {
    // ── Layer enums ───────────────────────────────────────────────────────
    py::enum_<LayerDir>(m, "LayerDir")
        .value("HORIZONTAL", LayerDir::HORIZONTAL)
        .value("VERTICAL",   LayerDir::VERTICAL);

    py::enum_<LayerType>(m, "LayerType")
        .value("TOP", LayerType::TOP)
        .value("LOW", LayerType::LOW);

    py::enum_<TegMode>(m, "TegMode")
        .value("THRU", TegMode::THRU)
        .value("OVER", TegMode::OVER);

    // ── Core geometry types ───────────────────────────────────────────────
    py::class_<Point>(m, "Point")
        .def(py::init<int, int>())
        .def_readwrite("x", &Point::x)
        .def_readwrite("y", &Point::y);

    py::class_<Rect>(m, "Rect")
        .def(py::init<int, int, int, int>())
        .def_readwrite("x1", &Rect::x1)
        .def_readwrite("y1", &Rect::y1)
        .def_readwrite("x2", &Rect::x2)
        .def_readwrite("y2", &Rect::y2);

    py::class_<Segment>(m, "Segment")
        .def(py::init<>())
        .def_readwrite("start",      &Segment::start)
        .def_readwrite("end",        &Segment::end)
        .def_readwrite("layer_hint", &Segment::layer_hint);

    py::class_<Busterm>(m, "Busterm")
        .def(py::init<>())
        .def_readwrite("block_name", &Busterm::block_name)
        .def_readwrite("bbox",       &Busterm::bbox)
        .def_readwrite("rects",      &Busterm::rects)
        .def_readwrite("teg_mode",   &Busterm::teg_mode);

    py::class_<Topology>(m, "Topology")
        .def(py::init<>())
        .def_readwrite("type",                  &Topology::type)
        .def_readwrite("segments",              &Topology::segments)
        .def_readwrite("estimated_wirelength",  &Topology::estimated_wirelength)
        .def_readwrite("trunk_location",        &Topology::trunk_location)
        .def_readwrite("pass_through_count",    &Topology::pass_through_count)
        .def_readwrite("seg_busterms",          &Topology::seg_busterms)
        .def_readwrite("bridge_segments",       &Topology::bridge_segments)
        .def_readwrite("connected_block_names", &Topology::connected_block_names);

    // ── Floorplan ─────────────────────────────────────────────────────────
    py::class_<BlockCornerMargin>(m, "BlockCornerMargin")
        .def(py::init<>())
        .def_readwrite("dx", &BlockCornerMargin::dx)
        .def_readwrite("dy", &BlockCornerMargin::dy);

    py::class_<KeepoutZone>(m, "KeepoutZone")
        .def_readwrite("bbox",      &KeepoutZone::bbox)
        .def_readwrite("layer_ids", &KeepoutZone::layer_ids);

    py::class_<Floorplan>(m, "Floorplan").def(py::init<>())
        .def("add_block",              &Floorplan::add_block)
        .def("add_block_rects", [](Floorplan& fp, const std::string& name,
                                   const std::vector<std::tuple<int,int,int,int>>& rects_py,
                                   TegMode mode) {
            std::vector<Rect> rects;
            rects.reserve(rects_py.size());
            for (const auto& [x1,y1,x2,y2] : rects_py)
                rects.push_back(Rect{x1,y1,x2,y2});
            fp.add_block_rects(name, rects, mode);
        }, py::arg("name"), py::arg("rects"), py::arg("teg_mode") = TegMode::THRU)
        .def("set_block_teg_mode",      &Floorplan::set_block_teg_mode)
        .def("get_block_teg_mode",      &Floorplan::get_block_teg_mode)
        .def("set_container",           &Floorplan::set_container,
             py::arg("name"), py::arg("is_container") = true)
        .def("is_container",            &Floorplan::is_container)
        .def("low_layer_keepouts",      &Floorplan::low_layer_keepouts,
             py::arg("low_layer_ids"))
        .def("add_keepout_zone",        &Floorplan::add_keepout_zone)
        .def("get_keepout_zones",       &Floorplan::get_keepout_zones)
        .def("get_block_rects", [](const Floorplan& fp, const std::string& name) {
            auto rects = fp.get_block_rects(name);
            std::vector<std::tuple<int,int,int,int>> out;
            for (const auto& r : rects) out.emplace_back(r.x1, r.y1, r.x2, r.y2);
            return out;
        })
        .def("set_block_corner_margin", &Floorplan::set_block_corner_margin)
        .def("set_global_corner_margin",&Floorplan::set_global_corner_margin)
        .def("set_min_stub_length",     &Floorplan::set_min_stub_length)
        .def("set_min_stub_length_dir", [](Floorplan& fp, LayerDir dir, int val) {
            fp.set_min_stub_length_dir(static_cast<int>(dir), val);
        })
        .def("set_min_stub_length_layer",&Floorplan::set_min_stub_length_layer)
        .def("get_min_stub_length", [](const Floorplan& fp, LayerDir dir, int layer_id) {
            return fp.get_min_stub_length(static_cast<int>(dir), layer_id);
        })
        .def("set_detour_channel",      &Floorplan::set_detour_channel)
        .def("get_detour_channel", [](const Floorplan& fp) {
            const auto& dc = fp.get_detour_channel();
            py::dict d;
            d["north"] = dc.north; d["south"] = dc.south;
            d["east"]  = dc.east;  d["west"]  = dc.west;
            return d;
        })
        .def("get_block_corner_margin", &Floorplan::get_block_corner_margin)
        .def("get_block_bounds",        &Floorplan::get_block_bounds)
        .def("has_block",               &Floorplan::has_block)
        .def("get_hanan_grid", [](const Floorplan& fp) {
            std::vector<int> x, y; fp.get_hanan_grid(x, y); return std::make_pair(x, y);
        })
        .def("get_all_blocks", [](const Floorplan& fp) { return fp.get_all_blocks(); });

    // ── LayerStack ────────────────────────────────────────────────────────
    py::class_<LayerStack>(m, "LayerStack").def(py::init<>())
        .def("add_layer",               &LayerStack::add_layer)
        .def("set_layer_dilution",      &LayerStack::set_layer_dilution)
        .def("set_layer_overhead",      &LayerStack::set_layer_overhead)
        .def("set_bit_pitch",           &LayerStack::set_bit_pitch)
        .def("eff_bus_width",           &LayerStack::eff_bus_width,
             py::arg("bits"), py::arg("base_width"), py::arg("layer_id"))
        .def("set_layer_span",          &LayerStack::set_layer_span)
        .def("set_layer_kspan",         &LayerStack::set_layer_kspan)
        .def("is_top",                  &LayerStack::is_top)
        .def("has_layer",               &LayerStack::has_layer)
        .def("get_layer_dir",           &LayerStack::get_layer_dir)
        .def("get_layer_ids_by_dir",    &LayerStack::get_layer_ids_by_dir)
        .def("get_layer_ids_preferred", &LayerStack::get_layer_ids_preferred)
        .def("get_top_layer",           &LayerStack::get_top_layer)
        .def("get_layer_type",          &LayerStack::get_layer_type)
        .def("get_layer_dilution",      &LayerStack::get_layer_dilution);

    // ── TopologyGenerator ─────────────────────────────────────────────────
    py::class_<TopologyGenerator>(m, "TopologyGenerator")
        .def(py::init<const Floorplan&>())
        .def("set_busterm_mode",   &TopologyGenerator::set_busterm_mode)
        .def("set_double_detour",  &TopologyGenerator::set_double_detour)
        .def("set_layer_ids",      &TopologyGenerator::set_layer_ids)
        .def("set_all_h_layers",   &TopologyGenerator::set_all_h_layers)
        .def("set_all_v_layers",   &TopologyGenerator::set_all_v_layers)
        .def("generate_candidates", [](TopologyGenerator& self,
                                       const std::string& src,
                                       py::object dsts) {
            if (py::isinstance<py::str>(dsts))
                return self.generate_candidates(src, {dsts.cast<std::string>()});
            return self.generate_candidates(src, dsts.cast<std::vector<std::string>>());
        });

    // ── Bundle planner types (defined in congestion_planner.h) ───────────
    // BundleAssignment and BundleWrapper live here because they are tightly
    // coupled to CongestionPlanner; bind_bundler.cpp only binds bundler.h types.
    py::class_<BundleAssignment>(m, "BundleAssignment")
        .def_readwrite("bundle_id",  &BundleAssignment::bundle_id)
        .def_readwrite("topo_index", &BundleAssignment::topo_index)
        .def_readwrite("v_layer_id", &BundleAssignment::v_layer_id)
        .def_readwrite("h_layer_id", &BundleAssignment::h_layer_id)
        .def_readwrite("seg_layers", &BundleAssignment::seg_layers)
        .def_readwrite("seg_perp",   &BundleAssignment::seg_perp);

    py::class_<BundleInput>(m, "BundleInput")
        .def(py::init<>())
        .def_readwrite("original_bundle",   &BundleInput::original_bundle)
        .def_readwrite("candidates",        &BundleInput::candidates)
        .def_readwrite("width",             &BundleInput::width)
        .def_readwrite("pinned_seg_layers", &BundleInput::pinned_seg_layers)
        .def_readwrite("assigned_v_layer",  &BundleInput::assigned_v_layer)
        .def_readwrite("assigned_h_layer",  &BundleInput::assigned_h_layer)
        .def_readwrite("topology_pinned",   &BundleInput::topology_pinned);

    py::class_<BundlePlan>(m, "BundlePlan")
        .def(py::init<>())
        .def_readwrite("selected_topology_index", &BundlePlan::selected_topology_index)
        .def_readwrite("seg_layers",              &BundlePlan::seg_layers)
        .def_readwrite("seg_perp",                &BundlePlan::seg_perp)
        .def_readwrite("seg_net_pull",            &BundlePlan::seg_net_pull)
        .def_readwrite("seg_slide_lo",            &BundlePlan::seg_slide_lo)
        .def_readwrite("seg_slide_hi",            &BundlePlan::seg_slide_hi);

    py::class_<BundleHierMeta>(m, "BundleHierMeta")
        .def(py::init<>())
        .def_readwrite("level",           &BundleHierMeta::level)
        .def_readwrite("priority",        &BundleHierMeta::priority)
        .def_readwrite("has_reservation", &BundleHierMeta::has_reservation)
        .def_readwrite("res_x1",          &BundleHierMeta::res_x1)
        .def_readwrite("res_y1",          &BundleHierMeta::res_y1)
        .def_readwrite("res_x2",          &BundleHierMeta::res_x2)
        .def_readwrite("res_y2",          &BundleHierMeta::res_y2);

    py::class_<BundleWrapper>(m, "BundleWrapper")
        .def(py::init<>())
        .def_readwrite("input", &BundleWrapper::input)
        .def_readwrite("plan",  &BundleWrapper::plan)
        .def_readwrite("hier",  &BundleWrapper::hier);

    // ── CongestionPlanner ─────────────────────────────────────────────────
    py::class_<GlobalCut>(m, "GlobalCut")
        .def(py::init<>())
        .def_readwrite("p1",         &GlobalCut::p1)
        .def_readwrite("p2",         &GlobalCut::p2)
        .def_readwrite("cut_coord",  &GlobalCut::cut_coord)
        .def_readwrite("dir",        &GlobalCut::dir)
        .def_readwrite("layer_id",   &GlobalCut::layer_id)
        .def_property_readonly("band_cap",
            [](const GlobalCut& c) { return c.caps(); })
        .def_property_readonly("band_usage",
            [](const GlobalCut& c) { return c.usages(); })
        .def("num_bands", &GlobalCut::num_bands)
        .def("cap",       &GlobalCut::cap,   py::arg("band"))
        .def("usage",     &GlobalCut::usage, py::arg("band"));

    py::class_<CongestionPlanner>(m, "CongestionPlanner")
        .def(py::init<const Floorplan&, const LayerStack&>())
        .def("set_planner_param",    &CongestionPlanner::set_planner_param)
        .def("set_track_pitch",      &CongestionPlanner::set_track_pitch)
        .def("build_congestion_map", &CongestionPlanner::build_congestion_map)
        .def("optimize_topologies",  &CongestionPlanner::optimize_topologies)
        .def("get_cuts",             &CongestionPlanner::get_cuts)
        .def("get_x_grid",           &CongestionPlanner::get_x_grid)
        .def("get_y_grid",           &CongestionPlanner::get_y_grid);

    // ── FloorplannerEngine ────────────────────────────────────────────────
    // Note: write_bdb(BDB&) requires BDB to already be registered (bind_db first).
    py::class_<FloorplanBlockRow>(m, "FloorplanBlockRow")
        .def_readwrite("name", &FloorplanBlockRow::name)
        .def_readwrite("x1",   &FloorplanBlockRow::x1)
        .def_readwrite("y1",   &FloorplanBlockRow::y1)
        .def_readwrite("x2",   &FloorplanBlockRow::x2)
        .def_readwrite("y2",   &FloorplanBlockRow::y2);

    py::class_<FloorplanIssue>(m, "FloorplanIssue")
        .def_readwrite("kind",    &FloorplanIssue::kind)
        .def_readwrite("block_a", &FloorplanIssue::block_a)
        .def_readwrite("block_b", &FloorplanIssue::block_b)
        .def_readwrite("message", &FloorplanIssue::message);

    py::class_<FloorplannerEngine>(m, "FloorplannerEngine")
        .def(py::init<>())
        .def("set_die",              &FloorplannerEngine::set_die,
             py::arg("w"), py::arg("h"))
        .def("set_grid",             &FloorplannerEngine::set_grid,
             py::arg("grid"))
        .def("die_w",                &FloorplannerEngine::die_w)
        .def("die_h",                &FloorplannerEngine::die_h)
        .def("grid",                 &FloorplannerEngine::grid)
        .def("add_block",            &FloorplannerEngine::add_block,
             py::arg("name"), py::arg("x1"), py::arg("y1"),
             py::arg("x2"), py::arg("y2"))
        .def("add_child_block",      &FloorplannerEngine::add_child_block,
             py::arg("name"), py::arg("local_x"), py::arg("local_y"),
             py::arg("w"), py::arg("h"))
        .def("move_block_raw",       &FloorplannerEngine::move_block_raw,
             py::arg("name"), py::arg("x"), py::arg("y"))
        .def("resize_block_raw",     &FloorplannerEngine::resize_block_raw,
             py::arg("name"), py::arg("x1"), py::arg("y1"),
             py::arg("x2"), py::arg("y2"))
        .def("move_child_local",     &FloorplannerEngine::move_child_local,
             py::arg("name"), py::arg("local_x"), py::arg("local_y"))
        .def("align_bottom",         &FloorplannerEngine::align_bottom, py::arg("names"))
        .def("align_top",            &FloorplannerEngine::align_top,    py::arg("names"))
        .def("align_left",           &FloorplannerEngine::align_left,   py::arg("names"))
        .def("align_right",          &FloorplannerEngine::align_right,  py::arg("names"))
        .def("get_block",            &FloorplannerEngine::get_block,
             py::arg("name"))
        .def("get_child_local_origin", &FloorplannerEngine::get_child_local_origin,
             py::arg("name"))
        .def("validate",             &FloorplannerEngine::validate)
        .def("write_bdb",            &FloorplannerEngine::write_bdb,
             py::arg("db"));
}
