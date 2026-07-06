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
