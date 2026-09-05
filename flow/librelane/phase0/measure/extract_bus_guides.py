#!/usr/bin/env python3
"""Pull the bus nets' guides out of a full-route guide file.

    extract_bus_guides.py all.guide bus.guide [--prefix 'mid['] [--dy UM]

`--dy` shifts the corridors by a whole number of microns (default 0).  Use a
NON-zero shift for the sharper form of measurement A: if detailed routing then
seats the bus in the SHIFTED rectangles, it followed the guide rather than
happening to agree with it.  Keep the shift inside the channel between the
two macros or the guide asks for the impossible.
"""
import argparse
from guide_io import read_guides, write_guides

DBU = 1000

ap = argparse.ArgumentParser()
ap.add_argument("src"); ap.add_argument("dst")
ap.add_argument("--prefix", default="mid[")
ap.add_argument("--dy", type=float, default=0.0)
a = ap.parse_args()

g = read_guides(a.src)
bus = {n: r for n, r in g.items() if n.startswith(a.prefix)}
if not bus:
    raise SystemExit(f"no net starting with {a.prefix!r} in {a.src} "
                     f"({len(g)} nets; first: {list(g)[:5]})")
dy = int(round(a.dy * DBU))
out = {n: [(x1, y1 + dy, x2, y2 + dy, l) for x1, y1, x2, y2, l in r] for n, r in bus.items()}
write_guides(a.dst, out)
print(f"{len(out)} bus net(s) -> {a.dst}" + (f" (shifted {a.dy} um in y)" if dy else ""))
