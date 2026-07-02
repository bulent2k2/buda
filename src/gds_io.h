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
// gds_io.h — GDSII stream import (Phase G1 of docs/internal/gds_oa_interchange.md).
// A self-contained, hand-written binary reader in its own translation unit
// (the repo's importer pattern — no external EDA library), populating the same
// BDB tables as import_def_lef/import_verilog, coordinates normalized to µm.

#include <string>
#include <vector>

namespace buda {

class BDB;

struct GdsImportStats {
    int n_structures = 0;        // structures in the library
    int n_cells      = 0;        // cell rows written
    int n_components = 0;        // component rows written (elaborated)
    int n_texts      = 0;        // TEXT records seen (consumed in Phase G2)
    std::vector<std::string> tops;   // unreferenced structures (roots)
    std::vector<std::string> warnings;
};

// Import a GDSII stream file into the BDB: structures -> cell rows (footprint
// = recursive geometry bbox), SREF/AREF placements -> component hierarchy
// (absolute µm bboxes, dotted paths, growing depth). Fresh load: clears
// pin/net_props/net/component/cell first, like import_def_lef. Throws
// std::runtime_error on malformed input.
GdsImportStats import_gds(BDB& db, const std::string& path);

}  // namespace buda
