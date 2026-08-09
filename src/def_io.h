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

// def_io.h — DEF reader (Phase 3 of docs/internal/lefdef_interface_plan.md).
//
// The sibling of lef_io.h, on the same token layer and with the same contract:
// self-contained, BDB-agnostic, and explicit about what it cannot model.
//
// It replaces a three-state line-at-a-time `std::regex` machine that read
// UNITS / DIEAREA / COMPONENTS / NETS and discarded everything else — on
// `demo/ariane/ariane.def` that is 20 TRACKS statements, 6 GCELLGRIDs, 495
// PINS and every SPECIALNET, silently.  Line-at-a-time also meant a legal
// multi-line COMPONENTS entry was skipped rather than read, and per-line
// std::regex does not scale to the 10^6..10^8 lines of a real post-place DEF.
//
// COORDINATES ARE DEF DATABASE UNITS, exactly as the file states them.
// Converting to the design's layout units is the caller's job
// (docs/internal/engine_units.md); this reader has no opinion about what a
// layout unit is.

#pragma once

#include <string>
#include <vector>

namespace buda {

struct DefRect { double x1 = 0, y1 = 0, x2 = 0, y2 = 0; };   // DBU

struct DefComponent {
    std::string name;
    std::string cell;
    std::string orient = "N";
    double x = 0, y = 0;
    bool placed = false;          // PLACED / FIXED / COVER seen
    // HALO l b r t — a keep-clear margin around the instance.  Optional, and
    // distinct from a zero halo.
    bool has_halo = false;
    double halo_l = 0, halo_b = 0, halo_r = 0, halo_t = 0;
};

struct DefNetConn {
    std::string inst;             // "PIN" for a die port (DEF's own spelling)
    std::string pin;
    bool is_port() const { return inst == "PIN"; }
};

struct DefNet {
    std::string name;
    std::string use;              // SIGNAL / POWER / GROUND / CLOCK / ""
    std::string nondefaultrule;   // + NONDEFAULTRULE <name>
    std::vector<DefNetConn> conns;
};

// A top-level port.  These are what a net reaching the die edge connects to,
// and the reader this replaces skipped them outright.
struct DefPin {
    std::string name;
    std::string net;
    std::string dir;              // INPUT / OUTPUT / INOUT / ""
    std::string use;              // SIGNAL / POWER / …
    std::string layer;            // last + LAYER seen
    std::string orient = "N";
    bool placed = false;
    double x = 0, y = 0;          // PLACED point
    std::vector<DefRect> rects;   // port shapes, relative to (x, y)
};

// `TRACKS X <start> DO <count> STEP <step> LAYER <l> …` — a FINITE set of
// tracks, which is why TrackPattern grew a bounded form (Phase 3b): tiling a
// repeating pattern outward past the declared range invents tracks the
// technology does not have, silently and in the direction of optimism.
struct DefTracks {
    std::string dir;              // "X" (vertical tracks) or "Y" (horizontal)
    double start = 0;
    int count = 0;
    double step = 0;
    std::vector<std::string> layers;
    double last() const { return start + step * (count > 0 ? count - 1 : 0); }
};

struct DefGCellGrid {
    std::string dir;              // "X" / "Y"
    double start = 0;
    int count = 0;
    double step = 0;
};

struct DefBlockage {
    std::string layer;            // "" for a PLACEMENT blockage
    bool is_placement = false;
    bool has_density = false;
    double max_density = 0;       // + PARTIAL <maxDensity>
    std::vector<DefRect> rects;
};

// A routed SPECIALNETS wire — a power/ground strap.  Real metal that a
// signal cannot use, i.e. a keepout.
struct DefSpecialWire {
    std::string net;
    std::string layer;
    double width = 0;
    std::vector<std::pair<double, double>> pts;   // polyline, DBU
};

struct DefUnmodelled {
    std::string construct;
    std::string detail;
    int line = 0;
};

struct DefDesign {
    std::string design;
    int units = 0;                // UNITS DISTANCE MICRONS (0 = unstated)
    bool has_die = false;
    DefRect die;

    std::vector<DefComponent> components;
    std::vector<DefNet> nets;
    std::vector<DefPin> pins;
    std::vector<DefTracks> tracks;
    std::vector<DefGCellGrid> gcellgrid;
    std::vector<DefBlockage> blockages;
    std::vector<DefSpecialWire> special_wires;
    std::vector<std::string> nondefaultrules;

    // What the section headers CLAIMED.  DEF states its own counts, so a
    // reader that ignores them cannot tell a fully-read file from a
    // half-read one — and a half-read floorplan is still a floorplan.
    int declared_components = -1, declared_nets = -1, declared_pins = -1;
    int declared_blockages = -1, declared_specialnets = -1;

    std::vector<DefUnmodelled> unmodelled;
    std::vector<std::string> warnings;
};

// Parse `path`.  Throws std::runtime_error with file:line on malformed input.
DefDesign read_def(const std::string& path);
DefDesign parse_def(const std::string& text, const std::string& where);

}  // namespace buda
