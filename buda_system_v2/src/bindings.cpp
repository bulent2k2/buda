#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/iostream.h>
#include "bundler.h"
#include "topology.h"
#include "conn_topology.h"
#include "layering.h"
#include "global_router.h"
#include "nuts.h"
#include "routing_grid.h"
#include "detailed_nuts.h"
namespace py = pybind11;
using namespace interconnect;
PYBIND11_MODULE(interconnect, m) {
    py::add_ostream_redirect(m, "ostream_redirect");
    py::enum_<Strategy>(m, "Strategy")
        .value("STRICT",     Strategy::STRICT)
        .value("CONVERGENT", Strategy::CONVERGENT);
    py::enum_<LayerDir>(m, "LayerDir").value("HORIZONTAL", LayerDir::HORIZONTAL).value("VERTICAL", LayerDir::VERTICAL);
    py::enum_<LayerType>(m, "LayerType").value("TOP", LayerType::TOP).value("LOW", LayerType::LOW);
    py::class_<Point>(m, "Point").def(py::init<int, int>()).def_readwrite("x", &Point::x).def_readwrite("y", &Point::y);
    py::class_<Rect>(m, "Rect").def_readwrite("x1", &Rect::x1).def_readwrite("y1", &Rect::y1).def_readwrite("x2", &Rect::x2).def_readwrite("y2", &Rect::y2);
    py::class_<Segment>(m, "Segment").def(py::init<>()).def_readwrite("start", &Segment::start).def_readwrite("end", &Segment::end).def_readwrite("layer_hint", &Segment::layer_hint);
    py::class_<Busterm>(m, "Busterm").def(py::init<>()).def_readwrite("block_name", &Busterm::block_name).def_readwrite("bbox", &Busterm::bbox);
    py::class_<Topology>(m, "Topology").def(py::init<>())
        .def_readwrite("type",              &Topology::type)
        .def_readwrite("segments",          &Topology::segments)
        .def_readwrite("estimated_wirelength", &Topology::estimated_wirelength)
        .def_readwrite("trunk_location",    &Topology::trunk_location)
        .def_readwrite("pass_through_count", &Topology::pass_through_count)
        .def_readwrite("seg_busterms",      &Topology::seg_busterms);
    py::class_<Bundle>(m, "Bundle").def(py::init<>()).def_readwrite("id", &Bundle::id).def_readwrite("net_names", &Bundle::net_names).def_readwrite("reason", &Bundle::reason).def("get_net_names", &Bundle::get_net_names);
    py::class_<BundleAssignment>(m, "BundleAssignment")
        .def_readwrite("bundle_id",  &BundleAssignment::bundle_id)
        .def_readwrite("topo_index", &BundleAssignment::topo_index)
        .def_readwrite("v_layer_id", &BundleAssignment::v_layer_id)
        .def_readwrite("h_layer_id", &BundleAssignment::h_layer_id)
        .def_readwrite("seg_layers", &BundleAssignment::seg_layers);
    py::class_<BundleWrapper>(m, "BundleWrapper").def(py::init<>())
        .def_readwrite("original_bundle",         &BundleWrapper::original_bundle)
        .def_readwrite("candidates",              &BundleWrapper::candidates)
        .def_readwrite("selected_topology_index", &BundleWrapper::selected_topology_index)
        .def_readwrite("width",                   &BundleWrapper::width)
        .def_readwrite("seg_layers",              &BundleWrapper::seg_layers)
        .def_readwrite("pinned_seg_layers",       &BundleWrapper::pinned_seg_layers)
        .def_readwrite("assigned_v_layer",        &BundleWrapper::assigned_v_layer)
        .def_readwrite("assigned_h_layer",        &BundleWrapper::assigned_h_layer)
        .def_readwrite("topology_pinned",         &BundleWrapper::topology_pinned);
    py::class_<Netlist>(m, "Netlist").def(py::init<>()).def("add_net", &Netlist::add_net);
    py::class_<Bundler>(m, "Bundler").def(py::init<>()).def("set_strategy", &Bundler::set_strategy).def("run", &Bundler::run);
    py::class_<BlockCornerMargin>(m, "BlockCornerMargin")
        .def(py::init<>())
        .def_readwrite("dx", &BlockCornerMargin::dx)
        .def_readwrite("dy", &BlockCornerMargin::dy);
    py::class_<Floorplan>(m, "Floorplan").def(py::init<>())
        .def("add_block",              &Floorplan::add_block)
        .def("set_block_corner_margin", &Floorplan::set_block_corner_margin)
        .def("set_global_corner_margin",&Floorplan::set_global_corner_margin)
        .def("set_min_stub_length",    &Floorplan::set_min_stub_length)
        .def("set_min_stub_length_dir", [](Floorplan& fp, LayerDir dir, int val) {
            fp.set_min_stub_length_dir(static_cast<int>(dir), val);
        })
        .def("set_min_stub_length_layer",&Floorplan::set_min_stub_length_layer)
        .def("get_min_stub_length", [](const Floorplan& fp, LayerDir dir, int layer_id) {
            return fp.get_min_stub_length(static_cast<int>(dir), layer_id);
        })
        .def("get_block_corner_margin", &Floorplan::get_block_corner_margin)
        .def("get_block_bounds",        &Floorplan::get_block_bounds)
        .def("get_hanan_grid", [](const Floorplan& fp) {
            std::vector<int> x, y; fp.get_hanan_grid(x, y); return std::make_pair(x, y);
        })
        .def("get_all_blocks", [](const Floorplan& fp) { return fp.get_all_blocks(); });
    py::class_<LayerStack>(m, "LayerStack").def(py::init<>())
        .def("add_layer",        &LayerStack::add_layer)
        .def("set_layer_span",   &LayerStack::set_layer_span)
        .def("set_layer_kspan",  &LayerStack::set_layer_kspan)
        .def("is_top",           &LayerStack::is_top)
        .def("get_layer_ids_by_dir",   &LayerStack::get_layer_ids_by_dir)
        .def("get_layer_ids_preferred",&LayerStack::get_layer_ids_preferred)
        .def("get_top_layer",    &LayerStack::get_top_layer)
        .def("get_layer_type",   &LayerStack::get_layer_type);
    py::class_<SegConn>(m, "SegConn")
        .def_readwrite("kind",       &SegConn::kind)
        .def_readwrite("block_name", &SegConn::block_name)
        .def_readwrite("face_coord", &SegConn::face_coord)
        .def_readwrite("seg_idx",    &SegConn::seg_idx)
        .def_readwrite("at_pos",     &SegConn::at_pos);
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
        .def_readwrite("conns",    &ConnSeg::conns);
    py::class_<MSTEdge>(m, "MSTEdge")
        .def_readwrite("u",      &MSTEdge::u)
        .def_readwrite("v",      &MSTEdge::v)
        .def_readwrite("dist",   &MSTEdge::dist)
        .def_readwrite("u_name", &MSTEdge::u_name)
        .def_readwrite("v_name", &MSTEdge::v_name);
    py::class_<ConnTopology>(m, "ConnTopology")
        .def(py::init<>())
        .def("build",      &ConnTopology::build)
        .def("segs",       &ConnTopology::segs)
        .def("trunk_mst",  &ConnTopology::trunk_mst);
    m.def("manhattan_nearest", &manhattan_nearest);
    m.def("seg_bbox",          &seg_bbox);
    m.def("compute_mst",       &compute_mst);
    py::class_<TopologyGenerator>(m, "TopologyGenerator")
        .def(py::init<const Floorplan&>())
        .def("set_busterm_mode",              &TopologyGenerator::set_busterm_mode)
        .def("set_double_detour",             &TopologyGenerator::set_double_detour)
        .def("set_layer_ids",                 &TopologyGenerator::set_layer_ids)
        .def("generate_candidates",           &TopologyGenerator::generate_candidates)
        .def("generate_multicast_candidates", &TopologyGenerator::generate_multicast_candidates);
    py::class_<GlobalCut>(m, "GlobalCut")
        .def(py::init<>())
        .def_readwrite("p1",         &GlobalCut::p1)
        .def_readwrite("p2",         &GlobalCut::p2)
        .def_readwrite("cut_coord",  &GlobalCut::cut_coord)
        .def_readwrite("dir",        &GlobalCut::dir)
        .def_readwrite("layer_id",   &GlobalCut::layer_id)
        .def_readwrite("band_cap",   &GlobalCut::band_cap)
        .def_readwrite("band_usage", &GlobalCut::band_usage);
    py::class_<GlobalRouter>(m, "GlobalRouter")
        .def(py::init<const Floorplan&, const LayerStack&>())
        .def("set_layer_overhead",  &GlobalRouter::set_layer_overhead)
        .def("set_planner_param",   &GlobalRouter::set_planner_param)
        .def("build_congestion_map",&GlobalRouter::build_congestion_map)
        .def("optimize_topologies", &GlobalRouter::optimize_topologies)
        .def("get_cuts",            &GlobalRouter::get_cuts)
        .def("get_x_grid",          &GlobalRouter::get_x_grid)
        .def("get_y_grid",          &GlobalRouter::get_y_grid);
    py::class_<TrackSegment>(m, "TrackSegment")
        .def(py::init<>())
        .def_readwrite("bundle_id",    &TrackSegment::bundle_id)
        .def_readwrite("seg_idx",      &TrackSegment::seg_idx)
        .def_readwrite("layer",        &TrackSegment::layer)
        .def_readwrite("horiz",        &TrackSegment::horiz)
        .def_readwrite("span_lo",      &TrackSegment::span_lo)
        .def_readwrite("span_hi",      &TrackSegment::span_hi)
        .def_readwrite("interval_lo",  &TrackSegment::interval_lo)
        .def_readwrite("interval_hi",  &TrackSegment::interval_hi)
        .def_readwrite("width",        &TrackSegment::width)
        .def_readwrite("track_position", &TrackSegment::track_position)
        .def_readwrite("placed",       &TrackSegment::placed);
    py::class_<OverlapDetail>(m, "OverlapDetail")
        .def_readwrite("layer",    &OverlapDetail::layer)
        .def_readwrite("bid_a",    &OverlapDetail::bid_a)
        .def_readwrite("seg_a",    &OverlapDetail::seg_a)
        .def_readwrite("bid_b",    &OverlapDetail::bid_b)
        .def_readwrite("seg_b",    &OverlapDetail::seg_b)
        .def_readwrite("span_lo",  &OverlapDetail::span_lo)
        .def_readwrite("span_hi",  &OverlapDetail::span_hi)
        .def_readwrite("perp_lo",  &OverlapDetail::perp_lo)
        .def_readwrite("perp_hi",  &OverlapDetail::perp_hi);
    py::class_<NUTSResult>(m, "NUTSResult")
        .def(py::init<>())
        .def_readwrite("segments",           &NUTSResult::segments)
        .def_readwrite("overlap_details",    &NUTSResult::overlap_details)
        .def_readwrite("num_violations",     &NUTSResult::num_violations)
        .def_readwrite("num_overlaps",       &NUTSResult::num_overlaps)
        .def_readwrite("overlaps_per_layer", &NUTSResult::overlaps_per_layer);
    py::class_<NUTSEngine>(m, "NUTSEngine")
        .def(py::init<const Floorplan&>())
        .def("set_track_pitch",       &NUTSEngine::set_track_pitch)
        .def("set_extra_grid_points", &NUTSEngine::set_extra_grid_points)
        .def("run",                   &NUTSEngine::run)
        .def("rerun_layer",           &NUTSEngine::rerun_layer);

    // -----------------------------------------------------------------------
    // Stage 8 — Routing Grid
    // -----------------------------------------------------------------------
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
        .def_readwrite("origin",         &TrackPattern::origin)
        .def_readwrite("slots",          &TrackPattern::slots)
        .def("unit_pitch",               &TrackPattern::unit_pitch)
        .def("signal_density",           &TrackPattern::signal_density)
        .def("dilution_factor",          &TrackPattern::dilution_factor)
        .def("tracks_in_range",          &TrackPattern::tracks_in_range,
             py::arg("lo"), py::arg("hi"));

    py::class_<RoutingGrid>(m, "RoutingGrid")
        .def("effective_pattern_at", [](const RoutingGrid& g, double x, double y) {
            return g.effective_pattern_at(x, y);  // returns by value (copy)
        }, py::arg("x"), py::arg("y"))
        .def("signal_tracks_in", &RoutingGrid::signal_tracks_in,
             py::arg("x"), py::arg("lo"), py::arg("hi"));

    py::class_<RoutingGridStack>(m, "RoutingGridStack")
        .def(py::init<>())
        .def("define_layer",  &RoutingGridStack::define_layer,
             py::arg("layer_id"), py::arg("pattern"))
        .def("add_override",  &RoutingGridStack::add_override,
             py::arg("layer_id"),
             py::arg("x1"), py::arg("y1"), py::arg("x2"), py::arg("y2"),
             py::arg("pattern"))
        .def("get_layer_grid", [](RoutingGridStack& s, int id) -> RoutingGrid& {
            return s.get_layer_grid(id);
        }, py::arg("layer_id"), py::return_value_policy::reference_internal)
        .def("has_layer",      &RoutingGridStack::has_layer, py::arg("layer_id"));

    // -----------------------------------------------------------------------
    // Stage 9 — Detailed NUTS
    // -----------------------------------------------------------------------
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
        .def_readwrite("lo_adj_seg_idx",  &BusSegment::lo_adj_seg_idx)
        .def_readwrite("hi_adj_seg_idx",  &BusSegment::hi_adj_seg_idx)
        .def_readwrite("abstract_pos",    &BusSegment::abstract_pos);

    py::class_<NetSegment>(m, "NetSegment")
        .def(py::init<>())
        .def_readwrite("bundle_id",      &NetSegment::bundle_id)
        .def_readwrite("seg_idx",        &NetSegment::seg_idx)
        .def_readwrite("bit_index",      &NetSegment::bit_index)
        .def_readwrite("track_position", &NetSegment::track_position)
        .def_readwrite("width",          &NetSegment::width)
        .def_readwrite("layer",          &NetSegment::layer)
        .def_readwrite("span_lo",        &NetSegment::span_lo)
        .def_readwrite("span_hi",        &NetSegment::span_hi);

    py::class_<DetailedNUTSResult>(m, "DetailedNUTSResult")
        .def(py::init<>())
        .def_readwrite("net_segments",  &DetailedNUTSResult::net_segments)
        .def_readwrite("num_unplaced",  &DetailedNUTSResult::num_unplaced);

    py::class_<DetailedNUTSEngine>(m, "DetailedNUTSEngine")
        .def(py::init<const RoutingGridStack&>())
        .def("run", &DetailedNUTSEngine::run, py::arg("bus_segments"));
}
