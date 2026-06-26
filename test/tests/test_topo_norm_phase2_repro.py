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
Repro tests for the two deferred topo-norm Phase 2 defects.  Both are marked xfail
(strict): they currently FAIL because the defect is present, and will XPASS once the
defect is fixed -- at which point the strict marker turns the unexpected pass into a
failure, prompting removal of the marker.

  - Defect 2 (staircase): github issue #57
  - Defect 5 (outlier slide): github issue #58

Rationale and the architectural blockers are in
docs/internal/topo-norm-phase2-deferred.md.
"""
import pytest
import buda


def _make_gen(fp, h=4, v=5):
    gen = buda.TopologyGenerator(fp)
    gen.set_layer_ids(h, v)
    return gen


def _is_trunk(cs):
    """A trunk segment: >=2 SEG conns and no busterm of its own (nuts.cpp predicate)."""
    n_seg = sum(1 for c in cs.conns if c.kind == buda.SegConnKind.SEG)
    n_bt  = sum(1 for c in cs.conns if c.kind != buda.SegConnKind.SEG)
    return n_seg >= 2 and n_bt == 0


# ── Defect 2 — collinear-stub staircase jogs (issue #57) ─────────────────────

@pytest.mark.xfail(strict=True,
                   reason="defect 2 (issue #57): collinear relay stubs are bridged "
                          "by a 2-unit offset jog because ConnTopology infers only "
                          "perpendicular junctions; spec-a2 combine not implemented")
def test_defect2_no_collinear_staircase_jogs():
    """No generated candidate should contain a tiny offset 'staircase' jog.

    A relay touched by two COLLINEAR stubs (same orientation + same perpendicular
    coordinate, e.g. a trunk stub and an MST edge both on a block's top face) is
    wired with a connector offset by 2 units -- emitting degenerate len<=2 segments
    -- because ConnTopology cannot infer a collinear (end-to-end) join.  Spec a2
    (mst-stub-conn.md) says such stubs should be COMBINED into one straight wire, so
    a normalized generator would emit no such jog.
    """
    # Compact 4-block config: an MST edge and the trunk stub land collinearly on a
    # branch block's face, forcing the offset jog (e.g. TRUNK_H+MST@y440).
    fp = buda.Floorplan()
    fp.add_block("A", 0,   0,  80,  80)
    fp.add_block("B", 300, 0,  380, 80)
    fp.add_block("C", 600, 0,  680, 80)
    fp.add_block("D", 300, 400, 380, 480)

    gen = _make_gen(fp)
    cands = gen.generate_candidates("A", ["B", "C", "D"])

    offenders = []
    for c in cands:
        for s in c.segments:
            length = abs(s.end.x - s.start.x) + abs(s.end.y - s.start.y)
            if 0 < length <= 2:                       # degenerate staircase-jog leg
                offenders.append((c.type, (s.start.x, s.start.y, s.end.x, s.end.y)))
    assert not offenders, (
        f"{len(offenders)} collinear-stub staircase jog(s) emitted, e.g. "
        f"{offenders[:3]}"
    )


# ── Defect 5 — unbounded (runaway) trunk slide (issue #58) ───────────────────

@pytest.mark.xfail(strict=True,
                   reason="defect 5 (issue #58): a trunk whose stubs all anchor on "
                          "one side keeps an INT-sentinel-unbounded perp slide; the "
                          "cluster clamp is not implemented (cascades through "
                          "filter_pinched / NUTS placement)")
def test_defect5_trunk_slide_is_bounded():
    """A trunk's perpendicular slide window must stay bounded near its cluster.

    A trunk (>=2 SEG conns, no busterm of its own) is pushed out by Pass 2 only on
    sides where a stub bounds it.  When every stub anchors on one side (an OOB trunk
    hugging the block cluster) the other side stays at its INT sentinel -- unbounded
    -- so NUTS could slide the trunk to the chip edge.  A normalized
    compute_slide_ranges would clamp the runaway side to the receiver cluster.
    """
    # Driver plus receivers all ABOVE the row: the OOB trunk below them stubs only
    # upward, leaving its lower side unbounded.
    fp = buda.Floorplan()
    fp.add_block("D",  900, 1000, 1000, 1100)
    fp.add_block("R1", 200, 1400, 300,  1500)
    fp.add_block("R2", 900, 1400, 1000, 1500)
    fp.add_block("R3", 1600, 1400, 1700, 1500)
    fp.add_block("R4", 900, 1850, 1000, 1950)

    gen = _make_gen(fp)
    cands = gen.generate_candidates("D", ["R1", "R2", "R3", "R4"])

    # The whole layout spans < 2000 units, so any trunk slide window wider than that
    # is a runaway to the chip edge, not a legitimate freedom.
    MAX_REASONABLE = 2000
    worst = 0
    worst_type = None
    for c in cands:
        ct = buda.ConnTopology()
        ct.build(c, fp)
        for cs in ct.segs():
            if _is_trunk(cs):
                width = cs.perp_hi - cs.perp_lo
                if width > worst:
                    worst, worst_type = width, c.type
    assert worst <= MAX_REASONABLE, (
        f"trunk slide window {worst} (in {worst_type}) is unbounded -- "
        f"expected <= {MAX_REASONABLE}"
    )
