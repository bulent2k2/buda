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
// gds_io.h — GDSII stream import + export
// (Phases G1-G4 of docs/internal/gds_oa_interchange.md).
// Self-contained, hand-written binary reader/writer in its own translation
// unit (the repo's importer pattern — no external EDA library), populating /
// streaming the same BDB tables as import_def_lef/import_verilog,
// coordinates normalized to µm.

#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace buda {

class BDB;

struct GdsImportStats {
    int n_structures = 0;        // structures in the library
    int n_cells      = 0;        // cell rows written
    int n_components = 0;        // component rows written (elaborated)
    int n_texts      = 0;        // TEXT records seen
    int n_nets       = 0;        // nets recovered from TEXT labels (Phase G2)
    int n_pins       = 0;        // label pins attached to components
    int n_labels_skipped = 0;    // labels outside every component / filtered
    int n_routing_shapes = 0;    // shapes on mapped routing layers, excluded
                                 // from cell footprints (Phase G3)
    std::vector<std::string> tops;   // unreferenced structures (roots)
    std::vector<std::string> warnings;
};

// Import a GDSII stream file into the BDB: structures -> cell rows (footprint
// = recursive geometry bbox), SREF/AREF placements -> component hierarchy
// (absolute µm bboxes, dotted paths, growing depth), and TEXT labels ->
// net/pin rows (Phase G2: each label string is a net; the pin lands on the
// DEEPEST component containing the label's elaborated position; labels flow
// through the hierarchy transforms like geometry). `label_layers` filters
// which GDS layers carry labels (empty = every TEXT record is a label).
// `routing_layers` (Phase G3) lists GDS (layer, datatype) pairs that carry
// routing wires — BOUNDARY/BOX/PATH shapes on those pairs are counted but
// excluded from cell footprints, so re-importing a routed GDS keeps macro
// outlines clean (the export->import round-trip requirement).
// Fresh load: clears the design tables first, like import_def_lef. Throws
// std::runtime_error on malformed input.
GdsImportStats import_gds(BDB& db, const std::string& path,
                          const std::vector<int>& label_layers = {},
                          const std::vector<std::pair<int,int>>& routing_layers = {});

struct GdsExportStats {
    int n_structures = 0;        // structures written (cells + top)
    int n_placements = 0;        // SREF placements written
    int n_wire_shapes = 0;       // routing rectangles (net_segment / bus_segment)
    int n_via_shapes = 0;        // via squares (net_via / bus_via)
    int n_labels = 0;            // TEXT labels (one per pin row)
    std::string stage;           // "detailed_nuts", "abstract_nuts", "" = no routing
    std::vector<std::string> warnings;
};

// Export the BDB as a GDSII stream file (Phase G4) — the reverse of
// import_gds, streaming from the persisted tables so it works on a reopened
// checkpoint with no live pipeline. Deterministic output (zeroed timestamps,
// sorted iteration): identical DBs give identical bytes.
//
// - Each `cell` row becomes a structure: outline BOUNDARY (0,0,w,h) on
//   (outline_layer, 0) + SREF children reconstructed from its first component
//   instance (relative offsets; PROPVALUE carries the child instance name).
// - A synthetic top structure places every root component via SREF, draws the
//   die extent on the outline layer, and carries the routing + labels:
//   `net_segment` bit-wire rectangles as BOUNDARY on each layer's mapped
//   (gds_layer, datatype) pair — falling back to abstract `bus_segment`
//   rectangles when detailed rows are absent — `net_via`/`bus_via` rows as
//   via_size squares on the upper layer's pair, and one TEXT label per `pin`
//   row (net name at the pin position) on (label_layer, 0) unless
//   write_labels is false.
// - layer_map entries are (buda_layer, gds_layer, gds_datatype) — the
//   LayerStack's def_gds_layer bindings. An unmapped routing layer falls back
//   to (buda_layer, 0) with a warning.
//
// Re-importing the file with the same def_gds_layer map recovers the design:
// wires are excluded from footprints (Phase G3), labels recover nets/pins
// (Phase G2; pin direction is not representable — dirs come back UNKNOWN).
GdsExportStats export_gds(BDB& db, const std::string& path,
                          const std::vector<std::tuple<int,int,int>>& layer_map = {},
                          int outline_layer = 10,
                          int label_layer = 63,
                          bool write_labels = true,
                          double via_size = 1.0);

}  // namespace buda
