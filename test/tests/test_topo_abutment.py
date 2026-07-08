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

The crossing wire is centred on the shared edge and only min-stub-length long (not
the full block width — coverage is by overlap, so a short straddling wire covers
both blocks), floored at a project-level epsilon so it is never zero-length.

Corner point-touch is the point-contact analogue of edge abutment: the two blocks
meet at exactly one corner, so every direct/L/Z/U segment is zero-slide and dropped
— leaving no candidate.  The same fallback rescues it by routing an L AROUND the
shared corner (`corner_diagonal_L`, two variants `CORNER_HV`/`CORNER_VH`), the way
the MST path already does.  Fully-coincident (and partially overlapping blocks that
end up with no survivor) stay candidate-free — a degenerate placement the
zero-candidate warning is meant to flag, not silently route.

Sibling of test_topo_mst_abutted.py, which covers the N>=4 MST-edge abutment (and
test_topo_mst_corner.py, the N>=4 corner-diagonal case).
"""
import contextlib
import io

import buda

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
    lo, hi = sorted((seg.start.x, seg.end.x))
    assert seg.start.y == seg.end.y, "crossing wire must be horizontal (constant y)"
    # Minimized: the wire straddles the shared edge x=100 but does NOT span the
    # full 200-wide block pair — it is only the min-stub length (saves channel).
    assert lo < 100 < hi, f"wire must straddle the shared edge x=100, got [{lo},{hi}]"
    assert (hi - lo) < 200, f"wire must be minimized, not full-width, got len {hi - lo}"
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
    lo, hi = sorted((seg.start.y, seg.end.y))
    assert seg.start.x == seg.end.x, "crossing wire must be vertical (constant x)"
    assert lo < 100 < hi, f"wire must straddle the shared edge y=100, got [{lo},{hi}]"
    assert (hi - lo) < 200, f"wire must be minimized, not full-width, got len {hi - lo}"
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


def _abut_candidate_with_min_stub(min_stub):
    """Generate the abutment candidate for A|B (shared vertical edge x=100) with a
    given global min-stub-length, via the CLI so set_min_stub_length is exercised."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ("def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
                  "def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
                  f"set_min_stub_length {min_stub}",
                  "add_block A 0 0 100 100", "add_block B 100 0 200 100",
                  "add_bus n[8] A.p B.p", "run_bundler", "generate_topologies"):
            s.do_command(c)
    seg = s.bundles[0].input.candidates[0].segments[0]
    return abs(seg.end.x - seg.start.x)


def test_abutment_wire_length_tracks_min_stub_setting():
    """The crossing wire's along-length is the min-stub-length setting — so the bus
    occupies the minimum channel rather than the full block width."""
    assert _abut_candidate_with_min_stub(40) == 40
    assert _abut_candidate_with_min_stub(10) == 10


def test_abutment_wire_never_zero_length_epsilon_floor():
    """min-stub 0 must NOT produce a zero-length wire (no conn-segs to pin it, and
    NUTS cannot place a point) — it is floored at the project-level epsilon."""
    length = _abut_candidate_with_min_stub(0)
    assert length >= 2, f"expected epsilon-floored length, got {length}"
    assert length < 100, "epsilon floor must still be minimal, not a block width"


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


def test_fully_coincident_blocks_produce_no_candidate():
    """Two blocks occupying the SAME rectangle share no clean edge and no routable
    channel, so the fallback deliberately does NOT invent a candidate (the
    zero-candidate warning then fires at generate_topologies).  A corner point-touch
    is NOT degenerate — it is rescued (see the corner tests below); a partial overlap
    keeps its own I/U candidates and is likewise not the fallback's concern."""
    fp = _fp({"A": (0, 0, 100, 100), "B": (0, 0, 100, 100)})
    cands = _gen(fp).generate_candidates("A", ["B"])
    assert len(cands) == 0, f"expected no candidate, got {[c.type for c in cands]}"


def test_partial_overlap_keeps_i_and_u_candidates():
    """The other end of the adjacency spectrum: two blocks that partially OVERLAP
    still leave a routable channel, so the ordinary generator handles them — the
    abutment/corner fallback never fires.  A(0,0,100,100) and B(50,50,150,150) share
    the band 50<=x<=100, 50<=y<=100, which gives a straight I-shape straight through
    the overlap (I_H and I_V, the shortest routes) plus U detours around the pair.
    So this is NOT a zero-candidate case (contrast the coincident / corner tests),
    and the straight through-overlap wire is strictly shorter than any detour."""
    fp = _fp({"A": (0, 0, 100, 100), "B": (50, 50, 150, 150)})
    cands = _gen(fp).generate_candidates("A", ["B"])
    assert cands, "partial-overlap blocks produced NO candidate"
    types = {c.type for c in cands}
    # Straight wires through the shared band exist BECAUSE the faces overlap.
    assert "I_H" in types and "I_V" in types, sorted(types)
    assert any(t.startswith("U_") for t in types), sorted(types)   # detours too
    i_wl = [c.estimated_wirelength for c in cands if c.type in ("I_H", "I_V")]
    u_wl = [c.estimated_wirelength for c in cands if c.type.startswith("U_")]
    assert max(i_wl) < min(u_wl), (i_wl, u_wl)   # through-overlap beats the detour
    for c in cands:
        if c.type in ("I_H", "I_V"):
            assert len(c.segments) == 1, f"{c.type}: I-shape is a single wire"
            assert "BUSTERM_OPEN" not in _violations(c, fp), (c.type, _violations(c, fp))
            ct = buda.ConnTopology(); ct.build(c, fp)
            cs = ct.segs()[0]
            assert cs.perp_hi > cs.perp_lo, f"{c.type}: zero-slide [{cs.perp_lo},{cs.perp_hi}]"


def test_corner_touch_rescued_by_diagonal_L():
    """A and B meet at exactly one corner (A's top-right == B's bottom-left, the
    point (100,100)).  Every direct/L/Z/U segment there is zero-slide and dropped,
    so without a rescue the bus is silently unrouted.  The fallback emits two L's
    routed AROUND the corner (`CORNER_HV`/`CORNER_VH`), each a 2-segment L that taps
    both faces and covers both blocks — the point-contact analogue of edge abutment."""
    fp = _fp({"A": (0, 0, 100, 100), "B": (100, 100, 200, 200)})
    cands = _gen(fp).generate_candidates("A", ["B"])
    assert cands, "corner-touch 2-pin blocks produced NO candidate (silently unrouted)"
    types = sorted(c.type for c in cands)
    assert types == ["CORNER_HV", "CORNER_VH"], types
    for c in cands:
        assert len(c.segments) == 2, f"{c.type}: corner L must have two legs"
        assert "BUSTERM_OPEN" not in _violations(c, fp), (c.type, _violations(c, fp))
        # Every leg must have a real (non-zero) perpendicular slide window.
        ct = buda.ConnTopology(); ct.build(c, fp)
        for cs in ct.segs():
            assert cs.perp_hi > cs.perp_lo, f"{c.type}: zero-slide leg [{cs.perp_lo},{cs.perp_hi}]"


def test_corner_touch_bus_routes_to_completion():
    """End-to-end: an 8-bit bus across a corner point-touch must place ALL bits in
    DetailedNUTS (pre-fix it produced zero candidates and never routed)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ("def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
                  "def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
                  "add_block A 0 0 100 100", "add_block B 100 100 200 200",
                  "add_bus n[8] A.p B.p", "run_bundler", "generate_topologies",
                  "def_track_pattern 4 0 SIGNAL 2 2", "def_track_pattern 5 0 SIGNAL 2 2",
                  "def_track_pattern 6 0 SIGNAL 2 2", "def_track_pattern 7 0 SIGNAL 2 2",
                  "run_planner 3", "run_nuts", "run_detailed_nuts"):
            s.do_command(c)
    assert s.nuts_result.num_violations == 0, "corner-touch bus left a NUTS interval violation"
    assert s.detailed_result.num_unplaced == 0, \
        f"{s.detailed_result.num_unplaced}/8 bits unplaced — corner-touch bus did not route"
