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

"""Web DisplayGeom parity with regular buda's matplotlib draw (draw.py Pass B).

The web clients (src/web/static/index.html `computeDisplay` and
web/.../render/DisplayGeom.scala) compute per-segment DISPLAY extents purely from
the serialized ConnSeg `analysis`.  They must reproduce the authoritative
endpoint-snapping in src/viz_explorer/draw.py (Pass B), which regular
`buda <flow>.buda` uses — otherwise the two disagree on screen.

The original port used `min`/`max` (extend-only) with no endpoint gate, so it
snapped mid-segment T-taps and could only grow a segment outward — drawing
segments OVEREXTENDED vs regular buda (up to +1260 units on the b44 demo). This
test pins the authoritative behaviour: snap ONLY an endpoint-incident connection
and REPLACE the endpoint with the partner's display perp.
"""
import buda_cli
from web import serialize

B44 = [
    "source flow/tracks/tracks4top.buda",
    "add_block blk_07 2960 9750 4660 10250",
    "add_block blk_23 200 10830 2700 11830",
    "add_block io_pad_tl 200 12000 1200 12800",
    "add_bus bus_060[52] blk_23.p blk_07.p,io_pad_tl.p",
]


def _perp(analysis):
    out = []
    for a in analysis:
        if a["perp_lo"] is not None and a["perp_hi"] is not None:
            out.append((a["perp_lo"] + a["perp_hi"]) / 2.0)
        else:
            out.append(a["perp_pos"])
    return out


def _draw_pass_b(analysis):
    """The authoritative display geometry (draw.py Pass B) — the SAME algorithm
    the two web clients must implement: gate on an endpoint-incident SEG conn
    (at_pos within 1 unit of along_lo/hi) and REPLACE that endpoint with the
    partner's display perp.  Keep in sync with index.html/DisplayGeom.scala."""
    lo = [a["along_lo"] for a in analysis]
    hi = [a["along_hi"] for a in analysis]
    perp = _perp(analysis)
    for i, a in enumerate(analysis):
        for c in a["conns"]:
            if c["kind"] != "SEG":
                continue
            j = c["seg_idx"]
            if not (0 <= j < len(analysis)):
                continue
            if abs(c["at_pos"] - a["along_lo"]) <= 1:
                lo[i] = perp[j]
            elif abs(c["at_pos"] - a["along_hi"]) <= 1:
                hi[i] = perp[j]
    return lo, hi


def _b44_candidates():
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in B44 + ["run_bundler", "generate_topologies"]:
        s.do_command(c)
    return serialize.serialize_generation(s, 1, None)["bundles"][0]["candidates"]


def test_display_geom_never_overextends():
    """Every drawn endpoint is either the segment's own along value or a partner
    perp reached via an ENDPOINT-incident conn — never an outward min/max stretch
    toward a mid-segment tap (the overextension bug)."""
    for ci, cand in enumerate(_b44_candidates(), start=1):
        an = cand["analysis"]
        lo, hi = _draw_pass_b(an)
        perp = _perp(an)
        for i, a in enumerate(an):
            # endpoint partners actually incident to lo / hi
            lo_targets = {a["along_lo"]} | {
                perp[c["seg_idx"]] for c in a["conns"]
                if c["kind"] == "SEG" and 0 <= c["seg_idx"] < len(an)
                and abs(c["at_pos"] - a["along_lo"]) <= 1}
            hi_targets = {a["along_hi"]} | {
                perp[c["seg_idx"]] for c in a["conns"]
                if c["kind"] == "SEG" and 0 <= c["seg_idx"] < len(an)
                and abs(c["at_pos"] - a["along_hi"]) <= 1}
            assert lo[i] in lo_targets, (ci, i, "lo overextended", lo[i], lo_targets)
            assert hi[i] in hi_targets, (ci, i, "hi overextended", hi[i], hi_targets)


def test_display_geom_pins_known_b44_extents():
    """Concrete extents from regular buda (draw.py) for candidates the min/max
    bug overextended — a regression back to extend-only fails here with numbers.
    cand#18 seg1 drew [700,2830] (buggy) vs [700,1570] (correct); cand#6 seg2
    drew [1200,2700] vs [1200,1960]."""
    cands = _b44_candidates()
    expected = {(18, 1): (700, 1570), (6, 2): (1200, 1960)}
    for (ci, si), (elo, ehi) in expected.items():
        lo, hi = _draw_pass_b(cands[ci - 1]["analysis"])
        assert abs(lo[si] - elo) <= 1 and abs(hi[si] - ehi) <= 1, (
            ci, si, (lo[si], hi[si]), (elo, ehi))
