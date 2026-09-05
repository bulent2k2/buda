#!/usr/bin/env python3
"""Measurement A verdict: is every bus wire point inside that net's guides?

    check_inside.py guided.def bus.guide [--prefix 'mid['] [--slack UM]

Passes when every `( x y )` of every bus net's routed wiring lies within one
of that net's guide rectangles (any layer -- a via lands where two layers'
guides meet), with `--slack` microns of tolerance for a wire's half-width
and end extensions (default 0.5).  Prints the worst offender otherwise.
"""
import argparse
from def_wires import net_entries, points
from guide_io import read_guides

DBU = 1000
ap = argparse.ArgumentParser()
ap.add_argument("def_file"); ap.add_argument("guide")
ap.add_argument("--prefix", default="mid[")
ap.add_argument("--slack", type=float, default=0.5)
a = ap.parse_args()
slack = int(a.slack * DBU)

entries = net_entries(open(a.def_file).read(), a.prefix)
guides = read_guides(a.guide)
if not entries:
    raise SystemExit(f"no {a.prefix!r} nets with wiring in {a.def_file}: NOT ROUTED")
bad, total = [], 0
for net, text in entries.items():
    rects = guides.get(net, [])
    for x, y, layer in points(text):
        total += 1
        if not any(x1 - slack <= x <= x2 + slack and y1 - slack <= y <= y2 + slack
                   for x1, y1, x2, y2, _ in rects):
            bad.append((net, x / DBU, y / DBU, layer))
print(f"{len(entries)} bus net(s), {total} wire point(s), {len(bad)} outside their guides")
for net, x, y, layer in bad[:10]:
    print(f"  OUTSIDE: {net} at ({x:.3f}, {y:.3f}) um on {layer}")
raise SystemExit(1 if bad else 0)
