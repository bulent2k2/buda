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

"""Spec for seg-busterm containment (b34_bus_028).

Three abutted blocks where a bus endpoint block CONTAINS the trunk axis.  The
connectivity model only knows BUSTERM (endpoint on a block face) and SEG
(T-junction) connections, so to connect the contained endpoint block the trunk
generator extends the spine to that block's far face — manufacturing an edge tap
and a redundant spine segment, instead of connecting by containment / pass-through.

For this geometry the ideal topology is a SINGLE horizontal segment at y=4615
(the shared edge of blk_15/blk_32, interior to blk_00): it taps blk_15 and blk_32
at x=2230 and passes through blk_00 — connecting all three with no over-extension.

See docs/internal/seg_busterm_containment.md.  Marked xfail until the containment
generation lands; it should XPASS once a contained endpoint block is connected by
pass-through rather than an edge tap.
"""
import pytest
import buda


def _fp():
    fp = buda.Floorplan()
    fp.add_block("blk_00", 1250, 3780, 2230, 4775)   # receiver — contains the trunk axis
    fp.add_block("blk_15", 2230, 3365, 3500, 4615)   # driver
    fp.add_block("blk_32", 2230, 4615, 3500, 5350)   # receiver
    return fp


def _gen(fp):
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)
    return g


def _violations(topo, fp):
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return [str(v.kind).split(".")[-1] for v in buda.check_topo(ct, topo, fp, 0).violations]


@pytest.mark.xfail(strict=True, reason="seg-busterm containment not yet implemented "
                   "(docs/internal/seg_busterm_containment.md)")
def test_contained_endpoint_connects_by_a_single_straight_segment():
    """A single straight segment should connect all three blocks via containment.

    blk_15/blk_32 share the edge y=4615 which is interior to blk_00, so one H
    segment at y=4615 taps both and passes through blk_00 — the minimal honest
    topology.  Today the generator only offers ≥2-segment trunks that edge-tap the
    contained block.
    """
    fp = _fp()
    cands = _gen(fp).generate_candidates("blk_15", ["blk_32", "blk_00"])
    clean_singles = [
        c for c in cands
        if len(c.segments) == 1
        and "BUSTERM_OPEN" not in _violations(c, fp)
        and "FEEDTHRU_RELAY" not in _violations(c, fp)
    ]
    assert clean_singles, (
        "expected a single-segment candidate connecting all three blocks by "
        "containment; got shapes: " + ", ".join(f"{c.type}({len(c.segments)}seg)"
                                                 for c in cands)
    )
