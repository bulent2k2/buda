#!/usr/bin/env python3
"""What a routed cell asks of its children's EDGES, read from the BDB.

    tools/cell_face_demand.py <routed.bdb> [--top u] [--json out.json]

`flow/tcl/soc_local.tcl` routes one cell of the SoC vehicle alone (top
instance `u`, external buses ending on `pt_<k>` port blocks round it) into a
file-backed BDB.  This reads the stored tables -- `component`, `pin`, `net`,
`net_segment` -- with `sqlite3` and imports no engine, the judge's rule
(`tools/independent_audit.py`): the measurement comes from the wires the
router stored, not from what it reports about them.

Three things, per child of the top instance:

  * CONNECTIONS -- bits between each pair of children, and between each
    child and each port (i.e. the world outside the cell), from the
    netlist's leaf pins.  What a plan should put next to what.
  * EDGE DEMAND -- per face (N/S/E/W) of each child, the distinct nets whose
    stored metal crosses or lands on that face, against the face's
    capacity at the stack's bit pitch (face length / 4.0 -- the face rule's
    own measure).  A leaf counts LANDINGS only (a TOP-layer wire flying over
    a leaf is not a demand on its pins); a container counts every crossing
    of a net that has a pin inside it.
  * the local wire: total stored bit-wire length.

Nets with no stored metal (stranded bits) contribute nothing, so read
EDGE DEMAND beside the run's own unplaced count.
"""
import argparse
import collections
import json
import sqlite3
import sys

BITPITCH = 4.0
EPS = 0.51


def load(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    comps = {}
    for cid, name, cell, parent, depth, x1, y1, x2, y2, leaf in con.execute(
            "SELECT id, name, cell, parent_id, depth, x1, y1, x2, y2, is_leaf FROM component"):
        comps[cid] = dict(name=name, cell=cell, parent=parent, depth=depth,
                          box=(x1, y1, x2, y2), leaf=bool(leaf))
    nets = dict(con.execute("SELECT id, name FROM net"))
    pins = collections.defaultdict(set)
    for net_id, comp_id in con.execute("SELECT net_id, comp_id FROM pin"):
        pins[net_id].add(comp_id)
    segs = collections.defaultdict(list)
    for net_id, horiz, x1, y1, x2, y2 in con.execute(
            "SELECT net_id, is_horiz, x1, y1, x2, y2 FROM net_segment"):
        segs[net_id].append((bool(horiz), x1, y1, x2, y2))
    con.close()
    return comps, nets, pins, segs


def faces_touched(seg, box, leaf):
    """The faces of `box` this axis-aligned wire lands on (either end ON the
    face) or -- for a container -- crosses."""
    horiz, x1, y1, x2, y2 = seg
    bx1, by1, bx2, by2 = box
    lo, hi = (min(x1, x2), max(x1, x2)) if horiz else (min(y1, y2), max(y1, y2))
    out = set()
    if horiz:
        y = y1
        if not (by1 - EPS <= y <= by2 + EPS):
            return out
        for fx, f in ((bx1, "W"), (bx2, "E")):
            ends = abs(lo - fx) <= EPS or abs(hi - fx) <= EPS
            through = lo + EPS < fx < hi - EPS
            if ends or (through and not leaf):
                out.add(f)
    else:
        x = x1
        if not (bx1 - EPS <= x <= bx2 + EPS):
            return out
        for fy, f in ((by1, "S"), (by2, "N")):
            ends = abs(lo - fy) <= EPS or abs(hi - fy) <= EPS
            through = lo + EPS < fy < hi - EPS
            if ends or (through and not leaf):
                out.add(f)
    return out


def measure(db, top="u"):
    comps, nets, pins, segs = load(db)
    by_name = {c["name"]: cid for cid, c in comps.items()}
    if top not in by_name:
        sys.exit(f"cell_face_demand: no component '{top}' in {db}")
    uid = by_name[top]
    kids = [cid for cid, c in comps.items() if c["parent"] == uid]
    ports = [cid for cid, c in comps.items()
             if c["parent"] is None and c["name"].startswith("pt_")]

    def group_of(cid):
        """The child of `top` (or the port) a component sits under."""
        c = cid
        while c is not None:
            if c in kids or c in ports:
                return c
            c = comps[c]["parent"]
        return None

    def under(cid, anc):
        c = cid
        while c is not None:
            if c == anc:
                return True
            c = comps[c]["parent"]
        return False

    # leaf endpoints of each net
    leaf_pins = {n: {c for c in cs if comps[c]["leaf"]} for n, cs in pins.items()}
    conn = collections.Counter()
    for n, cs in leaf_pins.items():
        gs = sorted({group_of(c) for c in cs} - {None})
        for i, a in enumerate(gs):
            for b in gs[i + 1:]:
                conn[(comps[a]["name"], comps[b]["name"])] += 1

    wl = sum(abs(x2 - x1) + abs(y2 - y1) for ss in segs.values()
             for (_h, x1, y1, x2, y2) in ss)

    demand = {}
    for k in kids:
        c = comps[k]
        bx1, by1, bx2, by2 = c["box"]
        cap = {"N": (bx2 - bx1) / BITPITCH, "S": (bx2 - bx1) / BITPITCH,
               "E": (by2 - by1) / BITPITCH, "W": (by2 - by1) / BITPITCH}
        per = {f: set() for f in "NSEW"}
        for n, cs in leaf_pins.items():
            inside = [x for x in cs if under(x, k)]
            if not inside or len(inside) == len(cs):
                continue      # not this child's, or entirely inside it
            for seg in segs.get(n, ()):
                for f in faces_touched(seg, c["box"], c["leaf"]):
                    per[f].add(n)
        demand[c["name"]] = {
            "cell": c["cell"], "box": c["box"], "leaf": c["leaf"],
            "faces": {f: {"bits": len(per[f]), "cap": cap[f],
                          "use": round(len(per[f]) / cap[f], 3) if cap[f] else None}
                      for f in "NSEW"}}
    return dict(db=db, wl=round(wl), stored_nets=len(segs), nets=len(nets),
                connections={f"{a}|{b}": v for (a, b), v in sorted(conn.items())},
                demand=demand)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bdb")
    ap.add_argument("--top", default="u")
    ap.add_argument("--json")
    a = ap.parse_args()
    r = measure(a.bdb, a.top)
    print(f"{a.bdb}: detailed WL {r['wl']:,}  ({r['stored_nets']} of {r['nets']} nets stored)")
    print("connections (bits):")
    for k, v in r["connections"].items():
        print(f"  {k:<28} {v}")
    print("edge demand (bits / face capacity at the bit pitch):")
    for name, d in r["demand"].items():
        cells = "  ".join(f"{f}:{d['faces'][f]['bits']}/{d['faces'][f]['cap']:.0f}"
                          for f in "NSEW")
        print(f"  {name:<10} {d['cell']:<12} {cells}")
    if a.json:
        with open(a.json, "w") as f:
            json.dump(r, f, indent=1)


if __name__ == "__main__":
    main()
