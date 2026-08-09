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

// gds_io.cpp — GDSII stream importer (Phase G1).
// Hand-written binary record reader; no external EDA library. Populates the
// same BDB tables as the DEF/Verilog importers, all coordinates in µm.
// See docs/internal/gds_oa_interchange.md.

#include "gds_io.h"
#include "bdb.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <functional>
#include <limits>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace buda {

namespace {

// ── Record types (GDSII stream) ─────────────────────────────────────────────
enum : uint8_t {
    R_HEADER = 0x00, R_BGNLIB = 0x01, R_UNITS = 0x03, R_ENDLIB = 0x04,
    R_BGNSTR = 0x05, R_STRNAME = 0x06, R_ENDSTR = 0x07,
    R_BOUNDARY = 0x08, R_PATH = 0x09, R_SREF = 0x0A, R_AREF = 0x0B,
    R_TEXT = 0x0C, R_LAYER = 0x0D, R_DATATYPE = 0x0E, R_WIDTH = 0x0F,
    R_XY = 0x10,
    R_ENDEL = 0x11, R_SNAME = 0x12, R_COLROW = 0x13, R_TEXTTYPE = 0x16,
    R_STRING = 0x19,
    R_STRANS = 0x1A, R_MAG = 0x1B,
    R_ANGLE = 0x1C, R_PATHTYPE = 0x21, R_LIBNAME = 0x02,
    R_PROPATTR = 0x2B, R_PROPVALUE = 0x2C, R_BOX = 0x2D,
    R_BOXTYPE = 0x2E,
};

struct Rec {
    uint8_t type = 0;
    const uint8_t* data = nullptr;
    size_t len = 0;

    // Every fixed-width accessor bounds-checks against the RECORD length
    // (audit C8-02): the stream reader guarantees data..data+len is inside
    // the file buffer, but a malformed record shorter than its declared
    // fields would otherwise read the NEXT record's bytes (or past the
    // buffer on the last record) as coordinate data — silently importing
    // garbage geometry instead of the fail-LOUD the importer promises.
    void need(size_t off, size_t n) const {
        if (off + n > len)
            throw std::runtime_error(
                "import_gds: truncated record (type 0x" +
                std::to_string(type) + ": field at offset " +
                std::to_string(off) + "+" + std::to_string(n) +
                " exceeds record length " + std::to_string(len) + ")");
    }
    int16_t i16(size_t off = 0) const {
        need(off, 2);
        return (int16_t)((data[off] << 8) | data[off + 1]);
    }
    int32_t i32(size_t off = 0) const {
        need(off, 4);
        return (int32_t)((uint32_t(data[off]) << 24) | (uint32_t(data[off + 1]) << 16) |
                         (uint32_t(data[off + 2]) << 8) | data[off + 3]);
    }
    // Excess-64 base-16 8-byte real.
    double real8(size_t off = 0) const {
        need(off, 8);
        const uint8_t* b = data + off;
        int sign = (b[0] & 0x80) ? -1 : 1;
        int exp = (b[0] & 0x7F) - 64;
        uint64_t mant = 0;
        for (int i = 1; i < 8; ++i) mant = (mant << 8) | b[i];
        return sign * (double)mant / std::pow(2.0, 56) * std::pow(16.0, exp);
    }
    std::string str() const {
        size_t n = len;
        while (n > 0 && data[n - 1] == '\0') --n;   // strip even-pad NUL
        return std::string(reinterpret_cast<const char*>(data), n);
    }
};

struct BBox {
    double x1 = std::numeric_limits<double>::infinity();
    double y1 = std::numeric_limits<double>::infinity();
    double x2 = -std::numeric_limits<double>::infinity();
    double y2 = -std::numeric_limits<double>::infinity();
    bool empty() const { return x1 > x2; }
    void grow(double x, double y) {
        x1 = std::min(x1, x); y1 = std::min(y1, y);
        x2 = std::max(x2, x); y2 = std::max(y2, y);
    }
    void grow(const BBox& o) {
        if (o.empty()) return;
        grow(o.x1, o.y1); grow(o.x2, o.y2);
    }
};

// Affine transform limited to the GDS placement model: reflect-about-X (before
// rotation), magnification, rotation (snapped to 0/90/180/270), translation.
struct XForm {
    double a = 1, b = 0, c = 0, d = 1, tx = 0, ty = 0;   // [a b; c d]·p + t
    static XForm place(double x, double y, bool mirror, int angle, double mag) {
        double ca = 1, sa = 0;
        switch (((angle % 360) + 360) % 360) {
            case 90:  ca = 0;  sa = 1;  break;
            case 180: ca = -1; sa = 0;  break;
            case 270: ca = 0;  sa = -1; break;
            default:  break;
        }
        double fy = mirror ? -1.0 : 1.0;      // reflect about X axis first
        XForm t;
        t.a = ca * mag;        t.b = -sa * mag * fy;
        t.c = sa * mag;        t.d = ca * mag * fy;
        t.tx = x;              t.ty = y;
        return t;
    }
    void apply(double x, double y, double& ox, double& oy) const {
        ox = a * x + b * y + tx;
        oy = c * x + d * y + ty;
    }
    XForm compose(const XForm& ch) const {    // this ∘ child
        XForm r;
        r.a = a * ch.a + b * ch.c;  r.b = a * ch.b + b * ch.d;
        r.c = c * ch.a + d * ch.c;  r.d = c * ch.b + d * ch.d;
        r.tx = a * ch.tx + b * ch.ty + tx;
        r.ty = c * ch.tx + d * ch.ty + ty;
        return r;
    }
    BBox apply(const BBox& in) const {
        BBox out;
        if (in.empty()) return out;
        const double xs[2] = {in.x1, in.x2}, ys[2] = {in.y1, in.y2};
        for (double x : xs)
            for (double y : ys) {
                double ox, oy;
                apply(x, y, ox, oy);
                out.grow(ox, oy);
            }
        return out;
    }
};

struct Ref {                                  // one SREF or AREF element
    std::string sname;
    std::string inst_name;                    // PROPVALUE if present
    bool   mirror = false;
    int    angle = 0;
    double mag = 1.0;
    // Placements: origin + (cols x rows) steps; SREF = 1x1 with zero steps.
    double ox = 0, oy = 0, cdx = 0, cdy = 0, rdx = 0, rdy = 0;
    int cols = 1, rows = 1;
};

struct Text {                                 // one TEXT label (µm, local coords)
    int layer = 0;
    double x = 0, y = 0;
    std::string str;
};

struct GStruct {
    std::string name;
    BBox geom;                                // own BOUNDARY/BOX/PATH extent
    std::vector<Ref> refs;
    std::vector<Text> texts;                  // labels (net recovery, Phase G2)
};

int snap_angle(double deg, std::vector<std::string>& warnings,
               const std::string& where) {
    int a = (int)std::lround(deg / 90.0) * 90;
    if (std::fabs(deg - a) > 1e-6) {
        warnings.push_back("non-orthogonal ANGLE " + std::to_string(deg) +
                           " at " + where + " snapped to " + std::to_string(a));
    }
    return ((a % 360) + 360) % 360;
}

// Canonical 8-orientation token <-> GDS placement transform. The transform is
// "reflect about the X axis FIRST (STRANS mirror bit), THEN rotate CCW by
// `angle`" — exactly XForm::place / the GDS STRANS+ANGLE convention. N/W/S/E
// are the pure 0/90/180/270 rotations; F* are the mirrored variants. Tokens
// are DEFINED by this (mirror, angle) pair (BDB's component.orient, v12), not
// by DEF's letter semantics. Non-unit MAG is not captured here (it stays baked
// into the bbox, as before).
std::string transform_to_orient(bool mirror, int angle) {
    int a = ((angle % 360) + 360) % 360;
    static const char* rot[4]  = {"N",  "W",  "S",  "E"};   // 0, 90, 180, 270
    static const char* frot[4] = {"FN", "FW", "FS", "FE"};
    int idx = a / 90;
    return mirror ? frot[idx] : rot[idx];
}
bool orient_is_mirror(const std::string& o) {
    return !o.empty() && o[0] == 'F';
}
int orient_angle(const std::string& o) {
    const std::string t = orient_is_mirror(o) ? o.substr(1) : o;
    if (t == "W") return 90;
    if (t == "S") return 180;
    if (t == "E") return 270;
    return 0;   // "N" or unknown -> identity
}

}  // namespace

GdsImportStats import_gds(BDB& db, const std::string& path,
                          const std::vector<int>& label_layers,
                          const std::vector<std::pair<int,int>>& routing_layers) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("import_gds: cannot open " + path);
    std::vector<uint8_t> buf((std::istreambuf_iterator<char>(f)),
                             std::istreambuf_iterator<char>());

    GdsImportStats stats;

    // ── Pass 1: parse records into structures ───────────────────────────────
    std::vector<GStruct> structs;
    std::map<std::string, size_t> by_name;
    double um_per_dbu = -1.0;
    GStruct* cur = nullptr;
    Ref* cur_ref = nullptr;                    // element being parsed (SREF/AREF)
    enum { GK_NONE, GK_POLY, GK_PATH } geom_kind = GK_NONE;
    bool in_text = false;
    Text cur_text;
    bool aref = false;
    double path_width_dbu = 0;                 // WIDTH record (dbu; abs value)
    int    path_type = 0;                      // PATHTYPE: 0 butt, 1/2 extended
    int    elem_layer = 0, elem_dtype = 0;     // current shape's LAYER/DATATYPE
    const std::set<std::pair<int,int>> routing(routing_layers.begin(),
                                               routing_layers.end());
    bool saw_header = false, saw_endlib = false;

    size_t pos = 0;
    while (pos + 4 <= buf.size()) {
        uint16_t rlen = (uint16_t)((buf[pos] << 8) | buf[pos + 1]);
        if (rlen < 4 || pos + rlen > buf.size())
            throw std::runtime_error("import_gds: truncated/corrupt record at byte " +
                                     std::to_string(pos));
        Rec r{buf[pos + 2], buf.data() + pos + 4, (size_t)(rlen - 4)};
        pos += rlen;

        if (!saw_header) {
            if (r.type != R_HEADER)
                throw std::runtime_error("import_gds: not a GDSII stream file "
                                         "(missing HEADER): " + path);
            saw_header = true;
            continue;
        }

        switch (r.type) {
            case R_UNITS:
                if (r.len < 16)
                    throw std::runtime_error("import_gds: short UNITS record");
                um_per_dbu = r.real8(8) * 1e6;   // meters/dbu -> µm/dbu
                // ... and µm -> LAYOUT UNITS, the scale this design
                // stores its coordinates in (1.0 = microns).
                um_per_dbu *= db.import_scale();
                break;
            case R_BGNSTR:
                structs.emplace_back();
                cur = &structs.back();
                break;
            case R_STRNAME:
                if (cur) {
                    cur->name = r.str();
                    by_name[cur->name] = structs.size() - 1;
                }
                break;
            case R_ENDSTR:
                cur = nullptr;
                break;
            case R_BOUNDARY: case R_BOX:
                geom_kind = GK_POLY;
                elem_layer = 0; elem_dtype = 0;
                break;
            case R_PATH:
                geom_kind = GK_PATH;
                path_width_dbu = 0;
                path_type = 0;
                elem_layer = 0; elem_dtype = 0;
                break;
            case R_WIDTH:
                // Negative = absolute (not magnified); bbox use is the same.
                path_width_dbu = std::fabs((double)r.i32());
                break;
            case R_PATHTYPE:
                path_type = r.i16();
                break;
            case R_TEXT:
                in_text = true;
                cur_text = Text{};
                ++stats.n_texts;
                break;
            case R_LAYER:
                if (in_text) cur_text.layer = r.i16();
                else         elem_layer = r.i16();
                break;
            case R_DATATYPE: case R_BOXTYPE:
                if (!in_text) elem_dtype = r.i16();
                break;
            case R_STRING:
                if (in_text) cur_text.str = r.str();
                break;
            case R_SREF: case R_AREF:
                if (cur) {
                    cur->refs.emplace_back();
                    cur_ref = &cur->refs.back();
                    aref = (r.type == R_AREF);
                }
                break;
            case R_SNAME:
                if (cur_ref) cur_ref->sname = r.str();
                break;
            case R_STRANS:
                if (cur_ref) cur_ref->mirror = (r.i16() & (int16_t)0x8000) != 0;
                break;
            case R_MAG:
                if (cur_ref) cur_ref->mag = r.real8();
                break;
            case R_ANGLE:
                if (cur_ref)
                    cur_ref->angle = snap_angle(
                        r.real8(), stats.warnings,
                        (cur ? cur->name : path) + "/" + cur_ref->sname);
                break;
            case R_COLROW:
                if (cur_ref) {
                    cur_ref->cols = std::max<int>(1, r.i16(0));
                    cur_ref->rows = std::max<int>(1, r.i16(2));
                    // Bound the AREF explosion (audit C8-03): COLROW comes
                    // straight off the file, so a hostile/corrupt stream can
                    // declare cols=rows=32767 (~1.07e9 instances) and hang the
                    // importer — each element is an add_comp SQL insert. Fail
                    // LOUD past any real array size.
                    const long long AREF_MAX = 1LL << 20;   // 1,048,576
                    if ((long long)cur_ref->cols * cur_ref->rows > AREF_MAX)
                        throw std::runtime_error(
                            "import_gds: AREF array of " +
                            std::to_string(cur_ref->cols) + "x" +
                            std::to_string(cur_ref->rows) +
                            " exceeds the " + std::to_string(AREF_MAX) +
                            "-element cap (corrupt COLROW record?)");
                }
                break;
            case R_PROPVALUE:
                if (cur_ref && cur_ref->inst_name.empty())
                    cur_ref->inst_name = r.str();
                break;
            case R_XY: {
                if (um_per_dbu <= 0)
                    throw std::runtime_error("import_gds: XY before UNITS");
                auto xy = [&](size_t i, double& x, double& y) {
                    x = r.i32(i * 8) * um_per_dbu;
                    y = r.i32(i * 8 + 4) * um_per_dbu;
                };
                size_t npts = r.len / 8;
                if (in_text) {
                    xy(0, cur_text.x, cur_text.y);
                } else if (cur_ref) {
                    xy(0, cur_ref->ox, cur_ref->oy);
                    if (aref && npts >= 3) {
                        double x2, y2, x3, y3;
                        xy(1, x2, y2);
                        xy(2, x3, y3);
                        cur_ref->cdx = (x2 - cur_ref->ox) / cur_ref->cols;
                        cur_ref->cdy = (y2 - cur_ref->oy) / cur_ref->cols;
                        cur_ref->rdx = (x3 - cur_ref->ox) / cur_ref->rows;
                        cur_ref->rdy = (y3 - cur_ref->oy) / cur_ref->rows;
                    }
                } else if ((geom_kind == GK_POLY || geom_kind == GK_PATH) &&
                           cur && routing.count({elem_layer, elem_dtype})) {
                    // Phase G3: shapes on mapped routing (layer, datatype)
                    // pairs are wires, not macro-outline geometry — they must
                    // not grow the cell footprint (round-trip requirement).
                    ++stats.n_routing_shapes;
                } else if (geom_kind == GK_POLY && cur) {
                    for (size_t i = 0; i < npts; ++i) {
                        double x, y;
                        xy(i, x, y);
                        cur->geom.grow(x, y);
                    }
                } else if (geom_kind == GK_PATH && cur) {
                    // A PATH's footprint is the stroked centerline: half the
                    // WIDTH on each side; PATHTYPE 1/2 also extends the ends.
                    const double hw = path_width_dbu * um_per_dbu / 2.0;
                    const double ext = (path_type == 1 || path_type == 2) ? hw : 0.0;
                    double px = 0, py = 0;
                    for (size_t i = 0; i < npts; ++i) {
                        double x, y;
                        xy(i, x, y);
                        if (i == 0 && npts == 1) {          // degenerate dot
                            cur->geom.grow(x - hw, y - hw);
                            cur->geom.grow(x + hw, y + hw);
                        } else if (i > 0) {
                            if (px == x) {                  // vertical segment
                                cur->geom.grow(x - hw, std::min(py, y) - ext);
                                cur->geom.grow(x + hw, std::max(py, y) + ext);
                            } else if (py == y) {           // horizontal segment
                                cur->geom.grow(std::min(px, x) - ext, y - hw);
                                cur->geom.grow(std::max(px, x) + ext, y + hw);
                            } else {                        // diagonal: safe over-cover
                                cur->geom.grow(std::min(px, x) - hw - ext,
                                               std::min(py, y) - hw - ext);
                                cur->geom.grow(std::max(px, x) + hw + ext,
                                               std::max(py, y) + hw + ext);
                            }
                        }
                        px = x; py = y;
                    }
                }
                // TEXT XY is captured for net recovery but deliberately does
                // NOT grow the structure's footprint.
                break;
            }
            case R_ENDEL:
                if (in_text && cur && !cur_text.str.empty())
                    cur->texts.push_back(cur_text);
                cur_ref = nullptr;
                in_text = false;
                geom_kind = GK_NONE;
                aref = false;
                break;
            case R_ENDLIB:
                saw_endlib = true;
                break;
            default:
                break;                          // unhandled records are skipped
        }
        if (saw_endlib) break;
    }
    if (!saw_endlib)
        throw std::runtime_error("import_gds: missing ENDLIB (truncated file?): " + path);
    stats.n_structures = (int)structs.size();

    // ── Recursive footprint bbox per structure (memoized, cycle-guarded) ────
    std::map<std::string, BBox> full_bbox;
    std::set<std::string> visiting;
    std::function<BBox(const std::string&)> bbox_of =
        [&](const std::string& name) -> BBox {
        auto it = full_bbox.find(name);
        if (it != full_bbox.end()) return it->second;
        auto sit = by_name.find(name);
        if (sit == by_name.end()) {
            stats.warnings.push_back("reference to undefined structure '" +
                                     name + "'");
            return BBox{};
        }
        if (!visiting.insert(name).second) {
            stats.warnings.push_back("reference cycle at structure '" + name +
                                     "' — cycle edge ignored");
            return BBox{};
        }
        const GStruct& s = structs[sit->second];
        BBox out = s.geom;
        for (const Ref& ref : s.refs) {
            BBox cb = bbox_of(ref.sname);
            if (cb.empty()) continue;
            for (int rr = 0; rr < ref.rows; ++rr)
                for (int cc = 0; cc < ref.cols; ++cc) {
                    XForm t = XForm::place(ref.ox + cc * ref.cdx + rr * ref.rdx,
                                           ref.oy + cc * ref.cdy + rr * ref.rdy,
                                           ref.mirror, ref.angle, ref.mag);
                    out.grow(t.apply(cb));
                }
        }
        visiting.erase(name);
        full_bbox[name] = out;
        return out;
    };

    // ── Write cells + elaborate components ──────────────────────────────────
    db.clear_design();
    for (const GStruct& s : structs) {
        BBox bb = bbox_of(s.name);
        double w = bb.empty() ? 0 : bb.x2 - bb.x1;
        double h = bb.empty() ? 0 : bb.y2 - bb.y1;
        if (bb.empty())
            stats.warnings.push_back("structure '" + s.name +
                                     "' has no geometry anywhere — 0x0 cell");
        db.add_cell(s.name, w, h);
        ++stats.n_cells;
    }

    std::set<std::string> referenced;
    for (const GStruct& s : structs)
        for (const Ref& ref : s.refs) referenced.insert(ref.sname);

    // Placed components (for label containment) + labels elaborated through
    // the same hierarchy transforms as geometry (Phase G2 net recovery).
    struct Placed { int id; int depth; BBox bb; };
    std::vector<Placed> placed;
    struct Label { std::string net; double x, y; };
    std::vector<Label> labels;
    const std::set<int> lab_layers(label_layers.begin(), label_layers.end());
    auto emit_labels = [&](const GStruct& s, const XForm& xf) {
        for (const Text& t : s.texts) {
            if (!lab_layers.empty() && !lab_layers.count(t.layer)) continue;
            double lx, ly;
            xf.apply(t.x, t.y, lx, ly);
            labels.push_back({t.str, lx, ly});
        }
    };

    std::function<void(const GStruct&, const XForm&, const std::string&,
                       const std::string&, int, const std::string&)> elaborate =
        [&](const GStruct& s, const XForm& xf, const std::string& comp_path,
            const std::string& parent_path, int depth,
            const std::string& orient) {
        // A valid GDS structure graph is a DAG; a corrupt/hostile stream with
        // a reference cycle would recurse forever (stack overflow). Fail LOUD
        // at a depth no real hierarchy reaches (audit C8-01).
        if (depth > 256)
            throw std::runtime_error(
                "import_gds: structure reference depth >256 at '" + comp_path +
                "' — reference cycle in the GDS stream?");
        BBox bb = xf.apply(bbox_of(s.name));
        if (bb.empty()) bb = BBox{0, 0, 0, 0};
        // orient = this instance's LOCAL orientation (relative to its parent);
        // the bbox above is still the absolute axis-aligned extent.
        int cid = db.add_comp(comp_path, s.name, parent_path,
                              bb.x1, bb.y1, bb.x2, bb.y2, s.refs.empty(), orient);
        placed.push_back({cid, depth, bb});
        emit_labels(s, xf);
        ++stats.n_components;
        std::map<std::string, int> ordinal;     // per-parent synthesized names
        for (const Ref& ref : s.refs) {
            auto sit = by_name.find(ref.sname);
            if (sit == by_name.end()) continue; // warned during bbox pass
            const GStruct& child = structs[sit->second];
            for (int rr = 0; rr < ref.rows; ++rr)
                for (int cc = 0; cc < ref.cols; ++cc) {
                    XForm t = xf.compose(XForm::place(
                        ref.ox + cc * ref.cdx + rr * ref.rdx,
                        ref.oy + cc * ref.cdy + rr * ref.rdy,
                        ref.mirror, ref.angle, ref.mag));
                    std::string nm = ref.inst_name;
                    if (nm.empty() || ref.cols * ref.rows > 1 ||
                        ordinal.count(nm)) {
                        // GDS refs are anonymous: synthesize a deterministic
                        // per-parent ordinal name (arrays always synthesize).
                        int n = ordinal[ref.sname]++;
                        nm = ref.sname + "_" + std::to_string(n);
                    } else {
                        ordinal[nm] = 1;        // reserve the property name
                    }
                    elaborate(child, t, comp_path + "/" + nm, comp_path,
                              depth + 1,
                              transform_to_orient(ref.mirror, ref.angle));
                }
        }
    };

    // Top structures are NOT materialized as component rows: their references
    // elaborate as unprefixed depth-0 roots — exactly how import_verilog
    // elaborates the top module's instances — so the documented geometry-only
    // merge (import_gds + import_verilog) matches placements by name. A
    // childless top with own geometry still gets a row (it IS the placement).
    std::set<std::string> root_names;
    for (const GStruct& s : structs) {
        if (referenced.count(s.name)) continue;
        stats.tops.push_back(s.name);
        if (s.refs.empty()) {
            elaborate(s, XForm{}, s.name, "", 0, "N");
            continue;
        }
        emit_labels(s, XForm{});   // the top's own labels (identity transform)
        std::map<std::string, int> ordinal;
        for (const Ref& ref : s.refs) {
            auto sit = by_name.find(ref.sname);
            if (sit == by_name.end()) continue;
            const GStruct& child = structs[sit->second];
            for (int rr = 0; rr < ref.rows; ++rr)
                for (int cc = 0; cc < ref.cols; ++cc) {
                    XForm t = XForm::place(
                        ref.ox + cc * ref.cdx + rr * ref.rdx,
                        ref.oy + cc * ref.cdy + rr * ref.rdy,
                        ref.mirror, ref.angle, ref.mag);
                    std::string nm = ref.inst_name;
                    if (nm.empty() || ref.cols * ref.rows > 1 ||
                        ordinal.count(nm)) {
                        int n = ordinal[ref.sname]++;
                        nm = ref.sname + "_" + std::to_string(n);
                    } else {
                        ordinal[nm] = 1;
                    }
                    if (!root_names.insert(nm).second) {
                        // Roots from multiple tops may collide; qualify.
                        nm = s.name + "_" + nm;
                        root_names.insert(nm);
                        stats.warnings.push_back(
                            "root instance name collision across tops; using '" +
                            nm + "'");
                    }
                    elaborate(child, t, nm, "", 0,
                              transform_to_orient(ref.mirror, ref.angle));
                }
        }
    }
    // A single top's extent is the die (the DEF DIEAREA analogue).
    if (stats.tops.size() == 1) {
        BBox tb = bbox_of(stats.tops[0]);
        if (!tb.empty()) db.set_die(tb.x2 - tb.x1, tb.y2 - tb.y1);
    }

    // ── Phase G2: TEXT labels -> net/pin rows ────────────────────────────────
    // Each label string is a net; its pin lands on the DEEPEST component
    // containing the label's elaborated position (a label inside a referenced
    // structure repeats per instance — the standard GDS flattening semantic,
    // which shorts those instances' pins onto the label's net).
    std::set<std::string> net_names;
    for (const Label& lb : labels) {
        const Placed* best = nullptr;
        for (const Placed& pc : placed) {
            if (lb.x < pc.bb.x1 || lb.x > pc.bb.x2 ||
                lb.y < pc.bb.y1 || lb.y > pc.bb.y2)
                continue;
            if (!best || pc.depth > best->depth) best = &pc;
        }
        if (!best) {
            ++stats.n_labels_skipped;
            stats.warnings.push_back("label '" + lb.net +
                                     "' is outside every component — skipped");
            continue;
        }
        db.add_label_pin(lb.net, best->id, lb.net, lb.x, lb.y);
        net_names.insert(lb.net);
        ++stats.n_pins;
    }
    stats.n_nets = (int)net_names.size();

    return stats;
}

// ─────────────────────────────────────────────────────────────────────────────
// Phase G4 — GDSII stream writer (the C++ port of tools/gds_build.py, same
// record conventions: 1nm dbu, zeroed timestamps, PROPATTR 61 instance names).
// ─────────────────────────────────────────────────────────────────────────────

namespace {

class GdsWriter {
public:
    static constexpr double kDbuUm = 0.001;         // 1 nm database unit
    static constexpr int    kInstNameProp = 61;     // matches gds_build.py

    void header(const std::string& libname) {
        std::vector<uint8_t> v;
        put_i16(v, 600);
        rec(R_HEADER, 2, v);
        rec(R_BGNLIB, 2, zero12());
        rec(R_LIBNAME, 6, str_pl(libname));
        // UNITS: user-units per dbu (user unit = µm), meters per dbu.
        std::vector<uint8_t> u = real8(kDbuUm);
        append(u, real8(kDbuUm * 1e-6));
        rec(R_UNITS, 5, u);
    }
    void begin_struct(const std::string& name) {
        rec(R_BGNSTR, 2, zero12());
        rec(R_STRNAME, 6, str_pl(name));
    }
    void end_struct() { rec(R_ENDSTR, 0, {}); }
    void end_lib()    { rec(R_ENDLIB, 0, {}); }

    // Axis-aligned rectangle (closed 5-point BOUNDARY).
    void boundary_rect(int layer, int datatype,
                       double x1, double y1, double x2, double y2) {
        rec(R_BOUNDARY, 0, {});
        rec(R_LAYER, 2, i16_pl(layer));
        rec(R_DATATYPE, 2, i16_pl(datatype));
        std::vector<uint8_t> xy;
        const double xs[5] = {x1, x2, x2, x1, x1};
        const double ys[5] = {y1, y1, y2, y2, y1};
        for (int i = 0; i < 5; ++i) {
            put_i32(xy, to_dbu(xs[i]));
            put_i32(xy, to_dbu(ys[i]));
        }
        rec(R_XY, 3, xy);
        rec(R_ENDEL, 0, {});
    }
    void sref(const std::string& sname, double x, double y,
              bool mirror, int angle, const std::string& inst_name) {
        rec(R_SREF, 0, {});
        rec(R_SNAME, 6, str_pl(sname));
        int a = ((angle % 360) + 360) % 360;
        // STRANS (mirror bit) + ANGLE precede XY, mirroring gds_build.py's
        // _trans helper. STRANS emitted whenever the placement is non-identity.
        if (mirror || a) {
            std::vector<uint8_t> st;
            put_i16(st, mirror ? 0x8000 : 0);
            rec(R_STRANS, 1, st);
        }
        if (a) rec(R_ANGLE, 5, real8((double)a));
        std::vector<uint8_t> xy;
        put_i32(xy, to_dbu(x));
        put_i32(xy, to_dbu(y));
        rec(R_XY, 3, xy);
        if (!inst_name.empty()) {
            rec(R_PROPATTR, 2, i16_pl(kInstNameProp));
            rec(R_PROPVALUE, 6, str_pl(inst_name));
        }
        rec(R_ENDEL, 0, {});
    }
    void text(int layer, int texttype, double x, double y,
              const std::string& s) {
        rec(R_TEXT, 0, {});
        rec(R_LAYER, 2, i16_pl(layer));
        rec(R_TEXTTYPE, 2, i16_pl(texttype));
        std::vector<uint8_t> xy;
        put_i32(xy, to_dbu(x));
        put_i32(xy, to_dbu(y));
        rec(R_XY, 3, xy);
        rec(R_STRING, 6, str_pl(s));
        rec(R_ENDEL, 0, {});
    }

    void write(const std::string& path) const {
        std::ofstream f(path, std::ios::binary);
        if (!f) throw std::runtime_error("export_gds: cannot write " + path);
        f.write(reinterpret_cast<const char*>(out_.data()),
                (std::streamsize)out_.size());
        if (!f) throw std::runtime_error("export_gds: write failed: " + path);
    }

private:
    std::vector<uint8_t> out_;

    static int32_t to_dbu_static(double um) {
        return (int32_t)std::llround(um / kDbuUm);
    }
    // BDB coordinates are in LAYOUT UNITS, which are microns only at the
    // default import scale (docs/internal/engine_units.md).  Divide by the
    // design's own scale first, or a DBU-scale BDB would export geometry
    // `lu_per_um` times too large — silently, in a valid-looking GDS.
    int32_t to_dbu(double lu) const { return to_dbu_static(lu / lu_per_um_); }

public:
    void set_layout_scale(double lu_per_um) {
        if (lu_per_um > 0.0) lu_per_um_ = lu_per_um;
    }

private:
    double lu_per_um_ = 1.0;

    void rec(uint8_t rtype, uint8_t dcode, const std::vector<uint8_t>& payload) {
        size_t len = 4 + payload.size();
        out_.push_back((uint8_t)(len >> 8));
        out_.push_back((uint8_t)(len & 0xFF));
        out_.push_back(rtype);
        out_.push_back(dcode);
        out_.insert(out_.end(), payload.begin(), payload.end());
    }
    static void put_i16(std::vector<uint8_t>& p, int v) {
        p.push_back((uint8_t)((v >> 8) & 0xFF));
        p.push_back((uint8_t)(v & 0xFF));
    }
    static void put_i32(std::vector<uint8_t>& p, int32_t v) {
        for (int s = 24; s >= 0; s -= 8)
            p.push_back((uint8_t)(((uint32_t)v >> s) & 0xFF));
    }
    static std::vector<uint8_t> i16_pl(int v) {
        std::vector<uint8_t> p;
        put_i16(p, v);
        return p;
    }
    static std::vector<uint8_t> str_pl(const std::string& s) {
        std::vector<uint8_t> p(s.begin(), s.end());
        if (p.size() % 2) p.push_back(0);       // even-length pad
        return p;
    }
    static std::vector<uint8_t> zero12() {      // 12 zeroed timestamp shorts
        return std::vector<uint8_t>(24, 0);
    }
    // Excess-64 base-16 GDSII 8-byte real (inverse of Rec::real8).
    static std::vector<uint8_t> real8(double value) {
        std::vector<uint8_t> p(8, 0);
        if (value == 0) return p;
        int sign = value < 0 ? 0x80 : 0;
        double v = std::fabs(value);
        int exp = 64;
        while (v >= 1.0)        { v /= 16.0; ++exp; }
        while (v < 1.0 / 16.0)  { v *= 16.0; --exp; }
        uint64_t mant = (uint64_t)(v * (double)(1ULL << 56));
        p[0] = (uint8_t)(sign | exp);
        for (int i = 0; i < 7; ++i)
            p[1 + i] = (uint8_t)((mant >> (8 * (6 - i))) & 0xFF);
        return p;
    }
    static void append(std::vector<uint8_t>& a, const std::vector<uint8_t>& b) {
        a.insert(a.end(), b.begin(), b.end());
    }
};

std::string last_path_segment(const std::string& p) {
    size_t n = p.rfind('/');
    return n == std::string::npos ? p : p.substr(n + 1);
}

}  // namespace

GdsExportStats export_gds(BDB& db, const std::string& path,
                          const std::vector<std::tuple<int,int,int>>& layer_map,
                          int outline_layer, int label_layer,
                          bool write_labels, double via_size) {
    GdsExportStats stats;

    std::map<int, std::pair<int,int>> lmap;     // buda layer -> (gds, datatype)
    for (const auto& [bl, gl, dt] : layer_map) lmap[bl] = {gl, dt};
    std::set<int> warned_unmapped;
    auto gds_pair = [&](int layer) -> std::pair<int,int> {
        auto it = lmap.find(layer);
        if (it != lmap.end()) return it->second;
        if (warned_unmapped.insert(layer).second)
            stats.warnings.push_back(
                "routing layer " + std::to_string(layer) +
                " has no def_gds_layer mapping — defaulting to GDS (" +
                std::to_string(layer) + ",0)");
        return {layer, 0};
    };

    // ── Gather + sort the design (deterministic bytes) ──────────────────────
    auto cells = db.all_cells();
    std::sort(cells.begin(), cells.end(),
              [](const CellRow& a, const CellRow& b) { return a.name < b.name; });
    std::map<std::string, const CellRow*> cell_by_name;
    for (const auto& c : cells) cell_by_name[c.name] = &c;

    auto comps = db.all_components();
    std::map<int, const ComponentRow*> comp_by_id;
    for (const auto& c : comps) comp_by_id[c.id] = &c;
    std::map<int, std::vector<const ComponentRow*>> children;
    std::vector<const ComponentRow*> roots;
    for (const auto& c : comps) {
        if (c.parent_id >= 0 && comp_by_id.count(c.parent_id))
            children[c.parent_id].push_back(&c);
        else
            roots.push_back(&c);
    }
    auto by_name = [](const ComponentRow* a, const ComponentRow* b) {
        return a->name < b->name;
    };
    for (auto& [pid, ch] : children) std::sort(ch.begin(), ch.end(), by_name);
    std::sort(roots.begin(), roots.end(), by_name);

    // Template instance per cell (lowest component id): cell-internal SREFs
    // are reconstructed from ITS children at relative offsets — BDB stores
    // per-instance absolute bboxes, but instances of one cell share their
    // internal structure (the replication assumption the BDB is built on).
    std::map<std::string, const ComponentRow*> tmpl;
    for (const auto& c : comps) {
        auto it = tmpl.find(c.cell);
        if (it == tmpl.end() || c.id < it->second->id) tmpl[c.cell] = &c;
    }

    // Emit an oriented SREF that reproduces the component's stored bbox: place
    // the cell's local (0,0,w,h) box under the instance's orientation, then
    // shift so its min corner lands at the bbox origin (ox/oy = the enclosing
    // frame's origin — the template parent for cell-internal children, 0 for
    // roots). For an unoriented instance this reduces to (c->x1 - ox, ...),
    // matching the pre-v12 behavior. n_dim_mismatch now flags only a genuine
    // resize (bbox dims != the ORIENTED cell extent), not a representable
    // rotation.
    int n_dim_mismatch = 0;
    auto place = [&](GdsWriter& w, const ComponentRow* c, double ox, double oy,
                     const std::string& inst_name) {
        const bool mir = orient_is_mirror(c->orient);
        const int  ang = orient_angle(c->orient);
        double cw = c->x2 - c->x1, ch = c->y2 - c->y1;   // fallback = bbox dims
        auto ci = cell_by_name.find(c->cell);
        if (ci != cell_by_name.end()) {
            cw = ci->second->width;
            ch = ci->second->height;
        }
        XForm t0 = XForm::place(0, 0, mir, ang, 1.0);
        BBox lb = t0.apply(BBox{0, 0, cw, ch});
        if (ci != cell_by_name.end()) {
            const double bw = c->x2 - c->x1, bh = c->y2 - c->y1;
            if (std::fabs((lb.x2 - lb.x1) - bw) > 1e-9 ||
                std::fabs((lb.y2 - lb.y1) - bh) > 1e-9)
                ++n_dim_mismatch;
        }
        w.sref(c->cell, (c->x1 - lb.x1) - ox, (c->y1 - lb.y1) - oy,
               mir, ang, inst_name);
        ++stats.n_placements;
    };

    // The top structure. import_gds materializes a `cell` row for the top
    // structure it reads (footprint = die extent) but no component row — so
    // a GDS-imported BDB holds an "orphan" cell (no instances) whose size IS
    // the die. Exporting that orphan as its own structure PLUS a synthetic
    // top would re-import as two tops (spurious component, no die): instead
    // the top structure takes the orphan's name, so re-import reproduces the
    // same single top, cell row, and die. The die-size match is REQUIRED
    // evidence: a merely-uninstantiated library cell (e.g. an unused LEF
    // macro — import_def_lef inserts every macro) must keep its own
    // structure, not be consumed as the top.
    const double die_w = std::atof(db.meta_get("die_w", "0").c_str());
    const double die_h = std::atof(db.meta_get("die_h", "0").c_str());
    std::set<std::string> placed_cells;
    for (const auto& c : comps) placed_cells.insert(c.cell);
    const CellRow* top_cell = nullptr;
    for (const auto& c : cells)
        if (!placed_cells.count(c.name) && die_w > 0 &&
            std::fabs(c.width - die_w) < 1e-6 &&
            std::fabs(c.height - die_h) < 1e-6) { top_cell = &c; break; }

    // ── Stream ──────────────────────────────────────────────────────────────
    GdsWriter w;
    w.set_layout_scale(db.import_scale());
    w.header("BUDA");

    for (const auto& cell : cells) {
        if (top_cell && &cell == top_cell) continue;    // emitted as the top
        w.begin_struct(cell.name);
        if (cell.width > 0 || cell.height > 0)
            w.boundary_rect(outline_layer, 0, 0, 0, cell.width, cell.height);
        auto ti = tmpl.find(cell.name);
        if (ti != tmpl.end()) {
            auto ki = children.find(ti->second->id);
            if (ki != children.end())
                for (const ComponentRow* ch : ki->second)
                    place(w, ch, ti->second->x1, ti->second->y1,
                          last_path_segment(ch->name));
        }
        w.end_struct();
        ++stats.n_structures;
    }

    // Top: die extent + root placements + routing + labels. On re-import the
    // top is the die, not a component (G1 semantics), so this round-trips to
    // the same unprefixed depth-0 roots.
    std::string top_name = top_cell ? top_cell->name : "top";
    while (!top_cell && cell_by_name.count(top_name)) top_name = "_" + top_name;
    w.begin_struct(top_name);
    ++stats.n_structures;
    // The die is an EXTENT (import sets it from the top's bbox width/height),
    // so the die rectangle is anchored at the roots' min corner — that keeps
    // bbox_of(top) == die on re-import even when the layout origin is not 0.
    double ox = 0, oy = 0;
    if (!roots.empty()) {
        ox = oy = std::numeric_limits<double>::infinity();
        for (const ComponentRow* r : roots) {
            ox = std::min(ox, r->x1);
            oy = std::min(oy, r->y1);
        }
    }
    if (die_w > 0 && die_h > 0)
        w.boundary_rect(outline_layer, 0, ox, oy, ox + die_w, oy + die_h);
    for (const ComponentRow* r : roots)
        place(w, r, 0, 0, r->name);
    if (n_dim_mismatch > 0)
        stats.warnings.push_back(
            std::to_string(n_dim_mismatch) + " placement(s) have a bbox that "
            "differs from the ORIENTED cell footprint (resized instances, not "
            "a representable rotation) — exported at the bbox origin");

    // Routing from the persisted tables: detailed bit-wires when present,
    // else the abstract bus rectangles.
    auto bundles = db.all_bundles();
    std::sort(bundles.begin(), bundles.end(),
              [](const BundleRow& a, const BundleRow& b) { return a.id < b.id; });
    std::vector<NetSegRow> nsegs;
    std::vector<NetViaRow> nvias;
    for (const auto& b : bundles) {
        auto s = db.net_segments(b.id);
        nsegs.insert(nsegs.end(), s.begin(), s.end());
        auto v = db.net_vias(b.id);
        nvias.insert(nvias.end(), v.begin(), v.end());
    }
    auto emit_rect = [&](int layer, double x1, double y1, double x2, double y2) {
        auto [gl, dt] = gds_pair(layer);
        w.boundary_rect(gl, dt, std::min(x1, x2), std::min(y1, y2),
                        std::max(x1, x2), std::max(y1, y2));
        ++stats.n_wire_shapes;
    };
    // `via_size` is documented and defaulted in MICRONS (`export_gds …
    // via_size <um>`) — it describes the GDS boundary, not the design, so it
    // is the one distance here that is NOT already in layout units.  Convert
    // it, or the writer's ÷scale would take a 1 µm via to 0.0005 µm at 2000
    // DBU/µm (Codex P1 on #645).  Identity at the default scale.
    const double via_size_lu = via_size * db.import_scale();
    auto emit_via = [&](int from_layer, int to_layer, double x, double y) {
        auto [gl, dt] = gds_pair(std::max(from_layer, to_layer));
        const double h = via_size_lu / 2.0;
        w.boundary_rect(gl, dt, x - h, y - h, x + h, y + h);
        ++stats.n_via_shapes;
    };
    if (!nsegs.empty()) {
        stats.stage = "detailed_nuts";
        for (const auto& s : nsegs) emit_rect(s.layer, s.x1, s.y1, s.x2, s.y2);
        for (const auto& v : nvias) emit_via(v.from_layer, v.to_layer, v.x, v.y);
    } else {
        std::vector<BusSegRow> bsegs;
        std::vector<BusViaRow> bvias;
        for (const auto& b : bundles) {
            auto s = db.bus_segments(b.id);
            bsegs.insert(bsegs.end(), s.begin(), s.end());
            auto v = db.bus_vias(b.id);
            bvias.insert(bvias.end(), v.begin(), v.end());
        }
        if (!bsegs.empty()) stats.stage = "abstract_nuts";
        for (const auto& s : bsegs) emit_rect(s.layer, s.x1, s.y1, s.x2, s.y2);
        for (const auto& v : bvias) emit_via(v.from_layer, v.to_layer, v.x, v.y);
    }

    // One TEXT label per pin row: net name at the pin position — re-import
    // in labeled mode recovers the nets/pins (dirs come back UNKNOWN).
    if (write_labels) {
        std::map<int, std::string> net_name;
        for (const auto& n : db.all_nets()) net_name[n.id] = n.name;
        auto pins = db.all_pins();
        std::sort(pins.begin(), pins.end(),
                  [&](const PinRow& a, const PinRow& b) {
                      const std::string &an = net_name[a.net_id],
                                        &bn = net_name[b.net_id];
                      if (an != bn) return an < bn;
                      if (a.comp_id != b.comp_id) return a.comp_id < b.comp_id;
                      if (a.px != b.px) return a.px < b.px;
                      return a.py < b.py;
                  });
        for (const auto& p : pins) {
            if (p.px == -1 && p.py == -1) continue;         // unknown position
            auto it = net_name.find(p.net_id);
            if (it == net_name.end() || it->second.empty()) continue;
            w.text(label_layer, 0, p.px, p.py, it->second);
            ++stats.n_labels;
        }
    }

    w.end_struct();
    w.end_lib();
    w.write(path);
    return stats;
}

}  // namespace buda
