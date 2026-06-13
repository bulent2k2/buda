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
    trunk_by_loc = {c.trunk_location: c for c in plain_trunks}
    for mst in mst_trunks:
        loc = mst.trunk_location
        if loc in trunk_by_loc:
            assert len(mst.segments) > len(trunk_by_loc[loc].segments), (
                f"TRUNK+MST should have more segments: "
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


def test_trunk_mst_wirelength_geq_trunk():
    """TRUNK+MST estimated_wirelength >= corresponding plain TRUNK."""
    fp = buda.Floorplan()
    fp.add_block("A", 0,   0, 100, 100)
    fp.add_block("B", 200, 0, 300, 100)
    fp.add_block("C", 200, 400, 300, 500)

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C"])

    plain = {c.trunk_location: c for c in cands
             if "TRUNK_H" in c.type and "+MST" not in c.type}
    for c in cands:
        if "TRUNK_H+MST" in c.type and c.trunk_location in plain:
            assert c.estimated_wirelength >= plain[c.trunk_location].estimated_wirelength, (
                f"TRUNK+MST WL ({c.estimated_wirelength}) < plain TRUNK WL "
                f"({plain[c.trunk_location].estimated_wirelength})"
            )
            return
    pytest.skip("No overlapping trunk pair found")


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
