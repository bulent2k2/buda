"""Connectivity checks (check_topo / check_nuts / check_dnuts) on realistic
hbundle-style trunk-and-stub topologies.

These exercise the same verification that `check_connectivity {topo,nuts,dnuts}`
runs in the CLI over HBundle candidates.  The geometry comes from the real
TopologyGenerator (the code path hbundles use), so the inferred connectivity —
which segment connects to which segment and which busterm — is exactly what the
planner/NUTS will see.

Each stage gets:
  * a structural assertion (segs connect to the segs and busterms we expect),
  * a clean positive case (no opens), and
  * negative cases that must surface the right ViolationKind.

Connectivity is independent of bit pitch / overlap packing, so the NUTS and
DetailedNUTS fixtures place every bit at the segment's nominal track position;
that isolates continuity / face / coverage / unplaced from track-packing noise.
"""
import buda


LAYER_H, LAYER_V = 4, 5   # M4 horizontal, M5 vertical


# ── fixtures ───────────────────────────────────────────────────────────────────

def _layers():
    ls = buda.LayerStack()
    ls.add_layer(LAYER_H, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(LAYER_V, "M5", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)
    return ls


def _seg(x1, y1, x2, y2, hint):
    s = buda.Segment()
    s.start = buda.Point(x1, y1)
    s.end   = buda.Point(x2, y2)
    s.layer_hint = hint
    return s


def _gen_candidate(fp, src, dsts, type_prefix):
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(LAYER_H, LAYER_V)
    cands = g.generate_candidates(src, dsts)
    topo = next((c for c in cands if c.type.startswith(type_prefix)), None)
    assert topo is not None, (
        f"no {type_prefix!r} candidate; got {[c.type for c in cands]}"
    )
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return topo, ct


def _z_setup():
    """Point-to-point Z_HVH: A --H stub-- (V trunk) --H stub-- B.

    seg0 H stub anchored to A, seg1 V trunk, seg2 H stub anchored to B.
    """
    fp = buda.Floorplan()
    fp.add_block("A", 0,   0,   100, 100)
    fp.add_block("B", 400, 200, 500, 300)
    topo, ct = _gen_candidate(fp, "A", ["B"], "Z_HVH")
    return fp, topo, ct


def _multicast_setup():
    """Multicast TRUNK_H: SRC --(H trunk)-- branches to D1 and D2.

    seg0 H trunk anchored to SRC, seg1/seg2 V stubs to D1/D2.
    """
    fp = buda.Floorplan()
    fp.add_block("SRC", 0,   100, 100, 200)
    fp.add_block("D1",  400, 0,   500, 100)
    fp.add_block("D2",  400, 250, 500, 350)
    topo, ct = _gen_candidate(fp, "SRC", ["D1", "D2"], "TRUNK_H")
    return fp, topo, ct


def _busterms(cs):
    return {c.block_name for c in cs.conns if c.kind == buda.SegConnKind.BUSTERM}


def _seg_conns(cs):
    return {c.seg_idx for c in cs.conns if c.kind == buda.SegConnKind.SEG}


def _nominal_nuts(ct, bundle_id=1):
    """A consistent NUTSResult: each segment placed at its nominal position."""
    nuts = buda.NUTSResult()
    out = []
    for i, cs in enumerate(ct.segs()):
        ts = buda.TrackSegment()
        ts.bundle_id = bundle_id
        ts.seg_idx = i
        ts.horiz = cs.horiz
        ts.layer = LAYER_H if cs.horiz else LAYER_V
        ts.span_lo, ts.span_hi = cs.along_lo, cs.along_hi
        ts.track_position = float(cs.perp_pos)
        ts.width = 1.0
        ts.placed = True
        out.append(ts)
    nuts.segments = out
    return nuts


def _nominal_dnuts(ct, num_bits, bundle_id=1):
    """A consistent DetailedNUTSResult: every (seg,bit) placed at the segment's
    nominal track position (bit pitch is irrelevant to connectivity)."""
    dnuts = buda.DetailedNUTSResult()
    out = []
    for i, cs in enumerate(ct.segs()):
        for b in range(num_bits):
            ns = buda.NetSegment()
            ns.bundle_id = bundle_id
            ns.seg_idx = i
            ns.bit_index = b
            ns.layer = LAYER_H if cs.horiz else LAYER_V
            ns.span_lo, ns.span_hi = cs.along_lo, cs.along_hi
            ns.track_position = float(cs.perp_pos)
            ns.width = 1.0
            out.append(ns)
    dnuts.net_segments = out
    return dnuts


def _kinds(res, kind):
    return [v for v in res.violations if v.kind == kind]


# ── connection structure (the heart of the request) ─────────────────────────────

def test_zhvh_structure_segs_connect_to_expected_segs_and_busterms():
    _, _, ct = _z_setup()
    segs = ct.segs()
    assert len(segs) == 3

    # seg0: horizontal stub anchored to A only, joins the trunk (seg1).
    assert segs[0].horiz is True
    assert _busterms(segs[0]) == {"A"}
    assert _seg_conns(segs[0]) == {1}

    # seg1: vertical trunk — no busterm of its own, bridges both stubs.
    assert segs[1].horiz is False
    assert _busterms(segs[1]) == set()
    assert _seg_conns(segs[1]) == {0, 2}

    # seg2: horizontal stub anchored to B only, joins the trunk (seg1).
    assert segs[2].horiz is True
    assert _busterms(segs[2]) == {"B"}
    assert _seg_conns(segs[2]) == {1}


def test_multicast_trunk_branches_to_both_receivers():
    _, _, ct = _multicast_setup()
    segs = ct.segs()
    assert len(segs) == 3

    # seg0: H trunk anchored to the source, branching to both receiver stubs.
    assert _busterms(segs[0]) == {"SRC"}
    assert _seg_conns(segs[0]) == {1, 2}

    # seg1, seg2: receiver stubs, each anchored to one destination and the trunk.
    assert _busterms(segs[1]) == {"D1"}
    assert _seg_conns(segs[1]) == {0}
    assert _busterms(segs[2]) == {"D2"}
    assert _seg_conns(segs[2]) == {0}

    # Every connected block is reachable, and the reciprocal trunk→stub edges
    # match the stub→trunk edges (no dangling half-connections).
    assert _busterms(segs[0]) | _busterms(segs[1]) | _busterms(segs[2]) == {
        "SRC", "D1", "D2"}


# ── topo stage ──────────────────────────────────────────────────────────────────

def test_check_topo_clean_on_zhvh():
    fp, topo, ct = _z_setup()
    res = buda.check_topo(ct, topo, fp, 1)
    assert res.ok(), [v.message for v in res.violations]


def test_check_topo_clean_on_multicast():
    fp, topo, ct = _multicast_setup()
    res = buda.check_topo(ct, topo, fp, 1)
    assert res.ok(), [v.message for v in res.violations]


def test_check_topo_reports_block_with_no_connection():
    # A block listed as connected but touched by no segment (no busterm, no
    # pass-through) must be flagged BUSTERM_OPEN.
    fp, topo, ct = _z_setup()
    fp.add_block("C", 900, 900, 1000, 1000)
    topo.connected_block_names = list(topo.connected_block_names) + ["C"]
    res = buda.check_topo(ct, topo, fp, 1)
    opens = _kinds(res, buda.ViolationKind.BUSTERM_OPEN)
    assert [v.block_name for v in opens] == ["C"], [v.message for v in res.violations]


def test_check_topo_reports_busterm_off_block_face():
    # An annotated busterm whose segment sits off the block's face is unbuildable.
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    seg = _seg(0, 500, 200, 500, LAYER_H)   # H wire far above A's face
    topo = buda.Topology()
    topo.type = "OFFFACE"
    topo.segments = [seg]
    topo.connected_block_names = ["A"]
    bt = buda.Busterm()
    bt.block_name = "A"
    bt.bbox = buda.Rect(0, 0, 0, 100)
    topo.seg_busterms = {0: (bt, None)}     # left endpoint annotated as busterm to A
    ct = buda.ConnTopology()
    ct.build(topo, fp)

    res = buda.check_topo(ct, topo, fp, 1)
    face = _kinds(res, buda.ViolationKind.BUSTERM_FACE)
    assert any(v.block_name == "A" and v.seg_idx == 0 for v in face), (
        [v.message for v in res.violations])


# ── nuts stage ──────────────────────────────────────────────────────────────────

def test_check_nuts_clean_on_zhvh():
    fp, topo, ct = _z_setup()
    res = buda.check_nuts(ct, _nominal_nuts(ct), topo, fp, _layers(), 1)
    assert res.ok(), [v.message for v in res.violations]


def test_check_nuts_clean_on_multicast():
    fp, topo, ct = _multicast_setup()
    res = buda.check_nuts(ct, _nominal_nuts(ct), topo, fp, _layers(), 1)
    assert res.ok(), [v.message for v in res.violations]


def test_check_nuts_reports_seg_open_when_trunk_slides_away():
    # Move the trunk far along its perpendicular axis; both stubs lose contact.
    fp, topo, ct = _z_setup()
    nuts = _nominal_nuts(ct)
    trunk = next(ts for ts in nuts.segments if ts.seg_idx == 1)
    trunk.track_position = 5000.0
    res = buda.check_nuts(ct, nuts, topo, fp, _layers(), 1)
    opens = _kinds(res, buda.ViolationKind.SEG_OPEN)
    pairs = {tuple(sorted((v.seg_idx, v.seg_idx2))) for v in opens}
    assert (0, 1) in pairs and (1, 2) in pairs, [v.message for v in res.violations]


def test_check_nuts_reports_layer_dir_for_trunk_on_wrong_layer():
    # The vertical trunk routed on an H layer is unbuildable.
    fp, topo, ct = _z_setup()
    nuts = _nominal_nuts(ct)
    trunk = next(ts for ts in nuts.segments if ts.seg_idx == 1)
    trunk.layer = LAYER_H
    res = buda.check_nuts(ct, nuts, topo, fp, _layers(), 1)
    bad = _kinds(res, buda.ViolationKind.LAYER_DIR)
    assert [v.seg_idx for v in bad] == [1], [v.message for v in res.violations]


def test_check_nuts_reports_busterm_face_when_stub_off_face():
    # Slide stub seg2 off B's face perpendicular extent.
    fp, topo, ct = _z_setup()
    nuts = _nominal_nuts(ct)
    stub = next(ts for ts in nuts.segments if ts.seg_idx == 2)
    stub.track_position = 9000.0
    res = buda.check_nuts(ct, nuts, topo, fp, _layers(), 1)
    face = _kinds(res, buda.ViolationKind.BUSTERM_FACE)
    assert any(v.block_name == "B" and v.seg_idx == 2 for v in face), (
        [v.message for v in res.violations])


# ── dnuts stage ─────────────────────────────────────────────────────────────────

NUM_BITS = 4


def test_check_dnuts_clean_on_zhvh():
    fp, topo, ct = _z_setup()
    res = buda.check_dnuts(ct, _nominal_dnuts(ct, NUM_BITS), topo, fp,
                           _layers(), 1, NUM_BITS)
    assert res.ok(), [v.message for v in res.violations]


def test_check_dnuts_clean_on_multicast():
    fp, topo, ct = _multicast_setup()
    res = buda.check_dnuts(ct, _nominal_dnuts(ct, NUM_BITS), topo, fp,
                           _layers(), 1, NUM_BITS)
    assert res.ok(), [v.message for v in res.violations]


def test_check_dnuts_reports_unplaced_bit():
    # Drop one (seg, bit): DetailedNUTS could not place that bit-wire.
    fp, topo, ct = _z_setup()
    dnuts = _nominal_dnuts(ct, NUM_BITS)
    dnuts.net_segments = [ns for ns in dnuts.net_segments
                          if not (ns.seg_idx == 2 and ns.bit_index == 3)]
    res = buda.check_dnuts(ct, dnuts, topo, fp, _layers(), 1, NUM_BITS)
    unplaced = _kinds(res, buda.ViolationKind.UNPLACED)
    assert [(v.seg_idx, v.bit_index) for v in unplaced] == [(2, 3)], (
        [v.message for v in res.violations])


def test_check_dnuts_reports_seg_open_for_single_bit():
    # Break continuity for exactly one bit of the trunk; only that bit opens.
    fp, topo, ct = _z_setup()
    dnuts = _nominal_dnuts(ct, NUM_BITS)
    bad_bit = 2
    for ns in dnuts.net_segments:
        if ns.seg_idx == 1 and ns.bit_index == bad_bit:
            ns.track_position = 5000.0   # trunk bit slid off both stubs' spans
    res = buda.check_dnuts(ct, dnuts, topo, fp, _layers(), 1, NUM_BITS)
    opens = _kinds(res, buda.ViolationKind.SEG_OPEN)
    assert opens, [v.message for v in res.violations]
    assert all(v.bit_index == bad_bit for v in opens), [v.message for v in opens]
    pairs = {tuple(sorted((v.seg_idx, v.seg_idx2))) for v in opens}
    assert (0, 1) in pairs and (1, 2) in pairs


def test_check_dnuts_reports_busterm_face_for_single_bit():
    # Slide one bit of stub seg2 off B's face; only that bit is flagged.
    fp, topo, ct = _z_setup()
    dnuts = _nominal_dnuts(ct, NUM_BITS)
    bad_bit = 1
    for ns in dnuts.net_segments:
        if ns.seg_idx == 2 and ns.bit_index == bad_bit:
            ns.track_position = 9000.0
    res = buda.check_dnuts(ct, dnuts, topo, fp, _layers(), 1, NUM_BITS)
    face = _kinds(res, buda.ViolationKind.BUSTERM_FACE)
    assert [(v.seg_idx, v.bit_index, v.block_name) for v in face] == [
        (2, bad_bit, "B")], [v.message for v in res.violations]
