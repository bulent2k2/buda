#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/stl_bind.h>
#include <pybind11/iostream.h>
#include "bdb.h"
#include "bundler.h"
#include "topology.h"
#include "conn_topology.h"
#include "layering.h"
#include "congestion_planner.h"
#include "nuts.h"
#include "routing_grid.h"
#include "detailed_nuts.h"
#include "verify.h"

namespace py = pybind11;
using namespace buda;

PYBIND11_MAKE_OPAQUE(std::vector<BusSegmentConn>);

PYBIND11_MODULE(buda, m) {
    py::bind_vector<std::vector<BusSegmentConn>>(m, "BusSegmentConnList");
    py::add_ostream_redirect(m, "ostream_redirect");

    py::enum_<Strategy>(m, "Strategy")
        .value("STRICT",     Strategy::STRICT)
        .value("CONVERGENT", Strategy::CONVERGENT);

    py::enum_<LayerDir>(m, "LayerDir")
        .value("HORIZONTAL", LayerDir::HORIZONTAL)
        .value("VERTICAL",   LayerDir::VERTICAL);

    py::enum_<LayerType>(m, "LayerType")
        .value("TOP", LayerType::TOP)
        .value("LOW", LayerType::LOW);

    py::enum_<TegMode>(m, "TegMode")
        .value("THRU", TegMode::THRU)
        .value("OVER", TegMode::OVER);

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
        .def_readwrite("type",              &Topology::type)
        .def_readwrite("segments",          &Topology::segments)
        .def_readwrite("estimated_wirelength", &Topology::estimated_wirelength)
        .def_readwrite("trunk_location",    &Topology::trunk_location)
        .def_readwrite("pass_through_count", &Topology::pass_through_count)
        .def_readwrite("seg_busterms",           &Topology::seg_busterms)
        .def_readwrite("bridge_segments",        &Topology::bridge_segments)
        .def_readwrite("connected_block_names",  &Topology::connected_block_names);

    py::class_<Bundle>(m, "Bundle")
        .def(py::init<>())
        .def_readwrite("id",            &Bundle::id)
        .def_readwrite("net_names",     &Bundle::net_names)
        .def_readwrite("reason",        &Bundle::reason)
        .def_readwrite("num_terminals", &Bundle::num_terminals)
        .def("get_net_names",           &Bundle::get_net_names);

    py::class_<BundleAssignment>(m, "BundleAssignment")
        .def_readwrite("bundle_id",  &BundleAssignment::bundle_id)
        .def_readwrite("topo_index", &BundleAssignment::topo_index)
        .def_readwrite("v_layer_id", &BundleAssignment::v_layer_id)
        .def_readwrite("h_layer_id", &BundleAssignment::h_layer_id)
        .def_readwrite("seg_layers", &BundleAssignment::seg_layers);

    py::class_<BundleWrapper>(m, "BundleWrapper")
        .def(py::init<>())
        .def_readwrite("original_bundle",         &BundleWrapper::original_bundle)
        .def_readwrite("candidates",              &BundleWrapper::candidates)
        .def_readwrite("selected_topology_index", &BundleWrapper::selected_topology_index)
        .def_readwrite("width",                   &BundleWrapper::width)
        .def_readwrite("seg_layers",              &BundleWrapper::seg_layers)
        .def_readwrite("pinned_seg_layers",       &BundleWrapper::pinned_seg_layers)
        .def_readwrite("assigned_v_layer",        &BundleWrapper::assigned_v_layer)
        .def_readwrite("assigned_h_layer",        &BundleWrapper::assigned_h_layer)
        .def_readwrite("topology_pinned",         &BundleWrapper::topology_pinned);

    py::class_<Netlist>(m, "Netlist")
        .def(py::init<>())
        .def("add_net", &Netlist::add_net);

    py::class_<Bundler>(m, "Bundler")
        .def(py::init<>())
        .def("set_strategy", &Bundler::set_strategy)
        .def("run",          &Bundler::run);

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
        .def("set_block_teg_mode", &Floorplan::set_block_teg_mode)
        .def("get_block_teg_mode", &Floorplan::get_block_teg_mode)
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
        .def("add_layer",               &LayerStack::add_layer)
        .def("set_layer_dilution",      &LayerStack::set_layer_dilution)
        .def("set_layer_overhead",      &LayerStack::set_layer_overhead)
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
        .def("generate_candidates", [](TopologyGenerator& self,
                                       const std::string& src,
                                       py::object dsts) {
            if (py::isinstance<py::str>(dsts))
                return self.generate_candidates(src, {dsts.cast<std::string>()});
            return self.generate_candidates(src, dsts.cast<std::vector<std::string>>());
        });

    py::class_<GlobalCut>(m, "GlobalCut")
        .def(py::init<>())
        .def_readwrite("p1",         &GlobalCut::p1)
        .def_readwrite("p2",         &GlobalCut::p2)
        .def_readwrite("cut_coord",  &GlobalCut::cut_coord)
        .def_readwrite("dir",        &GlobalCut::dir)
        .def_readwrite("layer_id",   &GlobalCut::layer_id)
        .def_readwrite("band_cap",   &GlobalCut::band_cap)
        .def_readwrite("band_usage", &GlobalCut::band_usage);

    py::class_<CongestionPlanner>(m, "CongestionPlanner")
        .def(py::init<const Floorplan&, const LayerStack&>())
        .def("set_planner_param",   &CongestionPlanner::set_planner_param)
        .def("build_congestion_map",&CongestionPlanner::build_congestion_map)
        .def("optimize_topologies", &CongestionPlanner::optimize_topologies)
        .def("get_cuts",            &CongestionPlanner::get_cuts)
        .def("get_x_grid",          &CongestionPlanner::get_x_grid)
        .def("get_y_grid",          &CongestionPlanner::get_y_grid);

    py::class_<TrackSegment>(m, "TrackSegment")
        .def(py::init<>())
        .def_readwrite("bundle_id",     &TrackSegment::bundle_id)
        .def_readwrite("seg_idx",       &TrackSegment::seg_idx)
        .def_readwrite("layer",         &TrackSegment::layer)
        .def_readwrite("horiz",         &TrackSegment::horiz)
        .def_readwrite("span_lo",       &TrackSegment::span_lo)
        .def_readwrite("span_hi",       &TrackSegment::span_hi)
        .def_readwrite("interval_lo",   &TrackSegment::interval_lo)
        .def_readwrite("interval_hi",   &TrackSegment::interval_hi)
        .def_readwrite("width",         &TrackSegment::width)
        .def_readwrite("track_position", &TrackSegment::track_position)
        .def_readwrite("placed",        &TrackSegment::placed)
        .def_readwrite("net_pull",      &TrackSegment::net_pull);

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
        .def(py::init<const Floorplan&, const LayerStack&>())
        .def("set_track_pitch",       &NUTSEngine::set_track_pitch)
        .def("set_extra_grid_points", &NUTSEngine::set_extra_grid_points)
        .def("run",                   &NUTSEngine::run)
        .def("rerun_layer",           &NUTSEngine::rerun_layer);

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
            return g.effective_pattern_at(x, y);
        }, py::arg("x"), py::arg("y"))
        .def("signal_tracks_in", &RoutingGrid::signal_tracks_in,
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

    // ── BDB ────────────────────────────────────────────────────────────────
    py::class_<BDB>(m, "BDB")
        .def(py::init<const std::string&>())
        .def("import_def_lef",  &BDB::import_def_lef,
             py::arg("def_path"), py::arg("lef_path"))
        .def("import_verilog",  &BDB::import_verilog, py::arg("v_path"))
        .def("compute_hpwl",    &BDB::compute_hpwl)
        .def("compute_fanout",  &BDB::compute_fanout)
        .def("compute_all",     &BDB::compute_all)
        .def("all_components",  &BDB::all_components)
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
        .def_static("db_path",  &BDB::db_path, py::arg("def_path"))
        .def("move_comp",   &BDB::move_comp,
             py::arg("name"), py::arg("x"), py::arg("y"))
        .def("resize_cell", &BDB::resize_cell,
             py::arg("cell"), py::arg("w"), py::arg("h"))
        .def("add_comp",    &BDB::add_comp,
             py::arg("name"), py::arg("cell"), py::arg("parent_name"),
             py::arg("x1"), py::arg("y1"), py::arg("x2"), py::arg("y2"),
             py::arg("is_leaf") = true)
        .def("add_cell",    &BDB::add_cell,
             py::arg("name"), py::arg("w"), py::arg("h"))
        .def("all_cells",   &BDB::all_cells)
        .def("add_inst",    &BDB::add_inst,
             py::arg("inst_name"), py::arg("cell_name"), py::arg("parent_name"),
             py::arg("x"), py::arg("y"));

    // ── verify ─────────────────────────────────────────────────────────────

    py::enum_<ViolationKind>(m, "ViolationKind")
        .value("SEG_OPEN",     ViolationKind::SEG_OPEN)
        .value("BUSTERM_OPEN", ViolationKind::BUSTERM_OPEN)
        .value("BUSTERM_FACE", ViolationKind::BUSTERM_FACE)
        .value("UNPLACED",     ViolationKind::UNPLACED);

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
    m.def("check_nuts",  &check_nuts,
          py::arg("ct"), py::arg("nuts"), py::arg("topo"), py::arg("fp"),
          py::arg("bundle_id"));
    m.def("check_dnuts", &check_dnuts,
          py::arg("ct"), py::arg("dnuts"), py::arg("topo"), py::arg("fp"),
          py::arg("bundle_id"), py::arg("num_bits"));
}
