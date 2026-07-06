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

#pragma once
#include "conn_topology.h"
#include <cstdint>
#include <memory>
#include <utility>
#include <vector>

// ── Topology analysis passes ─────────────────────────────────────────────────
//
// The six derivation passes behind ConnTopology::build, extracted as named,
// individually re-runnable units (Phase A of
// docs/internal/topo_conn_unification.md).  Each is a pure function of
// (Topology, Floorplan) mutating the shared ConnSeg vector; build() drives
// them in this exact order:
//
//   derive_conn_segs      geometry re-encode + connection inference (reads the
//                         authoritative seg_busterms / seg_conns annotations —
//                         never geometry)
//   derive_slide_ranges   busterm-anchored perp slide windows (2-pass fixpoint)
//   tighten_passthrough   pass-through block coverage clamps
//   pin_relay_taps        OTC windows for relay JOG/extension connectors
//   derive_net_pull       signed perpendicular placement preference
//   derive_along_flex     along-flex DOF annotation (flags/cover/pull)
//
// The uniform (topo, fp, segs) signature is deliberate — later phases re-run
// individual passes on a dirty subset, so the driver and the incremental path
// share one shape.  ConnTopology (conn_topology.h) remains the frozen consumer
// facade; nothing outside it needs to call these directly today.

namespace buda {

// ── Cached analysis (Phase B) ────────────────────────────────────────────────
//
// The result of the six passes, cached on the Topology itself
// (Topology::analysis_cache_).  Validation is by CONTENT: `topo_fp` is a
// fingerprint of every Topology field the passes read (segments, seg_busterms,
// seg_conns, connected_block_names — plus feedthru/bridges for future passes),
// and (fp_uid, fp_rev) names the exact Floorplan state (identity + revision;
// all Floorplan mutation is behind methods, so its revision is airtight).
// analyze() re-fingerprints on every call and recomputes on any mismatch —
// a stale cache is structurally impossible no matter who mutates what, from
// C++ or Python.  The result is immutable and shared (consumers hold a
// shared_ptr snapshot, preserving today's "built ct survives later topology
// mutation" semantics).
struct TopoAnalysis {
    std::vector<ConnSeg> segs;
    uint64_t topo_fp = 0;
    uint64_t fp_uid  = 0;
    uint64_t fp_rev  = 0;
};

// The one entry point consumers need: return the cached analysis if it is
// current, else run the six passes and cache.  ConnTopology::build is a thin
// wrapper over this.
std::shared_ptr<const TopoAnalysis> analyze(const Topology& topo,
                                            const Floorplan& fp);

// Content fingerprint of the analysis inputs (FNV-1a over the fields named
// above).  Also the natural precursor of Phase E1's persisted topo_uid.
uint64_t topology_fingerprint(const Topology& topo);

// Instrumentation for the Phase B/C tests and perf smokes: cumulative
// (computes, hits) across the process, and a reset.  Not part of the routing
// API — a cache with different counters is still byte-identical routing.
std::pair<uint64_t, uint64_t> analysis_cache_counters();
void analysis_cache_reset_counters();

void derive_conn_segs   (const Topology& topo, const Floorplan& fp,
                         std::vector<ConnSeg>& segs);
void derive_slide_ranges(const Topology& topo, const Floorplan& fp,
                         std::vector<ConnSeg>& segs);
void tighten_passthrough(const Topology& topo, const Floorplan& fp,
                         std::vector<ConnSeg>& segs);
void pin_relay_taps     (const Topology& topo, const Floorplan& fp,
                         std::vector<ConnSeg>& segs);
void derive_net_pull    (const Topology& topo, const Floorplan& fp,
                         std::vector<ConnSeg>& segs);
void derive_along_flex  (const Topology& topo, const Floorplan& fp,
                         std::vector<ConnSeg>& segs);

} // namespace buda
