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

The EXIT CODE is the verdict, and `--max-outside-pct` says what a pass is
by the STRICT measure (outside the net's own-layer boxes); the corridor
measure has its own, `--max-corridor-outside-pct` (outside the guide's
footprint on every layer), for the question a single-layer corridor plan
can answer while the router still has to change layers inside it.
Without it every miss fails (the strict rule, right for a synthetic DEF).
With it the run passes when the STRICT measure -- wire length outside its
own layer's boxes, layer changes and corridor exits both, as a percentage
of the bus's wire -- is at or under the number, which is what a real
routed DEF needs: the real result is 1.9 % (as-routed corridor) and 3.7 %
(the corridor shifted a gcell), all of it gcell-edge overshoot and
pin-access legs, and a script that exits 1 on the run the recipe calls a
pass is one no harness can gate on.  Everything the strict rule sees is
in that measure (Codex #879): a RECT patch is metal and enters as the
length of its long axis, checked along it like a segment; a LONE point --
a one-point path, a via with no wire on that layer -- has no length, so
one outside the corridor's footprint on every layer fails the threshold
verdict separately rather than vanishing from it.  An unrouted bus bit
fails under either rule: no threshold makes missing wire a pass.
"""
import argparse
from def_wires import net_entries, paths, patches
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
    ap.add_argument("--max-outside-pct", type=float, default=None, metavar="PCT",
                    help="pass when at most PCT %% of the bus wire (by length) lies outside "
                         "its own layer's guide boxes; default: any miss fails")
    ap.add_argument("--max-corridor-outside-pct", type=float, default=None, metavar="PCT",
                    help="pass when at most PCT %% of the bus wire lies outside the guide's "
                         "xy footprint on EVERY layer -- the corridor measure, blind to layer "
                         "changes inside it; with --max-outside-pct too, both must pass")
    a = ap.parse_args()
    for opt, val in (("--max-outside-pct", a.max_outside_pct),
                     ("--max-corridor-outside-pct", a.max_corridor_outside_pct)):
        if val is not None and not 0 <= val <= 100:
            raise SystemExit(f"{opt} {val}: a percentage, 0..100")
    slack = int(a.slack * DBU)

    entries = net_entries(open(a.def_file).read(), a.prefix)
    guides = read_guides(a.guide)
    if not entries:
        raise SystemExit(f"no {a.prefix!r} nets in {a.def_file}")
    bad, n_seg, unrouted = [], 0, []
    n_layer = n_corridor = 0          # the two kinds of miss (docstring)
    wl = wl_layer = wl_corridor = 0   # the same, weighted by wire LENGTH
    lone_exits = 0                    # one-point paths outside the footprint: no length to weigh
    for net, text in entries.items():
        rects = guides.get(net, [])
        net_paths = paths(text)
        if sum(len(pts) for _, pts in net_paths) < 2:
            unrouted.append(net)
            continue
        for layer, x1, y1, x2, y2 in patches(text):
            # A via's patch metal counts as metal (Codex #877): each corner
            # must sit in the net's guide on that layer, like a path point --
            # and it enters the LENGTH measure as its long axis (Codex #879),
            # so threshold mode weighs it like the wire it is.
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            p, q = ((x1, cy), (x2, cy)) if x2 - x1 >= y2 - y1 else ((cx, y1), (cx, y2))
            lo, hi, own = segment_spans(p, q, layer, rects, slack)
            _, _, anyl = segment_spans(p, q, "*", rects, slack)
            wl += hi - lo
            out_any = uncovered_length(lo, hi, anyl)
            wl_corridor += out_any
            wl_layer += uncovered_length(lo, hi, own) - out_any
            if not all(point_inside(x, y, [r for r in rects if r[4] == layer], slack)
                       for x, y in ((x1, y1), (x2, y2))):
                kind = "corridor" if any(not point_inside(x, y, rects, slack)
                                         for x, y in ((x1, y1), (x2, y2))) else "layer"
                if kind == "layer":
                    n_layer += 1
                else:
                    n_corridor += 1
                bad.append((net, f"patch ({x1 / DBU:.3f}, {y1 / DBU:.3f})-({x2 / DBU:.3f}, {y2 / DBU:.3f}) "
                                 f"on {layer} [{kind}]"))
        for layer, pts in net_paths:
            for x, y in pts:
                # `rects` unfiltered: a point outside EVERY layer's box is a
                # corridor exit; a point on a segment is weighed by the
                # segment's length below, a lone one has none to weigh.
                if not point_inside(x, y, rects, slack):
                    n_corridor += 1
                    lone = len(pts) == 1
                    lone_exits += lone
                    bad.append((net, f"{'lone point' if lone else 'point'} ({x / DBU:.3f}, {y / DBU:.3f}) "
                                     f"on {layer} [corridor]"))
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
    outside_pct = 100 * (wl_layer + wl_corridor) / max(wl, 1)
    corridor_pct = 100 * wl_corridor / max(wl, 1)
    if a.max_outside_pct is None and a.max_corridor_outside_pct is None:
        ok = not (bad or unrouted)
    else:
        # Two thresholds for two questions.  The STRICT one asks whether the
        # wire is where the guide said, layer included; the CORRIDOR one
        # asks only whether it stayed inside the guide's footprint, which is
        # what a corridor handoff promises when the guide's layer is a
        # single-layer plan and the router must change layers inside it to
        # reach pins (§8 step 5b: 7.7 % outside the corridor, 22.7 % on
        # another layer inside it -- a pin-row disagreement, not a corridor
        # one).  Both, when both are given.
        ok = not unrouted and not lone_exits
        verdicts = []
        if a.max_outside_pct is not None:
            ok = ok and outside_pct <= a.max_outside_pct
            verdicts.append(f"{outside_pct:.1f}% of the bus wire outside its own layer's guides, "
                            f"threshold {a.max_outside_pct:g}%")
        if a.max_corridor_outside_pct is not None:
            ok = ok and corridor_pct <= a.max_corridor_outside_pct
            verdicts.append(f"{corridor_pct:.1f}% outside the corridor on every layer, "
                            f"threshold {a.max_corridor_outside_pct:g}%")
        print(f"  {'PASS' if ok else 'FAIL'}: " + "; ".join(verdicts)
              + (f", {len(unrouted)} unrouted" if unrouted else "")
              + (f", {lone_exits} lone point(s) outside the corridor" if lone_exits else ""))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
