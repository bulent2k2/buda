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
Regression: MST topologies must connect ABUTTED blocks.

Two blocks that share an edge have Manhattan distance 0, so their MST edge sorts
first and is always chosen — but closest_points collapses the shared edge to a
single point (p1 == p2) and the edge realizers used to DROP that zero-length edge,
leaving the abutted block disconnected (unplaced bits in DetailedNUTS).
See docs/internal/mst-abutted-blocks.md.  Repro flow:
flow/big_data_test/big2_b1_bus_007.buda.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
import buda

_ROOT = Path(__file__).parents[2]


def _make_fp(coords):
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


# ── unit level ────────────────────────────────────────────────────────────────

def test_standalone_mst_connects_abutted_block():
    """A 4-block MST whose tree includes an abutment edge connects all blocks.

    B shares the vertical edge x=100 with driver A (y-overlap [0,100]); pre-fix the
    A-B edge was dropped and B was left with no busterm/pass-through (a check_topo
    coverage violation).  The shared-edge segment now keeps every block tapped.
    """
    coords = {
        "A": (0,   0,   100, 100),
        "B": (100, 0,   200, 100),   # abuts A on x=100
        "C": (0,   300, 100, 400),
        "D": (300, 300, 400, 400),
    }
    fp = _make_fp(coords)
    cands = _gen(fp).generate_candidates("A", ["B", "C", "D"])
    mst = [c for c in cands if c.type.startswith("MST_")]
    assert mst, "expected standalone MST candidates for 4 blocks"
    for c in mst:
        # BUSTERM_OPEN == "block has no BUSTERM conn and no pass-through" — the
        # exact dropped-abutment symptom.  (Unrelated relay-quality flags such as
        # FEEDTHRU_RELAY are out of scope here.)
        assert "BUSTERM_OPEN" not in _violations(c, fp), (
            f"{c.type} dropped an abutted block: {_violations(c, fp)}"
        )


def test_mst_connects_both_abutment_orientations():
    """Both a shared-vertical-edge and a shared-horizontal-edge abutment connect.

    B abuts A on x=100 (vertical shared edge); C abuts A on y=100 (horizontal
    shared edge).  Each abutment must be realized, not dropped.
    """
    coords = {
        "A": (100, 100, 200, 200),
        "B": (200, 100, 300, 200),   # shared vertical edge x=200
        "C": (100, 200, 200, 300),   # shared horizontal edge y=200
        "D": (500, 500, 600, 600),
    }
    fp = _make_fp(coords)
    cands = _gen(fp).generate_candidates("A", ["B", "C", "D"])
    mst = [c for c in cands if c.type.startswith("MST_")]
    assert mst, "expected standalone MST candidates for 4 blocks"
    for c in mst:
        assert "BUSTERM_OPEN" not in _violations(c, fp), (
            f"{c.type} dropped an abutted block (B or C): {_violations(c, fp)}"
        )


def test_mst_collinear_internal_abutted_chain():
    """Straight A-B-C abutment chain (B internal, both shared edges vertical).

    Fixed by the perpendicular-crossing realization: each abutment edge crosses
    its shared edge (here horizontally), so the two edges incident to the internal
    node B overlap and touch rather than being collinear stubs ConnTopology can't
    join — no FEEDTHRU_RELAY. (Previously a deferred defect-2 case.)
    """
    coords = {
        "A": (0,   0,   100, 100),
        "B": (100, 0,   200, 100),   # abuts A on x=100
        "C": (200, 0,   300, 100),   # abuts B on x=200 (collinear with A-B at B)
        "D": (600, 500, 700, 600),
    }
    fp = _make_fp(coords)
    cands = _gen(fp).generate_candidates("A", ["B", "C", "D"])
    mst = [c for c in cands if c.type.startswith("MST_")]
    assert mst, "expected standalone MST candidates for 4 blocks"
    for c in mst:
        assert "FEEDTHRU_RELAY" not in _violations(c, fp), (
            f"{c.type} collinear internal abutted node unjoined: {_violations(c, fp)}"
        )


@pytest.mark.xfail(
    strict=True,
    reason="Deferred collinear-join (defect 2): when a perpendicular abutment "
    "crossing meets a REGULAR MST edge end-to-end collinearly (here A abutted "
    "above B, B-C a regular V edge below at the same x), ConnTopology infers no "
    "SEG join, so the standalone MST is disconnected. check_topo now flags it "
    "(FEEDTHRU_RELAY) and the connectivity gate drops it (so the planner can't "
    "route an open), but the topology cannot yet be REALIZED connected. Will "
    "XPASS once collinear-join inference lands. See docs/internal/"
    "mst-abutted-blocks.md and Codex review on PR #61.",
)
def test_abutment_collinear_butt_joint_connects():
    """Abutment crossing collinear end-to-end with a regular edge should connect."""
    coords = {
        "A": (0, 200, 100, 300),
        "B": (0, 100, 100, 200),   # A abuts B above (shared y=200)
        "C": (0, 0,   100, 50),    # B-C regular V edge below (gap), same x => collinear
        "D": (600, 600, 700, 700),
    }
    fp = _make_fp(coords)
    cands = _gen(fp).generate_candidates("B", ["A", "C", "D"])
    mst = [c for c in cands if c.type.startswith("MST_")]
    # The disconnected MST is dropped at generation, so no standalone MST survives
    # until collinear butt-joints can be inferred/realized.
    assert mst, "collinear butt-joint MST should connect and survive generation"


# ── end-to-end repro ──────────────────────────────────────────────────────────

@pytest.mark.mid
def test_big2_b1_bus_007_repro_routes_cleanly():
    """The committed repro flow routes with no opens and 0 unplaced bits."""
    repro = _ROOT / "flow" / "big_data_test" / "big2_b1_bus_007.buda"
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
    # No block left without a busterm/pass-through at any stage.
    assert "no BUSTERM connection" not in out, out
    assert "no pass-through" not in out, out
    assert "no placed track" not in out, out
    assert "0 bits unplaced" in out, out
