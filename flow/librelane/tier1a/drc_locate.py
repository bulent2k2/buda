#!/usr/bin/env python3
"""Where a top-level KLayout DRC marker IS, and whose metal it is on.

    drc_locate.py <drc.klayout.lyrdb> <top.def> [<cell>.lef ...] [--json out.json]

A top run's KLayout DRC (`62-klayout-drc/reports/drc.klayout.lyrdb`) reports
markers in TOP coordinates on a flattened GDS, so five markers in five
places can be one defect: the same spot of one cell, repeated wherever the
cell is placed (#896: 5 x `m2.2`, all at `acc_cell` local (69.4, 0.0)).
This reads the marker database, the top DEF's COMPONENTS, and -- when the
macro LEFs are given -- each macro's pin and OBS rectangles, and reports per
marker:

  * the instance whose placed box its extent overlaps (most, when it
    straddles a boundary), its cell, and the CELL-LOCAL coordinates (the
    DEF orientation inverted), so repeats collapse;
  * per offending edge, whether it lies on metal the macro's LEF CLAIMS on
    that layer (a pin or OBS rectangle), in a HOLE of the abstract (inside
    the macro's box on no claimed shape -- a spot the router reads as free,
    so the edge may be the top's wire routed into it, or the macro's own
    metal the abstract omits; the GDS decides which, and the nearest
    claimed shape is named so a notch beside a pin reads as one), or
    OUTSIDE the box (the top's routing against the macro edge).  A claimed
    edge against a hole edge is the abstraction-notch shape #896 turned out
    to be: the router overhangs a wire into a corner Magic's LEF left
    uncovered, against the macro's real metal there.  Whether a layer is
    obstructed elsewhere says nothing about THIS spot, which is the
    inference the first cut drew and the N=8 artefacts refuted;
  * the GROUPS: markers with the same cell, layer and local spot (0.1 um),
    which is what "one defect, not five" means.

The layer comes from the category (`m2.2` -> met2, `li1.3` -> li1, `via2.x`
-> via2), which is KLayout's sky130A rule naming; an unknown prefix is kept
as a name and matches no LEF shape.  A marker's geometry is an `edge-pair`
(two edges, each classified), a `polygon`/`box`/`edge` (one shape, its
corners classified together).  No LEF: the location half runs alone.  This
is a READER of a finished run, not a check: it never decides whether the
violation is real, only where and against what.
"""
import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdn_connect as pc   # noqa: E402
import pdn_phase as pp     # noqa: E402

EPS = 1e-6
GROUP_GRID = 0.1           # local-coordinate bin for "the same spot"

_NUM = r"-?\d+(?:\.\d+)?"
_PT = re.compile(r"\(\s*(%s)\s*,\s*(%s)\s*\)" % (_NUM, _NUM))
_PAIR = re.compile(r"\(\s*(%s),(%s);(%s),(%s)\s*\)" % ((_NUM,) * 4))


class InputShape(Exception):
    """An input of a shape this did not expect (exit 2)."""


def layer_of(category):
    """`m2.2` -> met2, `li1.3` -> li1, `via2.1` -> via2, `mcon.1` -> mcon;
    anything else is its own prefix."""
    head = category.split(".", 1)[0].strip().lower()
    m = re.fullmatch(r"m(\d)", head)
    if m:
        return "met" + m.group(1)
    return head


def read_lyrdb(path):
    """[{category, layer, kind, edges:[(x1,y1,x2,y2)], bbox, cell}] in um.
    KLayout's ReportDatabase XML: each <item> has a quoted <category>
    ('m2.2') and <values>/<value> strings such as
    `edge-pair: (x1,y1;x2,y2)/(x3,y3;x4,y4)` or `polygon: (x,y;x,y;...)`."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        raise InputShape(f"{path}: not a KLayout report database (XML): {e}")
    items = []
    for it in root.iter("item"):
        cat = (it.findtext("category") or "").strip().strip("'\"")
        cell = (it.findtext("cell") or "").strip()
        for v in it.iter("value"):
            text = (v.text or "").strip()
            kind, _, geom = text.partition(":")
            kind = kind.strip()
            edges = []
            if kind == "edge-pair":
                halves = [_PAIR.search(h) for h in geom.split("/")]
                if len(halves) != 2 or not all(halves):
                    raise InputShape(f"{path}: cannot read edge-pair {text!r}")
                for m in halves:
                    edges.append(tuple(float(m.group(i)) for i in range(1, 5)))
            elif kind == "edge":
                m = _PAIR.search(geom)
                if not m:
                    raise InputShape(f"{path}: cannot read edge {text!r}")
                edges.append(tuple(float(m.group(i)) for i in range(1, 5)))
            elif kind in ("polygon", "box", "path"):
                pts = [(float(a), float(b)) for a, b in re.findall(
                    r"(%s)\s*,\s*(%s)" % (_NUM, _NUM), geom)]
                if not pts:
                    raise InputShape(f"{path}: cannot read {kind} {text!r}")
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                edges.append((min(xs), min(ys), max(xs), max(ys)))
            else:
                continue                     # text/float values carry no geometry
            xs = [e[0] for e in edges] + [e[2] for e in edges]
            ys = [e[1] for e in edges] + [e[3] for e in edges]
            items.append({"category": cat, "layer": layer_of(cat), "kind": kind, "cell": cell,
                          "edges": edges, "bbox": (min(xs), min(ys), max(xs), max(ys))})
    return items


def read_def(path):
    text = open(path).read()
    dbu = pc.read_units(text)
    comps = pc.read_components(text, dbu)
    return comps[0] if isinstance(comps, tuple) else comps


def _inverse(orient, w, h):
    """Placed-frame (x, y) -> cell-local, the inverse of `pdn_phase.orient_rect`
    on points: the forward map is affine, so three probes fix it."""
    def fwd(x, y):
        r = pp.orient_rect((x, y, x, y), orient, w, h)
        return r[0], r[1]
    bx, by = fwd(0.0, 0.0)
    ax1, ay1 = fwd(1.0, 0.0)
    ax2, ay2 = fwd(0.0, 1.0)
    a, b, c, d = ax1 - bx, ax2 - bx, ay1 - by, ay2 - by      # [[a b][c d]] maps local -> placed
    det = a * d - b * c
    return lambda x, y: (((x - bx) * d - (y - by) * b) / det, (-(x - bx) * c + (y - by) * a) / det)


def _inside(x, y, r):
    return r[0] - EPS <= x <= r[2] + EPS and r[1] - EPS <= y <= r[3] + EPS


def locate(items, comps, lefs):
    """Every marker with its instance, cell-local coordinates and per-edge
    classification against the LEF (when the cell's LEF is known)."""
    placed = []
    for c in comps:
        m = lefs.get(c["cell"])
        if m:
            w, h = pp.placed_size(c["orient"], *m["size"])
        else:
            w = h = None
        placed.append({**c, "w": w, "h": h})
    out = []
    for it in items:
        bx1, by1, bx2, by2 = it["bbox"]
        cx, cy = (bx1 + bx2) / 2, (by1 + by2) / 2
        # the holder is the macro whose box the marker's extent overlaps most
        # -- not the one holding its CENTRE: an edge pair straddling the
        # boundary with the outer edge farther out has its centre outside,
        # which is exactly the macro-versus-top-routing case (Codex #900)
        holder, best = None, None
        for c in placed:
            if c["w"] is None:
                continue
            ox = min(bx2, c["x"] + c["w"]) - max(bx1, c["x"])
            oy = min(by2, c["y"] + c["h"]) - max(by1, c["y"])
            if ox < -EPS or oy < -EPS:
                continue
            score = (max(ox, 0.0) * max(oy, 0.0), max(ox, 0.0) + max(oy, 0.0),
                     1 if _inside(cx, cy, (c["x"], c["y"], c["x"] + c["w"], c["y"] + c["h"])) else 0)
            if best is None or score > best:
                holder, best = c, score
        row = {**{k: it[k] for k in ("category", "layer", "kind", "edges", "bbox")},
               "instance": None, "cell": None, "orient": None, "local": None, "edge_verdicts": []}
        if holder is None:
            # nearest placed box, for a marker in the channel between macros
            best = None
            for c in placed:
                if c["w"] is None:
                    continue
                dx = max(c["x"] - cx, 0.0, cx - (c["x"] + c["w"]))
                dy = max(c["y"] - cy, 0.0, cy - (c["y"] + c["h"]))
                d = max(dx, dy)
                if best is None or d < best[0]:
                    best = (d, c)
            if best:
                row["nearest"] = {"instance": best[1]["name"], "cell": best[1]["cell"],
                                  "distance": round(best[0], 3)}
            out.append(row)
            continue
        m = lefs[holder["cell"]]
        inv = _inverse(holder["orient"], *m["size"])
        lx1, ly1 = inv(bx1 - holder["x"], by1 - holder["y"])
        lx2, ly2 = inv(bx2 - holder["x"], by2 - holder["y"])
        row.update({"instance": holder["name"], "cell": holder["cell"], "orient": holder["orient"],
                    "local": (round(min(lx1, lx2), 3), round(min(ly1, ly2), 3),
                              round(max(lx1, lx2), 3), round(max(ly1, ly2), 3))})
        # the LEF's shapes on the marker's layer, in the instance's frame
        claimed = []
        for pname, pin in m["pins"].items():
            for (layer, *r) in pin["rects"]:
                if layer == it["layer"]:
                    claimed.append((f"pin {pname}", pp.orient_rect(tuple(r), holder["orient"], *m["size"])))
        for (layer, *r) in m.get("obs", []):
            if layer == it["layer"]:
                claimed.append(("OBS", pp.orient_rect(tuple(r), holder["orient"], *m["size"])))
        box = (holder["x"], holder["y"], holder["x"] + holder["w"], holder["y"] + holder["h"])
        for (ex1, ey1, ex2, ey2) in it["edges"]:
            mx, my = (ex1 + ex2) / 2, (ey1 + ey2) / 2
            lx, ly = mx - holder["x"], my - holder["y"]
            on = [what for what, r in claimed if _inside(lx, ly, r)]
            inside = _inside(mx, my, box)
            nearest = None
            if not on and claimed:
                for what, r in claimed:
                    d = max(r[0] - lx, 0.0, lx - r[2], r[1] - ly, ly - r[3])
                    if nearest is None or d < nearest[1]:
                        nearest = (what, round(d, 3))
            if on:
                verdict = "macro-lef"
            elif inside:
                verdict = "hole"
            else:
                verdict = "outside"
            row["edge_verdicts"].append({"edge": (ex1, ey1, ex2, ey2), "inside_box": inside,
                                         "on": on, "verdict": verdict,
                                         "nearest_claimed": nearest})
        kinds = {ev["verdict"] for ev in row["edge_verdicts"]}
        row["shape"] = ("notch" if kinds == {"macro-lef", "hole"} else
                        "hole" if kinds == {"hole"} else
                        "macro" if kinds == {"macro-lef"} else
                        "boundary" if "outside" in kinds else "mixed")
        out.append(row)
    return out


def groups(rows):
    """Markers with the same cell, layer and local spot, to GROUP_GRID."""
    g = {}
    for r in rows:
        if r["instance"] is None:
            continue
        key = (r["cell"], r["layer"], round(r["local"][0] / GROUP_GRID) * GROUP_GRID,
               round(r["local"][1] / GROUP_GRID) * GROUP_GRID)
        g.setdefault(key, []).append(r["instance"])
    return [{"cell": k[0], "layer": k[1], "local": (round(k[2], 3), round(k[3], 3)), "instances": v}
            for k, v in sorted(g.items(), key=lambda kv: (-len(kv[1]), kv[0]))]


VERDICT_TEXT = {
    "macro-lef": "on metal the macro's LEF claims",
    "hole": "inside the macro box on NO LEF shape -- a spot the abstract leaves free, so the router may "
            "have put the top's wire here, or it is macro metal the abstract omits; the GDS decides",
    "outside": "outside the macro box -- the top's routing against its edge",
}
SHAPE_TEXT = {
    "notch": "claimed metal against an abstract HOLE: the router overhung a wire into a corner the LEF "
             "leaves uncovered, against the macro's real metal there (the #896 shape) -- the fix is in "
             "the abstract (cover the metal, or read the bloated `<cell>.openroad.lef`), not the placement",
    "hole": "both edges in an abstract hole: the GDS says whose metal each is",
    "macro": "both edges on LEF-claimed metal: the macro's own DRC question (check its block run)",
    "boundary": "one edge outside the box: the top's routing against the macro edge",
}


def report(rows, grp, lefs, out=sys.stdout):
    p = lambda *a: print(*a, file=out)
    cats = sorted({r["category"] for r in rows})
    located = [r for r in rows if r["instance"]]
    cells = sorted({r["cell"] for r in located})
    p(f"drc_locate: {len(rows)} marker(s), {len(cats)} categor{'y' if len(cats) == 1 else 'ies'} "
      f"({', '.join(cats)}), {len(located)} inside a placed macro ({len(cells)} cell type(s)"
      f"{': ' + ', '.join(cells) if cells else ''}), {len(rows) - len(located)} elsewhere"
      + ("" if lefs else "; no LEF given, so no edge is classified"))
    for r in rows:
        b = r["bbox"]
        head = f"{r['category']} ({b[0]:.3f},{b[1]:.3f})-({b[2]:.3f},{b[3]:.3f})"
        if r["instance"] is None:
            near = r.get("nearest")
            p(f"{head} -> no macro holds it" + (f"; nearest {near['instance']} [{near['cell']}] "
                                                f"{near['distance']:.3f} um away" if near else ""))
            continue
        l = r["local"]
        p(f"{head} -> {r['instance']} [{r['cell']} {r['orient']}] local ({l[0]:.3f},{l[1]:.3f})-"
          f"({l[2]:.3f},{l[3]:.3f})")
        for n, ev in enumerate(r["edge_verdicts"]):
            e = ev["edge"]
            near = ev.get("nearest_claimed")
            p(f"    edge {'AB'[n] if len(r['edge_verdicts']) == 2 else n}: ({e[0]:.3f},{e[1]:.3f})-"
              f"({e[2]:.3f},{e[3]:.3f}) {VERDICT_TEXT[ev['verdict']]}"
              + (f" ({', '.join(ev['on'])})" if ev["on"] else "")
              + (f"; nearest claimed shape {near[0]} at {near[1]:.3f} um" if near and ev["verdict"] == "hole"
                 else ""))
        if r.get("shape") in SHAPE_TEXT:
            p(f"    => {SHAPE_TEXT[r['shape']]}")
    for g in grp:
        p(f"GROUP {g['cell']} {g['layer']} local ~({g['local'][0]:.1f},{g['local'][1]:.1f}): "
          f"{len(g['instances'])} marker(s) in {', '.join(g['instances'])}"
          + (" -- one defect, repeated per instance" if len(g["instances"]) > 1 else ""))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("lyrdb", help="KLayout DRC report database (drc.klayout.lyrdb)")
    ap.add_argument("def_", metavar="DEF", help="the top DEF whose COMPONENTS place the macros")
    ap.add_argument("lef", nargs="*", help="the macros' LEFs (final/lef/<cell>.lef); optional")
    ap.add_argument("--json", metavar="OUT", help="write every marker's location and verdicts")
    a = ap.parse_args(argv)
    try:
        items = read_lyrdb(a.lyrdb)
        comps = read_def(a.def_)
        lefs = {}
        for pth in a.lef:
            if not os.path.exists(pth):
                raise InputShape(f"{pth}: no such LEF")
            lefs.update(pp.read_lef(pth))
    except (InputShape, pc.InputShape, pp.InputShape) as e:
        print(f"drc_locate: ERROR: {e}", file=sys.stderr)
        return 2
    rows = locate(items, comps, lefs)
    grp = groups(rows)
    report(rows, grp, lefs)
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"markers": rows, "groups": grp}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
