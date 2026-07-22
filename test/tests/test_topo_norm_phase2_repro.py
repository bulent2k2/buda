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
Repro tests for the two (formerly deferred) topo-norm Phase 2 defects.  Both are
now FIXED and these assert they stay fixed:

  - Defect 2 (staircase): github issue #57 — collinear-stub MERGE with tap transfer.
  - Defect 5 (outlier slide): github issue #58 — `clamp_sentinel_windows` bounds a
    still-sentinel slide side to the candidate's design extent.

History and the (now-resolved) architectural blockers are in
docs/internal/topo-norm-phase2-deferred.md.
"""
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

def test_defect2_no_collinear_staircase_jogs():
    """No generated candidate should contain a tiny offset 'staircase' jog.

    A relay touched by two COLLINEAR stubs (same orientation + same perpendicular
    coordinate, e.g. a trunk stub and an MST edge both on a block's top face) used
    to be wired with a connector offset by 2 units -- emitting degenerate len<=2
    segments -- because ConnTopology cannot infer a collinear (end-to-end) join.

    FIXED (issue #57): `complete_relay_junctions`' degenerate-collinear MERGE now
    fires even when a stub's far endpoint taps another block, by REPOINTING that
    block's landing onto the surviving merged wire (the tap transfer), so the two
    stubs COMBINE into one straight pass-through wire and no jog is emitted.
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

def test_defect5_trunk_slide_is_bounded():
    """A trunk's perpendicular slide window must stay bounded near its cluster.

    A trunk (>=2 SEG conns, no busterm of its own) is pushed out by Pass 2 only on
    sides where a stub bounds it.  When every stub anchors on one side (an OOB trunk
    hugging the block cluster) the other side used to stay at its INT sentinel --
    unbounded.

    FIXED (issue #58): the analysis' final pass `clamp_sentinel_windows` bounds any
    still-sentinel side to the candidate's own design extent (its segment nominals
    UNION the blocks bbox, + any reserved detour-channel margin).  It runs LAST, so
    it never feeds tighten_passthrough / pin_relay_taps (the cascade that blocked
    every earlier in-`compute_slide_ranges` clamp), and the bound is looser than any
    interior Hanan cell — NUTS and the planner both re-clamp to the grid, so the fix
    is routed-output-neutral; it only makes the window honest for the explorer.
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
