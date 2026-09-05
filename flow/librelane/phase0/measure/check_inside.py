#!/usr/bin/env python3
"""Measurement A verdict: is every bus wire inside that net's guides?

    check_inside.py guided.def bus.guide [--prefix 'mid['] [--slack UM]

A net PASSES when it has routed wiring at all, and every SEGMENT of it --
the metal drawn between consecutive points of one DEF path, on that path's
layer -- lies within the union of that net's guide rectangles ON THAT LAYER,
along its whole length, with `--slack` microns of tolerance for a wire's
half-width and end extensions (default 0.5).  Every point is also required
to sit in some guide rectangle on any layer (a via lands where two layers'
guides meet).

Two things this deliberately refuses to let pass (Codex #875, both P1):

* A net with NO wiring.  Its `- mid[k] ... ;` entry still exists in the
  DEF, so "the entry is there" proves nothing; the router may simply have
  left the bit unrouted, and that must read as a failure of the corridor
  handoff, not a pass.
* A segment whose two ENDPOINTS are each inside a guide box while its
  interior crosses a gap between boxes.  Vertices are not metal; the wire
  between them is, so coverage is checked along the segment.

The verdict names the worst offender: the net, the segment, and the first
uncovered stretch of it.
"""
import argparse
from def_wires import net_entries, paths
from guide_io import read_guides

DBU = 1000


def _uncovered(lo, hi, spans):
    """The first stretch of [lo, hi] not covered by the union of `spans`."""
    cur = lo
    for a, b in sorted(spans):
        if b < cur:
            continue
        if a > cur:
            return (cur, min(a, hi))
        cur = max(cur, b)
        if cur >= hi:
            return None
    return (cur, hi) if cur < hi else None


def segment_gap(p, q, layer, rects, slack):
    """None if the axis-aligned segment p-q on `layer` is covered by the
    union of the same-layer rects (each grown by slack); else (lo, hi)."""
    (x1, y1), (x2, y2) = p, q
    same = [r for r in rects if r[4] == layer]
    if x1 == x2:                      # vertical: cover along y at x
        spans = [(ry1 - slack, ry2 + slack) for rx1, ry1, rx2, ry2, _ in same
                 if rx1 - slack <= x1 <= rx2 + slack]
        return _uncovered(min(y1, y2), max(y1, y2), spans)
    if y1 == y2:                      # horizontal: cover along x at y
        spans = [(rx1 - slack, rx2 + slack) for rx1, ry1, rx2, ry2, _ in same
                 if ry1 - slack <= y1 <= ry2 + slack]
        return _uncovered(min(x1, x2), max(x1, x2), spans)
    return (0, 0)                      # a diagonal is not axis-aligned DEF wiring


def point_inside(x, y, rects, slack):
    return any(x1 - slack <= x <= x2 + slack and y1 - slack <= y <= y2 + slack
               for x1, y1, x2, y2, _ in rects)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("def_file"); ap.add_argument("guide")
    ap.add_argument("--prefix", default="mid[")
    ap.add_argument("--slack", type=float, default=0.5)
    a = ap.parse_args()
    slack = int(a.slack * DBU)

    entries = net_entries(open(a.def_file).read(), a.prefix)
    guides = read_guides(a.guide)
    if not entries:
        raise SystemExit(f"no {a.prefix!r} nets in {a.def_file}")
    bad, n_seg, unrouted = [], 0, []
    for net, text in entries.items():
        rects = guides.get(net, [])
        net_paths = paths(text)
        if sum(len(pts) for _, pts in net_paths) < 2:
            unrouted.append(net)
            continue
        for layer, pts in net_paths:
            for x, y in pts:
                if not point_inside(x, y, rects, slack):
                    bad.append((net, f"point ({x / DBU:.3f}, {y / DBU:.3f}) on {layer}"))
            for p, q in zip(pts, pts[1:]):
                n_seg += 1
                gap = segment_gap(p, q, layer, rects, slack)
                if gap:
                    lo, hi = gap
                    bad.append((net, f"segment ({p[0] / DBU:.3f}, {p[1] / DBU:.3f})-"
                                     f"({q[0] / DBU:.3f}, {q[1] / DBU:.3f}) on {layer}, "
                                     f"uncovered {lo / DBU:.3f}..{hi / DBU:.3f}"))
    print(f"{len(entries)} bus net(s), {n_seg} segment(s), "
          f"{len(unrouted)} unrouted, {len(bad)} outside their guides")
    for net in unrouted[:10]:
        print(f"  UNROUTED: {net} -- an entry with no wiring is not a pass")
    for net, what in bad[:10]:
        print(f"  OUTSIDE: {net} {what}")
    raise SystemExit(1 if (bad or unrouted) else 0)


if __name__ == "__main__":
    main()
