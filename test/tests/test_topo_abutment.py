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


def test_overlap_corner_ls_route_around_overlap():
    """A partial overlap also admits two L's around the FREE outer corners of the
    union — routes that avoid the shared band entirely (add_l_shapes' centre
    projection degenerates them; add_overlap_corner_ls emits them).  For the "/"
    stagger A(0,0,100,100), B(50,50,150,150) the free corners are top-left and
    bottom-right: `L_OVL_TL` (V off A's top outside B, then H into B's left outside
    A) and `L_OVL_BR` (H off A's right, then V into B's bottom)."""
    fp = _fp({"A": (0, 0, 100, 100), "B": (50, 50, 150, 150)})
    cands = _gen(fp).generate_candidates("A", ["B"])
    types = {c.type for c in cands}
    assert {"L_OVL_TL", "L_OVL_BR"} <= types, sorted(types)
    for c in cands:
        if not c.type.startswith("L_OVL"):
            continue
        assert len(c.segments) == 2, f"{c.type}: corner L has two legs"
        # One horizontal + one vertical leg (a real L, not collinear).
        horiz = [s.start.y == s.end.y for s in c.segments]
        assert sorted(horiz) == [False, True], f"{c.type}: legs not perpendicular"
        assert "BUSTERM_OPEN" not in _violations(c, fp), (c.type, _violations(c, fp))
        ct = buda.ConnTopology(); ct.build(c, fp)
        for cs in ct.segs():
            assert cs.perp_hi > cs.perp_lo, f"{c.type}: zero-slide leg"


def test_overlap_corner_ls_other_diagonal():
    """The mirrored "\\" stagger A(0,50,100,150), B(50,0,150,100) frees the other
    diagonal — bottom-left and top-right — so the corner L's are L_OVL_BL / L_OVL_TR."""
    fp = _fp({"A": (0, 50, 100, 150), "B": (50, 0, 150, 100)})
    types = {c.type for c in _gen(fp).generate_candidates("A", ["B"])}
    assert {"L_OVL_BL", "L_OVL_TR"} <= types, sorted(types)


def test_nested_blocks_emit_no_corner_l():
    """One block fully containing the other is NOT a staggered cross — there are no
    exclusive perpendicular faces, so no corner L is invented."""
    fp = _fp({"A": (0, 0, 200, 200), "B": (50, 50, 150, 150)})   # B inside A
    types = {c.type for c in _gen(fp).generate_candidates("A", ["B"])}
    assert not any(t.startswith("L_OVL") for t in types), sorted(types)


def test_overlap_corner_ls_set_wirelength():
    """PR #221 review P2: the L_OVL candidates are emitted after the normal
    annotate/sort pass, so their estimated_wirelength must be set explicitly (else
    it stays 0 and the planner's WL term can't rank the two L's)."""
    fp = _fp({"A": (0, 0, 100, 100), "B": (50, 50, 150, 150)})
    ovl = [c for c in _gen(fp).generate_candidates("A", ["B"]) if c.type.startswith("L_OVL")]
    assert ovl
    for c in ovl:
        want = sum(abs(s.end.x - s.start.x) + abs(s.end.y - s.start.y) for s in c.segments)
        assert c.estimated_wirelength == want > 0, (c.type, c.estimated_wirelength, want)


def test_overlap_corner_ls_respect_corner_margin():
    """PR #221 review P2: L_OVL taps must land on the margin-inset bbox faces, not
    the physical orig_bbox — otherwise a tap sits inside the corner_margin the
    generator is meant to keep clear.  With a 20-unit margin on A(0,0,100,100),
    A's tap faces move to the shrunk box [20,80]²; no L_OVL endpoint may touch A's
    physical edge (x or y in {0,100})."""
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100); fp.set_block_corner_margin("A", 20, 20)
    fp.add_block("B", 30, 50, 400, 400); fp.set_block_corner_margin("B", 20, 20)
    g = buda.TopologyGenerator(fp); g.set_layer_ids(4, 5)
    ovl = [c for c in g.generate_candidates("A", ["B"]) if c.type.startswith("L_OVL")]
    assert ovl, "expected an L_OVL for the margined overlap"
    for c in ovl:
        for s in c.segments:
            for (x, y) in ((s.start.x, s.start.y), (s.end.x, s.end.y)):
                assert x not in (0, 100) and y not in (0, 100), (
                    f"{c.type} taps A's unshrunk face at ({x},{y}) — ignores corner_margin")


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


# ── Overlap corner-wrapping U's (U_OVL_*) + exclusive-band perp clamp ─────────
# For a staggered-cross block pair the generic U's cross a block and double back
# to tap it; add_overlap_corner_us replaces them with corner-wrapping U's whose
# face-tap arm carries a generation-supplied perp clamp pinning it to the tapped
# block's EXCLUSIVE band.  Without the clamp NUTS slides that arm across the far
# block and collapses the detour into a pass-through (bits then land inside the
# far block → DetailedNUTS opens); the clamp makes that impossible by construction.

def _ct(topo, fp):
    ct = buda.ConnTopology(); ct.build(topo, fp)
    return ct


def test_overlap_corner_us_generated_slash():
    """"/" stagger emits the corner-wrapping U's (and no generic pass-through U)."""
    fp = _fp({"A": (0, 0, 400, 400), "B": (200, 200, 600, 600)})
    types = {c.type for c in _gen(fp).generate_candidates("A", ["B"])}
    assert {"U_OVL_HVH", "U_OVL_VHV"} <= types, sorted(types)
    assert not any(t in ("U_top", "U_bot", "U_left", "U_right") for t in types), sorted(types)


def test_overlap_corner_u_hvh_taparm_clamped_below_far_block():
    """"/" U_OVL_HVH: the A-tap H arm (seg0) must be pinned to A's exclusive band
    BELOW B (y ∈ [A.y1, B.y1]).  Pre-clamp its window was A's full face [0,400],
    which lets NUTS slide it up into B's band [200,600] and collapse the detour."""
    fp = _fp({"A": (0, 0, 400, 400), "B": (200, 200, 600, 600)})
    hvh = next(c for c in _gen(fp).generate_candidates("A", ["B"]) if c.type == "U_OVL_HVH")
    seg0 = _ct(hvh, fp).segs()[0]
    assert seg0.perp_hi > seg0.perp_lo, f"tap arm went zero/negative slide [{seg0.perp_lo},{seg0.perp_hi}]"
    assert seg0.perp_lo >= 0 and seg0.perp_hi <= 200, \
        f"A-tap arm y-window [{seg0.perp_lo},{seg0.perp_hi}] escapes A's exclusive band [0,200] (into B)"


def test_overlap_corner_u_vhv_taparm_clamped_left_of_far_block():
    """"/" U_OVL_VHV: the A-tap V arm (seg0) must be pinned LEFT of B (x ∈ [A.x1,B.x1])."""
    fp = _fp({"A": (0, 0, 400, 400), "B": (200, 200, 600, 600)})
    vhv = next(c for c in _gen(fp).generate_candidates("A", ["B"]) if c.type == "U_OVL_VHV")
    seg0 = _ct(vhv, fp).segs()[0]
    assert seg0.perp_hi > seg0.perp_lo, f"tap arm went zero/negative slide [{seg0.perp_lo},{seg0.perp_hi}]"
    assert seg0.perp_lo >= 0 and seg0.perp_hi <= 200, \
        f"A-tap arm x-window [{seg0.perp_lo},{seg0.perp_hi}] escapes A's exclusive band [0,200] (into B)"


def test_overlap_corner_u_hvh_taparm_clamped_above_far_block_backslash():
    """"\\" stagger, U_OVL_HVH: A is the upper-left block, so its right-face tap arm
    must stay ABOVE B (y ∈ [B.y2, A.y2]) — the mirror of the "/" case."""
    fp = _fp({"A": (0, 200, 400, 600), "B": (200, 0, 600, 400)})
    hvh = next(c for c in _gen(fp).generate_candidates("A", ["B"]) if c.type == "U_OVL_HVH")
    seg0 = _ct(hvh, fp).segs()[0]
    assert seg0.perp_hi > seg0.perp_lo, f"tap arm went zero/negative slide [{seg0.perp_lo},{seg0.perp_hi}]"
    assert seg0.perp_lo >= 400 and seg0.perp_hi <= 600, \
        f"A-tap arm y-window [{seg0.perp_lo},{seg0.perp_hi}] escapes A's exclusive band [400,600] (into B)"


def test_overlap_u_dispatch_uses_shrunken_bbox():
    """Codex #224 P2: the U-vs-U_OVL dispatch must gate on the margin-inset `bbox`,
    matching add_overlap_corner_us's own gate.  When corner_margin shrinks two
    physically-overlapping blocks so their usable boxes are DISJOINT, they route as
    disjoint blocks — the bundle must keep the generic U/UU detours and get NO
    U_OVL.  (Testing orig_bbox instead fired the U_OVL branch, whose bbox gate then
    emitted nothing, stranding the bundle with no U detour at all.)"""
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 400, 400); fp.set_block_corner_margin("A", 150, 150)
    fp.add_block("B", 200, 200, 600, 600); fp.set_block_corner_margin("B", 150, 150)
    # bbox_A=[150,250]², bbox_B=[350,450]² → disjoint on both axes.
    g = buda.TopologyGenerator(fp); g.set_layer_ids(4, 5); g.set_double_detour(True)
    types = {c.type for c in g.generate_candidates("A", ["B"])}
    assert any(t.startswith("U_") and not t.startswith("U_OVL") for t in types), sorted(types)
    assert not any(t.startswith(("U_OVL", "UU_OVL")) for t in types), sorted(types)


def test_overlap_corner_u_covers_both_blocks():
    """Every U_OVL candidate must tap/cover both endpoint blocks (no BUSTERM_OPEN)."""
    fp = _fp({"A": (0, 0, 400, 400), "B": (200, 200, 600, 600)})
    us = [c for c in _gen(fp).generate_candidates("A", ["B"]) if c.type.startswith("U_OVL")]
    assert us
    for c in us:
        assert "BUSTERM_OPEN" not in _violations(c, fp), (c.type, _violations(c, fp))


def test_corner_touch_keeps_corner_ls_under_double_detour():
    """The corner L's are FIRST-CLASS candidates, not an empty-list rescue: with
    double_detour the UU wraps clear the two blocks and survive the filters, and
    pre-fix that non-empty list suppressed the CORNER_* emission — turning ON an
    additive knob made the cheapest realization vanish.  Both families must
    coexist."""
    fp = _fp({"A": (0, 0, 100, 100), "B": (100, 100, 200, 200)})
    g = _gen(fp)
    g.set_double_detour(True)
    types = {c.type for c in g.generate_candidates("A", ["B"])}
    assert {"CORNER_HV", "CORNER_VH"} <= types, sorted(types)
    assert any(t.startswith("UU_") for t in types), sorted(types)


def test_overlap_corner_us_mirrors_generated():
    """The 180° mirrors (`*_M`) wrap around the LEFT block (P=A on "/"), offering
    A's far faces (left/bottom) — without them only the right block ever got its
    far faces (the unused-`ux1` compiler warning was the breadcrumb).  Both
    diagonals; UU mirrors appear under double_detour."""
    fp = _fp({"A": (0, 0, 400, 400), "B": (200, 200, 600, 600)})
    types = {c.type for c in _gen(fp).generate_candidates("A", ["B"])}
    assert {"U_OVL_HVH_M", "U_OVL_VHV_M"} <= types, sorted(types)
    g = _gen(fp)
    g.set_double_detour(True)
    types = {c.type for c in g.generate_candidates("A", ["B"])}
    assert {"UU_OVL_HVHV_M", "UU_OVL_VHVH_M"} <= types, sorted(types)
    # "\" diagonal too.
    fp = _fp({"A": (0, 200, 400, 600), "B": (200, 0, 600, 400)})
    types = {c.type for c in _gen(fp).generate_candidates("A", ["B"])}
    assert {"U_OVL_HVH_M", "U_OVL_VHV_M"} <= types, sorted(types)


def test_overlap_corner_u_mirror_taparm_clamped_outside_left_block():
    """"/" mirrors: the B-tap arm (seg0) must stay in B's exclusive band — the
    HVH_M H arm ABOVE A (y ∈ [A.y2, B.y2]), the VHV_M V arm RIGHT of A
    (x ∈ [A.x2, B.x2]) — and the mirror detour arm must stay LEFT/BELOW the
    union, so no leg can slide across A and collapse the wrap."""
    fp = _fp({"A": (0, 0, 400, 400), "B": (200, 200, 600, 600)})
    cands = _gen(fp).generate_candidates("A", ["B"])
    hvh = next(c for c in cands if c.type == "U_OVL_HVH_M")
    segs = _ct(hvh, fp).segs()
    assert segs[0].perp_lo >= 400 and segs[0].perp_hi <= 600, \
        f"B-tap arm y-window [{segs[0].perp_lo},{segs[0].perp_hi}] escapes B's band above A [400,600]"
    assert segs[1].perp_hi <= 0, \
        f"LEFT detour arm x-window [{segs[1].perp_lo},{segs[1].perp_hi}] enters the union (x>0)"
    vhv = next(c for c in cands if c.type == "U_OVL_VHV_M")
    segs = _ct(vhv, fp).segs()
    assert segs[0].perp_lo >= 400 and segs[0].perp_hi <= 600, \
        f"B-tap arm x-window [{segs[0].perp_lo},{segs[0].perp_hi}] escapes B's band right of A [400,600]"
    assert segs[1].perp_hi <= 0, \
        f"BOT detour arm y-window [{segs[1].perp_lo},{segs[1].perp_hi}] enters the union (y>0)"


_PATN = ("POWER 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 "
         "GROUND 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5")


def _route_pinned_overlap_u(kind, double_detour=False):
    """Run an 8-bit bus over the '/' overlap A(0,0,400,400)/B(200,200,600,600),
    pinned to the named corner-wrapping U.  Returns (session, selected_topology)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    gen = "generate_topologies double_detour" if double_detour else "generate_topologies"
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ("def_layer 4 M4 H TOP 55.56", "def_layer 5 M5 V TOP 55.56",
                  f"def_track_pattern 4 0 {_PATN}", f"def_track_pattern 5 0 {_PATN}",
                  "add_block A 0 0 400 400", "add_block B 200 200 600 600",
                  "add_bus n[8] A.tx B.rx", "run_bundler strict", gen):
            s.do_command(c)
        idx = next(i for i, c in enumerate(s.bundles[0].input.candidates) if c.type == kind)
        for c in (f"select_topology 1 {idx + 1}", "run_planner 1",
                  "run_nuts", "run_detailed_nuts"):
            s.do_command(c)
    w = s.bundles[0]
    sel = w.input.candidates[w.plan.selected_topology_index]
    assert sel.type == kind, f"pin did not take: selected {sel.type}"
    return s, sel


def _bit_in_B_interior(detailed, sel, B, eps=1.0):
    """First (seg_idx, bit_index) whose placed bit-wire rectangle penetrates B's
    OPEN interior (B shrunk by eps), or None.  A leg crossing B — the collapse the
    perp clamp prevents — shows up here; face taps (which only touch B's boundary
    from outside) do not."""
    bx1, by1, bx2, by2 = B
    for ns in detailed.net_segments:
        seg = sel.segments[ns.seg_idx]
        horiz = seg.start.y == seg.end.y
        half = ns.width / 2.0
        if horiz:
            rx1, ry1, rx2, ry2 = ns.span_lo, ns.track_position - half, ns.span_hi, ns.track_position + half
        else:
            rx1, ry1, rx2, ry2 = ns.track_position - half, ns.span_lo, ns.track_position + half, ns.span_hi
        ox = min(rx2, bx2 - eps) - max(rx1, bx1 + eps)
        oy = min(ry2, by2 - eps) - max(ry1, by1 + eps)
        if ox > 0 and oy > 0:
            return (ns.seg_idx, ns.bit_index, (rx1, ry1, rx2, ry2))
    return None


def test_overlap_corner_u_family_routes_clear_of_far_block():
    """End-to-end: every corner-wrapping U/UU pinned on an 8-bit bus must place ALL
    bits AND keep every bit-wire out of the wrapped block's interior — B for the
    base family (wraps around B), A for the 180° mirrors (wrap around A).  The
    per-segment perp clamps (face-tap arms in their exclusive band, detour arms
    outside the union) are what prevent a leg collapsing THROUGH the wrapped
    block — pre-clamp the UU detour arm slid straight across it."""
    A = (0, 0, 400, 400)
    B = (200, 200, 600, 600)
    for kind, dd, wrapped in (("U_OVL_HVH", False, B), ("U_OVL_VHV", False, B),
                              ("UU_OVL_HVHV", True, B), ("UU_OVL_VHVH", True, B),
                              ("U_OVL_HVH_M", False, A), ("U_OVL_VHV_M", False, A),
                              ("UU_OVL_HVHV_M", True, A), ("UU_OVL_VHVH_M", True, A)):
        s, sel = _route_pinned_overlap_u(kind, dd)
        assert s.nuts_result.num_violations == 0, f"{kind}: NUTS interval violation"
        assert s.detailed_result.num_unplaced == 0, \
            f"{kind}: {s.detailed_result.num_unplaced}/8 bits unplaced"
        hit = _bit_in_B_interior(s.detailed_result, sel, wrapped)
        assert hit is None, f"{kind}: bit-wire seg {hit[0]} bit {hit[1]} penetrates the wrapped block at {hit[2]}"
