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

"""Spec for seg-busterm containment (b34_bus_028), gated on feedthru.

Three abutted blocks where a bus endpoint block (blk_00) CONTAINS the trunk axis.
A bus segment carries N bit-wires, so it needs a non-zero perpendicular interval
(it lives in a Hanan *cell*, never on a Hanan *line*).  The "single straight
segment at y=4615" idea is invalid: y=4615 is the abutment line of blk_15/blk_32
(zero slide).  The bus could instead route through blk_00's interior (a real
interval) — i.e. connect to blk_00 by **containment**.

But containment connects blk_00 with NO stub landing on its face: the trunk
relays the bus across blk_00's interior to reach its pin.  That is electrically
valid only if blk_00 is a declared **feedthru** on the trunk layer — exactly the
set_feedthru relay contract.  So the containment treatment is GATED:

- blk_00 NOT a feedthru → the trunk is NOT collapsed; blk_00 is connected by a
  real edge tap (a BUSTERM on its own face with a non-zero slide interval).
- blk_00 IS a feedthru (set_feedthru) → the slim containment candidate is legal
  and blk_00 is covered by pass-through (no edge tap).

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


def _clean_containment_candidates(fp):
    """Clean candidates (no BUSTERM_OPEN/FEEDTHRU_RELAY) connecting blk_00 with no
    edge tap — i.e. by containment."""
    cands = _gen(fp).generate_candidates("blk_15", ["blk_32", "blk_00"])
    out = []
    for c in cands:
        ct = _build(c, fp)
        v = _violations(ct, c, fp)
        if "BUSTERM_OPEN" in v or "FEEDTHRU_RELAY" in v:
            continue                          # not a clean connect
        if "blk_00" not in _busterm_blocks(ct):
            out.append(c)                     # blk_00 connected without an edge tap
    return cands, out


def test_contained_endpoint_edge_taps_without_feedthru():
    """blk_00 NOT a feedthru → the in-bbox V trunk edge-taps blk_00, not contains.

    Containment (covering blk_00 by a wire passing over it, no face tap) would
    rely on blk_00 relaying the bus internally — only legal under feedthru.  With
    no feedthru declared, the in-bbox trunk (TRUNK_V@x1740) must not collapse: it
    connects blk_00 by a real BUSTERM edge tap (3 segs, not the slim 2-seg
    containment shape).
    """
    fp = _fp()
    cands = _gen(fp).generate_candidates("blk_15", ["blk_32", "blk_00"])
    flagged = [c for c in cands if c.type == "TRUNK_V@x1740"]
    assert flagged, "expected the in-bbox TRUNK_V@x1740 shape"
    for c in flagged:
        ct = _build(c, fp)
        assert "blk_00" in _busterm_blocks(ct), (
            f"{c.type} connects blk_00 by containment without feedthru "
            f"(should edge-tap): {len(c.segments)} segs"
        )


@pytest.mark.xfail(reason="OOB trunk connects blk_00 by suppression-induced "
                          "containment; gating stub-suppression generally "
                          "regresses big2 (60→128 unplaced). Deferred — needs a "
                          "feedthru-aware coverage model + planner that absorbs "
                          "the kept stubs. See seg_busterm_containment.md.")
def test_oob_trunk_edge_taps_without_feedthru():
    """blk_00 NOT a feedthru → the OOB trunk should also edge-tap, not contain.

    TRUNK_V_OOB@x1025 suppresses blk_00's own stub and covers it only by the
    blk_15/blk_32 stubs crossing it (landing at the zero-slide triple corner
    2230,4615).  Per the feedthru principle this is illegal containment, but the
    fix (gating stub-suppression) has broad blast radius — deferred.
    """
    fp = _fp()
    cands = _gen(fp).generate_candidates("blk_15", ["blk_32", "blk_00"])
    flagged = [c for c in cands if c.type == "TRUNK_V_OOB@x1025"]
    assert flagged, "expected the OOB TRUNK_V_OOB@x1025 shape"
    for c in flagged:
        ct = _build(c, fp)
        assert "blk_00" in _busterm_blocks(ct), (
            f"{c.type} connects blk_00 by containment without feedthru"
        )


def test_contained_endpoint_connects_by_containment_with_feedthru():
    """blk_00 IS a feedthru → a clean candidate connects it by containment.

    With set_feedthru declared the slim containment topology is legal: blk_00 is
    covered by pass-through (no BUSTERM edge tap) while every block stays
    connected.
    """
    fp = _fp()
    fp.set_feedthru_block("blk_00", True)
    cands, containment = _clean_containment_candidates(fp)
    assert containment, (
        "expected a clean candidate connecting blk_00 by containment (no BUSTERM "
        "edge tap) once blk_00 is a feedthru; shapes: "
        + ", ".join(f"{c.type}({len(c.segments)}seg)" for c in cands)
    )


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
