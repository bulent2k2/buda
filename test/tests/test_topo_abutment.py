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

"""
Regression: a DIRECT (2-pin) bus between two blocks that share a full edge must
produce a valid, covered, and ROUTABLE candidate.

When two blocks abut (share a full face), their facing faces coincide, so:
  * the direct I-shape collapses to zero length (src face == dst face) and is
    skipped by the non-degenerate guard,
  * the direct I in the perpendicular axis needs an interval a shared edge does
    not provide, and
  * every L / Z / U stub is sub-min-stub-length,
leaving NO candidate at all — a silently unrouted bus (common for adjacent
macros).  When nothing else survives (incl. after the keepout cull + pinch
filter), generate_2pin realizes the shared edge with `shared_edge_segment`: a
wire CROSSING the edge at the overlap centre — the same routable form the MST
edge realizers use.  Its NUTS slide window spans the full face overlap, so the
bus actually places.

Note the shape: a shared VERTICAL edge is crossed by a HORIZONTAL wire (`ABUT_H`,
track axis = y); a shared HORIZONTAL edge by a VERTICAL wire (`ABUT_V`, track axis
= x).  An along-edge wire (an earlier attempt) is clamped to ZERO slide by the
pass-through tighten once connected_block_names is set and strands every bit in
DetailedNUTS — the whole point of the crossing realization is a non-zero window.

Point-touch corners and fully-coincident blocks are DEGENERATE placements (no
shared edge, no routable channel) and deliberately stay candidate-free.

Sibling of test_topo_mst_abutted.py, which covers the N>=4 MST-edge abutment.
"""
import contextlib
import io

import buda
import pytest

import buda_cli


def _fp(coords):
    fp = buda.Floorplan()
    for name, (x1, y1, x2, y2) in coords.items():
        fp.add_block(name, x1, y1, x2, y2)
    return fp


def _gen(fp):
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)  # h=M4, v=M5
    return g


def _violations(topo, fp):
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return [str(v.kind).split(".")[-1] for v in buda.check_topo(ct, topo, fp, 0).violations]


def test_vertical_edge_abutment_crossed_by_horizontal_wire():
    """A and B share the vertical edge x=100 → a HORIZONTAL wire crossing it, both
    covered, with a real (non-zero) perpendicular slide window."""
    fp = _fp({"A": (0, 0, 100, 100), "B": (100, 0, 200, 100)})
    cands = _gen(fp).generate_candidates("A", ["B"])
    assert cands, "abutting 2-pin blocks produced NO candidate (silently unrouted bus)"
    c = cands[0]
    assert c.type.startswith("ABUT_H"), f"expected a horizontal crossing wire, got {c.type}"
    assert len(c.segments) == 1
    seg = c.segments[0]
    assert seg.start.y == seg.end.y, "crossing wire must be horizontal (constant y)"
    assert (seg.start.x, seg.end.x) == (0, 200), "wire must span both blocks (tap both far faces)"
    assert "BUSTERM_OPEN" not in _violations(c, fp), _violations(c, fp)
    # The crossing wire has a real perpendicular slide window (not zero-slide).
    ct = buda.ConnTopology()
    ct.build(c, fp)
    cs = ct.segs()[0]
    assert cs.perp_hi > cs.perp_lo, f"crossing wire is zero-slide [{cs.perp_lo},{cs.perp_hi}]"


def test_horizontal_edge_abutment_crossed_by_vertical_wire():
    """A and B share the horizontal edge y=100 → a VERTICAL wire crossing it."""
    fp = _fp({"A": (0, 0, 100, 100), "B": (0, 100, 100, 200)})
    cands = _gen(fp).generate_candidates("A", ["B"])
    assert cands, "abutting 2-pin blocks produced NO candidate (silently unrouted bus)"
    c = cands[0]
    assert c.type.startswith("ABUT_V"), f"expected a vertical crossing wire, got {c.type}"
    assert len(c.segments) == 1
    seg = c.segments[0]
    assert seg.start.x == seg.end.x, "crossing wire must be vertical (constant x)"
    assert (seg.start.y, seg.end.y) == (0, 200), "wire must span both blocks (tap both far faces)"
    assert "BUSTERM_OPEN" not in _violations(c, fp), _violations(c, fp)
    ct = buda.ConnTopology()
    ct.build(c, fp)
    cs = ct.segs()[0]
    assert cs.perp_hi > cs.perp_lo, f"crossing wire is zero-slide [{cs.perp_lo},{cs.perp_hi}]"


def test_abutting_bus_routes_to_completion():
    """End-to-end: an 8-bit bus across a full-edge abutment must place ALL bits in
    DetailedNUTS (the along-edge form placed 0/8 — a zero-slide window)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ("def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
                  "def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
                  "add_block A 0 0 100 100", "add_block B 100 0 200 100",
                  "add_bus n[8] A.p B.p", "run_bundler", "generate_topologies",
                  "def_track_pattern 4 0 SIGNAL 2 2", "def_track_pattern 5 0 SIGNAL 2 2",
                  "def_track_pattern 6 0 SIGNAL 2 2", "def_track_pattern 7 0 SIGNAL 2 2",
                  "run_planner 3", "run_nuts", "run_detailed_nuts"):
            s.do_command(c)
    assert s.nuts_result.num_violations == 0, "abutment bus left a NUTS interval violation"
    assert s.detailed_result.num_unplaced == 0, \
        f"{s.detailed_result.num_unplaced}/8 bits unplaced — abutment bus did not route"


def test_abutment_fallback_fires_after_keepout_cull():
    """The fallback is evaluated AFTER the keepout cull, so it also rescues a
    partial-edge abutment whose only other candidates (U-shapes) keepouts removed.

    A(0,0,100,100) shares the vertical edge x=100 with B(100,40,200,140) (overlap
    y∈[40,100]); the two U_HVH detours are culled by side keepouts, and pre-rework
    the empty-check ran before the cull so the bus was silently lost."""
    fp = _fp({"A": (0, 0, 100, 100), "B": (100, 40, 200, 140)})
    fp.add_keepout_zone(-40, -200, -10, 300, [4, 5, 6, 7])
    fp.add_keepout_zone(210, -200, 240, 300, [4, 5, 6, 7])
    cands = _gen(fp).generate_candidates("A", ["B"])
    assert cands, "keepout-culled abutment produced NO candidate (silently unrouted)"
    assert cands[0].type.startswith("ABUT_H"), cands[0].type
    assert "BUSTERM_OPEN" not in _violations(cands[0], fp), _violations(cands[0], fp)


@pytest.mark.parametrize("coords, why", [
    ({"A": (0, 0, 100, 100), "B": (100, 100, 200, 200)}, "corner point-touch"),
    ({"A": (0, 0, 100, 100), "B": (0, 0, 100, 100)},     "fully coincident"),
])
def test_degenerate_placements_produce_no_candidate(coords, why):
    """Point-touch and fully-coincident blocks share no edge: no routable channel,
    so the fallback deliberately does NOT invent a candidate (the zero-candidate
    warning then fires at generate_topologies)."""
    fp = _fp(coords)
    cands = _gen(fp).generate_candidates("A", ["B"])
    assert len(cands) == 0, f"{why}: expected no candidate, got {[c.type for c in cands]}"
