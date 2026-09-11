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
        # The configured LEF must EXIST.  `notch.sh` moves it into place only
        # on its success path, so when a later layer fails it leaves the early
        # `<cell>.notch.<layer>.json` behind and no LEF -- and an earlier cut
        # of this code skipped its freshness check when the LEF was missing,
        # which let exactly those partial JSONs reach a verdict (Codex, #925).
        if not os.path.isfile(lef):
            sys.exit("corridor_notch_check: REFUSING -- %s names %s in MACROS and "
                     "it does not exist.\n  notch.sh writes it only on success, so "
                     "the arm is mid-failure; re-run notch.sh." % (cell, lef))
        # WHICH layers this LEF was built from, from the provenance notch.sh
        # writes beside it -- not from a glob, and not from mtime.  A glob
        # cannot tell a current JSON from one a wider earlier run left, and
        # mtime cannot either: with `--layers met2,met3` the met2 JSON is
        # written a whole KLayout pass before the LEF gets the met3 pass's
        # timestamp, so a fresh JSON is legitimately minutes older.
        prov = base + ".notch.layers"
        if not os.path.isfile(prov):
            sys.exit("corridor_notch_check: REFUSING -- no %s.\n  Which layers that "
                     "LEF was patched from is unrecorded, so a JSON beside it cannot "
                     "be told from one an earlier, wider run left behind.\n  Re-run "
                     "notch.sh (it writes this file since PR #925)." % prov)
        found = {}
        for lay in open(prov).read().split():
            jp = "%s.notch.%s.json" % (base, lay)
            if not os.path.isfile(jp):
                sys.exit("corridor_notch_check: REFUSING -- %s patched %s but %s is "
                         "missing.\n  That layer would go unmeasured." % (cell, lay, jp))
            found[lay] = json.load(open(jp)).get("uncovered", [])
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


def corridors(arm, dbu, die_um):
    """What CONSTRAINS the router, read from the file it is handed.

    `guides.sh` writes two artefacts and only one of them reaches OpenROAD:
    `out/buda_bus.guide` is what `read_guides` consumes, and
    `out/buda_guides.json` is the abstract bundle-level manifest beside it.
    They are not the same geometry -- the `.guide` is gcell-expanded per bit
    and carries the `terminal met2,met3` pin-access strips -- so a notch piece
    meeting only an added strip is invisible in the manifest while being very
    much present in what the router was told (Codex, PR #925).  The manifest
    is still read, for the cross-check below.
    """
    gp = os.path.join(arm, "top", "out", "buda_bus.guide")
    if not os.path.isfile(gp):
        sys.exit("corridor_notch_check: no %s -- that is the file `read_guides` "
                 "consumes; run guides.sh first" % gp)
    out = collections.defaultdict(list)
    net, inside = None, False
    for ln, line in enumerate(open(gp), 1):
        t = line.split()
        if not t:
            continue
        if t[0] == "(":
            inside = True
            continue
        if t[0] == ")":
            inside = False
            continue
        if not inside:
            net = t[0]
            continue
        if net is not None and inside and t:
            # Inside a net block every row is geometry.  A row this cannot
            # parse is DROPPED if we `continue`, `n_cor` stays non-zero, and
            # the verdict can come back INERT with the dropped row the only
            # one that intersected -- the silent zero this tool exists to
            # prevent, in its own reader (Codex, PR #925).
            if len(t) != 5 or not t[4].startswith("met"):
                sys.exit("corridor_notch_check: REFUSING -- %s:%d is inside net %r "
                         "and is not a geometry row: %r.\n  Dropping it could hide "
                         "the only intersection." % (gp, ln, net, line.rstrip()))
            try:
                x1, y1, x2, y2 = (float(v) for v in t[:4])
            except ValueError:
                sys.exit("corridor_notch_check: REFUSING -- %s:%d has a non-numeric "
                         "coordinate: %r." % (gp, ln, line.rstrip()))
            out[t[4]].append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
    n_manifest = 0
    mp = os.path.join(arm, "top", "out", "buda_guides.json")
    if os.path.isfile(mp):
        n_manifest = sum(len(b["corridors"]) for b in json.load(open(mp))["bundles"])

    # UNITS are REPORTED, not guarded.  `guides.sh` declares
    # `set_import_scale dbu`, so the guide is in DBU beside a placement that
    # multiplies microns by `dbu` -- verified on the N=4 arm (guide extent
    # 772720 against a 944000 DBU die).  A guard was tried and removed: the
    # only signal available is the guide's extent against the die, and a
    # legitimately small corridor near the origin is indistinguishable from a
    # 1000x scale error, so it rejected valid inputs.  Defending a
    # hypothetical by refusing real designs is the wrong trade (Codex, #924).
    # The two extents are printed instead, where a mismatch is visible.
    return out, n_manifest


def union_area(boxes):
    """Area covered by a set of axis-aligned rects, counting overlap once."""
    if not boxes:
        return 0.0
    xs = sorted({v for b in boxes for v in (b[0], b[2])})
    total = 0.0
    for i in range(len(xs) - 1):
        x0, x1 = xs[i], xs[i + 1]
        if x1 <= x0:
            continue
        spans = sorted((b[1], b[3]) for b in boxes if b[0] <= x0 and b[2] >= x1)
        cy = None
        cov = 0.0
        for lo, hi in spans:
            if cy is None or lo > cy:
                cov += hi - lo
                cy = hi
            elif hi > cy:
                cov += hi - cy
                cy = hi
        total += (x1 - x0) * cov
    return total


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
    cor, n_manifest = corridors(arm, dbu, pl.get("die_um"))

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
        boxes = []
        for a in cs:
            for b in ps:
                if inter_area(a, b) > 0:
                    hits += 1
                    boxes.append((max(a[0], b[0]), max(a[1], b[1]),
                                  min(a[2], b[2]), min(a[3], b[3])))
        # UNION, not the pairwise sum.  The per-net terminal strips overlap
        # each other heavily, so summing every (box, piece) pair counts the
        # same physical metal once per guide box and inflates the figure by a
        # large factor (Codex, PR #925).  Coordinate compression over the
        # intersection rects gives the real area.
        area = union_area(boxes)
        hits_total += hits
        closest = min((gap(a, b) for a in cs for b in ps), default=None)
        rows.append({"layer": lay, "corridors": len(cs), "pieces": len(ps),
                     "intersections": hits, "area_um2": round(area / (dbu * dbu), 6),  # union, not pair-sum
                     "closest_um": None if closest is None else round(closest / dbu, 3),
                     "live": bool(cs and ps)})
    return {"arm": os.path.abspath(arm), "instances": len(insts), "dbu": dbu,
            "cells_with_notch": {c: v for c, v in sorted(found.items())},
            "pieces_placed": n_pieces, "corridors": n_cor,
            "manifest_corridors": n_manifest,
            "shared_layers": shared, "layers": rows,
            "guide_extent": round(max((b[2] for v in cor.values() for b in v), default=0.0), 3),
            "die_extent": round(max(pl.get("die_um") or [0])* dbu, 3),
            "intersections": hits_total,
            "verdict": "inert" if hits_total == 0 else "meets"}


def report(res, out=sys.stdout):
    w = out.write
    w("corridor_notch_check: do BUDA's corridors meet the notch metal?\n")
    w("  arm %s\n  %d instance(s), %d notch piece(s) placed, %d guide box(es) from\n"
      "  buda_bus.guide -- what read_guides consumes -- beside %d manifest corridor(s)\n\n"
      % (res["arm"], res["instances"], res["pieces_placed"], res["corridors"],
         res["manifest_corridors"]))
    w("  guide extent %.0f, die %.0f (same units, or nothing below means anything)\n\n"
      % (res["guide_extent"], res["die_extent"]))
    w("  layer  guideboxes   pieces   intersections     area um^2   closest um   live\n")
    for r in res["layers"]:
        w("  %-5s  %10d %8d %15d %13.4f %12s   %s\n"
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
