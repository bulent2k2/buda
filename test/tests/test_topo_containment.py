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

Three abutted blocks where a bus endpoint block (blk_00) CONTAINS the trunk axis.
The connectivity model only has BUSTERM (endpoint on a block FACE) and SEG
connections — no containment — so the trunk generator extends the spine to
blk_00's far face to manufacture an edge tap, instead of connecting by
containment / pass-through.

A bus segment carries N bit-wires, so it needs a non-zero perpendicular interval
(it lives in a Hanan *cell*, never on a Hanan *line*).  The "single straight
segment at y=4615" idea is therefore invalid: y=4615 is the abutment line of
blk_15/blk_32 (zero slide).  The bus must instead route through blk_00's interior
(a real interval) — i.e. connect to blk_00 by containment.

Target (this spec): a clean candidate connects blk_00 by **containment** — blk_00
is covered with NO BUSTERM (edge-tap) connection on any segment — rather than by
pinning a segment to blk_00's boundary.  Marked xfail until the containment model
lands; see docs/internal/seg_busterm_containment.md.
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


def test_contained_endpoint_connects_by_containment_not_edge_tap():
    """A clean candidate should connect blk_00 by containment (no edge tap).

    Today every clean candidate edge-taps blk_00 (a BUSTERM face conn) and
    over-extends a stub to its boundary.  The fix should produce a candidate
    where blk_00 is covered by a segment passing through it — connected with NO
    BUSTERM conn — while every block stays connected.
    """
    fp = _fp()
    cands = _gen(fp).generate_candidates("blk_15", ["blk_32", "blk_00"])
    containment = []
    for c in cands:
        ct = _build(c, fp)
        v = _violations(ct, c, fp)
        if "BUSTERM_OPEN" in v or "FEEDTHRU_RELAY" in v:
            continue                          # not a clean connect
        if "blk_00" not in _busterm_blocks(ct):
            containment.append(c)             # blk_00 connected without an edge tap
    assert containment, (
        "expected a clean candidate connecting blk_00 by containment (no BUSTERM "
        "edge tap); shapes: " + ", ".join(f"{c.type}({len(c.segments)}seg)" for c in cands)
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
