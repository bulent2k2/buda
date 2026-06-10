"""
Layer-direction validity checks (ViolationKind.LAYER_DIR) and the
select_topology re-plan that prevents the violation from arising.

Regression: `select_topology` after `run_planner` changed only the selected
topology index, leaving seg_layers from the PREVIOUS topology.  NUTS then
routed e.g. a vertical stub on an H layer at coordinates taken from the wrong
axis — and check_nuts/check_dnuts passed coincidentally because they only
compared coordinates, never layer direction.  (Seen in
flow/nuts_relax_range_reg.buda: left V stub of the pinned U_VHV on M4.)
"""
import buda


def _make_v_topo_and_ct(fp):
    """Single vertical segment between the two blocks' facing edges."""
    seg = buda.Segment()
    seg.start = buda.Point(50, 100)
    seg.end   = buda.Point(50, 200)
    seg.layer_hint = 5
    topo = buda.Topology()
    topo.type = "TEST_V"
    topo.segments = [seg]
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return topo, ct


def _setup():
    fp = buda.Floorplan()
    fp.add_block("bot", 0, 0,   100, 100)
    fp.add_block("top", 0, 200, 100, 300)
    ls = buda.LayerStack()
    ls.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(5, "M5", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)
    return fp, ls


def _layer_dir_violations(res):
    return [v for v in res.violations if v.kind == buda.ViolationKind.LAYER_DIR]


# ---------------------------------------------------------------------------
# check_nuts
# ---------------------------------------------------------------------------

def _track_segment(layer):
    ts = buda.TrackSegment()
    ts.bundle_id = 1
    ts.seg_idx = 0
    ts.layer = layer
    ts.horiz = False
    ts.span_lo, ts.span_hi = 100, 200
    ts.track_position = 50.0
    ts.placed = True
    return ts


def test_check_nuts_flags_v_segment_on_h_layer():
    fp, ls = _setup()
    topo, ct = _make_v_topo_and_ct(fp)
    nuts = buda.NUTSResult()
    nuts.segments = [_track_segment(layer=4)]   # V segment on H layer
    res = buda.check_nuts(ct, nuts, topo, fp, ls, 1)
    bad = _layer_dir_violations(res)
    assert len(bad) == 1, f"expected one LAYER_DIR violation, got {[v.message for v in res.violations]}"
    assert bad[0].seg_idx == 0
    assert "unbuildable" in bad[0].message


def test_check_nuts_accepts_v_segment_on_v_layer():
    fp, ls = _setup()
    topo, ct = _make_v_topo_and_ct(fp)
    nuts = buda.NUTSResult()
    nuts.segments = [_track_segment(layer=5)]
    res = buda.check_nuts(ct, nuts, topo, fp, ls, 1)
    assert not _layer_dir_violations(res)


# ---------------------------------------------------------------------------
# check_dnuts
# ---------------------------------------------------------------------------

def _net_segment(layer):
    ns = buda.NetSegment()
    ns.bundle_id = 1
    ns.seg_idx = 0
    ns.bit_index = 0
    ns.layer = layer
    ns.span_lo, ns.span_hi = 100, 200
    ns.track_position = 50.0
    return ns


def test_check_dnuts_flags_v_bits_on_h_layer():
    fp, ls = _setup()
    topo, ct = _make_v_topo_and_ct(fp)
    dnuts = buda.DetailedNUTSResult()
    dnuts.net_segments = [_net_segment(layer=4)]   # V bit wire on H layer
    res = buda.check_dnuts(ct, dnuts, topo, fp, ls, 1, 1)
    bad = _layer_dir_violations(res)
    assert len(bad) == 1, f"expected one LAYER_DIR violation, got {[v.message for v in res.violations]}"
    assert "unbuildable" in bad[0].message


def test_check_dnuts_accepts_v_bits_on_v_layer():
    fp, ls = _setup()
    topo, ct = _make_v_topo_and_ct(fp)
    dnuts = buda.DetailedNUTSResult()
    dnuts.net_segments = [_net_segment(layer=5)]
    res = buda.check_dnuts(ct, dnuts, topo, fp, ls, 1, 1)
    assert not _layer_dir_violations(res)


# ---------------------------------------------------------------------------
# select_topology after run_planner must re-plan layer assignments
# ---------------------------------------------------------------------------

def test_select_topology_after_run_planner_replans_layers():
    from buda_cli import BudaSession

    s = BudaSession()
    for line in [
        "def_layer 4 M4 H TOP 0.0",
        "def_layer 5 M5 V TOP 0.0",
        "add_block left  0   0 400 500",
        "add_block right 450 0 850 500",
        "add_bus a[6] left right",
        "run_bundler",
        "generate_topologies",
        "run_planner",
    ]:
        s.do_command(line)

    w = s.bundles[0]
    # Pick any candidate with more segments than the planner's choice.
    planner_idx = w.selected_topology_index
    new_idx = next(i for i, c in enumerate(w.candidates)
                   if i != planner_idx and len(c.segments) > 1)
    s.do_command(f"select_topology 1 {new_idx + 1}")

    assert w.topology_pinned, "select_topology must pin the choice"
    assert w.selected_topology_index == new_idx, "pinned index must survive the re-plan"
    topo = w.candidates[new_idx]
    assert len(w.seg_layers) == len(topo.segments), (
        f"seg_layers must match the new topology's segment list; "
        f"got {list(w.seg_layers)} for {len(topo.segments)} segments"
    )
    for seg, lid in zip(topo.segments, w.seg_layers):
        seg_horiz = seg.start.y == seg.end.y
        lay_horiz = s.layers.get_layer_dir(lid) == buda.LayerDir.HORIZONTAL
        assert seg_horiz == lay_horiz, (
            f"segment dir and layer M{lid} dir must agree after re-plan"
        )
