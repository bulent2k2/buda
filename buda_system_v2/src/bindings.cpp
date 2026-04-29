#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "bundler.h"
#include "topology.h"
#include "layering.h"
#include "global_router.h"
#include "nuts.h"
namespace py = pybind11;
using namespace interconnect;
PYBIND11_MODULE(interconnect, m) {
    py::enum_<Strategy>(m, "Strategy").value("STRICT", Strategy::STRICT);
    py::enum_<LayerDir>(m, "LayerDir").value("HORIZONTAL", LayerDir::HORIZONTAL).value("VERTICAL", LayerDir::VERTICAL);
    py::enum_<LayerType>(m, "LayerType").value("TOP", LayerType::TOP).value("LOW", LayerType::LOW);
    py::class_<Point>(m, "Point").def(py::init<int, int>()).def_readwrite("x", &Point::x).def_readwrite("y", &Point::y);
    py::class_<Rect>(m, "Rect").def_readwrite("x1", &Rect::x1).def_readwrite("y1", &Rect::y1).def_readwrite("x2", &Rect::x2).def_readwrite("y2", &Rect::y2);
    py::class_<Segment>(m, "Segment").def(py::init<>()).def_readwrite("start", &Segment::start).def_readwrite("end", &Segment::end).def_readwrite("layer_hint", &Segment::layer_hint);
    py::class_<Topology>(m, "Topology").def(py::init<>()).def_readwrite("type", &Topology::type).def_readwrite("segments", &Topology::segments);
    py::class_<Bundle>(m, "Bundle").def(py::init<>()).def_readwrite("id", &Bundle::id).def_readwrite("net_names", &Bundle::net_names).def("get_net_names", &Bundle::get_net_names);
    py::class_<BundleWrapper>(m, "BundleWrapper").def(py::init<>()).def_readwrite("original_bundle", &BundleWrapper::original_bundle).def_readwrite("candidates", &BundleWrapper::candidates).def_readwrite("selected_topology_index", &BundleWrapper::selected_topology_index).def_readwrite("width", &BundleWrapper::width);
    py::class_<Netlist>(m, "Netlist").def(py::init<>()).def("add_net", &Netlist::add_net);
    py::class_<Bundler>(m, "Bundler").def(py::init<>()).def("set_strategy", &Bundler::set_strategy).def("run", &Bundler::run);
    py::class_<Floorplan>(m, "Floorplan").def(py::init<>()).def("add_block", &Floorplan::add_block).def("get_hanan_grid", [](const Floorplan& fp) { std::vector<int> x, y; fp.get_hanan_grid(x, y); return std::make_pair(x, y); }).def("get_all_blocks", [](const Floorplan& fp) { return fp.get_all_blocks(); });
    py::class_<LayerStack>(m, "LayerStack").def(py::init<>()).def("add_layer", &LayerStack::add_layer);
    py::class_<TopologyGenerator>(m, "TopologyGenerator").def(py::init<const Floorplan&>()).def("generate_candidates", &TopologyGenerator::generate_candidates);
    py::class_<GlobalCut>(m, "GlobalCut")
        .def(py::init<>())
        .def_readwrite("p1",            &GlobalCut::p1)
        .def_readwrite("p2",            &GlobalCut::p2)
        .def_readwrite("capacity",      &GlobalCut::capacity)
        .def_readwrite("current_usage", &GlobalCut::current_usage);
    py::class_<GlobalRouter>(m, "GlobalRouter")
        .def(py::init<const Floorplan&, const LayerStack&>())
        .def("set_layer_overhead",  &GlobalRouter::set_layer_overhead)
        .def("build_congestion_map",&GlobalRouter::build_congestion_map)
        .def("optimize_topologies", &GlobalRouter::optimize_topologies)
        .def("get_cuts",            &GlobalRouter::get_cuts);
    py::class_<TrackSegment>(m, "TrackSegment")
        .def(py::init<>())
        .def_readwrite("bundle_id",    &TrackSegment::bundle_id)
        .def_readwrite("seg_idx",      &TrackSegment::seg_idx)
        .def_readwrite("layer",        &TrackSegment::layer)
        .def_readwrite("span_lo",      &TrackSegment::span_lo)
        .def_readwrite("span_hi",      &TrackSegment::span_hi)
        .def_readwrite("interval_lo",  &TrackSegment::interval_lo)
        .def_readwrite("interval_hi",  &TrackSegment::interval_hi)
        .def_readwrite("width",        &TrackSegment::width)
        .def_readwrite("track_position", &TrackSegment::track_position)
        .def_readwrite("placed",       &TrackSegment::placed);
    py::class_<NUTSResult>(m, "NUTSResult")
        .def(py::init<>())
        .def_readwrite("segments",       &NUTSResult::segments)
        .def_readwrite("num_violations", &NUTSResult::num_violations)
        .def_readwrite("num_overlaps",   &NUTSResult::num_overlaps);
    py::class_<NUTSEngine>(m, "NUTSEngine")
        .def(py::init<const Floorplan&>())
        .def("set_track_pitch", &NUTSEngine::set_track_pitch)
        .def("run",             &NUTSEngine::run);
}