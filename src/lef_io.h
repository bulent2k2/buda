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

// lef_io.h — LEF reader (Phase 2 of docs/internal/lefdef_interface_plan.md).
//
// Self-contained, in its own translation unit, following the gds_io.cpp
// precedent and BDB-agnostic by design: this produces a `LefLibrary` and
// nothing else, so it can be tested — and reused by the floorplanner or a
// tech-setup path — without a database.
//
// It replaces two line-oriented scanners in bdb.cpp that recognised exactly
// MACRO / SIZE / PIN / DIRECTION / USE / RECT and ignored everything else.
// The point is not merely to read MORE: it is that what cannot be honoured is
// RECORDED (`LefLibrary::unmodelled`) instead of vanishing, so the distance
// between the file and the model is visible rather than assumed.
//
// All distances are in MICRONS, as the file states them.  Converting to the
// design's layout units is the caller's job (docs/internal/engine_units.md) —
// this reader has no opinion about what a layout unit is.

#pragma once

#include <string>
#include <vector>

namespace buda {

struct LefRect {                       // µm, in the macro's own coordinates
    double x1 = 0, y1 = 0, x2 = 0, y2 = 0;
};

// One PORT of a pin: the shapes on one or more layers.  A pin may have
// several ports (physically separate landings of the same electrical node),
// which the old scanner collapsed by averaging every RECT it saw.
struct LefPort {
    std::string layer;                 // last LAYER seen for these shapes
    std::vector<LefRect> rects;
};

struct LefPinDef {
    std::string name;
    std::string dir;                   // INPUT / OUTPUT / INOUT / "" (unstated)
    std::string use;                   // SIGNAL / POWER / GROUND / CLOCK / ""
    std::string shape;                 // ABUTMENT / RING / FEEDTHRU / ""
    std::vector<LefPort> ports;

    // Centroid of every port RECT, in macro coordinates.  `has_geometry` is
    // false when the pin declared no shapes at all — distinct from a pin
    // whose centroid happens to be the origin.
    bool centroid(double& x, double& y) const;
    bool has_geometry() const;
};

struct LefMacro {
    std::string name;
    std::string cls;                   // CLASS (CORE / BLOCK / PAD / …)
    double w = 0, h = 0;               // SIZE … BY …
    // ORIGIN: the offset from the macro's placement point to its geometry
    // origin.  Pin and obstruction coordinates are relative to the GEOMETRY
    // origin, so a non-zero ORIGIN shifts every landing — which the old
    // scanner ignored, putting every pin of such a macro in the wrong place.
    double ox = 0, oy = 0;
    std::string symmetry;              // "X Y R90" etc., verbatim
    std::string site;
    std::string foreign;               // FOREIGN cell name, if any
    std::vector<LefPinDef> pins;
    std::vector<LefPort> obs;          // OBS shapes (routing obstructions)

    const LefPinDef* find_pin(const std::string& n) const;
};

// A technology LAYER.  Read now, consumed by Phase 2b; `type` decides what
// the rest means (ROUTING carries pitch/width/spacing, CUT does not).
struct LefTechLayer {
    std::string name;
    std::string type;                  // ROUTING / CUT / MASTERSLICE / OVERLAP
    std::string dir;                   // HORIZONTAL / VERTICAL / "" (unstated)
    double pitch = 0, width = 0, spacing = 0, offset = 0, area = 0;
    bool has_pitch = false, has_width = false, has_spacing = false;
    bool has_offset = false, has_area = false;
};

// Something the file said that the model cannot represent.  Kept as data —
// with the line it came from — because "we ignored it" and "it wasn't there"
// must not look the same to whoever debugs the result later.
struct LefUnmodelled {
    std::string construct;             // e.g. "LAYER.SPACINGTABLE"
    std::string detail;                // the statement, joined
    int line = 0;
};

struct LefLibrary {
    double units_dbu = 0;              // UNITS DATABASE MICRONS (0 = unstated)
    double manufacturing_grid = 0;     // MANUFACTURINGGRID (0 = unstated)
    std::vector<LefMacro> macros;
    std::vector<LefTechLayer> layers;
    std::vector<LefUnmodelled> unmodelled;
    std::vector<std::string> warnings;

    const LefMacro* find_macro(const std::string& n) const;
};

// Parse `path`.  Throws std::runtime_error with file:line on malformed
// input — a technology file that cannot be read must stop the run, not
// produce a partial library that looks complete.
LefLibrary read_lef(const std::string& path);

// Same, for text already in memory (the test path, and any caller that has
// the bytes already).  `where` names the source in error messages.
LefLibrary parse_lef(const std::string& text, const std::string& where);

}  // namespace buda
