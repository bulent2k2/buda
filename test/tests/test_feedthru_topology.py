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

A trunk that crosses a feedthru-enabled non-endpoint block is split at that
block's faces (no stub) and the block is recorded in Topology.feedthru_blocks;
the block's own router bridges the gap.  Gated on Floorplan.get_feedthru, so
non-feedthru flows are unaffected.

These drive the real TopologyGenerator over a multicast (A -> [B, mid]) net,
which produces TRUNK_H candidates.  FT (150..300, 0..300) sits between the
source and the destinations and is tall enough to straddle every trunk y.
"""
import buda


def _gen(configure=None, dsts=("B", "mid")):
    fp = buda.Floorplan()
    fp.add_block("A", 0, 100, 100, 200)
    fp.add_block("FT", 150, 0, 300, 300)
    fp.add_block("mid", 320, 50, 430, 180)
    fp.add_block("B", 480, 100, 580, 200)
    if configure:
        configure(fp)
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)  # h_layer=4, v_layer=5
    return g.generate_candidates("A", list(dsts))


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

def test_disabled_trunk_is_continuous():
    cands = _gen()  # no feedthru
    th = _trunk_h(cands)
    assert th, "expected TRUNK_H candidates"
    for c in th:
        assert list(c.feedthru_blocks) == []
    # The straight trunk spans across FT's x-range [150, 300].
    assert any(_crosses(c, 150, 300) for c in th)


def test_per_block_feedthru_splits_trunk():
    cands = _gen(lambda fp: fp.set_feedthru_block("FT", True))
    ft_cands = [c for c in _trunk_h(cands) if "FT" in c.feedthru_blocks]
    assert ft_cands, "expected at least one TRUNK_H with FT as feedthru"
    for c in ft_cands:
        # Split: no single H segment spans the whole FT x-range.
        assert not _crosses(c, 150, 300), f"{c.type} not split over FT: {_h_segs(c)}"
        # The two halves stop at FT's faces (x=150 and x=300).
        xs = {x for seg in _h_segs(c) for x in seg}
        assert 150 in xs and 300 in xs


def test_global_feedthru_splits_trunk():
    cands = _gen(lambda fp: fp.set_feedthru(True))
    ft_cands = [c for c in _trunk_h(cands) if "FT" in c.feedthru_blocks]
    assert ft_cands
    for c in ft_cands:
        assert not _crosses(c, 150, 300)


def test_per_layer_gates_by_trunk_layer():
    # Feedthru only on layer 4 (the H trunk layer) -> H trunks split.
    cands = _gen(lambda fp: fp.set_feedthru_layer(4, True))
    assert any("FT" in c.feedthru_blocks for c in _trunk_h(cands))
    # Feedthru only on layer 5 (the V layer) -> H trunks are NOT affected.
    cands = _gen(lambda fp: fp.set_feedthru_layer(5, True))
    assert all("FT" not in c.feedthru_blocks for c in _trunk_h(cands))


def test_endpoint_block_is_not_split():
    # Marking a destination (mid) feedthru must not split the trunk at it:
    # endpoints have to stay connected.
    cands = _gen(lambda fp: fp.set_feedthru_block("mid", True))
    for c in _trunk_h(cands):
        assert "mid" not in c.feedthru_blocks


def test_only_straddling_blocks_recorded():
    # 'faraway' is feedthru-enabled but sits at y=500..600, so no trunk crosses it.
    def cfg(fp):
        fp.add_block("faraway", 200, 500, 280, 600)
        fp.set_feedthru(True)
    cands = _gen(cfg)
    for c in _trunk_h(cands):
        assert "faraway" not in c.feedthru_blocks
    # FT is still picked up where the trunk straddles it.
    assert any("FT" in c.feedthru_blocks for c in _trunk_h(cands))


def test_feedthru_block_has_no_stub():
    # A split trunk must not also emit a BUSTERM stub to the feedthru block.
    cands = _gen(lambda fp: fp.set_feedthru_block("FT", True))
    fp = buda.Floorplan()  # rebuild fp for ConnTopology.build
    for n, (x1, y1, x2, y2) in {
        "A": (0, 100, 100, 200), "FT": (150, 0, 300, 300),
        "mid": (320, 50, 430, 180), "B": (480, 100, 580, 200),
    }.items():
        fp.add_block(n, x1, y1, x2, y2)
    fp.set_feedthru_block("FT", True)
    for c in _trunk_h(cands):
        if "FT" not in c.feedthru_blocks:
            continue
        ct = buda.ConnTopology()
        ct.build(c, fp)
        for cs in ct.segs():
            for conn in cs.conns:
                if conn.kind == buda.SegConnKind.BUSTERM:
                    assert conn.block_name != "FT", "feedthru block must have no stub"
