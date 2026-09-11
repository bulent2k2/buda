#!/usr/bin/env python3
"""Do BUDA's corridors ever meet the notch metal the top's abstract adds?

`guides.sh` plans corridors against MAGIC's abstract; the top routes against
the NOTCH-PATCHED one (`notch.sh`, #896).  Same layer, and the two views
differ exactly INSIDE the macros -- the notch adds metal there and the
corridor plan never saw it.  The inconsistency can only bite where a
corridor overlaps a notch piece, so this measures that intersection.

Read-only: every input is an artefact the arm already wrote.

WHY IT REFUSES RATHER THAN REPORTS ZERO.  The answer this tool exists to
give is a zero, which makes it the exact shape that a broken instrument
also produces -- and that is not hypothetical here.  #905 was opened on a
"pdngen made none of 512" that turned out to be a reporting defect, and
while chasing it a strap walk in this same tree found ZERO straps (a regex
that missed `+ SHAPE STRIPE`) and duly reported every via as having no
strap on either side, which read exactly like confirmation.  So a zero is
only worth printing when the two sets COULD have met:

  * no notch piece placed  -> refuse (nothing to intersect)
  * no corridor read       -> refuse (nothing to intersect)
  * pieces and corridors share no layer -> refuse, naming both layer sets

and the liveness line keys on the PIECES, not on macro boxes.  An earlier
cut computed it from corridors-vs-macro-boxes and so printed "the check was
live: True" on an arm carrying no notch data at all (#913).
"""
import argparse
import collections
import glob
import json
import os
import sys


def _load_placement(arm):
    p = os.path.join(arm, "top", "placement.json")
    if not os.path.isfile(p):
        sys.exit("corridor_notch_check: no %s -- is %s an arm directory?" % (p, arm))
    pl = json.load(open(p))
    # The transform below is the IDENTITY one.  A rotated instance needs the
    # DEF orientation table (tools/def_orient.py); the BDB table in
    # src/orient_rect.py differs on all four flips, so guessing is worse than
    # refusing.  placement.json carries DEF tokens.
    bad = sorted({i["orient"] for i in pl["instances"]} - {"N"})
    if bad:
        sys.exit("corridor_notch_check: instance orientation(s) %s need the DEF "
                 "orient transform; this check implements N only" % ", ".join(bad))
    return pl


def notch_pieces(arm, insts, dbu, layers=("met2", "met3")):
    """Every cell's `uncovered` rects, placed in each instance's frame (DBU).

    The per-cell JSON is `notch_obs.py`'s, beside the patched LEF.  The run
    tag is DISCOVERED rather than assumed `h`: an arm whose blocks hardened
    under another tag would otherwise match no file and read as "no notch
    metal anywhere", which is the silent zero above wearing a second hat.
    """
    per_cell, found = {}, {}
    for cell in sorted({i["cell"] for i in insts}):
        for lay in layers:
            hits = sorted(glob.glob(os.path.join(
                arm, cell, "runs", "*", "final", "lef", "%s.notch.%s.json" % (cell, lay))))
            if not hits:
                continue
            found.setdefault(cell, []).append(hits[0])
            per_cell.setdefault(cell, {})[lay] = json.load(open(hits[0])).get("uncovered", [])
    pieces = collections.defaultdict(list)
    for i in insts:
        ox, oy = i["x"], i["y"]
        for lay, rects in per_cell.get(i["cell"], {}).items():
            for (x1, y1, x2, y2) in rects:
                pieces[lay].append(((ox + x1) * dbu, (oy + y1) * dbu,
                                    (ox + x2) * dbu, (oy + y2) * dbu))
    return pieces, found


def corridors(arm):
    p = os.path.join(arm, "top", "out", "buda_guides.json")
    if not os.path.isfile(p):
        sys.exit("corridor_notch_check: no %s -- run guides.sh first" % p)
    out = collections.defaultdict(list)
    for b in json.load(open(p))["bundles"]:
        for s in b["corridors"]:
            out[s["layer_name"]].append((s["x1"], s["y1"], s["x2"], s["y2"]))
    return out


def inter_area(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def gap(a, b):
    dx = max(0.0, max(a[0] - b[2], b[0] - a[2]))
    dy = max(0.0, max(a[1] - b[3], b[1] - a[3]))
    return (dx * dx + dy * dy) ** 0.5


def check(arm):
    pl = _load_placement(arm)
    dbu, insts = pl["dbu"], pl["instances"]
    pieces, found = notch_pieces(arm, insts, dbu)
    cor = corridors(arm)

    n_pieces = sum(len(v) for v in pieces.values())
    n_cor = sum(len(v) for v in cor.values())
    if n_pieces == 0:
        sys.exit("corridor_notch_check: REFUSING -- 0 notch pieces placed from %d "
                 "instance(s).\n  Nothing to intersect, so a zero here would mean "
                 "'not measured', not 'inert'.\n  Expected <cell>.notch.<layer>.json "
                 "beside each patched LEF; run notch.sh first." % len(insts))
    if n_cor == 0:
        sys.exit("corridor_notch_check: REFUSING -- 0 corridors read from the "
                 "manifest.\n  Nothing to intersect; an unplanned arm reserves "
                 "nothing (BUDA-1701).")
    shared = sorted(set(pieces) & set(cor))
    if not shared:
        sys.exit("corridor_notch_check: REFUSING -- notch metal is on {%s} and the "
                 "corridors are on {%s}.\n  No shared layer, so the two sets cannot "
                 "meet on geometry and a zero says nothing about the question."
                 % (", ".join(sorted(pieces)), ", ".join(sorted(cor))))

    rows, hits_total = [], 0
    for lay in sorted(set(cor) | set(pieces)):
        cs, ps = cor.get(lay, []), pieces.get(lay, [])
        hits = 0
        area = 0.0
        for a in cs:
            for b in ps:
                v = inter_area(a, b)
                if v > 0:
                    hits += 1
                    area += v
        hits_total += hits
        closest = min((gap(a, b) for a in cs for b in ps), default=None)
        rows.append({"layer": lay, "corridors": len(cs), "pieces": len(ps),
                     "intersections": hits, "area_um2": round(area / (dbu * dbu), 6),
                     "closest_um": None if closest is None else round(closest / dbu, 3),
                     "live": bool(cs and ps)})
    return {"arm": os.path.abspath(arm), "instances": len(insts), "dbu": dbu,
            "cells_with_notch": {c: v for c, v in sorted(found.items())},
            "pieces_placed": n_pieces, "corridors": n_cor,
            "shared_layers": shared, "layers": rows,
            "intersections": hits_total,
            "verdict": "inert" if hits_total == 0 else "meets"}


def report(res, out=sys.stdout):
    w = out.write
    w("corridor_notch_check: do BUDA's corridors meet the notch metal?\n")
    w("  arm %s\n  %d instance(s), %d notch piece(s) placed, %d corridor(s)\n\n"
      % (res["arm"], res["instances"], res["pieces_placed"], res["corridors"]))
    w("  layer  corridors   pieces   intersections     area um^2   closest um   live\n")
    for r in res["layers"]:
        w("  %-5s  %9d %8d %15d %13.4f %12s   %s\n"
          % (r["layer"], r["corridors"], r["pieces"], r["intersections"], r["area_um2"],
             "-" if r["closest_um"] is None else "%.3f" % r["closest_um"], r["live"]))
    w("\n  a layer is LIVE when it carries both a corridor and a notch piece --\n"
      "  only a live layer's zero is evidence (see the module docstring).\n")
    w("\nVERDICT: %s\n" % (
        "INERT -- no corridor meets any notch piece on %s"
        % ", ".join(res["shared_layers"]) if res["verdict"] == "inert" else
        "%d intersection(s) -- the two views diverge where it matters"
        % res["intersections"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("arm", nargs="?", default=".",
                    help="the arm directory (the one holding top/ and the cells)")
    ap.add_argument("--json", dest="out_json", help="write the findings as JSON")
    a = ap.parse_args(argv)
    res = check(a.arm)
    report(res)
    if a.out_json:
        json.dump(res, open(a.out_json, "w"), indent=2)
    return 0 if res["verdict"] == "inert" else 1


if __name__ == "__main__":
    sys.exit(main())
