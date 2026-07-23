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
Repro tests for the two topo-norm Phase 2 defects.  Both are now FIXED.

  - Defect 2 (staircase): github issue #57
  - Defect 5 (outlier slide / OOB trunk): github issue #58

Defect 5's resolution (see docs/internal/topo-norm-phase2-deferred.md): the
runaway *analysis* slide window is a deliberate free-DOF representation and is
left alone (bounding it cascades — 25 tests / a corpus regression, PR #410).
What actually mattered is the *placement*: an out-of-bbox trunk lives beyond the
Hanan grid, so NUTS could not seat it and its bits stranded.  The fix makes the
NUTS design boundary detour-channel aware (an explicit ``detour_channel``
reserves the band the OOB trunk routes in) and, when a selected OOB trunk still
has no channel to sit in, fails LOUD (``NUTSResult.unseatable_trunks``) instead
of silently reporting "0 overlaps" while detailed NUTS drops the bits.
"""
import buda
import buda_cli


def _make_gen(fp, h=4, v=5):
    gen = buda.TopologyGenerator(fp)
    gen.set_layer_ids(h, v)
    return gen


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


# ── Defect 5 — out-of-bbox trunk placement (issue #58) ───────────────────────

# The floorplan the original repro used: driver D plus receivers, with a pure
# TRUNK_H_OOB candidate whose trunk sits BELOW the block bbox (y=905 < the
# bottom block edge y=1000).  Two TOP layers + track patterns so the full
# NUTS -> detailed-NUTS pipeline runs and we can see whether the OOB trunk's
# 8 bits place or strand.
_OOB_SETUP = [
    "def_layer 5 M5 V TOP 50",
    "def_layer 6 M6 H TOP 52.94",
    "def_track_pattern 5 0 POWER 3 1 SIGNAL 2 1 SIGNAL 2 1 SIGNAL 2 1 "
    "SIGNAL 2 1 GROUND 3 1 SIGNAL 2 1 SIGNAL 2 1 SIGNAL 2 1 SIGNAL 2 1",
    "def_track_pattern 6 -400 POWER 4 1 SIGNAL 2 1 SIGNAL 2 1 SIGNAL 2 1 "
    "SIGNAL 2 1 GROUND 4 1 SIGNAL 2 1 SIGNAL 2 1 SIGNAL 2 1 SIGNAL 2 1",
    "add_block D  900 1000 1000 1100",
    "add_block R1 200 1400 300  1500",
    "add_block R2 900 1400 1000 1500",
    "add_block R3 1600 1400 1700 1500",
    "add_block R4 900 1850 1000 1950",
    "add_bus b[8] D.o R1.i,R2.i,R3.i,R4.i",
    "run_bundler",
    "generate_topologies",
]


def _pin_below_bbox_oob_trunk(detour_size=None):
    """Build the setup, pin the pure below-bbox OOB-H trunk, optionally reserve
    a detour channel, and run the full NUTS + detailed-NUTS pipeline."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    for cmd in _OOB_SETUP:
        s.do_command(cmd)
    cands = list(s.bundles[0].input.candidates)
    idx = None
    for i, c in enumerate(cands):
        if c.type.startswith("TRUNK_H_OOB") and "MST" not in c.type:
            ys = [min(sg.start.y, sg.end.y) for sg in c.segments]
            if min(ys) < 1000:                 # trunk below the block bbox
                idx = i + 1
                break
    assert idx is not None, "no below-bbox pure OOB-H trunk candidate generated"
    s.do_command(f"select_topology 1 {idx}")
    if detour_size is not None:
        s.do_command(f"detour_channel A {detour_size}")
    s.do_command("run_nuts")
    s.do_command("run_detailed_nuts")
    return s


def test_defect5_oob_trunk_seats_in_detour_channel():
    """With a detour_channel wide enough to reach it, the below-bbox OOB trunk
    is SEATED and all its bits place -- the NUTS design boundary now reaches
    into the reserved band (issue #58)."""
    s = _pin_below_bbox_oob_trunk(detour_size=500)
    assert not s.nuts_result.unseatable_trunks, (
        "OOB trunk should be seatable inside a detour_channel A 500, but NUTS "
        f"reported {len(s.nuts_result.unseatable_trunks)} unseatable trunk(s)")
    assert s.detailed_result.num_unplaced == 0, (
        f"expected all bits placed, got {s.detailed_result.num_unplaced} "
        "unplaced -- the detour-seated trunk should route cleanly")


def test_defect5_oob_trunk_unseatable_without_channel_is_loud():
    """With NO detour_channel the below-bbox OOB trunk cannot be seated; NUTS
    must say so LOUDLY (unseatable_trunks) rather than report a clean solve
    while detailed NUTS silently strands the bits (issue #58)."""
    s = _pin_below_bbox_oob_trunk(detour_size=None)
    assert s.nuts_result.unseatable_trunks, (
        "an OOB trunk with no detour_channel to sit in must be flagged "
        "unseatable, not silently accepted")
    u = s.nuts_result.unseatable_trunks[0]
    assert u.horiz and u.nom < u.bound, (
        "the flagged trunk should be the below-bbox H trunk (nominal below "
        f"the boundary): nom={u.nom} bound={u.bound}")
    # And the strand it warns about is real: those bits do not place.
    assert s.detailed_result.num_unplaced > 0
