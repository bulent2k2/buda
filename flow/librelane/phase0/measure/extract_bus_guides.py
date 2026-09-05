#!/usr/bin/env python3
"""Pull the bus nets' guides out of a full-route guide file.

    extract_bus_guides.py all.guide bus.guide [--prefix 'mid['] [--dy UM]
                          [--channel X1 X2] [--riser-layer met2]

With `--dy 0` (default) the bus's guides are copied as the reference route
had them.  With a NON-zero `--dy` the corridor is moved, for the sharper
form of measurement A: if detailed routing then seats the bus in boxes the
router did NOT choose, it followed the guide rather than happening to agree
with it.

What moves is only the CHANNEL -- rectangles lying wholly between the two
macros (x within `--channel`, default 100..160 um: the gap between u0 at
20..100 and u1 at 160..240 in two_reg32).  The terminal boxes over the
macros stay put, because the pins they give access to do not move (Codex
#875 P2: shifting everything left every pin outside its own guide, so the
router could not reach it and the check failed on a correct route).  Each
shifted rectangle is then re-connected to what it left behind with a RISER
at each x-end: a box on `--riser-layer` (a vertical layer; met2 by default)
spanning the original and shifted y, so the guide stays one connected set
of boxes -- the router changes layer, runs the riser, and changes back.

Prints what it did; refuses when no channel rectangle was found, since a
shift that moved nothing is not the experiment.
"""
import argparse
from guide_io import read_guides, write_guides

DBU = 1000

ap = argparse.ArgumentParser()
ap.add_argument("src"); ap.add_argument("dst")
ap.add_argument("--prefix", default="mid[")
ap.add_argument("--dy", type=float, default=0.0)
ap.add_argument("--channel", type=float, nargs=2, default=(100.0, 160.0),
                metavar=("X1", "X2"), help="x-extent of the channel, um")
ap.add_argument("--riser-layer", default="met2")
ap.add_argument("--riser-width", type=float, default=1.0, help="um")
a = ap.parse_args()

g = read_guides(a.src)
bus = {n: r for n, r in g.items() if n.startswith(a.prefix)}
if not bus:
    raise SystemExit(f"no net starting with {a.prefix!r} in {a.src} "
                     f"({len(g)} nets; first: {list(g)[:5]})")
dy = int(round(a.dy * DBU))
if not dy:
    write_guides(a.dst, bus)
    print(f"{len(bus)} bus net(s) -> {a.dst} (as routed)")
    raise SystemExit(0)

cx1, cx2 = (int(round(v * DBU)) for v in a.channel)
hw = int(round(a.riser_width * DBU / 2))
out, moved, kept = {}, 0, 0
for net, rects in bus.items():
    new = []
    for x1, y1, x2, y2, layer in rects:
        if cx1 <= x1 and x2 <= cx2:
            new.append((x1, y1 + dy, x2, y2 + dy, layer))
            lo, hi = (y1, y2 + dy) if dy > 0 else (y1 + dy, y2)
            new.append((x1 - hw, lo, x1 + hw, hi, a.riser_layer))
            new.append((x2 - hw, lo, x2 + hw, hi, a.riser_layer))
            moved += 1
        else:
            new.append((x1, y1, x2, y2, layer))
            kept += 1
    out[net] = new
if not moved:
    raise SystemExit(f"no guide rectangle lies within the channel x={a.channel[0]}..{a.channel[1]} um "
                     f"-- nothing to shift; check --channel against the macro placement")
write_guides(a.dst, out)
print(f"{len(out)} bus net(s) -> {a.dst}: {moved} channel rect(s) shifted {a.dy} um in y "
      f"with {2 * moved} {a.riser_layer} riser(s); {kept} terminal rect(s) kept in place")
