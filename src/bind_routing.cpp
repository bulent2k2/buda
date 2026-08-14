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
#include "bind_opaque.h"   // FIRST: opaque vector<BundleWrapper> (P2)

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/stl_bind.h>
#include <atomic>
#include <mutex>
#include <cstdio>
#include <iostream>
#include <sstream>
#include <thread>
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

// Geometry fingerprint for the routing busterm id (audit P3-03): the id was
// keyed by block NAME alone, but two bundles' frames can contain a same-named
// block with different geometry (cell-local floorplans of two cell types, a
// 90-degree rotation-class clone template, or a cell-local name colliding
// with a top-level block) — with the cross-bundle dedup set, the first
// writer's bbox reloaded for BOTH bundles.  Suffixing a deterministic hash of
// the full one-true-source content keeps identical-geometry blocks on one
// shared row while differing frames split.  FNV-1a, hex — stable across
// platforms/runs so persisted fixtures stay diffable.
std::string busterm_geom_fp(const Busterm& bt) {
    uint64_t h = 1469598103934665603ULL;
    auto mix = [&h](long long v) {
        for (int i = 0; i < 8; ++i) {
            h ^= (unsigned char)(v >> (8 * i));
            h *= 1099511628211ULL;
        }
    };
    auto rect = [&](const Rect& r) { mix(r.x1); mix(r.y1); mix(r.x2); mix(r.y2); };
    rect(bt.bbox);
    rect(bt.orig_bbox);
    for (const auto& r : bt.rects) rect(r);
    mix(bt.teg_mode == TegMode::OVER ? 1 : 0);
    char buf[17];
    std::snprintf(buf, sizeof buf, "%08x",
                  (unsigned)((h >> 32) ^ (h & 0xffffffffULL)));
    return buf;
}

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
        // 'tb:' distinct from hier 'bt:'; geometry-suffixed so same-named
        // blocks from different frames never share a row (audit P3-03).
        const std::string id =
            "tb:" + bt->block_name + ":" + busterm_geom_fp(*bt);
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
        // Real copies for Python holders.  Element access on a bound
        // vector member (def_readwrite getter = reference_internal) hands
        // back a REFERENCE into the vector's storage — held across a
        // size-changing reassignment of that vector it dangles (storage
        // reallocates).  copy.copy() gives an independent Topology (the
        // analysis cache is an immutable shared_ptr, safely shared).
        .def("__copy__", [](const Topology& t) { return Topology(t); })
        .def("__deepcopy__",
             [](const Topology& t, py::dict) { return Topology(t); },
             py::arg("memo"))
        .def("clear_analysis_cache",            &Topology::clear_analysis_cache)
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
        // seg_bits' companion: which bits each BUSTERM tap serves, keyed
        // (segment index, tap ordinal).  Read-only — it is DERIVED, and the
        // one writer is derive_fanin_seg_bits.  Exposed because a taper
        // question is nearly always "which bits does this tap belong to?",
        // and answering it from Python otherwise means inferring it from the
        // spans the placer already produced.
        .def_readonly("seg_busterm_bits",       &Topology::seg_busterm_bits)
        .def_readwrite("feedthru_blocks",       &Topology::feedthru_blocks)
        .def_readwrite("wl_lo",                 &Topology::wl_lo)
        .def_readwrite("wl_hi",                 &Topology::wl_hi);

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

    // Hot-metric accessor (rnr runtime N1): the SELECTED candidate's uid +
    // the bundle id in ONE zero-copy crossing.  The Python-side equivalent
    // costs a full candidate-POOL copy per access (`w.input.candidates`
    // materializes every Topology as a Python list) plus an original_bundle
    // copy for the id — paid per bundle per metric evaluation in ripup's
    // stage-b loop.  A bound BundleWrapper argument passes by reference, so
    // this reads the selection in place.  uid == "" means no valid
    // selection (skip the bundle, matching the historical bounds check).
    m.def("selected_topo_key", [](const BundleWrapper& w) {
        const int sel = w.plan.selected_topology_index;
        const int bid = w.input.original_bundle.id;
        if (sel < 0 || sel >= (int)w.input.candidates.size())
            return py::make_tuple(std::string(), bid);
        char buf[17];
        std::snprintf(buf, sizeof buf, "%016llx",
                      (unsigned long long)topology_fingerprint(
                          w.input.candidates[sel]));
        return py::make_tuple(std::string(buf), bid);
    }, py::arg("wrapper"),
       "(selected candidate's topo_uid or \"\", bundle id) without copying "
       "the candidate pool");

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
        .def_readwrite("layer_ids", &KeepoutZone::layer_ids)
        .def_readwrite("inside_block", &KeepoutZone::inside_block);

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
        .def("add_keepout_zone",        &Floorplan::add_keepout_zone,
             py::arg("x1"), py::arg("y1"), py::arg("x2"), py::arg("y2"),
             py::arg("layer_ids"), py::arg("inside_block") = false)
        .def("get_keepout_zones",       &Floorplan::get_keepout_zones)
        .def("set_keepout_loci_outside_only",
             &Floorplan::set_keepout_loci_outside_only, py::arg("on"))
        .def("keepout_loci_outside_only", &Floorplan::keepout_loci_outside_only)
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
        // Deep copy for derived per-cell views (hier_layer_caps.md Phase 3
        // Tier 1: a shared cell's cell-local solve gets a clone with
        // bit_pitch = unit_pitch / n_kept on its shared layers).
        .def("clone", [](const LayerStack& s) { return s; })
        .def("add_layer",               &LayerStack::add_layer)
        .def("remove_layer",            &LayerStack::remove_layer)
        .def("set_layer_dilution",      &LayerStack::set_layer_dilution)
        .def("set_layer_overhead",      &LayerStack::set_layer_overhead)
        .def("set_bit_pitch",           &LayerStack::set_bit_pitch)
        .def("set_ndr_geom",            &LayerStack::set_ndr_geom)
        .def("ndr_geom",                &LayerStack::ndr_geom,
             py::return_value_policy::reference_internal)
        // R1: the per-signal-slot pitch an ABSOLUTE NDR value is quantized
        // against (ndr_resolve_for_pitch).  0 when the layer is unknown or
        // has no track pattern — which is exactly the "no slot geometry to
        // resolve against" case `def_ndr` refuses on.
        .def("bit_pitch", &LayerStack::bit_pitch, py::arg("layer_id"))
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
        // keep_alive<1,2>: stores const Floorplan& — the Python Floorplan
        // must outlive the generator; a temporary parent
        // (TopologyGenerator(Floorplan())) previously dangled immediately
        // (audit C7-03).
        .def(py::init<const Floorplan&>(), py::keep_alive<1, 2>())
        .def("set_busterm_mode",   &TopologyGenerator::set_busterm_mode)
        .def("set_double_detour",  &TopologyGenerator::set_double_detour)
        .def("set_multi_trunk",    &TopologyGenerator::set_multi_trunk)
        .def("set_hanan_loci",     &TopologyGenerator::set_hanan_loci)
        .def("set_spine_relays",   &TopologyGenerator::set_spine_relays)
        .def("set_mst_leg_trim",   &TopologyGenerator::set_mst_leg_trim)
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

    // ── Parallel candidate generation (chip_flow_parallelism.md P5) ───────
    // Fan a batch of per-bundle generation tasks across a thread pool with
    // the GIL released.  Each task is one PRE-CONFIGURED TopologyGenerator
    // (1:1 task:generator — generate_candidates is not const) plus its
    // (src, dsts); generators may share read-only Floorplans (audited: no
    // mutable state, no const_cast; get_hanan_grid builds fresh vectors).
    // Decision-identical to the sequential loop by construction (per-bundle
    // generation is pure), and PRINT-identical too: each task's "[TopoGen]"
    // notes are captured in a private buffer via set_note_stream and
    // returned per task, so the caller replays them in bundle order exactly
    // where the sequential loop would have printed them.
    // Returns [(candidates, notes_str), ...] in input order.
    m.def("generate_candidates_batch",
          [](py::sequence gens, const std::vector<std::string>& srcs,
             const std::vector<std::vector<std::string>>& dsts,
             int n_threads) {
        std::vector<TopologyGenerator*> tg;
        for (auto h : gens)
            tg.push_back(h.cast<TopologyGenerator*>());
        const size_t n = tg.size();
        if (srcs.size() != n || dsts.size() != n)
            throw std::runtime_error("generate_candidates_batch: length mismatch");
        std::vector<std::vector<Topology>> pools(n);
        std::vector<std::string> notes(n);
        std::exception_ptr first_err = nullptr;
        {
            py::gil_scoped_release release;
            unsigned hw = std::thread::hardware_concurrency();
            unsigned nt = n_threads > 0 ? (unsigned)n_threads : (hw ? hw : 1);
            nt = std::min<unsigned>(nt, (unsigned)std::max<size_t>(n, 1));
            std::atomic<size_t> next{0};
            // Exception containment (Codex #610 P1): an exception escaping a
            // spawned thread is std::terminate, and one unwinding the caller
            // thread past joinable threads terminates too — so each worker
            // catches, records the FIRST error, and the abort flag drains the
            // remaining tasks; the pool always joins, note streams are always
            // restored, and the error rethrows AFTER the join with the GIL
            // held (pybind translates it to a Python exception, exactly like
            // the sequential path's failure).
            std::atomic<bool> abort_flag{false};
            std::mutex err_mu;
            auto worker = [&]() {
                for (size_t i; (i = next.fetch_add(1)) < n; ) {
                    if (abort_flag.load(std::memory_order_relaxed))
                        break;
                    std::ostringstream buf;
                    tg[i]->set_note_stream(&buf);
                    try {
                        pools[i] = tg[i]->generate_candidates(srcs[i], dsts[i]);
                    } catch (...) {
                        std::lock_guard<std::mutex> lk(err_mu);
                        if (!first_err)
                            first_err = std::current_exception();
                        abort_flag.store(true, std::memory_order_relaxed);
                    }
                    tg[i]->set_note_stream(&std::cerr);
                    notes[i] = buf.str();
                }
            };
            if (nt <= 1) {
                worker();
            } else {
                std::vector<std::thread> threads;
                for (unsigned t = 1; t < nt; ++t)
                    threads.emplace_back(worker);
                worker();
                for (auto& th : threads) th.join();
            }
        }
        if (first_err)
            std::rethrow_exception(first_err);
        py::list out;
        for (size_t i = 0; i < n; ++i)
            out.append(py::make_tuple(py::cast(std::move(pools[i])),
                                      py::cast(notes[i])));
        return out;
    }, py::arg("generators"), py::arg("srcs"), py::arg("dsts"),
       py::arg("n_threads") = 0);

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

    // Non-default rule spec (phase 1, path A — see src/ndr.h): resolved
    // session-side per bundle, charged everywhere via ndr_group_demand.
    py::class_<NdrSpec>(m, "NdrSpec")
        .def(py::init<>())
        .def_readwrite("width_abs",    &NdrSpec::width_abs)
        .def_readwrite("spacing_abs",  &NdrSpec::spacing_abs)
        .def_readwrite("width_slots",  &NdrSpec::width_slots)
        .def_readwrite("guard_slots",  &NdrSpec::guard_slots)
        .def_readwrite("shield_mode",  &NdrSpec::shield_mode)
        .def_readwrite("shield_per_n", &NdrSpec::shield_per_n)
        .def_readwrite("shield_net",   &NdrSpec::shield_net)
        .def_readwrite("credit_shields", &NdrSpec::credit_shields)
        .def_readwrite("bond_stride",    &NdrSpec::bond_stride)
        .def_readwrite("metal_quant",  &NdrSpec::metal_quant)
        .def_readwrite("rule_name",    &NdrSpec::rule_name)
        // READ-ONLY by construction: pybind11 converts std::map BY VALUE, so
        // `spec.per_layer[id] = rule` would mutate a throwaway copy and
        // silently do nothing — a rule that reads as declared and governs
        // nothing.  Mutation goes through set_layer_rule, which cannot.
        .def_property_readonly("per_layer",
                               [](const NdrSpec& s) { return s.per_layer; })
        .def("set_layer_rule",
             [](NdrSpec& s, int layer_id, const NdrLayerRule& r) {
                 s.per_layer[layer_id] = r;
             }, py::arg("layer_id"), py::arg("rule"))
        .def("clear_layer_rules", [](NdrSpec& s) { s.per_layer.clear(); })
        .def("active",                 &NdrSpec::active);

    // R1 per-layer declared values (`def_ndr_layer`).
    py::class_<NdrLayerRule>(m, "NdrLayerRule")
        .def(py::init<>())
        .def_readwrite("width_abs",   &NdrLayerRule::width_abs)
        .def_readwrite("spacing_abs", &NdrLayerRule::spacing_abs)
        .def_readwrite("width_slots", &NdrLayerRule::width_slots)
        .def_readwrite("guard_slots", &NdrLayerRule::guard_slots);

    // R1 metal-shaped quantization: a layer's signal slots as contiguous
    // runs.  `runs` is exposed so a test can build one by hand without a
    // track pattern, and so a report can show what a layer actually offers.
    py::class_<NdrLayerGeom>(m, "NdrLayerGeom")
        .def(py::init<>())
        .def_readwrite("runs",  &NdrLayerGeom::runs)
        .def("empty",           &NdrLayerGeom::empty);

    m.def("ndr_metal_for_slots", &ndr_metal_for_slots, py::arg("geom"),
          py::arg("k"),
          "Metal delivered by the NARROWEST window of k consecutive signal "
          "slots (-1 = no run is that long: the wire would span a rail).");
    m.def("ndr_clearance_for_guards", &ndr_clearance_for_guards,
          py::arg("geom"), py::arg("guards"),
          "Clearance delivered by `guards` guard slots between two bits, "
          "narrowest window (-1 = the period cannot host that many).");
    m.def("ndr_declared_width_on", &ndr_declared_width_on,
          py::arg("spec"), py::arg("layer_id"),
          "The absolute width in force on a layer (0 = none).");
    m.def("ndr_max_slots", &ndr_max_slots, py::arg("geom"),
          "Longest contiguous signal run — the realizability ceiling.");
    m.def("ndr_resolve_on_layer",
          [](const NdrSpec& s, int layer_id, const NdrLayerGeom& g,
             double bit_pitch) {
              bool ok = true;
              NdrSpec o = ndr_resolve_on_layer(s, layer_id, g, bit_pitch, &ok);
              return py::make_tuple(o, ok);
          },
          py::arg("spec"), py::arg("layer_id"), py::arg("geom"),
          py::arg("bit_pitch") = 0.0,
          "Resolve a rule on one layer -> (spec, realizable).");
    m.def("ndr_resolve_for_pitch", &ndr_resolve_for_pitch,
          py::arg("spec"), py::arg("slot_pitch"),
          "R1: quantize a rule's ABSOLUTE width/spacing against one "
          "layer's per-signal-slot pitch, rounding UP.  Identity for a "
          "multiplier-only rule and for a pitch <= 0.");
    m.def("ndr_group_demand", &ndr_group_demand,
          "The single-sourced R4 group demand conversion (slots for a "
          "rule-uniform group of nbits bits)");
    m.def("ndr_run_layout", &ndr_run_layout,
          "Slot-role layout of the ascending run (B/b/S/G), lockstep with "
          "ndr_group_demand");
    m.def("ndr_shield_net_matches", &ndr_shield_net_matches,
          "Shield net-identity predicate (R5a/R9): label electrically "
          "identical to the requested shield net (case-insensitive; "
          "GND/VSS/GROUND one family, VDD/VCC/POWER another)");
    m.def("ndr_group_demand_credited", &ndr_group_demand_credited,
          "R5a-credited group demand: base demand minus credited END "
          "shields (c_lo/c_hi); identity for uncredited/unshielded specs");
    m.def("ndr_run_layout_credited", &ndr_run_layout_credited,
          "R5a-credited run layout: the base layout with credited end 'S' "
          "dropped, lockstep with ndr_group_demand_credited");
    m.def("ndr_rail_credits", &ndr_rail_credits,
          "True when a pattern rail (label, slot type) satisfies the "
          "rule's shield identity for R5a crediting — the ONE predicate "
          "the DNUTS seat search and the R9 audit share");

    py::class_<BundleInput>(m, "BundleInput")
        .def(py::init<>())
        .def_readwrite("ndr",               &BundleInput::ndr)
        .def_readwrite("original_bundle",   &BundleInput::original_bundle)
        // candidates: the getter returns the pool BY VALUE, so Python
        // receives OWNED Topology copies — never element views into the
        // live vector.  The def_readwrite getter (reference_internal, the
        // policy the stl caster propagates to elements) handed out views
        // that (a) dangled across any pool reassignment (the hazard the
        // Topology __copy__ note documents) and (b) left stale entries in
        // pybind's instance registry at freed addresses, so a LATER cast
        // of a fresh temporary Topology landing at a recycled address
        // returned the STALE object instead of the new value — the
        // intermittent topo_uid use-after-free segfault (audit C7-04).
        // Session code already treats the pool as a value (read → mutate →
        // assign back — see e.g. hier._derive_hier_fanin_bits), so
        // semantics are unchanged.
        .def_property("candidates",
            [](const BundleInput& s) { return s.candidates; },
            [](BundleInput& s, std::vector<Topology> v) {
                s.candidates = std::move(v);
            })
        .def_readwrite("width",             &BundleInput::width)
        .def_readwrite("pinned_seg_layers", &BundleInput::pinned_seg_layers)
        .def_readwrite("assigned_v_layer",  &BundleInput::assigned_v_layer)
        .def_readwrite("assigned_h_layer",  &BundleInput::assigned_h_layer)
        .def_readwrite("topology_pinned",   &BundleInput::topology_pinned)
        .def_readwrite("pinned_group",      &BundleInput::pinned_group)
        // Per-cell layer policy, binary form (hier_layer_caps.md Phase 1).
        .def_readwrite("allowed_layers",    &BundleInput::allowed_layers)
        .def_readwrite("layer_cap",         &BundleInput::layer_cap)
        .def_readwrite("layer_floor",       &BundleInput::layer_floor)
        .def("allows_layer",                &BundleInput::allows_layer)
        // Fractional layer shares, Tier-2 state (hier_layer_caps.md Phase 3).
        .def_readwrite("layer_shares",      &BundleInput::layer_shares)
        .def_readwrite("share_group",       &BundleInput::share_group)
        .def_readwrite("share_budgets",     &BundleInput::share_budgets)
        .def("share_of",                    &BundleInput::share_of)
        .def("share_budget_of",             &BundleInput::share_budget_of);

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
        .def_readwrite("hier",  &BundleWrapper::hier)
        // Intent methods (risk_reduction_plan.md R1 step 3): keep the
        // COUPLED fields atomic so Python-side healer/edit code cannot
        // half-write the historical hazard states.  Raw field writes stay
        // legal (tests, tools); session code routes through these — the
        // allowed-writers test pins the discipline.
        .def("pin", [](BundleWrapper& w, int idx) {
            // Pin a candidate: selection + pin move together, and any
            // forced per-segment layers from a PREVIOUS shape are dropped
            // (the unpin hazard — they would apply to the new candidate).
            w.plan.selected_topology_index = idx;
            w.input.topology_pinned = true;
            w.input.pinned_seg_layers.clear();
        }, py::arg("idx"))
        .def("unpin", [](BundleWrapper& w) {
            // Clear the pin AND the forced layers (the coupled pair whose
            // half-clear was the historical unpin hazard).  pinned_group is
            // deliberately NOT touched: sites differ (unpin_topology clears
            // it, negotiate preserves it) — keep that decision explicit at
            // the call site.
            w.input.topology_pinned = false;
            w.input.pinned_seg_layers.clear();
        });

    // C++-backed wrapper container (rnr runtime P2, see bind_opaque.h):
    // element access / iteration return REFERENCES into the vector, and
    // every engine call taking vector<BundleWrapper> accepts it by
    // reference with zero copying.  The session's pipeline holds
    // `session.bundles` as one of these; plain lists still work through
    // the per-function sequence fallbacks (copy semantics, as before).
    // NOTE the bind_vector aliasing contract: structural mutation
    // (append/insert/del) invalidates outstanding element references, so
    // the pipeline only ever REPLACES the container wholesale.
    py::bind_vector<std::vector<BundleWrapper>>(m, "BundleWrapperVec");

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

    // Debug cost-inspection rows (the topology explorer's `debug` view).
    py::class_<CongestionPlanner::SegCost>(m, "SegCost")
        .def_readonly("seg_idx", &CongestionPlanner::SegCost::seg_idx)
        .def_readonly("layer",   &CongestionPlanner::SegCost::layer)
        .def_readonly("cong",    &CongestionPlanner::SegCost::cong)
        .def_readonly("span",    &CongestionPlanner::SegCost::span)
        .def_readonly("non_top", &CongestionPlanner::SegCost::non_top)
        .def_readonly("balance", &CongestionPlanner::SegCost::balance)
        .def_readonly("height",  &CongestionPlanner::SegCost::height)
        .def_readonly("peak",    &CongestionPlanner::SegCost::peak)
        .def_readonly("total",   &CongestionPlanner::SegCost::total);
    py::class_<CongestionPlanner::CandidateCost>(m, "CandidateCost")
        .def_readonly("cand_index", &CongestionPlanner::CandidateCost::cand_index)
        .def_readonly("total",      &CongestionPlanner::CandidateCost::total)
        .def_readonly("seg_cost",   &CongestionPlanner::CandidateCost::seg_cost)
        .def_readonly("wl_term",    &CongestionPlanner::CandidateCost::wl_term)
        .def_readonly("feasible",   &CongestionPlanner::CandidateCost::feasible)
        .def_readonly("segs",       &CongestionPlanner::CandidateCost::segs);

    py::class_<CongestionPlanner>(m, "CongestionPlanner")
        // Planner keeps references to both args (floorplan_/layers_); keep them
        // alive as long as the planner so temporaries are not freed early.
        .def(py::init<const Floorplan&, const LayerStack&>(),
             py::keep_alive<1, 2>(), py::keep_alive<1, 3>())
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
        .def("replan_candidates",    &CongestionPlanner::replan_candidates,
             py::arg("bundles"), py::arg("target_bundle_id"),
             py::arg("tidxs"),
             "Batched screen replan: one committed-usage recharge, then "
             "plan the target pinned to each candidate index (no commits) "
             "— per-candidate assignments identical to a replan_bundle "
             "sequence")
        .def("replan_bundle_ripup",  &CongestionPlanner::replan_bundle_ripup,
             py::arg("bundles"), py::arg("target_bundle_id"))
        .def("inject_band_demand",   &CongestionPlanner::inject_band_demand,
             py::arg("layer_id"), py::arg("span_lo"), py::arg("span_hi"),
             py::arg("perp_lo"), py::arg("perp_hi"), py::arg("amount"))
        .def("clear_injected_demand", &CongestionPlanner::clear_injected_demand)
        .def("extend_grid_for",      &CongestionPlanner::extend_grid_for,
             py::arg("bundles"),
             "Pre-extend the Hanan grid from the bundles' out-of-range "
             "candidate endpoints (idempotent; optimize_topologies runs the "
             "same pass) — call BEFORE inject_band_demand so the injections "
             "survive the optimize run (the extension's cuts rebuild wipes "
             "injected records)")
        .def("recharge_committed",   &CongestionPlanner::recharge_committed,
             py::arg("bundles"))
        .def("candidate_costs",      &CongestionPlanner::candidate_costs,
             py::arg("bundles"), py::arg("target_bundle_id"),
             "Read-only debug scorer: planner cost of every candidate of one "
             "bundle vs the current committed state, with per-segment + WL/seg "
             "breakdown; empty if the planner isn't set up (caller falls back "
             "to the intrinsic wirelength cost)")
        .def("band_occupants",       &CongestionPlanner::band_occupants,
             py::arg("bundles"), py::arg("layer_id"), py::arg("span_lo"),
             py::arg("span_hi"), py::arg("perp_lo"), py::arg("perp_hi"),
             py::arg("top_k"),
             py::arg("placed") = std::vector<std::tuple<int,int,int>>{})
        // Sequence FALLBACKS (P2, bind_opaque.h): the natives above take the
        // zero-copy BundleWrapperVec; plain lists of wrappers land here and
        // keep the historical copy semantics (C++-side vector mutations
        // discarded — the write-back contract is the returned assignments).
        .def("optimize_topologies",
             [](CongestionPlanner& p, py::sequence seq, int iters) {
                 auto v = wrappers_from_seq(seq);
                 return p.optimize_topologies(v, iters);
             })
        .def("replan_bundle",
             [](CongestionPlanner& p, py::sequence seq, int bid) {
                 auto v = wrappers_from_seq(seq);
                 return p.replan_bundle(v, bid);
             }, py::arg("bundles"), py::arg("target_bundle_id"))
        .def("replan_candidates",
             [](CongestionPlanner& p, py::sequence seq, int bid,
                const std::vector<int>& tidxs) {
                 auto v = wrappers_from_seq(seq);
                 return p.replan_candidates(v, bid, tidxs);
             }, py::arg("bundles"), py::arg("target_bundle_id"),
             py::arg("tidxs"))
        .def("replan_bundle_ripup",
             [](CongestionPlanner& p, py::sequence seq, int bid) {
                 auto v = wrappers_from_seq(seq);
                 return p.replan_bundle_ripup(v, bid);
             }, py::arg("bundles"), py::arg("target_bundle_id"))
        .def("extend_grid_for",
             [](CongestionPlanner& p, py::sequence seq) {
                 p.extend_grid_for(wrappers_from_seq(seq));
             }, py::arg("bundles"))
        .def("recharge_committed",
             [](CongestionPlanner& p, py::sequence seq) {
                 p.recharge_committed(wrappers_from_seq(seq));
             }, py::arg("bundles"))
        .def("candidate_costs",
             [](CongestionPlanner& p, py::sequence seq, int bid) {
                 auto v = wrappers_from_seq(seq);
                 return p.candidate_costs(v, bid);
             }, py::arg("bundles"), py::arg("target_bundle_id"))
        .def("band_occupants",
             [](CongestionPlanner& p, py::sequence seq, int layer_id,
                double s_lo, double s_hi, double p_lo, double p_hi,
                int top_k,
                const std::vector<std::tuple<int,int,int>>& placed) {
                 return p.band_occupants(wrappers_from_seq(seq), layer_id,
                                         s_lo, s_hi, p_lo, p_hi, top_k,
                                         placed);
             }, py::arg("bundles"), py::arg("layer_id"), py::arg("span_lo"),
             py::arg("span_hi"), py::arg("perp_lo"), py::arg("perp_hi"),
             py::arg("top_k"),
             py::arg("placed") = std::vector<std::tuple<int,int,int>>{})
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
