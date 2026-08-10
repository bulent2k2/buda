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

// def_io.cpp — recursive-descent DEF reader.  See def_io.h for the contract.
//
// DEF is a `;`-terminated statement language, so unlike LEF it fits the
// lexer's statement() view almost everywhere: a section is `KEYWORD <n> ;`,
// then entries beginning with `-`, then `END KEYWORD`.  Entries may wrap
// across any number of lines, which is exactly what the line-at-a-time
// reader this replaces could not do.

#include "def_io.h"

#include <algorithm>
#include <cctype>
#include <functional>
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

// DEF escapes `[`/`]` (and other specials) with a backslash inside
// hierarchical names.  The lexer keeps the escape so the caller decides; the
// BDB stores unescaped names, and a token that misses its lookup is a
// connection SILENTLY dropped, so this must be applied consistently.
std::string unescape(const std::string& s) {
    std::string out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '\\' && i + 1 < s.size()) { out += s[++i]; continue; }
        out += s[i];
    }
    return out;
}

bool is_number(const std::string& s, double& v) {
    if (s.empty()) return false;
    try {
        size_t used = 0;
        v = std::stod(s, &used);
        return used == s.size();
    } catch (const std::exception&) {
        return false;
    }
}

void note(DefDesign& d, const std::string& construct,
          const std::vector<std::string>& toks, int line) {
    d.unmodelled.push_back({construct, LefDefLexer::join(toks), line});
}

// `( x y )` starting at `i`; advances `i` past the closing paren.
bool take_point(const std::vector<std::string>& t, size_t& i,
                double& x, double& y) {
    if (i >= t.size() || t[i] != "(") return false;
    size_t j = i + 1;
    double vx = 0, vy = 0;
    // `*` means "same as the previous point" in a wire polyline; the caller
    // handles it, so report the token position rather than guessing here.
    if (j >= t.size() || !is_number(t[j], vx)) return false;
    ++j;
    if (j >= t.size() || !is_number(t[j], vy)) return false;
    ++j;
    while (j < t.size() && t[j] != ")") ++j;      // EXTVALUE and friends
    if (j >= t.size()) return false;
    i = j + 1;
    x = vx; y = vy;
    return true;
}

int declared_count(const std::vector<std::string>& st) {
    double v = 0;
    if (st.size() >= 2 && is_number(st[1], v)) return static_cast<int>(v);
    return -1;
}

// ── COMPONENTS ─────────────────────────────────────────────────────────────
DefComponent read_component(const std::vector<std::string>& t,
                            LefDefLexer& lx, DefDesign& d, int line) {
    DefComponent c;
    // `- <instName> <modelName> [+ ...] ;`
    if (t.size() < 3)
        lx.fail("malformed COMPONENTS entry: " + LefDefLexer::join(t));
    c.name = unescape(t[1]);
    c.cell = unescape(t[2]);
    for (size_t i = 3; i < t.size(); ++i) {
        if (t[i] != "+") continue;
        if (i + 1 >= t.size()) break;
        const std::string kw = upper(t[i + 1]);
        size_t j = i + 2;
        if (kw == "PLACED" || kw == "FIXED" || kw == "COVER") {
            double x = 0, y = 0;
            if (take_point(t, j, x, y)) {
                c.placed = true;
                c.x = x; c.y = y;
                if (j < t.size() && t[j] != "+") c.orient = upper(t[j]);
            }
        } else if (kw == "HALO") {
            // `+ HALO [SOFT] l b r t`
            if (j < t.size() && upper(t[j]) == "SOFT") ++j;
            double v[4] = {0, 0, 0, 0};
            size_t k = 0;
            for (; k < 4 && j + k < t.size(); ++k)
                if (!is_number(t[j + k], v[k])) break;
            if (k == 4) {
                c.has_halo = true;
                c.halo_l = v[0]; c.halo_b = v[1];
                c.halo_r = v[2]; c.halo_t = v[3];
            }
        } else if (kw == "UNPLACED" || kw == "SOURCE" || kw == "WEIGHT" ||
                   kw == "REGION" || kw == "PROPERTY" || kw == "ROUTEHALO" ||
                   kw == "MASKSHIFT" || kw == "EEQMASTER") {
            note(d, "COMPONENTS." + kw, t, line);
        } else {
            note(d, "COMPONENTS." + kw, t, line);
        }
    }
    return c;
}

// ── NETS ───────────────────────────────────────────────────────────────────
DefNet read_net(const std::vector<std::string>& t, LefDefLexer& lx,
                DefDesign& d, int line) {
    DefNet n;
    if (t.size() < 2) lx.fail("malformed NETS entry: " + LefDefLexer::join(t));
    n.name = unescape(t[1]);
    size_t i = 2;
    // `( instName pinName )` pairs, until the first `+`.
    while (i < t.size() && t[i] == "(") {
        size_t j = i + 1;
        if (j + 1 >= t.size()) break;
        DefNetConn cn;
        cn.inst = unescape(t[j]);
        cn.pin  = unescape(t[j + 1]);
        j += 2;
        while (j < t.size() && t[j] != ")") ++j;   // + SYNTHESIZED etc.
        if (j >= t.size()) break;
        n.conns.push_back(cn);
        i = j + 1;
    }
    for (; i < t.size(); ++i) {
        if (t[i] != "+") continue;
        if (i + 1 >= t.size()) break;
        const std::string kw = upper(t[i + 1]);
        if (kw == "USE" && i + 2 < t.size())            n.use = upper(t[i + 2]);
        else if (kw == "NONDEFAULTRULE" && i + 2 < t.size())
            n.nondefaultrule = t[i + 2];
        else if (kw == "ROUTED" || kw == "FIXED" || kw == "COVER" ||
                 kw == "NOSHIELD" || kw == "SHIELDNET") {
            // Existing routing: real data, and NOT what this importer is for
            // (BUDA plans interconnect; adopting a route would make the plan
            // a description of someone else's).  Recorded, not read.
            note(d, "NETS.ROUTING", {kw}, line);
        } else {
            note(d, "NETS." + kw, {kw}, line);
        }
    }
    return n;
}

// ── PINS ───────────────────────────────────────────────────────────────────
DefPin read_pin(const std::vector<std::string>& t, LefDefLexer& lx,
                DefDesign& d, int line) {
    DefPin p;
    if (t.size() < 2) lx.fail("malformed PINS entry: " + LefDefLexer::join(t));
    p.name = unescape(t[1]);
    for (size_t i = 2; i < t.size(); ++i) {
        if (t[i] != "+") continue;
        if (i + 1 >= t.size()) break;
        const std::string kw = upper(t[i + 1]);
        size_t j = i + 2;
        if (kw == "NET" && j < t.size())                p.net = unescape(t[j]);
        else if (kw == "DIRECTION" && j < t.size())     p.dir = upper(t[j]);
        else if (kw == "USE" && j < t.size())           p.use = upper(t[j]);
        else if (kw == "LAYER" && j < t.size()) {
            p.layer = t[j];
            ++j;
            double x1 = 0, y1 = 0, x2 = 0, y2 = 0;
            if (take_point(t, j, x1, y1) && take_point(t, j, x2, y2)) {
                if (x1 > x2) std::swap(x1, x2);
                if (y1 > y2) std::swap(y1, y2);
                p.rects.push_back({x1, y1, x2, y2});
            }
        } else if (kw == "PLACED" || kw == "FIXED" || kw == "COVER") {
            double x = 0, y = 0;
            if (take_point(t, j, x, y)) {
                p.placed = true;
                p.x = x; p.y = y;
                if (j < t.size() && t[j] != "+") p.orient = upper(t[j]);
            }
        } else {
            note(d, "PINS." + kw, {kw}, line);
        }
    }
    return p;
}

// ── BLOCKAGES ──────────────────────────────────────────────────────────────
DefBlockage read_blockage(const std::vector<std::string>& t, DefDesign& d,
                          int line) {
    DefBlockage b;
    size_t i = 0;
    if (i < t.size() && t[i] == "-") ++i;
    const std::string kind = i < t.size() ? upper(t[i]) : "";
    if (kind == "LAYER" && i + 1 < t.size()) { b.layer = t[i + 1]; i += 2; }
    else if (kind == "PLACEMENT") { b.is_placement = true; ++i; }
    for (; i < t.size(); ++i) {
        if (t[i] == "+") {
            const std::string kw = i + 1 < t.size() ? upper(t[i + 1]) : "";
            if (kw == "PARTIAL" && i + 2 < t.size()) {
                double v = 0;
                if (is_number(t[i + 2], v)) { b.has_density = true; b.max_density = v; }
                i += 2;
            } else if (!kw.empty()) {
                note(d, "BLOCKAGES." + kw, {kw}, line);
                ++i;
            }
            continue;
        }
        if (t[i] == "RECT") {
            size_t j = i + 1;
            double x1 = 0, y1 = 0, x2 = 0, y2 = 0;
            if (take_point(t, j, x1, y1) && take_point(t, j, x2, y2)) {
                if (x1 > x2) std::swap(x1, x2);
                if (y1 > y2) std::swap(y1, y2);
                b.rects.push_back({x1, y1, x2, y2});
                i = j - 1;
            }
        } else if (t[i] == "POLYGON") {
            note(d, "BLOCKAGES.POLYGON", {"POLYGON"}, line);
        }
    }
    return b;
}

// ── SPECIALNETS ────────────────────────────────────────────────────────────
void read_specialnet(const std::vector<std::string>& t, DefDesign& d,
                     int line) {
    const std::string net = t.size() >= 2 ? unescape(t[1]) : "";
    const size_t before = d.special_wires.size();
    // `+ ROUTED <layer> <width> ( x y ) ( x y ) … [NEW <layer> <width> …]`
    for (size_t i = 2; i < t.size(); ++i) {
        const std::string kw = upper(t[i]);
        const bool starts = (kw == "ROUTED" || kw == "FIXED" || kw == "COVER" ||
                             kw == "NEW");
        if (!starts) continue;
        size_t j = i + 1;
        DefSpecialWire w;
        w.net = net;
        if (j < t.size()) w.layer = t[j++];
        double width = 0;
        if (j < t.size() && is_number(t[j], width)) { w.width = width; ++j; }
        double px = 0, py = 0;
        bool have_prev = false;
        while (j < t.size() && t[j] == "(") {
            // A `*` coordinate repeats the previous point's value — the
            // shorthand every real SPECIALNETS uses for orthogonal runs.
            size_t k = j + 1;
            if (k + 1 < t.size()) {
                double x = px, y = py;
                bool okx = (t[k] == "*") ? have_prev : is_number(t[k], x);
                bool oky = (t[k + 1] == "*") ? have_prev : is_number(t[k + 1], y);
                if (okx && oky) {
                    w.pts.push_back({x, y});
                    px = x; py = y; have_prev = true;
                }
            }
            while (k < t.size() && t[k] != ")") ++k;
            if (k >= t.size()) break;
            j = k + 1;
        }
        if (w.pts.size() >= 2) d.special_wires.push_back(std::move(w));
        i = j - 1;
    }
    // Per-NET, not per-file: checking the global list would stop recording a
    // geometry-less special net as soon as any OTHER one had produced a wire.
    if (d.special_wires.size() == before)
        note(d, "SPECIALNETS.no_geometry", {net}, line);
}

}  // namespace

DefDesign parse_def(std::string text, const std::string& where) {
    DefDesign d;
    LefDefLexer lx(std::move(text), where);
    std::vector<std::string> st;
    std::string section;                 // the open `… END <section>` block
    int line = 0;

    auto entries_of = [&](const std::string& sec, int& count,
                          const std::function<void(const std::vector<std::string>&,
                                                   int)>& on_entry) {
        // Entries begin with `-` and end with `;`; the section ends with
        // `END <sec>`, which has no `;` and so cannot come out of statement().
        for (;;) {
            if (upper(lx.peek()) == "END") {
                std::string t;
                lx.next(t);
                const std::string got = lx.expect("name after END");
                if (upper(got) != sec)
                    lx.fail("END " + got + " closes " + sec +
                            " — mismatched section END");
                break;
            }
            const int l = lx.line();
            if (!lx.statement(st)) lx.fail("unterminated " + sec + " section");
            if (st.empty()) continue;
            if (st[0] != "-") { note(d, sec + ".stray", st, l); continue; }
            on_entry(st, l);
            ++count;
        }
    };

    for (;;) {
        if (lx.peek().empty()) break;
        line = lx.line();
        const std::string kw0 = upper(lx.peek());

        // `END DESIGN` carries no `;` — it is the file terminator, not a
        // statement — so it has to be recognised BEFORE statement(), which
        // would otherwise run to end of file and report it unterminated.
        if (kw0 == "END") {
            std::string t;
            lx.next(t);
            if (!lx.peek().empty() && lx.peek() != ";") lx.next(t);
            if (lx.peek() == ";") lx.next(t);
            continue;
        }

        // Sections whose header is `KEYWORD <count> ;`.
        if (kw0 == "COMPONENTS" || kw0 == "NETS" || kw0 == "PINS" ||
            kw0 == "BLOCKAGES" || kw0 == "SPECIALNETS" ||
            kw0 == "NONDEFAULTRULES" || kw0 == "VIAS" || kw0 == "REGIONS" ||
            kw0 == "GROUPS" || kw0 == "PROPERTYDEFINITIONS" ||
            kw0 == "STYLES" || kw0 == "SLOTS" || kw0 == "FILLS" ||
            kw0 == "PINPROPERTIES" || kw0 == "SCANCHAINS") {
            if (!lx.statement(st)) break;
            const int n = declared_count(st);
            int seen = 0;
            if (kw0 == "COMPONENTS") {
                d.declared_components = n;
                entries_of("COMPONENTS", seen, [&](const auto& e, int l) {
                    d.components.push_back(read_component(e, lx, d, l));
                });
            } else if (kw0 == "NETS") {
                d.declared_nets = n;
                entries_of("NETS", seen, [&](const auto& e, int l) {
                    d.nets.push_back(read_net(e, lx, d, l));
                });
            } else if (kw0 == "PINS") {
                d.declared_pins = n;
                entries_of("PINS", seen, [&](const auto& e, int l) {
                    d.pins.push_back(read_pin(e, lx, d, l));
                });
            } else if (kw0 == "BLOCKAGES") {
                d.declared_blockages = n;
                entries_of("BLOCKAGES", seen, [&](const auto& e, int l) {
                    d.blockages.push_back(read_blockage(e, d, l));
                });
            } else if (kw0 == "SPECIALNETS") {
                d.declared_specialnets = n;
                entries_of("SPECIALNETS", seen, [&](const auto& e, int l) {
                    read_specialnet(e, d, l);
                });
            } else if (kw0 == "NONDEFAULTRULES") {
                // Read in FULL (item 7): the session translates each rule
                // into BUDA's def_ndr multiplier model against the LEF's
                // default widths, so this keeps the per-layer values rather
                // than the name alone.  Clauses with no BUDA model (VIA,
                // MINCUTS, ...) go to `unmodelled`, and only those reach
                // the census — a translated rule is APPLIED, not recorded.
                entries_of("NONDEFAULTRULES", seen, [&](const auto& e, int l) {
                    if (e.size() < 2) { note(d, "NONDEFAULTRULES", e, l); return; }
                    DefNdr r;
                    r.name = e[1];
                    d.nondefaultrules.push_back(r.name);
                    for (size_t i = 2; i + 1 < e.size(); ++i) {
                        if (e[i] != "+") continue;
                        const std::string kw = upper(e[i + 1]);
                        if (kw == "HARDSPACING") {
                            r.hardspacing = true; ++i;
                        } else if (kw == "LAYER" && i + 2 < e.size()) {
                            DefNdrLayer L; L.layer = e[i + 2];
                            size_t j = i + 3;
                            for (; j + 1 < e.size() && e[j] != "+"; j += 2) {
                                const std::string sub = upper(e[j]);
                                if (sub == "WIDTH")
                                    L.width_dbu = std::atof(e[j + 1].c_str());
                                else if (sub == "SPACING")
                                    L.spacing_dbu = std::atof(e[j + 1].c_str());
                                else
                                    r.unmodelled.push_back("LAYER." + sub);
                            }
                            r.layers.push_back(L);
                            i = j - 1;
                        } else {
                            r.unmodelled.push_back(kw); ++i;
                        }
                    }
                    for (const auto& u : r.unmodelled)
                        note(d, "NONDEFAULTRULES." + u, {r.name}, l);
                    d.ndrs.push_back(std::move(r));
                });
            } else {
                // Sections Phase 3 does not model (VIAS is deferred to the
                // router path, §0).  Skipped as a unit and recorded.
                entries_of(kw0, seen, [&](const auto& e, int l) {
                    note(d, kw0, e, l);
                });
            }
            continue;
        }

        if (!lx.statement(st)) break;
        if (st.empty()) continue;
        const std::string kw = upper(st[0]);
        double v = 0;
        if (kw == "DESIGN" && st.size() >= 2) {
            d.design = st[1];
        } else if (kw == "UNITS" && st.size() >= 4 &&
                   upper(st[1]) == "DISTANCE" && upper(st[2]) == "MICRONS") {
            if (is_number(st[3], v)) d.units = static_cast<int>(v);
        } else if (kw == "DIEAREA") {
            std::vector<double> nums;
            for (size_t i = 1; i < st.size(); ++i)
                if (is_number(st[i], v)) nums.push_back(v);
            if (nums.size() >= 4) {
                d.has_die = true;
                d.die = {std::min(nums[0], nums[2]), std::min(nums[1], nums[3]),
                         std::max(nums[0], nums[2]), std::max(nums[1], nums[3])};
            }
        } else if (kw == "TRACKS") {
            // `TRACKS X <start> DO <count> STEP <step> [MASK n] LAYER <l> …`
            DefTracks tr;
            size_t i = 1;
            if (i < st.size()) tr.dir = upper(st[i++]);
            if (i < st.size() && is_number(st[i], v)) { tr.start = v; ++i; }
            for (; i < st.size(); ++i) {
                const std::string k = upper(st[i]);
                if (k == "DO" && i + 1 < st.size() && is_number(st[i + 1], v))
                    { tr.count = static_cast<int>(v); ++i; }
                else if (k == "STEP" && i + 1 < st.size() && is_number(st[i + 1], v))
                    { tr.step = v; ++i; }
                else if (k == "LAYER")
                    for (size_t j = i + 1; j < st.size(); ++j)
                        tr.layers.push_back(st[j]);
            }
            if (tr.count > 0 && tr.step > 0) d.tracks.push_back(tr);
            else note(d, "TRACKS.incomplete", st, line);
        } else if (kw == "GCELLGRID") {
            DefGCellGrid g;
            size_t i = 1;
            if (i < st.size()) g.dir = upper(st[i++]);
            if (i < st.size() && is_number(st[i], v)) { g.start = v; ++i; }
            for (; i < st.size(); ++i) {
                const std::string k = upper(st[i]);
                if (k == "DO" && i + 1 < st.size() && is_number(st[i + 1], v))
                    { g.count = static_cast<int>(v); ++i; }
                else if (k == "STEP" && i + 1 < st.size() && is_number(st[i + 1], v))
                    { g.step = v; ++i; }
            }
            d.gcellgrid.push_back(g);
        } else if (kw == "END") {
            // END DESIGN, or the tail of a section we already consumed.
        } else if (kw == "VERSION" || kw == "DIVIDERCHAR" ||
                   kw == "BUSBITCHARS" || kw == "TECHNOLOGY" ||
                   kw == "HISTORY" || kw == "ROW") {
            if (kw == "ROW") note(d, "ROW", {st[0]}, line);
        } else {
            note(d, kw, st, line);
        }
    }
    return d;
}

DefDesign read_def(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("read_def: cannot open " + path);
    // ONE buffer.  The `ostringstream` this replaces held the file, `.str()`
    // copied it, and the lexer copied it again — three simultaneous copies of
    // a DEF that can be gigabytes (Codex P1 on #649).  Sizing the string up
    // front and moving it into the parser leaves exactly one.
    //
    // This does NOT make the reader streaming, and the honest reason is that
    // the text is not the dominant term: measured on a 19.7 MB / 1.02M-line
    // DEF the whole parse peaks at ~115 MB, of which the parsed model — 340k
    // components at ~220 B each — is most of it.  True streaming means
    // inserting rows as they parse, which `import_def_lef` currently cannot
    // do (it walks `def.components` a second time for macro OBS, and net
    // resolution needs the name index).  Recorded as the follow-up it is,
    // with the ceiling stated rather than implied: budget roughly 6x the
    // file size.
    std::string text;
    f.seekg(0, std::ios::end);
    const std::streamoff n = f.tellg();
    f.seekg(0, std::ios::beg);
    if (n > 0) {
        text.resize(static_cast<size_t>(n));
        f.read(&text[0], n);
        text.resize(static_cast<size_t>(f.gcount()));
    } else {
        // Not a regular file (a pipe, /dev/stdin): fall back to draining it.
        std::ostringstream ss;
        ss << f.rdbuf();
        text = ss.str();
    }
    return parse_def(std::move(text), path);
}

}  // namespace buda
