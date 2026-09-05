#!/usr/bin/env python3
"""Write `pins.def`: a DEF template placing every reg32 pin at an exact spot.

This is the shape BUDA will emit per block in phase 1 (the "block pins at
exact positions" row of docs/internal/librelane_hier_flow.md §4), written by
hand here so phase 0 can test the LibreLane side of that handoff alone:
`Odb.ApplyDEFTemplate` copies die area and non-power pin locations from a
template DEF (`FP_DEF_TEMPLATE`), matching pins BY NAME, and requires the same
pin set in strict mode (`scripts/odbpy/defutil.py:relocate_pins`).

Layout: d[31:0] on the WEST edge, q[31:0] on the EAST edge -- the bus enters
one face and leaves the opposite one, so a top level chaining two instances
gets a straight bus -- clk/rst on the SOUTH edge.  Pins on a vertical edge are
horizontal wires and sit on the H layer; on a horizontal edge, the V layer.

Coordinates snap to the layer's TRACKS (offset + k*pitch) so the hardened
block's router can reach each pin without a jog, and to the manufacturing
grid.  The defaults are sky130A's met2/met3 (from its tech LEF PITCH/OFFSET);
confirm against `$PDK_ROOT/sky130A/libs.ref/sky130_fd_sc_hd/techlef/*.tlef`
if the run reports off-track pins, and pass --h-layer/--v-layer if the PDK's
`IO_PIN_H_LAYER`/`IO_PIN_V_LAYER` (see the run's resolved.json) differ.

Usage:  python3 gen_pins_def.py > pins.def
"""
import argparse

DBU = 1000            # sky130A: UNITS DISTANCE MICRONS 1000
GRID = 5              # manufacturing grid 0.005 um


def snap(v):
    return int(round(v / GRID)) * GRID


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", default="reg32")
    ap.add_argument("--die", type=float, default=80.0, help="die W=H in um")
    ap.add_argument("--h-layer", default="met3")
    ap.add_argument("--h-pitch", type=float, default=0.68)
    ap.add_argument("--h-offset", type=float, default=0.34)
    ap.add_argument("--h-width", type=float, default=0.30)
    ap.add_argument("--v-layer", default="met2")
    ap.add_argument("--v-pitch", type=float, default=0.46)
    ap.add_argument("--v-offset", type=float, default=0.23)
    ap.add_argument("--v-width", type=float, default=0.14)
    ap.add_argument("--length", type=float, default=2.0, help="pin length um")
    ap.add_argument("--every", type=int, default=2, help="tracks per pin")
    ap.add_argument("--first-track", type=int, default=12)
    a = ap.parse_args()

    die = snap(a.die * DBU)
    L = snap(a.length * DBU)
    hw = snap(a.h_width * DBU / 2)
    vw = snap(a.v_width * DBU / 2)

    def h_track(k):
        return snap((a.h_offset + k * a.h_pitch) * DBU)

    def v_track(k):
        return snap((a.v_offset + k * a.v_pitch) * DBU)

    pins = []   # (name, direction, layer, (x1,y1,x2,y2) rel, (px,py))
    for i in range(32):
        y = h_track(a.first_track + i * a.every)
        pins.append((f"d[{i}]", "INPUT", a.h_layer, (0, -hw, L, hw), (0, y)))
        pins.append((f"q[{i}]", "OUTPUT", a.h_layer, (-L, -hw, 0, hw), (die, y)))
    for j, (name, d) in enumerate((("clk", "INPUT"), ("rst", "INPUT"))):
        x = v_track(a.first_track + j * 4)
        pins.append((name, d, a.v_layer, (-vw, 0, vw, L), (x, 0)))

    out = [
        "VERSION 5.8 ;",
        'DIVIDERCHAR "/" ;',
        'BUSBITCHARS "[]" ;',
        f"DESIGN {a.design} ;",
        f"UNITS DISTANCE MICRONS {DBU} ;",
        f"DIEAREA ( 0 0 ) ( {die} {die} ) ;",
        f"PINS {len(pins)} ;",
    ]
    for name, dirn, layer, (x1, y1, x2, y2), (px, py) in pins:
        out.append(
            f"  - {name} + NET {name} + DIRECTION {dirn} + USE SIGNAL"
            f" + LAYER {layer} ( {x1} {y1} ) ( {x2} {y2} )"
            f" + PLACED ( {px} {py} ) N ;")
    out += ["END PINS", "END DESIGN"]
    print("\n".join(out))


if __name__ == "__main__":
    main()
