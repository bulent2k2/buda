# Copyright 2026 Ben Bulent Basaran
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The 8-orientation rect transform, in ONE place.

A cell-local rectangle (a `cell_rect` row, schema v30) has to be read in the
frame of each INSTANCE of that cell, which means transforming it by the
instance's orientation token over the cell's `w x h` box.  Three callers need
that and none of them can share a home: `buda_session.hier` (the
BDB->Floorplan projections and the busterm stamp) imports the compiled `buda`
module, `tools/bdb2buda.py` deliberately imports only `buda_db`, and the C++
twin (`orient_map` in `topology.cpp`) lives in a DIFFERENT shared library from
the BDB layer.

So this is standalone and dependency-free, for the same reason
`src/slot_groups.py` and `src/bus_names.py` are: a converter must be able to
read the geometry without dragging in the routing engine.  The table mirrors
`orient_map` in `topology.cpp` — (swap axes, reflect x, reflect y).
"""

# (swap, reflect_x, reflect_y) per DEF/BDB orientation token.
ORIENT_MAPS = {"N":  (0, 0, 0), "S":  (0, 1, 1),
               "FN": (0, 0, 1), "FS": (0, 1, 0),
               "W":  (1, 1, 0), "E":  (1, 0, 1),
               "FW": (1, 0, 0), "FE": (1, 1, 1)}

# Direction-preserving orientations: H stays H, V stays V.
DIR_PRESERVING = ("N", "S", "FN", "FS")


def oxf_rect(o, x1, y1, x2, y2, w, h):
    """Transform a rect through orientation `o` over a w x h box
    (normalized: the transformed box's lower-left stays at the origin)."""
    s, rx, ry = ORIENT_MAPS[o]

    def pt(x, y):
        if s:
            x, y, bw, bh = y, x, h, w
        else:
            bw, bh = w, h
        if rx:
            x = bw - x
        if ry:
            y = bh - y
        return x, y
    ax, ay = pt(x1, y1)
    bx, by = pt(x2, y2)
    return (round(min(ax, bx), 3), round(min(ay, by), 3),
            round(max(ax, bx), 3), round(max(ay, by), 3))


def comp_rects_abs(comp_x1, comp_y1, orient, cell_w, cell_h, rects,
                   ox=0.0, oy=0.0, to_int=True):
    """A component's multi-rect footprint in the target frame.

    `rects` are CELL-LOCAL (v30 `cell_rect`); the result is each one
    transformed by `orient` over the cell's `cell_w x cell_h` box and
    translated to the instance's placed lower-left, minus the frame origin
    (`ox`,`oy`).  `to_int` rounds to the Floorplan's integer grid the way
    every other BDB->Floorplan projection does; a converter writing script
    text passes False to keep the stored micron values.
    """
    x0 = comp_x1 - ox
    y0 = comp_y1 - oy
    out = []
    for r in rects:
        a, b, c, d = oxf_rect(orient or "N", r[0], r[1], r[2], r[3],
                              cell_w, cell_h)
        q = (x0 + a, y0 + b, x0 + c, y0 + d)
        out.append(tuple(int(round(v)) for v in q) if to_int else q)
    return out
