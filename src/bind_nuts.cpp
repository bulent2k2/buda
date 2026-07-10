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

// bind_nuts.cpp — Python bindings for NUTS, detailed NUTS, routing grid,
//                 connectivity topology, and verify functions.
// bind_routing(m) must be called before this (Floorplan/LayerStack are referenced).
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/stl_bind.h>
#include "conn_topology.h"
#include "nuts.h"
#include "routing_grid.h"
#include "detailed_nuts.h"
#include "verify.h"

namespace py = pybind11;
using namespace buda;

// Must be before <pybind11/stl.h> auto-conversion kicks in for this type.
// Declared here because only this TU binds BusSegment (which holds connections).
PYBIND11_MAKE_OPAQUE(std::vector<BusSegmentConn>);

void bind_nuts(py::module_& m) {
    py::bind_vector<std::vector<BusSegmentConn>>(m, "BusSegmentConnList");

    // ── ConnTopology ──────────────────────────────────────────────────────
    py::class_<SegConn>(m, "SegConn")
        .def_readwrite("kind",        &SegConn::kind)
        .def_readwrite("block_name",  &SegConn::block_name)
        .def_readwrite("face_coord",  &SegConn::face_coord)
        .def_readwrite("seg_idx",     &SegConn::seg_idx)
        .def_readwrite("at_pos",      &SegConn::at_pos)
        .def_readwrite("is_endpoint", &SegConn::is_endpoint);

    py::enum_<SegConn::Kind>(m, "SegConnKind")
        .value("BUSTERM", SegConn::BUSTERM)
        .value("SEG",     SegConn::SEG);

    py::class_<ConnSeg>(m, "ConnSeg")
        .def_readwrite("horiz",    &ConnSeg::horiz)
        .def_readwrite("layer_id", &ConnSeg::layer_id)
        .def_readwrite("along_lo", &ConnSeg::along_lo)
        .def_readwrite("along_hi", &ConnSeg::along_hi)
        .def_readwrite("perp_pos", &ConnSeg::perp_pos)
        .def_readwrite("perp_lo",  &ConnSeg::perp_lo)
        .def_readwrite("perp_hi",  &ConnSeg::perp_hi)
        .def_readwrite("net_pull", &ConnSeg::net_pull)
        .def_readwrite("along_flex_lo",  &ConnSeg::along_flex_lo)
        .def_readwrite("along_flex_hi",  &ConnSeg::along_flex_hi)
        .def_readwrite("along_cover_lo", &ConnSeg::along_cover_lo)
        .def_readwrite("along_cover_hi", &ConnSeg::along_cover_hi)
        .def_readwrite("along_pull",     &ConnSeg::along_pull)
        .def_readwrite("conns",    &ConnSeg::conns);

    py::class_<MSTEdge>(m, "MSTEdge")
        .def_readwrite("u",      &MSTEdge::u)
        .def_readwrite("v",      &MSTEdge::v)
        .def_readwrite("dist",   &MSTEdge::dist)
        .def_readwrite("u_name", &MSTEdge::u_name)
        .def_readwrite("v_name", &MSTEdge::v_name);

    py::class_<ConnTopology>(m, "ConnTopology")
        .def(py::init<>())
        .def("build",     &ConnTopology::build)
        .def("segs",      &ConnTopology::segs)
        .def("trunk_mst", &ConnTopology::trunk_mst);

    m.def("manhattan_nearest", &manhattan_nearest);
    m.def("seg_bbox",          &seg_bbox);
    m.def("compute_mst",       &compute_mst);

    // ── Abstract NUTS ─────────────────────────────────────────────────────
    // Placed-segment hierarchy (Phase G, docs/internal/placed_segment_preroutes.md).
    // Additive: SegKind + read-only `kind` tags + the new PreRoutedSegment type;
    // every pre-existing binding below is unchanged.
    py::enum_<SegKind>(m, "SegKind")
        .value("BUS",      SegKind::BUS)
        .value("NET",      SegKind::NET)
        .value("PREROUTE", SegKind::PREROUTE);

    py::class_<PreRoutedSegment>(m, "PreRoutedSegment")
        .def(py::init<>())
        .def_readonly ("kind",           &PreRoutedSegment::kind)
        .def_readwrite("layer",          &PreRoutedSegment::layer)
        .def_readwrite("span_lo",        &PreRoutedSegment::span_lo)
        .def_readwrite("span_hi",        &PreRoutedSegment::span_hi)
        .def_readwrite("track_position", &PreRoutedSegment::track_position)
        .def_readwrite("width",          &PreRoutedSegment::width)
        .def_readwrite("placed",         &PreRoutedSegment::placed)
        .def_readwrite("label",          &PreRoutedSegment::label)
        .def_readwrite("slot_type",      &PreRoutedSegment::slot_type)
        .def_readwrite("track_index",    &PreRoutedSegment::track_index);

    py::class_<TrackSegment>(m, "TrackSegment")
        .def(py::init<>())
        .def_readonly ("kind",      &TrackSegment::kind)
        .def_readwrite("bundle_id",      &TrackSegment::bundle_id)
        .def_readwrite("seg_idx",        &TrackSegment::seg_idx)
        .def_readwrite("layer",          &TrackSegment::layer)
        .def_readwrite("horiz",          &TrackSegment::horiz)
        .def_readwrite("span_lo",        &TrackSegment::span_lo)
        .def_readwrite("span_hi",        &TrackSegment::span_hi)
        .def_readwrite("interval_lo",    &TrackSegment::interval_lo)
        .def_readwrite("interval_hi",    &TrackSegment::interval_hi)
        .def_readwrite("width",          &TrackSegment::width)
        .def_readwrite("track_position", &TrackSegment::track_position)
        .def_readwrite("placed",         &TrackSegment::placed)
        .def_readwrite("net_pull",       &TrackSegment::net_pull)
        .def_readwrite("pull_target",    &TrackSegment::pull_target)
        .def_readwrite("is_jog",         &TrackSegment::is_jog)
        .def_readwrite("track_lo_bound", &TrackSegment::track_lo_bound)
        .def_readwrite("track_hi_bound", &TrackSegment::track_hi_bound);

    py::class_<OverlapDetail>(m, "OverlapDetail")
        .def_readwrite("layer",   &OverlapDetail::layer)
        .def_readwrite("bid_a",   &OverlapDetail::bid_a)
        .def_readwrite("seg_a",   &OverlapDetail::seg_a)
        .def_readwrite("bid_b",   &OverlapDetail::bid_b)
        .def_readwrite("seg_b",   &OverlapDetail::seg_b)
        .def_readwrite("span_lo", &OverlapDetail::span_lo)
        .def_readwrite("span_hi", &OverlapDetail::span_hi)
        .def_readwrite("perp_lo", &OverlapDetail::perp_lo)
        .def_readwrite("perp_hi", &OverlapDetail::perp_hi);

    py::class_<JunctionInfeasibility>(m, "JunctionInfeasibility")
        .def_readwrite("bundle_id", &JunctionInfeasibility::bundle_id)
        .def_readwrite("seg_a",     &JunctionInfeasibility::seg_a)
        .def_readwrite("seg_b",     &JunctionInfeasibility::seg_b);

    py::class_<NUTSResult>(m, "NUTSResult")
        .def(py::init<>())
        .def_readwrite("segments",           &NUTSResult::segments)
        .def_readwrite("overlap_details",    &NUTSResult::overlap_details)
        .def_readwrite("junction_infeasibilities",
                       &NUTSResult::junction_infeasibilities)
        .def_readwrite("num_violations",     &NUTSResult::num_violations)
        .def_readwrite("num_keepout_conflicts", &NUTSResult::num_keepout_conflicts)
        .def_readwrite("num_overlaps",       &NUTSResult::num_overlaps)
        .def_readwrite("overlaps_per_layer", &NUTSResult::overlaps_per_layer)
        .def_readwrite("dogleg_topologies",   &NUTSResult::dogleg_topologies)
        .def_readwrite("dogleg_seg_layers",   &NUTSResult::dogleg_seg_layers)
        .def_readwrite("dogleg_seg_net_pull", &NUTSResult::dogleg_seg_net_pull)
        .def_readwrite("dogleg_seg_perp",     &NUTSResult::dogleg_seg_perp)
        .def_readwrite("dogleg_seg_slide_lo", &NUTSResult::dogleg_seg_slide_lo)
        .def_readwrite("dogleg_seg_slide_hi", &NUTSResult::dogleg_seg_slide_hi);

    py::class_<NUTSEngine>(m, "NUTSEngine")
        .def(py::init<const Floorplan&, const LayerStack&>())
        .def("set_track_pitch",       &NUTSEngine::set_track_pitch)
        .def("set_extra_grid_points", &NUTSEngine::set_extra_grid_points)
        .def("run",                   &NUTSEngine::run)
        .def("rerun_layer",           &NUTSEngine::rerun_layer);

    // ── Routing grid + detailed NUTS ──────────────────────────────────────
    py::class_<TrackSlot>(m, "TrackSlot")
        .def(py::init([](const std::string& type, const std::string& label,
                         double width, double space_after) {
            TrackSlot s; s.type = type; s.label = label;
            s.width = width; s.space_after = space_after;
            return s;
        }), py::arg("type"), py::arg("label"),
            py::arg("width"), py::arg("space_after"))
        .def_readwrite("type",        &TrackSlot::type)
        .def_readwrite("label",       &TrackSlot::label)
        .def_readwrite("width",       &TrackSlot::width)
        .def_readwrite("space_after", &TrackSlot::space_after);

    py::class_<TrackPattern>(m, "TrackPattern")
        .def(py::init([](double origin, const std::vector<TrackSlot>& slots) {
            TrackPattern p; p.origin = origin; p.slots = slots; return p;
        }), py::arg("origin"), py::arg("slots"))
        .def_readwrite("origin",      &TrackPattern::origin)
        .def_readwrite("slots",       &TrackPattern::slots)
        .def("unit_pitch",            &TrackPattern::unit_pitch)
        .def("signal_density",        &TrackPattern::signal_density)
        .def("dilution_factor",       &TrackPattern::dilution_factor)
        .def("tracks_in_range",       &TrackPattern::tracks_in_range,
             py::arg("lo"), py::arg("hi"));

    py::class_<RoutingGrid>(m, "RoutingGrid")
        .def("effective_pattern_at", [](const RoutingGrid& g, double x, double y) {
            return g.effective_pattern_at(x, y);
        }, py::arg("x"), py::arg("y"))
        .def("signal_tracks_in",      &RoutingGrid::signal_tracks_in,
             py::arg("x"), py::arg("lo"), py::arg("hi"))
        .def("count_signal_tracks_in", &RoutingGrid::count_signal_tracks_in,
             py::arg("x"), py::arg("lo"), py::arg("hi"));

    py::class_<RoutingGridStack>(m, "RoutingGridStack")
        .def(py::init<>())
        .def("define_layer",  &RoutingGridStack::define_layer,
             py::arg("layer_id"), py::arg("pattern"), py::arg("is_horizontal"))
        .def("add_override",  &RoutingGridStack::add_override,
             py::arg("layer_id"),
             py::arg("x1"), py::arg("y1"), py::arg("x2"), py::arg("y2"),
             py::arg("pattern"))
        .def("add_keepout",   &RoutingGridStack::add_keepout,
             py::arg("layer_id"), py::arg("x1"), py::arg("y1"), py::arg("x2"), py::arg("y2"))
        .def("get_layer_grid", [](RoutingGridStack& s, int id) -> RoutingGrid& {
            return s.get_layer_grid(id);
        }, py::arg("layer_id"), py::return_value_policy::reference_internal)
        .def("preroutes",      &RoutingGridStack::preroutes,
             py::arg("layer_id"), py::arg("perp_lo"), py::arg("perp_hi"),
             py::arg("along_lo"), py::arg("along_hi"),
             py::arg("include_signal") = false,
             "Non-SIGNAL track slots of one layer as PreRoutedSegments "
             "(global pattern split at override shadows + overrides "
             "clipped to their regions); include_signal adds the SIGNAL "
             "slots for track-rail display")
        .def("has_layer",      &RoutingGridStack::has_layer, py::arg("layer_id"));

    py::class_<BusSegmentConn>(m, "BusSegmentConn")
        .def(py::init<>())
        .def_readwrite("seg_idx",     &BusSegmentConn::seg_idx)
        .def_readwrite("at_pos",      &BusSegmentConn::at_pos)
        .def_readwrite("is_endpoint", &BusSegmentConn::is_endpoint)
        .def_readwrite("lo_end",      &BusSegmentConn::lo_end);

    py::class_<BusSegment>(m, "BusSegment")
        .def(py::init<>())
        .def_readwrite("bundle_id",       &BusSegment::bundle_id)
        .def_readwrite("seg_idx",         &BusSegment::seg_idx)
        .def_readwrite("layer",           &BusSegment::layer)
        .def_readwrite("span_lo",         &BusSegment::span_lo)
        .def_readwrite("span_hi",         &BusSegment::span_hi)
        .def_readwrite("interval_lo",     &BusSegment::interval_lo)
        .def_readwrite("interval_hi",     &BusSegment::interval_hi)
        .def_readwrite("bit_width",       &BusSegment::bit_width)
        .def_readwrite("bit_order",       &BusSegment::bit_order)
        .def_readwrite("timing_critical", &BusSegment::timing_critical)
        .def_readwrite("connections",     &BusSegment::connections)
        .def_readwrite("busterm_faces",   &BusSegment::busterm_faces)
        .def_readwrite("abstract_pos",    &BusSegment::abstract_pos)
        .def_readwrite("track_lo_bound",  &BusSegment::track_lo_bound)
        .def_readwrite("track_hi_bound",  &BusSegment::track_hi_bound);

    py::class_<NetSegment>(m, "NetSegment")
        .def(py::init<>())
        .def_readonly ("kind",      &NetSegment::kind)
        .def_readwrite("bundle_id",      &NetSegment::bundle_id)
        .def_readwrite("seg_idx",        &NetSegment::seg_idx)
        .def_readwrite("bit_index",      &NetSegment::bit_index)
        .def_readwrite("track_position", &NetSegment::track_position)
        .def_readwrite("width",          &NetSegment::width)
        .def_readwrite("layer",          &NetSegment::layer)
        .def_readwrite("span_lo",        &NetSegment::span_lo)
        .def_readwrite("span_hi",        &NetSegment::span_hi);

    py::class_<NetVia>(m, "NetVia")
        .def(py::init<>())
        .def_readwrite("bundle_id",  &NetVia::bundle_id)
        .def_readwrite("from_seg",   &NetVia::from_seg)
        .def_readwrite("to_seg",     &NetVia::to_seg)
        .def_readwrite("bit_index",  &NetVia::bit_index)
        .def_readwrite("from_layer", &NetVia::from_layer)
        .def_readwrite("to_layer",   &NetVia::to_layer)
        .def_readwrite("x",          &NetVia::x)
        .def_readwrite("y",          &NetVia::y);

    py::class_<DetailedNUTSResult>(m, "DetailedNUTSResult")
        .def(py::init<>())
        .def_readwrite("net_segments", &DetailedNUTSResult::net_segments)
        .def_readwrite("net_vias",     &DetailedNUTSResult::net_vias)
        .def_readwrite("num_unplaced", &DetailedNUTSResult::num_unplaced)
        .def_readwrite("num_keepout_bits", &DetailedNUTSResult::num_keepout_bits);

    py::class_<DetailedNUTSEngine>(m, "DetailedNUTSEngine")
        .def(py::init<const RoutingGridStack&>())
        .def("run", &DetailedNUTSEngine::run, py::arg("bus_segments"));

    // Stage-4 -> stage-9 handoff, single-sourced in C++: every TrackSegment
    // of the NUTS result becomes a BusSegment (track_position -> abstract_pos,
    // corner bounds carried, bit_width = the bundle's net count) with its SEG
    // connections + BUSTERM faces derived from the selected topology's cached
    // analysis — the derivation the abstract solve placed with, existing once.
    m.def("make_bus_segments", &make_bus_segments,
          py::arg("bundles"), py::arg("nuts_result"), py::arg("floorplan"),
          py::arg("bit_order") = "LO_HI",
          "Build DetailedNUTSEngine.run() input from the placed NUTS result");

    // ── Verify ────────────────────────────────────────────────────────────
    py::enum_<ViolationKind>(m, "ViolationKind")
        .value("SEG_OPEN",     ViolationKind::SEG_OPEN)
        .value("BUSTERM_OPEN", ViolationKind::BUSTERM_OPEN)
        .value("BUSTERM_FACE", ViolationKind::BUSTERM_FACE)
        .value("UNPLACED",     ViolationKind::UNPLACED)
        .value("LAYER_DIR",    ViolationKind::LAYER_DIR)
        .value("FEEDTHRU_RELAY", ViolationKind::FEEDTHRU_RELAY)
        .value("KEEPOUT_CROSS", ViolationKind::KEEPOUT_CROSS);

    py::class_<ConnViolation>(m, "ConnViolation")
        .def_readwrite("kind",       &ConnViolation::kind)
        .def_readwrite("bundle_id",  &ConnViolation::bundle_id)
        .def_readwrite("seg_idx",    &ConnViolation::seg_idx)
        .def_readwrite("seg_idx2",   &ConnViolation::seg_idx2)
        .def_readwrite("bit_index",  &ConnViolation::bit_index)
        .def_readwrite("block_name", &ConnViolation::block_name)
        .def_readwrite("message",    &ConnViolation::message);

    py::class_<ConnResult>(m, "ConnResult")
        .def_readwrite("violations", &ConnResult::violations)
        .def("ok",                   &ConnResult::ok);

    m.def("check_topo",  &check_topo,
          py::arg("ct"), py::arg("topo"), py::arg("fp"), py::arg("bundle_id"));
    // zone_fp: floorplan whose keepout zones the engine placed against, for
    // the KEEPOUT_CROSS audit — None = fp (see verify.h).
    m.def("check_nuts",  &check_nuts,
          py::arg("ct"), py::arg("nuts"), py::arg("topo"), py::arg("fp"),
          py::arg("layers"), py::arg("bundle_id"),
          py::arg("zone_fp") = nullptr);
    m.def("check_dnuts", &check_dnuts,
          py::arg("ct"), py::arg("dnuts"), py::arg("topo"), py::arg("fp"),
          py::arg("layers"), py::arg("bundle_id"), py::arg("num_bits"),
          py::arg("zone_fp") = nullptr);
}
