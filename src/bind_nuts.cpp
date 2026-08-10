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
#include "bind_opaque.h"   // FIRST: opaque vector<BundleWrapper> (P2)

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/stl_bind.h>
#include "conn_topology.h"
#include "nuts.h"
#include "nuts_geom.h"
#include "routing_grid.h"
#include "detailed_nuts.h"
#include "verify.h"
#include "trial_sweep.h"

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
        .def_readwrite("pull_break", &ConnSeg::pull_break)
        .def_readwrite("pull_lo", &ConnSeg::pull_lo)
        .def_readwrite("pull_hi", &ConnSeg::pull_hi)
        .def_readwrite("pull_f_lo", &ConnSeg::pull_f_lo)
        .def_readwrite("pull_f_hi", &ConnSeg::pull_f_hi)
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
    m.def("compute_mst",
          static_cast<std::vector<MSTEdge>(*)(
              const std::vector<std::pair<std::string, Rect>>&)>(&compute_mst));

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

    py::class_<PassthruCrossing>(m, "PassthruCrossing")
        .def(py::init<>())
        .def_readwrite("along_lo", &PassthruCrossing::along_lo)
        .def_readwrite("along_hi", &PassthruCrossing::along_hi)
        .def_readwrite("perp_lo",  &PassthruCrossing::perp_lo)
        .def_readwrite("perp_hi",  &PassthruCrossing::perp_hi)
        .def_readwrite("block",    &PassthruCrossing::block)
        .def_readwrite("rect",     &PassthruCrossing::rect);

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
        .def_readwrite("track_hi_bound", &TrackSegment::track_hi_bound)
        .def_readwrite("passthru_spans", &TrackSegment::passthru_spans);

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

    py::class_<UnseatableTrunk>(m, "UnseatableTrunk")
        .def_readwrite("bundle_id", &UnseatableTrunk::bundle_id)
        .def_readwrite("seg_idx",   &UnseatableTrunk::seg_idx)
        .def_readwrite("horiz",     &UnseatableTrunk::horiz)
        .def_readwrite("nom",       &UnseatableTrunk::nom)
        .def_readwrite("bound",     &UnseatableTrunk::bound);

    py::class_<NUTSResult>(m, "NUTSResult")
        .def(py::init<>())
        .def_readwrite("segments",           &NUTSResult::segments)
        .def_readwrite("overlap_details",    &NUTSResult::overlap_details)
        .def_readwrite("junction_infeasibilities",
                       &NUTSResult::junction_infeasibilities)
        .def_readwrite("unseatable_trunks",  &NUTSResult::unseatable_trunks)
        .def_readwrite("num_violations",     &NUTSResult::num_violations)
        .def_readwrite("num_keepout_conflicts", &NUTSResult::num_keepout_conflicts)
        .def_readwrite("num_overlaps",       &NUTSResult::num_overlaps)
        .def_readwrite("overlaps_per_layer", &NUTSResult::overlaps_per_layer)
        // Per-pass solve profile (RR round-3 Phase 0) — observation only.
        // readwrite: Python-side result MERGES (the bottom-up paths build
        // combined results) must be able to sum and reassign the profile.
        .def_readwrite("pass_seconds",       &NUTSResult::pass_seconds)
        .def_readwrite("n_solves",           &NUTSResult::n_solves)
        .def_readwrite("overlaps_post_fixpoint",
                       &NUTSResult::overlaps_post_fixpoint)
        // Getter by value: dict values must be OWNED Topology copies, not
        // registered views into the map — a view held after this result is
        // replaced dangles, and its stale registry entry can alias a later
        // cast at a recycled address (audit C7-04; same fix as
        // BundleInput::candidates in bind_routing.cpp).
        .def_property("dogleg_topologies",
            [](const NUTSResult& r) { return r.dogleg_topologies; },
            [](NUTSResult& r, std::map<int, Topology> m) {
                r.dogleg_topologies = std::move(m);
            })
        .def_readwrite("dogleg_seg_layers",   &NUTSResult::dogleg_seg_layers)
        .def_readwrite("dogleg_seg_net_pull", &NUTSResult::dogleg_seg_net_pull)
        .def_readwrite("dogleg_seg_perp",     &NUTSResult::dogleg_seg_perp)
        .def_readwrite("dogleg_seg_slide_lo", &NUTSResult::dogleg_seg_slide_lo)
        .def_readwrite("dogleg_seg_slide_hi", &NUTSResult::dogleg_seg_slide_hi);

    py::class_<NUTSEngine>(m, "NUTSEngine")
        // Engine keeps references to both args (floorplan_/layers_); keep them
        // alive as long as the engine so temporaries are not freed early.
        .def(py::init<const Floorplan&, const LayerStack&>(),
             py::keep_alive<1, 2>(), py::keep_alive<1, 3>())
        .def("set_track_pitch",       &NUTSEngine::set_track_pitch)
        .def("set_skip_tighten",      &NUTSEngine::set_skip_tighten)
        .def("set_skip_doglegs",      &NUTSEngine::set_skip_doglegs)
        .def("set_extra_grid_points", &NUTSEngine::set_extra_grid_points)
        .def("add_fixed_segments",    &NUTSEngine::add_fixed_segments,
             py::arg("segs"))
        .def("add_fixed_segments_except",
             &NUTSEngine::add_fixed_segments_except,
             py::arg("baseline"), py::arg("exclude_bid"),
             "Fix every placed segment of `baseline` except `exclude_bid`'s "
             "(the RR fixed-context screen's frozen occupancy, one call)")
        .def("run",                   &NUTSEngine::run)
        .def("screen_candidates",     &NUTSEngine::screen_candidates,
             py::arg("bundles"), py::arg("target_bid"), py::arg("tidxs"),
             py::arg("planner"), py::arg("clear_dogleg_overrides") = false,
             "Batched fixed-context screen: score every candidate in one "
             "call on a pre-configured screen engine — one wrapper-list "
             "conversion per contender, no session-state mutation.  Returns "
             "[(tidx, overlaps, violations)], or None when the incremental "
             "replan is unavailable")
        .def("rerun_layer",           &NUTSEngine::rerun_layer)
        .def("rerun_bundle_warm",     &NUTSEngine::rerun_bundle_warm,
             py::arg("prev"), py::arg("bundles"), py::arg("target_bid"),
             "Warm-start single-bundle re-solve: place target_bid against "
             "prev frozen, then run the safety passes on the unfrozen "
             "union.  Exact metrics for the WARM state — a predictor of "
             "the cold run()'s metric, never a substitute")
        // Sequence FALLBACKS (P2, bind_opaque.h): the natives above take
        // the zero-copy BundleWrapperVec; plain lists of wrappers land
        // here and keep the historical copy semantics.
        .def("run", [](NUTSEngine& e, py::sequence seq) {
                 return e.run(wrappers_from_seq(seq));
             })
        .def("screen_candidates",
             [](NUTSEngine& e, py::sequence seq, int target_bid,
                const std::vector<int>& tidxs, CongestionPlanner& planner,
                bool clear_dogleg_overrides) {
                 return e.screen_candidates(wrappers_from_seq(seq),
                                            target_bid, tidxs, planner,
                                            clear_dogleg_overrides);
             },
             py::arg("bundles"), py::arg("target_bid"), py::arg("tidxs"),
             py::arg("planner"), py::arg("clear_dogleg_overrides") = false)
        .def("rerun_layer",
             [](NUTSEngine& e, const NUTSResult& prev, py::sequence seq,
                int layer_id) {
                 return e.rerun_layer(prev, wrappers_from_seq(seq),
                                      layer_id);
             })
        .def("rerun_bundle_warm",
             [](NUTSEngine& e, const NUTSResult& prev, py::sequence seq,
                int target_bid) {
                 return e.rerun_bundle_warm(prev, wrappers_from_seq(seq),
                                            target_bid);
             }, py::arg("prev"), py::arg("bundles"), py::arg("target_bid"));

    m.def("transform_track_segment", &transform_track_segment,
          py::arg("ts"), py::arg("orient"), py::arg("cell_w"),
          py::arg("cell_h"), py::arg("src_x"), py::arg("src_y"),
          py::arg("dst_x"), py::arg("dst_y"), py::arg("new_bundle_id"),
          "Orientation-aware offset_track_segment (N/S/FN/FS; 90/270 throw)");
    m.def("transform_net_segment", &transform_net_segment,
          py::arg("ns"), py::arg("orient"), py::arg("cell_w"),
          py::arg("cell_h"), py::arg("src_x"), py::arg("src_y"),
          py::arg("dst_x"), py::arg("dst_y"), py::arg("new_bundle_id"),
          py::arg("horiz"),
          "Orientation-aware offset_net_segment (N/S/FN/FS; 90/270 throw)");
    m.def("transform_net_via", &transform_net_via,
          py::arg("v"), py::arg("orient"), py::arg("cell_w"),
          py::arg("cell_h"), py::arg("src_x"), py::arg("src_y"),
          py::arg("dst_x"), py::arg("dst_y"), py::arg("new_bundle_id"),
          "Orientation-aware offset_net_via (N/S/FN/FS; 90/270 throw)");
    m.def("offset_track_segment", &offset_track_segment,
          py::arg("ts"), py::arg("dx"), py::arg("dy"), py::arg("new_bundle_id"),
          "Translate a placed TrackSegment by (dx, dy) and re-key it to "
          "another bundle — the per-instance copy step of bottom-up "
          "template planning");

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
        .def_readonly("bounded",      &TrackPattern::bounded)
        .def_readonly("bound_lo",     &TrackPattern::bound_lo)
        .def_readonly("bound_hi",     &TrackPattern::bound_hi)
        .def("set_bounds",            &TrackPattern::set_bounds,
             py::arg("lo"), py::arg("hi"))
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
        .def("signal_tracks_in_span", &RoutingGrid::signal_tracks_in_span,
             py::arg("along_lo"), py::arg("along_hi"),
             py::arg("lo"), py::arg("hi"))
        .def("global_pattern",  &RoutingGrid::global_pattern,
             py::return_value_policy::copy)
        .def("has_overrides",   &RoutingGrid::has_overrides)
        .def("is_horizontal",   &RoutingGrid::is_horizontal)
        .def("count_signal_tracks_in", &RoutingGrid::count_signal_tracks_in,
             py::arg("x"), py::arg("lo"), py::arg("hi"))
        .def("count_signal_tracks_in_span",
             &RoutingGrid::count_signal_tracks_in_span,
             py::arg("along_lo"), py::arg("along_hi"),
             py::arg("lo"), py::arg("hi"));

    py::class_<RoutingGridStack>(m, "RoutingGridStack")
        .def(py::init<>())
        // Deep copy for derived per-cell views (hier_layer_caps.md Phase 3
        // Tier 1: the bottom-up DNUTS reference solve gets a clone with the
        // shared cells' thinned patterns installed as instance-bbox
        // overrides).
        .def("clone", [](const RoutingGridStack& s) { return s; })
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
        .def_readwrite("bit_list",        &BusSegment::bit_list)
        .def_readwrite("timing_critical", &BusSegment::timing_critical)
        .def_readwrite("connections",     &BusSegment::connections)
        .def_readwrite("busterm_faces",   &BusSegment::busterm_faces)
        .def_readwrite("passthru_spans",  &BusSegment::passthru_spans)
        .def_readwrite("abstract_pos",    &BusSegment::abstract_pos)
        .def_readwrite("ndr",             &BusSegment::ndr)
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
        .def_readwrite("span_hi",        &NetSegment::span_hi)
        .def_readwrite("is_shield",      &NetSegment::is_shield);

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
        .def_readwrite("num_keepout_bits", &DetailedNUTSResult::num_keepout_bits)
        .def_readwrite("pair_misalign_wl", &DetailedNUTSResult::pair_misalign_wl)
        .def_readwrite("aborted",          &DetailedNUTSResult::aborted)
        // Per-pass profile (RR round-3 Phase 0) — observation only.
        // readwrite: the bottom-up DNUTS path merges two engine results in
        // Python and must carry the summed profile (Codex #289).
        .def_readwrite("pass_seconds",     &DetailedNUTSResult::pass_seconds);

    py::class_<DetailedNUTSEngine>(m, "DetailedNUTSEngine")
        // The engine keeps a reference to the stack (stack_); keep it alive as
        // long as the engine so a temporary stack (e.g. Engine(make_stack()))
        // is not freed before run() dereferences it.
        .def(py::init<const RoutingGridStack&>(), py::keep_alive<1, 2>())
        .def("add_fixed_bits", &DetailedNUTSEngine::add_fixed_bits,
             py::arg("bits"))
        .def("set_pair_align", &DetailedNUTSEngine::set_pair_align,
             py::arg("on"),
             "Pairwise-overlap seating for this engine (prototype); the "
             "measured-accept alignment heal toggles it per-run.")
        .def("run", &DetailedNUTSEngine::run, py::arg("bus_segments"),
             py::arg("emit_vias") = true, py::arg("abort_unplaced") = -1);

    m.def("offset_net_segment", &offset_net_segment,
          py::arg("ns"), py::arg("dx"), py::arg("dy"),
          py::arg("new_bundle_id"), py::arg("horiz"),
          "Translate a solved bit-wire by (dx, dy) and re-key it to a "
          "sibling instance's bundle (bottom-up stage c copy)");
    m.def("offset_net_via", &offset_net_via,
          py::arg("v"), py::arg("dx"), py::arg("dy"), py::arg("new_bundle_id"),
          "Translate a per-bit via by (dx, dy) and re-key it to a sibling "
          "instance's bundle (bottom-up stage c copy)");

    // Stage-4 -> stage-9 handoff, single-sourced in C++: every TrackSegment
    // of the NUTS result becomes a BusSegment (track_position -> abstract_pos,
    // corner bounds carried, bit_width = the bundle's net count) with its SEG
    // connections + BUSTERM faces derived from the selected topology's cached
    // analysis — the derivation the abstract solve placed with, existing once.
    m.def("make_bus_segments", &make_bus_segments,
          py::arg("bundles"), py::arg("nuts_result"), py::arg("floorplan"),
          py::arg("bit_order") = "LO_HI",
          "Build DetailedNUTSEngine.run() input from the placed NUTS result");
    // Sequence fallback (P2): plain wrapper lists keep the copy path.
    m.def("make_bus_segments",
          [](py::sequence seq, const NUTSResult& nr, const Floorplan& fp,
             const std::string& bit_order) {
              return make_bus_segments(wrappers_from_seq(seq), nr, fp,
                                       bit_order);
          },
          py::arg("bundles"), py::arg("nuts_result"), py::arg("floorplan"),
          py::arg("bit_order") = "LO_HI");

    // ── Test support: overlap-delta exactness probes (corner-pass work) ──
    // The repair/repack accept guards replaced their per-move full
    // find_overlaps recounts with overlap_delta_vs; these two hooks let the
    // test suite pin the identity  delta == count(after) − count(before)
    // on arbitrary segment sets (test_overlap_delta.py).
    m.def("_find_overlap_count",
          [](const std::vector<TrackSegment>& segs) {
              return find_overlaps(segs).size();
          });
    m.def("_overlap_delta_vs",
          [](const std::vector<TrackSegment>& before,
             const std::vector<TrackSegment>& after) {
              PlacementSnapshot snap;
              snap.take(before);
              return overlap_delta_vs(after, snap);
          });

    // ── Parallel trial sweep (rnr runtime P1, trial_sweep.h) ─────────────
    // Evaluate k ('idx', tidx) ripup moves against the committed baseline on
    // worker threads (GIL released — inputs are converted up front and the
    // workers never touch Python state).  Returns [(primary, secondary,
    // viols, wl, ok), ...] per move in input order (viols/wl feed
    // refine_selection's full-trial sweep; the ripup sweeps ignore them);
    // the caller picks the first in-order strict improver and REPLAYS it
    // through the normal sequential trial before committing (metrics here
    // carry the stall certificate and the pick order, never the committed
    // state).
    m.def("parallel_sweep",
          [](const std::vector<BundleWrapper>& bundles,
             const std::vector<std::pair<int, int>>& moves,
             CongestionPlanner& planner, const Floorplan& fp,
             const LayerStack& layers, double track_pitch,
             const std::vector<TrackSegment>& fixed_segs,
             const std::vector<int>& extra_x,
             const std::vector<int>& extra_y,
             bool stage_b, bool skip_tighten_stage_a,
             const std::set<int>& dogleg_slot_bids,
             const std::map<int, int>& base_disc,
             const std::map<int, int>& net_counts,
             const RoutingGridStack* grid, const std::string& bit_order,
             int abort_unplaced,
             const std::set<int>& ref_ids, const std::set<int>& skip_ids,
             const std::vector<std::tuple<int, int, std::string, int, int,
                                          int, int, int, int>>& copy_specs,
             const std::map<std::pair<int, int>, bool>& horiz_of,
             int n_threads, bool full_trials,
             const RoutingGridStack* ref_grid) {
              std::vector<SweepMove> mv;
              mv.reserve(moves.size());
              for (const auto& [bid, tidx] : moves)
                  mv.push_back(SweepMove{bid, tidx});
              SweepDnutsCtx dn;
              dn.enabled = stage_b;
              dn.grid = grid;
              dn.ref_grid = ref_grid;
              dn.bit_order = bit_order;
              dn.abort_unplaced = abort_unplaced;
              dn.ref_ids = ref_ids;
              dn.skip_ids = skip_ids;
              for (const auto& t : copy_specs) {
                  SweepDnutsCtx::CopySpec cs;
                  std::tie(cs.ref_bid, cs.sib_bid, cs.orient, cs.cw, cs.ch,
                           cs.rx, cs.ry, cs.sx, cs.sy) = t;
                  dn.copy_specs.push_back(std::move(cs));
              }
              dn.horiz_of = horiz_of;
              std::vector<std::tuple<int, int, int, double, bool>> out;
              {
                  py::gil_scoped_release release;
                  auto res = parallel_sweep(
                      bundles, mv, planner, fp, layers, track_pitch,
                      fixed_segs, extra_x, extra_y, stage_b,
                      skip_tighten_stage_a, full_trials, dogleg_slot_bids,
                      base_disc, net_counts, dn, n_threads);
                  out.reserve(res.size());
                  for (const auto& r : res)
                      out.emplace_back(r.primary, r.secondary, r.viols,
                                       r.wl, r.ok);
              }
              return out;
          },
          py::arg("bundles"), py::arg("moves"), py::arg("planner"),
          py::arg("floorplan"), py::arg("layers"), py::arg("track_pitch"),
          py::arg("fixed_segs"), py::arg("extra_x"), py::arg("extra_y"),
          py::arg("stage_b"), py::arg("skip_tighten_stage_a"),
          py::arg("dogleg_slot_bids"), py::arg("base_disc"),
          py::arg("net_counts"), py::arg("grid") = nullptr,
          py::arg("bit_order") = "LO_HI", py::arg("abort_unplaced") = -1,
          py::arg("ref_ids") = std::set<int>{},
          py::arg("skip_ids") = std::set<int>{},
          py::arg("copy_specs") = std::vector<std::tuple<
              int, int, std::string, int, int, int, int, int, int>>{},
          py::arg("horiz_of") = std::map<std::pair<int, int>, bool>{},
          py::arg("n_threads") = 0, py::arg("full_trials") = false,
          py::arg("ref_grid") = nullptr);

    // Batched PARALLEL fixed-context screening (the refine/ripup chunk
    // builds' sequential-screen cost at chip scale): one worker per
    // (bundle, tidxs) job, private planner clone + engine per job — the
    // exact _rr_screen_scores shape, so batching is decision-identical to
    // the sequential per-bundle calls.  Returns per job the screen rows
    // [(tidx, overlaps, violations)] or None (unscreened fallback).
    m.def("parallel_screen",
          [](const std::vector<BundleWrapper>& bundles,
             const std::vector<std::tuple<int, std::vector<int>,
                                          bool>>& jobs,
             const CongestionPlanner& planner, const Floorplan& fp,
             const LayerStack& layers, double track_pitch,
             const NUTSResult& baseline,
             const std::vector<int>& extra_x,
             const std::vector<int>& extra_y, int n_threads) {
              std::vector<ScreenJob> js;
              js.reserve(jobs.size());
              for (const auto& [bid, tidxs, clear] : jobs)
                  js.push_back(ScreenJob{bid, tidxs, clear});
              std::vector<std::optional<
                  std::vector<std::array<int, 3>>>> res;
              {
                  py::gil_scoped_release release;
                  res = parallel_screen(bundles, js, planner, fp, layers,
                                        track_pitch, baseline, extra_x,
                                        extra_y, n_threads);
              }
              py::list out;
              for (const auto& r : res) {
                  if (!r) { out.append(py::none()); continue; }
                  py::list rows;
                  for (const auto& a : *r)
                      rows.append(py::make_tuple(a[0], a[1], a[2]));
                  out.append(rows);
              }
              return out;
          },
          py::arg("bundles"), py::arg("jobs"), py::arg("planner"),
          py::arg("floorplan"), py::arg("layers"), py::arg("track_pitch"),
          py::arg("baseline"), py::arg("extra_x"), py::arg("extra_y"),
          py::arg("n_threads") = 0);

    // ── Verify ────────────────────────────────────────────────────────────
    py::enum_<ViolationKind>(m, "ViolationKind")
        .value("SEG_OPEN",     ViolationKind::SEG_OPEN)
        .value("BUSTERM_OPEN", ViolationKind::BUSTERM_OPEN)
        .value("BUSTERM_FACE", ViolationKind::BUSTERM_FACE)
        .value("UNPLACED",     ViolationKind::UNPLACED)
        .value("LAYER_DIR",    ViolationKind::LAYER_DIR)
        .value("FEEDTHRU_RELAY", ViolationKind::FEEDTHRU_RELAY)
        .value("BIT_SHORT",    ViolationKind::BIT_SHORT)
        .value("KEEPOUT_CROSS", ViolationKind::KEEPOUT_CROSS)
        .value("ANTENNA",      ViolationKind::ANTENNA)
        .value("DISCONNECTED", ViolationKind::DISCONNECTED);

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
    // Declared-feedthru scoping for the DISCONNECTED gate: True iff the wire
    // graph splits into 2+ islands AND every island touches a declared feedthru
    // block (so the split is bridged, not a real open — see verify.h).  Used by
    // ripup's disconnected-bits metric term to exempt legitimately-bridged
    // feedthru splits (issue #399 follow-up 2).
    m.def("disconnected_islands_bridged", &disconnected_islands_bridged,
          py::arg("ct"), py::arg("topo"), py::arg("fp"));
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
