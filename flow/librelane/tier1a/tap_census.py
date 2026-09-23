#!/usr/bin/env python3
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

"""Does every standard-cell row fragment of a placed top hold a well tap?

    tap_census.py <placed.def> [--json]

Exit 0 when every ROW fragment that holds any cell also holds a
`tapvpwrvgnd` cell, 1 otherwise, naming the fragments (x-start, width, how
many rows, what sits in them).  A fragment with cells and no tap has a
FLOATING N-WELL once fill insertion pads it out: the fillers' and decaps'
VPB pin lands on its own net, netgen counts every such group as an LVS net
difference, and any real cell placed there (a clock-delay buffer is the
common one) sits on an unbiased well.  Read it on the DETAILED-PLACEMENT
DEF, before the router is paid for -- the compact H+B arm at N = 8 lost a
978-error LVS verdict to 764 such fragments (docs/internal/librelane_hier_flow.md
§7g): the 96.6 um edge cells on the 121.44 um PE pitch left 26.7 um gaps,
inside the 10 um macro halos a 6.7 um fragment, too short for the 13 um tap
pitch.  What shapes the fragments is the macro HALO (harm.sh --halo) and
what taps them is FP_TAPCELL_DIST; this says whether a given pair left any
fragment out.

What it MEASURES against the recorded arms: H (48 um channels) has 0
untapped fragments and LVS 0; the recorded H+B has 253, all 27 um wide and
holding decaps only, and ALSO LVS 0 -- so a decap-only island is not by
itself what netgen counts (why is not established; the paired row's well or
netgen's treatment of a decap's VPB are the candidates, neither measured).
The compact arm's 764 held real cells in 162 of them and failed.  So the
exit code is strict -- any untapped fragment with cells -- and the report
marks the fragments with REAL cells, which is the part known to fail.  It
counts ROW fragments (what `cut_rows` left), not abutting cell groups, so an
empty fragment is fine and one the fill pass will pad out is counted.
"""
import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_arms as ra  # noqa: E402


def census(def_path):
    die, comps, _core = ra.parse_def(def_path)
    rows = []
    for line in open(def_path):
        m = re.match(r"ROW \S+ \S+ (-?\d+) (-?\d+) \S+ DO (\d+) BY (\d+) STEP (\d+) (\d+)", line)
        if m:
            x, y, nx, ny, sx, sy = map(int, m.groups())
            rows.append((x / 1000, y / 1000, (x + nx * sx) / 1000))
    by_y = collections.defaultdict(list)
    for x0, y, x1 in rows:
        by_y[round(y, 3)].append((x0, x1))
    cells = collections.defaultdict(collections.Counter)      # fragment -> kinds
    for n, c, x, y, o in comps:
        if not c.startswith("sky130"):
            continue
        for x0, x1 in by_y.get(round(y, 3), []):
            if x0 - 1e-6 <= x < x1:
                cells[(x0, round(y, 3), x1)][c.split("__")[1].rsplit("_", 1)[0]] += 1
                break
    bad = [(f, k) for f, k in cells.items() if k.get("tapvpwrvgnd", 0) == 0]
    return rows, cells, bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("def_path")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows, cells, bad = census(a.def_path)
    groups = collections.defaultdict(lambda: {"rows": 0, "cells": collections.Counter()})
    for (x0, y, x1), k in bad:
        g = groups[(round(x0, 2), round(x1 - x0, 2))]
        g["rows"] += 1
        g["cells"].update(k)
    out = {"row_fragments": len(rows), "fragments_with_cells": len(cells), "untapped": len(bad),
           "untapped_groups": [{"x": x, "width_um": w, "rows": g["rows"], "cells": dict(g["cells"])}
                               for (x, w), g in sorted(groups.items())]}
    if a.json:
        print(json.dumps(out, indent=1))
    else:
        print(f"tap_census: {len(rows)} row fragments, {len(cells)} hold cells, {len(bad)} of those have NO tap")
        for g in out["untapped_groups"]:
            real = {k: v for k, v in g["cells"].items() if k not in ("fill", "decap")}
            print(f"  x={g['x']:.2f} width {g['width_um']:.2f} um x {g['rows']} rows: {dict(g['cells'])}"
                  + (f"  <- REAL cells on a floating well: {real}" if real else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
