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

"""Spec for seg-busterm containment (b34_bus_028) — bounded interior spine (#84).

Three abutted blocks where a bus endpoint block (blk_00) CONTAINS the trunk axis.
A bus segment carries N bit-wires, so it needs a non-zero perpendicular interval
(it lives in a Hanan *cell*, never on a Hanan *line*).  The "single straight
segment at y=4615" idea is invalid: y=4615 is the abutment line of blk_15/blk_32
(zero slide).  The bus routes through blk_00's interior (a real interval) — it
connects to blk_00 by **containment** (the trunk touches blk_00 by overlap).

The trunk must NOT manufacture an artificial tap to blk_00's far face (the conn-
segs straddle it, so it cannot slide off — issue #84, test_topo_trunk_tap_edge).
But a spine-less collapse would leave the two collinear stubs with no junction
constraint — ConnTopology links only *perpendicular* pairs — so NUTS could place
the branches on different tracks: a silent open (Codex P1).  The fix emits a
**bounded interior spine**: a short trunk segment from the junction toward the
contained block's centre, strictly inside it (no face tap), carrying the SEG
junction the stubs hang off.  blk_00 is connected by containment either way;
feedthru does not change this geometry (a spine fully inside the block is not a
pass-through, so it is not feedthru-split).

See docs/internal/seg_busterm_containment.md.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
import buda

_ROOT = Path(__file__).parents[2]


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


def _build(topo, fp):
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return ct


def _violations(ct, topo, fp):
    return [str(v.kind).split(".")[-1] for v in buda.check_topo(ct, topo, fp, 0).violations]


def _busterm_blocks(ct):
    out = set()
    for s in ct.segs():
        for c in s.conns:
            if c.kind == buda.SegConnKind.BUSTERM:
                out.add(c.block_name)
    return out


def _seg_link_count(ct):
    """Number of SEG (perpendicular junction) conn endpoints across all segs."""
    return sum(1 for s in ct.segs() for c in s.conns
               if c.kind == buda.SegConnKind.SEG)


def test_contained_endpoint_connects_by_containment_with_junction():
    """blk_00 NOT a feedthru → TRUNK_V@x1740 connects blk_00 by containment, and
    its bounded interior spine carries a real junction (issue #84).

    The trunk touches blk_00 by overlap (no artificial top/bottom edge tap); a
    short vertical spine *inside* blk_00 holds the SEG junction the two H stubs
    hang off, so NUTS cannot place the branches on independent tracks (Codex P1).
    """
    fp = _fp()
    flagged = [c for c in _gen(fp).generate_candidates("blk_15", ["blk_32", "blk_00"])
               if c.type == "TRUNK_V@x1740"]
    assert flagged, "expected the in-bbox TRUNK_V@x1740 shape"
    c = flagged[0]
    ct = _build(c, fp)
    v = _violations(ct, c, fp)
    assert "FEEDTHRU_RELAY" not in v and "BUSTERM_OPEN" not in v, v
    assert "blk_00" not in _busterm_blocks(ct), "blk_00 should be containment, not edge-tapped"
    assert _seg_link_count(ct) >= 2, (
        "the bounded interior spine must carry a SEG junction to both stubs (Codex P1)")


def test_degenerate_straddle_spreads_no_dangle_opposing_pulls():
    """The spine spans BETWEEN the two spread stubs (no dead wire), and the stubs
    carry opposing net_pull — the straddle pulls (#84 refinement).

    The earlier block-centre anchor left the spine's far endpoint connected to
    nothing (an overstretched dead segment NUTS cannot retract) and both stubs at
    the same spine end, so net_pull came out 0.  Spreading each stub toward its own
    block's side makes both spine endpoints land on a stub and the trunk endpoints
    pull inward (low=+1 / high=-1).
    """
    fp = _fp()
    c = next(x for x in _gen(fp).generate_candidates("blk_15", ["blk_32", "blk_00"])
             if x.type == "TRUNK_V@x1740")
    ct = _build(c, fp)
    cs = ct.segs()
    spine_idx = [i for i, s in enumerate(c.segments) if s.start.x == s.end.x]
    stub_idx = [i for i, s in enumerate(c.segments) if s.start.y == s.end.y]
    assert len(spine_idx) == 1 and len(stub_idx) == 2, "expected one V spine + two H stubs"
    sp = c.segments[spine_idx[0]]
    sp_lo, sp_hi = sorted((sp.start.y, sp.end.y))
    stub_ys = sorted(c.segments[i].start.y for i in stub_idx)
    assert (sp_lo, sp_hi) == (stub_ys[0], stub_ys[1]), (
        f"spine ({sp_lo},{sp_hi}) must span exactly between the stub perps {stub_ys} "
        f"— no dangling/overstretched endpoint")
    pulls = [cs[i].net_pull for i in stub_idx]
    assert pulls[0] * pulls[1] < 0, f"stubs must pull in opposite directions, got {pulls}"


def test_no_junctionless_containment_without_feedthru():
    """No clean candidate connects blk_00 by junction-less containment.

    A block covered only by a wire passing over it, with no perpendicular segment
    linking the branches, is a silent-open risk (Codex P1).  The degenerate
    straddle now emits a bounded interior spine (a junction), and the old
    junction-less suppression-containment OOB candidate (TRUNK_V_OOB@x1025) is
    dropped — so every containment connect to blk_00 carries a junction.
    """
    fp = _fp()
    for c in _gen(fp).generate_candidates("blk_15", ["blk_32", "blk_00"]):
        if "MST" in c.type:
            continue
        ct = _build(c, fp)
        v = _violations(ct, c, fp)
        if "BUSTERM_OPEN" in v or "FEEDTHRU_RELAY" in v:
            continue
        if "blk_00" in _busterm_blocks(ct):
            continue                          # edge-tapped, not containment
        assert _seg_link_count(ct) >= 2, (
            f"{c.type} connects blk_00 by junction-less containment (Codex P1)")


def test_feedthru_contained_spine_keeps_junction():
    """blk_00 a feedthru → the contained spine is NOT split away; junction stays.

    A feedthru relay needs the trunk to pass THROUGH the block; a spine fully
    inside the block does not, so it must not be split out (which would delete the
    only junction and reopen Codex P1).  TRUNK_V@x1740 stays a bounded spine whose
    junction links both stubs.
    """
    fp = _fp()
    fp.set_feedthru_block("blk_00", True)
    flagged = [c for c in _gen(fp).generate_candidates("blk_15", ["blk_32", "blk_00"])
               if c.type == "TRUNK_V@x1740"]
    assert flagged, "expected TRUNK_V@x1740"
    ct = _build(flagged[0], fp)
    assert _seg_link_count(ct) >= 2, (
        "feedthru must not split away the contained spine's junction")


@pytest.mark.mid
def test_b34_bus_028_repro_routes_cleanly():
    """The committed repro routes end to end with no opens and 0 unplaced bits.

    blk_00 (which contains the trunk axis) is connected by containment, not an
    over-extended edge tap; the candidate collapses to its branches and routes.
    """
    repro = _ROOT / "flow" / "big_data_test" / "big2" / "b34_bus_028.buda"
    build_dir = _ROOT / "build"
    tools_dir = _ROOT / "tools"
    ppath = os.environ.get("PYTHONPATH", "")
    new_ppath = f"{build_dir}:{tools_dir}" + (f":{ppath}" if ppath else "")
    env = {**os.environ, "PYTHONPATH": new_ppath}
    r = subprocess.run(
        [sys.executable, str(_ROOT / "src" / "buda_cli.py"), "--no-viz", str(repro)],
        capture_output=True, text=True, env=env,
    )
    out = r.stdout + r.stderr
    assert r.returncode == 0, f"non-zero exit {r.returncode}\n{out}"
    assert "no BUSTERM connection" not in out, out
    assert "0 bits unplaced" in out, out
    assert "Success: no opens found." in out, out


def test_b34_cheapest_candidate_is_not_a_pinch():
    """The cheapest (auto-selected) candidate routes clean — no zero-slide pinch.

    Regression: an add_trunk_h collapse once emitted a degenerate H-trunk whose
    blk_00 stub landed on the abutment Hanan line (zero perp interval), and being
    the cheapest candidate the planner auto-selected it → 28/28 unplaced.  Drive
    the flow pinning the FIRST candidate and require a clean route.
    """
    src_dir = str(_ROOT / "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    import buda_cli
    import io
    import contextlib

    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [
        "add_block blk_00 1250 3780 2230 4775",
        "add_block blk_15 2230 3365 3500 4615",
        "add_block blk_32 2230 4615 3500 5350",
        "add_bus bus_028[28] blk_15.p blk_32.p,blk_00.p",
        "run_bundler", "generate_topologies",
        "select_topology 1 1",                      # pin the FIRST (cheapest) candidate
        f"source {_ROOT / 'flow' / 'big_data_test' / 'big2' / 'tracks4top.buda'}",
        "run_planner", "run_nuts", "run_detailed_nuts",
    ]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    assert s.nuts_result.num_violations == 0, "NUTS interval violation in first candidate"
    assert s.detailed_result.num_unplaced == 0, (
        f"first candidate left {s.detailed_result.num_unplaced} bits unplaced "
        f"(a zero-slide pinch slipped through as the cheapest candidate)"
    )
