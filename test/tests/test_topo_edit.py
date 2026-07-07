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

"""Phase E3 — transactional TopoEdit operations
(docs/internal/topo_conn_unification.md §8/E3).

Every operation is mutate → annotation maintenance → cached re-analysis →
EditVerdict, so a hand edit can never silently corrupt annotations.  The
expert flow exercised end-to-end here is the one specified for E3: pick an
axis, pick a Hanan grid line, add a (default full-span) trunk, add stubs to
blocks, override spans, and connect/disconnect perpendicular segments.
Undo = snapshot/restore of the (value-typed) Topology, verified by topo_uid.
"""
import buda

H_LAYER, V_LAYER = 4, 5


def _fp():
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("B", 400, 0, 500, 100)
    fp.add_block("C", 200, 400, 300, 500)
    return fp


def _hanan(fp):
    return fp.get_hanan_grid()


def test_build_topology_from_scratch_with_edits():
    """The full expert loop: H trunk on a Hanan line between the blocks,
    full span by default, then a stub to each block -> a covered, clean tree."""
    fp = _fp()
    topo = buda.Topology()
    topo.type = "USER"
    topo.connected_block_names = ["A", "B", "C"]

    xs, ys = _hanan(fp)
    y = 200                                   # a channel row between the blocks
    v = buda.edit_add_trunk(topo, fp, horiz=True, perp_pos=y, layer=H_LAYER)
    assert v.applied and v.seg_idx == 0
    seg = topo.segments[0]
    # Default full span = the Hanan extent on the trunk's axis.
    assert (seg.start.x, seg.end.x) == (min(xs), max(xs))
    assert seg.start.y == seg.end.y == y
    # Blocks unreached yet: the verdict must say so rather than stay silent.
    assert not v.ok()
    kinds = {str(viol.kind).split(".")[-1] for viol in v.conn.violations}
    assert "BUSTERM_OPEN" in kinds

    for block in ("A", "B", "C"):
        v = buda.edit_add_stub(topo, fp, block, to_seg=0, layer=V_LAYER)
        assert v.applied, v.note
    assert v.ok(), (v.note, [str(x.kind) for x in v.conn.violations], v.pinched)
    assert len(topo.segments) == 4
    # Stubs carry real taps; the trunk carries junctions to all three.
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    trunk_conns = ct.segs()[0].conns
    assert sum(1 for c in trunk_conns if c.kind == buda.SegConnKind.SEG) == 3


def test_add_stub_rejections():
    fp = _fp()
    topo = buda.Topology()
    topo.connected_block_names = ["A"]
    assert buda.edit_add_trunk(topo, fp, True, 200, layer=H_LAYER).applied
    v = buda.edit_add_stub(topo, fp, "NOPE", 0, V_LAYER)
    assert not v.applied and "unknown block" in v.note
    v = buda.edit_add_stub(topo, fp, "A", 7, V_LAYER)
    assert not v.applied and "out of range" in v.note
    # A trunk crossing the block needs no stub.
    v2 = buda.edit_add_trunk(topo, fp, True, 50, layer=H_LAYER)
    assert v2.applied
    v = buda.edit_add_stub(topo, fp, "A", v2.seg_idx, V_LAYER)
    assert not v.applied and "pass-through" in v.note


def test_set_span_breaks_and_verdict_reports():
    """Span override is allowed to break connectivity — the verdict reports it."""
    fp = _fp()
    topo = buda.Topology()
    topo.connected_block_names = ["A", "B"]
    buda.edit_add_trunk(topo, fp, True, 200, layer=H_LAYER)
    buda.edit_add_stub(topo, fp, "A", 0, V_LAYER)
    buda.edit_add_stub(topo, fp, "B", 0, V_LAYER)
    ok = buda.edit_set_span(topo, fp, 0, 0, 500)
    assert ok.applied and ok.ok(), ok.note
    assert ok.components == 1
    # Retract the trunk so B's stub junction (at x=450) falls off it: B stays
    # tapped by its stub (no BUSTERM_OPEN) but the wire graph splits — exactly
    # the case check_topo alone cannot see and the verdict's components catch.
    broken = buda.edit_set_span(topo, fp, 0, 0, 120)
    assert broken.applied
    assert broken.components == 2
    assert not broken.ok()


def test_connect_and_disconnect_roundtrip():
    fp = _fp()
    topo = buda.Topology()
    topo.connected_block_names = []
    # Two disjoint perpendicular segments.
    buda.edit_add_trunk(topo, fp, True, 200, 0, 300, H_LAYER)     # H at y=200
    buda.edit_add_trunk(topo, fp, False, 350, 300, 450, V_LAYER)  # V at x=350
    assert topo.seg_conns == {}

    v = buda.edit_connect(topo, fp, 0, 1)
    assert v.applied, v.note
    # i's near endpoint moved to the crossing (350, 200); j extended down to it.
    assert (topo.segments[0].end.x, topo.segments[0].end.y) == (350, 200)
    assert min(topo.segments[1].start.y, topo.segments[1].end.y) == 200
    assert ((0, 1) in topo.seg_conns) or ((1, 0) in topo.seg_conns) \
        or any(k[0] in (0, 1) for k in topo.seg_conns)

    v = buda.edit_disconnect(topo, fp, 0, 1, retract_to=300)
    assert v.applied, v.note
    assert all(1 not in parts for k, parts in topo.seg_conns.items()
               if k[0] == 0), "junction record to partner survived disconnect"

    # Parallel pair is refused.
    buda.edit_add_trunk(topo, fp, True, 260, 0, 300, H_LAYER)
    v = buda.edit_connect(topo, fp, 0, 2)
    assert not v.applied and "parallel" in v.note


def test_connect_refuses_tapped_endpoint():
    fp = _fp()
    topo = buda.Topology()
    topo.connected_block_names = ["A"]
    buda.edit_add_trunk(topo, fp, True, 200, layer=H_LAYER)
    sv = buda.edit_add_stub(topo, fp, "A", 0, V_LAYER)     # tap at block end
    buda.edit_add_trunk(topo, fp, True, 120, 0, 60, H_LAYER)  # near the stub's tap end
    v = buda.edit_connect(topo, fp, sv.seg_idx, 2)
    # The stub's endpoint nearest y=120 is the tap end (y=100): refused.
    assert not v.applied and "busterm tap" in v.note


def test_remove_segment_rekeys_annotations():
    fp = _fp()
    topo = buda.Topology()
    topo.connected_block_names = ["A", "B"]
    buda.edit_add_trunk(topo, fp, True, 200, layer=H_LAYER)   # 0
    buda.edit_add_stub(topo, fp, "A", 0, V_LAYER)             # 1 (tap)
    buda.edit_add_stub(topo, fp, "B", 0, V_LAYER)             # 2 (tap)
    v = buda.edit_remove_segment(topo, fp, 1)
    assert v.applied
    assert len(topo.segments) == 2
    # B's tap re-keyed from 2 -> 1 and still valid.
    assert 1 in topo.seg_busterms
    a, b = topo.seg_busterms[1]
    assert (a or b).block_name == "B"
    # ...and the verdict flags A as now-open.
    kinds = {str(viol.kind).split(".")[-1] for viol in v.conn.violations}
    assert "BUSTERM_OPEN" in kinds


def test_snapshot_restore_is_exact_undo():
    fp = _fp()
    topo = buda.Topology()
    topo.connected_block_names = ["A", "B"]
    buda.edit_add_trunk(topo, fp, True, 200, layer=H_LAYER)
    buda.edit_add_stub(topo, fp, "A", 0, V_LAYER)
    before = buda.offset_topology(topo, 0, 0)          # deep snapshot
    uid_before = buda.topo_uid(topo)

    buda.edit_add_stub(topo, fp, "B", 0, V_LAYER)
    buda.edit_set_span(topo, fp, 0, 0, 250)
    assert buda.topo_uid(topo) != uid_before

    topo = before                                       # restore
    assert buda.topo_uid(topo) == uid_before


def test_failed_op_leaves_topology_untouched():
    fp = _fp()
    topo = buda.Topology()
    topo.connected_block_names = ["A"]
    buda.edit_add_trunk(topo, fp, True, 200, layer=H_LAYER)
    uid = buda.topo_uid(topo)
    assert not buda.edit_add_stub(topo, fp, "NOPE", 0, V_LAYER).applied
    assert not buda.edit_set_span(topo, fp, 0, 500, 100).applied
    assert not buda.edit_remove_segment(topo, fp, 9).applied
    assert buda.topo_uid(topo) == uid
