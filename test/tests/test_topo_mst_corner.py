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
Regression: MST topologies for CORNER-DIAGONAL block pairs.

Two blocks whose facing projections meet at only a single point (e.g.
blk_09.x2 == blk_39.x1 with no y-overlap) are corner-diagonal.  closest_points
returns a straight edge pinned to that single coordinate (zero perpendicular
slide), so filter_pinched dropped the whole MST candidate — leaving the bundle
with NO standalone MST (issue "no mst, nor hybrid trunk-mst",
flow/big_data_test/big2/b3_bus_023.buda).  The fix realizes such an edge as an
L-shape around the corner so each leg has slide.
See docs/internal/mst-abutted-blocks.md.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
import buda
from subprocess_env import buda_env

_ROOT = Path(__file__).parents[2]


def _make_fp(coords):
    fp = buda.Floorplan()
    for name, (x1, y1, x2, y2) in coords.items():
        fp.add_block(name, x1, y1, x2, y2)
    return fp


def _gen(fp):
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)
    return g


def _violations(topo, fp):
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return [str(v.kind).split(".")[-1] for v in buda.check_topo(ct, topo, fp, 0).violations]


def test_corner_diagonal_mst_generated_and_clean():
    """A corner-diagonal MST edge no longer pinches the candidate out of existence.

    A and B touch only at the point (100,100)/(100,200) — x-projections meet at
    x=100, y-projections are disjoint.  Their MST edge (distance 100, the shortest)
    is therefore in every MST.  Pre-fix it was a straight V pinned at x=100, so
    filter_pinched dropped both MST_HV and MST_VH (zero standalone MST).  The
    L-shape realization keeps them, connected and clean.
    """
    coords = {
        "A": (0,   0,   100, 100),
        "B": (100, 200, 200, 300),   # corner-diagonal to A (touch x=100, y-gap)
        "C": (500, 0,   600, 100),
        "D": (500, 500, 600, 600),
    }
    fp = _make_fp(coords)
    cands = _gen(fp).generate_candidates("A", ["B", "C", "D"])
    mst = [c for c in cands if c.type.startswith("MST_")]
    assert mst, "corner-diagonal edge pinched the standalone MST out of existence"
    for c in mst:
        v = _violations(c, fp)
        assert "BUSTERM_OPEN" not in v and "FEEDTHRU_RELAY" not in v, (
            f"{c.type} corner-diagonal edge not cleanly connected: {v}"
        )


def test_corner_diagonal_trunk_mst_3blocks():
    """A 3-block bundle keeps TRUNK+MST coverage across a corner-diagonal edge.

    For N<4 there is no standalone MST (add_mst_candidates bails), so the trunk+MST
    hybrid is the only MST coverage.  B and C meet at only the point (300,300)/
    (300,400); pre-fix the hybrid's realize_edges sent that edge through the
    straight pinned branch and filter_pinched dropped every TRUNK+MST candidate.
    The L-shape realization in realize_edges keeps them.
    """
    coords = {
        "A": (0,   0,   100, 100),
        "B": (200, 200, 300, 300),
        "C": (300, 400, 400, 500),   # corner-diagonal to B (touch x=300, y-gap)
    }
    fp = _make_fp(coords)
    cands = _gen(fp).generate_candidates("A", ["B", "C"])
    mstish = [c for c in cands if "MST" in c.type]
    assert mstish, "corner-diagonal edge dropped all TRUNK+MST coverage for 3 blocks"
    for c in mstish:
        v = _violations(c, fp)
        assert "BUSTERM_OPEN" not in v and "FEEDTHRU_RELAY" not in v, (
            f"{c.type} corner-diagonal hybrid not cleanly connected: {v}"
        )


@pytest.mark.mid
def test_big2_b3_bus_023_repro_routes_cleanly():
    """The committed repro routes with no opens and 0 unplaced, and now offers MST."""
    repro = _ROOT / "flow" / "big_data_test" / "big2" / "b3_bus_023.buda"
    env = buda_env(_ROOT)
    r = subprocess.run(
        [sys.executable, str(_ROOT / "src" / "buda_cli.py"), "--no-viz", str(repro)],
        capture_output=True, text=True, env=env,
    )
    out = r.stdout + r.stderr
    _fl = repro.parent / "log" / f"{repro.stem}_flow.log"
    if _fl.exists():                       # per-command detail now lives here
        out += _fl.read_text()
    assert r.returncode == 0, f"non-zero exit {r.returncode}\n{out}"
    assert "no BUSTERM connection" not in out, out
    assert "0 bits unplaced" in out, out
