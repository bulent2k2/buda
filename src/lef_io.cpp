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

// lef_io.cpp — recursive-descent LEF reader.  See lef_io.h for the contract
// and docs/internal/lefdef_interface_plan.md §3 for where it sits.

#include "lef_io.h"

#include <algorithm>
#include <fstream>
#include <sstream>
#include <stdexcept>

#include "lefdef_lex.h"

namespace buda {

namespace {

std::string upper(std::string s) {
    for (char& c : s) c = static_cast<char>(std::toupper(
        static_cast<unsigned char>(c)));
    return s;
}

// LEF block bodies are terminated by `END` (optionally naming the block).  A
// statement-oriented loop needs to know when to stop WITHOUT consuming the
// END of an enclosing block, so every body loop routes through this.
bool at_end(LefDefLexer& lx) { return upper(lx.peek()) == "END"; }

// Consume `END [name]`.  LEF allows the name to be omitted (PORT, OBS) and
// requires it elsewhere (MACRO, PIN); when present it must match, because a
// mismatched END is the signature of a truncated or spliced file and reading
// on would attribute the next macro's pins to this one.
void take_end(LefDefLexer& lx, const std::string& block,
              const std::string& name) {
    const std::string kw = lx.expect("END of " + block);
    if (upper(kw) != "END")
        lx.fail("expected END of " + block + " '" + name + "', got '" + kw + "'");
    if (name.empty()) return;
    if (upper(lx.peek()) == upper(name)) { std::string t; lx.next(t); }
}

void note(LefLibrary& lib, const std::string& construct,
          const std::vector<std::string>& toks, int line) {
    lib.unmodelled.push_back({construct, LefDefLexer::join(toks), line});
}

// Skip a block whose contents the model cannot hold, recording it once.
//
// The one hazard is that LEF spells a nested BLOCK and a plain STATEMENT with
// the same keyword — `LAYER m1 … END m1` inside a NONDEFAULTRULE versus
// `LAYER m1 ;` inside a VIARULE.  Counting bare `END` tokens would therefore
// end the outer block early and hand the rest of the file to the top-level
// loop as garbage.  So an opener is resolved the only way it can be: consume
// the keyword and its name, then look at what follows — `;` means it was a
// statement, anything else means a block, and that recurses.
void skip_block(LefDefLexer& lx, LefLibrary& lib, const std::string& construct,
                const std::string& name) {
    const int line = lx.line();
    std::vector<std::string> st;
    std::string t;
    for (;;) {
        const std::string& la = lx.peek();
        if (la.empty())
            lx.fail("unterminated " + construct +
                    (name.empty() ? "" : " '" + name + "'"));
        const std::string u = upper(la);
        if (u == "END") { take_end(lx, construct, name); break; }
        if (u == "LAYER" || u == "VIA" || u == "VIARULE" ||
            u == "NONDEFAULTRULE" || u == "PROPERTY") {
            lx.next(t);                                   // the keyword
            const std::string inner = lx.expect(u + " name");
            if (lx.peek() == ";") { lx.next(t); continue; }   // statement
            skip_block(lx, lib, u, inner);                    // nested block
            continue;
        }
        if (!lx.statement(st))
            lx.fail("unterminated " + construct);
    }
    lib.unmodelled.push_back({construct, name, line});
}

// ── PORT / OBS geometry ────────────────────────────────────────────────────
// A PORT body is a sequence of `LAYER <name> ;` / `RECT x1 y1 x2 y2 ;` /
// `POLYGON …;` / `PATH …;` statements, ended by `END`.
std::vector<LefPort> read_geometry(LefDefLexer& lx, LefLibrary& lib,
                                   const std::string& construct) {
    std::vector<LefPort> ports;
    LefPort cur;
    std::vector<std::string> st;
    while (!at_end(lx)) {
        const int line = lx.line();
        if (!lx.statement(st)) lx.fail("unterminated " + construct);
        if (st.empty()) continue;
        const std::string kw = upper(st[0]);
        if (kw == "LAYER" && st.size() >= 2) {
            // A new LAYER starts a new shape group; flush what we have so a
            // pin's landings stay attributable to the layer they are on.
            if (!cur.rects.empty()) ports.push_back(cur);
            cur = LefPort{};
            cur.layer = st[1];
        } else if (kw == "RECT" && st.size() >= 5) {
            // Optional MASK/ITERATE prefixes push the numbers along; take the
            // LAST four tokens, which are the coordinates in every form.
            const size_t n = st.size();
            try {
                LefRect r{std::stod(st[n - 4]), std::stod(st[n - 3]),
                          std::stod(st[n - 2]), std::stod(st[n - 1])};
                if (r.x1 > r.x2) std::swap(r.x1, r.x2);
                if (r.y1 > r.y2) std::swap(r.y1, r.y2);
                cur.rects.push_back(r);
            } catch (const std::exception&) {
                lx.fail("malformed RECT: " + LefDefLexer::join(st));
            }
        } else if (kw == "POLYGON" || kw == "PATH" || kw == "VIA") {
            // Real geometry the Rect-based model cannot hold.  Recorded, and
            // its bounding box is NOT invented: a pin whose only shape is a
            // polygon is better reported missing than placed approximately.
            note(lib, construct + "." + kw, st, line);
        } else {
            note(lib, construct + "." + kw, st, line);
        }
    }
    if (!cur.rects.empty() || !cur.layer.empty()) ports.push_back(cur);
    return ports;
}

// ── PIN ────────────────────────────────────────────────────────────────────
LefPinDef read_pin(LefDefLexer& lx, LefLibrary& lib, const std::string& name) {
    LefPinDef pin;
    pin.name = name;
    std::vector<std::string> st;
    for (;;) {
        const std::string& la = lx.peek();
        if (la.empty()) lx.fail("unterminated PIN '" + name + "'");
        const std::string u = upper(la);
        if (u == "END") { take_end(lx, "PIN", name); break; }
        if (u == "PORT") {
            std::string t; lx.next(t);                 // PORT (no ';')
            auto ports = read_geometry(lx, lib, "PIN.PORT");
            take_end(lx, "PORT", "");
            for (auto& p : ports) pin.ports.push_back(std::move(p));
            continue;
        }
        const int line = lx.line();
        if (!lx.statement(st)) lx.fail("unterminated PIN '" + name + "'");
        if (st.empty()) continue;
        const std::string kw = upper(st[0]);
        if (kw == "DIRECTION" && st.size() >= 2)       pin.dir   = upper(st[1]);
        else if (kw == "USE" && st.size() >= 2)        pin.use   = upper(st[1]);
        else if (kw == "SHAPE" && st.size() >= 2)      pin.shape = upper(st[1]);
        else if (kw == "ANTENNAMODEL" || kw.rfind("ANTENNA", 0) == 0) {
            note(lib, "PIN.ANTENNA", st, line);        // router path only
        } else {
            note(lib, "PIN." + kw, st, line);
        }
    }
    return pin;
}

// ── MACRO ──────────────────────────────────────────────────────────────────
LefMacro read_macro(LefDefLexer& lx, LefLibrary& lib, const std::string& name) {
    LefMacro m;
    m.name = name;
    std::vector<std::string> st;
    for (;;) {
        const std::string& la = lx.peek();
        if (la.empty()) lx.fail("unterminated MACRO '" + name + "'");
        const std::string u = upper(la);
        if (u == "END") { take_end(lx, "MACRO", name); break; }
        if (u == "PIN") {
            std::string t; lx.next(t);                 // PIN
            const std::string pname = lx.expect("PIN name");
            m.pins.push_back(read_pin(lx, lib, pname));
            continue;
        }
        if (u == "OBS") {
            std::string t; lx.next(t);                 // OBS
            auto obs = read_geometry(lx, lib, "OBS");
            take_end(lx, "OBS", "");
            for (auto& p : obs) m.obs.push_back(std::move(p));
            continue;
        }
        const int line = lx.line();
        if (!lx.statement(st)) lx.fail("unterminated MACRO '" + name + "'");
        if (st.empty()) continue;
        const std::string kw = upper(st[0]);
        if (kw == "SIZE") {
            // `SIZE <w> BY <h>` — take the numbers, not fixed positions, so
            // spacing around BY cannot matter.
            std::vector<double> nums;
            for (size_t i = 1; i < st.size(); ++i) {
                try {
                    size_t used = 0;
                    const double v = std::stod(st[i], &used);
                    if (used == st[i].size()) nums.push_back(v);
                } catch (const std::exception&) { /* 'BY' */ }
            }
            if (nums.size() < 2)
                lx.fail("malformed SIZE: " + LefDefLexer::join(st));
            m.w = nums[0];
            m.h = nums[1];
        } else if (kw == "ORIGIN" && st.size() >= 3) {
            try {
                m.ox = std::stod(st[1]);
                m.oy = std::stod(st[2]);
            } catch (const std::exception&) {
                lx.fail("malformed ORIGIN: " + LefDefLexer::join(st));
            }
        } else if (kw == "CLASS" && st.size() >= 2) {
            m.cls = upper(st[1]);
            for (size_t i = 2; i < st.size(); ++i) m.cls += " " + upper(st[i]);
        } else if (kw == "SYMMETRY") {
            m.symmetry = LefDefLexer::join(
                std::vector<std::string>(st.begin() + 1, st.end()));
        } else if (kw == "SITE" && st.size() >= 2) {
            m.site = st[1];
        } else if (kw == "FOREIGN" && st.size() >= 2) {
            m.foreign = st[1];
        } else {
            note(lib, "MACRO." + kw, st, line);
        }
    }
    return m;
}

// ── tech LAYER ─────────────────────────────────────────────────────────────
LefTechLayer read_layer(LefDefLexer& lx, LefLibrary& lib,
                        const std::string& name) {
    LefTechLayer l;
    l.name = name;
    std::vector<std::string> st;
    for (;;) {
        const std::string& la = lx.peek();
        if (la.empty()) lx.fail("unterminated LAYER '" + name + "'");
        if (upper(la) == "END") { take_end(lx, "LAYER", name); break; }
        const int line = lx.line();
        if (!lx.statement(st)) lx.fail("unterminated LAYER '" + name + "'");
        if (st.empty()) continue;
        const std::string kw = upper(st[0]);
        auto num = [&](double& dst, bool& flag) {
            if (st.size() < 2) lx.fail("missing value: " + LefDefLexer::join(st));
            try {
                dst = std::stod(st[1]);
                flag = true;
            } catch (const std::exception&) {
                lx.fail("malformed " + kw + ": " + LefDefLexer::join(st));
            }
        };
        if (kw == "TYPE" && st.size() >= 2)             l.type = upper(st[1]);
        else if (kw == "DIRECTION" && st.size() >= 2)   l.dir  = upper(st[1]);
        else if (kw == "PITCH")   num(l.pitch,   l.has_pitch);
        else if (kw == "WIDTH")   num(l.width,   l.has_width);
        else if (kw == "OFFSET")  num(l.offset,  l.has_offset);
        else if (kw == "AREA")    num(l.area,    l.has_area);
        else if (kw == "SPACING" && st.size() == 2)     num(l.spacing, l.has_spacing);
        else {
            // SPACING with a qualifier (RANGE/ENDOFLINE/…), SPACINGTABLE,
            // MINENCLOSEDAREA, cut rules: real constraints with no home in
            // the current model.  Recorded per lef_io.h.
            note(lib, "LAYER." + kw, st, line);
        }
    }
    return l;
}

}  // namespace

// ── LefPinDef / lookups ────────────────────────────────────────────────────

bool LefPinDef::has_geometry() const {
    for (const auto& p : ports)
        if (!p.rects.empty()) return true;
    return false;
}

bool LefPinDef::centroid(double& x, double& y) const {
    double sx = 0, sy = 0;
    size_t n = 0;
    for (const auto& p : ports)
        for (const auto& r : p.rects) {
            sx += (r.x1 + r.x2) / 2.0;
            sy += (r.y1 + r.y2) / 2.0;
            ++n;
        }
    if (n == 0) return false;
    x = sx / static_cast<double>(n);
    y = sy / static_cast<double>(n);
    return true;
}

const LefPinDef* LefMacro::find_pin(const std::string& n) const {
    for (const auto& p : pins) if (p.name == n) return &p;
    return nullptr;
}

const LefMacro* LefLibrary::find_macro(const std::string& n) const {
    for (const auto& m : macros) if (m.name == n) return &m;
    return nullptr;
}

// ── entry points ───────────────────────────────────────────────────────────

LefLibrary parse_lef(const std::string& text, const std::string& where) {
    LefLibrary lib;
    LefDefLexer lx(text, where);
    std::vector<std::string> st;
    for (;;) {
        const std::string& la = lx.peek();
        if (la.empty()) break;
        const std::string u = upper(la);
        std::string t;
        if (u == "MACRO") {
            lx.next(t);
            const std::string name = lx.expect("MACRO name");
            lib.macros.push_back(read_macro(lx, lib, name));
            continue;
        }
        if (u == "LAYER") {
            lx.next(t);
            const std::string name = lx.expect("LAYER name");
            lib.layers.push_back(read_layer(lx, lib, name));
            continue;
        }
        if (u == "UNITS") {
            // A BLOCK (`UNITS … END UNITS`), not a statement: `UNITS` itself
            // carries no `;`, so reading it as one swallows the `DATABASE
            // MICRONS <n> ;` line that holds the only number we want.
            lx.next(t);
            while (!at_end(lx)) {
                const int uline = lx.line();
                if (!lx.statement(st)) lx.fail("unterminated UNITS");
                if (st.empty()) continue;
                if (upper(st[0]) == "DATABASE" && st.size() >= 3 &&
                    upper(st[1]) == "MICRONS") {
                    try { lib.units_dbu = std::stod(st[2]); }
                    catch (const std::exception&) {
                        lx.fail("malformed DATABASE MICRONS: " +
                                LefDefLexer::join(st));
                    }
                } else {
                    // TIME / CAPACITANCE / RESISTANCE / POWER / …: real, and
                    // meaningless to a geometry model.
                    note(lib, "UNITS." + upper(st[0]), st, uline);
                }
            }
            take_end(lx, "UNITS", "UNITS");
            continue;
        }
        if (u == "VIA" || u == "VIARULE" || u == "SITE" ||
            u == "NONDEFAULTRULE" || u == "PROPERTYDEFINITIONS" ||
            u == "SPACING" || u == "ARRAY" || u == "BEGINEXT") {
            // Blocks Phase 2 does not model (VIA/VIARULE are explicitly
            // deferred to the router path, §0 of the plan).  Skipped as a
            // unit and recorded, never guessed at.
            lx.next(t);
            std::string name;
            if (!lx.peek().empty() && upper(lx.peek()) != "END" &&
                lx.peek() != ";") {
                lx.next(name);
            }
            // `SPACING … ;` also exists as a bare statement at top level;
            // only the block form reaches skip_block.
            if (lx.peek() == ";") { lx.next(t); note(lib, u, {u, name}, lx.line()); }
            else skip_block(lx, lib, u, name);
            continue;
        }
        if (u == "END") {
            // END LIBRARY (or a stray END): consume it and whatever names it.
            lx.next(t);
            if (!lx.peek().empty() && upper(lx.peek()) == "LIBRARY") lx.next(t);
            continue;
        }
        const int line = lx.line();
        if (!lx.statement(st)) break;
        if (st.empty()) continue;
        const std::string kw = upper(st[0]);
        if (kw == "MANUFACTURINGGRID" && st.size() >= 2) {
            try { lib.manufacturing_grid = std::stod(st[1]); }
            catch (const std::exception&) {
                lx.fail("malformed MANUFACTURINGGRID: " +
                        LefDefLexer::join(st));
            }
        } else if (kw == "VERSION" || kw == "BUSBITCHARS" ||
                   kw == "DIVIDERCHAR" || kw == "NAMESCASESENSITIVE" ||
                   kw == "CLEARANCEMEASURE" || kw == "USEMINSPACING") {
            // Header statements with no geometric consequence here.
        } else {
            note(lib, kw, st, line);
        }
    }
    return lib;
}

LefLibrary read_lef(const std::string& path) {
    std::ifstream f(path);
    if (!f) throw std::runtime_error("read_lef: cannot open " + path);
    std::ostringstream ss;
    ss << f.rdbuf();
    return parse_lef(ss.str(), path);
}

}  // namespace buda
