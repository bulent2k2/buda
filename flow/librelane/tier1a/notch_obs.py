#!/usr/bin/env python3
"""Obstruct the metal the abstract OMITS, and nothing else -- the third
candidate fix for #896, the one the two measured ones point at.

    notch_obs.py <cell.gds> <magic.lef> <out.lef> [--layer met2] [--gds-layer 69/20] [--json out.json]

WHY.  Magic's LEF (`final/lef/<cell>.lef`, what the top reads) abstracts a
block's metal rect by rect and leaves a NOTCH uncovered where a pin rect
ends and the OBS blanket begins; the macro's real metal sits in it, the
top's router reads the notch as free and overhangs a wire into it, and the
result is an `m2.2` marker inside the macro box (#896: `acc_cell` local
x 69.37-69.65, y 0-0.56 beside pin `in[22]`, 0.130 um against 0.140; the
same beside `wbuf_cell`'s `rst`).  Both blanket fixes FAIL, measured
(librelane_hier_flow.md s11 item 13): the whole `-bloat_occupied_layers`
abstract adds met4/met5 OBS the PDN cannot cross (125,800 power-grid
violations), and a met2-only blanket closes the DRC but contradicts what the
arm does on met2 -- the top routes met2 to the N/S bus pins and over the
macro -- so Magic counts 6,233 illegal `obsm2`/`metal2` overlaps.

So the fix has to be exactly the DIFFERENCE: every piece of the cell's real
metal on the layer that no LEF shape (pin or OBS) claims, added to the OBS
as rectangles.  That leaves every place the top legitimately routes free
and closes every notch, and it is what this computes:

  * the cell's metal on the layer, read from its GDS (BOUNDARY records on
    the mapped layer/datatype, flattened through SREF/AREF -- a standard
    cell's own shapes live in subcells; sky130's have no met2, but the
    reader does not assume it);
  * the LEF's claim on that layer: every PIN's rects (any USE -- a signal
    pin claims its metal too) and every OBS rect;
  * the per-rectangle difference, coalesced, written into the OBS block
    under a new `LAYER <layer>` clause and reported piece by piece.

What it REFUSES: a GDS shape on the layer that is not an axis-aligned
rectangle (a BOUNDARY with more than four corners, or a PATH), because a
bounding box would over-claim and a guessed decomposition would be a
different fix than the one measured to be needed; Magic writes rectangles,
and a file that does not is reported with the count.  A LEF that draws the
layer's OBS as a POLYGON is refused by the LEF reader the same way.

What it does NOT decide: whether the patched abstract closes the marker
without costing pin access.  That is a top run (`harm.sh` with the patched
LEF in place of Magic's), and this prints what the run has to confirm.
"""
import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdn_phase import InputShape, read_lef   # noqa: E402

EPS = 1e-6
GRID = 0.005
SKY130_GDS = {"li1": (67, 20), "met1": (68, 20), "met2": (69, 20), "met3": (70, 20),
              "met4": (71, 20), "met5": (72, 20)}

# GDSII record types (type byte << 8 | data type byte)
_HEADER, _BGNLIB, _LIBNAME, _UNITS, _ENDLIB = 0x0002, 0x0102, 0x0206, 0x0305, 0x0400
_BGNSTR, _STRNAME, _ENDSTR = 0x0502, 0x0606, 0x0700
_BOUNDARY, _PATH, _SREF, _AREF, _TEXT = 0x0800, 0x0900, 0x0A00, 0x0B00, 0x0C00
_LAYER, _DATATYPE, _XY, _ENDEL, _SNAME = 0x0D02, 0x0E02, 0x1003, 0x1100, 0x1206
_COLROW, _STRANS, _MAG, _ANGLE = 0x1302, 0x1A01, 0x1B05, 0x1C05


def _real8(b):
    """GDSII 8-byte real: sign, 7-bit excess-64 exponent (base 16), 56-bit mantissa."""
    (w,) = struct.unpack(">Q", b)
    sign = -1.0 if w >> 63 else 1.0
    exp = ((w >> 56) & 0x7F) - 64
    mant = (w & ((1 << 56) - 1)) / float(1 << 56)
    return sign * mant * (16.0 ** exp)


def read_gds(path):
    """{structure: {"elems": [...], "refs": [...]}} plus the dbu in um.
    An element is ("boundary", layer, datatype, [(x, y), ...]) or
    ("path", layer, datatype, n_points); a ref is (sname, x, y, angle_deg,
    mirror_x, mag, cols, rows, (col_dx, col_dy), (row_dx, row_dy)) in dbu,
    with cols=rows=1 and zero vectors for an SREF.  The AREF vectors keep
    BOTH components -- a rotated lattice has a column vector with a y part
    -- and MAG is read and applied, not just recognised (Codex #902)."""
    data = open(path, "rb").read()
    pos, dbu_um, structs, cur, elem, refs = 0, None, {}, None, None, None
    while pos + 4 <= len(data):
        length, rt = struct.unpack(">HH", data[pos:pos + 4])
        if length < 4:
            raise InputShape(f"{path}: malformed GDS record at byte {pos}")
        body = data[pos + 4:pos + length]
        pos += length
        if rt == _UNITS:
            dbu_um = _real8(body[:8])                     # user units (um) per database unit
        elif rt == _BGNSTR:
            cur = {"elems": [], "refs": []}
        elif rt == _STRNAME and cur is not None:
            structs[body.rstrip(b"\x00").decode()] = cur
        elif rt == _ENDSTR:
            cur = None
        elif rt in (_BOUNDARY, _PATH, _SREF, _AREF, _TEXT):
            elem = {"kind": rt, "layer": None, "datatype": None, "xy": [], "sname": None,
                    "angle": 0.0, "mirror": False, "mag": 1.0, "cols": 1, "rows": 1}
        elif rt == _LAYER and elem is not None:
            elem["layer"] = struct.unpack(">h", body[:2])[0]
        elif rt == _DATATYPE and elem is not None:
            elem["datatype"] = struct.unpack(">h", body[:2])[0]
        elif rt == _SNAME and elem is not None:
            elem["sname"] = body.rstrip(b"\x00").decode()
        elif rt == _STRANS and elem is not None:
            elem["mirror"] = bool(struct.unpack(">H", body[:2])[0] & 0x8000)
        elif rt == _ANGLE and elem is not None:
            elem["angle"] = _real8(body[:8])
        elif rt == _MAG and elem is not None:
            elem["mag"] = _real8(body[:8])
        elif rt == _COLROW and elem is not None:
            elem["cols"], elem["rows"] = struct.unpack(">hh", body[:4])
        elif rt == _XY and elem is not None:
            n = len(body) // 8
            elem["xy"] = [struct.unpack(">ii", body[8 * i:8 * i + 8]) for i in range(n)]
        elif rt == _ENDEL and elem is not None and cur is not None:
            if elem["kind"] == _BOUNDARY:
                cur["elems"].append(("boundary", elem["layer"], elem["datatype"], elem["xy"]))
            elif elem["kind"] == _PATH:
                cur["elems"].append(("path", elem["layer"], elem["datatype"], len(elem["xy"])))
            elif elem["kind"] in (_SREF, _AREF):
                xy = elem["xy"]
                if elem["kind"] == _SREF:
                    cur["refs"].append((elem["sname"], xy[0][0], xy[0][1], elem["angle"],
                                        elem["mirror"], elem["mag"], 1, 1, (0, 0), (0, 0)))
                else:
                    cols, rows = elem["cols"], elem["rows"]
                    # p2 = origin + cols * column vector, p3 = origin + rows *
                    # row vector, both in the PARENT's frame: keep both parts
                    cv = ((xy[1][0] - xy[0][0]) / cols, (xy[1][1] - xy[0][1]) / cols) if cols else (0, 0)
                    rv = ((xy[2][0] - xy[0][0]) / rows, (xy[2][1] - xy[0][1]) / rows) if rows else (0, 0)
                    cur["refs"].append((elem["sname"], xy[0][0], xy[0][1], elem["angle"],
                                        elem["mirror"], elem["mag"], cols, rows, cv, rv))
            elem = None
    if dbu_um is None:
        raise InputShape(f"{path}: no UNITS record -- not a GDSII file")
    if not structs:
        raise InputShape(f"{path}: no structure in the GDS")
    return structs, dbu_um


def _xform(pt, angle, mirror):
    x, y = pt
    if mirror:
        y = -y
    a = int(round(angle)) % 360
    if a == 90:
        x, y = -y, x
    elif a == 180:
        x, y = -x, -y
    elif a == 270:
        x, y = y, -x
    elif a != 0:
        raise InputShape(f"a reference rotated by {angle} degrees: only multiples of 90 keep a "
                         f"rectangle a rectangle")
    return x, y


def layer_shapes(structs, top, layer_dt, dbu_um, depth=0, seen=None):
    """Every shape on (layer, datatype) under `top`, flattened, in um:
    ([(x1, y1, x2, y2)], [refusals]) -- a refusal names a non-rectangle."""
    if top not in structs:
        raise InputShape(f"GDS has no structure {top!r}; it has {sorted(structs)[:8]}")
    if depth > 64:
        raise InputShape("reference depth over 64 -- a cyclic GDS")
    rects, bad = [], []
    st = structs[top]
    for e in st["elems"]:
        if (e[1], e[2]) != layer_dt:
            continue
        if e[0] == "path":
            bad.append(f"{top}: a PATH with {e[3]} points on the layer")
            continue
        pts = [(x * dbu_um, y * dbu_um) for x, y in e[3]]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        xs, ys = sorted({round(p[0], 6) for p in pts}), sorted({round(p[1], 6) for p in pts})
        if len(pts) != 4 or len(xs) != 2 or len(ys) != 2 or \
                {(round(p[0], 6), round(p[1], 6)) for p in pts} != {(x, y) for x in xs for y in ys}:
            bad.append(f"{top}: a BOUNDARY with {len(pts)} corners that is not an axis-aligned rectangle "
                       f"(bbox {min(p[0] for p in pts):.3f},{min(p[1] for p in pts):.3f} - "
                       f"{max(p[0] for p in pts):.3f},{max(p[1] for p in pts):.3f})")
            continue
        rects.append((xs[0], ys[0], xs[1], ys[1]))
    for (sname, ox, oy, angle, mirror, mag, cols, rows, cv, rv) in st["refs"]:
        sub, sub_bad = layer_shapes(structs, sname, layer_dt, dbu_um, depth + 1)
        bad.extend(sub_bad)
        if mag <= 0:
            raise InputShape(f"{top}: a reference to {sname} with MAG {mag}")
        for c in range(cols):
            for r in range(rows):
                dx = (ox + c * cv[0] + r * rv[0]) * dbu_um
                dy = (oy + c * cv[1] + r * rv[1]) * dbu_um
                for (x1, y1, x2, y2) in sub:
                    # GDS applies mirror, then MAG, then the rotation, then the
                    # translation; on an axis-aligned rectangle the uniform
                    # scale commutes with the rest
                    p = [_xform((x * mag, y * mag), angle, mirror) for x in (x1, x2) for y in (y1, y2)]
                    rects.append((min(q[0] for q in p) + dx, min(q[1] for q in p) + dy,
                                  max(q[0] for q in p) + dx, max(q[1] for q in p) + dy))
    return rects, bad


def subtract(rect, cut):
    """`rect` minus `cut`: up to four rectangles (the standard split)."""
    x1, y1, x2, y2 = rect
    cx1, cy1, cx2, cy2 = cut
    if cx1 >= x2 - EPS or cx2 <= x1 + EPS or cy1 >= y2 - EPS or cy2 <= y1 + EPS:
        return [rect]
    out = []
    if cy1 > y1 + EPS:
        out.append((x1, y1, x2, cy1))                       # below
    if cy2 < y2 - EPS:
        out.append((x1, cy2, x2, y2))                       # above
    lo, hi = max(y1, cy1), min(y2, cy2)
    if cx1 > x1 + EPS:
        out.append((x1, lo, cx1, hi))                       # left
    if cx2 < x2 - EPS:
        out.append((cx2, lo, x2, hi))                       # right
    return out


def difference(metal, claimed):
    """Every piece of `metal` no rectangle of `claimed` covers, coalesced."""
    pieces = []
    for m in metal:
        todo = [m]
        for c in claimed:
            todo = [p for r in todo for p in subtract(r, c)]
        pieces.extend(todo)
    pieces = [(round(a, 4), round(b, 4), round(c, 4), round(d, 4)) for (a, b, c, d) in pieces
              if c - a > EPS and d - b > EPS]
    # coalesce pairs that share a full edge (the split leaves many)
    merged = True
    while merged:
        merged = False
        for i in range(len(pieces)):
            for j in range(i + 1, len(pieces)):
                a, b = pieces[i], pieces[j]
                if abs(a[1] - b[1]) < EPS and abs(a[3] - b[3]) < EPS and \
                        (abs(a[2] - b[0]) < EPS or abs(b[2] - a[0]) < EPS):
                    pieces[i] = (min(a[0], b[0]), a[1], max(a[2], b[2]), a[3])
                elif abs(a[0] - b[0]) < EPS and abs(a[2] - b[2]) < EPS and \
                        (abs(a[3] - b[1]) < EPS or abs(b[3] - a[1]) < EPS):
                    pieces[i] = (a[0], min(a[1], b[1]), a[2], max(a[3], b[3]))
                else:
                    continue
                del pieces[j]
                merged = True
                break
            if merged:
                break
    return sorted(set(pieces))


def lef_polygons_on(text, cell, layer):
    """How many POLYGON shapes the LEF draws on `layer` inside MACRO `cell`,
    in a PIN or in the OBS.  `read_lef` refuses a polygon only in a POWER
    pin (its own concern); here a SIGNAL pin's polygon matters just as much,
    because an unread pin shape is metal the difference would then claim as
    OBS -- over the pin itself (Codex #902)."""
    n, in_macro, cur = 0, False, None
    for line in text.splitlines():
        t = line.split()
        if not t:
            continue
        if t[0] == "MACRO" and len(t) > 1:
            in_macro = t[1] == cell
        elif t[0] == "END" and len(t) > 1 and t[1] == cell:
            in_macro = False
        elif in_macro and t[0] == "LAYER" and len(t) > 1:
            cur = t[1].rstrip(";")
        elif in_macro and t[0] == "POLYGON" and cur == layer:
            n += 1
    return n


def patch_lef(text, cell, layer, rects):
    """Magic's LEF with `rects` appended to `cell`'s OBS under a new LAYER
    clause; every other byte unchanged."""
    lines = text.splitlines(True)
    start = next((i for i, l in enumerate(lines) if l.split() and l.split()[0] == "MACRO"
                  and len(l.split()) > 1 and l.split()[1] == cell), None)
    if start is None:
        raise InputShape(f"LEF has no MACRO {cell}")
    end = next((i for i in range(start, len(lines)) if lines[i].split()[:2] == ["END", cell]), None)
    if end is None:
        raise InputShape(f"LEF's MACRO {cell} has no END {cell}")
    obs = next((i for i in range(start, end) if lines[i].split() == ["OBS"]), None)
    block = [f"    LAYER {layer} ;\n"] + [f"      RECT {a:.3f} {b:.3f} {c:.3f} {d:.3f} ;\n"
                                          for (a, b, c, d) in rects]
    if obs is None:
        ins = ["  OBS\n"] + block + ["  END\n"]
        lines[end:end] = ins
    else:
        lines[obs + 1:obs + 1] = block
    return "".join(lines)


def run(gds_path, lef_path, layer, gds_layer):
    lefs = read_lef(lef_path)
    if len(lefs) != 1:
        raise InputShape(f"{lef_path}: expected ONE macro, found {sorted(lefs)}")
    cell = next(iter(lefs))
    macro = lefs[cell]
    structs, dbu_um = read_gds(gds_path)
    top = cell if cell in structs else None
    if top is None:
        # the top structure is the one no other structure references
        referenced = {r[0] for st in structs.values() for r in st["refs"]}
        tops = [n for n in structs if n not in referenced]
        if len(tops) != 1:
            raise InputShape(f"{gds_path}: no structure named {cell} and no single unreferenced top "
                             f"({tops[:8]})")
        top = tops[0]
    n_poly = lef_polygons_on(open(lef_path).read(), cell, layer)
    if n_poly:
        raise InputShape(f"{lef_path}: MACRO {cell} draws {n_poly} {layer} shape(s) as POLYGON; this reads "
                         f"RECTs only, and a pin shape it cannot read is metal the difference would then "
                         f"obstruct -- over the pin itself.  Write the LEF with rectangles")
    metal, bad = layer_shapes(structs, top, gds_layer, dbu_um)
    if bad:
        raise InputShape(f"{gds_path}: {len(bad)} shape(s) on {layer} that are not rectangles -- a bbox "
                         f"would over-claim and a guessed decomposition is a different fix; first: "
                         + "; ".join(bad[:3]))
    if not metal:
        raise InputShape(f"{gds_path}: no {layer} ({gds_layer[0]}/{gds_layer[1]}) rectangle under {top} -- "
                         f"wrong layer map, or the cell draws none")
    claimed = [(r[1], r[2], r[3], r[4]) for pin in macro["pins"].values() for r in pin["rects"]
               if r[0] == layer]
    n_pin = len(claimed)
    claimed += [(r[1], r[2], r[3], r[4]) for r in macro["obs"] if r[0] == layer]
    pieces = difference(metal, claimed)
    area = sum((c - a) * (d - b) for (a, b, c, d) in pieces)
    return {"cell": cell, "top_structure": top, "layer": layer, "gds_layer": list(gds_layer),
            "metal_rects": len(metal), "claimed_pin_rects": n_pin, "claimed_obs_rects": len(claimed) - n_pin,
            "uncovered": [list(p) for p in pieces], "uncovered_area": round(area, 4),
            "size": list(macro["size"])}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("gds", help="the hardened block's final GDS")
    ap.add_argument("lef", help="Magic's LEF for it (final/lef/<cell>.lef)")
    ap.add_argument("out", help="the patched LEF to write")
    ap.add_argument("--layer", default="met2", help="the LEF layer with the notch (met2)")
    ap.add_argument("--gds-layer", default=None, metavar="L/DT",
                    help="the GDS layer/datatype of that metal (sky130A: met2=69/20)")
    ap.add_argument("--json", metavar="OUT", help="write the pieces as JSON")
    a = ap.parse_args(argv)
    try:
        if a.gds_layer:
            l, _, dt = a.gds_layer.partition("/")
            gl = (int(l), int(dt or 0))
        elif a.layer in SKY130_GDS:
            gl = SKY130_GDS[a.layer]
        else:
            raise InputShape(f"no GDS layer map for {a.layer}; pass --gds-layer L/DT")
        for pth in (a.gds, a.lef):
            if not os.path.exists(pth):
                raise InputShape(f"{pth}: no such file")
        res = run(a.gds, a.lef, a.layer, gl)
        text = open(a.lef).read()
        open(a.out, "w").write(patch_lef(text, res["cell"], a.layer, [tuple(p) for p in res["uncovered"]]))
    except InputShape as e:
        print(f"notch_obs: ERROR: {e}", file=sys.stderr)
        return 2
    p = print
    p(f"notch_obs: {res['cell']} ({res['size'][0]} x {res['size'][1]}), {a.layer} = GDS "
      f"{res['gds_layer'][0]}/{res['gds_layer'][1]} under structure {res['top_structure']}: "
      f"{res['metal_rects']} metal rect(s); LEF claims {res['claimed_pin_rects']} pin + "
      f"{res['claimed_obs_rects']} OBS rect(s) on it")
    if not res["uncovered"]:
        p(f"  every {a.layer} rectangle is covered by a pin or OBS rect: no notch on this layer, "
          f"{a.out} is Magic's LEF unchanged")
    else:
        p(f"  {len(res['uncovered'])} uncovered piece(s), {res['uncovered_area']:.4f} um^2 in all, added to "
          f"the OBS as `LAYER {a.layer}` RECTs -> {a.out}")
        for (x1, y1, x2, y2) in res["uncovered"][:40]:
            p(f"    RECT {x1:.3f} {y1:.3f} {x2:.3f} {y2:.3f}   ({x2 - x1:.3f} x {y2 - y1:.3f})")
        if len(res["uncovered"]) > 40:
            p(f"    ... {len(res['uncovered']) - 40} more (--json)")
        p("  what a top run has to confirm: the marker gone, Magic's overlap count unchanged, and the "
          "top's wire within noise -- the blanket cost +0.15% wire and 6,233 overlaps; this claims only "
          "the metal the abstract omits")
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
