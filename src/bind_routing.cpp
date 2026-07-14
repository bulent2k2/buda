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
#include <cstdio>
#include <iostream>
#include "topology.h"
#include "topology_analysis.h"
#include "topo_edit.h"
#include "layering.h"
#include "congestion_planner.h"
#include "floorplanner.h"
#include "bdb.h"        // BDB, BustermRow, TopoSegBustermRow (seg-busterm persistence)
#include "busterm.h"    // encode/decode_rects_json

namespace py = pybind11;
using namespace buda;

namespace {

// ── seg_busterms ⇄ BDB (single-source-of-topo-truth, Phase 3) ────────────────
// Persist a topology's seg_busterms LOGICALLY: each real tap becomes a routing-
// time busterm row ('tb:<block>', carrying the full Busterm — margin bbox, orig
// bbox, multi-rect, TEG) plus a topology_seg_busterm link naming which segment
// endpoint taps it.  A junction endpoint (nullopt) writes no row.  Reload rebuilds
// the annotation from those rows alone — no geometric re-derivation, no floorplan.

void persist_seg_busterms(BDB& bdb, const std::string& bundle_id,
                          int cand_index, const Topology& topo,
                          py::object seen = py::none()) {
    // A 'tb:<block>' busterm row is derived purely from block geometry, so it
    // is byte-identical for every candidate that taps the block — yet the row
    // (a wide INSERT with JSON-encoded rects) dominates generate-time persist.
    // When `seen` (a set of already-written 'tb:' ids) is supplied, write each
    // busterm row ONCE and emit only the cheap per-candidate link row for
    // repeats.  seen=None keeps the old always-write behavior for callers that
    // don't dedup (e.g. the planner path, which persists few candidates).
    const bool dedup = !seen.is_none();
    py::set seen_set;
    if (dedup) seen_set = py::reinterpret_borrow<py::set>(seen);
    auto put = [&](int seg_idx, const std::optional<Busterm>& bt,
                   const char* endpoint) {
        if (!bt) return;                         // junction: no row (the default)
        const std::string id = "tb:" + bt->block_name;  // distinct from hier 'bt:'
        bool write_row = true;
        if (dedup) {
            py::str pid(id);
            if (seen_set.contains(pid)) write_row = false;
            else                        seen_set.add(pid);
        }
        if (write_row) {
            BustermRow row;
            row.id         = id;
            row.comp_id    = -1;                 // no component (bound NULL)
            row.hier_path  = bt->block_name;
            row.depth      = -1;
            row.x1 = bt->bbox.x1; row.y1 = bt->bbox.y1;
            row.x2 = bt->bbox.x2; row.y2 = bt->bbox.y2;
            row.resolution = "BLOCK";
            std::vector<std::tuple<double,double,double,double>> rts;
            for (const auto& r : bt->rects)
                rts.emplace_back(r.x1, r.y1, r.x2, r.y2);
            row.rects    = encode_rects_json(rts);
            row.teg_mode = (bt->teg_mode == TegMode::OVER) ? "OVER" : "THRU";
            row.orig_x1 = bt->orig_bbox.x1; row.orig_y1 = bt->orig_bbox.y1;
            row.orig_x2 = bt->orig_bbox.x2; row.orig_y2 = bt->orig_bbox.y2;
            bdb.add_busterm(row);
        }
        bdb.add_topology_seg_busterm(
            TopoSegBustermRow{bundle_id, cand_index, seg_idx, endpoint, id});
    };
    for (const auto& [seg_idx, eps] : topo.seg_busterms) {
        put(seg_idx, eps.first,  "start");
        put(seg_idx, eps.second, "end");
    }
}

// ── seg_conns ⇄ BDB (single-source-of-topo-truth, Phase 5) ───────────────────
// Persist a topology's seg_conns LOGICALLY: one topology_seg_conn row per real
// junction link (seg endpoint → other segment).  Purely index-valued, so the
// round-trip involves no geometry at all.

void persist_seg_conns(BDB& bdb, const std::string& bundle_id,
                       int cand_index, const Topology& topo) {
    for (const auto& [key, others] : topo.seg_conns)
        for (int other : others)
            bdb.add_topology_seg_conn(TopoSegConnRow{
                bundle_id, cand_index, key.first,
                key.second == 0 ? "start" : "end", other});
}

// Rebuild topo.seg_conns from the persisted rows (replacing whatever was
// there).  Returns the number of link rows read — 0 means nothing persisted
// (pre-v12 checkpoint), which load_seg_busterms uses to fall back.
int load_seg_conns(BDB& bdb, const std::string& bundle_id,
                   int cand_index, Topology& topo) {
    std::map<std::pair<int,int>, std::vector<int>> sc;
    int n = 0;
    for (const auto& r : bdb.topology_seg_conns(bundle_id, cand_index)) {
        sc[{r.seg_index, r.endpoint == "start" ? 0 : 1}].push_back(r.other_seg);
        ++n;
    }
    topo.seg_conns = std::move(sc);   // SELECT order keeps values sorted
    return n;
}

// Rebuild topo.seg_busterms from the persisted rows (replacing whatever was
// there), then restore seg_conns — EVERY reload path must hand ConnTopology a
// fully-annotated topology (Phase 4 retired the geometric junction scan, so a
// reloaded candidate with empty seg_conns would silently lose all stub↔trunk /
// bend edges — Codex #151 P2).  Callers must assign topo.segments BEFORE
// calling (load_pipeline does).  seg_conns loads LOGICALLY from the
// topology_seg_conn links (Phase 5); a pre-v12 checkpoint (zero link rows for a
// multi-segment candidate) falls back to one explicit geometric derivation.
void load_seg_busterms(BDB& bdb, const std::string& bundle_id,
                       int cand_index, Topology& topo) {
    auto to_busterm = [](const BustermRow& r) {
        Busterm bt;
        bt.block_name = r.hier_path;
        bt.bbox      = Rect{(int)r.x1, (int)r.y1, (int)r.x2, (int)r.y2};
        bt.orig_bbox = Rect{(int)r.orig_x1, (int)r.orig_y1,
                            (int)r.orig_x2, (int)r.orig_y2};
        for (const auto& [x1, y1, x2, y2] : decode_rects_json(r.rects))
            bt.rects.push_back(Rect{(int)x1, (int)y1, (int)x2, (int)y2});
        bt.teg_mode = (r.teg_mode == "OVER") ? TegMode::OVER : TegMode::THRU;
        return bt;
    };
    std::map<int, SegEndpoints> sb;
    for (const auto& link : bdb.topology_seg_busterms(bundle_id, cand_index)) {
        auto row = bdb.busterm(link.busterm_id);
        if (!row) continue;
        auto& ep = sb[link.seg_index];
        if (link.endpoint == "start") ep.first  = to_busterm(*row);
        else                          ep.second = to_busterm(*row);
    }
    topo.seg_busterms = std::move(sb);
    // Junction links: logical restore; geometric derive only for pre-v12
    // checkpoints (the fallback prints once so a silent downgrade is visible).
    if (load_seg_conns(bdb, bundle_id, cand_index, topo) == 0 &&
        topo.segments.size() > 1) {
        std::cout << "[load] no persisted seg_conns for bundle " << bundle_id
                  << " cand " << cand_index
                  << " (pre-v12 checkpoint) — deriving geometrically once.\n";
        annotate_seg_conns(topo);
    }
}

}  // namespace

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
        .def_readwrite("layer_hint", &Segment::layer_hint)
        .def_readwrite("is_jog",     &Segment::is_jog)
        .def_readwrite("edge_id",    &Segment::edge_id)
        .def_readwrite("perp_clamp_lo", &Segment::perp_clamp_lo)
        .def_readwrite("perp_clamp_hi", &Segment::perp_clamp_hi);

    py::class_<Busterm>(m, "Busterm")
        .def(py::init<>())
        .def_readwrite("block_name", &Busterm::block_name)
        .def_readwrite("bbox",       &Busterm::bbox)
        .def_readwrite("orig_bbox",  &Busterm::orig_bbox)
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
        .def_readwrite("seg_conns",             &Topology::seg_conns)
        .def_readwrite("bridge_segments",       &Topology::bridge_segments)
        .def_readwrite("connected_block_names", &Topology::connected_block_names)
        .def_readwrite("seg_bits",              &Topology::seg_bits)
        .def_readwrite("feedthru_blocks",       &Topology::feedthru_blocks);

    // Tapered fan-in: derive per-segment bit membership (Topology::seg_bits)
    // from the bundle's per-net endpoint blocks — one driver + receiver list
    // per bit, index = the bundle's net order.  Returns the bit indices that
    // fell back to all-segments (no attach/path — the net-driver fidelity
    // check's per-bit input).
    m.def("derive_fanin_seg_bits", &derive_fanin_seg_bits,
          py::arg("topo"), py::arg("fp"),
          py::arg("driver_per_bit"), py::arg("receivers_per_bit"));

    // Deep-copy a topology with all geometry shifted by (dx, dy), preserving the
    // seg_busterms endpoint annotation (used by the hier flow to place a
    // cell-local candidate at an instance without losing authoritative
    // connectivity — see conn_topology.cpp geometric-fallback caveat).
    m.def("offset_topology", &offset_topology,
          py::arg("topo"), py::arg("dx"), py::arg("dy"),
          py::arg("name_prefix") = std::string());
    m.def("transform_topology", &transform_topology,
          py::arg("topo"), py::arg("orient"), py::arg("cell_w"),
          py::arg("cell_h"), py::arg("dx"), py::arg("dy"),
          py::arg("name_prefix") = std::string(),
          "Orientation-aware offset_topology: map a cell-local topology "
          "through an 8-orientation token over a cell_w x cell_h box, then "
          "shift; 90/270 swap H<->V segments (layers must be re-assigned)");
    m.def("orient_compose", &orient_compose,
          py::arg("outer"), py::arg("inner"),
          "Compose two 8-orientation tokens (outer applied after inner)");
    m.def("orient_inverse", &orient_inverse, py::arg("orient"),
          "Inverse of an 8-orientation token (mirrors/180 are involutions)");

    // Explicitly annotate a (hand-built or reloaded) topology's seg_busterms from
    // a floorplan, so ConnTopology can read the authoritative endpoint taps
    // (it no longer geometrically guesses).  Mutates topo in place.
    m.def("annotate_topology", [](Topology& topo, const Floorplan& fp) {
        annotate_topology(topo, fp);
    }, py::arg("topo"), py::arg("fp"));

    // Explicitly (re)derive a topology's seg_conns from its segments — the
    // authoritative seg-to-seg junction annotation ConnTopology reads (it no
    // longer scans for touching segments; topo-truth Phase 4).  Skips
    // busterm-tapped endpoints, so call AFTER seg_busterms is in place
    // (annotate_topology already does both).  Mutates topo in place.
    m.def("annotate_seg_conns", [](Topology& topo) {
        annotate_seg_conns(topo);
    }, py::arg("topo"));

    // Per-edge L/Z flip: swap one MST edge's bend to the opposite corner in place
    // (floorplan-validated: rejects a bend onto a block interior/corner).
    m.def("flip_mst_edge", &flip_mst_edge,
          py::arg("topo"), py::arg("edge_id"),
          py::arg("h_layer"), py::arg("v_layer"), py::arg("fp"));

    // Analysis-cache instrumentation (Phase B, topo_conn_unification.md):
    // cumulative (computes, hits) of the content-fingerprint-validated
    // ConnTopology analysis cache, plus a reset.  Test/diagnostic only.
    m.def("analysis_cache_counters", &analysis_cache_counters);
    m.def("analysis_cache_reset_counters", &analysis_cache_reset_counters);

    // Stable candidate identity (Phase E1): a hex content key over all
    // load-bearing persisted topology state (segments incl. edge_id,
    // canonical seg_busterms, seg_conns, bridges, feedthru/connected blocks).
    // Recomputable from a checkpoint alone — uid(generated) == uid(reloaded);
    // pins and sidecar selections re-attach by this key across regenerations.
    m.def("topo_uid", [](const Topology& t) {
        char buf[17];
        std::snprintf(buf, sizeof buf, "%016llx",
                      (unsigned long long)topology_fingerprint(t));
        return std::string(buf);
    }, py::arg("topo"));

    // ── TopoEdit (Phase E3): transactional expert edits, each returning an
    // EditVerdict (check_topo violations + pinch) so a hand edit can never
    // silently corrupt annotations.  Undo = snapshot/restore the Topology.
    py::class_<EditVerdict>(m, "EditVerdict")
        .def_readonly("applied", &EditVerdict::applied)
        .def_readonly("note",    &EditVerdict::note)
        .def_readonly("seg_idx", &EditVerdict::seg_idx)
        .def_readonly("conn",    &EditVerdict::conn)
        .def_readonly("pinched", &EditVerdict::pinched)
        .def_readonly("components", &EditVerdict::components)
        .def("ok", &EditVerdict::ok);
    m.def("edit_add_trunk", &edit_add_trunk,
          py::arg("topo"), py::arg("fp"), py::arg("horiz"), py::arg("perp_pos"),
          py::arg("along_lo") = 1, py::arg("along_hi") = 0,   // lo>hi = full span
          py::arg("layer"));
    m.def("edit_remove_segment", &edit_remove_segment,
          py::arg("topo"), py::arg("fp"), py::arg("seg_idx"));
    m.def("edit_add_stub", &edit_add_stub,
          py::arg("topo"), py::arg("fp"), py::arg("block"), py::arg("to_seg"),
          py::arg("layer"));
    m.def("edit_set_span", &edit_set_span,
          py::arg("topo"), py::arg("fp"), py::arg("seg_idx"),
          py::arg("along_lo"), py::arg("along_hi"));
    m.def("edit_connect", &edit_connect,
          py::arg("topo"), py::arg("fp"), py::arg("i"), py::arg("j"));
    m.def("edit_disconnect", &edit_disconnect,
          py::arg("topo"), py::arg("fp"), py::arg("i"), py::arg("j"),
          py::arg("retract_to"));
    m.def("edit_verdict", &edit_verdict, py::arg("topo"), py::arg("fp"));

    // Persist / reload a topology's seg_busterms logically (Phase 3): the tap
    // annotation round-trips through the BDB busterm + topology_seg_busterm tables
    // with no geometric re-derivation.  See single_source_topo_truth.md.
    m.def("persist_seg_busterms", &persist_seg_busterms,
          py::arg("bdb"), py::arg("bundle_id"), py::arg("cand_index"),
          py::arg("topo"), py::arg("seen") = py::none());
    // Persist / reload a topology's seg_conns logically (Phase 5): the junction
    // links round-trip through topology_seg_conn with no geometric re-derivation.
    m.def("persist_seg_conns", &persist_seg_conns,
          py::arg("bdb"), py::arg("bundle_id"), py::arg("cand_index"),
          py::arg("topo"));
    m.def("load_seg_conns", &load_seg_conns,
          py::arg("bdb"), py::arg("bundle_id"), py::arg("cand_index"),
          py::arg("topo"));
    m.def("load_seg_busterms", &load_seg_busterms,
          py::arg("bdb"), py::arg("bundle_id"), py::arg("cand_index"),
          py::arg("topo"));

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
        .def("set_feedthru",            &Floorplan::set_feedthru)
        .def("set_feedthru_block",      &Floorplan::set_feedthru_block)
        .def("set_feedthru_layer",      &Floorplan::set_feedthru_layer)
        .def("set_feedthru_block_layer",&Floorplan::set_feedthru_block_layer)
        .def("get_feedthru",            &Floorplan::get_feedthru)
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
        .def("set_gds_mapping",         &LayerStack::set_gds_mapping,
             py::arg("id"), py::arg("gds_layer"), py::arg("gds_datatype") = 0)
        .def("get_gds_layer",           &LayerStack::get_gds_layer)
        .def("get_gds_datatype",        &LayerStack::get_gds_datatype)
        .def("layer_for_gds",           &LayerStack::layer_for_gds,
             py::arg("gds_layer"), py::arg("gds_datatype"))
        .def("gds_mapped_pairs",        &LayerStack::gds_mapped_pairs)
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
        .def("set_multi_trunk",    &TopologyGenerator::set_multi_trunk)
        .def("set_layer_ids",      &TopologyGenerator::set_layer_ids)
        .def("set_all_h_layers",   &TopologyGenerator::set_all_h_layers)
        .def("set_all_v_layers",   &TopologyGenerator::set_all_v_layers)
        .def("generate_candidates", [](TopologyGenerator& self,
                                       const std::string& src,
                                       py::object dsts) {
            if (py::isinstance<py::str>(dsts))
                return self.generate_candidates(src, {dsts.cast<std::string>()});
            return self.generate_candidates(src, dsts.cast<std::vector<std::string>>());
        })
        // Coverage gate, directly testable: returns the filtered list (pybind
        // copies the vector, so in-place mutation would not reach the caller).
        .def("filter_uncovered", [](const TopologyGenerator& self,
                                    std::vector<Topology> cands) {
            self.filter_uncovered(cands);
            return cands;
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
        .def_readwrite("res_y2",          &BundleHierMeta::res_y2)
        .def_readwrite("locked",          &BundleHierMeta::locked);

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

    py::enum_<CapacityMode>(m, "CapacityMode")
        .value("WIDTH",         CapacityMode::WIDTH)
        .value("SIGNAL_TRACKS", CapacityMode::SIGNAL_TRACKS);

    py::class_<CongestionPlanner>(m, "CongestionPlanner")
        .def(py::init<const Floorplan&, const LayerStack&>())
        .def("set_planner_param",    &CongestionPlanner::set_planner_param)
        .def("set_track_pitch",      &CongestionPlanner::set_track_pitch)
        // Opt-in signal-track capacity model (Gap A part 2).  keep_alive so the
        // RoutingGridStack outlives the planner that stores a pointer to it.
        .def("set_routing_grid",     &CongestionPlanner::set_routing_grid,
             py::arg("grid"), py::keep_alive<1, 2>())
        .def("set_capacity_mode",    &CongestionPlanner::set_capacity_mode, py::arg("mode"))
        .def("build_congestion_map", &CongestionPlanner::build_congestion_map)
        .def("optimize_topologies",  &CongestionPlanner::optimize_topologies)
        .def("replan_bundle",        &CongestionPlanner::replan_bundle,
             py::arg("bundles"), py::arg("target_bundle_id"))
        .def("replan_bundle_ripup",  &CongestionPlanner::replan_bundle_ripup,
             py::arg("bundles"), py::arg("target_bundle_id"))
        .def("inject_band_demand",   &CongestionPlanner::inject_band_demand,
             py::arg("layer_id"), py::arg("span_lo"), py::arg("span_hi"),
             py::arg("perp_lo"), py::arg("perp_hi"), py::arg("amount"))
        .def("clear_injected_demand", &CongestionPlanner::clear_injected_demand)
        .def("recharge_committed",   &CongestionPlanner::recharge_committed,
             py::arg("bundles"))
        .def("band_occupants",       &CongestionPlanner::band_occupants,
             py::arg("bundles"), py::arg("layer_id"),
             py::arg("span_lo"), py::arg("span_hi"),
             py::arg("perp_lo"), py::arg("perp_hi"), py::arg("top_k"))
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
        .def("rotate_block",         &FloorplannerEngine::rotate_block,
             py::arg("name"), py::arg("cw"))
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
