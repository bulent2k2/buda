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
#include "gds_io.h"

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
        // keep_alive<1,2>: BustermGen stores BDB& — the Python BDB must
        // outlive the generator or every later call dangles (audit C7-01).
        .def(py::init<BDB&>(), py::arg("db"), py::keep_alive<1, 2>())
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
        .def_readwrite("is_replicated", &ComponentRow::is_replicated)
        .def_readwrite("orient",        &ComponentRow::orient);

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

    py::class_<BundleRow>(m, "BundleRow")
        .def(py::init<>())
        .def_readwrite("id",             &BundleRow::id)
        .def_readwrite("level",          &BundleRow::level)
        .def_readwrite("strategy",       &BundleRow::strategy)
        .def_readwrite("reason",         &BundleRow::reason)
        .def_readwrite("num_terminals",  &BundleRow::num_terminals)
        .def_readwrite("cell_context",   &BundleRow::cell_context)
        .def_readwrite("instances",      &BundleRow::instances)
        .def_readwrite("parent_id",      &BundleRow::parent_id)
        .def_readwrite("is_replicated",  &BundleRow::is_replicated)
        .def_readwrite("drv_spec_depth", &BundleRow::drv_spec_depth)
        .def_readwrite("rcv_spec_depth", &BundleRow::rcv_spec_depth)
        .def_readwrite("drv_spec_path",  &BundleRow::drv_spec_path)
        .def_readwrite("rcv_spec_paths", &BundleRow::rcv_spec_paths)
        .def_readwrite("is_expanded",    &BundleRow::is_expanded)
        .def_readwrite("bu_locked",      &BundleRow::bu_locked)
        .def_readwrite("cloned_from",    &BundleRow::cloned_from);

    py::class_<TopoRow>(m, "TopoRow")
        .def(py::init<>())
        .def_readwrite("id",                 &TopoRow::id)
        .def_readwrite("cand_index",         &TopoRow::cand_index)
        .def_readwrite("type",               &TopoRow::type)
        .def_readwrite("wirelength",         &TopoRow::wirelength)
        .def_readwrite("trunk_location",     &TopoRow::trunk_location)
        .def_readwrite("pass_through_count", &TopoRow::pass_through_count)
        .def_readwrite("connected_blocks",   &TopoRow::connected_blocks)
        .def_readwrite("feedthru_blocks",    &TopoRow::feedthru_blocks)
        .def_readwrite("is_selected",        &TopoRow::is_selected)
        .def_readwrite("is_pinned",          &TopoRow::is_pinned)
        .def_readwrite("topo_uid",           &TopoRow::topo_uid)
        .def_readwrite("source",             &TopoRow::source);

    py::class_<TopoSegRow>(m, "TopoSegRow")
        .def(py::init<>())
        .def_readwrite("id",         &TopoSegRow::id)
        .def_readwrite("cand_index", &TopoSegRow::cand_index)
        .def_readwrite("seg_index",  &TopoSegRow::seg_index)
        .def_readwrite("x1",         &TopoSegRow::x1)
        .def_readwrite("y1",         &TopoSegRow::y1)
        .def_readwrite("x2",         &TopoSegRow::x2)
        .def_readwrite("y2",         &TopoSegRow::y2)
        .def_readwrite("layer_hint", &TopoSegRow::layer_hint)
        .def_readwrite("is_jog",     &TopoSegRow::is_jog)
        .def_readwrite("assigned_layer", &TopoSegRow::assigned_layer)
        .def_readwrite("edge_id",    &TopoSegRow::edge_id)
        .def_readwrite("perp_clamp_lo", &TopoSegRow::perp_clamp_lo)
        .def_readwrite("perp_clamp_hi", &TopoSegRow::perp_clamp_hi);

    py::class_<TopoBridgeRow>(m, "TopoBridgeRow")
        .def(py::init<>())
        .def_readwrite("id",         &TopoBridgeRow::id)
        .def_readwrite("cand_index", &TopoBridgeRow::cand_index)
        .def_readwrite("block_name", &TopoBridgeRow::block_name)
        .def_readwrite("x1",         &TopoBridgeRow::x1)
        .def_readwrite("y1",         &TopoBridgeRow::y1)
        .def_readwrite("x2",         &TopoBridgeRow::x2)
        .def_readwrite("y2",         &TopoBridgeRow::y2)
        .def_readwrite("layer_hint", &TopoBridgeRow::layer_hint)
        .def_readwrite("is_jog",     &TopoBridgeRow::is_jog);

    // One persisted seg_conns junction link (v12); raw-row access for tests —
    // the routing bridge (persist/load_seg_conns in the buda module) is the
    // production surface.
    py::class_<TopoSegConnRow>(m, "TopoSegConnRow")
        .def(py::init<>())
        .def_readwrite("bundle_id",  &TopoSegConnRow::bundle_id)
        .def_readwrite("cand_index", &TopoSegConnRow::cand_index)
        .def_readwrite("seg_index",  &TopoSegConnRow::seg_index)
        .def_readwrite("endpoint",   &TopoSegConnRow::endpoint)
        .def_readwrite("other_seg",  &TopoSegConnRow::other_seg);

    py::class_<BusSegRow>(m, "BusSegRow")
        .def(py::init<>())
        .def_readwrite("id",             &BusSegRow::id)
        .def_readwrite("seg_idx",        &BusSegRow::seg_idx)
        .def_readwrite("layer",          &BusSegRow::layer)
        .def_readwrite("is_horiz",       &BusSegRow::is_horiz)
        .def_readwrite("x1",             &BusSegRow::x1)
        .def_readwrite("y1",             &BusSegRow::y1)
        .def_readwrite("x2",             &BusSegRow::x2)
        .def_readwrite("y2",             &BusSegRow::y2)
        .def_readwrite("track_position", &BusSegRow::track_position)
        .def_readwrite("width",          &BusSegRow::width)
        .def_readwrite("placed",         &BusSegRow::placed)
        .def_readwrite("is_jog",         &BusSegRow::is_jog)
        .def_readwrite("interval_lo",    &BusSegRow::interval_lo)
        .def_readwrite("interval_hi",    &BusSegRow::interval_hi)
        .def_readwrite("track_lo_bound", &BusSegRow::track_lo_bound)
        .def_readwrite("track_hi_bound", &BusSegRow::track_hi_bound);

    py::class_<BusViaRow>(m, "BusViaRow")
        .def(py::init<>())
        .def_readwrite("id",         &BusViaRow::id)
        .def_readwrite("from_seg",   &BusViaRow::from_seg)
        .def_readwrite("to_seg",     &BusViaRow::to_seg)
        .def_readwrite("from_layer", &BusViaRow::from_layer)
        .def_readwrite("to_layer",   &BusViaRow::to_layer)
        .def_readwrite("x",          &BusViaRow::x)
        .def_readwrite("y",          &BusViaRow::y)
        .def_readwrite("bit_width",  &BusViaRow::bit_width);

    py::class_<NetSegRow>(m, "NetSegRow")
        .def(py::init<>())
        .def_readwrite("id",             &NetSegRow::id)
        .def_readwrite("seg_idx",        &NetSegRow::seg_idx)
        .def_readwrite("bit_index",      &NetSegRow::bit_index)
        .def_readwrite("net_id",         &NetSegRow::net_id)
        .def_readwrite("net_name",       &NetSegRow::net_name)
        .def_readwrite("layer",          &NetSegRow::layer)
        .def_readwrite("is_horiz",       &NetSegRow::is_horiz)
        .def_readwrite("x1",             &NetSegRow::x1)
        .def_readwrite("y1",             &NetSegRow::y1)
        .def_readwrite("x2",             &NetSegRow::x2)
        .def_readwrite("y2",             &NetSegRow::y2)
        .def_readwrite("track_position", &NetSegRow::track_position)
        .def_readwrite("width",          &NetSegRow::width);

    py::class_<NetViaRow>(m, "NetViaRow")
        .def(py::init<>())
        .def_readwrite("id",         &NetViaRow::id)
        .def_readwrite("from_seg",   &NetViaRow::from_seg)
        .def_readwrite("to_seg",     &NetViaRow::to_seg)
        .def_readwrite("bit_index",  &NetViaRow::bit_index)
        .def_readwrite("net_id",     &NetViaRow::net_id)
        .def_readwrite("net_name",   &NetViaRow::net_name)
        .def_readwrite("from_layer", &NetViaRow::from_layer)
        .def_readwrite("to_layer",   &NetViaRow::to_layer)
        .def_readwrite("x",          &NetViaRow::x)
        .def_readwrite("y",          &NetViaRow::y);

    py::class_<RouteSnapshotRow>(m, "RouteSnapshotRow")
        .def(py::init<>())
        .def_readwrite("hash",           &RouteSnapshotRow::hash)
        .def_readwrite("n_bus_segments", &RouteSnapshotRow::n_bus_segments)
        .def_readwrite("n_bus_vias",     &RouteSnapshotRow::n_bus_vias)
        .def_readwrite("stage",          &RouteSnapshotRow::stage)
        .def_readwrite("n_net_segments", &RouteSnapshotRow::n_net_segments)
        .def_readwrite("n_net_vias",     &RouteSnapshotRow::n_net_vias);

    py::class_<CellRow>(m, "CellRow")
        .def_readwrite("name",        &CellRow::name)
        .def_readwrite("width",       &CellRow::width)
        .def_readwrite("height",      &CellRow::height)
        .def_readwrite("bottom_up",   &CellRow::bottom_up)
        .def_readwrite("layer_cap",   &CellRow::layer_cap)
        .def_readwrite("layer_floor", &CellRow::layer_floor);

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
    py::class_<GdsImportStats>(m, "GdsImportStats")
        .def_readonly("n_structures", &GdsImportStats::n_structures)
        .def_readonly("n_cells",      &GdsImportStats::n_cells)
        .def_readonly("n_components", &GdsImportStats::n_components)
        .def_readonly("n_texts",      &GdsImportStats::n_texts)
        .def_readonly("n_nets",       &GdsImportStats::n_nets)
        .def_readonly("n_pins",       &GdsImportStats::n_pins)
        .def_readonly("n_labels_skipped", &GdsImportStats::n_labels_skipped)
        .def_readonly("n_routing_shapes", &GdsImportStats::n_routing_shapes)
        .def_readonly("tops",         &GdsImportStats::tops)
        .def_readonly("warnings",     &GdsImportStats::warnings);

    py::class_<GdsExportStats>(m, "GdsExportStats")
        .def_readonly("n_structures",  &GdsExportStats::n_structures)
        .def_readonly("n_placements",  &GdsExportStats::n_placements)
        .def_readonly("n_wire_shapes", &GdsExportStats::n_wire_shapes)
        .def_readonly("n_via_shapes",  &GdsExportStats::n_via_shapes)
        .def_readonly("n_labels",      &GdsExportStats::n_labels)
        .def_readonly("stage",         &GdsExportStats::stage)
        .def_readonly("warnings",      &GdsExportStats::warnings);

    py::class_<BDB>(m, "BDB")
        .def(py::init<const std::string&>())
        .def("import_def_lef",  &BDB::import_def_lef,
             py::arg("def_path"), py::arg("lef_path"))
        .def("import_verilog",  &BDB::import_verilog, py::arg("v_path"))
        .def("import_gds", [](BDB& db, const std::string& path,
                              const std::vector<int>& label_layers,
                              const std::vector<std::pair<int,int>>& routing_layers) {
                 return import_gds(db, path, label_layers, routing_layers);
             }, py::arg("path"),
             py::arg("label_layers") = std::vector<int>{},
             py::arg("routing_layers") = std::vector<std::pair<int,int>>{})
        .def("export_gds", [](BDB& db, const std::string& path,
                              const std::vector<std::tuple<int,int,int>>& layer_map,
                              int outline_layer, int label_layer,
                              bool write_labels, double via_size) {
                 return export_gds(db, path, layer_map, outline_layer,
                                   label_layer, write_labels, via_size);
             }, py::arg("path"),
             py::arg("layer_map") = std::vector<std::tuple<int,int,int>>{},
             py::arg("outline_layer") = 10,
             py::arg("label_layer") = 63,
             py::arg("write_labels") = true,
             py::arg("via_size") = 1.0)
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
        .def("add_bundle",      &BDB::add_bundle, py::arg("br"))
        .def("add_bundle_net",  &BDB::add_bundle_net,
             py::arg("bundle_id"), py::arg("net_name"))
        .def("add_bundle_busterm", &BDB::add_bundle_busterm,
             py::arg("bundle_id"), py::arg("busterm_id"), py::arg("role") = "")
        .def("clear_bundles",        &BDB::clear_bundles,
             py::arg("keep_user") = false)
        .def("bundle_nets",     &BDB::bundle_nets, py::arg("bundle_id"))
        .def("bundle_busterms", &BDB::bundle_busterms, py::arg("bundle_id"))
        .def("begin_batch",          &BDB::begin_batch)
        .def("commit_batch",         &BDB::commit_batch)
        .def("rollback_batch",       &BDB::rollback_batch)
        .def("add_topology",         &BDB::add_topology, py::arg("tr"))
        .def("add_topology_segment", &BDB::add_topology_segment, py::arg("sr"))
        .def("clear_topologies",     &BDB::clear_topologies,
             py::arg("keep_user") = false)
        .def("renumber_topology",    &BDB::renumber_topology,
             py::arg("bundle_id"), py::arg("old_ci"), py::arg("new_ci"))
        .def("delete_topology",      &BDB::delete_topology,
             py::arg("bundle_id"), py::arg("ci"))
        .def("set_bundle_gen_knobs", &BDB::set_bundle_gen_knobs,
             py::arg("bundle_id"), py::arg("knobs"))
        .def("bundle_gen_knobs",     &BDB::bundle_gen_knobs, py::arg("bundle_id"))
        .def("topologies",           &BDB::topologies, py::arg("bundle_id"))
        .def("topology_segments",    &BDB::topology_segments,
             py::arg("bundle_id"), py::arg("cand_index"))
        .def("add_topology_bridge",  &BDB::add_topology_bridge, py::arg("r"))
        .def("add_topology_seg_conn", &BDB::add_topology_seg_conn, py::arg("r"))
        .def("topology_seg_conns",   &BDB::topology_seg_conns,
             py::arg("bundle_id"), py::arg("cand_index"))
        .def("topology_bridges",     &BDB::topology_bridges,
             py::arg("bundle_id"), py::arg("cand_index"))
        .def("all_topology_bridges", &BDB::all_topology_bridges,
             py::arg("bundle_id"))
        .def("set_topology_selected", &BDB::set_topology_selected,
             py::arg("bundle_id"), py::arg("cand_index"))
        .def("set_segment_layer",     &BDB::set_segment_layer,
             py::arg("bundle_id"), py::arg("cand_index"),
             py::arg("seg_index"), py::arg("layer"))
        .def("reset_assigned_layers", &BDB::reset_assigned_layers,
             py::arg("bundle_id"))
        .def("clear_expanded_bundles", &BDB::clear_expanded_bundles)
        .def("add_bus_segment",   &BDB::add_bus_segment, py::arg("r"))
        .def("add_bus_via",       &BDB::add_bus_via, py::arg("r"))
        .def("clear_bus_routing", &BDB::clear_bus_routing)
        .def("bus_segments",      &BDB::bus_segments, py::arg("bundle_id"))
        .def("bus_vias",          &BDB::bus_vias, py::arg("bundle_id"))
        .def("add_net_segment",   &BDB::add_net_segment, py::arg("r"))
        .def("add_net_via",       &BDB::add_net_via, py::arg("r"))
        .def("clear_detailed_routing", &BDB::clear_detailed_routing)
        .def("net_segments",      &BDB::net_segments, py::arg("bundle_id"))
        .def("net_vias",          &BDB::net_vias, py::arg("bundle_id"))
        .def("set_route_snapshot", &BDB::set_route_snapshot,
             py::arg("hash"), py::arg("n_bus_segments"),
             py::arg("n_bus_vias"), py::arg("stage"),
             py::arg("n_net_segments") = 0, py::arg("n_net_vias") = 0)
        .def("route_snapshot",    &BDB::route_snapshot)
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
        .def("schema_version",  &BDB::schema_version)
        .def("meta_get",        &BDB::meta_get,
             py::arg("key"), py::arg("def") = std::string())
        .def("meta_set",        &BDB::meta_set,
             py::arg("key"), py::arg("value"))
        .def("save_copy",       &BDB::save_copy, py::arg("dest_path"))
        .def_readonly_static("SCHEMA_VERSION", &BDB::SCHEMA_VERSION)
        .def("die_w",           &BDB::die_w)
        .def("die_h",           &BDB::die_h)
        .def("set_die",         &BDB::set_die, py::arg("w"), py::arg("h"))
        .def_static("db_path",  &BDB::db_path, py::arg("def_path"))
        .def("move_comp",       &BDB::move_comp,
             py::arg("name"), py::arg("x"), py::arg("y"))
        .def("translate_comp",  &BDB::translate_comp,
             py::arg("name"), py::arg("dx"), py::arg("dy"))
        .def("set_comp_is_leaf", &BDB::set_comp_is_leaf,
             py::arg("name"), py::arg("is_leaf"))
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
             py::arg("is_leaf") = true, py::arg("orient") = "N")
        .def("add_cell",        &BDB::add_cell,
             py::arg("name"), py::arg("w"), py::arg("h"))
        .def("all_cells",       &BDB::all_cells)
        .def("set_cell_bottom_up", &BDB::set_cell_bottom_up,
             py::arg("cell"), py::arg("on"))
        .def("cell_bottom_up",  &BDB::cell_bottom_up, py::arg("cell"))
        .def("bottom_up_cells", &BDB::bottom_up_cells)
        .def("set_cell_layer_band", &BDB::set_cell_layer_band,
             py::arg("cell"), py::arg("floor"), py::arg("cap"))
        .def("cell_layer_band", &BDB::cell_layer_band, py::arg("cell"))
        .def("layer_capped_cells", &BDB::layer_capped_cells)
        .def("set_cell_layer_share", &BDB::set_cell_layer_share,
             py::arg("cell"), py::arg("layer_id"), py::arg("share"))
        .def("cell_layer_shares", &BDB::cell_layer_shares, py::arg("cell"))
        .def("layer_share_cells", &BDB::layer_share_cells)
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
