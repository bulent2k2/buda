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
Regression: a DIRECT (2-pin) bus between two blocks that share a full edge must
still produce a valid, covered candidate.

When two blocks abut (share a full face), their facing faces coincide, so:
  * the direct I-shape collapses to zero length (src face == dst face) and is
    skipped by the non-degenerate guard,
  * the direct I in the perpendicular axis needs an interval that a shared edge
    does not provide, and
  * every L / Z / U stub is sub-min-stub-length,
leaving NO candidate at all — a silently unrouted bus (common for adjacent
macros).  generate_2pin now emits a single segment running ALONG the shared edge
over the face overlap; it lies on both blocks' facing faces, so both are covered
as pass-through and check_topo reports no BUSTERM_OPEN.

Point-touch corners and fully-coincident blocks are DEGENERATE placements (no
routable channel) and deliberately stay candidate-free.

Sibling of test_topo_mst_abutted.py, which covers the N>=4 MST-edge abutment.
"""
import buda
import pytest


def _fp(coords):
    fp = buda.Floorplan()
    for name, (x1, y1, x2, y2) in coords.items():
        fp.add_block(name, x1, y1, x2, y2)
    return fp


def _gen(fp):
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)  # h=M4, v=M5
    return g


def _violations(topo, fp):
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return [str(v.kind).split(".")[-1] for v in buda.check_topo(ct, topo, fp, 0).violations]


def test_vertical_edge_abutment_direct_bus_is_covered():
    """A and B share the vertical edge x=100 -> one V segment along it, both covered."""
    fp = _fp({"A": (0, 0, 100, 100), "B": (100, 0, 200, 100)})
    cands = _gen(fp).generate_candidates("A", ["B"])
    assert cands, "abutting 2-pin blocks produced NO candidate (silently unrouted bus)"
    c = cands[0]
    assert c.type.startswith("I_V"), f"expected a vertical shared-edge segment, got {c.type}"
    assert len(c.segments) == 1
    seg = c.segments[0]
    assert seg.start.x == seg.end.x == 100, "segment must lie on the shared face x=100"
    assert "BUSTERM_OPEN" not in _violations(c, fp), _violations(c, fp)


def test_horizontal_edge_abutment_direct_bus_is_covered():
    """A and B share the horizontal edge y=100 -> one H segment along it, both covered."""
    fp = _fp({"A": (0, 0, 100, 100), "B": (0, 100, 100, 200)})
    cands = _gen(fp).generate_candidates("A", ["B"])
    assert cands, "abutting 2-pin blocks produced NO candidate (silently unrouted bus)"
    c = cands[0]
    assert c.type.startswith("I_H"), f"expected a horizontal shared-edge segment, got {c.type}"
    assert len(c.segments) == 1
    seg = c.segments[0]
    assert seg.start.y == seg.end.y == 100, "segment must lie on the shared face y=100"
    assert "BUSTERM_OPEN" not in _violations(c, fp), _violations(c, fp)


@pytest.mark.parametrize("coords, why", [
    ({"A": (0, 0, 100, 100), "B": (100, 100, 200, 200)}, "corner point-touch"),
    ({"A": (0, 0, 100, 100), "B": (0, 0, 100, 100)},     "fully coincident"),
])
def test_degenerate_placements_produce_no_candidate(coords, why):
    """Point-touch and fully-coincident blocks are degenerate: no routable channel,
    so the fallback deliberately does NOT invent a candidate."""
    fp = _fp(coords)
    cands = _gen(fp).generate_candidates("A", ["B"])
    assert cands == [] or len(cands) == 0, f"{why}: expected no candidate, got {[c.type for c in cands]}"
