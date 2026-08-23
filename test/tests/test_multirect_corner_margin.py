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

"""Corner margins on multi-rect blocks (teg_multirect_status.md open 9).

`set_block_corner_margin` / the global margin used to shrink only the UNION
`Busterm::bbox`; the individual `rects` were never inset, so a declared
per-block margin silently did nothing on exactly the faces multi-rect routing
lands on (best_rect faces, stubs, taps all read the unshrunk rects).

The fix insets EACH rect the same way the union bbox is inset, at Busterm
construction (`shrink_rects`, topology.h) — `Rect::shrink` carries the
per-axis guard, applied per rect, so a rect too thin for the margin keeps
that axis at full extent while its siblings still inset.  Generation, the
per-bundle Hanan grid, `best_rect` faces and taps all see the inset rects,
and `derive_slide_ranges`' tap→rect attribution matches the inset face
spelling too (zero margin = identity, so margin-free designs are
byte-identical — guarded by the fast tier and the measured no-change on
flow/lShape1 / flow/teg_over_audit, the checked-in multi-rect flows that
declare no margin).
"""
import buda

H, V = 6, 5


def _gen(margin, rects=((0, 200, 100, 300), (300, 200, 400, 300))):
    """S below a two-rect TEG block T; candidates S→T (npin path — the 2-pin
    family is bypassed for multi-rect endpoints)."""
    fp = buda.Floorplan()
    fp.add_block_rects("T", list(rects))
    fp.add_block("S", 150, 0, 250, 50)
    if margin:
        fp.set_global_corner_margin(*margin)
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(H, V)
    return fp, g.generate_candidates("S", ["T"])


def _taps_on_T(cands):
    """(endpoint, carried Busterm) for every tap on T across all candidates."""
    out = []
    for t in cands:
        for si, eps in t.seg_busterms.items():
            for k, bt in enumerate(eps):
                if bt is not None and bt.block_name == "T":
                    seg = t.segments[si]
                    out.append((seg.start if k == 0 else seg.end, bt))
    return out


def test_margin_insets_each_rect_and_taps_land_on_inset_faces():
    """With corner_margin dx 20 dy 10, every Busterm carries per-rect inset
    rects and no tap lands on a physical (uninset) horizontal face of T."""
    _, cands = _gen((20, 10))
    taps = _taps_on_T(cands)
    assert taps, "no taps generated on T"
    for _, bt in taps:
        assert [(r.x1, r.y1, r.x2, r.y2) for r in bt.rects] == \
            [(20, 210, 80, 290), (320, 210, 380, 290)], \
            "each rect must be inset by (dx=20, dy=10) like the union bbox"
    ys = {p.y for p, _ in taps}
    assert 200 not in ys and 300 not in ys, (
        f"tap on a physical face {sorted(ys)} — the margin is inert on rects "
        f"again (open 9 regressed)")
    # The bottom-face landings sit on the INSET faces.
    assert 210 in ys, f"no tap on the inset bottom face: {sorted(ys)}"


def test_no_margin_taps_physical_faces_unchanged():
    """Margin-free multi-rect generation is untouched: taps on the physical
    rect faces, rects carried unshrunk."""
    _, cands = _gen(None)
    taps = _taps_on_T(cands)
    assert taps
    for _, bt in taps:
        assert [(r.x1, r.y1, r.x2, r.y2) for r in bt.rects] == \
            [(0, 200, 100, 300), (300, 200, 400, 300)]
    ys = {p.y for p, _ in taps}
    assert 200 in ys, f"physical bottom-face tap missing: {sorted(ys)}"


def _annotate_stub(fp, y):
    """Hand-built USER stub rising from open ground to (50, y) — the shape
    TopoEdit or a restored checkpoint hands annotate_topology."""
    t = buda.Topology()
    t.type = "USER"
    s = buda.Segment()
    s.start = buda.Point(50, y)
    s.end = buda.Point(50, 50)
    t.segments = [s]
    buda.annotate_topology(t, fp)
    eps = t.seg_busterms.get(0)
    return None if eps is None else (eps[0].block_name if eps[0] else None)


def _margined_fp():
    fp = buda.Floorplan()
    fp.add_block_rects("T", [(0, 200, 100, 300), (300, 200, 400, 300)])
    fp.add_block("S", 150, 0, 250, 50)
    fp.set_global_corner_margin(20, 10)
    return fp


def test_annotate_accepts_physical_face_spelling():
    """PR #835 P2: annotate_endpoints' multi-rect branch used to check ONLY
    the inset rects — unlike its single-rect branch, which checks BOTH
    orig_bbox and the inset bbox — so a hand-built (TopoEdit/USER) or
    restored endpoint landing on the PHYSICAL face of a margined multi-rect
    block lost its tap and the block read open.  Both spellings must tap."""
    fp = _margined_fp()
    assert _annotate_stub(fp, 200) == "T", \
        "physical-face endpoint lost its tap (P2 regressed)"


def test_annotate_accepts_inset_face_spelling():
    fp = _margined_fp()
    assert _annotate_stub(fp, 210) == "T"
    # A coordinate on neither spelling still does not tap.
    assert _annotate_stub(fp, 195) is None


def test_annotate_zero_margin_identity():
    """No margin: orig_rects stays empty (the guard in the construction
    sites) and the physical face taps exactly as before open 9."""
    fp = buda.Floorplan()
    fp.add_block_rects("T", [(0, 200, 100, 300), (300, 200, 400, 300)])
    fp.add_block("S", 150, 0, 250, 50)
    assert _annotate_stub(fp, 200) == "T"
    assert _annotate_stub(fp, 195) is None
    # And the carried Busterms advertise no second spelling.
    _, cands = _gen(None)
    for _, bt in _taps_on_T(cands):
        assert len(bt.orig_rects) == 0


def test_per_axis_guard_applies_per_rect():
    """A rect too thin for the margin keeps THAT axis at full extent (the
    same 2*margin >= face_extent guard the union shrink has), per rect: the
    thin rect keeps its y extent while the tall sibling insets both axes."""
    _, cands = _gen((20, 10),
                    rects=((0, 200, 100, 300),      # tall: insets both axes
                           (300, 200, 400, 215)))   # 15 tall: 2*10 >= 15
    taps = _taps_on_T(cands)
    assert taps
    for _, bt in taps:
        got = [(r.x1, r.y1, r.x2, r.y2) for r in bt.rects]
        assert got == [(20, 210, 80, 290),          # dx and dy applied
                       (320, 200, 380, 215)], got   # dx applied, dy guarded
