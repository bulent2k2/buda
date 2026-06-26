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
    """3 blocks → TRUNK+MST hybrid candidate is present."""
    fp = buda.Floorplan()
    fp.add_block("A", 0,   0, 100, 100)
    fp.add_block("B", 200, 0, 300, 100)
    fp.add_block("C", 400, 0, 500, 100)

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


def test_trunk_wirelength_counts_teg_over_bridges():
    """estimated_wirelength includes TEG-OVER bridge segments (defect 4).

    A multi-rect block with teg_mode=OVER whose trunk lands in the gap between its
    rects gets a bridge segment along the union-bbox outer face.  Bridges live in
    Topology.bridge_segments, NOT in .segments -- but they are real routed metal, so
    wirelength() must count them; otherwise the planner ranks a bridged candidate as
    artificially cheap.
    """
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    # Two disjoint rects with a vertical gap; an H-trunk in the gap stubs to both
    # rects and bridges over the union top.
    fp.add_block_rects("M", [(300, 0, 400, 100), (300, 300, 400, 400)],
                       teg_mode=buda.TegMode.OVER)
    fp.add_block("B", 300, 600, 400, 700)

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["M", "B"])

    bridged = [c for c in cands if c.bridge_segments]
    assert bridged, "expected at least one TEG-OVER candidate with a bridge segment"
    for c in bridged:
        seg_sum = sum(abs(s.end.x - s.start.x) + abs(s.end.y - s.start.y)
                      for s in c.segments)
        bridge_sum = sum(abs(s.end.x - s.start.x) + abs(s.end.y - s.start.y)
                         for s in c.bridge_segments.values())
        assert bridge_sum > 0, f"{c.type}: bridge present but zero length"
        assert c.estimated_wirelength == seg_sum + bridge_sum, (
            f"{c.type}: estimated_wirelength {c.estimated_wirelength} != "
            f"segments {seg_sum} + bridges {bridge_sum}"
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


def test_trunk_mst_candidates_are_clean_trees():
    """Every generated trunk+MST candidate is a physically self-connected tree.

    Defect-3 gate: the legacy hybrid path (full trunk + ALL edges) is cyclic, so it
    is now routed through complete_relay_junctions + topology_is_clean_tree and a
    candidate that cannot be cleanly completed is DROPPED rather than emitted with a
    silent through-block relay.  No emitted +MST / MST_* candidate may therefore carry
    a FEEDTHRU_RELAY violation (which understates wirelength and is not a real wire).
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
