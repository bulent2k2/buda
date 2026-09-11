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

# Slack when comparing a notch JSON against its LEF -- see notch_pieces().
STALE_S = 2.0


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


def notch_lefs(arm):
    """{cell: the patched LEF the TOP actually names}, from its own config.

    Resolved rather than searched.  Globbing `runs/*/final/lef` picks the
    lexicographically first tag when a cell hardened under several (`h` and
    `hb` both exist in this tree), which is a guess: nothing ties that run to
    the abstract the top consumed, and two hardenings can differ in geometry,
    so the check could compare corridors against stale pieces and report
    INERT (Codex, PR #925).  `MACROS.<cell>.lef` is the authority.
    """
    p = os.path.join(arm, "top", "config.json")
    if not os.path.isfile(p):
        sys.exit("corridor_notch_check: no %s -- the top's config names the "
                 "patched LEFs this check has to read" % p)
    macros = json.load(open(p)).get("MACROS") or {}
    out = {}
    for cell, m in macros.items():
        # `lef` is a LIST and other readers here consume all of it
        # (`pdn_phase.py` builds `lef_paths` from every element), so a cell may
        # legitimately carry a supplemental view beside the patched abstract.
        # Taking the last would then derive the JSON path from the wrong file.
        # Select by NAME and refuse ambiguity (Codex, PR #925).
        cands = [os.path.normpath(os.path.join(
                     arm, "top", l[len("dir::"):] if l.startswith("dir::") else l))
                 for l in (m.get("lef") or [])]
        patched = [c for c in cands if c.endswith(".notch.lef")]
        if len(patched) > 1:
            sys.exit("corridor_notch_check: REFUSING -- %s names %d patched LEFs in "
                     "MACROS: %s.\n  Which one the top used is not decidable here."
                     % (cell, len(patched), ", ".join(patched)))
        if patched:
            out[cell] = patched[0]
    return out


def notch_pieces(arm, insts, dbu):
    """Every cell's `uncovered` rects, placed in each instance's frame (DBU).

    REFUSES on partial coverage.  A cell with no notch JSON used to be skipped
    while another cell's pieces satisfied the global count, so `INERT` could be
    printed with a whole cell unmeasured -- and the unmeasured one could be
    exactly where a corridor crosses (Codex, PR #925).  Coverage is all-or-
    nothing, and the layer SET must agree across cells for the same reason: a
    cell patched on fewer layers is a cell partly unmeasured.
    """
    lefs = notch_lefs(arm)
    placed_cells = sorted({i["cell"] for i in insts})
    missing = [c for c in placed_cells if c not in lefs]
    if missing:
        sys.exit("corridor_notch_check: REFUSING -- %d placed cell(s) are not in "
                 "the top's MACROS: %s.\n  Their instances would be silently "
                 "unmeasured." % (len(missing), ", ".join(missing)))

    per_cell, by_cell_layers = {}, {}
    for cell in placed_cells:
        lef = lefs[cell]
        base = lef[:-len(".notch.lef")] if lef.endswith(".notch.lef") else os.path.splitext(lef)[0]
        found = {}
        lef_mtime = os.path.getmtime(lef) if os.path.isfile(lef) else None
        for j in sorted(glob.glob(base + ".notch.*.json")):
            lay = os.path.basename(j)[len(os.path.basename(base)) + len(".notch."):-len(".json")]
            # `notch_obs.py` writes the JSON and the per-layer LEF in one
            # invocation and `mv` preserves the mtime, so a CURRENT pair shares
            # a timestamp (measured: equal to the second on every cell of the
            # N=4 arm).  A JSON meaningfully older than the LEF beside it is
            # therefore from an earlier run -- which `notch.sh` left standing
            # until PR #925 -- and pairing the two would read metal the top's
            # abstract does not contain.  STALE_S is slack for the two writes
            # straddling a second boundary, not a tolerance for old files.
            if lef_mtime is not None and os.path.getmtime(j) < lef_mtime - STALE_S:
                sys.exit("corridor_notch_check: REFUSING -- %s predates the patched "
                         "LEF beside it by %.0fs.\n  It is from an earlier notch.sh "
                         "run and describes metal that LEF may not carry; re-run "
                         "notch.sh." % (j, lef_mtime - os.path.getmtime(j)))
            found[lay] = json.load(open(j)).get("uncovered", [])
        per_cell[cell] = found
        by_cell_layers[cell] = frozenset(found)

    bare = sorted(c for c, l in by_cell_layers.items() if not l)
    if bare:
        sys.exit("corridor_notch_check: REFUSING -- %d placed cell(s) have no notch "
                 "JSON beside the LEF the top names: %s.\n  A verdict over the rest "
                 "would leave those instances unmeasured; run notch.sh."
                 % (len(bare), ", ".join(bare)))
    sets = set(by_cell_layers.values())
    if len(sets) > 1:
        sys.exit("corridor_notch_check: REFUSING -- cells disagree on which layers "
                 "were patched: %s.\n  A cell patched on fewer layers is a cell "
                 "partly unmeasured."
                 % "; ".join("%s={%s}" % (c, ",".join(sorted(l)))
                             for c, l in sorted(by_cell_layers.items())))

    pieces = collections.defaultdict(list)
    for i in insts:
        ox, oy = i["x"], i["y"]
        for lay, rects in per_cell.get(i["cell"], {}).items():
            for (x1, y1, x2, y2) in rects:
                pieces[lay].append(((ox + x1) * dbu, (oy + y1) * dbu,
                                    (ox + x2) * dbu, (oy + y2) * dbu))
    return pieces, {c: lefs[c] for c in placed_cells}


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
