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
Feedthru phase 2: the generator trunk-split.

A *feedthru* block is a busterm of THIS bundle that the trunk passes straight
through (it would otherwise be a silent pass-through).  When the block opts in,
the trunk is split at its two faces and the block's own lower-level routing
bridges the gap -- giving the block two BUSTERM landings (it "connects >=2 of
the bundle's stubs").  An *unrelated* block the trunk merely crosses is NOT a
feedthru candidate: we only feed through blocks this topology connects to.

These drive the real TopologyGenerator over a multicast (A -> [mid, B]) net.
'mid' (300..400, 0..300) is a destination tall enough to straddle every trunk
y, so the trunk passes through it.
"""
import buda


def _gen(configure=None, dsts=("mid", "B")):
    fp = buda.Floorplan()
    fp.add_block("A",   0, 100, 100, 200)
    fp.add_block("mid", 300, 0, 400, 300)   # tall dest -> trunk passes through it
    fp.add_block("B",   600, 100, 700, 200)
    if configure:
        configure(fp)
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)  # h_layer=4, v_layer=5
    return fp, g.generate_candidates("A", list(dsts))


def _trunk_h(cands):
    return [c for c in cands if c.type.startswith("TRUNK_H")]


def _h_segs(c):
    """(x_lo, x_hi) of every H segment sitting on the trunk line."""
    yt = c.trunk_location
    return sorted((s.start.x, s.end.x) for s in c.segments
                  if s.start.y == s.end.y == yt)


def _crosses(c, x1, x2):
    """True if any single H segment spans the whole [x1, x2] interval."""
    return any(lo <= x1 and hi >= x2 for lo, hi in _h_segs(c))


# ---------------------------------------------------------------------------

def test_disabled_trunk_passes_through_mid():
    _, cands = _gen()  # no feedthru
    th = _trunk_h(cands)
    assert th, "expected TRUNK_H candidates"
    for c in th:
        assert list(c.feedthru_blocks) == []
    # Straight trunk spans across mid's x-range [300, 400] (a silent pass-through).
    inner = [c for c in th if not c.type.endswith("OOB") and "+MST" not in c.type]
    assert any(_crosses(c, 300, 400) and c.pass_through_count >= 1 for c in inner)


def test_per_block_feedthru_splits_at_busterm():
    _, cands = _gen(lambda fp: fp.set_feedthru_block("mid", True))
    ft_cands = [c for c in _trunk_h(cands) if "mid" in c.feedthru_blocks]
    assert ft_cands, "expected a TRUNK_H with mid as feedthru"
    for c in ft_cands:
        # Split: no single H segment spans the whole mid x-range.
        assert not _crosses(c, 300, 400), f"{c.type} not split over mid: {_h_segs(c)}"
        # The two halves stop at mid's faces (x=300 and x=400).
        xs = {x for seg in _h_segs(c) for x in seg}
        assert 300 in xs and 400 in xs
        # mid is no longer counted as a silent pass-through.
        assert c.pass_through_count == 0


def test_global_feedthru_splits_at_busterm():
    _, cands = _gen(lambda fp: fp.set_feedthru(True))
    ft_cands = [c for c in _trunk_h(cands) if "mid" in c.feedthru_blocks]
    assert ft_cands
    for c in ft_cands:
        assert not _crosses(c, 300, 400)


def test_per_layer_gates_by_trunk_layer():
    # Feedthru only on layer 4 (the H trunk layer) -> H trunks split.
    _, cands = _gen(lambda fp: fp.set_feedthru_layer(4, True))
    assert any("mid" in c.feedthru_blocks for c in _trunk_h(cands))
    # Feedthru only on layer 5 (the V layer) -> H trunks are NOT affected.
    _, cands = _gen(lambda fp: fp.set_feedthru_layer(5, True))
    assert all("mid" not in c.feedthru_blocks for c in _trunk_h(cands))


def test_unrelated_block_is_never_feedthru():
    # 'FT' is feedthru-enabled and the trunk crosses it, but it is NOT a busterm
    # of this bundle (A -> [mid, B]).  A block the topology does not connect to
    # must never be fed through -- only mid (a real destination) is.
    def cfg(fp):
        fp.add_block("FT", 150, 0, 250, 300)  # unrelated, between A and mid
        fp.set_feedthru(True)
    _, cands = _gen(cfg)
    for c in _trunk_h(cands):
        assert "FT" not in c.feedthru_blocks
    assert any("mid" in c.feedthru_blocks for c in _trunk_h(cands))


def test_stubbed_destination_is_not_split():
    # An extreme destination (B, at the trunk's right end) connects via its face,
    # not as a pass-through, so marking it feedthru must not split the trunk.
    _, cands = _gen(lambda fp: fp.set_feedthru_block("B", True))
    for c in _trunk_h(cands):
        assert "B" not in c.feedthru_blocks


def test_feedthru_block_has_two_busterm_landings():
    # The split gives mid a BUSTERM connection on each face, so its lower-level
    # bridge has two valid landings and check_topo accepts the relay as declared.
    fp, cands = _gen(lambda fp: fp.set_feedthru_block("mid", True))
    c = next(x for x in cands if x.type == "TRUNK_H@y150")
    assert "mid" in c.feedthru_blocks
    ct = buda.ConnTopology()
    ct.build(c, fp)
    mid_busterms = sum(
        1
        for cs in ct.segs()
        for conn in cs.conns
        if conn.kind == buda.SegConnKind.BUSTERM and conn.block_name == "mid"
    )
    assert mid_busterms == 2, f"expected 2 busterm landings on mid, got {mid_busterms}"
    # A *declared* feedthru relay is accepted (no FEEDTHRU_RELAY violation).
    res = buda.check_topo(ct, c, fp, 0)
    assert [str(v.kind) for v in res.violations] == []


def test_undeclared_relay_still_flagged():
    # Same geometry, but with the feedthru metadata stripped, the verifier must
    # still catch the silent relay -- the skip is gated on the declaration.
    fp, cands = _gen(lambda fp: fp.set_feedthru_block("mid", True))
    c = next(x for x in cands if x.type == "TRUNK_H@y150")
    c.feedthru_blocks = []
    ct = buda.ConnTopology()
    ct.build(c, fp)
    res = buda.check_topo(ct, c, fp, 0)
    assert any(str(v.kind) == "ViolationKind.FEEDTHRU_RELAY" for v in res.violations)
