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
Tests for keepout-aware topology generation and trunk+MST hybrid candidates.

Coverage:
  - Trunk positions fully blocked on all H/V layers are suppressed
  - Trunk positions partially blocked (one layer free) are retained
  - 2-pin candidates with fully-blocked segments are filtered
  - Keepout edges extend the Hanan grid so midpoints avoid keepout bands
  - TRUNK+MST hybrid candidates generated for N>=3 blocks with unconnected blocks
  - MST standalone threshold lowered to N>=3
  - set_all_h_layers / set_all_v_layers control the "all-blocked" check
"""
import pytest
import buda


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_gen(fp):
    gen = buda.TopologyGenerator(fp)
    gen.set_layer_ids(4, 5)  # h=M4, v=M5
    return gen


def _type_set(cands):
    """Return the set of topology type prefixes (before '@')."""
    return {c.type.split("@")[0] for c in cands}


def _types_list(cands):
    return [c.type for c in cands]


# ── Keepout filtering — N-pin trunks ─────────────────────────────────────────

def test_trunk_h_position_skipped_when_all_h_layers_blocked():
    """H-trunk position in a band blocked on all H layers is suppressed."""
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("B", 200, 0, 300, 100)
    fp.add_block("C", 400, 0, 500, 100)
    # Blocks all span y=[0,100]. Without keepout: one in-bbox midpoint at y=50.
    # Keepout spanning [0,100] on layer 4 (the only H layer) blocks y=50.
    # No new Hanan edges are added (0 and 100 are already there) → still one
    # midpoint at y=50 → suppressed → zero in-bbox TRUNK_H.
    fp.add_keepout_zone(0, 0, 500, 100, [4])

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C"])
    in_bbox_h = [c for c in cands if "TRUNK_H" in c.type and "_OOB" not in c.type]
    assert len(in_bbox_h) == 0, (
        f"Expected no in-bbox TRUNK_H, got: {[c.type for c in in_bbox_h]}"
    )


def test_trunk_h_position_retained_when_alternate_h_layer_available():
    """H-trunk position blocked on one H layer is kept when another H layer is free."""
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("B", 200, 0, 300, 100)
    fp.add_block("C", 400, 0, 500, 100)
    # Keepout on layer 4 only; layer 3 is a free H layer.
    fp.add_keepout_zone(0, 40, 500, 60, [4])

    gen = _make_gen(fp)
    gen.set_all_h_layers([3, 4])  # two H layers; only layer 4 is blocked
    cands = gen.generate_candidates("A", ["B", "C"])

    # At least one in-bbox TRUNK_H should survive because layer 3 is free.
    in_bbox_h = [c for c in cands if "TRUNK_H" in c.type and "_OOB" not in c.type]
    assert len(in_bbox_h) > 0, (
        "Expected in-bbox TRUNK_H with alternate H layer available, got none"
    )


def test_trunk_h_not_suppressed_without_keepout():
    """Baseline: in-bbox TRUNK_H candidates appear when no keepout is present."""
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("B", 200, 0, 300, 100)
    fp.add_block("C", 400, 0, 500, 100)

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C"])
    in_bbox_h = [c for c in cands if "TRUNK_H" in c.type and "_OOB" not in c.type]
    assert len(in_bbox_h) > 0, "Expected in-bbox TRUNK_H without any keepout"

    # With a full-coverage keepout, in-bbox trunks disappear.
    fp2 = buda.Floorplan()
    fp2.add_block("A", 0, 0, 100, 100)
    fp2.add_block("B", 200, 0, 300, 100)
    fp2.add_block("C", 400, 0, 500, 100)
    fp2.add_keepout_zone(0, 0, 500, 100, [4])
    gen2 = _make_gen(fp2)
    cands2 = gen2.generate_candidates("A", ["B", "C"])
    in_bbox_h2 = [c for c in cands2 if "TRUNK_H" in c.type and "_OOB" not in c.type]
    assert len(in_bbox_h2) == 0, (
        f"Full-coverage keepout should suppress in-bbox TRUNK_H, got {len(in_bbox_h2)}"
    )


def test_keepout_edge_added_to_hanan_grid():
    """Keepout edges appear in the Hanan grid so trunk midpoints avoid interior bands."""
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("B", 0, 400, 100, 500)
    fp.add_block("C", 0, 800, 100, 900)
    # Keepout at y=[220, 280]: after adding these edges, Hanan midpoints in the
    # [100,400] band become (100+220)/2=160 and (280+400)/2=340 — both outside
    # the keepout interior.
    fp.add_keepout_zone(0, 220, 100, 280, [5])

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C"])

    # No in-bbox TRUNK_V should have trunk at y inside [220,280].
    bad = [c for c in cands
           if "TRUNK_V" in c.type and "_OOB" not in c.type
           and 220 <= c.trunk_location <= 280]
    assert len(bad) == 0, (
        f"Found trunks inside keepout band: {[c.type for c in bad]}"
    )


def test_trunk_v_skipped_when_all_v_layers_blocked():
    """V-trunk position blocked on all V layers is suppressed."""
    fp = buda.Floorplan()
    fp.add_block("A", 0,   0, 100, 100)
    fp.add_block("B", 0, 300, 100, 400)
    fp.add_block("C", 0, 600, 100, 700)
    # Block stack is vertical; in-bbox V-trunk midpoints span x=[0,100].
    # Keepout spanning the full x range on layer 5 (only V layer) blocks all
    # in-bbox V positions.  0 and 100 are already Hanan edges, so no new edges
    # are added and there's exactly one midpoint: (0+100)/2=50, blocked.
    fp.add_keepout_zone(0, 0, 100, 700, [5])

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C"])

    in_bbox_v = [c for c in cands if "TRUNK_V" in c.type and "_OOB" not in c.type]
    assert len(in_bbox_v) == 0, (
        f"Expected no in-bbox TRUNK_V, got: {[c.type for c in in_bbox_v]}"
    )


def test_keepout_not_blocking_oob_trunk():
    """Keepout in the in-bbox band does not suppress OOB trunks."""
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("B", 200, 0, 300, 100)
    fp.add_block("C", 400, 0, 500, 100)
    # Fully block all in-bbox H positions.
    fp.add_keepout_zone(0, 0, 500, 100, [4])

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C"])

    oob_h = [c for c in cands if "TRUNK_H_OOB" in c.type]
    assert len(oob_h) > 0, "Expected at least one OOB TRUNK_H even with in-bbox keepout"


# ── Keepout filtering — 2-pin shapes ─────────────────────────────────────────

def test_2pin_lshape_suppressed_when_segment_fully_blocked():
    """L-shape whose horizontal segment is fully blocked on all H layers is removed."""
    # Blocks at same height (y=0..100); L-shape has a horizontal segment at y=50.
    fp = buda.Floorplan()
    fp.add_block("A",   0, 0, 100, 100)
    fp.add_block("B", 300, 0, 400, 100)
    # Block the only horizontal channel (y∈[0,100]) on all H layers.
    fp.add_keepout_zone(0, 0, 400, 100, [4])

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", "B")

    l_shapes = [c for c in cands if c.type.startswith("L_")]
    # L_HV has a horizontal first segment; it should be removed.
    l_hv = [c for c in l_shapes if c.type == "L_HV"]
    assert len(l_hv) == 0, (
        f"L_HV should be suppressed when H segment is fully blocked, got {len(l_hv)}"
    )


def test_2pin_ushape_outside_keepout_retained():
    """U-shape that routes outside the keepout is retained."""
    fp = buda.Floorplan()
    fp.add_block("A",   0, 0, 100, 100)
    fp.add_block("B", 300, 0, 400, 100)
    # Keepout only in the interior of the block bounding box.
    fp.add_keepout_zone(50, 0, 250, 100, [4])

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", "B")

    # U-shapes route outside the bbox so their trunk is OOB; they should survive.
    u_shapes = [c for c in cands if c.type.startswith("U_")]
    assert len(u_shapes) > 0, "Expected at least one U-shape to survive keepout"


# ── Trunk+MST hybrid ─────────────────────────────────────────────────────────

def test_trunk_mst_candidate_generated_for_3_blocks():
    """3 blocks → TRUNK+MST hybrid candidate is present.

    C is offset in y so the placement is NOT a single collinear row: a
    collinear row's every trunk locus produces only degenerate hybrids whose
    stubs all leave the spine at one point (a single-point seed trunk), which
    the generation gate now correctly drops (trunk_is_single_point in
    topology.cpp) — leaving a real, non-degenerate hybrid here."""
    fp = buda.Floorplan()
    fp.add_block("A", 0,   0, 100, 100)
    fp.add_block("B", 200, 0, 300, 100)
    fp.add_block("C", 400, 200, 500, 300)

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C"])
    mst_hybrids = [c for c in cands if "+MST" in c.type]
    assert len(mst_hybrids) > 0, (
        f"Expected TRUNK+MST candidates, got types: {_type_set(cands)}"
    )


def test_trunk_mst_generated_for_4_blocks():
    """4 blocks → both TRUNK and TRUNK+MST candidates appear."""
    fp = buda.Floorplan()
    fp.add_block("A",   0,   0, 100, 100)
    fp.add_block("B", 200,   0, 300, 100)
    fp.add_block("C", 400,   0, 500, 100)
    fp.add_block("D", 200, 400, 300, 500)  # D is off the horizontal trunk spines

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C", "D"])

    trunks     = [c for c in cands if "TRUNK_H" in c.type and "+MST" not in c.type]
    mst_hybrid = [c for c in cands if "+MST" in c.type]
    assert len(trunks)     > 0, "Expected plain TRUNK_H candidates"
    assert len(mst_hybrid) > 0, f"Expected TRUNK+MST candidates, got: {_type_set(cands)}"


def test_trunk_mst_type_string_contains_mst():
    """TRUNK+MST candidate type string contains '+MST'."""
    fp = buda.Floorplan()
    fp.add_block("A", 0,   0, 100, 100)
    fp.add_block("B", 200, 0, 300, 100)
    fp.add_block("C", 400, 200, 500, 300)  # offset vertically so trunk misses it

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C"])
    mst_hybrids = [c for c in cands if "+MST" in c.type]
    for c in mst_hybrids:
        assert "+MST" in c.type, f"Type should contain '+MST': {c.type}"


# ── MST edge-identity tagging (Segment.edge_id) ──────────────────────────────
#
# Each MST-realized leg is stamped with the index of the MST edge it belongs to,
# so a later stage can flip one edge's L/Z realization in place.  Non-MST
# segments (trunk spines, plain stubs) stay edge_id == -1.

def _diag_4block_fp():
    """Four blocks in a staggered/diagonal layout so MST edges are true
    diagonals (two-leg L's), giving edges with 2 tagged segments each."""
    fp = buda.Floorplan()
    fp.add_block("A",   0,   0, 100, 100)
    fp.add_block("B", 300, 150, 400, 250)
    fp.add_block("C", 600, 350, 700, 450)
    fp.add_block("D", 900, 550, 1000, 650)
    return fp


def test_standalone_mst_legs_are_edge_tagged():
    """Every segment of an MST_HV / MST_VH candidate carries a non-negative
    edge_id, and the ids partition the legs into per-edge groups of 1-2 (a
    straight edge = 1 leg, a diagonal L = 2 legs)."""
    fp = _diag_4block_fp()
    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C", "D"])
    mst = [c for c in cands if c.type.split("@")[0] in ("MST_HV", "MST_VH")]
    assert mst, f"Expected standalone MST candidates, got {_type_set(cands)}"
    for c in mst:
        assert c.segments, c.type
        # Standalone MST is pure edge legs — every segment is tagged.
        assert all(s.edge_id >= 0 for s in c.segments), \
            f"{c.type}: untagged leg(s): {[s.edge_id for s in c.segments]}"
        groups = {}
        for s in c.segments:
            groups.setdefault(s.edge_id, []).append(s)
        # 4 blocks → MST has exactly 3 edges → at most 3 distinct ids.
        assert len(groups) <= 3, f"{c.type}: {len(groups)} edge groups > 3"
        for eid, segs in groups.items():
            assert 1 <= len(segs) <= 2, \
                f"{c.type}: edge {eid} has {len(segs)} legs (want 1-2)"


def test_trunk_mst_hybrid_spine_untagged_legs_tagged():
    """In a TRUNK+MST hybrid the MST-edge legs are tagged while the trunk spine
    (and plain stubs) stay edge_id == -1."""
    fp = _diag_4block_fp()
    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C", "D"])
    hybrids = [c for c in cands if "+MST" in c.type]
    assert hybrids, f"Expected TRUNK+MST candidates, got {_type_set(cands)}"
    for c in hybrids:
        tagged   = [s for s in c.segments if s.edge_id >= 0]
        untagged = [s for s in c.segments if s.edge_id < 0]
        # A hybrid has at least one MST leg (tagged) and a trunk spine (untagged).
        assert tagged,   f"{c.type}: no tagged MST legs"
        assert untagged, f"{c.type}: no untagged trunk/stub segments"


def test_non_mst_candidates_have_no_edge_tags():
    """Plain L/Z/U/TRUNK candidates carry no edge identity — every segment is -1."""
    fp = _diag_4block_fp()
    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C", "D"])
    for c in cands:
        if "MST" in c.type:
            continue
        assert all(s.edge_id == -1 for s in c.segments), \
            f"{c.type}: non-MST candidate has edge-tagged segment(s)"


# ── Smart per-edge default: dodge a keepout-blocked leg (step 2) ─────────────

def _mst_hv(cands):
    for c in cands:
        if c.type.split("@")[0] == "MST_HV":
            return c
    return None


def _seg_set(c):
    return {(s.start.x, s.start.y, s.end.x, s.end.y) for s in c.segments}


def test_smart_default_flips_blocked_edge_to_clear_L():
    """When the default (H-first) L of an MST edge is keepout-blocked on all H
    layers, the smart per-edge default realizes that edge with the alternate
    (V-first) L instead, so no H leg still crosses the blockage — while an
    unblocked run keeps the default orientation."""
    # Baseline: no keepout — record MST_HV geometry (default orientation).
    # (Hold fp in a named var: TopologyGenerator keeps a reference to it, so a
    # temporary Floorplan would be GC'd out from under generate_candidates.)
    fp0 = _diag_4block_fp()
    base = _mst_hv(_make_gen(fp0).generate_candidates("A", ["B", "C", "D"]))
    assert base is not None, "expected an MST_HV candidate"
    # The A->B edge's default H-first H-leg runs at y≈A-face across x∈[100,300].
    # Block it on the H layer (M4) so the smart default must flip that edge.
    fp = _diag_4block_fp()
    fp.add_keepout_zone(110, 40, 290, 120, [4])   # covers the H-first H-leg band
    kept = _mst_hv(_make_gen(fp).generate_candidates("A", ["B", "C", "D"]))
    assert kept is not None

    # The blockage must actually change the realization (non-vacuous).
    assert _seg_set(base) != _seg_set(kept), \
        "keepout on the default L-leg should flip an edge's orientation"

    # After flipping, no H leg (layer M4) may still run through the keepout band.
    kx1, ky1, kx2, ky2 = 110, 40, 290, 120
    for s in kept.segments:
        is_h = (s.start.y == s.end.y)
        if is_h and s.layer_hint == 4:
            y = s.start.y
            x1, x2 = sorted((s.start.x, s.end.x))
            crosses = (ky1 <= y <= ky2) and not (x2 < kx1 or x1 > kx2)
            assert not crosses, \
                f"H leg still crosses keepout after smart flip: " \
                f"{(s.start.x, s.start.y, s.end.x, s.end.y)}"


# ── Per-edge L/Z flip primitive (step 4a: flip_mst_edge) ─────────────────────

def _edge_groups(topo):
    groups = {}
    for i, s in enumerate(topo.segments):
        if s.edge_id >= 0:
            groups.setdefault(s.edge_id, []).append(i)
    return groups


def _edge_far_and_bend(topo, i, j):
    a, b = topo.segments[i], topo.segments[j]
    pa = {(a.start.x, a.start.y), (a.end.x, a.end.y)}
    pb = {(b.start.x, b.start.y), (b.end.x, b.end.y)}
    bend = (pa & pb).pop()           # shared endpoint = the bend
    far = frozenset(pa ^ pb)         # the two non-shared endpoints = p1, p2
    return far, bend


def test_flip_mst_edge_moves_bend_and_preserves_endpoints():
    """Flipping a 2-leg diagonal MST edge moves the bend to the opposite corner,
    keeps the edge's two endpoints (so connectivity is preserved), reuses the same
    segment slots + edge_id, and is an involution."""
    fp = _diag_4block_fp()
    gen = _make_gen(fp)
    mst = _mst_hv(gen.generate_candidates("A", ["B", "C", "D"]))
    assert mst is not None
    groups = _edge_groups(mst)
    eid = next((e for e, idx in groups.items() if len(idx) == 2), None)
    assert eid is not None, "expected a 2-leg diagonal MST edge"
    i, j = groups[eid]

    far0, bend0 = _edge_far_and_bend(mst, i, j)
    assert buda.flip_mst_edge(mst, eid, 4, 5, fp) is True
    far1, bend1 = _edge_far_and_bend(mst, i, j)
    assert far1 == far0, "flip must preserve the edge's two endpoints"
    assert bend1 != bend0, "flip must move the bend to the other corner"
    # Same slots + identity; legs are still one H + one V (a valid L).
    assert mst.segments[i].edge_id == eid and mst.segments[j].edge_id == eid
    dirs = {(mst.segments[k].start.y == mst.segments[k].end.y) for k in (i, j)}
    assert dirs == {True, False}, "flipped legs must be one horizontal + one vertical"
    # Re-annotates cleanly (junction geometry moved).
    buda.annotate_topology(mst, fp)

    # Involution: flipping again restores the original bend.
    assert buda.flip_mst_edge(mst, eid, 4, 5, fp) is True
    far2, bend2 = _edge_far_and_bend(mst, i, j)
    assert far2 == far0 and bend2 == bend0


def test_flip_mst_edge_noop_for_unknown_or_untagged():
    """flip_mst_edge returns False (no change) for an unknown edge id, the -1
    "not an MST leg" sentinel (must not collect untagged segments), and a non-MST
    candidate whose segments carry no edge identity."""
    fp = _diag_4block_fp()
    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C", "D"])
    mst = _mst_hv(cands)
    assert mst is not None
    assert buda.flip_mst_edge(mst, 99999, 4, 5, fp) is False   # unknown id
    assert buda.flip_mst_edge(mst, -1, 4, 5, fp) is False       # sentinel guard
    # A non-MST candidate: no edge_id >= 0, so any flip is a no-op (incl. the -1
    # sentinel, which must NOT collect its untagged segments).
    nonmst = next((c for c in cands if "MST" not in c.type), None)
    if nonmst is not None:
        assert buda.flip_mst_edge(nonmst, -1, 4, 5, fp) is False


def _seg(x1, y1, x2, y2, layer, edge_id):
    s = buda.Segment()
    s.start = buda.Point(x1, y1)
    s.end = buda.Point(x2, y2)
    s.layer_hint = layer
    s.edge_id = edge_id
    return s


def test_flip_mst_edge_rejects_bend_on_block_corner():
    """The alternate bend of a corner-diagonal edge IS the block corner the
    generator routed around, so flip_mst_edge must reject a flip whose opposite
    bend lands on (or inside) a block — leaving the geometry untouched — while the
    same edge in open space flips normally."""
    # Edge (100,50)->(250,150), currently bent at (250,50).  Opposite bend =
    # (100,50)+(250,150)-(250,50) = (100,150).
    def make_topo():
        t = buda.Topology()
        t.type = "MST_HV"
        t.segments = [_seg(100, 50, 250, 50, 4, 0),    # H leg to the current bend
                      _seg(250, 50, 250, 150, 5, 0)]   # V leg to p2
        return t

    # A block whose CORNER is exactly the opposite bend (100,150) -> reject.
    fp_corner = buda.Floorplan()
    fp_corner.add_block("K", 100, 150, 200, 250)
    t1 = make_topo()
    before = [(s.start.x, s.start.y, s.end.x, s.end.y) for s in t1.segments]
    assert buda.flip_mst_edge(t1, 0, 4, 5, fp_corner) is False, \
        "flip onto a block corner must be rejected"
    after = [(s.start.x, s.start.y, s.end.x, s.end.y) for s in t1.segments]
    assert before == after, "a rejected flip must not mutate the geometry"

    # Same edge, empty floorplan: the flip is allowed (bend moves to (100,150)).
    t2 = make_topo()
    assert buda.flip_mst_edge(t2, 0, 4, 5, buda.Floorplan()) is True
    bends = {(s.end.x, s.end.y) for s in t2.segments} | \
            {(s.start.x, s.start.y) for s in t2.segments}
    assert (100, 150) in bends, "unobstructed flip should reach the opposite bend"


def test_flip_mst_edge_refuses_when_a_stub_t_junctions_a_leg_interior():
    """A foreign segment T-junctioned on the INTERIOR of an MST edge's leg
    depends on that leg's whole extent — the MST TEG attachment stub
    (Final-state limitation 1's fix) hangs an OVER rect's stub off an
    arbitrary tree segment — and the flip moves the extent wholesale, so
    nothing would repair the stranded junction (ripup's `_rr_apply_move`
    re-derives seg_conns but not geometry).  The bend-anchor guard now
    covers the whole leg; the legs' far endpoints (p1/p2) survive the flip,
    so a sibling sharing one of those must NOT block it."""
    def make_topo(stub):
        t = buda.Topology()
        t.type = "MST_HV"
        t.segments = [_seg(100, 50, 250, 50, 4, 0),     # H leg p1 -> bend
                      _seg(250, 50, 250, 150, 5, 0),    # V leg bend -> p2
                      stub]                              # foreign attachment
        return t

    fp = buda.Floorplan()
    # Mid-leg T-junction at (180,50) — strictly inside the H leg: refuse.
    t1 = make_topo(_seg(180, 110, 180, 50, 5, -1))
    before = [(s.start.x, s.start.y, s.end.x, s.end.y) for s in t1.segments]
    assert buda.flip_mst_edge(t1, 0, 4, 5, fp) is False, \
        "flip must refuse when a foreign endpoint sits on a leg's interior"
    assert before == [(s.start.x, s.start.y, s.end.x, s.end.y)
                      for s in t1.segments], \
        "a refused flip must not mutate the geometry"

    # A stub anchored at p1 (100,50) — a far endpoint that SURVIVES the
    # flip — must not block it.
    t2 = make_topo(_seg(40, 50, 100, 50, 4, -1))
    assert buda.flip_mst_edge(t2, 0, 4, 5, fp) is True, \
        "a junction at the edge's far endpoint survives the flip"


def test_mst_teg_attachment_tie_prefers_single_t_stub():
    """The attachment pass's tie rule, pinned on a hand-built geometry (via
    the public floorplan overload — the test_planner_notch_low precedent):
    when a LATER along-overlapping segment offers a single perpendicular
    T-stub at the SAME total length as an EARLIER segment's two-leg L, the
    T-stub must win — one segment, no bend, fewer routing constraints; the
    pass's contract says equal-cost ties prefer it.  The first cut compared
    with a strict `<`, so the equal-cost later T could never displace the
    earlier L (Codex P2 on PR #846, measured: the tie emitted the two-leg L
    (900,50)->(600,50)->(600,150) instead of the single stub).

    The tie rule in full, kept deterministic in segment-index visit order:
    a T displaces an equal-cost L; an equal-cost same-kind candidate never
    displaces an earlier one (lower index wins); an equal-cost L never
    displaces anything."""
    def seg_at(x1, y1, x2, y2):
        return _seg(x1, y1, x2, y2, 4, -1)

    fp = buda.Floorplan()
    fp.add_block_rects("T2", [(500, 150, 600, 250),    # reached rect
                              (900, 0, 1000, 100)])    # unreached rect
    fp.set_block_teg_mode("T2", buda.TegMode.OVER)

    # L-offering segment FIRST (cost 300+100=400), equal-cost T-offering
    # segment SECOND (gap 500-100=400): the tie must yield the single T-stub
    # from the rect's top face at its along-centre.
    t = buda.Topology()
    t.type = "MST_HV"
    t.segments = [seg_at(500, 150, 700, 150),    # touches rect#0; L offer
                  seg_at(850, 500, 1050, 500)]   # T offer, same total length
    buda.add_mst_teg_attachments(t, fp, 4, 5)
    added = [(s.start.x, s.start.y, s.end.x, s.end.y) for s in t.segments[2:]]
    assert added == [(950, 100, 950, 500)], (
        "equal-cost tie must pick the single perpendicular T-stub, got "
        f"{added}")
    # Face-seeded tap on the block (the #823 orientation: face at the START).
    bts = t.seg_busterms.get(2, (None, None))
    assert bts[0] is not None and bts[0].block_name == "T2"

    # Determinism control: a strictly CHEAPER L must still beat the T — the
    # tie preference is not a blanket kind preference.
    t2 = buda.Topology()
    t2.type = "MST_HV"
    t2.segments = [seg_at(500, 150, 700, 150),   # L offer, cost 400
                   seg_at(850, 600, 1050, 600)]  # T offer, gap 500 (dearer)
    buda.add_mst_teg_attachments(t2, fp, 4, 5)
    added2 = [(s.start.x, s.start.y, s.end.x, s.end.y) for s in t2.segments[2:]]
    assert added2 == [(900, 50, 600, 50), (600, 50, 600, 150)], (
        "a strictly cheaper L must still win over a dearer T-stub, got "
        f"{added2}")


def test_trunk_mst_has_more_segments_than_trunk():
    """TRUNK+MST has more segments than its corresponding plain TRUNK."""
    fp = buda.Floorplan()
    fp.add_block("A", 0,   0, 100, 100)
    fp.add_block("B", 200, 0, 300, 100)
    fp.add_block("C", 200, 400, 300, 500)  # off trunk spine for H trunk

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C"])

    plain_trunks = [c for c in cands if "TRUNK_H" in c.type and "+MST" not in c.type]
    mst_trunks   = [c for c in cands if "TRUNK_H" in c.type and "+MST"  in c.type]

    if not plain_trunks or not mst_trunks:
        pytest.skip("No matching trunk pair available for this geometry")

    # Find a TRUNK+MST that corresponds to a plain TRUNK (same trunk_location).
    # The MST hybrid adds inter-branch shortcut edges, so it never has FEWER
    # segments than the matching plain trunk.  It may have the SAME count when an
    # orthogonal relay is completed by extending stubs (over-the-cell) rather than
    # adding a connector, so the bound is >= not strictly >.
    trunk_by_loc = {c.trunk_location: c for c in plain_trunks}
    for mst in mst_trunks:
        loc = mst.trunk_location
        if loc in trunk_by_loc:
            assert len(mst.segments) >= len(trunk_by_loc[loc].segments), (
                f"TRUNK+MST should not have fewer segments than its plain trunk: "
                f"{len(mst.segments)} vs {len(trunk_by_loc[loc].segments)}"
            )
            return
    pytest.skip("No overlapping trunk_location found between plain and MST trunks")


def test_trunk_mst_connected_block_names_complete():
    """TRUNK+MST connected_block_names covers all blocks."""
    fp = buda.Floorplan()
    fp.add_block("A", 0,   0, 100, 100)
    fp.add_block("B", 200, 0, 300, 100)
    fp.add_block("C", 200, 400, 300, 500)

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C"])
    mst_hybrids = [c for c in cands if "+MST" in c.type]
    if not mst_hybrids:
        pytest.skip("No TRUNK+MST candidate for this geometry")

    all_names = {"A", "B", "C"}
    for c in mst_hybrids:
        names = set(c.connected_block_names)
        assert names >= all_names, (
            f"TRUNK+MST missing blocks: {all_names - names}"
        )


def test_trunk_mst_wirelength_is_honest():
    """TRUNK+MST estimated_wirelength reflects its ACTUAL segments.

    Before completion landed, the hybrid added shortcut edges on top of the full
    trunk (so its WL was always >= the plain trunk).  The completion redesign makes
    each MST edge *replace* a child block's trunk stub, so a +MST hybrid is a clean
    trunk-rooted tree whose wirelength can be BELOW the plain trunk -- and must
    equal the sum of its own segment lengths (no under/over-counting)."""
    fp = buda.Floorplan()
    fp.add_block("A", 0,   0, 100, 100)
    fp.add_block("B", 200, 0, 300, 100)
    fp.add_block("C", 200, 400, 300, 500)

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C"])

    hybrids = [c for c in cands if "+MST" in c.type]
    assert hybrids, "expected at least one TRUNK+MST hybrid"
    for c in hybrids:
        seg_sum = sum(abs(s.end.x - s.start.x) + abs(s.end.y - s.start.y)
                      for s in c.segments)
        assert c.estimated_wirelength == seg_sum, (
            f"{c.type}: estimated_wirelength {c.estimated_wirelength} != "
            f"segment-length sum {seg_sum}"
        )


def test_trunk_teg_over_metal_is_ordinary_segments_and_wl_honest():
    """TEG-OVER connection metal is ordinary segments, honestly priced.

    Historically a gap trunk emitted a "bridge segment" along the union-bbox
    outer face, kept in Topology.bridge_segments OUTSIDE .segments, and
    wirelength() added it separately (defect 4) — while nothing downstream
    ever placed it.  Open 1(a) replaced the bridge with real per-rect gap
    stubs in .segments, so: (a) generation emits NO bridge_segments, (b) both
    rects of the OVER block are tapped by real stub metal, and (c)
    estimated_wirelength equals the plain segment sum — priced metal IS the
    metal the router builds.  wirelength() still adds bridge_segments for
    candidates restored from pre-change checkpoints (see load_pipeline)."""
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    # Two disjoint rects with a vertical gap; an H-trunk in the gap stubs both
    # rects to the trunk (joined through it).
    fp.add_block_rects("M", [(300, 0, 400, 100), (300, 300, 400, 400)],
                       teg_mode=buda.TegMode.OVER)
    fp.add_block("B", 300, 600, 400, 700)

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["M", "B"])

    assert all(not c.bridge_segments for c in cands), \
        "generation must no longer emit bridge_segments"
    # Find a gap trunk (H trunk strictly between M's rects, y in (100, 300)).
    gap = []
    for c in cands:
        if not c.type.startswith("TRUNK_H@y"):
            continue
        y = int(c.type.split("@y")[1].split("+")[0])
        if 100 < y < 300:
            gap.append((c, y))
    assert gap, "expected at least one gap-trunk candidate"
    for c, y in gap:
        # Both rects tapped by V stubs reaching the trunk.
        m_stub_faces = set()
        for i, s in enumerate(c.segments):
            if s.start.x != s.end.x:
                continue
            bts = c.seg_busterms.get(i, (None, None))
            if any(bt is not None and bt.block_name == "M" for bt in bts):
                assert y in (s.start.y, s.end.y), \
                    f"{c.type}: M stub does not reach the trunk"
                m_stub_faces.add(min(s.start.y, s.end.y) if y == max(s.start.y, s.end.y)
                                 else max(s.start.y, s.end.y))
        assert m_stub_faces >= {100, 300}, (
            f"{c.type}: expected stubs to both rect faces (y=100 top of lower, "
            f"y=300 bottom of upper), got {sorted(m_stub_faces)}"
        )
        seg_sum = sum(abs(s.end.x - s.start.x) + abs(s.end.y - s.start.y)
                      for s in c.segments)
        assert c.estimated_wirelength == seg_sum, (
            f"{c.type}: estimated_wirelength {c.estimated_wirelength} != "
            f"segment-length sum {seg_sum}"
        )


def test_trunk_mst_no_legs_shorter_than_min_stub():
    """TRUNK+MST edges never emit a leg shorter than the minimum stub length.

    Regression for the incomplete-L-leg bug: a diagonal branch-pair whose
    second leg fell below the minimum was previously truncated, leaving a
    dangling shortcut.  Now such an edge invalidates the whole candidate, so
    every emitted TRUNK+MST segment must satisfy the per-direction minimum.
    """
    fp = buda.Floorplan()
    # Two branch blocks with a small (10-unit) horizontal gap and a larger
    # vertical separation — the diagonal MST edge would have a sub-minimum
    # H leg under the default min stub length.
    fp.add_block("A", 0,   0,   100, 100)   # spine block
    fp.add_block("B", 300, 400, 400, 500)   # branch
    fp.add_block("C", 410, 800, 510, 900)   # branch, 10-unit x gap from B's right
    fp.set_min_stub_length(20)

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C"])

    m_h = fp.get_min_stub_length(buda.LayerDir.HORIZONTAL, 4)
    m_v = fp.get_min_stub_length(buda.LayerDir.VERTICAL, 5)
    for c in cands:
        if "+MST" not in c.type:
            continue
        ct = buda.ConnTopology()
        ct.build(c, fp)
        for cs in ct.segs():
            is_stub = any(co.kind == buda.SegConnKind.BUSTERM for co in cs.conns)
            if not is_stub:
                continue
            length = abs(cs.along_hi - cs.along_lo)
            m = m_h if cs.horiz else m_v
            assert length >= m, (
                f"TRUNK+MST {c.type} has sub-minimum stub: {length} < {m}"
            )


def test_mst_any_for_3_blocks():
    """Any MST-type candidate (TRUNK+MST hybrid) is present for 3 blocks.

    Standalone MST (MST_*) requires N≥4; for N=3 the TRUNK+MST hybrid
    already provides MST-like inter-block connectivity on top of the trunk.
    """
    fp = buda.Floorplan()
    fp.add_block("A", 0,   0,  100, 100)
    fp.add_block("B", 500, 0,  600, 100)   # far right
    fp.add_block("C", 250, 300, 350, 400)  # below center

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C"])

    mst_cands = [c for c in cands if "MST" in c.type]
    assert len(mst_cands) > 0, (
        f"Expected MST-type candidates for 3 blocks. Got types: {_type_set(cands)}"
    )


def test_mst_any_for_3_blocks_no_beneficial_shortcut():
    """3-block bundle keeps MST coverage even when no inter-block edge beats a stub.

    The selective trunk+MST stub replacement drops a stub only when its MST parent
    edge is a strict shortcut.  When no block has a beneficial shortcut, a <4-block
    bundle (which has no standalone MST_* candidate, since add_mst_candidates needs
    N>=4) now FORCES the completed trunk-rooted tree -- using every tree edge -- so a
    clean TRUNK+MST candidate is still emitted instead of falling through to the
    cyclic legacy hybrid, which the clean-tree gate would drop.  Regression guard for
    that forced-tree fall-through (preserves ad29c1f's 3-pin MST coverage).
    """
    # Three blocks spread along a row: short perpendicular stubs to the trunk,
    # long inter-block edges -- a configuration prone to having no shortcut.
    fp = buda.Floorplan()
    fp.add_block("A", 862, 331, 962, 431)
    fp.add_block("B", 236, 923, 336, 1023)
    fp.add_block("C", 342, 1394, 442, 1494)

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C"])
    mst_cands = [c for c in cands if "MST" in c.type]
    assert len(mst_cands) > 0, (
        f"Expected MST-type coverage for 3 blocks with no beneficial shortcut. "
        f"Got types: {_type_set(cands)}"
    )


def test_mst_coverage_for_non_simple_3_blocks():
    """A NON-simple 3-block bundle (multi-rect/TEG branch) still gets MST coverage.

    The forced trunk-rooted tree only fires for the 'simple' case (all branch blocks
    single-rect with a stub-owning root).  A multi-rect/TEG branch falls through to
    the legacy hybrid, which the clean-tree gate may drop when completion can't form a
    tree without dangling the branch's bridge.  Because add_mst_candidates emits no
    standalone MST_* below 4 blocks, dropping every trunk position would leave the
    bundle with no MST-type coverage at all -- so add_trunk_mst_candidates keeps a
    single un-completed hybrid as a last-resort coverage fallback (its over-counted
    wirelength keeps the planner from ever preferring it to the plain trunk).  Either
    way the bundle must retain at least one MST-type candidate.  (Regression guard for
    the P2 review on PR #55: non-simple <4-block hybrids must not silently lose MST
    coverage.)
    """
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 80, 80)            # driver, single-rect
    fp.add_block("B", 600, 0, 680, 80)         # single-rect branch
    # Multi-rect (TEG-OVER) branch -> the bundle is 'non-simple'.
    fp.add_block_rects("C", [(200, 200, 280, 280), (200, 460, 280, 540)],
                       teg_mode=buda.TegMode.OVER)

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C"])
    mst_cands = [c for c in cands if "MST" in c.type]
    assert len(mst_cands) > 0, (
        f"Non-simple 3-block bundle lost all MST-type coverage. "
        f"Got types: {_type_set(cands)}"
    )


def test_trunk_mst_candidates_are_clean_trees():
    """Every generated trunk+MST candidate is a physically self-connected tree.

    Defect-3 gate: the legacy hybrid path (full trunk + ALL edges) is cyclic, so it
    is now routed through complete_relay_junctions + topology_is_clean_tree and a
    candidate that cannot be cleanly completed is DROPPED rather than emitted with a
    silent through-block relay.  For >=4 blocks (this config) the base trunk +
    standalone MST_* cover the bundle, so no +MST / MST_* candidate may carry a
    FEEDTHRU_RELAY violation.  (The only exception is the <4-block last-resort coverage
    fallback, which cannot apply here -- see test_mst_coverage_for_non_simple_3_blocks.)
    """
    fp = buda.Floorplan()
    # 4 blocks: B/C straddle the trunk row at very different x, D sits off-spine --
    # the configuration that previously produced un-completed legacy hybrids.
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("B", 200, 0, 300, 100)
    fp.add_block("C", 600, 0, 700, 100)
    fp.add_block("D", 400, 400, 500, 500)

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C", "D"])
    mst_cands = [c for c in cands if "MST" in c.type]
    assert mst_cands, "expected MST-type candidates for 4 blocks"
    for c in mst_cands:
        ct = buda.ConnTopology()
        ct.build(c, fp)
        res = buda.check_topo(ct, c, fp, 0)
        kinds = [str(v.kind) for v in res.violations]
        assert not any("FEEDTHRU_RELAY" in k for k in kinds), (
            f"{c.type} is a silent feedthru relay (should have been gated): {kinds}"
        )


def test_trunk_mst_spine_not_overextended_past_junctions():
    """A completed TRUNK_H+MST spine ends at its outermost trunk-line junction.

    Defect-1: the trunk span is sized in add_trunk_h from *all* branch blocks; when
    an MST edge replaces+drops a block's stub, the spine was copied verbatim and left
    dangling dead wire past the last real connection.  clip_spine_to_landings now
    re-clips the spine to the extreme kept junction -- counting BOTH a stub endpoint
    that lands on the trunk line AND a perpendicular segment that *crosses* it (a
    T-junction) -- extended only to still span any pass-through block.  Assert no
    in-bbox TRUNK_H+MST spine extends past its outermost such junction.
    """
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)        # root, near trunk
    fp.add_block("B", 300, 0, 400, 100)      # branch, near trunk
    fp.add_block("C", 500, 400, 600, 500)    # branch, far off-spine (reached via edge)

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C"])
    names = ["A", "B", "C"]
    checked = 0
    for c in cands:
        if not c.type.startswith("TRUNK_H+MST"):
            continue                          # in-bbox H completed-tree hybrids only
        tp = c.trunk_location
        spine = [s for s in c.segments if s.start.y == s.end.y == tp]
        if len(spine) != 1:
            continue                          # feedthru-split spine is not clipped
        sp = spine[0]
        lo, hi = sorted((sp.start.x, sp.end.x))
        # Junctions: non-spine endpoints on the trunk line, plus perpendicular
        # crossings of it.
        junc = set()
        for s in c.segments:
            if s is sp:
                continue
            for p in (s.start, s.end):
                if p.y == tp:
                    junc.add(p.x)
            if s.start.x == s.end.x and min(s.start.y, s.end.y) <= tp <= max(s.start.y, s.end.y):
                junc.add(s.start.x)           # V seg crossing the H trunk
        # Pass-through blocks the spine must still cover.
        spans = []
        for nm in names:
            b = fp.get_block_bounds(nm)
            if b.y1 <= tp <= b.y2:
                spans.append((b.x1, b.x2))

        def anchored(v):
            return v in junc or any(bl <= v <= bh for bl, bh in spans)

        assert anchored(lo) and anchored(hi), (
            f"{c.type}: spine [{lo},{hi}] dangles past its outermost junction "
            f"(junctions={sorted(junc)}, pass-through spans={spans})"
        )
        checked += 1
    assert checked > 0, "no in-bbox TRUNK_H+MST candidate exercised the spine clip"


def test_mst_not_generated_for_2_blocks():
    """No MST-type candidate is generated for 2 blocks (degenerates to L/Z/U shapes)."""
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("B", 200, 200, 300, 300)

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B"])
    mst_cands = [c for c in cands if "MST" in c.type]
    assert len(mst_cands) == 0, (
        f"No MST candidate should be generated for 2 blocks, got: {[c.type for c in mst_cands]}"
    )


# ── set_all_h_layers / set_all_v_layers API ──────────────────────────────────

def test_set_all_h_layers_controls_blocking_check():
    """set_all_h_layers determines whether a keepout fully blocks H trunks."""
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("B", 200, 0, 300, 100)
    fp.add_block("C", 400, 0, 500, 100)
    # Spanning keepout on layer 4 only: blocks y=[0,100], the only in-bbox band.
    fp.add_keepout_zone(0, 0, 500, 100, [4])

    # Single H layer (4): fully blocked → trunks suppressed.
    gen1 = _make_gen(fp)
    gen1.set_all_h_layers([4])
    cands1 = gen1.generate_candidates("A", ["B", "C"])
    in_h1 = [c for c in cands1 if "TRUNK_H" in c.type and "_OOB" not in c.type]

    # Two H layers (3, 4): layer 3 is free → trunks retained.
    gen2 = _make_gen(fp)
    gen2.set_all_h_layers([3, 4])
    cands2 = gen2.generate_candidates("A", ["B", "C"])
    in_h2 = [c for c in cands2 if "TRUNK_H" in c.type and "_OOB" not in c.type]

    assert len(in_h1) == 0, "With single H layer blocked, in-bbox TRUNK_H should be suppressed"
    assert len(in_h2) > 0,  "With alternate H layer free, in-bbox TRUNK_H should survive"


# ── Integration ──────────────────────────────────────────────────────────────

def test_congestion_planner_accepts_trunk_mst_topology():
    """CongestionPlanner can select a TRUNK+MST topology without error."""
    fp = buda.Floorplan()
    fp.add_block("A", 0,   0, 100, 100)
    fp.add_block("B", 200, 0, 300, 100)
    fp.add_block("C", 200, 400, 300, 500)

    ls = buda.LayerStack()
    ls.add_layer(3, "M3", buda.LayerDir.HORIZONTAL, buda.LayerType.LOW)
    ls.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(5, "M5", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C"])

    bundle = buda.HBundle()
    bundle.id = 0
    bundle.net_names = ["n0"]

    w = buda.BundleWrapper()
    w.input.original_bundle = bundle
    w.input.width = 2.0
    w.input.candidates = cands

    planner = buda.CongestionPlanner(fp, ls)
    planner.build_congestion_map()
    assignments = planner.optimize_topologies([w], 1)

    assert len(assignments) == 1
    asn = assignments[0]
    assert asn.topo_index >= 0, f"Expected valid topo_index, got {asn.topo_index}"


def test_keepout_does_not_eliminate_all_candidates():
    """Even with a keepout, at least some candidates (OOB trunks) remain."""
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("B", 200, 0, 300, 100)
    fp.add_block("C", 400, 0, 500, 100)
    # Heavy keepout covers in-bbox bands on all H layers.
    fp.add_keepout_zone(0, 0, 500, 100, [4])

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C"])

    assert len(cands) > 0, "Should always have some candidates (OOB trunks etc.)"


def test_2pin_u_shape_clears_keepout_when_blocks_at_same_y_level():
    """U_VHV detour is generated when a keepout's Y range exceeds the block Y span.

    Regression for the gap3 demonstrator: L and R are at the same Y level
    [200,300], and the keepout [300,100]–[600,450] on M4+M5 covers the block Y
    range entirely.  Without keepout edges in the 2-pin Hanan grid the auto-
    margin (10% of block Y span = 20) gives OOB trunk positions y=180 and y=320,
    both inside the keepout.  With the fix, keepout edges expand hanan_y to
    [100,200,300,450], margin grows to 35, and y=65/y=485 clear the keepout.
    """
    fp = buda.Floorplan()
    fp.add_block("L", 100, 200, 200, 300)
    fp.add_block("R", 700, 200, 800, 300)
    fp.add_keepout_zone(300, 100, 600, 450, [4, 5])  # blocks M4 and M5

    gen = buda.TopologyGenerator(fp)
    gen.set_layer_ids(4, 5)
    gen.set_all_h_layers([4])
    gen.set_all_v_layers([5])
    gen.set_double_detour(True)

    cands = gen.generate_candidates("L", ["R"])
    u_vhv = [c for c in cands if "U_VHV" in c.type]
    assert len(u_vhv) > 0, (
        f"Expected at least one U_VHV candidate detour above/below keepout, "
        f"got types: {[c.type for c in cands]}"
    )


# ── Collinear MST-relay merge (big.buda bundle 13 regression) ─────────────────

def test_collinear_mst_relay_merges_into_passthrough():
    """A pass-through relay entered by two collinear bus stubs (opposite faces,
    SAME row/column) is wired by a SINGLE straight pass-through wire, not a jog.

    Regression for the big.buda / tc3a_flat bundle-13 DetailedNUTS strand: when an
    MST branch relays through a mid block by landing on its two opposite faces at
    the same row, the two stubs are collinear and cannot be joined by a
    perpendicular connector (ConnTopology infers only perpendicular junctions).
    complete_relay_junctions used to bridge them with a trivial 2-unit jog, which
    the planner offloaded to a zero-signal-track layer -> 32 bits unplaced at
    DetailedNUTS.  The fix MERGES the two collinear stubs into one wire spanning
    the block face-to-face (pass-through coverage, no busterm tap).

    Geometry mirrors bundle 13 (blk_05 -> [blk_16, blk_13, blk_00, blk_09]): the
    mid receiver blk_13 sits on the relay row; its stubs' far ends are pure
    junctions (the trunk on one side, a bend to blk_09 on the other), so the merge
    guard (both far endpoints off any block face) fires.
    """
    fp = buda.Floorplan()
    fp.add_block("blk_05", 3300, 3000, 3520, 7900)   # source (tall -> V trunk)
    fp.add_block("blk_16", 2100, 3030, 2290, 3230)   # left receiver
    fp.add_block("blk_13", 5850, 3510, 7050, 3710)   # MID relay (collinear row)
    fp.add_block("blk_00", 3300, 7660, 3520, 7860)   # up the trunk
    fp.add_block("blk_09", 7700, 5420, 7900, 5620)   # up-right (bend target)

    gen = buda.TopologyGenerator(fp)
    gen.set_layer_ids(6, 7)
    cands = gen.generate_candidates("blk_05", ["blk_16", "blk_13", "blk_00", "blk_09"])

    def spans_blk13(c):
        # A single H segment crossing blk_13 from face (x=5850) to face (x=7050)
        # at a row inside blk_13 -> the merged pass-through wire.
        for s in c.segments:
            if s.start.y != s.end.y:
                continue
            lo, hi = min(s.start.x, s.end.x), max(s.start.x, s.end.x)
            if lo <= 5850 and hi >= 7050 and 3510 <= s.start.y <= 3710:
                return True
        return False

    def has_trivial_jog(c):
        # The old fallback's 2-unit bridge legs.  A legitimate stub is never this
        # short (min-stub is far larger), so any (0, 6]-length segment is a jog.
        return any(0 < abs(s.end.x - s.start.x) + abs(s.end.y - s.start.y) <= 6
                   for s in c.segments)

    merged = [c for c in cands if "MST" in c.type and spans_blk13(c)]
    assert merged, (
        "expected at least one MST candidate to span blk_13 face-to-face with a "
        f"single pass-through wire (the collinear-relay merge); got types "
        f"{sorted({c.type for c in cands if 'MST' in c.type})}"
    )
    for c in merged:
        assert not has_trivial_jog(c), (
            f"{c.type} spans blk_13 but still carries a trivial jog "
            "(collinear stubs were not merged)"
        )
        ct = buda.ConnTopology()
        ct.build(c, fp)
        res = buda.check_topo(ct, c, fp, 0)
        assert not res.violations, (
            f"{c.type} merged relay fails check_topo: "
            f"{[str(v.kind).split('.')[-1] for v in res.violations]}"
        )


def test_feedthru_relay_not_merged_by_collinear_pass():
    """The collinear-relay merge must NOT collapse a declared feedthru split.

    A feedthru block keeps its two BUSTERM landings (the block bridges the split
    via its own routing); merging them into a straight crossing would silently
    turn an opt-in feedthru into a pass-through.  Complements the merge test above
    by pinning the feedthru guard.
    """
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("mid", 300, 0, 400, 100)
    fp.add_block("B", 600, 0, 700, 100)
    fp.add_block("C", 300, 400, 400, 500)   # off-spine -> forces a +MST hybrid
    fp.set_feedthru(True)                    # global opt-in

    gen = buda.TopologyGenerator(fp)
    gen.set_layer_ids(4, 5)
    cands = gen.generate_candidates("A", ["mid", "B", "C"])

    ft = [c for c in cands if "TRUNK_H" in c.type and "mid" in c.feedthru_blocks]
    assert ft, "expected a TRUNK_H feedthru candidate through 'mid'"
    for c in ft:
        # A declared feedthru must NOT be crossed by one continuous wire spanning
        # both faces (x=300..400) at the trunk row -- the split must survive.
        crossing = [s for s in c.segments
                    if s.start.y == s.end.y
                    and min(s.start.x, s.end.x) <= 300
                    and max(s.start.x, s.end.x) >= 400
                    and 0 <= s.start.y <= 100]
        assert not crossing, (
            f"feedthru block 'mid' was collapsed into a straight crossing by "
            f"{c.type} (merge should skip declared feedthru blocks)"
        )
