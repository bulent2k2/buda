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
#include <fstream>
#include <functional>
#include <limits>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>

namespace buda {

namespace {

// ── Record types (GDSII stream) ─────────────────────────────────────────────
enum : uint8_t {
    R_HEADER = 0x00, R_BGNLIB = 0x01, R_UNITS = 0x03, R_ENDLIB = 0x04,
    R_BGNSTR = 0x05, R_STRNAME = 0x06, R_ENDSTR = 0x07,
    R_BOUNDARY = 0x08, R_PATH = 0x09, R_SREF = 0x0A, R_AREF = 0x0B,
    R_TEXT = 0x0C, R_XY = 0x10, R_ENDEL = 0x11, R_SNAME = 0x12,
    R_COLROW = 0x13, R_STRANS = 0x1A, R_MAG = 0x1B, R_ANGLE = 0x1C,
    R_PROPVALUE = 0x2C, R_BOX = 0x2D,
};

struct Rec {
    uint8_t type = 0;
    const uint8_t* data = nullptr;
    size_t len = 0;

    int16_t i16(size_t off = 0) const {
        return (int16_t)((data[off] << 8) | data[off + 1]);
    }
    int32_t i32(size_t off = 0) const {
        return (int32_t)((uint32_t(data[off]) << 24) | (uint32_t(data[off + 1]) << 16) |
                         (uint32_t(data[off + 2]) << 8) | data[off + 3]);
    }
    // Excess-64 base-16 8-byte real.
    double real8(size_t off = 0) const {
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

struct GStruct {
    std::string name;
    BBox geom;                                // own BOUNDARY/BOX/PATH extent
    std::vector<Ref> refs;
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

}  // namespace

GdsImportStats import_gds(BDB& db, const std::string& path) {
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
    bool in_text = false, in_geom = false;
    bool aref = false;
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
            case R_BOUNDARY: case R_BOX: case R_PATH:
                in_geom = true;
                break;
            case R_TEXT:
                in_text = true;
                ++stats.n_texts;               // consumed in Phase G2
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
                if (cur_ref) {
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
                } else if (in_geom && cur) {
                    for (size_t i = 0; i < npts; ++i) {
                        double x, y;
                        xy(i, x, y);
                        cur->geom.grow(x, y);
                    }
                }
                // TEXT XY deliberately ignored: labels must not grow footprints.
                break;
            }
            case R_ENDEL:
                cur_ref = nullptr;
                in_text = in_geom = false;
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
    (void)in_text;
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

    std::function<void(const GStruct&, const XForm&, const std::string&,
                       const std::string&)> elaborate =
        [&](const GStruct& s, const XForm& xf, const std::string& comp_path,
            const std::string& parent_path) {
        BBox bb = xf.apply(bbox_of(s.name));
        if (bb.empty()) bb = BBox{0, 0, 0, 0};
        db.add_comp(comp_path, s.name, parent_path, bb.x1, bb.y1, bb.x2, bb.y2,
                    s.refs.empty());
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
                    elaborate(child, t, comp_path + "/" + nm, comp_path);
                }
        }
    };

    for (const GStruct& s : structs) {
        if (referenced.count(s.name)) continue;
        stats.tops.push_back(s.name);
        elaborate(s, XForm{}, s.name, "");
    }

    return stats;
}

}  // namespace buda
