#!/usr/bin/env python3
"""Pull the bus nets' guides out of a full-route guide file.

    extract_bus_guides.py all.guide bus.guide [--prefix 'mid['] [--dy UM]
                          [--channel X1 X2] [--gcell UM]

With `--dy 0` (default) the bus's guides are copied as the reference route
had them.  With a NON-zero `--dy` the corridor is moved, for the sharper
form of measurement A: if detailed routing then seats the bus in boxes the
router did NOT choose, it followed the guide rather than happening to agree
with it.

What moves is only the CHANNEL -- the part of each rectangle lying between
the two macros (x within `--channel`, default 90..160 um: the gap between
u0 at 10..90 and u1 at 160..240 in two_reg32).  A rectangle is CLIPPED at
the channel bounds: the part inside shifts, the parts outside stay.  It has
to be a clip and not a whole-box test, because OpenROAD's guide boxes are
gcell-aligned (6.9 um here) and merged along a run, so a bus bit's channel
run is ONE box from 89.7 to 165.6 um that overhangs both macro edges
(measured 2026-09-05) -- "wholly within the channel" shifted nothing.  The
metal over the macros stays put, because the pins it gives access to do not
move (Codex #875 P2: shifting everything left every pin outside its own
guide, so the router could not reach it and the check failed on a correct
route).  Each CUT is then bridged with a RISER: a box on the vertical
layer next to the cut box's layer (met1/met3 -> met2/met4 above, met5 ->
met4 below), spanning the original and shifted y and the gcell column on
EACH side of the cut, so the guide stays one connected set of boxes -- the
router changes layer, runs the riser, and changes back.  Two gcells wide
because adjacent-layer guides connect only where they SHARE a gcell
(OpenROAD's own met3/met4 boxes overlap in one), and a riser that merely
abutted the pieces it was to join intersected nothing: `[ERROR DRT-0218]
Guide is not connected to design` and DRT-0229, measured 2026-09-05.  A
box wholly inside the channel moves with its neighbours and needs none.

Everything is in GCELL units (`--gcell`, 6.9 um here -- the `GCELLGRID`
detailed routing prints), because that is what a guide IS to the router: a
box that covers no whole gcell has no gcell index, and TritonRoute stops on
it (`[ERROR DRT-0229] genGuides_split split_indices is empty on met2`,
measured 2026-09-05 on 1 um-wide risers and clip edges at 90/160 um).  So
the channel bounds snap INWARD to the gcell grid, a riser is one gcell
column wide, and `--dy` must be a whole number of gcells -- which is also
the sharp form of the question: a box moved by less still overlaps the
gcell it came from, so the router may stay there and the check cannot tell
following from agreeing.  A `--dy` that is not a multiple is refused.

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
ap.add_argument("--channel", type=float, nargs=2, default=(90.0, 160.0),
                metavar=("X1", "X2"), help="x-extent of the channel, um")
ap.add_argument("--gcell", type=float, default=6.9, help="gcell size, um (DRT's GCELLGRID STEP)")
a = ap.parse_args()

g = read_guides(a.src)
bus = {n: r for n, r in g.items() if n.startswith(a.prefix)}
if not bus:
    raise SystemExit(f"no net starting with {a.prefix!r} in {a.src} "
                     f"({len(g)} nets; first: {list(g)[:5]})")
dy = int(round(a.dy * DBU))
if not dy:
    write_guides(a.dst, bus, g.spelling)
    print(f"{len(bus)} bus net(s) -> {a.dst} (as routed)")
    raise SystemExit(0)

RISER = {"met1": "met2", "met3": "met4", "met5": "met4", "M1": "M2", "M3": "M4", "M5": "M4"}
gc = int(round(a.gcell * DBU))
if dy % gc:
    raise SystemExit(f"--dy {a.dy} um is not a whole number of gcells ({a.gcell} um): a box moved "
                     f"by less still overlaps the gcell it came from, and a box off the gcell grid "
                     f"is one the router refuses (DRT-0229)")
cx1 = -(-int(round(a.channel[0] * DBU)) // gc) * gc      # snap inward: ceil
cx2 = (int(round(a.channel[1] * DBU)) // gc) * gc         # floor
if cx1 >= cx2:
    raise SystemExit(f"channel x={a.channel[0]}..{a.channel[1]} um holds no whole gcell of {a.gcell} um")
out, moved, kept, split, risers = {}, 0, 0, 0, 0
for net, rects in bus.items():
    new = []
    for x1, y1, x2, y2, layer in rects:
        ix1, ix2 = max(x1, cx1), min(x2, cx2)        # the part inside the channel
        if ix1 >= ix2:
            new.append((x1, y1, x2, y2, layer))
            kept += 1
            continue
        if x1 < ix1:
            new.append((x1, y1, ix1, y2, layer))     # the part west of the channel stays
        if ix2 < x2:
            new.append((ix2, y1, x2, y2, layer))     # the part east of it stays
        new.append((ix1, y1 + dy, ix2, y2 + dy, layer))
        lo, hi = min(y1, y1 + dy), max(y2, y2 + dy)
        if x1 < ix1 or ix2 < x2:
            split += 1
            if layer not in RISER:
                raise SystemExit(f"{net}: a box on {layer} crosses the channel edge and this "
                                 f"script knows no vertical riser layer next to it")
            for cut in [c for c, cut_here in ((ix1, x1 < ix1), (ix2, ix2 < x2)) if cut_here]:
                new.append((cut - gc, lo, cut + gc, hi, RISER[layer]))
                risers += 1
        moved += 1
    out[net] = new
if not moved:
    raise SystemExit(f"no guide rectangle reaches into the channel x={cx1 / DBU}..{cx2 / DBU} um "
                     f"(gcell-snapped from {a.channel[0]}..{a.channel[1]}) -- nothing to shift; "
                     f"check --channel against the macro placement")
write_guides(a.dst, out, g.spelling)
print(f"{len(out)} bus net(s) -> {a.dst}: {moved} channel piece(s) shifted {a.dy} um in y within "
      f"x={cx1 / DBU}..{cx2 / DBU} ({split} of them clipped out of a box that overhangs the channel, "
      f"{risers} riser(s) at the cuts); {kept} rect(s) kept in place")
