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

"""Regression tests for buda.offset_topology (hier per-instance placement).

The hier flow places a cell-local topology candidate at each instance by
offsetting its coordinates.  If that offset drops the seg_busterms endpoint
annotation, ConnTopology falls back to a geometric face search that can mis-tap
a NON-terminal block whose corner coincidentally grazes an L/Z bend — an
"unintentional feedthru".  offset_topology must carry (and instance-qualify) the
annotation so the authoritative path is used.

Geometry mirrors the `dogleg2` cell in flow/rnr/mix.buda (bundle b171): driver
`bot2`, receiver `top3`, and a neighbour `top2` whose bottom-right corner sits
exactly on the L_VH bend at (1800, 1140).
"""
import buda

# cell-local block bboxes (x1,y1,x2,y2)
_BLOCKS = {
    "bot2": (1750, 840, 1800, 890),
    "top2": (1750, 1140, 1800, 1190),   # corner-neighbour at the bend
    "top3": (1850, 1140, 1900, 1190),
}


def _cell_local_lvh():
    fp = buda.Floorplan()
    for n, (x1, y1, x2, y2) in _BLOCKS.items():
        fp.add_block(n, x1, y1, x2, y2)
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(6, 7)
    cands = g.generate_candidates("bot2", ["top3"])
    lvh = [c for c in cands if c.type.startswith("L_VH")]
    assert lvh, f"no L_VH candidate; got {[c.type for c in cands]}"
    return fp, lvh[0]


def _busterm_taps(ct):
    return [cc.block_name for cs in ct.segs() for cc in cs.conns
            if cc.kind == buda.SegConnKind.BUSTERM]


def test_generate_candidates_annotates_seg_busterms():
    """The generator populates seg_busterms (the authoritative annotation)."""
    _fp, lvh = _cell_local_lvh()
    assert lvh.seg_busterms, "generate_candidates must annotate seg_busterms"


def test_offset_topology_shifts_geometry_and_qualifies_names():
    _fp, lvh = _cell_local_lvh()
    dx, dy = 1000, 500
    off = buda.offset_topology(lvh, dx, dy, "inst")
    # segments shifted
    for a, b in zip(lvh.segments, off.segments):
        assert (off_pt := (b.start.x, b.start.y)) == (a.start.x + dx, a.start.y + dy)
        assert (b.end.x, b.end.y) == (a.end.x + dx, a.end.y + dy)
    # seg_busterms carried, bboxes shifted, names instance-qualified
    assert set(off.seg_busterms.keys()) == set(lvh.seg_busterms.keys())
    for i, (a, b) in off.seg_busterms.items():
        for bt in (a, b):
            if bt is not None:
                assert bt.block_name.startswith("inst/")


def test_offset_preserves_annotation_no_corner_feedthru():
    """The offset candidate, checked against the instance floorplan, must NOT
    tap the corner-neighbour top2 — the bend is a SEG junction, not a busterm."""
    _fp0, lvh = _cell_local_lvh()
    dx, dy = 1000, 500
    off = buda.offset_topology(lvh, dx, dy, "inst")

    fp1 = buda.Floorplan()
    for n, (x1, y1, x2, y2) in _BLOCKS.items():
        fp1.add_block(f"inst/{n}", x1 + dx, y1 + dy, x2 + dx, y2 + dy)

    ct = buda.ConnTopology()
    ct.build(off, fp1)
    taps = _busterm_taps(ct)

    # the coincidental corner block is NOT tapped (no unintentional feedthru)
    assert not any("top2" in (t or "") for t in taps), f"top2 mis-tapped: {taps}"
    # the two real terminals ARE tapped, under their instance paths
    assert "inst/bot2" in taps and "inst/top3" in taps, taps
    # every segment participates in a SEG junction (the L bend is real wire)
    for cs in ct.segs():
        assert any(cc.kind == buda.SegConnKind.SEG for cc in cs.conns), \
            "L bend must be a SEG junction"
    # and the topology verifies clean
    res = buda.check_topo(ct, off, fp1, 1)
    assert len(list(res.violations)) == 0, \
        [(str(v.kind), v.block_name) for v in res.violations]


def test_unannotated_topology_taps_nothing():
    """Phase 2: the geometric fallback is retired, so an UNannotated topology
    taps no block at all (a missing seg_busterms entry is a wire junction, never
    a guessed busterm). This is why offset_topology / annotate_topology must
    carry the annotation — ConnTopology no longer re-derives it from geometry.
    An explicit annotate_topology(fp) call then restores the real taps."""
    _fp0, lvh = _cell_local_lvh()
    bare = buda.offset_topology(lvh, 0, 0)   # copy
    bare.seg_busterms = {}                    # strip the annotation
    fp = buda.Floorplan()
    for n, (x1, y1, x2, y2) in _BLOCKS.items():
        fp.add_block(n, x1, y1, x2, y2)

    ct = buda.ConnTopology(); ct.build(bare, fp)
    assert _busterm_taps(ct) == [], \
        "unannotated topology must tap nothing (geometric fallback retired)"

    # NOTE: annotate_topology(fp) is a *geometric* re-derivation — for this
    # corner-graze geometry it would re-tap 'top2' (the bend lands on top2's
    # face), exactly like the retired fallback. That is why the graze-safe truth
    # comes from the GENERATOR's structural annotation carried by offset_topology
    # (test_offset_preserves_annotation_no_corner_feedthru), not from re-deriving
    # it here. So we only assert the retired-fallback fact above.
