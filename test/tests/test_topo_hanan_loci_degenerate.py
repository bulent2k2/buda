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

"""The hanan_loci degenerate-candidate defect (flip blockers 1-3 in
docs/internal/hanan_loci_flip_audit.md).

A trunk locus sampled ON a block face / abutment line (the `hanan_loci`
knob's extra loci) used to emit three broken families:

1. DISCONNECTED all-tap trees — an aligned column's shared face line makes
   every stub's trunk-side endpoint a busterm TAP (tap wins over junction),
   so the wire graph splits into islands; at the WL floor they sort FIRST
   and get auto-selected.
2. Junction-less-but-connected trees that defeat the fan-in taper.
3. Mis-tapped zero-slide face-riders — an abutment-line spine whose slide
   window only collapses under the block contract's pass-through clamps,
   slipping past the (pre-contract) filter_pinched.

Fixed two ways, both guarded here:
* generation correctness — restore_face_graze_junctions clears the graze
  taps so the real stub<->spine junctions are derived (the candidate becomes
  VALID, not just dropped);
* defense-in-depth — filter_uncovered drops DISCONNECTED candidates (same
  island computation as check_topo) and generate_npin's loci gate drops
  loci-only candidates with a post-contract zero-slide segment.

Default-off pools must be untouched by all of it.
"""
import buda


# Aligned columns sharing face lines (the test_seg_conn_annotation _COLUMN
# fixture): y=160 is A0/B0's shared top-face line, x=500 the B-column's left
# faces — both become trunk loci under the knob.
_COLUMN = {
    "S":  (0, 0, 60, 480),
    "A0": (200, 100, 260, 160), "A1": (200, 300, 260, 360),
    "A2": (200, 500, 260, 560),
    "B0": (500, 100, 560, 160), "B1": (500, 300, 560, 360),
    "B2": (500, 500, 560, 560),
}

# Three abutted blocks (the b34_bus_028 fixture): y=4615 is the blk_15/blk_32
# abutment line AND within blk_00's y-extent — the abutment-line spine's slide
# window collapses to [4615,4615] once the block contract clamps it.
_B34 = {
    "blk_00": (1250, 3780, 2230, 4775),
    "blk_15": (2230, 3365, 3500, 4615),
    "blk_32": (2230, 4615, 3500, 5350),
}


def _gen(blocks, src, dsts, loci):
    fp = buda.Floorplan()
    for n, (x1, y1, x2, y2) in blocks.items():
        fp.add_block(n, x1, y1, x2, y2)
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(6, 7)
    if loci:
        g.set_hanan_loci(True)
    return fp, g.generate_candidates(src, dsts)


def _islands(fp, t):
    ct = buda.ConnTopology()
    ct.build(t, fp)
    return [v for v in buda.check_topo(ct, t, fp, -1).violations
            if v.kind == buda.ViolationKind.DISCONNECTED]


def test_face_line_trunk_pool_has_no_disconnected_candidates():
    """Blocker 1: no candidate in the loci pool splits into islands."""
    fp, cands = _gen(_COLUMN, "S", [k for k in _COLUMN if k != "S"], loci=True)
    assert cands
    broken = [c.type for c in cands if _islands(fp, c)]
    assert broken == [], f"DISCONNECTED candidates shipped: {broken}"


def test_face_line_trunk_is_repaired_not_just_dropped():
    """The face-riding trunk survives WITH its junctions restored: the graze
    taps at the shared face line are cleared, so the stub<->spine junctions
    are derived and the candidate is valid (generation correctness, not just
    the gate)."""
    fp, cands = _gen(_COLUMN, "S", [k for k in _COLUMN if k != "S"], loci=True)
    at_face = [c for c in cands
               if c.type == "TRUNK_H@y160" and len(c.segments) >= 2]
    assert at_face, "expected the face-line trunk TRUNK_H@y160 in the pool"
    for c in at_face:
        assert c.seg_conns, f"{c.type} lost its junction records"
        assert not _islands(fp, c)


def test_abutment_line_spine_zero_slide_is_dropped():
    """Blocker 3: the abutment-line spine (post-contract slide [4615,4615],
    generator taps {blk_15, blk_15}) is excluded by the loci pinch gate —
    an excluded degenerate locus is strictly better than an unplaceable
    candidate that sorts first on wirelength."""
    fp, cands = _gen(_B34, "blk_15", ["blk_32", "blk_00"], loci=True)
    assert cands
    assert not any(c.type == "TRUNK_H@y4615" for c in cands), \
        "the pinched abutment-line spine shipped"
    # And nothing else in the pool carries a post-contract zero-slide segment
    # at a loci-only trunk position (the gate's invariant).
    for c in cands:
        ct = buda.ConnTopology()
        ct.build(c, fp)
        if c.type.startswith("TRUNK_H@y4615") or c.type.startswith("TRUNK_V@x2230"):
            assert all(cs.perp_lo < cs.perp_hi for cs in ct.segs()), c.type


def test_default_off_pools_unaffected():
    """Default-off (no knob): pools contain no loci-only candidates to gate,
    and every default candidate still exists under the knob (the gate only
    ever removes loci-only candidates — the superset property survives)."""
    for blocks, src, dsts in (
        (_COLUMN, "S", [k for k in _COLUMN if k != "S"]),
        (_B34, "blk_15", ["blk_32", "blk_00"]),
    ):
        fp, base = _gen(blocks, src, dsts, loci=False)
        # Default pool is clean of the degenerate families on its own.
        assert not any(_islands(fp, c) for c in base)
        _fp2, with_knob = _gen(blocks, src, dsts, loci=True)
        base_uids = {buda.topo_uid(c) for c in base}
        knob_uids = {buda.topo_uid(c) for c in with_knob}
        missing = base_uids - knob_uids
        assert not missing, \
            f"gate removed {len(missing)} default candidate(s) under the knob"


# ---------------------------------------------------------------------------
# Declared-feedthru exemption scoping (Codex P2 on #335): the DISCONNECTED
# gate exempts a split ONLY when every island is bridged by a declared
# feedthru block — a candidate that ALSO carries an unrelated island (no
# feedthru-block touch) is a genuine open and must be dropped.
# ---------------------------------------------------------------------------

def _feedthru_fixture():
    """A / mid / B on one row + off-spine C, mid a declared feedthru: the
    TRUNK_H spine splits at mid's faces and C's stub lands in the split gap —
    check_topo flags DISCONNECTED, but every island touches mid (its internal
    routing is the declared bridge), so the gate keeps it."""
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("mid", 300, 0, 400, 100)
    fp.add_block("B", 600, 0, 700, 100)
    fp.add_block("C", 300, 400, 400, 500)
    fp.set_feedthru(True)
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)
    return fp, g


def test_feedthru_bridged_split_is_kept():
    """Every island of the declared split touches 'mid' -> exempt, KEPT."""
    fp, g = _feedthru_fixture()
    cands = g.generate_candidates("A", ["mid", "B", "C"])
    ft = [c for c in cands
          if c.type.startswith("TRUNK_H") and "mid" in c.feedthru_blocks]
    assert ft, "expected a TRUNK_H feedthru candidate through 'mid'"
    # The exemption is only meaningful if the kept candidate really is
    # DISCONNECTED-flagged (the split-gap stub island).
    assert any(_islands(fp, c) for c in ft), \
        "fixture no longer produces a DISCONNECTED-flagged feedthru split"


def test_feedthru_with_unrelated_island_is_dropped():
    """The same feedthru candidate carrying an ADDITIONAL island that touches
    no declared feedthru block is a genuine open: the candidate-wide exemption
    must not cover it (Codex P2) -> DROPPED by the gate."""
    fp, g = _feedthru_fixture()
    cands = g.generate_candidates("A", ["mid", "B", "C"])
    broken = next(c for c in cands
                  if c.type.startswith("TRUNK_H") and "mid" in c.feedthru_blocks
                  and _islands(fp, c))
    clean = next(c for c in cands if not _islands(fp, c))
    # Sanity: unmodified, the gate keeps the bridged split next to a clean
    # alternative (the scoped exemption at work).
    kept = g.filter_uncovered([broken, clean])
    assert {c.type for c in kept} == {broken.type, clean.type}
    # Graft an unrelated floating island (far from every block and from the
    # declared feedthru) onto the feedthru candidate.
    extra = buda.Segment()
    extra.start = buda.Point(2000, 2000)
    extra.end = buda.Point(2200, 2000)
    extra.layer_hint = 4
    broken.segments = list(broken.segments) + [extra]
    buda.annotate_topology(broken, fp)
    assert len(_islands(fp, broken)) >= 1
    kept = g.filter_uncovered([broken, clean])
    assert [c.type for c in kept] == [clean.type], \
        "unrelated island rode the candidate-wide feedthru exemption"
