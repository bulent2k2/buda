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
uncovered stretch of it -- and says WHICH KIND of miss each is, because the
two mean different things for the handoff: a segment outside its own
layer's boxes but inside the guide's xy footprint on SOME layer is the
router changing LAYER within the corridor (measured 2026-09-05: a 23 um
vertical run on met2 where the guide box was met4), while one outside the
footprint on every layer is the router leaving the corridor.  The strict
count is the verdict; the split is what it means.
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


def uncovered_length(lo, hi, spans):
    """Total length of [lo, hi] not covered by the union of `spans`."""
    cur, total = lo, 0
    for a, b in sorted(spans):
        if b <= cur:
            continue
        if a > cur:
            total += min(a, hi) - cur
        cur = max(cur, b)
        if cur >= hi:
            return total
    return total + max(0, hi - cur)


def segment_spans(p, q, layer, rects, slack):
    """(lo, hi, spans): the segment's own extent and the same-layer guide
    spans that cover it -- `*` as the layer means any layer."""
    (x1, y1), (x2, y2) = p, q
    same = [r for r in rects if layer == "*" or r[4] == layer]
    if x1 == x2:
        return (min(y1, y2), max(y1, y2),
                [(ry1 - slack, ry2 + slack) for rx1, ry1, rx2, ry2, _ in same
                 if rx1 - slack <= x1 <= rx2 + slack])
    return (min(x1, x2), max(x1, x2),
            [(rx1 - slack, rx2 + slack) for rx1, ry1, rx2, ry2, _ in same
             if ry1 - slack <= y1 <= ry2 + slack])


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


def footprint_gap(p, q, rects, slack):
    """segment_gap against the guide's xy footprint on ANY layer."""
    anylayer = [(x1, y1, x2, y2, "*") for x1, y1, x2, y2, _ in rects]
    return segment_gap(p, q, "*", anylayer, slack)


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
    n_layer = n_corridor = 0          # the two kinds of miss (docstring)
    wl = wl_layer = wl_corridor = 0   # the same, weighted by wire LENGTH
    for net, text in entries.items():
        rects = guides.get(net, [])
        net_paths = paths(text)
        if sum(len(pts) for _, pts in net_paths) < 2:
            unrouted.append(net)
            continue
        for layer, pts in net_paths:
            for x, y in pts:
                if not point_inside(x, y, rects, slack):
                    n_corridor += 1
                    bad.append((net, f"point ({x / DBU:.3f}, {y / DBU:.3f}) on {layer} [corridor]"))
            for p, q in zip(pts, pts[1:]):
                n_seg += 1
                lo, hi, own = segment_spans(p, q, layer, rects, slack)
                _, _, anyl = segment_spans(p, q, "*", rects, slack)
                wl += hi - lo
                out_own = uncovered_length(lo, hi, own)
                out_any = uncovered_length(lo, hi, anyl)
                wl_corridor += out_any
                wl_layer += out_own - out_any
                gap = segment_gap(p, q, layer, rects, slack)
                if gap:
                    lo, hi = gap
                    kind = "corridor" if footprint_gap(p, q, rects, slack) else "layer"
                    if kind == "layer":
                        n_layer += 1
                    else:
                        n_corridor += 1
                    bad.append((net, f"segment ({p[0] / DBU:.3f}, {p[1] / DBU:.3f})-"
                                     f"({q[0] / DBU:.3f}, {q[1] / DBU:.3f}) on {layer}, "
                                     f"uncovered {lo / DBU:.3f}..{hi / DBU:.3f} [{kind}]"))
    print(f"{len(entries)} bus net(s), {n_seg} segment(s), "
          f"{len(unrouted)} unrouted, {len(bad)} outside their guides"
          f" ({n_layer} on another layer inside the corridor, {n_corridor} outside the corridor)")
    print(f"  by wire length: {wl / DBU:.1f} um of bus wire, "
          f"{wl_layer / DBU:.1f} um ({100 * wl_layer / max(wl, 1):.1f}%) on another layer inside the corridor, "
          f"{wl_corridor / DBU:.1f} um ({100 * wl_corridor / max(wl, 1):.1f}%) outside the corridor")
    for net in unrouted[:10]:
        print(f"  UNROUTED: {net} -- an entry with no wiring is not a pass")
    for net, what in bad[:10]:
        print(f"  OUTSIDE: {net} {what}")
    raise SystemExit(1 if (bad or unrouted) else 0)


if __name__ == "__main__":
    main()
